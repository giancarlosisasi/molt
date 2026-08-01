"""Conformance tests for the prompt adapter -- ``molt.ui.prompts.QuestionaryPrompts``.

Ports ``packages/cli/src/utils/cli-utilities.ts`` (the six clack wrappers, research doc 03 section
2.1) and ``askWithEditor.ts``, plus the two rules that are molt's own: a grouped multiselect with
selectable headers (design D4, because the library has no ``groupMultiselect``) and a run that
cannot ask a question never blocking (design D5, molt-NEW -- changesets has no ``--yes`` at all and
a headless ``changeset add`` hangs, research doc 03 sections 9.3 and 11.7 item 7).

**What this module can and cannot see.** Every row asserts what molt handed the library and what it
did with the answer. None of them renders a frame, so whether the header row is visibly
distinguishable, whether the two-space indent survives wrapping and whether the pointer is legible
on a cp1252 console are settled by the hand run recorded in the change's ``tasks.md`` section 7, not
here. That limit is gap ``IA-8``.

The seam is the ``questionary`` module itself (design D8): the adapter *is* the swappable layer, so
a second seam under it would defeat the point. Because the adapter imports the library inside each
method body (design D2), patching the real module reaches every call.
"""

from __future__ import annotations

import importlib
import inspect
import io
import sys
from typing import TYPE_CHECKING, Any, Final

import pytest
from tests.cli.fake_questionary import FakeEditor, FakeQuestionary, pick

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytest.importorskip("questionary", reason="the prompt library lands with implement-interactive-add")

from molt.errors import MoltError
from molt.ui.prompts import (
    _ASCII_CHROME,
    CANCEL,
    Prompts,
    QuestionaryPrompts,
    is_cancel,
    set_non_interactive,
)

pytestmark = pytest.mark.unit


#: The question every parametrized refusal row asks, so the assertion can look for it by name.
QUESTION: Final = "Which packages would you like to include?"

#: The five protocol methods, for the rows that must hold for all of them.
METHODS: Final[tuple[str, ...]] = ("multiselect", "select", "confirm", "text", "editor")

#: A documented default per method, for the "non-interactive resolves without asking" row.
DEFAULTS: Final[dict[str, Any]] = {
    "multiselect": [],
    "select": "patch",
    "confirm": False,
    "text": "",
    "editor": "",
}


def ask(prompts: QuestionaryPrompts, method: str, **kwargs: Any) -> Any:
    """Call one protocol method with a shape appropriate to it, so the rows can parametrize."""
    if method == "multiselect":
        return prompts.multiselect(QUESTION, ["pkg-a", "pkg-b"], **kwargs)
    if method == "select":
        return prompts.select(QUESTION, ["patch", "minor", "major"], **kwargs)
    if method == "confirm":
        return prompts.confirm(QUESTION, **kwargs)
    if method == "text":
        return prompts.text(QUESTION, **kwargs)
    return prompts.editor(QUESTION, **kwargs)


# --------------------------------------------------------------------------------------
# The two guards without which this module is order-dependent garbage
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _interactive_run() -> Iterator[None]:
    """Reset the process-global ``--non-interactive`` flag around every row.

    ``molt.ui.prompts`` keeps it in a module global written once per run by the CLI shell, so any
    earlier test in the session that dispatched through ``molt.cli`` leaves it set and every row
    here would take the non-interactive path.
    """
    set_non_interactive(False)
    yield
    set_non_interactive(False)


@pytest.fixture(autouse=True)
def _has_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the terminal probe report a terminal.

    Under pytest ``sys.stdin`` is ``DontReadFromInput``, whose ``isatty()`` is ``False``, so
    without this every row would take the no-terminal refusal path and nothing could drive a real
    question (design D5, seam note). The refusal rows flip it back themselves.
    """
    monkeypatch.setattr("molt.ui.prompts._stdin_is_a_terminal", lambda: True)


@pytest.fixture
def prompts() -> QuestionaryPrompts:
    return QuestionaryPrompts()


# ======================================================================================
# The grouped multiselect (design D4; adapts createChangeset.ts:41-71 + cli-utilities.ts:36)
# ======================================================================================


def test_a_flat_multiselect_passes_its_values_through(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flat list of choices is offered as-is and the answer comes back unchanged.

    The major and minor questions of ``molt add`` use this shape (``createChangeset.ts:186-233``);
    only the package question is grouped.
    """
    library = FakeQuestionary(checkbox=[pick("pkg-b")]).install(monkeypatch)

    answer = prompts.multiselect(QUESTION, ["pkg-a", "pkg-b", "pkg-c"])

    assert answer == ["pkg-b"]
    call = library.of("checkbox")[0]
    assert call.message == QUESTION
    assert call.titles == ["pkg-a", "pkg-b", "pkg-c"]
    assert call.values == ["pkg-a", "pkg-b", "pkg-c"]


def test_two_groups_render_as_headers_above_indented_members(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Groups keep the order supplied, each label above its own two-space-indented members.

    Upstream draws this with ``@clack/prompts``' ``groupMultiselect``; the pinned library has no
    equivalent, so the adapter emits a selectable header row per group (design D4). The indent is
    molt's own and is ASCII by construction.
    """
    library = FakeQuestionary(checkbox=[[]]).install(monkeypatch)

    prompts.multiselect(
        QUESTION,
        [("changed packages", ["pkg-b"]), ("unchanged packages", ["pkg-a", "pkg-c"])],
    )

    assert library.of("checkbox")[0].titles == [
        "changed packages",
        "  pkg-b",
        "unchanged packages",
        "  pkg-a",
        "  pkg-c",
    ]


def test_selecting_a_group_header_returns_its_members(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A header is selected, its members are returned, and no label reaches the caller.

    ``molt/commands/add.py::_ask_multiselect`` runs ``normalize_name(str(value))`` over the answer,
    so a group label arriving there would be silently dropped rather than loudly refused.
    """
    FakeQuestionary(checkbox=[pick("changed packages")]).install(monkeypatch)

    answer = prompts.multiselect(
        QUESTION,
        [("changed packages", ["pkg-b", "pkg-c"]), ("unchanged packages", ["pkg-a"])],
    )

    assert answer == ["pkg-b", "pkg-c"]


def test_a_header_and_one_of_its_members_returns_each_member_once(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expansion de-duplicates and keeps first-seen order.

    Selecting a group and then one of its members is an ordinary thing to do with a checkbox, and
    the caller must not receive the same package twice -- a changeset naming one package twice is
    what ``molt.apply`` refuses outright (gap ``ARP-3``).
    """
    FakeQuestionary(checkbox=[pick("changed packages", "  pkg-c")]).install(monkeypatch)

    answer = prompts.multiselect(QUESTION, [("changed packages", ["pkg-b", "pkg-c"])])

    assert answer == ["pkg-b", "pkg-c"]


def test_a_choice_named_exactly_like_a_group_stays_distinct(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A group's header value is a private token object, never its label string.

    A uv workspace really can contain a package called ``packages``, and with a string token
    "the user picked the group" and "the user picked that package" would be the same answer.
    """
    choices = [("packages", ["pkg-a"]), ("unchanged packages", ["packages"])]
    FakeQuestionary(checkbox=[pick("packages"), pick("  packages")]).install(monkeypatch)

    picked_group = prompts.multiselect(QUESTION, choices)
    picked_package = prompts.multiselect(QUESTION, choices)

    assert picked_group == ["pkg-a"]
    assert picked_package == ["packages"]


def test_required_installs_a_validator_that_refuses_an_empty_selection(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``required=True`` keeps the question open rather than returning an empty answer.

    The library's own ``validate=`` hook is what does it, which is why a selected header counts:
    the validator sees a non-empty list of values and the expansion happens afterwards.
    """
    library = FakeQuestionary(checkbox=[[]]).install(monkeypatch)

    prompts.multiselect(QUESTION, ["pkg-a"], required=True)

    validate = library.of("checkbox")[0].kwargs.get("validate")
    assert callable(validate), "required=True must install the library's validate hook"
    assert validate(["pkg-a"]) is True
    refusal = validate([])
    assert isinstance(refusal, str) and refusal, "an empty submission must be refused with a reason"


def test_a_cancelled_multiselect_is_returned_verbatim(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``.ask()`` turns Ctrl-C into ``None``; the adapter hands it straight back.

    The adapter never converts, wraps or swallows a cancellation (design D3) -- every command
    already routes prompt results through ``is_cancel``.
    """
    FakeQuestionary(checkbox=[None]).install(monkeypatch)

    answer = prompts.multiselect(QUESTION, [("changed packages", ["pkg-a"])])

    assert answer is None
    assert is_cancel(answer)


# ======================================================================================
# select / confirm / text (cli-utilities.ts:29-91)
# ======================================================================================


def test_select_forwards_its_message_and_choices(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``askList`` (``cli-utilities.ts:52-64``) -- the single-package bump question."""
    library = FakeQuestionary(select=["minor"]).install(monkeypatch)

    answer = prompts.select(QUESTION, ["patch", "minor", "major"])

    assert answer == "minor"
    call = library.of("select")[0]
    assert call.message == QUESTION
    assert list(call.choices or ()) == ["patch", "minor", "major"]


def test_confirm_defaults_to_yes(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Port of ``askConfirm``'s ``initialValue = true`` (``cli-utilities.ts:67``).

    The one behavioral default the adapter carries: it is what makes ``molt add``'s first-major
    confirmation proceed when the user just presses enter, exactly as upstream's does.
    """
    library = FakeQuestionary(confirm=[True]).install(monkeypatch)

    assert prompts.confirm(QUESTION) is True
    assert library.of("confirm")[0].kwargs["default"] is True


def test_a_cancelled_confirm_returns_the_cancel_sentinel(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled yes/no is a cancellation, not a "no"."""
    FakeQuestionary(confirm=[None]).install(monkeypatch)

    answer = prompts.confirm(QUESTION)

    assert is_cancel(answer)
    assert answer is not False, "a cancellation must not be readable as a declined confirmation"


# (kwargs molt was called with, the library parameter they must land on, why)
TEXT_FORWARDING_CASES: Final = [
    ({"validate": len}, "validate", "a validator is the library's own, forwarded untouched"),
    (
        {"placeholder": "e.g. main"},
        "instruction",
        "questionary 2.1.1 has no `placeholder`; `instruction` is what it renders beside the "
        "question (checked against the installed signature, not assumed)",
    ),
]


@pytest.mark.parametrize(("kwargs", "parameter", "why"), TEXT_FORWARDING_CASES)
def test_text_forwards_its_validator_and_placeholder(
    prompts: QuestionaryPrompts,
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, Any],
    parameter: str,
    why: str,
) -> None:
    """``askQuestion`` (``cli-utilities.ts:75-91``) -- the free-text summary prompt."""
    library = FakeQuestionary(text=["a summary"]).install(monkeypatch)

    assert prompts.text(QUESTION, **kwargs) == "a summary", why

    call = library.of("text")[0]
    assert call.message == QUESTION
    assert parameter in call.kwargs, why


# ======================================================================================
# The editor (design D7; askWithEditor.ts, with click.edit for launch-editor)
# ======================================================================================


def test_the_editor_returns_the_saved_text_without_molts_preamble(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Molt's guidance is written into the buffer and removed by exact prefix match only."""
    preamble = _ASCII_CHROME["editor_preamble"]
    editor = FakeEditor(preamble + "A real summary.\n").install(monkeypatch)

    assert prompts.editor(QUESTION) == "A real summary.\n"
    assert editor.buffers == [preamble], "the buffer molt opens is its own preamble"
    assert editor.calls[0].kwargs.get("extension") == ".md", (
        "the .md extension is what makes an editor highlight a changeset summary"
    )


def test_author_typed_markdown_headings_survive_the_editor(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """molt does NOT port ``askWithEditor.ts:31``'s ``replace(/^#.*\\n?/gm, "")``.

    That regex deletes every Markdown heading the author typed (research doc 03 section 10.6).
    molt removes its own preamble and nothing else, so a summary that is mostly headings survives
    intact -- which is what ``test_add.py::test_editor_summary_preserves_markdown_headings`` has
    always asserted about, and this is the first row where a real editor path produces it.
    """
    written = "## What changed\n\nAdded a `--stream` flag.\n\n### Migration\n\nSwitch to streams."
    FakeEditor(_ASCII_CHROME["editor_preamble"] + written).install(monkeypatch)

    assert prompts.editor(QUESTION) == written


def test_an_editor_that_saved_nothing_yields_an_empty_answer(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing without saving leaves the preamble alone, so the strip yields ``""``.

    ``molt/commands/add.py::_try_editor`` then falls into its existing "Did not find a summary"
    retry prompt rather than losing anything.
    """
    FakeEditor(_ASCII_CHROME["editor_preamble"]).install(monkeypatch)

    assert prompts.editor(QUESTION) == ""


def test_editor_with_a_file_opens_that_path_and_captures_nothing(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``molt add --open``: the named file is opened and no text is captured."""
    target = tmp_path / "cool-pandas-shake.md"
    editor = FakeEditor(None).install(monkeypatch)

    assert prompts.editor(QUESTION, file=str(target)) is None
    assert editor.calls[0].kwargs.get("filename") == str(target)
    assert "text" not in editor.calls[0].kwargs, "the --open path captures nothing"


# ======================================================================================
# A run that cannot ask a question never blocks (design D5)
# ======================================================================================


@pytest.mark.parametrize("method", METHODS)
def test_non_interactive_with_a_default_returns_it_without_asking(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """``--non-interactive`` plus a documented default resolves to it and asks nothing.

    "Asks nothing" is the load-bearing half: a prompt that is built and then answered from a
    default is a prompt that can still block if the default is ever dropped.
    """
    library = FakeQuestionary().install(monkeypatch)
    editor = FakeEditor().install(monkeypatch)
    set_non_interactive(True)

    assert ask(prompts, method, default=DEFAULTS[method]) == DEFAULTS[method]
    assert library.builders == [], "no question may be constructed at all"
    assert editor.calls == []


@pytest.mark.parametrize("method", METHODS)
def test_non_interactive_without_a_default_names_the_question(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """With no default there is nothing to resolve to, so the run stops naming the flag it got."""
    FakeQuestionary().install(monkeypatch)
    FakeEditor().install(monkeypatch)
    set_non_interactive(True)

    with pytest.raises(MoltError) as caught:
        ask(prompts, method)

    assert QUESTION in str(caught.value)
    assert "--non-interactive" in str(caught.value)


@pytest.mark.parametrize("method", METHODS)
def test_no_terminal_refuses_without_blaming_a_flag_the_user_never_passed(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """molt-NEW: no terminal means stop immediately, naming the question.

    changesets has no ``--yes`` and blocks forever here (research doc 03 sections 9.3 and 11.7
    item 7). The message must **not** mention ``--non-interactive``: telling a user that a flag was
    given when it was not sends them searching their own script for something that is not there.
    """
    FakeQuestionary().install(monkeypatch)
    FakeEditor().install(monkeypatch)
    monkeypatch.setattr("molt.ui.prompts._stdin_is_a_terminal", lambda: False)

    with pytest.raises(MoltError) as caught:
        ask(prompts, method)

    message = str(caught.value)
    assert QUESTION in message
    assert "terminal" in message
    assert "--non-interactive" not in message


def test_the_flag_is_reported_before_the_missing_terminal(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both refusals apply at once, and the flag the user actually passed wins (design D5).

    The two rows above each hold with only one condition true, so neither can see the **order** the
    two checks run in -- a run under ``--non-interactive`` in pytest still has no terminal, and the
    row above supplies one. This row is the only place the order is observable: a headless CI step
    that passed the flag must be told about the flag, not sent looking for a terminal it was never
    going to have.

    Added after the change's own mutation check (f) -- swapping the two checks -- survived
    ``test_non_interactive_without_a_default_names_the_question`` untouched. It now fails here.
    """
    FakeQuestionary().install(monkeypatch)
    monkeypatch.setattr("molt.ui.prompts._stdin_is_a_terminal", lambda: False)
    set_non_interactive(True)

    with pytest.raises(MoltError) as caught:
        ask(prompts, "multiselect")

    message = str(caught.value)
    assert "--non-interactive" in message
    assert "terminal" not in message, (
        "the terminal probe must run second: blaming the terminal in a run that explicitly "
        "passed the flag sends the user looking for the wrong thing"
    )


# (where the library gives up, why)
TERMINAL_FAILURE_CASES: Final = [
    ("build", "the framework opens the console while it lays the prompt out"),
    ("ask", "the framework opens the console when the question is driven"),
]


@pytest.mark.parametrize(("stage", "why"), TERMINAL_FAILURE_CASES)
def test_a_terminal_that_cannot_host_a_prompt_gets_the_same_refusal(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch, stage: str, why: str
) -> None:
    """A probe that says "terminal" and a framework that then says "no" is still a refusal.

    Found by hand, not by design: on Windows ``isatty()`` reports ``True`` for the ``NUL`` device,
    so ``molt add < NUL`` -- the documented way to prove a headless run does not block -- passes
    the probe and fails deep inside the terminal framework. Before this, the user got an
    internal-error traceback instead of the one sentence design D5 promises.
    ``io.UnsupportedOperation`` is the POSIX spelling of the same thing (a Vt100 input that is not
    a TTY); Windows raises ``NoConsoleScreenBufferError``, which cannot be imported on POSIX.
    """
    import questionary

    failure = io.UnsupportedOperation("Vt100Input requires a TTY.")
    if stage == "build":
        monkeypatch.setattr(questionary, "checkbox", _raiser(failure))
    else:
        FakeQuestionary(checkbox=[failure]).install(monkeypatch)

    with pytest.raises(MoltError) as caught:
        prompts.multiselect(QUESTION, ["pkg-a"])

    assert QUESTION in str(caught.value), why
    assert "terminal" in str(caught.value), why


def _raiser(error: BaseException) -> Any:
    """A builder that fails the way the terminal framework does when it has no console."""

    def build(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise error

    return build


# ======================================================================================
# Prompt chrome, the drive contract, and the import cost
# ======================================================================================


@pytest.mark.parametrize("name", sorted(_ASCII_CHROME))
def test_every_literal_molt_supplies_to_the_library_is_ascii(name: str) -> None:
    """Design D6 -- ASCII on every platform, with no branch on the detected encoding.

    The library's own ``DEFAULT_SELECTED_POINTER`` is ``U+00BB``, which a cp1252 console cannot
    encode; supplying molt's own pointer is what keeps the flagship flow readable on Windows. What
    the library draws internally and takes no parameter for -- the checkbox indicators -- is
    outside molt's reach and is recorded as gap ``IA-9``.
    """
    value = _ASCII_CHROME[name]
    assert value.isascii(), f"{name!r} is not ASCII: {value!r}"


def test_every_question_is_driven_through_ask_with_an_empty_kbi_msg(
    prompts: QuestionaryPrompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design D3 -- ``.ask()``, never ``unsafe_ask()``, and molt owns the cancellation line.

    ``.ask()`` is what turns Ctrl-C into ``None``; ``unsafe_ask()`` would push a raw
    ``KeyboardInterrupt`` past ``molt add``'s "leave ``.changeset/`` untouched" cleanup into a
    second exit path. The double's ``unsafe_ask`` fails the test outright, so reaching it is not a
    subtle difference. The empty ``kbi_msg`` silences the library's own "Cancelled by user" notice
    so the single "Canceled" line comes from molt's console in molt's voice.
    """
    library = FakeQuestionary(
        checkbox=[[]], select=["patch"], confirm=[True], text=["a summary"]
    ).install(monkeypatch)

    prompts.multiselect(QUESTION, ["pkg-a"])
    prompts.select(QUESTION, ["patch"])
    prompts.confirm(QUESTION)
    prompts.text(QUESTION)

    assert [call.kind for call in library.asks] == ["checkbox", "select", "confirm", "text"]
    assert [call.kwargs.get("kbi_msg") for call in library.asks] == ["", "", "", ""]


def test_importing_the_cli_loads_neither_questionary_nor_prompt_toolkit() -> None:
    """Starting the CLI must not pay for the terminal framework (design D2).

    ``src/molt/cli.py`` imports ``molt.ui.prompts`` at module scope, so a module-scope
    ``import questionary`` in the adapter would put ``prompt_toolkit`` -- a large package with a
    deep import tree -- on the critical path of ``molt --help``, of shell completion and of every
    command that never prompts.

    **This is a new row rather than two more entries in**
    ``tests/cli/test_cli.py::HEAVY_MODULE_PREFIXES``, and the reason is worth keeping: that tuple's
    reader purges only ``molt.*`` from ``sys.modules`` before re-importing ``molt.cli``, so adding
    third-party names to it would make the existing row **order dependent** -- any earlier row in
    the session that imported this adapter leaves ``questionary`` in ``sys.modules``, the purge
    does not remove it, and the row fails for a reason unrelated to ``molt.cli``. This row purges
    the third-party names too. Recorded as gap ``IA-10``.
    """
    third_party = ("questionary", "prompt_toolkit")
    saved = dict(sys.modules)
    try:
        doomed = [
            name
            for name in sys.modules
            if name == "molt" or name.startswith("molt.") or name.startswith(third_party)
        ]
        for name in doomed:
            del sys.modules[name]
        importlib.invalidate_caches()
        importlib.import_module("molt.cli")
        leaked = sorted(name for name in sys.modules if name in third_party)
    finally:
        for name in set(sys.modules) - set(saved):
            del sys.modules[name]
        sys.modules.update(saved)

    assert leaked == [], (
        "the prompt library must be imported inside each adapter method, never at module scope"
    )


def test_questionary_prompts_satisfies_the_prompts_protocol() -> None:
    """Every ``Prompts`` method exists on the implementation with a compatible signature.

    Compared against the protocol itself rather than a hand-copied list, so a method added to
    ``Prompts`` and forgotten here fails this row instead of failing at a user's terminal.
    """
    declared = sorted(
        name
        for name, value in vars(Prompts).items()
        if not name.startswith("_") and callable(value)
    )
    assert declared == ["confirm", "editor", "multiselect", "select", "text"]

    for name in declared:
        implementation = getattr(QuestionaryPrompts, name, None)
        assert implementation is not None, f"QuestionaryPrompts is missing {name!r}"
        assert str(inspect.signature(implementation)) == str(
            inspect.signature(getattr(Prompts, name))
        ), f"{name!r} does not match the protocol signature"


def test_the_cancel_sentinel_is_still_recognised_by_the_shared_predicate() -> None:
    """Both accepted forms of "the user aborted" reach one predicate.

    The library returns ``None``; a test double or a command with its own cleanup hands back
    :data:`molt.ui.prompts.CANCEL`. Anything else is a real answer.
    """
    assert is_cancel(None)
    assert is_cancel(CANCEL)
    assert not is_cancel([])
    assert not is_cancel("")
