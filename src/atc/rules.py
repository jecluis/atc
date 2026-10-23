"""Permission rules that another rule always overrides."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from atc.layers import Layer

type DeadList = Literal["allow", "ask"]
type CoverList = Literal["ask", "deny"]

_COVERED_BY: Mapping[DeadList, tuple[CoverList, ...]] = {
    "allow": ("deny", "ask"),
    "ask": ("deny",),
}
"""For each list, the lists whose rules override it, strongest first."""

_RULE = re.compile(r"(?P<tool>[\w.-]+)(?:\((?P<spec>.*)\))?", re.DOTALL)


@dataclass(frozen=True, slots=True)
class RuleWarning:
    """
    A rule that never applies, because another rule takes precedence.

    ``dead_rule`` is in the list ``dead_list``, and ``by_rule`` in ``by_list``
    covers it. Each ``*_origins`` names the units that gave the rule.
    """

    dead_list: DeadList
    dead_rule: str
    dead_origins: tuple[str, ...]
    by_list: CoverList
    by_rule: str
    by_origins: tuple[str, ...]

    def message(self) -> str:
        """Describe the warning, naming both rules and the units that gave them."""
        dead = f"{self.dead_list} {self.dead_rule} ({', '.join(self.dead_origins)})"
        by = f"{self.by_list} {self.by_rule} ({', '.join(self.by_origins)})"
        return f"{dead} never applies: {by} takes precedence"


def covers(by: str, rule: str) -> bool:
    """
    Say whether ``by`` matches everything that ``rule`` matches.

    The test is conservative: it may miss a cover, but it never reports one that
    does not hold. A rule is ``Tool`` or ``Tool(spec)``, and anything else
    neither covers nor is covered. A bare tool covers every rule for that tool,
    and equal rules cover each other. For ``Bash``, ``by`` covers ``rule`` when
    ``by``'s spec, with ``*`` as a wildcard, matches the text of ``rule``'s spec
    read literally. A trailing ``:*`` counts as `` *`` in both. Other tools are
    compared only for equal text.
    """
    covering = _RULE.fullmatch(by)
    covered = _RULE.fullmatch(rule)
    if covering is None or covered is None:
        return False
    tool: str = covering["tool"]
    if tool != covered["tool"]:
        return False
    by_spec: str | None = covering["spec"]
    rule_spec: str | None = covered["spec"]
    if by_spec is None:
        return True
    if rule_spec is None:
        return False
    if by_spec == rule_spec:
        return True
    if tool != "Bash":
        return False
    pattern = ".*".join(re.escape(part) for part in _bash_spec(by_spec).split("*"))
    return re.fullmatch(pattern, _bash_spec(rule_spec), re.DOTALL) is not None


def dead_rules(layers: Sequence[Layer]) -> tuple[RuleWarning, ...]:
    """
    Find the rules in ``layers`` that can never apply.

    Claude Code applies deny rules before ask rules, and those before allow
    rules, whichever settings file each comes from. So an ``allow`` rule is dead
    when a ``deny`` or an ``ask`` rule covers it, and an ``ask`` rule when a
    ``deny`` rule does. ``layers`` are taken as one, in order. A dead rule gets
    one warning, naming the strongest rule that covers it: a ``deny`` over an
    ``ask``, then the earliest in merge order.
    """
    rules = _group(layers)
    warnings: list[RuleWarning] = []
    for dead_list, by_lists in _COVERED_BY.items():
        for rule, origins in rules.get(dead_list, {}).items():
            for by_list in by_lists:
                by_rules = rules.get(by_list, {})
                by_rule = next((r for r in by_rules if covers(r, rule)), None)
                if by_rule is not None:
                    warning = RuleWarning(
                        dead_list, rule, origins, by_list, by_rule, by_rules[by_rule]
                    )
                    warnings.append(warning)
                    break
    return tuple(warnings)


def _group(layers: Sequence[Layer]) -> dict[str, dict[str, tuple[str, ...]]]:
    """Collect the rules of every layer: list, then rule, then the units."""
    grouped: dict[str, dict[str, tuple[str, ...]]] = {}
    for layer in layers:
        for (name, rule), units in layer.origins.items():
            rules = grouped.setdefault(name, {})
            rules[rule] = tuple(dict.fromkeys((*rules.get(rule, ()), *units)))
    return grouped


def _bash_spec(spec: str) -> str:
    """Spell a trailing ``:*`` as `` *``, which Claude Code reads the same way."""
    return f"{spec.removesuffix(':*')} *" if spec.endswith(":*") else spec
