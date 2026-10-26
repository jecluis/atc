# atc project and variable commands: design

- Date: 2026-10-26
- Status: accepted
- Design: [001-design.md](001-design.md) and
  [003-account-commands-design.md](003-account-commands-design.md), which this
  extends

## Why

`atc account add` asks for what an account needs on this machine, variables
included. Projects have no such help. `atc prj add` registers whatever its
arguments say, and leaving an option out silently means its default, so the user
has to know the options before running it. It also leaves the project's
variables to be found later, when `atc src check` or `atc sync` stops on one.
`atc prj rm` removes a project without asking, unlike `atc account rm`.

There is also no way to see the variables as a whole: which ones `machine.yaml`
defines, which ones the units need, for which account or project, and which of
them are still undefined. Today the only way to find an undefined one is to run
`atc sync` and read the error.

Finally, `atc check` run in a session that `atc run` did not start reports three
FAILs for the repository's packed history. The session never had that guard, so
the report reads as a broken protection when nothing broke.

## Goals

- `atc check` tells apart three kinds of session: one that `atc run` guarded,
  one it started without a guard, and one it did not start. It checks the packed
  history only where a guard was meant to hold.
- `atc prj rm` asks before it removes anything.
- `atc prj add` asks for every option its arguments leave out, and for every
  variable the project needs that nothing defines. After it, `atc src check`
  does not stop on an undefined variable for that project.
- `atc vars ls` shows every variable that `machine.yaml` defines or the units
  need. Each comes with its owner (the machine, an account or a project), its
  value or the fact that it is undefined, and what uses it.
- `atc vars set` defines a variable at the level its reference names, one at a
  time or by asking for every undefined one in turn.

## Non-goals

- A fallback from one level to another. The owner decided that the reference
  names the level: `{{vars.X}}` is machine-wide, `{{account.vars.X}}` belongs to
  an account and `{{project.vars.X}}` to a project, as they are today. Templates
  do not change.
- Variables that hold lists. The registry's variables are strings.
- Built-in variables, such as `home`, `account.dir` or `sources.NAME.dir`. They
  cannot be defined in `machine.yaml`, so they are not listed.
- References in a place where their level does not exist, such as
  `{{project.vars.X}}` in an account's unit. `atc src check` reports those.
- Changing an existing project's options, other than its variables.
- What a project does once no account may work on it. 003 left this for later,
  and it still is.

## Anti-goals

- A question in a script. `atc prj add` and `atc prj rm` refuse to run without a
  terminal rather than wait for an answer that never comes.
- Hiding a real gap in `atc check`. When a guard was meant to hold for the
  repository, its checks run and fail as they do today.
- Writing a file that is not a valid registry, or losing a comment in an edit.

## 1. The `atc run` marker

- When `atc run` guards the session, it adds `ATC_GUARD` to the agent's
  environment. Its value is the git common directory of the repository it
  guarded. `ATC_GUARD` is an `ATC_` variable, so a nested `atc run` drops it, as
  it drops the others, and sets its own.
- `atc check` runs three checks only when `ATC_GUARD` is set: `objects/pack` is
  read-only, `packed-refs` is read-only, and automatic gc is off. In place of
  them it gives one note, which is not a FAIL:
  - with neither `ATC_GUARD` nor `ATC_ACCOUNT`: this session was not started by
    `atc run`, so its packed history is not guarded and those checks are
    skipped;
  - with `ATC_ACCOUNT` but no `ATC_GUARD`: `atc run` started the session without
    a guard (`--no-guard`, or started outside a repository), so those checks are
    skipped.
- When `ATC_GUARD` names another repository than the one `atc check` is run in,
  the three checks still run for the repository it is in, since that is the
  history at stake. A note names the repository that `atc run` guarded.
- The other repository checks (hooks, config, `config.worktree`, new objects,
  the directory above the worktree) do not depend on the guard and run as they
  do today.
- The exit status counts FAILs only, as today, so a session with nothing failed
  apart from the skipped checks exits `0`.
- `ATC_GUARD` joins the environment table in 001-design.md §11 and the README.

## 2. `atc prj rm NAME`

1. An unknown `NAME` is an error that lists the projects there are, as today.
2. Without a terminal it is a usage error, exit `2`, since nothing can be
   confirmed.
3. It says what goes: the project's entry, with its repository's path, and its
   variables if it has any. It also says what stays: the repository, its
   worktrees and `projects/NAME.json`. Then it asks
   `Remove project NAME? [y/N]`, no by default.
4. No changes nothing, with exit `0`. Ctrl-C changes nothing, with exit `130`.
5. If `machine.yaml` changed between the question and the answer, nothing is
   written, as `atc account rm` does.
6. What is removed, comments included, is as today.

## 3. The variable inventory

`atc vars` and `atc prj add` both work from one list of variables. An entry is a
variable and its owner:

- A reference `{{vars.X}}` belongs to the machine.
- A reference `{{account.vars.X}}` belongs to an account.
- A reference `{{project.vars.X}}` belongs to a project.

References are looked for in the places where their level exists, each unit with
what it imports:

- For each account: its unit, every templated asset (atc renders each for every
  account), and the unit of every project the account may work on. A project
  whose `accounts` is left out allows every account.
- For each project: its unit. `{{project.vars.X}}` there belongs to the project.
  `{{account.vars.X}}` belongs to each account the project allows.
- `{{vars.X}}` belongs to the machine wherever it is found.

Every variable defined in `machine.yaml` is in the list too, even when nothing
uses it, and is then marked unused. Only names of one segment count, since those
are the only ones the registry can define. If an account's or a project's units
cannot be read, a warning names that account or project and says why, and the
rest is still listed. A unit that no account or project uses is not scanned, so
its references are not listed.

## 4. `atc vars ls`

A table on standard output with one row per variable and owner:

- **Variable**: the reference as units write it, such as `account.vars.scratch`.
- **Owner**: `machine`, `account NAME` or `project NAME`.
- **Value**: the value, or `undefined`, styled to stand out.
- **Used by**: the units and assets that use it, the first three and a count of
  the rest, or `-` when nothing does.

Rows come in this order: the machine's first, then the accounts' in the order of
the file, then the projects'. Within each owner, variables come in the order
they are first used, then the unused ones by name. When any variable is
undefined, a line on standard error counts them and points at `atc vars set`.
The exit status is `0` either way, since nothing failed. With no registry it
says so, as `atc account ls` does.

## 5. `atc vars set [NAME [VALUE]] [-a ACCOUNT | -p PROJECT]`

- `NAME` is the variable's name as the registry spells it, such as `scratch`.
  `-a ACCOUNT` writes it in that account's `vars`, `-p PROJECT` in that
  project's, and with neither it goes in the machine-wide `vars`. Giving both is
  a usage error.
- A value must not be empty or contain `{{`, and a leading `~` or `~/` is
  written as the home directory, as in `atc account add`.
- With `NAME` and `VALUE`, nothing is asked, so it works without a terminal.
- With `NAME` alone, it asks for the value with path completion. The current
  value, if there is one, is the default.
- With no `NAME`, it asks for every undefined variable in the inventory, in the
  order of `atc vars ls`, each question naming the owner and what uses it. An
  empty answer leaves that one undefined and goes on. `-a` or `-p` limits the
  questions to that owner. Everything answered is written in one edit at the
  end, and Ctrl-C writes nothing.
- Asking needs a terminal. Without one it is a usage error, exit `2`.
- Replacing a value says what it was and what it is now, and asks nothing.
- When nothing references the variable at that level, a warning says so, since
  it is likely a misspelling, and the value is written anyway.
- A machine-wide `vars:` that does not exist yet goes before `accounts:`, not at
  the end of the file. An account's or project's `vars` goes at the end of its
  entry. `atc account add` places a new machine-wide `vars:` the same way.
- It ends by saying when the change takes effect: a project's variables at its
  next `atc run`, and the others once `atc sync` runs. It never syncs, as
  `atc account add` does not.

## 6. `atc vars rm NAME [-a ACCOUNT | -p PROJECT] [--force]`

- The level is chosen as for `set`. A variable not defined there is an error
  that lists the ones that are.
- A variable that something still uses is refused with exit `1`, naming what
  uses it, unless `--force` is given. With `--force` it becomes undefined.
- It always asks `Remove NAME from OWNER? [y/N]`, no by default, and needs a
  terminal, as the other `rm` commands do.
- A `vars` map left empty is removed with it.
- It ends with the same note as `set` about when the change takes effect.

## 7. `atc prj add NAME [REPO] [--worktrees DIR] [--from BRANCH] [-a ACCOUNT]...`

### Checks before any question

1. Without a terminal it is a usage error, exit `2`. The owner chose this over
   working from the arguments alone.
2. `NAME` must be a valid project name that is not registered yet. These two
   checks move ahead of every question instead of waiting for the write.
3. When `REPO` is given, the checks it has today: a git repository that no
   project has yet.
4. Each `-a ACCOUNT` must exist and be given only once.
5. The config sources load. If none has `projects/NAME.json`, the warning that
   says so comes now, so the user can stop before answering anything.

### Questions, in this order, each only when its argument was not given

1. **The repository.** A path with completion, of directories only. The default
   is the current directory, when that is in a git repository. An answer is
   needed, and it is checked on the spot, as `REPO` is. What is registered is
   the main worktree, as today.
2. **The branch new branches start from.** Text with completion from the
   repository's local branches. The default is `main` when that branch exists.
   The answer must be an existing branch. When it is `main`, `branch-from` is
   not written, since `main` is the default.
3. **The worktrees directory.** A path with completion, of directories only,
   that need not exist. An empty answer means none, and `worktrees` is not
   written.
4. **The accounts.** A checklist of the registry's accounts, none ticked.
   Nothing ticked means every account may work on the project, and neither
   `account` nor `accounts` is written. When more than one is ticked, a second
   question picks the default among them, with the registry's default account
   preselected if it is ticked, or else the first. `account` is the default, and
   `accounts` lists the default first and then the others in the order of the
   file. This is the same rule as `-a`, where the first is the default.
5. **The variables.** Before asking, the project's layer is built for each
   account it allows, with a stand-in for each undefined variable. Anything else
   that would stop the build stops `add` here, before any variable is asked.
   Then it asks for every variable that nothing defines among the references in
   the project's own unit and what it imports, which is what `atc src check`
   builds for the project. A variable that an allowed account's own units need
   is left to `atc vars set`. The ones asked are:
   - `{{project.vars.X}}`, for the project;
   - `{{account.vars.X}}`, for each allowed account that lacks it, one question
     per account;
   - `{{vars.X}}`, for the machine.

   Values follow the rules of `atc vars set`, except that an answer is needed,
   as in `atc account add`, since the project cannot be synced without it.

### Writing

- One edit writes the project's entry, its `vars` inside it, and the variables
  for accounts and the machine. The result is checked as the registry always is
  and replaced atomically.
- The entry's keys come in this order: `repo`, `worktrees`, `branch-from`,
  `account`, `accounts`, `vars`.
- Ctrl-C at any question writes nothing and exits `130`.
- When it wrote variables for accounts or the machine, it ends by asking for
  `atc sync`, as `atc account add` does.

## Constraints

- The registry stays the one place for its rules. Every command checks the file
  it is about to write the way every reader does, and checks an answer on the
  spot only where the answer can be judged alone.
- CLAUDE.md's typing rules hold. A text question with completion from a list,
  for the branch, is a new `Ui` method that narrows its answer in place, and
  CLAUDE.md's description of `_answer` lists it.
- The README documents every new and changed command in Usage.
- Exit statuses are the ones in `exitcodes.py`.

## Code to build on

- `needed.py`: how `atc account add` finds the variables an account needs,
  including the stand-in build. The inventory generalizes it.
- `cli/account.py`: `_value_fault`, `_as_value`, `_variable_question`, the
  directory question and `_cancelled`, which `prj add` and `vars set` share once
  they are moved out of it.
- `registry.AccountRemoval`: one read, then a refusal if the file changed before
  the write.
- `launch._atc_environment` and `check._repository`.
- `ui.py`: `ask_path`, `confirm`, `choose`, `pick` and `need_terminal`.
- `tests/test_account.py`: the `answering` helper that drives the questions.

## Choices made while drafting

The owner decided:

- `atc check` should tell when `atc run` did not guard the session.
- `atc prj rm` always confirms.
- `atc prj add` asks for what its arguments leave out, empty answers are allowed
  for what is optional, and it asks for variables as `atc account add` does.
- `atc prj add` needs a terminal.
- The reference names a variable's level, with no fallback.
- Variables can be listed and then defined at the machine, account or project
  level.

These were settled while writing this document, and can be changed:

- The name `ATC_GUARD`, its value, and the skipped checks becoming notes, which
  leave the exit status at `0`.
- `atc prj rm` needs a terminal, and writes nothing if the file changed while it
  asked.
- The command group is `atc vars`, with `ls`, `set` and `rm`. `rm` was not asked
  for, and is there so a mistaken definition can be undone without editing the
  file.
- `set` takes a plain name and a level option rather than the full reference. It
  replaces a value without asking, warns about a name nothing references, and
  skips empty answers when asking for every undefined variable.
- `vars rm` refuses a variable in use without `--force`, as `atc account rm`
  refuses an account in use.
- `vars ls` has no filters, and counts the undefined variables on standard
  error.
- What `prj add` asks, in what order and with which defaults. The default for
  the branch is `main`, and `branch-from` is not written when the answer is
  `main`. The accounts checklist starts with nothing ticked, and the default
  question comes only when more than one is ticked.
- Variable values in `prj add` cannot be empty.
- `prj add` asks only for the variables of the project's own units, not for gaps
  an allowed account's units already have.
- `vars set`, `vars rm` and `prj add` end by saying when a change takes effect,
  and never run `atc sync`.
- `atc account add` also puts a new machine-wide `vars:` before `accounts:`.

## Left to the builder

- How the inventory is computed and shared between `vars` and `prj add`.
- How a new machine-wide `vars:` is placed before `accounts:`.
- How tests drive the questions without a terminal.
- The exact wording of questions and messages, and the table's headings.
