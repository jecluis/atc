"""Templated assets: which files they are, and what rendering one gives."""

import pytest

from atc.errors import ConfigError, TemplateError
from atc.refs import resolve_registry
from atc.rendered import render_templates, rendered_copy, rendered_name
from tests.conftest import ContextBuilder
from tests.helpers import registry, unit

PERSONAL = "conf/accounts/personal.json"
SCRATCH = registry("vars: {scratch: /mnt/s/p}")


@pytest.mark.parametrize(
    ("name", "copy"),
    [
        ("clean.in.sh", "clean.sh"),
        ("AGENTS.in.md", "AGENTS.md"),
        ("motd.in", "motd"),
        ("a.b.in.sh", "a.b.sh"),
        (".hidden.in.sh", ".hidden.sh"),
    ],
)
def test_a_template_is_named_in_before_its_extension(name: str, copy: str) -> None:
    assert rendered_name(name) == copy


@pytest.mark.parametrize(
    "name",
    ["clean.sh", "install.sh", "plugin.ini", "x.inc", "x.in.tar.gz", ".in", "..in"],
)
def test_other_names_are_not_templates(name: str) -> None:
    assert rendered_name(name) is None


def test_a_template_is_rendered_with_the_accounts_variables(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            PERSONAL: unit(),
            "assets/bin/clean.in.sh": 'ROOT="{{account.vars.scratch}}"\n',
            "assets/bin/other.sh": "{{not a template}}\n",
        },
        SCRATCH,
    )
    (ctx.dirs.conf_d / "assets" / "bin" / "clean.in.sh").chmod(0o755)

    (rendering,) = render_templates(ctx, ctx.registry.account("personal"))

    rendered = ctx.dirs.state_dir / "sync" / "personal" / "assets"
    assert rendering.path == rendered / "bin" / "clean.sh"
    assert rendering.text == 'ROOT="/mnt/s/p"\n'
    assert rendering.mode == 0o700
    assert rendering.template.display == "bin/clean.in.sh"


def test_a_template_nobody_can_run_gives_a_private_copy(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({PERSONAL: unit(), "assets/AGENTS.in.md": "rules\n"})

    (rendering,) = render_templates(ctx, ctx.registry.account("personal"))

    assert rendering.mode == 0o600


def test_each_source_renders_under_its_own_name_with_its_own_variables(
    build_context: ContextBuilder,
) -> None:
    text = "{{source.name}} {{source.assets}} {{source.rendered}}\n"
    ctx = build_context(
        {"bravo/conf/a.json": unit(), "bravo/assets/x.in": text},
        "version: 1\ndefault-account: a\naccounts:\n  a: {agent: claude, dir: /a}\n",
    )

    (rendering,) = render_templates(ctx, ctx.registry.account("a"))

    bravo = ctx.sources.named("bravo")
    rendered = ctx.dirs.state_dir / "sync" / "a" / "assets" / "bravo"
    assert rendering.path == rendered / "x"
    assert rendering.text == f"bravo {bravo.assets} {rendered}\n"


def test_a_template_has_no_project_variables(build_context: ContextBuilder) -> None:
    ctx = build_context({PERSONAL: unit(), "assets/x.in.sh": "\n{{project.repo}}\n"})

    with pytest.raises(TemplateError) as raised:
        _ = render_templates(ctx, ctx.registry.account("personal"))

    message = str(raised.value)
    assert message.startswith("x.in.sh (personal): line 2: ")
    assert "unknown reference {{project.repo}}" in message


def test_a_template_that_is_not_utf8_is_a_config_error(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({PERSONAL: unit(), "assets/x.in": "x"})
    _ = (ctx.dirs.conf_d / "assets" / "x.in").write_bytes(b"\xff\xfe")

    with pytest.raises(ConfigError, match=r"x\.in: a template must be UTF-8 text"):
        _ = render_templates(ctx, ctx.registry.account("personal"))


def test_without_templates_nothing_is_rendered(build_context: ContextBuilder) -> None:
    ctx = build_context({PERSONAL: unit(), "assets/statusline.sh": "x\n"})

    assert render_templates(ctx, ctx.registry.account("personal")) == ()


def test_the_copy_of_a_template_asset_is_where_it_is_rendered(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {PERSONAL: unit(), "assets/a/AGENTS.in.md": "x\n", "assets/plain.md": "y\n"}
    )
    personal = ctx.registry.account("personal")
    template = resolve_registry(ctx.sources, "a/AGENTS.in.md", "asset")
    plain = resolve_registry(ctx.sources, "plain.md", "asset")
    assert template is not None
    assert plain is not None

    copy = rendered_copy(ctx, personal, template)

    (rendering,) = render_templates(ctx, personal)
    assert copy == rendering.path
    assert copy == ctx.dirs.state_dir / "sync" / "personal" / "assets/a/AGENTS.md"
    assert rendered_copy(ctx, personal, plain) is None


@pytest.mark.parametrize(
    ("first", "second", "message"),
    [
        ("a.in.sh", "a.sh.in", r"a\.in\.sh and a\.sh\.in both render as .*/a\.sh$"),
        ("bin.in", "bin/x.in.sh", r"bin\.in renders as .*/bin, where bin/x\.in\.sh"),
    ],
)
def test_two_templates_that_need_one_path_are_a_config_error(
    build_context: ContextBuilder, first: str, second: str, message: str
) -> None:
    ctx = build_context(
        {PERSONAL: unit(), f"assets/{first}": "1\n", f"assets/{second}": "2\n"}
    )

    with pytest.raises(ConfigError, match=message):
        _ = render_templates(ctx, ctx.registry.account("personal"))
