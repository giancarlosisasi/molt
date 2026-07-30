"""molt's release engine -- the dependency graph, and the release plan computed over it.

Two halves. :mod:`molt.engine.graph` answers *which packages depend on which, and how tightly* --
the edge set. :mod:`molt.engine.assemble` answers *what gets released, and at which version* --
the monotone 3-pass fixpoint loop, ``fixed`` / ``linked`` resolution, and PEP 440 snapshot and
prerelease derivation.

This is the moat. Both abandoned Python ports of changesets died on dependency propagation, and it
is where a wrong answer is least visible: an edge that should not exist silently suppresses a
dependent's release, a missing one silently over-releases, and every output here is a version
number nobody can eyeball for correctness. Read each module's docstring before changing a rule --
several of them look like bugs and are not, and several deliberately are not upstream's.

Two smaller modules sit either side of them. :mod:`molt.engine.adapters` turns what molt actually
holds -- a :class:`molt.ecosystem.Workspace` and a :class:`molt.config.Config` -- into the ported
shapes the two halves read, which is what lets a command drive the engine from a real repository.
:mod:`molt.engine.view` turns the assembled plan back into a reportable value: string versions and
a JSON-ready payload, shared by every command that prints a plan.
"""

from __future__ import annotations

from molt.engine.adapters import (
    EngineConfig,
    EngineManifest,
    EnginePackage,
    EnginePackages,
    is_versionable,
    skipped_package_names,
    to_engine_config,
    to_engine_packages,
)
from molt.engine.assemble import (
    ChangesetLike,
    ChangesetReleaseLike,
    PlanConfig,
    PrePhase,
    Release,
    ReleasePlan,
    SnapshotParams,
    assemble_release_plan,
)
from molt.engine.graph import (
    WORKSPACE_PREFIX,
    Classification,
    DependencyKind,
    EdgeKind,
    GraphConfig,
    GraphError,
    ManifestLike,
    PackageLike,
    PackagesLike,
    classify_dependency,
    get_dependents_graph,
    relative_package_path,
    resolve_workspace_range,
)
from molt.engine.view import (
    ChangesetReleaseView,
    ChangesetView,
    PlanView,
    ReleaseView,
    plan_payload,
    plan_view,
)

__all__ = [
    "WORKSPACE_PREFIX",
    "ChangesetLike",
    "ChangesetReleaseLike",
    "ChangesetReleaseView",
    "ChangesetView",
    "Classification",
    "DependencyKind",
    "EdgeKind",
    "EngineConfig",
    "EngineManifest",
    "EnginePackage",
    "EnginePackages",
    "GraphConfig",
    "GraphError",
    "ManifestLike",
    "PackageLike",
    "PackagesLike",
    "PlanConfig",
    "PlanView",
    "PrePhase",
    "Release",
    "ReleasePlan",
    "ReleaseView",
    "SnapshotParams",
    "assemble_release_plan",
    "classify_dependency",
    "get_dependents_graph",
    "is_versionable",
    "plan_payload",
    "plan_view",
    "relative_package_path",
    "resolve_workspace_range",
    "skipped_package_names",
    "to_engine_config",
    "to_engine_packages",
]
