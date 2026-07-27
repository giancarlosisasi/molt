"""Package discovery, behind a backend seam.

This is the public face of the ecosystem layer: every consumer -- the engine, the commands, the
config layer's group expansion -- imports from here, and **nothing** imports
:mod:`molt.ecosystem.uv` directly. That indirection is the whole point (design D9); the moment a
command reaches for ``UvBackend`` the seam has stopped being one.

Backend selection follows the ``ecosystem`` config option:

- ``"auto"`` (the default) inspects the workspace root -- ``[tool.uv.workspace]`` selects uv, its
  absence selects single-package (design D8).
- an explicit name selects that backend with **no** detection, so a repository can be pinned.

Poetry, Hatch, PDM and setuptools are scheduled after the 0.1 MVP. The config layer rejects them at
parse time, so they normally never reach here; the guard below is the second line of defence for a
caller that built a ``Config`` by hand. It raises rather than falling back to single-package,
because silently discovering the wrong set of packages is how a release tool ships a wrong version.
"""

from __future__ import annotations

from pathlib import Path

from molt.ecosystem.manifest import is_private, read_manifest, read_toml
from molt.ecosystem.protocol import EcosystemBackend, Package, Workspace
from molt.ecosystem.single import SingleBackend
from molt.ecosystem.uv import MANIFEST_NAME, UvBackend, workspace_table
from molt.errors import MoltError

__all__ = [
    "AUTO",
    "EcosystemBackend",
    "Package",
    "Workspace",
    "discover_workspace",
    "find_workspace_root",
    "is_private",
    "read_manifest",
    "read_toml",
    "resolve_backend",
]

#: The default ``ecosystem`` value -- detect rather than assume.
AUTO = "auto"

#: Backends molt can actually run today.
_BACKENDS: dict[str, EcosystemBackend] = {
    UvBackend.name: UvBackend(),
    SingleBackend.name: SingleBackend(),
}

#: Recognized, not yet built; scheduled after 0.1. Listed so the failure names the gap instead of
#: pretending the repository is single-package. Kept in step with ``molt.config.parse``'s copy,
#: which is what users actually hit.
_PLANNED = ("poetry", "hatch", "pdm", "setuptools")


def find_workspace_root(start: Path) -> Path:
    """Walk up from ``start`` to the directory that owns the workspace.

    Configuration is a property of the workspace root, not of the current directory
    (``website/docs/config/config-file.md``), so this is what makes ``molt version`` behave the same
    run from a member package as from the top.

    The nearest ancestor declaring ``[tool.uv.workspace]`` wins. With none, the nearest ancestor
    holding a ``pyproject.toml`` is the root -- the single-package case. With neither, ``start``
    itself is returned so callers get a defaults-only result instead of an exception; "there is no
    project here" is ``molt init``'s problem to report, not this function's.
    """
    begin = start if start.is_dir() else start.parent
    candidates = [begin, *begin.parents]
    for directory in candidates:
        if workspace_table(directory) is not None:
            return directory
    for directory in candidates:
        if (directory / MANIFEST_NAME).is_file():
            return directory
    return begin


def resolve_backend(ecosystem: str, root: Path) -> EcosystemBackend:
    """Pick the backend for ``ecosystem`` at ``root``; ``"auto"`` detects, anything else pins."""
    if ecosystem != AUTO:
        backend = _BACKENDS.get(ecosystem)
        if backend is None:
            planned = ", ".join(_PLANNED)
            raise MoltError(
                f'ecosystem "{ecosystem}" is not implemented yet. '
                f"Available: {', '.join(sorted(_BACKENDS))}. Planned: {planned}."
            )
        return backend
    uv = _BACKENDS[UvBackend.name]
    return uv if uv.detect(root) else _BACKENDS[SingleBackend.name]


def discover_workspace(root: Path, ecosystem: str = AUTO) -> Workspace:
    """Enumerate every package in the workspace at ``root`` through the selected backend."""
    return resolve_backend(ecosystem, root).discover(root)
