"""The prompt adapter -- the single choke point every interactive question passes through.

Ports ``packages/cli/src/utils/cli-utilities.ts`` and ``askWithEditor`` (research doc 03 §2), but
only as a **protocol**: the concrete implementation is still owed, because the grouped multiselect
it has to reproduce (§11.2) is command work, not shell work. This paragraph used to name the
``add``-flow change as its home; that change shipped without it, driving every prompt through an
injected double instead -- see :class:`QuestionaryPrompts`.

What the shell owns is here -- the protocol commands may depend on, the uniform cancellation
contract and its :data:`CANCEL` sentinel, and the ``--non-interactive`` choke point.

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
    "CANCEL",
    "Prompts",
    "QuestionaryPrompts",
    "cancelable",
    "is_cancel",
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


class _Cancel:
    """Sentinel type for :data:`CANCEL`.

    Falsy on purpose. A command that forgets the explicit check and reaches for truthiness still
    treats a cancellation as "no answer" rather than as a real, empty one -- the wrong outcome, but
    the *safe* wrong outcome.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "CANCEL"

    def __bool__(self) -> bool:
        return False


#: The value a :class:`Prompts` implementation returns when the user aborted the question.
#:
#: ``questionary`` reports a cancellation by returning ``None``, so ``None`` stays accepted
#: (:func:`is_cancel`); this sentinel exists because a prompt whose *legitimate* answer is ``None``
#: -- or a test double scripting a cancellation -- needs something unambiguous to hand back.
CANCEL: Final = _Cancel()


def is_cancel(value: Any) -> bool:
    """Whether ``value`` means "the user aborted this prompt".

    The one predicate every prompt result goes through, so "was this cancelled?" cannot be spelled
    two ways in two commands. Both accepted forms are here: ``questionary``'s ``None`` and molt's
    own :data:`CANCEL`.
    """
    return value is None or isinstance(value, _Cancel)


def cancelable(value: Any) -> Any:
    """Pass ``value`` through, or report a cancellation and exit 0.

    ``questionary`` signals "the user hit Ctrl-C" by returning ``None``, so without a shared helper
    every call site would have to remember the check -- and the one that forgot would treat a
    cancellation as an empty answer. Raising :class:`typer.Exit` rather than calling ``sys.exit``
    keeps the funnel in ``molt.cli.main`` in charge of the actual process exit.

    A command that has cleanup of its own to do -- ``molt add`` must report "Canceled" and leave
    ``.changeset/`` untouched -- tests :func:`is_cancel` directly instead, which is the same
    contract without the control flow.
    """
    if is_cancel(value):
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

    Registered here so the seam has a named home and the import graph is settled.

    **The bodies did not land with the ``add``-flow change, contrary to what this docstring used to
    predict.** That change drives every prompt through an injected double, so no conformance row
    reaches these methods and molt still has no prompt library as a dependency. What is owed is the
    concrete implementation plus the grouped multiselect (research doc 03 §2, §11.2), which is the
    only genuinely hard prompt in molt. Until then a fully interactive ``molt add`` -- one with no
    selection flags, no ``--stdin`` and no ``--empty`` -- raises from here; every non-interactive
    surface works.

    Every method must route its result through :func:`cancelable` (or :func:`is_cancel`) and
    consult :func:`is_non_interactive` first.
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
