"""The publish pipeline: decide, build, upload -- three stages, one versioned document.

changesets v3 split publishing into ``publish-plan`` -> ``pack --out-dir`` ->
``publish --from-pack-dir`` (research README section 3.2). molt keeps that shape because PyPI makes
it *more* necessary rather than less: versions are immutable, there is no unpublish, and a partial
monorepo publish cannot be rolled back (research README section 4.3). Splitting the decision from
the upload is what lets everything that can be checked be checked while it is still free.

What lives where
----------------
- :mod:`molt.publish.plan` -- computing the plan
  (:func:`~molt.publish.plan.build_publish_plan`), the versioned envelope, and the guard every
  reader of a plan file goes through (:func:`~molt.publish.plan.read_publish_plan`).
- :mod:`molt.pack` -- producing the artifacts and writing an enriched plan. The *library* is
  ``molt.pack`` while the *command* is ``molt build``: ``molt.build`` would read as a build backend
  sitting next to ``python -m build``.
- :mod:`molt.publish.publish` -- the upload stage, and the only irreversible thing molt does.
  Exhaustive pre-flight, chunked uploads in dependency order, stop-on-failure, and the git tags
  that record what went out.
- :mod:`molt.publish.yank` -- ``molt yank``, which is **read-only**: PyPI exposes no yank API, so
  the command verifies the release and prints the browser steps rather than pretending to act.

Nothing here ever prompts. PyPI has no publish-time OTP, so upstream's whole ``AuthState``
interactive-retry machine is deleted rather than ported (research README section 4.4), and a
credential that cannot be resolved is a failure rather than a question -- which is what keeps a CI
job from hanging on something nobody will answer.
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
    tag_name,
    targets_public_pypi,
    write_publish_plan,
)
from molt.publish.publish import (
    DUPLICATE_MARKERS,
    DUPLICATE_STATUS,
    PublishResult,
    ReleaseRef,
    TrustedPublishingOIDC,
    UploadRejected,
    UvUploader,
    is_duplicate_upload_error,
    publish,
)
from molt.publish.yank import (
    DEFAULT_REPOSITORY,
    MANAGE_URL_PATH,
    IndexRelease,
    PyPIIndex,
    YankReport,
    yank,
)

__all__ = [
    "ARTIFACT_KINDS",
    "DEFAULT_REPOSITORY",
    "DUPLICATE_MARKERS",
    "DUPLICATE_STATUS",
    "INTEGRITY_PREFIX",
    "MANAGE_URL_PATH",
    "PLAN_FILENAME",
    "PUBLISH_PLAN_VERSION",
    "SNAPSHOT_VERSION_PATTERN",
    "IndexRelease",
    "Plan",
    "PlanEntry",
    "PublishResult",
    "PyPIIndex",
    "ReleaseRef",
    "TrustedPublishingOIDC",
    "UploadRejected",
    "UvUploader",
    "YankReport",
    "build_publish_plan",
    "chunk_entries",
    "is_duplicate_upload_error",
    "is_snapshot_version",
    "plan_envelope",
    "publish",
    "publishable_members",
    "read_publish_plan",
    "tag_name",
    "targets_public_pypi",
    "write_publish_plan",
    "yank",
]
