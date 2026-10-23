"""Command line interface for atc."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from atc.errors import ConfigError
from atc.jsonval import dumps
from atc.paths import user_config_root
from atc.resolve import resolve

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_USAGE_ERROR = 2

PROGRAM = "atc"


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for every atc command."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Compose a Claude Code settings.local.json from reusable "
            "configuration units."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    build = commands.add_parser(
        "build",
        help="merge configuration units and print the result",
        description=(
            "Merge the named configuration units, together with everything "
            "they import, and write the resulting settings.local.json to "
            "standard output."
        ),
    )
    _ = build.add_argument(
        "units",
        nargs="+",
        metavar="UNIT",
        help=(
            "unit to merge, as a path relative to the configuration root, "
            "for example langs/rust.json. Units are merged left to right, "
            "except that a unit an earlier one already imported keeps the "
            "position it had."
        ),
    )
    _ = build.add_argument(
        "--root",
        metavar="DIR",
        help=(
            "read units from DIR instead of the installed configuration root, "
            f"which is {user_config_root()}"
        ),
    )
    _ = build.add_argument(
        "--lax",
        action="store_true",
        help=(
            "report settings keys Claude Code does not define as warnings "
            "rather than errors"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run atc and return the process exit status."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    match _text(arguments, "command"):
        case "build":
            return _run_build(arguments)
        case _:
            return EXIT_USAGE_ERROR


def _run_build(arguments: argparse.Namespace) -> int:
    """Resolve the requested units and print the merged settings."""
    override = _text(arguments, "root")
    if override is not None and not override:
        _fail("--root requires a directory")
        return EXIT_USAGE_ERROR

    root = Path(override) if override else user_config_root()
    if not root.is_dir():
        _fail(f"no configuration root at {root}")
        return EXIT_USAGE_ERROR

    try:
        resolution = resolve(
            root,
            _strings(arguments, "units"),
            lax=_flag(arguments, "lax"),
        )
    except ConfigError as exc:
        _fail(str(exc))
        return EXIT_CONFIG_ERROR

    for warning in resolution.warnings:
        _fail(f"warning: {warning}")
    _ = sys.stdout.write(dumps(resolution.settings))
    return EXIT_OK


def _fail(message: str) -> None:
    """Write a diagnostic to standard error, keeping standard output clean."""
    print(f"{PROGRAM}: {message}", file=sys.stderr)


def _raw(namespace: argparse.Namespace, name: str) -> object:
    """
    Read a parsed argument as an opaque value.

    argparse types every attribute as ``Any``; funnelling access through here
    keeps that to one place, and callers narrow to the type they expect.
    """
    return getattr(namespace, name)  # pyright: ignore[reportAny]


def _text(namespace: argparse.Namespace, name: str) -> str | None:
    """Read a string argument, or ``None`` when it was not supplied."""
    value = _raw(namespace, name)
    return value if isinstance(value, str) else None


def _flag(namespace: argparse.Namespace, name: str) -> bool:
    """Read a boolean flag."""
    return _raw(namespace, name) is True


def _strings(namespace: argparse.Namespace, name: str) -> tuple[str, ...]:
    """Read a repeated string argument."""
    value = _raw(namespace, name)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in cast(list[object], value) if isinstance(item, str))
