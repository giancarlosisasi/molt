"""The bridge from real discovery and real configuration into the engine's ported shapes.

:mod:`molt.engine.graph` and :mod:`molt.engine.assemble` read their inputs **structurally**, in the
shape ``@changesets/types`` defines and ``tests/engine`` declares: a workspace of
``root_dir`` / ``packages[i].dir`` / ``manifest.{name, version, dependencies, dev_dependencies,
optional_dependencies}`` where each dependency section is a ``name -> constraint`` map, and a flat
configuration object whose snapshot and private-package keys are single attributes. Neither is what
molt actually holds: :class:`molt.ecosystem.Workspace` carries PEP 508 requirement *lists* and
``pathlib`` directories, and :class:`molt.config.Config` nests ``snapshot`` and
``private_packages``.

This module is the only place those two worlds meet. It closes the three adapter gaps recorded
against the engine (``openspec/GAPS.md``: ``RPE-1`` private-package skipping, ``RPE-2`` the config
adapter, ``RPE-3`` the workspace adapter), and it is deliberately shared rather than inlined in one
command: ``molt status`` and ``molt version`` must drive the engine from *identical* inputs, or the
preview and the release it previews can disagree.

Three rules are worth reading before changing anything here.

**Private packages are folded into ``ignore``** (``RPE-1``). Upstream's ``shouldSkipPackage``
(``assemble-release-plan/src/index.ts``) is one predicate -- *ignored, or private while
``privatePackages.version`` is false* -- consulted at every skip site: flattening, dependent
propagation, and fixed-group forcing. The engine's ported ``Packages`` shape carries no privacy
marker at all, so it cannot re-derive the second half; naming the private packages in ``ignore``
reproduces the same predicate at all three sites without teaching the engine a Python-only concept.
The one place the two differ is the mixed-changeset diagnostic -- see the ``## Open gaps`` section
of this change's ``design.md``.

**A workspace source arrives already normalized.** ``[tool.uv.sources]`` is read by
:mod:`molt.ecosystem.uv`, which records ``workspace:*`` / ``workspace:<relpath>`` on
:attr:`molt.ecosystem.Package.workspace_sources`; this module only moves that marker into the
constraint slot the engine reads. A dependency that carries *both* a workspace source and a PEP 440
specifier keeps the specifier (as ``workspace:<specifier>``), because the specifier is the tighter
statement and the engine already understands that spelling.

**The workspace root is a member when it declares ``[project]``.** That is uv's rule, and
:class:`molt.ecosystem.Workspace` already applies it, so the root appears in both ``root_package``
and ``packages``. ``molt.engine.graph``'s docstring anticipates exactly this: "a backend that lists
the root among ``packages`` is declaring it a real member, and it is treated as one".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from packaging.requirements import InvalidRequirement, Requirement

from molt.names import normalize_name

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from molt.config.models import Config
    from molt.ecosystem import Package, Workspace

__all__ = [
    "EngineConfig",
    "EngineManifest",
    "EnginePackage",
    "EnginePackages",
    "is_versionable",
    "skipped_package_names",
    "to_engine_config",
    "to_engine_packages",
]

#: A PEP 508 requirement splits into the distribution name and everything after it -- the *tail*
#: the ported shape puts in its constraint slot (``""``, ``"==1.0.0"``, ``"[extra]>=1 ; marker"``,
#: ``"@ file:///..."``). Splitting rather than re-rendering keeps the author's spelling, which
#: :func:`molt.engine.graph.classify_dependency` reads verbatim -- it decides "location, not
#: constraint" by looking for a ``:``.
_NAME_AND_TAIL = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$", re.DOTALL)

#: The prefix :mod:`molt.ecosystem` normalizes a workspace source to, and the one
#: :mod:`molt.engine.graph` classifies. Spelled here so this module reads without a second import.
_WORKSPACE_PREFIX = "workspace:"


# ======================================================================================
# The ported workspace shape (RPE-3)
# ======================================================================================


@dataclass(frozen=True)
class EngineManifest:
    """``[project]`` as the engine reads it: three ``name -> constraint`` maps.

    Satisfies :class:`molt.engine.graph.ManifestLike`. ``version`` is ``None`` for a
    dynamically-versioned distribution, which the engine refuses rather than skips (``RPE-4``).
    """

    name: str
    version: str | None
    dependencies: dict[str, str] = field(default_factory=dict)
    dev_dependencies: dict[str, str] = field(default_factory=dict)
    optional_dependencies: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EnginePackage:
    """One workspace member: its manifest plus its directory as a POSIX string.

    Satisfies :class:`molt.engine.graph.PackageLike`. ``dir`` is a string, not a ``Path``, because
    the engine compares it against a ``workspace:<relpath>`` source written in a manifest, and that
    comparison has to mean the same thing on Windows.
    """

    manifest: EngineManifest
    dir: str


@dataclass(frozen=True)
class EnginePackages:
    """The discovered workspace, in the ported ``@changesets/types`` shape.

    Satisfies :class:`molt.engine.graph.PackagesLike`.
    """

    root_package: EnginePackage | None
    root_dir: str
    packages: tuple[EnginePackage, ...]


def to_engine_packages(workspace: Workspace) -> EnginePackages:
    """Adapt a discovered :class:`molt.ecosystem.Workspace` to the engine's input shape.

    Every member keeps its declared name spelling; PEP 503 folding is the engine's own job and it
    does it on both sides of every comparison. Discovery order is preserved -- it reaches the
    release plan and then the changelog, so re-sorting here would churn changelog diffs.
    """
    root = workspace.root.as_posix()
    packages = tuple(_engine_package(package) for package in workspace.packages)
    root_package = None
    if workspace.root_package is not None:
        root_package = _engine_package(workspace.root_package)
    return EnginePackages(root_package=root_package, root_dir=root, packages=packages)


def _engine_package(package: Package) -> EnginePackage:
    sources = {normalize_name(name): marker for name, marker in package.workspace_sources}
    return EnginePackage(
        manifest=EngineManifest(
            name=package.name,
            version=package.version,
            dependencies=_constraints(package.dependencies, sources),
            dev_dependencies=_constraints(package.dev_dependencies, sources),
            optional_dependencies=_constraints(package.optional_dependencies, sources),
        ),
        dir=package.directory.as_posix(),
    )


def _constraints(requirements: Sequence[str], sources: Mapping[str, str]) -> dict[str, str]:
    """Turn PEP 508 requirement strings into the ``name -> constraint`` map the engine reads.

    An entry molt cannot parse is dropped rather than guessed at: the engine treats an unreadable
    declaration as "not an edge" anyway (graph design D2), and inventing a name for it would be the
    one way to make it worse than silent.
    """
    constraints: dict[str, str] = {}
    for entry in requirements:
        parsed = _split(entry)
        if parsed is None:
            continue
        name, tail = parsed
        marker = sources.get(normalize_name(name))
        constraints[name] = tail if marker is None else _workspace_constraint(entry, marker)
    return constraints


def _split(entry: str) -> tuple[str, str] | None:
    """Split one requirement into ``(declared name, tail)``, or ``None`` when it is not one."""
    match = _NAME_AND_TAIL.match(entry)
    if match is None:
        return None
    try:
        requirement = Requirement(entry)
    except InvalidRequirement:
        return None
    name, tail = match.group(1), match.group(2).strip()
    if normalize_name(name) != normalize_name(requirement.name):
        # The regex and the real parser disagree, which means the entry starts with something that
        # only looks like a name. Trust the parser and give up the author's spelling.
        return requirement.name, tail
    return name, tail


def _workspace_constraint(entry: str, marker: str) -> str:
    """Merge a workspace source with whatever specifier the requirement already declared.

    ``pkg-a`` plus ``{ workspace = true }`` is ``workspace:*``; ``pkg-a>=1.2`` plus the same source
    is ``workspace:>=1.2``. Keeping the specifier matters because ``workspace:*`` resolves to an
    **exact** pin on the dependency's current version (``determine-dependents.ts:199-202``), which
    is a strictly different -- and much churnier -- claim than the one the author wrote.
    """
    try:
        specifier = Requirement(entry).specifier
    except InvalidRequirement:  # pragma: no cover - `_split` already rejected these
        return marker
    text = str(specifier)
    return f"{_WORKSPACE_PREFIX}{text}" if text else marker


# ======================================================================================
# The flat configuration shape (RPE-2), with the private-package skip folded in (RPE-1)
# ======================================================================================


@dataclass(frozen=True)
class EngineConfig:
    """molt's configuration, flattened to the attribute names the engine and apply read.

    Satisfies :class:`molt.engine.assemble.PlanConfig`, :class:`molt.engine.graph.GraphConfig` and
    :class:`molt.apply.apply.ApplyConfigLike` at once -- deliberately, because the three describe
    one configuration and a caller holding a plan already holds everything apply needs.

    ``ignore`` is the *effective* skip list: the configured one plus every private package when
    ``private_packages.version`` is false. ``private_packages_version`` is carried through unchanged
    so ``molt.apply``, which can still see a manifest's classifiers, keeps its own equivalent rule.
    """

    ignore: tuple[str, ...] = ()
    fixed: tuple[tuple[str, ...], ...] = ()
    linked: tuple[tuple[str, ...], ...] = ()
    bump_workspace_sources_only: bool = False
    update_internal_dependents: str = "out-of-range"
    update_internal_dependencies: str = "patch"
    snapshot_use_calculated_version: bool = False
    snapshot_prerelease_template: str | None = None
    private_packages_version: bool = True
    changelog: Any = None
    base_branch: str = "main"
    changed_file_patterns: tuple[str, ...] = ("**",)


def to_engine_config(config: Config, workspace: Workspace) -> EngineConfig:
    """Flatten ``config`` for the engine, folding the private-package skip into ``ignore``.

    ``workspace`` is required for exactly that fold: privacy is a property of a manifest, not of
    the configuration, and the ported ``Packages`` shape the engine reads cannot carry it.
    """
    return EngineConfig(
        ignore=skipped_package_names(workspace, config),
        fixed=tuple(tuple(group) for group in config.fixed),
        linked=tuple(tuple(group) for group in config.linked),
        bump_workspace_sources_only=config.bump_workspace_sources_only,
        update_internal_dependents=config.update_internal_dependents,
        update_internal_dependencies=config.update_internal_dependencies,
        snapshot_use_calculated_version=config.snapshot.use_calculated_version,
        snapshot_prerelease_template=config.snapshot.prerelease_template,
        private_packages_version=config.private_packages.version,
        changelog=config.changelog,
        base_branch=config.base_branch,
        changed_file_patterns=tuple(config.changed_file_patterns),
    )


def skipped_package_names(workspace: Workspace, config: Config) -> tuple[str, ...]:
    """Every package the release must not version: the configured ``ignore``, plus private ones.

    Configured entries come first and in their configured order -- they are already expanded to
    concrete workspace names by :mod:`molt.config`, so no globbing happens here (and must not: that
    is what produced upstream's ``matchFixedConstraint`` bug). Private packages follow in workspace
    discovery order. Duplicates are removed under PEP 503 folding, keeping first appearance.
    """
    private: Iterable[str] = ()
    if not config.private_packages.version:
        private = (package.name for package in workspace.packages if package.private)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in (*config.ignore, *private):
        key = normalize_name(name)
        if key not in seen:
            seen.add(key)
            ordered.append(name)
    return tuple(ordered)


def is_versionable(package: Package, config: Config) -> bool:
    """Whether ``package`` is a package this configuration releases at all.

    The Python reading of upstream's ``shouldSkipPackage``: not ignored, and not private while
    ``private_packages.version`` is false. Used by ``molt status``'s CI gate, which asks the
    question about a *changed* package rather than about a planned release.
    """
    if package.private and not config.private_packages.version:
        return False
    key = normalize_name(package.name)
    return all(key != normalize_name(entry) for entry in config.ignore)
