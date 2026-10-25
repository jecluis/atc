# atc

`atc` is the one command that starts coding agents on this machine. Run it
inside a project and it works out which project that is, which agent and
account should take the session, and which settings and protections apply, then
hands the terminal over to the agent. It generates each account's settings from
shared configuration that lives in your own repositories, apart from the tool.
Claude Code is the first agent it supports.

Its commands are `atc run`, `atc sync`, `atc check`, `atc maint`,
`atc show account`, `atc show project`, `atc prj ls`, `atc prj show`,
`atc prj add`, `atc prj rm`, `atc wt new`, `atc wt ls`, `atc wt rm`,
`atc skills ls`, `atc skills pick`, `atc skills add`, `atc skills rm`,
`atc src ls`, `atc src check`, `atc src pull` and `atc usage`.

## Install

```sh
UV_LINK_MODE=copy uv tool install ./atc
```

Run this from a commit you have reviewed, and never with `--editable`: an
editable install runs the code in the working checkout, which sessions can
edit, and atc's code decides every session's protections. `UV_LINK_MODE=copy`
makes the installed files copies, not hard links into uv's cache, which
sessions may be allowed to write.

atc's code also includes the Python installation its environment is built on,
which for uv is one of its managed interpreters. Keep sessions from writing
either one: deny `{{atc.install_dir}}` and `{{atc.python_dir}}` in
`sandbox.filesystem.denyWrite`, and `Edit(/{{atc.install_dir}}/**)` and
`Edit(/{{atc.python_dir}}/**)` in `permissions.deny` (see Templates). `atc check`
verifies that a session cannot write them.

## Configuration

atc reads its configuration from `$XDG_CONFIG_HOME/atc`, which is `~/.config/atc`
when `XDG_CONFIG_HOME` is unset, empty or not an absolute path. Inside it,
`conf.d/` holds the **config sources**: directories that contain at least one of
`conf/` (units), `assets/` (files the units and the registry point at) and
`skills/`. Anything else in a source is ignored, so a whole repository can be a source as it is.

A source is normally a git clone. There are two layouts:

| Layout | `conf.d/` looks like | Sources |
| --- | --- | --- |
| Single | `conf.d/{conf,assets,skills}/` | `conf.d` itself, with no name |
| Multi | `conf.d/personal/{conf,assets,skills}/`, `conf.d/work/{conf,assets,skills}/` | each visible subdirectory that is a source, named after it |

```sh
git clone <url> ~/.config/atc/conf.d            # single layout
git clone <url> ~/.config/atc/conf.d/personal   # multi layout, one clone per source
```

Mixing the layouts is an error, and atc looks no deeper than one level. Source
names are lowercase letters, digits and `_`, starting with a letter, so that a
unit can refer to them (see Templates). Entries whose names start with `.`, and
plain files, are ignored. In the multi layout, a visible subdirectory that has
none of `conf/`, `assets/` and `skills/` is ignored with a warning.

Hooks and the statusline run from `assets/` outside the sandbox, so atc only
loads a source that meets these rules, and otherwise stops with an error that
names the offending path:

- No symbolic links: not `conf.d`, not a source directory, and nothing under
  its `conf/`, `assets/` and `skills/`.
- `conf.d`, each source directory, and everything under `conf/`, `assets/` and
  `skills/` is owned by you and is not group- or world-writable. Whoever can
  write a directory can swap another tree in for the one atc checked. If
  something is writable, run `chmod go-w <path>`.

With a umask of 002, as on Fedora, a fresh clone is group-writable. Run
`chmod -R go-w` on it once after cloning. From then on, `atc src pull` keeps the
modes right.

You develop units in a separate working checkout. A change reaches atc only as a
commit that you pull into `conf.d` with `atc src pull`, which checks the commit
before it deploys it.

## The machine registry

`machine.yaml`, in atc's configuration directory next to `conf.d/`, holds what
is specific to this machine: which accounts exist and where each one lives. It
belongs to no config source, so it never lands in a git repository by accident.
It may be a symbolic link, into a dotfiles repository for example: the commands
that edit it write the file the link leads to, and the link stays. That file must
then be as far out of sessions' reach as atc's configuration directory, since a
session that could edit the registry could change the agent's command or widen
the sandbox; `atc check` verifies that a session cannot write it.

```yaml
version: 1
vars:
  scratch_root: /mnt/scratch
default-account: personal
default-agents-md: AGENTS.in.md
agents:
  claude:
    bin: claude
accounts:
  personal:
    agent: claude
    dir: ~/.claude
    use-default-agents-md: true
    skills: [git-commits, rust-2024]
  work:
    agent: claude
    dir: ~/.config/work/claude
    unit: "@work/accounts/work.json"
    agents-md-file: "@work/AGENTS.in.md"
    skills: [git-commits, "@work/review"]
    vars:
      scratch: /mnt/scratch/work
projects:
  bar:
    repo: ~/src/bar
    worktrees: ~/src/wt/bar
    branch-from: main
    account: work
    accounts: [work, personal]
    snapshot: work
    vars:
      team: ci
```

| Key | Required | Meaning |
| --- | --- | --- |
| `version` | yes | Must be `1`. |
| `vars` | no | Variables for every unit, as `{{vars.<name>}}` (see Templates). |
| `default-account` | yes | The account used outside projects, and by projects that name none. It must be one of `accounts`. |
| `default-agents-md` | no | The file that `use-default-agents-md` links, as an asset reference (see Units). Defaults to `AGENTS.in.md`: a template, linked as its copy rendered for the account (see Templated assets under `atc sync`), and not named `AGENTS.md` so that an agent working in the source's own repository does not load it as that repository's instructions. |
| `agents.<agent>.bin` | no | The binary `atc run` starts for the agent: a command name, looked up on `PATH`, or a path, which is absolute or starts with `~/`. A value with a `/` in it is a path. Defaults to the agent's name. Only `claude` exists. |
| `accounts.<name>.agent` | yes | The coding agent the account is for. Only `claude` exists. |
| `accounts.<name>.dir` | yes | The account's configuration directory. |
| `accounts.<name>.unit` | no | The unit the account's settings are built from, as a unit reference (see Units). Defaults to `accounts/<name>.json`. |
| `accounts.<name>.use-default-agents-md` | no | `true` links `default-agents-md` as the account's instructions file (see `atc sync`). Defaults to `false`. |
| `accounts.<name>.agents-md-file` | no | The file to link as the account's instructions file instead of the default: an absolute or `~/` path, or an asset reference (see Units). `null` means not set. |
| `accounts.<name>.skills` | no | The skills to link into the account, as skill references (see Skills). Defaults to none. |
| `accounts.<name>.vars` | no | Variables for this account's units, as `{{account.vars.<name>}}`. |
| `projects.<name>.repo` | yes | A checkout of the project's repository. |
| `projects.<name>.worktrees` | no | The directory the project's worktrees are made in, as a path like a `dir`. Defaults to none. |
| `projects.<name>.branch-from` | no | The branch that new branches of the project start from. Defaults to `main`. |
| `projects.<name>.account` | no | The account the project uses by default. Defaults to `default-account`. |
| `projects.<name>.accounts` | no | The accounts allowed to work on the project. When set, it must name at least one and include the project's account: `account`, or `default-account` when the project sets none. Defaults to every account. |
| `projects.<name>.unit` | no | The unit the project's settings are built from, as a unit reference (see Units). Defaults to `projects/<name>.json`. When that default does not exist, the project has no settings of its own. |
| `projects.<name>.snapshot` | no | The name of a snapper config. `atc run` takes a snapshot with it before each session in the project (see The guard). Defaults to no snapshot. |
| `projects.<name>.vars` | no | Variables for this project's units, as `{{project.vars.<name>}}`. |
| `maint.gc-hours` | no | The hours between full `git gc` runs in `atc maint`. A whole number of at least 1. Defaults to `24`. |

No other keys are accepted yet, and a misspelled key is an error. The rules:

- The file is YAML 1.2, so `no`, `on` and `off` stay strings. Only values JSON
  can hold are accepted: a timestamp such as `2026-10-23` is an error that
  asks you to quote it. Repeated keys and keys that are not strings are errors,
  and so are an alias (`*name`) for a list or a mapping and a merge key (`<<`):
  write the value out.
- A leading `~` in a path stands for your home directory. Every path must then
  be absolute. Environment variables are not expanded, so `$HOME/x` is an
  error.
- Account and project names are lowercase letters, digits, `_` and `-`,
  starting with a letter or a digit. No account can be called `auto`, which
  `atc -a auto` uses to pick the least used account of a project.
- Every account that the registry mentions must exist: `default-account`, and
  each project's `account` and `accounts`.
- A project's `accounts` cannot be empty, and must include the account the
  project uses by default: its `account`, or `default-account` when it sets
  none.
- A `repo` and a `worktrees` are paths like a `dir`: a leading `~` is expanded
  and it must then be absolute.
- An `agents-md-file` that starts with `/` or `~` is a path, treated like a
  `dir`. Anything else is an asset reference.
- An account that sets both `agents-md-file` and `use-default-agents-md: true`
  gets a warning, since the flag then has no effect.
- `skills` is a list of strings. Whether each names a skill that exists is
  checked by `atc src check`, `atc sync` and `atc skills ls`, not when the
  registry is read.
- Two accounts cannot share a `dir`, however it is written: a trailing `/` or
  a symbolic link in the path makes no difference.
- Variable names are lowercase letters, digits and `_`, starting with a letter,
  so they can be written in a reference. Values are strings and must not contain
  `{{`.

## Skills

A **skill** is a directory that sits directly under a source's `skills/` and
contains a `SKILL.md`. Directories with no `SKILL.md`, and anything deeper, are
not skills.

- **Name:** the directory's name, which must be lowercase letters, digits, `_`
  and `-`, starting with a letter or a digit. A directory with another name is
  skipped, with a warning.
- **Description:** the `description` in the YAML front matter of `SKILL.md`,
  which is the text between a first line of `---` and the next `---` line. It
  is only shown by `atc skills ls`. atc warns when the front matter's `name`
  differs from the directory's name, or when the front matter cannot be read as
  YAML, which it reads as it reads the registry, aliases refused. The skill
  counts either way.
- **Selection:** an account's `skills` in the registry lists the skills to link
  into it. Each account chooses its own. An entry is the skill's name, such as
  `git-commits`, or `@name/git-commits` for the source called `name`. Without
  `@`, exactly one source must have the skill, as for asset references (see
  Units). It is one directory name, with no `/`. Two selected skills with the
  same name, from different sources, are an error.
- **Linking:** `atc sync` links each selected skill as `skills/<name>` in the
  account's directory, as described under `atc sync`. `atc skills ls` shows
  what each account has.
- **Choosing:** `atc skills pick` offers a checklist, and `atc skills add` and
  `atc skills rm` change the selection from the command line. All three write
  the selection into `machine.yaml` and link it, as `atc sync` would.

## Units

A **unit** is a JSON file under a source's `conf/`. It contributes a fragment
of Claude Code settings and can import other units:

```json
{
  "version": 2,
  "description": "Account isolation",
  "imports": ["common/base.json", "@personal/langs/rust.json"],
  "settings": {
    "permissions": { "allow": ["Bash(cargo test *)"] }
  }
}
```

| Field | Meaning |
| --- | --- |
| `version` | Required, and must be `2`. A unit with `"version": 1` is refused: change the number. |
| `description` | Optional note for people. atc ignores it. |
| `imports` | Optional list of unit references, merged before this unit's own `settings`. |
| `settings` | Optional fragment of Claude Code settings. Its top-level keys must be ones Claude Code defines (`atc show account --lax` accepts others with a warning). |

No other fields are allowed. A unit with only `imports` works as a profile that
groups other units.

**References.** An import, and the `unit` of an account or project in the
registry, is one of:

- `path/to/unit.json`: a path relative to the `conf/` of the same source.
- `@name/path/to/unit.json`: a path relative to the `conf/` of the source
  called `name`. This only works in the multi layout.

A reference must be relative, must spell out the `.json` extension, and must
stay inside the `conf/` of its source, links included. In an import, a
reference without `@` always means the unit's own source.

Outside a unit, as in the registry, a reference without `@` is looked up in
every source, and exactly one must contain it. If none does the unit does not
exist, and if several do you are asked to add an `@name/` prefix. With a single
source, or with names that never collide, the prefix is never needed.

**Asset references** name a file under a source's `assets/` instead: the
registry's `default-agents-md`, and an `agents-md-file` that is not a path, such
as `AGENTS.in.md` or `@work/docs/AGENTS.in.md`. They follow the same rules as
unit references, with `assets/` in place of `conf/` and with no extension
required: a reference must be relative and must stay inside the `assets/` of its
source, links included, and without `@` exactly one source must contain the
file. A reference to a templated asset means its copy rendered for the account
(see Templated assets under `atc sync`).

**Merging.** Imports are followed depth-first and in order, each before the
unit's own `settings`, so an importing unit overrides what it imports and a
later import overrides an earlier one. A unit contributes once, at the first
point the walk reaches it. Objects merge recursively, arrays concatenate
without duplicates, and for any other value the later one wins. Two values of
different JSON types at the same key are an error. The output is
deterministic: keys are sorted.

### Templates

A string in `settings`, at any depth, can contain **references**: `{{home}}`,
`{{account.dir}}`. A reference is lowercase letters, digits and `_`, with `.`
between parts and no spaces. Each unit is expanded as it is loaded, before it is
merged, so one unit can serve every account:

```json
{
  "version": 2,
  "settings": {
    "sandbox": {
      "credentials": {
        "files": [
          { "path": "{{account.secrets}}", "mode": "deny" },
          { "path": "{{peers.dir}}", "mode": "deny" }
        ]
      }
    },
    "permissions": { "deny": ["Read(/{{peers.dir}}/**)"] },
    "statusLine": { "command": "bash {{source.assets}}/statusline.sh" }
  }
}
```

In a `Read` or `Edit` permission rule, a path starting with `/` is relative to
the settings file, and only `//` starts an absolute one. Path variables hold
absolute paths, so a rule writes `/{{…}}` to get `//`. Sandbox paths take the
variable as it is.

- A reference to a string is replaced in place, wherever it is in the string.
  Replaced text is never scanned again.
- A reference to a list expands the array element that contains it: the element
  is repeated once per item, in order, with the reference replaced in each copy.
  An empty list removes the element, which is still checked for errors. The
  element can be a string or an object, and it may use only one list reference.
  A list reference outside an array is an error.
- `imports`, `description` and object keys are never substituted. A key that
  contains `{{` is an error, and so is any `{{` that does not start a valid
  reference.
- An unknown reference is an error. It names the unit, where in `settings` it
  is, the reference, and the account being built.

| Reference | Type | Value |
| --- | --- | --- |
| `home` | string | Your home directory. |
| `atc.config_dir`, `atc.conf_d`, `atc.state_dir`, `atc.runtime_dir` | string | The directories atc uses: its configuration, its config sources, its state, and its locks. |
| `atc.install_dir` | string | The Python environment atc runs from. |
| `atc.python_dir` | string | The Python installation that environment is built on, with symbolic links resolved. For uv, one of its managed interpreters under `~/.local/share/uv/python`. The same as `atc.install_dir` when atc does not run in a virtual environment. |
| `source.name` | string | The name of the source that contains the unit. Empty with a single source. |
| `source.root`, `source.conf`, `source.assets` | string | That source's directory, its `conf/` and its `assets/`. |
| `source.rendered` | string | Where `atc sync` renders that source's templated assets for the account (see Templated assets under `atc sync`). |
| `sources.<name>.root`, `.conf`, `.assets`, `.rendered` | string | The same for any source, by name. Only with several sources. |
| `vars.<name>` | string | The registry's `vars`. |
| `account.name`, `account.agent`, `account.dir` | string | The account being built, or in a project's units the account chosen for the project. |
| `account.files` | list | Files the account owns outside its `dir`. For Claude, `~/.claude.json` when `dir` is `~/.claude`, otherwise none. |
| `account.secrets` | list | The account's credentials and state file, which its own sessions must not read. For Claude, `.credentials.json` in `dir` and the state file: `~/.claude.json` when `dir` is `~/.claude`, otherwise `.claude.json` in `dir`. |
| `account.vars.<name>` | string | The account's `vars`. |
| `peers.dir` | list | The `dir` of every other account. |
| `peers.files` | list | The `files` of every other account, in one list. |
| `project.name`, `project.repo` | string | The project being built. Only in a project's units. |
| `project.vars.<name>` | string | The project's `vars`. Only in a project's units. |

In an account's units, `project.*` does not exist, so using it there is an
unknown-reference error.

## Usage

Run `atc --help`, or `--help` after any command, for the options. `-h` means
`--help` too.

### `atc [run] [-a ACCOUNT] [--settings FILE|JSON] [--no-guard] [--print] [-q] [--lax] [--conf-d DIR] [-- AGENT-ARGS]...`

Starts the agent in the right account, replacing atc with it. It works out:

- **the project:** the one in the machine registry whose `repo` is in the same
  git repository as the current directory. The main checkout and every
  worktree of a repository match the same project, and a repository that is not
  registered has none, as has a directory that is not in a repository. If git
  fails for any other reason on the current directory, such as refusing a
  repository that someone else owns, atc stops with exit status `1` rather than
  starting without a project. A registered project whose `repo` git cannot read
  is left out, with a warning.
- **the account:** `ACCOUNT` when given, otherwise the project's `account`,
  otherwise `default-account`. If the project lists `accounts`, the account
  must be one of them. The account's `agent` decides which agent starts.
  `-a auto` picks the project's least used account (see below).
- **the binary:** `agents.<agent>.bin` from the registry, otherwise the
  agent's name, looked up on `PATH`. It must exist. atc refuses to start when it
  resolves to atc itself, whether it was found on `PATH` or named in the
  registry, which happens when `claude` is a link to `atc`; name the real binary
  under `agents.claude.bin`.

Before it replaces itself, atc prints one line on standard error:

```
atc: project bar · account bravo · guard on
```

It says `project –` outside a registered project. The guard is `on`, `off
(--no-guard)` or `off (not in a repository)` (see The guard). `-q` leaves the
line out. Nothing else from atc appears once the agent has started, and the
agent's exit status is the command's.

**`-a auto`.** In a project that lists `accounts`, `-a auto` starts the one of
them that is the least used, going by the records that `atc usage` shows. Each
candidate scores the five-hour percentage of its record if that was recorded
less than five hours ago, and 0 otherwise, since its window has then rolled
over. So an account scores 0 when it has no record, when its record cannot be
used, when the record has no five-hour figure, and when the record is five hours
old or older. The lowest score wins, and among equal scores the first one in the
project's `accounts` does. atc reads only the records of those accounts. A line
on standard error, just above the one that names the project, says which
account was chosen and why:

```text
atc: -a auto chose personal: 5-hour usage 10%, recorded 2h 0m ago
atc: -a auto chose bravo: no data
atc: -a auto chose bravo: recorded 6h 0m ago, so its 5-hour window has rolled over
```

`-q` leaves it out together with the line that names the project. A record that
could not be read, or whose percentage was clamped, is a warning, printed
before that line and not hidden by `-q`.

The chosen account is then used as if it had been named. Outside a registered
project, and in a project that lists no `accounts`, there is no choice to make:
`-a auto` is the same as leaving `-a` out, which is the project's `account` and
then `default-account`, and atc reads no record and says nothing about it.
`--print` has no such line on standard error, and shows the same reason in its
plan, under the account; the warnings about records are printed as usual. Usage
from other machines, or from claude.ai, is never recorded, so `-a auto` cannot
see it.

**`atc` with no command.** `atc` with no arguments, or with a first argument
that starts with `-`, is `atc run`; this includes `--`. `atc -- --resume x`
therefore starts the agent with `--resume x`. Any other first word must be a
command, so a typo such as `atc shwo` is an error and never starts a session.
`atc --help`, `atc -h` and `atc --version` are atc's own, not `run`'s. After
`--`, `-h` goes to the agent.

**Arguments for the agent** go after `--`, and are passed on as they are, in
order, after atc's own. An option that atc does not know, given before `--`, is
an error, and so is any other word before it. To make every `claude` command go
through atc, keep atc's own options for `atc` itself:

```sh
alias claude='atc --'
claude --resume x        # atc -- --resume x
claude "fix the build"   # atc -- "fix the build"
atc -a work --print      # atc's own options are not given through the alias
```

A `--settings` or `--settings=…` among the agent's arguments is refused with
exit status `2`: Claude Code takes only one `--settings`, so it would replace
the one atc passes. Give `--settings` to `atc`, before `--`.

**Environment.** The agent gets atc's environment with these changes:

| Variable | Value |
| --- | --- |
| `ATC_ACCOUNT` | The account's name. |
| `ATC_PROJECT` | The project's name. Removed outside a registered project. |
| `ATC_STATE_DIR` | atc's state directory. |
| `CLAUDE_CONFIG_DIR` | The account's directory. Removed for `~/.claude`, the directory Claude Code uses when the variable is not set. |
| `GIT_CONFIG_*` | In a guarded session: `gc.auto=0` and `maintenance.auto=false`, added after any entries already set (see The guard). |

Every other `ATC_` variable is removed, so a session started from inside another
one never inherits the outer one's values.

**Settings.** The agent is started as `claude --settings '<settings as JSON>' …`.
The settings are one document, made of three layers, each winning over the ones
before it by the merge rules that `atc show` uses:

1. **The project's settings,** built as `atc show project` builds them, for the
   account that was chosen. A project with no unit contributes nothing, and a
   session outside a registered project has no project settings. If they cannot
   be built, for instance because a unit sets a key the project's settings must
   not, atc stops with exit status `1` and the agent does not start.
2. **The session entries** that guard the repository, in a guarded session (see
   The guard).
3. **Your `--settings`,** read as strict JSON. They are not templates, and must
   not set `sandbox.allowUnsandboxedCommands`. A key that has one type in the
   project's settings and another in yours is an error.

When all are empty there is no `--settings` argument.

atc does not build the account's settings to start a session. The agent reads
them from the account's directory, where `atc sync` wrote them, so a unit that
the account's settings are built from cannot stop a launch. `--print` does build
them, to check the permission rules (see Rule warnings). If they cannot be
built, a warning says why, only the project's rules and those of your
`--settings` are checked, and the plan is printed all the same.

#### The guard

Claude Code's sandbox lets a session write to the repository's `.git`, because
commits need it. For a linked worktree that is the main repository's shared
`.git`. Commits never write to `objects/pack` or `packed-refs`, though, and
those two hold everything git has packed: almost all of the history and most
branches. Inside a git repository, atc therefore guards the session:

- **Write protection.** Each of `objects/pack` and `packed-refs` in the shared
  `.git` that exists gets a `sandbox.filesystem.denyWrite` entry, which holds
  commands, and a rule in `permissions.deny`, which holds Claude's file tools:
  `Edit(//<path>/**)` for the directory and `Edit(//<path>)` for the file. A
  path that does not exist yet is left out, since the sandbox would put a
  read-only placeholder where it is. These are the session entries, merged
  after the project's settings and before yours, so `--settings` cannot take
  them away.
- **No automatic gc.** `GIT_CONFIG_*` turns off `gc.auto` and
  `maintenance.auto` for the session, since git can no longer write packs. The
  two entries are added after any that atc's own environment has.
- **Repository list.** The shared `.git` is added to `<state_dir>/repos`, one
  path per line, if it is not there. The list tells `atc maint` which
  repositories to keep packed. The list, and any directory atc makes for it or
  for the lock, is readable and writable by you alone.
- **Lock.** atc takes a shared `flock` on `<runtime_dir>/<key>.lock`, where
  `<key>` is the first 16 hex digits of the SHA-256 of the shared `.git`'s path
  and `<runtime_dir>` is `atc` in `$XDG_RUNTIME_DIR`, or `/run/user/<uid>/atc`
  when that is not set to an absolute path. The agent and everything it starts
  inherit the lock, so it lasts until the last of those processes exits, and
  maintenance, which needs the lock exclusively, never rewrites `packed-refs`
  under a running session. atc waits up to 60 seconds for it. If maintenance
  is still running by then, or the lock file cannot be made, atc warns and
  starts the agent without the lock. If `objects/pack` or `packed-refs` only
  appeared while atc waited, so the session entries could not name it, atc
  warns that the session starts with it unprotected; restart the session to
  protect it.

**Fetching.** A `git fetch` inside a guarded session that brings 100 objects or
more fails: git keeps what such a fetch brings as a pack in `objects/pack`, which
the session cannot write. A smaller fetch is unpacked into loose objects and
works. Fetch from your own terminal, outside the session, or start the session
with `--no-guard`. atc does not raise git's `transfer.unpackLimit` for the
session instead, because that would apply to every git command in it, clones of
other repositories included.

The repository is listed and the lock taken just before the agent starts. If
the list cannot be written, atc stops with exit status `1`. Outside a git
repository there is nothing to guard, and the announcement says `guard off (not
in a repository)`. `--no-guard` leaves out all of the above and warns that the
session is not protected; the announcement then says `guard off (--no-guard)`.

**Snapshot.** When the project has a `snapshot`, atc runs
`snapper -c <snapshot> create --cleanup-algorithm number --description "atc: <directory>"`
just before the agent starts, after the steps of the guard, with `<directory>`
the current directory. It does so whether or not the session is guarded. If
snapper fails or is not installed, atc warns and starts the agent anyway.

| Option | Effect |
| --- | --- |
| `-a`, `--account ACCOUNT` | Use `ACCOUNT` instead of the project's own account. `auto` picks the least used of the project's accounts. |
| `--settings FILE\|JSON` | Settings for this session only: the path of a JSON file, or JSON text, which is anything that starts with `{`. They win over the project's settings, and are added to the session entries without being able to remove them. |
| `--no-guard` | Do not guard the repository's packed history in this session: no session entries, no gc override, no lock and no listing in the repository list. A warning says so inside a git repository. The project's snapshot is still made. |
| `--print` | Show on standard output how the agent would be started, and exit with status `0` without starting it: the repository, project, account (with why `-a auto` chose it), whether the session is guarded, the snapshot if there is one, the changes to the environment, the command, and the settings as JSON. Nothing is listed, locked or snapshotted. Warnings about permission rules that never apply go to standard error. |
| `-q`, `--quiet` | Leave out the line that names the project, the account and the guard, and the one that says what `-a auto` chose. |
| `--lax` | Report settings keys Claude Code does not define in the project's units, and under `--print` in the account's unit too, as warnings rather than errors. |
| `--conf-d DIR` | Read config sources from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). Only with `--print`. |

### `atc src ls [--conf-d DIR]`

Lists the config sources as a table, one row per source:

- its name (`-` for the single layout), path and layout
- how many units (`*.json` files under `conf/`), asset files and skills it has
- for git clones: the branch, the short commit, `dirty` when `conf/`,
  `assets/` or `skills/` has uncommitted changes, and how many commits the
  clone is ahead of and behind its upstream as of the last fetch (`ahead/behind`,
  blank when there is no upstream)

The table goes to standard output. Warnings and errors go to standard error.

| Option | Effect |
| --- | --- |
| `--conf-d DIR` | Read config sources from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). The trust rules still apply. |

### `atc src check [--conf-d DIR]`

Checks every unit in every config source: each `*.json` under a source's
`conf/` is loaded and resolved together with its imports, including imports
into other sources. It reports every problem it finds, not only the first:

- a unit that is not valid JSON or does not fit the unit format
- a settings key Claude Code does not define
- an import that is malformed, missing, outside its source's `conf/`, or
  circular
- values of different types for the same setting
- a malformed reference (see Templates), or an object key that contains `{{`,
  in any unit, including one that no account or project uses

When the machine registry exists, it also builds the settings of every account,
as `atc show account` does, and of every project, as `atc show project` does,
once for each account the project allows (every account when it names none). It
reports:

- an account whose unit does not exist, or exists in several sources, and the
  same for a project whose registry entry names a `unit`
- an account that asks for an instructions file (`use-default-agents-md` or
  `agents-md-file`) that does not exist, or that several sources have
- an account that selects a skill that does not exist or that several sources
  have, or two skills of one name, so a commit that removes a skill an account
  uses is not deployed by `atc src pull`
- a templated asset that cannot be rendered for an account (see Templated
  assets under `atc sync`)
- a unit with a reference that cannot be expanded for an account, or for a
  project and one of its accounts, such as an unknown variable
- a unit that sets a key its layer does not allow: `autoMemoryDirectory` is
  refused in an account's settings, because the value is the memory directory
  itself and every project would share it, and `sandbox.allowUnsandboxedCommands`
  is refused in a project's

It also warns about permission rules that can never apply, or that are better
written another way (see Rule warnings), and about skills that were skipped or
whose front matter looks wrong (see Skills). Warnings never fail the check.
Without a registry, only the units are checked, with a warning saying so.

It warns, too, about an account whose settings leave one of atc's own
directories writable: its configuration, state and runtime directories, the
Python environment it runs from, and the Python installation that one is built
on. The file tools cannot write a directory when an `Edit` rule in
`permissions.deny` names it. Commands cannot write it when the sandbox is on
(`sandbox.enabled` is `true`) and `sandbox.filesystem.denyWrite` names it, or
such an `Edit` rule does, since Claude Code adds those rules' paths to
`denyWrite`. A rule or entry for a directory above it protects it as well. A
rule names a directory with a path that starts with `//` or `~` and ends in
`/**`, and an entry with an absolute path or one under `~`, the `/**` optional.
A bare `Edit` keeps the file tools out of everything, but not commands. A path
with any other wildcard in it does not count, so a directory such a path covers
is still reported. `atc check`, run inside a session, tests the same
directories for real.

When all is well it prints `ok: N units in M sources` and exits `0`. Otherwise
it prints each problem, once, and exits `1`. Everything goes to standard
error. A source that breaks the trust rules, or a registry that is invalid,
stops the check with that error.

| Option | Effect |
| --- | --- |
| `--conf-d DIR` | Read config sources from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). The trust rules still apply. |

### `atc src pull [NAME]...`

Updates config sources that are git clones: the ones called `NAME`, or all of
them. A source is only ever moved to a commit that has been checked. For each
source:

1. It refuses if `conf/`, `assets/` or `skills/` has uncommitted changes, which
   are modified, deleted and untracked files. Deployed configuration is not
   edited in place. Untracked files elsewhere in the source do not count.
2. It fetches, and stops if the upstream cannot be reached by fast-forward: the
   clone has commits of its own, whether or not the upstream has moved.
3. It writes the whole fetched commit into a temporary directory, the way the
   fast-forward would write it: symbolic links stay links, the commit's own
   `.gitattributes` decide line endings and filters, and its `export-ignore`
   and `export-subst` rules play no part. The directory then gets the same mode
   fixes as in step 4. What is checked is that tree, and only a commit that
   passes is deployed:
   - The tree must still be a config source: it has at least one of `conf/`,
     `assets/` and `skills/`, and with the single layout no other directory in
     it holds `conf/`, `assets/` or `skills/` of its own.
   - It must meet the trust rules, so a commit that adds a symbolic link under
     `conf/`, `assets/` or `skills/` is not deployed.
   - Then come the checks of `atc src check` on `conf.d` with this source
     replaced by that tree, the other sources being as they are deployed. When
     the machine registry exists they cover its accounts and projects, so a
     commit that removes a unit an account still uses is not deployed either.
4. Only if all of that passes, it fast-forwards to the commit that was checked.
   Git applies your umask to what it writes, which under a umask of 002 is
   group write, so it then removes group and world write from the `conf/`,
   `assets/` and `skills/` directories and everything under them, skipping
   symbolic links. If it cannot, the source is reported as failed, naming the
   path. The clone has been fast-forwarded by then, and atc refuses it until
   the path is fixed with `chmod go-w`.

It prints one line per source on standard output, `NAME: STATUS (DETAIL)`, and
for the single layout's source, which has no name, the path of `conf.d` takes
the place of `NAME`.

For each source it fast-forwarded, it then shows on standard error what it
deployed: the commits, as `git log --oneline OLD..NEW` lists them, the newest 20
followed by `… N more` when there are others, and `git diff --stat OLD..NEW --
conf assets skills`, the files that changed in what a source deploys. Review
them. The upstream can be a checkout where sessions commit, and a commit there
that weakens a unit, a hook, the instructions file or a skill passes every
check and takes effect in the next session. Nothing more is shown for a source
that was not updated.

| Status | Meaning |
| --- | --- |
| `updated` | Fast-forwarded. The detail is `OLD..NEW`, the short commits. |
| `current` | Already at the upstream's commit. |
| `skipped` | Not a git clone, or its branch has no upstream. Nothing to pull from. |
| `refused` | Uncommitted changes, or it cannot fast-forward. Left as it was. |
| `failed` | Git failed, the incoming commit did not pass the checks, or the modes could not be fixed. When the commit did not pass, the source is left as it was. |

The problems the checks found on a failed source go to standard error, as
`atc src check` prints them, and so do the warnings of a checked commit. When
the registry does not exist, only the sources are checked, with a warning saying
so. The exit status is `1` if any source failed or was refused, and `0`
otherwise. An unknown `NAME` is an error that lists the sources there are, and
nothing is pulled.

### `atc skills ls [ACCOUNT]... [--conf-d DIR]`

Lists the skills in the config sources, and where each account has them. Each
row is a skill: its name, its source (`-` for the single layout) and the start
of its description, cut to 60 characters. There is then one column for each
`ACCOUNT`, or for every account:

| Column shows | Meaning |
| --- | --- |
| `linked` | The account selects the skill, and its skills directory links it. |
| `selected` | The account selects the skill, and `atc sync` has not linked it yet, or would re-point its link. |
| `conflict` | The account selects the skill, but something atc did not make is where the link is to go. |
| blank | The account does not select the skill. |

Below the table, each account's skills directory is searched for the entries
atc does not manage: Claude Code's own `synced/` and `.trash/`, skills made by
hand, links made by other tools, and whatever is in a conflict. They are listed
dimmed, a directory with a `/` after its name and a link with its target. The
accounts' directories must exist. Nothing is changed.

The table goes to standard output, warnings and errors to standard error. It
exits `1` when an account cannot be planned, for example because it selects a
skill that does not exist.

| Option | Effect |
| --- | --- |
| `--conf-d DIR` | Read config sources from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). The trust rules still apply. |

### `atc skills pick ACCOUNT [--take-over]`

Shows a checklist of every skill in the config sources, one line each:

```text
git-commits  personal  Guidelines for commit granularity, sizing, and message…
review       bravo     Review a change before it lands
review       personal  Review a pull request
```

A line is the skill's name, its source (`-` for the single layout) and the start
of its description. The ones `ACCOUNT` selects are ticked. Move with the arrow
keys, tick with the space bar, toggle all with `a`, invert with `i`, and confirm
with enter.

- **Confirming** writes the selection into `machine.yaml`, then links and
  unlinks the account's skills as `atc sync` does (see Skills under `atc sync`),
  without touching its settings or its instructions file.
- **Ctrl-C or Ctrl-Q** cancels. Nothing is changed, and the command says so and
  exits `130`, as for any interrupt.
- **Without a terminal** on both standard input and standard output, it exits `2`
  and points at `add` and `rm`.

The selection is written into `machine.yaml` with the file's comments, key order
and quoting kept. Only `accounts.<ACCOUNT>.skills` changes: it is written as a
list on one line, `skills: [git-commits, rust-2024]`, and an account that had no
`skills` gets it after its other keys. The comment on the `skills` key, and the
comment lines after the entry, stay where they are. What is lost is the comment
on each item of a `skills` list that was written one item per line, since that
list is replaced.

The whole file is rewritten, with 2-space indentation, so other lists written one
item per line come out indented under their key, and spacing inside one-line
lists and mappings is normalized. The file is replaced in one step and keeps its
permissions. It is not written when the selection is the one it already holds,
nor when the new selection cannot be linked, for example because two of its
skills have the same name, or because the account's directory does not exist. If
an edit would lose any other comment, `atc` refuses it and leaves the file alone.

Entries of the current selection that are still ticked keep their place and
spelling. Newly ticked skills follow them, as the skill's name, or as
`@source/name` when several sources have a skill of that name. An entry that
names no skill is not shown, so confirming drops it.

| Option | Effect |
| --- | --- |
| `--take-over` | Replace a link or file that is where a selected skill's link is to go. A directory is never replaced. |

It prints `ACCOUNT: skills written to FILE` when it changed the selection, then
one line for each link that changed, as `atc sync` does. When neither the
selection nor any link changed, it prints `ACCOUNT: skills unchanged` instead.
It exits `3` when a link is left alone for want of `--take-over`. The selection
has been written by then.

### `atc skills add ACCOUNT SKILL... [--take-over]`

Adds skills to the selection of `ACCOUNT` without the checklist, so scripts can.
Each `SKILL` is a skill's name, or `@source/name` when several sources have it.
It is written after the entries already there, as the name or, when that is
ambiguous, as `@source/name`, and a skill that is selected already is left as it
is. A `SKILL` that does not exist, or the same name from two sources, is an error
that exits `1` with nothing changed. The file, the links, the output and the exit
status are those of `atc skills pick`.

### `atc skills rm ACCOUNT SKILL...`

Removes skills from the selection of `ACCOUNT` without the checklist. Each `SKILL`
is a skill's name, `@source/name`, or an entry of the selection as written, which
is how an entry that names no skill any more is removed. A skill that exists but
is not selected is left as it is. A `SKILL` that is neither a skill nor selected
is an error that exits `1` with nothing changed. The links of the removed skills
that atc made are removed, and anything else in the skills directory is left
alone. Otherwise it behaves as `atc skills add`.

### `atc show account NAME [--conf-d DIR] [--lax]`

Builds the settings of the account `NAME`, one of the accounts in the machine
registry, and writes them to standard output as JSON. They are the account's
unit merged together with everything it imports, as described under Units, with
the references in each unit expanded for the account (see Templates). Every unit
is checked against what the account's settings may contain.

The output has sorted keys and a trailing newline, and is the same every time
for the same configuration. On a terminal it is coloured. Redirected, it is
plain JSON, so it is safe to pipe. Warnings and errors go to standard error,
among them the permission rules of the account that can never apply (see Rule
warnings). An unknown `NAME` is an error that lists the accounts there are.

| Option | Effect |
| --- | --- |
| `--conf-d DIR` | Read config sources from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). The trust rules still apply. The registry is still read from atc's configuration directory. |
| `--lax` | Report settings keys Claude Code does not define as warnings rather than errors. |

### `atc show project NAME [-a ACCOUNT] [--conf-d DIR] [--lax]`

Builds the settings of the project `NAME`, one of the projects in the machine
registry, and writes them to standard output as JSON, in the same format as
`atc show account`. They are the project's unit merged together with everything
it imports, with the references in each unit expanded for the project and the
account that works on it (see Templates). Every unit is checked against what a
project's settings may contain.

The account is `ACCOUNT` when given, otherwise the project's `account`,
otherwise `default-account`. If the project lists `accounts`, the account must
be one of them. A project with no unit, because `projects/NAME.json` does not
exist and the registry names none, has no settings of its own: the output is an
empty object, and a warning says why. An unknown `NAME` or `ACCOUNT` is an error
that lists the ones there are.

The account's settings are built too, so that the permission rules of the
account and of the project can be checked together (see Rule warnings). The
output is still the project's settings alone. If the account's settings cannot
be built, a warning says why and only the project's own rules are checked: the
output and the exit status are the same as without the check.

| Option | Effect |
| --- | --- |
| `-a`, `--account ACCOUNT` | Build the settings for `ACCOUNT` instead of the project's own. |
| `--conf-d DIR` | Read config sources from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). The trust rules still apply. The registry is still read from atc's configuration directory. |
| `--lax` | Report settings keys Claude Code does not define as warnings rather than errors. |

### `atc prj ls`

Lists the projects of the machine registry as a table, one row per project, in
the order of the file: its name, its repository, its account and its worktrees
directory, which is `-` when it has none. The account is the one the project
uses by default: its `account`, or else `default-account`. The table goes to
standard output. With no projects it prints no table, and says so on standard
error.

### `atc prj show [NAME]`

Shows one project: its entry in the machine registry, with the defaults filled
in (the account, the unit, `branch-from`), as labelled lines, and below them a
table of its worktrees as git lists them: the path, the branch (`(detached)` when
`HEAD` is not on a branch) and the first 7 characters of the commit. A bare
repository is not itself a worktree and is left out. If git cannot list the
worktrees, a warning says why and the lines are still shown.

`NAME` is a project of the registry. Without it, the project is the one the
current directory is in, whether in the main checkout or in a worktree, as for
`atc run`. Outside every registered project that is a usage error, exit status
`2`. An unknown `NAME` is an error that lists the projects there are.

### `atc prj add NAME [REPO] [--worktrees DIR] [--from BRANCH] [-a ACCOUNT]`

Adds the project `NAME` to the machine registry. `REPO` is a checkout of the
project's repository, the main one, a worktree or a bare repository, and
defaults to the current directory. What is written as `repo` is the repository's
main worktree, whichever of its worktrees `REPO` is in, or the repository itself
when it is bare, so that `atc wt rm` of a worktree never leaves the project
without its repository. When the git directory is kept apart from the checkout,
as a submodule's is, it is the top level of `REPO`. Before anything is
written it checks that `REPO` is a git repository that no project has yet, and
that the branch `BRANCH` exists in it, as `refs/heads/BRANCH`, so `main~1` or a
tag is refused. `BRANCH` is `--from`, or `main` when that is not given. A failed
check is an error that exits `1`.

The entry goes after the others in `machine.yaml`, as `repo`, then `worktrees`,
`branch-from` and `account` for the options that were given. Paths are written
as absolute paths. The rest of the file stays as it was, comments included. The
new file is checked as the registry always is, and when it is not valid, because
`NAME` is not a valid name, `NAME` is registered already or `ACCOUNT` is not an
account, the file is left alone and the error exits `1`.

If no config source has `projects/NAME.json`, a warning says so: the project then
has no settings of its own until one exists. It is still added.

| Option | Effect |
| --- | --- |
| `--worktrees DIR` | Sets `worktrees`: the directory the project's worktrees are made in. It need not exist. |
| `--from BRANCH` | Sets `branch-from`: the branch new branches start from. |
| `-a`, `--account ACCOUNT` | Sets `account`: the account the project uses by default. |

### `atc prj rm NAME`

Removes the project `NAME` from the machine registry. What goes is its entry,
comments inside it included, and the comment lines directly above it, at the
same indentation as its key and with no blank line between, which describe it.
Everything else in the file stays as it was: other comments, a comment indented
deeper that follows the entry before, and blank lines. Nothing else is touched:
not the repository, not its worktrees, and not `projects/NAME.json` in the config
sources. An unknown `NAME` is an error that lists the projects there are, and the
file is left alone.

### `atc wt new BRANCH [--from REF] [--run]`

Adds a worktree for `BRANCH` to the project the current directory is in,
whether in the main checkout or in a worktree, as for `atc run`. The project
must have a `worktrees` directory in the registry, and the worktree is made in
it, in a subdirectory named after `BRANCH`. The name is the branch's slug:

- the branch name is lowercased
- each space becomes `-`
- every character other than `a-z`, `0-9`, `_`, `.` and `-` becomes `--`

So `feature/x` is made in `feature--x`, and `v1.2_rc` in `v1.2_rc`. The directory
need not exist, and git makes it.

If `BRANCH` exists, it is checked out in the worktree. Otherwise it is created,
at `REF` when `--from` gives one, or else at the project's `branch-from`, which is
`main` unless the registry says otherwise. `REF` is any commit, branch or tag,
and plays no part when `BRANCH` exists. The path of the worktree is reported on
standard output. Git's refusals, for a branch that is checked out in
another worktree or a directory that exists already, are errors that exit `1`,
and so is a project with no `worktrees`. Outside every registered project it is
a usage error, exit status `2`.

atc keeps no list of worktrees. Git is the only record, so worktrees made with
`git worktree add` are shown by `atc wt ls` and removed by `atc wt rm` just the
same, as long as they are in the project's `worktrees` directory.

Keep `worktrees` out of every directory a session may write to, so that a session
cannot remove its own worktree. `atc check` tests this for the worktree it runs in.

| Option | Effect |
| --- | --- |
| `--from REF` | Start a new branch at `REF` instead of at `branch-from`. |
| `--run` | Once the worktree is made, change into it and start the agent, as `atc run` does with no options. Nothing is started when the worktree could not be made. |

### `atc wt ls`

Lists the worktrees of the current project, as `atc prj show` does under its
fields: a table of what git lists for the project's repository, the main checkout
first, with each worktree's path, its branch (`(detached)` when `HEAD` is not on
a branch) and the first 7 characters of its commit. That includes worktrees that
are not in the project's `worktrees` directory. The table goes to standard output.

### `atc wt rm NAME [--force]`

Removes the worktree in the directory `NAME` of the project's `worktrees`
directory, which is the slug that `atc wt new` made, such as `feature--x`. `NAME`
must be a single directory name: `..`, and anything with a `/` in it, is an error.

Without `--force`, a worktree whose removal would lose work is refused, with an
error that says why and exits `1`:

- git refuses one with uncommitted changes to tracked files, or untracked files
- atc refuses one whose `HEAD` is detached at a commit that no branch contains,
  since nothing would keep that commit once the worktree and its reflog are gone
- atc refuses one where a rebase, merge, cherry-pick, revert or bisect is in
  progress

`--force` removes it anyway. A locked worktree is refused either way, and so is
the project's own `repo`, which is never removed. **Files
that git ignores are deleted** with the worktree, whatever they hold, as build
output such as `target/` or `.venv` usually is: move anything you want to keep
first. The branch is never deleted, so what is committed on it stays in the
repository, and a later `atc wt new` with the same branch checks it out again.

| Option | Effect |
| --- | --- |
| `--force` | Remove the worktree even if it has uncommitted changes or untracked files, a detached `HEAD` that no branch contains, or an operation in progress. |

### `atc sync [ACCOUNT]... [--check] [--diff] [--take-over] [--discard-live] [--lax] [--conf-d DIR]`

Brings four things in line with the configuration, for each account named, or
for every account:

- `settings.json` in the account's directory, written from the settings atc
  builds for the account (see `atc show account`)
- the instructions file (`CLAUDE.md`) there, as a symbolic link
- the skills the account selects, each as a symbolic link in `skills/` there
  (see Skills)
- the copies of templated assets, rendered for the account (see Templated
  assets)

The directory must exist already, because creating an account means logging in,
which atc does not do. Every account is planned before any is changed, so an
error in planning one leaves all of them as they were, and every account that
fails is reported. A write that fails, once the accounts are being written,
stops `atc sync` there with exit status `1`: the accounts before it stay
written.

#### Settings

Three versions are involved: the **new** settings the units produce, the **live**
file, and the **last** settings atc wrote, which it remembers. They are compared
as JSON, so a file that Claude Code only reformatted has not been edited. A live
file that is not valid JSON equals nothing: it counts as never synced when there
is no last, and as edited outside atc otherwise. A last that cannot be read counts
as missing, so it is never trusted. Each account is in one of these situations:

| Live file | Last | Situation | What happens |
| --- | --- | --- | --- |
| missing | any | Not created yet | New is written. |
| the same as new | any | Already current | Nothing is written, and new is recorded as last. |
| present | unknown | Never synced, and differs | The difference from live to new is shown, and nothing is written unless `--take-over` is given. |
| the same as last | known | Unchanged since the last sync | New is written. |
| not the same as last | known | Edited outside atc | The edit, from last to live, is shown, and nothing is written unless `--discard-live` is given. |

To keep an edit made outside atc, move it into a unit, commit it, run
`atc src pull`, and run `atc sync` again: live then equals new, so nothing is
discarded.

Each line of output is `ACCOUNT: STATUS` on standard output, where the status is
one of:

- `written`
- `current`
- `would write`, with `--check` or `--diff`
- `never synced and differs (--take-over writes it)`
- `edited outside atc (--discard-live overwrites it)`

Each link that changes, or is in conflict, gets one more line, for the entry's
name: `ACCOUNT: CLAUDE.md: BEFORE → AFTER STATUS`, or `ACCOUNT: <skill>: …`. `BEFORE` is `(none)`, the target
atc made, or a note that something else is there, and `AFTER` is the new target
or `(none)`. The status is `(would change)` with `--check` or `--diff`,
`(changed)`, or `(conflict; --take-over replaces a file or link, never a
directory)`.

An account whose settings are left alone also gets a unified diff. Warnings
about the account's settings, among them permission rules that can never apply
(see Rule warnings), go to standard error, and so do warnings about skills that
were skipped or whose front matter looks wrong (see Skills), once each.

**Every write** first copies the live file, when there is one, to
`<state_dir>/sync/ACCOUNT/backups/<UTC time>-settings.json`, such as
`20261023T120000Z-settings.json`, and keeps the last ten. It then replaces the file
in one step, from a temporary file in the same directory, and keeps the file's
permissions. A new file is readable by you alone. When `settings.json` is a
symbolic link, the file it leads to is the one replaced, from a temporary file in
that file's directory, and the link stays. That file must then be as far out of
sessions' reach as atc's configuration directory; `atc check` verifies that a
session cannot write it. The last settings are kept in
`<state_dir>/sync/ACCOUNT/settings.json`. `<state_dir>` is `~/.local/state/atc`, or
`atc` in `$XDG_STATE_HOME` when that is an absolute path. Backups and records
are readable by you alone, because settings can hold secrets.

Run `atc sync` from your own terminal: Claude Code does not let commands in its
sandbox write `settings.json`.

#### Instructions file

An account's instructions file is the `CLAUDE.md` in its directory. It becomes a
link to:

1. the account's `agents-md-file`, if it sets one: a path, or an asset
2. otherwise, if `use-default-agents-md` is `true`, the asset that
   `default-agents-md` names, which is `AGENTS.in.md` unless the registry says
   otherwise
3. otherwise nothing: atc creates nothing and leaves alone any file it did not
   make, while a link it made earlier is removed

An asset that is a template, such as the default, is linked as its copy (see
Templated assets).

The link is symbolic and its target is absolute. The target must exist, or the
account cannot be planned (and so none is changed). An `agents-md-file` that
is the account's own `CLAUDE.md` is refused too, as it would link to itself.

`<state_dir>/sync/ACCOUNT/links.json` records each link atc made and its target,
as paths. That tells atc's links from everything else, the way the last settings
do for `settings.json`. A record that cannot be read is ignored, so atc then
claims no link. Each entry is in one of these situations:

| Entry | Situation | What happens |
| --- | --- | --- |
| missing | Not created yet | The link is created, with any directories above it that are missing, if one is wanted. |
| a link to the wanted file | Already current | Nothing is written, and the link is recorded. |
| a link to the recorded file | The configuration changed, or a source moved | It is re-pointed, or removed when no link is wanted any more. |
| anything else | Not made by atc | It is left alone. If a link is wanted, that is a conflict, and `--take-over` replaces a link, or backs a regular file up and replaces it. A directory is never replaced. |

A link is made beside the entry under a temporary name and renamed over it, so
the entry is never missing and never dangling while it changes. A regular file
that `--take-over` replaces is first copied to
`<state_dir>/sync/ACCOUNT/backups/<UTC time>-CLAUDE.md`, and the last ten
backups of each file name are kept.

#### Skills

Each skill an account selects is linked as `<name>` in the `skills` directory of
the account's directory, to the skill's directory in its source, with the same
rules as for the instructions file. The `skills` directory is created if it is
missing. If it is a symbolic link, atc refuses the account with an error rather
than make links wherever it leads: replace it with a directory. A skill that is
no longer selected loses the link atc made, and a link that atc made is
re-pointed when its skill moves.

The account's `skills` directory holds entries that atc did not make: Claude
Code's own `synced/` and `.trash/`, directories made by hand, and links made by
tools such as an `install-skills.sh` into a working checkout. atc looks only at
the entry for each selected skill, and at the links in `links.json`. Everything
else is never touched. When something it did not make is where a selected
skill's link is to go, it is a conflict: `--take-over` replaces a link, or backs
a file up and replaces it, but a directory is never replaced.

#### Templated assets

A file under a source's `assets/` named `NAME.in.EXT`, or `NAME.in`, is a
template, such as `bin/clean-scratch-targets.in.sh` or `AGENTS.in.md`. It holds
what differs between accounts or machines, such as a scratch directory, so the
source does not have to name it. For each account, atc renders every template
to `<state_dir>/sync/ACCOUNT/assets/`, at the same path it has under `assets/`
but with `.in` dropped: `bin/clean-scratch-targets.sh`. With several sources,
each source's copies go in a directory named after it.

- **Rendering:** references are replaced as in a unit's settings (see
  Templates), with the account's variables and those of the template's own
  source, never a project's. Only string references can be used. As in a unit,
  a `{{` that does not start a reference is an error, and there is no escape: a
  shell script that needs a literal `{{` can write `'{''{'`. Everything else is
  copied as it is.
- **Errors:** a reference that cannot be expanded is an error that names the
  template, the account and the line. A template that is not UTF-8 text, and
  two templates whose copies need one path (`a.in.sh` and `a.sh.in`), are
  errors too. Any of them stops the account from being synced.
- **Mode:** the copy is executable by you when the template is executable, and
  readable only by you either way.
- **Using a copy:** `{{source.rendered}}` is where a unit's own source has its
  copies, so a unit that allows a script writes
  `Bash({{source.rendered}}/bin/clean-scratch-targets.sh:*)`. An instructions
  file that names a template links to its copy.
- **Safety:** the copy is out of reach of sessions, as `conf.d` is, so a script
  that relies on a path fixed in it keeps relying on it. atc follows no
  symbolic link there: a link where a copy goes is replaced, and a directory
  that is a link is an error.
- **Changes:** a copy is written, in one step, only when its text or mode
  differs from what its template gives. A copy whose template is gone is
  removed, after the links are made, since atc alone writes there. A changed
  template reaches an account at its next `atc sync`, like a changed unit.

#### Options

| Option | Effect |
| --- | --- |
| `--check` | Write nothing. Exit `3` if anything would be written, or was left alone. |
| `--diff` | Write nothing, and show the settings diff of each account that would change, each link that would change, and the diff of each rendered copy that would change. |
| `--take-over` | Write settings that were never synced but differ from new, and replace a link or a file that is in the way of the instructions file link or of a skill's link. |
| `--discard-live` | Write settings that were edited outside atc, discarding the edit. |
| `--lax` | Report settings keys Claude Code does not define as warnings rather than errors. |
| `--conf-d DIR` | Read config sources from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). Only with `--check` or `--diff`: `sync` writes only what the default sources build. |

The exit status is `3` when an account was left alone (never synced and
differing, edited outside atc, or an instructions file or a skill link in
conflict) and with `--check` when anything would be written, `1` when an
account cannot be built, planned or written, and `0` otherwise.

### `atc check`

Checks, from inside a session, that its protections hold. Run it through the
agent's shell tool, so that it runs in the sandbox: ask the agent to run
`atc check` after the first session in a repository, and again whenever the
sandbox settings change. It only inspects and never writes. It reads the machine
registry, but none of the config sources.

It prints one `PASS` or `FAIL` line for each check, in these groups:

- **The repository:** `objects/pack` and `packed-refs` cannot be written, nor can
  `hooks` and `config` when they exist, new objects can be written, git's
  automatic gc is off, and the directory above the worktree cannot be written.
  When `extensions.worktreeConfig` is on, git also reads `config.worktree`, the
  shared one and the current worktree's own, and neither can be written; when
  it is off, they are not checked.
  If git cannot inspect the repository, for example because it refuses one that
  someone else owns, that is a `FAIL`, not a skip.
- **The accounts:** the session's own account's credentials (`.credentials.json`
  and the state file), and every other account's directory and
  files, cannot be read. The session's account is `ATC_ACCOUNT`, otherwise the
  account whose directory is `CLAUDE_CONFIG_DIR`, or the one in `~/.claude` when
  that is unset. If none matches, a check fails and every account counts as
  another one.
- **atc itself:** atc's configuration directory, its `conf.d` of config sources,
  its state and runtime directories, the directory atc is installed in, and the
  Python installation it runs on (`atc.python_dir`), cannot be written, and
  neither can `machine.yaml`, or the file it leads to when it is a link.
- **Settings, instructions and skills:** the account's `settings.json`, or the
  file it leads to when it is a link, cannot be written, and neither can what the
  account's instructions file and every entry of its skills directory lead to.
  That is a link's target, or the entry itself when it is a file or a directory,
  and for each skill, its `SKILL.md` too. This includes links that `atc sync`
  did not make.

A path that is missing, or a link that leads nowhere, is no reason to pass: a
session could create what is not there. Such a check passes only when the
nearest directory above the path that exists cannot be written, and the line
says the path is missing and can't be created; otherwise it fails, saying where
the path could be created. A skills directory that is there but cannot be listed
is a `FAIL` too. Links are followed to where a write would land, including a link
that is the nearest directory above a missing path, and a loop of links, or more
than 40 of them, is a `FAIL`.

A check that does not apply is not a `PASS`: a plain line says so instead. That
is the case for the repository checks outside a repository, and for the
instructions and skills checks when no account matches the session.

The status is `0` when every check passes, and `3` when any fails. If the home
directory can be written this is not a sandbox: `atc check` says so on standard
error and exits with `2`. `PASS` and `FAIL` are coloured only on a terminal.

### `atc maint`

Keeps the repositories of guarded sessions packed. Sessions cannot write a
repository's packed history and git's own automatic gc is off inside them (see
The guard), so `atc maint` does the packing from outside. It reads the machine
registry but none of the config sources, and prints nothing when all is well.

For each repository in `<state_dir>/repos`, in order, it:

1. skips it with a warning on standard error when it no longer has an
   `objects/` directory.
2. runs `git repack --geometric=2 -d -q`. Geometric repacking only merges packs
   and never drops an object, so it is safe while sessions run.
3. tries to take the repository's lock exclusively, without waiting (see The
   guard). When a session holds it, that is all. Otherwise it runs
   `git pack-refs --all`, and then `git gc --quiet` if the repository's stamp,
   `<state_dir>/<key>.gc`, is missing or older than `maint.gc-hours` (see The
   machine registry), and renews the stamp. Both rewrite `packed-refs`, which a
   running session must not see change, so they wait for the sessions to end.

A repository that fails is reported on standard error as
`error: maintenance failed for <path>: <git's message>`, and the others are still
maintained. The status is `1` when any repository failed, and `0` otherwise.

Run it from your own terminal or a timer, not through the agent: it writes to
the repositories. atc comes with a systemd user service and timer for it in
`atc/contrib/systemd/`: the timer starts the service 5 minutes after boot and
every 10 minutes after the last run, and the service runs
`%h/.local/bin/atc maint` at low priority, which is where `uv tool install`
puts atc. Install them by hand, changing `ExecStart` if atc is elsewhere:

```sh
mkdir -p ~/.config/systemd/user
cp atc/contrib/systemd/atc-maint.service atc/contrib/systemd/atc-maint.timer \
    ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now atc-maint.timer
```

The systemd user manager does not see variables set in your shell's startup
files. If you set `XDG_STATE_HOME`, `XDG_RUNTIME_DIR` or `XDG_CONFIG_HOME` there,
set them for the user manager too, with `systemctl --user set-environment` or a
file in `~/.config/environment.d/`. Otherwise the timer's `atc maint` reads a
different repository list, and takes different locks, than the `atc` you run.

If `claude-guard`'s maintenance timer is installed, disable it at the same time,
so that only one of them packs the repositories:

```sh
systemctl --user disable --now claude-guard-maint.timer
```

### `atc usage`

Shows each account's recorded five-hour usage, as a table with one row per
account of the machine registry, in the order of the file: the account, how much
of its five-hour limit the last record says was used, and how long ago that was
recorded (`just now`, `5m ago`, `3h 20m ago`, `2d 4h ago`). The percentage is
rounded to a whole number. On a terminal it is yellow below 70 % and red from
70 %, as in the statusline, and it has no colour otherwise. The table goes to
standard output. Nothing is changed.

- An account with no record shows `no data`.
- A record with no five-hour figure shows `-` for the percentage, with its age.
  Claude Code does not always give one.
- A record that cannot be used shows `no data` too, and a warning on standard
  error names the file and what is wrong with it. The exit status stays `0`.
- A record whose percentage was clamped shows the clamped number, with the same
  kind of warning.

**The record.** The statusline of a session that atc started, which has
`ATC_ACCOUNT` and `ATC_STATE_DIR` in its environment, writes
`<state_dir>/usage/ACCOUNT.json` each time Claude Code gives it `rate_limits`:

```json
{ "recorded_at": "2026-10-23T09:00:00Z", "rate_limits": {} }
```

`recorded_at` is an ISO 8601 time with a UTC offset, `Z` as the statusline
writes it. `rate_limits` is copied as the statusline received it. atc reads only
`five_hour.used_percentage` in it, a number, normally from 0 to 100, that may be
missing.

Nothing in the file is trusted, since anyone who can write the state directory
can write it:

- **What is read.** Only a regular file of at most 64 KiB. A FIFO, a device, a
  directory or a symbolic link in its place, even one to a regular file, is
  refused without being read or waited for, and so is a larger file. Each
  gets a warning that names the file.
- **When the record cannot be used.** When the file is not valid JSON or not an
  object, when `recorded_at` is missing, is not a time with a UTC offset, or is
  in the future, when `rate_limits` or `five_hour` is not an object, or when the
  percentage is not a finite number.
- **A percentage outside 0 to 100** is clamped to 0 or 100, and the record is
  used with a warning that names the file and says it was clamped. An account
  that has used up its limit can be recorded as, say, 100.4; clamped, it reads as
  full and not as unknown.

`atc -a auto` picks a project's least used account from these records (see
`atc [run]`). It treats an account whose record cannot be used as one with no
data.

Only usage from sessions that atc started on this machine is recorded. Usage
from other machines, or from claude.ai, is not.

### Rule warnings

Claude Code applies permission rules deny first, then ask, then allow, whichever
settings file each rule comes from. A rule that a stronger rule always overrides
never takes effect, so atc warns about it:

- an `allow` rule that a `deny` or an `ask` rule covers
- an `ask` rule that a `deny` rule covers

If `common/base.json` denies `Bash(git rebase:*)` and `projects/bar.json` allows
it, every rebase in `bar` is still refused:

```text
warning: allow Bash(git rebase:*) (projects/bar.json) never applies: deny Bash(git rebase:*) (common/base.json) takes precedence
```

The warning names both rules and the units that gave them, and leaves the fix to
you. A rule is reported once, naming the strongest rule that covers it: a `deny`
over an `ask`, then the earliest in the merge order.

A rule **covers** another when it matches everything the other one matches:

- the two have the same text
- it is a bare tool name, such as `Bash`, and the other is a rule for that tool
- for `Bash` rules, its pattern, with `*` as a wildcard, matches the text of the
  other read literally. A trailing `:*` means the same as a trailing ` *`, so
  `Bash(git *)` covers `Bash(git rebase:*)`, and `Bash(rm:*)` covers
  `Bash(rm -rf target:*)`. Claude Code reads a rule that ends in `:*` as a
  literal prefix, so a `*` before the `:*` stands for itself:
  `Bash(SQLX_* cargo build:*)` matches only commands that start with
  `SQLX_* cargo build`. Write `Bash(SQLX_* cargo build *)` for the wildcard

The check is conservative: it can miss a conflict, but it does not report one
that is not there. A partial overlap is not reported, so denying
`Bash(git rebase -i:*)` does not make allowing `Bash(git rebase:*)` dead. Rules in
a repository's own `.claude/settings*.json` are not considered.

`atc show account` checks the account's settings alone. `atc show project`
checks the account's and the project's together, as Claude Code sees them.
`atc run --print` checks the account's, the project's and the `--settings` it
was given together, and names `--settings` as the origin of a rule that comes
from there. `atc src check` checks every account, and every project with each
account it allows. `atc sync` checks the account it writes. A launch without
`--print` does not check. These warnings go to standard error. They never change
what atc prints or writes, and never change the exit status.

`atc src check` also warns about a rule, in any unit, that is better written
another way:

- a file path on a `Write`, `NotebookEdit`, `Grep` or `Glob` rule, such as
  `Write(*.env)`, which never matches anything. Claude Code matches file paths
  only on `Edit` rules, which cover `Edit`, `Write` and `NotebookEdit`, and on
  `Read` rules, which cover `Read`, `Grep` and `Glob`. In an `allow`, the
  `Edit` or `Read` rule that replaces it allows that tool as well
- a `Bash` rule whose only `*` is a trailing ` *`, such as `Bash(git log *)`. It
  matches the same commands as `Bash(git log:*)`, and is reported so that every
  prefix is written the same way

```text
warning: deny Write(*.env) (common/base.json) never matches: Claude Code matches file paths only on Edit rules, which cover Edit, Write and NotebookEdit; write it as Edit(*.env)
```

### Output and exit status

Output is coloured only when it goes to a terminal. `NO_COLOR` and
`FORCE_COLOR` are honoured.

Exit status is `0` on success, `1` for a problem with the configuration, a
config source or git, which includes a source `atc src pull` failed or refused,
a repository `atc maint` could not maintain and a file `atc sync` could not write, `2` for a problem with the command line or for
`atc check` outside a sandbox,
`3` when `atc sync` left an account alone or, with `--check`, has something to
write, or when a check of `atc check` fails, and `130` when the command is interrupted with Ctrl-C, after a short
`aborted` on standard error (`cancelled; nothing changed` for `atc skills pick`).

## Development

From the repository root:

```sh
uv run --directory atc atc show --help    # run atc from the working tree
./atc/check-and-fix.sh                    # format, lint, type check, test
./atc/check-and-fix.sh --check            # verify without changing anything
```
