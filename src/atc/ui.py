"""Terminal output: data on standard output, diagnostics on standard error."""

from dataclasses import dataclass
from typing import Self

from rich.console import Console
from rich.json import JSON
from rich.table import Table
from rich.text import Text

from atc.jsonval import JsonValue, dumps


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

    def table(self, table: Table) -> None:
        """Write ``table`` on standard output."""
        self.out.print(table)

    def _diagnostic(self, prefix: str, style: str, message: str) -> None:
        # Text takes the message as it is; a string given to print would be
        # parsed as markup, and a path or branch name can look like markup.
        line = Text.assemble((prefix, style), " ", message)
        self.err.print(line, soft_wrap=True)
