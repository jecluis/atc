"""The variables that a new account or project needs and nothing defines yet."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from atc.context import BuildContext
from atc.inventory import (
    ACCOUNT_VARS,
    MACHINE,
    MACHINE_VARS,
    Owner,
    Variable,
    owners_of,
    template_references,
    unit_references,
)
from atc.layers import build_account_layer, build_project_layer
from atc.registry import Account, Project, account_unit, project_unit
from atc.rendered import render_templates

_STAND_IN = "x"


@dataclass(frozen=True, slots=True)
class Needed:
    """
    A variable that a new account needs, and that nothing defines.

    ``reference`` is how units name it, such as ``account.vars.scratch``, and
    ``users`` are the units and templated assets that use it, in the order
    they were found.
    """

    reference: str
    users: tuple[str, ...]

    @property
    def machine_wide(self) -> bool:
        """Whether it is a machine-wide variable, not one of the account's."""
        return self.reference.startswith(MACHINE_VARS)

    @property
    def name(self) -> str:
        """The variable's name, as the registry spells it."""
        return self.reference.rpartition(".")[2]


def needed_variables(ctx: BuildContext, account: Account) -> list[Needed]:
    """
    Find the variables that ``account`` needs and nothing defines yet.

    ``ctx.registry`` holds ``account``. What it needs is every reference to one
    of its own variables, or to a machine-wide one, in the units its settings
    are built from, in the templated assets atc renders for every account, and
    in the units of the projects it may work on: those that allow every
    account, since a new account is in no project's ``accounts``. They come in
    the order they are first used, in that order of places.

    With a stand-in for each of them, the account and those projects are then
    built, so that whatever else would stop them is found now, before the
    values are asked for.

    Raises:
        ConfigError: something other than those variables stops the account,
            or one of those projects, from being built: a unit that is missing
            or malformed, an import cycle, a reference to a variable no value
            can define, and so on.

    """
    projects = _open_projects(ctx)
    roots = [account_unit(ctx.sources, ctx.registry, account)]
    pairs = [*unit_references(ctx, roots), *template_references(ctx)]
    for project in projects:
        root = project_unit(ctx.sources, ctx.registry, project)
        if root is not None:
            pairs.extend(unit_references(ctx, [root]))
    found: dict[str, list[str]] = {}
    for user, reference in pairs:
        users = found.setdefault(reference, [])
        if user not in users:
            users.append(user)

    needed = [
        Needed(reference, tuple(users))
        for reference, users in found.items()
        if _undefined(reference, account.vars, ctx.registry.vars)
    ]
    _build_with_stand_ins(ctx, account, needed, projects)
    return needed


def _open_projects(ctx: BuildContext) -> list[Project]:
    """Give the projects that allow every account, in the order of the file."""
    return [p for p in ctx.registry.projects.values() if p.accounts is None]


def _undefined(
    reference: str, own: Mapping[str, str], machine: Mapping[str, str]
) -> bool:
    """Say whether ``reference`` names a variable that a value could define, unset."""
    for prefix, defined in ((ACCOUNT_VARS, own), (MACHINE_VARS, machine)):
        if reference.startswith(prefix):
            name = reference.removeprefix(prefix)
            return "." not in name and name not in defined
    return False


def _build_with_stand_ins(
    ctx: BuildContext, account: Account, needed: list[Needed], projects: list[Project]
) -> None:
    """Build ``account`` and ``projects``, with a stand-in for each of ``needed``."""
    own = {n.name: _STAND_IN for n in needed if not n.machine_wide}
    machine = {n.name: _STAND_IN for n in needed if n.machine_wide}
    trial_account = replace(account, vars={**account.vars, **own})
    registry = replace(
        ctx.registry,
        vars={**ctx.registry.vars, **machine},
        accounts={**ctx.registry.accounts, account.name: trial_account},
    )
    trial = replace(ctx, registry=registry)
    _ = build_account_layer(trial, trial_account, lax=True)
    _ = render_templates(trial, trial_account)
    for project in projects:
        _ = build_project_layer(trial, project, trial_account, lax=True)


def project_variables(ctx: BuildContext, project: Project) -> list[Variable]:
    """
    Find the variables that the new ``project`` needs and nothing defines yet.

    ``ctx.registry`` holds ``project``. What it needs is every reference to a
    variable in its unit and what that imports, which is what is built for the
    project: its own variables, those of each account it allows, and the
    machine-wide ones. They come by owner, the project's, then each account's
    in the order of the file, then the machine's, each in the order they are
    first used.

    With a stand-in for each of them, the project is then built for each
    account it allows, so that whatever else would stop it is found now,
    before the values are asked for.

    Raises:
        ConfigError: something other than those variables stops the project from
            being built for an account it allows: a unit that is missing or
            malformed, an import cycle, a reference to a variable no value can
            define, and so on.

    """
    registry = ctx.registry
    allowed = registry.allowed_accounts(project)
    root = project_unit(ctx.sources, registry, project)
    references = [] if root is None else unit_references(ctx, [root])
    found: dict[Owner, dict[str, list[str]]] = {}
    for user, reference in references:
        for owner, name in owners_of(reference, allowed, project.name):
            if name in owner.defined(registry):
                continue
            users = found.setdefault(owner, {}).setdefault(name, [])
            if user not in users:
                users.append(user)
    owners = [
        Owner("project", project.name),
        *(Owner("account", name) for name in registry.accounts),
        MACHINE,
    ]
    needed = [
        Variable(name, owner, None, tuple(users))
        for owner in owners
        for name, users in found.get(owner, {}).items()
    ]
    _build_project_with_stand_ins(ctx, project, needed, allowed)
    return needed


def _build_project_with_stand_ins(
    ctx: BuildContext,
    project: Project,
    needed: list[Variable],
    allowed: tuple[str, ...],
) -> None:
    """Build ``project`` for each of ``allowed``, with a stand-in for ``needed``."""

    def stand_ins(owner: Owner) -> dict[str, str]:
        return {v.name: _STAND_IN for v in needed if v.owner == owner}

    registry = ctx.registry
    trial_project = replace(
        project,
        vars={**project.vars, **stand_ins(Owner("project", project.name))},
    )
    accounts = {
        name: replace(
            account, vars={**account.vars, **stand_ins(Owner("account", name))}
        )
        for name, account in registry.accounts.items()
    }
    trial = replace(
        ctx,
        registry=replace(
            registry,
            vars={**registry.vars, **stand_ins(MACHINE)},
            accounts=accounts,
            projects={**registry.projects, project.name: trial_project},
        ),
    )
    for name in allowed:
        _ = build_project_layer(trial, trial_project, accounts[name], lax=True)
