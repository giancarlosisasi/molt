"""Conformance tests for changelog entry assembly and the newline clamp.

Ports ``packages/apply-release-plan/src/get-changelog-entry.test.ts`` -- the 4-row section of
``roadmap/research/test-suite/04-apply-changelog.md`` (4 Port / 0 Adapt / 0 Drop) -- and adds
direct unit coverage of the ``getChangelogEntry`` assembly rules that the group file only
exercises end-to-end through ``applyReleasePlan`` (rows 27-36, ``index.test.ts``). Agent P4a
covers those through ``apply``; the two levels catch different bugs, and the overlap is
deliberate.

Both functions are pure and forge-agnostic: no filesystem, no network, no git. Everything a
changelog generator plugin contributes arrives as an already-rendered string, which is why this
module can pin the exact bytes.

The newline clamp is the load-bearing piece
-------------------------------------------
``generateMarkdownForVersionType`` (``get-changelog-entry.ts:122-149``) does not simply join
lines. It runs a small state machine, locked upstream by ``get-changelog-entry.test.ts:39-73``:

    start with ``new_lines = 2`` (the blank line after the heading);
    per line: add that line's *leading* newline count, clamp the total to ``[1, 2]``, emit that
    many ``\\n``, then the *trimmed* line; carry that line's *trailing* newline count forward.

That arithmetic is the entire reason ``changelog-git``'s ``"- ..."`` lines
(``changelog-git/src/index.ts:9-17``) come out adjacent while ``changelog-github``'s
``"\\n\\n- ...\\n"`` lines (``changelog-github/src/index.ts:187``) come out blank-line separated,
from the same assembler with no generator-specific branching. Both shapes are pinned below as
named cases, and the ``[1, 2]`` bound is pinned as a hypothesis property.

Seams (P4 shared brief section 8)
----------------------------------
``generate_markdown_for_version_type(bump: BumpType, lines: Sequence[str]) -> str | None``
    Direct rename of ``generateMarkdownForVersionType``; ``undefined`` becomes ``None``.

``get_changelog_entry(...) -> str | None``
    The registry gives no argument list, so this file *chooses* one and flags it for the owner::

        get_changelog_entry(
            release, releases, changesets, generator, *,
            deps=(), dev_deps=(), optional_deps=(),
            update_internal_dependencies=BumpType.PATCH, options=None, forge=None,
        ) -> str | None

    Upstream reads ``release.packageJson.dependencies`` off a ``ModCompWithPackage``
    (``get-changelog-entry.ts:53-58``). ``molt.engine.Release`` carries no manifest, so the three
    dependency sections are passed in explicitly, as sequences of PEP 508 strings, using the same
    ``deps`` / ``dev_deps`` / ``optional_deps`` vocabulary as ``tmp_project.add_package``. Passing
    all three -- rather than only the runtime deps -- is deliberate: it makes "dev and optional
    dependencies are invisible in the changelog" a rule the function enforces and a test can
    check, instead of an accident of what the caller chose to forward.

    ``options`` and ``forge`` are pure pass-through: the assembler never reads either, it only
    hands both to the generator on every call. They are in the signature because without them
    ``get_changelog_entry`` could never drive ``molt.changelog.github``, whose whole job is to
    turn a ``forge`` into commit and pull-request links -- an earlier revision of this file
    omitted ``forge`` and would have pinned an assembler that can only ever run the offline
    ``git`` generator. See "The generator protocol" below.

The generator protocol -- four parameters, synchronous
------------------------------------------------------
Both generator methods are spelled ``(changeset_or_changesets, ..., options, forge)``::

    get_release_line(changeset, bump, options, forge) -> str
    get_dependency_release_line(changesets, dependencies, options, forge) -> str

That is the contract in ``website/docs/extending/changelog-plugins.md`` (the ``Protocol``
declaration under "The contract") and in the worked implementation in
``website/docs/extending/custom-generators.md`` ("2. Implement the contract"), and it is what
``tests/changelog/test_changelog.py`` drives the two built-ins with. ``forge`` is ``None`` when
no forge is configured, which is the normal case for the default ``git`` generator -- the docs
say a generator that does not need a forge "simply ignores the argument".

``options`` and ``forge`` are given defaults of ``None`` on the local double only so that the
call sites in this file, none of which care about either, stay readable; the protocol itself
requires all four to be accepted.

Divergences pinned as assertions (research README section 3.4)
---------------------------------------------------------------
- **Markdown headings inside a summary survive.** Upstream's summary post-processing strips every
  line beginning with ``#``, destroying headings in changeset descriptions. molt treats a summary
  as literal prose (``website/docs/guides/changelog-templates.md``, "Correct Markdown, the first
  time").
- **Replacement is function-based.** A summary or sha containing ``$``, ``\\1`` or ``\\g<0>`` must
  survive byte-for-byte, which in Python means a replacement *callable* passed to ``re.sub``,
  never a replacement string.
- **Whitespace-only release lines are dropped**, where upstream's truthiness filter
  (``get-changelog-entry.ts:126``) lets them through and emits a three-newline gap that breaks the
  very clamp this function exists to enforce. Flagged for owner confirmation.

Marker: ``unit``, module-wide -- a deliberate Type deviation, recorded
----------------------------------------------------------------------
The group file types rows 2, 3 and 4 of the ``get-changelog-entry.test.ts`` section as
``snapshot`` (they are ``toMatchInlineSnapshot`` upstream), and the P4 shared brief section 5
maps ``snapshot`` -> the ``snapshot`` marker. This module uses ``unit`` for all of them, with
inline expected strings instead of syrupy -- same rationale as ``tests/engine/test_assemble.py``:

- The outputs are a few lines long and their *exact newlines* are the entire behavior under
  test. An inline literal shows a reviewer what changed in the diff; a snapshot hides it behind
  a regenerated baseline file.
- A guarded module never runs, so ``--snapshot-update`` cannot generate a baseline at all until
  build step 7 lands. Marking these ``snapshot`` would put rows into the ``-m snapshot``
  conformance gate (``roadmap/research/test-suite/MILESTONES.md``) that carry no snapshot
  assertion and never will.
- Everything here is pure in-memory: no filesystem, no network, no git. ``unit`` is accurate.

Flagged so the Type-column mismatch against the group file reads as a decision rather than an
oversight.

research doc 04 sections 3.1-3.2; research README section 3.3.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st
from packaging.version import Version

from molt.versioning import BumpType

pytest.importorskip(
    "molt.changelog", reason="build step 7 - changelog not yet implemented (TDD target)"
)

from molt.changelog import (  # pyrefly: ignore[missing-import]
    generate_markdown_for_version_type,
    get_changelog_entry,
)

pytestmark = pytest.mark.unit

PATCH = BumpType.PATCH
MINOR = BumpType.MINOR
MAJOR = BumpType.MAJOR
NONE = BumpType.NONE


# ======================================================================================
# Local test doubles.
#
# Deliberately module-local rather than shared: `tests/apply/fake_release_plan.py` belongs to
# agent P4a and these are ten lines of structural stand-in, not a fixture worth coupling to.
# ======================================================================================


@dataclasses.dataclass(frozen=True)
class ChangesetRelease:
    """One ``name: bump`` row of a changeset's frontmatter."""

    name: str
    type: BumpType


@dataclasses.dataclass(frozen=True)
class Changeset:
    """``molt.changeset.Changeset`` stand-in: ``.id``, ``.summary``, ``.releases``."""

    id: str
    summary: str
    releases: tuple[ChangesetRelease, ...]


@dataclasses.dataclass(frozen=True)
class Release:
    """``molt.engine.Release`` stand-in."""

    name: str
    type: BumpType
    old_version: Version
    new_version: Version
    changesets: tuple[str, ...] = ()


class GitStyleGenerator:
    """A faithful stand-in for ``molt.changelog.git`` (``changelog-git/src/index.ts:3-35``).

    Used because it is the *default* generator, so its line shapes are the ones the clamp has to
    get right in practice. One divergence, and it is the point of divergence #2 in research README
    section 3.4: a blank continuation line is emitted as a genuinely blank line, not as the two
    trailing spaces upstream leaves for prettier to clean up.

    Four-parameter methods, per ``website/docs/extending/changelog-plugins.md``. ``git`` ignores
    both ``options`` and ``forge`` -- that is precisely why it is the default generator, "works
    with no configuration and no network" -- but it must still *accept* them, because the
    assembler calls every generator the same way.
    """

    def get_release_line(
        self,
        changeset: Changeset,
        bump: BumpType,
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        first, *rest = (line.rstrip() for line in changeset.summary.split("\n"))
        line = f"- {first}"
        if rest:
            line += "\n" + "\n".join(f"  {text}" if text else "" for text in rest)
        return line

    def get_dependency_release_line(
        self,
        changesets: Sequence[Changeset],
        dependencies_updated: Sequence[Release],
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        if not dependencies_updated:
            return ""
        links = ["- Updated dependencies" for _ in changesets]
        listed = [f"  - {dep.name}@{dep.new_version}" for dep in dependencies_updated]
        return "\n".join(links + listed)


GIT = GitStyleGenerator()


@dataclasses.dataclass
class RecordingGenerator:
    """Records every ``(options, forge)`` pair the assembler passes down.

    Exists for one assertion the rest of this file cannot make: that ``options`` and ``forge``
    reach the generator *unchanged and un-dropped*. Everything else here uses :data:`GIT`, which
    ignores both, so an assembler that quietly passed ``forge=None`` -- or never accepted a
    ``forge`` at all -- would look perfectly healthy while being structurally unable to drive
    ``molt.changelog.github``.
    """

    release_calls: list[tuple[Mapping[str, object] | None, object | None]] = dataclasses.field(
        default_factory=list
    )
    dependency_calls: list[tuple[Mapping[str, object] | None, object | None]] = dataclasses.field(
        default_factory=list
    )

    def get_release_line(
        self,
        changeset: Changeset,
        bump: BumpType,
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        self.release_calls.append((options, forge))
        return f"- {changeset.summary}"

    def get_dependency_release_line(
        self,
        changesets: Sequence[Changeset],
        dependencies_updated: Sequence[Release],
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        self.dependency_calls.append((options, forge))
        if not dependencies_updated:
            return ""
        listed = "\n".join(f"  - {dep.name}@{dep.new_version}" for dep in dependencies_updated)
        return f"- Updated dependencies\n{listed}"


class StubForge:
    """A ``molt.forge.Forge`` placeholder. Identity is all these tests need from it."""


def v(text: str) -> Version:
    return Version(text)


# ======================================================================================
# Row 1 -- an all-empty section is not emitted at all
# ======================================================================================

EMPTY_CASES = [
    ((), "no lines were bucketed for this bump type at all"),
    (("",), "one empty line -- what an empty getDependencyReleaseLine returns"),
    (("", ""), "row 1 verbatim: generateMarkdownForVersionType('patch', ['', ''])"),
]


@pytest.mark.parametrize(("lines", "why"), EMPTY_CASES)
def test_an_all_empty_section_returns_none(lines: Sequence[str], why: str) -> None:
    """Row 1 (get-changelog-entry.test.ts:5-7) -- ``undefined`` becomes ``None``.

    Load-bearing downstream: the ``.filter()`` in the final assembly
    (``get-changelog-entry.ts:117``) is what keeps an empty ``### Patch Changes`` heading out of a
    release that only had a major change. Every entry pushes a dependency line into the patch
    bucket unconditionally (``get-changelog-entry.ts:95-103``), so without this ``None`` most
    entries would carry a bare heading.
    """
    assert generate_markdown_for_version_type(PATCH, lines) is None, why


# ======================================================================================
# Row 2 -- the section heading map
# ======================================================================================

HEADING_CASES = [
    (MAJOR, "### Major Changes\n\n- something", "capitalize('major') + ' Changes'"),
    (MINOR, "### Minor Changes\n\n- something", "capitalize('minor') + ' Changes'"),
    (PATCH, "### Patch Changes\n\n- something", "capitalize('patch') + ' Changes'"),
]


@pytest.mark.parametrize(("bump", "expected", "why"), HEADING_CASES)
def test_the_heading_is_derived_from_the_bump_type(bump: BumpType, expected: str, why: str) -> None:
    """Row 2 (get-changelog-entry.test.ts:9-28) -- three ``expect.soft`` calls, parametrized.

    ``capitalize`` is ``utils.ts:83-85``. Level three (``###``) is fixed: the file title is ``#``
    and the version heading is ``##`` (research doc 04 section 3.1).
    """
    assert generate_markdown_for_version_type(bump, ["- something"]) == expected, why


def test_a_none_bump_has_no_section() -> None:
    """``none`` is not a section. Upstream types the parameter as ``keyof ChangelogLines``.

    There is no ``### None Changes`` heading anywhere in changesets, and a ``none`` release never
    reaches this function -- ``getChangelogEntry`` returns early at ``get-changelog-entry.ts:31``
    and the per-changeset bucketing skips ``none`` at ``:45``. Pinned so a Python port that
    iterates ``BumpType`` cannot quietly grow a fourth section.

    ``ValueError``, and a bare ``KeyError`` is NOT acceptable. An earlier revision accepted
    either, which made a naive ``_HEADINGS[bump]`` lookup satisfy the test -- so nothing forced a
    deliberate guard, and the "no fourth section" rule was being enforced by an accident of dict
    indexing that any refactor to ``.get(bump, ...)`` would silently remove. Requiring a
    ``ValueError`` whose message names ``none`` forces the check to be written on purpose, and
    makes the failure legible to whoever hits it.

    Note this is about ``generate_markdown_for_version_type``, the *section* renderer, and does
    not contradict ``get_changelog_entry`` returning ``None`` for a ``none`` release
    (``test_a_none_release_produces_no_entry`` below). The entry-level function has a documented
    "nothing to write" answer; the section-level one has no section to render and no way to say
    so, because ``None`` there already means "this section is empty, omit it".
    """
    with pytest.raises(ValueError, match="none"):
        generate_markdown_for_version_type(NONE, ["- something"])


# ======================================================================================
# Row 3 -- per-line trimming
# ======================================================================================

TRIM_CASES = [
    ("\n  - something  \n", "row 3 verbatim: leading newline, indent, trailing spaces, newline"),
    ("- something", "already clean -- trimming is idempotent"),
    ("\t- something\t", "tabs count as surrounding whitespace"),
    ("\n\n\n- something\n\n\n", "the newlines are consumed by the clamp, not left in the text"),
]


@pytest.mark.parametrize(("line", "why"), TRIM_CASES)
def test_surrounding_whitespace_is_trimmed_from_each_release_line(line: str, why: str) -> None:
    """Row 3 (get-changelog-entry.test.ts:30-37).

    The trim is what lets a generator express *preferred spacing* through leading and trailing
    newlines without that whitespace reaching the file: the clamp reads the newlines, the trim
    removes them, and the clamp alone decides the gap.
    """
    assert (
        generate_markdown_for_version_type(MINOR, [line]) == "### Minor Changes\n\n- something"
    ), why


def test_trimming_does_not_touch_newlines_inside_a_release_line() -> None:
    """Only the ends are trimmed; a multi-paragraph summary keeps its internal blank line.

    This is the shape ``changelog-git`` produces for a multi-line summary
    (``changelog-git/src/index.ts:13-15``, and the worked example in research doc 04 section 3.5).
    The clamp governs the gap *between* release lines and must not reach inside one.
    """
    line = "\n- Random stuff\n\n  get it while it's hot!\n"
    assert generate_markdown_for_version_type(PATCH, [line]) == (
        "### Patch Changes\n\n- Random stuff\n\n  get it while it's hot!"
    )


# ======================================================================================
# Row 4 -- THE newline clamp
# ======================================================================================

#: get-changelog-entry.test.ts:42-51, verbatim.
CLAMP_FIXTURE = [
    "trimmed",
    "\nleading one",
    "\n\nleading two",
    "\n\n\nleading three",
    "trailing one\n",
    "trailing two\n\n",
    "trailing three\n\n\n",
    "\nmixed one\n",
    "\n\nmixed two\n\n",
    "\n\n\nmixed three\n\n\n",
]

#: get-changelog-entry.test.ts:54-71, verbatim.
CLAMP_EXPECTED = (
    "### Patch Changes\n"
    "\n"
    "trimmed\n"
    "leading one\n"
    "\n"
    "leading two\n"
    "\n"
    "leading three\n"
    "trailing one\n"
    "trailing two\n"
    "\n"
    "trailing three\n"
    "\n"
    "mixed one\n"
    "\n"
    "mixed two\n"
    "\n"
    "mixed three"
)


def test_spacing_between_release_lines_is_clamped_between_one_and_two_newlines() -> None:
    """Row 4 (get-changelog-entry.test.ts:39-73) -- the fixture and its output, both verbatim.

    Worth reading against the algorithm, because several rows are counter-intuitive:

    - ``trimmed`` gets two newlines because ``new_lines`` starts at 2 (the blank line after the
      heading), so the first gap is always exactly two regardless of the line's own preference.
    - ``leading three`` asks for three and gets two: ``min(max(n, 1), 2)``.
    - ``trailing one`` follows ``leading three``, which ended with zero trailing newlines, so the
      running count is 0 and the ``max(n, 1)`` floor produces the single newline.
    - ``mixed one`` follows ``trailing three``: the carried 3 plus its own leading 1 is 4, clamped
      back to 2. The carry is what makes the two generators' shapes work out.
    """
    assert generate_markdown_for_version_type(PATCH, CLAMP_FIXTURE) == CLAMP_EXPECTED


GENERATOR_SHAPE_CASES = [
    (
        ["- one", "- two", "- three"],
        "### Patch Changes\n\n- one\n- two\n- three",
        "changelog-git returns a bare `- ...` line (changelog-git/src/index.ts:9-17), so the "
        "carry is 0 and the max(n, 1) floor packs the bullets adjacently",
    ),
    (
        ["\n\n- one\n", "\n\n- two\n", "\n\n- three\n"],
        "### Patch Changes\n\n- one\n\n- two\n\n- three",
        "changelog-github returns `\\n\\n- ...\\n` (changelog-github/src/index.ts:187), so carry 1 "
        "plus leading 2 clamps to 2 and the bullets end up blank-line separated",
    ),
    (
        ["- one", "\n\n- two\n"],
        "### Patch Changes\n\n- one\n\n- two",
        "mixed generators in one section still clamp -- the state is per-gap, not per-generator",
    ),
]


@pytest.mark.parametrize(("lines", "expected", "why"), GENERATOR_SHAPE_CASES)
def test_the_two_default_generator_shapes_render_as_documented(
    lines: Sequence[str], expected: str, why: str
) -> None:
    """The clamp's whole purpose, stated as the two shapes it exists to reconcile.

    research doc 04 section 3.2 spells this out: one assembler, no generator-specific branching,
    and the difference in output comes entirely from the newlines the generator chose to put on
    its own string.
    """
    assert generate_markdown_for_version_type(PATCH, lines) == expected, why


def test_whitespace_only_release_lines_are_dropped() -> None:
    """DIVERGENCE, flagged for owner confirmation.

    Upstream filters on JavaScript truthiness (``get-changelog-entry.ts:126``), so a line of
    spaces survives the filter, contributes a gap, and then trims to nothing -- yielding
    ``"### Patch Changes\\n\\n\\n- real"``, three consecutive newlines, in direct contradiction of
    the clamp this function exists to enforce. There is no upstream test for it. molt filters on
    the *stripped* line, which makes the ``[1, 2]`` bound unconditional (see the second property
    below) and is the same "emit correct markdown, no formatter pass" call as research README
    section 3.4 items on blank lines.
    """
    assert generate_markdown_for_version_type(PATCH, ["   ", "- real"]) == (
        "### Patch Changes\n\n- real"
    )
    assert generate_markdown_for_version_type(PATCH, ["\n\n"]) is None
    assert generate_markdown_for_version_type(PATCH, ["", "  ", "\t"]) is None


_LINE_SHAPES = st.tuples(st.integers(0, 3), st.integers(0, 3), st.integers(0, 40))


def _build_lines(shapes: Sequence[tuple[int, int, int]]) -> list[str]:
    return [
        "\n" * lead + f"- item {index} {'x' * width}" + "\n" * trail
        for index, (lead, trail, width) in enumerate(shapes)
    ]


def _gaps(rendered: str) -> list[str]:
    return re.findall(r"\n+", rendered)


@pytest.mark.property
@given(shapes=st.lists(_LINE_SHAPES, min_size=1, max_size=8))
def test_every_gap_is_one_or_two_newlines(shapes: Sequence[tuple[int, int, int]]) -> None:
    """The invariant row 4 samples: no gap is ever 0, and none is ever 3 or more.

    Lines here carry no internal newlines, so every newline run in the output is a gap the clamp
    produced. A gap of 0 would weld two bullets into one; a gap of 3+ is what upstream's own
    ``changelog-github`` shape would produce without the clamp, and what the prettier step exists
    to clean up (research README section 5 item 13 -- molt has no such step).
    """
    rendered = generate_markdown_for_version_type(PATCH, _build_lines(shapes))
    assert rendered is not None
    assert all(1 <= len(gap) <= 2 for gap in _gaps(rendered))


@pytest.mark.property
@given(shapes=st.lists(_LINE_SHAPES, min_size=1, max_size=8))
def test_the_first_gap_after_the_heading_is_always_exactly_two(
    shapes: Sequence[tuple[int, int, int]],
) -> None:
    """``new_lines`` starts at 2 and only ever grows before the first clamp, so this cannot vary.

    It is what makes the blank line under ``### Patch Changes`` unconditional, which is what
    turns the section into a valid Markdown heading rather than a paragraph of literal ``###``.
    """
    rendered = generate_markdown_for_version_type(PATCH, _build_lines(shapes))
    assert rendered is not None
    assert rendered.startswith("### Patch Changes\n\n")


@pytest.mark.property
@given(shapes=st.lists(_LINE_SHAPES, min_size=1, max_size=8))
def test_every_release_line_survives_in_order(shapes: Sequence[tuple[int, int, int]]) -> None:
    """The clamp rewrites the gaps and nothing else: content and order are untouched."""
    lines = _build_lines(shapes)
    rendered = generate_markdown_for_version_type(PATCH, lines)
    assert rendered is not None
    assert re.findall(r"- item \d+", rendered) == [f"- item {i}" for i in range(len(lines))]


@pytest.mark.property
@given(
    shapes=st.lists(_LINE_SHAPES, min_size=1, max_size=6),
    blanks=st.lists(st.sampled_from(["", " ", "\t", "\n", "\n\n", "   \n  "]), max_size=6),
)
def test_the_clamp_holds_even_with_blank_lines_interleaved(
    shapes: Sequence[tuple[int, int, int]], blanks: Sequence[str]
) -> None:
    """The molt divergence, stated as a property rather than a single example.

    Under upstream's truthiness filter this fails: a whitespace-only line consumes a gap and
    contributes no text, so two gaps collapse together into a 3+ newline run. Under molt's
    stripped filter the ``[1, 2]`` bound is unconditional for arbitrary input, which is what makes
    it safe to hand a section lines from a third-party generator plugin.
    """
    lines = _build_lines(shapes)
    interleaved: list[str] = []
    for index, line in enumerate(lines):
        if index < len(blanks):
            interleaved.append(blanks[index])
        interleaved.append(line)
    rendered = generate_markdown_for_version_type(PATCH, interleaved)
    assert rendered is not None
    assert all(1 <= len(gap) <= 2 for gap in _gaps(rendered))


# ======================================================================================
# get_changelog_entry -- assembly (research doc 04 section 3.1)
# ======================================================================================

BASE_SUMMARY = "Hey, let's have fun with testing!"


def test_a_none_release_produces_no_entry() -> None:
    """``get-changelog-entry.ts:31`` -- and therefore **no CHANGELOG.md write at all**.

    Group file index row 14 asserts the same thing through ``apply``. The consequence is bigger
    than an empty section: a ``none`` release must not create or touch the file, so an
    ``## <version>`` heading for a version that was never published cannot appear.
    """
    release = Release("pkg-a", NONE, v("1.0.0"), v("1.0.0"))
    assert get_changelog_entry(release, [release], [], GIT) is None


def test_an_entry_is_a_version_heading_plus_its_sections() -> None:
    """``get-changelog-entry.ts:111-118`` -- ``["## <version>", major?, minor?, patch?]``.

    Group file index row 28 (``index.test.ts:2118-2155``) is the same assertion through ``apply``,
    where it also acquires the ``# pkg-a`` file title.
    """
    release = Release("pkg-a", MINOR, v("1.0.0"), v("1.1.0"), ("quick-lions-devour",))
    changesets = [
        Changeset("quick-lions-devour", BASE_SUMMARY, (ChangesetRelease("pkg-a", MINOR),))
    ]
    assert get_changelog_entry(release, [release], changesets, GIT) == (
        f"## 1.1.0\n\n### Minor Changes\n\n- {BASE_SUMMARY}"
    )


def test_sections_render_major_then_minor_then_patch_joined_by_a_blank_line() -> None:
    """``get-changelog-entry.ts:112-118`` -- fixed order, ``"\\n\\n"`` join.

    Also confirmed as intended molt behaviour by ``website/docs/guides/changelog-templates.md``
    ("Sections render in order: Major, then Minor, then Patch").
    """
    release = Release("pkg-a", MAJOR, v("1.0.0"), v("2.0.0"), ("cs-a", "cs-b", "cs-c"))
    changesets = [
        Changeset("cs-a", "Drop Python 3.10", (ChangesetRelease("pkg-a", MAJOR),)),
        Changeset("cs-b", "Add streaming", (ChangesetRelease("pkg-a", MINOR),)),
        Changeset("cs-c", "Fix a typo", (ChangesetRelease("pkg-a", PATCH),)),
    ]
    assert get_changelog_entry(release, [release], changesets, GIT) == (
        "## 2.0.0\n"
        "\n"
        "### Major Changes\n"
        "\n"
        "- Drop Python 3.10\n"
        "\n"
        "### Minor Changes\n"
        "\n"
        "- Add streaming\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- Fix a typo"
    )


def test_an_entry_with_no_release_lines_is_just_the_version_heading() -> None:
    """``.filter()`` drops all three sections, and ``join`` is left with one element.

    Reachable in practice: a package pulled into a release by ``fixed``, whose own changesets are
    all ``none`` and whose dependency line came back empty.
    """
    release = Release("pkg-a", PATCH, v("1.0.0"), v("1.0.1"))
    assert get_changelog_entry(release, [release], [], GIT) == "## 1.0.1"


def test_changeset_order_follows_release_plan_order() -> None:
    """``changesets.forEach`` (``get-changelog-entry.ts:43``) preserves the caller's order.

    The engine's output is insertion-ordered (research README section 3.1), so this is what makes
    changelog bullet order deterministic across runs and reviewable in a diff.
    """
    release = Release("pkg-a", MINOR, v("1.0.0"), v("1.1.0"), ("cs-second", "cs-first"))
    changesets = [
        Changeset("cs-second", "Second in the list", (ChangesetRelease("pkg-a", MINOR),)),
        Changeset("cs-first", "First in the list", (ChangesetRelease("pkg-a", MINOR),)),
    ]
    assert get_changelog_entry(release, [release], changesets, GIT) == (
        "## 1.1.0\n\n### Minor Changes\n\n- Second in the list\n- First in the list"
    )


def test_a_changeset_that_does_not_release_this_package_is_ignored() -> None:
    """``cs.releases.find(r => r.name === release.name)`` (``get-changelog-entry.ts:44``).

    ``changesets`` is the whole plan's changeset list, not a per-package one, so the filter is
    what stops pkg-b's summary from landing in pkg-a's changelog.
    """
    release = Release("pkg-a", MINOR, v("1.0.0"), v("1.1.0"), ("cs-a",))
    changesets = [
        Changeset("cs-a", "Mine", (ChangesetRelease("pkg-a", MINOR),)),
        Changeset("cs-b", "Not mine", (ChangesetRelease("pkg-b", MINOR),)),
    ]
    entry = get_changelog_entry(release, [release], changesets, GIT)
    assert entry == "## 1.1.0\n\n### Minor Changes\n\n- Mine"
    assert "Not mine" not in entry


def test_a_none_release_inside_a_changeset_contributes_no_line() -> None:
    """``rls.type !== "none"`` (``get-changelog-entry.ts:45``) -- ``none`` buckets nowhere.

    There is no ``### None Changes`` section for it to land in, so the summary is simply not
    rendered for that package.
    """
    release = Release("pkg-a", MINOR, v("1.0.0"), v("1.1.0"), ("cs-a", "cs-b"))
    changesets = [
        Changeset("cs-a", "Real change", (ChangesetRelease("pkg-a", MINOR),)),
        Changeset("cs-b", "Bookkeeping only", (ChangesetRelease("pkg-a", NONE),)),
    ]
    entry = get_changelog_entry(release, [release], changesets, GIT)
    assert entry == "## 1.1.0\n\n### Minor Changes\n\n- Real change"
    assert "Bookkeeping only" not in entry


def test_options_and_forge_are_passed_through_to_the_generator_on_every_call() -> None:
    """The generator protocol is four-parameter (``changeset, bump, options, forge``).

    ``website/docs/extending/changelog-plugins.md`` declares it under "The contract" and
    ``website/docs/extending/custom-generators.md`` implements it that way in its worked
    example, where ``forge`` is what turns a commit sha into a pull-request link. The
    assembler reads neither argument; it only forwards both, unchanged, to **both** generator
    methods.

    Pinned explicitly because every other test in this file drives :data:`GIT`, which ignores
    ``options`` and ``forge`` entirely. Against that double, an assembler with a three-parameter
    protocol -- or one that accepted ``forge`` and then always forwarded ``None`` -- passes all
    of them while being structurally incapable of driving ``molt.changelog.github``: the
    generator would have nothing to resolve links through, so the GitHub changelog would
    silently degrade to plain ``git`` output. This is the test that fails in that case.

    ``is`` identity, not ``==``: a forge is a stateful client whose cache is the point
    (``tests/changelog/test_changelog.py`` pins one HTTP call per ``repo+kind+id``), so an
    assembler that copied or rebuilt it per call would defeat the cache while comparing equal.
    """
    forge = StubForge()
    options: Mapping[str, object] = {"repo": "acme/widgets"}
    generator = RecordingGenerator()

    pkg_a = Release("pkg-a", PATCH, v("1.0.0"), v("1.0.1"), ("cs-a",))
    pkg_b = Release("pkg-b", MINOR, v("1.0.0"), v("1.1.0"), ("cs-b",))
    changesets = [
        Changeset("cs-a", "own change", (ChangesetRelease("pkg-a", PATCH),)),
        Changeset("cs-b", "b changed", (ChangesetRelease("pkg-b", MINOR),)),
    ]

    entry = get_changelog_entry(
        pkg_a,
        [pkg_a, pkg_b],
        changesets,
        generator,
        deps=("pkg-b>=1.0.0,<2.0.0",),
        options=options,
        forge=forge,
    )

    assert entry == (
        "## 1.0.1\n\n### Patch Changes\n\n- own change\n- Updated dependencies\n  - pkg-b@1.1.0"
    )
    assert generator.release_calls == [(options, forge)], (
        "get_release_line must receive the options table and the forge verbatim"
    )
    assert generator.dependency_calls == [(options, forge)], (
        "get_dependency_release_line takes the same two trailing arguments -- upstream's "
        "getDependencyReleaseLine gets `options` too (get-changelog-entry.ts:105-110)"
    )
    assert generator.release_calls[0][1] is forge, "the forge instance itself, not a copy"
    assert generator.dependency_calls[0][1] is forge


def test_a_generator_may_ignore_options_and_forge() -> None:
    """The mirror of the test above: ``forge=None`` is a supported call, not an accident.

    ``website/docs/extending/changelog-plugins.md``: "A generator that does not need a forge
    simply ignores the argument; ``forge`` is ``None`` when no forge is configured." That is
    the default configuration -- ``git`` is the default generator and needs no network -- so
    the no-forge path is the common one, not the fallback.
    """
    release = Release("pkg-a", MINOR, v("1.0.0"), v("1.1.0"), ("cs-a",))
    changesets = [Changeset("cs-a", BASE_SUMMARY, (ChangesetRelease("pkg-a", MINOR),))]

    with_defaults = get_changelog_entry(release, [release], changesets, GIT)
    explicit_none = get_changelog_entry(
        release, [release], changesets, GIT, options=None, forge=None
    )

    assert with_defaults == explicit_none == (f"## 1.1.0\n\n### Minor Changes\n\n- {BASE_SUMMARY}")


# ======================================================================================
# get_changelog_entry -- the dependency line (research doc 04 section 3.1, README section 3.3)
# ======================================================================================


def _dependency_fixture(
    own_bump: BumpType, own_version: str
) -> tuple[Release, list[Release], list[Changeset]]:
    """pkg-a (bumped by its own changesets) depending on pkg-b (bumped minor, 1.0.0 -> 1.1.0)."""
    pkg_a = Release("pkg-a", own_bump, v("1.0.0"), v(own_version), ("cs-own", "cs-small"))
    pkg_b = Release("pkg-b", MINOR, v("1.0.0"), v("1.1.0"), ("cs-dep",))
    changesets = [
        Changeset("cs-own", "The headline change", (ChangesetRelease("pkg-a", own_bump),)),
        Changeset("cs-small", "Fix a typo", (ChangesetRelease("pkg-a", PATCH),)),
        Changeset("cs-dep", "Add streaming to pkg-b", (ChangesetRelease("pkg-b", MINOR),)),
    ]
    return pkg_a, [pkg_a, pkg_b], changesets


DEPENDENCY_PLACEMENT_CASES = [
    (
        MAJOR,
        "2.0.0",
        "## 2.0.0\n"
        "\n"
        "### Major Changes\n"
        "\n"
        "- The headline change\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- Fix a typo\n"
        "- Updated dependencies\n"
        "  - pkg-b@1.1.0",
        "a major release still files its dependency bump under Patch",
    ),
    (
        MINOR,
        "1.1.0",
        "## 1.1.0\n"
        "\n"
        "### Minor Changes\n"
        "\n"
        "- The headline change\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- Fix a typo\n"
        "- Updated dependencies\n"
        "  - pkg-b@1.1.0",
        "and so does a minor one",
    ),
    (
        PATCH,
        "1.0.1",
        "## 1.0.1\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- The headline change\n"
        "- Fix a typo\n"
        "- Updated dependencies\n"
        "  - pkg-b@1.1.0",
        "when the own bump is already patch, the dependency line still sorts last",
    ),
]


@pytest.mark.parametrize(("own_bump", "own_version", "expected", "why"), DEPENDENCY_PLACEMENT_CASES)
def test_dependency_bumps_are_always_last_and_always_in_the_patch_section(
    own_bump: BumpType, own_version: str, expected: str, why: str
) -> None:
    """``get-changelog-entry.ts:95-103`` -- pushed into ``patch``, and pushed *last*.

    Load-bearing (research README section 3.3; group file index row 30). The rationale is in
    ``website/docs/guides/changelog-templates.md``: the dependent's own code did not change, so
    however large the dependency's release was, the dependent is only being re-released to keep
    its pin valid. Two things are pinned here at once -- the *section* and the *position*.
    """
    release, releases, changesets = _dependency_fixture(own_bump, own_version)
    entry = get_changelog_entry(release, releases, changesets, GIT, deps=("pkg-b>=1.0.0,<2.0.0",))
    assert entry == expected, why


INVISIBLE_SECTION_CASES = [
    (
        "dev_deps",
        "PEP 735 [dependency-groups] -- molt's devDependencies. Upstream reads only "
        "`dependencies` and `peerDependencies` (get-changelog-entry.ts:54-58)",
    ),
    (
        "optional_deps",
        "[project.optional-dependencies]. NOTE: this is the *changelog* rule only. It does not "
        "contradict the P2 engine decision that optional-dependencies propagate like runtime "
        "deps -- a package can be bumped without its changelog naming the dependency",
    ),
]


@pytest.mark.parametrize(("section", "why"), INVISIBLE_SECTION_CASES)
def test_dev_and_optional_dependencies_never_produce_an_updated_dependencies_line(
    section: str, why: str
) -> None:
    """Group file index row 31; research README section 3.3 ("devDeps invisible").

    The paired positive assertion matters as much as the negative one: the same release, moved
    into ``deps``, *does* produce the line, so this is proving a rule rather than a broken fixture.
    """
    release, releases, changesets = _dependency_fixture(PATCH, "1.0.1")
    # `section` is a test parameter, so no type checker can tell which keyword this unpack fills;
    # pyrefly checks the tuple against `update_internal_dependencies` and `options` too and rejects
    # both. Naming the two sections literally instead would lose the point of the parametrization,
    # which is that "which section" is data.
    hidden = get_changelog_entry(
        release,
        releases,
        changesets,
        GIT,
        **{section: ("pkg-b>=1.0.0,<2.0.0",)},  # pyrefly: ignore[bad-argument-type]
    )
    assert hidden is not None, "a patch release always produces an entry"
    assert "Updated dependencies" not in hidden, why
    assert "pkg-b" not in hidden, why

    visible = get_changelog_entry(release, releases, changesets, GIT, deps=("pkg-b>=1.0.0,<2.0.0",))
    assert visible is not None
    assert "- Updated dependencies\n  - pkg-b@1.1.0" in visible


def test_only_dependencies_that_were_updated_are_listed() -> None:
    """Group file index row 35 -- selectivity.

    Three candidates, one line: a released dependency this package does not declare, a declared
    dependency whose release type is ``none``, and the one real bump.
    """
    pkg_a = Release("pkg-a", PATCH, v("1.0.0"), v("1.0.1"), ())
    pkg_b = Release("pkg-b", MINOR, v("1.0.0"), v("1.1.0"), ("cs-b",))
    pkg_c = Release("pkg-c", NONE, v("2.0.0"), v("2.0.0"), ("cs-c",))
    pkg_d = Release("pkg-d", MAJOR, v("1.0.0"), v("2.0.0"), ("cs-d",))
    changesets = [
        Changeset("cs-b", "b changed", (ChangesetRelease("pkg-b", MINOR),)),
        Changeset("cs-c", "c did not", (ChangesetRelease("pkg-c", NONE),)),
        Changeset("cs-d", "d changed", (ChangesetRelease("pkg-d", MAJOR),)),
    ]
    entry = get_changelog_entry(
        pkg_a,
        [pkg_a, pkg_b, pkg_c, pkg_d],
        changesets,
        GIT,
        deps=("pkg-b>=1.0.0,<2.0.0", "pkg-c>=2.0.0,<3.0.0"),
    )
    assert entry == ("## 1.0.1\n\n### Patch Changes\n\n- Updated dependencies\n  - pkg-b@1.1.0")
    assert "pkg-c" not in entry
    assert "pkg-d" not in entry


NO_CONSTRAINT_CASES = [
    (
        "pkg-b @ file:///C:/src/pkg-b",
        "direct reference (path). ``validRange(versionRange) != null``, "
        "``get-changelog-entry.ts:62``, in PEP 508 terms: upstream's guard skips npm dist-tags and "
        "`file:`/`link:` protocols, and a PEP 508 direct reference is the Python analogue",
    ),
    (
        "pkg-b @ git+https://example.invalid/pkg-b.git@v1",
        "direct reference (VCS) -- same guard",
    ),
    (
        "pkg-b latest",
        "not a parseable requirement at all -- the closest analogue of `bulbasaur` "
        "(group file index row 21)",
    ),
    (
        "pkg-b",
        "DIVERGENCE, flagged for owner confirmation. Upstream's `*` passes `validRange` and then "
        "`semver.satisfies(new, '*')`, so a `*` dependency IS listed under Updated dependencies "
        "even though version-package.ts:93-102 refuses to rewrite it -- the changelog claims an "
        "update the manifest never made. molt keeps the two consistent: an unconstrained "
        "dependency 'never triggers a bump' "
        "(website/docs/guides/dependency-propagation.md), so it is never recorded either",
    ),
]


@pytest.mark.parametrize(("declared", "why"), NO_CONSTRAINT_CASES)
def test_a_dependency_with_no_live_constraint_produces_no_updated_dependencies_line(
    declared: str, why: str
) -> None:
    """A constraint that cannot go stale is a constraint whose update is not worth recording.

    Direct references carry no version specifier at all, and an unconstrained requirement is
    satisfied by every version, so in both cases the manifest is left untouched by ``apply``
    (doc 04 section 2.4 rules 1, 4 and 6) and the changelog has nothing to report. The first three
    rows are upstream-faithful; the fourth is a deliberate divergence, spelled out in its ``why``.
    """
    pkg_a = Release("pkg-a", PATCH, v("1.0.0"), v("1.0.1"), ())
    pkg_b = Release("pkg-b", MINOR, v("1.0.0"), v("1.1.0"), ("cs-b",))
    changesets = [Changeset("cs-b", "b changed", (ChangesetRelease("pkg-b", MINOR),))]
    entry = get_changelog_entry(pkg_a, [pkg_a, pkg_b], changesets, GIT, deps=(declared,))
    assert entry == "## 1.0.1", why


# PEP 503 normalization at the changelog layer (P4 shared brief section 9; Session 2 decision 4:
# normalize at COMPARISON time, preserve the author's literal spelling on disk).
#
# `(release_name, declared, expected_listed_name, why)`. `expected_listed_name` is what the
# `- <name>@<version>` bullet must read, and it is always the RELEASE's own spelling -- the
# release plan is the authority on what a distribution is called, and the changelog reports what
# was released, not how one particular dependent happened to spell the requirement.
NORMALIZED_NAME_CASES = [
    (
        "Foo_Bar",
        "foo-bar>=1.0.0,<2.0.0",
        "Foo_Bar",
        "release spelled Foo_Bar, requirement spelled foo-bar -- PEP 503 makes them the same "
        "project, so the line must appear",
    ),
    (
        "foo-bar",
        "Foo_Bar>=1.0.0,<2.0.0",
        "foo-bar",
        "the reverse direction: the odd spelling is in the manifest this time. Normalization "
        "has to happen on BOTH sides, so a `canonicalize_name(release.name) == dep.name` "
        "half-fix fails here",
    ),
    (
        "Foo.Bar",
        "foo_bar>=1.0.0,<2.0.0",
        "Foo.Bar",
        "PEP 503 folds runs of `-`, `_` and `.` to a single `-`, so a dot and an underscore "
        "are the same separator; a normalizer that only handled `_`->`-` fails this row",
    ),
    (
        "FOO--BAR",
        "foo-bar>=1.0.0,<2.0.0",
        "FOO--BAR",
        "case folding plus the RUN collapse (`--` -> `-`), the two halves of the PEP 503 "
        "rule `re.sub(r'[-_.]+', '-', name).lower()`",
    ),
]


@pytest.mark.parametrize(
    ("release_name", "declared", "expected_listed_name", "why"), NORMALIZED_NAME_CASES
)
def test_dependency_matching_normalizes_names_per_pep_503(
    release_name: str, declared: str, expected_listed_name: str, why: str
) -> None:
    """P4 shared brief section 9; research README section 4.5. ``Foo_Bar`` == ``foo-bar``.

    Two rules at once, and they pull in opposite directions:

    1. **Matching is normalized.** ``packaging.utils.canonicalize_name`` is applied to both the
       release name and the requirement's name before they are compared, so any of PEP 503's
       equivalent spellings of one project finds the same release.
    2. **Rendering is literal.** The bullet keeps the release's own spelling. Session 2 decision
       4 is "normalize at comparison time, preserve the literal spelling on disk" -- a changelog
       that silently renamed ``Foo_Bar`` to ``foo-bar`` would stop matching what the author
       wrote in ``pyproject.toml`` and what PyPI shows on the project page.

    Every other ``deps=`` fixture in this file declares ``pkg-b`` in both places, already
    canonical, so all of them pass against an implementation that compares ``Requirement.name``
    verbatim. A mutation replacing ``canonicalize_name`` with a raw string comparison left the
    whole module green; these four rows are what discriminate it.
    """
    pkg_a = Release("pkg-a", PATCH, v("1.0.0"), v("1.0.1"), ())
    dep = Release(release_name, MINOR, v("1.0.0"), v("1.1.0"), ("cs-dep",))
    changesets = [Changeset("cs-dep", "dep changed", (ChangesetRelease(release_name, MINOR),))]

    entry = get_changelog_entry(pkg_a, [pkg_a, dep], changesets, GIT, deps=(declared,))

    assert entry == (
        f"## 1.0.1\n\n### Patch Changes\n\n- Updated dependencies\n  - {expected_listed_name}@1.1.0"
    ), why


def test_a_genuinely_different_name_is_not_matched_by_normalization() -> None:
    """Anti-vacuity guard for the four rows above.

    PEP 503 normalization folds separators and case; it does not fold *characters*. An
    implementation that normalized too aggressively -- stripping every non-alphanumeric, say,
    or comparing on a prefix -- would pass the whole table above and then attribute one
    distribution's release to another's changelog. ``foobar`` and ``foo-bar`` normalize to
    different names and must not match.
    """
    pkg_a = Release("pkg-a", PATCH, v("1.0.0"), v("1.0.1"), ())
    dep = Release("foobar", MINOR, v("1.0.0"), v("1.1.0"), ("cs-dep",))
    changesets = [Changeset("cs-dep", "dep changed", (ChangesetRelease("foobar", MINOR),))]

    entry = get_changelog_entry(
        pkg_a, [pkg_a, dep], changesets, GIT, deps=("foo-bar>=1.0.0,<2.0.0",)
    )

    assert entry == "## 1.0.1", "foobar != foo-bar under PEP 503; no line"


UPDATE_GATE_CASES = [
    (
        MINOR,
        "pkg-b>=1.0.0,<2.0.0",
        False,
        "group file index row 34: min gate is minor, pkg-b only patched and stays in range, so "
        "no line (utils.ts:74-75)",
    ),
    (
        PATCH,
        "pkg-b>=1.0.0,<2.0.0",
        True,
        "the default gate is patch, so the same patch bump does produce a line",
    ),
    (
        MINOR,
        "pkg-b==1.0.0",
        True,
        "group file index row 36: an exact pin leaves its range on any bump, and out-of-range "
        "always wins over the min gate (utils.ts:69-72)",
    ),
]


@pytest.mark.parametrize(("gate", "declared", "expected_line", "why"), UPDATE_GATE_CASES)
def test_the_update_internal_dependencies_gate_decides_whether_a_line_appears(
    gate: BumpType, declared: str, expected_line: bool, why: str
) -> None:
    """``shouldUpdateDependencyBasedOnConfig`` reaches the changelog too (doc 04 sections 2.5/3.1).

    The changelog and the manifest use the same predicate on purpose: a dependency line that
    claims an update the manifest did not make would be a lie in a published artifact.
    """
    pkg_a = Release("pkg-a", PATCH, v("1.0.0"), v("1.0.1"), ())
    pkg_b = Release("pkg-b", PATCH, v("1.0.0"), v("1.0.1"), ("cs-b",))
    changesets = [Changeset("cs-b", "b patched", (ChangesetRelease("pkg-b", PATCH),))]
    entry = get_changelog_entry(
        pkg_a,
        [pkg_a, pkg_b],
        changesets,
        GIT,
        deps=(declared,),
        update_internal_dependencies=gate,
    )
    assert entry is not None, "pkg-a takes a patch release either way, so there is always an entry"
    assert ("Updated dependencies" in entry) is expected_line, why


def test_the_dependency_line_gets_one_bullet_per_relevant_changeset() -> None:
    """``relevantChangesets`` is the union of the updated dependencies' changeset ids.

    ``get-changelog-entry.ts:83-93``. It is what lets ``changelog-github`` attribute the dependency
    bump to the right commits, and it must not include changesets belonging to dependencies that
    were filtered out.
    """
    pkg_a = Release("pkg-a", PATCH, v("1.0.0"), v("1.0.1"), ())
    pkg_b = Release("pkg-b", MINOR, v("1.0.0"), v("1.1.0"), ("cs-b1", "cs-b2"))
    pkg_e = Release("pkg-e", MINOR, v("1.0.0"), v("1.1.0"), ("cs-e",))
    changesets = [
        Changeset("cs-b1", "b one", (ChangesetRelease("pkg-b", MINOR),)),
        Changeset("cs-b2", "b two", (ChangesetRelease("pkg-b", PATCH),)),
        Changeset("cs-e", "e one", (ChangesetRelease("pkg-e", MINOR),)),
    ]
    entry = get_changelog_entry(
        pkg_a, [pkg_a, pkg_b, pkg_e], changesets, GIT, deps=("pkg-b>=1.0.0,<2.0.0",)
    )
    assert entry == (
        "## 1.0.1\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- Updated dependencies\n"
        "- Updated dependencies\n"
        "  - pkg-b@1.1.0"
    )


# ======================================================================================
# Divergences from upstream, asserted rather than merely noted
# ======================================================================================


def test_markdown_headings_inside_a_summary_are_preserved() -> None:
    """DIVERGENCE. research README section 3.4: upstream strips every line starting with ``#``.

    That post-processing silently deletes the heading structure of any changeset written as real
    Markdown -- a body like ``# Breaking\\n...`` loses its title. molt treats a summary as literal
    prose (``website/docs/guides/changelog-templates.md``, "Your summaries are preserved
    literally"), so the assembler must pass ``#`` lines through untouched.
    """
    summary = "# Breaking change\n\n## What moved\n\nThe export API is now streaming-only."
    release = Release("pkg-a", MAJOR, v("1.0.0"), v("2.0.0"), ("cs-a",))
    changesets = [Changeset("cs-a", summary, (ChangesetRelease("pkg-a", MAJOR),))]
    entry = get_changelog_entry(release, [release], changesets, GIT)
    assert entry == (
        "## 2.0.0\n"
        "\n"
        "### Major Changes\n"
        "\n"
        "- # Breaking change\n"
        "\n"
        "  ## What moved\n"
        "\n"
        "  The export API is now streaming-only."
    )
    assert "# Breaking change" in entry
    assert "## What moved" in entry


ADVERSARIAL_PAYLOADS = [
    (r"Fix the $1 placeholder in the export template", "a regex replacement-string group"),
    (r"Escape \1 and \2 correctly", "backslash group references"),
    (r"Handle \g<0> in user templates", "a Python named-group backreference"),
    (r"Costs $0.50, or 100% of $$ if you round up", "literal dollar signs and a percent"),
    (r"Backslash at the end \\", "a trailing backslash pair"),
    (r"Windows path C:\1\2\temp", "a path that reads as backreferences"),
]


@pytest.mark.parametrize(("summary", "why"), ADVERSARIAL_PAYLOADS)
def test_regex_metacharacters_in_a_summary_survive_verbatim(summary: str, why: str) -> None:
    """DIVERGENCE. research README section 3.3/3.4 -- replacement must be *function-based*.

    ``re.sub(pattern, replacement_string, text)`` expands ``\\1`` and ``\\g<0>`` inside the
    replacement, so any substitution that routes user text through the replacement argument
    corrupts it -- upstream has shipped exactly this bug for shas containing ``$``. In Python the
    fix is a replacement **callable**, or no ``re.sub`` at all on this path.

    SCOPE, stated honestly. These twelve cases (this test and the one below) are **pass-through
    guards, not discriminators.** In Python the ``\\1``-expansion hazard lives entirely in
    ``re.sub``'s *replacement* position; ``re.sub`` never interprets its subject, and this
    assembler has no substitution step at all, so a mutation that swapped a replacement callable
    for a replacement string here has nothing to corrupt and leaves both tables green. That was
    confirmed by a mutation run against a reference implementation.

    What actually discriminates the bug is
    ``tests/changelog/test_render_template.py::test_render_template_does_not_interpret_regex_replacement_syntax``
    -- there the token value genuinely passes through a substitution, and a replacement-string
    implementation fails it. What these rows do earn their place for is the *other* half of the
    promise in ``website/docs/guides/changelog-templates.md`` ("Your summaries are preserved
    literally"): they pin that the assembly path performs no rewriting of author prose at all,
    which is a regression guard on a rule that is easy to break by adding a "helpful"
    normalization step later. Same framing as
    ``tests/changelog/test_changelog.py::test_regex_replacement_syntax_in_a_summary_survives_linkification``.
    """
    release = Release("pkg-a", PATCH, v("1.0.0"), v("1.0.1"), ("cs-a",))
    changesets = [Changeset("cs-a", summary, (ChangesetRelease("pkg-a", PATCH),))]
    entry = get_changelog_entry(release, [release], changesets, GIT)
    assert entry == f"## 1.0.1\n\n### Patch Changes\n\n- {summary}", why


@pytest.mark.parametrize(("summary", "why"), ADVERSARIAL_PAYLOADS)
def test_regex_metacharacters_survive_the_section_renderer_too(summary: str, why: str) -> None:
    """The same payload one layer down, so a failure localises to the right function.

    Same scope caveat as the test above: a pass-through guard on "no rewriting of author prose",
    not the discriminator for the replacement-string bug.
    """
    assert generate_markdown_for_version_type(PATCH, [f"- {summary}"]) == (
        f"### Patch Changes\n\n- {summary}"
    ), why
