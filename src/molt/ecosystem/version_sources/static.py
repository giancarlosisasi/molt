"""The ``static`` version source: ``[project].version``, today's behaviour behind the seam.

Research doc 02 section 12.5 recommends reading ``[project].version`` first and writing back to the
same place; this is that source, and it is registered in the ``molt.version_source`` entry-point
group exactly like the other two -- **no privileged path** for a built-in (design D8), the same
rule ``implement-changelog-generators`` design D1 applied to changelog generators.

``plan_write`` returns **``None``**, and that is not an oversight: ``None`` is the sentinel meaning
"the apply layer does its own ``[project].version`` edit" (design D9's three-valued contract).
Returning a manifest write instead would move a byte-identical, pinned path onto new code for no
gain -- ``tests/apply``'s rows drive :func:`molt.apply.apply_release_plan` with a fixture that has
no version sources at all, and they must keep exercising the same edit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from molt.errors import MoltError

if TYPE_CHECKING:
    from pathlib import Path

    from molt.ecosystem.version_sources.declaration import VersionSourceOptions
    from molt.ecosystem.version_sources.protocol import VersionWrite

__all__ = ["StaticVersionSource", "build"]


class StaticVersionSource:
    """A version declared literally in the package's own ``[project].version``."""

    kind = "static"

    __slots__ = ("_name", "_version")

    def __init__(self, *, name: str, version: str | None) -> None:
        self._name = name
        self._version = version

    def read(self) -> str:
        """The declared version.

        Raises:
            MoltError: the manifest declares none. Unreachable through
                :func:`molt.ecosystem.version_sources.resolve.resolve_version_source`, which only
                selects this source when a version is present, but stated rather than assumed --
                a third-party caller may construct one directly.
        """
        if self._version is None:
            raise MoltError(
                f'"{self._name}" declares no [project].version, so there is no static version '
                "to read."
            )
        return self._version

    def plan_write(self, new_version: str) -> tuple[VersionWrite, ...] | None:
        """``None`` -- the apply layer owns the ``[project].version`` edit (design D9)."""
        del new_version
        return None

    def describe(self) -> str:
        return "[project].version"


def build(
    *, name: str, directory: Path, version: str | None, options: VersionSourceOptions
) -> StaticVersionSource:
    """The entry-point constructor. ``directory`` / ``options`` carry nothing this source uses."""
    del directory, options
    return StaticVersionSource(name=name, version=version)
