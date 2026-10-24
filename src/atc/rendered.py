"""Templated assets, and the copies of them that ``atc sync`` renders for an account."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from atc.context import BuildContext
from atc.errors import ConfigError
from atc.refs import Located
from atc.registry import Account
from atc.state import SyncState
from atc.template import expand_text
from atc.variables import layer_variables, with_source

_TEMPLATE = re.compile(r"(?P<stem>.+)\.in(?P<suffix>\.[^.]+)?")


def rendered_name(name: str) -> str | None:
    """
    Name the rendered copy of the asset file called ``name``.

    A template is named ``<stem>.in.<ext>`` or ``<stem>.in``, and its copy
    ``<stem>.<ext>`` or ``<stem>``. ``None`` means ``name`` is not a template.
    """
    match = _TEMPLATE.fullmatch(name)
    if match is None or match["stem"] in {".", ".."}:
        return None
    return match["stem"] + (match["suffix"] or "")


@dataclass(frozen=True, slots=True)
class Rendering:
    """
    One template, rendered for an account.

    ``path`` is where the copy goes and ``text`` what it holds. ``mode`` is
    0o700 when the template is executable by anyone, else 0o600.
    """

    template: Located
    path: Path
    text: str
    mode: int


def render_templates(ctx: BuildContext, account: Account) -> tuple[Rendering, ...]:
    """
    Render every templated asset of every source for ``account``, writing nothing.

    A template is a file under a source's ``assets`` named as
    :func:`rendered_name` says. Its references are expanded with the variables
    of ``account``'s layer and of the template's own source, never a project's.
    Its copy goes to the same place under the source's directory of rendered
    assets, which sessions cannot write to.

    Raises:
        ConfigError: a template cannot be read or is not UTF-8 text, or two
            templates need the same path, as a file or as a directory.
        TemplateError: a reference in a template cannot be expanded.

    """
    state = SyncState(ctx.dirs.state_dir, account.name)
    variables = layer_variables(ctx, account)
    renderings: list[Rendering] = []
    for source in ctx.sources.items:
        own = with_source(variables, source, state.rendered(source))
        for path in sorted(source.assets.rglob("*")):
            name = rendered_name(path.name)
            if name is None or not path.is_file():
                continue
            relative = path.relative_to(source.assets)
            display = source.display(relative.as_posix())
            try:
                content = path.read_bytes().decode("utf-8")
                executable = path.stat().st_mode & 0o111
            except OSError as exc:
                raise ConfigError(f"{display}: cannot be read: {exc}") from exc
            except UnicodeDecodeError as exc:
                message = f"{display}: a template must be UTF-8 text"
                raise ConfigError(message) from exc
            renderings.append(
                Rendering(
                    template=Located(source, path.resolve(), display),
                    path=state.rendered(source) / relative.with_name(name),
                    text=expand_text(
                        content, own, origin=f"{display} ({account.name})"
                    ),
                    mode=0o700 if executable else 0o600,
                )
            )
    _check_distinct(renderings)
    return tuple(renderings)


def rendered_copy(ctx: BuildContext, account: Account, asset: Located) -> Path | None:
    """
    Give where :func:`render_templates` puts the copy of ``asset`` for ``account``.

    ``None`` means ``asset`` is not a template, so it has no copy.
    """
    name = rendered_name(asset.path.name)
    if name is None:
        return None
    relative = asset.path.relative_to(asset.source.assets.resolve())
    rendered = SyncState(ctx.dirs.state_dir, account.name).rendered(asset.source)
    return rendered / relative.with_name(name)


def _check_distinct(renderings: Sequence[Rendering]) -> None:
    """
    Refuse copies that would be one file, or a file where another needs a directory.

    ``a.in.sh`` and ``a.sh.in`` both render as ``a.sh``, and ``bin.in`` as a file
    where ``bin/x.in.sh`` needs a directory.

    Raises:
        ConfigError: two of ``renderings`` need the same path.

    """
    by_path: dict[Path, Rendering] = {}
    for rendering in renderings:
        other = by_path.setdefault(rendering.path, rendering)
        if other is not rendering:
            first, second = other.template.display, rendering.template.display
            message = f"{first} and {second} both render as {rendering.path}"
            raise ConfigError(message)
    for rendering in renderings:
        for parent in rendering.path.parents:
            other = by_path.get(parent)
            if other is not None:
                message = (
                    f"{other.template.display} renders as {parent}, where "
                    f"{rendering.template.display} needs a directory"
                )
                raise ConfigError(message)
