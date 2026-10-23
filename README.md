# atc

`atc` is the one command that starts coding agents on this machine. Run it
inside a project and it works out which project that is, which agent and
account should take the session, and which settings and protections apply, then
hands the terminal over to the agent. It generates each account's settings from
shared configuration that lives in your own repositories, apart from the tool.
Claude Code is the first agent it supports.

Today the tool implements one command, `atc build`. The rest is being built
step by step.

## Install

```sh
uv tool install ./atc
```

Run this from a commit you have reviewed, and never with `--editable`: an
editable install runs the code in the working checkout, which sessions can
edit, and atc's code decides every session's protections.

## Usage

### `atc build UNIT...`

Merges the named configuration units, together with everything they import, and
writes the resulting `settings.local.json` to standard output. Several units
can be named at once, as paths relative to the configuration root, for example
`langs/rust.json`.

| Option | Effect |
| --- | --- |
| `--root DIR` | Read units from `DIR` instead of the default configuration root. |
| `--lax` | Report settings keys Claude Code does not define as warnings rather than errors. |

Only the merged JSON goes to standard output, so it is safe to redirect or
pipe. Warnings and errors go to standard error.

Exit status is `0` on success, `1` for a problem with the configuration, and
`2` for a problem with the command line.

## Development

From the repository root:

```sh
uv run --directory atc atc build --help   # run atc from the working tree
./atc/check-and-fix.sh                    # format, lint, type check, test
./atc/check-and-fix.sh --check            # verify without changing anything
```
