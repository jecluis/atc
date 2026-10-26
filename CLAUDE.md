# atc — Project Rules

`atc` starts coding agents in the right account, with settings built from shared
configuration. Source lives in `src/atc/`, tests in `tests/`. Units are not part
of the tool; they live in config sources.

## Code Comments

- Comment only what isn't obvious from the code itself — don't narrate what a
  reader can already see.
- Keep comments to the minimum a developer needs to understand what the code
  does.

## Documentation for Consumers

- Data structures, functions, CLI commands/arguments, and API/OpenAPI surfaces
  must always be documented — but scoped to what the consumer needs in order to
  use them. Avoid verbosity on details that don't affect how the consumer uses
  it.
- User-facing documentation — API docs, CLI help output, or anything meant to be
  read or rendered for a user — must never reference internal design, plan, or
  review documents, or any other internal documentation, unless the user
  explicitly asks for that reference. Example: a CLI `--help` string must not
  say "see design 007" — that information is meaningless to the person running
  the command.

## Typing

`pyrightconfig.json` runs basedpyright in `typeCheckingMode: "all"`. Treat that
as a design constraint, not a lint setting.

- No `Any` and no bare `object` in normal code. The sanctioned exceptions are
  the `json.loads` boundary in `jsonval.py`, the ruamel boundary in `yamlval.py`
  and the questionary boundary in `ui.py`. Each carries a scoped
  `# pyright: ignore` and a comment explaining why it is sound: `jsonval` casts
  what `json.loads` returns to `JsonValue`, and `yamlval` rebuilds what ruamel
  returns as a `JsonValue`, refusing any type JSON lacks. Its round-trip edit
  (`set_in_text`) reads the nodes ruamel built through the small protocols
  there, after `isinstance` has said what they are. `_answer` in `ui.py` takes
  the untyped answer of questionary's `unsafe_ask()` as `object`, and the `Ui`
  method that asked (`pick`, `confirm`, `choose`, `ask_path`, `ask_text`)
  narrows it on the spot to what it returns, so nothing untyped leaves `Ui`;
  `unsafe_ask` and not `ask`, because `ask` prints its cancel message on
  standard output. Do not add another such boundary without a comparable
  justification. click passes typed values to command functions, so the command
  line needs none; the one typed view of click's `Any`-returning `main` is in
  `cli/__init__.py`.
- Prefer `match` over a closed union to dispatch on JSON values, so the type
  checker can prove the dispatch is exhaustive.
- A private class needs `@final` or per-attribute annotations to satisfy
  `reportUnannotatedClassAttribute`. Prefer `@final`.
- Call results must be used or explicitly discarded with `_ =`.

## Validation

- pydantic validates; it never transforms what reaches the output. `load_unit`
  reads `settings` straight from the parsed document, so no default invented
  during validation can appear in generated settings.
- If a model is ever dumped, it must use `model_dump(exclude_unset=True)`. A
  plain `model_dump()` fills in defaults for fields nobody set, which would
  write settings the user never asked for.
- New Claude Code settings keys go in `settings_keys.py`. `--lax` exists to
  cover the gap until that happens; it is not a substitute for updating the
  list.

## Testing

- Any change to merge or resolution behaviour is written as a failing test
  first.
- Tests are type checked under the same settings as `src/`.

## Markdown

prettier formats the markdown files and markdownlint-cli2 lints them, always
through `scripts/normalize-markdown.sh`, which pins both versions; never run
either tool directly. A hook in `.claude/settings.json` runs
`scripts/normalize-markdown.sh --fix <file>` whenever Claude Code edits or
writes a markdown file, and hands back what markdownlint-cli2 still reports; fix
that. A markdown file changed any other way, such as from a shell, needs the
command run by hand. The tools come from the npm registry until they are cached;
if the script cannot fetch them, report that rather than skipping it.

## Before committing

`./check-and-fix.sh` must pass. It runs ruff format, ruff check,
`scripts/normalize-markdown.sh`, basedpyright, and pytest, in that order. Use
`./check-and-fix.sh --check` for a non-mutating verification.

## Commit messages

Format is `component: short description`, subject at most 72 characters, body at
most 80 columns and 2–4 sentences, omitted when the subject says enough.

Components used in this project:

- `atc` — the tool itself, its packaging, its tooling and its examples
- `docs` — documentation

Commits are DCO signed off (`-s`). Every commit must be GPG-signed by the user,
not by an agent, unless the user says otherwise. Until then an agent commits
with `--no-gpg-sign`, also when amending or rebasing, and leaves signing to the
user.
