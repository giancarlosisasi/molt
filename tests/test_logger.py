"""Conformance tests for molt's console-output seam.

Ports ``packages/logger/src/index.test.ts`` (test-suite doc 08, logger group: 5 Adapt) --
``packages/logger/src/index.ts:1-35`` is a ``util.format`` join plus a ``butterfly `` prefix, one
function per ``console.*`` method it forwards to.

TWO CORRECTIONS TO THE PHASE BRIEF -- READ BEFORE TRUSTING THE GROUP FILE FOR THIS SECTION
============================================================================================

1. **The seam is ``molt.ui.console``, not ``molt.logger``.** The accepted OpenSpec change
   ``adopt-typer-cli-shell`` (``specs/terminal-ui/spec.md``, "Console output adapter") defines a
   ``Console`` protocol in ``molt.ui.console``, a module-level singleton ``console``, and a
   concrete ``RichConsole(file=...)`` implementation. ``tests/cli/test_cli.py`` already targets
   that module (``console_singleton()``, the ``RichConsole`` factory lookup at line ~751). A
   separate ``molt.logger`` module would be a second, competing seam for the same behavior. This
   file keeps the filename ``test_logger.py`` -- the phase file and ``MILESTONES.md`` name it --
   but every test below imports ``molt.ui.console``. Flagged for owner sign-off.

2. **All five levels go to stderr, not just ``error``.** The group file's fixtures note says
   ``error`` -> stderr while ``info``/``warn``/``success``/``log`` -> stdout, mirroring upstream's
   raw ``console.*`` routing. That is superseded: the committed CLI suite pins a **stream
   contract** stated in ``tests/cli/test_cli.py`` (the block above
   ``test_shell_output_goes_through_the_console_adapter``) -- every human-facing byte (banner,
   info/success/warn/error/note, spinners, progress) goes to **stderr**; stdout carries only
   ``--output json`` machine payloads. ``tests/cli/test_cli.py::test_every_console_level_writes_
   to_stderr`` already asserts this for the product ``console`` singleton. Nothing here
   contradicts it: every row below either uses an explicitly injected ``file=`` (stream-agnostic)
   or asserts the ``RichConsole()`` *default* also lands on stderr, never stdout.

Also dropped entirely: upstream's generic ``log`` level (``console.log`` with no label). molt's
``Console`` protocol only has ``info``/``success``/``warn``/``error``/``note`` -- every message is
levelled. Group row 1 (the multi-argument join) is therefore asserted against the concrete levels
instead of a ``log`` that does not exist in molt's design.

Guard note (test-contract.md section 1 + ``tests/cli/fake_cli.py::require_cli_app``): ``molt.ui``
may exist as an empty or partial namespace before ``RichConsole`` is defined on it, so
``pytest.importorskip`` alone is not sufficient -- ``require_rich_console`` below follows it with
an attribute check that skips the whole module (``allow_module_level=True``) the same way
``require_cli_app`` does for ``molt.cli``.
"""

from __future__ import annotations

import io
from typing import Any, Final

import pytest
from tests.cli.fake_cli import strip_ansi

pytestmark = pytest.mark.unit


def require_rich_console() -> Any:
    """Return ``molt.ui.console.RichConsole``, skipping this module if it is not ready yet.

    Mirrors ``tests/cli/fake_cli.py::require_cli_app``: a plain ``pytest.importorskip(
    "molt.ui.console")`` only guards while the module is *absent*. ``tests/cli/test_cli.py``
    already proves ``molt.ui.console`` can exist with other attributes before ``RichConsole`` is
    defined on it, so the attribute check with ``allow_module_level=True`` is required too.
    """
    module = pytest.importorskip(
        "molt.ui.console", reason="build step 6 - the ui/ seam lands with the cli-shell change"
    )
    factory = getattr(module, "RichConsole", None)
    if factory is None:
        pytest.skip(
            "molt.ui.console.RichConsole not implemented yet (assumed seam, tasks.md 2.2)",
            allow_module_level=True,
        )
    return factory


RichConsole = require_rich_console()

#: Written as an escape, not a literal, so this source file stays ASCII-only (project rule;
#: mirrors ``tests/cli/test_cli.py::BANNER_GLYPH``). Upstream's prefix is ``"\U0001f98b "``
#: (``logger/src/index.ts:4``), applied to *every* level, not only a startup banner.
BUTTERFLY: Final = "\U0001f98b"

LEVELS: Final = ("info", "success", "warn", "error")


# ======================================================================================
# Group row 1 -- the multi-argument join (upstream: log("Message 1", "Message 2"))
# ======================================================================================

JOIN_CASES: Final = [
    ("info", "info() is molt's nearest analogue to upstream's dropped generic log()"),
    ("success", "the join has to be level-agnostic, so success() must join too"),
    ("warn", "warn() must join too"),
    ("error", "error() must join too"),
]


@pytest.mark.parametrize(("level", "why"), JOIN_CASES)
def test_multiple_arguments_are_joined_in_order(level: str, why: str) -> None:
    """logger/src/index.ts:6-15, group row 1 -- ``util.format("", *args)`` joins every argument,
    in order, into one rendered line (``logger/src/index.test.ts:18-23``).
    """
    stream = io.StringIO()
    console = RichConsole(file=stream)

    getattr(console, level)("Message 1", "Message 2")

    text = strip_ansi(stream.getvalue())
    first = text.find("Message 1")
    second = text.find("Message 2")
    assert first != -1 and second != -1, why
    assert first < second, f"arguments must render in call order, {why}"


# ======================================================================================
# Group rows 2-5 -- each level renders a distinct, visible label
# ======================================================================================

LABEL_CASES: Final = [
    ("info", "logger/src/index.test.ts:40-54, group row 3 -- cyan 'info' label"),
    ("success", "logger/src/index.test.ts:72-86, group row 5 -- green 'success' label"),
    ("warn", "logger/src/index.test.ts:56-70, group row 4 -- yellow 'warn' label"),
    ("error", "logger/src/index.test.ts:25-39, group row 2 -- red 'error' label"),
]


@pytest.mark.parametrize(("level", "why"), LABEL_CASES)
def test_level_renders_a_distinct_visible_label(level: str, why: str) -> None:
    """Each level's rendered line must carry its own name as a visible label -- the label
    *text*, not merely the *stream* it landed on. Stream routing (all five to stderr) is already
    pinned by ``tests/cli/test_cli.py::test_every_console_level_writes_to_stderr``; this row is
    what the group file's rows 2-5 actually add beyond that: ``error`` renders differently from
    ``info``/``warn``/``success`` even though, post-correction, all four share a stream.

    ANTI-VACUITY: the *absence* of every other level's name is asserted too. ``level in text``
    alone is satisfied by an adapter that prints one fixed banner containing all four words on
    every call -- which renders the levels indistinguishable, the exact thing this row exists to
    forbid, and which upstream gets for free by routing to four different ``console`` methods
    (``logger/src/index.ts:17-35``). Verified by mutation: without the second assertion, an
    all-labels-every-time adapter leaves this row green.
    """
    stream = io.StringIO()
    console = RichConsole(file=stream)

    getattr(console, level)("PAYLOAD")

    text = strip_ansi(stream.getvalue()).lower()
    assert level in text, f"{level}() must render a recognizable {level!r} label ({why})"
    assert "payload" in text
    for other in LEVELS:
        if other != level:
            assert other not in text, (
                f"{level}() must not also render the {other!r} label -- the four levels have to "
                f"be distinguishable from the text alone ({why})"
            )


# ======================================================================================
# Windows-correctness -- beyond the letter of the 5 group rows, required by the phase brief
# ======================================================================================


def test_butterfly_prefix_and_labels_survive_cp1252() -> None:
    """terminal-ui spec, "Emoji banner on a legacy code page"; phase brief section 3.2.

    ``tests/cli/test_cli.py::test_console_degrades_the_banner_on_a_cp1252_stream`` proves this
    for one banner-carrying ``info`` call. Upstream's butterfly prefix (``logger/src/index.ts:4``)
    is written on *every* level, so this row proves the degrade-not-raise contract holds for all
    four -- exactly the class of bug molt promises to avoid (repo ``CLAUDE.md``, "Windows-correct
    from day one"). No level may raise ``UnicodeEncodeError``, and the message text must still be
    legible even though the glyph itself cannot survive cp1252.
    """
    for level in LEVELS:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="")
        console = RichConsole(file=stream)

        getattr(console, level)(f"{BUTTERFLY} startup")
        stream.flush()

        written = stream.buffer.getvalue().decode("cp1252")
        assert "startup" in written, f"{level}: the text survives even though the glyph cannot"
        assert BUTTERFLY not in written, f"{level}: cp1252 cannot encode the glyph literally"


def test_richconsole_writes_only_to_the_injected_stream(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """terminal-ui spec, "Console output adapter" -- makes the whole seam testable in isolation.

    Nothing else in this file proves ``RichConsole(file=...)`` actually threads its ``file``
    argument through to the write call rather than, say, always writing to the real
    ``sys.stderr`` underneath. Every other row here depends on that being true.
    """
    stream = io.StringIO()
    console = RichConsole(file=stream)

    console.info("ISOLATED-MARKER")

    assert "ISOLATED-MARKER" in strip_ansi(stream.getvalue())
    captured = capsys.readouterr()
    assert "ISOLATED-MARKER" not in captured.out
    assert "ISOLATED-MARKER" not in captured.err


def test_richconsole_defaults_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """Stream-contract corollary (module docstring, correction 2): ``file=None`` must default to
    stderr, not stdout, so a ``RichConsole()`` built anywhere in the codebase inherits the correct
    default without every call site passing ``file=sys.stderr`` explicitly.
    ``tests/cli/test_cli.py::test_every_console_level_writes_to_stderr`` asserts the same fact
    for the product's ``console`` singleton; this row asserts it at the constructor default.
    """
    console = RichConsole()

    console.info("DEFAULT-STREAM-MARKER")

    captured = capsys.readouterr()
    assert "DEFAULT-STREAM-MARKER" in captured.err
    assert "DEFAULT-STREAM-MARKER" not in captured.out
