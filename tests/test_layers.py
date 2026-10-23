"""Building the account layer, and the policy on what a layer may set."""

from pathlib import Path

import pytest

from atc import layers
from atc.context import BuildContext
from atc.errors import PathError, PolicyError, TemplateError
from atc.jsonval import JsonObject
from atc.layers import Layer, build_account_layer, build_project_layer, check_policy
from tests.conftest import ContextBuilder
from tests.helpers import MINIMAL_REGISTRY, unit


def test_account_layer_merges_the_account_unit_and_its_imports(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(
                {"model": "opus"}, imports=["common/base.json"]
            ),
            "conf/common/base.json": unit({"model": "sonnet", "theme": "dark"}),
        }
    )

    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert layer.kind == "account"
    assert layer.settings == {"model": "opus", "theme": "dark"}
    assert layer.warnings == ()


def test_the_registry_can_name_another_account_unit(
    build_context: ContextBuilder,
) -> None:
    registry = """\
        version: 1
        default-account: personal
        accounts:
          personal: {agent: claude, dir: ~/.claude, unit: "@bravo/accounts/x.json"}
        """
    ctx = build_context(
        {
            "bravo/conf/accounts/x.json": unit({"model": "opus"}),
            "personal/conf/accounts/x.json": unit(),
        },
        registry,
    )

    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert layer.settings == {"model": "opus"}


def test_a_missing_account_unit_is_an_error(build_context: ContextBuilder) -> None:
    ctx = build_context({"conf/other.json": unit()})

    with pytest.raises(PathError, match=r"accounts/personal\.json.*no such unit"):
        _ = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)


def test_auto_memory_directory_is_refused_in_the_account_layer(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {"conf/accounts/personal.json": unit({"autoMemoryDirectory": "/m"})}
    )

    with pytest.raises(PolicyError) as caught:
        _ = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert str(caught.value) == (
        "accounts/personal.json: /autoMemoryDirectory is not allowed in the "
        "account layer"
    )


def test_policy_names_the_imported_unit_that_set_the_key(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(imports=["common/memory.json"]),
            "conf/common/memory.json": unit({"autoMemoryDirectory": "/m"}),
        }
    )

    with pytest.raises(PolicyError, match=r"^common/memory\.json: /autoMemory"):
        _ = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)


def test_policy_is_checked_before_unknown_keys(build_context: ContextBuilder) -> None:
    # Unknown keys are errors without `lax`, so checking them first would raise
    # a UnitError instead.
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(
                {"autoMemoryDirectory": "/m", "permisions": {}}
            )
        }
    )

    with pytest.raises(PolicyError):
        _ = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)


def test_lax_reports_unknown_keys_as_layer_warnings(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({"conf/accounts/personal.json": unit({"permisions": {}})})

    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=True)

    assert layer.settings == {"permisions": {}}
    assert len(layer.warnings) == 1
    assert "permisions" in layer.warnings[0]


def test_check_policy_refuses_a_key_only_in_the_layers_it_names() -> None:
    settings: JsonObject = {"autoMemoryDirectory": "/m"}

    with pytest.raises(PolicyError, match=r"x\.json: /autoMemoryDirectory"):
        check_policy(settings, "account", "x.json")
    check_policy(settings, "project", "x.json")
    check_policy(settings, "session", "x.json")
    check_policy({"model": "opus"}, "account", "x.json")


def test_a_policy_pointer_reaches_into_nested_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(layers, "POLICY", {"/a/b": frozenset({"project"})})

    check_policy({"a": {"c": 1}}, "project", "x.json")
    check_policy({"a": 1}, "project", "x.json")
    check_policy({"a": {"b": 1}}, "account", "x.json")
    with pytest.raises(PolicyError, match=r"x\.json: /a/b is not allowed"):
        check_policy({"a": {"b": 1}}, "project", "x.json")


def test_templates_are_expanded_with_the_account_in_each_unit(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(
                {"env": {"WHO": "{{account.name}}"}}, imports=["common/base.json"]
            ),
            "conf/common/base.json": unit({"env": {"DIR": "{{account.dir}}"}}),
        }
    )

    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert layer.settings == {"env": {"WHO": "personal", "DIR": str(home / ".claude")}}


def test_a_template_error_names_the_unit_the_pointer_and_the_layer(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {"conf/accounts/personal.json": unit({"permissions": {"allow": ["{{nope}}"]}})}
    )

    with pytest.raises(TemplateError) as caught:
        _ = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert str(caught.value) == (
        "accounts/personal.json (account layer for personal): "
        "/permissions/allow/0: unknown reference {{nope}}"
    )


def test_the_description_is_not_expanded(build_context: ContextBuilder) -> None:
    document: JsonObject = {
        "version": 2,
        "description": "{{nope",
        "settings": {"model": "x"},
    }
    ctx = build_context({"conf/accounts/personal.json": document})

    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert layer.settings == {"model": "x"}


PROJECT_REGISTRY = (
    MINIMAL_REGISTRY
    + "      work: {agent: claude, dir: ~/.config/work, vars: {who: me}}\n"
    + "    projects:\n      bar: {repo: /r, vars: {team: ci}}\n"
)


def build_project(ctx: BuildContext, account: str = "personal") -> Layer | None:
    return build_project_layer(
        ctx, ctx.registry.project("bar"), ctx.registry.account(account), lax=False
    )


def test_project_layer_merges_the_project_unit_for_the_account(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/projects/bar.json": unit(
                {
                    "env": {
                        "P": "{{project.name}}:{{project.repo}}:{{project.vars.team}}"
                    }
                },
                imports=["common/base.json"],
            ),
            "conf/common/base.json": unit({"env": {"A": "{{account.vars.who}}"}}),
        },
        PROJECT_REGISTRY,
    )

    layer = build_project(ctx, "work")

    assert layer is not None
    assert layer.kind == "project"
    assert layer.settings == {"env": {"A": "me", "P": "bar:/r:ci"}}


def test_project_layer_is_none_when_the_default_unit_is_absent(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({"conf/accounts/personal.json": unit()}, PROJECT_REGISTRY)

    assert build_project(ctx) is None


def test_a_missing_explicit_project_unit_is_an_error(
    build_context: ContextBuilder,
) -> None:
    registry = PROJECT_REGISTRY.replace("vars: {team: ci}", "unit: gone.json")
    ctx = build_context({"conf/accounts/personal.json": unit()}, registry)

    with pytest.raises(PathError, match=r"project 'bar': 'gone\.json': no such unit"):
        _ = build_project(ctx)


def test_allow_unsandboxed_commands_is_refused_in_the_project_layer(
    build_context: ContextBuilder,
) -> None:
    settings: JsonObject = {"sandbox": {"allowUnsandboxedCommands": True}}
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(settings),
            "conf/projects/bar.json": unit(settings),
        },
        PROJECT_REGISTRY,
    )

    with pytest.raises(PolicyError) as caught:
        _ = build_project(ctx)

    assert str(caught.value) == (
        "projects/bar.json: /sandbox/allowUnsandboxedCommands is not allowed in the "
        "project layer"
    )
    account = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)
    assert account.settings == settings


def test_allow_unsandboxed_commands_is_refused_in_the_session_layer() -> None:
    settings: JsonObject = {"sandbox": {"allowUnsandboxedCommands": True}}

    with pytest.raises(PolicyError, match="session layer"):
        check_policy(settings, "session", "x.json")
    check_policy(settings, "account", "x.json")


def test_project_variables_exist_only_in_the_project_layer(
    build_context: ContextBuilder,
) -> None:
    settings: JsonObject = {"model": "{{project.name}}"}
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(settings),
            "conf/projects/bar.json": unit(settings),
        },
        PROJECT_REGISTRY,
    )

    with pytest.raises(TemplateError, match=r"/model: unknown reference \{\{project"):
        _ = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)
    layer = build_project(ctx)
    assert layer is not None
    assert layer.settings == {"model": "bar"}


def test_a_project_template_error_names_the_project_and_the_account(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {"conf/projects/bar.json": unit({"model": "{{account.vars.who}}"})},
        PROJECT_REGISTRY,
    )

    with pytest.raises(TemplateError) as caught:
        _ = build_project(ctx)

    assert str(caught.value) == (
        "projects/bar.json (project layer for bar with account personal): "
        "/model: unknown reference {{account.vars.who}}"
    )
