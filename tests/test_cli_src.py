"""``atc src ls``: what the table says about each config source."""

from pathlib import Path

import pytest

from atc import pull as pull_module
from atc.cli import main
from atc.git import run_git
from atc.paths import Dirs
from tests.conftest import ConfigWriter, RegistryWriter
from tests.helpers import (
    MINIMAL_REGISTRY,
    PROTECTING,
    bare_upstream,
    git_repo,
    make_source,
    publish,
    unit,
)


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "400")


def run_ls(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert main(["src", "ls", *args]) == 0
    return capsys.readouterr().out


def row_of(out: str, name: str) -> list[str]:
    (line,) = (line for line in out.splitlines() if line.split()[:1] == [name])
    return line.split()


def track_upstream(source: Path, bare: Path) -> None:
    """Give ``source`` an upstream, a bare copy of it, that it has fetched."""
    _ = run_git(["clone", "-q", "--bare", str(source), str(bare)])
    _ = run_git(["remote", "add", "origin", str(bare)], cwd=source)
    _ = run_git(["fetch", "-q", "origin"], cwd=source)
    _ = run_git(["branch", "-q", "-u", "origin/main"], cwd=source)


def test_src_ls_lists_every_source(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(dirs.conf_d / "bravo", {"assets/x.sh": "#!/bin/sh\n"})
    out = run_ls(capsys)
    assert "personal" in out
    assert "bravo" in out


def test_src_ls_counts_units_assets_and_skills(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(
        dirs.conf_d / "personal",
        {
            "conf/a.json": unit(),
            "conf/langs/b.json": unit(),
            "assets/hooks/one.sh": "x",
            "assets/hooks/two.sh": "x",
            "assets/three.txt": "x",
            "skills/review/SKILL.md": "x",
            "skills/review/extra.md": "x",
            "skills/notes/SKILL.md": "x",
            "skills/draft/notes.md": "x",
            "skills/Bad/SKILL.md": "x",
        },
    )
    _, path, layout, units, assets, skills = row_of(run_ls(capsys), "personal")[:6]
    assert path == str(dirs.conf_d / "personal")
    assert (layout, units, assets, skills) == ("multi", "2", "3", "2")


def test_src_ls_warns_about_a_skill_it_skipped(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d, {"skills/Bad/SKILL.md": "x"})

    assert main(["src", "ls"]) == 0

    assert "'Bad' is not a valid skill name" in capsys.readouterr().err


def test_src_ls_shows_a_source_without_a_name(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d, {"conf/a.json": unit()})
    _, path, layout = row_of(run_ls(capsys), "-")[:3]
    assert (path, layout) == (str(dirs.conf_d), "single")


def test_src_ls_shows_branch_and_commit_of_git_clones(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    commit = run_git(["rev-parse", "--short", "HEAD"], cwd=source)
    row = row_of(run_ls(capsys), "personal")
    assert row[6:8] == ["main", commit]


def test_src_ls_marks_uncommitted_changes(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    _ = (source / "conf" / "a.json").write_text("{}", encoding="utf-8")
    assert "dirty" in run_ls(capsys)


def test_src_ls_marks_new_files_as_changes(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    _ = make_source(source, {"assets/new.sh": "x"})
    assert "dirty" in run_ls(capsys)


def test_src_ls_does_not_mark_a_clean_clone(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    assert "dirty" not in run_ls(capsys)


def test_src_ls_ignores_changes_outside_the_source_directories(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    _ = make_source(source, {"notes/todo.md": "x"})
    assert "dirty" not in run_ls(capsys)


def test_src_ls_shows_how_far_a_clone_is_from_its_upstream(
    dirs: Dirs, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    track_upstream(source, tmp_path / "origin.git")
    other = tmp_path / "other"
    _ = run_git(["clone", "-q", str(tmp_path / "origin.git"), str(other)])
    _ = run_git(["commit", "-q", "--allow-empty", "-m", "elsewhere"], cwd=other)
    _ = run_git(["push", "-q"], cwd=other)
    _ = run_git(["fetch", "-q"], cwd=source)
    for message in ("one", "two"):
        _ = run_git(["commit", "-q", "--allow-empty", "-m", message], cwd=source)
    assert row_of(run_ls(capsys), "personal")[8:] == ["2/1"]


def test_src_ls_leaves_ahead_and_behind_blank_without_an_upstream(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    assert len(row_of(run_ls(capsys), "personal")) == 8


def test_src_ls_reports_a_trust_violation_as_a_config_error(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})
    (source / "conf" / "a.json").chmod(0o666)
    assert main(["src", "ls"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(source / "conf" / "a.json") in captured.err


def test_src_ls_warns_about_directories_that_are_not_sources(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(dirs.conf_d / "stray", {"notes.md": "x"})
    assert main(["src", "ls"]) == 0
    captured = capsys.readouterr()
    assert "stray" in captured.err
    assert "warning:" in captured.err
    assert "stray" not in captured.out


def test_src_ls_says_how_to_add_a_source_when_there_is_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["src", "ls"]) == 1
    assert "git clone" in capsys.readouterr().err


def test_conf_d_option_reads_another_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = make_source(tmp_path / "elsewhere", {"conf/a.json": unit()})
    assert str(elsewhere) in run_ls(capsys, "--conf-d", str(elsewhere))


def test_src_ls_keeps_the_whole_path_in_a_narrow_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    deep = make_source(
        tmp_path / "a" / "deeply" / "nested" / "dir", {"conf/a.json": unit()}
    )
    header, _, *body = run_ls(capsys, "--conf-d", str(deep)).splitlines()[1:]
    start, end = header.index("Path"), header.index("Layout")
    assert "".join(line[start:end].strip() for line in body) == str(deep)


def run_check(
    capsys: pytest.CaptureFixture[str], *args: str, status: int = 0
) -> tuple[str, str]:
    assert main(["src", "check", *args]) == status
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_src_check_passes_a_valid_tree(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(
        dirs.conf_d,
        {
            "conf/base.json": unit({"model": "opus"}),
            "conf/top.json": unit(imports=["base.json"]),
            "assets/notes.txt": "not a unit",
        },
    )
    out, err = run_check(capsys)
    assert out == ""
    assert "ok: 2 units in 1 sources" in err


def test_src_check_counts_the_units_of_every_source(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(
        dirs.conf_d / "bravo",
        {"conf/b.json": unit(), "conf/langs/c.json": unit(), "assets/x.sh": "x"},
    )
    assert "ok: 3 units in 2 sources" in run_check(capsys)[1]


def test_src_check_reports_every_broken_unit(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_source(
        dirs.conf_d,
        {
            "conf/good.json": unit(),
            "conf/bad-json.json": "{",
            "conf/bad-version.json": {"version": 1},
        },
    )
    out, err = run_check(capsys, status=1)
    assert out == ""
    assert str(source / "conf" / "bad-json.json") in err
    assert str(source / "conf" / "bad-version.json") in err
    assert "ok:" not in err


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b'{"version": 2, "description": "\xff"}', "not UTF-8"),
        ('{"version": 2, "settings": {"cleanupPeriodDays": 1e400}}', "1e400"),
        ('{"version": 2, "description": "\\ud800"}', "unpaired surrogate"),
        ('{"version": 2, "imports": ["a\\u0000.json"]}', "NUL"),
        ("[" * 1_000_000, "nested too deeply"),
    ],
    ids=["not-utf8", "overflow", "surrogate", "nul-import", "deep"],
)
def test_src_check_reports_a_unit_json_cannot_hold(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: str | bytes,
    message: str,
) -> None:
    conf_d = make_source(
        tmp_path / "conf.d", {"conf/good.json": unit(), "conf/bad.json": content}
    )

    assert main(["src", "check", "--conf-d", str(conf_d)]) == 1

    err = capsys.readouterr().err
    (error,) = [line for line in err.splitlines() if line.startswith("error: ")]
    assert "bad.json" in error
    assert message in error


def test_src_check_reports_a_dangling_import(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d, {"conf/a.json": unit(imports=["nowhere.json"])})
    err = run_check(capsys, status=1)[1]
    assert "error: a.json: imports 'nowhere.json', which does not exist" in err


def test_src_check_follows_imports_across_sources(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/common/base.json": unit()})
    _ = make_source(
        dirs.conf_d / "bravo",
        {"conf/x.json": unit(imports=["@personal/common/base.json"])},
    )
    assert "ok: 2 units in 2 sources" in run_check(capsys)[1]


def test_src_check_reports_a_dangling_import_across_sources(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/common/base.json": unit()})
    _ = make_source(
        dirs.conf_d / "bravo",
        {"conf/x.json": unit(imports=["@personal/common/gone.json"])},
    )
    err = run_check(capsys, status=1)[1]
    assert "@bravo/x.json: imports '@personal/common/gone.json'" in err


def test_src_check_reports_a_broken_import_once(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_source(
        dirs.conf_d,
        {
            "conf/base.json": "{",
            "conf/one.json": unit(imports=["base.json"]),
            "conf/two.json": unit(imports=["base.json"]),
        },
    )
    err = run_check(capsys, status=1)[1]
    assert err.count(str(source / "conf" / "base.json")) == 1


def test_src_check_warns_about_directories_that_are_not_sources(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(dirs.conf_d / "stray", {"notes.md": "x"})
    err = run_check(capsys)[1]
    assert "warning:" in err
    assert "stray" in err
    assert "ok: 1 units in 1 sources" in err


def test_src_check_reports_a_trust_violation_as_a_config_error(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})
    (source / "conf" / "a.json").chmod(0o666)
    err = run_check(capsys, status=1)[1]
    assert str(source / "conf" / "a.json") in err
    assert "ok:" not in err


def test_src_check_conf_d_option_reads_another_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = make_source(tmp_path / "elsewhere", {"conf/a.json": unit()})
    err = run_check(capsys, "--conf-d", str(elsewhere))[1]
    assert "ok: 1 units in 1 sources" in err


def test_src_check_passes_when_every_account_has_its_unit(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({"conf/accounts/personal.json": unit({"model": "x", **PROTECTING})})

    out, err = run_check(capsys)

    assert out == ""
    assert "ok: 1 units in 1 sources" in err
    assert "warning:" not in err


def test_src_check_warns_about_an_account_that_leaves_atc_writable(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({"conf/accounts/personal.json": unit({"model": "x"})})

    err = run_check(capsys)[1]

    assert "warning: account personal leaves atc's state directory" in err
    assert "ok: 1 units in 1 sources" in err


def test_src_check_reports_an_account_whose_unit_is_missing(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({"conf/other.json": unit()})

    err = run_check(capsys, status=1)[1]

    assert "account 'personal'" in err
    assert "accounts/personal.json" in err
    assert "ok:" not in err


def test_src_check_reports_a_bad_registry(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({"conf/accounts/personal.json": unit()}, "version: 3\n")

    err = run_check(capsys, status=1)[1]

    assert "/version" in err
    assert "ok:" not in err


def test_src_check_without_a_registry_checks_sources_only(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d, {"conf/a.json": unit()})

    err = run_check(capsys)[1]

    assert "warning:" in err
    assert "machine.yaml" in err
    assert "not checked" in err
    assert "ok: 1 units in 1 sources" in err


ACCOUNT_UNIT = "conf/accounts/personal.json"


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    return bare_upstream(
        tmp_path / "upstream.git", {ACCOUNT_UNIT: unit({"model": "x"})}
    )


@pytest.fixture
def deployed(dirs: Dirs, upstream: Path) -> Path:
    """Clone the upstream as the source `personal` in `conf_d`."""
    root = dirs.conf_d / "personal"
    _ = run_git(["clone", "-q", str(upstream), str(root)])
    return root


def run_pull(
    capsys: pytest.CaptureFixture[str], *args: str, status: int = 0
) -> tuple[str, str]:
    assert main(["src", "pull", *args]) == status
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_src_pull_deploys_a_valid_commit(
    deployed: Path, upstream: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit = publish(upstream, {"conf/b.json": unit()})

    out, err = run_pull(capsys)

    assert out.startswith("personal: updated (")
    assert "error:" not in err
    assert run_git(["rev-parse", "HEAD"], cwd=deployed) == commit


@pytest.mark.usefixtures("deployed")
def test_src_pull_says_when_a_source_is_current(
    capsys: pytest.CaptureFixture[str],
) -> None:
    out, err = run_pull(capsys)

    assert out.startswith("personal: current (")
    assert [line for line in err.splitlines() if not line.startswith("warning: ")] == []


@pytest.mark.usefixtures("deployed")
def test_src_pull_shows_the_commits_and_files_it_deploys(
    upstream: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pull_module, "LOG_LIMIT", 1)
    first = publish(upstream, {"conf/b.json": unit()}, message="conf: add b")
    second = publish(upstream, {"assets/x": "x"}, message="assets: add \x1b[2Jx")

    out, err = run_pull(capsys)

    assert out.startswith("personal: updated (")
    assert out.endswith(f"..{second[:7]})\n")
    assert first[:7] not in err
    lines = [line for line in err.splitlines() if not line.startswith("warning: ")]
    assert lines[:2] == [f"  {second[:7]} assets: add \\x1b[2Jx", "  … 1 more"]
    assert any(line.startswith("  assets/x ") for line in lines)
    assert any(line.startswith("  conf/b.json ") for line in lines)
    assert lines[-1].startswith("  2 files changed")


def test_src_pull_does_not_deploy_a_commit_that_fails_the_check(
    deployed: Path, upstream: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = run_git(["rev-parse", "HEAD"], cwd=deployed)
    _ = publish(upstream, {"conf/bad.json": "{", "conf/bad-version.json": "[]"})

    out, err = run_pull(capsys, status=1)

    assert out.startswith("personal: failed (")
    assert f"error: {deployed / 'conf' / 'bad.json'}" in err
    assert str(deployed / "conf" / "bad-version.json") in err
    assert run_git(["rev-parse", "HEAD"], cwd=deployed) == before


def test_src_pull_checks_the_accounts_when_the_registry_exists(
    deployed: Path,
    upstream: Path,
    write_registry: RegistryWriter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(MINIMAL_REGISTRY)
    before = run_git(["rev-parse", "HEAD"], cwd=deployed)
    _ = publish(upstream, {"conf/other.json": unit()}, remove=[ACCOUNT_UNIT])

    out, err = run_pull(capsys, status=1)

    assert out.startswith("personal: failed (")
    assert "account 'personal'" in err
    assert "not checked" not in err
    assert run_git(["rev-parse", "HEAD"], cwd=deployed) == before


@pytest.mark.usefixtures("deployed")
def test_src_pull_without_a_registry_checks_the_sources_only(
    upstream: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = publish(upstream, {"conf/b.json": unit()})

    out, err = run_pull(capsys)

    assert out.startswith("personal: updated (")
    assert "warning:" in err
    assert "machine.yaml" in err
    assert "not checked" in err


def test_src_pull_does_not_deploy_a_commit_that_breaks_the_trust_rules(
    deployed: Path, upstream: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = run_git(["rev-parse", "HEAD"], cwd=deployed)
    _ = publish(upstream, {"assets/tool.sh": "x"}, links={"assets/link": "tool.sh"})

    out, err = run_pull(capsys, status=1)

    assert out.startswith("personal: failed (")
    assert f"error: {deployed / 'assets' / 'link'}: is a symbolic link" in err
    assert run_git(["rev-parse", "HEAD"], cwd=deployed) == before


def test_src_pull_shows_warnings_of_what_it_deployed(
    deployed: Path, upstream: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(deployed.parent / "stray", {"notes.md": "x"})
    _ = publish(upstream, {"conf/b.json": unit()})

    err = run_pull(capsys)[1]

    assert err.count("stray") == 1
    assert "warning:" in err


def test_src_pull_exits_1_for_a_refused_source(
    deployed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = (deployed / ACCOUNT_UNIT).write_text("{}", encoding="utf-8")

    out, err = run_pull(capsys, status=1)

    assert out.startswith("personal: refused (uncommitted changes")
    assert "error:" not in err


def test_src_pull_skips_a_source_that_is_not_a_clone(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})
    assert run_pull(capsys)[0] == "personal: skipped (not a git clone)\n"


def test_src_pull_names_the_single_source_by_its_directory(
    dirs: Dirs, upstream: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = run_git(["clone", "-q", str(upstream), str(dirs.conf_d)])
    _ = publish(upstream, {"conf/b.json": unit()})
    assert run_pull(capsys)[0].startswith(f"{dirs.conf_d}: updated (")


@pytest.mark.usefixtures("deployed")
def test_src_pull_reports_every_source_and_exits_1_if_one_fails(
    dirs: Dirs, upstream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bravo = bare_upstream(tmp_path / "bravo.git", {"conf/c.json": unit()})
    _ = run_git(["clone", "-q", str(bravo), str(dirs.conf_d / "bravo")])
    _ = publish(bravo, {"conf/d.json": unit()})
    _ = publish(upstream, {"conf/bad.json": "{"})

    out, _ = run_pull(capsys, status=1)

    first, second = out.splitlines()
    assert first.startswith("bravo: updated (")
    assert second.startswith("personal: failed (")


@pytest.mark.usefixtures("deployed")
def test_src_pull_goes_on_after_a_source_with_a_unit_that_is_not_utf8(
    dirs: Dirs, upstream: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    zeta = bare_upstream(tmp_path / "zeta.git", {"conf/z.json": unit()})
    _ = run_git(["clone", "-q", str(zeta), str(dirs.conf_d / "zeta")])
    _ = publish(upstream, {"conf/bad.json": b'{"version": 2, "description": "\xff"}'})
    commit = publish(zeta, {"conf/y.json": unit()})

    out, err = run_pull(capsys, status=1)

    first, second = out.splitlines()
    assert first.startswith("personal: failed (")
    assert second.startswith("zeta: updated (")
    assert "not UTF-8" in err
    assert run_git(["rev-parse", "HEAD"], cwd=dirs.conf_d / "zeta") == commit


@pytest.mark.usefixtures("deployed")
def test_src_pull_takes_source_names(
    dirs: Dirs, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bravo = bare_upstream(tmp_path / "bravo.git", {"conf/c.json": unit()})
    _ = run_git(["clone", "-q", str(bravo), str(dirs.conf_d / "bravo")])
    _ = publish(bravo, {"conf/d.json": unit()})

    out, _ = run_pull(capsys, "personal")

    assert out.startswith("personal: current (")
    assert out.count("\n") == 1


@pytest.mark.usefixtures("deployed")
def test_src_pull_reports_an_unknown_source_name_as_a_config_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    out, err = run_pull(capsys, "nowhere", status=1)

    assert out == ""
    assert "error: no config source named 'nowhere'" in err
    assert "personal" in err


def test_src_pull_reports_a_trust_violation_in_what_is_deployed(
    deployed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (deployed / ACCOUNT_UNIT).chmod(0o666)

    out, err = run_pull(capsys, status=1)

    assert out == ""
    assert str(deployed / ACCOUNT_UNIT) in err
