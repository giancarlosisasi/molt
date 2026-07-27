"""The uv workspace backend -- the first (and, for now, only) implementation of the seam.

uv is the primary backend because ``[tool.uv.workspace] members`` is "the only real, declarative,
widely-adopted Python monorepo primitive" (research doc 02 section 12.5, recommendation 1). Nothing
in this module is importable from the engine or the commands: they go through
:mod:`molt.ecosystem`, which is what keeps :class:`~molt.ecosystem.protocol.EcosystemBackend` a
seam rather than a description of uv.

Two rules matter more than the glob walking:

- **The declared name wins over the directory name.** Enforced in
  :func:`molt.ecosystem.manifest.read_manifest`, which this backend does not second-guess.
- **The workspace root is itself a member** when it declares ``[project]``, matching uv.
"""

from __future__ import annotations

from pathlib import Path

from molt.ecosystem.manifest import read_manifest, read_toml
from molt.ecosystem.protocol import Package, Workspace

__all__ = ["MANIFEST_NAME", "UvBackend", "workspace_table"]

MANIFEST_NAME = "pyproject.toml"


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
        return Workspace(
            root=root, packages=tuple(packages), backend=self.name, root_package=root_package
        )


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
