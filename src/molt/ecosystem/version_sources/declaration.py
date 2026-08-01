"""The explicit ``[tool.molt.version_source]`` declaration, as discovery records it.

A leaf module: it imports nothing else from :mod:`molt.ecosystem`, which is what lets
:mod:`molt.ecosystem.protocol` name :class:`VersionSourceOptions` in a ``Package`` field without a
cycle.

The declaration is read from the **package's own** manifest, never from the workspace root
(design D5): where a version lives is a property of a package, and a root-level name-to-source map
would duplicate what discovery already knows. Research doc 02 section 12.5 sketches a root-level
``version_files = [...]`` instead; that divergence is recorded as gap ``VS-8``.

Nothing here validates the declaration beyond its shape. ``molt.config`` owns validation, because
in a single-package repository the root manifest **is** the member manifest and the table sits in
the very ``[tool.molt]`` section ``molt.config`` parses -- with configuration strict, an undeclared
key there would be a hard error (research doc 02 section 12.5; research README section 5 item 1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["VersionSourceOptions", "parse_version_source_options"]


@dataclass(frozen=True)
class VersionSourceOptions:
    """What a manifest declared about where its version lives.

    Every field is ``str | None`` so the value stays hashable inside the frozen
    :class:`molt.ecosystem.Package` that carries it.

    ``kind`` is a free string rather than a fixed set: an installed distribution may register a
    fourth source through the ``molt.version_source`` entry-point group (design D8), so the names
    molt ships are documented rather than enumerated in the type. An unresolvable kind fails at
    *use*, naming the sources that are installed -- the same arrangement ``changelog``'s generator
    reference already uses.
    """

    kind: str
    path: str | None = None
    pattern: str | None = None


def parse_version_source_options(raw: Mapping[str, Any] | None) -> VersionSourceOptions | None:
    """Read a raw ``[tool.molt.version_source]`` mapping, or ``None`` when there is nothing usable.

    Deliberately tolerant, and deliberately **not** a validator. Discovery runs on every command
    and must never raise for a manifest it did not write; a table with no ``kind``, or with a
    wrong-typed member, is reported by :mod:`molt.config` with a located message that names the fix.
    Returning ``None`` here means "no explicit declaration", which falls through to detection.
    """
    if not isinstance(raw, Mapping):
        return None
    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind:
        return None
    return VersionSourceOptions(
        kind=kind,
        path=_text(raw.get("path")),
        pattern=_text(raw.get("pattern")),
    )


def _text(value: object) -> str | None:
    """``value`` when it is a non-empty string, else ``None``."""
    return value if isinstance(value, str) and value else None
