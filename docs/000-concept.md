# atc — Agent Traffic Control: concept

- Date: 2026-10-23
- Status: draft
- Design: [001-design.md](001-design.md)

## In one paragraph

`atc` is the one command that starts coding agents on this machine. Run `atc`
inside a project and it works out which project that is, which agent and account
should take the session, and which settings and sandbox protections apply. Then
it hands the terminal over to the agent. It also generates each account's
settings from shared configuration, which lives in your own repositories apart
from the tool, and gives each account its instructions file and the skills you
chose for it. And it creates worktrees, and checks and maintains the protections
it sets up. Claude Code is the first agent it supports. Codex is planned next,
to spread load across more accounts.

## Why

Each of these problems was found between 2026-10-20 and 2026-10-23.

1. **Two Claude accounts have drifted apart.**
   - The personal account (`~/.claude`) and the Acme account
     (`~/.config/acme/claude`) have separate `settings.json` files that were
     edited by hand.
   - The Acme account has no global `CLAUDE.md`.
   - Each account's copy of the command-shape hook has a check the other lacks.
   - The git deny rules differ between the two in content and in syntax.
   - A typo (`allowedDomanins`) means the Acme sandbox's network allowlist is
     silently ignored.
2. **The settings need paths that differ by account and by machine.**
   - Hardening the sandbox means each account must deny access to the other
     account's directory.
   - Scratch roots and repository paths depend on the machine.
   - None of these can be hard-coded in a repository that is checked out on more
     than one machine.
3. **Project settings don't keep.**
   - A repository's `.claude/settings.local.json` collects every "always allow"
     answer. For example, the `anvil.git` file has piled up one-off entries like
     `TMPDIR=/tmp/claude-1000 … cargo test …`.
   - Rules that protect something, such as `denyRead` on vault files, sit mixed
     in with that pile.
   - The file is untracked and belongs to one checkout, so every new worktree
     starts without it.
4. **The logic for starting a session lives in three places.**
   - The `acme-claude` shell alias picks the account.
   - `claude-guard` protects packed git history, holds the repository lock and
     runs maintenance.
   - `ccwtctl` keeps the list of projects and their worktrees.
5. **Choosing an account is becoming a routing decision.** The personal account
   already takes Acme work when the Acme account is near its limits. Once Codex
   is added there will be more accounts, and more reason to pick one by
   available capacity.
6. **Memory is split by account.** The same repository builds up separate, and
   in places contradictory, memories depending on which account was used.

## What atc does

| Job              | Command      | In short                                                                                                        |
| :--------------- | :----------- | :-------------------------------------------------------------------------------------------------------------- |
| Start a session  | `atc`        | Finds the project, picks the account, builds this session's settings, guards the repository, starts the agent   |
| Account settings | `atc sync`   | Generates each account's `settings.json`, links its `CLAUDE.md` and skills, and stops on edits made outside atc |
| Skills           | `atc skills` | Picks, per account, which skills from your sources it gets                                                      |
| Configuration    | `atc src`    | Lists, checks and updates the config sources atc reads its units and assets from                                |
| Projects         | `atc prj`    | Lists projects and their defaults, and adds new ones                                                            |
| Worktrees        | `atc wt`     | Creates, lists and removes a project's worktrees                                                                |
| Verify           | `atc check`  | Run from inside a session: confirms the protections actually hold                                               |
| Maintain         | `atc maint`  | Packs new git history so sessions can't delete it (run by a systemd timer)                                      |
| Usage            | `atc usage`  | Shows each account's recent rate-limit usage. Later, `atc -a auto` picks an account from it                     |

## The model

| Term                  | Meaning                                                                                                                                          |
| :-------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Agent**             | A coding agent program: `claude` now, `codex` later                                                                                              |
| **Account**           | One login for one agent, with its own config directory. `personal` and `acme` are both Claude accounts                                           |
| **Project**           | A git repository registered with atc, with its own default account and settings                                                                  |
| **Worktree**          | Any checkout of a project. All worktrees of a project share its settings                                                                         |
| **Unit**              | A JSON fragment of agent settings that can import other units. Units are shared between machines                                                 |
| **Layer**             | Where merged units end up: account, project or session (below)                                                                                   |
| **Machine registry**  | `machine.yaml`, the per-machine file of accounts, projects, paths and variables                                                                  |
| **Config source**     | A directory with `conf/` (units), `assets/` (scripts and files the units point at) and `skills/`, usually a git clone in `~/.config/atc/conf.d/` |
| **Instructions file** | The account-wide `CLAUDE.md` (`AGENTS.md` for Codex). Each account links to a default one, its own, or none                                      |
| **Skill**             | A directory with a `SKILL.md`. Each account gets the skills chosen for it                                                                        |

### Three layers

| Layer   | Ends up in                    | Built when         | Typical contents                                                                      |
| :------ | :---------------------------- | :----------------- | :------------------------------------------------------------------------------------ |
| Account | `<account dir>/settings.json` | `atc sync`         | Strict sandbox mode, isolation from other accounts, hooks, plugins, preferences       |
| Project | the session's `--settings`    | every `atc` launch | A project's network domains, `denyRead` rules, MCP permissions, `autoMemoryDirectory` |
| Session | the session's `--settings`    | every `atc` launch | Write protection for the project's packed git history                                 |

The account layer goes in a file because some settings only behave correctly
from user settings. It also means a session started without atc still gets the
account's protections.

The project and session layers are passed on the command line. That way they
apply to every worktree, and they never mix with what Claude Code writes into
`settings.local.json`.

### Where things live

| What                           | Where                                           | Shared between machines? | Who changes it                         |
| :----------------------------- | :---------------------------------------------- | :----------------------- | :------------------------------------- |
| atc's code                     | this repository                                 | yes                      | you, through git                       |
| Your units, assets and skills  | your own repositories, as config sources        | yes                      | you, through git                       |
| Deployed configuration         | `~/.config/atc/conf.d/`, one or more git clones | yes (the same commits)   | `atc src pull`                         |
| Machine registry               | `~/.config/atc/machine.yaml`                    | no                       | you, `atc prj add`, or `atc skills`    |
| Account settings               | `<account dir>/settings.json`                   | no (generated)           | `atc sync`                             |
| Account `CLAUDE.md` and skills | links in `<account dir>`, into `conf.d/`        | no (generated)           | `atc sync`, `atc skills`               |
| State and locks                | `~/.local/state/atc/`, `$XDG_RUNTIME_DIR/atc/`  | no                       | atc, and the statusline for usage data |

## Principles

- **Units never contain machine-specific values.** Anything that could differ
  between machines (paths, account directories, scratch roots) lives in the
  machine registry, and units refer to it through variables such as
  `{{account.dir}}`.
- **The tool and your configuration are separate.** atc reads units and assets
  from config sources under `~/.config/atc/conf.d/`. That can be one repository,
  or several, such as one for personal and one for Acme configuration. atc has
  its own repository, which holds no configuration.
- **A session can't change its own rules.**
  - atc reads only the deployed clones in `conf.d/`, never the checkout an agent
    might be editing. A change arrives only as a commit that you pull, and atc
    checks it first.
  - Its own directories are closed to sessions.
  - Hooks and the statusline, which run outside the sandbox, run only from those
    deployed clones.
- **Your rules are yours.** atc doesn't decide what is allowed or denied. It
  warns when a rule can never take effect, such as an allow that a deny
  elsewhere always beats, and leaves the fix to you.
- **Generated settings never silently overwrite edits.** If an account's
  `settings.json` was changed outside atc, `atc sync` shows the change and
  stops. It treats a `CLAUDE.md` or skill it didn't create the same way.
- **One entry point, and going around it fails safe.** `claude` still works on
  its own. The account layer's protections still apply, because they live in
  user settings.
- **Check, don't assume.** The design separates facts that were checked from
  assumptions, and `atc check` re-tests the protections from inside a real
  session.

## Scope

### In the first version

- Claude Code as the only agent.
- Every command in the table above, except automatic account choice, which comes
  once there's real usage data to base it on.
- Each account's `CLAUDE.md`: a shared default, one of its own, or none.
- Each account's skills, chosen from a checklist.
- Moving everything listed in [What it replaces](#what-it-replaces) into atc.

### Not in the first version

- The Codex backend. The design leaves room for it but doesn't specify it.
- Installing plugins and adding marketplaces per account. Settings can turn
  plugins on and off; choosing what to install will likely follow the skills
  picker later.
- Containers or VMs. atc builds on Claude Code's own sandbox.
- Judging permission rules. atc only warns about rules that can never take
  effect.
- Setting up snapshots. The snapper setup script stays a separate, one-off root
  task.

## A day with atc

```sh
cd /mnt/pci5-dev/acme/wt/anvil/feature-x
atc                        # acme account, anvil settings, history protected
atc -a personal            # the same project, on the personal account
atc wt new fix-y --run     # new worktree for branch fix-y, straight into a session
atc src pull               # deploy the latest committed configuration
atc sync --diff            # what would change in each account's settings and links
atc skills pick acme       # tick the skills the acme account should have
atc usage                  # how close each account is to its limits
```

## What it replaces

| Today                                                 | Becomes                                                                                    |
| :---------------------------------------------------- | :----------------------------------------------------------------------------------------- |
| `repo.conf/` and `claudeconf`                         | atc for the tool; its unit format and merge rules carry over. The units move to `conf/`    |
| account dirs' `statusline-command.sh` symlinks, hooks | `assets/`, run from the deployed clone                                                     |
| `acme-claude` alias                                   | `atc` (`-a` picks another account)                                                         |
| `claude-guard`                                        | `atc` (protects history during a session) and `atc maint`                                  |
| `claude-guard-check`                                  | `atc check`                                                                                |
| `ccwtctl`                                             | `atc prj` and `atc wt`                                                                     |
| sandbox-hardening `.jq` filters                       | units in the account and project layers                                                    |
| hand-edited account `settings.json` files             | generated by `atc sync`                                                                    |
| a `CLAUDE.md` in one account and none in the other    | `assets/AGENTS.in.md`, linked into each account that wants it by `atc sync`                |
| `install-skills.sh`, linking every skill everywhere   | `atc skills`: chosen per account, linked into the deployed clone, not the working checkout |
