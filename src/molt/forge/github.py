"""The GitHub forge backend: hand-written GraphQL over ``httpx``, with a cache that hits.

**Attribution and commit creation are GraphQL; releases and the pull-request lifecycle are REST.**
That split is GitHub's own, not molt drifting, and each half is forced:

* GitHub's GraphQL schema has **no release-creation mutation**, so
  :meth:`GitHubForge.create_release` posts to the REST API (``changesets/action`` v1.9.0
  ``src/run.ts::createRelease`` reaches for ``octokit.rest.repos.createRelease`` for the same
  reason), and the pull-request lifecycle follows it there: listing and creating a pull request are
  REST upstream too, and molt updates one over REST as well rather than through the GraphQL
  mutation upstream needs only because it also flips draft state (design D2).
* GitHub has **no signed REST commit**. The REST git-database path
  (``blobs`` -> ``trees`` -> ``commits`` -> ``refs``) composes the commit object *client-side*, so
  there is nothing for GitHub to sign and the result shows as unverified. The GraphQL
  ``createCommitOnBranch`` mutation is the only API that authors a multi-file commit server-side,
  which is what makes it signed and marked verified -- so :meth:`GitHubForge.create_commit` is
  GraphQL (api-commits design D2). The ref choreography around it is REST, because GraphQL has no
  ref mutations either.

Every transport goes through the one :meth:`GitHubForge._send` loop, so none of them can ship
without the resilience the first has.

Ports ``packages/get-github-info`` @ v3.0.0-next.9 -- ``env.ts``, ``utils.ts``, ``dataloader.ts``,
``get-commit-info.ts`` and ``get-pull-request-info.ts`` (research doc 08, the two
``get-github-info`` test sections: 9 rows, all "Adapt"). No GraphQL client library and no Octokit:
the two queries are small enough to write out, and ``website/docs/forges/github.md`` states the
choice as product behavior -- "a small, hand-written GraphQL query over ``httpx`` -- not a
heavyweight REST client".

The two upstream defects this file refuses to port (research README section 3.4)
-------------------------------------------------------------------------------
1. **A cache that never hits.** ``dataloader.ts:62-72`` calls ``GHDataLoader.load({...options,
   kind})`` -- a freshly allocated object per call -- and configures no ``cacheKeyFn``, so
   DataLoader's map is keyed by object *identity* and every lookup misses. molt keys by
   ``(repo, kind, id)`` (design D4). The conformance rows build equal-but-distinct argument
   strings on purpose, so an identity-keyed cache cannot make them pass.
2. **Zero resilience.** See :mod:`molt.forge.retry`.

Two more failure modes, neither of which upstream reports as a failure
---------------------------------------------------------------------
* **A 200 is not success** (design D6). GraphQL puts errors in the body: HTTP 200 with an
  ``errors`` array and no ``data``. A backend that checked only the status reads the entity as
  merely *missing* and drops the attribution silently -- a published changelog with no credits and
  no error anywhere. :func:`GitHubForge._read_data` checks the body before anything reads a result.
* **A missing token is an error, not an anonymous request** (design D7). Anonymous GitHub access
  gets 60 requests an hour, so falling back to it turns one clear configuration mistake into an
  intermittent failure much later whose message looks like "the repository does not exist".

Configuration is environment-first (``env.ts:22-34``; ``website/docs/forges/github.md``,
"Configuration"), because that is what CI already sets. Constructor keywords exist for the
GitHub-flavored changelog generator, which the same page documents as able to "take an explicit
``repo`` in its options instead of relying on ``GITHUB_REPOSITORY``".
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Any, Literal

import httpx

from molt.errors import MoltForgeError
from molt.forge.protocol import (
    NO_FILE_ADDITIONS,
    AuthorRef,
    CommitInfo,
    CommitRef,
    PullRef,
    PullRequestInfo,
    ReleaseInfo,
    validate_repo_name,
)
from molt.forge.retry import (
    MAX_ATTEMPTS,
    MAX_TOTAL_BACKOFF_SECONDS,
    backoff_delay,
    is_transient_status,
    retry_after_seconds,
    sleep_for,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "ASSOCIATED_PULL_REQUESTS_LIMIT",
    "DEFAULT_GRAPHQL_URL",
    "DEFAULT_SERVER_URL",
    "GITHUB_API_VERSION",
    "REQUEST_TIMEOUT_SECONDS",
    "TEMPORARY_BRANCH_PREFIX",
    "GitHubForge",
]

#: Where a failed temporary-ref cleanup is reported. A release whose commit already landed must not
#: fail because a leftover ref could not be deleted, so the failure is logged rather than raised --
#: and the backend has no console seam of its own (``molt.forge`` is imported inside a command
#: body, never at module scope), so the standard library's logger is the channel. Same mechanism
#: ``molt.apply`` uses.
_LOGGER = logging.getLogger("molt.forge")

#: ``env.ts:23-26``. Overridden by ``GITHUB_GRAPHQL_URL`` for GitHub Enterprise Server -- the
#: override routes the request, and every URL in a result comes from the response, so the rendered
#: links follow it too (design D8).
DEFAULT_GRAPHQL_URL = "https://api.github.com/graphql"

#: ``env.ts:27-30``. The human-facing host, overridden by ``GITHUB_SERVER_URL``.
DEFAULT_SERVER_URL = "https://github.com"

#: ``dataloader.ts:31`` -- ``associatedPullRequests(first: 50)``. This bounds the candidate set
#: attribution is chosen from, so it is behavior rather than a tuning knob: raising it changes
#: which pull request an old, much-cherry-picked commit is credited to.
ASSOCIATED_PULL_REQUESTS_LIMIT = 50

#: An explicit request timeout, which upstream does not set at all -- ``website/docs/forges/
#: github.md`` promises one. Without it a hung connection stalls a release with no output.
REQUEST_TIMEOUT_SECONDS = 30.0

#: The REST API version header GitHub asks every REST client to pin. Unpinned, a future default
#: version changes the response shape :meth:`GitHubForge.create_release` reads ``html_url`` and
#: ``id`` out of, in somebody's CI, with no change on molt's side.
GITHUB_API_VERSION = "2022-11-28"

#: The suffix a GraphQL endpoint carries, stripped to derive the REST base when ``GITHUB_API_URL``
#: is not set (design D5).
_GRAPHQL_PATH_SUFFIX = "/graphql"

#: Where :meth:`GitHubForge.create_commit` stages a replacement release branch (api-commits design
#: D4, ``openspec/GAPS.md`` ``ACM-3``). Upstream's ``@changesets/ghcommit`` uses
#: ``changesets-ghcommit-temp/<branch>``; molt namespaces it under ``molt/`` instead, because it is
#: molt's ref in the user's repository and a ``changesets-`` prefix in a molt user's branch list is
#: a support question waiting to happen. One prefix also lets a repository protect or ignore the
#: whole namespace with a single rule.
TEMPORARY_BRANCH_PREFIX = "molt/tmp/"

#: The two headers GitHub's REST API asks a client to pin, sent by every REST call this backend
#: makes. The GraphQL path sends ``Accept: application/json``, which is right there and wrong here.
_REST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": GITHUB_API_VERSION,
}

#: The variables ``env.ts:5-34`` reads, in the order that page documents them, plus
#: ``GITHUB_API_URL`` -- the REST base, which every GitHub Actions runner exports (GitHub
#: Enterprise Server included). It joins this tuple rather than being read on its own so the
#: ``.env`` fallback covers it on exactly the same terms as the other four.
_ENVIRONMENT_VARIABLES = (
    "GITHUB_TOKEN",
    "GITHUB_REPOSITORY",
    "GITHUB_SERVER_URL",
    "GITHUB_GRAPHQL_URL",
    "GITHUB_API_URL",
)

# The two queries, written out. `string.Template` rather than an f-string or `.format()` on
# purpose: GraphQL is made of braces, and doubling all 30 of them would make the text stop reading
# like the query that goes on the wire -- which is the one thing these constants exist to be,
# since `tests/forge/test_github.py` pins both byte for byte. Neither query uses GraphQL variables,
# so `$` is free to be the placeholder marker; a future query that needs `$var` must escape it as
# `$$var`.
#
# The `repo__0` / `commit__<sha>` / `pull__<n>` aliasing is not decoration: it IS upstream's
# batching mechanism (``dataloader.ts:133-138`` reads the response back by those exact keys), and
# it is what lets one query carry several repositories once molt batches lookups.
_COMMIT_QUERY = Template("""\
query {
  repo__0: repository(
    owner: "$owner",
    name: "$name"
  ) {
    commit__$sha: object(expression: "$sha") {
      ... on Commit {
        ...CommitFragment
      }
    }
  }
}
fragment CommitFragment on Commit {
  commitUrl
  associatedPullRequests(first: $first) {
    nodes {
      number
      url
      mergedAt
      author {
        login
        url
      }
    }
  }
  author {
    user {
      login
      url
    }
  }
}
""")

_PULL_QUERY = Template("""\
query {
  repo__0: repository(
    owner: "$owner",
    name: "$name"
  ) {
    pull__$number: pullRequest(number: $number) {
      ...PullFragment
    }
  }
}
fragment PullFragment on PullRequest {
  url
  author {
    login
    url
  }
  mergeCommit {
    commitUrl
    abbreviatedOid
  }
}
""")

# The commit mutation is the first query here that DOES use a GraphQL variable, so it escapes `$`
# as `$$` exactly as the comment above says a future one must. Everything it sends travels in that
# one `$input` variable rather than being interpolated into the text -- which is not a style
# choice: `fileChanges` carries base64 file contents and `message` carries whatever a workflow put
# in its `commit` input, and interpolating either into a query is how a payload becomes a query
# injection (api-commits task 3.1).
#
# The branch is addressed as `{repositoryNameWithOwner, branchName}` (design D2). GitHub's
# `CommittableBranch` accepts either that pair or a global node id; upstream's
# `@changesets/ghcommit` resolves the node id first, which is one extra round trip and one more
# hand-written query to keep.
_CREATE_COMMIT_MUTATION = Template("""\
mutation ($$input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $$input) {
    commit {
      oid
      commitUrl
    }
  }
}
""")


def _dotenv_values(path: Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines out of a ``.env`` file; return ``{}`` if there is nothing to read.

    ``env.ts:5-20`` loads a ``.env`` before reading the environment, and ``website/docs/forges/
    github.md`` promises it: "For local runs, molt also reads these from a ``.env`` file if
    present." Hand-parsed rather than adding a dependency, because molt needs four known variable
    names and none of ``python-dotenv``'s interpolation, multiline or export semantics.

    Every failure is swallowed deliberately. A ``.env`` that is unreadable, a directory, or not
    UTF-8 must not stop a release: the variable it would have supplied is then simply unset, which
    the caller already reports actionably.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _environment() -> dict[str, str]:
    """The five GitHub variables, with the real environment winning over ``.env``.

    Precedence matters and is the same as every dotenv loader's default: an exported variable is a
    deliberate act -- what CI does -- and a ``.env`` is a convenience for a local run, so the file
    must never shadow it. Empty values are dropped rather than kept, so ``GITHUB_TOKEN=`` reads as
    "unset" and produces the actionable missing-token error instead of an empty ``Authorization``
    header and a 401.
    """
    values = {
        key: value
        for key, value in _dotenv_values(Path.cwd() / ".env").items()
        if key in _ENVIRONMENT_VARIABLES and value
    }
    for key in _ENVIRONMENT_VARIABLES:
        actual = os.environ.get(key)
        if actual:
            values[key] = actual
    return values


def _rest_base(api_url: str, configured: str | None) -> str:
    """The REST base URL: ``GITHUB_API_URL`` if it is set, else the GraphQL URL minus ``/graphql``.

    Release creation is the one call that cannot be GraphQL -- GitHub's schema has no
    release-creation mutation, which is why upstream reaches for ``octokit.rest.repos
    .createRelease`` -- so the backend needs a second base URL. It stays **private** and never
    reaches :class:`molt.forge.Forge` (design D5): the protocol's endpoint is ``api_url``
    precisely so a backend whose transport is REST does not have to pretend it speaks GraphQL, and
    a second, transport-named attribute on the protocol would re-import that mistake.

    The derived fallback is exact for ``https://api.github.com/graphql`` and **approximate** for
    GitHub Enterprise Server, whose REST base is ``/api/v3`` while its GraphQL base is
    ``/api/graphql``. Recorded as a gap rather than papered over: on a real GHES runner
    ``GITHUB_API_URL`` is set and this fallback never runs.
    """
    if configured:
        return configured.rstrip("/")
    return api_url.rstrip("/").removesuffix(_GRAPHQL_PATH_SUFFIX).rstrip("/")


def _is_duplicate_release(response: httpx.Response) -> bool:
    """Whether ``response`` says the host already carries a release for that tag.

    A **conjunction**, deliberately: HTTP 422 *and* an ``errors[].code`` of ``already_exists``.
    GitHub answers an ordinary validation failure -- a body over the size limit, a malformed tag
    -- with the same 422, and reading that as "already released" would drop a release with no
    error anywhere. Anything unrecognised stays a failure, which is the safe direction. Same shape
    as :func:`molt.publish.is_duplicate_upload_error` for the package index, and for the same
    reason.
    """
    if response.status_code != httpx.codes.UNPROCESSABLE_ENTITY:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    errors = body.get("errors")
    if not isinstance(errors, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("code") == "already_exists" for entry in errors
    )


def _is_rate_limited_forbidden(response: httpx.Response) -> bool:
    """Whether a ``403`` is GitHub's secondary rate limit rather than a permanent refusal.

    GitHub answers two different situations with the same 403 status: a permanently forbidden
    request (a bad token, a scope the token lacks) and a *secondary* rate limit, which clears on
    its own. Only the response headers tell them apart -- ``x-ratelimit-remaining: 0`` marks an
    exhausted budget, and a ``Retry-After`` header marks a throttle that names its own wait. Owner
    ruling 2026-07-30 (research README section 3.4; ``openspec/GAPS.md`` ``FS-2``): a 403 carrying
    either header is transient and retried within the same bounded budget as every other transient
    status; a 403 carrying neither stays permanent, exactly as it did before this ruling.

    This lives here rather than in :mod:`molt.forge.retry` because that module commits to staying
    host-neutral, and ``x-ratelimit-remaining`` is GitHub's header name -- a second backend would
    not necessarily share it.
    """
    return (
        response.headers.get("x-ratelimit-remaining") == "0"
        or response.headers.get("Retry-After") is not None
    )


def _author_ref(node: Mapping[str, Any] | None) -> AuthorRef | None:
    """An ``{login, url}`` block as an :class:`AuthorRef`, or ``None`` when the block is null.

    ``author: null`` is ordinary, not exceptional: a deleted account on a pull request, or a commit
    whose email GitHub cannot map to a user (``get-commit-info.ts:65-71``).
    """
    if not node:
        return None
    login, url = node.get("login"), node.get("url")
    if not isinstance(login, str) or not isinstance(url, str):
        return None
    return AuthorRef(login=login, url=url)


def _pull_ref(document: Any) -> PullRef | None:
    """A REST pull-request document as a :class:`PullRef`, or ``None`` if it is not one.

    ``html_url`` rather than ``url``: the REST API returns both, and ``url`` is the *API* endpoint
    -- a link a workflow log printed from it would take a human to a JSON document. Read off the
    response and never built from a base URL, which is the rule that makes GitHub Enterprise Server
    work with no backend change (design D8).
    """
    if not isinstance(document, dict):
        return None
    number, url = document.get("number"), document.get("html_url")
    if not isinstance(number, int) or not isinstance(url, str):
        return None
    return PullRef(number=number, url=url)


def _commit_ref(data: Mapping[str, Any]) -> CommitRef:
    """The commit ``createCommitOnBranch`` created, read off its response.

    ``oid`` and ``commitUrl`` come **off the response** and are never built from a base URL
    (design D8) -- the rule that makes a GitHub Enterprise Server install work with no backend
    change. A 200 that carries no commit is a failure rather than a ``None``: the mutation
    reported success, so molt cannot tell the caller "no commit was needed" and cannot report one
    either.
    """
    payload = data.get("createCommitOnBranch")
    commit = payload.get("commit") if isinstance(payload, dict) else None
    if not isinstance(commit, dict):
        raise MoltForgeError(
            f"The GitHub API accepted the commit mutation but returned no commit: {data!r}"
        )
    oid, url = commit.get("oid"), commit.get("commitUrl")
    if not isinstance(oid, str):
        raise MoltForgeError(f"The GitHub API returned a created commit with no oid: {commit!r}")
    return CommitRef(sha=oid, url=url if isinstance(url, str) else "")


def _merge_key(node: Mapping[str, Any]) -> tuple[bool, str]:
    """Sort key implementing ``get-commit-info.ts:43-51`` -- earliest merge first, nulls last.

    The first element is what puts unmerged pull requests at the end; the second orders the merged
    ones by their ISO-8601 timestamp, which sorts correctly as text because GitHub always returns
    the same zero-padded, ``Z``-suffixed format.

    A **total** order matters here. Upstream's comparator returns 0 for a null/non-null pair, which
    leaves the winner dependent on the order GraphQL happened to return the nodes in, and GitHub
    promises no order at all; ``test_the_earliest_merged_pull_request_wins_regardless_of_node
    _order`` permutes the same five nodes to catch exactly that.
    """
    merged_at = node.get("mergedAt")
    return (not isinstance(merged_at, str), merged_at if isinstance(merged_at, str) else "")


def _select_pull_request(nodes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Which associated pull request a commit is attributed to (``get-commit-info.ts:42-55``).

    One merged pull request wins; among several, the **earliest** merged one does, because that is
    the pull request that introduced the change and a later merge is a backport or a re-land. With
    none merged, the **first associated** one is used -- upstream's comparator ties for every pair
    and ``Array.prototype.sort`` is stable, so ``[0]`` is the first node as given, and
    :func:`sorted` is stable for the same reason.

    That last case is what separates a faithful port from ``min()`` over a merged-only list, which
    would return ``None`` here and silently drop the pull-request link from an open-PR changelog
    preview.
    """
    if not nodes:
        return None
    return sorted(nodes, key=_merge_key)[0]


class GitHubForge:
    """molt's GitHub backend; satisfies :class:`molt.forge.Forge`.

    One instance per release run. The cache lives on the instance (design D4) rather than in a
    module-level singleton like ``dataloader.ts:62``, so its lifetime is the run's lifetime: a
    process-global cache is unbounded, survives a config change, cannot be cleared between two
    releases in one process, and makes every test that touches it order-dependent.
    """

    #: How config selects this backend once there is more than one.
    name = "github"

    def __init__(
        self,
        *,
        repo: str | None = None,
        token: str | None = None,
        api_url: str | None = None,
        server_url: str | None = None,
    ) -> None:
        """Resolve endpoints and credentials, keyword overrides first, then the environment.

        Nothing is validated here and nothing is requested here. Construction has to stay cheap and
        total: ``molt status`` builds a forge to render links it may never look up, and a
        constructor that rejected a malformed ``GITHUB_REPOSITORY`` would fail a command that was
        never going to call the API.
        """
        environment = _environment()
        self.repo = repo if repo is not None else environment.get("GITHUB_REPOSITORY")
        self.api_url = api_url or environment.get("GITHUB_GRAPHQL_URL") or DEFAULT_GRAPHQL_URL
        self.server_url = server_url or environment.get("GITHUB_SERVER_URL") or DEFAULT_SERVER_URL
        self._token = token if token is not None else environment.get("GITHUB_TOKEN")
        #: The REST base, private on purpose -- see :func:`_rest_base`.
        self._rest_url = _rest_base(self.api_url, environment.get("GITHUB_API_URL"))
        #: ``(repo, kind, id) -> result``, the fix for the identity-keyed DataLoader. ``None``
        #: results are cached too: "this commit does not exist" is an answer, and re-asking for it
        #: once per changelog line is the storm the cache exists to prevent.
        self._cache: dict[tuple[str, str, str], CommitInfo | PullRequestInfo | None] = {}

    # -- the protocol ------------------------------------------------------------------

    def validate_repo(self, repo: str) -> None:
        """Reject anything that is not ``owner/name`` (``utils.ts:1-9``)."""
        validate_repo_name(repo)

    def commit_info(self, commit: str, *, repo: str | None = None) -> CommitInfo | None:
        """Resolve ``commit`` to its pull request and author; ``None`` if there is no such commit.

        Absence is a value, not an exception: the changelog generator's documented fallback -- a
        bare ``[`sha7`]`` code span with no link -- is built on this returning ``None``.
        """
        target = self._target_repo(repo)
        node = self._lookup("commit", target, commit)
        return node if isinstance(node, CommitInfo) else None

    def pull_request_info(self, pull: int, *, repo: str | None = None) -> PullRequestInfo | None:
        """Resolve ``pull`` to its author and merge commit; ``None`` if it is absent."""
        target = self._target_repo(repo)
        node = self._lookup("pull", target, str(pull))
        return node if isinstance(node, PullRequestInfo) else None

    def create_release(
        self,
        tag: str,
        *,
        name: str,
        body: str,
        prerelease: bool = False,
        repo: str | None = None,
    ) -> ReleaseInfo | None:
        """Create a GitHub Release for ``tag``; ``None`` if one already exists.

        Ports ``changesets/action`` v1.9.0 ``src/run.ts::createRelease``, which sends exactly
        these four fields once per released package, after ``git.pushTag`` -- so the tag exists
        before the release references it. molt changes three things about it:

        * **The transport is REST** (design D3). GitHub's GraphQL schema has no release-creation
          mutation, so this is the one call that leaves :meth:`_request`'s body handling behind.
          It still goes through :meth:`_send`, so the bounded retry budget, the ``Retry-After``
          floor, the 403 ruling and the mandatory token all apply to it unchanged.
        * **A duplicate is a value, not an exception** (design D2). Upstream throws whatever the
          API returns, so re-running a publish that half-failed fails again on the packages that
          already succeeded.
        * **``prerelease`` is the caller's answer, not this backend's** (design D4). Upstream
          derives it from ``version.includes("-")``, which is npm semver's rule and wrong for PEP
          440; :func:`molt.versioning.is_prerelease` is where that decision lives.

        The repository is validated before any socket is opened -- the same guard the attribution
        path keeps, and load-bearing here too, because the slug is interpolated into the URL.
        """
        response = self._send(
            "POST",
            f"{self._repo_url(repo)}/releases",
            json={"tag_name": tag, "name": name, "body": body, "prerelease": prerelease},
            headers=_REST_HEADERS,
        )
        if response.status_code == httpx.codes.CREATED:
            return self._release_info(response, tag=tag, name=name)
        if _is_duplicate_release(response):
            return None
        raise self._http_error(response)

    def find_open_pull_request(
        self, head: str, *, base: str, repo: str | None = None
    ) -> PullRef | None:
        """The open pull request from ``head`` onto ``base``, or ``None`` (``run.ts:347-378``).

        Upstream's ``octokit.rest.pulls.list`` sends ``state: "open"``, ``head:
        "<owner>:<branch>"`` and ``base``, then takes ``data[0]``. All three filters are
        load-bearing: without ``state`` a *closed* release pull request would be found and updated
        into a zombie, and without ``head`` the query returns every open pull request in the
        repository, whose first element is whatever the host happens to list first.

        The ``head`` filter's owner prefix comes from the target slug rather than from a separate
        context object -- molt has no ambient GitHub context to read one out of. The consequence is
        deliberate: a release branch pushed from a fork is not found, which is also true upstream,
        because the action pushes to the repository it runs in.

        With more than one open match the **first** the host lists wins, faithfully. That needs
        someone to have opened a second one by hand; the ordering is then the host's, not molt's
        (``openspec/GAPS.md`` ``AP-9``).
        """
        owner, _, _name = self._target_repo(repo).partition("/")
        response = self._send(
            "GET",
            f"{self._repo_url(repo)}/pulls",
            params={"state": "open", "head": f"{owner}:{head}", "base": base},
            headers=_REST_HEADERS,
        )
        if response.status_code != httpx.codes.OK:
            raise self._http_error(response)
        listing = self._json_body(response)
        if not isinstance(listing, list):
            raise MoltForgeError(f"The GitHub API returned an unexpected payload: {listing!r}")
        for entry in listing:
            pull = _pull_ref(entry)
            if pull is not None:
                return pull
        return None

    def create_pull_request(
        self, head: str, *, base: str, title: str, body: str, repo: str | None = None
    ) -> PullRef:
        """Open the release pull request (``run.ts:363-370``)."""
        response = self._send(
            "POST",
            f"{self._repo_url(repo)}/pulls",
            json={"base": base, "head": head, "title": title, "body": body},
            headers=_REST_HEADERS,
        )
        if response.status_code != httpx.codes.CREATED:
            raise self._http_error(response)
        return self._require_pull_ref(response)

    def update_pull_request(
        self, number: int, *, title: str, body: str, repo: str | None = None
    ) -> PullRef:
        """Replace a pull request's title and body, and re-open it (``run.ts:391-407``).

        **Adapted transport** (design D2): upstream sends a GraphQL ``updatePullRequest`` mutation
        because the same mutation optionally flips the pull request to draft, which needs the node
        id. Draft modes are out of scope, so the node id buys nothing and would mean carrying a
        ``node_id`` field on :class:`~molt.forge.PullRef` that no other caller wants. REST ``PATCH``
        takes the number the lookup already returned.

        ``state: "open"`` is **ported, not incidental**: a force-push onto the release branch can
        close the pull request, and the next run must reopen that one rather than open a second.
        """
        response = self._send(
            "PATCH",
            f"{self._repo_url(repo)}/pulls/{number}",
            json={"title": title, "body": body, "state": "open"},
            headers=_REST_HEADERS,
        )
        if response.status_code != httpx.codes.OK:
            raise self._http_error(response)
        return self._require_pull_ref(response)

    def create_commit(
        self,
        branch: str,
        *,
        base: str,
        message: str,
        additions: Mapping[str, bytes] = NO_FILE_ADDITIONS,
        deletions: Sequence[str] = (),
        repo: str | None = None,
    ) -> CommitRef | None:
        """Make ``branch`` carry one commit holding these changes on top of ``base``.

        Ports ``changesets/action`` v1.9.0 ``src/git.ts::pushChanges`` and the
        ``@changesets/ghcommit`` ``src/core.ts::commitChanges`` choreography it calls
        (api-commits design D2 and D4; both fetched and read 2026-08-01).

        **GitHub signs what it authors.** The GraphQL ``createCommitOnBranch`` mutation is the only
        GitHub API that composes a multi-file commit server-side -- "Commits made using this
        mutation are automatically signed by GitHub if supported and will be marked as verified" --
        which is the whole product claim behind ``commit-mode: api``. The REST git-database path
        would deliver every mechanic of this method and none of its purpose, because a commit
        object the client builds itself has nothing for GitHub to sign.

        The choreography, and why the temporary branch is not an optimisation to remove::

            head = read_ref(branch)
            if head is None:   create_ref(branch, base); commit on branch
            elif head == base: commit on branch
            else:              force temp -> base; commit on temp;
                               force branch -> commit; delete temp

        Forcing the release branch back to ``base`` and committing on it would be one call fewer
        and is exactly what ``@changesets/ghcommit`` warns against in its own comment: *"We cannot
        reset the branch and then commit because if the branch has an existing PR, GitHub will
        auto-close as it sees there's no changes with the base."* molt is more exposed to that than
        upstream, because reusing one release pull request across days is pinned product behaviour
        -- its number, its review comments and its subscribers have to survive every update, and
        with "automatically delete head branches" on, an auto-close can take the branch with it and
        the following mutation then fails against a ref that no longer exists.

        The temporary ref is **force-updated** when it already exists rather than created, so a
        branch left behind by a crashed run cannot fail the next one, and it is deleted in a
        ``finally`` whose own failure is logged rather than raised -- a release whose commit already
        landed must not fail on cleanup.

        ``None`` with no additions and no deletions (design D5): the branch is still forced onto
        ``base``, exactly as ``git-cli`` mode force-pushes unconditionally, but **no mutation is
        sent**. Sending one with empty ``fileChanges`` would write an empty commit onto the release
        branch every time a version script no-ops, and the release pull request's diff is a public
        document.

        A stale ``expectedHeadOid`` is reported and **not retried** -- see :meth:`_commit_on`.
        """
        target = self._target_repo(repo)
        if not message.strip():
            raise MoltForgeError(
                "A commit message is required: `message` was empty or whitespace only. GitHub's "
                "commit headline cannot be blank, so molt refuses before sending the commit."
            )

        payload_additions = [
            {"path": path, "contents": base64.b64encode(bytes(contents)).decode("ascii")}
            for path, contents in sorted(additions.items())
        ]
        payload_deletions = [{"path": path} for path in sorted(deletions)]

        if not payload_additions and not payload_deletions:
            self._force_ref(branch, base, repo=repo)
            return None

        file_changes: dict[str, Any] = {}
        if payload_additions:
            file_changes["additions"] = payload_additions
        if payload_deletions:
            file_changes["deletions"] = payload_deletions

        commit_on = partial(
            self._commit_on, slug=target, expected=base, message=message, changes=file_changes
        )

        head = self._read_ref(branch, repo=repo)
        if head is None:
            self._create_ref(branch, base, repo=repo)
            return commit_on(branch)
        if head == base:
            return commit_on(branch)

        temporary = f"{TEMPORARY_BRANCH_PREFIX}{branch}"
        self._force_ref(temporary, base, repo=repo)
        try:
            commit = commit_on(temporary)
            self._update_ref(branch, commit.sha, repo=repo)
        finally:
            self._delete_ref(temporary, repo=repo)
        return commit

    def _commit_on(
        self,
        branch: str,
        *,
        slug: str,
        expected: str,
        message: str,
        changes: Mapping[str, Any],
    ) -> CommitRef:
        """Send one ``createCommitOnBranch`` mutation and read the commit back out of it.

        The message is split into GraphQL's ``CommitMessage``: the first line is ``headline``
        (a required non-empty scalar) and the remainder is ``body``, omitted when there is none.

        **A stale head is reported, never retried.** ``expectedHeadOid`` is what makes this write
        safe -- it means "commit only if nothing else has touched this branch since I measured it"
        -- so GitHub answering HTTP 200 with an ``errors`` array saying the head moved is a real
        answer, not a blip. Retrying past it would commit changes measured against a base that is
        no longer there, which is how a release silently loses somebody else's work. The retry
        budget in :meth:`_send` never sees it: a 200 is not a transient status.
        """
        headline, _, body = message.partition("\n")
        commit_message: dict[str, str] = {"headline": headline.strip() or message.strip()}
        if body.strip():
            commit_message["body"] = body.strip("\n")

        variables = {
            "input": {
                "branch": {
                    "repositoryNameWithOwner": slug,
                    "branchName": branch,
                },
                "expectedHeadOid": expected,
                "message": commit_message,
                "fileChanges": dict(changes),
            }
        }
        try:
            data = self._request(_CREATE_COMMIT_MUTATION.substitute(), variables=variables)
        except MoltForgeError as error:
            raise MoltForgeError(
                f"GitHub refused the commit on {branch!r} at {expected}: {error}. Another run may "
                f"have moved the branch; molt does not retry, because the changes were measured "
                f"against {expected} and committing them onto a different head would discard that "
                f"work."
            ) from error
        return _commit_ref(data)

    def _require_pull_ref(self, response: httpx.Response) -> PullRef:
        """Read a pull-request document into a :class:`PullRef`, or fail saying what came back.

        Unlike a created *release* -- whose partial response is reported rather than raised on,
        because the release exists either way -- a pull request molt cannot identify is useless to
        the caller: the number is what the next run updates, and the URL is what the workflow
        prints. Guessing either would strand the release pull request.
        """
        pull = _pull_ref(self._json_body(response))
        if pull is None:
            raise MoltForgeError(
                "The GitHub API answered a pull-request call without a number and a URL: "
                f"{response.text[:200]!r}"
            )
        return pull

    @staticmethod
    def _release_info(response: httpx.Response, *, tag: str, name: str) -> ReleaseInfo:
        """Read a created-release response into a :class:`ReleaseInfo`.

        ``html_url`` and ``id`` are read off the response and never built from a base URL
        (design D8), which is what makes a GitHub Enterprise Server install work with no backend
        change. ``tag`` and ``name`` echo the caller, the same way ``CommitRef.sha`` does.

        A response that is missing either field is reported with an empty URL and a ``0`` id
        rather than raised on, deliberately: the release **was** created, and failing after a
        successful write would leave a caller believing nothing happened, then meeting a duplicate
        on the retry. A body that is not JSON at all is a different thing -- that means the
        response did not come from the API -- and does raise.
        """
        try:
            document = response.json()
        except ValueError as exc:
            raise MoltForgeError(
                f"The GitHub API returned a {response.status_code} that is not JSON: "
                f"{response.text[:200]!r}"
            ) from exc
        if not isinstance(document, dict):
            raise MoltForgeError(f"The GitHub API returned an unexpected payload: {document!r}")
        url = document.get("html_url")
        identifier = document.get("id")
        return ReleaseInfo(
            tag=tag,
            name=name,
            url=url if isinstance(url, str) else "",
            id=identifier if isinstance(identifier, int) else 0,
        )

    # -- refs (api-commits design D4) --------------------------------------------------
    #
    # REST, because GraphQL has no ref mutations. Each one is a single request through `_send`, so
    # the retry budget, the `Retry-After` floor, the 403 ruling and the mandatory token apply to
    # them exactly as they do to everything else this backend sends.

    def _read_ref(self, branch: str, *, repo: str | None) -> str | None:
        """The sha ``refs/heads/<branch>`` points at, or ``None`` when the host does not have it.

        ``GET /repos/{owner}/{repo}/git/ref/heads/{branch}`` -- the **singular** ``ref`` endpoint,
        which answers 404 for a branch that does not exist. Absence is a value here: a first release
        run has no release branch yet, and that is the ordinary case rather than a failure.
        """
        response = self._send(
            "GET", f"{self._repo_url(repo)}/git/ref/heads/{branch}", headers=_REST_HEADERS
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if response.status_code != httpx.codes.OK:
            raise self._http_error(response)
        document = self._json_body(response)
        if not isinstance(document, dict):
            raise MoltForgeError(f"The GitHub API returned an unexpected payload: {document!r}")
        obj = document.get("object")
        sha = obj.get("sha") if isinstance(obj, dict) else None
        if not isinstance(sha, str):
            raise MoltForgeError(
                f"The GitHub API answered a ref lookup for {branch!r} with no sha: "
                f"{response.text[:200]!r}"
            )
        return sha

    def _create_ref(self, branch: str, sha: str, *, repo: str | None) -> None:
        """Create ``refs/heads/<branch>`` at ``sha`` (``POST .../git/refs``)."""
        response = self._send(
            "POST",
            f"{self._repo_url(repo)}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
            headers=_REST_HEADERS,
        )
        if response.status_code != httpx.codes.CREATED:
            raise self._http_error(response)

    def _force_ref(self, branch: str, sha: str, *, repo: str | None) -> None:
        """Point ``refs/heads/<branch>`` at ``sha``, creating it if it is not there yet.

        A create that answers 422 means the ref already exists, which is the **normal** state for a
        temporary branch a previous run was interrupted before deleting. Treating that as fatal
        would let one crashed run block every release afterwards, so it force-updates instead.
        """
        response = self._send(
            "POST",
            f"{self._repo_url(repo)}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
            headers=_REST_HEADERS,
        )
        if response.status_code == httpx.codes.CREATED:
            return
        if response.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
            self._update_ref(branch, sha, repo=repo)
            return
        raise self._http_error(response)

    def _update_ref(self, branch: str, sha: str, *, repo: str | None) -> None:
        """Force ``refs/heads/<branch>`` onto ``sha`` (``PATCH .../git/refs/heads/<branch>``).

        ``force: true`` because the release branch is molt's to own and is replaced on every run --
        which is already true in ``git-cli`` mode, where the same step is a ``git push --force``
        (``run.ts:146``). A non-forcing update is not expressible and nothing asks for one.

        A branch that vanished between the read and this update answers 422; it is reported naming
        the branch rather than silently recreated, because a ref disappearing mid-run means
        something else is writing to this repository.
        """
        response = self._send(
            "PATCH",
            f"{self._repo_url(repo)}/git/refs/heads/{branch}",
            json={"sha": sha, "force": True},
            headers=_REST_HEADERS,
        )
        if response.status_code != httpx.codes.OK:
            detail = self._http_error(response)
            raise MoltForgeError(f"GitHub would not move the branch {branch!r} to {sha}: {detail}")

    def _delete_ref(self, branch: str, *, repo: str | None) -> None:
        """Delete ``refs/heads/<branch>``. **Never raises** -- cleanup must not fail a release.

        This runs in the ``finally`` of :meth:`create_commit`, so it can be reached both after a
        commit that landed and after a mutation that failed. In the first case the release is
        already done and failing on a leftover ref would report a failure that did not happen; in
        the second, raising here would replace the real error with this one. Either way the next
        run force-updates the ref rather than creating it, so a survivor is harmless.
        """
        try:
            response = self._send(
                "DELETE", f"{self._repo_url(repo)}/git/refs/heads/{branch}", headers=_REST_HEADERS
            )
        except MoltForgeError as error:
            _LOGGER.warning("Could not delete the temporary branch %s: %s", branch, error)
            return
        if response.status_code not in (httpx.codes.NO_CONTENT, httpx.codes.OK):
            _LOGGER.warning(
                "Could not delete the temporary branch %s: HTTP %s",
                branch,
                response.status_code,
            )

    # -- resolution --------------------------------------------------------------------

    def _repo_url(self, repo: str | None) -> str:
        """``<rest base>/repos/<owner>/<name>`` for this call's repository.

        Every REST call is rooted here, so the slug is validated in exactly one place and can never
        be interpolated into a URL unvalidated.
        """
        owner, _, name = self._target_repo(repo).partition("/")
        return f"{self._rest_url}/repos/{owner}/{name}"

    def _target_repo(self, repo: str | None) -> str:
        """The repository this call is about: the per-call override, else the configured one.

        Validated **before** the caller can reach the network, which is the load-bearing half of
        the guard: a malformed slug is interpolated into a query, and validating after the request
        would have leaked the token to whatever the interpolation produced.
        """
        target = repo if repo is not None else self.repo
        # `is None` and not a truthiness test: an explicitly passed empty string is a *malformed
        # slug*, and the caller is owed the validator's ValueError naming the expected form, not a
        # "nothing is configured" message about a variable they did not use.
        if target is None:
            raise MoltForgeError(
                "No GitHub repository configured: set GITHUB_REPOSITORY to userOrOrg/repoName, "
                "or pass an explicit repo"
            )
        validate_repo_name(target)
        return target

    def _lookup(
        self, kind: Literal["commit", "pull"], repo: str, identifier: str
    ) -> CommitInfo | PullRequestInfo | None:
        """One cached lookup. The key is ``(repo, kind, id)`` (design D4).

        ``kind`` is a :data:`~typing.Literal` rather than a ``str`` because it selects the branch
        twice -- once for the query and once for the parser -- and a misspelling would otherwise
        fall through to the pull-request arm silently, returning a shape the caller then discards
        as ``None``.

        Each component earns its place. Without ``repo``, one repository's commit metadata is
        served for another's -- shas are repository-scoped, and the per-call override exists
        precisely to make that reachable. Without ``kind``, a commit lookup can be answered with
        pull-request metadata, because a short sha and a pull-request number can be the same
        string: ``"1613"`` is a plausible sha.

        The host is *not* in the key, though ``website/docs/forges/overview.md`` writes it as
        ``(host, kind, repository, id)``: the cache is scoped to the instance and the instance
        holds exactly one endpoint, so the host component is already constant per cache.
        """
        key = (repo, kind, identifier)
        if key in self._cache:
            return self._cache[key]
        owner, _, name = repo.partition("/")
        if kind == "commit":
            query = _COMMIT_QUERY.substitute(
                owner=owner, name=name, sha=identifier, first=ASSOCIATED_PULL_REQUESTS_LIMIT
            )
        else:
            query = _PULL_QUERY.substitute(owner=owner, name=name, number=identifier)
        node = self._node(self._request(query), f"{kind}__{identifier}")
        result: CommitInfo | PullRequestInfo | None
        if node is None:
            result = None
        elif kind == "commit":
            result = self._commit_info(node, identifier)
        else:
            result = self._pull_request_info(node, int(identifier))
        self._cache[key] = result
        return result

    @staticmethod
    def _node(data: Mapping[str, Any], alias: str) -> Mapping[str, Any] | None:
        """The aliased entity out of a ``data`` block, or ``None`` when it is not there.

        Three shapes mean the same thing and all three appear in the reference fixtures
        (``dataloader.ts:137``; ``get-commit-info.ts:28-29`` documents both as ``undefined``): the
        alias resolves to ``null`` (no such commit), the alias is absent, or the whole ``repo__0``
        block is missing (no such repository).
        """
        repository = data.get("repo__0")
        if not isinstance(repository, dict):
            return None
        node = repository.get(alias)
        return node if isinstance(node, dict) else None

    def _commit_info(self, node: Mapping[str, Any], sha: str) -> CommitInfo:
        """Build a :class:`CommitInfo` (``get-commit-info.ts:42-79``).

        ``sha`` is the value the **caller** passed, echoed onto the field verbatim
        (``get-commit-info.ts:61``) -- molt's callers pass whatever a changeset's ``commit`` field
        holds, which is a full 40-character sha whenever ``molt add`` wrote it from
        ``git rev-parse HEAD``. Only the rendered link slices.

        Attribution is ``pr?.author ?? data.author?.user`` (``:57``): the **pull-request author
        beats the commit author**, so a change is credited to the human who proposed it rather than
        to the maintainer who pressed merge.
        """
        associated = node.get("associatedPullRequests") or {}
        nodes = associated.get("nodes") if isinstance(associated, dict) else None
        selected = _select_pull_request(nodes if isinstance(nodes, list) else [])
        pull: PullRef | None = None
        author: AuthorRef | None = None
        if selected is not None:
            number, url = selected.get("number"), selected.get("url")
            if isinstance(number, int) and isinstance(url, str):
                pull = PullRef(number=number, url=url)
            author = _author_ref(selected.get("author"))
        if author is None:
            commit_author = node.get("author") or {}
            author = _author_ref(
                commit_author.get("user") if isinstance(commit_author, dict) else None
            )
        return CommitInfo(
            commit=CommitRef(sha=sha, url=str(node.get("commitUrl") or "")),
            pull=pull,
            author=author,
        )

    def _pull_request_info(self, node: Mapping[str, Any], number: int) -> PullRequestInfo:
        """Build a :class:`PullRequestInfo` (``get-pull-request-info.ts:39-59``).

        Note the asymmetry with the commit path, which is easy to get backwards: here the sha comes
        from the **response** (``mergeCommit.abbreviatedOid``, ``:54``), not from the caller. An
        unmerged pull request has ``mergeCommit: null`` and therefore no commit at all.
        """
        merge_commit = node.get("mergeCommit")
        commit: CommitRef | None = None
        if isinstance(merge_commit, dict):
            oid, url = merge_commit.get("abbreviatedOid"), merge_commit.get("commitUrl")
            if isinstance(oid, str) and isinstance(url, str):
                commit = CommitRef(sha=oid, url=url)
        return PullRequestInfo(
            pull=PullRef(number=number, url=str(node.get("url") or "")),
            author=_author_ref(node.get("author")),
            commit=commit,
        )

    # -- transport ---------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """The headers every attempt carries, retries included (``dataloader.ts:102-108``).

        ``Token``, not ``Bearer``: that is the scheme upstream sends and the one the conformance
        suite pins. The token is required here rather than in ``__init__`` so that reading a forge's
        configuration never fails, while **no request is ever sent without it** (design D7).
        """
        if not self._token:
            raise MoltForgeError(
                "No GitHub token: set GITHUB_TOKEN to a token with read access to the repository. "
                "molt does not fall back to an anonymous request, which GitHub limits to 60 calls "
                "an hour and then answers with errors that look like a missing repository"
            )
        return {
            "Authorization": f"Token {self._token}",
            "Accept": "application/json",
        }

    def _send(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Send one request, retrying transient failures within a bounded budget.

        The **host-neutral half** of the transport (design D3): it knows about statuses, naps and
        the token, and nothing about GraphQL or REST bodies. Both callers -- :meth:`_request` for
        the attribution queries and :meth:`create_release` for release publication -- reuse it, so
        a second transport cannot silently ship without resilience, which is exactly the defect
        this capability exists to fix upstream (research README section 3.4).

        Any status the loop will **not** retry is returned to the caller, including a failing one:
        success is transport-specific (200 for GraphQL, 201 for a created release) and so is
        failure (a 422 is a duplicate release, not an error). What this method raises is what the
        caller cannot interpret anyway: a transport failure, or a transient status that outlived
        the budget.

        The loop is deliberately explicit about where it stops: it never sleeps after the final
        attempt, and it stops early rather than exceeding the total backoff budget, so the number
        of naps is always one fewer than the number of attempts.

        ``json`` defaults to ``None`` and ``params`` exists because the pull-request lookup is a
        ``GET`` with a query string and no body -- the first read this backend does over REST. A
        second, bodyless transport helper would have been a second place for the retry budget to
        drift out of.
        """
        request_headers = {**self._headers(), **(headers or {})}
        slept = 0.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            last_attempt = attempt == MAX_ATTEMPTS
            try:
                response = self._issue(method, url, json, params, request_headers)
            except httpx.TransportError as exc:
                if last_attempt:
                    raise MoltForgeError(f"Could not reach the GitHub API at {url}: {exc}") from exc
                delay = backoff_delay(attempt)
            else:
                transient = is_transient_status(response.status_code) or (
                    response.status_code == httpx.codes.FORBIDDEN
                    and _is_rate_limited_forbidden(response)
                )
                if not transient:
                    return response
                if last_attempt:
                    raise self._http_error(response)
                delay = backoff_delay(
                    attempt, retry_after=retry_after_seconds(response.headers.get("Retry-After"))
                )
                if slept + delay > MAX_TOTAL_BACKOFF_SECONDS:
                    raise self._http_error(response)
            sleep_for(delay)
            slept += delay
        # Unreachable: the final attempt either returns or raises. Kept as an assertion rather
        # than a fall-through, because a future edit to the loop that removes one of those exits
        # would otherwise return `None` into a caller annotated to receive a response.
        raise MoltForgeError("The GitHub request loop ended without a result")

    def _request(
        self, query: str, *, variables: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        """POST ``query`` through :meth:`_send` and return its ``data`` block.

        The **GraphQL half** of the transport: this is where a 200 stops being success on its own
        (design D6) and where anything that is not a 200 becomes an error. Everything about naps,
        statuses and headers lives one level down.

        ``variables`` is omitted from the body entirely when it is ``None``, so the two attribution
        queries -- which use none -- still send exactly ``{"query": ...}``, the shape
        ``dataloader.ts:107`` sends and ``tests/forge/test_github.py`` pins. The commit mutation is
        the one caller that supplies them, and it supplies **everything** that way rather than
        interpolating file contents into query text.
        """
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = dict(variables)
        response = self._send("POST", self.api_url, json=payload)
        if response.status_code != httpx.codes.OK:
            raise self._http_error(response)
        return self._read_data(response)

    def _issue(
        self,
        method: str,
        url: str,
        json: Any,
        params: Mapping[str, str] | None,
        headers: Mapping[str, str],
    ) -> httpx.Response:
        """One request, with a client per request.

        Closed by the context manager: there is no ``close()`` on the forge for a caller to
        forget, and the cache means a release makes a handful of these rather than one per
        changelog line. ``json=`` is what sets ``Content-Type: application/json`` -- and upstream
        sends a *string* body on the GraphQL path, which ``fetch`` types as ``text/plain``. A
        ``None`` body sends no content at all, which is what a ``GET`` needs.
        """
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            return client.request(
                method,
                url,
                json=json,
                params=dict(params) if params is not None else None,
                headers=dict(headers),
            )

    @staticmethod
    def _json_body(response: httpx.Response) -> Any:
        """The response's parsed JSON, or a :class:`~molt.errors.MoltForgeError` naming the body.

        Shared by every REST reader. A body that is not JSON means the response did not come from
        the API -- a proxy error page, a captive portal -- and the raw text is echoed (truncated)
        because that is the only clue the operator gets.
        """
        try:
            return response.json()
        except ValueError as exc:
            raise MoltForgeError(
                f"The GitHub API returned a {response.status_code} that is not JSON: "
                f"{response.text[:200]!r}"
            ) from exc

    @staticmethod
    def _read_data(response: httpx.Response) -> Mapping[str, Any]:
        """The ``data`` block of a 200 -- after proving the 200 is not a failure (design D6).

        Two distinct failures live in here, and conflating them is the silent bug:
        ``{"data": {...}}`` with a null alias means "no such entity" and is the caller's ``None``,
        while an ``errors`` array or a missing ``data`` key means the **call** failed
        (``dataloader.ts:120-131``: "this is mainly for the case where there's an authentication
        problem"). Reporting the second as the first drops attribution out of a published
        changelog with no error anywhere.
        """
        try:
            body = response.json()
        except ValueError as exc:
            raise MoltForgeError(
                f"The GitHub API returned a {response.status_code} that is not JSON: "
                f"{response.text[:200]!r}"
            ) from exc
        if not isinstance(body, dict):
            raise MoltForgeError(f"The GitHub API returned an unexpected payload: {body!r}")
        errors = body.get("errors")
        if errors:
            raise MoltForgeError(f"The GitHub API reported errors: {_error_summary(errors)}")
        data = body.get("data")
        if not isinstance(data, dict):
            message = body.get("message")
            detail = f": {message}" if isinstance(message, str) else ""
            raise MoltForgeError(
                f"The GitHub API returned no data block{detail}. This usually means the request "
                "was not authenticated -- check that GITHUB_TOKEN is set and has read access"
            )
        return data

    def _http_error(self, response: httpx.Response) -> MoltForgeError:
        """The error a non-200 becomes, naming the status and, when GitHub says so, the rate limit.

        The rate-limit branch is what research doc 04 section 5.5 records as missing upstream: with
        no ``x-ratelimit-*`` inspection an exhausted budget surfaces as "Fetched data from GitHub
        has missing data" (``dataloader.ts:128``), which describes the symptom of a completely
        different bug. ``website/docs/forges/github.md`` promises "an actionable error (with the
        reset time)", so the reset timestamp is rendered rather than echoed as an epoch.
        """
        status = response.status_code
        detail = _response_detail(response)
        if response.headers.get("x-ratelimit-remaining") == "0":
            reset = _reset_time(response.headers.get("x-ratelimit-reset"))
            window = f" It resets at {reset}." if reset else ""
            return MoltForgeError(
                f"The GitHub API rate limit is exhausted (HTTP {status}).{window} Wait for the "
                f"reset, or use a token with a larger budget.{detail}"
            )
        return MoltForgeError(f"The GitHub API request failed with HTTP {status}.{detail}")


def _error_summary(errors: object) -> str:
    """Join the ``message`` fields of a GraphQL ``errors`` array into one sentence.

    The messages are carried through verbatim -- ``"Bad credentials"`` is GitHub telling the
    operator exactly what to fix, and paraphrasing it would lose that.
    """
    if not isinstance(errors, list):
        return str(errors)
    messages = [
        str(entry.get("message"))
        if isinstance(entry, dict) and entry.get("message")
        else str(entry)
        for entry in errors
    ]
    return "; ".join(messages)


def _response_detail(response: httpx.Response) -> str:
    """GitHub's own explanation for a failed response, when it gave one.

    Truncated: an HTML error page from a proxy in front of an Enterprise instance would otherwise
    put a kilobyte of markup in a CLI error message.
    """
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return f" {text[:200]}" if text else ""
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return f" {body['message']}"
    return ""


def _reset_time(value: str | None) -> str | None:
    """Render an ``x-ratelimit-reset`` epoch as a UTC timestamp, or ``None`` if it is unusable."""
    if not value:
        return None
    try:
        moment = datetime.fromtimestamp(int(value), tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")
