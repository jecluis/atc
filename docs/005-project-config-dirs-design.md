# atc project config directories: design

- Date: 2026-11-21
- Status: draft
- Design: [001-design.md](001-design.md), which this extends

## Why

A project's settings reach Claude Code only as the session's `--settings`, and
Claude Code never writes there. What Claude Code writes on its own goes to the
account's directory, which every session of the account shares:

- `/model`, `/config`, `/advisor`, `/effort` and `/fast` write
  `$CLAUDE_CONFIG_DIR/settings.json` (P1). That is the file `atc sync`
  generates, so the next sync stops on it as edited outside atc, and the change
  reaches every other session of the account. On this machine the personal
  account's file has already drifted that way.
- While the sandbox is admin-required, an approved network domain is saved to
  the user settings too (P2), so a domain approved for one project is approved
  for every project the account works on.
- Session history, `--resume` and auto memory live in the account's directory,
  for all projects together.

The only way to give Claude Code another user settings file is another
`CLAUDE_CONFIG_DIR` (P3). This design gives a project its own configuration
directory for each account that works on it, built by atc as an account's
directory is.

## Goals

- A project can ask for its own configuration directories. It then gets one for
  each account it allows, and `atc run` in the project starts Claude Code there.
- That directory's `settings.json` holds the account's settings with the
  project's merged over them, so a project's domains, sandbox exclusions and
  permission rules sit in user settings for that project alone.
- `atc sync` builds every project directory of every account it syncs, so a
  change pulled into `conf.d` reaches each (account, project) pair in use.
  `atc run` brings its own directory up to date before it starts the agent.
- What Claude Code adds to a project directory's `settings.json` survives the
  next sync: keys the units do not set, and entries added to lists. A setting
  the units give stays theirs: `/model` holds until the next sync or `atc run`
  when a unit sets `model`.
- The protections hold as they do for an account: each account's credentials, in
  all its directories, are out of its own sessions' reach, and every other
  account's directories are out of reach entirely.

## Non-goals

- Sharing one login between an account's directories. Each directory has its own
  login (P4), so the first session in a project directory asks for one. Linking
  `.credentials.json` across directories is undocumented, and a token refresh in
  one could log the others out.
- Filling a new project directory with what the account has outside atc's reach:
  installed plugins and marketplaces (`plugins/`), `agents/`, `commands/`,
  `rules/`, `keybindings.json`, and what Claude Code keeps in `.claude.json`. A
  project directory starts as a fresh one, with the settings, instructions file
  and skills that atc builds. `enabledPlugins` there names plugins that must be
  installed in it before they load.
- Merging Claude Code's own writes into an account's `settings.json`. The merge
  below is for project directories. Accounts keep the current behaviour.
- Removing project directories. Turning the option off, removing the project, or
  taking an account out of its `accounts` leaves the directories, with their
  logins and history, as they are.
- A command that turns the option on. It is a key in `machine.yaml`.

## Anti-goals

- A directory that a session can edit to widen its own rules. A project
  directory is protected as the account's own directory is, no less, and no edit
  to its `settings.json` survives a sync where it takes away what the units set.
  Additions do survive, and can loosen what the units leave unset, such as an
  entry of `sandbox.excludedCommands` or a hook; every `atc sync` lists them,
  and `atc run` does when one is new.
- A project directory reached through a symbolic link. atc refuses one at
  `projects`, at the project's directory or at the account's below it, as it
  refuses a linked `skills/`, so what it writes stays where it says.
- A launch that silently starts with stale settings. If `atc run` cannot bring
  the directory up to date, it does not start the agent.

## Facts

| ID  | Fact                                                                                                                                                            | Source                                                                                   |
| :-- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------- |
| P1  | `/model` saves to `$CLAUDE_CONFIG_DIR/settings.json`; `/config`, `/fast`, `/advisor` and `/effort` write `settings.json` too                                    | Claude Code settings documentation, "Change a setting for one session", "/config menu"   |
| P2  | "Yes, and don't ask again" writes the repository's `.claude/settings.local.json`; while the sandbox is admin-required, an approved domain goes to user settings | Claude Code sandboxing documentation, "Where approvals are saved"                        |
| P3  | `CLAUDE_CONFIG_DIR` moves the whole directory; nothing moves `settings.json` alone. `--settings` is never written to                                            | Claude Code documentation, `.claude` directory and settings pages                        |
| P4  | Each configuration directory has its own settings, session history and login                                                                                    | Claude Code authentication documentation, "Log in with multiple accounts"                |
| P5  | `atc run`'s `--settings` is written by Claude Code to a file named by the SHA-256 of its content, so concurrent sessions with different settings do not collide | Observed on this machine: the sandbox lists `claude-settings-<first 16 hex digits>.json` |

**Not established yet**, and settled by running `atc check` in a session in a
project directory:

- That Claude Code writes its own configuration directory freely under a
  directory that `sandbox.filesystem.denyWrite` and an `Edit` deny rule cover.
  Both govern commands and Claude's file tools, not Claude Code itself, so it
  should.
- That Claude Code's file tools are refused writes to auto memory under a
  directory an `Edit` deny rule covers. If so, auto memory in a project
  directory under atc's state directory would fail, which is why it is moved out
  (§3). If not, it could stay, with no harm done by moving it.
- That a `plugins` link into another directory works, and that two accounts can
  install into one plugins directory.

## 1. The registry

`projects.<name>.own-config-dir`, a boolean, defaults to `false`. With `true`,
the project has a **project directory** for each account it allows: every
account when it sets no `accounts`.

A project directory is `<state_dir>/projects/<project>/<account>`, computed in
one place (`atc.projectdirs`). The project's accounts share
`<state_dir>/projects/<project>/_common`; no account can be called `_common`,
since an account's name starts with a letter or a digit. A project's auto memory
is `<data_dir>/projects/<project>/memory`, where `data_dir` is `atc` in
`$XDG_DATA_HOME`, `~/.local/share/atc` by default.

In atc's state directory, the hardening units already keep sessions from writing
a project directory, with commands and with Claude's file tools alike, so no
session can edit the settings of its own project directory or of another's. Auto
memory is the one thing in a configuration directory that Claude Code writes
with its file tools, so it lives in the data directory, which nothing denies.

## 2. Variables

Units are built for a project directory with the same variables as for its
account. `account.dir` is still the account's directory: it names the account,
and units that use it keep meaning the same file.

`account.secrets` grows: after the account's own credentials and state file come
those of its project directories that exist, in the order of the projects. A
unit that denies `{{account.secrets}}` therefore denies an account's sessions
every login of the account, from whichever directory they run.

`peers.dir` grows too: after every other account's directory come their project
directories that exist, and then the auto memory directories that exist of the
projects with their own directories that the account may not work on. A unit
that denies `{{peers.dir}}` keeps an account's sessions out of all of them.
`peers.files` does not change. The auto memory of a project is shared by every
account the project allows: that is the one deliberate gap in their isolation.

Only files that exist are listed, as the guard lists only paths that exist: the
sandbox puts a placeholder where a denied path is missing, and an empty
`.credentials.json` or `.claude.json` in a directory nobody has logged into
would be taken for Claude Code's own. The cost: the first session in a project
directory, the one that logs in, is not denied the credentials it writes, and
the account's other sessions are denied them once `atc sync` or `atc run` has
rebuilt their settings. Starting the project's first session again after logging
in closes the gap for it.

## 3. What a project directory holds

As an account's directory, built by the same code, with the account's selection:

- `settings.json`: the account layer, then `autoMemoryDirectory` set to the
  project's auto memory directory, then the project layer, merged with the usual
  rules. The account layer is built as for the account; the project layer as
  `atc run` builds it, for that account. The policy of each layer applies to its
  own units, as now, and a project unit may set `autoMemoryDirectory` to move
  the memory elsewhere. A memory directory per project, rather than a link per
  worktree, works because Claude Code uses `autoMemoryDirectory` as the memory
  directory itself (F5 of 001), while the memory it finds on its own is under a
  directory named for the working directory, one per worktree.
- `CLAUDE.md` and `skills/`: the account's instructions file and skills, linked
  as for the account. The templated assets are the account's rendered copies.
- `plugins`: a link to `plugins` in `_common`, so the project's accounts share
  what is installed for the project.

The auto memory directory and `_common/plugins` are made, private to the user,
when a project directory is synced.

atc's record for a project directory is
`<state_dir>/sync/<account>/projects/<project>/`: the settings last generated,
the links it made, and the backups of what it replaced. The rendered copies stay
the account's.

`atc run` still passes the project layer in `--settings` (P3): the precedence of
a project's settings does not change, and a session started in the directory
without atc still gets them from `settings.json`.

## 4. Claude Code's own writes: the merge

In a project directory, Claude Code is expected to write `settings.json` (P1).
Stopping on that as an edit would leave the project's settings stale; taking
Claude Code's version wherever the units did not change a key would let a
removed deny rule or a disabled sandbox stick. So the units win wherever they
set a value, and only what Claude Code adds is kept. With **base** what atc last
generated, **live** the file and **new** what the units give now, at each key:

- New sets it, and it is neither an object nor a list: new's value.
- New sets an object, and live has an object there: merge the two the same way.
- New sets a list, and live has a list there: new's entries, then the entries of
  live that neither new nor base has, in live's order.
- New sets an object or a list, and live has something else: new's value.
- New does not set it, and base did: it goes, as the units removed it.
- New does not set it, and base did not: live's value stays. It is Claude
  Code's.

atc records new, not what it wrote, as what it generated, so a kept addition is
still an addition at the next sync. What the merge keeps from live, and what of
live it resets, is listed by every `atc sync`, and by `atc run` when it writes
the file or keeps something that it did not keep before: the sync record holds
what was last kept. `atc run --print` lists what it keeps. A live file that is
not a JSON object stops the sync as an edit does today, and `--discard-live`
writes new over it. A file atc never wrote is taken over only with
`--take-over`, as now.

## 5. `atc sync`

`atc sync`, and `atc sync ACCOUNT`, also sync the project directories of each
account synced: those that exist, of every project with `own-config-dir: true`
that allows the account, in the order of the file. `atc sync` does not make a
project directory: the first `atc run` in the project does, for the account it
runs with. Each project directory is reported as `ACCOUNT/PROJECT`, with the
same parts, flags and exit statuses as an account.

## 6. `atc run`

In a project with `own-config-dir: true`, before the agent starts, `atc run`
syncs the project directory of the chosen account. The directory is made if
missing, private to the user, and its settings, links and the account's rendered
copies are brought up to date as `atc sync` would, without `--take-over` or
`--discard-live`. If that cannot be done, because a unit fails to build, the
live file is not JSON, a link is not atc's, or something cannot be written, atc
stops with exit status `1` and says to run `atc sync --diff`. The account's own
directory is not touched.

The agent is then started with `CLAUDE_CONFIG_DIR` set to the project directory.
`--print` changes nothing; it names the configuration directory and says whether
it would be synced.

## 7. `atc check`

In a session that `atc run` started in a project directory, where `ATC_PROJECT`
names a project with `own-config-dir: true`, the settings, instructions and
skills checks are made for the project directory of `ATC_ACCOUNT`. The secrets
checked are `account.secrets`: those of every directory of the account.

## 8. Elsewhere

- `atc prj show` shows `config dir`: the project directories, or that the
  project uses the account's.
- `atc usage` and `-a auto` are unchanged: the status line records usage under
  `ATC_ACCOUNT`, whichever directory the session runs in.

## Choices made while drafting

- **In atc's state directory, by project and then account.** The hardening units
  already deny sessions every write there, which keeps each project directory's
  settings out of reach of every session. Inside the account's directory, a
  session of the account could edit them with Claude's file tools.
- **Auto memory in the data directory, through `autoMemoryDirectory`.** Moving
  it out of the state directory needs no change to the hardening units, whose
  one `Edit` rule on the state directory keeps covering whatever atc puts there.
  Leaving it in would need that rule split into one per directory of atc's.
- **History and logins not shared.** `--resume` across accounts would hand one
  account's transcripts to another, and a login must never be shared.
- **Project layer in the file and on the command line.** In the file alone, a
  repository's own `.claude/settings.json` would override a project's scalar
  settings, which `--settings` outranks today. On the command line alone, the
  directory would be no better than the account's for settings that only behave
  from user settings.
- **Units win, additions stay.** Keeping Claude Code's value wherever the units
  did not change a key would also keep a session's edit that drops a deny rule.
  Resetting what the units set, and keeping only additions, cannot weaken them.
- **The merge only for project directories.** It changes what a sync of an
  edited file does, so accounts keep today's behaviour until that is decided on
  its own.
- **Directories made by `atc run`, not `atc sync`.** Only pairs in use get one,
  and no session is denied files of a directory nobody has logged into.
