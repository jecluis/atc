"""The machine registry: the accounts of this machine, and where they live."""

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


class _RegistryModel(_Model):
    version: int
    vars: dict[str, str] = Field(default_factory=dict)
    accounts: dict[str, _AccountModel]

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
class Registry:
    """
    The registry read from ``path``.

    ``vars`` are the machine-wide variables. ``accounts`` is in the order of
    the file. ``warnings`` are problems that do not stop atc from using the
    registry.
    """

    path: Path
    vars: Mapping[str, str]
    accounts: Mapping[str, Account]
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
        known = ", ".join(self.accounts)
        suffix = f"; the accounts are {known}" if known else "; it defines no accounts"
        raise RegistryError(f"{self.path}: no account named '{name}'{suffix}")


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
        if not _NAME.fullmatch(name):
            message = (
                f"{origin}: /accounts/{name}: '{name}' is not a valid account "
                "name; names are lowercase letters, digits, '_' and '-', and "
                "start with a letter or a digit"
            )
            raise RegistryError(message)
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
    return Registry(path=origin, vars=dict(model.vars), accounts=accounts, warnings=())


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
