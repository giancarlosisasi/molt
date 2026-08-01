"""A scripted stand-in for the ``questionary`` module, for the prompt-adapter suite.

``src/molt/ui/prompts.py`` is the one module in molt that names the prompt library, so it *is* the
swappable seam and introducing a second seam under it to make it testable would defeat the point
(design D8). The seam this module patches is therefore the library itself: because the adapter
imports ``questionary`` **inside** each method body (design D2), a
``monkeypatch.setattr(questionary, "checkbox", ...)`` reaches every call with no import-order games.

Two doubles live here:

* :class:`FakeQuestionary` -- replaces the four builders (``checkbox`` / ``select`` / ``confirm`` /
  ``text``), records the arguments each was built with, and answers from a per-builder script. Its
  ``Question`` stand-in records the ``kbi_msg`` it was asked with and **fails the test** if anything
  reaches ``unsafe_ask`` (design D3 forbids it).
* :class:`FakeEditor` -- replaces ``click.edit``, records how it was called and answers from a
  script.

Deliberately **not** added to :mod:`tests.cli.fake_cli`: that harness is frozen and shared by every
CLI suite, and nothing else needs this.

No row that uses these may reach the real builders, so nothing here opens a terminal, spawns a
process or touches the filesystem. Import by full dotted path
(``--import-mode=importlib`` does not put test directories on ``sys.path``)::

    from tests.cli.fake_questionary import FakeEditor, FakeQuestionary, pick
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    import pytest

__all__ = [
    "BUILDERS",
    "AskCall",
    "BuilderCall",
    "EditorCall",
    "FakeEditor",
    "FakeQuestion",
    "FakeQuestionary",
    "ScriptExhausted",
    "pick",
]

#: The ``questionary`` builders molt's adapter uses. Every one is replaced together, so a row that
#: expects "no builder was called at all" cannot pass because it patched only three of four.
BUILDERS: Final[tuple[str, ...]] = ("checkbox", "select", "confirm", "text")


class ScriptExhausted(AssertionError):
    """A builder fired that the row did not script an answer for.

    An ``AssertionError`` on purpose, exactly like
    :class:`tests.cli.fake_cli.PromptExhausted`: an unscripted question means the code asked
    something the row did not expect, which is a test failure rather than a product error.
    """


@dataclass(frozen=True)
class BuilderCall:
    """One recorded ``questionary.<builder>(...)`` construction."""

    kind: str
    """``checkbox`` | ``select`` | ``confirm`` | ``text``."""

    message: str
    choices: tuple[Any, ...] | None = None
    kwargs: Mapping[str, Any] = field(default_factory=dict)

    @property
    def titles(self) -> list[str]:
        """The ``title`` of every choice, in display order (raw strings pass through)."""
        return [str(getattr(choice, "title", choice)) for choice in self.choices or ()]

    @property
    def values(self) -> list[Any]:
        """The ``value`` of every choice, in display order."""
        return [getattr(choice, "value", choice) for choice in self.choices or ()]


@dataclass(frozen=True)
class AskCall:
    """One recorded ``Question.ask(...)``."""

    kind: str
    kwargs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EditorCall:
    """One recorded ``click.edit(...)``."""

    kwargs: Mapping[str, Any] = field(default_factory=dict)


class FakeQuestion:
    """What a faked builder returns: a ``Question`` whose ``ask`` yields the scripted answer."""

    def __init__(self, recorder: FakeQuestionary, call: BuilderCall, answer: Any) -> None:
        self._recorder = recorder
        self._call = call
        self._answer = answer

    def ask(self, **kwargs: Any) -> Any:
        self._recorder.asks.append(AskCall(self._call.kind, dict(kwargs)))
        answer = self._answer
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            # Scripting by *title* -- see `pick`. The adapter builds its own choice objects, so a
            # row cannot name the group token it wants selected until the choices exist.
            return answer(self._call.choices or ())
        return answer

    def unsafe_ask(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError(
            "the adapter must drive every question through `Question.ask()`, never "
            "`unsafe_ask()`: `.ask()` is what turns Ctrl-C into None, which `is_cancel` "
            "already accepts (design D3)"
        )


class FakeQuestionary:
    """Records every builder call and answers it from a per-builder script.

    Each keyword takes a sequence of answers consumed in order by the matching builder. An answer
    may be a plain value, an exception instance (raised from ``ask``), or a callable taking the
    built choice sequence and returning the answer -- which is how a row selects a group header it
    could not name in advance (see :func:`pick`).
    """

    def __init__(
        self,
        *,
        checkbox: Sequence[Any] = (),
        select: Sequence[Any] = (),
        confirm: Sequence[Any] = (),
        text: Sequence[Any] = (),
    ) -> None:
        self._script: dict[str, list[Any]] = {
            "checkbox": list(checkbox),
            "select": list(select),
            "confirm": list(confirm),
            "text": list(text),
        }
        self.builders: list[BuilderCall] = []
        self.asks: list[AskCall] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeQuestionary:
        """Replace all four builders on the real ``questionary`` module. Returns ``self``."""
        import questionary

        for kind in BUILDERS:
            monkeypatch.setattr(questionary, kind, self._builder(kind))
        return self

    # -- internals ----------------------------------------------------------------------
    def _builder(self, kind: str) -> Callable[..., FakeQuestion]:
        def build(message: str, choices: Any = None, **kwargs: Any) -> FakeQuestion:
            recorded = BuilderCall(
                kind,
                message,
                tuple(choices) if choices is not None else None,
                dict(kwargs),
            )
            self.builders.append(recorded)
            queue = self._script[kind]
            if not queue:
                raise ScriptExhausted(
                    f"unscripted {kind!r} question: {message!r} "
                    f"(builders so far: {[call.kind for call in self.builders]})"
                )
            return FakeQuestion(self, recorded, queue.pop(0))

        return build

    # -- assertion helpers --------------------------------------------------------------
    def of(self, kind: str) -> list[BuilderCall]:
        """Every recorded call of one builder, in order."""
        return [call for call in self.builders if call.kind == kind]

    @property
    def kinds(self) -> list[str]:
        """The builders in the order they fired -- the flow's shape."""
        return [call.kind for call in self.builders]

    @property
    def remaining(self) -> dict[str, int]:
        """Unconsumed answers per builder; a non-zero count means the flow asked less."""
        return {kind: len(queue) for kind, queue in self._script.items() if queue}


class FakeEditor:
    """A scripted stand-in for ``click.edit``.

    Answers are consumed in order. ``None`` models "the editor was never saved", which is what
    ``click.edit`` returns when it has nothing to hand back.
    """

    def __init__(self, *answers: Any) -> None:
        self._answers: list[Any] = list(answers)
        self.calls: list[EditorCall] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeEditor:
        """Replace ``click.edit``. Returns ``self``."""
        import click

        monkeypatch.setattr(click, "edit", self)
        return self

    def __call__(self, text: Any = None, **kwargs: Any) -> Any:
        recorded = dict(kwargs)
        if text is not None:
            recorded["text"] = text
        self.calls.append(EditorCall(recorded))
        if not self._answers:
            raise ScriptExhausted(f"unscripted editor launch: {recorded!r}")
        return self._answers.pop(0)

    @property
    def buffers(self) -> list[str]:
        """The ``text=`` buffer each launch was handed, in order (absent reads as ``""``)."""
        return [str(call.kwargs.get("text", "")) for call in self.calls]


def pick(*titles: str) -> Callable[[Sequence[Any]], list[Any]]:
    """Script a multiselect answer by the choice **titles** the user clicked, in click order.

    A row cannot name a group header's value in advance -- the adapter mints a private token object
    per group -- and naming it by label would defeat the very thing the token exists to prevent. So
    the answer is written the way a user gives it: the rows they highlighted. The two-space indent
    a group member's title carries is part of the title, which is what makes the indent itself
    pinned rather than assumed.
    """

    def choose(choices: Sequence[Any]) -> list[Any]:
        by_title: dict[str, Any] = {}
        for choice in choices:
            by_title.setdefault(str(getattr(choice, "title", choice)), choice)
        missing = [title for title in titles if title not in by_title]
        assert not missing, f"no choice titled {missing!r}; offered {sorted(by_title)!r}"
        return [getattr(by_title[title], "value", by_title[title]) for title in titles]

    return choose
