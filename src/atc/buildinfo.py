"""Which commit this copy of atc was built from."""

import re
from pathlib import Path

COMMIT_FILE = "COMMIT"
"""The file that a build leaves in the package, naming the commit it came from."""

_PACKAGE_DIR = Path(__file__).parent
_COMMIT = re.compile(r"[0-9a-f]+(-dirty)?")


def built_commit(directory: Path = _PACKAGE_DIR) -> str | None:
    """
    Give the commit that the package in ``directory`` was built from.

    ``None`` means no build recorded one, as for an editable install or a build
    from outside a repository, or that the file holds anything else: it goes
    into click's message template and onto the terminal. A commit that ends in
    ``-dirty`` had tracked files changed when it was built.
    """
    try:
        text = (directory / COMMIT_FILE).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    commit = text.strip()
    return commit if _COMMIT.fullmatch(commit) else None


def version_message(directory: Path = _PACKAGE_DIR) -> str:
    """Write the ``--version`` line, as a template for click to fill in."""
    commit = built_commit(directory) or "commit not recorded"
    return f"%(prog)s, version %(version)s ({commit})"
