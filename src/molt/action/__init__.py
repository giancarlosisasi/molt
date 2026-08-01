"""molt's CI automation loops -- what a release workflow calls.

Ports ``packages/release-utils`` (research doc 04 section 6), the half of the changesets GitHub
Action that is *not* YAML: open a version branch and push it, publish what a merge produced, and
scrape a changelog for the section a pull-request body should quote.

Four parts, deliberately separate
---------------------------------
- :mod:`molt.action.run` -- the git choreography (:func:`run_version`, :func:`run_publish`). It
  spawns a subprocess and pushes to a remote.
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
It is not the packaged composite action: the ``action.yml``, the ``setup-uv`` step and the pinned
``uvx molt-cli`` wiring are not delivered here, and nothing in this package registers a CLI command
(``openspec/GAPS.md`` ``CA-1``). The outer pull-request orchestration **is** here now -- an earlier
version of this docstring said it was absent because it lived in the separate ``changesets/action``
repository, which was not in the checkout; that repository's v1.9.0 tag was fetched and read on
2026-07-31 and every rule is now ported with a citation. What is still missing is only the YAML.

It has **no pre mode**. Upstream marks a release pull request opened during a prerelease with a
title suffix and a warning banner, read out of ``.changeset/pre.json``. molt keeps no such state --
``molt pre`` exists only to refuse and point at ``molt version --pre {a,b,rc,dev}`` -- so there is
nothing to read and nothing is invented (``openspec/GAPS.md`` ``AP-1``).
"""

from __future__ import annotations

from molt.action.body import (
    DEFAULT_PR_TITLE,
    MAX_BODY_CHARACTERS,
    build_pull_request_body,
    pull_request_entry,
)
from molt.action.mode import Mode, select_mode
from molt.action.orchestrate import ActionResult, run_action
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
    "DEFAULT_PR_TITLE",
    "MAX_BODY_CHARACTERS",
    "VERSION_BRANCH_PREFIX",
    "ActionResult",
    "ChangedPackage",
    "ChangelogEntry",
    "Mode",
    "PublishedPackage",
    "RunPublishResult",
    "RunVersionResult",
    "build_pull_request_body",
    "changelog_entry_sort_key",
    "get_changelog_entry",
    "pull_request_entry",
    "run_action",
    "run_publish",
    "run_version",
    "select_mode",
    "sort_changelog_entries",
]
