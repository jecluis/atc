"""Warnings about permission rules that another rule always overrides."""

import pytest

from atc.jsonval import JsonObject
from atc.layers import Layer, build_account_layer, build_project_layer
from atc.rules import covers, dead_rules
from tests.conftest import ContextBuilder
from tests.helpers import MINIMAL_REGISTRY, unit

REBASE = "Bash(git rebase:*)"
PERSONAL = "conf/accounts/personal.json"


@pytest.mark.parametrize(
    ("by", "rule", "expected"),
    [
        ("Bash(git rebase:*)", "Bash(git rebase:*)", True),
        ("Bash(git *)", "Bash(git rebase:*)", True),
        ("Bash(rm:*)", "Bash(rm -rf target:*)", True),
        ("Bash", "Bash(ls)", True),
        ("Read(.env)", "Read(.env)", True),
        ("Bash(git rebase -i:*)", "Bash(git rebase:*)", False),
        ("Bash(ls)", "Bash", False),
        ("Read(*.env)", "Read(.env)", False),
        ("Edit(x)", "Read(x)", False),
        ("Bash(git * push*)", "Bash(git push:*)", False),
        ("Bash(SQLX_* cargo build:*)", "Bash(SQLX_OFFLINE=1 cargo build -q)", False),
        ("Bash(SQLX_* cargo build:*)", "Bash(SQLX_* cargo build --release)", False),
        ("Bash(SQLX_* cargo build:*)", "Bash(SQLX_* cargo build -q:*)", True),
        ("Bash(git *:*)", "Bash(git * push)", False),
        ("Bash(SQLX_* cargo build *)", "Bash(SQLX_OFFLINE=1 cargo build -q)", True),
    ],
)
def test_covers(by: str, rule: str, expected: bool) -> None:
    assert covers(by, rule) is expected


@pytest.mark.parametrize(
    ("by", "rule", "expected"),
    [
        ("Bash(git:*)", "Bash(git rebase *)", True),
        ("Bash(git *)", "Bash(git rebase*)", True),
        ("Bash(git*)", "Bash(git rebase:*)", True),
        ("Bash(git a*c)", "Bash(git abc)", True),
        ("Bash(git *)", "Bash(git commit -m 'a b\nc':*)", True),
        ("Bash(git rebase *)", "Bash(git rebase*)", False),
        ("Bash(git rebase)", "Bash(git rebase:*)", False),
        ("Bash(echo a.c)", "Bash(echo abc)", False),
        ("Bash(a(b))", "Bash(a(b))", True),
        ("mcp__srv-1__tool", "mcp__srv-1__tool(x)", True),
        ("Bash(", "Bash(", False),
        ("(x)", "(x)", False),
    ],
)
def test_covers_reads_only_a_star_as_special(
    by: str, rule: str, expected: bool
) -> None:
    assert covers(by, rule) is expected


def permissions(**lists: list[str]) -> JsonObject:
    return {"permissions": {name: [*rules] for name, rules in lists.items()}}


def test_an_allow_covered_by_a_deny_across_layers_names_both_units(
    build_context: ContextBuilder,
) -> None:
    registry = MINIMAL_REGISTRY + "    projects:\n      bar: {repo: /r}\n"
    ctx = build_context(
        {
            PERSONAL: unit(imports=["common/base.json"]),
            "conf/common/base.json": unit(permissions(deny=[REBASE])),
            "conf/projects/bar.json": unit(permissions(allow=[REBASE])),
        },
        registry,
    )
    personal = ctx.registry.account("personal")
    account = build_account_layer(ctx, personal, lax=False)
    project = build_project_layer(ctx, ctx.registry.project("bar"), personal, lax=False)
    assert project is not None

    warnings = dead_rules([account, project])

    assert [w.message() for w in warnings] == [
        "allow Bash(git rebase:*) (projects/bar.json) never applies: "
        + "deny Bash(git rebase:*) (common/base.json) takes precedence"
    ]
    assert dead_rules([account]) == ()
    assert dead_rules([project]) == ()


def test_a_pattern_in_one_layer_covers_a_narrower_rule_in_another(
    build_context: ContextBuilder,
) -> None:
    registry = MINIMAL_REGISTRY + "    projects:\n      bar: {repo: /r}\n"
    ctx = build_context(
        {
            PERSONAL: unit(permissions(deny=["Bash(git *)"])),
            "conf/projects/bar.json": unit(permissions(allow=[REBASE])),
        },
        registry,
    )
    personal = ctx.registry.account("personal")
    account = build_account_layer(ctx, personal, lax=False)
    project = build_project_layer(ctx, ctx.registry.project("bar"), personal, lax=False)
    assert project is not None

    assert [w.message() for w in dead_rules([account, project])] == [
        "allow Bash(git rebase:*) (projects/bar.json) never applies: "
        + "deny Bash(git *) (accounts/personal.json) takes precedence"
    ]


def test_an_ask_covered_by_a_deny(build_context: ContextBuilder) -> None:
    ctx = build_context(
        {PERSONAL: unit(permissions(ask=["Read(.env)"], deny=["Read(.env)"]))}
    )
    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert [w.message() for w in dead_rules([layer])] == [
        "ask Read(.env) (accounts/personal.json) never applies: "
        + "deny Read(.env) (accounts/personal.json) takes precedence"
    ]


def test_an_allow_covered_by_an_ask(build_context: ContextBuilder) -> None:
    ctx = build_context(
        {PERSONAL: unit(permissions(allow=["Bash(ls -la)"], ask=["Bash(ls:*)"]))}
    )
    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert [w.message() for w in dead_rules([layer])] == [
        "allow Bash(ls -la) (accounts/personal.json) never applies: "
        + "ask Bash(ls:*) (accounts/personal.json) takes precedence"
    ]


def test_a_partial_overlap_is_not_reported(build_context: ContextBuilder) -> None:
    ctx = build_context(
        {PERSONAL: unit(permissions(allow=[REBASE], deny=["Bash(git rebase -i:*)"]))}
    )
    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert dead_rules([layer]) == ()


def test_an_allow_or_ask_never_makes_a_stronger_rule_dead(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            PERSONAL: unit(
                permissions(
                    allow=["Bash"], ask=["Bash(rm:*)", "Read"], deny=["Bash(rm -rf:*)"]
                )
            )
        }
    )
    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert dead_rules([layer]) == ()


def test_a_rule_is_reported_once_with_the_strongest_cover(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            PERSONAL: unit(
                permissions(
                    allow=["Bash(git push:*)"],
                    ask=["Bash(git push:*)", "Bash(git *)"],
                    deny=["Bash(git *)", "Bash(git push:*)"],
                )
            )
        }
    )
    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    warnings = dead_rules([layer])

    assert [(w.dead_list, w.dead_rule, w.by_list, w.by_rule) for w in warnings] == [
        ("allow", "Bash(git push:*)", "deny", "Bash(git *)"),
        ("ask", "Bash(git push:*)", "deny", "Bash(git *)"),
        ("ask", "Bash(git *)", "deny", "Bash(git *)"),
    ]


def test_an_ask_is_the_cover_when_no_deny_covers(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            PERSONAL: unit(
                permissions(
                    allow=["Bash(git push:*)"],
                    ask=["Bash(git *)"],
                    deny=["Bash(git push -f:*)"],
                )
            )
        }
    )
    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    [warning] = dead_rules([layer])

    assert (warning.by_list, warning.by_rule) == ("ask", "Bash(git *)")


def test_a_cover_given_by_several_units_lists_them_all(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            PERSONAL: unit(permissions(allow=[REBASE]), imports=["a.json", "b.json"]),
            "conf/a.json": unit(permissions(deny=[REBASE])),
            "conf/b.json": unit(permissions(deny=[REBASE, "Bash(git:*)"])),
        }
    )
    layer = build_account_layer(ctx, ctx.registry.account("personal"), lax=False)

    assert [w.message() for w in dead_rules([layer])] == [
        "allow Bash(git rebase:*) (accounts/personal.json) never applies: "
        + "deny Bash(git rebase:*) (a.json, b.json) takes precedence"
    ]


def test_a_rule_given_in_both_layers_names_each_unit_once() -> None:
    account = Layer(
        "account",
        {},
        (),
        {("allow", REBASE): ("common/base.json",), ("deny", REBASE): ("deny.json",)},
    )
    project = Layer(
        "project", {}, (), {("allow", REBASE): ("common/base.json", "projects/x.json")}
    )

    [warning] = dead_rules([account, project])

    assert warning.dead_origins == ("common/base.json", "projects/x.json")
    assert warning.by_origins == ("deny.json",)


def test_rules_in_lists_other_than_the_three_are_ignored() -> None:
    layer = Layer("account", {}, (), {("other", REBASE): ("x.json",)})

    assert dead_rules([layer]) == ()
