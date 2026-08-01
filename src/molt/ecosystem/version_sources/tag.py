"""The ``tag`` version source: detected, and refused. Design D6, in one module.

A setuptools-scm / hatch-vcs / versioningit project has **nothing on disk to write**: the git tag
*is* the version. So ``molt version`` would have to either create the tag or record the new version
somewhere the later commands can find it, and each candidate mechanism fails on something
structural:

1. **``molt version`` creates the tag on its own version commit.** Correct for the local flow, and
   **wrong** for the release-pull-request flow molt documents: there the version commit lives on
   ``changeset-release/<base>`` and is merged -- often squashed -- days later, so the tag points at
   a commit that never lands. Tagging a doomed commit is worse than not tagging.
2. **Record the new version in a file the merge carries, and tag at publish time.** Correct in both
   flows, but molt has no such file: ``pre.json`` was deliberately killed (research README section
   4.2) and ``molt version`` has no ``--output`` by owner ruling (``VC-5``).
3. **Inject the computed version into the build** --
   ``SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>`` / ``PDM_BUILD_SCM_VERSION``. The right long-term
   answer, and it inherits mechanism 2's persistence problem plus a second tool-specific surface
   inside ``molt build``.

**Owner ruling 2026-08-01 (session 6): the follow-up is committed and named --
``implement-tag-version-injection``, taking mechanism 3, its design starting from design D6.**
Until it lands, this source detects and refuses, which is a strict improvement on the silence it
replaces: the user is told molt saw the package, identified its version source as a git tag, and
cannot release it -- instead of the package simply not appearing.

**Deliberately not shipped: reading the tag without writing it.** A read with no write buys
nothing -- the package still cannot be released, so nothing consumes the version -- and it would
put a git subprocess inside a resolution step that is otherwise pure file I/O (design D2). Recorded
as part of gap ``VS-1``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from molt.errors import MoltError

if TYPE_CHECKING:
    from pathlib import Path

    from molt.ecosystem.version_sources.declaration import VersionSourceOptions
    from molt.ecosystem.version_sources.protocol import VersionWrite

__all__ = ["TagVersionSource", "build"]


class TagVersionSource:
    """A version derived from a git tag by the project's build backend. Detected, never written."""

    kind = "tag"

    __slots__ = ("_name",)

    def __init__(self, *, name: str) -> None:
        self._name = name

    def read(self) -> str:
        """Always refuses, naming the package, the source and the remedy (design D6/D7)."""
        raise MoltError(self._refusal())

    def plan_write(self, new_version: str) -> tuple[VersionWrite, ...] | None:
        """Always refuses, with the same sentence :meth:`read` uses."""
        del new_version
        raise MoltError(self._refusal())

    def describe(self) -> str:
        return "a git tag"

    def _refusal(self) -> str:
        """One sentence, used by both members so the two can never drift apart."""
        return (
            f'"{self._name}" takes its version from a git tag (setuptools-scm, hatch-vcs or '
            f"versioningit). molt cannot yet create the tag that would realize a new version, so "
            f"it cannot release this package. Give it a static [project].version, or point "
            f"[tool.molt.version_source] at a file that holds the version."
        )


def build(
    *, name: str, directory: Path, version: str | None, options: VersionSourceOptions
) -> TagVersionSource:
    """The entry-point constructor. Nothing but the name reaches a source that never reads."""
    del directory, version, options
    return TagVersionSource(name=name)
