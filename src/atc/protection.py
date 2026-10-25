"""Whether an account's settings keep its sessions from writing atc's own files."""

from pathlib import Path

from atc.context import BuildContext
from atc.jsonval import JsonObject, JsonValue
from atc.layers import permission_rules

_WILDCARDS = frozenset("*?[")


def unprotected(
    ctx: BuildContext, account: str, settings: JsonObject
) -> tuple[str, ...]:
    """
    Describe how ``settings`` leave atc's own directories writable.

    The directories are atc's configuration, state and runtime directories, the
    Python environment atc runs from, and the Python installation that one is
    built on. Commands cannot write a directory when the sandbox is enabled and
    ``sandbox.filesystem.denyWrite`` names it, or an ``Edit`` rule in
    ``permissions.deny`` does, as Claude Code adds those rules' paths to
    ``denyWrite``. The file tools cannot write it when such an ``Edit`` rule
    names it, or a bare ``Edit`` is denied. An entry or rule names a directory,
    and everything under it, with an absolute path or one under ``~``: in a
    rule, a path that starts with ``//`` or ``~`` and ends in ``/**``, and in
    an entry the same, with the ``/**`` optional. A path with any other
    wildcard in it names nothing here, so a directory may be reported that such
    a path does cover. ``account`` names the account in the messages.
    """
    named: dict[Path, str] = {}
    for label, path in (
        ("configuration", ctx.dirs.config_dir),
        ("state", ctx.dirs.state_dir),
        ("runtime", ctx.dirs.runtime_dir),
        ("install", ctx.install_dir),
        ("Python", ctx.python_dir),
    ):
        _ = named.setdefault(path, label)
    denied = [rule for name, rule in permission_rules(settings) if name == "deny"]
    edits = [
        path for rule in denied if (path := _edit_path(rule, ctx.home)) is not None
    ]
    blocked = [
        path
        for entry in _strings(settings, "sandbox", "filesystem", "denyWrite")
        if (path := _entry_path(entry, ctx.home)) is not None
    ]
    sandboxed = _value(settings, "sandbox", "enabled") is True
    warnings: list[str] = []
    if not sandboxed:
        warnings.append(
            f"account {account} leaves atc's directories writable to commands: "
            + "sandbox.enabled is not true"
        )
    for path, label in named.items():
        subject = f"account {account} leaves atc's {label} directory {path} writable"
        if sandboxed and not _covered(path, [*blocked, *edits]):
            warnings.append(
                f"{subject} to commands: neither a sandbox.filesystem.denyWrite "
                + "entry nor an Edit rule in permissions.deny covers it"
            )
        if "Edit" not in denied and not _covered(path, edits):
            warnings.append(
                f"{subject} to the file tools: "
                + "no Edit rule in permissions.deny covers it"
            )
    return tuple(warnings)


def _value(settings: JsonObject, *keys: str) -> JsonValue | None:
    """Give the value at ``keys`` in ``settings``, or ``None`` if there is none."""
    node: JsonValue = settings
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _strings(settings: JsonObject, *keys: str) -> list[str]:
    """Give the strings of the array at ``keys`` in ``settings``, if it is one."""
    node = _value(settings, *keys)
    if not isinstance(node, list):
        return []
    return [item for item in node if isinstance(item, str)]


def _edit_path(rule: str, home: Path) -> Path | None:
    """Give the directory an ``Edit`` rule names, with what is under it, if any."""
    if rule == "Edit(//**)":
        return Path("/")
    if not (rule.startswith("Edit(") and rule.endswith("/**)")):
        return None
    spec = rule.removeprefix("Edit(").removesuffix("/**)")
    if spec.startswith("//") or spec == "~" or spec.startswith("~/"):
        return _path(spec, home)
    return None


def _entry_path(entry: str, home: Path) -> Path | None:
    """Give the directory a ``denyWrite`` entry names, with what is under it."""
    if entry.endswith("/**"):
        return _path(entry.removesuffix("/**") or "/", home)
    return _path(entry, home)


def _path(entry: str, home: Path) -> Path | None:
    """Read ``entry`` as an absolute path, or one under ``home``, without wildcards."""
    if _WILDCARDS.intersection(entry):
        return None
    if entry == "~" or entry.startswith("~/"):
        return home / entry.removeprefix("~").lstrip("/")
    if entry.startswith("/"):
        return Path("/" + entry.lstrip("/"))
    return None


def _covered(path: Path, protected: list[Path]) -> bool:
    """Say whether ``path`` is one of ``protected`` or under one."""
    return any(path.is_relative_to(directory) for directory in protected)
