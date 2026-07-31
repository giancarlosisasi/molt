"""Conformance tests for writing a changeset to disk (``write_changeset``).

Ports ``packages/write/src/index.test.ts`` -- see
``roadmap/research/test-suite/03-config-changeset-io.md``, section
``packages/write/src/index.test.ts``, rows 1-4: **2 Port / 2 Adapt / 0 Drop**.
Implementation reference: ``write/src/index.ts:32-64``; the byte-level template is written up in
``roadmap/research/changesets-02-config-and-data-model.md`` section 5.5.

molt's template (LF only, no BOM), per **doc 02 section 12.6**::

    ---
    "<name>": <type>        (one line per release, joined by \\n)
    ---

    <summary>
                            (one trailing LF; NO trailing spaces)

Upstream's template (``write/src/index.ts:52-57``) ends ``<summary>`` + ``\\n`` + **two spaces**
and **no** final newline. molt drops the two spaces -- see the decision record on
:func:`test_the_written_bytes_match_the_template_exactly`.

**Names are always double-quoted, types never are.** The quoting is load-bearing, not cosmetic:
``write/src/index.ts:49-51`` says so outright, and doc 02 section 5.3 shows why -- an unquoted
name starting with a reserved character is a hard YAML error, so an unquoted writer would emit
files its own parser rejects.

Adaptations:

* **Rows 2-3 (the formatter).** Upstream detects and shells out to prettier / oxfmt / deno /
  dprint (``@changesets/format``) and its tests assert ``detect -> "prettier"`` and
  ``format(..., {formatter: "oxfmt"})``. That JS toolchain matrix does not port (research README
  section 4.4; group file, write row 3). Two things change at once:

  1. the *backend matrix* is gone -- the assertions below go through a monkeypatched seam and
     never name a JS formatter;
  2. the *default* is inverted. Upstream defaults to ``format: "auto"``
     (``write/src/index.ts:46``); **molt defaults to no formatter at all** --
     ``website/docs/config/options.md:95`` ("molt emits correct, deterministic Markdown itself
     ... so the default is ``false``"), doc 02 section 12.1 ("default ``false`` -- do not shell
     out to Node"), research README section 5 item 13 ("Correct changelog markdown (no formatter
     pass)"). The group file's inline default of ``"auto"`` is a verbatim copy of *upstream's*
     config object, annotated Adapt; it is not a molt decision.

  So the surviving contract is: the default path runs **nothing**, and only an explicitly
  configured formatter reaches the hook.
* **Row 1 (the id).** Upstream mocks ``human-id`` to return ``"ascii"``. molt takes the id as an
  explicit keyword argument, so the shared ``seeded_ids`` fixture supplies it (first id
  ``strange-words-combine``) and nothing is monkeypatched.

Assumed signatures (test contract section 5, plus what rows 2-3 require)::

    write_changeset(root_dir, changeset, *, id=None, format=None) -> Path
        # format=None  -> use config; the config default is `false` (no formatter)
        # format=False -> explicit opt-out
        # format="<name>" -> run that formatter through the seam below
    molt.changeset.write.format_files(...)          # the formatter seam, spied on below

Input changesets are built by **parsing a canonical document** rather than by calling a
``Changeset`` constructor. The value is identical, and it keeps these tests from over-pinning a
constructor signature the contract does not specify -- while making every assertion here a
genuine ``write``/``parse`` round-trip, which is what row 1 asks for anyway.
"""

from __future__ import annotations

import contextlib
import importlib
from typing import TYPE_CHECKING, Any, Final

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from molt.versioning import BumpType

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import ProjectBuilder, SeededIds

pytest.importorskip(
    "molt.changeset", reason="build step 3 - changeset IO not yet implemented (TDD target)"
)

from molt.changeset import parse_changeset, write_changeset

pytestmark = pytest.mark.functional


# --------------------------------------------------------------------------------------
# The formatter seam (rows 2-3). Named once, here, so it is trivial to re-point.
# --------------------------------------------------------------------------------------

FORMATTER_SEAM_MODULE: Final = "molt.changeset.write"
FORMATTER_SEAM_ATTR: Final = "format_files"


def _formatter_seam_module() -> Any:
    with contextlib.suppress(ModuleNotFoundError):
        return importlib.import_module(FORMATTER_SEAM_MODULE)
    return importlib.import_module("molt.changeset")


def install_formatter_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Replace the formatter hook with a spy and return the list it records calls into.

    Deliberately records only *that* the hook ran and with what positional arguments -- the hook's
    argument shape is molt's to choose (upstream passes ``[path], {cwd, formatter}``), and pinning
    it here would re-import the JS formatter matrix through the back door.

    The ``hasattr`` guard is load-bearing: ``raising=False`` alone would let every
    "the formatter did NOT run" assertion pass vacuously against a misnamed seam, since a spy
    that was never installed on the real attribute can never be called.
    """
    module = _formatter_seam_module()
    assert hasattr(module, FORMATTER_SEAM_ATTR), (
        f"formatter seam missing: expected {FORMATTER_SEAM_MODULE}.{FORMATTER_SEAM_ATTR}; "
        "without it the negative formatter assertions below are vacuous"
    )

    calls: list[tuple[Any, ...]] = []

    def spy(*args: Any, **kwargs: Any) -> None:
        calls.append(args)

    monkeypatch.setattr(module, FORMATTER_SEAM_ATTR, spy, raising=False)
    return calls


# --------------------------------------------------------------------------------------
# Helpers and fixtures-as-data
# --------------------------------------------------------------------------------------


def releases_of(changeset: Any) -> list[tuple[str, BumpType]]:
    """Normalize ``Changeset.releases`` to ``(name, type)`` pairs; see ``test_parse.py``."""
    return [(release.name, release.type) for release in changeset.releases]


def read_written(path: Path) -> str:
    """Read a written changeset **without** universal-newline translation.

    ``Path.read_text`` silently rewrites ``\\r\\n`` to ``\\n`` (and the ``newline=`` argument only
    exists from 3.13), which would mask the exact Windows bug the CRLF cases here exist to catch.
    Decoding the bytes directly keeps the file honest.
    """
    return path.read_bytes().decode("utf-8")


CANONICAL_SOURCE: Final = '---\n"pkg-a": minor\n---\n\nThis is a summary\n'
EMPTY_SOURCE: Final = "---\n---"

# The byte-exact expected output for CANONICAL_SOURCE. One constant, because it is the single
# place the trailer is decided -- see `test_the_written_bytes_match_the_template_exactly`.
EXPECTED_BYTES: Final = b'---\n"pkg-a": minor\n---\n\nThis is a summary\n'

# The three trailers a changeset file can end with in the wild. molt writes the second and must
# read all three (doc 02 section 12.6: "make the reader tolerant of both").
TRAILER_VARIANTS = [
    pytest.param("\n  ", id="upstream-lf-then-two-spaces-no-final-newline"),
    pytest.param("\n", id="molt-single-lf"),
    pytest.param("\n  \n", id="two-spaces-then-lf"),
]


# --------------------------------------------------------------------------------------
# Row 1 (Port) -- write, then round-trip through parse
# --------------------------------------------------------------------------------------


def test_writes_a_changeset_that_round_trips_through_parse(
    tmp_project: ProjectBuilder, seeded_ids: SeededIds
) -> None:
    """Row 1 (``write/src/index.test.ts:23-55``): the round-trip is the primary assertion.

    Upstream mocks ``human-id`` to ``"ascii"``; molt passes the id explicitly, seeded by the
    shared ``seeded_ids`` fixture (first id ``strange-words-combine``, matching the reference
    ``human-id`` mock recorded in the test contract section 3). Asserting a *generated* id is
    exactly what doc 02 section 6 warns against -- "the name is never semantically meaningful".

    ``.changeset`` is created if missing (``write/src/index.ts:59``: ``mkdir -p``).
    """
    source = parse_changeset(CANONICAL_SOURCE)
    changeset_id = seeded_ids.next()
    assert changeset_id == "strange-words-combine"
    assert not (tmp_project.root / ".changeset").exists()

    path = write_changeset(tmp_project.root, source, id=changeset_id, format=False)

    assert path == tmp_project.root / ".changeset" / f"{changeset_id}.md"
    written = parse_changeset(read_written(path))
    assert releases_of(written) == [("pkg-a", BumpType.MINOR)]
    assert written.summary == "This is a summary"
    assert releases_of(written) == releases_of(source)
    assert written.summary == source.summary


def test_the_written_bytes_match_the_template_exactly(
    tmp_project: ProjectBuilder, seeded_ids: SeededIds
) -> None:
    """Byte-exact serialization, LF only, name quoted (doc 02 section 5.5).

    Read as **bytes** on purpose: a text-mode read would hide the exact failure this guards
    against, namely Python's default newline translation turning ``\\n`` into ``\\r\\n`` on
    Windows. changeset files are committed and diffed; emitting CRLF would churn every file on
    every Windows contributor's machine.

    ``format=False`` because the formatter hook is allowed to rewrite the file (doc 02 section
    5.5: *"with format: false the template bytes are final"*).

    **DECISION (trailer): molt writes** ``<summary>\\n`` **-- one LF, no trailing spaces.**
    Source: doc 02 section 12.6, *"Drop the trailing two spaces (they're a JS template-literal
    artifact) and add a single trailing newline -- but make the reader tolerant of both. Document
    the divergence."* Upstream (``write/src/index.ts:52-57``) instead emits ``<summary>`` +
    ``\\n`` + two spaces and **no** final newline; those two spaces are a Markdown hard line
    break that ``markdownlint`` MD009 flags on every file the tool writes, which is why molt
    diverges. The reader side of that decision is pinned by
    :func:`test_the_reader_tolerates_every_trailer_variant`.
    """
    source = parse_changeset(CANONICAL_SOURCE)

    path = write_changeset(tmp_project.root, source, id=seeded_ids.next(), format=False)

    raw = path.read_bytes()
    assert raw == EXPECTED_BYTES
    assert b"\r\n" not in raw, (
        "the template's own newlines are LF; summary bytes pass through verbatim "
        "(a summary that itself contains CRLF is preserved -- see "
        "test_write_then_parse_is_the_identity)"
    )
    assert raw[:3] != b"\xef\xbb\xbf", "no BOM (doc 02 section 5.5)"


@pytest.mark.parametrize("trailer", TRAILER_VARIANTS)
def test_the_reader_tolerates_every_trailer_variant(trailer: str) -> None:
    """The other half of the trailer decision: molt writes one form and reads all three.

    doc 02 section 12.6 asks for exactly this -- *"make the reader tolerant of both"*. It is not
    theoretical: a repository migrated from changesets has ``\\n`` + two spaces on disk (upstream's
    bytes), a repository whose files were touched by Prettier has a bare ``\\n``, and the variant
    with both is what the group file's template block shows. All three must yield the same
    ``Changeset``, because ``summary.strip()`` (``parse/src/index.ts:75``) eats the trailer.
    """
    changeset = parse_changeset('---\n"pkg-a": minor\n---\n\nThis is a summary' + trailer)

    assert releases_of(changeset) == [("pkg-a", BumpType.MINOR)]
    assert changeset.summary == "This is a summary"


# --------------------------------------------------------------------------------------
# Rows 2, 3 (Adapt) -- the formatter hook, with the JS backend matrix removed
# --------------------------------------------------------------------------------------


def test_format_false_skips_the_formatter_hook(
    tmp_project: ProjectBuilder, seeded_ids: SeededIds, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 2 (``write/src/index.test.ts:57-92``), **adapted**.

    Upstream asserts ``detect`` and ``format`` from ``@changesets/format`` were never called.
    molt has no such module (research README section 4.4; the prettier/oxfmt/deno/dprint
    detection matrix does not port), so the assertion moves to molt's own formatter seam. The
    behavior under test is unchanged: opting out must mean *nothing* runs over the new file.

    The summary below is upstream's -- an HTML snippet inside a fence, i.e. content a formatter
    would visibly rewrite, which is why opting out matters.
    """
    calls = install_formatter_spy(monkeypatch)
    summary = (
        "This is a summary\n~~~html\n<style>custom-element::part(thing) {color:blue}</style>\n~~~"
    )
    source = parse_changeset(f'---\n"pkg-a": minor\n---\n\n{summary}\n')

    write_changeset(tmp_project.root, source, id=seeded_ids.next(), format=False)

    assert calls == []


def test_an_explicitly_configured_formatter_reaches_the_hook(
    tmp_project: ProjectBuilder, seeded_ids: SeededIds, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 3 (``write/src/index.test.ts:94-132``), **adapted**.

    Upstream drops an ``.oxfmtrc.json`` in the project and asserts ``detect`` returned
    ``"prettier"`` and ``format`` was called with ``{cwd, formatter: "oxfmt"}``. Those exact
    assertions are dropped (group file, write row 3): they encode a JS-toolchain detection order
    with no Python analogue, and molt does not auto-detect at all.

    What survives is the half of row 3 that is still true for molt: when a formatter **is**
    configured, ``write_changeset`` must actually route the new file through it. The name passed
    here is deliberately a Python one and is never asserted on -- only that the seam fired.
    """
    calls = install_formatter_spy(monkeypatch)
    source = parse_changeset(CANONICAL_SOURCE)

    write_changeset(tmp_project.root, source, id=seeded_ids.next(), format="mdformat")

    assert len(calls) == 1


def test_the_default_path_does_not_invoke_the_formatter_hook(
    tmp_project: ProjectBuilder, seeded_ids: SeededIds, monkeypatch: pytest.MonkeyPatch
) -> None:
    """molt's default is **no formatter** -- the inverse of upstream's ``format: "auto"``.

    ``write/src/index.ts:46`` defaults to ``"auto"`` and shells out to whatever JS formatter it
    detects. molt does not: ``website/docs/config/options.md:95`` states *"molt emits correct,
    deterministic Markdown itself ... so the default is ``false``"*, doc 02 section 12.1 says
    *"default ``false`` (do not shell out to Node)"*, and research README section 5 item 13 lists
    *"Correct changelog markdown (no formatter pass)"* as a differentiator. A default that
    silently spawned a subprocess would make ``molt changeset add`` non-hermetic and slow, and
    would make the byte-exact template above unenforceable.

    Passing no ``format=`` at all is the case under test -- this is what the CLI does when the
    user has not configured one.
    """
    calls = install_formatter_spy(monkeypatch)
    source = parse_changeset(CANONICAL_SOURCE)

    write_changeset(tmp_project.root, source, id=seeded_ids.next())

    assert calls == []


# --------------------------------------------------------------------------------------
# Row 4 (Port) -- the empty changeset
# --------------------------------------------------------------------------------------


def test_writes_an_empty_changeset(tmp_project: ProjectBuilder, seeded_ids: SeededIds) -> None:
    """Row 4 (``write/src/index.test.ts:134-166``): ``{summary: "", releases: []}`` round-trips.

    The frontmatter block collapses to a blank line between two fences (``---\\n\\n---``), which
    ``parse_changeset`` reads back as ``releases == []`` (doc 02 section 5.5). This is the file
    ``molt changeset add --empty`` produces, and it must survive its own writer.
    """
    source = parse_changeset(EMPTY_SOURCE)
    assert releases_of(source) == []

    path = write_changeset(tmp_project.root, source, id=seeded_ids.next(), format=False)

    written = parse_changeset(read_written(path))
    assert releases_of(written) == []
    assert written.summary == ""


# --------------------------------------------------------------------------------------
# Property -- write then parse is the identity. The strongest guard on this whole slice.
# --------------------------------------------------------------------------------------

package_names = st.from_regex(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,15}\Z")
bump_types = st.sampled_from(list(BumpType))
# The alphabet is chosen to hit exactly the characters that break naive writers: `-` (so `---`
# fences appear inside the body), `#` (the upstream heading-stripping bug, research README
# section 3.4), `\r` and `\n` (CRLF), blank lines and quotes.
summaries = st.text(alphabet=st.sampled_from('abXY 019-#*.:"\n'), max_size=60).map(str.strip)
crlf_summaries = summaries.map(lambda text: text.replace("\n", "\r\n"))


@pytest.mark.property
@given(name=package_names, bump=bump_types, summary=st.one_of(summaries, crlf_summaries))
@settings(
    deadline=None,
    max_examples=40,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_write_then_parse_is_the_identity(
    tmp_project: ProjectBuilder, name: str, bump: BumpType, summary: str
) -> None:
    """``parse(write(c)) == c`` for arbitrary valid name / type / summary triples.

    This is the invariant the whole changeset IO slice rests on: every ``molt changeset add``
    must produce a file every ``molt version`` can read. Generating summaries that contain
    ``---``, ``#`` headings, quotes and CRLF is the point -- each one is a documented way to break
    a naive writer (doc 02 sections 5.1, 5.5; research README section 3.4).

    Summaries are pre-stripped because ``summary.strip()`` in the parser makes round-tripping
    lossy for leading/trailing whitespace (doc 02 section 5.5). That lossiness is upstream
    behavior and is deliberately not "fixed" here.

    ``format=False`` keeps the property hermetic: a real formatter would be free to rewrite the
    body, and this property is about the writer, not the formatter.
    """
    source = parse_changeset(f'---\n"{name}": {bump.value}\n---\n\n{summary}\n')

    path = write_changeset(tmp_project.root, source, id="property-case", format=False)

    written = parse_changeset(read_written(path))
    assert releases_of(written) == releases_of(source)
    assert written.summary == source.summary
