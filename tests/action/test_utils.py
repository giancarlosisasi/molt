"""Conformance tests for the changelog-scraping half of ``molt.action``.

Ports ``packages/release-utils/src/utils.test.ts`` (5 rows, all Port) -- the second
``release-utils`` section of ``roadmap/research/test-suite/08-infra-forge-utils.md`` and research
doc 04 section 6.3. These four functions exist solely so the action can slice one version's section
back out of a package's just-written ``CHANGELOG.md`` to build a PR body / GitHub Release notes
(doc 04 section 6.3); they are pure string parsing, no filesystem, no git, no network.

Why explicit literals instead of syrupy snapshots
--------------------------------------------------
The group file marks these ``toMatchSnapshot()`` / Type=snapshot, but the phase brief (section 6)
is explicit: prefer literals. ``molt.action`` is guarded by ``importorskip`` and never runs today,
so a ``--snapshot-update`` run has no baseline to write against -- an absent baseline records
nothing, and whatever the eventual implementation emits would silently become "correct". The
upstream snapshot file (``packages/release-utils/src/__snapshots__/utils.test.ts.snap``) already
gives the exact expected bytes, so they are ported here as literals instead, verified against that
file line-for-line. Same rationale as ``tests/apply/test_apply.py`` and
``tests/apply/test_changelog_entry.py``. The ``snapshot`` marker therefore goes unused; these are
``unit``.

Load-bearing fact: fence-length matching
-----------------------------------------
``getChangelogEntry``'s heading/fence scanner (``utils.ts:39-55``) treats a code-fence *closer* as
any line whose backtick run is **at least** as long as the opener's -- there is no upper bound and
no end-of-line anchor. That is what lets a 4-backtick outer fence swallow a complete 3-backtick
fenced block without prematurely closing on the inner fence's 3 backticks, and it is exactly the
detail an implementation that "closes on any triple-backtick line" gets wrong
(:func:`test_get_changelog_entry_extracts_the_named_version_section`, nested-fence row).

Surface pinned by this file (test-contract section 5, ``molt.action``)
------------------------------------------------------------------------
``BUMP_LEVELS: dict[str, int]`` (``{"dep": 0, "patch": 1, "minor": 2, "major": 3}``, snake_case
port of upstream's ``BumpLevels``); ``get_changelog_entry(changelog, version) -> ChangelogEntry``
where ``ChangelogEntry`` exposes ``.content: str`` and ``.highest_level: int`` (snake_case, per
project decision 6); ``sort_changelog_entries(entries) -> list`` operating on any sequence of
mappings carrying ``"private"`` and ``"highest_level"`` keys.
"""

from __future__ import annotations

import pytest

pytest.importorskip("molt.action", reason="build step 9 - molt.action is a TDD target")

from molt.action import (  # pyrefly: ignore[missing-import]
    BUMP_LEVELS,
    get_changelog_entry,
    sort_changelog_entries,
)

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------------------
# Fixture changelog -- utils.test.ts:8-73, verbatim (multiple packages, multiple heading
# depths, so the "closing heading of the SAME depth" rule and the file-title `#` are exercised).
# --------------------------------------------------------------------------------------

# The two `voussoir`/`keystone-alpha` migration-note lines run past 100 columns in the upstream
# fixture; split via implicit adjacent-string concatenation so the byte content is untouched.
CHANGELOG = "\n".join(
    [
        "# @keystone-alpha/email",
        "",
        "## 3.0.1",
        "",
        "### Patch Changes",
        "",
        "- [19fe6c1b](https://github.com/keystonejs/keystone-5/commit/19fe6c1b):",
        "",
        "  Move frontmatter in docs into comments",
        "",
        "## 3.0.0",
        "",
        "### Major Changes",
        "",
        "- [2164a779](https://github.com/keystonejs/keystone-5/commit/2164a779):",
        "",
        "  - Replace jade with pug because Jade was renamed to Pug, and `jade` package is outdated",
        "",
        "### Patch Changes",
        "",
        "- [81dc0be5](https://github.com/keystonejs/keystone-5/commit/81dc0be5):",
        "",
        "  - Update dependencies",
        "",
        "## 2.0.0",
        "",
        "- [patch][b69fb9b7](https://github.com/keystonejs/keystone-5/commit/b69fb9b7):",
        "",
        "  - Update dev devependencies",
        "",
        "- [major][f97e4ecf](https://github.com/keystonejs/keystone-5/commit/f97e4ecf):",
        "",
        "  - Export { emailSender } as the API, rather than a default export",
        "",
        "## 1.0.2",
        "",
        "- [patch][7417ea3a](https://github.com/keystonejs/keystone-5/commit/7417ea3a):",
        "",
        "  - Update patch-level dependencies",
        "",
        "## 1.0.1",
        "",
        "- [patch][1f0bc236](https://github.com/keystonejs/keystone-5/commit/1f0bc236):",
        "",
        '  - Update the package.json author field to "The Keystone Development Team"',
        "",
        "## 1.0.0",
        "",
        "- [major] 8b6734ae:",
        "",
        "  - This is the first release of keystone-alpha (previously voussoir).",
        "    All packages in the `@voussoir` namespace are now available in the "
        "`@keystone-alpha` namespace, starting at version `1.0.0`.",
        "    To upgrade your project you must update any `@voussoir/<foo>` dependencies in "
        '`package.json` to point to `@keystone-alpha/<foo>: "^1.0.0"` and update any '
        "`require`/`import` statements in your code.",
        "",
        "# @voussoir/email",
        "",
        "## 0.0.2",
        "",
        "- [patch] 113e16d4:",
        "",
        "  - Remove unused dependencies",
        "",
        "- [patch] 625c1a6d:",
        "",
        "  - Update mjml-dependency",
        "",
    ]
)


# --------------------------------------------------------------------------------------
# BUMP_LEVELS itself -- the scale every other row in this file compares against.
# utils.ts:4-9.
# --------------------------------------------------------------------------------------


def test_bump_levels_are_the_documented_ordered_scale() -> None:
    """``utils.ts:4-9`` -- ``{dep: 0, patch: 1, minor: 2, major: 3}``.

    ANTI-VACUITY, and the reason this row exists at all: every ``highest_level`` assertion below
    is written ``== BUMP_LEVELS["major"]``, i.e. against the implementation's *own* constant. If
    the constant were wrong the comparison would still hold, so without this row the whole scale
    is unpinned. Verified by mutation: flattening ``minor`` to ``1`` leaves every other row in
    this file green.

    The values are not decorative. ``sort_changelog_entries`` subtracts them (``utils.ts:91``),
    so the *ordering and the gaps* are the contract -- ``dep < patch < minor < major`` -- and a
    collapsed ``minor``/``patch`` pair silently reorders a PR body's package list.
    """
    assert BUMP_LEVELS == {"dep": 0, "patch": 1, "minor": 2, "major": 3}


def test_a_minor_changes_heading_is_recognized_as_minor() -> None:
    """``utils.ts:61`` -- the level scanner's alternation is ``(major|minor|patch)``.

    Upstream's five rows only ever produce ``major`` or ``patch``, so an implementation that
    transcribed the alternation as ``(major|patch)`` -- dropping the middle term -- passes every
    one of them and then reports a minor-only release as ``dep`` (0), sorting it below every
    patch release in the PR body. Verified by mutation: dropping ``minor`` from the alternation
    leaves the five ported rows green.
    """
    changelog = "# Changelog\n\n## 1.1.0\n\n### Minor Changes\n\n- A feature\n\n## 1.0.0\n\nInit"

    entry = get_changelog_entry(changelog, "1.1.0")

    assert entry.content == "### Minor Changes\n\n- A feature"
    assert entry.highest_level == BUMP_LEVELS["minor"]


# --------------------------------------------------------------------------------------
# Rows 1-2 (Port) -- get_changelog_entry extracts the named version section and its bump level.
# utils.test.ts:76-86; snapshots ported verbatim from
# __snapshots__/utils.test.ts.snap:3-23.
# --------------------------------------------------------------------------------------


def test_get_changelog_entry_extracts_the_3_0_0_section_as_major() -> None:
    """Row 1 (Port) -- ``utils.test.ts:76-80`` ('it works').

    Snapshot content ported verbatim from ``__snapshots__/utils.test.ts.snap:3-15``.
    """
    entry = get_changelog_entry(CHANGELOG, "3.0.0")

    assert entry.content == (
        "### Major Changes\n"
        "\n"
        "- [2164a779](https://github.com/keystonejs/keystone-5/commit/2164a779):\n"
        "\n"
        "  - Replace jade with pug because Jade was renamed to Pug, and `jade` package is "
        "outdated\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- [81dc0be5](https://github.com/keystonejs/keystone-5/commit/81dc0be5):\n"
        "\n"
        "  - Update dependencies"
    )
    assert entry.highest_level == BUMP_LEVELS["major"]


def test_get_changelog_entry_extracts_the_3_0_1_section_as_patch() -> None:
    """Row 2 (Port) -- ``utils.test.ts:82-86`` ('it works again!').

    Snapshot content ported verbatim from ``__snapshots__/utils.test.ts.snap:17-23``. This entry
    is also the load-bearing check that ``highestLevel`` is computed only up to and including the
    requested entry's closing heading -- the file's own 3.0.0/2.0.0/... entries below 3.0.1 (which
    include a ``major``) must NOT be scanned, or this row would wrongly report major too.
    """
    entry = get_changelog_entry(CHANGELOG, "3.0.1")

    assert entry.content == (
        "### Patch Changes\n"
        "\n"
        "- [19fe6c1b](https://github.com/keystonejs/keystone-5/commit/19fe6c1b):\n"
        "\n"
        "  Move frontmatter in docs into comments"
    )
    assert entry.highest_level == BUMP_LEVELS["patch"]


# --------------------------------------------------------------------------------------
# Row 3 (Port) -- fenced code blocks: `#` lines inside a fence are not headings.
# utils.test.ts:88-107; snapshot __snapshots__/utils.test.ts.snap:25-33.
# --------------------------------------------------------------------------------------


def test_get_changelog_entry_ignores_headings_inside_a_fenced_code_block() -> None:
    """Row 3 (Port) -- ``utils.test.ts:88-107`` ('it works with code blocks').

    Without the fence-skip, the three ``#``/``##``/``###`` lines inside the fence would be parsed
    as real headings and would prematurely close the ``1.0.0`` entry at the first ``# not a
    heading`` line -- which is a **higher** depth (1) than the ``## 1.0.0`` entry heading (2), so
    it would not even match the "same depth" close rule and the scan would run past the fence
    entirely, silently absorbing ``## 0.0.1`` too. Either way the content would be wrong.
    """
    changelog_with_code_blocks = (
        "# Changelog\n"
        "\n"
        "## 1.0.0\n"
        "\n"
        "### Major Changes\n"
        "\n"
        "```\n"
        "# not a heading\n"
        "## not a heading\n"
        "### not a heading\n"
        "```\n"
        "\n"
        "## 0.0.1\n"
        "\n"
        "Initial release"
    )

    entry = get_changelog_entry(changelog_with_code_blocks, "1.0.0")

    assert entry.content == (
        "### Major Changes\n\n```\n# not a heading\n## not a heading\n### not a heading\n```"
    )
    assert entry.highest_level == BUMP_LEVELS["major"]


# --------------------------------------------------------------------------------------
# Row 4 (Port) -- nested code blocks: fence-LENGTH matching, not "any triple-backtick line".
# utils.test.ts:109-135; snapshot __snapshots__/utils.test.ts.snap:35-50.
# --------------------------------------------------------------------------------------


def test_get_changelog_entry_handles_a_4_backtick_fence_spanning_a_3_backtick_one() -> None:
    """Row 4 (Port) -- ``utils.test.ts:109-135`` ('it works with nested code blocks').

    **Load-bearing** (test-contract fact 3 / phase brief section 7 item 3): the outer fence opens
    with 4 backticks and encloses a *complete* 3-backtick fenced block. An implementation that
    closes a fence on ANY line starting with 3+ backticks -- rather than matching the opener's own
    backtick count -- closes prematurely on the inner fence's opening ```` ``` ```` line, then
    treats the inner block's own ``#`` lines as real headings, corrupting both the content and the
    highest-level scan.
    """
    changelog_with_nested_code_blocks = (
        "# Changelog\n"
        "\n"
        "## 1.0.0\n"
        "\n"
        "### Major Changes\n"
        "\n"
        "````\n"
        "# not a heading\n"
        "## not a heading\n"
        "### not a heading\n"
        "\n"
        "```\n"
        "# not a heading\n"
        "## not a heading\n"
        "### not a heading\n"
        "```\n"
        "\n"
        "````\n"
        "\n"
        "## 0.0.1\n"
        "\n"
        "Initial release"
    )

    entry = get_changelog_entry(changelog_with_nested_code_blocks, "1.0.0")

    assert entry.content == (
        "### Major Changes\n"
        "\n"
        "````\n"
        "# not a heading\n"
        "## not a heading\n"
        "### not a heading\n"
        "\n"
        "```\n"
        "# not a heading\n"
        "## not a heading\n"
        "### not a heading\n"
        "```\n"
        "\n"
        "````"
    )
    assert entry.highest_level == BUMP_LEVELS["major"]


# --------------------------------------------------------------------------------------
# Row 5 (Port) -- sort_changelog_entries: public before private, then highest bump level first.
# utils.test.ts:138-159; snapshot __snapshots__/utils.test.ts.snap:52-70.
# --------------------------------------------------------------------------------------


def test_sort_changelog_entries_sorts_public_first_then_by_highest_bump_level() -> None:
    """Row 5 (Port) -- ``utils.test.ts:138-159`` ('it sorts the things right').

    Two independent rules compose in one comparator (``utils.ts:86-97``): a private entry always
    sorts after every public one regardless of its bump level, and *within* each visibility group
    the highest ``highest_level`` sorts first. The fixture is built so both rules are load-bearing
    at once -- entry ``a`` is ``major`` but private, so it must still sort dead last, after the
    public ``patch`` entry ``b``.
    """
    entries = [
        {"name": "a", "highest_level": BUMP_LEVELS["major"], "private": True},
        {"name": "b", "highest_level": BUMP_LEVELS["patch"], "private": False},
        {"name": "c", "highest_level": BUMP_LEVELS["major"], "private": False},
    ]

    sorted_entries = sort_changelog_entries(entries)

    assert [entry["name"] for entry in sorted_entries] == ["c", "b", "a"]
