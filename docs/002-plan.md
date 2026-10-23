# atc Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- Date: 2026-10-23
- Status: draft
- Design: [001-design.md](001-design.md) · Concept: [000-concept.md](000-concept.md)

**Goal:** Turn `repo.conf/` (`claudeconf`) into `atc`, which generates each account's settings, links its instructions file and skills, and starts guarded agent sessions, as the design specifies.

**Architecture:** One Python package in `atc/src/atc/`. Functions that decide (`decide_*`, `plan_*`, `validate_*`) are kept apart from those that write or exec (`apply_*`, `execute`), so nearly all behaviour is tested without side effects. Configuration comes only from config sources under `conf_d` and from `machine.yaml`. Everything specific to Claude Code sits behind the `Backend` protocol.

**Tech Stack:** Python 3.13, uv, pydantic v2, ruamel.yaml, click with rich-click, rich, questionary, pytest, ruff, basedpyright (`all` mode).

**Spec:** `atc/docs/001-design.md`. Every `§N` below refers to it. Read the concept first, then the design, then your task.

## How this plan is organised

The phases follow design §17. **Checkpoints** are where live configuration changes. An agent prepares the change in this repository. You review it and run the commands that touch `~/.config/atc`, the account directories or `~/.zshrc`; the sandbox write-protects those from sessions anyway.

Every task is one commit, and the commits follow the git-commits skill:

- **One capability per commit.** Each commit lets you run something you couldn't run before. Infrastructure that nothing uses yet rides with its first user.
- **No dead code.** Every function, class, field and constant has a caller or reader in the commit that adds it. That includes `machine.yaml` keys: each key, with its validation and its dataclass field, arrives with the first code that reads it. The registry therefore grows over Tasks 4–18, and each checkpoint says which keys exist at that point.
- **Size.** Roughly 400–800 authored lines, tests included; `uv.lock` doesn't count. If a commit runs well past 800 lines, split it at the point named in its task.
- **Messages.** `component: description`, usually without a body. Trailers come from git flags, never from the message text.

| Task | Commit subject                                              | Est. lines   |
| :--- | :---------------------------------------------------------- | :----------- |
| 1    | `atc: move claudeconf to atc/ and rename it`                | ~100 + moves |
| 2    | `atc: list config sources with atc src ls`                  | ~700         |
| 3    | `atc: check units across config sources with atc src check` | ~550         |
| 4    | `atc: show an account's settings with atc show account`     | ~650         |
| 5    | `atc: expand templates in units from registry variables`    | ~650         |
| 6    | `atc: show a project's settings with atc show project`      | ~450         |
| 7    | `atc: warn about permission rules that can never apply`     | ~350         |
| 8    | `atc: deploy config sources only after checking them`       | ~400         |
| 9    | `atc: generate account settings with atc sync`              | ~750         |
| —    | Checkpoint A: account settings come from units              |              |
| 10   | `atc: link each account's instructions file`                | ~600         |
| 11   | `atc: link the skills selected for each account`            | ~650         |
| 12   | `atc: choose an account's skills with atc skills pick`      | ~400         |
| —    | Checkpoint B: instructions file and skills                  |              |
| 13   | `atc: start the agent in the right account with atc run`    | ~600         |
| 14   | `atc: apply the project layer to sessions started by atc`   | ~300         |
| 15   | `atc: guard the repository's packed history in sessions`    | ~450         |
| 16   | `atc: check a session's protections with atc check`         | ~400         |
| —    | Checkpoint C: launching through atc                         |              |
| 17   | `atc: keep guarded repositories packed with atc maint`      | ~300         |
| 18   | `atc: manage registered projects with atc prj`              | ~450         |
| 19   | `atc: add and remove working trees with atc wt`             | ~350         |
| —    | Checkpoint D: maintenance timer and projects from ccwtctl   |              |
| 20   | `atc: show each account's recorded usage`                   | ~250         |
| 21   | `atc: pick the least used account with -a auto`             | ~200         |
| —    | Checkpoint E: usage recording, shared memory, cleanup       |              |

The estimates are judgement, not measurement. Task 1 sits below the skill's ~200-line floor on purpose: it is a pure rename, kept apart so that git follows every moved file (Task 1 says more).

## Global Constraints

- Python `>=3.13`. Package `atc` in `atc/src/atc/`, script `atc = "atc.cli:main"`.
- Runtime dependencies: `pydantic>=2.9`, `ruamel.yaml`, `click>=8.2`, `rich-click`, `rich`, `questionary`, each added with `uv add` by the task that first imports it. Nothing else.
- `./atc/check-and-fix.sh` passes before every commit: ruff format, ruff check, basedpyright in `all` mode, pytest.
- No `Any` and no bare `object` outside three boundaries: `json.loads` in `jsonval`, ruamel in `yamlval`, and questionary's `unsafe_ask()` in `ui` (`ask()` prints to stdout on Ctrl-C, which is kept for data). Each carries a scoped `# pyright: ignore[...]` and a comment saying why it is sound. Don't read click's `ctx.obj`.
- pydantic validates and never transforms what reaches output. Settings are always taken verbatim from the parsed document (existing `CLAUDE.md` rule).
- Tests use fixture sources and registries under `tmp_path` only. They never read `~/.config/atc`, `~/.claude`, `~/.config/acme` or this repository's `conf/`. An autouse fixture (Task 2) isolates `HOME`, the XDG variables, git's configuration, and `ATC_*` and `CLAUDE_CONFIG_DIR`.
- Exit codes: 0 success; 1 configuration error (`ConfigError`); 2 usage error, or `atc check` outside a sandbox; 3 `check` failure, or `sync` left something unwritten; 130 interrupted (Ctrl-C) or a cancelled picker.
- Only `atc show` writes machine-readable data to stdout. Other commands write their primary output, such as tables, to stdout for people. Diagnostics always go to stderr as `error: …` or `warning: …`, naming the file and JSON pointer where there is one.
- `Ui` methods take plain text and escape it, so rich never reads outside text as markup.
- Help text never mentions design, plan or review documents.
- No machine-specific value in product code: everything comes from `Dirs`, the registry or the backend. Literal paths appear only in test fixtures.
- Run tools from the repository root, without `cd … &&` (the command-shape hook refuses it): `uv run --directory atc pytest tests/test_x.py -v`.
- Commits: `component: description` with components `atc`, `conf`, `assets`, `skills`, `docs`; subject at most 72 characters; usually no body. Commits are made autonomously on the `atc` branch, following the git-autonomous-commits skill:
  - Run `git commit --no-gpg-sign -s -m "…"`. The `-s` sign-off is the only trailer. Never add one that names the agent or model: no `Co-Authored-By:`, no `Assisted-by:`, no `Claude-Session:`.
  - Stage files by name. Never `git add -A` or `git add .`.
  - Run git commands one at a time, never chained with `&&` or `;`.
  - Never `git -C` or `git -c` (the command-shape hook refuses both).
  - The user's permission rules deny any git command containing the words ` pull`, ` push`, ` reset`, ` checkout`, ` restore`, ` worktree`, `remote` or `tag` anywhere after `git`, including inside a commit message. Word commit messages around them (for example "deploy", "working tree"). Tests that run git through `subprocess` aren't affected.

## Review Focus

1. **A hand-broken live `settings.json`** (invalid JSON). `sync` treats it as edited outside atc: it shows the text and writes only with `--discard-live`, or `--take-over` if it was never synced. It never crashes or overwrites silently. Tested in Task 9.
2. **`--settings` passed after `--`** (`atc -- --settings x`, `--settings=x`). It is refused, because Claude Code honours only the last `--settings` (F2) and would silently drop the guard. Tested in Task 13.
3. **An account directory written differently from how it resolves** (`~/.claude/`, a symlinked path). Default-directory detection, `peers` and "no two accounts share a dir" all compare normalized paths. Tested in Tasks 4 and 5.
4. **`XDG_RUNTIME_DIR` unset, or `/run/user/<uid>` missing** (ssh, cron). A guarded launch warns and continues without the lock instead of failing. Tested in Task 15.
5. **A fresh clone with group-writable files** (umask 002). The trust error names the file and says `chmod go-w`. Tested in Task 2.

---

## Phase 1: Rename (§17 step 1)

### Task 1: Move `repo.conf` to `atc/` and rename the package

The authored change is small, about 100 lines. It stays a commit of its own because it moves every file of the tool. Mixing those moves with Task 2's rewrites would stop git from seeing them as renames, and `git log --follow` and blame would lose each file's history. It passes the smell test on its own: one purpose (claudeconf becomes atc, in `atc/`), its parent works, it reverts cleanly, and its tests pass on the renamed package.

**Files:**

- Move with `git mv`:
  - `repo.conf/{.gitignore,CLAUDE.md,README.md,check-and-fix.sh,pyproject.toml,pyrightconfig.json,uv.lock,tests}` → `atc/`
  - `repo.conf/src/claudeconf/` → `atc/src/atc/`
  - `repo.conf/conf.d/acme/` → `conf/acme/`, `repo.conf/conf.d/langs/` → `conf/langs/`
- Delete: `atc/src/atc/install.py`, `atc/tests/test_install.py`
- Modify: `atc/pyproject.toml`, `atc/src/atc/{cli,paths}.py`, `atc/tests/*.py`, `atc/CLAUDE.md`, `atc/README.md`

**Interfaces:**

- Produces: package `atc`, and `atc build UNIT... [--root DIR] [--lax]`, which behaves exactly as `claudeconf build` did. `install` is gone; `atc src pull` (Task 8) replaces it.

- [ ] **Step 1: Move the files.** `atc/` already holds `docs/`, so move entries individually. Run each git command as its own call:

```bash
git mv repo.conf/.gitignore repo.conf/CLAUDE.md repo.conf/README.md repo.conf/check-and-fix.sh repo.conf/pyproject.toml repo.conf/pyrightconfig.json repo.conf/uv.lock repo.conf/tests atc/
mkdir -p atc/src conf
git mv repo.conf/src/claudeconf atc/src/atc
git mv repo.conf/conf.d/acme conf/acme
git mv repo.conf/conf.d/langs conf/langs
git rm -q atc/src/atc/install.py atc/tests/test_install.py
git ls-files repo.conf
```

Expected: the last command prints nothing, because no tracked file is left under `repo.conf/`. Untracked leftovers remain (`.venv`, caches, `.claude/.cc-writes`); with `.gitignore` moved, `git status` shows them as `??`. Sessions can't run `rm`, so ask the user to delete `repo.conf/`.

- [ ] **Step 2: Rename.**
  - Replace `claudeconf` with `atc` in every import under `atc/src` and `atc/tests`. Set `PROGRAM = "atc"` and `APP_NAME = "atc"`.
  - `pyproject.toml`:
    - name `atc`
    - description "Start coding agents in the right account, with settings built from shared configuration"
    - `[project.scripts] atc = "atc.cli:main"`
    - wheel `packages = ["src/atc"]`
  - Remove the `install` subcommand from `cli.py`, along with its three tests in `test_cli.py`:
    - `test_install_reports_where_it_installed`
    - `test_build_reads_the_installed_root_by_default`
    - `test_installing_over_an_existing_root_is_a_config_error`
- [ ] **Step 3: Update the documentation.**
  - **`atc/CLAUDE.md`:**
    - Title `atc — Project Rules`.
    - Intro: source in `src/atc/`, tests in `tests/`. Units are not part of the tool; they live in config sources.
    - Components: `atc`, `conf`, `assets`, `skills`, `docs`.
    - Keep every other rule.
  - **`atc/README.md`:** a short rewrite covering:
    - what atc does (one paragraph, from the concept)
    - development: `uv run --directory atc atc …` and `./atc/check-and-fix.sh`
    - install: `uv tool install ./atc` from a reviewed commit, never `--editable` (§3)
- [ ] **Step 4: Verify.** `uv sync --directory atc`, then `./atc/check-and-fix.sh`. Expected: all green, with the same tests as before minus the install ones.
- [ ] **Step 5: Commit.** The moves and deletions from Step 1 are already staged. Stage each edited file by name, check `git status` and `git diff --cached --stat`, then run `git commit --no-gpg-sign -s -m "atc: move claudeconf to atc/ and rename it"`.

---

## Phase 2: Core (§17 step 2)

### Task 2: List config sources with `atc src ls`

This task also switches the command line to click and rich. On its own, that switch would be infrastructure with nothing new to run, so it comes with the first command that needs rich's tables.

**Files:**

- Create:
  - `atc/src/atc/ui.py`, `atc/src/atc/exitcodes.py`
  - `atc/src/atc/sources.py`, `atc/src/atc/git.py`
  - `atc/src/atc/cli/__init__.py`, `atc/src/atc/cli/build.py`, `atc/src/atc/cli/src.py`
  - `atc/tests/helpers.py`
  - `atc/tests/test_ui.py`, `test_paths.py`, `test_sources.py`, `test_cli_src.py`
- Delete: `atc/src/atc/cli.py`
- Modify:
  - `atc/src/atc/paths.py`: `Dirs`, replacing `user_config_root`
  - `atc/src/atc/errors.py`: `SourceError(ConfigError)`, `GitError(ConfigError)`
  - `atc/tests/conftest.py`, `atc/tests/test_cli.py`
  - `atc/pyproject.toml`: `uv add click rich-click rich`
  - `atc/CLAUDE.md`: the typing section no longer mentions argparse's `_raw`

**Interfaces:**

- **`exitcodes`:** `OK = 0`, `CONFIG = 1`, `USAGE = 2`. `CHECK = 3` arrives with Task 9, its first user.
- **`ui`:** `Ui(out: Console, err: Console)`, frozen. Methods:
  - `Ui.default() -> Ui` builds `Console()` and `Console(stderr=True)`
  - `error(message: str) -> None`: red `error:` prefix, on stderr
  - `warning(message: str) -> None`: yellow `warning:` prefix, on stderr
  - `json(value: JsonValue) -> None`: stdout. Syntax-highlighted when stdout is a terminal, otherwise exactly `jsonval.dumps(value)`. `build` prints through it.
  - `table(table: Table) -> None`: stdout

  Messages are escaped with `rich.markup.escape`. Styled output is built from `rich.text.Text`, never from markup strings that contain outside text.

- **`cli`:**
  - `app`: the root `click.Group`, named `atc`, with `--version`. Commands are registered in `cli/__init__.py` with `app.add_command(...)`.
  - `main(argv: Sequence[str] | None = None) -> int`:
    - invokes `app` with `standalone_mode=False`
    - `ConfigError` → `Ui.error`, returns 1
    - `click.UsageError` → click's message, returns 2
    - `click.exceptions.Exit` → its code
    - otherwise 0
  - `atc build UNIT... [--root DIR] [--lax]` is ported unchanged.
- **`paths`:**
  - `Dirs(config_dir: Path, conf_d: Path)`, a frozen dataclass. Task 5 adds `state_dir` and `runtime_dir` when variables first read them.
  - `dirs_from_env(env: Mapping[str, str], home: Path) -> Dirs`. An XDG value that is set but not absolute is ignored (§4).
- **`sources`:**
  - `SOURCE_DIRS = ("conf", "assets", "skills")`.
  - `Source(name: str, root: Path)`. Properties `conf`, `assets` and `skills` return the subdirectory paths. `display(rel: str) -> str` returns `"@{name}/{rel}"`, or `rel` when `name` is `""`.
  - `Sources(conf_d: Path, layout: Literal["single", "multi"], items: tuple[Source, ...], warnings: tuple[str, ...])`. `items` are sorted by name.
  - `discover(conf_d: Path, *, uid: int) -> Sources`. Implements every rule in §6: layouts, depth, ignored and unused entries, names, trust. It raises `SourceError`:
    - no `conf_d`: the message includes `git clone <url> <conf_d>`
    - sources at both levels
    - no sources
    - a bad name
    - a trust violation. For a group- or world-writable entry the message is `"{path}: group- or world-writable; run chmod go-w '{path}'"`.
- **`git`:**
  - `run_git(args: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> str`: stdout stripped, raises `GitError`
  - `git_or_none(...) -> str | None`
- **CLI:** `atc src ls [--conf-d DIR]`. A table, one row per source:
  - name, path, layout, unit count, asset file count
  - for git clones: branch, short commit, `dirty` if `git status --porcelain -- conf assets skills` is non-empty, and ahead/behind from `git rev-list --left-right --count HEAD...@{u}` (blank when there is no upstream)
- **Test helpers (`tests/helpers.py`):**
  - `UID = os.getuid()`
  - `unit(settings: JsonObject | None = None, *, imports: Sequence[str] = ()) -> JsonObject` returns a unit envelope. It uses `UNIT_VERSION`, so Task 3's version bump flows through.
  - `make_source(root: Path, files: Mapping[str, str | JsonObject]) -> Path` writes each file (a `JsonObject` as JSON), sets mode 0o644 on files and 0o755 on directories, and returns `root`
  - `git_repo(path: Path) -> Path` runs `git init -q -b main`, then `add -A`, then `commit -q -m init`
- **Fixtures (`tests/conftest.py`):**
  - autouse `isolated_env(tmp_path, monkeypatch) -> Path`:
    - sets `HOME=tmp_path/"home"` (created), `XDG_CONFIG_HOME`, `XDG_STATE_HOME` and `XDG_RUNTIME_DIR` under `tmp_path`
    - sets `GIT_CONFIG_GLOBAL=os.devnull`, `GIT_CONFIG_NOSYSTEM=1`, and the `GIT_AUTHOR_*` and `GIT_COMMITTER_*` names and emails
    - removes every `ATC_*`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_*`, `GIT_CONFIG_VALUE_*` and `CLAUDE_CONFIG_DIR`
    - returns home
  - `home` returns that path
  - `dirs` returns `dirs_from_env(os.environ, home)`

- [ ] **Step 1: Write the failing CLI and UI tests.** Port the existing `test_cli.py` tests unchanged (they call `main([...])` and read `capsys`). Add:

```python
def test_unknown_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bulid"]) == 2
    assert "No such command" in capsys.readouterr().err


def test_version_names_the_program(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert "atc" in capsys.readouterr().out


def test_a_config_error_exits_1_with_an_error_line(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["build", "missing.json", "--root", str(root)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error:")
```

`tests/test_ui.py`:

```python
def test_messages_are_not_read_as_markup(capsys: pytest.CaptureFixture[str]) -> None:
    Ui.default().error("[red]x[/red]")
    assert "[red]x[/red]" in capsys.readouterr().err


def test_json_is_canonical_when_stdout_is_not_a_terminal(capsys: pytest.CaptureFixture[str]) -> None:
    Ui.default().json({"b": 1, "a": [True]})
    assert capsys.readouterr().out == dumps({"b": 1, "a": [True]})


def test_no_color_is_honoured(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    Ui.default().warning("w")
    assert "\x1b[" not in capsys.readouterr().err
```

- [ ] **Step 2: Run them and see them fail**, then implement `ui.py`, `exitcodes.py`, `cli/__init__.py` and `cli/build.py`, and delete `cli.py`. Run until green.
  - Use `rich_click` for help rendering.
  - If basedpyright reports unknown types from rich-click's decorators, use plain `click` decorators with `cls=rich_click.RichGroup` and `cls=rich_click.RichCommand` instead.
  - Remove `_raw` and its siblings.
- [ ] **Step 3: Write the failing source tests** (`tests/test_sources.py`):

```python
def test_single_layout(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    found = discover(conf_d, uid=UID)
    assert found.layout == "single"
    assert [s.name for s in found.items] == [""]


def test_multi_layout_names_sources_after_their_directories(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(conf_d / "acme", {"assets/x.sh": "#!/bin/sh\n"})
    assert [s.name for s in discover(conf_d, uid=UID).items] == ["acme", "personal"]


def test_sources_at_both_levels_are_an_error(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit(), "personal/conf/b.json": unit()})
    with pytest.raises(SourceError, match="both"):
        _ = discover(conf_d, uid=UID)


def test_nothing_deeper_than_one_level_is_read(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(conf_d / "group" / "deep", {"conf/b.json": unit()})
    found = discover(conf_d, uid=UID)
    assert [s.name for s in found.items] == ["personal"]
    assert any("group" in w for w in found.warnings)


def test_hidden_entries_and_plain_files_are_ignored(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(conf_d, {".git/HEAD": "x", "README": "x"})
    assert discover(conf_d, uid=UID).warnings == ()


def test_source_names_follow_the_name_rule(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "Personal", {"conf/a.json": unit()})
    with pytest.raises(SourceError, match="Personal"):
        _ = discover(conf_d, uid=UID)


def test_a_symlink_under_conf_is_refused(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    (conf_d / "conf" / "b.json").symlink_to(conf_d / "conf" / "a.json")
    with pytest.raises(SourceError, match=r"b\.json"):
        _ = discover(conf_d, uid=UID)


def test_a_symlinked_source_directory_is_refused(tmp_path: Path) -> None:
    real = make_source(tmp_path / "checkout", {"conf/a.json": unit()})
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "conf.d" / "personal").symlink_to(real)
    with pytest.raises(SourceError, match="personal"):
        _ = discover(tmp_path / "conf.d", uid=UID)


def test_a_group_writable_file_is_refused_with_the_fix(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"assets/hook.sh": "#!/bin/sh\n"})
    (conf_d / "assets" / "hook.sh").chmod(0o664)
    with pytest.raises(SourceError, match=r"hook\.sh.*chmod go-w"):
        _ = discover(conf_d, uid=UID)


def test_entries_owned_by_someone_else_are_refused(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    with pytest.raises(SourceError, match="owned"):
        _ = discover(conf_d, uid=UID + 1)


def test_a_missing_conf_d_says_how_to_add_a_source(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="git clone"):
        _ = discover(tmp_path / "nope", uid=UID)
```

Also: `test_a_group_writable_conf_d_is_refused` (single and multi layout) and `test_a_group_or_world_writable_source_directory_is_refused`, each matching the path and `chmod go-w`.

`test_paths.py`: `test_an_xdg_value_that_is_not_absolute_is_ignored`, `test_conf_d_sits_under_the_config_dir`.

- [ ] **Step 4: Implement `paths.Dirs`, `dirs_from_env` and `sources`,** and run until green.
- [ ] **Step 5: Write the failing `src ls` tests** (`tests/test_cli_src.py`). Each builds `dirs.conf_d` with `make_source`:
  - `test_src_ls_lists_every_source`: both names appear in stdout
  - `test_src_ls_shows_branch_and_commit_of_git_clones`: after `git_repo(source)`, stdout contains `main` and `git rev-parse --short HEAD`
  - `test_src_ls_marks_uncommitted_changes`: edit `conf/a.json` after the commit → stdout contains `dirty`
  - `test_src_ls_reports_a_trust_violation_as_a_config_error`: exit 1, the path in stderr
  - `test_conf_d_option_reads_another_directory`
- [ ] **Step 6: Implement `git.py` and `cli/src.py`,** and run until green.
- [ ] **Step 7: Run `./atc/check-and-fix.sh`,** then **commit** `atc: list config sources with atc src ls`. If this commit runs well past 800 lines, split the click and rich switch out into a commit of its own, ahead of `src ls`.

### Task 3: Check units across config sources with `atc src check`

**Files:**

- Create: `atc/src/atc/refs.py`, `atc/src/atc/validate.py`, `atc/tests/test_refs.py`
- Modify:
  - `atc/src/atc/unit.py`: envelope version 2, which is when imports can first cross sources (§7.1)
  - `atc/src/atc/resolve.py`: walks across sources
  - `atc/src/atc/sources.py`: `Sources.named`
  - `atc/src/atc/paths.py`: `resolve_unit` moves into `refs`
  - `atc/src/atc/ui.py`: `info`
  - `atc/src/atc/cli/src.py`, `atc/src/atc/cli/build.py`
  - `atc/tests/test_unit.py`, `test_resolve.py`, `test_cli_src.py`, `test_cli.py`
  - `conf/**/*.json`: `"version": 2`

**Interfaces:**

- **`unit`:** `UNIT_VERSION = 2`, `version: Literal[2]`. Templates (Task 5) are the other half of what version 2 means, and arrive before any version 2 unit uses `{{`.
- **`sources`:** `Sources.named(name: str) -> Source` raises `PathError`.
- **`refs`:**
  - `Located(source: Source, path: Path, display: str)`.
  - `resolve_import(sources: Sources, origin: Source, text: str) -> Located` for imports (§7.2):
    - `@name/` crosses into the named source and is an error in the single layout
    - the target must be a relative path ending in `.json`, contained in its source's `conf/`
  - `resolve_registry(sources: Sources, text: str) -> Located | None` for unit references given outside a unit:
    - unqualified: exactly one source may contain it. If none does, it returns `None`; if several do, it raises `PathError` asking for an `@name/` prefix
    - qualified: a missing target raises `PathError`
- **`resolve`:** `resolve(sources: Sources, roots: Sequence[Located], *, lax: bool) -> Resolution`. Same merge-once semantics as today. Messages name units with `Located.display`.
- **`ui`:** `info(message: str) -> None`, stderr.
- **`validate`:** `Validation(units: int, errors: tuple[str, ...], warnings: tuple[str, ...])` and `validate_sources(sources: Sources, *, lax: bool) -> Validation`. It loads every `*.json` under every `conf/` and resolves each as a root, collecting every error rather than stopping at the first.
- **CLI:**
  - `atc src check [--conf-d DIR]`: prints `ok: N units in M sources`, or every error, and exits 1.
  - `atc build UNIT... [--conf-d DIR] [--lax]`: each `UNIT` goes through `resolve_registry`, and `None` is a `PathError` "no such unit". `--root` is gone.

- [ ] **Step 1: Write the failing envelope tests.** In `tests/test_unit.py`, replace the version tests, and change every other fixture document from `"version": 1` to `"version": 2`:

```python
def test_a_minimal_unit_loads(write_unit: UnitWriter) -> None:
    unit = load_unit(write_unit("a.json", {"version": 2}))
    assert unit.settings == {}
    assert unit.imports == ()


def test_version_1_is_refused(write_unit: UnitWriter) -> None:
    with pytest.raises(UnitError, match="version"):
        _ = load_unit(write_unit("a.json", {"version": 1}))
```

- [ ] **Step 2: Write the failing reference and resolution tests.** `tests/test_refs.py`:
  - `test_an_import_without_at_stays_in_its_own_source`
  - `test_at_name_crosses_into_another_source`: `resolve_import(srcs, acme, "@personal/common/base.json").path == personal.conf / "common/base.json"`
  - `test_at_name_is_an_error_in_the_single_layout`: raises `PathError`, matching `"@"`
  - `test_registry_lookup_needs_exactly_one_match`: two sources contain `common/x.json` → `PathError` matching `"@name/"`
  - `test_registry_lookup_returns_none_when_no_source_has_it`
  - `test_imports_cannot_escape_conf`: `"../assets/x.json"` → `PathError`
  - `test_unit_references_must_end_in_json`

  Port `tests/test_resolve.py`: each test builds a single-layout source with `make_source` and calls `resolve(sources, [resolve_registry(...)], lax=False)`. Add:
  - `test_imports_follow_into_a_named_source`
  - `test_errors_name_units_by_source_and_path`: the message contains `@acme/projects/x.json`

- [ ] **Step 3: Implement the version bump, `refs` and the new `resolve`,** and run until green.
- [ ] **Step 4: Write the failing CLI tests:**
  - `test_src_check_passes_a_valid_tree`: exit 0, stderr contains `ok: 2 units in 1 sources`
  - `test_src_check_reports_every_broken_unit`: two malformed units → exit 1, both paths in stderr
  - `test_src_check_reports_a_dangling_import`
  - `test_src_check_follows_imports_across_sources`
  - `test_build_resolves_units_across_sources`: replaces the `--root` tests in `test_cli.py`
- [ ] **Step 5: Implement `validate.py`, `src check` and the `build` changes,** and run until green.
- [ ] **Step 6: Run `./atc/check-and-fix.sh`,** then **commit** `atc: check units across config sources with atc src check`.

### Task 4: Show an account's settings with `atc show account`

**Files:**

- Create:
  - `atc/src/atc/yamlval.py`, `atc/src/atc/registry.py`, `atc/src/atc/context.py`, `atc/src/atc/layers.py`
  - `atc/src/atc/cli/show.py`
  - `atc/tests/test_yamlval.py`, `test_registry.py`, `test_layers.py`, `test_cli_show.py`
- Delete: `atc/src/atc/cli/build.py` and its tests. `show account` replaces it.
  - Move `test_output_has_sorted_keys_and_a_trailing_newline` and `test_identical_input_produces_identical_output` to `test_cli_show.py`.
  - Rewrite `test_a_config_error_exits_1_with_an_error_line` against `show account missing`.
- Modify:
  - `errors.py`: `RegistryError(ConfigError)`, `PolicyError(ConfigError)`
  - `resolve.py`: transform hook
  - `validate.py`, `cli/src.py`, `cli/__init__.py`
  - `pyproject.toml`: `uv add ruamel.yaml`
  - `atc/CLAUDE.md`: name the ruamel boundary in the typing section

**Interfaces:**

- **`yamlval`:**
  - `parse_yaml(text: str, origin: str) -> JsonValue`
  - `load_yaml(path: Path) -> JsonValue`

  Both use ruamel `typ="safe"` with YAML 1.2. They raise `RegistryError` for: syntax errors, duplicate keys, non-string keys, and any value that isn't a JSON type. The last message says `quote it`.

- **`registry`.** In this task the registry accepts only `version` and `accounts.<name>.{dir, unit}`; every other §5 key arrives with its first reader (see the table in "How this plan is organised").
  - `REGISTRY_FILE = "machine.yaml"`
  - `Account(name: str, dir: Path, unit: str)`
  - `Registry(path: Path, accounts: Mapping[str, Account], warnings: tuple[str, ...])`. Mappings keep file order. `account(name: str) -> Account` raises `RegistryError` naming the known accounts.
  - `parse_registry(text: str, origin: Path, home: Path) -> Registry` and `load_registry(path: Path, home: Path) -> Registry`. They validate with pydantic models (`extra="forbid"`, `strict=True`, kebab-case aliases), then build the dataclasses. Paths go through `normalize_path(text: str, home: Path) -> Path`:
    - expand a leading `~` with `home`
    - require an absolute path
    - `Path(text).resolve(strict=False)`, so a trailing `/` or a symlinked prefix compares equal

    Account names match `[a-z0-9][a-z0-9_-]*`. Two accounts with the same normalized `dir` are a `RegistryError`.

- **`context`:**
  - `BuildContext(registry: Registry, sources: Sources, dirs: Dirs, home: Path)`, frozen.
  - `load_context(*, env: Mapping[str, str], home: Path, uid: int, conf_d: Path | None) -> BuildContext`. `conf_d` overrides `dirs.conf_d`.
- **`resolve`:** `type UnitTransform = Callable[[Located, JsonObject], JsonObject]`. `resolve(..., transform: UnitTransform | None = None)` applies it to each unit's settings before the key check and the merge.
- **`layers`:**
  - `LayerKind = Literal["account", "project", "session"]`
  - `Layer(kind: LayerKind, settings: JsonObject, warnings: tuple[str, ...])`
  - `POLICY: Mapping[str, frozenset[LayerKind]] = {"/autoMemoryDirectory": frozenset({"account"})}`. Task 6 adds the project-layer row.
  - `check_policy(settings: JsonObject, kind: LayerKind, origin: str) -> None` raises `PolicyError(f"{origin}: {pointer} is not allowed in the {kind} layer")`
  - `build_account_layer(ctx: BuildContext, account: Account, *, lax: bool) -> Layer`. A missing account unit is an error. The policy check runs on each unit through the transform.
- **`validate`:** `validate_config(ctx: BuildContext, *, lax: bool) -> Validation`. It runs `validate_sources`, then every account's `unit` reference, then builds every account layer.
- **CLI:**
  - `atc show account NAME [--conf-d DIR] [--lax]` prints through `Ui.json`, with warnings on stderr.
  - `src check` uses `validate_config` when `machine.yaml` exists. Otherwise it uses `validate_sources` and warns that the registry wasn't checked.
- **Fixture:** `write_registry(dirs) -> Callable[[str], Path]` writes dedented text to `dirs.config_dir / "machine.yaml"`.

- [ ] **Step 1: Write the failing YAML and registry tests.** Use `MINIMAL` as the base text:

```python
MINIMAL = """\
    version: 1
    accounts:
      personal: {dir: ~/.claude}
    """


def test_minimal_registry_loads(write_registry: RegistryWriter, home: Path) -> None:
    r = load_registry(write_registry(MINIMAL), home)
    assert r.accounts["personal"].dir == home / ".claude"
    assert r.accounts["personal"].unit == "accounts/personal.json"


def test_two_accounts_cannot_share_a_dir_however_it_is_written(write_registry: RegistryWriter, home: Path) -> None:
    text = MINIMAL + "      other: {dir: ~/.claude/}\n"
    with pytest.raises(RegistryError, match="share"):
        _ = load_registry(write_registry(text), home)


def test_an_unquoted_timestamp_is_refused(home: Path) -> None:
    with pytest.raises(RegistryError, match="quote"):
        _ = parse_yaml("when: 2026-10-23\n", "t")


def test_no_stays_a_string() -> None:
    assert parse_yaml("flag: no\n", "t") == {"flag": "no"}
```

Also write:

- `test_duplicate_keys_are_refused`
- `test_non_string_keys_are_refused` (`1: x`)
- `test_misspelled_keys_are_refused` (`acounts`)
- `test_relative_paths_are_refused`
- `test_environment_variables_are_not_expanded` (`dir: $HOME/x`)
- `test_account_names_follow_the_name_rule`

- [ ] **Step 2: Implement `yamlval` and `registry`,** and run `uv run --directory atc pytest tests/test_yamlval.py tests/test_registry.py -v` until green.
- [ ] **Step 3: Write the failing layer tests** (`tests/test_layers.py`), using a registry plus `make_source`:
  - `test_account_layer_merges_the_account_unit_and_its_imports`
  - `test_the_registry_can_name_another_account_unit`: `unit: "@acme/accounts/x.json"`
  - `test_a_missing_account_unit_is_an_error`
  - `test_auto_memory_directory_is_refused_in_the_account_layer`: matches `/autoMemoryDirectory` and `accounts/personal.json`
  - `test_policy_names_the_imported_unit_that_set_the_key`
- [ ] **Step 4: Implement the `resolve` transform hook, `context` and `layers`,** and run until green.
- [ ] **Step 5: Write the failing CLI tests** (`tests/test_cli_show.py`):
  - `test_show_account_prints_canonical_json`: stdout equals `dumps(expected)`
  - `test_show_unknown_account_is_a_config_error`: exit 1, the known names are in stderr
  - `test_show_lax_warns_about_unknown_keys`: exit 0, `warning:` in stderr
  - `test_src_check_builds_every_account_layer`: an account unit that breaks policy → exit 1, naming the unit
  - `test_src_check_without_a_registry_checks_sources_only`
  - the two moved determinism tests
- [ ] **Step 6: Implement `cli/show.py`, the `validate_config` wiring and the removal of `build`,** and run until green.
- [ ] **Step 7: Run `./atc/check-and-fix.sh`,** then **commit** `atc: show an account's settings with atc show account`.

### Task 5: Expand templates in units from registry variables

**Files:**

- Create:
  - `atc/src/atc/template.py`, `atc/src/atc/variables.py`
  - `atc/src/atc/backends/__init__.py`, `atc/src/atc/backends/claude.py`
  - `atc/tests/test_template.py`, `test_variables.py`, `test_backend_claude.py`
- Modify:
  - `errors.py`: `TemplateError(ConfigError)`
  - `registry.py`: `vars`, `accounts.<name>.agent`, `accounts.<name>.vars`
  - `paths.py`: `Dirs.state_dir`, `Dirs.runtime_dir`; `dirs_from_env` gains `uid`
  - `context.py`: an `install_dir: Path` field. `load_context` fills it from `Path(sys.prefix)`.
  - `layers.py`: the transform expands templates, then checks policy
  - `tests/conftest.py`: `dirs` passes `UID`
  - `tests/test_registry.py`, `tests/test_layers.py`: `agent` is now required, so add `agent: claude` to `MINIMAL` and every other fixture registry

**Interfaces:**

- **`registry`:**
  - `Registry.vars: Mapping[str, str]`
  - `Account.agent: str`: required, `Literal["claude"]` in the model
  - `Account.vars: Mapping[str, str]`
  - Variable names match `[a-z][a-z0-9_]*`. Values must not contain `{{`.
- **`paths`:**
  - `Dirs` gains `state_dir` and `runtime_dir`
  - `dirs_from_env(env, home, uid)`. The runtime fallback is `/run/user/<uid>`.
- **`template`:**
  - `type VarValue = str | tuple[str, ...]`
  - `REFERENCE = re.compile(r"\{\{([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\}\}")`
  - `expand(settings: JsonObject, variables: Mapping[str, VarValue], *, origin: str) -> JsonObject`. Errors are `TemplateError(f"{origin}: {pointer}: …")`. `origin` names the unit and the layer, for example `"accounts/acme.json (account layer for acme)"`.
- **`backends`:**
  - the `Backend` protocol. In this task: `name: str`, `default_dir() -> Path`, `is_default(account: Account) -> bool`, `files(account: Account) -> tuple[Path, ...]`, `secrets(account: Account) -> tuple[Path, ...]`
  - `backend_for(agent: str, home: Path) -> Backend`
  - `ClaudeBackend(home: Path)` implements §9's rows for these members
- **`variables`:**
  - `layer_variables(ctx: BuildContext, account: Account) -> dict[str, VarValue]`. Keys: every account-layer key in §7.4, which is every key except `project.*`, including `atc.install_dir`. `sources.<name>.*` exist only in the multi layout. Task 6 adds a `project` parameter.
  - `with_source(variables: Mapping[str, VarValue], source: Source) -> dict[str, VarValue]` adds `source.name`, `source.root`, `source.conf` and `source.assets`.

**Expansion algorithm.** The signature doesn't determine it. Substitution happens in one pass, so substituted text is never scanned again:

```text
expand_value(value, bindings, pointer):
  str  -> substitute(value, bindings, pointer)
  dict -> a key containing "{{" is an error; values via expand_value
  list -> concatenate expand_element(elem, bindings, pointer/i) for each element
expand_element(elem, bindings, pointer):
  refs = distinct list-valued references in strings reachable from elem without
         entering a nested list, excluding names already in bindings
  none   -> [expand_value(elem, bindings, pointer)]
  one r  -> [expand_value(elem, bindings | {r: item}, pointer) for item in variables[r]]
            (an empty list yields [], which removes the element; it is still
             expanded once with a placeholder item and the result discarded,
             so a bad reference inside it is an error)
  more   -> error "only one list reference per array element"
substitute(text, bindings, pointer):
  a "{{" that doesn't start a REFERENCE match -> error "malformed reference"
  each match: bound name -> its item; str variable -> its value;
              list variable -> error "list reference outside an array element";
              unknown -> error "unknown reference {{name}}"
```

- [ ] **Step 1: Write the failing template tests:**

```python
V: dict[str, VarValue] = {
    "home": "/h",
    "account.dir": "/h/.claude",
    "peers.dir": ("/p1", "/p2"),
    "peers.files": ("/f",),
    "none": (),
}


def test_scalar_references_are_replaced_anywhere() -> None:
    assert expand({"a": "x{{home}}y"}, V, origin="u") == {"a": "x/hy"}


def test_a_list_reference_expands_a_string_element() -> None:
    got = expand({"a": ["Read({{peers.dir}}/**)", "k"]}, V, origin="u")
    assert got == {"a": ["Read(/p1/**)", "Read(/p2/**)", "k"]}


def test_a_list_reference_expands_an_object_element() -> None:
    got = expand({"f": [{"path": "{{peers.dir}}", "mode": "deny"}]}, V, origin="u")
    assert got == {"f": [{"path": "/p1", "mode": "deny"}, {"path": "/p2", "mode": "deny"}]}


def test_an_empty_list_removes_the_element() -> None:
    assert expand({"a": ["{{none}}", "k"]}, V, origin="u") == {"a": ["k"]}


def test_nested_arrays_bind_to_the_nearest_element() -> None:
    got = expand({"a": [{"b": ["{{peers.dir}}"], "c": "{{home}}"}]}, V, origin="u")
    assert got == {"a": [{"b": ["/p1", "/p2"], "c": "/h"}]}


def test_two_list_references_in_one_element_are_an_error() -> None:
    with pytest.raises(TemplateError, match="one list reference"):
        _ = expand({"a": [{"x": "{{peers.dir}}", "y": "{{peers.files}}"}]}, V, origin="u")


def test_a_list_reference_outside_an_array_is_an_error() -> None:
    with pytest.raises(TemplateError, match="outside an array"):
        _ = expand({"a": "{{peers.dir}}"}, V, origin="u")


def test_an_unknown_reference_names_origin_and_pointer() -> None:
    with pytest.raises(TemplateError, match=r"^u: /a/b/0: unknown reference \{\{nope\}\}"):
        _ = expand({"a": {"b": ["{{nope}}"]}}, V, origin="u")


@pytest.mark.parametrize("text", ["{{ home }}", "{{Home}}", "{{home", "a{{b"])
def test_malformed_references_are_errors(text: str) -> None:
    with pytest.raises(TemplateError, match="malformed"):
        _ = expand({"a": text}, V, origin="u")


def test_keys_are_never_substituted() -> None:
    with pytest.raises(TemplateError, match="key"):
        _ = expand({"{{home}}": 1}, V, origin="u")


def test_substituted_text_is_not_scanned_again() -> None:
    assert expand({"k": "{{a}}"}, {"a": "{{b}}", "b": "z"}, origin="u") == {"k": "{{b}}"}


def test_non_strings_pass_through() -> None:
    assert expand({"a": [1, True, None, 2.5]}, V, origin="u") == {"a": [1, True, None, 2.5]}
```

- [ ] **Step 2: Implement `template.py`,** and run until green.
- [ ] **Step 3: Write the failing backend, variable and registry tests:**
  - `test_the_default_dir_account_owns_the_home_state_file`: personal's `files == (home/".claude.json",)`; acme's `files == ()`
  - `test_secrets_for_the_default_and_other_dirs`:
    - personal: `(home/".claude/.credentials.json", home/".claude.json")`
    - acme: `(d/".credentials.json", d/".claude.json")`
  - `test_default_dir_detection_survives_a_trailing_slash`: `dir: ~/.claude/` still counts as the default
  - `test_peers_are_every_other_account_of_any_agent`
  - `test_source_variables_point_at_the_units_own_source`: an acme unit using `{{source.assets}}` builds to acme's `assets/`
  - `test_named_source_variables_exist_only_in_the_multi_layout`
  - `test_install_dir_comes_from_the_context`
  - `test_atc_directories_follow_xdg`: `atc.state_dir` and `atc.runtime_dir`, including the `/run/user/<uid>` fallback when `XDG_RUNTIME_DIR` is unset
  - `test_the_isolation_unit_builds_the_bravo_entries`: the §7.3 example unit, built for acme, gives exactly the credential entries and `Read`/`Edit` rules listed in §7.3
  - Registry:
    - `test_an_unknown_agent_is_refused`
    - `test_variable_values_must_not_contain_braces`
    - `test_variable_names_follow_the_name_rule`
- [ ] **Step 4: Implement the registry fields, `paths`, `backends`, `variables` and the layer wiring,** and run until green.
- [ ] **Step 5: Run `./atc/check-and-fix.sh`,** then **commit** `atc: expand templates in units from registry variables`.

### Task 6: Show a project's settings with `atc show project`

**Files:**

- Modify:
  - `registry.py`: `default-account`, and `projects.<name>.{repo, account, accounts, unit, vars}`
  - `layers.py`: `build_project_layer`, and the second `POLICY` row
  - `variables.py`: `project.*`
  - `validate.py`, `cli/show.py`
- Test: `test_registry.py`, `test_layers.py`, `test_variables.py`, `test_cli_show.py`

**Interfaces:**

- **`registry`:**
  - `Registry.default_account: str`, required. Add `default-account: personal` to `MINIMAL` and every other fixture registry in the existing tests.
  - `Registry.projects: Mapping[str, Project]`
  - `Project(name: str, repo: Path, account: str | None, accounts: tuple[str, ...] | None, unit: str, unit_is_default: bool, vars: Mapping[str, str])`
  - Consistency checks: `default-account` and every project's `account` and `accounts` name existing accounts; a project's `account` is in its `accounts` when both are set; project names follow the name rule.
  - `repo` is normalized like `dir`. Its reader is the `project.repo` variable.
  - `choose_account(registry: Registry, project: Project | None, override: str | None) -> Account`, per §10.3 step 4: `-a`, then the project's `account`, then `default-account`. It raises `RegistryError` if the project's `accounts` doesn't allow the account. `show project` uses it now, and `atc run` from Task 13.
- **`layers`:**
  - `POLICY` gains `"/sandbox/allowUnsandboxedCommands": frozenset({"project", "session"})`. Task 13's `session_layer` is the first to check the `"session"` kind.
  - `build_project_layer(ctx: BuildContext, project: Project, account: Account, *, lax: bool) -> Layer | None`. It returns `None` only when `unit_is_default` and no source has the unit.
- **`variables`:** `layer_variables(ctx, account, project: Project | None = None)` adds `project.name`, `project.repo` and `project.vars.<name>` when a project is given.
- **`validate`:** `validate_config` also builds each project layer with each account the project allows, or with every account when it sets no `accounts`.
- **CLI:** `atc show project NAME [-a ACCOUNT] [--conf-d DIR] [--lax]`.

- [ ] **Step 1: Write the failing tests:**
  - **Registry:**
    - `test_default_account_must_exist`
    - `test_project_account_must_be_in_its_accounts`
    - `test_every_mentioned_account_must_exist`
    - `test_project_unit_defaults_and_is_marked_default`
    - `test_account_precedence_is_option_then_project_then_default`
  - **Layers:**
    - `test_project_layer_is_none_when_the_default_unit_is_absent`
    - `test_a_missing_explicit_project_unit_is_an_error`
    - `test_allow_unsandboxed_commands_is_refused_in_the_project_layer`
    - `test_project_variables_exist_only_in_the_project_layer`: `{{project.name}}` in an account unit is an unknown-reference error
  - **CLI:**
    - `test_show_project_uses_the_projects_default_account`
    - `test_show_project_refuses_an_account_the_project_does_not_allow`: exit 1
    - `test_src_check_builds_every_project_with_each_allowed_account`: a project unit that breaks policy → exit 1, naming the project unit
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: show a project's settings with atc show project`.

### Task 7: Warn about permission rules that can never apply

**Files:**

- Create: `atc/src/atc/rules.py`, `atc/tests/test_rules.py`
- Modify:
  - `layers.py`: `Layer.origins`
  - `cli/show.py`, `validate.py`
  - design §2 and §19, with the probe result

**Interfaces:**

- **`layers`:** `Layer.origins: Mapping[tuple[str, str], tuple[str, ...]]`. It maps `(list_name, rule)` to the displays of the units that contributed the rule, in merge order. `list_name` is `allow`, `ask` or `deny`. The transform records it after expansion, from each unit's `permissions` lists.
- **`rules`:**
  - `RuleWarning(dead_list: Literal["allow", "ask"], dead_rule: str, dead_origins: tuple[str, ...], by_list: Literal["ask", "deny"], by_rule: str, by_origins: tuple[str, ...])`. `message() -> str` gives `"allow Bash(git rebase:*) (projects/anvil.json) never applies: deny Bash(git rebase:*) (common/base.json) takes precedence"`.
  - `covers(by: str, rule: str) -> bool`, per §8.
  - `dead_rules(layers: Sequence[Layer]) -> tuple[RuleWarning, ...]`: at most one warning per dead rule, naming the strongest cover (a deny over an ask, then the earliest in merge order).

**`covers`:**

```text
parse "Tool" or "Tool(spec)"; anything else never covers and is never covered
different tools -> False
by has no spec -> True                  (the whole tool)
rule has no spec -> False
identical text -> True
tool is Bash: replace a trailing ":*" with " *" in both specs; the by spec,
  with only "*" special (".*" in a regex, everything else re.escape'd),
  fullmatches the rule spec read literally -> True
otherwise False
```

- [ ] **Step 1: Probe Claude Code's matching** (open question 4). This needs network access to `api.anthropic.com` (Bash `allowed_domains`). It works only before Checkpoint A, which hides account credentials from sessions; this task comes before it. In a scratch git repository under `$TMPDIR`, run headless `claude -p --model haiku --no-session-persistence` sessions with permission rules in `--settings`:
  - a deny on `Bash(echo a:*)`, then ask the model to run `echo a b`
  - the same with `Bash(echo a *)`
  - a deny on `Bash(echo a *)`, then ask it to run `echo a` (bare)

  Record whether each was refused. If `:*` and ` *` behave differently, change the normalization in `covers` and the test table below to match what was observed. Write the observed facts into design §2 as a new fact, and close question 4 in §19.

- [ ] **Step 2: Write the failing tests:**

```python
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
    ],
)
def test_covers(by: str, rule: str, expected: bool) -> None:
    assert covers(by, rule) is expected
```

Also write:

- `test_an_allow_covered_by_a_deny_across_layers_names_both_units`: the base unit in the account layer denies, the project unit allows; the message contains both unit displays
- `test_an_ask_covered_by_a_deny`
- `test_an_allow_covered_by_an_ask`
- `test_a_partial_overlap_is_not_reported`
- `test_a_rule_is_reported_once_with_the_strongest_cover`
- CLI: `test_show_project_warns_without_changing_output_or_status`: stdout equals the JSON without warnings, exit 0, and `never applies` in stderr
- CLI: `test_src_check_reports_rule_warnings_and_still_passes`

`show project` checks the account and project layers together. `show account` checks the account layer alone.

- [ ] **Step 3: Implement,** and run until green.
- [ ] **Step 4: Run `./atc/check-and-fix.sh`,** then **commit** `atc: warn about permission rules that can never apply`, together with the design update from Step 1.

### Task 8: Deploy config sources with a checked `src pull`

**Files:**

- Create: `atc/src/atc/pull.py`, `atc/tests/test_pull.py`
- Modify: `sources.py` (`Sources.replace`), `cli/src.py`

**Interfaces:**

- **`sources`:** `Sources.replace(name: str, root: Path) -> Sources`, the same sources with one root swapped.
- **`pull`:**
  - `PullResult(name: str, status: Literal["updated", "current", "skipped", "refused", "failed"], detail: str, validation: Validation | None)`
  - `pull(sources: Sources, names: Sequence[str], validate: Callable[[Sources], Validation]) -> list[PullResult]`

  It runs §10.1's four steps per source. The whole commit is written into a `tempfile.TemporaryDirectory` the way checkout writes it: `git read-tree <sha>` into a temporary `GIT_INDEX_FILE`, then `git checkout-index --all` with `GIT_WORK_TREE=<tmp>` and the temporary index (not `--prefix`, which would read the clone's old `.gitattributes` instead of the incoming commit's). Not `git archive`, which honours the incoming commit's `export-ignore` and `export-subst` attributes and so could check a different tree from the one the merge deploys. The candidate gets the same `go-w` normalization as after the merge, then `pull()` itself runs the trust rules and discovery's shape checks on it before calling `validate`. Untracked files outside `conf/`, `assets/` and `skills/` don't count as uncommitted changes. After the fast-forward, the `conf/`, `assets/` and `skills/` directories and everything under them lose group and world write (`chmod go-w`, skipping symbolic links), because git applies the umask to what it writes. If that chmod fails, the status is `failed`, with the path in `detail`.

- **CLI:** `atc src pull [NAME...]` prints one line per source, plus the validation errors of any that failed. It exits 1 if any source failed or was refused.

- [ ] **Step 1: Write the failing tests** with real git. A bare upstream repository, cloned into `conf_d`, then a second clone used to push new commits:
  - `test_a_valid_upstream_commit_is_deployed`: `HEAD` of the source equals the upstream commit, status `updated`
  - `test_an_invalid_upstream_commit_is_not_deployed`: the new commit adds a malformed unit; status `failed`, `HEAD` unchanged, the error names the unit
  - `test_uncommitted_changes_are_refused`: edit `conf/a.json` in the deployed clone; status `refused`
  - `test_a_diverged_clone_is_refused`: a local commit in the deployed clone; status `refused`, mentioning fast-forward
  - `test_a_source_that_is_not_a_git_clone_is_skipped`
  - `test_already_current_is_reported`
  - `test_validation_sees_the_other_sources_unchanged`: the incoming commit imports `@other/x.json`, which exists only in the other source; status `updated`
  - `test_a_pull_under_umask_002_leaves_a_trusted_tree`: with `os.umask(0o002)` around the pull, a commit that adds `conf/new/x.json` and a first `skills/a/SKILL.md` (new directories at both levels) is deployed, and `discover` accepts the source afterwards
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: deploy config sources only after checking them`.

---

## Phase 3: Account settings (§17 step 3)

### Task 9: Generate account settings with `atc sync`

**Files:**

- Create:
  - `atc/src/atc/fsutil.py`, `atc/src/atc/state.py`, `atc/src/atc/sync.py`
  - `atc/src/atc/cli/sync.py`
  - `atc/tests/test_fsutil.py`, `test_sync.py`, `test_cli_sync.py`
- Modify: `jsonval.py` (`parse_json`), `exitcodes.py` (`CHECK = 3`), `backends/` (`settings_file`, `render`, `parse`), `cli/__init__.py`

**Interfaces:**

- **`jsonval`:** `parse_json(text: str, origin: str) -> JsonValue`, the same strictness as `load_json`, which now delegates to it.
- **`Backend`:**
  - `settings_file(account: Account) -> Path`
  - `render(settings: JsonObject) -> str`, which is `dumps`
  - `parse(text: str, origin: str) -> JsonObject`, raising `UnitError` when the text isn't an object
- **`fsutil`:**
  - `atomic_write(path: Path, text: str, *, default_mode: int = 0o600) -> None`: a temporary file in the same directory, fsync, the existing file's mode kept, then `os.replace`
  - `backup(path: Path, backups_dir: Path, now: datetime, *, keep: int = 10) -> Path`: copies to `backups_dir / f"{now:%Y%m%dT%H%M%SZ}-{path.name}"` (a `-N` suffix on collision), and keeps the newest `keep` with the same name
- **`state`:** `SyncState(state_dir: Path, account: str)`, frozen:
  - `root` is `state_dir/"sync"/account`
  - `backups_dir`
  - `last_settings() -> JsonObject | None`. A missing or unparseable file gives `None`, which forces the "never synced" path; it is never trusted.
  - `record_settings(settings: JsonObject) -> None`
- **`sync`:**
  - `Situation = Literal["missing", "current", "never-synced", "unchanged", "edited"]`
  - `SettingsPlan(situation: Situation, new: JsonObject, new_text: str, live_text: str | None, last_text: str | None, write: bool, blocked: bool)`
  - `decide_settings(new: JsonObject, live_text: str | None, last: JsonObject | None, *, parse: Callable[[str], JsonObject], take_over: bool, discard_live: bool) -> SettingsPlan`. This is the §10.2 table. Live text that doesn't parse equals nothing: "never-synced" when there is no `last`, otherwise "edited".
  - `AccountPlan(account: Account, settings_path: Path, settings: SettingsPlan, rule_warnings: tuple[RuleWarning, ...], warnings: tuple[str, ...])`
  - `plan_account(ctx: BuildContext, account: Account, *, take_over: bool, discard_live: bool, lax: bool) -> AccountPlan`. A missing account directory is a `ConfigError`.
  - `apply_account(plan: AccountPlan, ctx: BuildContext, now: datetime) -> None`. Back up the live file if it exists, write atomically, record `last`. For "current", only record `last`.
- **CLI:** `atc sync [ACCOUNT...] [--check] [--diff] [--take-over] [--discard-live] [--lax] [--conf-d DIR]`.
  1. Plan every named account (all by default). Any `ConfigError` exits 1 before anything is written.
  2. Print one status line per account: `written`, `current`, `would write`, `never synced and differs (--take-over writes it)`, or `edited outside atc (--discard-live overwrites it)`.
  3. Print unified diffs (`difflib`) for `--diff`, and always for a blocked account.
  4. Print rule warnings.
  5. `--check` and `--diff` write nothing; otherwise apply.
  6. Exit 3 if any account is blocked, or with `--check` if anything would be written.

  `--conf-d` without `--check` or `--diff` is a usage error (exit 2).

- [ ] **Step 1: Write the failing decision tests:**

```python
NEW: JsonObject = {"a": 1}
CLAUDE = ClaudeBackend(Path("/h"))


def parse_live(text: str) -> JsonObject:
    return CLAUDE.parse(text, "live")


def plan(live: str | None, last: JsonObject | None, *, take_over: bool = False, discard_live: bool = False) -> SettingsPlan:
    return decide_settings(NEW, live, last, parse=parse_live, take_over=take_over, discard_live=discard_live)


def test_a_missing_file_is_written() -> None:
    p = plan(None, None)
    assert (p.situation, p.write, p.blocked) == ("missing", True, False)


def test_reformatting_alone_is_current() -> None:
    p = plan('{"a":1}', {"a": 2})
    assert (p.situation, p.write) == ("current", False)


def test_never_synced_and_different_needs_take_over() -> None:
    assert (plan('{"a": 2}', None).blocked, plan('{"a": 2}', None, take_over=True).write) == (True, True)


def test_unchanged_since_the_last_sync_is_rewritten() -> None:
    p = plan('{"a": 2}', {"a": 2})
    assert (p.situation, p.write) == ("unchanged", True)


def test_edited_outside_atc_stops_unless_discarded() -> None:
    assert plan('{"a": 3}', {"a": 2}).situation == "edited"
    assert plan('{"a": 3}', {"a": 2}).blocked
    assert plan('{"a": 3}', {"a": 2}, discard_live=True).write


def test_an_unparseable_live_file_counts_as_edited() -> None:
    p = plan("{nope", {"a": 2})
    assert (p.situation, p.blocked) == ("edited", True)


def test_an_unparseable_never_synced_file_needs_take_over() -> None:
    assert plan("{nope", None).situation == "never-synced"
```

- [ ] **Step 2: Implement `jsonval.parse_json`, the backend members and `sync.decide_settings`,** and run until green.
- [ ] **Step 3: Write the failing apply and CLI tests:**
  - `test_fsutil.py`:
    - `test_atomic_write_keeps_the_mode` (0o644 stays 0o644)
    - `test_atomic_write_leaves_no_temporary_file_on_failure`
    - `test_backups_keep_the_newest_ten_per_file_name`: twelve `settings.json` backups and one `CLAUDE.md` backup leave ten plus one
  - `test_cli_sync.py`:
    - `test_first_sync_writes_settings_and_records_last`
    - `test_check_exits_3_when_a_write_is_pending_and_writes_nothing`
    - `test_check_exits_0_when_current`
    - `test_diff_writes_nothing`
    - `test_edited_outside_atc_shows_the_diff_and_exits_3`
    - `test_take_over_backs_up_and_writes`
    - `test_an_error_in_one_account_writes_no_account`: two accounts, one with a broken unit; neither file changes, exit 1
    - `test_conf_d_requires_check_or_diff`: exit 2
    - `test_a_missing_account_dir_is_a_config_error`
    - `test_rule_warnings_are_printed`
- [ ] **Step 4: Implement `fsutil`, `state`, `plan_account`, `apply_account` and `cli/sync.py`,** and run until green.
- [ ] **Step 5: Run `./atc/check-and-fix.sh`,** then **commit** `atc: generate account settings with atc sync`.

### Checkpoint A: account settings come from units

At this point the registry accepts `version`, `default-account`, `vars`, `accounts.<name>.{agent, dir, unit, vars}` and `projects.<name>.{repo, account, accounts, unit, vars}`.

**The agent prepares, in this repository.** Each commit is proposed for review, and each is something a reviewer can accept or reject on its own:

1. **`conf: build both accounts' settings from shared units`.** These files only make sense together: the shared units are useless until an account imports them, and the account units point at the assets.
   - **`conf/common/base.json`:** the entries that are identical in both live `settings.json` files.
   - **`conf/common/hardening.json`:**
     - strict mode: `sandbox.allowUnsandboxedCommands: false`, `sandbox.failIfUnavailable: true`
     - the isolation unit from §7.3
     - the denies from §13 item 2 for `{{atc.config_dir}}`, `{{atc.state_dir}}`, `{{atc.runtime_dir}}` and `{{atc.install_dir}}`
   - **`conf/accounts/personal.json` and `conf/accounts/acme.json`:** each imports both shared units, and keeps its remaining entries **unchanged**. The differences between the accounts' rules are yours to reconcile later; rule warnings will point out any dead ones.
   - **`assets/hooks/block-bad-cmd-shape.sh`:**
     - one script with every check from both copies: checks 1–5, plus the newline-aware `cd` check
     - logging only when given `--log FILE`. The acme unit's hook command passes `--log {{account.dir}}/hooks/commands.log`.
   - **Other assets:**
     - `assets/statusline.sh`: a **copy** of `statusline-command.sh`, with §11's `ATC_ACCOUNT` block. The original stays until Checkpoint E, because both account directories link to it today.
     - `assets/bin/clean-scratch-targets.sh`
     - units refer to all of these through `{{source.assets}}`
2. **`conf: fix the misspelled network allowlist for the acme account`.** The one-line `allowedDomanins` → `allowedDomains` fix. It is small, but it changes what the acme sandbox allows, so it gets its own commit for review, and can be reverted alone.

**The agent verifies,** with a throwaway `machine.yaml` under `$TMPDIR` (`XDG_CONFIG_HOME` pointed there):

- `atc src check --conf-d <repo>` passes.
- `atc sync --diff --conf-d <repo>` shows only these changes against the live files: hook and statusline command paths, the acme allow rules naming `clean-scratch-targets.sh` (now its asset path), the hardening entries, and the typo fix. Anything else is a mistake in the units.

**You:**

1. `uv tool install ./atc`.
2. `git clone <this repository> ~/.config/atc/conf.d`, then `chmod -R go-w ~/.config/atc/conf.d` (with Fedora's umask 002 a fresh clone is group-writable, which the trust rules refuse). Write `~/.config/atc/machine.yaml` with `version`, `default-account`, `vars`, and both accounts' `agent`, `dir` and `vars`.
3. `atc src check`, `atc sync --diff`, then `atc sync --take-over`.
4. Start a session in each account, and confirm that the hook refuses a `cd … && …` command and that the statusline shows.

---

## Phase 4: Instructions file and skills (§17 step 4)

### Task 10: Link each account's instructions file

**Files:**

- Create: `atc/src/atc/links.py`, `atc/tests/test_links.py`
- Modify:
  - `registry.py`: `default-agents-md`, `use-default-agents-md`, `agents-md-file`
  - `refs.py`: the `asset` kind
  - `backends/`: `instructions_file`
  - `state.py`: links
  - `sync.py`: link plans, parts
  - `cli/sync.py`, `validate.py`
  - `tests/test_cli_sync.py`

**Interfaces:**

- **`registry`:**
  - `Registry.default_agents_md: str`, defaulting to `"AGENTS.md"`
  - `Account.use_default_agents_md: bool`
  - `Account.agents_md_file: Path | str | None`. A `Path` is a filesystem path (the value started with `/` or `~`); a `str` is an asset reference.
  - A warning when both `agents-md-file` and `use-default-agents-md: true` are set.
- **`refs`:** `RefKind = Literal["unit", "asset"]`. `resolve_registry(sources, text, kind: RefKind = "unit")`. An asset reference is a relative path to an existing file under `assets/`.
- **`Backend`:** `instructions_file(account: Account) -> Path`, which is `<dir>/CLAUDE.md` for Claude.
- **`state`:**
  - `SyncState.links() -> dict[str, str]`: entry path to target, both absolute, from `links.json`; missing or unparseable gives `{}`
  - `record_links(links: Mapping[str, str]) -> None`
- **`links`:**
  - `LinkAction = Literal["none", "create", "repoint", "remove", "replace-link", "replace-file", "conflict", "leave"]`
  - `LinkPlan(entry: Path, wanted: Path | None, recorded: Path | None, action: LinkAction)`, with properties:
    - `pending` (not `none`, `leave` or `conflict`)
    - `blocked` (`conflict`)
    - `describe() -> str`, for example `CLAUDE.md: (none) → /…/assets/AGENTS.md`
  - `decide_link(entry: Path, wanted: Path | None, recorded: Path | None, *, take_over: bool) -> LinkPlan`. It reads the filesystem with `lstat` and `readlink` and never writes. It implements §10.2's links table:

    ```text
    missing                            -> create if wanted, else none
    link whose target == wanted        -> none
    link whose target == recorded      -> repoint if wanted, else remove
    any other link (dangling included) -> wanted: replace-link with take_over, else conflict; not wanted: leave
    regular file                       -> wanted: replace-file with take_over, else conflict; not wanted: leave
    directory                          -> wanted: conflict (even with take_over); not wanted: leave
    ```

  - `apply_link(plan: LinkPlan, backups_dir: Path, now: datetime) -> Path | None`. It creates missing parent directories, backs up the file before `replace-file`, makes absolute symlinks, never touches a directory, and returns the target that atc now owns at `entry` (`None` if none).
  - `wanted_instructions(ctx: BuildContext, account: Account) -> Path | None`, per §10.2 "Instructions file". A missing target is a `ConfigError`.

- **`sync`:**
  - `Part = Literal["settings", "instructions"]`, `ALL_PARTS: frozenset[Part]`. Task 11 adds `"skills"`.
  - `plan_account(..., parts: frozenset[Part] = ALL_PARTS)`. `AccountPlan.links: tuple[LinkPlan, ...]`. `settings` becomes `SettingsPlan | None`, which is `None` when `"settings"` isn't in `parts`.
  - `apply_account` updates `links.json`. Entries outside this run's parts keep their records. Each applied entry's record is set to `apply_link`'s return value, or removed when that is `None`. `conflict` and `leave` don't change the record.
- **CLI:** `sync` lists each pending or blocked link with `describe()`. Exit 3 also covers blocked links.

- [ ] **Step 1: Write the failing link tests** (`tests/test_links.py`), one per row of the table above plus:
  - `test_take_over_backs_up_a_regular_file_before_linking`: the backup content equals the old file
  - `test_a_directory_is_never_replaced_even_with_take_over`
  - `test_a_missing_parent_directory_is_created`
  - `test_links_are_absolute`

  And for `wanted_instructions`:
  - `test_agents_md_file_path_wins_over_the_flag`
  - `test_agents_md_file_can_be_an_asset_reference`: `"@acme/AGENTS.md"`
  - `test_the_flag_links_the_default`
  - `test_neither_set_wants_nothing`
  - `test_a_missing_target_is_a_config_error`
  - `test_an_ambiguous_default_asks_for_a_source_prefix`

- [ ] **Step 2: Implement `refs` (asset), the `registry` fields, `Backend.instructions_file`, `state` links and `links`,** and run until green.
- [ ] **Step 3: Write the failing sync tests** in `tests/test_cli_sync.py`:
  - `test_a_hand_written_claude_md_is_a_conflict_and_left_alone`: exit 3, file unchanged
  - `test_take_over_replaces_it_with_a_link_and_keeps_a_backup`
  - `test_an_account_without_claude_md_gets_the_link`
  - `test_turning_the_flag_off_removes_atcs_link`
  - `test_turning_the_flag_off_leaves_a_hand_written_file`
  - `test_diff_lists_the_link_change_and_writes_nothing`
  - `test_src_check_fails_when_the_default_instructions_file_is_missing`: an account uses the default, and no source has `assets/AGENTS.md`
  - `test_both_keys_set_warns`
- [ ] **Step 4: Implement the `sync`, `cli/sync.py` and `validate` changes,** and run until green.
- [ ] **Step 5: Run `./atc/check-and-fix.sh`,** then **commit** `atc: link each account's instructions file`.

### Task 11: Link the skills selected for each account

**Files:**

- Create:
  - `atc/src/atc/skills.py`
  - `atc/src/atc/cli/skills.py`
  - `atc/tests/test_skills.py`, `test_cli_skills.py`
- Modify:
  - `registry.py`: `skills`
  - `refs.py`: the `skill` kind
  - `backends/`: `skills_dir`
  - `sync.py`: the skills part
  - `validate.py`, `cli/src.py` (a skills column), `cli/__init__.py`

**Interfaces:**

- **`registry`:** `Account.skills: tuple[str, ...]`, defaulting to `()`.
- **`refs`:** `RefKind` adds `"skill"`: one path segment naming a directory under `skills/` that contains `SKILL.md`.
- **`Backend`:** `skills_dir(account: Account) -> Path`, which is `<dir>/skills` for Claude.
- **`skills`:**
  - `Skill(name: str, source: Source, path: Path, description: str)`
  - `Catalog(skills: tuple[Skill, ...], warnings: tuple[str, ...])`. `resolve(ref: str, sources: Sources) -> Skill` goes through `refs.resolve_registry(..., "skill")`, then finds the matching `Skill`; anything missing is a `PathError`.
  - `discover_skills(sources: Sources) -> Catalog`:
    - only direct subdirectories of each `skills/` that contain `SKILL.md`
    - names must match `[a-z0-9][a-z0-9_-]*`, otherwise a warning and the directory is skipped
    - front matter is the text between a leading `---` line and the next `---` line, parsed with `yamlval.parse_yaml`
    - `description` is the front matter's `description` if it's a string, else `""`
    - a warning when the front matter's `name` differs from the directory name, or when the front matter doesn't parse
  - `wanted_skill_links(ctx: BuildContext, account: Account, catalog: Catalog) -> dict[Path, Path]` maps `<skills_dir>/<name>` to `skill.path`. Two selected skills with the same name are a `ConfigError`.
- **`sync`:** `Part` adds `"skills"`. The skills part plans one link per wanted skill, plus `decide_link(entry, None, recorded)` for every recorded entry under the skills directory that is no longer wanted. Nothing else in that directory is looked at.
- **CLI:**
  - `atc skills ls [ACCOUNT...]`: a table with columns name, source, description (truncated to 60 characters), and one column per account:
    - `linked`
    - `selected` (not linked yet)
    - `conflict`
    - blank when not selected

    Then, per account, the entries in its skills directory that atc doesn't manage, dimmed.

  - `src ls` gains a skills count.
  - `validate_config` resolves every account's `skills`, and checks for duplicate names.

- [ ] **Step 1: Write the failing tests:**
  - **Discovery:**
    - `test_only_direct_subdirectories_with_skill_md_are_skills`
    - `test_a_bad_skill_name_is_skipped_with_a_warning`
    - `test_the_description_comes_from_front_matter`
    - `test_a_front_matter_name_mismatch_warns`
    - `test_unparseable_front_matter_warns_and_the_skill_still_counts`
  - **Resolution:**
    - `test_an_ambiguous_skill_name_needs_a_source_prefix`
    - `test_at_name_selects_a_skill_from_one_source`
    - `test_two_selected_skills_with_one_name_are_an_error`
  - **Sync**, in `tests/test_cli_sync.py`:
    - `test_selected_skills_are_linked_into_the_account`
    - `test_a_missing_skills_directory_is_created`
    - `test_a_deselected_skill_loses_atcs_link`
    - `test_a_foreign_skill_link_is_a_conflict_until_take_over`: a link into another directory, as `install-skills.sh` makes
    - `test_directories_and_unrecorded_entries_are_left_alone`: `phase-review/` (a directory), `synced/`, `.trash/`
  - **CLI:**
    - `test_skills_ls_shows_each_accounts_state`
    - `test_src_check_fails_when_a_selected_skill_is_gone`
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: link the skills selected for each account`.

### Task 12: Choose an account's skills with `atc skills pick`

**Files:**

- Modify:
  - `yamlval.py`: `set_in_text`
  - `registry.py`: `set_skills`
  - `ui.py`: `PickChoice`, `Ui.pick`
  - `cli/skills.py`
  - `atc/CLAUDE.md`: name the questionary boundary (§15)
  - `pyproject.toml`: `uv add questionary`
- Test: `test_yamlval.py`, `test_registry.py`, `test_cli_skills.py`

**Interfaces:**

- **`yamlval`:** `set_in_text(text: str, keys: Sequence[str], value: JsonValue) -> str`. A ruamel round trip (`typ="rt"`) that creates intermediate mappings, keeps comments and key order, and writes lists in flow style.
- **`registry`:** `set_skills(path: Path, home: Path, account: str, refs: Sequence[str]) -> Registry`:
  1. read the text
  2. `set_in_text(text, ["accounts", account, "skills"], list(refs))`
  3. `parse_registry` the new text, which raises on an unknown account or invalid result, leaving the file untouched
  4. `fsutil.atomic_write`
  5. return the new `Registry`
- **`ui`:**
  - `PickChoice(value: str, label: str, checked: bool)`
  - `Ui.pick(title: str, choices: Sequence[PickChoice]) -> list[str] | None`: `questionary.checkbox`, narrowed immediately to `list[str] | None`, where `None` means cancelled. If stdin or stdout isn't a terminal, it raises `click.UsageError("atc skills pick needs a terminal; use atc skills add or atc skills rm")`.
- **CLI:**
  - `atc skills pick ACCOUNT [--take-over]`
  - `atc skills add ACCOUNT SKILL... [--take-over]`
  - `atc skills rm ACCOUNT SKILL...`

  Each writes the selection, runs `plan_account(..., parts=frozenset({"skills"}))` and `apply_account`, then prints the link changes. Exit 3 if a conflict remains. A selection entry is written as the bare name unless that name is ambiguous, in which case it's `@source/name`. Labels read `name  source  description…`.

- [ ] **Step 1: Write the failing tests:**
  - `test_set_in_text_keeps_comments_and_other_keys`
  - `test_set_skills_refuses_an_unknown_account_and_leaves_the_file`
  - `test_add_then_rm_round_trips_the_selection`
  - `test_adding_an_unknown_skill_exits_1_and_changes_nothing`
  - `test_pick_writes_the_selection_and_links_it`: monkeypatch `Ui.pick` to return `["git-commits"]`
  - `test_cancelling_the_picker_changes_nothing`: `Ui.pick` returns `None`
  - `test_pick_without_a_terminal_is_a_usage_error`: exit 2
  - `test_an_ambiguous_name_is_written_with_its_source`
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: choose an account's skills with atc skills pick`.

### Checkpoint B: instructions file and skills

**The agent:** commits a copy of `~/.claude/CLAUDE.md` as `assets/AGENTS.md` (`assets: add the default instructions file`).

**You:**

1. Reinstall atc (`uv tool install --reinstall ./atc`), then `atc src pull`.
2. Add `use-default-agents-md: true` to both accounts in `machine.yaml`.
3. `atc sync --diff`, then `atc sync --take-over`.
4. Start a session in each account, and ask "What do your global instructions say about commit trailers?". The answer must mention `Claude-Session:`. That settles open question 3. If it doesn't, stop: the design needs another way to deliver the file, such as a one-line `@import` file.
5. `atc skills pick personal --take-over`, then `atc skills pick acme --take-over`. Both accounts' current links point into the working checkout, so each selected skill needs `--take-over`. Confirm that `phase-review/`, `_old/`, `synced/` and `.trash/` are untouched.
6. Remove, by hand, any remaining links into the working checkout for skills you didn't select (`ls -l <account dir>/skills`). atc never touches entries it didn't make, and those links would let a session edit what later sessions are told (§13 item 8).

**The agent, afterwards:** `git rm install-skills.sh` (`skills: retire install-skills.sh in favour of atc skills`).

---

## Phase 5: Launch (§17 step 5)

### Task 13: Start the agent in the right account with `atc run`

The design's `run` is split across Tasks 13–15, so each commit stays inside the size range and delivers one capability. This task starts the agent in the right account, with your own `--settings`. Task 14 adds the project layer, and Task 15 adds the guard.

**Files:**

- Create:
  - `atc/src/atc/launch.py`
  - `atc/src/atc/cli/run.py`
  - `atc/tests/test_launch.py`, `test_cli_run.py`
- Modify:
  - `cli/__init__.py`: `AtcGroup`
  - `git.py`: `common_dir`
  - `registry.py`: `agents.<agent>.bin`
  - `layers.py`: `session_layer`
  - `backends/`: `environment`, `argv`

**Interfaces:**

- **`registry`:** `Registry.agent_bins: Mapping[str, str]`, from `agents.<agent>.bin`.
- **`cli`:** `AtcGroup(click.Group)`. When there are no arguments, or the first argument starts with `-` and isn't `--help`, `-h` or `--version`, it inserts `run`. That includes `--`, which `alias claude='atc --'` relies on. `app` becomes an `AtcGroup` (a `RichGroup` subclass if Task 2 used rich-click classes).
- **`git`:** `common_dir(cwd: Path) -> Path | None`. It runs `git rev-parse --path-format=absolute --git-common-dir`, resolves the result, and returns `None` outside a repository.
- **`layers`:** `session_layer(settings: JsonObject, origin: str) -> Layer` runs `check_policy(..., "session", origin)`.
- **`Backend`:**
  - `environment(account: Account, env: Mapping[str, str]) -> dict[str, str]`: Claude sets `CLAUDE_CONFIG_DIR=<dir>`, or removes it for the default directory
  - `argv(bin: str, fragment: JsonObject, args: Sequence[str]) -> list[str]`: `[bin, "--settings", canonical(fragment), *args]`, without `--settings` when the fragment is `{}`
- **`launch`:**
  - `find_project(registry: Registry, common: Path | None, *, git_common_dir: Callable[[Path], Path | None]) -> Project | None`
  - `read_settings_arg(value: str) -> JsonObject`: a value starting with `{` is JSON text, otherwise a file path. Strict JSON, not a template.
  - `LaunchPlan(cwd: Path, common_dir: Path | None, project: Project | None, account: Account, fragment: JsonObject, env: dict[str, str], env_changes: dict[str, str | None], argv: list[str], warnings: tuple[str, ...])`
  - `plan_launch(ctx: BuildContext, *, cwd: Path, env: Mapping[str, str], account: str | None, settings: JsonObject | None, agent_args: Sequence[str], self_path: Path, which: Callable[[str], str | None] = shutil.which, git_common_dir: Callable[[Path], Path | None] = git.common_dir) -> LaunchPlan`:
    - **Fragment:** your `--settings` through `session_layer`, or `{}`.
    - **Environment:** drop every `ATC_*`; set `ATC_ACCOUNT`, `ATC_STATE_DIR`, and `ATC_PROJECT` when there is a project; then apply `Backend.environment`.
    - **Agent arguments:** `--settings` or `--settings=…` among them raises `click.UsageError`, explaining that only one `--settings` takes effect and atc's own would be lost.
    - **Binary:** `agents.<agent>.bin`, otherwise `which(agent)`. Not found is a `ConfigError`. If `Path(bin).resolve() == self_path.resolve()`, it's a `ConfigError` saying the agent binary resolves to atc.
  - `execute(plan: LaunchPlan) -> NoReturn`: `os.execvpe(plan.argv[0], plan.argv, plan.env)`
- **CLI:** `atc [run] [-a ACCOUNT] [--settings FILE|JSON] [--print] [-q] [--conf-d DIR] [-- AGENT-ARGS...]`.
  - `--conf-d` only with `--print`.
  - `--print` renders the plan as a rich tree, plus the fragment as JSON, and exits 0.
  - Otherwise it prints one stderr line, `atc: project anvil · account acme` (`project –` outside one), unless `-q`, then executes.
  - `self_path` is `sys.argv[0]` resolved through `which` when it isn't a path.

- [ ] **Step 1: Write the failing tests:**
  - **Group:**
    - `test_no_arguments_means_run`
    - `test_a_leading_option_means_run`: `-a personal --print`
    - `test_help_is_the_groups_help`
    - `test_a_typo_never_starts_a_session`: exit 2, `execute` not called
    - `test_a_leading_double_dash_means_run_with_agent_arguments`: `atc -- --resume x` plans `run` with `--resume x` at the end of `argv`
    - `test_an_unknown_option_before_double_dash_is_an_error`: exit 2
    - `test_arguments_after_double_dash_reach_the_agent`: `atc --print -- --resume x` puts `--resume x` at the end of `argv`
  - **Plan:**
    - `test_outside_git_there_is_no_project`
    - `test_an_unregistered_repository_uses_the_default_account`
    - `test_a_linked_worktree_finds_its_project`: real `git worktree add`
    - `test_atc_variables_are_replaced_or_removed`: a stale `ATC_PROJECT` and `ATC_FOO` are removed outside a project
    - `test_claude_config_dir_is_removed_for_the_default_dir_and_set_otherwise`
    - `test_your_settings_become_the_fragment`
    - `test_an_empty_fragment_adds_no_settings_flag`
    - `test_settings_after_double_dash_are_refused`: both `--settings` forms
    - `test_an_agent_binary_that_resolves_to_atc_is_refused`
    - `test_the_registry_can_name_the_agent_binary`
    - `test_your_settings_cannot_set_allow_unsandboxed_commands`
  - **CLI:**
    - `test_print_shows_the_plan_and_does_not_execute`: monkeypatch `launch.execute` to fail the test if called
    - `test_conf_d_requires_print`: exit 2
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: start the agent in the right account with atc run`.

### Task 14: Apply the project layer to sessions started by atc

**Files:**

- Modify: `launch.py`, `layers.py` (`merge_fragment`), `cli/run.py`
- Test: `test_launch.py`, `test_cli_run.py`

**Interfaces:**

- **`layers`:** `merge_fragment(layers: Sequence[Layer]) -> JsonObject` merges in the given order.
- **`launch`:**
  - `plan_launch(..., lax: bool, explain: bool)`. The fragment is the project layer, then your `--settings`.
  - `LaunchPlan.rule_warnings: tuple[RuleWarning, ...]`. It is computed only when `explain` is true (`--print`), over the account layer (built from the units), the project layer and the session layer together. A plain launch never builds the account layer, so it can't fail on an account unit; Claude Code uses the synced `settings.json`. Under `--print`, an account layer that can't be built gives the warning `rule check skipped for the account layer: …` and the project and session layers are checked alone, as in `show project`; `--print` still exits 0.
- **CLI:** `run` gains `--lax`. `--print` also prints rule warnings.

- [ ] **Step 1: Write the failing tests:**
  - `test_fragment_order_is_project_then_your_settings`: your `--settings` wins on a scalar
  - `test_a_project_without_a_unit_contributes_nothing`
  - `test_rule_warnings_are_computed_only_when_explaining`
  - `test_a_broken_account_unit_does_not_block_a_plain_launch`: with `explain=True` the plan still succeeds, with the rule-check-skipped warning
  - `test_lax_turns_unknown_project_keys_into_warnings`
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: apply the project layer to sessions started by atc`.

### Task 15: Guard the repository's packed history in sessions

**Files:**

- Create: `atc/src/atc/guard.py`, `atc/tests/test_guard.py`
- Modify: `registry.py` (`projects.<name>.snapshot`), `launch.py`, `backends/` (`session_fragment`), `cli/run.py` (`--no-guard`)

**Interfaces:**

- **`registry`:** `Project.snapshot: str | None`.
- **`guard`:**
  - `protected_paths(common_dir: Path) -> tuple[Path, ...]`: whichever of `objects/pack` and `packed-refs` exist
  - `with_gc_disabled(env: Mapping[str, str]) -> dict[str, str]`: appends `gc.auto=0` and `maintenance.auto=false` at index `GIT_CONFIG_COUNT` (default 0), and updates the count
  - `repo_key(common_dir: Path) -> str`: the first 16 hex digits of `sha256(str(common_dir))`
  - `register_repo(dirs: Dirs, common_dir: Path) -> None`: appends to `dirs.state_dir / "repos"` if absent
  - `open_session_lock(dirs: Dirs, common_dir: Path, *, timeout: float = 60.0) -> tuple[int | None, str | None]`: the descriptor (inheritable, shared `flock`), or `None` and a warning on timeout, or when `runtime_dir` can't be created or opened
  - `take_snapshot(config: str, cwd: Path, *, run: Callable[[Sequence[str]], int]) -> str | None`: runs `snapper -c <config> create --cleanup-algorithm number --description "atc: <cwd>"`, and returns a warning on failure
- **`Backend`:** `session_fragment(common_dir: Path) -> JsonObject`. For Claude, `{}` when there are no protected paths, otherwise:
  - `{"sandbox": {"filesystem": {"denyWrite": [str(p), ...]}}, "permissions": {"deny": ["Edit(/" + str(p) + "/**)" for directories, "Edit(/" + str(p) + ")" for files]}}`
- **`launch`:**
  - `LaunchPlan` gains `guarded: bool` and `snapshot: str | None`.
  - `plan_launch(..., no_guard: bool)`. Inside a repository and without `no_guard`, it sets `guarded=True`, adds the session fragment between the project layer and your `--settings`, applies `with_gc_disabled`, and sets `snapshot` to the project's `snapshot`. `--no-guard` inside a repository adds a warning.
  - `execute(plan: LaunchPlan, dirs: Dirs) -> NoReturn`. The signature gains `dirs`. When guarded, it registers the repository, opens the lock, then takes the snapshot, printing any warnings, then execs.
- **CLI:** the announcement says `guard on`, `guard off (--no-guard)` or `guard off (not in a repository)`.

- [ ] **Step 1: Write the failing tests:**
  - `test_only_existing_paths_are_protected`
  - `test_session_fragment_uses_absolute_edit_rules`: `"Edit(//…/objects/pack/**)"` and `"Edit(//…/packed-refs)"`
  - `test_session_entries_sit_between_the_project_layer_and_your_settings`
  - `test_gc_entries_are_added_after_existing_ones`: with `GIT_CONFIG_COUNT=1`, the keys `_1` and `_2` are added and the count becomes 3
  - `test_registering_twice_keeps_one_line`
  - `test_a_shared_lock_blocks_an_exclusive_one`: a second descriptor's `LOCK_EX | LOCK_NB` fails
  - `test_the_lock_outlives_exec_in_a_child`: start `sleep 2` with `close_fds=False`, close the parent's descriptor; an exclusive non-blocking lock still fails
  - `test_a_runtime_dir_that_cannot_be_created_warns_and_continues`: `dirs.runtime_dir` sits under a directory made read-only with `chmod 0o500`; the result is `(None, warning)`
  - `test_no_guard_leaves_out_entries_gc_and_lock_and_warns`
  - `test_outside_a_repository_is_unguarded_without_a_warning`
  - `test_a_failed_snapshot_warns_and_continues`
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: guard the repository's packed history in sessions`.

### Task 16: Check a session's protections with `atc check`

**Files:**

- Create:
  - `atc/src/atc/check.py`
  - `atc/src/atc/cli/check.py`
  - `atc/tests/test_check.py`
- Modify: `cli/__init__.py`

**Interfaces:**

- **`check`:**
  - `Probe` protocol: `writable(path: Path) -> bool`, `readable_contents(path: Path) -> bool`, `exists(path: Path) -> bool`, `git(args: Sequence[str]) -> str | None`, `readlink(path: Path) -> Path | None`
  - `RealProbe` uses the same `os.access` and character-device logic as `claude-guard-check`
  - `CheckResult(ok: bool, what: str)`
  - `in_sandbox(probe: Probe, home: Path) -> bool`: false when home is writable
  - `current_account(registry: Registry, env: Mapping[str, str], home: Path) -> Account | None`: `ATC_ACCOUNT` first; otherwise the account whose normalized `dir` equals `CLAUDE_CONFIG_DIR`; or, if that is unset, the account using the backend's default directory
  - `run_checks(probe: Probe, *, registry: Registry, dirs: Dirs, install_dir: Path, home: Path, env: Mapping[str, str]) -> list[CheckResult]`. Every check in §10.7, in order:
    - **Repository**, skipped with a note outside one: `objects/pack` and `packed-refs` read-only, `hooks` and `config` read-only when present, `objects` writable, `gc.auto` is `0`, the worktree's parent read-only.
    - **Accounts:** the current account's `secrets`, and every peer's `dir` and `files`, are unreadable.
    - **atc itself:** `config_dir`, `state_dir`, `runtime_dir` and `install_dir` are not writable.
    - **Instructions and skills:** the targets of `instructions_file` and of each link in `skills_dir` are not writable.
- **CLI:** `atc check`:
  - outside a sandbox, prints the reason and exits 2
  - otherwise one `PASS  …` or `FAIL  …` line per result (green or red only on a terminal), then exits 0 or 3
  - it reads `machine.yaml`, but never `conf_d`

- [ ] **Step 1: Write the failing tests** with a `FakeProbe` that holds sets of writable and readable paths:
  - `test_everything_protected_passes`
  - `test_a_writable_packed_refs_fails`
  - `test_a_readable_peer_dir_fails`
  - `test_a_writable_install_dir_fails`
  - `test_a_writable_instructions_target_fails`
  - `test_outside_a_sandbox_exits_2`
  - `test_the_current_account_comes_from_atc_account_first`
  - `test_the_current_account_falls_back_to_claude_config_dir`
  - `test_outside_a_repository_the_repository_checks_are_skipped`
  - `test_output_is_plain_text_when_not_a_terminal`
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: check a session's protections with atc check`.

### Checkpoint C: launching through atc

At this point the registry also accepts `agents.<agent>.bin` and `projects.<name>.snapshot`.

**The agent:**

1. Writes `conf/projects/anvil.json` from `anvil.jq` and the deliberate entries in `/mnt/pci5-dev/acme/anvil.git/.claude/settings.local.json`: network domains, `denyRead`, `allowWrite`, and the earlier `conf/acme/anvil.json` (moving that file there). When proposing the commit (`conf: add the anvil project's settings`), list the one-off allows it left out, so you can confirm none was deliberate.
2. Writes the F7 probe (open question 2) as a script under `$TMPDIR` for you to run. After Checkpoint A, sessions can no longer read an account's credentials, so a nested `claude -p` can't authenticate from inside one. The script:
   - creates a scratch repository with a project-level `sandbox.filesystem.allowWrite` entry
   - runs headless sessions with and without `--settings '{"sandbox":{"allowUnsandboxedCommands":false}}'`
   - prints whether a write to that path still succeeds

**You:**

1. Reinstall atc, then `atc src pull` and `atc sync`.
2. Add the `anvil` project to `machine.yaml`: `repo`, `account`, `accounts`, and `snapshot` if you want one.
3. In `anvil.git`, run `atc --print` and review the plan.
4. Start `atc`, and ask the agent to run `atc check`.
   - All `PASS` settles open question 1.
   - If the packed-history checks fail, stop. The design changes before the guard is relied on.
5. Run the F7 probe script from your own terminal, and paste its output to the agent. The agent records the result in design §2 (F7) and §19. If the claim is wrong, it removes the policy row, with a test, in a separate `atc:` commit.
6. In `~/.zshrc`, replace the `acme-claude` and `claude-guard` aliases with `alias claude='atc --'`. The `--` passes every argument to the agent, so `claude --resume` and `claude "prompt"` keep working.

---

## Phase 6: Maintenance (§17 step 6)

### Task 17: Keep guarded repositories packed with `atc maint`

**Files:**

- Create:
  - `atc/src/atc/cli/maint.py`
  - `atc/contrib/systemd/atc-maint.service`, `atc/contrib/systemd/atc-maint.timer`
  - `atc/tests/test_maint.py`
- Modify: `registry.py` (`maint.gc-hours`), `guard.py`, `cli/__init__.py`

**Interfaces:**

- **`registry`:** `Registry.gc_hours: int`, from `maint.gc-hours`, defaulting to 24 and required to be at least 1.
- **`guard`:**
  - `registered_repos(dirs: Dirs) -> list[Path]`
  - `maint_repo(dirs: Dirs, common_dir: Path, *, gc_hours: int, now: datetime) -> None`:
    1. `git repack --geometric=2 -d -q` with `GIT_DIR=common_dir`
    2. Try an exclusive non-blocking lock on the session lock file. Only if atc gets the lock (no session holds it), run `git pack-refs --all`, then `git gc --quiet` if the stamp `state_dir/<key>.gc` is missing or older than `gc_hours`, then touch the stamp. gc also rewrites `packed-refs`, so it needs the lock too (§10.8).

    Raises `GitError` on failure.

  - `maint(dirs: Dirs, *, gc_hours: int, now: datetime) -> list[str]`: one message per failed repository, skipping (with a warning) any repository without `objects/`

- **CLI:** `atc maint`. Exit 1 if any repository failed.
- **systemd:** the service is `Type=oneshot`, `ExecStart=%h/.local/bin/atc maint`, `Nice=10`, `IOSchedulingClass=idle`. The timer is `OnBootSec=5min`, `OnUnitActiveSec=10min`, `WantedBy=timers.target`.

- [ ] **Step 1: Write the failing tests** with temporary repositories:
  - `test_loose_objects_get_packed`
  - `test_refs_are_packed_only_when_no_session_holds_the_lock`
  - `test_gc_runs_only_after_gc_hours`: set the stamp's mtime with `os.utime`
  - `test_gc_hours_defaults_to_24_and_must_be_positive`
  - `test_a_repository_that_no_longer_exists_is_skipped`
  - `test_a_failure_is_reported_and_the_rest_continue`
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: keep guarded repositories packed with atc maint`.

---

## Phase 7: Projects and worktrees (§17 step 7)

### Task 18: Manage registered projects with `atc prj`

**Files:**

- Create:
  - `atc/src/atc/worktree.py`
  - `atc/src/atc/cli/prj.py`
  - `atc/tests/test_prj.py`
- Modify:
  - `yamlval.py`: `delete_in_text`
  - `registry.py`: `projects.<name>.{worktrees, branch-from}`, `add_project`, `remove_project`
  - `cli/__init__.py`

**Interfaces:**

- **`yamlval`:** `delete_in_text(text: str, keys: Sequence[str]) -> str`, a round trip that keeps comments.
- **`registry`:**
  - `Project.worktrees: Path | None` and `Project.branch_from: str`, defaulting to `"main"`
  - `add_project(path: Path, home: Path, name: str, fields: JsonObject) -> Registry`: an existing name is a `RegistryError`
  - `remove_project(path: Path, home: Path, name: str) -> Registry`

  Both validate the new text before writing it atomically, as `set_skills` does.

- **`worktree`:**
  - `Worktree(path: Path, branch: str | None, head: str)`
  - `list_worktrees(repo: Path) -> list[Worktree]`, from `git worktree list --porcelain`
- **CLI:**
  - `atc prj ls`: name, repository, default account, worktrees directory
  - `atc prj show [NAME]`: `NAME` defaults to the current project. Shows the fields plus the worktrees.
  - `atc prj add NAME [REPO] [--worktrees DIR] [--from BRANCH] [-a ACCOUNT]`:
    - `REPO` defaults to `git rev-parse --show-toplevel`
    - it checks that `REPO` is a repository and that `refs/heads/<BRANCH>` exists
    - it warns if no source has `projects/<NAME>.json`
  - `atc prj rm NAME`

- [ ] **Step 1: Write the failing tests:**
  - `test_add_writes_the_project_and_keeps_comments`
  - `test_add_refuses_an_existing_name`
  - `test_add_refuses_an_unknown_account_and_leaves_the_file`
  - `test_add_refuses_a_missing_branch`
  - `test_add_defaults_repo_to_the_current_repository`
  - `test_add_warns_without_a_project_unit`
  - `test_rm_removes_only_the_entry`
  - `test_show_defaults_to_the_current_project_and_lists_worktrees`
  - `test_ls_lists_every_project`
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: manage registered projects with atc prj`.

### Task 19: Create and remove worktrees with `atc wt`

**Files:**

- Create: `atc/src/atc/cli/wt.py`, `atc/tests/test_wt.py`
- Modify: `worktree.py`, `cli/__init__.py`

**Interfaces:**

- **`worktree`:**
  - `slug(branch: str) -> str`: lowercase; spaces become `-`; every character outside `[a-z0-9_.-]` becomes `--`
  - `new_worktree(project: Project, branch: str, from_ref: str | None) -> Path`: `git worktree add <path> BRANCH` if the branch exists, otherwise `git worktree add -b BRANCH <path> <from_ref or branch_from>`. A missing `worktrees` is a `ConfigError`.
  - `remove_worktree(project: Project, name: str, *, force: bool) -> None`: `git worktree remove [--force] <worktrees>/<name>`. The branch is never deleted.
- **CLI:**
  - `atc wt new BRANCH [--from REF] [--run]`: `--run` changes directory and invokes the `run` command with its defaults
  - `atc wt ls`
  - `atc wt rm NAME [--force]`

  All three require being inside a registered project.

- [ ] **Step 1: Write the failing tests:**

```python
@pytest.mark.parametrize(
    ("branch", "expected"),
    [("feature/x", "feature--x"), ("Feature X", "feature-x"), ("feat/a b", "feat--a-b"), ("v1.2_rc", "v1.2_rc")],
)
def test_slug(branch: str, expected: str) -> None:
    assert slug(branch) == expected
```

Also write:

- `test_new_uses_an_existing_branch`
- `test_new_creates_a_branch_from_branch_from`
- `test_new_from_ref`
- `test_new_without_a_worktrees_dir_is_an_error`
- `test_rm_refuses_a_dirty_worktree_without_force`
- `test_rm_keeps_the_branch`
- `test_wt_outside_a_project_is_an_error`
- `test_new_run_starts_a_session_in_the_worktree`: monkeypatch `launch.execute` and assert the plan's `cwd`

- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: add and remove working trees with atc wt`.

### Checkpoint D: maintenance timer and projects from ccwtctl

At this point the registry also accepts `maint.gc-hours` and `projects.<name>.{worktrees, branch-from}`.

**The agent:** drafts the `projects:` block for `machine.yaml` from `~/.config/ccwt/projects/*.yaml`, mapping `repo-dir`, `worktree-dir` and `branch-from`.

**You:**

1. Reinstall atc. Copy `atc/contrib/systemd/atc-maint.{service,timer}` into `~/.config/systemd/user/`, disable `claude-guard`'s maintenance timer, then `systemctl --user daemon-reload` and `systemctl --user enable --now atc-maint.timer`. Only one of the two should pack the repositories.
2. Paste the drafted `projects:` block into `machine.yaml`, then check `atc prj ls` and `atc wt ls` in each project.

---

## Phase 8: Usage (§17 step 8)

### Task 20: Show each account's recorded usage

**Files:**

- Create:
  - `atc/src/atc/usage.py`
  - `atc/src/atc/cli/usage.py`
  - `atc/tests/test_usage.py`
- Modify: `cli/__init__.py`

**Interfaces:**

- **`usage`:**
  - `UsageRecord(recorded_at: datetime, five_hour_pct: float | None)`
  - `read_usage(dirs: Dirs, account: str) -> tuple[UsageRecord | None, str | None]`. It reads `state_dir/usage/<account>.json` (`{"recorded_at": "<ISO 8601 Z>", "rate_limits": {...}}`) with `parse_json`. A missing file gives `(None, None)`. A malformed file gives `(None, warning)`.
- **CLI:** `atc usage`: a table of account, 5-hour percentage (yellow below 70, red from 70), and the record's age, or `no data`.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_valid_record_is_read`
  - `test_a_missing_file_is_no_data`
  - `test_a_malformed_file_is_no_data_with_a_warning`
  - `test_a_record_without_five_hour_data_has_no_percentage`
  - `test_usage_lists_every_account`
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: show each account's recorded usage`.

### Task 21: Pick the least used account with `-a auto`

**Files:**

- Modify: `usage.py`, `launch.py`, `cli/run.py`
- Test: `test_usage.py`, `test_launch.py`

**Interfaces:**

- **`usage`:** `choose_auto(candidates: Sequence[str], records: Mapping[str, UsageRecord | None], now: datetime) -> tuple[str, str]`, returning the account and a reason. §10.9's rules:
  - the score is `five_hour_pct` if recorded within the last five hours, otherwise 0
  - the lowest score wins
  - a tie goes to the candidate listed first
- **`launch`:** when `plan_launch` gets `account="auto"`, it resolves the name before calling `registry.choose_account`, which stays unchanged:
  - The candidates are the project's `accounts`. Outside a project, or for a project without `accounts`, there is no choice: `auto` acts as if `-a` weren't given (so the project's `account`, then `default-account`), and no usage is read.
  - Otherwise it calls `choose_auto` with `read_usage` for each candidate and `datetime.now(UTC)`.
  - The reason, and any `read_usage` warning, go into the plan's warnings, so they print on stderr.

- [ ] **Step 1: Write the failing tests:**
  - `test_the_lowest_recent_usage_wins`
  - `test_a_stale_record_scores_zero`
  - `test_a_tie_goes_to_the_first_listed`
  - `test_no_data_scores_zero`
  - `test_outside_a_project_auto_means_the_default_account`
  - `test_a_project_without_accounts_uses_its_own_account_under_auto`
  - `test_the_reason_is_printed`
- [ ] **Step 2: Implement,** and run until green.
- [ ] **Step 3: Run `./atc/check-and-fix.sh`,** then **commit** `atc: pick the least used account with -a auto`.

### Checkpoint E: usage recording, shared memory, cleanup (§17 steps 8–10)

1. **Usage recording** (`assets: record account usage from the statusline`). The agent changes `assets/statusline.sh`: when `ATC_ACCOUNT` and `.rate_limits` are present, it writes `$ATC_STATE_DIR/usage/$ATC_ACCOUNT.json` atomically (a temporary file in that directory, then `mv`) with `jq`. You pull, and check `atc usage` after a few minutes.
2. **Shared memory** (`000-todo.md`). The agent adds `autoMemoryDirectory` to `conf/projects/anvil.json` (`conf: share the anvil memory between accounts`). Merging the two memories needs your decisions on the three contradictions listed there.
3. **Cleanup.** Once atc is the only way sessions start:
   - The agent proposes removing the duplicated protective entries from `anvil.git`'s `settings.local.json`, dropping the statusline's `CLAUDE_CONFIG_DIR` fallback, and deleting the root `statusline-command.sh`.
   - You uninstall `claude-guard`, retire `ccwtctl`, and remove the old `statusline-command.sh` symlinks and `hooks/` copies from both account directories.
