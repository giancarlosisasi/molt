"""The version-source seam: what a source is, and what resolving a workspace produces.

This is the missing half of the ecosystem seam. :mod:`molt.ecosystem` already answers *which
packages exist*; a version source answers *where each one's version lives*, how molt reads it, and
what writing a new one costs. Research doc 02 section 12.5 calls the abstraction "the single
largest piece of new design" in the port, and research README section 5 item 1 lists it as
differentiator number 1 -- the capability changesets rejected in 2020 (#310) and deferred again in
June 2026 (#2124). **There is no upstream counterpart**: ``@manypkg/get-packages`` has no concept
of a version living anywhere but ``package.json``.

**No member of this protocol may name a tool**, the same rule
:mod:`molt.ecosystem.protocol` states for :class:`~molt.ecosystem.protocol.EcosystemBackend`. A
source that declared ``hatch_path()`` would be hatch with extra steps; a third-party source has to
be able to satisfy this surface without adapting anything.

The write contract is **three-valued** (design D9), and that is the whole point:

======================  =====================================================================
``None``                the apply layer does its own ``[project].version`` edit -- what a
                        statically versioned package gets, byte-identical to before the seam
a tuple of writes       apply does **no** manifest version edit and buffers exactly these
an empty tuple          there is nothing to write on disk (reserved for a future tag source)
======================  =====================================================================

``None`` rather than an empty tuple as the default is what keeps ``tests/apply``'s pinned rows
untouched: they drive :func:`molt.apply.apply_release_plan` with the ported ``@changesets/types``
fixture, which has no version sources at all, and they must keep exercising the same code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from molt.names import normalize_name

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    "ResolvedVersion",
    "UnresolvedVersion",
    "VersionResolution",
    "VersionSource",
    "VersionWrite",
]


@dataclass(frozen=True)
class VersionWrite:
    """One file a version source wants replaced, as bytes.

    Bytes rather than text, deliberately: a version file the user authored may be CRLF, and
    ``Path.write_text`` would translate newlines and make the same fixture produce different files
    on two platforms. The source encodes once, at the point where it still knows what it read --
    the rule ``tests/conftest.py::ProjectBuilder`` already states for every fixture it writes.
    """

    path: Path
    data: bytes


@runtime_checkable
class VersionSource(Protocol):
    """Where one package's version lives, and what changing it costs."""

    #: Stable identifier, matching the ``kind`` a ``[tool.molt.version_source]`` table names and
    #: the name this source is registered under in the ``molt.version_source`` entry-point group.
    kind: str

    def read(self) -> str:
        """The package's current version.

        Raises:
            MoltError: the source exists but molt cannot read a version from it -- a version file
                with no locatable literal, two candidate literals, or a source molt recognises and
                cannot resolve at all (a git tag). The resolver turns this into an
                :class:`UnresolvedVersion` rather than letting it escape.
        """
        ...

    def plan_write(self, new_version: str) -> tuple[VersionWrite, ...] | None:
        """What writing ``new_version`` back costs -- see this module's three-valued table.

        Raises:
            MoltError: this source cannot realize a new version at all.
        """
        ...

    def describe(self) -> str:
        """A short human phrase naming where the version lives, for a report or a refusal."""
        ...


@dataclass(frozen=True)
class ResolvedVersion:
    """A member whose version molt read, and the source it read it from."""

    name: str
    version: str
    source: VersionSource


@dataclass(frozen=True)
class UnresolvedVersion:
    """A member that declares a dynamic version molt cannot resolve or cannot write.

    ``reason`` is a **complete, printable sentence including the remedy** (design D7). This bucket
    is deliberately loud: a package declaring ``dynamic = ["version"]`` is by definition a
    distribution somebody intends to build, and today's silence about it is the defect the
    version-source seam exists to fix. ``kind`` is the source molt detected, or ``None`` when it
    detected none.
    """

    name: str
    kind: str | None
    reason: str


@dataclass(frozen=True)
class VersionResolution:
    """The outcome of one resolution pass over a workspace.

    Two channels rather than exceptions, the same shape -- and for the same reason --
    :func:`molt.config.load_config` uses: the caller gets **every** failure at once, as data, and
    can report them in one run instead of fixing one per invocation.

    A member in neither channel is design D7's middle bucket: it declares no version and no
    ``dynamic = ["version"]``, so it is not a versioned distribution at all -- an application, a
    docs site, a workspace-only root. It is skipped **silently**, exactly as before this seam
    existed.
    """

    resolved: Mapping[str, ResolvedVersion]
    unresolved: tuple[UnresolvedVersion, ...] = ()

    def version_of(self, name: str) -> str | None:
        """The resolved version of ``name``, or ``None`` when it was not resolved.

        PEP 503-aware on both sides, like every other name lookup in molt: a changeset written
        against ``Foo_Bar`` has to find the member declaring ``foo-bar``.
        """
        entry = self.resolved.get(normalize_name(name))
        return None if entry is None else entry.version

    def source_of(self, name: str) -> VersionSource | None:
        """The source that resolved ``name``, or ``None`` when it was not resolved."""
        entry = self.resolved.get(normalize_name(name))
        return None if entry is None else entry.source
