"""Choosing a version source per package, and resolving a whole workspace in one pass.

**Design D1: this is a second pass, not part of discovery.**
:func:`molt.ecosystem.discover_workspace` stays a pure read of manifests -- reading a version file
means opening a second file per member, and a future tag source means a subprocess, while discovery
runs on every command. Resolving lazily on ``Package.version`` was the other alternative and is
worse still: ``Package`` is a frozen value passed everywhere, and a property with I/O behind it
turns every read into an unpredictable cost and surfaces failures at arbitrary call sites.

An explicit pass instead means the caller decides when to pay, can inject a double, and gets
**all** failures at once as data rather than as exceptions raised one at a time -- the same
two-channel shape :func:`molt.config.load_config` uses, and for the same reason.

The resolution order (design D1)
--------------------------------
1. the ``[tool.molt.version_source]`` table the package's **own** manifest declares;
2. the source detected from that manifest's build-tool tables -- consulted only when the manifest
   declares no literal ``[project].version``, because a manifest that wrote a version down has
   already answered the question and a leftover tool table beside it must not override the author;
3. a static ``[project].version``.

The first that applies wins, and no later step overrides it. There is no merging: two sources for
one package would give molt two versions and no rule for choosing between them.

Sources resolve through the ``molt.version_source`` entry-point group with **no privileged path**
for molt's own three (design D8), by the same prefix-stripping mechanism
:func:`molt.commit.load_provider` and :func:`molt.apply.generators.resolve_generator` share (owner
ruling ``AC-7``, 2026-07-30). Entry points come from **installed** distribution metadata, so
anybody pulling this must ``uv sync``, not just ``git pull`` -- the reminder steps 13 and ``CO``
both had to write.
"""

from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING, Any

from molt.ecosystem.version_sources.declaration import VersionSourceOptions
from molt.ecosystem.version_sources.detect import detect_version_source
from molt.ecosystem.version_sources.protocol import (
    ResolvedVersion,
    UnresolvedVersion,
    VersionResolution,
    VersionSource,
)
from molt.errors import MoltError
from molt.names import normalize_name

if TYPE_CHECKING:
    from pathlib import Path

    from molt.ecosystem.protocol import Package, Workspace

__all__ = [
    "VERSION_SOURCE_GROUP",
    "load_version_source_builder",
    "resolve_version_source",
    "resolve_workspace_versions",
]

#: The entry-point group a distribution registers a version source in.
VERSION_SOURCE_GROUP = "molt.version_source"

#: The kind :func:`resolve_version_source` falls back to when a manifest declares a literal
#: version and names no source of its own.
_STATIC = "static"


def resolve_version_source(package: Package, *, root: Path) -> VersionSource | None:
    """The one source ``package``'s version comes from, or ``None`` when it has no version at all.

    ``root`` is the workspace root, carried so a caller can render a source's description relative
    to it; nothing here reads outside ``package.directory``.

    Raises:
        MoltError: the declared source names a kind that is not installed, or the declaration is
            unusable (a ``file`` source with no path, a path escaping the package directory).
            :func:`resolve_workspace_versions` catches these and reports them as data.
    """
    # Imported here, not at module scope: `molt.ecosystem.manifest` imports this package's
    # `declaration` module, so a module-scope import would close the cycle at interpreter start.
    from molt.ecosystem.manifest import read_toml

    del root
    declared = package.version_source
    if declared is not None:
        return _build(package, declared)

    if package.version is None:
        detected = detect_version_source(package, read_toml(package.manifest_path))
        if detected is not None:
            return _build(package, detected)

    if package.version is not None:
        return _build(package, VersionSourceOptions(kind=_STATIC))
    return None


def resolve_workspace_versions(workspace: Workspace) -> VersionResolution:
    """Resolve every member's version in one pass. **Never raises.**

    Every failure becomes an :class:`~molt.ecosystem.version_sources.protocol.UnresolvedVersion`
    carrying a printable reason, so one unreleasable package cannot block the whole run (owner
    ruling 2026-08-01, session 6: warn and carry on -- failing the run was the stricter alternative
    and was declined). The line where molt still refuses is unchanged: a changeset that *names* the
    package.

    A member with no static version and no ``dynamic = ["version"]`` is in **neither** channel --
    design D7's middle bucket. It is not a versioned distribution, so it is skipped silently and
    its pins still move, exactly as before this seam existed.
    """
    resolved: dict[str, ResolvedVersion] = {}
    unresolved: list[UnresolvedVersion] = []
    for package in workspace.packages:
        entry, failure = _resolve_one(package, root=workspace.root)
        if entry is not None:
            resolved[normalize_name(package.name)] = entry
        elif failure is not None:
            unresolved.append(failure)
    return VersionResolution(resolved=resolved, unresolved=tuple(unresolved))


def _resolve_one(
    package: Package, *, root: Path
) -> tuple[ResolvedVersion | None, UnresolvedVersion | None]:
    """One member's outcome: resolved, unresolved-and-named, or neither (the middle bucket)."""
    try:
        source = resolve_version_source(package, root=root)
    except MoltError as error:
        return None, UnresolvedVersion(name=package.name, kind=None, reason=str(error))

    if source is None:
        if not package.dynamic_version:
            return None, None
        return None, UnresolvedVersion(name=package.name, kind=None, reason=_no_source(package))

    try:
        return ResolvedVersion(name=package.name, version=source.read(), source=source), None
    except MoltError as error:
        return None, UnresolvedVersion(name=package.name, kind=source.kind, reason=str(error))


def _no_source(package: Package) -> str:
    """The reason a ``dynamic = ["version"]`` member with no detectable source is skipped.

    Names the package, says what molt looked for and names the remedy -- design D7 requires a
    complete, printable sentence including the fix, because this bucket exists precisely to replace
    silence.
    """
    return (
        f'"{package.name}" declares dynamic = ["version"] but molt could not work out where its '
        f"version comes from: it recognises [tool.hatch.version], [tool.setuptools.dynamic] and "
        f"[tool.pdm.version], and found none of them. Add [tool.molt.version_source] with "
        f'kind = "file" and a path to {package.name}\'s own pyproject.toml, or give it a static '
        f"[project].version."
    )


# --------------------------------------------------------------------------------------
# The entry-point group (design D8)
# --------------------------------------------------------------------------------------


def _build(package: Package, options: VersionSourceOptions) -> VersionSource:
    """Construct the source ``options`` names, through the entry-point group."""
    builder = load_version_source_builder(options.kind)
    source = builder(
        name=package.name,
        directory=package.directory,
        version=package.version,
        options=options,
    )
    if not isinstance(source, VersionSource):  # pragma: no cover - a broken third-party plugin
        raise MoltError(
            f'The version source "{options.kind}" did not produce a usable source: it must offer '
            f"kind, read(), plan_write(new_version) and describe()."
        )
    return source


def load_version_source_builder(kind: str) -> Any:
    """The ``build`` callable registered for ``kind``, or a refusal naming what *is* installed.

    Two spellings match, exactly as :func:`molt.apply.generators._entry_point` accepts: the written
    name, and the written name with the ``molt.version_source.`` group prefix stripped. Stripping
    the prefix rather than special-casing molt's own three keeps every built-in on the path a
    third-party source takes, which is what makes the seam proven by molt's own use of it.
    """
    candidates = {kind}
    prefix = f"{VERSION_SOURCE_GROUP}."
    if kind.startswith(prefix):
        candidates.add(kind[len(prefix) :])
    available: list[str] = []
    for entry_point in importlib.metadata.entry_points(group=VERSION_SOURCE_GROUP):
        available.append(entry_point.name)
        if entry_point.name in candidates:
            loaded = entry_point.load()
            builder = getattr(loaded, "build", loaded)
            if not callable(builder):  # pragma: no cover - a broken third-party plugin
                raise MoltError(
                    f'The version source "{kind}" is registered but exposes no callable build().'
                )
            return builder
    raise MoltError(
        f'Unknown version source "{kind}". molt resolves version sources through the '
        f'"{VERSION_SOURCE_GROUP}" entry-point group; installed sources are '
        f"{', '.join(sorted(available)) or '(none)'}."
    )
