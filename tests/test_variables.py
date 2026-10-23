"""The variables a unit's references can name."""

import dataclasses
import os
import sys
from pathlib import Path

import pytest

from atc.context import load_context
from atc.jsonval import JsonObject
from atc.layers import build_account_layer
from atc.sources import Source
from atc.variables import layer_variables, with_source
from tests.conftest import ContextBuilder
from tests.helpers import UID, unit

THREE_ACCOUNTS = """\
    version: 1
    default-account: personal
    vars: {scratch_root: /mnt/s}
    accounts:
      personal: {agent: claude, dir: ~/.claude}
      bravo:
        agent: claude
        dir: ~/.config/bravo/claude
        vars: {scratch: /mnt/s/bravo}
      work: {agent: claude, dir: ~/.config/work}
"""

ISOLATION_REGISTRY = """\
    version: 1
    default-account: personal
    accounts:
      personal: {agent: claude, dir: ~/.claude, unit: isolation.json}
      bravo: {agent: claude, dir: ~/.config/bravo/claude, unit: isolation.json}
"""

ISOLATION = unit(
    {
        "sandbox": {
            "credentials": {
                "files": [
                    {"path": "{{account.secrets}}", "mode": "deny"},
                    {"path": "{{account.dir}}/auth.token", "mode": "deny"},
                    {"path": "{{peers.dir}}", "mode": "deny"},
                    {"path": "{{peers.files}}", "mode": "deny"},
                ]
            }
        },
        "permissions": {
            "deny": [
                "Read(/{{peers.dir}}/**)",
                "Edit(/{{peers.dir}}/**)",
                "Read(/{{peers.files}})",
                "Edit(/{{peers.files}})",
            ]
        },
    }
)


def test_an_account_layer_has_every_variable_but_the_source_and_project_ones(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = build_context({"conf/a.json": unit()}, THREE_ACCOUNTS)
    bravo = home / ".config" / "bravo" / "claude"

    got = layer_variables(ctx, ctx.registry.account("bravo"))

    assert got == {
        "home": str(home),
        "atc.config_dir": str(ctx.dirs.config_dir),
        "atc.conf_d": str(ctx.dirs.conf_d),
        "atc.state_dir": str(ctx.dirs.state_dir),
        "atc.runtime_dir": str(ctx.dirs.runtime_dir),
        "atc.install_dir": str(ctx.install_dir),
        "vars.scratch_root": "/mnt/s",
        "account.name": "bravo",
        "account.agent": "claude",
        "account.dir": str(bravo),
        "account.files": (),
        "account.secrets": (
            str(bravo / ".credentials.json"),
            str(bravo / ".claude.json"),
        ),
        "account.vars.scratch": "/mnt/s/bravo",
        "peers.dir": (str(home / ".claude"), str(home / ".config" / "work")),
        "peers.files": (str(home / ".claude.json"),),
    }


def test_peers_are_every_other_account_of_any_agent(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = build_context({"conf/a.json": unit()}, THREE_ACCOUNTS)

    personal = layer_variables(ctx, ctx.registry.account("personal"))
    work = layer_variables(ctx, ctx.registry.account("work"))

    assert personal["peers.dir"] == (
        str(home / ".config" / "bravo" / "claude"),
        str(home / ".config" / "work"),
    )
    assert personal["peers.files"] == ()
    assert work["peers.dir"] == (
        str(home / ".claude"),
        str(home / ".config" / "bravo" / "claude"),
    )
    assert work["peers.files"] == (str(home / ".claude.json"),)


def test_with_source_adds_the_values_of_that_source() -> None:
    variables = {"home": "/h"}

    got = with_source(variables, Source("s", Path("/c/s")))

    assert got == {
        "home": "/h",
        "source.name": "s",
        "source.root": "/c/s",
        "source.conf": "/c/s/conf",
        "source.assets": "/c/s/assets",
    }
    assert variables == {"home": "/h"}


def test_source_variables_point_at_the_units_own_source(
    build_context: ContextBuilder,
) -> None:
    registry = """\
        version: 1
        default-account: a
        accounts:
          a: {agent: claude, dir: /a, unit: "@bravo/accounts/a.json"}
        """
    ctx = build_context(
        {
            "bravo/conf/accounts/a.json": unit(
                {"statusLine": {"command": "bash {{source.assets}}/s.sh"}},
                imports=["@other/common/x.json"],
            ),
            "other/conf/common/x.json": unit(
                {"env": {"NAME": "{{source.name}}", "ASSETS": "{{source.assets}}"}}
            ),
        },
        registry,
    )

    layer = build_account_layer(ctx, ctx.registry.account("a"), lax=False)

    bravo, other = ctx.sources.named("bravo"), ctx.sources.named("other")
    assert layer.settings == {
        "statusLine": {"command": f"bash {bravo.assets}/s.sh"},
        "env": {"NAME": "other", "ASSETS": str(other.assets)},
    }


def test_the_one_source_of_the_single_layout_has_no_name(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(
                {"env": {"NAME": "[{{source.name}}]", "ROOT": "{{source.root}}"}}
            )
        }
    )

    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert layer.settings == {"env": {"NAME": "[]", "ROOT": str(ctx.dirs.conf_d)}}


def test_named_source_variables_exist_only_in_the_multi_layout(
    build_context: ContextBuilder,
) -> None:
    multi = build_context(
        {"bravo/conf/a.json": unit(), "other/assets/f": "x"}, THREE_ACCOUNTS
    )
    bravo = multi.sources.named("bravo")

    got = layer_variables(multi, multi.registry.account("personal"))

    assert got["sources.bravo.root"] == str(bravo.root)
    assert got["sources.bravo.conf"] == str(bravo.conf)
    assert got["sources.bravo.assets"] == str(bravo.assets)
    assert got["sources.other.assets"] == str(multi.sources.named("other").assets)


def test_the_single_layout_has_no_named_sources(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({"conf/a.json": unit()})

    got = layer_variables(ctx, ctx.registry.account("personal"))

    assert not [name for name in got if name.startswith("sources.")]


def test_install_dir_comes_from_the_context(build_context: ContextBuilder) -> None:
    ctx = build_context({"conf/a.json": unit()})
    account = ctx.registry.account("personal")

    assert layer_variables(ctx, account)["atc.install_dir"] == sys.prefix
    moved = dataclasses.replace(ctx, install_dir=Path("/opt/atc"))
    assert layer_variables(moved, account)["atc.install_dir"] == "/opt/atc"


def test_atc_directories_follow_xdg(
    build_context: ContextBuilder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = build_context({"conf/a.json": unit()})
    account = ctx.registry.account("personal")

    got = layer_variables(ctx, account)

    assert got["atc.state_dir"] == str(tmp_path / "xdg" / "state" / "atc")
    assert got["atc.runtime_dir"] == str(tmp_path / "xdg" / "runtime" / "atc")

    monkeypatch.delenv("XDG_RUNTIME_DIR")
    fallback = load_context(env=os.environ, home=ctx.home, uid=UID, conf_d=None)
    assert layer_variables(fallback, account)["atc.runtime_dir"] == (
        f"/run/user/{UID}/atc"
    )


def test_the_isolation_unit_builds_the_bravo_entries(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = build_context({"conf/isolation.json": ISOLATION}, ISOLATION_REGISTRY)
    bravo = home / ".config" / "bravo" / "claude"

    layer = build_account_layer(ctx, ctx.registry.account("bravo"), lax=False)

    assert layer.settings == {
        "sandbox": {
            "credentials": {
                "files": _deny(
                    bravo / ".credentials.json",
                    bravo / ".claude.json",
                    bravo / "auth.token",
                    home / ".claude",
                    home / ".claude.json",
                )
            }
        },
        "permissions": {
            "deny": [
                f"Read(/{home / '.claude'}/**)",
                f"Edit(/{home / '.claude'}/**)",
                f"Read(/{home / '.claude.json'})",
                f"Edit(/{home / '.claude.json'})",
            ]
        },
    }


def test_the_isolation_unit_builds_the_personal_entries(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = build_context({"conf/isolation.json": ISOLATION}, ISOLATION_REGISTRY)
    bravo = home / ".config" / "bravo" / "claude"

    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert layer.settings == {
        "sandbox": {
            "credentials": {
                "files": _deny(
                    home / ".claude" / ".credentials.json",
                    home / ".claude.json",
                    home / ".claude" / "auth.token",
                    bravo,
                )
            }
        },
        "permissions": {"deny": [f"Read(/{bravo}/**)", f"Edit(/{bravo}/**)"]},
    }


def _deny(*paths: Path) -> list[JsonObject]:
    return [{"path": str(path), "mode": "deny"} for path in paths]


def test_a_project_adds_its_name_repo_and_variables(
    build_context: ContextBuilder,
) -> None:
    registry = THREE_ACCOUNTS + (
        "    projects:\n      bar: {repo: ~/src/bar, vars: {team: ci, ci: x}}\n"
    )
    ctx = build_context({"conf/a.json": unit()}, registry)
    account = ctx.registry.account("bravo")

    plain = layer_variables(ctx, account)
    got = layer_variables(ctx, account, ctx.registry.project("bar"))

    assert not [name for name in plain if name.startswith("project.")]
    assert got == {
        **plain,
        "project.name": "bar",
        "project.repo": str(ctx.home / "src" / "bar"),
        "project.vars.team": "ci",
        "project.vars.ci": "x",
    }
