"""Every variable of the machine registry: those it defines and those units use."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, override

from atc.context import BuildContext
from atc.errors import ConfigError
from atc.jsonval import JsonObject, JsonValue
from atc.refs import Located
from atc.registry import Registry, account_unit, project_unit
from atc.rendered import rendered_name
from atc.resolve import resolve
from atc.template import REFERENCE

ACCOUNT_VARS = "account.vars."
"""How a reference to one of an account's own variables starts."""
PROJECT_VARS = "project.vars."
"""How a reference to one of a project's own variables starts."""
MACHINE_VARS = "vars."
"""How a reference to a machine-wide variable starts."""

type Level = Literal["machine", "account", "project"]

_PREFIXES: dict[Level, str] = {
    "machine": MACHINE_VARS,
    "account": ACCOUNT_VARS,
    "project": PROJECT_VARS,
}


@dataclass(frozen=True, slots=True)
class Owner:
    """
    Where a variable is defined: the machine, or an account or project by name.

    ``name`` is ``""`` for the machine.
    """

    level: Level
    name: str = ""

    @override
    def __str__(self) -> str:
        return self.level if self.level == "machine" else f"{self.level} {self.name}"

    def reference(self, name: str) -> str:
        """Give the reference that units write for the variable ``name`` here."""
        return _PREFIXES[self.level] + name

    def defined(self, registry: Registry) -> Mapping[str, str]:
        """Give the variables that ``registry`` defines here."""
        match self.level:
            case "machine":
                return registry.vars
            case "account":
                return registry.accounts[self.name].vars
            case "project":
                return registry.projects[self.name].vars


MACHINE = Owner("machine")


@dataclass(frozen=True, slots=True)
class Variable:
    """
    One variable of one owner.

    ``value`` is ``None`` when the registry does not define it. ``users`` are
    the units and templated assets that use it, in the order they were found,
    and none when nothing does.
    """

    name: str
    owner: Owner
    value: str | None
    users: tuple[str, ...]

    @property
    def reference(self) -> str:
        """The reference as units write it, such as ``account.vars.scratch``."""
        return self.owner.reference(self.name)


@dataclass(frozen=True, slots=True)
class Inventory:
    """
    The variables of a registry, and why some owners could not be scanned.

    ``variables`` come by owner: the machine's, then each account's and each
    project's in the order of the file. An owner's come in the order they are
    first used, then those defined but unused, by name.
    """

    variables: tuple[Variable, ...]
    warnings: tuple[str, ...]

    def undefined(self) -> list[Variable]:
        """Give the variables that something uses and the registry does not define."""
        return [v for v in self.variables if v.value is None]


def take_inventory(ctx: BuildContext) -> Inventory:
    """
    List every variable that ``ctx.registry`` defines or that a unit it uses needs.

    References are looked for where their level exists, each unit with what it
    imports. For each account: its unit, every templated asset, which atc
    renders for every account, and the unit of every project it may work on.
    For each project: its unit, where ``project.vars`` are the project's own and
    ``account.vars`` belong to every account the project allows. A ``vars``
    reference is the machine's wherever it is. Only names of one segment count,
    since those are the only ones the registry can define.

    An account or a project whose units cannot be read gets a warning, and what
    else uses its variables is still listed.
    """
    registry = ctx.registry
    found: dict[Owner, dict[str, list[str]]] = {}
    warnings: list[str] = []

    def record(
        user: str, reference: str, accounts: Sequence[str], project: str
    ) -> None:
        for owner, name in owners_of(reference, accounts, project):
            users = found.setdefault(owner, {}).setdefault(name, [])
            if user not in users:
                users.append(user)

    templates = list(template_references(ctx))
    for account in registry.accounts.values():
        try:
            roots = [account_unit(ctx.sources, registry, account)]
            references = unit_references(ctx, roots)
        except ConfigError as exc:
            warnings.append(_unreadable(f"account '{account.name}'", exc))
            references = []
        for user, reference in [*references, *templates]:
            record(user, reference, [account.name], "")
    for project in registry.projects.values():
        allowed = (
            project.accounts
            if project.accounts is not None
            else tuple(registry.accounts)
        )
        try:
            root = project_unit(ctx.sources, registry, project)
            references = [] if root is None else unit_references(ctx, [root])
        except ConfigError as exc:
            warnings.append(_unreadable(f"project '{project.name}'", exc))
            references = []
        for user, reference in references:
            record(user, reference, allowed, project.name)

    owners = [
        MACHINE,
        *(Owner("account", name) for name in registry.accounts),
        *(Owner("project", name) for name in registry.projects),
    ]
    variables: list[Variable] = []
    for owner in owners:
        used = found.get(owner, {})
        defined = owner.defined(registry)
        names = [*used, *sorted(name for name in defined if name not in used)]
        variables.extend(
            Variable(name, owner, defined.get(name), tuple(used.get(name, ())))
            for name in names
        )
    return Inventory(tuple(variables), tuple(warnings))


def owners_of(
    reference: str, accounts: Sequence[str], project: str = ""
) -> list[tuple[Owner, str]]:
    """
    Find whose variable ``reference`` names, and the name it has there.

    ``accounts`` are the accounts an ``account.vars`` reference belongs to, and
    ``project`` the project a ``project.vars`` one does, ``""`` where there is
    none. A name of more than one segment, which no registry can define, and a
    reference to anything but a variable belong to nobody.
    """
    owners: list[tuple[Owner, str]] = []
    if reference.startswith(MACHINE_VARS):
        owners.append((MACHINE, reference.removeprefix(MACHINE_VARS)))
    elif reference.startswith(ACCOUNT_VARS):
        name = reference.removeprefix(ACCOUNT_VARS)
        owners.extend((Owner("account", account), name) for account in accounts)
    elif project and reference.startswith(PROJECT_VARS):
        owners.append((Owner("project", project), reference.removeprefix(PROJECT_VARS)))
    return [(owner, name) for owner, name in owners if "." not in name]


def unit_references(
    ctx: BuildContext, roots: Sequence[Located]
) -> list[tuple[str, str]]:
    """
    Give each reference in ``roots`` and what they import, with the unit using it.

    They come as pairs of the unit's display name and the reference, in the
    order the units are merged, and once for each unit that uses a reference.

    Raises:
        ConfigError: a unit is missing or malformed, or the imports form a cycle.

    """
    references: dict[tuple[str, str], None] = {}

    def collect(unit: Located, settings: JsonObject) -> JsonObject:
        for text in _strings(settings):
            for match in REFERENCE.finditer(text):
                references[unit.display, match[1]] = None
        return settings

    _ = resolve(ctx.sources, roots, lax=True, transform=collect)
    return list(references)


def template_references(ctx: BuildContext) -> Iterator[tuple[str, str]]:
    """
    Give each reference in the templated assets of the sources, with its asset.

    An asset that cannot be read as UTF-8 text is left out here: ``atc sync``
    and ``atc src check``, which render it, report it.
    """
    for user, text in _templates(ctx):
        seen: set[str] = set()
        for match in REFERENCE.finditer(text):
            if match[1] not in seen:
                seen.add(match[1])
                yield user, match[1]


def _templates(ctx: BuildContext) -> Iterator[tuple[str, str]]:
    """
    Give each templated asset of the sources, as its name and its text.

    One that cannot be read as UTF-8 text is left out, as
    :func:`template_references` says.
    """
    for source in ctx.sources.items:
        for path in sorted(source.assets.rglob("*")):
            if rendered_name(path.name) is None or not path.is_file():
                continue
            try:
                text = path.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            yield source.display(path.relative_to(source.assets).as_posix()), text


def _strings(value: JsonValue) -> Iterator[str]:
    """Give every string in ``value``; keys are never substituted, so not those."""
    match value:
        case str():
            yield value
        case dict():
            for member in value.values():
                yield from _strings(member)
        case list():
            for item in value:
                yield from _strings(item)
        case _:
            pass


def _unreadable(owner: str, exc: ConfigError) -> str:
    """Say that the units of ``owner`` cannot be read, so they are left out."""
    return f"{owner}: the variables of its units are left out: {exc}"
