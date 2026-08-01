"""Detecting where a dynamically-versioned package keeps its version, from the tool tables.

Design D2: **molt reads tool tables and configuration; it never runs the build.** The only
universally correct way to learn a dynamic version is to ask the backend through PEP 517's
``prepare_metadata_for_build_wheel``, and that needs an isolated environment, network access and
arbitrary code execution out of the repository being released -- for a value molt reads on every
``molt status``. Research doc 02 section 12.5 recommends reading the tool tables instead, and that
is what this module does. Ruled "not now", not "never" (owner ruling 2026-08-01, session 6); the
condition to reopen it is a real project molt cannot read any other way (gap ``VS-2``).

The consequence is stated rather than hidden: **detection is a heuristic over other tools'
tables.** When it is wrong or absent the answer is the explicit ``[tool.molt.version_source]``
declaration, which always wins (design D1's order), and failing that a named refusal (design D7).
It is never a guess: two candidate literals in one file is an error, not a first-wins pick.

The table (design D3)
---------------------
=========================================================  ========
``[tool.hatch.version] path``                              ``file``
``[tool.hatch.version] source = "vcs"``                    ``tag``
``[tool.setuptools.dynamic] version = { attr = "..." }``   ``file``
``[tool.setuptools.dynamic] version = { file = "..." }``   ``file``
``[tool.pdm.version] source = "file"`` + ``path``          ``file``
``[tool.pdm.version] source = "scm"``                      ``tag``
an scm plugin in ``[build-system].requires``               ``tag``
=========================================================  ========

``{attr = "pkg.__version__"}`` is resolved to a **file path** by the ordinary import-path
convention, never by importing: importing a package to learn its version runs its top-level code,
which is design D2's objection at a smaller scale. A layout the convention misses falls through to
the unresolved bucket with the explicit ``path`` declaration named as the remedy (gap ``VS-6``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from molt.ecosystem.version_sources.declaration import VersionSourceOptions
from molt.names import normalize_name

if TYPE_CHECKING:
    from molt.ecosystem.protocol import Package

__all__ = [
    "SCM_BUILD_REQUIREMENTS",
    "attribute_pattern",
    "detect_version_source",
    "resolve_attr_path",
]

#: Build requirements that mean "this project's version comes from a git tag", matched on the
#: PEP 503 normalized project name of each ``[build-system].requires`` entry. ``setuptools_scm``
#: and ``setuptools-scm`` are the same distribution and both spellings are common in the wild,
#: which is exactly what the normalization is for.
SCM_BUILD_REQUIREMENTS: tuple[str, ...] = ("setuptools-scm", "hatch-vcs", "versioningit")

#: The kinds this module can name. Spelled as literals rather than imported from the source
#: modules so detection stays a pure table read with no import of the sources it names.
_FILE = "file"
_TAG = "tag"


def detect_version_source(
    package: Package, document: Mapping[str, Any]
) -> VersionSourceOptions | None:
    """The version source ``package``'s build-tool tables imply, or ``None`` when none do.

    ``document`` is the already-parsed manifest, so detection opens no file of its own: discovery
    read the manifest once and this pass reads what it produced (design D1).
    """
    tool = _table(document, "tool")
    for detector in (_hatch, _setuptools, _pdm):
        detected = detector(package, tool)
        if detected is not None:
            return detected
    if _requires_scm(document):
        return VersionSourceOptions(kind=_TAG)
    return None


# --------------------------------------------------------------------------------------
# Per-tool detectors
# --------------------------------------------------------------------------------------


def _hatch(package: Package, tool: Mapping[str, Any]) -> VersionSourceOptions | None:
    """``[tool.hatch.version]`` -- ``path`` names a file, ``source = "vcs"`` names a tag."""
    del package
    version = _table(_table(tool, "hatch"), "version")
    if not version:
        return None
    if _text(version.get("source")) == "vcs":
        return VersionSourceOptions(kind=_TAG)
    path = _text(version.get("path"))
    if path is not None:
        return VersionSourceOptions(kind=_FILE, path=path, pattern=_pattern_for(version))
    return None


def _setuptools(package: Package, tool: Mapping[str, Any]) -> VersionSourceOptions | None:
    """``[tool.setuptools.dynamic] version`` -- either ``{ file = ... }`` or ``{ attr = ... }``."""
    dynamic = _table(_table(tool, "setuptools"), "dynamic")
    version = dynamic.get("version") if dynamic else None
    if not isinstance(version, Mapping):
        return None
    path = _text(version.get("file"))
    if path is not None:
        return VersionSourceOptions(kind=_FILE, path=path)
    attr = _text(version.get("attr"))
    if attr is None:
        return None
    resolved = resolve_attr_path(attr, package)
    if resolved is None:
        return None
    return VersionSourceOptions(kind=_FILE, path=resolved, pattern=attribute_pattern(attr))


def _pdm(package: Package, tool: Mapping[str, Any]) -> VersionSourceOptions | None:
    """``[tool.pdm.version]`` -- ``source = "file"`` plus ``path``, or ``source = "scm"``."""
    del package
    version = _table(_table(tool, "pdm"), "version")
    if not version:
        return None
    source = _text(version.get("source"))
    if source == "scm":
        return VersionSourceOptions(kind=_TAG)
    if source == "file":
        path = _text(version.get("path"))
        if path is not None:
            return VersionSourceOptions(kind=_FILE, path=path)
    return None


def _requires_scm(document: Mapping[str, Any]) -> bool:
    """Whether ``[build-system].requires`` names a version-from-tag plugin.

    The last detector to run, because it is the weakest signal: a project may list a plugin and
    still pin its version somewhere a stronger table already named.
    """
    from packaging.requirements import InvalidRequirement, Requirement

    requires = _table(document, "build-system").get("requires")
    if not isinstance(requires, list):
        return False
    wanted = {normalize_name(name) for name in SCM_BUILD_REQUIREMENTS}
    for entry in requires:
        if not isinstance(entry, str):
            continue
        try:
            name = Requirement(entry).name
        except InvalidRequirement:
            continue
        if normalize_name(name) in wanted:
            return True
    return False


# --------------------------------------------------------------------------------------
# ``{attr = "pkg.__version__"}`` -> a file path, by convention and never by import
# --------------------------------------------------------------------------------------


def resolve_attr_path(attr: str, package: Package) -> str | None:
    """The relative path of the module holding ``attr``, by the import-path convention.

    Three candidates, in order: ``<module>/__init__.py``, ``src/<module>/__init__.py``,
    ``<module>.py``. The dotted attribute's last segment is the *attribute*; everything before it
    is the module. A layout none of the three finds returns ``None``, and the package lands in the
    unresolved bucket rather than being guessed at -- the explicit ``path`` declaration is the
    remedy the message names (gap ``VS-6``).

    Importing would be correct for every layout and is exactly what design D2 rules out: it runs
    the project's top-level code to learn a string.
    """
    module, _, attribute = attr.rpartition(".")
    if not module or not attribute:
        return None
    parts = module.split(".")
    if not all(parts):
        return None
    stem = "/".join(parts)
    for candidate in (f"{stem}/__init__.py", f"src/{stem}/__init__.py", f"{stem}.py"):
        if (package.directory / candidate).is_file():
            return candidate
    return None


def attribute_pattern(attr: str) -> str:
    """A version pattern locating exactly the attribute ``attr`` names.

    setuptools has told molt the attribute's name, so molt uses it rather than falling back to the
    three-name default: a module that assigns both ``VERSION`` and ``__version__`` is ambiguous to
    the default pattern and unambiguous here.
    """
    name = re.escape(attr.rpartition(".")[2])
    return (
        rf"(?m)^[ \t]*{name}[ \t]*(?::[^=\n]+)?=[ \t]*"
        r"(?P<quote>['\"])(?P<version>[^'\"\n]+)(?P=quote)"
    )


# --------------------------------------------------------------------------------------
# Small readers
# --------------------------------------------------------------------------------------


def _pattern_for(table: Mapping[str, Any]) -> str | None:
    """A tool table's own ``pattern`` member, when it is a usable string.

    hatchling accepts one and a project that needs it has already written it down; reading it costs
    nothing and is strictly better than making the user restate it under ``[tool.molt]``.
    """
    value = table.get("pattern")
    return value if isinstance(value, str) and value and "(?P<version>" in value else None


def _table(document: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = document.get(key)
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
