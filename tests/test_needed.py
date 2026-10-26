"""The variables a new project needs that nothing defines yet."""

from dataclasses import replace
from pathlib import Path

import pytest

from atc.context import BuildContext
from atc.errors import ConfigError
from atc.inventory import MACHINE, Owner, Variable
from atc.jsonval import JsonObject
from atc.needed import project_variables
from atc.registry import Project
from tests.conftest import ContextBuilder
from tests.helpers import unit

REGISTRY = """\
    version: 1
    vars: {root: /r}
    default-account: personal
    accounts:
      personal: {agent: claude, dir: ~/.claude, vars: {cache: c}}
      work: {agent: claude, dir: ~/.config/work}
"""
ACCOUNTS = {
    "conf/accounts/personal.json": unit(),
    "conf/accounts/work.json": unit(),
}
NEW = Owner("project", "new")


def new_project(accounts: tuple[str, ...] | None = None) -> Project:
    return Project(
        name="new",
        repo=Path("/src/new"),
        account=None if accounts is None else accounts[0],
        accounts=accounts,
        unit="projects/new.json",
        unit_is_default=True,
        vars={},
    )


def holding(ctx: BuildContext, project: Project) -> BuildContext:
    """Give `ctx` with `project` in its registry, as `prj add` builds it."""
    projects = {**ctx.registry.projects, project.name: project}
    return replace(ctx, registry=replace(ctx.registry, projects=projects))


def test_a_new_project_needs_what_its_units_use_and_nothing_defines(
    build_context: ContextBuilder,
) -> None:
    files = {
        **ACCOUNTS,
        "conf/projects/new.json": unit(
            {
                "env": {
                    "HOST": "{{vars.host}}",
                    "CACHE": "{{account.vars.cache}}",
                    "ROOT": "{{vars.root}}",
                },
            },
            imports=["common/port.json"],
        ),
        "conf/common/port.json": unit({"env": {"PORT": "{{project.vars.port}}"}}),
    }
    project = new_project()
    ctx = holding(build_context(files, REGISTRY), project)

    assert project_variables(ctx, project) == [
        Variable("port", NEW, None, ("common/port.json",)),
        Variable("cache", Owner("account", "work"), None, ("projects/new.json",)),
        Variable("host", MACHINE, None, ("projects/new.json",)),
    ]


def test_only_the_accounts_the_project_allows_are_asked_for(
    build_context: ContextBuilder,
) -> None:
    files = {
        **ACCOUNTS,
        "conf/projects/new.json": unit({"env": {"A": "{{account.vars.other}}"}}),
    }
    project = new_project(("work",))
    ctx = holding(build_context(files, REGISTRY), project)

    assert project_variables(ctx, project) == [
        Variable("other", Owner("account", "work"), None, ("projects/new.json",)),
    ]


def test_a_project_without_a_unit_needs_nothing(build_context: ContextBuilder) -> None:
    project = new_project()
    ctx = holding(build_context(ACCOUNTS, REGISTRY), project)

    assert project_variables(ctx, project) == []


@pytest.mark.parametrize(
    ("settings", "imports", "message"),
    [
        ({"env": {"A": "{{project.nmae}}"}}, [], "unknown reference {{project.nmae}}"),
        ({"env": {"A": "{{vars.a.b}}"}}, [], "{{vars.a.b}}"),
        (None, ["gone.json"], "gone.json"),
    ],
)
def test_what_no_answer_could_fix_is_reported_before_anything_is_asked(
    build_context: ContextBuilder,
    settings: JsonObject | None,
    imports: list[str],
    message: str,
) -> None:
    files = {**ACCOUNTS, "conf/projects/new.json": unit(settings, imports=imports)}
    project = new_project()
    ctx = holding(build_context(files, REGISTRY), project)

    with pytest.raises(ConfigError, match=message.replace("{", r"\{")):
        _ = project_variables(ctx, project)


def test_what_an_accounts_own_unit_needs_is_not_asked_for_a_project(
    build_context: ContextBuilder,
) -> None:
    files = {
        "conf/accounts/personal.json": unit({"env": {"A": "{{account.vars.own}}"}}),
        "conf/accounts/work.json": unit(),
        "conf/projects/new.json": unit({"env": {"P": "{{project.vars.p}}"}}),
    }
    project = new_project()
    ctx = holding(build_context(files, REGISTRY), project)

    assert project_variables(ctx, project) == [
        Variable("p", NEW, None, ("projects/new.json",)),
    ]


def test_a_variable_two_units_use_names_both(build_context: ContextBuilder) -> None:
    files = {
        **ACCOUNTS,
        "conf/projects/new.json": unit(
            {"env": {"A": "{{vars.host}}"}}, imports=["common/b.json"]
        ),
        "conf/common/b.json": unit({"env": {"B": "{{vars.host}}"}}),
    }
    project = new_project()
    ctx = holding(build_context(files, REGISTRY), project)

    assert project_variables(ctx, project) == [
        Variable("host", MACHINE, None, ("common/b.json", "projects/new.json")),
    ]
