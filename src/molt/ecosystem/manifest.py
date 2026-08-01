"""Reading a ``pyproject.toml`` into a :class:`~molt.ecosystem.protocol.Package`.

Shared by every backend, because ``[project]`` is PEP 621 -- it is the one part of a Python manifest
that is *not* tool-specific, so parsing it inside :mod:`molt.ecosystem.uv` would be the first step
towards a protocol only uv can satisfy (design D9).

Read with ``tomllib`` (stdlib on the 3.11 floor). The **write** path is a different module and uses
tomlkit, because ``tomli-w`` destroys user comments (research doc 02 section 12.5).

**Discovery is a pure manifest read, and must stay one** (version-sources design D1). This module
records *that* a version is declared dynamic and *what* version source the manifest names; it does
**not** open a version file, run git, or resolve which source applies. Reading a version file would
mean a second file per member and a future tag source would mean a subprocess, while discovery runs
on every command -- so resolution is an explicit second pass a caller drives
(:func:`molt.ecosystem.resolve_workspace_versions`). Do not "helpfully" resolve here.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from molt.ecosystem.protocol import PRIVATE_CLASSIFIER, Package
from molt.ecosystem.version_sources.declaration import parse_version_source_options
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

    This is the **only** place ``[project].dynamic`` and ``[tool.molt.version_source]`` are read.
    Both backends share this function precisely so ``uv.py`` and ``single.py`` cannot disagree
    about what a manifest says (spec ``ecosystem-backend``, "Version-source reading is shared, not
    per backend"): ``[project]`` is PEP 621 and belongs to no tool.
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
        dynamic_version=_declares_dynamic_version(project.get("dynamic")),
        version_source=parse_version_source_options(_version_source_table(document)),
    )


def _declares_dynamic_version(value: object) -> bool:
    """Whether ``[project].dynamic`` names ``version`` (PEP 621's list of build-computed fields).

    Anything that is not a list of strings reads as ``False``: a malformed ``dynamic`` is the build
    backend's error to report, and guessing at it here would mark a package dynamic on the strength
    of a typo.
    """
    if not isinstance(value, list):
        return False
    return any(entry == "version" for entry in value if isinstance(entry, str))


def _version_source_table(document: dict[str, Any]) -> dict[str, Any] | None:
    """``[tool.molt.version_source]`` of ``document``, or ``None`` when it is absent."""
    tool = document.get("tool")
    if not isinstance(tool, dict):
        return None
    molt = tool.get("molt")
    if not isinstance(molt, dict):
        return None
    table = molt.get("version_source")
    return table if isinstance(table, dict) else None


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
