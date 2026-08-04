"""Which packages a release will actually version, and which it will quietly walk past.

This is the check ``doctor`` exists for. Three of the four outcomes below are invisible until a
release is already running, where a package molt cannot version is skipped and a library can
disappear from a release without its author noticing.

The four outcomes, and the fields that separate them:

=====================  ==========================================================  ========
resolved               in ``VersionResolution.resolved``                           ``ok``
versionless            no ``[project].version`` **and** no ``dynamic = ["version"]``  ``warn``
unresolvable dynamic   ``dynamic = ["version"]`` with no locatable source           ``fail``
refused source         the version derives from a git tag                           ``fail``
=====================  ==========================================================  ========

**``Package.version is None`` means "no static version was declared", not "dynamic".**
``Package.dynamic_version`` is what separates rows 2 and 3, and collapsing them turns a failure
into a warning -- which is the quiet direction, and precisely the bug this check was written to
expose. Row 2 is a warning because a package with no version is usually an application or a docs
site and is skipped by design; row 3 is a failure because its author declared a version molt cannot
find, which is a mistake rather than a choice.

Row 4 is separated from row 3 on purpose. A git-tag source is one molt **recognises and refuses**,
not one it failed to locate, and a report that conflated the two would read as "molt is broken"
rather than "molt does not do this yet".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from molt.doctor.protocol import CheckBase
from molt.doctor.report import CheckGroup

if TYPE_CHECKING:
    from collections.abc import Iterator

    from molt.doctor.protocol import DoctorContext
    from molt.doctor.report import Row
    from molt.ecosystem import Package, UnresolvedVersion

__all__ = ["ResolvableCheck"]

#: The one version source molt detects and deliberately declines to release.
_TAG_KIND = "tag"


class ResolvableCheck(CheckBase):
    """One row per package -- including the ones a release would silently skip."""

    id: ClassVar[str] = "versions.resolvable"
    group: ClassVar[CheckGroup] = CheckGroup.VERSIONS
    requires: ClassVar[tuple[str, ...]] = ("workspace.packages",)

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        from molt.ecosystem import resolve_workspace_versions
        from molt.names import normalize_name

        assert ctx.workspace is not None  # guaranteed by `requires`
        resolution = resolve_workspace_versions(ctx.workspace)
        unresolved = {normalize_name(entry.name): entry for entry in resolution.unresolved}

        for package in ctx.workspace.packages:
            key = normalize_name(package.name)
            resolved = resolution.resolved.get(key)
            if resolved is not None:
                yield self.ok(
                    package.name, f"{resolved.version}, from {resolved.source.describe()}"
                )
                continue

            failure = unresolved.get(key)
            if failure is None:
                yield self._versionless(package)
            elif failure.kind == _TAG_KIND:
                yield self._refused(failure)
            else:
                yield self._unresolvable(failure)

    def _versionless(self, package: Package) -> Row:
        """No version and no ``dynamic`` -- not a versioned distribution, and skipped by design."""
        return self.warn(
            package.name,
            "declares no version and no dynamic version, so a release skips it.",
            f"Leave it as is if {package.name} is not a distribution, or give it a "
            "[project].version.",
        )

    def _unresolvable(self, failure: UnresolvedVersion) -> Row:
        """A dynamic version whose source molt could not find. The reason carries its own remedy."""
        return self.fail(
            failure.name,
            failure.reason,
            f"Declare where {failure.name}'s version lives, then run `molt doctor` again.",
        )

    def _refused(self, failure: UnresolvedVersion) -> Row:
        """A git-tag source: recognised, and deliberately not released.

        Worded as a limit rather than a defect. The remedy names the two things that work today, so
        the row is actionable even though the underlying capability is not built.
        """
        return self.fail(
            failure.name,
            f"{failure.reason} This is a known limit that molt reports deliberately, not a "
            "failure to find the version.",
            f"Give {failure.name} a static [project].version, or point "
            "[tool.molt.version_source] at a file that holds it.",
        )
