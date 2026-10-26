# atc — Agent Traffic Control: design

- Date: 2026-10-23
- Status: draft
- Concept: [000-concept.md](000-concept.md)
- Supersedes: `docs/superpowers/specs/2026-08-16-claudeconf-design.md`, in the
  config repository (§3)

This document describes how atc works. Read the concept first: it explains why
atc exists and defines the terms used here (agent, account, project, config
source, unit, layer, machine registry).

## 1. Scope

**First version:** Claude Code is the only agent. It includes `src`, `sync`,
`run`, `show`, `prj`, `wt`, `skills`, `check`, `maint` and `usage`. `sync` also
links each account's instructions file (`CLAUDE.md`) and its chosen skills.

**Second phase:** `atc -a auto` picks an account from recorded usage (§10.9).

**Later, designed separately:**

- the Codex backend. This document marks where it plugs in (§9) but doesn't
  specify it.
- installing plugins and adding marketplaces per account, probably with the same
  kind of picker as skills (§10.10)

**Out of scope:**

- containers
- snapshot setup
- judging what permission rules allow or deny. That is your configuration. atc
  only warns about rules that can never take effect (§8).

## 2. Facts this design relies on

| ID  | Fact                                                                                                                                                                                                     | Source                                                                                                   |
| :-- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------- |
| F1  | `CLAUDE_CONFIG_DIR` selects an account's whole directory. The default directory is `~/.claude`. Its state file is `~/.claude.json`, but for any other directory it is `<dir>/.claude.json`               | Observed on this machine: the Acme account's dir contains `.claude.json`; the personal one's sits in `~` |
| F2  | Claude Code honours only one `--settings`. With two, only the last takes effect                                                                                                                          | Headless probe, 2026-10-23                                                                               |
| F3  | `sandbox.filesystem.allowWrite` passed through `--settings` is added to the user settings' list, not swapped in for it                                                                                   | Headless probe, 2026-10-22                                                                               |
| F4  | Claude Code writes an inline `--settings` JSON to a temporary file, and the sandbox makes that file read-only                                                                                            | Same probe: `claude-settings-<hash>.json` is listed under the sandbox's write denies                     |
| F5  | `autoMemoryDirectory` is used as the memory directory itself; no per-project subfolder is created under it. It is honoured from a repository's `settings.local.json`, and `~/` expands                   | Headless probes, 2026-10-22. The docs say it is read from every settings scope                           |
| F6  | Claude Code merges settings in this order, highest first: managed, command line, local project, shared project, user. Arrays are combined across scopes                                                  | Claude Code settings documentation                                                                       |
| F7  | `sandbox.allowUnsandboxedCommands: false` set through `--settings` makes the sandbox admin-required. Claude Code then ignores the repository's `allowWrite`, allowed domains and `WebFetch` domain rules | Sandbox-hardening notes (`_tmp/claude-sandbox-hardening/README.md`, not in git). **Not re-checked here** |
| F8  | The JSON the statusline receives includes `rate_limits.five_hour.used_percentage`, though not always                                                                                                     | The existing statusline script reads it                                                                  |
| F9  | With `objects/pack` and `packed-refs` write-protected, `git commit` still works, and deleting or rewriting either fails                                                                                  | Sandbox-hardening notes (as for F7), tested there with throwaway repositories                            |
| F10 | The statusline command inherits the Claude Code process's environment                                                                                                                                    | The statusline script already reads `CLAUDE_CONFIG_DIR` to tell the accounts apart                       |
| F11 | Claude Code loads user skills from `<dir>/skills/<name>/SKILL.md`, following symbolic links                                                                                                              | Both accounts' `skills/` hold links into the config repository's `skills/`, and they load in sessions    |
| F12 | `<dir>/skills/` also holds entries that nothing here created: `synced/` and `.trash/` in both accounts, and hand-made directories in the Acme one                                                        | Observed on this machine, 2026-10-23                                                                     |
| F13 | Permission rules apply deny first, then ask, then allow, whichever settings file each comes from. A deny in any scope can't be overridden by an allow elsewhere                                          | Claude Code permissions documentation                                                                    |
| F14 | `Bash` denies `echo a:*` and `echo a *` act alike: each refuses `echo a b` and bare `echo a`, not `echo ab`. `echo a*` refuses `echo ab`; `echo * b*` doesn't refuse `echo b c`. `*` spans spaces        | Headless probe, 2026-10-23; a `git * pull*` deny refused a commit with ` pulled` in its message          |
| F15 | In `Read` and `Edit` permission rules, `//path` is absolute, `~/path` is under the home directory, `/path` is relative to the settings file, and a bare path is relative to the current directory        | Claude Code permissions documentation                                                                    |

**Not established yet:**

- Whether `sandbox.filesystem.denyWrite` from `--settings` merges the way F3
  shows `allowWrite` does. The session layer (§10.3) depends on it. See
  [open question 1](#19-open-questions).
- What happens if `CLAUDE_CONFIG_DIR` is set explicitly to `~/.claude`. §9
  avoids this case rather than relying on it.
- Whether Claude Code follows a symbolic link at `<dir>/CLAUDE.md`. It is
  expected, given F11, but untested. See [open question 3](#19-open-questions).

## 3. Repository layout

The tool and the configuration it reads are kept apart, in two repositories:
atc's, and the config repository, which holds your units, assets and skills. atc
started there as `atc/`, and moved out of it unchanged.

```text
atc/                          # this repository: the tool
  pyproject.toml              # package "atc", script atc = "atc.cli:main"
  pyrightconfig.json
  check-and-fix.sh
  README.md
  CLAUDE.md
  contrib/systemd/            # atc-maint.service, atc-maint.timer
  docs/
  examples/                   # a source and registry to start from
  src/atc/
  tests/                      # fixture config only, never the real one

my-config/                    # the config repository
  conf/                       # personal units (was repo.conf/conf.d/)
    common/
    accounts/
    projects/
    langs/
  assets/                     # files the units point at
    AGENTS.in.md              # the default instructions file (§10.2)
    hooks/
    bin/
    statusline.sh
  skills/                     # one directory per skill (§10.10)
```

The config repository's root contains `conf/`, `assets/` and `skills/`, so it is
itself a valid config source (§6). Cloning it into atc's `conf.d/` deploys it.

- **Toolchain:** Python 3.13, pydantic v2 and uv, as for `claudeconf`.
- **New dependencies:**
  - `ruamel.yaml` for the machine registry (§5) and skills' front matter
  - `click` and `rich-click` for the command line
  - `rich` for output (§12)
  - `questionary` for the skills picker (§10.10). rich has no interactive
    selection
- **Install:** `uv tool install .`, from a commit you have reviewed. Not
  `--editable`: an editable install runs the code in the working checkout, which
  sessions can edit, and atc's code decides every session's protections. The
  hardening unit write-protects the installed copy too (§13).
- **Quality gates:** `check-and-fix.sh` (ruff format, ruff check,
  `scripts/normalize-markdown.sh` for the markdown, basedpyright in `all` mode,
  pytest) stays the gate.

## 4. Directories atc uses

| Name          | Default                                   | Contents                                                            |
| :------------ | :---------------------------------------- | :------------------------------------------------------------------ |
| `config_dir`  | `${XDG_CONFIG_HOME:-~/.config}/atc`       | `machine.yaml`, and `conf.d/`                                       |
| `conf_d`      | `<config_dir>/conf.d`                     | Config sources (§6)                                                 |
| `state_dir`   | `${XDG_STATE_HOME:-~/.local/state}/atc`   | `repos` (the guard's repository list), gc stamps, `sync/`, `usage/` |
| `runtime_dir` | `${XDG_RUNTIME_DIR:-/run/user/<uid>}/atc` | Repository session locks                                            |

`<state_dir>/sync/<account>/` holds what `atc sync` last wrote for an account
(§10.2): `settings.json`, `links.json`, `backups/`, and `assets/`, the copies of
templated assets rendered for it.

As the XDG specification requires, an `XDG_*` variable that is set but not an
absolute path is ignored. Sessions must not be able to write to any of these
directories (§13).

## 5. Machine registry

The registry is `<config_dir>/machine.yaml`. It holds everything that is
specific to this machine, and nothing else. It belongs to no config source, so
it never lands in a git repository by accident.

```yaml
version: 1

# Machine-wide values, available to units as {{vars.<name>}}.
vars:
  scratch_root: /mnt/pci4-scratch/cc

# Used outside registered projects, and by projects that set no account.
default-account: personal

agents:
  claude:
    bin: claude # optional; otherwise looked up on PATH

accounts:
  personal:
    agent: claude
    dir: ~/.claude
    use-default-agents-md: true # link assets/AGENTS.in.md's copy as CLAUDE.md
    skills: [git-commits, rust-2024, seq-docs-convention]
    vars:
      scratch: /mnt/pci4-scratch/cc/joao
  acme:
    agent: claude
    dir: ~/.config/acme/claude
    use-default-agents-md: true
    skills: [git-commits, hawt-tracking, seq-docs-convention, seq-docs-review]
    vars:
      scratch: /mnt/pci4-scratch/cc/acme

projects:
  anvil:
    repo: /mnt/pci5-dev/acme/anvil.git
    worktrees: /mnt/pci5-dev/acme/wt/anvil
    branch-from: main
    account: acme # the default for this project
    accounts: [acme, personal] # the accounts allowed to work on it
    snapshot: acme # optional: snapper config to snapshot before each session
```

### Fields

| Path                                    | Required | Meaning                                                                                                                          |
| :-------------------------------------- | :------- | :------------------------------------------------------------------------------------------------------------------------------- |
| `version`                               | yes      | Currently `1`                                                                                                                    |
| `vars`                                  | no       | Machine-wide variables                                                                                                           |
| `default-account`                       | yes      | An account name                                                                                                                  |
| `default-agents-md`                     | no       | Asset reference (§7.2) to the default instructions file. Defaults to `AGENTS.in.md`                                              |
| `agents.<agent>.bin`                    | no       | The agent binary                                                                                                                 |
| `accounts.<name>.agent`                 | yes      | `claude` (later also `codex`)                                                                                                    |
| `accounts.<name>.dir`                   | yes      | The account's config directory                                                                                                   |
| `accounts.<name>.unit`                  | no       | Unit reference (§7.2). Defaults to `accounts/<name>.json`                                                                        |
| `accounts.<name>.use-default-agents-md` | no       | `true` links `default-agents-md` into the account (§10.2). Defaults to `false`                                                   |
| `accounts.<name>.agents-md-file`        | no       | An absolute or `~/` path, or an asset reference. When set, it is linked instead of the default (§10.2). `null` means not set     |
| `accounts.<name>.skills`                | no       | Skill references (§7.2) to link into the account (§10.10). Defaults to none                                                      |
| `accounts.<name>.vars`                  | no       | Variables for this account                                                                                                       |
| `projects.<name>.repo`                  | yes      | A checkout of the repository (main or linked; resolved to its shared `.git`)                                                     |
| `projects.<name>.worktrees`             | no       | Where `atc wt new` creates worktrees. Required by `wt new`                                                                       |
| `projects.<name>.branch-from`           | no       | Default base for new branches. Defaults to `main`                                                                                |
| `projects.<name>.account`               | no       | Default account. Defaults to `default-account`                                                                                   |
| `projects.<name>.accounts`              | no       | The accounts allowed for this project. If set, it must include `account`, and `-a` must name one of them                         |
| `projects.<name>.unit`                  | no       | Unit reference (§7.2). Defaults to `projects/<name>.json`. If the default doesn't exist, the project simply has no project layer |
| `projects.<name>.snapshot`              | no       | snapper config name                                                                                                              |
| `projects.<name>.vars`                  | no       | Variables for this project                                                                                                       |
| `maint.gc-hours`                        | no       | Hours between full `git gc` runs in `atc maint`. Defaults to 24                                                                  |

### Loading rules

- **Parser:** `ruamel.yaml` in safe mode with YAML 1.2. Unlike PyYAML, it keeps
  `no`, `on` and `off` as strings, and it rejects duplicate keys.
- **Accepted value types:** only JSON types. A YAML timestamp or any other
  non-JSON value is an error that tells you to quote it. An alias for a list or
  a mapping is an error too, since expanding aliases of aliases grows without
  bound, and so is a merge key (`<<`), which JSON has no form of. `SKILL.md`
  front matter is read the same way.
- **Validation:** pydantic models with `extra="forbid"` and strict types. A
  misspelled key is an error, not something silently ignored.
- **Paths:** a leading `~` is expanded. Every path must be absolute after that.
  Environment variables are not expanded.
- **Names:** account and project names match `[a-z0-9][a-z0-9_-]*`. Variable
  names match `[a-z][a-z0-9_]*`, so they can be used in references (§7.3).
  Variable values must not contain `{{`.
- **Consistency checks:**
  - Every account name that is mentioned must exist.
  - No two accounts may share a `dir`.
  - A project's `accounts`, if set, must not be empty, and must include the
    project's effective account: its `account`, or else `default-account`.
  - An account that sets both `agents-md-file` and `use-default-agents-md: true`
    gets a warning, since the flag then has no effect.
- **Writes:** `atc account add` and `atc account rm`, `atc prj add` and
  `atc prj rm`, `atc vars set`, and the `atc skills` commands that change a
  selection edit the file with ruamel's round-trip mode, so comments and key
  order survive. When `machine.yaml` is a symbolic link, as into a dotfiles
  repository, the file it leads to is replaced and the link stays. That file
  must be as far out of sessions' reach as `config_dir`: a session that could
  edit the registry could point `agents.claude.bin` at a program that runs
  outside the sandbox, or widen the sandbox through `vars`. `atc check` verifies
  it (§10.7).

## 6. Config sources

A **config source** is a directory that contains at least one of `conf/`
(units), `assets/` (files the units and the registry point at) and `skills/`
(§10.10). Anything else in it is ignored. That way a whole repository can be a
source as it is, whatever else it holds.

atc reads sources from `conf_d`, in one of two layouts:

| Layout | `conf_d` looks like                                            | Sources                                                   |
| :----- | :------------------------------------------------------------- | :-------------------------------------------------------- |
| Single | `conf.d/{conf,assets}/`                                        | `conf_d` itself, one source with no name                  |
| Multi  | `conf.d/personal/{conf,assets}/`, `conf.d/acme/{conf,assets}/` | each direct subdirectory that is a source, named after it |

**Discovery rules:**

- **Depth:** atc looks no deeper than one level.
- **Layout:** if `conf_d` itself contains `conf/`, `assets/` or `skills/`, the
  layout is single. Finding sources at both levels is an error.
- **Ignored entries:** entries whose names start with `.` (such as `.git`), and
  plain files.
- **Unused directories:** a visible subdirectory that has none of `conf/`,
  `assets/` and `skills/` gets a warning.
- **Names:** source names match `[a-z][a-z0-9_]*`, so every source can be named
  in a reference such as `{{sources.<name>.root}}` (§7.3).

Each source is normally a git clone, and it can come from a different repository
than the others. For example, `personal` might be your own config repository and
`acme` an Acme one. Moving from the single layout to the multi layout means
moving the clone one level down. Units that don't refer to other sources need no
changes (§7.2).

**Trust rules.** Hooks and the statusline run from `assets/` outside the
sandbox, and instructions files and skills tell every session what to do. So
`conf_d` itself, each source directory, and everything under a source's `conf/`,
`assets/` and `skills/` must meet these rules, or atc refuses to load it:

- **No symbolic links.** A link would let a working checkout, which an agent may
  be editing, stand in for deployed configuration.
- **Owned by you,** and **not group- or world-writable.** This covers the parent
  directories too: whoever can write `conf_d` or a source directory can rename
  the checked tree away and put another in its place.

With a umask of 002 (Fedora's default with per-user groups), a fresh clone is
group-writable. Run `chmod -R go-w` on `conf_d` once after cloning. The trust
error names the offending path and the `chmod go-w` to run, and `-R` covers the
rest. `atc src pull` keeps the modes correct from then on.

**Deploying and updating.**

- **Add a source:** `git clone <url> ~/.config/atc/conf.d[/<name>]`, then
  `chmod -R go-w ~/.config/atc/conf.d` if your umask is 002.
- **Update one:** `atc src pull` (§10.1). It checks the incoming commit before
  fast-forwarding to it.

You develop units in a separate working checkout. A change reaches atc only as a
commit that you pull into `conf_d`.

**Testing uncommitted changes.** Commands that write nothing accept
`--conf-d DIR`, for example `atc show account acme --conf-d ~/code/my-config`.
The trust rules still apply. Commands that write files or start sessions refuse
the option.

## 7. Units

### 7.1 Envelope

The envelope is the same as `claudeconf`'s, with a new version number:

```json
{
  "version": 2,
  "description": "Account isolation",
  "imports": ["common/base.json"],
  "settings": {}
}
```

Version 2 means two things:

- imports may name other sources (§7.2)
- strings in `settings` are templates (§7.3)

The envelope has no new fields. Only version 2 is accepted, and the two existing
units are migrated (§17). The rest stays exactly as in `claudeconf`:

- strict JSON parsing (no `NaN`, no duplicate keys)
- the merge rules
- each unit contributing once, at the first point the import walk reaches it
- deterministic output

### 7.2 Unit references

A unit reference is either:

- `path/to/unit.json`, a path relative to the `conf/` of the **same source**, or
- `@name/path/to/unit.json`, relative to the `conf/` of the source called
  `name`. This form only works in the multi layout.

**Imports** use these references. Path containment stays as in `claudeconf`: no
absolute paths, the `.json` extension written out, and nothing that resolves
outside its source's `conf/`.

**The registry's `unit` fields** use the same syntax. A reference without `@`
there is looked up in every source, and exactly one must contain it. If none
does, it's an error, except for a project's default unit, whose absence just
means no project layer. If several do, it's an error that asks you to add an
`@name/` prefix. With one source, or with names that don't collide, the registry
never needs a prefix.

**Asset and skill references** in the registry use the same syntax and the same
lookup, relative to a source's `assets/` or `skills/` instead of `conf/`:

- `default-agents-md` and `agents-md-file` name a file under `assets/`, such as
  `AGENTS.in.md` or `@acme/AGENTS.in.md`. A value of `agents-md-file` that
  starts with `/` or `~` is a filesystem path instead (§10.2). A reference to a
  templated asset means its copy rendered for the account (§10.2).
- each entry of `skills` names a skill directory under `skills/`, such as
  `git-commits` or `@personal/git-commits`

### 7.3 Templates

A **reference** is written `{{name.name…}}`. Each name matches
`[a-z][a-z0-9_]*`, and no whitespace is allowed inside the braces.

- **Where:** only in string values inside `settings`, at any depth.
  - Object keys are never substituted. A key that contains `{{` is an error.
  - `imports` and `description` are never substituted.
- **What a reference resolves to:** either a string or a list of strings,
  according to the variable table (§7.4). An unknown reference is an error that
  names the unit, the JSON pointer, the reference and the layer being built.
- **String references** are replaced in place, anywhere in the string.
- **List references** expand the array element that contains them:
  - The element is the nearest enclosing array element, which can be a string or
    an object.
  - It is emitted once per list item, in list order, with the reference replaced
    in each copy.
  - An empty list removes the element, after checking it as if it had one item,
    so a bad reference inside it is still an error.
  - An element may use only one distinct list reference.
  - A list reference with no enclosing array element is an error.
- **Malformed text:** any `{{` that doesn't start a valid reference is an error.
  There is no escape syntax; one can be added if a real need comes up.
- **No re-scanning:** substituted text is never scanned again.
- **When:** each unit is expanded as it is loaded, before merging. Variables
  come only from the registry, from atc itself and from the unit's own source,
  never from other units, so everything a unit needs is known at load time. The
  merge rules and array de-duplication therefore need no changes.

Here is the isolation rule from the sandbox-hardening `.jq` filters, written
once for every account:

```json
{
  "version": 2,
  "description": "Own tokens and every other account unreadable",
  "settings": {
    "sandbox": {
      "credentials": {
        "files": [
          { "path": "{{account.secrets}}", "mode": "deny" },
          { "path": "{{account.dir}}/cctt.token", "mode": "deny" },
          { "path": "{{peers.dir}}", "mode": "deny" },
          { "path": "{{peers.files}}", "mode": "deny" }
        ]
      }
    },
    "permissions": {
      "deny": [
        "Read(/{{peers.dir}}/**)",
        "Edit(/{{peers.dir}}/**)",
        "Read(/{{peers.files}})",
        "Edit(/{{peers.files}})"
      ]
    }
  }
}
```

Built for `acme`, this produces the same isolation entries as `acme.jq`:

- its own `.credentials.json`, `cctt.token` and `.claude.json`
- `~/.claude` and `~/.claude.json` in the credential denies
- `Read` and `Edit` rules for both of those

Built for `personal`, it produces the isolation entries of `default.jq`, as
absolute paths. Strict mode, which both filters also set, belongs in the same
hardening unit but is left out here.

The extra `/` in the permission rules matters. In a `Read` or `Edit` rule,
`/path` is relative to the settings file and only `//path` is absolute (F15).
The variables hold absolute paths, so `Read(/{{peers.dir}}/**)` expands to
`Read(//home/…/**)`. Sandbox paths, such as the credential entries above, take
plain absolute paths.

A unit points at its own source's assets the same way, wherever that source is
cloned:

```json
{
  "version": 2,
  "settings": {
    "hooks": {
      "PreToolUse": [
        {
          "matcher": "Bash",
          "hooks": [
            {
              "type": "command",
              "command": "bash {{source.assets}}/hooks/block-bad-cmd-shape.sh"
            }
          ]
        }
      ]
    },
    "statusLine": {
      "type": "command",
      "command": "bash {{source.assets}}/statusline.sh"
    }
  }
}
```

### 7.4 Variables

| Reference                                                          | Type | Value                                                                                       |
| :----------------------------------------------------------------- | :--- | :------------------------------------------------------------------------------------------ |
| `home`                                                             | str  | The user's home directory                                                                   |
| `atc.config_dir`, `atc.conf_d`, `atc.state_dir`, `atc.runtime_dir` | str  | §4                                                                                          |
| `atc.install_dir`                                                  | str  | The environment atc runs from (Python's `sys.prefix`)                                       |
| `atc.python_dir`                                                   | str  | The Python installation that environment is built on (`sys.base_prefix`, links resolved)    |
| `source.name`                                                      | str  | The name of the source that contains the unit. Empty in the single layout                   |
| `source.root`, `source.conf`, `source.assets`                      | str  | That source's directory, its `conf/` and its `assets/`                                      |
| `source.rendered`                                                  | str  | Where `atc sync` renders that source's templated assets for the account (§10.2)             |
| `sources.<name>.root`, `.conf`, `.assets`, `.rendered`             | str  | The same for any named source (multi layout)                                                |
| `vars.<name>`                                                      | str  | Registry `vars`                                                                             |
| `account.name`, `account.agent`, `account.dir`                     | str  | The account being built (account layer) or chosen (project layer)                           |
| `account.files`                                                    | list | Files the account owns outside its `dir` (from the backend, §9)                             |
| `account.secrets`                                                  | list | Credential and state files that the account's own sessions must not read (from the backend) |
| `account.vars.<name>`                                              | str  | The account's registry `vars`                                                               |
| `peers.dir`                                                        | list | `dir` of every other account, of any agent                                                  |
| `peers.files`                                                      | list | Every other account's `files`, combined into one list                                       |
| `project.name`, `project.repo`                                     | str  | Project layer only                                                                          |
| `project.vars.<name>`                                              | str  | Project layer only                                                                          |

In the account layer, `project.*` doesn't exist, so using it there is an
unknown-reference error.

## 8. Layers

| Layer   | Built from                                                     | Delivered as                       |
| :------ | :------------------------------------------------------------- | :--------------------------------- |
| Account | the account unit and its imports                               | `<account dir>/settings.json`      |
| Project | the project unit and its imports, for the chosen account       | part of the session's `--settings` |
| Session | generated by atc (§10.3), then any `--settings` given to `atc` | part of the session's `--settings` |

- **One fragment:** the project and session layers are merged into a single
  `--settings` document, in the order project, then atc's session entries, then
  your `--settings`, using the usual merge rules. It has to be one document
  because Claude Code honours only one `--settings` (F2).
- **Your own `--settings`** is parsed as strict JSON but not as a template.
- **Within Claude Code,** the fragment sits above `settings.local.json`, project
  settings and user settings (F6).

**Layer policy.** Some keys must not appear in some layers. The check runs on
every unit while a layer is being built, and on a `--settings` given to atc. A
failure names the unit and the JSON pointer.

| JSON pointer                        | Refused in       | Why                                                                                                     |
| :---------------------------------- | :--------------- | :------------------------------------------------------------------------------------------------------ |
| `/autoMemoryDirectory`              | account          | The value is the memory directory itself (F5), so in user settings every project would share one memory |
| `/sandbox/allowUnsandboxedCommands` | project, session | From `--settings` it makes the sandbox admin-required (F7)                                              |

The policy table lives in code and grows only from evidence: a fact from §2 or a
test.

**Rule warnings.** atc doesn't judge what your permission rules allow or deny.
That is configuration, and the units are yours. It does warn about rules that
can never take effect. Claude Code applies deny before ask, and ask before
allow, whichever settings file each rule comes from (F13). So:

- an `allow` rule is dead when a `deny` or an `ask` rule covers it
- an `ask` rule is dead when a `deny` rule covers it

For example, if `common/base.json` denies `Bash(git rebase:*)` and
`projects/anvil.json` allows it, every rebase in `anvil` is still refused. atc
names both units and both rules, and leaves the fix to you.

- **What is checked:**
  - the account layer on its own, in `atc sync` and `atc show account`
  - the account layer together with the project layer, in `atc show project`
  - the account layer together with the project layer, the session entries and
    your `--settings`, as Claude Code will see them, in `atc run --print`
  - every account layer and every project with each account it allows, in
    `atc src check` (and so in `src pull`)
- **What "covers" means.** Matching is deliberately conservative: it can miss a
  conflict, but it shouldn't report one that isn't there.
  - the same rule text, for any tool
  - a bare tool name, such as `Bash`, covering any rule for that tool
  - for `Bash` rules, the covering rule's pattern, with `*` as a wildcard,
    matches the other rule's text read literally. If the pattern matches the
    text with the text's `*` read as an ordinary character, it matches every
    command that text could stand for.
  - Partial overlaps are not reported. Denying `Bash(git rebase -i:*)` doesn't
    make allowing `Bash(git rebase:*)` dead.
- **Not considered:** rules in the repository's own `.claude/settings*.json`.
- **Effect:** warnings go to stderr. They never change what atc writes, and
  never change the exit status.

Claude Code reads a trailing `:*` and a trailing ` *` alike (F14), so the
matcher turns the first into the second in both rules before comparing them. A
`*` in the middle needs the text around it to be there, which the comparison
keeps: `Bash(git * push*)` doesn't cover `Bash(git push:*)`. A rule that ends in
`:*` is a literal prefix, though: a `*` before the `:*` stands for itself, so
`Bash(SQLX_* cargo build:*)` matches only commands that start with
`SQLX_* cargo build`, and covers no rule whose `*` is a wildcard.

## 9. Backends

A backend holds everything specific to one agent. Interface:

| Member                             | Claude backend                                                                                                                       |
| :--------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------- |
| `default_dir`                      | `~/.claude`                                                                                                                          |
| `settings_file(account)`           | `<dir>/settings.json`                                                                                                                |
| `instructions_file(account)`       | `<dir>/CLAUDE.md`                                                                                                                    |
| `skills_dir(account)`              | `<dir>/skills` (F11)                                                                                                                 |
| `files(account)`                   | `[~/.claude.json]` for the default directory, otherwise `[]` (F1)                                                                    |
| `secrets(account)`                 | `<dir>/.credentials.json` and the state file: `~/.claude.json` for the default directory, otherwise `<dir>/.claude.json`             |
| `environment(account)`             | Sets `CLAUDE_CONFIG_DIR=<dir>`. For the default directory it removes the variable instead, which avoids the unestablished case in §2 |
| `render(settings)` / `parse(text)` | JSON, sorted keys, two-space indent                                                                                                  |
| `argv(bin, fragment, args)`        | `[bin, "--settings", <fragment JSON>, *args]`, leaving out `--settings` when the fragment is empty                                   |
| `session_fragment(common_dir)`     | §10.3                                                                                                                                |

**Codex, when its backend is designed.** These are working assumptions to check
at that point:

- `CODEX_HOME` selects the account directory, which defaults to `~/.codex`.
- The account's login lives in `auth.json`, and its configuration in
  `config.toml`.
- The instructions file is `AGENTS.md` in that directory. This is why the
  default file in `assets/` is named for no particular agent. The default file
  is `AGENTS.in.md` and not `AGENTS.md` because agents working in the source's
  own repository load an `AGENTS.md` there as that repository's instructions.
- `install-skills.sh` links Codex skills into `~/.agents/skills`, which doesn't
  belong to any one account. If that is the only place Codex looks, per-account
  skills won't work for Codex as they do for Claude.

Its settings schema has nothing in common with Claude's. Units will therefore
need to say which agent they are for, which means a new envelope version with an
`agent` field. The way a per-session fragment is passed to Codex is still open.

The registry, config sources, projects, worktrees, the repository lock,
maintenance and usage don't depend on the agent. Only the settings, the session
fragment's contents, and where the instructions file and skills go are per
backend.

## 10. Commands

`atc` with no arguments, or with a first argument that starts with `-` (other
than `--help` and `--version`), means `atc run`. That includes `--`, so
`atc -- --resume` passes `--resume` to the agent. Any other first word must be a
known command. A typo is an error; it never starts a session.

### 10.1 `atc src`

- `atc src ls [--conf-d DIR]`: a table of the sources. For each source:
  - its name, path and layout
  - for git clones: branch, commit, whether `conf/`, `assets/` or `skills/` has
    uncommitted changes, and how far ahead of or behind its upstream it was at
    the last fetch
  - its number of units, assets and skills
- `atc src check [--conf-d DIR]`: validates everything atc would load:
  - discovery and the trust rules
  - every unit's JSON, envelope and template syntax
  - every import, across sources
  - every unit, asset and skill reference in the registry. A commit that removes
    a skill or instructions file still in use therefore never gets deployed by
    `src pull`.
  - every layer the registry defines: each account layer, and each project layer
    with each account the project allows (all accounts if it sets no
    `accounts`). This resolves every template reference, applies the layer
    policy, and reports rule warnings (§8).
  - every templated asset, rendered for each account (§10.2).

- `atc src pull [NAME...]`: updates git sources, all of them by default. For
  each one:
  1. Refuse if `conf/`, `assets/` or `skills/` has uncommitted changes: deployed
     configuration isn't edited in place.
  2. Run `git fetch`, and stop if the upstream can't be reached by fast-forward.
  3. Write the fetched commit's whole tree into a temporary directory the way
     checkout would, not with `git archive`, whose `export-ignore` and
     `export-subst` attributes could hide what the merge deploys. Then run the
     trust rules and `src check` on `conf_d` with this source replaced by that
     tree.
  4. Only if the check passes, `git merge --ff-only`. Then remove group and
     world write from the `conf/`, `assets/` and `skills/` directories and
     everything under them, skipping symbolic links, since git applies the umask
     to what the merge writes. If that fails, the source is reported as failed,
     naming the path.
  5. Show what was deployed, for review, on stderr:
     `git log --oneline old..new`, at most 20 commits and then a count of the
     rest, and `git diff --stat old..new -- conf assets skills`. The upstream
     can be the clone sessions commit in, and a commit that weakens a unit, a
     hook or a skill passes every check.

  A source that fails is left as it was, and its problems are shown. Sources
  that aren't git clones are skipped, with a note. A unit that can't be read, in
  one source, never stops the others from being updated.

Adding a source is a plain `git clone` (§6). An `atc src add` can come later if
it turns out to be useful.

### 10.2 `atc sync [ACCOUNT...] [--check] [--diff] [--take-over] [--discard-live] [--lax] [--conf-d DIR]`

For each named account (all accounts by default), `sync` brings four things in
line with the configuration:

- `settings.json`, built from the account layer
- the instructions file (`CLAUDE.md` for Claude), as a link
- the selected skills, as links (§10.10)
- the copies of templated assets, rendered for the account into
  `<state_dir>/sync/<account>/assets/`

atc works out everything for an account before it changes anything, so an error
leaves that account as it was. The account directory must already exist, because
creating an account means logging in, which atc doesn't do.

#### Settings

Three versions are involved:

- **new:** what the units produce now
- **live:** the current `settings.json`
- **last:** what atc last wrote, kept in
  `<state_dir>/sync/<account>/settings.json`

Versions are compared as canonical JSON, so if Claude Code only reformats the
file, that doesn't count as an edit. A live file that isn't valid JSON equals
nothing: it counts as never synced when there is no `last`, and as edited
outside atc otherwise. A `last` that can't be read counts as missing, so it is
never trusted.

| live    | last    | Situation                     | Action                                                                       |
| :------ | :------ | :---------------------------- | :--------------------------------------------------------------------------- |
| missing | any     | Not created yet               | Write new                                                                    |
| = new   | any     | Already current               | Record last = new. Nothing is written                                        |
| present | missing | Never synced, and differs     | Show the diff from live to new. Write only with `--take-over`                |
| = last  | present | Unchanged since the last sync | Write new                                                                    |
| ≠ last  | present | Edited outside atc            | Show the diff from last to live and stop. `--discard-live` writes new anyway |

- **To keep an outside edit:** move it into a unit, commit, run `atc src pull`,
  and run `atc sync` again. Live then equals new, so the second row applies.
- **Every write:**
  - Backs the live file up to
    `<state_dir>/sync/<account>/backups/<UTC timestamp>-settings.json`, keeping
    the last ten.
  - Replaces the file atomically (a temporary file in the same directory, then a
    rename) and keeps its permissions. When `settings.json` is a symbolic link,
    the file it leads to is replaced, from a temporary file in its own
    directory, and the link stays. That file must be as far out of sessions'
    reach as `config_dir`, and `atc check` verifies it (§10.7).

#### Instructions file

The account's instructions file links to:

1. `agents-md-file`, if it is set: a filesystem path, or an asset reference
   (§7.2).
2. Otherwise, if `use-default-agents-md` is `true`: the asset that
   `default-agents-md` names.
3. Otherwise nothing. atc creates nothing, and leaves alone any file it didn't
   make. A link it made earlier is removed (third row below).

An asset that is a template is linked as its rendered copy (below).

#### Links

The instructions file and the skills are symbolic links with absolute targets.
`<state_dir>/sync/<account>/links.json` records each link atc made and its
target. That record tells atc's own links apart from everything else, the way
`last` does for settings.

| Live entry                                      | Situation                                | Action                                                                                                                                                                |
| :---------------------------------------------- | :--------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Missing                                         | Not created yet                          | Create the link, if one is wanted                                                                                                                                     |
| A link to the wanted target                     | Current                                  | Record it. Nothing is written                                                                                                                                         |
| A link to the recorded target, no longer wanted | Configuration changed, or a source moved | Re-point it, or remove it if no link is wanted there any more                                                                                                         |
| Anything else                                   | Not made by atc                          | Leave it. If a link is wanted there, report a conflict. `--take-over` replaces a link, or backs a regular file up and then replaces it. A directory is never replaced |

- **Entries nobody asked for:** entries in the skills directory that are neither
  selected nor recorded are never touched. That covers Claude Code's own entries
  (F12) and skills installed by hand.
- **A linked skills directory:** a skills directory that is itself a symbolic
  link is an error for that account. atc doesn't follow it, so it never makes
  links wherever it leads.
- **No dangling links:** a wanted target must exist, otherwise it is a
  configuration error for that account.
- **Backups** of replaced regular files go to the same `backups/` directory as
  settings, named `<UTC timestamp>-<file name>`. The last ten of each file name
  are kept.

#### Templated assets

A file under a source's `assets/` named `<stem>.in.<ext>` or `<stem>.in` is a
template, such as `bin/clean-scratch-targets.in.sh`. It holds what differs
between accounts or machines, such as a scratch directory, so that the source
doesn't have to name it.

- **Rendering:** for each account, the template's references are replaced as in
  a unit's settings (§7.3), with the account layer's variables and those of the
  template's own source, never a project's. Only string references can be used,
  since a file has no array element to expand. A reference error names the
  template, the account and the line. A template that isn't UTF-8 text, and two
  templates whose copies need one path (`a.in.sh` and `a.sh.in`, or `bin.in` and
  `bin/x.in.sh`), are errors too. Each stops that account.
- **No escape:** as in units (§7.3), a `{{` that doesn't start a reference is an
  error. A shell script that needs a literal `{{` writes `'{''{'`.
- **The copy:** it goes to `<state_dir>/sync/<account>/assets/`, plus the
  source's name in the multi layout, at the template's path under `assets/` with
  `.in` dropped: `bin/clean-scratch-targets.sh`. It is executable by you if the
  template is executable by anyone, and private to you either way.
- **Reaching it:** units name it with `{{source.rendered}}` (§7.4). An
  instructions file that is a template is linked as its copy.
- **Why a copy, not a link:** a script whose safety rests on a fixed path, like
  the scratch cleaner's root, keeps that property: the path is fixed when the
  copy is made, and sessions can't write `state_dir` (§13) to change it.
- **Writes:** a copy is written, in one step, when its text or mode differs from
  the rendering. A copy whose template is gone is removed, along with
  directories left empty, after the links: a renamed instructions template keeps
  its old copy until the link has moved. Nothing else writes there, so there is
  nothing to compare against a `last`. A changed template reaches an account at
  its next `sync`, as a changed unit does.
- **No links followed:** a symbolic link where a copy goes is replaced, and a
  directory of copies that is a link is an error, as for skills.

#### Options

- **`--diff`** writes nothing. It shows the settings diff, each link change and
  each change to a rendered copy that a plain `atc sync` would make.
- **`--take-over`** writes settings that were never synced but differ, and
  replaces link entries that atc didn't make.
- **`--discard-live`** applies to settings only.
- **`--check`** writes nothing, and exits 3 if anything would be written, was
  edited outside atc, or is in conflict. With `--conf-d`, only `--check` and
  `--diff` are allowed.
- **Where it runs:** Claude Code write-protects `settings.json` against
  sandboxed commands, so `sync` works only from your own terminal, which is
  intended.

### 10.3 `atc [run] [-a ACCOUNT] [--settings FILE|JSON] [--no-guard] [--print] [-q] [--lax] [-- AGENT-ARGS...]`

1. **Load** the registry and the config sources.
2. **Find the repository:** run
   `git rev-parse --path-format=absolute --git-common-dir` in the current
   directory. Outside a repository, the result is empty.
3. **Find the project:** the registry project whose `repo` resolves to the same
   shared `.git`. Every worktree of a project therefore matches it, while an
   unregistered repository has no project.
4. **Pick the account:** `-a` first, then the project's `account`, then
   `default-account`.
   - If the project lists `accounts`, the chosen account must be one of them.
   - The chosen account's agent decides the backend.
5. **Build the fragment** (§8):
   - the project layer, if there is a project
   - the session entries, if inside a repository and without `--no-guard`
   - your own `--settings`
6. **Set the environment** (§11).
7. **When guarded:**
   - Add the shared `.git` to `<state_dir>/repos`.
   - Open `<runtime_dir>/<key>.lock` (`key` is the first 16 hex digits of the
     SHA-256 of the shared `.git`'s path).
   - Take a shared `flock`, waiting up to 60 seconds. If that fails, warn and
     continue without it.
   - Mark the descriptor inheritable, so the lock lasts until the session's last
     process exits.
8. **Snapshot:** if the project has `snapshot`, run
   `snapper -c <config> create --cleanup-algorithm number --description "atc: <cwd>"`.
   If it fails, warn and continue.
9. **Announce and exec.**
   - Print one line to stderr naming the project, account and guard state. `-q`
     turns it off.
   - Replace atc with the agent, using `argv` (§9).
   - The agent binary comes from `agents.<agent>.bin`, otherwise from `PATH`.
   - atc refuses to run if that binary resolves to atc itself, which can happen
     when `claude` is aliased or linked to `atc`.

Agent arguments go after `--`. An option atc doesn't know, given before `--`, is
an error rather than being passed through. A `--settings` or `--settings=…`
among the agent arguments is refused: Claude Code honours only the last
`--settings` (F2), so it would silently replace atc's fragment and drop the
guard.

**Session entries (Claude).** These come from `claude-guard`. Each of
`objects/pack` and `packed-refs` in the shared `.git` is protected, but only if
it already exists: the sandbox would put a read-only placeholder in place of a
missing path. Each protected path gets:

- a `sandbox.filesystem.denyWrite` entry for the path
- a `permissions.deny` rule: `Edit(/<path>/**)` for a directory, or
  `Edit(/<path>)` for a file. The leading `/` plus the absolute path gives
  Claude's `//` absolute-path form.

**`--print`** prints the plan and exits 0, without taking a lock, registering
the repository or taking a snapshot. The plan covers the repository, project,
account, environment changes, fragment and argv. It is the only form of `run`
that accepts `--conf-d`.

**`--no-guard`** leaves out the session entries, the gc override, the lock and
the registration, and prints a warning.

### 10.4 `atc show account NAME` / `atc show project NAME [-a ACCOUNT]` [`--conf-d DIR`] [`--lax`]

Prints one merged, expanded layer to stdout. Diagnostics go to stderr. This
replaces `claudeconf build`. `--lax` turns unknown settings keys into warnings,
here and in `sync` and `run`.

### 10.5 `atc prj`

- `atc prj ls`: name, repository, default account and worktrees directory of
  every project.
- `atc prj show [NAME]`: one project, including its worktrees (from git). `NAME`
  defaults to the current project.
- `atc prj add NAME [REPO] [--worktrees DIR] [--from BRANCH] [-a ACCOUNT]...`:
  adds a project to `machine.yaml`.
  - Each `-a ACCOUNT` goes into `accounts`, always written as a list, and the
    first also into `account`. Without `-a`, every account is allowed.
  - `REPO` defaults to the current directory. What is stored is the repository's
    main worktree, found from `git rev-parse --git-common-dir`, whichever
    worktree `REPO` is in: the common directory's parent, or the common
    directory itself for a bare repository. Removing a linked worktree then
    never leaves the project without its repository. When the common directory's
    parent isn't a checkout of the repository, as for a submodule, the top level
    of `REPO` is stored.
  - The repository and branch are checked first.
  - If no source contains `projects/<NAME>.json`, atc warns.
- `atc prj rm NAME`: removes the registry entry, once the user confirms it
  (yes/No, no by default). Nothing else is touched.

### 10.6 `atc wt`

Git is the only record of worktrees. Unlike `ccwtctl`, atc stores no list of
them.

- `atc wt new BRANCH [--from REF] [--run]`: creates `<worktrees>/<slug>` in the
  current project.
  - **Slug:** `ccwtctl`'s rule. Lowercase; spaces become `-`; any character
    outside `[a-z0-9_.-]` becomes `--`. For example, `feature/x` becomes
    `feature--x`.
  - **If the branch exists:** `git worktree add <path> BRANCH`.
  - **Otherwise:** `git worktree add -b BRANCH <path> <REF or branch-from>`.
  - **`--run`:** changes into the new worktree and continues as `atc run`.
- `atc wt ls`: the project's worktrees from `git worktree list --porcelain`,
  with their branches.
- `atc wt rm NAME [--force]`: `git worktree remove`. Without `--force` a removal
  that would lose work is refused: git refuses a dirty worktree, and atc one
  whose `HEAD` is detached at a commit no branch contains, or where a rebase,
  merge, cherry-pick, revert or bisect is in progress. Ignored files are not
  refused, since build output would make that refuse almost always; the help
  says plainly that they are deleted. The project's own `repo` is never removed.
  The branch is never deleted.

Worktree directories should sit outside every sandbox `allowWrite` entry, so a
session can't remove its own worktree. `atc check` tests this (§10.7).

### 10.7 `atc check`

Run inside a session, through the agent's shell tool. It only inspects and never
writes. If the home directory is writable, it isn't inside a sandbox: it says so
and exits 2. These checks come from `claude-guard-check`:

- **Repository:**
  - `objects/pack` and `packed-refs` are read-only, as are `hooks` and `config`
    when they exist.
  - When `extensions.worktreeConfig` is on, the shared `config.worktree` and the
    current worktree's own are read-only too: git reads them, and `atc maint`
    runs git outside the sandbox, where a setting such as `gc.recentObjectsHook`
    would run a command. When it is off, git doesn't read them and nothing is
    printed for them.
  - `objects` is writable.
  - `gc.auto` is `0`.
  - The worktree's parent directory is read-only.
  - The `objects/pack`, `packed-refs` and `gc.auto` checks are the guard's, so
    they are made only in a session `atc run` guarded, which it says in
    `ATC_GUARD` (§11). Otherwise a note says the session was not started by
    `atc run`, or was started unguarded, and they are skipped. When `ATC_GUARD`
    names another repository, they are made for this one, with a note naming the
    guarded one.
- **Accounts:**
  - The current account's `secrets` are unreadable. The current account comes
    from `ATC_ACCOUNT`, otherwise from the backend's environment.
  - Every peer's `dir` and `files` are unreadable.
- **atc itself:** `config_dir` (and so `conf_d`), `state_dir`, `runtime_dir`,
  `install_dir` and `python_dir` are not writable, and neither is
  `machine.yaml`, checked where it leads when it is a link.
- **Settings, instructions and skills:** the account's `settings.json`, checked
  where it leads when it is a link, is not writable. Nor are the account's
  instructions file and every entry in its skills directory, or the target when
  it is a link, nor each skill's `SKILL.md`. This matters for an
  `agents-md-file` outside `conf_d`, which the trust rules don't cover, and for
  links that atc didn't make.

A check that can't run is never a `PASS`. Outside a repository the repository
checks are skipped with a plain note; a git failure other than "not a git
repository" is a `FAIL`, and so is a skills directory that can't be listed. A
path that is missing, or a link that leads nowhere, is checked for whether a
session could create it: it passes only when the nearest existing directory
above it isn't writable.

It prints one `PASS` or `FAIL` line per check, and exits 0 when everything
passes, 3 otherwise.

### 10.8 `atc maint`

This is `claude-guard --guard-maint`. For each repository in `<state_dir>/repos`
that still exists:

1. Run `git repack --geometric=2 -d -q`. Geometric repacking only merges packs
   and never drops an object, so it is safe alongside sessions.
2. Only if the exclusive lock is free without waiting (no session is using the
   repository):
   - run `git pack-refs --all`
   - run `git gc` if its stamp is older than `maint.gc-hours`, then renew the
     stamp. gc rewrites `packed-refs` too, so it needs the lock as well.

`contrib/systemd/atc-maint.{service,timer}` run it every 10 minutes. They are
installed by hand into `~/.config/systemd/user/`.

### 10.9 `atc usage`

**Recording.** atc defines the file. The statusline writes it, because the
statusline already receives the data (F8) and runs often enough. On each update,
when both `ATC_ACCOUNT` and a `rate_limits` object are present, the statusline
atomically writes `$ATC_STATE_DIR/usage/$ATC_ACCOUNT.json`:

```json
{ "recorded_at": "2026-10-23T09:00:00Z", "rate_limits": {} }
```

`rate_limits` is copied verbatim, and the statusline uses `jq` (no Python on
every refresh). Sessions not started by atc record nothing.

**`atc usage`** prints each account's five-hour usage and the age of its record,
or "no data".

**Second phase: `-a auto`.**

1. The candidates are the project's `accounts`. Outside a project, or in a
   project that lists no `accounts`, there is no choice to make, so `auto` acts
   as if `-a` weren't given (§10.3 step 4).
2. Each candidate scores its `five_hour.used_percentage` if recorded within the
   last five hours, otherwise 0, since its window has rolled over.
3. The lowest score wins; on a tie, the candidate listed first wins.
4. The choice and the reason are printed to stderr.

The known blind spot: usage from other machines or from claude.ai never gets
recorded here.

### 10.10 `atc skills`

A **skill** is a directory that sits directly under a source's `skills/` and
contains a `SKILL.md`.

- **Name:** the directory's name. It must match `[a-z0-9][a-z0-9_-]*`; otherwise
  the directory gets a warning and is skipped.
- **Description:** read from `SKILL.md`'s YAML front matter, for display only.
  atc warns when the front matter's `name` differs from the directory name.
- **Selection:** each account's `skills` in the registry, a list of skill
  references (§7.2). Each account's skills are chosen separately.
- **Linking:** `atc sync` links each selected skill to `<skills dir>/<name>`
  (§10.2). Two selected skills with the same name, from different sources, are
  an error.

Commands:

- `atc skills ls [ACCOUNT...]`: one row per skill found in the sources, with its
  name, source and description. There is one column per account, showing whether
  the skill is linked, selected but not linked yet, in conflict, or not
  selected. Below the table, it lists the entries in each account's skills
  directory that atc doesn't manage, so skills installed by hand stay visible.
- `atc skills pick ACCOUNT [--take-over]`: a checklist of every skill, with the
  account's current selection ticked.
  - **Confirming** writes the new selection to `machine.yaml`. Then it links and
    unlinks that account's skills, as `atc sync` would, without touching its
    settings or instructions file.
  - **Cancelling** (Ctrl-C) changes nothing and exits 130.
  - **Without a terminal** on both stdin and stdout, it exits 2 and points at
    `add` and `rm`.
- `atc skills add ACCOUNT SKILL... [--take-over]` and
  `atc skills rm ACCOUNT SKILL...`: the same changes without the picker, for
  scripts.

**The picker** is `questionary`'s checkbox prompt, built on prompt_toolkit. Each
line shows the name, the source and the start of the description.

**Plugins and marketplaces** may later get their own commands on the same
pattern: chosen per account, recorded in the registry, applied by `sync`.

### 10.11 `atc vars`

- `atc vars ls`: every variable the registry defines or a unit needs, with its
  owner (the machine, an account or a project), its value or `undefined`, and
  what uses it.
- `atc vars set [NAME [VALUE]] [-a ACCOUNT | -p PROJECT]`: defines one at the
  level its reference names, or asks for each undefined one in turn.

The details are in
[004-project-and-variable-commands-design.md](004-project-and-variable-commands-design.md).

## 11. Environment passed to the agent

| Variable            | Value                                                                                       | Used by                                      |
| :------------------ | :------------------------------------------------------------------------------------------ | :------------------------------------------- |
| `ATC_ACCOUNT`       | The chosen account's name                                                                   | the statusline (display, usage), `atc check` |
| `ATC_PROJECT`       | The project's name. Removed outside a registered project                                    | the statusline, hooks                        |
| `ATC_STATE_DIR`     | `state_dir`                                                                                 | the statusline's usage recording             |
| `ATC_GUARD`         | When guarded: the git common directory of the guarded repository                            | `atc check`                                  |
| `CLAUDE_CONFIG_DIR` | Set, or removed, by the backend (§9)                                                        | Claude Code                                  |
| `GIT_CONFIG_*`      | When guarded: `gc.auto=0` and `maintenance.auto=false`, added after any entries already set | git                                          |

`ATC_*` variables already in atc's own environment are always replaced or
removed, so a session started from inside another session never inherits the
outer one's values.

**The statusline** inherits these variables (F10). It shows `ATC_ACCOUNT` when
it is set. It falls back to telling the accounts apart by `CLAUDE_CONFIG_DIR`,
as it does today, and marks those sessions so a session that bypassed atc is
easy to spot:

```bash
account_used="${ATC_ACCOUNT:-}"
account_direct=0
if [[ -z "${account_used}" ]]; then
  account_direct=1 # not started through atc
  case "${CLAUDE_CONFIG_DIR:-${HOME}/.claude}" in
    "${HOME}/.config/acme/claude") account_used="acme" ;;
    "${HOME}/.claude") account_used="personal" ;;
  esac
fi
```

The fallback keeps machine-specific paths in the statusline, which is a personal
asset (§3). It can go once atc is the only way sessions start.

## 12. Command line and output

**Parsing: `click`.**

- **Default command:** a small `click.Group` subclass inserts `run` when no
  command word is given (§10). An unknown word gives click's usual "no such
  command" error.
- **Agent arguments:** `run` collects everything after `--` into an `nargs=-1`
  argument. Before `--`, unknown options are errors.
- **Help:** `rich-click` renders it, with colour and grouped options.
- **Typing:** click passes typed values straight to the command functions, so
  the argparse `_raw` boundary goes away (§15).

**Output: `rich`.** There are two consoles, one on stdout and one on stderr.

- **Data on stdout.** `atc show` writes only JSON to stdout:
  - syntax-highlighted when stdout is a terminal
  - otherwise exactly the canonical rendering, so `atc show account x > f` gives
    the same bytes `sync` would write
- **Everything else is for people:**

  | Output                               | Form                                                                       |
  | :----------------------------------- | :------------------------------------------------------------------------- |
  | `src ls`, `prj ls`, `wt ls`, `usage` | Tables. Usage is yellow below 70 % and red from 70 %, as in the statusline |
  | `skills ls`                          | A table with one column per account, plus the unmanaged entries, dimmed    |
  | `skills pick`                        | An interactive checklist (questionary), then the link changes              |
  | `sync`                               | One status line per account, plus coloured unified diffs and link changes  |
  | `check`                              | `PASS` in green, `FAIL` in red, one line per check                         |
  | `run --print`                        | A panel with the plan as a tree and the fragment as highlighted JSON       |
  | `run`                                | The one-line announcement on stderr, then nothing more from atc            |
  | Diagnostics (stderr)                 | `error:` in red, `warning:` in yellow, with the file and JSON pointer      |

- **Colour policy:** rich's detection decides. There is no colour when the
  output isn't a terminal, and `NO_COLOR`, `FORCE_COLOR` and `TERM=dumb` are
  honoured. `atc check`, which an agent runs through its shell tool, therefore
  prints plain text.
- **Markup safety:** any text from outside atc (paths, branch names, git output)
  is escaped before rich renders it. Otherwise a path containing `[red]` would
  be read as markup.

## 13. Security

1. **atc reads configuration only from `conf_d`.** Sessions can't write there.
   You develop in a separate checkout, and a change reaches atc only as a commit
   you pull (§6). Symbolic links are refused, so `conf_d` can't stand in for a
   working checkout. `atc src pull` checks a commit before deploying it.
2. **Sessions can't touch atc's directories.** None of them is in any sandbox
   `allowWrite`. The hardening unit also adds explicit denies, in case a broader
   `allowWrite` is added later: `sandbox.filesystem.denyWrite` on
   `{{atc.config_dir}}`, `{{atc.state_dir}}` and `{{atc.runtime_dir}}`, plus
   matching rules written `Edit(/{{atc.config_dir}}/**)`, which expand to `//`
   absolute paths (F15), for Claude's file tools, which follow permission rules
   rather than the sandbox. `{{atc.install_dir}}` gets the same, because it
   holds atc's own code: the personal account's sandbox allows writes to uv's
   tool directory, where `uv tool install` puts it. So does
   `{{atc.python_dir}}`, the interpreter that environment is built on, which for
   uv is a managed one under `~/.local/share/uv/python`, also writable in that
   sandbox: a `sitecustomize.py` or an edited stdlib module there would run in
   every later `atc`, outside the sandbox. Outside a virtual environment the two
   are the same path, and the duplicate entries are harmless. atc is installed
   with `UV_LINK_MODE=copy`, so its files are not hard links into uv's cache,
   which sessions can write.
3. **The lock and the repository list are read-only to sessions.** A session
   can't edit the list that maintenance works from, or take the lock file away.
   It can release its own lock, though: it inherits the descriptor that holds
   it, and `flock -u` on that descriptor drops the lock, after which maintenance
   may pack under it. The lock keeps maintenance away from a session that leaves
   it alone; it doesn't defend against one that sets out to release it.
4. **Hooks and the statusline run outside the sandbox.** They run only from a
   source's `assets/`, under the trust rules in §6: no links, owned by you, not
   group- or world-writable, or from the copies `atc sync` renders from there
   into `state_dir`. This replaces today's statusline symlink into the checkout.
5. **The fragment is visible in the process list.** It is passed on the command
   line, so units must never carry secrets, including in `env`.
6. **Starting `claude` directly skips the project and session layers.** The
   account layer's protections still apply. Mitigations:
   - `alias claude='atc --'`. The `--` hands every argument to the agent, so
     `claude --resume` and `claude "prompt"` keep working; atc's own options go
     through `atc` itself.
   - The statusline marks sessions without `ATC_ACCOUNT` (§11).
   - Leave the protective entries that are already in `settings.local.json` in
     place until atc is the only way sessions start (§17, step 10).
7. **Forged usage files could only steer the choice of account.** Sessions can't
   write `state_dir`, and choosing an account grants nothing beyond what that
   account already has.
8. **Instructions and skills come from deployed clones.** Today both accounts'
   skills link into the working checkout, so a session that edits it changes
   what the next session is told (F11). atc links into `conf_d` instead, under
   the trust rules, or to a copy rendered from it into `state_dir`. An
   `agents-md-file` outside `conf_d` is allowed, because you chose it, and
   `atc check` fails if a session can write to it.

## 14. Errors and exit codes

| Code | Meaning                                                                                                                    |
| :--- | :------------------------------------------------------------------------------------------------------------------------- |
| 0    | Success                                                                                                                    |
| 1    | Configuration error: registry, source discovery or trust rules, unit, template, layer policy, merge, git or snapshot setup |
| 2    | Usage error, or `atc check` run outside a sandbox                                                                          |
| 3    | `check` found a failure, or `sync` left changes, outside edits or link conflicts unwritten (with `--check`, any change)    |
| 130  | Interrupted with Ctrl-C, or the skills picker was cancelled; nothing was changed by the interrupted step                   |

All diagnostics go to stderr and name the file and the JSON pointer where that
makes sense. A successful `run` replaces itself with the agent, so from then on
the exit status is the agent's.

## 15. Implementation

The implementation plan (`002-plan.md`) fixes the exact names and signatures.
This table is the map.

| Module                                       | Contents                                                                                                                                                  |
| :------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `jsonval`, `merge`, `settings_keys`          | Carried over. `jsonval` gains parsing from text                                                                                                           |
| `resolve`                                    | Carried over. It walks across sources and gains a per-unit transform hook for templates, layer policy and rule origins                                    |
| `unit`                                       | Envelope version 2                                                                                                                                        |
| `paths`                                      | The four directories (§4)                                                                                                                                 |
| `sources`, `refs`                            | Discovery, layouts and trust rules; unit, asset and skill references                                                                                      |
| `validate`, `pull`                           | `src check` and `src pull`. Together they replace `install`                                                                                               |
| `yamlval`, `registry`                        | The YAML boundary (load, round-trip edits, `SKILL.md` front matter); pydantic models and consistency checks                                               |
| `context`, `variables`, `template`           | Everything a layer build needs; the template variables (§7.4); reference parsing, substitution and list expansion                                         |
| `layers`, `rules`                            | Layer builders and the policy table; rule warnings (§8)                                                                                                   |
| `backends/`                                  | The backend protocol and `claude.py`                                                                                                                      |
| `sync`, `links`, `skills`, `state`, `fsutil` | Deciding (`decide_*`, no side effects) and applying (`apply_*`) settings and links; skill discovery; sync state; atomic writes and backups                |
| `launch`, `guard`                            | `plan_launch()` builds a `LaunchPlan` with no side effects, `execute()` does the lock, snapshot and exec; session entries, lock, repository list, `maint` |
| `worktree`, `check`, `usage`, `git`          | As named. `git` is a thin subprocess wrapper                                                                                                              |
| `cli/`, `ui`, `exitcodes`                    | click commands; rich consoles, tables, diffs and diagnostics, and the questionary picker, kept apart so tests can stub it                                 |

**Typing.** The project's `CLAUDE.md` allows only two untyped boundaries: the
`json.loads` boundary and argparse. Moving to click removes the argparse one.
YAML loading adds a new one, and this is its justification: ruamel returns
untyped mappings and sequences. `yamlval` contains them in one function that
converts to `JsonValue` and rejects anything else, under the same discipline as
`jsonval`. That still leaves two boundaries for parsed data.

The picker adds a third, small one. questionary's `unsafe_ask()` (not `ask()`,
which prints to stdout on Ctrl-C) returns an untyped value. `ui` narrows it on
the spot to `list[str] | None` (`None` meaning cancelled), and nothing untyped
leaves that function. The project's `CLAUDE.md` gets a line naming it.

**Lock across exec.** `fcntl.flock` on a descriptor marked with
`os.set_inheritable(fd, True)`, followed by `os.execvpe`. The lock outlives atc
in the agent and everything the agent starts.

## 16. Testing

Test first for every rule below, as the project's `CLAUDE.md` already requires
for merge and resolution changes. atc's tests use fixture sources only. Checking
the real configuration is a job for that configuration's repository (see below).

- **Sources:**
  - single and multi layouts
  - sources at both levels is an error
  - nothing deeper than one level is read
  - hidden entries are ignored, and a non-source directory gets a warning
  - name rules
  - a link anywhere that is checked is refused, and so is a group- or
    world-writable file
  - `src pull`:
    - an invalid upstream commit is not deployed
    - uncommitted changes and a non-fast-forward are refused
    - non-git sources are skipped
  - `--conf-d` is refused by commands that write
- **Unit references:**
  - a reference without `@` stays in its own source
  - `@name/` crosses into another source
  - `@name/` is an error in the single layout
  - registry lookup: exactly one match is required, an ambiguous match is an
    error, and a missing default project unit is not
- **Templates:**
  - scalar substitution
  - list expansion in a string element and in an object element
  - an empty list removes the element
  - two distinct list references in one element is an error
  - a list reference outside an array is an error
  - an unknown reference is an error naming the pointer
  - a malformed `{{` is an error
  - keys are not substituted
  - values are not re-scanned
  - `source.*` points at the unit's own source
- **Layers:**
  - each policy refusal, including in a `--settings` given to atc
  - fragment order: project, then session entries, then your `--settings`
- **Rule warnings:**
  - the same text in `deny` and `allow`, and in `deny` and `ask`
  - an `allow` covered by an `ask`
  - a `Bash` pattern covering a narrower rule, such as `Bash(git *)` over
    `Bash(git rebase:*)`, across layers, naming both units
  - partial overlaps are not reported
  - warnings never change the exit status or the output written
- **Links:**
  - every row of the links table in §10.2, for the instructions file and for
    skills
  - `--take-over` replacing a link, and backing a regular file up before
    replacing it
  - a directory is never replaced, even with `--take-over`
  - unselected, unrecorded entries are left alone (`synced/`, `.trash/`)
  - a missing target is an error, and nothing changes for that account
  - the instructions file: `agents-md-file` (a path, and an asset reference)
    over `use-default-agents-md`, and nothing when neither is set
- **Skills:**
  - discovery: direct subdirectories with `SKILL.md` only, the name rule, and
    the front-matter name mismatch warning
  - references: an ambiguous name needs `@name/`, and two selected skills with
    the same name are an error
  - selection edits keep `machine.yaml`'s comments; the picker is stubbed,
    including cancelling, and refused without a terminal
- **Registry:**
  - duplicate keys are rejected
  - `no` stays a string
  - an unquoted timestamp is rejected
  - every consistency check
  - `~` expansion
  - a round-trip edit keeps comments
- **Claude backend:** environment and secrets for the default directory and for
  any other directory.
- **Sync:** every row of the settings table in §10.2, reformatting not counting
  as an edit, backup rotation, atomic replacement, and an error in one part
  leaving the whole account unchanged.
- **Launch planning:**
  - account precedence and eligibility
  - outside git, in an unregistered repository, with `--no-guard`
  - session entries only for paths that exist
  - `GIT_CONFIG_*` entries added after ones already set
  - `ATC_*` values from the environment replaced or removed
  - refusing an agent binary that resolves to atc
- **Guard:**
  - maintenance with and without a session holding the lock, using temporary
    repositories (the cases the hardening README lists as tested)
  - the lock surviving exec into a child process
- **Worktrees:** slugs, new with an existing or a missing branch, removal
  refused for a dirty worktree.
- **Command line:**
  - with click's `CliRunner`: the default command, a typo not starting a
    session, unknown options before `--`, and arguments after `--`
  - `show` writing plain JSON when its output isn't a terminal
  - outside text being escaped before rich renders it
- **`atc check`:** its decisions are unit-tested with stubbed probes. The real
  proof is running it inside a live session (§17, step 5).

**The real configuration** is checked with `atc src check --conf-d <checkout>`,
and `atc sync --check --conf-d <checkout>` against the reviewed account
settings, from the config repository rather than from atc's tests.

## 17. Migration

Each step leaves a working setup. The steps ran in the config repository, before
atc moved out of its `atc/` (§3).

1. **Split and rename.**
   - `git mv repo.conf atc`, then recreate the virtualenv.
   - Rename the package `claudeconf` to `atc`, and switch the command line to
     click and rich.
   - Move `repo.conf/conf.d/*` to `conf/` at the repository root, and create
     `assets/` there.
   - Move the two existing units to version 2.
   - Update `README.md` and `CLAUDE.md`.
2. **Core:** registry, sources, templates, layer policy, `src`, `show`.
3. **Account layer.**
   - Turn both live `settings.json` files, `default.jq` and `acme.jq` into
     units: `conf/common/base.json`, `conf/common/hardening.json`,
     `conf/accounts/personal.json` and `conf/accounts/acme.json`.
   - Keep each account's rules as they are. Reconciling where they differ is a
     configuration choice for later, not atc's, and rule warnings (§8) point out
     any allow that a deny makes dead. Fix the `allowedDomanins` typo.
   - Merge the two hook versions into `assets/hooks/`.
   - Move the statusline and `clean-scratch-targets.sh` into `assets/`.
   - Commit, then clone the config repository into `~/.config/atc/conf.d`
     (single layout).
   - Review `atc sync --diff`, then run `atc sync --take-over`.
4. **Instructions and skills.**
   - `links`, `skills`, and the link half of `sync`.
   - Copy `~/.claude/CLAUDE.md` into `assets/AGENTS.in.md`, commit and pull it,
     and set `use-default-agents-md: true` on both accounts. As a template it
     must hold no `{{` other than a reference (§10.2). `atc sync --take-over`
     backs up the personal account's file and replaces it with the link. The
     Acme account has none, so it gets the link directly. Start a session in
     each account to settle [open question 3](#19-open-questions).
   - Pick each account's skills with `atc skills pick`. Both accounts' links
     point into the working checkout today, so `--take-over` re-points them into
     `conf_d`. The Acme account's `phase-review/` and `_old/` are directories,
     so atc leaves them alone.
   - Retire `install-skills.sh`.
5. **Launch.**
   - `run` and the session layer (porting `claude-guard`), then `check` (porting
     `claude-guard-check`).
   - `conf/projects/anvil.json`, built from the deliberate entries in
     `anvil.git`'s `settings.local.json` plus `anvil.jq`.
   - The statusline reads `ATC_ACCOUNT` (§11).
   - Before relying on the guard, run `atc check` in a real session to settle
     [open question 1](#19-open-questions).
   - Replace the `acme-claude` and `claude-guard` aliases with
     `alias claude='atc --'` (§13 item 6).
6. **Maintenance:** `maint` and the systemd units. Install the units by hand,
   and disable `claude-guard`'s maintenance timer at the same time, so only one
   of the two packs the repositories.
7. **Projects and worktrees:** `prj` and `wt`. Move `ccwtctl`'s
   `~/.config/ccwt/projects/*.yaml` into `machine.yaml` once, by hand.
8. **Usage:** recording in the statusline, `atc usage`, then `-a auto`.
9. **Shared memory:** add `autoMemoryDirectory` to `conf/projects/anvil.json`,
   and merge the two memories once.
10. **Cleanup,** once atc is the only way sessions start:
    - remove the duplicated protective entries from `settings.local.json`
    - uninstall `claude-guard`
    - retire `ccwtctl`
    - drop the statusline's fallback

If Acme configuration later gets its own repository, give it `conf/`, `assets/`
or `skills/` as needed, clone it into `conf.d/acme`, move the config
repository's clone to `conf.d/personal`, and add `@personal/` to the imports and
registry references that become ambiguous or cross over.

## 18. Rejected alternatives

| Alternative                                              | Why not                                                                                                                                       |
| :------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------- |
| Bundle units and assets with the tool                    | Your configuration would have left with atc when it moved to its own repository, and configuration couldn't be split across repositories      |
| atc's config file lists arbitrary source paths           | Pointing it at a working checkout is the easy mistake, and then an agent editing that checkout changes the rules for the next session         |
| One flat namespace across sources, later ones overriding | Shadowing would be silent, and the outcome would depend on directory names sorting a particular way                                           |
| Copy sources into an install directory (`atc install`)   | A git clone already is a copy. `git pull`, checked by `src pull`, is the update, and git history is the rollback                              |
| Searching deeper than one level                          | No layout needs it yet, and a fixed depth keeps discovery predictable                                                                         |
| Generate `settings.local.json` for projects              | It is untracked and belongs to one checkout, so new worktrees lack it; and Claude Code writes its own approvals into it                       |
| Patch live settings in place, like the `.jq` filters     | It can't remove anything, and it can't tell a deliberate setting from leftovers                                                               |
| One shared config directory for both accounts            | It would mix credentials, history and per-project memory, and it can't run both accounts at once                                              |
| `{name}` template syntax                                 | It collides with shell `${VAR}` expansions and `{a,b}` brace globs, which hook commands and rules can legitimately contain                    |
| Variables defined in units                               | Machine-specific values would creep back into the repository, and templates could no longer be expanded per unit before merging               |
| Keep separate tools (alias, `claude-guard`, `ccwtctl`)   | The guard owns the only `--settings` (F2), so project settings and the guard have to be built by one program                                  |
| `managed-settings.d` for the shared settings             | It needs root, lives outside the repository, would sit next to the Acme organisation's remote policy, and can't be overridden per account     |
| argparse                                                 | click gives typed parameters, nested commands and a default command with less code, and `rich-click` gives coloured help for free             |
| atc settles conflicting rules between units              | What is allowed or denied is your configuration. atc warns about rules that can never take effect (§8) and leaves the decision to you         |
| Copy the instructions file and skills into account dirs  | Copies drift. Links follow `src pull` with nothing to re-run, and Claude Code already loads linked skills (F11)                               |
| Keep skill selections in a config source                 | Accounts are defined per machine, so what each account uses is chosen next to it. A shared default set can come later if picking gets tedious |
| Skills under `assets/skills/`                            | Skills are already at the config repository's root, and a top-level `skills/` keeps a source's layout self-describing                         |

## 19. Open questions

None of these needs a decision. Each is a fact to test during implementation. If
a result changes the design, the design is updated before the affected part is
relied on.

1. **Does `denyWrite` from `--settings` merge into the sandbox?** The guard
   (§10.3) depends on it. It gets settled by running `atc check` in a real
   session (§17, step 5).
2. **F7 hasn't been reproduced here.** The policy row is cheap insurance either
   way. It can be dropped if a test shows the claim is wrong.
3. **Does Claude Code follow a link at `<dir>/CLAUDE.md`?** Expected, given F11.
   Settled by starting a session in each account (§17, step 4).

**Decided 2026-10-23:**

- Rule content is configuration. atc only warns about rules that can never take
  effect (§8).
- Each account's instructions file is chosen with `use-default-agents-md` and
  `agents-md-file`, and its skills with a per-account selection and a picker
  (§10.2, §10.10).
- A trailing `:*` and a trailing ` *` match alike in a `Bash` rule (F14), so the
  rule warnings read one as the other (§8). This was open question 4.
