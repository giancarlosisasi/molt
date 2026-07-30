"""The GitHub forge backend: hand-written GraphQL over ``httpx``, with a cache that hits.

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

import os
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Any, Literal

import httpx

from molt.errors import MoltForgeError
from molt.forge.protocol import (
    AuthorRef,
    CommitInfo,
    CommitRef,
    PullRef,
    PullRequestInfo,
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
    "REQUEST_TIMEOUT_SECONDS",
    "GitHubForge",
]

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

#: The variables ``env.ts:5-34`` reads, in the order that page documents them.
_ENVIRONMENT_VARIABLES = (
    "GITHUB_TOKEN",
    "GITHUB_REPOSITORY",
    "GITHUB_SERVER_URL",
    "GITHUB_GRAPHQL_URL",
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
    """The four GitHub variables, with the real environment winning over ``.env``.

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

    # -- resolution --------------------------------------------------------------------

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

    def _request(self, query: str) -> Mapping[str, Any]:
        """POST ``query``, retrying transient failures within a bounded budget, and return ``data``.

        The loop is deliberately explicit about where it stops: it never sleeps after the final
        attempt, and it stops early rather than exceeding the total backoff budget, so the number
        of naps is always one fewer than the number of attempts.
        """
        headers = self._headers()
        slept = 0.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            last_attempt = attempt == MAX_ATTEMPTS
            try:
                response = self._post(query, headers)
            except httpx.TransportError as exc:
                if last_attempt:
                    raise MoltForgeError(
                        f"Could not reach the GitHub API at {self.api_url}: {exc}"
                    ) from exc
                delay = backoff_delay(attempt)
            else:
                if response.status_code == httpx.codes.OK:
                    return self._read_data(response)
                if not is_transient_status(response.status_code) or last_attempt:
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
        # would otherwise return `None` into a caller annotated to receive a mapping.
        raise MoltForgeError("The GitHub request loop ended without a result")

    def _post(self, query: str, headers: Mapping[str, str]) -> httpx.Response:
        """One POST of ``{"query": ...}``.

        A client per request, closed by the context manager: there is no ``close()`` on the forge
        for a caller to forget, and the cache means a release makes a handful of these rather than
        one per changelog line. ``json=`` is what sets ``Content-Type: application/json`` -- and
        upstream sends a *string* body, which ``fetch`` types as ``text/plain``.
        """
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            return client.post(self.api_url, json={"query": query}, headers=dict(headers))

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
