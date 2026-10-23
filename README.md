# atc

`atc` is the one command that starts coding agents on this machine. Run it
inside a project and it works out which project that is, which agent and
account should take the session, and which settings and protections apply, then
hands the terminal over to the agent. It generates each account's settings from
shared configuration that lives in your own repositories, apart from the tool.
Claude Code is the first agent it supports.

Today the tool implements `atc run`, `atc sync`, `atc show account`,
`atc show project`, `atc skills ls`, `atc skills pick`, `atc skills add`,
`atc skills rm`, `atc src ls`, `atc src check` and `atc src pull`.
The rest is being built step by step.

## Install

```sh
uv tool install ./atc
```

Run this from a commit you have reviewed, and never with `--editable`: an
editable install runs the code in the working checkout, which sessions can
edit, and atc's code decides every session's protections.

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

```yaml
version: 1
vars:
  scratch_root: /mnt/scratch
default-account: personal
default-agents-md: AGENTS.md
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
    agents-md-file: "@work/AGENTS.md"
    skills: [git-commits, "@work/review"]
    vars:
      scratch: /mnt/scratch/work
projects:
  bar:
    repo: ~/src/bar
    account: work
    accounts: [work, personal]
    vars:
      team: ci
```

| Key | Required | Meaning |
| --- | --- | --- |
| `version` | yes | Must be `1`. |
| `vars` | no | Variables for every unit, as `{{vars.<name>}}` (see Templates). |
| `default-account` | yes | The account used outside projects, and by projects that name none. It must be one of `accounts`. |
| `default-agents-md` | no | The file that `use-default-agents-md` links, as an asset reference (see Units). Defaults to `AGENTS.md`. |
| `agents.<agent>.bin` | no | The binary `atc run` starts for the agent: a command name, looked up on `PATH`, or a path, which is absolute or starts with `~/`. A value with a `/` in it is a path. Defaults to the agent's name. Only `claude` exists. |
| `accounts.<name>.agent` | yes | The coding agent the account is for. Only `claude` exists. |
| `accounts.<name>.dir` | yes | The account's configuration directory. |
| `accounts.<name>.unit` | no | The unit the account's settings are built from, as a unit reference (see Units). Defaults to `accounts/<name>.json`. |
| `accounts.<name>.use-default-agents-md` | no | `true` links `default-agents-md` as the account's instructions file (see `atc sync`). Defaults to `false`. |
| `accounts.<name>.agents-md-file` | no | The file to link as the account's instructions file instead of the default: an absolute or `~/` path, or an asset reference (see Units). `null` means not set. |
| `accounts.<name>.skills` | no | The skills to link into the account, as skill references (see Skills). Defaults to none. |
| `accounts.<name>.vars` | no | Variables for this account's units, as `{{account.vars.<name>}}`. |
| `projects.<name>.repo` | yes | A checkout of the project's repository. |
| `projects.<name>.account` | no | The account the project uses by default. Defaults to `default-account`. |
| `projects.<name>.accounts` | no | The accounts allowed to work on the project. When set, it must name at least one and include the project's account: `account`, or `default-account` when the project sets none. Defaults to every account. |
| `projects.<name>.unit` | no | The unit the project's settings are built from, as a unit reference (see Units). Defaults to `projects/<name>.json`. When that default does not exist, the project has no settings of its own. |
| `projects.<name>.vars` | no | Variables for this project's units, as `{{project.vars.<name>}}`. |

No other keys are accepted yet, and a misspelled key is an error. The rules:

- The file is YAML 1.2, so `no`, `on` and `off` stay strings. Only values JSON
  can hold are accepted: a timestamp such as `2026-10-23` is an error that
  asks you to quote it. Repeated keys and keys that are not strings are errors.
- A leading `~` in a path stands for your home directory. Every path must then
  be absolute. Environment variables are not expanded, so `$HOME/x` is an
  error.
- Account and project names are lowercase letters, digits, `_` and `-`,
  starting with a letter or a digit.
- Every account that the registry mentions must exist: `default-account`, and
  each project's `account` and `accounts`.
- A project's `accounts` cannot be empty, and must include the account the
  project uses by default: its `account`, or `default-account` when it sets
  none.
- A `repo` is a path like a `dir`: a leading `~` is expanded and it must then
  be absolute.
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
  YAML. The skill counts either way.
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
| `settings` | Optional fragment of `settings.local.json`. Its top-level keys must be ones Claude Code defines (`atc show account --lax` accepts others with a warning). |

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
as `AGENTS.md` or `@work/docs/AGENTS.md`. They follow the same rules as unit
references, with `assets/` in place of `conf/` and with no extension required:
a reference must be relative and must stay inside the `assets/` of its source,
links included, and without `@` exactly one source must contain the file.

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
| `source.name` | string | The name of the source that contains the unit. Empty with a single source. |
| `source.root`, `source.conf`, `source.assets` | string | That source's directory, its `conf/` and its `assets/`. |
| `sources.<name>.root`, `.conf`, `.assets` | string | The same for any source, by name. Only with several sources. |
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

### `atc [run] [-a ACCOUNT] [--settings FILE|JSON] [--print] [-q] [--conf-d DIR] [-- AGENT-ARGS]...`

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
- **the binary:** `agents.<agent>.bin` from the registry, otherwise the
  agent's name, looked up on `PATH`. It must exist. atc refuses to start when it
  resolves to atc itself, whether it was found on `PATH` or named in the
  registry, which happens when `claude` is a link to `atc`; name the real binary
  under `agents.claude.bin`.

Before it replaces itself, atc prints one line on standard error:

```
atc: project bar · account bravo
```

It says `project –` outside a registered project. `-q` leaves the line out.
Nothing else from atc appears once the agent has started, and the agent's exit
status is the command's.

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

Every other `ATC_` variable is removed, so a session started from inside another
one never inherits the outer one's values.

**Settings.** With `--settings`, the agent is started as
`claude --settings '<settings as JSON>' …`. They are read as strict JSON, are
not templates, and must not set `sandbox.allowUnsandboxedCommands`. Without it
there is no `--settings` argument.

| Option | Effect |
| --- | --- |
| `-a`, `--account ACCOUNT` | Use `ACCOUNT` instead of the project's own account. |
| `--settings FILE\|JSON` | Settings for this session only: the path of a JSON file, or JSON text, which is anything that starts with `{`. |
| `--print` | Show on standard output how the agent would be started, and exit with status `0` without starting it: the repository, project, account, the changes to the environment, the command, and the settings as JSON. |
| `-q`, `--quiet` | Leave out the line that names the project and the account. |
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
- a unit with a reference that cannot be expanded for an account, or for a
  project and one of its accounts, such as an unknown variable
- a unit that sets a key its layer does not allow: `autoMemoryDirectory` is
  refused in an account's settings, because the value is the memory directory
  itself and every project would share it, and `sandbox.allowUnsandboxedCommands`
  is refused in a project's

It also warns about permission rules that can never apply (see Rule warnings),
and about skills that were skipped or whose front matter looks wrong (see
Skills). Warnings never fail the check. Without a registry, only the units are checked,
with a warning saying so.

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
one line for each link that changed, as `atc sync` does. It exits `3` when a link
is left alone for want of `--take-over`. The selection has been written by then.

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

### `atc sync [ACCOUNT]... [--check] [--diff] [--take-over] [--discard-live] [--lax] [--conf-d DIR]`

Brings three things in the directory of each account named, or of every account,
in line with the configuration:

- `settings.json`, written from the settings atc builds for the account (see
  `atc show account`)
- the instructions file (`CLAUDE.md`), as a symbolic link
- the skills the account selects, each as a symbolic link in `skills/` (see
  Skills)

The directory must exist already, because creating an account means logging in,
which atc does not do. Every account is planned before any is changed, so an
error in one leaves all of them as they were, and every account that fails is
reported.

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
permissions. A new file is readable by you alone. The last settings are kept in
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
   `default-agents-md` names, which is `AGENTS.md` unless the registry says
   otherwise
3. otherwise nothing: atc creates nothing and leaves alone any file it did not
   make, while a link it made earlier is removed

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

#### Options

| Option | Effect |
| --- | --- |
| `--check` | Write nothing. Exit `3` if anything would be written, or was left alone. |
| `--diff` | Write nothing, and show the settings diff of each account that would change, and each link that would change. |
| `--take-over` | Write settings that were never synced but differ from new, and replace a link or a file that is in the way of the instructions file link or of a skill's link. |
| `--discard-live` | Write settings that were edited outside atc, discarding the edit. |
| `--lax` | Report settings keys Claude Code does not define as warnings rather than errors. |
| `--conf-d DIR` | Read config sources from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). Only with `--check` or `--diff`: `sync` writes only what the default sources build. |

The exit status is `3` when an account was left alone (never synced and
differing, edited outside atc, or an instructions file or a skill link in
conflict) and with `--check` when anything would be written, `1` when an
account cannot be built, planned or written, and `0` otherwise.

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
  `Bash(rm -rf target:*)`

The check is conservative: it can miss a conflict, but it does not report one
that is not there. A partial overlap is not reported, so denying
`Bash(git rebase -i:*)` does not make allowing `Bash(git rebase:*)` dead. Rules in
a repository's own `.claude/settings*.json` are not considered.

`atc show account` checks the account's settings alone. `atc show project`
checks the account's and the project's together, as Claude Code sees them.
`atc src check` checks every account, and every project with each account it
allows. `atc sync` checks the account it writes. These warnings go to standard
error. They never change what atc prints
or writes, and never change the exit status.

### Output and exit status

Output is coloured only when it goes to a terminal. `NO_COLOR` and
`FORCE_COLOR` are honoured.

Exit status is `0` on success, `1` for a problem with the configuration, a
config source or git, which includes a source `atc src pull` failed or refused
and a file `atc sync` could not write, `2` for a problem with the command line,
`3` when `atc sync` left an account alone or, with `--check`, has something to
write, and `130` when the command is interrupted with Ctrl-C, after a short
`aborted` on standard error (`cancelled; nothing changed` for `atc skills pick`).

## Development

From the repository root:

```sh
uv run --directory atc atc show --help    # run atc from the working tree
./atc/check-and-fix.sh                    # format, lint, type check, test
./atc/check-and-fix.sh --check            # verify without changing anything
```
