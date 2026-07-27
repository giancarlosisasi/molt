"""The single-package backend -- what ``ecosystem = "auto"`` falls back to.

Design D8: absence of a workspace declaration means "one package rooted here", **not** a
misconfiguration. Most Python projects are single-package -- the ratio is inverted versus JavaScript
(research README section 5 item 9) -- so treating the fallback as degenerate would make the common
case the error case. ``@manypkg`` reaches the same place with its ``tool: "root"`` result
(research doc 02 section 12.5), which is the shape this mirrors.

It is a real backend, not a branch inside :mod:`molt.ecosystem.uv`: putting it there would make
"repository with no uv workspace" a uv concept.
"""

from __future__ import annotations

from pathlib import Path

from molt.ecosystem.manifest import read_manifest
from molt.ecosystem.protocol import Package, Workspace

__all__ = ["SingleBackend"]


class SingleBackend:
    """Treats the manifest at the workspace root as the one and only package."""

    name = "single"

    def detect(self, root: Path) -> bool:
        """True for any directory holding a ``pyproject.toml``; this is the last resort."""
        return (root / "pyproject.toml").is_file()

    def read_manifest(self, manifest_path: Path) -> Package:
        package = read_manifest(manifest_path)
        if package is None:
            raise ValueError(f"{manifest_path} declares no [project] table with a name")
        return package

    def discover(self, root: Path) -> Workspace:
        """A workspace of zero or one packages.

        Zero is reachable and legitimate: a repository whose root manifest carries only
        ``[build-system]`` has nothing to release yet, and reporting that as an empty workspace
        lets ``molt status`` say so instead of crashing.
        """
        package = read_manifest(root / "pyproject.toml")
        packages = () if package is None else (package,)
        return Workspace(root=root, packages=packages, backend=self.name, root_package=package)
