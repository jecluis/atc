"""The inventory of the variables a registry defines and its units use."""

from atc.inventory import MACHINE, Owner, Variable, take_inventory
from tests.conftest import ContextBuilder
from tests.helpers import VARIABLE_FILES, VARIABLE_REGISTRY, unit

PERSONAL = Owner("account", "personal")
WORK = Owner("account", "work")
BAR = Owner("project", "bar")
BAZ = Owner("project", "baz")


def test_every_variable_comes_by_owner_in_order_of_first_use(
    build_context: ContextBuilder,
) -> None:
    inventory = take_inventory(build_context(VARIABLE_FILES, VARIABLE_REGISTRY))

    assert inventory.warnings == ()
    assert inventory.variables == (
        Variable("root", MACHINE, "/r", ("common/base.json", "status.in.sh")),
        Variable("host", MACHINE, None, ("projects/bar.json",)),
        Variable("alpha", MACHINE, "a", ()),
        Variable("zeta", MACHINE, "z", ()),
        Variable("scratch", PERSONAL, "s", ("common/base.json",)),
        Variable("notes", PERSONAL, None, ("status.in.sh",)),
        Variable("cache", PERSONAL, None, ("projects/bar.json",)),
        Variable("other", PERSONAL, None, ("projects/baz.json",)),
        Variable("scratch", WORK, None, ("accounts/work.json",)),
        Variable("notes", WORK, None, ("status.in.sh",)),
        Variable("cache", WORK, None, ("projects/bar.json",)),
        Variable("port", BAR, None, ("projects/bar.json",)),
        Variable("port", BAZ, "1", ("projects/baz.json",)),
        Variable("extra", BAZ, "e", ()),
    )


def test_undefined_gives_the_variables_without_a_value(
    build_context: ContextBuilder,
) -> None:
    inventory = take_inventory(build_context(VARIABLE_FILES, VARIABLE_REGISTRY))

    assert [(v.reference, str(v.owner)) for v in inventory.undefined()] == [
        ("vars.host", "machine"),
        ("account.vars.notes", "account personal"),
        ("account.vars.cache", "account personal"),
        ("account.vars.other", "account personal"),
        ("account.vars.scratch", "account work"),
        ("account.vars.notes", "account work"),
        ("account.vars.cache", "account work"),
        ("project.vars.port", "project bar"),
    ]


def test_references_no_value_can_define_are_left_out(
    build_context: ContextBuilder,
) -> None:
    files = {
        "conf/accounts/personal.json": unit(
            {
                "env": {
                    "A": "{{account.vars.a.b}}",
                    "B": "{{project.vars.p}}",
                    "C": "{{home}} {{sources.x.dir}} {{vars.c.d}}",
                }
            }
        )
    }
    registry = """\
        version: 1
        default-account: personal
        accounts:
          personal: {agent: claude, dir: ~/.claude}
    """

    assert take_inventory(build_context(files, registry)).variables == ()


def test_an_owner_whose_units_cannot_be_read_is_warned_about_and_skipped(
    build_context: ContextBuilder,
) -> None:
    files = {
        "conf/accounts/personal.json": unit({"env": {"A": "{{account.vars.a}}"}}),
        "conf/projects/bar.json": unit(imports=["gone.json"]),
    }
    registry = """\
        version: 1
        default-account: personal
        accounts:
          personal: {agent: claude, dir: ~/.claude}
          work: {agent: claude, dir: ~/.config/work, unit: accounts/missing.json}
        projects:
          bar: {repo: /src/bar, vars: {p: '1'}}
    """

    inventory = take_inventory(build_context(files, registry))

    (work, bar) = inventory.warnings
    assert work.startswith("account 'work': the variables of its units are left out")
    assert "accounts/missing.json" in work
    assert bar.startswith("project 'bar': the variables of its units are left out")
    assert "gone.json" in bar
    assert inventory.variables == (
        Variable("a", PERSONAL, None, ("accounts/personal.json",)),
        Variable("p", BAR, "1", ()),
    )


def test_an_account_whose_unit_cannot_be_read_still_gets_what_else_uses(
    build_context: ContextBuilder,
) -> None:
    files = {
        "assets/notes.in.md": "{{account.vars.n}}\n",
        "conf/projects/bar.json": unit({"env": {"A": "{{account.vars.b}}"}}),
    }
    registry = """\
        version: 1
        default-account: personal
        accounts:
          personal: {agent: claude, dir: ~/.claude, unit: accounts/gone.json}
        projects:
          bar: {repo: /src/bar}
    """

    inventory = take_inventory(build_context(files, registry))

    (warning,) = inventory.warnings
    assert warning.startswith("account 'personal': the variables of its units are ")
    assert inventory.variables == (
        Variable("n", PERSONAL, None, ("notes.in.md",)),
        Variable("b", PERSONAL, None, ("projects/bar.json",)),
    )


def test_a_project_without_a_unit_has_only_what_it_defines(
    build_context: ContextBuilder,
) -> None:
    registry = """\
        version: 1
        default-account: personal
        accounts:
          personal: {agent: claude, dir: ~/.claude}
        projects:
          bar: {repo: /src/bar, vars: {p: '1'}}
    """
    files = {"conf/accounts/personal.json": unit()}

    inventory = take_inventory(build_context(files, registry))

    assert inventory.warnings == ()
    assert inventory.variables == (Variable("p", BAR, "1", ()),)


def test_an_owner_prints_as_its_level_and_name() -> None:
    assert (str(MACHINE), str(PERSONAL), str(BAR)) == (
        "machine",
        "account personal",
        "project bar",
    )
    assert MACHINE.reference("x") == "vars.x"
    assert PERSONAL.reference("x") == "account.vars.x"
    assert BAR.reference("x") == "project.vars.x"


def test_effects_say_whether_atc_sync_or_atc_run_builds_what_uses_each(
    build_context: ContextBuilder,
) -> None:
    files = {
        **VARIABLE_FILES,
        "conf/projects/baz.json": unit({"env": {"R": "{{vars.root}}"}}),
    }
    inventory = take_inventory(build_context(files, VARIABLE_REGISTRY))

    effects = inventory.effects
    assert effects[MACHINE, "root"] == {"sync", "run"}
    assert effects[MACHINE, "host"] == {"run"}
    assert effects[PERSONAL, "scratch"] == {"sync"}
    assert effects[PERSONAL, "notes"] == {"sync"}
    # An account's variable that a project's unit uses is built at atc run.
    assert effects[PERSONAL, "cache"] == {"run"}
    assert effects[BAR, "port"] == {"run"}
    assert (MACHINE, "zeta") not in effects
    assert inventory.effects_of([(PERSONAL, "scratch"), (BAR, "port")]) == {
        "sync",
        "run",
    }
    assert inventory.effects_of([(MACHINE, "zeta")]) == frozenset()


def test_effects_are_unknown_when_some_units_cannot_be_read(
    build_context: ContextBuilder,
) -> None:
    registry = VARIABLE_REGISTRY.replace(
        "unit: accounts/work.json", "unit: accounts/gone.json"
    )
    inventory = take_inventory(build_context(VARIABLE_FILES, registry))

    assert inventory.warnings
    assert inventory.effects_of([(MACHINE, "host")]) is None
