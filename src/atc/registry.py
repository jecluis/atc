"""The machine registry: the accounts and projects of this machine."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from atc.errors import PathError, RegistryError, WriteError
from atc.fsutil import atomic_write
from atc.jsonval import JsonObject, JsonValue
from atc.refs import Located, resolve_registry
from atc.sources import Sources
from atc.yamlval import (
    delete_in_text,
    delete_item_in_text,
    load_yaml,
    parse_yaml,
    set_in_text,
)

REGISTRY_VERSION = 1
REGISTRY_FILE = "machine.yaml"
"""The registry's file name, in atc's configuration directory."""
DEFAULT_AGENTS_MD = "AGENTS.in.md"
"""
The asset that ``default-agents-md`` names when the registry says nothing.

An agent working in the source's own repository would load an ``AGENTS.md``
there as that repository's instructions; a template is linked as its copy.
"""
DEFAULT_GC_HOURS = 24
"""The hours between full ``git gc`` runs when the registry says nothing."""
DEFAULT_BRANCH_FROM = "main"
"""The branch new branches start from when a project says nothing."""
AUTO_ACCOUNT = "auto"
"""What ``-a`` takes to mean the least used account, so no account has this name."""
AGENTS = ("claude",)
"""The agents an account can be for, as the registry's models spell them."""

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
_VARIABLE_NAME = re.compile(r"[a-z][a-z0-9_]*")


def _kebab(name: str) -> str:
    return name.replace("_", "-")


class _Model(BaseModel):
    """Checks a part of the registry; unknown keys and wrong types are errors."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True, alias_generator=_kebab
    )


class _AccountModel(_Model):
    agent: Literal["claude"]
    dir: str
    unit: str | None = None
    use_default_agents_md: bool = False
    agents_md_file: str | None = None
    skills: list[str] = Field(default_factory=list)
    vars: dict[str, str] = Field(default_factory=dict)


class _AgentModel(_Model):
    bin: str | None = Field(default=None, min_length=1)


class _ProjectModel(_Model):
    repo: str
    worktrees: str | None = None
    branch_from: str = Field(default=DEFAULT_BRANCH_FROM, min_length=1)
    account: str | None = None
    accounts: list[str] | None = None
    unit: str | None = None
    snapshot: str | None = Field(default=None, min_length=1)
    vars: dict[str, str] = Field(default_factory=dict)


class _MaintModel(_Model):
    gc_hours: int = Field(default=DEFAULT_GC_HOURS, ge=1)


class _RegistryModel(_Model):
    version: int
    vars: dict[str, str] = Field(default_factory=dict)
    default_account: str
    default_agents_md: str = DEFAULT_AGENTS_MD
    agents: dict[Literal["claude"], _AgentModel] = Field(default_factory=dict)
    accounts: dict[str, _AccountModel]
    projects: dict[str, _ProjectModel] = Field(default_factory=dict)
    maint: _MaintModel = Field(default_factory=_MaintModel)

    @field_validator("version")
    @classmethod
    def check_version(cls, value: int) -> int:
        # `Literal[1]` would let `true` and `1.0` through; a strict int does not.
        if value != REGISTRY_VERSION:
            raise ValueError(
                f"unsupported version; atc reads version {REGISTRY_VERSION}"
            )
        return value


@dataclass(frozen=True, slots=True)
class Account:
    """
    One account.

    ``agent`` is the coding agent it is for. ``dir`` is its configuration
    directory, absolute and normalized. ``unit`` is the unit reference that its
    settings are built from, in the form references take outside a unit.
    ``vars`` are its own variables.

    ``use_default_agents_md`` asks for the registry's default instructions file
    to be linked into the account. ``agents_md_file`` is linked instead of that
    default: a ``Path`` when the registry gave a filesystem path, absolute and
    normalized, and a ``str`` when it gave an asset reference, as written.
    ``None`` means the registry set none.

    ``skills`` are the references, as written, of the skills to link into the
    account, in the registry's order.
    """

    name: str
    agent: str
    dir: Path
    unit: str
    vars: Mapping[str, str]
    use_default_agents_md: bool = False
    agents_md_file: Path | str | None = None
    skills: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Project:
    """
    One project.

    ``repo`` is a checkout of its repository, absolute and normalized.
    ``account`` is the account it uses by default; ``None`` means the
    registry's default account. ``accounts`` are the accounts allowed to work
    on it; ``None`` means every account. ``unit`` is the unit reference that its
    settings are built from, and ``unit_is_default`` says whether the registry
    left it to the default, ``projects/<name>.json``. ``vars`` are its own
    variables. ``snapshot`` is the snapper config to snapshot before each
    session; ``None`` means no snapshot. ``worktrees`` is the directory its
    worktrees are made in, absolute and normalized; ``None`` means the registry
    names none. ``branch_from`` is the branch that new branches start from.
    """

    name: str
    repo: Path
    account: str | None
    accounts: tuple[str, ...] | None
    unit: str
    unit_is_default: bool
    vars: Mapping[str, str]
    snapshot: str | None = None
    worktrees: Path | None = None
    branch_from: str = DEFAULT_BRANCH_FROM


@dataclass(frozen=True, slots=True)
class Registry:
    """
    The registry read from ``path``.

    ``vars`` are the machine-wide variables. ``default_account`` is the account
    used outside projects, and by projects that name none. ``accounts`` and
    ``projects`` are in the order of the file. ``warnings`` are problems that do
    not stop atc from using the registry. ``default_agents_md`` is the asset
    reference of the instructions file that accounts link when they ask for the
    default. ``agent_bins`` maps an agent to the binary the registry names for
    it: an absolute path, or a command name to look up on ``PATH``. An agent
    that is not in it is looked up on ``PATH`` by its own name. ``gc_hours`` is
    the number of hours between the full ``git gc`` runs of ``atc maint``, at
    least 1.
    """

    path: Path
    vars: Mapping[str, str]
    default_account: str
    accounts: Mapping[str, Account]
    projects: Mapping[str, Project]
    warnings: tuple[str, ...]
    default_agents_md: str = DEFAULT_AGENTS_MD
    agent_bins: Mapping[str, str] = field(default_factory=dict[str, str])
    gc_hours: int = DEFAULT_GC_HOURS

    def account(self, name: str) -> Account:
        """
        Find the account called ``name``.

        Raises:
            RegistryError: there is no such account. The message names the ones
                there are.

        """
        found = self.accounts.get(name)
        if found is not None:
            return found
        raise RegistryError(f"{self.path}: {_no_account(name, self.accounts)}")

    def project(self, name: str) -> Project:
        """
        Find the project called ``name``.

        Raises:
            RegistryError: there is no such project. The message names the ones
                there are.

        """
        found = self.projects.get(name)
        if found is not None:
            return found
        known = ", ".join(self.projects)
        suffix = f"; the projects are {known}" if known else "; it defines no projects"
        raise RegistryError(f"{self.path}: no project named '{name}'{suffix}")


def load_registry(path: Path, home: Path) -> Registry:
    """
    Read and check the registry file at ``path``.

    ``home`` is what a leading ``~`` in a path stands for.

    Raises:
        RegistryError: the file is missing or unreadable, is not valid YAML, or
            does not describe a valid registry.

    """
    if not path.exists():
        raise RegistryError(_not_found(path))
    return _build(load_yaml(path), path, home)


def _not_found(path: Path) -> str:
    """Say that there is no registry file at ``path``."""
    return f"{path}: not found; atc account add creates it with its first account"


def parse_registry(text: str, origin: Path, home: Path) -> Registry:
    """
    Check ``text`` as the contents of the registry file ``origin``.

    Raises:
        RegistryError: as :func:`load_registry`, for everything but the file.

    """
    return _build(parse_yaml(text, str(origin)), origin, home)


def set_skills(path: Path, home: Path, account: str, refs: Sequence[str]) -> Registry:
    """
    Write ``refs`` as the ``skills`` of ``account`` in the registry file ``path``.

    The file is edited in place, so its comments and key order stay. It is
    replaced only when it is a valid registry before the edit and after it, and
    in one step, as :func:`atc.fsutil.atomic_write` does. Returns the registry
    as it now reads. ``home`` is what a leading ``~`` in a path stands for.

    Raises:
        RegistryError: the file cannot be read, or is not a valid registry,
            before or after the edit, or has no such account. The file is as it
            was.
        WriteError: the file cannot be written.

    """
    text = _read_text(path)
    _ = parse_registry(text, path, home).account(account)
    selection: list[JsonValue] = [*refs]
    edited = set_in_text(text, ["accounts", account, "skills"], selection)
    return _write_checked(path, home, edited)


def add_project(path: Path, home: Path, name: str, fields: JsonObject) -> Registry:
    """
    Add the project ``name``, set up by ``fields``, to the registry file ``path``.

    ``fields`` are the keys of the project as the file spells them, such as
    ``repo`` and ``branch-from``. The file is edited and replaced as
    :func:`set_skills` does, and the new entry goes after the others. Returns the
    registry as it now reads.

    Raises:
        RegistryError: the file cannot be read, or is not a valid registry,
            before or after the edit, or already has a project of that name. The
            file is as it was.
        WriteError: the file cannot be written.

    """
    text = _read_text(path)
    if name in parse_registry(text, path, home).projects:
        message = f"{path}: /projects/{name}: a project of that name exists already"
        raise RegistryError(message)
    edited = set_in_text(text, ["projects", name], fields)
    return _write_checked(path, home, edited)


def add_account(
    path: Path,
    home: Path,
    name: str,
    fields: JsonObject,
    machine_vars: Mapping[str, str] | None = None,
) -> Registry:
    """
    Add the account ``name``, set up by ``fields``, to the registry file ``path``.

    ``fields`` are the keys of the account as the file spells them, such as
    ``dir`` and ``use-default-agents-md``. ``machine_vars`` are added to the
    machine-wide ``vars``. When there is no file, it is created, with the
    account as its default account. Otherwise the file is edited and replaced
    as :func:`set_skills` does, and the new entry goes after the others.
    Returns the registry as it now reads.

    Raises:
        RegistryError: the file cannot be read, or is not a valid registry,
            before or after the edit, or already has an account of that name.
            The file is as it was.
        WriteError: the file cannot be written.

    """
    if path.exists():
        text = _read_text(path)
        if name in parse_registry(text, path, home).accounts:
            message = (
                f"{path}: /accounts/{name}: an account of that name exists already"
            )
            raise RegistryError(message)
    else:
        text = set_in_text("", ["version"], REGISTRY_VERSION)
        text = set_in_text(text, ["default-account"], name)
    for variable, value in (machine_vars or {}).items():
        text = set_in_text(text, ["vars", variable], value)
    edited = set_in_text(text, ["accounts", name], fields)
    return _write_checked(path, home, edited)


@dataclass(frozen=True, slots=True)
class ProjectRemoval:
    """
    The removal of the project ``name`` from the registry file ``path``.

    It is planned on ``text``, the file as it was read, which reads as
    ``registry``. The project's entry goes, comments inside it included, with
    the comment lines directly above it at its indentation.
    """

    path: Path
    home: Path
    text: str
    registry: Registry
    name: str

    @classmethod
    def plan(cls, path: Path, home: Path, name: str) -> Self:
        """
        Read the registry file ``path``, to remove the project ``name`` from it.

        ``home`` is what a leading ``~`` in a path stands for.

        Raises:
            RegistryError: the file is missing, cannot be read or is not a valid
                registry, or has no such project.

        """
        if not path.exists():
            raise RegistryError(_not_found(path))
        text = _read_text(path)
        registry = parse_registry(text, path, home)
        _ = registry.project(name)
        return cls(path, home, text, registry, name)

    @property
    def project(self) -> Project:
        """The project to remove, as the file read when it was planned."""
        return self.registry.projects[self.name]

    def write(self) -> Registry:
        """
        Remove the project, and replace the file as :func:`set_skills` does.

        Returns the registry as it now reads.

        Raises:
            RegistryError: the file no longer holds the text the removal was
                planned on, or cannot be read, or would not be a valid registry
                without the project. It is left as it is.
            WriteError: the file cannot be written.

        """
        _refuse_changed(self.path, self.text)
        edited = delete_in_text(self.text, ["projects", self.name])
        return _write_checked(self.path, self.home, edited)


@dataclass(frozen=True, slots=True)
class AccountUses:
    """
    Where the registry names one account, other than in its own entry.

    ``default`` says whether it is the default account. ``defaulted`` are the
    projects whose ``account`` it is, and ``allowed`` the projects whose
    ``accounts`` name it, both in the order of the file.
    """

    default: bool
    defaulted: tuple[str, ...]
    allowed: tuple[str, ...]

    def __bool__(self) -> bool:
        return self.default or bool(self.defaulted) or bool(self.allowed)


def account_uses(registry: Registry, name: str) -> AccountUses:
    """Find where ``registry`` names the account ``name``, other than in its entry."""
    projects = registry.projects.values()
    return AccountUses(
        default=registry.default_account == name,
        defaulted=tuple(p.name for p in projects if p.account == name),
        allowed=tuple(p.name for p in projects if name in (p.accounts or ())),
    )


def check_removable(registry: Registry, name: str) -> None:
    """
    Refuse to remove the account ``name`` when no removal could leave a valid registry.

    That is when it is the only account, since the registry needs a default,
    and when a project allows only it: dropping the emptied ``accounts`` would
    allow every account instead.

    Raises:
        RegistryError: there is no such account, or it cannot be removed.

    """
    _ = registry.account(name)
    if len(registry.accounts) == 1:
        message = f"{registry.path}: '{name}' is the only account, and one is needed"
        raise RegistryError(message)
    alone = [
        p.name
        for p in registry.projects.values()
        if p.accounts is not None and set(p.accounts) == {name}
    ]
    if alone:
        projects = ", ".join(f"'{project}'" for project in alone)
        message = (
            f"{registry.path}: account '{name}' is the only one that project "
            f"{projects} allows; allow another account there first"
        )
        raise RegistryError(message)


@dataclass(frozen=True, slots=True)
class AccountRemoval:
    """
    The removal of the account ``name`` from the registry file ``path``.

    It is planned on ``text``, the file as it was read, which reads as
    ``registry``; ``uses`` is where that names the account. The account's entry
    goes as :class:`ProjectRemoval` removes a project's. A project whose
    ``account`` it is loses that key, and so falls back to the default account,
    and every project's ``accounts`` lose it, each item edited out in place.
    """

    path: Path
    home: Path
    text: str
    registry: Registry
    name: str
    uses: AccountUses

    @classmethod
    def plan(cls, path: Path, home: Path, name: str) -> Self:
        """
        Read the registry file ``path``, to remove the account ``name`` from it.

        ``home`` is what a leading ``~`` in a path stands for.

        Raises:
            RegistryError: the file is missing, cannot be read or is not a valid
                registry, or has no such account, or the account cannot be
                removed (see :func:`check_removable`).

        """
        if not path.exists():
            raise RegistryError(_not_found(path))
        text = _read_text(path)
        registry = parse_registry(text, path, home)
        check_removable(registry, name)
        return cls(path, home, text, registry, name, account_uses(registry, name))

    def edited(self, default: str | None) -> tuple[str, Registry]:
        """
        Edit the account out, with ``default`` as the new default account.

        ``default`` is needed when the account is the default now. Returns the
        new text and the registry it reads as.

        Raises:
            RegistryError: the new text is not a valid registry, or the edit
                would lose a comment, or ``default`` is missing.

        """
        if self.uses.default and default is None:
            message = f"{self.path}: '{self.name}' is the default account; name another"
            raise RegistryError(message)
        edited = delete_in_text(self.text, ["accounts", self.name])
        if default is not None and default != self.registry.default_account:
            edited = set_in_text(edited, ["default-account"], default)
        for project in self.uses.defaulted:
            edited = delete_in_text(edited, ["projects", project, "account"])
        for project in self.uses.allowed:
            keys = ["projects", project, "accounts"]
            for _ in range(
                (self.registry.projects[project].accounts or ()).count(self.name)
            ):
                edited = delete_item_in_text(edited, keys, self.name)
        return edited, parse_registry(edited, self.path, self.home)

    def defaults(self) -> list[str]:
        """
        Give the accounts that can be the default in its place.

        Those are the ones that leave a valid registry, in the order of the file.

        Raises:
            RegistryError: none does. The message says why the first one does not.

        """
        found: list[str] = []
        first: RegistryError | None = None
        for other in self.registry.accounts:
            if other == self.name:
                continue
            try:
                _ = self.edited(other)
            except RegistryError as exc:
                first = first or exc
                continue
            found.append(other)
        if not found and first is not None:
            message = (
                f"no other account can be the default in place of '{self.name}'; "
                f"for one, {first}"
            )
            raise RegistryError(message) from first
        return found

    def write(self, edited: str) -> Registry:
        """
        Replace the file with ``edited``, as :func:`set_skills` does.

        Returns the registry as it now reads.

        Raises:
            RegistryError: the file no longer holds the text the removal was
                planned on, or cannot be read. It is left as it is.
            WriteError: the file cannot be written.

        """
        _refuse_changed(self.path, self.text)
        return _write_checked(self.path, self.home, edited)


def _refuse_changed(path: Path, text: str) -> None:
    """
    Refuse to go on when the registry file ``path`` no longer holds ``text``.

    Raises:
        RegistryError: it holds something else, or cannot be read.

    """
    if _read_text(path) != text:
        message = f"{path}: changed since it was read; nothing was removed"
        raise RegistryError(message)


def _read_text(path: Path) -> str:
    """Read the registry file ``path``."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RegistryError(f"{path}: cannot be read: {exc}") from exc


def _write_checked(path: Path, home: Path, edited: str) -> Registry:
    """Replace the registry file ``path`` with ``edited``, if it is a valid registry."""
    registry = parse_registry(edited, path, home)
    try:
        atomic_write(path, edited)
    except OSError as exc:
        raise WriteError(f"{path}: cannot be written: {exc}") from exc
    return registry


def account_unit(sources: Sources, registry: Registry, account: Account) -> Located:
    """
    Find the unit that the settings of ``account`` are built from.

    Raises:
        PathError: the account's unit reference is malformed, no source has the
            unit, or several do.

    """
    where = f"{registry.path}: account '{account.name}'"
    try:
        found = resolve_registry(sources, account.unit)
    except PathError as exc:
        raise PathError(f"{where}: {exc}") from exc
    if found is None:
        raise PathError(f"{where}: '{account.unit}': no such unit")
    return found


def project_unit(
    sources: Sources, registry: Registry, project: Project
) -> Located | None:
    """
    Find the unit that the settings of ``project`` are built from.

    ``None`` means the project has no unit: it left the unit to the default, and
    no source has it. A project without a unit has no project layer.

    Raises:
        PathError: the project's unit reference is malformed, a unit that the
            registry names does not exist, or several sources have the unit.

    """
    where = f"{registry.path}: project '{project.name}'"
    try:
        found = resolve_registry(sources, project.unit)
    except PathError as exc:
        raise PathError(f"{where}: {exc}") from exc
    if found is None and not project.unit_is_default:
        raise PathError(f"{where}: '{project.unit}': no such unit")
    return found


def choose_account(
    registry: Registry, project: Project | None, override: str | None
) -> Account:
    """
    Pick the account to work with: ``override``, else the project's, else the default.

    ``project`` is ``None`` outside the registered projects.

    Raises:
        RegistryError: there is no such account, or the project does not allow it.

    """
    if override is not None:
        name = override
    elif project is not None and project.account is not None:
        name = project.account
    else:
        name = registry.default_account
    chosen = registry.account(name)
    if (
        project is not None
        and project.accounts is not None
        and name not in project.accounts
    ):
        allowed = ", ".join(project.accounts)
        message = (
            f"{registry.path}: project '{project.name}' does not allow "
            f"account '{name}'; it allows {allowed}"
        )
        raise RegistryError(message)
    return chosen


def normalize_path(text: str, home: Path) -> Path:
    """
    Turn a path from the registry into the one it names.

    A leading ``~`` stands for ``home``, and nothing else is expanded, so
    ``$HOME`` stays as written. The path must then be absolute. It is
    normalized so that equal paths compare equal: no trailing ``/``, no ``..``,
    and symbolic links followed.

    Raises:
        RegistryError: the path is not absolute once ``~`` is expanded.

    """
    if text == "~":
        expanded = home
    elif text.startswith("~/"):
        expanded = home / text[2:]
    else:
        expanded = Path(text)
    if not expanded.is_absolute():
        raise RegistryError(f"'{text}': paths must be absolute, or start with ~/")
    return expanded.resolve(strict=False)


def _build(document: JsonValue, origin: Path, home: Path) -> Registry:
    """Check ``document`` and build the registry from it."""
    if not isinstance(document, dict):
        raise RegistryError(f"{origin}: the registry must be a mapping")
    try:
        model = _RegistryModel.model_validate(document)
    except ValidationError as exc:
        raise RegistryError(f"{origin}: {_describe(exc)}") from exc

    _check_variables(model.vars, "/vars", origin)
    accounts: dict[str, Account] = {}
    warnings: list[str] = []
    owners: dict[Path, str] = {}
    for name, entry in model.accounts.items():
        problem = account_name_problem(name)
        if problem is not None:
            raise RegistryError(f"{origin}: /accounts/{name}: {problem}")
        try:
            directory = normalize_path(entry.dir, home)
        except RegistryError as exc:
            raise RegistryError(f"{origin}: /accounts/{name}/dir: {exc}") from exc
        if directory in owners:
            message = (
                f"{origin}: accounts '{owners[directory]}' and '{name}' share "
                f"the directory {directory}"
            )
            raise RegistryError(message)
        owners[directory] = name
        _check_variables(entry.vars, f"/accounts/{name}/vars", origin)
        unit = entry.unit if entry.unit is not None else f"accounts/{name}.json"
        agents_md_file = _agents_md_file(
            entry.agents_md_file, f"/accounts/{name}/agents-md-file", origin, home
        )
        if agents_md_file is not None and entry.use_default_agents_md:
            message = (
                f"{origin}: /accounts/{name}: agents-md-file is set, so "
                "use-default-agents-md has no effect"
            )
            warnings.append(message)
        accounts[name] = Account(
            name=name,
            agent=entry.agent,
            dir=directory,
            unit=unit,
            vars=dict(entry.vars),
            use_default_agents_md=entry.use_default_agents_md,
            agents_md_file=agents_md_file,
            skills=tuple(entry.skills),
        )

    _check_account(model.default_account, accounts, "/default-account", origin)
    projects = {
        name: _build_project(name, entry, model.default_account, accounts, origin, home)
        for name, entry in model.projects.items()
    }
    return Registry(
        path=origin,
        vars=dict(model.vars),
        default_account=model.default_account,
        accounts=accounts,
        projects=projects,
        warnings=tuple(warnings),
        default_agents_md=model.default_agents_md,
        agent_bins={
            agent: _agent_bin(entry.bin, f"/agents/{agent}/bin", origin, home)
            for agent, entry in model.agents.items()
            if entry.bin is not None
        },
        gc_hours=model.maint.gc_hours,
    )


def _agent_bin(text: str, pointer: str, origin: Path, home: Path) -> str:
    """
    Read an agent's ``bin``: a path if it contains ``/``, otherwise a command name.

    A path has a leading ``~`` expanded and must then be absolute. It is not
    resolved, so a symbolic link is started as itself.
    """
    if "/" not in text:
        return text
    expanded = home / text[2:] if text.startswith("~/") else Path(text)
    if not expanded.is_absolute():
        message = (
            f"{origin}: {pointer}: '{text}': paths must be absolute, or start with ~/"
        )
        raise RegistryError(message)
    return str(expanded)


def _agents_md_file(
    text: str | None, pointer: str, origin: Path, home: Path
) -> Path | str | None:
    """
    Read an ``agents-md-file``: a path if it starts with ``/`` or ``~``, else an asset.

    A path is normalized as ``dir`` is. An asset reference is kept as written.
    """
    if text is None or not text.startswith(("/", "~")):
        return text
    try:
        return normalize_path(text, home)
    except RegistryError as exc:
        raise RegistryError(f"{origin}: {pointer}: {exc}") from exc


def _build_project(
    name: str,
    entry: _ProjectModel,
    default_account: str,
    accounts: Mapping[str, Account],
    origin: Path,
    home: Path,
) -> Project:
    """Check one project against the accounts that exist, and build it."""
    pointer = f"/projects/{name}"
    _check_name("project", name, pointer, origin)
    try:
        repo = normalize_path(entry.repo, home)
    except RegistryError as exc:
        raise RegistryError(f"{origin}: {pointer}/repo: {exc}") from exc
    worktrees = None
    if entry.worktrees is not None:
        try:
            worktrees = normalize_path(entry.worktrees, home)
        except RegistryError as exc:
            raise RegistryError(f"{origin}: {pointer}/worktrees: {exc}") from exc
    _check_variables(entry.vars, f"{pointer}/vars", origin)
    if entry.account is not None:
        _check_account(entry.account, accounts, f"{pointer}/account", origin)
    for allowed in entry.accounts or ():
        _check_account(allowed, accounts, f"{pointer}/accounts", origin)
    if entry.accounts is not None:
        _check_allowed(entry, default_account, pointer, origin)
    return Project(
        name=name,
        repo=repo,
        account=entry.account,
        accounts=None if entry.accounts is None else tuple(entry.accounts),
        unit=entry.unit if entry.unit is not None else f"projects/{name}.json",
        unit_is_default=entry.unit is None,
        vars=dict(entry.vars),
        snapshot=entry.snapshot,
        worktrees=worktrees,
        branch_from=entry.branch_from,
    )


def _check_allowed(
    entry: _ProjectModel, default_account: str, pointer: str, origin: Path
) -> None:
    """Refuse a project whose ``accounts`` leave out the account it would use."""
    allowed = entry.accounts or []
    if not allowed:
        message = (
            f"{origin}: {pointer}/accounts: must name at least one account; "
            "leave it out to allow every account"
        )
        raise RegistryError(message)
    if entry.account is not None and entry.account not in allowed:
        where, what = f"{pointer}/account", f"'{entry.account}'"
    elif entry.account is None and default_account not in allowed:
        where, what = f"{pointer}/accounts", f"the default account '{default_account}'"
    else:
        return
    message = (
        f"{origin}: {where}: {what} is not one of the project's accounts: "
        f"{', '.join(allowed)}"
    )
    raise RegistryError(message)


def account_name_problem(name: str) -> str | None:
    """Say what is wrong with ``name`` as an account's name; ``None`` if nothing is."""
    if not _NAME.fullmatch(name):
        return _invalid_name("account", name)
    if name == AUTO_ACCOUNT:
        return (
            f"'{name}' is reserved for -a {name}, which picks the project's least "
            "used account; name the account something else"
        )
    return None


def _check_name(kind: str, name: str, pointer: str, origin: Path) -> None:
    """Refuse an account or project name that the name rule does not allow."""
    if not _NAME.fullmatch(name):
        raise RegistryError(f"{origin}: {pointer}: {_invalid_name(kind, name)}")


def _invalid_name(kind: str, name: str) -> str:
    return (
        f"'{name}' is not a valid {kind} name; names are lowercase letters, "
        "digits, '_' and '-', and start with a letter or a digit"
    )


def _check_account(
    name: str, accounts: Mapping[str, Account], pointer: str, origin: Path
) -> None:
    """Refuse an account name, found at ``pointer``, that is not an account."""
    if name not in accounts:
        raise RegistryError(f"{origin}: {pointer}: {_no_account(name, accounts)}")


def _no_account(name: str, accounts: Mapping[str, Account]) -> str:
    """Say that there is no account ``name``, and which accounts there are."""
    known = ", ".join(accounts)
    suffix = f"; the accounts are {known}" if known else "; it defines no accounts"
    return f"no account named '{name}'{suffix}"


def _check_variables(variables: Mapping[str, str], pointer: str, origin: Path) -> None:
    """Refuse a variable whose name cannot be referenced, or whose value could."""
    for name, value in variables.items():
        if not _VARIABLE_NAME.fullmatch(name):
            message = (
                f"{origin}: {pointer}/{name}: '{name}' is not a valid variable "
                "name; names are lowercase letters, digits and '_', and start "
                "with a letter"
            )
            raise RegistryError(message)
        if "{{" in value:
            message = f"{origin}: {pointer}/{name}: the value must not contain '{{{{'"
            raise RegistryError(message)


def _describe(exc: ValidationError) -> str:
    """Flatten a pydantic validation failure into one line of JSON pointers."""
    return "; ".join(
        f"/{'/'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )
