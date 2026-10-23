"""The machine registry: the accounts and projects of this machine."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from atc.errors import PathError, RegistryError
from atc.jsonval import JsonValue
from atc.refs import Located, resolve_registry
from atc.sources import Sources
from atc.yamlval import load_yaml, parse_yaml

REGISTRY_VERSION = 1
REGISTRY_FILE = "machine.yaml"
"""The registry's file name, in atc's configuration directory."""

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
    vars: dict[str, str] = Field(default_factory=dict)


class _ProjectModel(_Model):
    repo: str
    account: str | None = None
    accounts: list[str] | None = None
    unit: str | None = None
    vars: dict[str, str] = Field(default_factory=dict)


class _RegistryModel(_Model):
    version: int
    vars: dict[str, str] = Field(default_factory=dict)
    default_account: str
    accounts: dict[str, _AccountModel]
    projects: dict[str, _ProjectModel] = Field(default_factory=dict)

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
    """

    name: str
    agent: str
    dir: Path
    unit: str
    vars: Mapping[str, str]


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
    variables.
    """

    name: str
    repo: Path
    account: str | None
    accounts: tuple[str, ...] | None
    unit: str
    unit_is_default: bool
    vars: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Registry:
    """
    The registry read from ``path``.

    ``vars`` are the machine-wide variables. ``default_account`` is the account
    used outside projects, and by projects that name none. ``accounts`` and
    ``projects`` are in the order of the file. ``warnings`` are problems that do
    not stop atc from using the registry.
    """

    path: Path
    vars: Mapping[str, str]
    default_account: str
    accounts: Mapping[str, Account]
    projects: Mapping[str, Project]
    warnings: tuple[str, ...]

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
        message = f"{path}: not found; create it, with a version and the accounts"
        raise RegistryError(message)
    return _build(load_yaml(path), path, home)


def parse_registry(text: str, origin: Path, home: Path) -> Registry:
    """
    Check ``text`` as the contents of the registry file ``origin``.

    Raises:
        RegistryError: as :func:`load_registry`, for everything but the file.

    """
    return _build(parse_yaml(text, str(origin)), origin, home)


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
    owners: dict[Path, str] = {}
    for name, entry in model.accounts.items():
        _check_name("account", name, f"/accounts/{name}", origin)
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
        accounts[name] = Account(
            name=name,
            agent=entry.agent,
            dir=directory,
            unit=unit,
            vars=dict(entry.vars),
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
        warnings=(),
    )


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


def _check_name(kind: str, name: str, pointer: str, origin: Path) -> None:
    """Refuse an account or project name that the name rule does not allow."""
    if not _NAME.fullmatch(name):
        message = (
            f"{origin}: {pointer}: '{name}' is not a valid {kind} name; names "
            "are lowercase letters, digits, '_' and '-', and start with a "
            "letter or a digit"
        )
        raise RegistryError(message)


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
