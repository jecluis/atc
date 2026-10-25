"""Warnings about atc's own directories that an account leaves writable."""

import dataclasses
from pathlib import Path

import pytest

from atc.context import BuildContext
from atc.jsonval import JsonObject
from atc.layers import build_account_layer
from atc.paths import Dirs
from atc.protection import unprotected
from tests.conftest import ContextBuilder
from tests.helpers import PROTECTING, unit

ACCOUNT = "conf/accounts/personal.json"
PYTHON = Path("/opt/python")
COMMANDS = "neither a sandbox.filesystem.denyWrite entry nor an Edit rule in "
TOOLS = "no Edit rule in permissions.deny covers it"


def _sandboxed(**settings: JsonObject) -> JsonObject:
    """Give ``settings`` with the sandbox enabled, and their own ``sandbox`` keys."""
    sandbox: JsonObject = {"enabled": True, **settings.pop("sandbox", {})}
    return unit({"sandbox": sandbox, **settings})


def _warnings(ctx: BuildContext) -> tuple[str, ...]:
    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)
    return unprotected(ctx, "personal", layer.settings)


def _subject(label: str, path: Path) -> str:
    return f"account personal leaves atc's {label} directory {path} writable"


def _build(context: ContextBuilder, account: JsonObject) -> BuildContext:
    """Build the account, with a Python installation of its own."""
    return dataclasses.replace(context({ACCOUNT: account}), python_dir=PYTHON)


def test_an_account_that_protects_every_directory_gets_no_warning(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({ACCOUNT: unit(PROTECTING)})

    assert _warnings(ctx) == ()


def test_a_writable_directory_is_named_for_commands_and_for_the_file_tools(
    build_context: ContextBuilder,
) -> None:
    ctx = _build(build_context, _sandboxed())

    warnings = _warnings(ctx)

    state = _subject("state", ctx.dirs.state_dir)
    assert len(warnings) == 10
    assert warnings[2:4] == (
        f"{state} to commands: {COMMANDS}permissions.deny covers it",
        f"{state} to the file tools: {TOOLS}",
    )
    assert warnings[-1].startswith(_subject("Python", PYTHON))


def test_without_the_sandbox_nothing_keeps_commands_out(
    build_context: ContextBuilder,
) -> None:
    ctx = _build(build_context, unit())

    warnings = _warnings(ctx)

    assert warnings[0] == (
        "account personal leaves atc's directories writable to commands: "
        + "sandbox.enabled is not true"
    )
    assert len(warnings) == 6
    assert not [w for w in warnings[1:] if " to commands: " in w]


def test_a_directory_above_protects_the_ones_below(
    build_context: ContextBuilder, tmp_path: Path
) -> None:
    account = _sandboxed(
        sandbox={"filesystem": {"denyWrite": [str(tmp_path)]}},
        permissions={"deny": [f"Edit(/{tmp_path}/**)"]},
    )
    ctx = build_context({ACCOUNT: account})
    ctx = dataclasses.replace(
        ctx, install_dir=tmp_path / "py" / "env", python_dir=tmp_path / "py" / "base"
    )

    assert _warnings(ctx) == ()


@pytest.mark.parametrize("rule", ["Edit(~/.local/share/uv/**)", "Edit(~/**)"])
def test_paths_under_home_protect_what_is_there(
    build_context: ContextBuilder, home: Path, rule: str
) -> None:
    account = _sandboxed(
        sandbox={"filesystem": {"denyWrite": ["~/.local/share/uv"]}},
        permissions={"deny": [rule]},
    )
    ctx = build_context({ACCOUNT: account})
    uv = home / ".local" / "share" / "uv"
    ctx = dataclasses.replace(
        ctx, install_dir=uv / "tools" / "atc", python_dir=uv / "python" / "cpython"
    )

    warnings = _warnings(ctx)

    assert len(warnings) == 6
    assert not [w for w in warnings if str(uv) in w]


def test_an_edit_rule_with_a_path_keeps_commands_out_as_well(
    build_context: ContextBuilder, tmp_path: Path
) -> None:
    account = _sandboxed(permissions={"deny": [f"Edit(/{tmp_path}/**)"]})
    ctx = build_context({ACCOUNT: account})
    ctx = dataclasses.replace(
        ctx, install_dir=tmp_path / "py" / "env", python_dir=tmp_path / "py" / "base"
    )

    assert _warnings(ctx) == ()


def test_deny_write_entries_may_start_with_two_slashes_or_end_in_a_glob(
    build_context: ContextBuilder, tmp_path: Path
) -> None:
    account = _sandboxed(sandbox={"filesystem": {"denyWrite": [f"/{tmp_path}/**"]}})
    ctx = build_context({ACCOUNT: account})
    ctx = dataclasses.replace(
        ctx, install_dir=tmp_path / "py" / "env", python_dir=tmp_path / "py" / "base"
    )

    warnings = _warnings(ctx)

    assert len(warnings) == 5
    assert all(w.endswith(f"to the file tools: {TOOLS}") for w in warnings)


def test_a_bare_edit_deny_keeps_out_the_file_tools_but_not_commands(
    build_context: ContextBuilder,
) -> None:
    ctx = _build(build_context, _sandboxed(permissions={"deny": ["Edit"]}))

    warnings = _warnings(ctx)

    assert len(warnings) == 5
    assert all(" to commands: " in w for w in warnings)


def test_an_edit_deny_for_every_absolute_path_protects_everything(
    build_context: ContextBuilder,
) -> None:
    ctx = _build(build_context, _sandboxed(permissions={"deny": ["Edit(//**)"]}))

    assert _warnings(ctx) == ()


def test_relative_wildcard_or_unglobbed_rules_and_allows_do_not_protect(
    build_context: ContextBuilder, dirs: Dirs, tmp_path: Path
) -> None:
    state = dirs.state_dir
    account = _sandboxed(
        sandbox={"filesystem": {"denyWrite": [f"{tmp_path}/*", ""]}},
        permissions={
            "deny": [
                f"Edit({state}/**)",
                f"Edit(/{tmp_path}/*/state/atc/**)",
                f"Edit(/{state})",
            ],
            "allow": [f"Edit(/{state}/**)"],
        },
    )
    ctx = build_context({ACCOUNT: account})

    warnings = _warnings(ctx)

    subject = _subject("state", state)
    assert f"{subject} to commands: {COMMANDS}permissions.deny covers it" in warnings
    assert f"{subject} to the file tools: {TOOLS}" in warnings


def test_a_python_that_is_atc_s_own_environment_is_named_once(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({ACCOUNT: _sandboxed()})
    ctx = dataclasses.replace(ctx, python_dir=ctx.install_dir)

    warnings = _warnings(ctx)

    assert len(warnings) == 8
    assert not [w for w in warnings if "Python" in w]
