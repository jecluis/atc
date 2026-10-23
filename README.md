# atc

`atc` is the one command that starts coding agents on this machine. Run it
inside a project and it works out which project that is, which agent and
account should take the session, and which settings and protections apply, then
hands the terminal over to the agent. It generates each account's settings from
shared configuration that lives in your own repositories, apart from the tool.
Claude Code is the first agent it supports.

Today the tool implements `atc build`, `atc src ls` and `atc src check`. The
rest is being built step by step.

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
`conf/` (units), `assets/` (files the units point at) and `skills/`. Anything
else in a source is ignored, so a whole repository can be a source as it is.

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
names are lowercase letters, digits, `_` and `-`, starting with a letter or a
digit. Entries whose names start with `.`, and plain files, are ignored. In the
multi layout, a visible subdirectory that has none of `conf/`, `assets/` and
`skills/` is ignored with a warning.

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
`chmod -R go-w` on it once after cloning.

## The machine registry

`machine.yaml`, in atc's configuration directory next to `conf.d/`, holds what
is specific to this machine: which accounts exist and where each one lives. It
belongs to no config source, so it never lands in a git repository by accident.

```yaml
version: 1
accounts:
  personal:
    dir: ~/.claude
  work:
    dir: ~/.config/work/claude
    unit: "@work/accounts/work.json"
```

| Key | Required | Meaning |
| --- | --- | --- |
| `version` | yes | Must be `1`. |
| `accounts.<name>.dir` | yes | The account's configuration directory. |
| `accounts.<name>.unit` | no | The unit the account's settings are built from, as a unit reference (see Units). Defaults to `accounts/<name>.json`. |

No other keys are accepted yet, and a misspelled key is an error. The rules:

- The file is YAML 1.2, so `no`, `on` and `off` stay strings. Only values JSON
  can hold are accepted: a timestamp such as `2026-10-23` is an error that
  asks you to quote it. Repeated keys and keys that are not strings are errors.
- A leading `~` in a path stands for your home directory. Every path must then
  be absolute. Environment variables are not expanded, so `$HOME/x` is an
  error.
- Account names are lowercase letters, digits, `_` and `-`, starting with a
  letter or a digit.
- Two accounts cannot share a `dir`, however it is written: a trailing `/` or
  a symbolic link in the path makes no difference.

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
| `settings` | Optional fragment of `settings.local.json`. Its top-level keys must be ones Claude Code defines (`atc build --lax` accepts others with a warning). |

No other fields are allowed. A unit with only `imports` works as a profile that
groups other units.

**References.** An import, a `UNIT` given to `atc build`, and an account's `unit`
in the registry are each one of:

- `path/to/unit.json`: a path relative to the `conf/` of the same source.
- `@name/path/to/unit.json`: a path relative to the `conf/` of the source
  called `name`. This only works in the multi layout.

A reference must be relative, must spell out the `.json` extension, and must
stay inside the `conf/` of its source, links included. In an import, a
reference without `@` always means the unit's own source.

Outside a unit, as on the command line or in the registry, a reference without
`@` is looked up in every source, and exactly one must contain it. If none does the unit does not
exist, and if several do you are asked to add an `@name/` prefix. With a single
source, or with names that never collide, the prefix is never needed.

**Merging.** Imports are followed depth-first and in order, each before the
unit's own `settings`, so an importing unit overrides what it imports and a
later import overrides an earlier one. A unit contributes once, at the first
point the walk reaches it. Objects merge recursively, arrays concatenate
without duplicates, and for any other value the later one wins. Two values of
different JSON types at the same key are an error. The output is
deterministic: keys are sorted.

## Usage

Run `atc --help`, or `--help` after any command, for the options.

### `atc src ls [--conf-d DIR]`

Lists the config sources as a table, one row per source:

- its name (`-` for the single layout), path and layout
- how many units (`*.json` files under `conf/`) and asset files it has
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

When the machine registry exists, it also checks that every account's unit
exists: an account whose unit is missing, or is in several sources, is an
error. Without a registry, only the units are checked, with a warning saying
so.

When all is well it prints `ok: N units in M sources` and exits `0`. Otherwise
it prints each problem, once, and exits `1`. Everything goes to standard
error. A source that breaks the trust rules, or a registry that is invalid,
stops the check with that error.

| Option | Effect |
| --- | --- |
| `--conf-d DIR` | Read config sources from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). The trust rules still apply. |

### `atc build UNIT... [--conf-d DIR] [--lax]`

Merges the named units, together with everything they import, and writes the
resulting `settings.local.json` to standard output. Several units can be named
at once. Each is a unit reference as described under Units, for example
`langs/rust.json` or `@work/langs/rust.json`, and they are merged left to
right, except that a unit an earlier one already imported keeps the position it
had.

| Option | Effect |
| --- | --- |
| `--conf-d DIR` | Read config sources from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). The trust rules still apply. |
| `--lax` | Report settings keys Claude Code does not define as warnings rather than errors. |

Only the merged JSON goes to standard output, so it is safe to redirect or
pipe. Warnings and errors go to standard error.

### Output and exit status

Output is coloured only when it goes to a terminal. `NO_COLOR` and
`FORCE_COLOR` are honoured.

Exit status is `0` on success, `1` for a problem with the configuration, a
config source or git, and `2` for a problem with the command line.

## Development

From the repository root:

```sh
uv run --directory atc atc build --help   # run atc from the working tree
./atc/check-and-fix.sh                    # format, lint, type check, test
./atc/check-and-fix.sh --check            # verify without changing anything
```
