"""molt's forge seam and its first backend.

:class:`Forge` is the protocol; :class:`GitHubForge` is the only backend that ships today. GitLab,
Gitea, Bitbucket and Azure DevOps are planned, and the point of the seam is that adding one is
writing a backend rather than re-architecting the release loop
(``website/docs/forges/gitlab-gitea-others.md``).

Nothing outside :mod:`molt.forge` may import :mod:`molt.forge.github`: that indirection is the
seam, the same rule ``molt.ecosystem`` follows for its uv backend. Callers construct a backend here
and then speak only :class:`Forge`.

What the protocol covers, and it is now the whole list
------------------------------------------------------
``website/docs/forges/overview.md`` ("What a forge backend provides") promises three answers, and
all three are declared: **attribution** (:meth:`Forge.commit_info`, :meth:`Forge.pull_request_info`
-- who wrote a change and which pull request carried it), **release publication**
(:meth:`Forge.create_release`), and the **pull-request lifecycle**
(:meth:`Forge.find_open_pull_request`, :meth:`Forge.create_pull_request`,
:meth:`Forge.update_pull_request` -- find the open release pull request for a branch, open one, or
replace the title and body of the one that is already there).

Importing this package costs ``httpx``, which is why it is on ``tests/cli/test_cli.py``'s
``HEAVY_MODULE_PREFIXES`` list -- ``molt --help`` must not pay for it, so a command that needs a
forge imports it inside ``run()``.
"""

from __future__ import annotations

from molt.forge.github import (
    ASSOCIATED_PULL_REQUESTS_LIMIT,
    DEFAULT_GRAPHQL_URL,
    DEFAULT_SERVER_URL,
    GITHUB_API_VERSION,
    REQUEST_TIMEOUT_SECONDS,
    GitHubForge,
)
from molt.forge.protocol import (
    SHORT_SHA_LENGTH,
    AuthorRef,
    CommitInfo,
    CommitRef,
    Forge,
    PullRef,
    PullRequestInfo,
    ReleaseInfo,
    validate_repo_name,
)
from molt.forge.retry import MAX_ATTEMPTS, MAX_BACKOFF_SECONDS, MAX_TOTAL_BACKOFF_SECONDS

__all__ = [
    "ASSOCIATED_PULL_REQUESTS_LIMIT",
    "DEFAULT_GRAPHQL_URL",
    "DEFAULT_SERVER_URL",
    "GITHUB_API_VERSION",
    "MAX_ATTEMPTS",
    "MAX_BACKOFF_SECONDS",
    "MAX_TOTAL_BACKOFF_SECONDS",
    "REQUEST_TIMEOUT_SECONDS",
    "SHORT_SHA_LENGTH",
    "AuthorRef",
    "CommitInfo",
    "CommitRef",
    "Forge",
    "GitHubForge",
    "PullRef",
    "PullRequestInfo",
    "ReleaseInfo",
    "validate_repo_name",
]
