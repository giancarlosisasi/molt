"""The uv workspace backend -- the first (and, for now, only) implementation of the seam.

uv is the primary backend because ``[tool.uv.workspace] members`` is "the only real, declarative,
widely-adopted Python monorepo primitive" (research doc 02 section 12.5, recommendation 1). Nothing
in this module is importable from the engine or the commands: they go through
:mod:`molt.ecosystem`, which is what keeps :class:`~molt.ecosystem.protocol.EcosystemBackend` a
seam rather than a description of uv.

Three rules matter more than the glob walking:

- **The declared name wins over the directory name.** Enforced in
  :func:`molt.ecosystem.manifest.read_manifest`, which this backend does not second-guess.
- **The workspace root is itself a member** when it declares ``[project]``, matching uv.
- **``[tool.uv.sources]`` is normalized here, not downstream.** A dependency redirected at another
  workspace member is recorded on :attr:`molt.ecosystem.Package.workspace_sources` as
  ``workspace:*`` or ``workspace:<relpath>`` -- the two spellings
  :func:`molt.engine.graph.classify_dependency` already understands. ``molt/engine/graph.py``'s
  module docstring names this as the ecosystem layer's job precisely so the engine keeps no
  uv-specific vocabulary (design D9).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from molt.ecosystem.manifest import read_manifest, read_toml
from molt.ecosystem.protocol import Package, Workspace

__all__ = ["MANIFEST_NAME", "UvBackend", "workspace_table"]

MANIFEST_NAME = "pyproject.toml"

#: The engine's spelling for "this dependency is another member of the workspace"
#: (``molt.engine.graph.WORKSPACE_PREFIX``). Duplicated as a literal rather than imported: the
#: ecosystem layer must not depend on the engine, and this is the seam's whole vocabulary.
_WORKSPACE_PREFIX = "workspace:"


def workspace_table(root: Path) -> dict[str, object] | None:
    """``[tool.uv.workspace]`` of ``root``'s manifest, or ``None`` when absent or unreadable."""
    manifest = root / MANIFEST_NAME
    if not manifest.is_file():
        return None
    table = read_toml(manifest).get("tool")
    if not isinstance(table, dict):
        return None
    uv = table.get("uv")
    if not isinstance(uv, dict):
        return None
    workspace = uv.get("workspace")
    return workspace if isinstance(workspace, dict) else None


class UvBackend:
    """Discovers a uv workspace from ``[tool.uv.workspace]``.

    Satisfies :class:`~molt.ecosystem.protocol.EcosystemBackend` structurally -- there is no
    inheritance, deliberately, so nothing in the protocol can leak an implementation detail down
    into a second backend.
    """

    name = "uv"

    def detect(self, root: Path) -> bool:
        """``[tool.uv.workspace]`` present. Its absence means single-package, never failure (D8)."""
        return workspace_table(root) is not None

    def read_manifest(self, manifest_path: Path) -> Package:
        package = read_manifest(manifest_path)
        if package is None:
            raise ValueError(f"{manifest_path} declares no [project] table with a name")
        return package

    def discover(self, root: Path) -> Workspace:
        """Enumerate the root package plus every ``members`` glob match that is a distribution.

        ``exclude`` is applied to the *directories* the members globs produced, matching uv. A
        member directory with no ``pyproject.toml``, or one that declares no ``[project].name``, is
        skipped rather than reported: uv itself errors there, but a config layer that refuses to
        load because an unrelated scratch directory is malformed is a worse failure than one that
        releases the packages it can see.

        ``[tool.uv.sources]`` is resolved in a second pass, once every member directory is known:
        deciding whether ``{ path = "../pkg-a" }`` points *inside* the workspace is not answerable
        while the member set is still being built.
        """
        table = workspace_table(root) or {}
        members = _patterns(table.get("members"))
        excluded = _patterns(table.get("exclude"))
        directories = _expand(root, members)
        if excluded:
            skip = {path.resolve() for path in _expand(root, excluded)}
            directories = [path for path in directories if path.resolve() not in skip]

        root_package = read_manifest(root / MANIFEST_NAME)
        packages: list[Package] = []
        seen: set[str] = set()
        if root_package is not None:
            packages.append(root_package)
            seen.add(root_package.normalized_name)
        for directory in directories:
            package = read_manifest(directory / MANIFEST_NAME)
            if package is None or package.normalized_name in seen:
                continue
            packages.append(package)
            seen.add(package.normalized_name)

        member_dirs = {package.directory.resolve() for package in packages}
        resolved = tuple(
            _with_sources(package, root=root, member_dirs=member_dirs) for package in packages
        )
        return Workspace(
            root=root,
            packages=resolved,
            backend=self.name,
            root_package=resolved[0] if root_package is not None else None,
        )


def _with_sources(package: Package, *, root: Path, member_dirs: set[Path]) -> Package:
    """Attach ``package``'s normalized ``[tool.uv.sources]`` workspace markers.

    Two spellings are recognised, and only two, because only these two name another member of this
    workspace: ``{ workspace = true }`` and ``{ path = "..." }`` resolving onto a member directory.
    A git, url or index source names something molt does not release, so it is left off entirely
    and the dependency keeps whatever PEP 508 constraint its ``[project]`` entry declared.

    A *list* value (uv's marker-selected form) is scanned for the first entry that qualifies: the
    engine's constraint slot holds one value, and any workspace entry in the list means the
    dependency can be a workspace edge on some platform.
    """
    document = read_toml(package.manifest_path)
    table = _sources_table(document)
    if not table:
        return package
    markers: list[tuple[str, str]] = []
    for name, source in table.items():
        marker = _workspace_marker(
            source, package_dir=package.directory, root=root, member_dirs=member_dirs
        )
        if marker is not None:
            markers.append((name, marker))
    return package if not markers else replace(package, workspace_sources=tuple(markers))


def _sources_table(document: dict[str, Any]) -> dict[str, Any]:
    tool = document.get("tool")
    if not isinstance(tool, dict):
        return {}
    uv = tool.get("uv")
    if not isinstance(uv, dict):
        return {}
    sources = uv.get("sources")
    return sources if isinstance(sources, dict) else {}


def _workspace_marker(
    source: object, *, package_dir: Path, root: Path, member_dirs: set[Path]
) -> str | None:
    """The normalized marker for one ``[tool.uv.sources]`` entry, or ``None`` when it is not one."""
    entries = source if isinstance(source, list) else [source]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("workspace") is True:
            return f"{_WORKSPACE_PREFIX}*"
        path = entry.get("path")
        if not isinstance(path, str):
            continue
        target = (package_dir / path).resolve()
        if target not in member_dirs:
            continue
        try:
            relative = target.relative_to(root.resolve()).as_posix()
        except ValueError:  # pragma: no cover - a member outside its own workspace root
            continue
        return f"{_WORKSPACE_PREFIX}{relative}"
    return None


def _patterns(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(entry for entry in value if isinstance(entry, str))


def _expand(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Resolve member globs to directories, deduplicated, in a stable (sorted) order.

    Sorted rather than filesystem order on purpose: discovery order becomes the order config globs
    expand into ``fixed`` / ``linked`` / ``ignore``, and those land in changelogs and release plans.
    Letting the filesystem decide would make a snapshot test pass on one machine and fail on
    another.
    """
    found: dict[Path, None] = {}
    for pattern in patterns:
        normalized = pattern.replace("\\", "/").rstrip("/")
        if not normalized:
            continue
        for path in sorted(root.glob(normalized)):
            if path.is_dir():
                found.setdefault(path, None)
    return list(found)
