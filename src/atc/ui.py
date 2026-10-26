"""Terminal output: data on standard output, diagnostics on standard error."""

import sys
from collections.abc import Callable, Iterable, Sequence
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
        """
        Report a problem on standard error.

        A control character in ``message`` is shown as its escape sequence, as
        :meth:`dim` shows it, since messages quote units and git's output.
        """
        self._diagnostic("error:", "red", message)

    def warning(self, message: str) -> None:
        """Report something suspect on standard error, as :meth:`error` does."""
        self._diagnostic("warning:", "yellow", message)

    def info(self, message: str) -> None:
        """
        Report progress or success on standard error, as plain text.

        A control character in ``message`` is shown as its escape sequence.
        """
        self.err.print(shown(message), soft_wrap=True)

    def line(self, message: str) -> None:
        """Write one line of plain text on standard output."""
        self.out.print(shown(message), soft_wrap=True)

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
        """Write ``table`` on standard output. Make its cells with :func:`shown`."""
        self.out.print(table)

    def tree(self, tree: Tree) -> None:
        """Write ``tree`` on standard output. Make its labels with :func:`shown`."""
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
        need_terminal("atc skills pick", "use atc skills add or atc skills rm")
        question = questionary.checkbox(
            title,
            choices=[
                questionary.Choice(
                    title=choice.label, value=choice.value, checked=choice.checked
                )
                for choice in choices
            ],
        )
        # A checkbox answers with the list of the ticked choices' values, which
        # are strings here.
        answer = _answer(question)
        if not isinstance(answer, list):
            return None
        return [item for item in cast("list[object]", answer) if isinstance(item, str)]

    def confirm(self, question: str, *, default: bool = False) -> bool | None:
        """
        Ask ``question``, to be answered yes or no, and return the answer.

        ``default`` is what enter alone answers. ``None`` means the user
        cancelled with Ctrl-C. Check for a terminal first, with
        :func:`need_terminal`.
        """
        answer = _answer(questionary.confirm(question, default=default))
        return answer if isinstance(answer, bool) else None

    def ask_path(
        self,
        question: str,
        *,
        default: str = "",
        check: Callable[[str], str | None] | None = None,
        directories: bool = False,
    ) -> str | None:
        """
        Ask ``question``, to be answered with a path, typed with completion.

        ``default`` is what the answer starts as. ``check`` says what is wrong
        with an answer, or ``None`` when nothing is, and an answer it finds
        fault with is not taken: the fault is shown, to be corrected. With
        ``directories``, only directories are offered to complete. ``None``
        means the user cancelled with Ctrl-C. Check for a terminal first, with
        :func:`need_terminal`.
        """

        def validate(text: str) -> bool | str:
            fault = None if check is None else check(text)
            return True if fault is None else fault

        prompt = questionary.path(
            question, default=default, validate=validate, only_directories=directories
        )
        answer = _answer(prompt)
        return answer if isinstance(answer, str) else None

    def choose(self, question: str, choices: Sequence[str]) -> str | None:
        """
        Let the user choose one of ``choices``, and return it.

        ``None`` means the user cancelled with Ctrl-C. Check for a terminal
        first, with :func:`need_terminal`.
        """
        answer = _answer(questionary.select(question, choices=list(choices)))
        return answer if isinstance(answer, str) else None

    def _diagnostic(self, prefix: str, style: str, message: str) -> None:
        # Text takes the message as it is; a string given to print would be
        # parsed as markup, and a path or branch name can look like markup.
        line = Text.assemble((prefix, style), " ", _visible(message))
        self.err.print(line, soft_wrap=True)


def need_terminal(command: str, advice: str | None = None) -> None:
    """
    Refuse to go on unless standard input and standard output are terminals.

    ``command`` names what needs them, and ``advice`` says what to do instead.

    Raises:
        click.UsageError: either is not a terminal.

    """
    if sys.stdin.isatty() and sys.stdout.isatty():
        return
    message = f"{command} needs a terminal"
    raise click.UsageError(message if advice is None else f"{message}; {advice}")


def _answer(question: questionary.Question) -> object:
    """Ask ``question``, and give its answer; ``None`` when Ctrl-C cancels it."""
    try:
        # The one place questionary's untyped answers are taken. Each is taken
        # as `object`, and the method that asked narrows it at once, so nothing
        # untyped leaves `Ui`.
        return question.unsafe_ask()  # pyright: ignore[reportAny]
    except KeyboardInterrupt:
        return None


def shown(text: str, style: str = "") -> Text:
    """
    Make ``text`` into a table cell or a tree label that shows it as it is.

    It is never read as markup, and a control character in it, such as the
    escape that starts a terminal command, is shown as its escape sequence.
    """
    return Text(_visible(text), style=style)


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
