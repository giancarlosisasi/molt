"""Changelog scraping for the CI action -- one version's section, and how to order sections.

Ports ``packages/release-utils/src/utils.ts`` (research doc 04 section 6.3). These functions exist
so the action can slice the section a release just wrote back out of a package's ``CHANGELOG.md``
and turn it into a pull-request body or a forge release note. They are pure string work: no
filesystem, no git, no network.

Why a line scanner rather than a markdown parser
------------------------------------------------
Upstream's first implementation parsed the changelog into an mdast tree and stringified the slice
back out, which round-trips the *whole* document through a serializer -- a formatter change
anywhere in that library rewrites bytes molt has already published. The shipped upstream version
scans lines instead (``utils.ts:39-55``), and so does this: the extracted content is a literal
substring of the input, so what a changelog says is what the pull request says.

The fence rule is the load-bearing detail (design D4)
------------------------------------------------------
A code-fence *closer* is any line whose backtick run is **at least** as long as the opener's. There
is no upper bound and no end-of-line anchor, which is what lets a 4-backtick fence contain a
complete 3-backtick fenced block. An implementation that closes on "any line starting with three
backticks" closes early on the inner fence, then reads the inner block's ``#`` lines as real
headings -- corrupting both the extracted content and the bump level. molt's own changelog will
document markdown, so this is not a hypothetical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from molt.errors import MoltError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "BUMP_LEVELS",
    "ChangelogEntry",
    "changelog_entry_sort_key",
    "get_changelog_entry",
    "sort_changelog_entries",
]

#: The ordered bump scale (``utils.ts:4-9``), snake_case port of upstream's ``BumpLevels``.
#:
#: The **values** are the contract, not just the ordering: :func:`sort_changelog_entries` orders on
#: them, so collapsing ``minor`` onto ``patch`` silently reorders the package list in a published
#: pull-request body. ``dep`` is the floor -- a section that names no bump at all is a
#: dependency-only release.
BUMP_LEVELS: dict[str, int] = {"dep": 0, "patch": 1, "minor": 2, "major": 3}

#: ``utils.ts:61`` -- the level scanner's alternation, all three terms.
#:
#: Transcribing it as ``(major|patch)`` still passes every section a major or patch release writes,
#: and then reports a minor-only release as ``dep`` (0) and sorts it below every patch.
_LEVEL_PATTERN = re.compile(r"(major|minor|patch)")

#: An ATX heading: one to six ``#`` followed by whitespace or the end of the line. The trailing
#: requirement is what keeps ``#hashtag`` out; CommonMark says the same.
_HEADING_PATTERN = re.compile(r"^(#{1,6})(?:\s+(.*?))?\s*$")

#: A code fence: three or more backticks, optionally indented. Deliberately unanchored at the end
#: -- an info string (```` ```python ````) still opens a fence, and a closer may carry trailing
#: whitespace.
_FENCE_PATTERN = re.compile(r"^\s{0,3}(`{3,})")


@dataclass(frozen=True, slots=True)
class ChangelogEntry:
    """One version's changelog section and the highest bump level it announces.

    ``content`` is a literal slice of the changelog with surrounding blank lines trimmed;
    ``highest_level`` is a value of :data:`BUMP_LEVELS`, defaulting to ``dep`` for a section that
    announces no bump of its own.
    """

    content: str
    highest_level: int


@dataclass(frozen=True, slots=True)
class _Heading:
    """One heading found outside a fenced code block."""

    index: int
    depth: int
    text: str


def get_changelog_entry(changelog: str, version: str) -> ChangelogEntry:
    """Extract ``version``'s section from ``changelog`` and report its bump level.

    The section starts after the first heading whose text is exactly ``version`` and ends at the
    next heading of the **same depth** (``utils.ts:39-55``) -- not at the next heading of any
    depth, which is what keeps a ``### Patch Changes`` sub-heading inside its own release.

    The bump level is scanned over that range only. Scanning the whole document instead is the
    quiet bug: every changelog ever written has an older major release further down the file, so a
    patch release would report itself as a major one.

    Raises :class:`~molt.errors.MoltError` when the file carries no section for ``version``.
    Upstream throws one level up, at the release-note call site (``run.ts``: "Could not find
    changelog entry"); molt raises here because every caller wants the same answer and a silent
    empty entry publishes a release with no notes.
    """
    lines = changelog.split("\n")
    headings = _headings(lines)

    start: _Heading | None = None
    end: _Heading | None = None
    for heading in headings:
        if start is None:
            if heading.text == version:
                start = heading
            continue
        if heading.depth == start.depth:
            end = heading
            break

    if start is None:
        raise MoltError(f"Could not find a changelog entry for version {version}")

    stop = end.index if end is not None else len(lines)
    content = _trim_blank_lines(lines[start.index + 1 : stop])
    highest = BUMP_LEVELS["dep"]
    for heading in headings:
        if start.index <= heading.index < stop:
            highest = max(highest, _level_of(heading.text))
    return ChangelogEntry(content="\n".join(content), highest_level=highest)


def changelog_entry_sort_key(entry: Mapping[str, Any]) -> tuple[bool, int]:
    """The sort key behind :func:`sort_changelog_entries`: public first, highest bump first.

    Design D5 -- upstream is a comparator (``utils.ts:86-97``) that subtracts the two levels;
    Python sorts by key, so the subtraction becomes a negation and the two independent rules become
    two tuple slots. ``private`` is read as a plain truth value so a caller may spell it with any
    boolean-ish value.
    """
    return (bool(entry["private"]), -int(entry["highest_level"]))


_EntryT = TypeVar("_EntryT", bound="Mapping[str, Any]")


def sort_changelog_entries(entries: Iterable[_EntryT]) -> list[_EntryT]:
    """Order changelog entries for a pull-request body (``utils.ts:86-97``).

    Two rules compose: every **public** entry precedes every private one regardless of its bump
    level, and within each visibility group the **highest** bump level comes first. The sort is
    stable, so entries that tie on both keep the order the caller supplied.
    """
    return sorted(entries, key=changelog_entry_sort_key)


# ======================================================================================
# The line scanner
# ======================================================================================


def _headings(lines: Sequence[str]) -> list[_Heading]:
    """Every ATX heading outside a fenced code block, in document order.

    The fence state machine is design D4: ``open_fence`` holds the opener's backtick count, and
    only a run at least that long closes it. A ``#`` line inside a fence is data, not structure.
    """
    found: list[_Heading] = []
    open_fence = 0
    for index, line in enumerate(lines):
        fence = _FENCE_PATTERN.match(line)
        if fence is not None:
            length = len(fence.group(1))
            if open_fence == 0:
                open_fence = length
            elif length >= open_fence:
                open_fence = 0
            continue
        if open_fence:
            continue
        heading = _HEADING_PATTERN.match(line)
        if heading is not None:
            found.append(
                _Heading(index=index, depth=len(heading.group(1)), text=(heading.group(2) or ""))
            )
    return found


def _level_of(heading_text: str) -> int:
    """The bump level a heading announces, or ``dep`` when it announces none (``utils.ts:61``)."""
    match = _LEVEL_PATTERN.search(heading_text.lower())
    return BUMP_LEVELS["dep"] if match is None else BUMP_LEVELS[match.group(1)]


def _trim_blank_lines(lines: Sequence[str]) -> list[str]:
    """Drop leading and trailing blank lines; keep every blank line in between."""
    start = 0
    stop = len(lines)
    while start < stop and not lines[start].strip():
        start += 1
    while stop > start and not lines[stop - 1].strip():
        stop -= 1
    return list(lines[start:stop])
