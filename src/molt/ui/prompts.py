"""The prompt adapter -- the single choke point every interactive question passes through.

Ports ``packages/cli/src/utils/cli-utilities.ts`` and ``askWithEditor`` (research doc 03 section 2),
as a **protocol plus the one implementation behind it**. The protocol is what command code depends
on; :class:`QuestionaryPrompts` is the only module in molt that names the prompt library, which is
what keeps that library swappable in one file (``tech-stack.md`` section 5, "cheap insurance -- do
it from day one").

What the shell owns is here -- the protocol commands may depend on, the uniform cancellation
contract and its :data:`CANCEL` sentinel, the ``--non-interactive`` choke point, and the
terminal probe that keeps a headless run from blocking.

Method names are molt's (``multiselect``/``select``/``confirm``/``text``/``editor``), not upstream's
``askMultiselect``/``askList``/``askQuestion``/``askConfirm``: the ``ask`` prefix restates what a
prompt object already is.

Two things every implementation inherits by construction:

* :func:`cancelable` -- ``questionary`` reports a cancelled prompt by *returning* ``None`` rather
  than raising, so one helper turns that sentinel into "Canceled" plus a clean **exit 0**, the same
  way for every prompt type (``cli-utilities.ts:14-21``; exit 0 is a deliberate, documented
  divergence from POSIX 130 -- research doc 03 section 11.6).
* :func:`non_interactive_answer` -- with ``--non-interactive``/``--yes`` set, a prompt resolves to
  its documented default or fails naming the missing input. It never blocks. molt-NEW: the JS CLI
  has no ``--yes`` at all, which is why a headless ``changeset add`` hangs (research doc 03 sections
  9.3 and 11.7 item 7).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Final, Protocol

import typer

from molt.errors import MoltError
from molt.ui.console import console

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

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


# ======================================================================================
# The terminal probe -- the second way a prompt can be impossible (design D5)
# ======================================================================================


def _stdin_is_a_terminal() -> bool:
    """Whether standard input is a terminal molt could draw a prompt on.

    A **named module-level function** on purpose, and it is load-bearing for the whole test suite:
    under pytest ``sys.stdin`` is ``DontReadFromInput``, whose ``isatty()`` is ``False``, so
    without a monkeypatchable probe every row that drives this adapter for real would take the
    refusal path below and no end-to-end row could exist (design D5, seam note). ``sys.stdin`` is
    read at call time rather than bound at import for the same reason.

    Defensive on purpose too: a replaced stream with no ``isatty`` is treated as "not a terminal",
    because refusing to prompt is the recoverable answer and blocking is not.
    """
    stream = getattr(sys, "stdin", None)
    probe = getattr(stream, "isatty", None)
    if probe is None:
        return False
    try:
        return bool(probe())
    except Exception:
        return False


#: The refusal for a run with no terminal (design D5, molt-NEW). Deliberately **not**
#: :func:`non_interactive_answer`'s message: naming ``--non-interactive`` here would tell a user
#: that a flag was given when it was not, which sends them looking through their own script for a
#: flag that is not there. The two cases are distinguishable by design.
#:
#: The word ``--non-interactive`` must not appear in this text; a conformance row asserts it.
_NO_TERMINAL: Final = (
    "molt cannot prompt: standard input is not a terminal, and this run needs an answer to: "
    "{message}. Supply it as a command-line flag, or run molt from a terminal."
)


class _Ask:
    """Sentinel type for :data:`_ASK` -- "no shortcut applied; go ahead and ask"."""

    __slots__ = ()


_ASK: Final = _Ask()


def _answer_without_asking(message: str, kwargs: Mapping[str, Any]) -> Any:
    """Resolve, refuse, or hand back :data:`_ASK` so the caller may build a real prompt.

    The two ways a prompt can be impossible, in the order design D5 fixes:

    1. ``--non-interactive`` was given -- the caller's documented ``default=`` answers the
       question, and without one the run stops naming the flag it was given.
    2. Standard input is not a terminal -- the run stops with a **different** message.

    **The order matters and is pinned by a conformance row.** Probing the terminal first would
    blame the terminal in a run that explicitly passed the flag, which is the wrong thing to send
    a user looking for.

    Both refusals raise :class:`molt.errors.MoltError`, so ``molt.cli.main``'s funnel prints them
    without a traceback and exits 1. Neither is a cancellation: the user did not choose to abort,
    molt could not ask.

    There is a **third** path to the same refusal, and it is not part of D5's ordering: a terminal
    that passes the probe and still cannot host a prompt. See :func:`_terminal_failures`.
    """
    if is_non_interactive():
        if "default" in kwargs:
            return non_interactive_answer(message, default=kwargs["default"])
        return non_interactive_answer(message)
    if not _stdin_is_a_terminal():
        raise MoltError(_NO_TERMINAL.format(message=message))
    return _ASK


# ======================================================================================
# The concrete implementation
# ======================================================================================

#: Every literal molt itself hands to the prompt library, collected in one place so a conformance
#: row can iterate it (design D6). All ASCII, on every platform, with **no** branch on the detected
#: encoding: a capability sniff would make the flagship flow render one way in CI and another on a
#: developer's machine, and the branch itself would be the untested path.
#:
#: The library's own defaults are not all ASCII -- ``questionary.constants``' selected pointer is
#: ``U+00BB`` -- which is exactly why ``pointer`` is supplied rather than left alone. What molt
#: cannot reach is recorded as gap ``IA-9``: the checkbox indicators
#: (``INDICATOR_SELECTED``/``INDICATOR_UNSELECTED``, ``U+25CF``/``U+25CB``) are hardcoded inside the
#: pinned version and take no parameter.
_ASCII_CHROME: Final[dict[str, str]] = {
    # `questionary`'s `qmark`: the marker drawn in front of every question.
    "qmark": "?",
    # `questionary`'s `pointer`: the cursor drawn beside the highlighted row.
    "pointer": ">",
    # molt's own two-space indent in front of a grouped multiselect's members (design D4).
    "member_indent": "  ",
    # Passed to `Question.ask(kbi_msg=...)`: the library prints its own "Cancelled by user" line by
    # default, and the single "Canceled" line has to come from molt's console in molt's voice.
    "kbi_msg": "",
    # Written into the editor buffer and removed from the result by exact prefix match (design D7).
    # An HTML comment, not a `#` line: molt does **not** strip `#` lines (upstream's
    # `askWithEditor.ts:31` bug, research doc 03 section 10.6), so if the exact-prefix strip ever
    # fails to match, a stray HTML comment is invisible in a rendered changelog where a stray `#`
    # line would become a heading.
    "editor_preamble": (
        "<!-- Write the changeset summary below, then save and close this file.\n"
        "     molt removes this comment and keeps everything else exactly as typed,\n"
        "     Markdown headings included. -->\n\n"
    ),
}

#: Refusal text for a ``required=True`` multiselect submitted empty. The library keeps the question
#: open and shows this, which is what "an empty selection cannot be submitted" means in practice.
_NOTHING_SELECTED: Final = "Select at least one entry, or press Ctrl-C to cancel."


class _GroupToken:
    """The value of a grouped multiselect's header row (design D4).

    An **object**, never the label string: a uv workspace really can contain a package named
    exactly like a group, and a string token would make "the user picked the group" and "the user
    picked the package called ``packages``" the same answer. Identity settles it.
    """

    __slots__ = ("label",)

    def __init__(self, label: str) -> None:
        self.label = label

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"_GroupToken({self.label!r})"


def _is_group(entry: Any) -> bool:
    """Whether ``entry`` is a ``(group label, members)`` pair rather than a plain choice.

    Structural, because that is the shape ``molt/commands/add.py::_monorepo_flow`` already passes:
    the package question supplies an ordered sequence of pairs and the major/minor questions supply
    a flat list of names. A ``str`` second element is excluded deliberately -- a two-character
    string is a two-element sequence.
    """
    return (
        isinstance(entry, tuple | list)
        and len(entry) == 2
        and isinstance(entry[0], str)
        and isinstance(entry[1], tuple | list)
    )


def _contains(pool: Sequence[Any], value: Any) -> bool:
    """``value in pool`` without requiring the values to be hashable.

    Choice values are the caller's own objects; ``add`` passes strings today but the protocol
    promises nothing, and a ``set`` would refuse the first unhashable one.
    """
    return any(item is value or item == value for item in pool)


def _expand(answer: Sequence[Any], members: Mapping[str, list[Any]]) -> list[Any]:
    """Replace every group token with its members, drop duplicates, keep first-seen order.

    The caller always receives a flat sequence of the values it supplied. A group label must never
    reach it: ``molt/commands/add.py::_ask_multiselect`` runs ``normalize_name(str(value))`` over
    the answer and would silently drop anything that is not one of its own package names.
    """
    picked: list[Any] = []
    for value in answer:
        expanded = members.get(value.label, []) if isinstance(value, _GroupToken) else [value]
        for item in expanded:
            if not _contains(picked, item):
                picked.append(item)
    return picked


def _at_least_one(selected: Sequence[Any]) -> bool | str:
    """``required=True``'s validator: refuse an empty submission, accept any non-empty one.

    A module-level function rather than a lambda so a mutation to it is visible and so the refusal
    text is written down once.
    """
    return True if selected else _NOTHING_SELECTED


def _terminal_failures() -> tuple[type[BaseException], ...]:
    """The exception types that mean "this process has no terminal to draw a prompt on".

    A **backstop for the probe**, and it is not theoretical: on Windows ``isatty()`` reports
    ``True`` for the ``NUL`` device, because ``NUL`` is a character device -- so
    ``molt add < NUL``, the documented way to prove a headless run does not block, sails past
    :func:`_stdin_is_a_terminal` and reaches the terminal framework, which then fails deep inside
    itself. Without this the user gets an internal-error traceback instead of the one sentence
    design D5 promises.

    Both members mean exactly the same thing, one per platform: the framework raises
    ``NoConsoleScreenBufferError`` when Windows hands it no console screen buffer, and
    ``io.UnsupportedOperation`` when a POSIX terminal is required and standard input is not one.
    The tuple is deliberately these two and nothing wider -- a blanket ``except Exception`` around
    a prompt would turn every real defect into "molt cannot prompt".

    Resolved at call time rather than at import: the Windows class lives in a module that imports
    ``ctypes.wintypes`` and cannot be imported at all on POSIX.
    """
    import io

    failures: list[type[BaseException]] = [io.UnsupportedOperation]
    try:
        from prompt_toolkit.output.win32 import NoConsoleScreenBufferError
    except Exception:  # pragma: no cover - the POSIX half; the import itself is the branch
        pass
    else:
        failures.append(NoConsoleScreenBufferError)
    return tuple(failures)


def _ask(message: str, build: Callable[[], Any]) -> Any:
    """Build a question and drive it through ``.ask()``, the one place either happens.

    ``build`` is deferred rather than a ready-made question because the terminal failures above
    are raised during *construction* as often as during the ask -- the framework opens the console
    when it lays the prompt out.
    """
    try:
        return build().ask(kbi_msg=_ASCII_CHROME["kbi_msg"])
    except _terminal_failures() as exc:
        raise MoltError(_NO_TERMINAL.format(message=message)) from exc


class QuestionaryPrompts:
    """The concrete :class:`Prompts` implementation, backed by ``questionary``.

    This is the only module in molt that names the prompt library (``terminal-ui`` spec, "The
    prompt library SHALL be named in exactly one module"), and
    ``tests/cli/test_cli.py::test_commands_depend_on_the_prompts_protocol_not_the_library`` greps
    every command module to keep it that way. Ports the six ``clack`` wrappers of
    ``cli-utilities.ts:29-91`` (research doc 03 section 2.1) plus ``askWithEditor``; the grouped
    multiselect has no library equivalent and is adapted per design D4.

    Three rules hold for every method here:

    * **The library is imported inside the method body, never at module scope.**
      ``src/molt/cli.py:43`` imports ``molt.ui.prompts`` at module scope, so a header-level
      ``import questionary`` would put ``prompt_toolkit`` -- a large package with a deep import
      tree -- on the critical path of ``molt --help``, of shell completion, and of every command
      that never prompts. Do not "tidy" these imports into the header (design D2).
    * **Every question is driven through** ``Question.ask()``, **never** ``unsafe_ask()``, with an
      empty ``kbi_msg``. ``.ask()`` is what turns Ctrl-C into ``None``, which :func:`is_cancel`
      already accepts; ``unsafe_ask()`` would push a raw ``KeyboardInterrupt`` past ``molt add``'s
      "leave ``.changeset/`` untouched" cleanup and into a second exit path (design D3).
    * **A cancellation is returned verbatim.** The adapter never converts, wraps or swallows it.
    """

    def multiselect(self, message: str, choices: Sequence[Any] = (), **kwargs: Any) -> Any:
        """Pick zero or more of ``choices``, flat or grouped (design D4).

        ``choices`` is either a flat sequence of values or an ordered sequence of
        ``(group label, members)`` pairs. A group renders as a selectable header row above its
        two-space-indented members, and selecting the header is selecting the whole group -- the
        value of upstream's ``groupMultiselect`` with ``selectableGroups: true``
        (``cli-utilities.ts:36``) without reaching into the terminal framework's internals.

        The accepted cost, recorded as gap ``IA-1``: the header expands on **submit**, so it does
        not flip its members' checkboxes as you move and "select the group, then deselect one
        member" is not expressible.
        """
        resolved = _answer_without_asking(message, kwargs)
        if resolved is not _ASK:
            return resolved

        # Imported here, not at module scope -- see the class docstring and design D2.
        import questionary

        entries: list[Any] = []
        members: dict[str, list[Any]] = {}
        for entry in choices or ():
            if not _is_group(entry):
                entries.append(questionary.Choice(title=str(entry), value=entry))
                continue
            label, group = str(entry[0]), list(entry[1])
            members[label] = group
            entries.append(questionary.Choice(title=label, value=_GroupToken(label)))
            entries.extend(
                questionary.Choice(title=_ASCII_CHROME["member_indent"] + str(value), value=value)
                for value in group
            )

        builder: dict[str, Any] = {
            "qmark": _ASCII_CHROME["qmark"],
            "pointer": _ASCII_CHROME["pointer"],
        }
        if kwargs.get("required"):
            # The library's own hook, so the question stays *open* rather than returning empty.
            # A selected header counts, because it expands to members.
            builder["validate"] = _at_least_one
        answer = _ask(message, lambda: questionary.checkbox(message, entries, **builder))
        if is_cancel(answer):
            return answer
        return _expand(answer, members)

    def select(self, message: str, choices: Sequence[Any] = (), **kwargs: Any) -> Any:
        """Pick exactly one of ``choices`` (``askList``, ``cli-utilities.ts:52-64``)."""
        resolved = _answer_without_asking(message, kwargs)
        if resolved is not _ASK:
            return resolved

        import questionary

        builder: dict[str, Any] = {
            "qmark": _ASCII_CHROME["qmark"],
            "pointer": _ASCII_CHROME["pointer"],
        }
        if "default" in kwargs:
            # The library's `default` is the initially highlighted row, which is the same thing
            # molt's documented default means here: what pressing enter gives you.
            builder["default"] = kwargs["default"]
        return _ask(message, lambda: questionary.select(message, list(choices or ()), **builder))

    def confirm(self, message: str, **kwargs: Any) -> Any:
        """A yes/no question, defaulting to **yes**.

        Port of ``askConfirm``'s ``initialValue = true`` (``cli-utilities.ts:67``) -- the one
        behavioral default this adapter carries, and what makes ``molt add``'s first-major
        confirmation behave as upstream's does when the user just presses enter.
        """
        resolved = _answer_without_asking(message, kwargs)
        if resolved is not _ASK:
            return resolved

        import questionary

        return _ask(
            message,
            lambda: questionary.confirm(
                message,
                default=bool(kwargs.get("default", True)),
                qmark=_ASCII_CHROME["qmark"],
            ),
        )

    def text(self, message: str, **kwargs: Any) -> Any:
        """A free-text line, with an optional placeholder and validator.

        molt's ``placeholder=`` maps onto the pinned version's ``instruction=``, which is the
        parameter ``questionary`` 2.1.1 renders beside the question; it has no ``placeholder``
        of its own (checked against the installed signature, not assumed).
        """
        resolved = _answer_without_asking(message, kwargs)
        if resolved is not _ASK:
            return resolved

        import questionary

        builder: dict[str, Any] = {"qmark": _ASCII_CHROME["qmark"]}
        if "default" in kwargs:
            builder["default"] = str(kwargs["default"])
        if kwargs.get("validate") is not None:
            builder["validate"] = kwargs["validate"]
        if kwargs.get("placeholder") is not None:
            builder["instruction"] = str(kwargs["placeholder"])
        return _ask(message, lambda: questionary.text(message, **builder))

    def editor(self, message: str = "", **kwargs: Any) -> Any:
        """Open the user's editor: capture a summary, or open an existing file (design D7).

        ``click.edit`` resolves ``$VISUAL``/``$EDITOR``, falls back per platform, round-trips the
        temp file and blocks until the editor exits -- the whole matrix research doc 03 section 11.2
        recommends it for, and the whole matrix molt would otherwise re-implement and under-test on
        Windows. The ``.md`` extension is deliberate: an editor that highlights by extension makes
        a changeset summary readable.

        With ``file=<path>`` the named file is opened and **nothing is captured**, which is
        ``molt add --open``. It returns ``None`` because there is no answer to give; the one caller
        discards the value. Blocking until the editor exits is a divergence from upstream's
        fire-and-forget ``launchEditor`` and is harmless where it is called -- last, after the
        changeset is on disk and reported (gap ``IA-3``).

        Without it, molt's guidance preamble is written into the buffer and removed from the result
        by **exact prefix match only**. No regex runs over the text: upstream's
        ``replace(/^#.*\\n?/gm, "")`` deletes every Markdown heading the author typed (research doc
        03 section 10.6) and molt does not port that. An editor that saved nothing leaves the
        preamble untouched, so the strip yields ``""`` and ``molt/commands/add.py::_try_editor``
        falls into its existing retry prompt.

        No "Opening external editor... Continue/Cancel" confirmation and no exception handling of
        its own: ``click.edit`` blocks, so the confirm would guard nothing, and ``_try_editor``
        already degrades to the console prompt for every failure except :class:`AssertionError`,
        which it re-raises on purpose.
        """
        resolved = _answer_without_asking(message, kwargs)
        if resolved is not _ASK:
            return resolved

        import click

        target = kwargs.get("file")
        if target is not None:
            click.edit(filename=str(target))
            return None

        preamble = _ASCII_CHROME["editor_preamble"]
        edited = click.edit(text=preamble, extension=".md", require_save=False)
        if edited is None:
            return ""
        if edited.startswith(preamble):
            edited = edited[len(preamble) :]
        return edited
