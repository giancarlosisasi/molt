"""The changeset grammar: ``---``-fenced YAML frontmatter followed by a markdown summary.

Ports ``packages/parse/src/index.ts`` -- the regex at ``index.ts:4``, the nine-step algorithm at
``index.ts:51-109`` and the exact error wording at research doc 02 section 5.4. The grammar itself
is written up in research doc 02 section 5.

Three things here are molt's, not upstream's, and each is load-bearing:

- **Line endings are normalized once, at the boundary** (design D2). Everything after
  :func:`parse_changeset`'s first statement sees LF, so no downstream rule has to remember to be
  CRLF-aware. molt is Windows-correct from day one (repo ``CLAUDE.md``), and a changeset checked out
  with ``core.autocrlf=true`` must not produce a different release set.
- **Markdown headings survive.** Upstream's summary post-processing strips every line starting with
  ``#``, destroying headings an author wrote (research README section 3.4, "upstream bugs -- do not
  port these"). The summary is only ``.strip()``ed here. Do not "restore" the stripping: the bug
  lives in changelog assembly, and if ``add`` ever needs to remove a prompt artifact it removes it
  at composition time, not at parse time (design D1).
- **The author's literal package name is kept** (design D3). PEP 503 says ``Foo_Bar`` and
  ``foo-bar`` are one distribution, but that is a *comparison* rule, not a rewriting one -- the
  changeset file is a source document under version control. :attr:`Release.normalized_name` is the
  view every comparison goes through (research README section 4.5; doc 02 section 12.5).

Frontmatter is read with ``ruamel.yaml`` in ``typ="safe"`` mode, pinned to **YAML 1.2** with
duplicate keys rejected, which is exactly what makes it agree with upstream's ``yaml@2``: 1.1 would
read ``yes`` as a boolean and 1.1 loaders let a duplicated package silently win (research doc 02
section 12.3). A duplicated package entry is a real merge-conflict artifact, and last-wins there
would drop a release.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ruamel.yaml import YAML, YAMLError

from molt.errors import MoltParseError
from molt.names import normalize_name
from molt.versioning import BumpType

__all__ = ["ECHO_LIMIT", "Changeset", "Release", "parse_changeset"]


#: The Python translation of ``/\s*---([^]*?)\r?\n\s*---(\s*(?:\n|$)[^]*)/``
#: (``parse/src/index.ts:4``; doc 02 section 12.3). ``[^]`` becomes ``.`` under ``re.DOTALL`` and
#: ``$`` becomes ``\Z``, because Python's ``$`` also matches before a trailing newline. The ``\r?``
#: of the reference is gone: by the time this runs there are no carriage returns left (design D2).
#:
#: Load-bearing properties, all exercised by ``tests/changeset/test_parse.py``: group 1 is **lazy**
#: so the *first* closing fence wins (``---`` inside a quoted name or in the body is safe); the
#: closing fence may be indented and may be followed by whitespace but nothing else on its line; and
#: the pattern is deliberately **unanchored**, so text before the opening fence is skipped.
_FRONTMATTER_RE = re.compile(r"\s*---(.*?)\n\s*---(\s*(?:\n|\Z).*)", re.DOTALL)

#: How much of a malformed file an error message may echo (``parse/src/index.ts:15-17``). Applied
#: where the message is built, not where the file is read, so a caller still has the full contents
#: while the rendered sentence stays terminal-sized (design D8).
ECHO_LIMIT = 200

#: The document shape every rejection message shows the user.
_EXAMPLE = '---\n"package-name": patch\n---\n\nYour changeset summary here.'

#: ``major, minor, patch, none`` -- derived from the enum rather than spelled out, so a new bump
#: type cannot leave the error message advertising a stale list.
_VALID_TYPES = ", ".join(bump.value for bump in sorted(BumpType, key=lambda b: -b.rank))


@dataclass(frozen=True)
class Release:
    """One ``package: bump`` entry of a changeset's frontmatter.

    ``name`` is the spelling the author wrote and is what :func:`~molt.changeset.write_changeset`
    writes back; :attr:`normalized_name` is the PEP 503 form every comparison must use. Keeping both
    is design D3 -- collapsing them at parse time would rewrite a user's file behind their back and
    make the write/parse round-trip a lie.
    """

    name: str
    type: BumpType

    @property
    def normalized_name(self) -> str:
        """The PEP 503 form of :attr:`name`; the only form comparisons may use."""
        return normalize_name(self.name)


@dataclass(frozen=True)
class Changeset:
    """A parsed changeset file: its releases, its summary, and its id.

    ``id`` is the filename minus the final ``.md`` and is supplied by
    :func:`~molt.changeset.read_changesets`; a changeset parsed from a string it does not yet have
    one, hence the default. It is not cosmetic -- ``apply_release_plan`` deletes ``<id>.md`` after
    versioning, so an id that has lost characters leaves a consumed changeset on disk and the next
    ``molt version`` double-bumps (doc 02 section 7.3).

    A changeset with no releases and an empty summary is **valid**: it is what ``molt add --empty``
    writes, to record "this change needs no release" (doc 02 section 5 gotcha 6).
    """

    releases: tuple[Release, ...] = ()
    summary: str = ""
    id: str = ""


def parse_changeset(contents: str, *, id: str = "", path: str | None = None) -> Changeset:
    """Parse one changeset document.

    ``path`` is carried onto the raised :class:`MoltParseError` rather than into its sentence: in a
    directory of thirty changesets the message alone does not say which file is broken, and the
    funnel that renders it needs the location as data.

    Raises :class:`MoltParseError` for each of the seven rejection rules with a message that says
    **which** rule failed -- an empty file, missing or malformed frontmatter, trailing content after
    the closing fence, invalid YAML, non-mapping frontmatter, an empty package name and an unknown
    bump type are seven different user mistakes with seven different fixes.
    """
    text = contents.replace("\r\n", "\n")
    trimmed = text.strip()
    if not trimmed:
        raise MoltParseError(
            "could not parse changeset - file is empty.\n"
            "Changesets must have frontmatter with package names and version types.\n"
            f"Example:\n{_EXAMPLE}",
            path=path,
        )

    match = _FRONTMATTER_RE.search(text)
    if match is None:
        # Upstream echoes the *trimmed* contents here and the raw contents everywhere else
        # (doc 02 section 5.4 flags the inconsistency); ported as-is rather than tidied, because
        # these sentences are what users search for.
        raise MoltParseError(
            "could not parse changeset - missing or invalid frontmatter.\n"
            'Changesets must start with frontmatter delimited by "---".\n'
            f"Example:\n{_EXAMPLE}\n"
            f"Received content:\n{_truncate(trimmed)}",
            path=path,
        )

    frontmatter = match.group(1)
    releases = _parse_releases(
        frontmatter,
        text,
        path=path,
        line_offset=text.count("\n", 0, match.start(1)),
    )
    return Changeset(releases=releases, summary=match.group(2).strip(), id=id)


def _parse_releases(
    frontmatter: str, contents: str, *, path: str | None, line_offset: int
) -> tuple[Release, ...]:
    """Steps 4-8 of ``parse/src/index.ts:51-109``: YAML, shape check, then per-entry validation."""
    value = _load_yaml(frontmatter, path=path, line_offset=line_offset)
    if _is_empty_block(value):
        return ()
    if not isinstance(value, dict):
        raise MoltParseError(
            "could not parse changeset - frontmatter must be an object mapping package names to "
            "version types.\n"
            f"Expected format:\n{_EXAMPLE}\n"
            f"Received:\n{frontmatter}",
            path=path,
        )

    releases: list[Release] = []
    for raw_name, raw_type in value.items():
        name = _release_name(raw_name, contents, path=path)
        releases.append(Release(name=name, type=_release_type(raw_type, name, contents, path=path)))
    return tuple(releases)


def _load_yaml(frontmatter: str, *, path: str | None, line_offset: int) -> Any:
    """Load the frontmatter block, reporting a YAML failure as its own rule.

    YAML 1.2 with duplicate keys rejected -- see this module's docstring for why both matter. The
    loader is built per call rather than shared: a ``YAML`` instance carries scanner state, and one
    malformed changeset must not be able to affect the next file in the directory.
    """
    loader = YAML(typ="safe", pure=True)
    loader.version = (1, 2)
    loader.allow_duplicate_keys = False
    try:
        return loader.load(frontmatter)
    except YAMLError as exc:
        # The one message that echoes user content untruncated (doc 02 section 5.4): a YAML error
        # is unreadable without the block it points into, and a frontmatter block is a few lines.
        raise MoltParseError(
            "could not parse changeset - invalid YAML in frontmatter.\n"
            'The frontmatter between the "---" delimiters must be valid YAML.\n'
            f"YAML error: {exc}\n"
            f"Frontmatter content:\n{frontmatter}",
            path=path,
            line=_yaml_line(exc, line_offset),
        ) from exc


def _yaml_line(exc: YAMLError, line_offset: int) -> int | None:
    """Translate a YAML mark (0-based, frontmatter-relative) to a 1-based file line."""
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return None
    return line_offset + mark.line + 1


def _is_empty_block(value: Any) -> bool:
    """Whether the frontmatter carries no releases.

    ``yaml.parse`` of an empty block is ``null`` and upstream treats **any** falsy value as "no
    releases" (``parse/src/index.ts:90-91``). JS falsiness is spelled out rather than reached for
    via Python truthiness, because the two disagree on exactly the case that matters: ``[]`` is
    falsy in Python but truthy in JS, where it goes on to be rejected as a non-mapping.
    """
    return value is None or value is False or value == 0 or value == ""


def _release_name(raw: Any, contents: str, *, path: str | None) -> str:
    """Validate one frontmatter key (``parse/src/index.ts:20-29``).

    A YAML document whose key is absent (``: minor``) resolves to ``None`` in Python and to the
    empty string in ``yaml@2``; they mean the same thing, so ``None`` is folded to ``""`` and the
    empty-name rule -- and the ``""`` upstream prints -- both stay faithful (doc 02 section 5.3).
    """
    name = "" if raw is None else raw
    if not isinstance(name, str) or not name.strip():
        raise MoltParseError(
            "could not parse changeset - invalid package name in frontmatter.\n"
            f"Expected a non-empty string for package name, but got: {_render(name)}\n"
            f"Changeset contents:\n{_truncate(contents)}",
            path=path,
        )
    return name


def _release_type(raw: Any, name: str, contents: str, *, path: str | None) -> BumpType:
    """Validate one frontmatter value (``parse/src/index.ts:31-47``)."""
    if not isinstance(raw, str):
        # Upstream prints JS `typeof`; the Python type name is the same diagnostic in this dialect.
        raise MoltParseError(
            f'could not parse changeset - invalid release type for package "{name}".\n'
            f"Expected a string for release type, but got: {type(raw).__name__}\n"
            f"Changeset contents:\n{_truncate(contents)}",
            path=path,
        )
    try:
        return BumpType(raw)
    except ValueError:
        raise MoltParseError(
            f"could not parse changeset - invalid version type {_render(raw)} for package "
            f'"{name}".\n'
            f"Valid version types are: {_VALID_TYPES}\n"
            f"Changeset contents:\n{_truncate(contents)}",
            path=path,
        ) from None


def _truncate(text: str) -> str:
    """``truncate(s, 200)`` (``parse/src/index.ts:15-17``): 200 characters, then an ellipsis."""
    return text if len(text) <= ECHO_LIMIT else text[:ECHO_LIMIT] + "..."


def _render(value: Any) -> str:
    """``JSON.stringify(value)`` for the values these messages echo, with a repr fallback."""
    try:
        return json.dumps(value)
    except TypeError:
        return repr(value)
