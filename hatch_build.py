"""
Record the commit a build comes from, for ``atc --version``.

An installed atc has no repository to ask, and asking git at run time would
describe whatever repository the user is in, so a build leaves the commit in a
file in the package.
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, override

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

COMMIT_FILE = "COMMIT"
_DESTINATIONS = {
    "wheel": f"atc/{COMMIT_FILE}",
    # A wheel built from this sdist has no repository, and takes the file from
    # the package directory like any other.
    "sdist": f"src/atc/{COMMIT_FILE}",
}


def commit_of(root: Path) -> str | None:
    """
    Name the commit that ``root`` holds, with ``-dirty`` if a tracked file differs.

    ``None`` means it cannot be told. ``root`` must be the top of a repository
    itself: an unpacked sdist inside some other repository is not that commit.
    """
    if not (root / ".git").exists():
        return None
    # --exclude keeps a tag from standing in for the commit.
    command = ["git", "describe", "--always", "--dirty", "--abbrev=12", "--exclude=*"]
    # A build started from a git hook has GIT_DIR set, for the other repository.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        done = subprocess.run(  # noqa: S603
            command, cwd=root, env=env, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout.strip() or None


class CommitHook(BuildHookInterface):
    """Adds the file that names the commit to wheels and sdists."""

    @override
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        self._recorded: Path | None = None
        # An editable install runs the checkout itself, and the next commit
        # would make the file wrong.
        commit = None if version == "editable" else commit_of(Path(self.root))
        destination = _DESTINATIONS.get(self.target_name)
        if commit is None or destination is None:
            return
        with tempfile.NamedTemporaryFile(
            "w", encoding="ascii", suffix=f".{COMMIT_FILE}", delete=False
        ) as file:
            _ = file.write(f"{commit}\n")
        self._recorded = Path(file.name)
        build_data["force_include"][file.name] = destination

    @override
    def finalize(
        self, version: str, build_data: dict[str, Any], artifact_path: str
    ) -> None:
        if self._recorded is not None:
            self._recorded.unlink(missing_ok=True)
