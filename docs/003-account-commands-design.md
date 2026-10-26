# atc account commands: design

- Date: 2026-10-26
- Status: draft
- Design: [001-design.md](001-design.md), which this extends

## Why

Setting atc up on a new machine means writing `machine.yaml` by hand: knowing
which keys exist, where each goes, and getting every value right. Most of what
an account needs is already said by the config sources in `conf.d/`: the unit
names the settings, the units it imports name the variables they use, and the
sources hold the skills and the instructions file. What is left is what belongs
to this machine, mostly paths.

atc should work out what it can from the sources and ask only for the rest. This
document covers accounts. Projects already have `atc prj add`, `ls`, `show` and
`rm`; changes to them come later.

## Goals

- `atc account add` registers an account from its name, its unit and its agent
  given on the command line, and asks interactively for everything else the
  account needs on this machine.
- The first `atc account add` on a machine creates `machine.yaml`.
- `atc account ls` lists the accounts and `atc account rm` removes one.
- After `atc account add NAME`, no unknown reference is left for the account:
  `atc src check` and `atc sync` do not stop on an undefined variable, for the
  account or for a project it may work on.

## Non-goals

- Projects. They keep the commands they have.
- Changing an existing account, other than its skills, which `atc skills` does
  already.
- Logging in or creating the account's directory. atc never creates an account,
  since that means logging in (README, `atc sync`).
- Running `atc sync` for the new account.
- Settling what a project should do once it has no account it may use. Removal
  refuses that case (see `atc account rm`) and the question is left for later.

## Anti-goals

The commands fail if any of these happens, even when they otherwise work:

- Normal setup still needs `machine.yaml` edited by hand.
- A removal quietly widens which accounts may work on a project.
- A registry that does not check out is written, or comments are lost.
- A prompt waits for input that can never come, because there is no terminal.

## The command group

`atc account`, with `atc acc` as an alias that works the same way. It sits with
`prj`, `wt`, `src` and `skills`.

## `atc account add NAME [-u|--unit UNIT] [--agent AGENT]`

`-u`/`--unit` takes a unit reference, with or without an `@name/` prefix, as the
registry's `unit` does. It defaults to `accounts/NAME.json`, the registry's own
default. `--agent` defaults to `claude`, the only agent there is; any other
value is a usage error. It has no short form, because `-a` means `--account`
everywhere else in atc.

### Checks before any question

These run first, so a doomed command never asks anything. Each failure is an
error that exits `1`, unless noted, and nothing is written.

1. Standard input and standard output are terminals. Otherwise it is a usage
   error, exit `2`, as for `atc skills pick`.
2. `NAME` follows the registry's rules for account names, is not `auto`, and is
   not an account already.
3. The config sources load, as for any command that reads them.
4. `UNIT` resolves to exactly one unit, by the registry's rules. When no source
   has it, the error lists the account units the sources do have: the files
   under each source's `conf/accounts/`, written as references. When several do,
   the error asks for an `@name/` prefix and names the sources.
5. If `machine.yaml` exists, it is a valid registry now. If it does not exist,
   it will be created.
6. The unit and every unit it imports load, and every reference in them, in the
   templated assets that could be rendered for the account, and in the project
   units of question 4, is one that either resolves already or is a variable a
   question will define. A missing or circular import, a misspelled reference,
   or a `{{project.*}}` in an account unit is reported with the error
   `atc src check` would give, before anything is asked.

### Questions, in this order

Ctrl-C at any question cancels: `cancelled; nothing changed` on standard error,
exit `130`, and nothing is written, as for `atc skills pick`.

1. **Directory** (`dir`). A path, typed with path completion, as questionary's
   path prompt provides. It suggests `~/.claude` when no account uses that
   directory, and `~/.config/NAME/claude` otherwise. An answer is checked on the
   spot, and asked again when it fails, against the registry's rules for a
   `dir`: a leading `~` is expanded, the result must be absolute, `$` is not
   expanded, and no other account may have the same directory, however it is
   written. The directory need not exist.
2. **Instructions file**. Asked only when the sources have the asset that
   `default-agents-md` names (`AGENTS.in.md` unless the registry says
   otherwise): whether to link it as the account's instructions file, yes by
   default. Yes writes `use-default-agents-md: true`. `agents-md-file` is never
   asked; it stays a hand edit.
3. **Skills**. Asked only when the sources have skills: whether to pick skills
   now, no by default. Yes shows the checklist of `atc skills pick`, with
   nothing ticked. A confirmed empty pick writes no `skills`.
4. **Variables**. One question for each variable the account needs that nothing
   defines yet:
   - `{{account.vars.X}}` is stored as `accounts.NAME.vars.X`;
   - `{{vars.X}}`, a machine-wide variable, is stored as `vars.X` at the top of
     `machine.yaml`, where every account and project sees it. One that the
     registry defines already is not asked.

   What the account needs is every such reference in its unit and the units it
   imports, and in the templated assets atc renders for it given the answers
   above, such as the instructions file of question 2. That is why variables
   come last. It also needs those in the units of every project it may work on,
   and the units they import, since each such project is built for each account
   it allows. A new account is in no project's `accounts` yet, so those are the
   projects that have no `accounts` list. Each question names the variable and
   the units or assets that use it. The answer is a string with path completion,
   since most variables hold paths. It must not be empty and must not contain
   `{{`; otherwise it is asked again. Variables are asked in the order of their
   first use.

### Writing

- The account goes after the others, with its keys in this order, and each only
  when it differs from the default: `agent`, `dir`, `unit`,
  `use-default-agents-md`, `skills`, `vars`. `agent` and `dir` are required by
  the registry, so they are always written. `unit` is left out when it is
  `accounts/NAME.json`.
- `dir` is written as typed, without a trailing `/`, so `~/.claude` stays
  `~/.claude`.
- Machine-wide variables are added to the top-level `vars`, which is created if
  missing.
- When `machine.yaml` does not exist, it is created holding `version: 1`,
  `default-account: NAME`, any `vars` and `accounts` with the one account. The
  first account is the default without asking.
- The new file is checked as a whole before it replaces the old one, and the old
  one stays when the check fails. Comments and key order are kept, and a
  symbolic link is written through, as the other commands that edit the registry
  do.
- Then it says what was added, and what to do next: run `atc sync`, for every
  account and not only the new one. Each account's settings are built knowing
  the others (`peers.dir` and `peers.files`), which is how a unit keeps one
  account's sessions out of another's directory and credentials; until the other
  accounts are synced, their sessions are not kept out of the new one's. If the
  directory does not exist yet, it says the agent has to create it first, by
  logging in.

## `atc account ls`

A table of the accounts, in the order of the file: name, agent, directory and
unit, with the unit defaulted as the registry does. The default account is
marked. It goes to standard output. With no accounts, or no `machine.yaml`, it
prints no table and says so on standard error, as `atc prj ls` does.

## `atc account rm NAME [--force]`

1. An unknown `NAME` is an error that lists the accounts there are.
2. The last account is never removed, even with `--force`: the registry needs a
   default account.
3. Without `--force`, an account still in use is refused with exit `1`, naming
   each use: it is `default-account`, or a project's `account`, or in a
   project's `accounts`.
4. With `--force`, the uses are cleared:
   - If it is the default, a question picks the new default from the accounts
     that remain.
   - A project whose `account` it is loses that key, so the project falls back
     to the default account.
   - It is taken out of every project's `accounts`.
   - A project whose `accounts` held only this account is refused, and named,
     even with `--force`. Dropping the emptied list would let every account work
     on the project.
   - If the result is still not a valid registry, for instance because the
     default account is not in a project's `accounts`, the removal is refused
     with the error that says why.
5. Before anything is written, with or without `--force`, it asks
   `Remove account NAME? [y/N]`, no by default. Under `--force`, the question
   first lists every other change it will make: the new default, and each
   project touched. Without a terminal it is a usage error, exit `2`, since
   nothing can be confirmed. No answers and Ctrl-C both change nothing, Ctrl-C
   with exit `130`.
6. The entry goes with its comments, as `atc prj rm` removes a project's.
   Nothing else is touched: not the account's directory, not its units, not the
   copies atc rendered for it.
7. Afterwards it says that the account's directory is still there, credentials
   included, and that once the other accounts are synced again their sessions
   are no longer kept out of it, so it should be deleted if it is no longer
   needed.

## Constraints

- The registry stays the one source of truth for its rules. The command checks
  the file it is about to write the way every reader does, and asks again on the
  spot only where an answer can be checked alone (a `dir`, a variable's value).
- CLAUDE.md's Typing rules hold. Questionary's answers are untyped, and
  `Ui.pick` in `ui.py` is the one place that takes one today. The new questions
  (a path or text with completion, yes/no, a choice of one) take theirs in `Ui`
  too, each narrowed on the spot, and CLAUDE.md's description of that boundary
  is updated to cover them.
- The README documents the three commands in Usage, as the other commands are,
  and the machine registry section says that `atc account add` writes the file.
- Exit statuses are the ones in `exitcodes.py`.

## Code to build on

- `registry.py`: `add_project`, `remove_project` and `set_skills` show how an
  edit reads, checks and writes the file; `_write_checked`, `parse_registry` and
  `normalize_path` hold the rules. Creating the file from nothing is new.
- `yamlval.py`: `set_in_text` and `delete_in_text`, the round-trip edits.
- `cli/prj.py`: the shape of `add`, `ls` and `rm`, and the table of `ls`.
- `cli/skills.py`: `pick`, its catalog of skills and its cancellation.
- `ui.py`: `Ui.pick`, the checklist and the typing boundary.
- `template.py`, `variables.py` and `rendered.py`: how references are written,
  which variables a unit sees, and which assets are rendered for an account.
- `registry.account_unit`: how an account's unit is resolved.

## Choices made while drafting

The owner decided the command lines, what `add` asks, and the rules for `rm`:
refusing the default or an account in use without `--force`, and always
confirming. These were settled while writing this document, and can be changed:

- The document's number and name.
- `--agent` has no short form.
- The directory suggestions, `~/.claude` and then `~/.config/NAME/claude`.
- The instructions question defaults to yes, the skills question to no.
- Variables cannot be empty, have path completion, and come in order of first
  use.
- `unit` is written only when it is not the default, and `dir` as typed.
- A reference no answer can fix stops `add` before any question, rather than
  adding the account with a warning.
- The variables asked include those of projects the account may work on.
- `add` ends by asking for `atc sync` of every account, not only the new one;
  `rm` ends by pointing at the directory it leaves.
- `rm` never removes the last account, and needs a terminal for its
  confirmation.
- The columns of `atc account ls`.

## Left to the builder

- How the alias is made, as long as `atc acc` and `atc account` behave the same
  and the help shows both.
- How references are collected from units and templated assets, as long as the
  goal holds: after `add`, nothing the account uses is undefined.
- How tests drive the questions without a terminal.
- The exact wording of questions and messages, and the columns' headings.
