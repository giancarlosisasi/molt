"""The publish pipeline: decide, build, upload -- three stages, one versioned document.

changesets v3 split publishing into ``publish-plan`` -> ``pack --out-dir`` ->
``publish --from-pack-dir`` (research README section 3.2). molt keeps that shape because PyPI makes
it *more* necessary rather than less: versions are immutable, there is no unpublish, and a partial
monorepo publish cannot be rolled back (research README section 4.3). Splitting the decision from
the upload is what lets everything that can be checked be checked while it is still free.

What lives where
----------------
- :mod:`molt.publish.plan` -- this change. Computing the plan
  (:func:`~molt.publish.plan.build_publish_plan`), the versioned envelope, and the guard every
  reader of a plan file goes through (:func:`~molt.publish.plan.read_publish_plan`).
- :mod:`molt.pack` -- this change. Producing the artifacts and writing an enriched plan. The
  *library* is ``molt.pack`` while the *command* is ``molt build``: ``molt.build`` would read as a
  build backend sitting next to ``python -m build``.
- ``publish()`` and ``yank()`` -- **not here yet**. The upload stage is a later change; this package
  deliberately exposes no ``publish`` attribute, which is what keeps
  ``tests/publish/test_publish.py`` and ``test_yank.py`` skipped while their implementation is
  still owed. Nothing in this change talks to a real index.
"""

from __future__ import annotations

from molt.publish.plan import (
    ARTIFACT_KINDS,
    INTEGRITY_PREFIX,
    PLAN_FILENAME,
    PUBLISH_PLAN_VERSION,
    SNAPSHOT_VERSION_PATTERN,
    Plan,
    PlanEntry,
    build_publish_plan,
    chunk_entries,
    is_snapshot_version,
    plan_envelope,
    publishable_members,
    read_publish_plan,
    targets_public_pypi,
    write_publish_plan,
)

__all__ = [
    "ARTIFACT_KINDS",
    "INTEGRITY_PREFIX",
    "PLAN_FILENAME",
    "PUBLISH_PLAN_VERSION",
    "SNAPSHOT_VERSION_PATTERN",
    "Plan",
    "PlanEntry",
    "build_publish_plan",
    "chunk_entries",
    "is_snapshot_version",
    "plan_envelope",
    "publishable_members",
    "read_publish_plan",
    "targets_public_pypi",
    "write_publish_plan",
]
