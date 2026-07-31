"""molt's CI automation loops -- what a release workflow calls.

Ports ``packages/release-utils`` (research doc 04 section 6), the half of the changesets GitHub
Action that is *not* YAML: open a version branch and push it, publish what a merge produced, and
scrape a changelog for the section a pull-request body should quote.

Two halves, deliberately separate
---------------------------------
- :mod:`molt.action.run` -- the git choreography (:func:`run_version`, :func:`run_publish`). It
  spawns a subprocess and pushes to a remote.
- :mod:`molt.action.utils` -- pure string parsing (:func:`get_changelog_entry`,
  :func:`sort_changelog_entries`, :data:`BUMP_LEVELS`). No filesystem, no git, no network, so the
  part of the action most likely to be wrong is the part that is cheapest to test.

What this package is **not**
-----------------------------
It is not the packaged composite action. The ``action.yml``, the ``setup-uv`` step and the pinned
``uvx molt`` wiring are not delivered here, and neither is the outer pull-request orchestration --
creating or updating the release PR, its title and body, and the ``hasChangesets`` branching. That
outer loop lives in the separate ``changesets/action`` repository, which is **not** in this
checkout (research README section 8), so it is left un-designed rather than reconstructed from
memory. Everything in this package is documented behavior from ``release-utils``, which is present.
"""

from __future__ import annotations

from molt.action.run import (
    DEFAULT_COMMIT_MESSAGE,
    VERSION_BRANCH_PREFIX,
    ChangedPackage,
    PublishedPackage,
    RunPublishResult,
    RunVersionResult,
    run_publish,
    run_version,
)
from molt.action.utils import (
    BUMP_LEVELS,
    ChangelogEntry,
    changelog_entry_sort_key,
    get_changelog_entry,
    sort_changelog_entries,
)

__all__ = [
    "BUMP_LEVELS",
    "DEFAULT_COMMIT_MESSAGE",
    "VERSION_BRANCH_PREFIX",
    "ChangedPackage",
    "ChangelogEntry",
    "PublishedPackage",
    "RunPublishResult",
    "RunVersionResult",
    "changelog_entry_sort_key",
    "get_changelog_entry",
    "run_publish",
    "run_version",
    "sort_changelog_entries",
]
