"""The prompt adapter -- the single choke point every interactive question passes through.

Ports ``packages/cli/src/utils/cli-utilities.ts`` and ``askWithEditor`` (research doc 03 §2), but
only as a **protocol**: the concrete ``questionary`` implementation lands with the ``add``-flow
change, because the grouped multiselect it has to reproduce (§11.2) is command work, not shell work.
What lands now is everything the shell owns -- the protocol commands may depend on, the uniform
cancellation contract, and the ``--non-interactive`` choke point.

Method names are molt's (``multiselect``/``select``/``confirm``/``text``/``editor``), not upstream's
``askMultiselect``/``askList``/``askQuestion``/``askConfirm``: the ``ask`` prefix restates what a
prompt object already is.

Two things every implementation inherits by construction:

* :func:`cancelable` -- ``questionary`` reports a cancelled prompt by *returning* ``None`` rather
  than raising, so one helper turns that sentinel into "Canceled" plus a clean **exit 0**, the same
  way for every prompt type (``cli-utilities.ts:14-21``; exit 0 is a deliberate, documented
  divergence from POSIX 130 -- research doc 03 §11.6).
* :func:`non_interactive_answer` -- with ``--non-interactive``/``--yes`` set, a prompt resolves to
  its documented default or fails naming the missing input. It never blocks. molt-NEW: the JS CLI
  has no ``--yes`` at all, which is why a headless ``changeset add`` hangs (§9.3, §11.7 item 7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Protocol

import typer

from molt.errors import MoltError
from molt.ui.console import console

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "Prompts",
    "QuestionaryPrompts",
    "cancelable",
    "is_non_interactive",
    "non_interactive_answer",
    "set_non_interactive",
]


class Prompts(Protocol):
    """The interactive surface molt's commands are allowed to depend on.

    Command code calls these methods and never imports ``questionary``; that import lives in the
    one concrete implementation, which is what keeps the prompt library swappable
    (``terminal-ui`` spec, "Commands depend on the protocol, not the library" -- pinned by
    ``tests/cli/test_cli.py``, which greps every ``molt/commands`` module for the name).
    """

    def multiselect(self, message: str, choices: Sequence[Any] = (), **kwargs: Any) -> Any:
        """Pick zero or more of ``choices``. Returns the cancel sentinel when aborted."""
        ...

    def select(self, message: str, choices: Sequence[Any] = (), **kwargs: Any) -> Any:
        """Pick exactly one of ``choices``."""
        ...

    def confirm(self, message: str, **kwargs: Any) -> Any:
        """A yes/no question."""
        ...

    def text(self, message: str, **kwargs: Any) -> Any:
        """A free-text line, with an optional placeholder and validator."""
        ...

    def editor(self, message: str = "", **kwargs: Any) -> Any:
        """Open ``$EDITOR`` for a multi-line answer."""
        ...


# ======================================================================================
# Cancellation -- one contract for every prompt type
# ======================================================================================


def cancelable(value: Any) -> Any:
    """Pass ``value`` through, or report a cancellation and exit 0.

    ``questionary`` signals "the user hit Ctrl-C" by returning ``None``, so without a shared helper
    every call site would have to remember the check -- and the one that forgot would treat a
    cancellation as an empty answer. Raising :class:`typer.Exit` rather than calling ``sys.exit``
    keeps the funnel in ``molt.cli.main`` in charge of the actual process exit.
    """
    if value is None:
        console.info("Canceled")
        raise typer.Exit(0)
    return value


# ======================================================================================
# The --non-interactive / --yes choke point
# ======================================================================================

_non_interactive: bool = False


def set_non_interactive(value: bool) -> None:
    """Record whether this invocation may prompt. Called once per run by the CLI shell."""
    # One process-wide flag, written once at startup by the CLI shell.
    global _non_interactive
    _non_interactive = value


def is_non_interactive() -> bool:
    """True when ``--non-interactive``/``--yes`` was given for this invocation."""
    return _non_interactive


class _Unset:
    """Sentinel type for :data:`_UNSET` -- distinguishes "no default" from a default of ``None``."""

    __slots__ = ()


_UNSET: Final = _Unset()


def non_interactive_answer(message: str, *, default: Any = _UNSET) -> Any:
    """Resolve a prompt without asking, or fail naming the input that is missing.

    The two documented outcomes of ``--non-interactive`` (``cli-shell`` spec, "Non-interactive run
    never blocks on a prompt"): a prompt with a documented default takes it; one without cannot be
    guessed, so the run stops with a message naming the question rather than blocking on a TTY that
    may not exist.
    """
    if isinstance(default, _Unset):
        raise MoltError(
            f"--non-interactive was given but this run needs an answer to: {message}. "
            "Supply it as a flag, or drop --non-interactive."
        )
    return default


class QuestionaryPrompts:
    """The concrete :class:`Prompts` implementation -- **deferred**.

    Registered here so the seam has a named home and the import graph is settled; the bodies land
    with the ``add``-flow change, together with the grouped multiselect (research doc 03 §2,
    §11.2) that is the only genuinely hard prompt in molt. Every method must route its result
    through :func:`cancelable` and consult :func:`is_non_interactive` first.
    """

    def multiselect(self, message: str, choices: Sequence[Any] = (), **kwargs: Any) -> Any:
        raise NotImplementedError(_DEFERRED)

    def select(self, message: str, choices: Sequence[Any] = (), **kwargs: Any) -> Any:
        raise NotImplementedError(_DEFERRED)

    def confirm(self, message: str, **kwargs: Any) -> Any:
        raise NotImplementedError(_DEFERRED)

    def text(self, message: str, **kwargs: Any) -> Any:
        raise NotImplementedError(_DEFERRED)

    def editor(self, message: str = "", **kwargs: Any) -> Any:
        raise NotImplementedError(_DEFERRED)


_DEFERRED: Final = (
    "the questionary prompt implementation lands with the add-flow change; "
    "the Prompts protocol and the cancelable/non-interactive contracts are already in place"
)
