"""The layout of atc's own shell scripts, and of its examples'."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPTS = [ROOT / "check-and-fix.sh", *sorted((ROOT / "examples").rglob("*.sh"))]
"""atc's own scripts, and its examples'."""

SHFMT = ("shfmt", "-i", "2", "-ci", "-bn")
"""shfmt with the options every script is formatted with."""

WIDTH = 80

each_script = pytest.mark.parametrize(
    "script", SCRIPTS, ids=[str(path.relative_to(ROOT)) for path in SCRIPTS]
)


def test_there_are_scripts_to_check() -> None:
    assert SCRIPTS


@pytest.mark.skipif(shutil.which("shfmt") is None, reason="shfmt is not installed")
@each_script
def test_the_script_is_formatted_as_shfmt_formats_it(script: Path) -> None:
    result = subprocess.run(  # noqa: S603 - fixed program, no shell
        [*SHFMT, "-d", str(script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@each_script
def test_no_line_of_the_script_is_wider_than_80_columns(script: Path) -> None:
    lines = script.read_text(encoding="utf-8").splitlines()

    wide = [number for number, line in enumerate(lines, 1) if len(line) > WIDTH]

    assert wide == []
