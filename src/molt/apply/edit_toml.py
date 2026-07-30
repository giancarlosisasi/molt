"""Format-preserving edits to a package manifest.

Ports ``packages/apply-release-plan/src/edit-json.ts`` -- and replaces almost all of it, because
the two properties that make ``editJson`` trivial are both absent in Python (research README
section 4.5; research doc 04 section 2.6):

* JSON round-trips losslessly, so upstream can parse, mutate and re-stringify. ``pyproject.toml``
  carries comments and hand-maintained formatting that a user cares about, so the write path is
  ``tomlkit`` (``roadmap/tech-stack.md`` section 8). ``tomllib`` stays the read path.
* ``dependencies`` is a ``{name: range}`` map upstream, so a range rewrite is one key assignment.
  In ``pyproject.toml`` it is a **list of PEP 508 requirement strings**, so a range rewrite is a
  **substring splice inside one array element**.

Two primitives, both ``str -> str``:

:func:`edit_toml`
    The direct ``editJson`` analogue -- write a scalar at a dotted key path, including addressing
    an array element by index. Like upstream (``edit-json.ts:42-44``) it can never *create* a key.

:func:`set_dependency_specifier`
    No upstream counterpart. Rewrite only the version-specifier region of the PEP 508 requirement
    strings that name a given distribution.

Byte-minimality is the contract, not a nicety
---------------------------------------------
An edit changes only the bytes of its target: a longer replacement does not disturb later bytes,
and writing a value back over itself is a byte-identical no-op. ``molt version`` runs over every
manifest in a workspace, so an edit that reflows a document is a defect even when the resulting
TOML is semantically identical.

Both primitives return ``text`` unchanged when nothing needs to move, so the no-op holds by
construction rather than by relying on ``tomlkit`` being lossless for that document.

Design decisions this module implements (``openspec/changes/implement-toml-editing/design.md``)
----------------------------------------------------------------------------------------------
* **D1/D2** -- the splice *locates* the specifier region by offset and replaces that slice of the
  **original** requirement string. It never rebuilds the string from a parsed ``Requirement``:
  ``str(Requirement('pkg-b[cli] >= 1.0 ; python_version < "3.12"'))`` normalises the author's
  spacing away. Inserting a specifier where there was none is the same operation with
  ``start == end``.
* **D3** -- the whole ``SpecifierSet`` the caller passes lands verbatim, so a compound range keeps
  both bounds. Upstream's ``getVersionRangeType`` (``version-package.ts:116-125``) keeps only the
  leading operator, silently widening ``>=1.0.0 <2.0.0`` to ``>=1.0.4`` (research README
  section 3.3). This module is agnostic about *which* specifier it writes; the release engine
  computes it.
* **D4** -- names match under PEP 503 normalization on both sides, compared as whole tokens.
* **D5** -- a direct reference is recognised by its **url**, not by an absent specifier:
  ``Requirement("pkg @ git+...").specifier`` is an *empty* ``SpecifierSet``, never ``None``, so a
  test for absence falls through to the insert branch and corrupts the requirement.
* **D6** -- ``[tool.uv.sources]`` is excluded **by section**, not by heuristic.
* **D7** -- PEP 735 ``include-group`` entries are skipped by shape, before any parse attempt.
* **D8** -- :class:`~molt.errors.MoltKeyPathError` for a missing path or an unsplice-able target,
  :class:`~molt.errors.MoltParseError` for malformed TOML. An **empty document is valid TOML**, so
  it is a key-path failure rather than a parse failure -- the inverse of upstream's
  ``allowEmptyContent: false``.
* **D9** -- there is no batch entry point. Both primitives take and return the whole document text,
  so a batch *is* a fold over the single-edit primitive and "batched equals sequential" holds by
  construction.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

import tomlkit
from packaging.requirements import InvalidRequirement, Requirement
from tomlkit.container import Container, OutOfOrderTableProxy
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import AoT, Array, InlineTable, String, StringType, Table

from molt.errors import MoltKeyPathError, MoltParseError
from molt.names import normalize_name

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tomlkit import TOMLDocument

__all__ = ["edit_toml", "set_dependency_specifier"]


# ======================================================================================
# Internals
# ======================================================================================

#: Returned by :func:`_child` for "there is nothing here". TOML has no null, so no real child can
#: collide with it; ``None`` would.
_MISSING: Final = object()

#: Node types a string key can be looked up in.
_MAPPINGS: Final = (Container, Table, InlineTable, OutOfOrderTableProxy)

#: Node types an integer index can be looked up in.
_SEQUENCES: Final = (Array, AoT)

#: The PEP 508 ``name`` production's character set. Used to find where the name token *ends*, never
#: to compare one name against another -- that goes through :func:`~molt.names.normalize_name`
#: (design D4). ``.`` and ``_`` are legal here and normalise to ``-`` only at comparison time.
_NAME_RUN: Final = re.compile(r"[A-Za-z0-9._-]+")

#: The uv workspace source table, excluded from splicing by section rather than by heuristic
#: (design D6).
_UV_SOURCES: Final = ("tool", "uv", "sources")


def _dotted(key_path: Sequence[str | int]) -> str:
    """Render a key path for an error message: ``("project", "deps", 7)`` -> ``project.deps.7``."""
    return ".".join(str(step) for step in key_path)


def _parse(text: str) -> TOMLDocument:
    """Parse ``text``, re-raising any tomlkit failure as :class:`MoltParseError`.

    The message says "failed to parse" because the CLI renders "your pyproject.toml is malformed"
    quite differently from "molt looked for a key that is not there", and the exception class plus
    this sentence are the only things that tell them apart (design D8). Upstream matched
    ``/Failed to parse JSON/`` (``edit-json.test.ts:105``).
    """
    try:
        return tomlkit.parse(text)
    except TOMLKitError as exc:
        raise MoltParseError(f"Failed to parse TOML: {exc}") from exc


def _child(node: Any, step: str | int) -> Any:
    """Return the item at ``step`` inside ``node``, or :data:`_MISSING`.

    A step that runs into a scalar, an absent key, or an out-of-range index all answer the same
    way: there is nothing to edit here. The caller turns that into a message naming the path.
    """
    if isinstance(step, int):
        if not isinstance(node, _SEQUENCES):
            return _MISSING
        if not -len(node) <= step < len(node):
            return _MISSING
        return node[step]
    if not isinstance(node, _MAPPINGS):
        return _MISSING
    if step not in node:
        return _MISSING
    return node[step]


def _missing_key_path(key_path: Sequence[str | int], depth: int) -> MoltKeyPathError:
    """The row-6 error: it names the **whole** requested path and the step that failed.

    Upstream asserts the message, not just the class (``edit-json.test.ts:89``), and has to: the
    caller is the version writer, editing a path it computed, so an error that does not name the
    path is unactionable.
    """
    parent = _dotted(key_path[:depth])
    where = f'under "{parent}"' if parent else "at the document root"
    return MoltKeyPathError(
        f'Key path "{_dotted(key_path)}" not found in the TOML document: '
        f'no "{key_path[depth]}" {where}'
    )


def _quote_style(item: Any) -> StringType:
    """The quote style ``item`` was written with, read off its rendered form.

    A naive ``table["version"] = value`` rewrites ``'1.0.0'`` to ``"1.0.0"``: tomlkit builds a
    fresh basic string and the author's literal-string quoting is lost. Carrying the style over is
    what keeps the edit byte-minimal. Derived from :meth:`~tomlkit.items.Item.as_string` rather
    than the private ``String._t`` so this stays on tomlkit's public surface.
    """
    if not isinstance(item, String):
        return StringType.SLB
    raw = item.as_string()
    for style in (StringType.MLL, StringType.MLB, StringType.SLL):
        if raw.startswith(style.value):
            return style
    return StringType.SLB


def _restyled(previous: Any, value: str) -> String:
    """A TOML string holding ``value``, quoted the way ``previous`` was."""
    style = _quote_style(previous)
    try:
        return String.from_raw(value, type_=style)
    except TOMLKitError:
        # `value` cannot live inside that quote style -- a literal string cannot contain `'`.
        # Fall back to a basic string, which escapes anything. This changes the entry's quoting,
        # which is a formatting regression; emitting invalid TOML would be a corruption.
        return String.from_raw(value, type_=StringType.SLB)


def _requirement(text: str) -> Requirement | None:
    """Parse a PEP 508 requirement, or ``None`` when ``text`` is not one.

    An unparseable entry is stepped over rather than raised on: it is provably not the dependency
    being looked for, and one malformed entry must not block an edit to a different one. If the
    *target* is the malformed entry, the caller still fails loudly -- with the "not declared"
    message, which names the dependency.
    """
    try:
        return Requirement(text)
    except InvalidRequirement:
        return None


def _end_of_head(text: str) -> int:
    """The offset just past ``<name><extras>`` in a PEP 508 requirement string.

    This is where a specifier gets *inserted* when there is none, so it sits before any gap the
    author left: ``pkg-b ; marker`` becomes ``pkg-b==1.1.0rc0 ; marker``, keeping the space in
    front of the ``;``.
    """
    name = _NAME_RUN.search(text)
    if name is None:  # pragma: no cover - Requirement() already accepted this string
        return 0
    cursor = name.end()
    probe = cursor
    while probe < len(text) and text[probe].isspace():
        probe += 1
    if probe < len(text) and text[probe] == "[":
        closing = text.find("]", probe)
        if closing >= 0:
            cursor = closing + 1
    return cursor


def _specifier_region(text: str) -> tuple[int, int]:
    """The ``(start, end)`` offsets of the specifier region inside a requirement string.

    A requirement is treated as ``<name><extras><gap><specifier><marker>`` (design D2). The region
    is the specifier alone: the gap in front of it, the marker behind it, and any padding inside
    the TOML string itself are all outside the slice and survive byte-for-byte. Whitespace *inside*
    the old specifier is not preserved -- the region is replaced wholesale.

    A specifier region cannot contain ``;``, so the first ``;`` in the string is the marker
    separator. When the region is empty the two offsets coincide, which is the insertion case.
    """
    head = _end_of_head(text)
    tail = text.find(";")
    if tail < 0:
        tail = len(text)
    region = text[head:tail]
    if not region.strip():
        return head, head
    start = head + len(region) - len(region.lstrip())
    end = tail - (len(region) - len(region.rstrip()))
    return start, end


def _resolve_dependency_array(doc: TOMLDocument, section: Sequence[str]) -> Array:
    """The array at ``section``, or a :class:`MoltKeyPathError` naming what was wrong.

    The three shapes a caller passes are ``("project", "dependencies")``,
    ``("project", "optional-dependencies", "<extra>")`` and ``("dependency-groups", "<group>")``
    (PEP 735). A missing section, a missing extra and a target that is not a list are different
    caller bugs, so each names the part that is wrong.
    """
    node: Any = doc
    for depth, step in enumerate(section):
        child = _child(node, step)
        if child is _MISSING:
            raise _missing_key_path(section, depth)
        node = child
    if not isinstance(node, Array):
        raise MoltKeyPathError(
            f'"{_dotted(section)}" is not a dependency list, so it holds no PEP 508 requirement '
            f"to splice (found {type(node).__name__.lower()})"
        )
    return node


def _reject_source_table(section: Sequence[str]) -> None:
    """Refuse ``[tool.uv.sources]`` outright (design D6).

    A source table says *where* a package comes from; the version constraint lives in the PEP 508
    string. Rewriting the table breaks workspace resolution, so it is excluded by section rather
    than by inspecting what the table happens to contain.
    """
    if tuple(section[: len(_UV_SOURCES)]) == _UV_SOURCES:
        raise MoltKeyPathError(
            f'"{_dotted(section)}" is a uv workspace source table, not a dependency list; '
            "molt never rewrites one -- a source table selects where a package comes from, and "
            "only the PEP 508 specifier says which versions satisfy it"
        )


# ======================================================================================
# Public API
# ======================================================================================


def edit_toml(text: str, key_path: Sequence[str | int], value: str) -> str:
    """Write ``value`` at ``key_path`` in ``text`` and return the whole document.

    ``key_path`` is a sequence of table keys, and an integer step addresses an array element by
    index. The write is byte-minimal: only the target value's bytes move, and writing a value back
    over itself returns ``text`` unchanged.

    Nothing is ever created. A path that does not resolve -- a missing leaf, a missing intermediate
    table, a path that runs through a scalar, or an index past the end of an array -- raises
    :class:`~molt.errors.MoltKeyPathError` naming that path (``edit-json.ts:42-44``).

    Prefer :func:`set_dependency_specifier` over an integer index into a dependency array: the
    index shifts whenever a dependency is added.

    Raises:
        MoltParseError: ``text`` is not valid TOML.
        MoltKeyPathError: ``key_path`` does not resolve, or is empty.
    """
    if not key_path:
        raise MoltKeyPathError("An empty key path addresses nothing in the TOML document")
    doc = _parse(text)
    node: Any = doc
    for depth, step in enumerate(key_path[:-1]):
        child = _child(node, step)
        if child is _MISSING:
            raise _missing_key_path(key_path, depth)
        node = child
    leaf = key_path[-1]
    previous = _child(node, leaf)
    if previous is _MISSING:
        raise _missing_key_path(key_path, len(key_path) - 1)
    if isinstance(previous, String) and previous.value == value:
        return text
    node[leaf] = _restyled(previous, value)
    return tomlkit.dumps(doc)


def set_dependency_specifier(
    text: str, *, section: Sequence[str], name: str, specifier: str
) -> str:
    """Rewrite the version specifier of every ``name`` requirement in ``section``.

    ``specifier`` lands verbatim, so a compound range keeps both bounds and a snapshot pin drops
    every range modifier -- the release engine decides which, this primitive only carries it out
    (design D3). Only the specifier region moves: the name, extras, gap and environment markers of
    each entry are byte-identical afterwards, and a requirement with no specifier gets one inserted
    right after its extras.

    Names match under PEP 503 normalization on both sides, compared as whole tokens, so ``cat``
    never splices ``cathode``. **Every** entry for the dependency is rewritten -- a marker-split
    dependency declares two live constraints and both go stale together. PEP 735
    ``include-group`` entries are stepped over.

    The primitive is strict: the skip rules live in the caller (``version-package.ts:48-102``), so
    an absent section, an absent dependency and a direct reference all raise rather than return
    ``text`` unchanged.

    Raises:
        MoltParseError: ``text`` is not valid TOML.
        MoltKeyPathError: ``section`` does not resolve or is not a list, ``name`` is not declared
            there, or the matched requirement is a direct reference and so has no specifier region
            (design D5).
    """
    _reject_source_table(section)
    doc = _parse(text)
    array = _resolve_dependency_array(doc, section)
    target = normalize_name(name)
    matched = False
    changed = False
    for index, entry in enumerate(list(array)):
        if not isinstance(entry, String):
            continue  # a PEP 735 `{include-group = "..."}` inline table, skipped by shape (D7)
        original = str(entry)
        requirement = _requirement(original)
        if requirement is None or normalize_name(requirement.name) != target:
            continue
        matched = True
        if requirement.url is not None:
            raise MoltKeyPathError(
                f'Dependency "{name}" in "{_dotted(section)}" is a direct reference '
                f"({original!r}) and so has no specifier region to rewrite -- PEP 508 forbids "
                "combining a URL with a version specifier"
            )
        start, end = _specifier_region(original)
        rewritten = f"{original[:start]}{specifier}{original[end:]}"
        if rewritten == original:
            continue
        array[index] = _restyled(entry, rewritten)
        changed = True
    if not matched:
        raise MoltKeyPathError(f'Dependency "{name}" is not declared in "{_dotted(section)}"')
    if not changed:
        return text
    return tomlkit.dumps(doc)
