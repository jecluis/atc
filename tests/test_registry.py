"""The machine registry: what it accepts, and what it refuses."""

import stat
import textwrap
from pathlib import Path

import pytest

from atc.errors import PathError, RegistryError
from atc.registry import (
    REGISTRY_FILE,
    account_unit,
    choose_account,
    load_registry,
    normalize_path,
    parse_registry,
    set_skills,
)
from atc.sources import discover
from tests.conftest import RegistryWriter
from tests.helpers import MINIMAL_REGISTRY, UID, make_source, unit


def test_minimal_registry_loads(write_registry: RegistryWriter, home: Path) -> None:
    path = write_registry(MINIMAL_REGISTRY)

    r = load_registry(path, home)

    assert r.path == path
    assert r.warnings == ()
    assert r.accounts["personal"].dir == home / ".claude"
    assert r.accounts["personal"].unit == "accounts/personal.json"


def test_an_account_can_name_its_unit(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = """\
        version: 1
        default-account: bravo
        accounts:
          bravo: {agent: claude, dir: ~/.config/bravo, unit: "@bravo/accounts/x.json"}
        """

    registry = load_registry(write_registry(text), home)

    assert registry.accounts["bravo"].unit == "@bravo/accounts/x.json"


def test_accounts_keep_the_order_of_the_file(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = """\
        version: 1
        default-account: zed
        accounts:
          zed: {agent: claude, dir: /z}
          alpha: {agent: claude, dir: /a}
          mid: {agent: claude, dir: /m}
        """

    registry = load_registry(write_registry(text), home)

    assert list(registry.accounts) == ["zed", "alpha", "mid"]


def test_two_accounts_cannot_share_a_dir_however_it_is_written(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = MINIMAL_REGISTRY + "      other: {agent: claude, dir: ~/.claude/}\n"
    with pytest.raises(RegistryError, match="'personal' and 'other' share"):
        _ = load_registry(write_registry(text), home)


def test_a_symlinked_prefix_counts_as_the_same_dir(
    write_registry: RegistryWriter, home: Path, tmp_path: Path
) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    text = f"""\
        version: 1
        default-account: a
        accounts:
          a: {{agent: claude, dir: {tmp_path}/real/x}}
          b: {{agent: claude, dir: {tmp_path}/link/x}}
        """

    with pytest.raises(RegistryError, match="share"):
        _ = load_registry(write_registry(text), home)


def test_duplicate_keys_are_refused(write_registry: RegistryWriter, home: Path) -> None:
    text = MINIMAL_REGISTRY + "      personal: {agent: claude, dir: ~/.other}\n"
    with pytest.raises(RegistryError, match="duplicate key"):
        _ = load_registry(write_registry(text), home)


def test_non_string_keys_are_refused(
    write_registry: RegistryWriter, home: Path
) -> None:
    with pytest.raises(RegistryError, match="not a string"):
        _ = load_registry(write_registry(MINIMAL_REGISTRY + "    1: x\n"), home)


def test_misspelled_keys_are_refused(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = "version: 1\ndefault-account: a\nacounts: {a: {agent: claude, dir: /a}}\n"
    with pytest.raises(RegistryError, match="/acounts"):
        _ = load_registry(write_registry(text), home)


def test_an_unknown_account_key_is_refused_with_its_place(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = (
        "version: 1\ndefault-account: personal\n"
        "accounts: {personal: {agent: claude, dir: /a, unti: x}}\n"
    )

    with pytest.raises(RegistryError, match="/accounts/personal/unti"):
        _ = load_registry(write_registry(text), home)


def test_unknown_top_level_keys_are_refused(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = MINIMAL_REGISTRY + "    bogus: x\n"

    with pytest.raises(RegistryError, match="/bogus"):
        _ = load_registry(write_registry(text), home)


@pytest.mark.parametrize("version", ["2", "0", "true", "1.0", '"1"'])
def test_only_the_integer_1_is_a_version(
    write_registry: RegistryWriter, home: Path, version: str
) -> None:
    text = (
        f"version: {version}\ndefault-account: a\n"
        "accounts: {a: {agent: claude, dir: /a}}\n"
    )

    with pytest.raises(RegistryError, match="/version"):
        _ = load_registry(write_registry(text), home)


def test_required_keys_are_required(write_registry: RegistryWriter, home: Path) -> None:
    for text in ("accounts: {}\n", "version: 1\n", "version: 1\naccounts: {a: {}}\n"):
        with pytest.raises(RegistryError, match="Field required"):
            _ = load_registry(write_registry(text), home)


def test_values_are_not_converted_to_the_expected_type(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = (
        "version: 1\ndefault-account: a\n"
        "accounts: {a: {agent: claude, dir: /a, unit: 5}}\n"
    )

    with pytest.raises(RegistryError, match="/accounts/a/unit"):
        _ = load_registry(write_registry(text), home)


@pytest.mark.parametrize("text", ["- a\n- b\n", ""])
def test_a_registry_must_be_a_mapping(home: Path, text: str) -> None:
    with pytest.raises(RegistryError, match="must be a mapping"):
        _ = parse_registry(text, Path("machine.yaml"), home)


def test_errors_name_the_registry_file(
    write_registry: RegistryWriter, home: Path
) -> None:
    path = write_registry("version: 1\nacounts: {}\n")

    with pytest.raises(RegistryError, match=f"^{path}: "):
        _ = load_registry(path, home)


def test_a_missing_registry_says_what_to_create(home: Path, tmp_path: Path) -> None:
    path = tmp_path / "absent" / REGISTRY_FILE

    with pytest.raises(RegistryError, match="not found"):
        _ = load_registry(path, home)


def test_relative_paths_are_refused(write_registry: RegistryWriter, home: Path) -> None:
    text = (
        "version: 1\ndefault-account: a\naccounts: {a: {agent: claude, dir: .claude}}\n"
    )
    with pytest.raises(RegistryError, match=r"/accounts/a/dir.*absolute"):
        _ = load_registry(write_registry(text), home)


def test_environment_variables_are_not_expanded(
    write_registry: RegistryWriter, home: Path
) -> None:
    relative = (
        "version: 1\ndefault-account: a\naccounts: {a: {agent: claude, dir: $HOME/x}}\n"
    )
    with pytest.raises(RegistryError, match=r"\$HOME/x"):
        _ = load_registry(write_registry(relative), home)

    absolute = (
        "version: 1\ndefault-account: a\n"
        "accounts: {a: {agent: claude, dir: /opt/$HOME/x}}\n"
    )
    registry = load_registry(write_registry(absolute), home)
    assert registry.accounts["a"].dir == Path("/opt/$HOME/x")


@pytest.mark.parametrize("name", ["a", "0a", "a-b_c", "personal2"])
def test_account_names_follow_the_name_rule(
    write_registry: RegistryWriter, home: Path, name: str
) -> None:
    text = (
        f"version: 1\ndefault-account: {name}\n"
        f"accounts: {{{name}: {{agent: claude, dir: /a}}}}\n"
    )

    assert name in load_registry(write_registry(text), home).accounts


@pytest.mark.parametrize("name", ["Personal", "-a", "_a", "a.b", "a b", "ä"])
def test_account_names_that_break_the_rule_are_refused(
    write_registry: RegistryWriter, home: Path, name: str
) -> None:
    text = (
        f'version: 1\ndefault-account: "{name}"\n'
        f'accounts: {{"{name}": {{agent: claude, dir: /a}}}}\n'
    )

    with pytest.raises(RegistryError, match=f"'{name}'.*not a valid account name"):
        _ = load_registry(write_registry(text), home)


def test_an_unknown_account_lists_the_known_ones(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = MINIMAL_REGISTRY + "      work: {agent: claude, dir: /w}\n"
    registry = load_registry(write_registry(text), home)

    assert registry.account("work").name == "work"
    with pytest.raises(RegistryError, match=r"'nope'.*personal, work"):
        _ = registry.account("nope")


def test_a_registry_without_accounts_has_none_to_default_to(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = "version: 1\ndefault-account: a\naccounts: {}\n"

    with pytest.raises(RegistryError, match=r"/default-account.*no accounts"):
        _ = load_registry(write_registry(text), home)


def test_normalize_path_expands_a_leading_tilde(home: Path) -> None:
    assert normalize_path("~", home) == home
    assert normalize_path("~/x/y", home) == home / "x" / "y"


def test_normalize_path_leaves_other_tildes_alone(home: Path) -> None:
    with pytest.raises(RegistryError, match="absolute"):
        _ = normalize_path("~other/x", home)
    assert normalize_path("/a/~/b", home) == Path("/a/~/b")


def test_normalize_path_drops_a_trailing_slash_and_dots(home: Path) -> None:
    assert normalize_path("/a/b/", home) == Path("/a/b")
    assert normalize_path("/a/./b/../c", home) == Path("/a/c")


def test_account_unit_finds_the_default_unit(
    write_registry: RegistryWriter, home: Path, tmp_path: Path
) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/accounts/personal.json": unit()})
    registry = load_registry(write_registry(MINIMAL_REGISTRY), home)

    found = account_unit(
        discover(conf_d, uid=UID), registry, registry.account("personal")
    )

    assert found.display == "accounts/personal.json"


def test_account_unit_follows_a_qualified_reference(
    write_registry: RegistryWriter, home: Path, tmp_path: Path
) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "bravo", {"conf/accounts/x.json": unit()})
    _ = make_source(conf_d / "other", {"conf/a.json": unit()})
    text = (
        "version: 1\ndefault-account: a\n"
        'accounts: {a: {agent: claude, dir: /a, unit: "@bravo/accounts/x.json"}}\n'
    )
    registry = load_registry(write_registry(text), home)

    found = account_unit(discover(conf_d, uid=UID), registry, registry.account("a"))

    assert found.display == "@bravo/accounts/x.json"


def test_account_unit_names_the_account_when_the_unit_is_missing(
    write_registry: RegistryWriter, home: Path, tmp_path: Path
) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/other.json": unit()})
    registry = load_registry(write_registry(MINIMAL_REGISTRY), home)

    with pytest.raises(PathError, match=r"personal.*accounts/personal\.json.*no such"):
        _ = account_unit(
            discover(conf_d, uid=UID), registry, registry.account("personal")
        )


def test_account_unit_refuses_a_unit_several_sources_have(
    write_registry: RegistryWriter, home: Path, tmp_path: Path
) -> None:
    conf_d = tmp_path / "conf.d"
    for name in ("bravo", "other"):
        _ = make_source(conf_d / name, {"conf/accounts/personal.json": unit()})
    registry = load_registry(write_registry(MINIMAL_REGISTRY), home)

    with pytest.raises(PathError, match=r"personal.*@name/"):
        _ = account_unit(
            discover(conf_d, uid=UID), registry, registry.account("personal")
        )


def test_agents_and_variables_are_read(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = """\
        version: 1
        default-account: personal
        vars: {scratch_root: /mnt/scratch}
        accounts:
          personal:
            agent: claude
            dir: ~/.claude
            vars: {scratch: /mnt/scratch/me}
        """

    registry = load_registry(write_registry(text), home)

    assert registry.vars == {"scratch_root": "/mnt/scratch"}
    assert registry.accounts["personal"].agent == "claude"
    assert registry.accounts["personal"].vars == {"scratch": "/mnt/scratch/me"}


def test_variables_default_to_none(write_registry: RegistryWriter, home: Path) -> None:
    registry = load_registry(write_registry(MINIMAL_REGISTRY), home)

    assert registry.vars == {}
    assert registry.accounts["personal"].vars == {}


def test_an_unknown_agent_is_refused(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = "version: 1\ndefault-account: a\naccounts: {a: {agent: codex, dir: /a}}\n"

    with pytest.raises(RegistryError, match=r"/accounts/a/agent.*'claude'"):
        _ = load_registry(write_registry(text), home)


def test_the_agent_is_required(write_registry: RegistryWriter, home: Path) -> None:
    text = "version: 1\ndefault-account: a\naccounts: {a: {dir: /a}}\n"

    with pytest.raises(RegistryError, match=r"/accounts/a/agent: Field required"):
        _ = load_registry(write_registry(text), home)


@pytest.mark.parametrize(
    ("text", "pointer"),
    [
        ("vars: {x: 'a{{b'}\naccounts: {a: {agent: claude, dir: /a}}", "/vars/x"),
        (
            "accounts: {a: {agent: claude, dir: /a, vars: {x: '{{home}}'}}}",
            "/accounts/a/vars/x",
        ),
    ],
)
def test_variable_values_must_not_contain_braces(
    write_registry: RegistryWriter, home: Path, text: str, pointer: str
) -> None:
    with pytest.raises(RegistryError, match=rf"{pointer}: .*must not contain '\{{\{{'"):
        _ = load_registry(
            write_registry(f"version: 1\ndefault-account: a\n{text}\n"), home
        )


@pytest.mark.parametrize("name", ["a", "scratch_root", "a1_b2"])
def test_variable_names_follow_the_name_rule(
    write_registry: RegistryWriter, home: Path, name: str
) -> None:
    text = (
        f"version: 1\ndefault-account: a\nvars: {{{name}: v}}\n"
        "accounts: {a: {agent: claude, dir: /a}}\n"
    )

    assert load_registry(write_registry(text), home).vars == {name: "v"}


@pytest.mark.parametrize("name", ["Scratch", "1a", "_a", "a-b", "a.b", "a b"])
def test_variable_names_that_break_the_rule_are_refused(
    write_registry: RegistryWriter, home: Path, name: str
) -> None:
    account = f'{{agent: claude, dir: /a, vars: {{"{name}": v}}}}'
    text = f"version: 1\ndefault-account: a\naccounts: {{a: {account}}}\n"

    with pytest.raises(RegistryError, match=f"/accounts/a/vars.*'{name}'.*not a valid"):
        _ = load_registry(write_registry(text), home)


def test_variable_values_are_not_converted_to_strings(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = (
        "version: 1\ndefault-account: a\nvars: {n: 5}\n"
        "accounts: {a: {agent: claude, dir: /a}}\n"
    )

    with pytest.raises(RegistryError, match="/vars/n"):
        _ = load_registry(write_registry(text), home)


def with_project(entry: str) -> str:
    """Add the account `work` and a project, `bar`, set up by `entry`, to MINIMAL."""
    return (
        MINIMAL_REGISTRY
        + "      work: {agent: claude, dir: ~/.config/work}\n"
        + f"    projects:\n      bar: {{{entry}}}\n"
    )


def test_default_account_is_required(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = "version: 1\naccounts: {a: {agent: claude, dir: /a}}\n"

    with pytest.raises(RegistryError, match="/default-account: Field required"):
        _ = load_registry(write_registry(text), home)


def test_default_account_must_exist(write_registry: RegistryWriter, home: Path) -> None:
    text = MINIMAL_REGISTRY.replace("account: personal", "account: nope")

    with pytest.raises(
        RegistryError, match=r"/default-account: no account named 'nope'.*personal"
    ):
        _ = load_registry(write_registry(text), home)


@pytest.mark.parametrize(
    ("entry", "pointer"),
    [
        ("repo: /r, account: nope", "/projects/bar/account"),
        ("repo: /r, accounts: [work, nope]", "/projects/bar/accounts"),
    ],
)
def test_every_mentioned_account_must_exist(
    write_registry: RegistryWriter, home: Path, entry: str, pointer: str
) -> None:
    with pytest.raises(RegistryError, match=rf"{pointer}: no account named 'nope'"):
        _ = load_registry(write_registry(with_project(entry)), home)


def test_project_account_must_be_in_its_accounts(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = with_project("repo: /r, account: work, accounts: [personal]")

    with pytest.raises(
        RegistryError, match=r"/projects/bar/account: 'work' is not one of .* personal"
    ):
        _ = load_registry(write_registry(text), home)


def test_projects_are_read(write_registry: RegistryWriter, home: Path) -> None:
    entry = "repo: ~/src/bar.git/, account: work, accounts: [work], vars: {team: ci}"

    registry = load_registry(write_registry(with_project(entry)), home)

    project = registry.projects["bar"]
    assert registry.default_account == "personal"
    assert project.name == "bar"
    assert project.repo == home / "src" / "bar.git"
    assert project.account == "work"
    assert project.accounts == ("work",)
    assert (project.unit, project.unit_is_default) == ("projects/bar.json", True)
    assert project.vars == {"team": "ci"}


def test_a_project_needs_only_a_repo(
    write_registry: RegistryWriter, home: Path
) -> None:
    registry = load_registry(write_registry(with_project("repo: /r")), home)
    project = registry.projects["bar"]

    assert (project.account, project.accounts, project.vars) == (None, None, {})
    assert load_registry(write_registry(MINIMAL_REGISTRY), home).projects == {}


def test_project_unit_defaults_and_is_marked_default(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = with_project("repo: /r") + '      x: {repo: /x, unit: "@c/p/x.json"}\n'

    registry = load_registry(write_registry(text), home)

    bar, other = registry.projects["bar"], registry.projects["x"]
    assert (bar.unit, bar.unit_is_default) == ("projects/bar.json", True)
    assert (other.unit, other.unit_is_default) == ("@c/p/x.json", False)


@pytest.mark.parametrize("name", ["Bar", "-a", "a.b"])
def test_project_names_that_break_the_rule_are_refused(
    write_registry: RegistryWriter, home: Path, name: str
) -> None:
    text = MINIMAL_REGISTRY + f'    projects:\n      "{name}": {{repo: /r}}\n'

    with pytest.raises(RegistryError, match=f"'{name}'.*not a valid project name"):
        _ = load_registry(write_registry(text), home)


@pytest.mark.parametrize(
    ("entry", "pointer"),
    [
        ("repo: r", "/projects/bar/repo.*absolute"),
        ("repo: /r, vars: {A: x}", "/projects/bar/vars.*'A'.*not a valid"),
    ],
)
def test_project_repo_and_variables_are_checked(
    write_registry: RegistryWriter, home: Path, entry: str, pointer: str
) -> None:
    with pytest.raises(RegistryError, match=pointer):
        _ = load_registry(write_registry(with_project(entry)), home)


def test_an_unknown_project_lists_the_known_ones(
    write_registry: RegistryWriter, home: Path
) -> None:
    registry = load_registry(write_registry(with_project("repo: /r")), home)

    assert registry.project("bar").name == "bar"
    with pytest.raises(RegistryError, match=r"no project named 'nope'.*bar"):
        _ = registry.project("nope")
    with pytest.raises(RegistryError, match="defines no projects"):
        _ = load_registry(write_registry(MINIMAL_REGISTRY), home).project("bar")


def test_account_precedence_is_option_then_project_then_default(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = with_project("repo: /r, account: work") + "      plain: {repo: /p}\n"
    registry = load_registry(write_registry(text), home)
    bar, plain = registry.projects["bar"], registry.projects["plain"]

    assert choose_account(registry, None, None).name == "personal"
    assert choose_account(registry, plain, None).name == "personal"
    assert choose_account(registry, bar, None).name == "work"
    assert choose_account(registry, None, "work").name == "work"
    assert choose_account(registry, bar, "personal").name == "personal"


def test_the_chosen_account_must_exist_and_be_allowed(
    write_registry: RegistryWriter, home: Path
) -> None:
    registry = load_registry(
        write_registry(with_project("repo: /r, account: work, accounts: [work]")), home
    )
    bar = registry.projects["bar"]

    with pytest.raises(RegistryError, match="no account named 'nope'"):
        _ = choose_account(registry, bar, "nope")
    with pytest.raises(
        RegistryError, match=r"project 'bar' does not allow account 'personal'.*work"
    ):
        _ = choose_account(registry, bar, "personal")


def test_a_project_without_an_account_must_allow_the_default_account(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = with_project("repo: /r, accounts: [work]")

    with pytest.raises(
        RegistryError,
        match=r"/projects/bar/accounts: .*default account 'personal'.*work",
    ):
        _ = load_registry(write_registry(text), home)

    fixed = with_project("repo: /r, accounts: [work, personal]")
    assert load_registry(write_registry(fixed), home).projects["bar"].accounts == (
        "work",
        "personal",
    )


def test_a_project_must_allow_at_least_one_account(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = with_project("repo: /r, account: work, accounts: []")

    with pytest.raises(RegistryError, match=r"/projects/bar/accounts: .*at least one"):
        _ = load_registry(write_registry(text), home)


def test_instructions_settings_default_to_none(
    write_registry: RegistryWriter, home: Path
) -> None:
    registry = load_registry(write_registry(MINIMAL_REGISTRY), home)

    account = registry.accounts["personal"]
    assert registry.default_agents_md == "AGENTS.md"
    assert account.use_default_agents_md is False
    assert account.agents_md_file is None
    assert registry.warnings == ()


def test_the_instructions_keys_are_read(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = """\
        version: 1
        default-account: a
        default-agents-md: "@bravo/AGENTS.md"
        accounts:
          a: {agent: claude, dir: /a, use-default-agents-md: true}
          b: {agent: claude, dir: /b, agents-md-file: ~/notes/AGENTS.md}
          c: {agent: claude, dir: /c, agents-md-file: /etc/rules.md}
          d: {agent: claude, dir: /d, agents-md-file: "@bravo/docs/RULES.md"}
          e: {agent: claude, dir: /e, agents-md-file: docs/RULES.md}
          f: {agent: claude, dir: /f, agents-md-file: null}
        """

    registry = load_registry(write_registry(text), home)

    assert registry.default_agents_md == "@bravo/AGENTS.md"
    accounts = registry.accounts
    assert accounts["a"].use_default_agents_md is True
    assert accounts["a"].agents_md_file is None
    assert accounts["b"].agents_md_file == home / "notes" / "AGENTS.md"
    assert accounts["c"].agents_md_file == Path("/etc/rules.md")
    assert accounts["d"].agents_md_file == "@bravo/docs/RULES.md"
    assert accounts["e"].agents_md_file == "docs/RULES.md"
    assert accounts["f"].agents_md_file is None
    assert registry.warnings == ()


def test_an_agents_md_file_path_is_normalized_like_a_dir(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = (
        "version: 1\ndefault-account: a\n"
        "accounts: {a: {agent: claude, dir: /a, agents-md-file: /x/../y/./AGENTS.md}}\n"
    )

    registry = load_registry(write_registry(text), home)

    assert registry.accounts["a"].agents_md_file == Path("/y/AGENTS.md")


def test_an_agents_md_file_that_is_neither_a_path_nor_an_asset_is_refused(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = (
        "version: 1\ndefault-account: a\n"
        "accounts: {a: {agent: claude, dir: /a, agents-md-file: '~other/x'}}\n"
    )

    with pytest.raises(RegistryError, match=r"/accounts/a/agents-md-file: .*absolute"):
        _ = load_registry(write_registry(text), home)


@pytest.mark.parametrize(
    "entry",
    [
        "use-default-agents-md: yes",
        "use-default-agents-md: 1",
        'use-default-agents-md: "true"',
        "agents-md-file: 5",
        "agents-md-file: [a]",
        "use_default_agents_md: true",
    ],
)
def test_the_instructions_keys_are_checked_strictly(
    write_registry: RegistryWriter, home: Path, entry: str
) -> None:
    text = (
        "version: 1\ndefault-account: a\n"
        f"accounts: {{a: {{agent: claude, dir: /a, {entry}}}}}\n"
    )

    with pytest.raises(RegistryError, match="/accounts/a/"):
        _ = load_registry(write_registry(text), home)


def test_default_agents_md_must_be_a_string(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = (
        "version: 1\ndefault-account: a\ndefault-agents-md: 3\n"
        "accounts: {a: {agent: claude, dir: /a}}\n"
    )

    with pytest.raises(RegistryError, match="/default-agents-md"):
        _ = load_registry(write_registry(text), home)


def test_both_a_file_and_the_flag_warn(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = """\
        version: 1
        default-account: a
        accounts:
          a:
            agent: claude
            dir: /a
            agents-md-file: /x.md
            use-default-agents-md: true
          b: {agent: claude, dir: /b, agents-md-file: /x.md}
          c: {agent: claude, dir: /c, use-default-agents-md: true}
          d:
            agent: claude
            dir: /d
            agents-md-file: null
            use-default-agents-md: true
          e:
            agent: claude
            dir: /e
            agents-md-file: /x.md
            use-default-agents-md: false
        """
    path = write_registry(text)

    registry = load_registry(path, home)

    warning = (
        f"{path}: /accounts/a: agents-md-file is set, so use-default-agents-md "
        "has no effect"
    )
    assert registry.warnings == (warning,)


def test_the_skills_are_read_in_order_as_references(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = """\
        version: 1
        default-account: a
        accounts:
          a: {agent: claude, dir: /a, skills: [git-commits, "@bravo/review", b]}
          b: {agent: claude, dir: /b, skills: []}
          c: {agent: claude, dir: /c}
        """

    registry = load_registry(write_registry(text), home)

    assert registry.accounts["a"].skills == ("git-commits", "@bravo/review", "b")
    assert registry.accounts["b"].skills == ()
    assert registry.accounts["c"].skills == ()


@pytest.mark.parametrize(
    "entry", ["skills: a", "skills: [1]", "skills: [[a]]", "skills: {a: b}"]
)
def test_skills_must_be_a_list_of_strings(
    write_registry: RegistryWriter, home: Path, entry: str
) -> None:
    text = (
        "version: 1\ndefault-account: a\n"
        f"accounts: {{a: {{agent: claude, dir: /a, {entry}}}}}\n"
    )

    with pytest.raises(RegistryError, match="/accounts/a/skills"):
        _ = load_registry(write_registry(text), home)


COMMENTED = """\
    # this machine
    version: 1
    default-account: a

    accounts:
      a:
        agent: claude
        dir: /a  # the main one

      # the second
      b: {agent: claude, dir: /b, skills: [x]}
"""


def test_set_skills_writes_the_selection_and_returns_the_registry(
    write_registry: RegistryWriter, home: Path
) -> None:
    path = write_registry(COMMENTED)

    registry = set_skills(path, home, "a", ["git-commits", "@bravo/review"])

    assert registry.accounts["a"].skills == ("git-commits", "@bravo/review")
    assert registry.accounts["b"].skills == ("x",)
    assert load_registry(path, home) == registry
    assert path.read_text(encoding="utf-8") == textwrap.dedent(COMMENTED).replace(
        "dir: /a  # the main one\n",
        "dir: /a  # the main one\n    skills: [git-commits, '@bravo/review']\n",
    )


def test_set_skills_can_empty_a_selection(
    write_registry: RegistryWriter, home: Path
) -> None:
    path = write_registry(COMMENTED)

    registry = set_skills(path, home, "b", [])

    assert registry.accounts["b"].skills == ()
    assert "b: {agent: claude, dir: /b, skills: []}" in path.read_text(encoding="utf-8")


def test_set_skills_refuses_an_unknown_account_and_leaves_the_file(
    write_registry: RegistryWriter, home: Path
) -> None:
    path = write_registry(COMMENTED)

    with pytest.raises(RegistryError, match=r"no account named 'ghost'; .* a, b"):
        _ = set_skills(path, home, "ghost", ["x"])

    assert path.read_text(encoding="utf-8") == textwrap.dedent(COMMENTED)


def test_set_skills_refuses_a_registry_that_is_invalid_and_leaves_it(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = textwrap.dedent(COMMENTED) + "  c: {agent: claude, dir: /a}\n"
    path = write_registry(text)

    with pytest.raises(RegistryError, match="share the directory"):
        _ = set_skills(path, home, "a", ["x"])

    assert path.read_text(encoding="utf-8") == text


def test_set_skills_keeps_the_permissions_of_the_file(
    write_registry: RegistryWriter, home: Path
) -> None:
    path = write_registry(COMMENTED)
    path.chmod(0o640)

    _ = set_skills(path, home, "a", ["x"])

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_set_skills_reports_a_file_that_is_missing(home: Path, tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="cannot be read"):
        _ = set_skills(tmp_path / "absent.yaml", home, "a", ["x"])


def test_an_agent_can_name_its_binary(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = (
        MINIMAL_REGISTRY + "    agents:\n      claude: {bin: /opt/claude/bin/claude}\n"
    )

    registry = load_registry(write_registry(text), home)

    assert registry.agent_bins == {"claude": "/opt/claude/bin/claude"}


def test_a_binary_path_expands_a_leading_tilde_and_a_name_is_kept(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = MINIMAL_REGISTRY + "    agents:\n      claude: {bin: ~/bin/claude}\n"
    registry = load_registry(write_registry(text), home)
    assert registry.agent_bins == {"claude": str(home / "bin" / "claude")}

    text = MINIMAL_REGISTRY + "    agents:\n      claude: {bin: claude-2}\n"
    registry = load_registry(write_registry(text), home)
    assert registry.agent_bins == {"claude": "claude-2"}


def test_a_binary_path_is_not_resolved(
    write_registry: RegistryWriter, home: Path, tmp_path: Path
) -> None:
    (tmp_path / "link").symlink_to(tmp_path / "target")
    text = MINIMAL_REGISTRY + f"    agents:\n      claude: {{bin: {tmp_path}/link}}\n"

    registry = load_registry(write_registry(text), home)

    assert registry.agent_bins == {"claude": f"{tmp_path}/link"}


def test_an_agent_without_a_binary_has_none_to_name(
    write_registry: RegistryWriter, home: Path
) -> None:
    assert load_registry(write_registry(MINIMAL_REGISTRY), home).agent_bins == {}
    text = MINIMAL_REGISTRY + "    agents:\n      claude: {}\n"
    assert load_registry(write_registry(text), home).agent_bins == {}


@pytest.mark.parametrize(
    ("agents", "pointer"),
    [
        ("{codex: {bin: codex}}", "/agents/codex"),
        ("{claude: {bin: ''}}", "/agents/claude/bin"),
        ("{claude: {bin: 3}}", "/agents/claude/bin"),
        ("{claude: {bin: bin/claude}}", r"/agents/claude/bin.*absolute"),
        ("{claude: {bin: ./claude}}", r"/agents/claude/bin.*absolute"),
        ("{claude: {path: claude}}", "/agents/claude/path"),
    ],
)
def test_an_agents_section_is_checked(
    write_registry: RegistryWriter, home: Path, agents: str, pointer: str
) -> None:
    text = MINIMAL_REGISTRY + f"    agents: {agents}\n"

    with pytest.raises(RegistryError, match=pointer):
        _ = load_registry(write_registry(text), home)
