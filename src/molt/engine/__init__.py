"""molt's release engine -- the dependency graph today, the release plan next.

The engine is built in two halves, and only the first is here. :mod:`molt.engine.graph` answers
*which packages depend on which, and how tightly* -- the edge set the release plan is computed
over. The plan itself (``assemble_release_plan``, the 3-pass fixpoint loop, ``fixed`` / ``linked``
resolution and snapshots) is build step 7 and will land as ``molt.engine.assemble``.

This is the moat. Both abandoned Python ports of changesets died on dependency propagation, and
the graph is where a wrong answer is least visible: an edge that should not exist silently
suppresses a dependent's release, while a missing one silently over-releases. Read
:mod:`molt.engine.graph`'s docstring before changing any classification rule -- several of them
look like bugs and are not.
"""

from __future__ import annotations

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
    resolve_workspace_range,
)

__all__ = [
    "WORKSPACE_PREFIX",
    "Classification",
    "DependencyKind",
    "EdgeKind",
    "GraphConfig",
    "GraphError",
    "ManifestLike",
    "PackageLike",
    "PackagesLike",
    "classify_dependency",
    "get_dependents_graph",
    "resolve_workspace_range",
]
