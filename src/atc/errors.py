"""Errors raised while loading, resolving, and merging config units."""


class ConfigError(Exception):
    """
    Base class for every problem with the configuration itself.

    Callers may catch this alone to report any user-facing failure.
    """


class UnitError(ConfigError):
    """A unit is unreadable, is not valid JSON, or violates the envelope."""


class PathError(ConfigError):
    """A unit reference is malformed, missing, or points outside the root."""


class CycleError(ConfigError):
    """The imports of a set of units form a cycle."""


class MergeError(ConfigError):
    """Two units disagree on the type of the same setting."""


class SourceError(ConfigError):
    """A config source is missing, ambiguous, badly named, or not trusted."""


class GitError(ConfigError):
    """A git command failed, or git could not be run."""
