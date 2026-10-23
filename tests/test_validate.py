"""Checking every unit in every source."""

from pathlib import Path

from atc.jsonval import JsonObject
from atc.sources import discover
from atc.validate import validate_config, validate_sources
from tests.conftest import ContextBuilder
from tests.helpers import UID, make_source, registry, skill_md, unit


def test_every_error_is_collected(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d",
        {
            "conf/good.json": unit(),
            "conf/dangling.json": unit(imports=["gone.json"]),
            "conf/unknown.json": unit({"permisions": {}}),
        },
    )

    result = validate_sources(discover(conf_d, uid=UID), lax=False)

    assert result.units == 3
    assert len(result.errors) == 2
    assert result.warnings == ()


def test_lax_turns_unknown_settings_keys_into_warnings(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d", {"conf/unknown.json": unit({"permisions": {}})}
    )

    result = validate_sources(discover(conf_d, uid=UID), lax=True)

    assert result.errors == ()
    assert len(result.warnings) == 1
    assert "permisions" in result.warnings[0]


def test_a_warning_shared_by_several_units_is_given_once(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d",
        {
            "conf/base.json": unit({"permisions": {}}),
            "conf/one.json": unit(imports=["base.json"]),
            "conf/two.json": unit(imports=["base.json"]),
        },
    )

    result = validate_sources(discover(conf_d, uid=UID), lax=True)

    assert len(result.warnings) == 1


def test_discovery_warnings_come_first(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit({"permisions": {}})})
    _ = make_source(conf_d / "stray", {"notes.md": "x"})

    result = validate_sources(discover(conf_d, uid=UID), lax=True)

    assert "stray" in result.warnings[0]
    assert "permisions" in result.warnings[1]


def test_a_merge_conflict_inside_one_unit_tree_is_an_error(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d",
        {
            "conf/base.json": unit({"permissions": {}}),
            "conf/top.json": unit({"permissions": []}, imports=["base.json"]),
        },
    )

    result = validate_sources(discover(conf_d, uid=UID), lax=False)

    assert len(result.errors) == 1
    assert "conflicting types" in result.errors[0]


def test_a_unit_in_a_directory_starting_with_at_is_checked(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/@odd/a.json": unit()})

    result = validate_sources(discover(conf_d, uid=UID), lax=False)

    assert (result.units, result.errors) == (1, ())


def test_a_unit_that_leads_out_of_conf_is_an_error(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    sources = discover(conf_d, uid=UID)
    outside = tmp_path / "outside.json"
    _ = outside.write_text("{}", encoding="utf-8")
    (conf_d / "conf" / "link.json").symlink_to(outside)

    result = validate_sources(sources, lax=False)

    assert result.units == 2
    assert len(result.errors) == 1
    assert "resolves outside" in result.errors[0]


TWO_ACCOUNTS = """\
    version: 1
    default-account: personal
    accounts:
      personal: {agent: claude, dir: ~/.claude}
      work: {agent: claude, dir: ~/.config/work, unit: profiles/work.json}
"""


def test_validate_config_reports_every_account_that_fails(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {"conf/accounts/personal.json": unit({"autoMemoryDirectory": "/m"})},
        TWO_ACCOUNTS,
    )

    result = validate_config(ctx, lax=False)

    assert len(result.errors) == 2
    assert "accounts/personal.json: /autoMemoryDirectory" in result.errors[0]
    assert "account 'work'" in result.errors[1]
    assert "profiles/work.json" in result.errors[1]


def test_validate_config_keeps_the_errors_of_the_sources(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(),
            "conf/profiles/work.json": unit(),
            "conf/stray.json": unit(imports=["gone.json"]),
        },
        TWO_ACCOUNTS,
    )

    result = validate_config(ctx, lax=False)

    assert result.units == 3
    assert len(result.errors) == 1
    assert "gone.json" in result.errors[0]


def test_validate_config_gives_a_problem_in_both_checks_once(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({"conf/accounts/personal.json": unit(imports=["gone.json"])})

    result = validate_config(ctx, lax=False)

    assert len(result.errors) == 1


def test_validate_config_reports_layer_warnings_once(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({"conf/accounts/personal.json": unit({"permisions": {}})})

    result = validate_config(ctx, lax=True)

    assert result.errors == ()
    assert len(result.warnings) == 1


def test_validate_config_reports_a_template_error_in_an_account_layer(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {"conf/accounts/personal.json": unit({"model": "{{nope}}"})},
    )

    result = validate_config(ctx, lax=False)

    assert result.units == 1
    assert len(result.errors) == 1
    assert "account layer for personal" in result.errors[0]
    assert "unknown reference {{nope}}" in result.errors[0]


WITH_PROJECTS = (
    TWO_ACCOUNTS
    + "    projects:\n"
    + "      both: {repo: /b}\n"
    + "      only-work: {repo: /w, account: work, accounts: [work]}\n"
)


def test_validate_config_builds_a_project_for_every_account_it_allows(
    build_context: ContextBuilder,
) -> None:
    # `who` is defined by the work account only.
    registry = WITH_PROJECTS.replace(
        "unit: profiles/work.json", "unit: profiles/work.json, vars: {who: w}"
    )
    uses_who = unit({"model": "{{account.vars.who}}"})
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(),
            "conf/profiles/work.json": unit(),
            "conf/projects/both.json": uses_who,
            "conf/projects/only-work.json": uses_who,
        },
        registry,
    )

    result = validate_config(ctx, lax=False)

    assert len(result.errors) == 1
    assert "project layer for both with account personal" in result.errors[0]


def test_validate_config_skips_a_project_without_a_unit(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(),
            "conf/profiles/work.json": unit(),
        },
        WITH_PROJECTS,
    )

    assert validate_config(ctx, lax=False).errors == ()


def test_validate_config_reports_a_missing_explicit_project_unit_once(
    build_context: ContextBuilder,
) -> None:
    registry = WITH_PROJECTS.replace(
        "both: {repo: /b}", "both: {repo: /b, unit: x.json}"
    )
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(),
            "conf/profiles/work.json": unit(),
        },
        registry,
    )

    result = validate_config(ctx, lax=False)

    assert len(result.errors) == 1
    assert "project 'both'" in result.errors[0]


REBASE = "Bash(git rebase:*)"


def rules(**lists: list[str]) -> JsonObject:
    return unit({"permissions": {name: [*rule] for name, rule in lists.items()}})


def test_validate_config_reports_a_dead_rule_once_however_many_layers_see_it(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": rules(allow=[REBASE], deny=[REBASE]),
            "conf/profiles/work.json": unit(),
            "conf/projects/both.json": unit(),
            "conf/projects/only-work.json": unit(),
        },
        WITH_PROJECTS,
    )

    result = validate_config(ctx, lax=False)

    assert result.errors == ()
    assert result.warnings == (
        "allow Bash(git rebase:*) (accounts/personal.json) never applies: "
        + "deny Bash(git rebase:*) (accounts/personal.json) takes precedence",
    )


def test_validate_config_checks_a_project_against_each_account_it_allows(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": rules(deny=[REBASE]),
            "conf/profiles/work.json": unit(),
            "conf/projects/both.json": rules(allow=[REBASE]),
            "conf/projects/only-work.json": rules(allow=[REBASE]),
        },
        WITH_PROJECTS,
    )

    result = validate_config(ctx, lax=False)

    assert result.errors == ()
    assert result.warnings == (
        "allow Bash(git rebase:*) (projects/both.json) never applies: "
        + "deny Bash(git rebase:*) (accounts/personal.json) takes precedence",
    )


def test_validate_config_checks_a_project_alone_when_its_account_layer_fails(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit({"autoMemoryDirectory": "/m"}),
            "conf/profiles/work.json": unit(),
            "conf/projects/both.json": rules(allow=[REBASE], deny=[REBASE]),
        },
        WITH_PROJECTS,
    )

    result = validate_config(ctx, lax=False)

    assert len(result.errors) == 1
    assert result.warnings == (
        "allow Bash(git rebase:*) (projects/both.json) never applies: "
        + "deny Bash(git rebase:*) (projects/both.json) takes precedence",
    )


def test_validate_config_resolves_every_selected_skill(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(),
            "skills/review/SKILL.md": skill_md("review"),
        },
        registry("skills: [review, ghost]"),
    )

    result = validate_config(ctx, lax=False)

    assert [error.split(": ", 1)[1] for error in result.errors] == [
        "account 'personal': skills: 'ghost': no such skill"
    ]


def test_validate_config_reports_two_selected_skills_with_one_name(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "personal/conf/accounts/personal.json": unit(),
            "personal/skills/review/SKILL.md": skill_md("review"),
            "bravo/skills/review/SKILL.md": skill_md("review"),
        },
        registry('skills: ["@personal/review", "@bravo/review"]'),
    )

    result = validate_config(ctx, lax=False)

    (error,) = result.errors
    assert "@personal/review and @bravo/review are both named 'review'" in error


def test_validate_config_warns_about_a_skill_with_odd_front_matter(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "conf/accounts/personal.json": unit(),
            "skills/review/SKILL.md": skill_md("code-review"),
        },
        registry("skills: [review]"),
    )

    result = validate_config(ctx, lax=False)

    assert result.errors == ()
    (warning,) = result.warnings
    assert "'code-review' differs from the directory name 'review'" in warning
