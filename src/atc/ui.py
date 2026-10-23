"""Terminal output: data on standard output, diagnostics on standard error."""

import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Self, cast

import click
import questionary
from rich.console import Console
from rich.json import JSON
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from atc.jsonval import JsonValue, dumps


@dataclass(frozen=True, slots=True)
class PickChoice:
    """
    One line of the picker.

    ``label`` is what the line shows, ``value`` is what picking it gives back,
    and ``checked`` says whether it starts ticked.
    """

    value: str
    label: str
    checked: bool


@dataclass(frozen=True, slots=True)
class Ui:
    """
    The two consoles atc writes to.

    ``out`` carries data and tables, ``err`` carries diagnostics. Whether either
    is coloured is left to rich: no colour when the stream is not a terminal, and
    ``NO_COLOR`` and ``FORCE_COLOR`` are honoured.
    """

    out: Console
    err: Console

    @classmethod
    def default(cls) -> Self:
        """Build a ``Ui`` on the process's standard output and error."""
        return cls(Console(), Console(stderr=True))

    def error(self, message: str) -> None:
        """Report a problem on standard error."""
        self._diagnostic("error:", "red", message)

    def warning(self, message: str) -> None:
        """Report something suspect on standard error."""
        self._diagnostic("warning:", "yellow", message)

    def info(self, message: str) -> None:
        """Report progress or success on standard error, as plain text."""
        self.err.print(Text(message), soft_wrap=True)

    def line(self, message: str) -> None:
        """Write one line of plain text on standard output."""
        self.out.print(Text(message), soft_wrap=True)

    def verdict(self, ok: bool, message: str) -> None:
        """
        Write ``message`` on standard output, led by ``PASS`` or ``FAIL``.

        The word is green or red on a terminal. A control character in
        ``message`` is shown as its escape sequence, as :meth:`dim` shows it.
        """
        label, style = ("PASS", "green") if ok else ("FAIL", "red")
        line = Text.assemble((label, style), "  ", _visible(message))
        self.out.print(line, soft_wrap=True)

    def dim(self, message: str) -> None:
        """
        Write one dimmed line of plain text on standard output.

        A control character in it, such as the escape that starts a terminal
        command, is shown as its escape sequence, since the text can name a file
        that someone else made.
        """
        self.out.print(Text(_visible(message), style="dim"), soft_wrap=True)

    def json(self, value: JsonValue) -> None:
        """
        Write ``value`` as JSON on standard output.

        On a terminal it is syntax-highlighted. Otherwise it is exactly
        :func:`atc.jsonval.dumps`, so redirected output is byte-for-byte what
        atc would write to a file.
        """
        if self.out.is_terminal:
            # soft_wrap: a long string must not get a line break inside it
            self.out.print(
                JSON(dumps(value), sort_keys=True, ensure_ascii=False), soft_wrap=True
            )
            return
        _ = self.out.file.write(dumps(value))
        self.out.file.flush()

    def diff(self, lines: Iterable[str]) -> None:
        """
        Write the lines of a unified diff on standard output.

        On a terminal, removed lines are red, added lines green and headers
        bold. The lines are plain text, never markup, and a control character
        in one, such as the escape that starts a terminal command, is shown as
        its escape sequence, since the lines can come from a file someone else
        edited.
        """
        for line in lines:
            text = Text(_visible(line), style=_diff_style(line))
            self.out.print(text, soft_wrap=True)

    def table(self, table: Table) -> None:
        """Write ``table`` on standard output."""
        self.out.print(table)

    def tree(self, tree: Tree) -> None:
        """Write ``tree`` on standard output."""
        self.out.print(tree)

    def pick(self, title: str, choices: Sequence[PickChoice]) -> list[str] | None:
        """
        Let the user tick any of ``choices``, and return the values of those ticked.

        An empty list is a confirmed pick of nothing. ``None`` means the user
        cancelled with Ctrl-C or Ctrl-Q, so nothing was chosen.

        Raises:
            click.UsageError: standard input or standard output is not a
                terminal.

        """
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise click.UsageError(
                "atc skills pick needs a terminal; use atc skills add or atc skills rm"
            )
        question = questionary.checkbox(
            title,
            choices=[
                questionary.Choice(
                    title=choice.label, value=choice.value, checked=choice.checked
                )
                for choice in choices
            ],
        )
        try:
            # The one place questionary's untyped answer is taken. For a
            # checkbox it is the list of the ticked choices' values, which are
            # strings here. It is narrowed at once, so nothing untyped leaves.
            answer: object = question.unsafe_ask()  # pyright: ignore[reportAny]
        except KeyboardInterrupt:
            return None
        if not isinstance(answer, list):
            return None
        return [item for item in cast("list[object]", answer) if isinstance(item, str)]

    def _diagnostic(self, prefix: str, style: str, message: str) -> None:
        # Text takes the message as it is; a string given to print would be
        # parsed as markup, and a path or branch name can look like markup.
        line = Text.assemble((prefix, style), " ", message)
        self.err.print(line, soft_wrap=True)


def _diff_style(line: str) -> str:
    """Pick the style of one line of a unified diff."""
    if line.startswith(("---", "+++")):
        return "bold"
    if line.startswith("@@"):
        return "cyan"
    if line.startswith("-"):
        return "red"
    if line.startswith("+"):
        return "green"
    return ""


def _visible(line: str) -> str:
    """Spell out the characters of ``line`` that a terminal would act on."""
    return "".join(
        char
        if char.isprintable() or char == "\t"
        else char.encode("unicode_escape").decode()
        for char in line
    )
