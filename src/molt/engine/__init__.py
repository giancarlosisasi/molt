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
"""

from __future__ import annotations

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

__all__ = [
    "WORKSPACE_PREFIX",
    "ChangesetLike",
    "ChangesetReleaseLike",
    "Classification",
    "DependencyKind",
    "EdgeKind",
    "GraphConfig",
    "GraphError",
    "ManifestLike",
    "PackageLike",
    "PackagesLike",
    "PlanConfig",
    "PrePhase",
    "Release",
    "ReleasePlan",
    "SnapshotParams",
    "assemble_release_plan",
    "classify_dependency",
    "get_dependents_graph",
    "relative_package_path",
    "resolve_workspace_range",
]
