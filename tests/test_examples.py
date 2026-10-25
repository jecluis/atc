"""The example config source and machine registry that atc ships."""

from pathlib import Path

from atc.validate import validate_config
from tests.conftest import ContextBuilder

EXAMPLES = Path(__file__).parents[1] / "examples"


def _source() -> dict[str, str]:
    """Give the example source's files, as the files of a source."""
    return {
        path.relative_to(EXAMPLES).as_posix(): path.read_text(encoding="utf-8")
        for top in ("conf", "assets")
        for path in sorted((EXAMPLES / top).rglob("*"))
        if path.is_file()
    }


def test_the_examples_check_without_errors_or_warnings(
    build_context: ContextBuilder,
) -> None:
    registry = (EXAMPLES / "machine.yaml").read_text(encoding="utf-8")
    ctx = build_context(_source(), registry)

    result = validate_config(ctx, lax=False)

    assert result.errors == ()
    assert result.warnings == ()
    assert result.units == len(list((EXAMPLES / "conf").rglob("*.json")))
