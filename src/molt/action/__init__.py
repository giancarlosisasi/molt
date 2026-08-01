"""molt's CI automation loops -- what a release workflow calls.

Ports ``packages/release-utils`` (research doc 04 section 6), the half of the changesets GitHub
Action that is *not* YAML: open a version branch and push it, publish what a merge produced, and
scrape a changelog for the section a pull-request body should quote.

Six parts, deliberately separate
--------------------------------
- :mod:`molt.action.run` -- the git choreography (:func:`run_version`, :func:`run_publish`). It
  spawns a subprocess and pushes to a remote -- unless :class:`CommitMode` selects the host's API,
  in which case the version phase makes no local branch, no local commit and no push at all.
- :mod:`molt.action.api_commit` -- the working tree read as file changes
  (:func:`collect_file_changes`), which is what an API commit carries. Two git reads and three
  refusals; no network.
- :mod:`molt.action.utils` -- pure string parsing (:func:`get_changelog_entry`,
  :func:`sort_changelog_entries`, :data:`BUMP_LEVELS`). No filesystem, no git, no network, so the
  part of the action most likely to be wrong is the part that is cheapest to test.
- :mod:`molt.action.mode` -- the four-case branch matrix (:func:`select_mode`), a
  ``.changeset/``-only read that decides whether a run is the version half or the publish half.
- :mod:`molt.action.body` -- the release pull request's body and its three-tier truncation
  (:func:`build_pull_request_body`), pure for the same reason ``utils`` is.
- :mod:`molt.action.orchestrate` -- the outer loop (:func:`run_action`) that composes all four and
  the forge seam.

What this package is **not**
-----------------------------
It is not the composite action document itself. ``action.yml`` lives at the repository root and the
``molt-action`` entry point is :mod:`molt.action.cli`; nothing in *this* module tree registers a
``molt`` verb, deliberately. An earlier version of this docstring said the outer pull-request
orchestration was absent because it lived in the separate ``changesets/action`` repository, which
was not in the checkout; that repository's v1.9.0 tag was fetched and read on 2026-07-31, every
rule is now ported with a citation, and the YAML shipped with ``implement-composite-action``
(``CA-1``, closed).

It has **no pre mode**. Upstream marks a release pull request opened during a prerelease with a
title suffix and a warning banner, read out of ``.changeset/pre.json``. molt keeps no such state --
``molt pre`` exists only to refuse and point at ``molt version --pre {a,b,rc,dev}`` -- so there is
nothing to read and nothing is invented (``openspec/GAPS.md`` ``AP-1``).
"""

from __future__ import annotations

from molt.action.api_commit import collect_file_changes
from molt.action.body import (
    DEFAULT_PR_TITLE,
    MAX_BODY_CHARACTERS,
    build_pull_request_body,
    pull_request_entry,
)
from molt.action.mode import Mode, select_mode
from molt.action.orchestrate import ActionFailed, ActionResult, run_action
from molt.action.run import (
    DEFAULT_COMMIT_MESSAGE,
    VERSION_BRANCH_PREFIX,
    ChangedPackage,
    CommitMode,
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
    "DEFAULT_PR_TITLE",
    "MAX_BODY_CHARACTERS",
    "VERSION_BRANCH_PREFIX",
    "ActionFailed",
    "ActionResult",
    "ChangedPackage",
    "ChangelogEntry",
    "CommitMode",
    "Mode",
    "PublishedPackage",
    "RunPublishResult",
    "RunVersionResult",
    "build_pull_request_body",
    "changelog_entry_sort_key",
    "collect_file_changes",
    "get_changelog_entry",
    "pull_request_entry",
    "run_action",
    "run_publish",
    "run_version",
    "select_mode",
    "sort_changelog_entries",
]
