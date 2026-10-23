"""``atc run``: start the agent in the right account."""

import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import override

import click
import rich_click
from rich.text import Text
from rich.tree import Tree

from atc import launch
from atc.cli.options import HELP_OPTIONS, conf_d_option, lax_option, load_build_context
from atc.launch import LaunchPlan
from atc.ui import Ui

ABSENT = "\N{EN DASH}"
"""What stands for a repository or project that there is none of."""


class _RunCommand(rich_click.RichCommand):
    """
    A command whose agent arguments are everything after the first ``--``.

    click drops the ``--`` it parses, and with it the difference between a
    word that is for the agent and one that is a mistake, so the split is made
    here. A word before ``--`` is then click's own unexpected-argument error.
    """

    @override
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        split = args.index("--") if "--" in args else len(args)
        remaining = super().parse_args(ctx, args[:split])
        ctx.args = args[split + 1 :]
        return remaining

    @override
    def collect_usage_pieces(self, ctx: click.Context) -> list[str]:
        return [*super().collect_usage_pieces(ctx), "[-- AGENT-ARGS]..."]


@click.command(cls=_RunCommand, name="run", context_settings=HELP_OPTIONS)
@click.option(
    "-a",
    "--account",
    "account_name",
    metavar="ACCOUNT",
    help=(
        "Use ACCOUNT instead of the project's own account, or the default "
        "account when the project names none."
    ),
)
@click.option(
    "--settings",
    "settings_arg",
    metavar="FILE|JSON",
    help=(
        "Settings for this session only: the path of a JSON file, or JSON text "
        "that starts with {. They win over the project's settings. They are not "
        "templates, and cannot set sandbox.allowUnsandboxedCommands."
    ),
)
@click.option(
    "--print",
    "show_plan",
    is_flag=True,
    help="Show how the agent would be started, and exit without starting it.",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help="Do not print the line that names the project and the account.",
)
@conf_d_option
@lax_option
@click.pass_context
def run(
    ctx: click.Context,
    account_name: str | None,
    settings_arg: str | None,
    show_plan: bool,
    quiet: bool,
    conf_d: Path | None,
    lax: bool,
) -> None:
    """
    Start the agent in the right account.

    The project is the one in the machine registry whose repository the current
    directory is in, whether as the main checkout or as any worktree of it. The
    account is ACCOUNT if given, otherwise the project's, otherwise the
    registry's default. atc then replaces itself with the account's agent,
    started with that account's configuration directory and with ATC_ACCOUNT,
    ATC_PROJECT and ATC_STATE_DIR set. Any other ATC_ variable it was started
    with is dropped. It prints one line on standard error naming the project
    and the account.

    The agent is given the project's settings, if the project has a unit, as
    its --settings, with the ones given to atc merged over them. The account's
    own settings are not built: the agent reads them from the account's
    directory, as sync wrote them. --print builds them too, to warn about a
    permission rule that can never apply because another rule overrides it,
    whether it is the account's, the project's or one given with --settings. If
    the account's settings cannot be built, a warning says why and the other
    rules are still checked.

    Everything after -- is for the agent, so a --settings there is refused: the
    agent takes only one, and it would replace the one atc passes. Give
    --settings to atc, before --. atc with no command, or with a first argument
    that starts with -, means run. "atc -- --resume" therefore starts the agent
    with --resume, and alias claude='atc --' sends every claude command through
    atc.

    --conf-d is accepted only with --print.
    """
    if conf_d is not None and not show_plan:
        raise click.UsageError("--conf-d is only accepted with --print")
    build = load_build_context(conf_d)
    settings = None if settings_arg is None else launch.read_settings_arg(settings_arg)
    plan = launch.plan_launch(
        build,
        cwd=Path.cwd(),
        env=os.environ,
        account=account_name,
        settings=settings,
        agent_args=ctx.args,
        self_path=_self_path(),
        lax=lax,
        explain=show_plan,
    )

    ui = Ui.default()
    for warning in plan.warnings:
        ui.warning(warning)
    for rule_warning in plan.rule_warnings:
        ui.warning(rule_warning.message())
    if show_plan:
        _show(ui, plan)
        return
    if not quiet:
        project = ABSENT if plan.project is None else plan.project.name
        ui.info(f"atc: project {project} · account {plan.account.name}")
    launch.execute(plan)


def _self_path() -> Path:
    """Find atc's own executable: ``sys.argv[0]``, looked up on ``PATH`` if a name."""
    name = sys.argv[0]
    if os.sep in name:
        return Path(name)
    found = shutil.which(name)
    return Path(name if found is None else found)


def _show(ui: Ui, plan: LaunchPlan) -> None:
    """Write the plan as a tree, followed by the fragment as JSON."""
    project = ABSENT if plan.project is None else plan.project.name
    repository = ABSENT if plan.common_dir is None else str(plan.common_dir)
    account = plan.account
    tree = Tree(Text("atc run"))
    for line in (
        f"repository: {repository}",
        f"project: {project}",
        f"account: {account.name} ({account.agent}, {account.dir})",
    ):
        _ = tree.add(Text(line))
    environment = tree.add(
        Text("environment" if plan.env_changes else "environment: unchanged")
    )
    for name, value in plan.env_changes.items():
        _ = environment.add(
            Text(f"{name} (removed)" if value is None else f"{name}={value}")
        )
    _ = tree.add(Text(f"argv: {shlex.join(plan.argv)}"))
    ui.tree(tree)
    ui.dim("fragment:")
    ui.json(plan.fragment)
