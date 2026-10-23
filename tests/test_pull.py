"""Deploying config sources: a commit is checked before it is fast-forwarded to."""

import errno
import json
import os
import shutil
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from atc.errors import PathError
from atc.git import run_git
from atc.paths import Dirs
from atc.pull import PullResult, pull
from atc.sources import Sources, discover
from atc.validate import Validation, validate_sources
from tests.helpers import UID, bare_upstream, git_repo, make_source, publish, unit


def check(sources: Sources) -> Validation:
    return validate_sources(sources, lax=False)


def pull_sources(conf_d: Path, names: Sequence[str] = ()) -> list[PullResult]:
    return pull(discover(conf_d, uid=UID), names, check)


def pull_one(conf_d: Path, name: str = "personal") -> PullResult:
    (result,) = pull_sources(conf_d, [name])
    return result


def head(root: Path) -> str:
    return run_git(["rev-parse", "HEAD"], cwd=root)


@contextmanager
def umask(mask: int) -> Generator[None]:
    previous = os.umask(mask)
    try:
        yield
    finally:
        _ = os.umask(previous)


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    return bare_upstream(tmp_path / "upstream.git", {"conf/a.json": unit()})


@pytest.fixture
def deployed(dirs: Dirs, upstream: Path) -> Path:
    """Clone the upstream as the source `personal` in `conf_d`."""
    root = dirs.conf_d / "personal"
    _ = run_git(["clone", "-q", str(upstream), str(root)])
    return root


def test_a_valid_upstream_commit_is_deployed(
    dirs: Dirs, deployed: Path, upstream: Path
) -> None:
    commit = publish(upstream, {"conf/b.json": unit({"model": "opus"})})

    result = pull_one(dirs.conf_d)

    assert result.name == "personal"
    assert result.status == "updated"
    assert result.validation is not None
    assert result.validation.errors == ()
    assert head(deployed) == commit
    assert (deployed / "conf" / "b.json").is_file()


def test_an_invalid_upstream_commit_is_not_deployed(
    dirs: Dirs, deployed: Path, upstream: Path
) -> None:
    before = head(deployed)
    _ = publish(upstream, {"conf/bad.json": "{"})

    result = pull_one(dirs.conf_d)

    assert result.status == "failed"
    assert head(deployed) == before
    assert not (deployed / "conf" / "bad.json").exists()
    assert result.validation is not None
    # The tree was checked somewhere else, but its problems name the source.
    assert any(
        str(deployed / "conf" / "bad.json") in error
        for error in result.validation.errors
    )


def test_uncommitted_changes_are_refused(
    dirs: Dirs, deployed: Path, upstream: Path
) -> None:
    before = head(deployed)
    _ = publish(upstream, {"conf/b.json": unit()})
    _ = (deployed / "conf" / "a.json").write_text("{}", encoding="utf-8")

    result = pull_one(dirs.conf_d)

    assert result.status == "refused"
    assert result.validation is None
    assert head(deployed) == before
    assert (deployed / "conf" / "a.json").read_text(encoding="utf-8") == "{}"


def test_a_new_file_in_a_source_directory_is_an_uncommitted_change(
    dirs: Dirs, deployed: Path
) -> None:
    _ = make_source(deployed, {"assets/new.sh": "x"})
    assert pull_one(dirs.conf_d).status == "refused"


def test_untracked_files_elsewhere_do_not_count_as_changes(
    dirs: Dirs, deployed: Path, upstream: Path
) -> None:
    _ = make_source(deployed, {"notes/todo.md": "x"})
    commit = publish(upstream, {"conf/b.json": unit()})

    assert pull_one(dirs.conf_d).status == "updated"
    assert head(deployed) == commit


@pytest.mark.parametrize("upstream_moved", [True, False], ids=["diverged", "ahead"])
def test_a_clone_that_cannot_fast_forward_is_refused(
    dirs: Dirs, deployed: Path, upstream: Path, upstream_moved: bool
) -> None:
    if upstream_moved:
        _ = publish(upstream, {"conf/b.json": unit()})
    _ = run_git(["commit", "-q", "--allow-empty", "-m", "local"], cwd=deployed)
    before = head(deployed)

    result = pull_one(dirs.conf_d)

    assert result.status == "refused"
    assert "fast-forward" in result.detail
    assert head(deployed) == before


def test_a_source_that_is_not_a_git_clone_is_skipped(dirs: Dirs) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})

    result = pull_one(dirs.conf_d)

    assert result.status == "skipped"
    assert "git" in result.detail


def test_a_clone_without_an_upstream_is_skipped(dirs: Dirs) -> None:
    _ = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))

    result = pull_one(dirs.conf_d)

    assert result.status == "skipped"
    assert "upstream" in result.detail


def test_already_current_is_reported(dirs: Dirs, deployed: Path) -> None:
    before = head(deployed)

    result = pull_one(dirs.conf_d)

    assert result.status == "current"
    assert result.validation is None
    assert head(deployed) == before


@pytest.mark.usefixtures("deployed")
def test_an_unreachable_upstream_fails(dirs: Dirs, upstream: Path) -> None:
    shutil.rmtree(upstream)

    result = pull_one(dirs.conf_d)

    assert result.status == "failed"
    assert result.detail


def test_validation_sees_the_other_sources_unchanged(
    dirs: Dirs, deployed: Path, upstream: Path
) -> None:
    _ = make_source(dirs.conf_d / "other", {"conf/x.json": unit()})
    commit = publish(upstream, {"conf/b.json": unit(imports=["@other/x.json"])})

    result = pull_one(dirs.conf_d)

    assert result.status == "updated"
    assert head(deployed) == commit


def test_validation_gets_the_incoming_tree_in_place_of_the_source(
    dirs: Dirs, deployed: Path, upstream: Path
) -> None:
    seen: list[Sources] = []

    def spy(sources: Sources) -> Validation:
        seen.append(sources)
        return check(sources)

    _ = publish(upstream, {"conf/b.json": unit()})
    _ = pull(discover(dirs.conf_d, uid=UID), [], spy)

    (candidate,) = seen
    assert candidate.conf_d == dirs.conf_d
    assert candidate.named("personal").root != deployed


@pytest.mark.usefixtures("deployed")
def test_a_symbolic_link_outside_the_source_directories_does_not_matter(
    dirs: Dirs, upstream: Path
) -> None:
    _ = publish(upstream, links={"link": "/nonexistent/elsewhere"})
    assert pull_one(dirs.conf_d).status == "updated"


@pytest.mark.parametrize(
    ("files", "links", "problem"),
    [
        pytest.param(
            {"assets/tool.sh": "x"},
            {"assets/link": "tool.sh"},
            "is a symbolic link",
            id="symbolic-link",
        ),
        pytest.param(
            {".gitattributes": "assets/link export-ignore\n", "assets/tool.sh": "x"},
            {"assets/link": "tool.sh"},
            "is a symbolic link",
            id="export-ignored-symbolic-link",
        ),
        pytest.param(
            {".gitattributes": "conf/bad.json export-ignore\n", "conf/bad.json": "{"},
            {},
            "bad.json",
            id="export-ignored-unit",
        ),
        pytest.param(
            {
                ".gitattributes": "conf/x.json export-subst\n",
                "conf/x.json": json.dumps(unit()) + "$Format:%n$",
            },
            {},
            "x.json",
            id="export-subst-makes-a-unit-valid",
        ),
    ],
)
def test_what_is_checked_is_what_would_be_deployed(
    dirs: Dirs,
    deployed: Path,
    upstream: Path,
    files: Mapping[str, str],
    links: Mapping[str, str],
    problem: str,
) -> None:
    before = head(deployed)
    _ = publish(upstream, files, links=links)

    result = pull_one(dirs.conf_d)

    assert result.status == "failed"
    assert result.validation is not None
    assert any(problem in error for error in result.validation.errors)
    assert head(deployed) == before


def test_the_checked_tree_is_what_the_merge_writes(
    dirs: Dirs, deployed: Path, upstream: Path
) -> None:
    seen: list[bytes] = []

    def spy(sources: Sources) -> Validation:
        seen.append((sources.named("personal").conf / "b.json").read_bytes())
        return check(sources)

    _ = publish(upstream, {".gitattributes": "*.json -text\n"})
    assert pull_one(dirs.conf_d).status == "updated"
    text = json.dumps(unit()) + "\n"
    _ = publish(
        upstream, {".gitattributes": "*.json text eol=crlf\n", "conf/b.json": text}
    )
    seen.clear()
    (result,) = pull(discover(dirs.conf_d, uid=UID), [], spy)

    crlf = text.replace("\n", "\r\n").encode()
    assert result.status == "updated"
    assert seen == [crlf]
    assert (deployed / "conf" / "b.json").read_bytes() == crlf


def test_a_commit_that_adds_a_nested_source_to_the_single_layout_is_not_deployed(
    dirs: Dirs, upstream: Path
) -> None:
    _ = run_git(["clone", "-q", str(upstream), str(dirs.conf_d)])
    before = head(dirs.conf_d)
    _ = publish(upstream, {"nested/conf/x.json": unit()})

    (result,) = pull_sources(dirs.conf_d)

    assert result.status == "failed"
    assert result.validation is not None
    assert any("both levels" in error for error in result.validation.errors)
    assert head(dirs.conf_d) == before


def test_a_commit_that_removes_every_source_directory_is_not_deployed_single(
    dirs: Dirs, upstream: Path
) -> None:
    _ = run_git(["clone", "-q", str(upstream), str(dirs.conf_d)])
    before = head(dirs.conf_d)
    _ = publish(upstream, remove=["conf/a.json"])

    (result,) = pull_sources(dirs.conf_d)

    assert result.status == "failed"
    assert result.validation is not None
    assert any("none of conf" in error for error in result.validation.errors)
    assert head(dirs.conf_d) == before


def test_a_commit_that_removes_every_source_directory_is_not_deployed_multi(
    dirs: Dirs, deployed: Path, upstream: Path
) -> None:
    before = head(deployed)
    _ = publish(upstream, remove=["conf/a.json"])

    result = pull_one(dirs.conf_d)

    assert result.status == "failed"
    assert result.validation is not None
    assert any(str(deployed) in error for error in result.validation.errors)
    assert head(deployed) == before


@pytest.mark.usefixtures("deployed")
def test_all_sources_are_pulled_by_default_in_name_order(
    dirs: Dirs, upstream: Path, tmp_path: Path
) -> None:
    bravo = bare_upstream(tmp_path / "bravo.git", {"conf/c.json": unit()})
    _ = run_git(["clone", "-q", str(bravo), str(dirs.conf_d / "bravo")])
    _ = publish(upstream, {"conf/b.json": unit()})
    _ = publish(bravo, {"conf/d.json": unit()})

    results = pull_sources(dirs.conf_d)

    assert [(r.name, r.status) for r in results] == [
        ("bravo", "updated"),
        ("personal", "updated"),
    ]


@pytest.mark.usefixtures("deployed")
def test_only_the_named_sources_are_pulled(
    dirs: Dirs, upstream: Path, tmp_path: Path
) -> None:
    bravo = bare_upstream(tmp_path / "bravo.git", {"conf/c.json": unit()})
    _ = run_git(["clone", "-q", str(bravo), str(dirs.conf_d / "bravo")])
    before = head(dirs.conf_d / "bravo")
    _ = publish(bravo, {"conf/d.json": unit()})
    _ = publish(upstream, {"conf/b.json": unit()})

    results = pull_sources(dirs.conf_d, ["personal"])

    assert [(r.name, r.status) for r in results] == [("personal", "updated")]
    assert head(dirs.conf_d / "bravo") == before


@pytest.mark.usefixtures("deployed")
def test_a_name_given_twice_is_pulled_once(dirs: Dirs) -> None:
    results = pull_sources(dirs.conf_d, ["personal", "personal"])
    assert [r.name for r in results] == ["personal"]


def test_an_unknown_name_is_an_error_before_anything_is_pulled(
    dirs: Dirs, deployed: Path, upstream: Path
) -> None:
    before = head(deployed)
    _ = publish(upstream, {"conf/b.json": unit()})

    with pytest.raises(PathError, match="nowhere"):
        _ = pull_sources(dirs.conf_d, ["personal", "nowhere"])

    assert head(deployed) == before


def test_the_single_layout_source_is_pulled_without_a_name(
    dirs: Dirs, upstream: Path
) -> None:
    _ = run_git(["clone", "-q", str(upstream), str(dirs.conf_d)])
    commit = publish(upstream, {"conf/b.json": unit()})

    (result,) = pull_sources(dirs.conf_d)

    assert (result.name, result.status) == ("", "updated")
    assert head(dirs.conf_d) == commit


def test_a_pull_under_umask_002_leaves_a_trusted_tree(
    dirs: Dirs, deployed: Path, upstream: Path
) -> None:
    commit = publish(
        upstream,
        {
            "conf/new/x.json": unit(),
            "skills/a/SKILL.md": "x",
            "assets/run.sh": "#!/bin/sh\n",
        },
    )

    with umask(0o002):
        result = pull_one(dirs.conf_d)

    assert result.status == "updated"
    assert head(deployed) == commit
    assert discover(dirs.conf_d, uid=UID).named("personal").skills.is_dir()


def test_a_failed_chmod_is_reported_with_its_path(
    dirs: Dirs, deployed: Path, upstream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = publish(upstream, {"conf/new.json": unit()})
    chmod = os.chmod

    def refuse(path: Path, mode: int) -> None:
        if Path(path) == deployed / "conf" / "new.json":
            raise PermissionError(errno.EPERM, "Operation not permitted", str(path))
        chmod(path, mode)

    monkeypatch.setattr(os, "chmod", refuse)

    with umask(0o002):
        result = pull_one(dirs.conf_d)

    assert result.status == "failed"
    assert str(deployed / "conf" / "new.json") in result.detail


def test_a_failed_chmod_of_the_checked_tree_names_the_source(
    dirs: Dirs, deployed: Path, upstream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = head(deployed)
    _ = publish(upstream, {"conf/new.json": unit()})
    chmod = os.chmod

    def refuse(path: Path, mode: int) -> None:
        if Path(path).name == "new.json":
            raise PermissionError(errno.EPERM, "Operation not permitted", str(path))
        chmod(path, mode)

    monkeypatch.setattr(os, "chmod", refuse)

    with umask(0o002):
        result = pull_one(dirs.conf_d)

    assert result.status == "failed"
    assert result.validation is not None
    assert any(
        str(deployed / "conf" / "new.json") in e for e in result.validation.errors
    )
    assert head(deployed) == before
