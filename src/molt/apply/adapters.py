"""The bridge from real discovery into the shape :func:`molt.apply.apply_release_plan` reads.

:mod:`molt.apply.apply` reads its workspace **structurally** -- ``root_dir`` / ``root_package`` /
``packages[i].{name, version, dir, manifest_path}`` -- because the conformance suite drives it with
the ported ``@changesets/types`` fixture (``tests/apply/fake_release_plan.py``) while
:class:`molt.ecosystem.Workspace` is a different shape again. This module is the only place the two
meet, and it closes the packages half of the apply wiring (``openspec/GAPS.md``: ``SC-6``; the
*config* half is already satisfied by :class:`molt.engine.EngineConfig`, which declares
``ApplyConfigLike``'s five keys as well as the engine's).

It is deliberately separate from :mod:`molt.engine.adapters`: apply wants ``pathlib`` paths, the
engine wants POSIX strings, and a single adapter serving both would have to carry one of them for
the other's benefit. Two small functions that each say what their consumer needs is the cheaper
arrangement -- and it keeps ``molt.apply`` from importing ``molt.engine``, which would invert the
layering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from molt.ecosystem import Package, Workspace

__all__ = ["ApplyPackage", "ApplyPackages", "to_apply_packages"]

#: What :attr:`ApplyPackage.version` carries for a member that declares none (``dynamic`` or simply
#: absent). :class:`molt.apply.apply.PackageLike` types the field as a non-optional ``str`` and
#: apply reads it in exactly two places: a truthiness filter, and an equality check that only ever
#: runs for a package being released. A versionless package is never released -- it is folded into
#: the effective ``ignore`` by :func:`molt.engine.skipped_package_names` -- so the empty string is
#: read as "no declared version" by the first and never reached by the second.
NO_DECLARED_VERSION = ""


@dataclass(frozen=True)
class ApplyPackage:
    """One workspace member, as ``molt.apply`` reads it.

    Satisfies :class:`molt.apply.apply.PackageLike`. ``dir`` is where ``CHANGELOG.md`` goes and
    ``manifest_path`` is what gets rewritten; both stay :class:`~pathlib.Path` so nothing downstream
    re-joins strings that a Windows path would break.
    """

    name: str
    version: str
    dir: Path
    manifest_path: Path


@dataclass(frozen=True)
class ApplyPackages:
    """The discovered workspace, as ``molt.apply`` reads it.

    Satisfies :class:`molt.apply.apply.PackagesLike`. ``root_package`` is present whenever the root
    declares a ``[project]`` table, which is what lets apply rewrite a root-level pin at a bumped
    member without ever versioning the root itself.
    """

    root_dir: Path
    root_package: ApplyPackage | None
    packages: tuple[ApplyPackage, ...]


def to_apply_packages(workspace: Workspace) -> ApplyPackages:
    """Adapt a discovered :class:`molt.ecosystem.Workspace` for the apply layer's input shape.

    Discovery order is preserved: it is the order manifests are visited and therefore the order
    they are flushed in, which ``tests/apply/test_apply.py::EXPECTED_FLUSH_ORDER`` observes.
    """
    return ApplyPackages(
        root_dir=workspace.root,
        root_package=None if workspace.root_package is None else _package(workspace.root_package),
        packages=tuple(_package(package) for package in workspace.packages),
    )


def _package(package: Package) -> ApplyPackage:
    return ApplyPackage(
        name=package.name,
        version=package.version if package.version is not None else NO_DECLARED_VERSION,
        dir=package.directory,
        manifest_path=package.manifest_path,
    )
