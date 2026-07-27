"""Reading a ``pyproject.toml`` into a :class:`~molt.ecosystem.protocol.Package`.

Shared by every backend, because ``[project]`` is PEP 621 -- it is the one part of a Python manifest
that is *not* tool-specific, so parsing it inside :mod:`molt.ecosystem.uv` would be the first step
towards a protocol only uv can satisfy (design D9).

Read with ``tomllib`` (stdlib on the 3.11 floor). The **write** path is a different module and uses
tomlkit, because ``tomli-w`` destroys user comments (research doc 02 section 12.5).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from molt.ecosystem.protocol import PRIVATE_CLASSIFIER, Package
from molt.errors import MoltParseError

__all__ = ["is_private", "read_manifest", "read_toml"]


def read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML document, re-raising a decode failure as a located :class:`MoltParseError`.

    ``tomllib``'s own error names neither the file nor a usable position, and a manifest failure in
    a 30-package workspace is unactionable without the path.
    """
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise MoltParseError(f"could not parse {path}: {exc}", path=str(path)) from exc


def is_private(classifiers: list[str]) -> bool:
    """Whether ``Private :: Do Not Upload`` is present -- the Python analogue of npm ``private``.

    Matched case- and whitespace-insensitively: PyPI's rejection is on the trove classifier's
    canonical form, and users do write ``Private::Do Not Upload``.
    """
    return any(
        "".join(entry.lower().split()) == "".join(PRIVATE_CLASSIFIER.split())
        for entry in classifiers
    )


def read_manifest(manifest_path: Path) -> Package | None:
    """Read one ``pyproject.toml``, or ``None`` when it declares no ``[project]`` table.

    A manifest without ``[project].name`` is not a distribution -- it is a build-tool-only or
    workspace-only file -- so it is skipped rather than guessed at. **The declared name wins over
    the directory name**, always: the two disagree often enough in real repos (``packages/core``
    holding ``acme-core``) that inferring from the path is a silent-wrong-package bug waiting for a
    release to expose it.
    """
    if not manifest_path.is_file():
        return None
    document = read_toml(manifest_path)
    project = document.get("project")
    if not isinstance(project, dict):
        return None
    name = project.get("name")
    if not isinstance(name, str) or not name:
        return None
    version = project.get("version")
    optional = project.get("optional-dependencies")
    groups = document.get("dependency-groups")
    return Package(
        name=name,
        directory=manifest_path.parent,
        manifest_path=manifest_path,
        version=version if isinstance(version, str) else None,
        dependencies=_requirements(project.get("dependencies")),
        optional_dependencies=_flatten(optional),
        dev_dependencies=_flatten(groups),
        private=is_private([c for c in project.get("classifiers", []) if isinstance(c, str)]),
    )


def _requirements(value: object) -> tuple[str, ...]:
    """PEP 508 requirement strings. A list, not npm's name -> range map (research README 4.5)."""
    if not isinstance(value, list):
        return ()
    return tuple(entry for entry in value if isinstance(entry, str))


def _flatten(value: object) -> tuple[str, ...]:
    """Flatten an extras / dependency-groups table into one requirement list.

    Which extra or group a requirement came from does not change any rule that consumes it here:
    ``dependencies`` + ``optional-dependencies`` are checked for the skip-tree rule and
    ``dependency-groups`` is excluded from it, and that is the only distinction the config layer
    draws.
    """
    if not isinstance(value, dict):
        return ()
    out: list[str] = []
    for entries in value.values():
        if isinstance(entries, list):
            out.extend(entry for entry in entries if isinstance(entry, str))
    return tuple(out)
