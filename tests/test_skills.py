"""Skills: finding them in the sources, naming them, and what an account links."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from atc.errors import ConfigError, PathError
from atc.jsonval import JsonObject
from atc.skills import Catalog, discover_skills, wanted_skill_links
from atc.sources import Sources, discover
from tests.conftest import ContextBuilder
from tests.helpers import UID, make_source, registry, skill_md


def catalog_of(
    root: Path, files: Mapping[str, str | JsonObject]
) -> tuple[Sources, Catalog]:
    """Make a source of ``files`` at ``root``, and discover its skills."""
    sources = discover(make_source(root, files), uid=UID)
    return sources, discover_skills(sources)


def two_sources(root: Path, *skills: str) -> Sources:
    """Make sources `personal` and `bravo`, both with every skill in ``skills``."""
    for name in ("personal", "bravo"):
        _ = make_source(
            root / name, {f"skills/{skill}/SKILL.md": skill_md() for skill in skills}
        )
    return discover(root, uid=UID)


def test_only_direct_subdirectories_with_skill_md_are_skills(tmp_path: Path) -> None:
    _, catalog = catalog_of(
        tmp_path / "conf.d",
        {
            "skills/review/SKILL.md": skill_md(),
            "skills/no-file/notes.md": "x",
            "skills/group/nested/SKILL.md": skill_md(),
            "skills/loose.md": "x",
        },
    )

    assert [skill.name for skill in catalog.skills] == ["review"]
    assert catalog.warnings == ()


def test_skills_are_found_in_every_source_in_order(tmp_path: Path) -> None:
    sources = two_sources(tmp_path / "conf.d", "b", "a")

    catalog = discover_skills(sources)

    assert [(skill.source.name, skill.name) for skill in catalog.skills] == [
        ("bravo", "a"),
        ("bravo", "b"),
        ("personal", "a"),
        ("personal", "b"),
    ]
    first = catalog.skills[0]
    assert first.path == tmp_path / "conf.d" / "bravo" / "skills" / "a"
    assert first.reference == "@bravo/a"


@pytest.mark.parametrize("name", ["Review", "has space", "-lead", ".hidden", "_x"])
def test_a_bad_skill_name_is_skipped_with_a_warning(tmp_path: Path, name: str) -> None:
    _, catalog = catalog_of(
        tmp_path / "conf.d",
        {f"skills/{name}/SKILL.md": skill_md(), "skills/ok/SKILL.md": skill_md()},
    )

    assert [skill.name for skill in catalog.skills] == ["ok"]
    (warning,) = catalog.warnings
    assert f"'{name}' is not a valid skill name" in warning
    assert str(tmp_path / "conf.d" / "skills" / name) in warning


@pytest.mark.parametrize(
    ("text", "description"),
    [
        (skill_md("review", "Check a change"), "Check a change"),
        (skill_md("review"), ""),
        ("# No front matter\n", ""),
        ("---\ndescription: [a, b]\n---\n", ""),
        ("---\n---\n", ""),
        ("---\ndescription: >\n  folded\n  text\n---\n", "folded text"),
    ],
    ids=["set", "absent", "no-front-matter", "not-a-string", "empty", "folded"],
)
def test_the_description_comes_from_front_matter(
    tmp_path: Path, text: str, description: str
) -> None:
    _, catalog = catalog_of(tmp_path / "conf.d", {"skills/review/SKILL.md": text})

    (skill,) = catalog.skills
    assert skill.description == description
    assert catalog.warnings == ()


def test_a_front_matter_name_mismatch_warns(tmp_path: Path) -> None:
    _, catalog = catalog_of(
        tmp_path / "conf.d",
        {"skills/review/SKILL.md": skill_md("code-review", "Check a change")},
    )

    (skill,) = catalog.skills
    assert (skill.name, skill.description) == ("review", "Check a change")
    (warning,) = catalog.warnings
    assert "'code-review' differs from the directory name 'review'" in warning
    assert str(tmp_path / "conf.d" / "skills" / "review" / "SKILL.md") in warning


@pytest.mark.parametrize(
    "text",
    ["---\nname: [oops\n---\n", "---\nname: a\n", "---\njust text\n---\n"],
    ids=["bad-yaml", "never-closed", "not-a-mapping"],
)
def test_unparseable_front_matter_warns_and_the_skill_still_counts(
    tmp_path: Path, text: str
) -> None:
    _, catalog = catalog_of(tmp_path / "conf.d", {"skills/review/SKILL.md": text})

    (skill,) = catalog.skills
    assert (skill.name, skill.description) == ("review", "")
    (warning,) = catalog.warnings
    assert "front matter" in warning
    assert str(tmp_path / "conf.d" / "skills" / "review" / "SKILL.md") in warning


def test_a_skill_file_that_cannot_be_decoded_warns_and_counts(tmp_path: Path) -> None:
    sources = discover(
        make_source(tmp_path / "conf.d", {"skills/review/SKILL.md": ""}), uid=UID
    )
    _ = (tmp_path / "conf.d" / "skills" / "review" / "SKILL.md").write_bytes(
        b"\xff\xfe"
    )

    catalog = discover_skills(sources)

    assert [skill.name for skill in catalog.skills] == ["review"]
    (warning,) = catalog.warnings
    assert "cannot be read" in warning


def test_a_source_without_skills_has_none(tmp_path: Path) -> None:
    _, catalog = catalog_of(tmp_path / "conf.d", {"assets/AGENTS.md": "x"})

    assert catalog == Catalog((), ())


def test_an_ambiguous_skill_name_needs_a_source_prefix(tmp_path: Path) -> None:
    sources = two_sources(tmp_path / "conf.d", "review")
    catalog = discover_skills(sources)

    with pytest.raises(PathError, match=r"'review' exists as .*add an @name/ prefix"):
        _ = catalog.resolve("review", sources)


def test_at_name_selects_a_skill_from_one_source(tmp_path: Path) -> None:
    sources = two_sources(tmp_path / "conf.d", "review")

    skill = discover_skills(sources).resolve("@bravo/review", sources)

    assert skill.source.name == "bravo"
    assert skill.path == tmp_path / "conf.d" / "bravo" / "skills" / "review"


def test_a_name_in_one_source_needs_no_prefix(tmp_path: Path) -> None:
    sources, catalog = catalog_of(
        tmp_path / "conf.d", {"skills/review/SKILL.md": skill_md("review", "Check")}
    )

    skill = catalog.resolve("review", sources)

    assert (skill.name, skill.description) == ("review", "Check")


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("ghost", r"'ghost': no such skill"),
        ("@bravo/ghost", r"'@bravo/ghost': no such skill"),
        ("no-file", r"'no-file': no such skill"),
        ("group/nested", r"one directory"),
        ("..", r"one directory"),
        ("Bad", r"'Bad': .*not a valid skill name"),
        ("@nowhere/review", r"no config source named 'nowhere'"),
    ],
)
def test_a_skill_that_is_missing_or_unusable_is_a_path_error(
    tmp_path: Path, reference: str, message: str
) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "bravo", {"skills/review/SKILL.md": skill_md()})
    _ = make_source(
        conf_d / "personal",
        {
            "skills/no-file/notes.md": "x",
            "skills/Bad/SKILL.md": skill_md(),
            "skills/group/nested/SKILL.md": skill_md(),
        },
    )
    sources = discover(conf_d, uid=UID)

    with pytest.raises(PathError, match=message):
        _ = discover_skills(sources).resolve(reference, sources)


def test_an_account_wants_one_link_per_selected_skill(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = build_context(
        {
            "skills/b/SKILL.md": skill_md(),
            "skills/a/SKILL.md": skill_md(),
            "skills/c/SKILL.md": skill_md(),
        },
        registry("skills: [b, a]"),
    )

    wanted = wanted_skill_links(
        ctx, ctx.registry.account("personal"), discover_skills(ctx.sources)
    )

    skills = ctx.dirs.conf_d / "skills"
    assert wanted == {
        home / ".claude" / "skills" / "b": skills / "b",
        home / ".claude" / "skills" / "a": skills / "a",
    }
    assert list(wanted) == [
        home / ".claude" / "skills" / "b",
        home / ".claude" / "skills" / "a",
    ]


def test_an_account_that_selects_nothing_wants_no_links(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({"skills/a/SKILL.md": skill_md()}, registry())

    wanted = wanted_skill_links(
        ctx, ctx.registry.account("personal"), discover_skills(ctx.sources)
    )

    assert wanted == {}


def test_selecting_a_skill_twice_is_one_link(build_context: ContextBuilder) -> None:
    ctx = build_context({"skills/a/SKILL.md": skill_md()}, registry("skills: [a, a]"))

    wanted = wanted_skill_links(
        ctx, ctx.registry.account("personal"), discover_skills(ctx.sources)
    )

    assert len(wanted) == 1


def test_two_selected_skills_with_one_name_are_an_error(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "bravo/skills/review/SKILL.md": skill_md(),
            "personal/skills/review/SKILL.md": skill_md(),
        },
        registry('skills: ["@bravo/review", "@personal/review"]'),
    )

    message = (
        r"account 'personal': skills: @bravo/review and @personal/review "
        r"are both named 'review'"
    )
    with pytest.raises(ConfigError, match=message):
        _ = wanted_skill_links(
            ctx, ctx.registry.account("personal"), discover_skills(ctx.sources)
        )


def test_a_selected_skill_that_is_gone_is_a_config_error(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({"skills/a/SKILL.md": skill_md()}, registry("skills: [a, b]"))

    with pytest.raises(
        PathError, match=r"account 'personal': skills: 'b': no such skill"
    ):
        _ = wanted_skill_links(
            ctx, ctx.registry.account("personal"), discover_skills(ctx.sources)
        )
