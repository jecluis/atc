# atc

`atc` is the one command that starts coding agents on this machine. Run it
inside a project and it works out which project that is, which agent and
account should take the session, and which settings and protections apply, then
hands the terminal over to the agent. It generates each account's settings from
shared configuration that lives in your own repositories, apart from the tool.
Claude Code is the first agent it supports.

Today the tool implements `atc build` and `atc src ls`. The rest is being built
step by step.

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

### `atc build UNIT...`

Merges the named configuration units, together with everything they import, and
writes the resulting `settings.local.json` to standard output. Several units
can be named at once, as paths relative to the configuration root, for example
`langs/rust.json`.

| Option | Effect |
| --- | --- |
| `--root DIR` | Read units from `DIR` instead of the default, `~/.config/atc/conf.d` (see Configuration). |
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
