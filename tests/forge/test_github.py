r"""Conformance tests for molt's GitHub forge backend (``molt.forge``).

Source rows
-----------
``roadmap/research/test-suite/08-infra-forge-utils.md``, the two ``get-github-info`` sections:
``packages/get-github-info/src/get-commit-info.test.ts`` (6 rows) and
``get-pull-request-info.test.ts`` (3 rows) -- 9 rows, all marked "Adapt". Upstream drives them
through ``nock``; here the transport is stubbed with ``respx`` over ``httpx`` (the shared
``forge_api`` fixture, ``tests/conftest.py``), so the query molt actually sends and the header it
actually attaches are themselves under test.

Upstream mock data is reproduced verbatim -- commit ``a085003`` / repo ``emotion-js/emotion`` /
PR ``#1613`` / author ``Andarist`` (``get-commit-info.test.ts:29-188``) and commit ``c7e9c69`` /
repo ``JedWatson/react-select`` / PR ``#3682`` / author ``lmvco`` (``:332-436``) -- so every
expectation here can be diffed against the reference suite line for line.

What molt changes, and why these tests must FAIL against upstream
-----------------------------------------------------------------
Research README section 3.4 lists ``get-github-info``'s client as carrying two bugs molt refuses
to port. Both are pinned here as *corrected*:

1. **A cache that never hits.** ``dataloader.ts:62-72`` builds a fresh ``{...options, kind}``
   object per call and configures no ``cacheKeyFn``, so DataLoader's map is keyed by object
   identity and every lookup misses. molt keys by ``(kind, repo, id)``
   (``website/docs/forges/github.md``, "Caching and resilience";
   ``website/docs/forges/overview.md``, "Caching and backoff, done right"). The cache rows below
   deliberately build **equal but non-identical** argument strings via :func:`fresh_copy`, and
   assert ``is not`` on them, so an identity-keyed cache cannot make the test vacuously green.
2. **Zero retry, zero rate-limit handling.** ``dataloader.ts:100-131`` has no ``Retry-After``
   inspection, no backoff, no 5xx handling and no timeout; a throttled request surfaces as a
   confusing "missing data" error. molt retries transient 5xx / 429 with bounded, jittered
   backoff and honors ``Retry-After``.

Forge-agnostic by construction
------------------------------
Research README section 5 item 7 (changesets issue #879: 35 reactions, **zero maintainer comments
in four years**, and the only open issue on the dead pychangeset) makes the forge a seam rather
than a hard-wired GitHub client. ``website/docs/forges/gitlab-gitea-others.md`` states the
contract a second backend implements. The protocol-conformance row below pins that surface and
asserts no GitHub-only vocabulary leaks into it, so a GitLab/Gitea backend is additive.

TDD targets declared by this file (they do not exist yet)
---------------------------------------------------------
``molt.forge.Forge`` -- a ``@runtime_checkable`` ``Protocol``::

    name: str            # backend id, e.g. "github"
    api_url: str         # the endpoint the backend POSTs to -- CONFIGURATION, not a constant
    server_url: str      # the human-facing host, for profile / issue URLs
    repo: str | None     # the default "owner/name" slug

    def validate_repo(self, repo: str) -> None: ...
    def commit_info(self, commit: str, *, repo: str | None = None) -> CommitInfo | None: ...
    def pull_request_info(self, pull: int, *, repo: str | None = None) -> PullRequestInfo | None:

``molt.forge.GitHubForge()`` -- satisfies it, configured entirely from the environment
(``GITHUB_TOKEN``, ``GITHUB_REPOSITORY``, ``GITHUB_SERVER_URL``, ``GITHUB_GRAPHQL_URL``) exactly
as ``website/docs/forges/github.md`` documents, which in turn ports ``env.ts:22-34``.

Result shape (``get-commit-info.ts:9-25``, ``get-pull-request-info.ts:9-25``), respelled in
snake_case per this repo's style: ``info.commit.sha / .url / .markdown_link``,
``info.author.login / .url / .markdown_link``, ``info.pull.number / .url / .markdown_link``.
``CommitInfo.commit`` and ``PullRequestInfo.pull`` are always present; the other two are
``None``-able.

Adaptation decisions flagged for the owner
------------------------------------------
- ``markdownLink`` -> ``markdown_link`` and attribute (not mapping) access. ``website/docs/
  extending/custom-generators.md:62-64`` sketches a *different* shape -- ``info.pull_request``
  plus a ``forge.pull_request_url(...)`` helper. That doc example and the 9 upstream rows cannot
  both be right; the rows (and the brief) win here, and the doc example is reported as needing a
  fix.
- ``repo`` is a keyword-only, optional per-call override on top of the forge's configured slug.
  Required by the committed ``tests/changelog/test_changelog.py::
  test_the_cache_key_includes_the_repository``, which drives ONE forge at two repositories;
  optional because ``custom-generators.md:62`` calls ``forge.commit_info(changeset.commit)`` with
  no repo at all.
- The synchronous signature is forced by the committed changelog contract (4-param, synchronous
  ``get_release_line(changeset, bump, options, forge)``); the forge it receives must be usable
  synchronously.
- The GraphQL query is pinned with an **exact inline constant** rather than a ``syrupy``
  snapshot. For a module that does not exist yet a snapshot is strictly weaker: the first
  ``--snapshot-update`` run after ``molt.forge`` lands would bless whatever the implementation
  happens to emit. An inline constant copied from ``get-commit-info.test.ts:153-187`` cannot be
  auto-blessed. Discriminating ``in`` assertions still run first so the failure output names the
  part that is wrong.
"""

from __future__ import annotations

import inspect
import json
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from tests.conftest import ForgeAPI

pytest.importorskip("molt.forge", reason="build step 9 - molt.forge is a TDD target")

# httpx and respx are imported BELOW the guard on purpose, mirroring
# tests/changelog/test_changelog.py:108-114: tests/conftest.py:77-86 imports the httpx stack
# lazily so a not-yet-synced environment gets a readable RuntimeError from the fixture instead of
# a collection-time ImportError. Importing them at the top would defeat that.
import httpx
import respx

# pyrefly: ignore[missing-import]  -- molt.forge is the TDD target of build step 9.
from molt.forge import Forge, GitHubForge

# ======================================================================================
# Upstream mock data, verbatim
# ======================================================================================

# get-commit-info.test.ts:29-188 -- the emotion fixture.
REPO = "emotion-js/emotion"
COMMIT_SHA = "a085003"
COMMIT_URL = "https://github.com/emotion-js/emotion/commit/a085003d4c8ca284c116668d7217fb747802ed85"
PULL_NUMBER = 1613
AUTHOR = "Andarist"

# get-commit-info.test.ts:332-436 -- the react-select fixture, where the PR author and the commit
# author DIFFER. That divergence is the whole point of the author-precedence row.
RS_REPO = "JedWatson/react-select"
RS_SHA = "c7e9c69"
RS_COMMIT_URL = (
    "https://github.com/JedWatson/react-select/commit/c7e9c697dada15ce3ff9a767bf914ad890080433"
)
RS_PULL_NUMBER = 3682
RS_PR_AUTHOR = "lmvco"
RS_COMMIT_AUTHOR = "JedWatson"

SERVER_URL = "https://github.com"

# The documented default endpoint (env.ts:23-26; website/docs/forges/github.md "Configuration").
DEFAULT_GRAPHQL_URL = "https://api.github.com/graphql"
# A non-routable stand-in used whenever the endpoint itself is not what is under test. `.invalid`
# is reserved by RFC 2606, so a bug that escaped respx would fail to resolve rather than reach a
# real host. Same convention as tests/changelog/test_changelog.py:144.
GRAPHQL_URL = "https://api.github.invalid/graphql"
GRAPHQL_URL_RE = r".+/graphql/?$"
TOKEN = "ghp-molt-conformance-token"

# The upstream repo-name validator (utils.ts:1-9). JavaScript's `\w` is ASCII-only; Python's is
# Unicode-aware, so a faithful port must scope the class (re.ASCII or an explicit class).
VALID_REPO_PATTERN = r"^[\w.-]+/[\w.-]+$"

# get-commit-info.test.ts:153-187 -- the exact query upstream inline-snapshots. See the module
# docstring for why this is an inline constant and not a syrupy snapshot.
EXPECTED_COMMIT_QUERY = """\
query {
  repo__0: repository(
    owner: "emotion-js",
    name: "emotion"
  ) {
    commit__a085003: object(expression: "a085003") {
      ... on Commit {
        ...CommitFragment
      }
    }
  }
}
fragment CommitFragment on Commit {
  commitUrl
  associatedPullRequests(first: 50) {
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
"""

# get-pull-request-info.test.ts:103-126.
EXPECTED_PULL_QUERY = """\
query {
  repo__0: repository(
    owner: "emotion-js",
    name: "emotion"
  ) {
    pull__1613: pullRequest(number: 1613) {
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
"""


# ======================================================================================
# Helpers (never named test_* -- `python_functions = "test"` is a PREFIX match)
# ======================================================================================


@pytest.fixture(autouse=True)
def isolated_forge_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Strip every ambient GitHub variable and run from an empty directory.

    ``env.ts:5-20`` (and ``website/docs/forges/github.md``, "For local runs, molt also reads
    these from a ``.env`` file if present") makes the forge read a ``.env`` from the current
    working directory. Without the ``chdir`` a developer's own ``.env`` -- or a real
    ``GITHUB_TOKEN`` exported in the shell, which CI always has -- would silently change what
    these tests exercise, and the missing-token row would pass for the wrong reason.
    """
    for var in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_SERVER_URL", "GITHUB_GRAPHQL_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


def build_forge(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repo: str | None = REPO,
    token: str | None = TOKEN,
    server_url: str = SERVER_URL,
    graphql_url: str | None = GRAPHQL_URL,
) -> Any:
    """Construct a ``GitHubForge`` from the environment (``env.ts:22-34``).

    Every knob is an environment variable because that is the documented contract
    (``website/docs/forges/github.md``, "Configuration"). ``None`` means "leave the variable
    unset", which is how the default-endpoint and missing-token rows are expressed.
    """
    if token is not None:
        monkeypatch.setenv("GITHUB_TOKEN", token)
    if repo is not None:
        monkeypatch.setenv("GITHUB_REPOSITORY", repo)
    monkeypatch.setenv("GITHUB_SERVER_URL", server_url)
    if graphql_url is not None:
        monkeypatch.setenv("GITHUB_GRAPHQL_URL", graphql_url)
    return GitHubForge()


@pytest.fixture
def forge(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The common case: a forge for ``emotion-js/emotion`` pointed at the stubbed endpoint."""
    return build_forge(monkeypatch)


def fresh_copy(text: str) -> str:
    """Return a string equal to ``text`` but guaranteed to be a **different object**.

    This is the anti-vacuity device for the cache rows. ``str`` literals are interned, and
    ``str(s)``, ``s[:]`` and ``f"{s}"`` all hand back the *same* object in CPython, so a naive
    "call it twice with the same sha" test would pass even against upstream's identity-keyed
    DataLoader (``dataloader.ts:62-72``) -- the exact bug being fixed. ``"".join(list(s))``
    materializes a new buffer for any string of length > 1, and every caller asserts ``is not``
    so this can never silently degrade back into an identity comparison.
    """
    return "".join(list(text))


def pr_node(
    number: int,
    merged_at: str | None,
    login: str,
    *,
    repo: str = REPO,
    host: str = SERVER_URL,
) -> dict[str, Any]:
    """One ``associatedPullRequests.nodes`` entry (``dataloader.ts:29-39``)."""
    return {
        "number": number,
        "url": f"{host}/{repo}/pull/{number}",
        "mergedAt": merged_at,
        "author": {"login": login, "url": f"{host}/{login}"},
    }


def commit_node(
    *,
    commit_url: str = COMMIT_URL,
    nodes: Sequence[dict[str, Any]] = (),
    author_login: str | None = AUTHOR,
    host: str = SERVER_URL,
) -> dict[str, Any]:
    """A ``CommitFragment`` payload (``dataloader.ts:27-46``)."""
    user = (
        None if author_login is None else {"login": author_login, "url": f"{host}/{author_login}"}
    )
    return {
        "commitUrl": commit_url,
        "associatedPullRequests": {"nodes": list(nodes)},
        "author": {"user": user},
    }


def pull_node(
    *,
    number: int = PULL_NUMBER,
    repo: str = REPO,
    author_login: str | None = AUTHOR,
    merge_commit_sha: str | None = COMMIT_SHA,
    merge_commit_url: str = COMMIT_URL,
    host: str = SERVER_URL,
) -> dict[str, Any]:
    """A ``PullFragment`` payload (``dataloader.ts:49-59``)."""
    author = (
        None if author_login is None else {"login": author_login, "url": f"{host}/{author_login}"}
    )
    merge_commit = (
        None
        if merge_commit_sha is None
        else {"commitUrl": merge_commit_url, "abbreviatedOid": merge_commit_sha}
    )
    return {
        "url": f"{host}/{repo}/pull/{number}",
        "author": author,
        "mergeCommit": merge_commit,
    }


def commit_payload(node: dict[str, Any] | None, *, sha: str = COMMIT_SHA) -> dict[str, Any]:
    """``{data: {repo__0: {commit__<sha>: ...}}}`` (``dataloader.ts:133-138``)."""
    return {"data": {"repo__0": {f"commit__{sha}": node}}}


def pull_payload(node: dict[str, Any] | None, *, number: int = PULL_NUMBER) -> dict[str, Any]:
    """``{data: {repo__0: {pull__<n>: ...}}}`` (``dataloader.ts:133-138``)."""
    return {"data": {"repo__0": {f"pull__{number}": node}}}


def sent_queries(forge_api: ForgeAPI) -> list[str]:
    return [json.loads(request.content).get("query", "") for request in forge_api.requests]


def assert_every_request_authorized(forge_api: ForgeAPI, token: str = TOKEN) -> None:
    """``dataloader.ts:102-108`` -- ``Authorization: Token <GITHUB_TOKEN>`` on EVERY request.

    Upstream's harness gates the whole ``nock`` interceptor on the header, so a request without
    it simply does not match and the test fails as "no mock". Asserting it explicitly, on every
    intercepted request rather than only the last, is the molt equivalent -- a retry that drops
    the header on the second attempt would otherwise go unnoticed.

    The scheme is compared case-insensitively (RFC 7235 makes auth schemes case-insensitive, so
    ``Token`` vs ``token`` is not a behavior) but ``bearer`` is rejected explicitly and the token
    value is compared exactly, so every way this can actually be wrong still fails.
    """
    assert forge_api.requests, "expected at least one request to assert the header on"
    for index, request in enumerate(forge_api.requests):
        header = request.headers.get("Authorization") or ""
        scheme, _, value = header.partition(" ")
        assert scheme.lower() == "token", f"request {index}: expected token scheme, got {header!r}"
        assert value == token, f"request {index}: the GITHUB_TOKEN must reach the wire unmodified"


def assert_actionable(exc: BaseException, *needles: str) -> None:
    """The raised error must be a deliberate, informative one -- not a crash.

    ``AttributeError`` / ``TypeError`` / ``NameError`` / ``IndexError`` from a half-written
    parser would satisfy a bare ``pytest.raises(Exception)`` and make the "is it retried?" rows
    pass for the wrong reason, so they are excluded by name.
    """
    assert not isinstance(exc, (AttributeError, TypeError, NameError, IndexError)), (
        f"expected a deliberate forge error, got a programming error: {exc!r}"
    )
    text = str(exc)
    for needle in needles:
        assert needle.lower() in text.lower(), f"expected {needle!r} in the error message: {text!r}"


@contextmanager
def scripted_forge(
    monkeypatch: pytest.MonkeyPatch,
    responses: Sequence[httpx.Response] | Callable[[httpx.Request], httpx.Response],
    **forge_kwargs: Any,
) -> Iterator[tuple[Any, Any, list[float]]]:
    """A forge over a **scripted sequence** of responses, plus the recorded backoff naps.

    GAP REPORTED TO THE ORCHESTRATOR: the shared ``forge_api`` fixture
    (``tests/conftest.py:483-529``) serves one fixed response for every request, so it cannot
    express "fail, then succeed" -- which is the entire retry contract. This local helper drives
    respx directly instead. ``tests/conftest.py`` is not mine to edit; ``ForgeAPI`` wants a
    ``set_responses(sequence)`` companion to ``set_response``.

    ``time.sleep`` is patched by *name* rather than by import, matching the seam the committed
    ``tests/changelog/test_changelog.py:1277`` already pins, and the arguments are recorded so
    the backoff bounds can be asserted instead of merely tolerated.
    """
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(float(seconds)))
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__regex=GRAPHQL_URL_RE).mock(
            side_effect=list(responses) if isinstance(responses, (list, tuple)) else responses
        )
        yield build_forge(monkeypatch, **forge_kwargs), route, sleeps


# ======================================================================================
# Rows: get-commit-info #1 and get-pull-request-info #1 -- repo-name validation
# ======================================================================================

INVALID_REPOS = [
    (
        "https://github.com/JedWatson/react-select",
        "get-commit-info.test.ts:18-27 / get-pull-request-info.test.ts:18-27 -- the literal "
        "upstream case: a full URL has extra slashes and a colon",
    ),
    ("emotion-js", "no slash at all -- the shape the message names"),
    ("emotion-js/emotion/extra", "three segments: two slashes fail the anchored pattern"),
    ("/emotion", "empty owner -- '+' requires at least one character"),
    ("emotion-js/", "empty name"),
    ("emotion js/emotion", "a space is outside [\\w.-]"),
    ("emotion-js/emo tion", "a space in the name segment"),
    ("git@github.com:emotion-js/emotion.git", "an scp-style remote is not a slug"),
    ("", "the empty string"),
    (
        "emotion-js/emotion\n",
        "a trailing newline: Python's $ also matches before a final \\n, so "
        "an unanchored port using $ instead of \\Z would wrongly accept this",
    ),
    ("emotion-js/emotion\nowner/other", "an embedded newline -- the same $ hazard, exploitable"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("bad_repo", "why"), INVALID_REPOS)
@pytest.mark.parametrize("entry", ["validate_repo", "commit_info", "pull_request_info"])
def test_an_invalid_repo_is_rejected_before_any_network_call(
    entry: str, bad_repo: str, why: str, forge: Any, forge_api: ForgeAPI
) -> None:
    """get-commit-info row 1 + get-pull-request-info row 1 (``utils.ts:1-9``).

    Both entry points share one validator upstream (``get-commit-info.ts:34`` and
    ``get-pull-request-info.ts:34`` each call ``validateRepoName`` first), so the table is run
    against ``validate_repo`` directly *and* through both public lookups -- an implementation
    that validated in only one of them would otherwise slip through.

    ``forge_api.requests == []`` is the load-bearing half: the guard must fire before the socket
    is opened, not after a wasted round-trip that leaks a token to whatever host a malformed
    slug interpolated into the URL.

    The two newline rows are a Python-specific hazard with no upstream analogue: JavaScript's
    ``$`` (without ``m``) matches only at the very end, while Python's ``$`` also matches just
    before a trailing newline. A port that spells the anchor ``$`` rather than ``\\Z`` accepts
    ``"owner/name\\n"`` -- and, with ``re.MULTILINE`` anywhere in the picture, an injected second
    line as well.
    """
    with pytest.raises(ValueError) as excinfo:
        if entry == "validate_repo":
            forge.validate_repo(bad_repo)
        elif entry == "commit_info":
            forge.commit_info(COMMIT_SHA, repo=bad_repo)
        else:
            forge.pull_request_info(PULL_NUMBER, repo=bad_repo)

    message = str(excinfo.value)
    assert "userOrOrg/repoName" in message, why
    assert repr(bad_repo).strip("'\"") in message or bad_repo in message, (
        "the rejected value must be echoed back so the error is actionable"
    )
    assert forge_api.requests == [], "validation must precede the network call"


VALID_REPOS = [
    ("emotion-js/emotion", "the plain case"),
    ("JedWatson/react-select", "a dash in the name"),
    ("a/b", "single characters -- '+' needs one, not two"),
    ("owner_name/repo_name", "underscores are inside \\w"),
    ("owner.name/repo.name", "dots are explicit members of the class"),
    ("0/9", "digits are inside \\w"),
    ("Owner-Name/Repo.Name_2", "the full class, mixed"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("good_repo", "why"), VALID_REPOS)
def test_a_well_formed_repo_slug_is_accepted(good_repo: str, why: str, forge: Any) -> None:
    """``utils.ts:1`` -- ``/^[\\w.-]+\\/[\\w.-]+$/``.

    The complement of the rejection table. Without it, ``validate_repo`` could reject everything
    and the rows above would all still pass.
    """
    forge.validate_repo(good_repo)
    assert re.match(VALID_REPO_PATTERN, good_repo, re.ASCII) is not None, why


@pytest.mark.unit
def test_repo_validation_is_ascii_scoped(forge: Any) -> None:
    """PORT HAZARD, no upstream row: ``\\w`` means different things in JS and Python.

    ``utils.ts:1`` is ``/^[\\w.-]+\\/[\\w.-]+$/`` -- in JavaScript ``\\w`` is exactly
    ``[A-Za-z0-9_]``. Python's ``\\w`` is Unicode-aware by default, so the same pattern
    transcribed literally accepts ``"caf\\u00e9/repo"``, which upstream rejects and which GitHub
    itself cannot name (logins are ASCII, repository names are ``[A-Za-z0-9._-]``). A faithful
    port needs ``re.ASCII`` or an explicit character class.

    ADAPTATION DECISION flagged for the owner: this is stricter than a literal transcription and
    is the behavior a forge-agnostic backend wants, since a homoglyph slug is a phishing shape.

    The payloads are spelled as escapes, not literal characters, because this repo's test files
    are ASCII-only -- a non-ASCII byte corrupts pytest's failure output on a cp1252 console.
    """
    non_ascii_repos = [
        "caf" + chr(0x00E9) + "/repo",  # LATIN SMALL LETTER E WITH ACUTE
        "owner/re" + chr(0x0441) + "ipe",  # CYRILLIC SMALL LETTER ES, homoglyph of ASCII 'c'
        chr(0x0430) + "/b",  # CYRILLIC SMALL LETTER A, homoglyph of ASCII 'a'
        "owner/repo" + chr(0x00B2),  # SUPERSCRIPT TWO, also alphanumeric to Python
    ]
    for bad in non_ascii_repos:
        assert re.match(VALID_REPO_PATTERN, bad) is not None, (
            "sanity: a literal transcription of the JS pattern DOES accept this, which is "
            "exactly the hazard -- if this ever stops holding the row below is vacuous"
        )
    for bad in non_ascii_repos:
        with pytest.raises(ValueError):
            forge.validate_repo(bad)


@pytest.mark.property
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    owner=st.from_regex(r"\A[A-Za-z0-9_.-]{1,20}\Z"),
    name=st.from_regex(r"\A[A-Za-z0-9_.-]{1,20}\Z"),
)
def test_any_two_class_segments_joined_by_one_slash_are_accepted(
    owner: str, name: str, forge: Any
) -> None:
    """Invariant over ``utils.ts:1``: the pattern is exactly "class, slash, class".

    The ``forge`` fixture is function-scoped and therefore reused across hypothesis examples;
    that is safe here (and the health check is suppressed for it) because ``validate_repo`` is
    pure -- it touches no network, no cache and no instance state.
    """
    forge.validate_repo(f"{owner}/{name}")


@pytest.mark.property
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(text=st.text(max_size=40).filter(lambda s: "/" not in s))
def test_a_string_without_a_slash_is_never_a_repo(text: str, forge: Any) -> None:
    """Invariant: the slash is mandatory, so no slash-free string can ever validate."""
    with pytest.raises(ValueError):
        forge.validate_repo(text)


# ======================================================================================
# Row get-commit-info #2 -- missing data
# ======================================================================================

MISSING_COMMIT_PAYLOADS = [
    (
        {"data": {"repo__0": {"commit__a085003": None}}},
        "get-commit-info.test.ts:29-46 -- the literal upstream payload: the alias resolves to "
        "null because the commit does not exist",
    ),
    (
        {"data": {"repo__0": {}}},
        "dataloader.ts:137 -- the alias is absent entirely; `?.[dataKey]` yields undefined",
    ),
    (
        {"data": {}},
        "dataloader.ts:137 -- the whole repo block is missing, i.e. the repository does not "
        "exist. get-commit-info.ts:28-29 documents both as `undefined`",
    ),
]


@pytest.mark.network
@pytest.mark.parametrize(("payload", "why"), MISSING_COMMIT_PAYLOADS)
def test_a_missing_commit_resolves_to_none(
    payload: dict[str, Any], why: str, forge: Any, forge_api: ForgeAPI
) -> None:
    """Row 2 (``get-commit-info.test.ts:29-46``; ``get-commit-info.ts:40``).

    ``undefined`` in TypeScript becomes ``None`` here. Absence must not be an exception: the
    changelog generator's documented fallback (a bare ``[`sha7`]`` code span with no link) is
    built on this returning a value.
    """
    forge_api.set_response(payload)

    assert forge.commit_info(COMMIT_SHA) is None, why
    assert len(forge_api.requests) == 1
    assert_every_request_authorized(forge_api)


# ======================================================================================
# Row get-commit-info #3 -- multiple PRs, one merged (+ the exact GraphQL query)
# ======================================================================================

# get-commit-info.test.ts:69-115, verbatim: five associated PRs, exactly one of them merged.
ONE_MERGED_NODES = [
    pr_node(973, None, "mitchellhamilton"),
    pr_node(1600, None, "mitchellhamilton"),
    pr_node(PULL_NUMBER, "2019-11-07T06:43:58Z", AUTHOR),
    pr_node(1628, None, AUTHOR),
    pr_node(1630, None, AUTHOR),
]

# get-commit-info.test.ts:211-257: #1600 merged LATER than #1613, and #1613 is not first in the
# list -- so neither "first node" nor "last merged" can pass by accident.
TWO_MERGED_NODES = [
    pr_node(973, None, "mitchellhamilton"),
    pr_node(1600, "2019-11-20T06:43:58Z", "mitchellhamilton"),
    pr_node(PULL_NUMBER, "2019-11-07T06:43:58Z", AUTHOR),
    pr_node(1628, None, AUTHOR),
    pr_node(1630, None, AUTHOR),
]


@pytest.mark.network
def test_a_commit_resolves_to_its_single_merged_pull_request(
    forge: Any, forge_api: ForgeAPI
) -> None:
    """Row 3 (``get-commit-info.test.ts:48-188``; ``get-commit-info.ts:42-79``).

    Four separate contracts in one row, exactly as upstream snapshots them:

    1. the merged PR (#1613) wins over four unmerged ones -- ``mergedAt: null`` sorts LAST
       (``get-commit-info.ts:43-51``);
    2. the full result shape and all three markdown-link spellings;
    3. the outgoing GraphQL query, byte for byte
       (``get-commit-info.test.ts:153-187``);
    4. the ``Authorization`` header, which upstream's ``nock`` gates the interceptor on.

    The query is asserted piecewise first and byte-exact second so a failure names the wrong
    part rather than dumping two 30-line strings. The exact form matters: the ``repo__<i>`` /
    ``commit__<sha>`` aliasing IS the batching mechanism (``dataloader.ts:133-138`` reads the
    response back by those exact keys), and ``associatedPullRequests(first: 50)`` bounds the
    candidate set attribution is chosen from.
    """
    forge_api.set_response(commit_payload(commit_node(nodes=ONE_MERGED_NODES)))

    info = forge.commit_info(COMMIT_SHA)

    assert info is not None
    assert info.commit.sha == COMMIT_SHA
    assert info.commit.url == COMMIT_URL
    assert info.commit.markdown_link == f"[`{COMMIT_SHA}`]({COMMIT_URL})"
    assert info.pull is not None
    assert info.pull.number == PULL_NUMBER
    assert info.pull.url == f"{SERVER_URL}/{REPO}/pull/{PULL_NUMBER}"
    assert info.pull.markdown_link == f"[#{PULL_NUMBER}]({SERVER_URL}/{REPO}/pull/{PULL_NUMBER})"
    assert info.author is not None
    assert info.author.login == AUTHOR
    assert info.author.url == f"{SERVER_URL}/{AUTHOR}"
    assert info.author.markdown_link == f"[@{AUTHOR}]({SERVER_URL}/{AUTHOR})"

    query = forge_api.last_query or ""
    assert "repo__0: repository(" in query
    assert 'owner: "emotion-js"' in query
    assert 'name: "emotion"' in query
    assert f'commit__{COMMIT_SHA}: object(expression: "{COMMIT_SHA}")' in query
    assert "... on Commit" in query
    assert "fragment CommitFragment on Commit" in query
    assert "associatedPullRequests(first: 50)" in query
    assert "mergedAt" in query, "the sort key must actually be requested"
    assert "commitUrl" in query
    assert query == EXPECTED_COMMIT_QUERY

    body = json.loads(forge_api.requests[-1].content)
    assert set(body) == {"query"}, "dataloader.ts:107 sends exactly {query: ...}"
    content_type = forge_api.requests[-1].headers.get("Content-Type", "")
    assert content_type.startswith("application/json"), (
        "upstream's `fetch` defaults a string body to text/plain; molt sends a correct type"
    )
    assert_every_request_authorized(forge_api)


@pytest.mark.network
def test_a_commit_with_no_associated_pull_requests_falls_back_to_the_commit_author(
    forge: Any, forge_api: ForgeAPI
) -> None:
    """``get-commit-info.ts:55-57`` -- ``pr?.author ?? data.author?.user``, the ``??`` arm.

    Upstream has no row for the empty-nodes case, but it is the common one for a
    directly-pushed commit, and it is what the changelog generator's "no PR link" branch rests
    on. ``pull`` must be ``None`` while ``commit`` and ``author`` survive.
    """
    forge_api.set_response(commit_payload(commit_node(nodes=[])))

    info = forge.commit_info(COMMIT_SHA)

    assert info is not None
    assert info.pull is None
    assert info.author is not None
    assert info.author.login == AUTHOR
    assert info.commit.markdown_link == f"[`{COMMIT_SHA}`]({COMMIT_URL})"


@pytest.mark.network
def test_a_commit_with_no_author_at_all_yields_no_attribution(
    forge: Any, forge_api: ForgeAPI
) -> None:
    """``get-commit-info.ts:65-71`` -- ``author ? {...} : undefined``.

    ``author.user`` is null for a commit whose email GitHub cannot map to an account (the usual
    case for a bot or a rewritten history). The commit block must still resolve.
    """
    forge_api.set_response(commit_payload(commit_node(nodes=[], author_login=None)))

    info = forge.commit_info(COMMIT_SHA)

    assert info is not None
    assert info.author is None
    assert info.pull is None
    assert info.commit.sha == COMMIT_SHA


@pytest.mark.network
def test_a_long_commit_sha_is_sliced_to_seven_in_the_markdown_link(
    forge: Any, forge_api: ForgeAPI
) -> None:
    """``get-commit-info.ts:61, 63`` -- ``sha`` keeps what the CALLER passed, the LINK TEXT slices.

    The commit-path mirror of
    :func:`test_a_long_merge_commit_oid_is_sliced_to_seven_in_the_markdown_link`. Upstream's whole
    fixture set uses the already-abbreviated ``a085003``, so its snapshots cannot tell a correct
    ``.slice(0, 7)`` from a missing one on this path either -- and molt's callers pass whatever is
    in the changeset's ``commit`` field, which is a **full 40-character sha** whenever the
    changeset was written by ``molt add`` from ``git rev-parse HEAD`` rather than by hand. Without
    this row a changelog line reading ``[`a085003d4c8ca284c116668d7217fb747802ed85`](...)`` passes
    the entire suite.

    Note the asymmetry being pinned: ``.sha`` is NOT truncated (``:61`` echoes ``options.commit``
    verbatim), only the rendered link text is (``:63``). An implementation that truncated the
    field as well would still produce a correct-looking link and fail here.
    """
    long_sha = "a085003d4c8ca284c116668d7217fb747802ed85"
    forge_api.set_response(commit_payload(commit_node(nodes=ONE_MERGED_NODES), sha=long_sha))

    info = forge.commit_info(long_sha)

    assert info is not None
    assert info.commit.sha == long_sha, "the full sha the caller passed is preserved on the field"
    assert info.commit.markdown_link == f"[`{COMMIT_SHA}`]({COMMIT_URL})", "only the link slices"


# ======================================================================================
# Row get-commit-info #4 -- multiple merged PRs, earliest wins
# ======================================================================================


@pytest.mark.network
def test_multiple_merged_pull_requests_resolve_to_the_earliest_merged(
    forge: Any, forge_api: ForgeAPI
) -> None:
    """Row 4 (``get-commit-info.test.ts:190-330``; ``get-commit-info.ts:42-55``).

    #1600 merged 2019-11-20, #1613 merged 2019-11-07: the EARLIEST merge wins, because that is
    the PR that actually introduced the change -- a later merge is a backport or a re-land.

    Note what the fixture rules out. #1613 is neither the first node nor the last, and the two
    merged PRs have different authors, so "take node[0]", "take the last merged" and "take the
    commit author" all produce a visibly different result.
    """
    forge_api.set_response(commit_payload(commit_node(nodes=TWO_MERGED_NODES)))

    info = forge.commit_info(COMMIT_SHA)

    assert info is not None
    assert info.pull is not None
    assert info.pull.number == PULL_NUMBER, "earliest mergedAt, not latest and not first"
    assert info.author is not None
    assert info.author.login == AUTHOR


NODE_ORDERS = [
    ([0, 1, 2, 3, 4], "the upstream order"),
    ([2, 1, 0, 3, 4], "the winner first"),
    ([4, 3, 2, 1, 0], "reversed"),
    ([1, 3, 0, 4, 2], "the winner last"),
    ([3, 4, 0, 1, 2], "the unmerged ones first"),
]


@pytest.mark.network
@pytest.mark.parametrize(("order", "why"), NODE_ORDERS)
def test_the_earliest_merged_pull_request_wins_regardless_of_node_order(
    order: list[int], why: str, forge: Any, forge_api: ForgeAPI
) -> None:
    """Row 4, generalized: GitHub does not promise an order for ``nodes``.

    Upstream asserts one fixed arrangement, so a comparator that only *happened* to work on
    that arrangement would pass. Permuting the same five nodes pins the actual invariant --
    total order by ``mergedAt`` with nulls last -- and catches a partial comparator (e.g. one
    that returns ``0`` for a null/non-null pair, which leaves the result dependent on the input
    order).
    """
    forge_api.set_response(commit_payload(commit_node(nodes=[TWO_MERGED_NODES[i] for i in order])))

    info = forge.commit_info(COMMIT_SHA)

    assert info is not None
    assert info.pull is not None
    assert info.pull.number == PULL_NUMBER, why


@pytest.mark.network
def test_with_no_merged_pull_request_the_first_associated_one_is_used(
    forge: Any, forge_api: ForgeAPI
) -> None:
    """``get-commit-info.ts:43-45, 55`` -- the all-null branch of the comparator.

    When every ``mergedAt`` is null the comparator returns ``0`` for every pair, and
    ``Array.prototype.sort`` is stable (spec-guaranteed since ES2019), so ``[0]`` is the FIRST
    node as given. Ported faithfully: ``pull`` is still populated, and the author still comes
    from the PR rather than the commit.

    This is the row that separates a faithful port from ``min(..., key=...)`` over a filtered
    "merged only" list, which would return ``None`` here and silently drop the PR link from an
    open-PR changelog preview.
    """
    forge_api.set_response(
        commit_payload(
            commit_node(
                nodes=[
                    pr_node(1628, None, "mitchellhamilton"),
                    pr_node(1630, None, AUTHOR),
                ]
            )
        )
    )

    info = forge.commit_info(COMMIT_SHA)

    assert info is not None
    assert info.pull is not None
    assert info.pull.number == 1628, "stable sort keeps the input order when all keys tie"
    assert info.author is not None
    assert info.author.login == "mitchellhamilton", "the author still comes from that PR"


# ======================================================================================
# Row get-commit-info #5 -- the PR author beats the commit author
# ======================================================================================


@pytest.mark.network
def test_the_pull_request_author_beats_the_commit_author(
    monkeypatch: pytest.MonkeyPatch, forge_api: ForgeAPI
) -> None:
    """Row 5 (``get-commit-info.test.ts:332-436``; ``get-commit-info.ts:57``).

    ``pr?.author ?? data.author?.user``. Upstream's fixture is the discriminating one and is
    reproduced exactly: the commit is authored by ``JedWatson`` (the maintainer who merged) but
    the PR was opened by ``lmvco``, and the changelog must thank ``lmvco``.
    ``website/docs/forges/github.md`` states the rule as product behavior: "the **PR author is
    preferred over the raw commit author** ... so a change is credited to the human who proposed
    it."
    """
    forge_api.set_response(
        commit_payload(
            commit_node(
                commit_url=RS_COMMIT_URL,
                nodes=[pr_node(RS_PULL_NUMBER, "2019-10-02T07:37:15Z", RS_PR_AUTHOR, repo=RS_REPO)],
                author_login=RS_COMMIT_AUTHOR,
            ),
            sha=RS_SHA,
        )
    )
    forge = build_forge(monkeypatch, repo=RS_REPO)

    info = forge.commit_info(RS_SHA)

    assert info is not None
    assert info.author is not None
    assert info.author.login == RS_PR_AUTHOR, "credit the proposer, not the merger"
    assert info.author.markdown_link == f"[@{RS_PR_AUTHOR}]({SERVER_URL}/{RS_PR_AUTHOR})"
    assert info.commit.markdown_link == f"[`{RS_SHA}`]({RS_COMMIT_URL})"
    assert info.pull is not None
    assert info.pull.number == RS_PULL_NUMBER
    assert 'owner: "JedWatson"' in (forge_api.last_query or "")
    assert 'name: "react-select"' in (forge_api.last_query or "")


# ======================================================================================
# Row get-commit-info #6 -- the endpoint is configuration, not a constant
# ======================================================================================

CUSTOM_GRAPHQL_URL = "https://custom.github.invalid/api/graphql"
CUSTOM_SERVER_URL = "https://custom.github.invalid"


@pytest.mark.network
def test_a_custom_graphql_url_redirects_the_request_and_the_rendered_links(
    monkeypatch: pytest.MonkeyPatch, forge_api: ForgeAPI
) -> None:
    """Row 6 (``get-commit-info.test.ts:438-542``; ``env.ts:23-26``).

    ``GITHUB_GRAPHQL_URL`` is the GitHub Enterprise Server escape hatch, and
    ``website/docs/forges/github.md`` documents it as backend configuration -- NOT as an
    Action-specific knob. Research README section 5 item 7 generalizes it: the base URL is
    configuration so a second forge is a config change plus a backend, not a rewrite.

    The exact request URL is asserted, not just "a request happened". That matters because the
    shared ``forge_api`` fixture matches ``.../graphql`` on ANY host
    (``tests/conftest.py:491``) -- which is what makes the override testable at all, but it also
    means a backend that hard-coded ``api.github.com`` would still be intercepted and would
    still return a green result. Only the URL comparison catches that.

    The markdown links must embed the custom host too, which they do for a structural reason
    worth stating: every URL in the result comes from the API *response*
    (``get-commit-info.ts:62, 68, 75``), never from string-concatenating a base. A backend that
    built ``https://github.com/{repo}/commit/{sha}`` itself would pass every other row here and
    fail this one.
    """
    forge_api.set_response(
        commit_payload(
            commit_node(
                commit_url=f"{CUSTOM_SERVER_URL}/{REPO}/commit/a085003d4c8ca284c116668d7217fb",
                nodes=[
                    pr_node(PULL_NUMBER, "2019-11-07T06:43:58Z", AUTHOR, host=CUSTOM_SERVER_URL)
                ],
                host=CUSTOM_SERVER_URL,
            )
        )
    )
    forge = build_forge(monkeypatch, server_url=CUSTOM_SERVER_URL, graphql_url=CUSTOM_GRAPHQL_URL)

    info = forge.commit_info(COMMIT_SHA)

    assert str(forge_api.requests[-1].url) == CUSTOM_GRAPHQL_URL
    assert info is not None
    assert info.commit.markdown_link == f"[`{COMMIT_SHA}`]({info.commit.url})"
    assert CUSTOM_SERVER_URL in info.commit.markdown_link
    assert info.pull is not None
    assert info.pull.markdown_link == (
        f"[#{PULL_NUMBER}]({CUSTOM_SERVER_URL}/{REPO}/pull/{PULL_NUMBER})"
    )
    assert info.author is not None
    assert info.author.markdown_link == f"[@{AUTHOR}]({CUSTOM_SERVER_URL}/{AUTHOR})"
    assert "github.com/" not in info.pull.markdown_link, "no hard-coded github.com anywhere"
    assert_every_request_authorized(forge_api)


@pytest.mark.network
def test_without_an_override_the_documented_default_endpoint_is_used(
    monkeypatch: pytest.MonkeyPatch, forge_api: ForgeAPI
) -> None:
    """``env.ts:23-26`` / ``website/docs/forges/github.md`` -- the default is
    ``https://api.github.com/graphql``.

    The complement of the override row: without it, a backend that ignored
    ``GITHUB_GRAPHQL_URL`` *and* pointed somewhere arbitrary would still pass, since the fixture
    matches any host.
    """
    forge_api.set_response(commit_payload(commit_node(nodes=ONE_MERGED_NODES)))
    forge = build_forge(monkeypatch, graphql_url=None)

    assert forge.commit_info(COMMIT_SHA) is not None
    assert str(forge_api.requests[-1].url) == DEFAULT_GRAPHQL_URL
    assert forge.api_url == DEFAULT_GRAPHQL_URL


# ======================================================================================
# Rows get-pull-request-info #2 and #3
# ======================================================================================


@pytest.mark.network
def test_a_missing_pull_request_resolves_to_none(forge: Any, forge_api: ForgeAPI) -> None:
    """get-pull-request-info row 2 (``get-pull-request-info.test.ts:29-43``;
    ``get-pull-request-info.ts:37``).

    Same absence contract as the commit path, keyed by ``pull__<n>`` instead of
    ``commit__<sha>``.
    """
    forge_api.set_response(pull_payload(None))

    assert forge.pull_request_info(PULL_NUMBER) is None
    assert len(forge_api.requests) == 1
    assert_every_request_authorized(forge_api)


@pytest.mark.network
def test_a_pull_request_resolves_to_its_author_and_merge_commit(
    forge: Any, forge_api: ForgeAPI
) -> None:
    """get-pull-request-info row 3 (``get-pull-request-info.test.ts:45-127``;
    ``get-pull-request-info.ts:39-59``).

    Note the asymmetry with the commit path, which is easy to get wrong: here the commit sha
    comes from ``mergeCommit.abbreviatedOid`` (a value from the response) whereas
    ``get-commit-info.ts:61`` echoes back the sha the *caller* passed. The markdown link then
    slices that oid to 7 characters (``:56``) -- so an already-7-character oid is unchanged,
    which is exactly why a fixture with a longer oid is needed to make the slice observable.
    That case is the row below.

    The PullFragment query is pinned byte-exact for the same reason as the commit query.
    """
    forge_api.set_response(pull_payload(pull_node()))

    info = forge.pull_request_info(PULL_NUMBER)

    assert info is not None
    assert info.pull.number == PULL_NUMBER
    assert info.pull.url == f"{SERVER_URL}/{REPO}/pull/{PULL_NUMBER}"
    assert info.pull.markdown_link == f"[#{PULL_NUMBER}]({SERVER_URL}/{REPO}/pull/{PULL_NUMBER})"
    assert info.author is not None
    assert info.author.login == AUTHOR
    assert info.author.markdown_link == f"[@{AUTHOR}]({SERVER_URL}/{AUTHOR})"
    assert info.commit is not None
    assert info.commit.sha == COMMIT_SHA
    assert info.commit.url == COMMIT_URL
    assert info.commit.markdown_link == f"[`{COMMIT_SHA}`]({COMMIT_URL})"

    query = forge_api.last_query or ""
    assert "repo__0: repository(" in query
    assert f"pull__{PULL_NUMBER}: pullRequest(number: {PULL_NUMBER})" in query
    assert "fragment PullFragment on PullRequest" in query
    assert "mergeCommit" in query
    assert "abbreviatedOid" in query
    assert "CommitFragment" not in query, "a pull lookup must not drag in the commit fragment"
    assert query == EXPECTED_PULL_QUERY
    assert_every_request_authorized(forge_api)


@pytest.mark.network
def test_a_long_merge_commit_oid_is_sliced_to_seven_in_the_markdown_link(
    forge: Any, forge_api: ForgeAPI
) -> None:
    """``get-pull-request-info.ts:54-56`` -- ``sha`` keeps the oid, the LINK TEXT is sliced.

    Upstream's fixture uses an oid that is already 7 characters, so its snapshot cannot tell a
    correct ``.slice(0, 7)`` from a missing one. GitHub's ``abbreviatedOid`` grows past 7 in
    large repositories, which is when the bug would surface.
    """
    long_oid = "a085003d4c8"
    forge_api.set_response(pull_payload(pull_node(merge_commit_sha=long_oid)))

    info = forge.pull_request_info(PULL_NUMBER)

    assert info is not None
    assert info.commit is not None
    assert info.commit.sha == long_oid, "the full abbreviated oid is preserved on the field"
    assert info.commit.markdown_link == f"[`a085003`]({COMMIT_URL})", "only the link text slices"


@pytest.mark.network
def test_an_unmerged_pull_request_has_no_commit_and_a_null_author_has_no_attribution(
    forge: Any, forge_api: ForgeAPI
) -> None:
    """``get-pull-request-info.ts:45-58`` -- both optional arms.

    An open PR has ``mergeCommit: null``; a PR from a deleted account has ``author: null``.
    Neither may raise, and ``pull`` must survive both.
    """
    forge_api.set_response(pull_payload(pull_node(author_login=None, merge_commit_sha=None)))

    info = forge.pull_request_info(PULL_NUMBER)

    assert info is not None
    assert info.pull.number == PULL_NUMBER
    assert info.author is None
    assert info.commit is None


# ======================================================================================
# GraphQL-level error handling (dataloader.ts:120-131) -- no upstream row, but the
# failure mode it guards is what a throttled request degrades into.
# ======================================================================================


@pytest.mark.network
def test_graphql_errors_in_a_200_response_are_raised(forge: Any, forge_api: ForgeAPI) -> None:
    """``dataloader.ts:120-124`` -- GraphQL reports failures inside a 200.

    An implementation that only checked the HTTP status would read ``data`` as absent and report
    the entity as merely missing, silently dropping attribution from a published changelog.
    """
    forge_api.set_response({"errors": [{"message": "Bad credentials", "type": "FORBIDDEN"}]})

    with pytest.raises(Exception) as excinfo:
        forge.commit_info(COMMIT_SHA)

    assert_actionable(excinfo.value, "Bad credentials")


@pytest.mark.network
def test_a_response_with_no_data_block_is_raised(forge: Any, forge_api: ForgeAPI) -> None:
    """``dataloader.ts:126-131`` -- "this is mainly for the case where there's an
    authentication problem".

    Distinct from row 2: ``{"data": {...}}`` with a null alias means "no such commit" and
    returns ``None``; a response with no ``data`` key at all means the call itself failed.
    """
    forge_api.set_response({"message": "Bad credentials"})

    with pytest.raises(Exception) as excinfo:
        forge.commit_info(COMMIT_SHA)

    assert_actionable(excinfo.value)


# ======================================================================================
# MOLT-NATIVE: a request-dedupe cache that actually caches
# (research README section 3.4; website/docs/forges/overview.md)
# ======================================================================================


@pytest.mark.network
def test_one_commit_is_fetched_exactly_once_across_equal_but_distinct_lookups(
    forge: Any, forge_api: ForgeAPI
) -> None:
    """UPSTREAM BUG FIXED, and the row that must fail against upstream's design.

    ``dataloader.ts:64-67`` calls ``GHDataLoader.load({...options, kind: "commit"})`` -- a
    freshly allocated object on every call -- and the loader is constructed with no
    ``cacheKeyFn`` (``:62``), so its cache map is keyed by **object identity** and never hits.
    ``website/docs/forges/overview.md`` ("A cache that actually caches") and
    ``website/docs/forges/github.md`` ("keys its attribution cache by ``(kind, repository,
    id)``") commit molt to the fix.

    ANTI-VACUITY, and the reason this test is written the awkward way it is: if both lookups
    passed the *same* string object, an identity-keyed cache would hit and the test would be
    green against the very bug it exists to catch. :func:`fresh_copy` builds an equal-but-
    distinct sha and the ``is not`` assertion below fails loudly if that ever stops being true.
    Verified by mutation: re-introducing an identity-keyed cache in a reference implementation
    makes this row fail with ``2 != 1``.

    The two results are compared as well -- a "cache" that answers differently the second time
    is worse than no cache.
    """
    forge_api.set_response(commit_payload(commit_node(nodes=ONE_MERGED_NODES)))
    first_sha, second_sha = COMMIT_SHA, fresh_copy(COMMIT_SHA)
    assert first_sha == second_sha and first_sha is not second_sha, (
        "the two keys must be equal but not identical, or this test cannot detect "
        "an identity-keyed cache"
    )

    first = forge.commit_info(first_sha)
    second = forge.commit_info(second_sha)

    assert len(forge_api.requests) == 1, "one distinct commit -> exactly one HTTP call"
    assert first is not None and second is not None
    assert (first.commit.sha, first.commit.url) == (second.commit.sha, second.commit.url)
    assert first.pull is not None and second.pull is not None
    assert first.pull.number == second.pull.number


@pytest.mark.network
def test_one_pull_request_is_fetched_exactly_once(forge: Any, forge_api: ForgeAPI) -> None:
    """The same fix on the pull path (``dataloader.ts:69-72`` has the identical defect).

    Ints below 257 are cached singletons in CPython, so ``fresh_copy`` has no int analogue;
    ``int("1613")`` does allocate, and the ``is not`` assertion proves it for this value.
    """
    forge_api.set_response(pull_payload(pull_node()))
    first_pull, second_pull = PULL_NUMBER, int(str(PULL_NUMBER))
    assert first_pull == second_pull and first_pull is not second_pull

    assert forge.pull_request_info(first_pull) is not None
    assert forge.pull_request_info(second_pull) is not None
    assert len(forge_api.requests) == 1


@pytest.mark.network
def test_two_different_commits_are_two_lookups(forge: Any, forge_api: ForgeAPI) -> None:
    """ANTI-VACUITY: a cache keyed on a constant also passes "fetched once".

    Such a cache would attribute the first commit's PR to every later one. Two shas must be two
    requests, and each request must name its own sha.
    """
    other_sha = "b1c2d3e"
    forge_api.set_response(
        {
            "data": {
                "repo__0": {
                    f"commit__{COMMIT_SHA}": commit_node(nodes=ONE_MERGED_NODES),
                    f"commit__{other_sha}": commit_node(nodes=[]),
                }
            }
        }
    )

    assert forge.commit_info(COMMIT_SHA) is not None
    assert forge.commit_info(other_sha) is not None

    assert len(forge_api.requests) == 2
    queries = sent_queries(forge_api)
    assert f"commit__{COMMIT_SHA}" in queries[0]
    assert f"commit__{other_sha}" in queries[1]


@pytest.mark.network
def test_the_cache_key_includes_the_repository(forge: Any, forge_api: ForgeAPI) -> None:
    """ANTI-VACUITY: ``(kind, repo, id)``, not ``(kind, id)``.

    Shas are repository-scoped. A cache that ignored the repo would serve one repository's
    commit metadata for another -- wrong links in a published changelog, and the first thing a
    monorepo with vendored history would hit. ``website/docs/forges/overview.md`` spells the
    key with the host in it too: ``(host, kind, repository, id)``.
    """
    forge_api.set_response(commit_payload(commit_node(nodes=ONE_MERGED_NODES)))

    assert forge.commit_info(COMMIT_SHA) is not None
    assert forge.commit_info(COMMIT_SHA, repo=fresh_copy("other/repo")) is not None

    assert len(forge_api.requests) == 2
    queries = sent_queries(forge_api)
    assert 'name: "emotion"' in queries[0]
    assert 'name: "repo"' in queries[1]
    assert 'owner: "other"' in queries[1]


@pytest.mark.network
def test_the_cache_key_includes_the_kind(forge: Any, forge_api: ForgeAPI) -> None:
    """ANTI-VACUITY: ``(kind, repo, id)``, not ``(repo, id)``.

    ``dataloader.ts:136`` builds the response alias from the kind precisely because a commit
    and a pull request can share an id string -- ``"1613"`` is a plausible short sha. A cache
    keyed without the kind would answer a commit lookup with pull-request metadata.
    """
    collision = "1613"
    forge_api.set_response(
        {
            "data": {
                "repo__0": {
                    f"commit__{collision}": commit_node(nodes=[]),
                    f"pull__{PULL_NUMBER}": pull_node(),
                }
            }
        }
    )

    commit = forge.commit_info(collision)
    pull = forge.pull_request_info(PULL_NUMBER)

    assert len(forge_api.requests) == 2
    assert commit is not None and commit.commit.sha == collision
    assert pull is not None and pull.pull.number == PULL_NUMBER
    queries = sent_queries(forge_api)
    assert f"commit__{collision}" in queries[0]
    assert f"pull__{PULL_NUMBER}" in queries[1]


@pytest.mark.network
def test_the_cache_is_scoped_to_the_forge_instance(
    monkeypatch: pytest.MonkeyPatch, forge_api: ForgeAPI
) -> None:
    """DIVERGENCE, deliberate: upstream's ``GHDataLoader`` is a MODULE-LEVEL singleton
    (``dataloader.ts:62``).

    A process-global cache is unbounded, survives a config change, cannot be cleared between
    two releases in one process, and makes every test in this file order-dependent. molt scopes
    the cache to the forge object, so its lifetime is the release's lifetime. Two independently
    constructed forges therefore each pay for their own lookup.
    """
    forge_api.set_response(commit_payload(commit_node(nodes=ONE_MERGED_NODES)))

    assert build_forge(monkeypatch).commit_info(COMMIT_SHA) is not None
    assert build_forge(monkeypatch).commit_info(COMMIT_SHA) is not None

    assert len(forge_api.requests) == 2, "the cache must not be a module-level singleton"


# ======================================================================================
# MOLT-NATIVE: retry and bounded backoff (research README section 3.4 / section 5 item 14)
# ======================================================================================

# The upper bound this suite pins. Not a number upstream has (it retries zero times); chosen so
# a wedged endpoint costs a bounded, small number of calls. website/docs/forges/github.md
# promises "retries transient 5xx responses with jittered backoff" without naming a count, so
# the contract asserted here is: at least one retry, and a hard ceiling.
MAX_ATTEMPTS = 5
# A single nap may not exceed this, and the whole retry budget may not exceed the total. Without
# both, "bounded" degrades into "eventually terminates", which a 30-minute sleep also satisfies.
MAX_SINGLE_SLEEP_SECONDS = 60.0
MAX_TOTAL_SLEEP_SECONDS = 120.0

TRANSIENT_STATUSES = [
    (500, "an internal error is transient by definition"),
    (502, "a bad gateway is the classic GitHub blip"),
    (503, "service unavailable -- the case website/docs/forges/github.md names"),
    (504, "a gateway timeout"),
    (429, "too many requests: the rate-limit response molt must survive rather than surface"),
]


@pytest.mark.network
@pytest.mark.parametrize(("status", "why"), TRANSIENT_STATUSES)
def test_a_transient_failure_is_retried_and_then_succeeds(
    status: int, why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UPSTREAM BUG FIXED: ``dataloader.ts:100-118`` has no retry of any kind.

    Upstream inspects no ``x-ratelimit-*`` header, honors no ``Retry-After``, and treats a 5xx
    body as JSON -- so a blip mid-release fails the whole run with a parse error. molt retries.

    Uses the local :func:`scripted_forge` helper because the shared fixture cannot express a
    *sequence* of responses (see that docstring for the reported gap).
    """
    with scripted_forge(
        monkeypatch,
        [
            httpx.Response(status, json={"message": "nope"}),
            httpx.Response(200, json=commit_payload(commit_node(nodes=ONE_MERGED_NODES))),
        ],
    ) as (forge, route, sleeps):
        info = forge.commit_info(COMMIT_SHA)

    assert route.call_count == 2, why
    assert info is not None
    assert info.pull is not None and info.pull.number == PULL_NUMBER
    assert len(sleeps) == 1, "one retry means exactly one nap"
    assert sleeps[0] > 0, "a retry with no wait is a hammer, not a backoff"
    assert sleeps[0] <= MAX_SINGLE_SLEEP_SECONDS


@pytest.mark.network
def test_retry_after_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    """``website/docs/forges/github.md``: "It honors ``Retry-After``".

    This is the one deterministic assertion available on the backoff schedule -- jitter makes
    the growth curve itself unassertable, but a server-specified delay is a floor the client
    must respect. GitHub sends ``Retry-After`` on secondary rate limits, and ignoring it is what
    escalates a throttle into a block.
    """
    with scripted_forge(
        monkeypatch,
        [
            httpx.Response(429, headers={"Retry-After": "7"}, json={"message": "slow down"}),
            httpx.Response(200, json=commit_payload(commit_node(nodes=ONE_MERGED_NODES))),
        ],
    ) as (forge, route, sleeps):
        assert forge.commit_info(COMMIT_SHA) is not None

    assert route.call_count == 2
    assert sleeps and sleeps[0] >= 7.0, f"Retry-After: 7 is a floor, not a hint; slept {sleeps!r}"
    assert sleeps[0] <= MAX_SINGLE_SLEEP_SECONDS


# Owner ruling 2026-07-30 (openspec/GAPS.md FS-2): GitHub answers both "permanently forbidden" and
# "secondary rate limit" with the same 403 status. Only these two headers tell them apart, and a
# 403 carrying either one is retried within the SAME bounded budget as every other transient
# status -- no new constant, no new knob.
TRANSIENT_403_HEADERS = [
    ({"x-ratelimit-remaining": "0"}, "an exhausted primary rate limit answered with 403"),
    ({"Retry-After": "1"}, "a secondary rate limit that names its own wait"),
]


@pytest.mark.network
@pytest.mark.parametrize(("headers", "why"), TRANSIENT_403_HEADERS)
def test_a_403_carrying_a_rate_limit_header_is_retried_and_then_succeeds(
    headers: dict[str, str], why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner ruling 2026-07-30 (``openspec/GAPS.md`` ``FS-2``): a 403 is retried, not failed fast,
    when it carries `x-ratelimit-remaining: 0` or `Retry-After` -- the two headers that separate
    GitHub's secondary rate limit from a permanent refusal, both of which answer with the same
    status. Same shape as :func:`test_a_transient_failure_is_retried_and_then_succeeds`: one
    failure, then success, exactly one nap, inside the same bounds every other transient status
    uses.
    """
    with scripted_forge(
        monkeypatch,
        [
            httpx.Response(403, headers=headers, json={"message": "nope"}),
            httpx.Response(200, json=commit_payload(commit_node(nodes=ONE_MERGED_NODES))),
        ],
    ) as (forge, route, sleeps):
        info = forge.commit_info(COMMIT_SHA)

    assert route.call_count == 2, why
    assert info is not None
    assert info.pull is not None and info.pull.number == PULL_NUMBER
    assert len(sleeps) == 1, "one retry means exactly one nap"
    assert sleeps[0] > 0, "a retry with no wait is a hammer, not a backoff"
    assert sleeps[0] <= MAX_SINGLE_SLEEP_SECONDS


PERMANENT_STATUSES = [
    (400, "a malformed query is malformed however many times it is sent"),
    (401, "a bad token stays bad -- retrying just burns the rate limit faster"),
    (404, "an endpoint that is not there will not appear"),
    (422, "an unprocessable entity"),
    (
        403,
        "a plain 403 with neither rate-limit header stays permanent (owner ruling 2026-07-30, "
        "openspec/GAPS.md FS-2) -- retrying would just burn the budget the exhaustion message "
        "then complains about",
    ),
]


@pytest.mark.network
@pytest.mark.parametrize(("status", "why"), PERMANENT_STATUSES)
def test_a_permanent_failure_is_not_retried(
    status: int, why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the retry contract: retrying a 4xx is a bug, not resilience.

    Without this row, "retry everything forever" would pass the transient rows, and a wrong
    ``GITHUB_TOKEN`` in CI would turn one clean 401 into ``MAX_ATTEMPTS`` of them plus minutes
    of sleeping before the same failure.
    """
    with (
        scripted_forge(
            monkeypatch, lambda _request: httpx.Response(status, json={"message": "no"})
        ) as (forge, route, sleeps),
        pytest.raises(Exception) as excinfo,
    ):
        forge.commit_info(COMMIT_SHA)

    assert route.call_count == 1, why
    assert sleeps == [], "a permanent failure must not sleep at all"
    assert_actionable(excinfo.value, str(status))


@pytest.mark.network
def test_a_permanently_failing_endpoint_gives_up_within_a_bounded_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded backoff: retry must terminate, and the waiting must be bounded too.

    Three separate ceilings, because dropping any one of them leaves a hole: an attempt cap
    (or the release never finishes), a per-nap cap (or attempt five sleeps for an hour), and a
    total-budget cap (or five capped naps still add up to an unacceptable stall). The floor
    (``>= 2``) is what keeps this from passing on a client that never retries at all.
    """
    with (
        scripted_forge(
            monkeypatch, lambda _request: httpx.Response(503, json={"message": "down"})
        ) as (forge, route, sleeps),
        pytest.raises(Exception) as excinfo,
    ):
        forge.commit_info(COMMIT_SHA)

    assert route.call_count >= 2, "a transient status must be retried at least once"
    assert route.call_count <= MAX_ATTEMPTS, f"unbounded retry: {route.call_count} attempts"
    assert len(sleeps) == route.call_count - 1, "one nap between each pair of attempts"
    assert all(0 < nap <= MAX_SINGLE_SLEEP_SECONDS for nap in sleeps), f"unbounded nap: {sleeps!r}"
    assert sum(sleeps) <= MAX_TOTAL_SLEEP_SECONDS, f"retry budget blown: {sleeps!r}"
    assert_actionable(excinfo.value, "503")


@pytest.mark.network
def test_an_exhausted_rate_limit_is_reported_actionably(monkeypatch: pytest.MonkeyPatch) -> None:
    """``website/docs/forges/github.md``: molt "reports an actionable error (with the reset
    time) when the rate limit is exhausted".

    Research doc 04 section 5.5 records that upstream inspects no ``x-ratelimit-*`` header at
    all, so an exhausted budget surfaces as "Fetched data from GitHub has missing data"
    (``dataloader.ts:128``) -- a message that sends the user looking for the wrong bug.
    """
    reset_at = "1639354050"
    with (
        scripted_forge(
            monkeypatch,
            lambda _request: httpx.Response(
                429,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset_at},
                json={"message": "API rate limit exceeded"},
            ),
        ) as (forge, route, _sleeps),
        pytest.raises(Exception) as excinfo,
    ):
        forge.commit_info(COMMIT_SHA)

    assert 2 <= route.call_count <= MAX_ATTEMPTS
    assert_actionable(excinfo.value, "rate limit")


# ======================================================================================
# MOLT-NATIVE: authentication is mandatory and explicit
# ======================================================================================


@pytest.mark.network
def test_every_request_carries_the_token_including_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """``dataloader.ts:104-106`` -- ``Authorization: Token <GITHUB_TOKEN>``.

    Upstream's ``nock`` harness gates the interceptor on ``reqheaders`` so an unauthenticated
    request simply fails to match. Here the header is asserted on **every** attempt, retries
    included: a retry path that rebuilt the request without the auth header would pass every
    other row in this file and fail silently in production as an anonymous, heavily throttled
    request.
    """
    with scripted_forge(
        monkeypatch,
        [
            httpx.Response(503, json={"message": "down"}),
            httpx.Response(200, json=commit_payload(commit_node(nodes=ONE_MERGED_NODES))),
        ],
    ) as (forge, route, _sleeps):
        assert forge.commit_info(COMMIT_SHA) is not None

        assert route.call_count == 2
        for index, call in enumerate(route.calls):
            header = call.request.headers.get("Authorization") or ""
            scheme, _, value = header.partition(" ")
            assert scheme.lower() == "token", f"attempt {index}: got {header!r}"
            assert value == TOKEN, f"attempt {index}: the token must survive the retry"


@pytest.mark.network
def test_a_missing_token_is_an_error_not_an_anonymous_request(
    monkeypatch: pytest.MonkeyPatch, forge_api: ForgeAPI
) -> None:
    """``dataloader.ts:84-93`` -- no token is a hard, instructive failure.

    Upstream raises with a ready-made link to the token-creation page and the exact scopes
    needed. molt must at minimum name the variable; what it must NOT do is fall through to an
    anonymous request, which GitHub answers with a 60-requests-per-hour budget and, once that is
    gone, an error that looks like "the repository does not exist".

    Construction and the call are both inside the ``raises`` block so the row does not
    over-specify *when* the check fires -- eager (in ``__init__``) and lazy (at first request)
    are both acceptable; a silent anonymous request is not.
    """
    forge_api.set_response(commit_payload(commit_node(nodes=ONE_MERGED_NODES)))

    with pytest.raises(Exception) as excinfo:
        build_forge(monkeypatch, token=None).commit_info(COMMIT_SHA)

    assert_actionable(excinfo.value, "GITHUB_TOKEN")
    assert forge_api.requests == [], "no token must mean no request, not an anonymous one"


# ======================================================================================
# MOLT-NATIVE: the Forge protocol -- the seam a GitLab/Gitea backend slots into
# (research README section 5 item 7; website/docs/forges/gitlab-gitea-others.md)
# ======================================================================================

# The surface a second backend implements. Method names are host-agnostic vocabulary; the
# attributes are the endpoint configuration that keeps the base URL out of the code.
PROTOCOL_METHODS = {"validate_repo", "commit_info", "pull_request_info"}
PROTOCOL_ATTRIBUTES = {"name", "api_url", "server_url", "repo"}

# A protocol that mentions any of these is not a seam, it is a GitHub client with an interface
# bolted on. "graphql" is on the list deliberately: it is GitHub's transport, and GitLab's is
# REST -- a `graphql_url` attribute on the PROTOCOL would force every backend to pretend.
HOST_SPECIFIC_TOKENS = ("github", "graphql", "octokit", "gitlab", "gitea", "bitbucket")

PROTOCOL_SIGNATURES = [
    ("validate_repo", ["self", "repo"], None, "the shared validator (utils.ts:3)"),
    (
        "commit_info",
        ["self", "commit"],
        "repo",
        "get-commit-info.ts:31-33 takes {commit, repo}; repo is keyword-only and optional here "
        "so custom-generators.md:62 `forge.commit_info(changeset.commit)` works while the "
        "changelog generator can still redirect it per call",
    ),
    (
        "pull_request_info",
        ["self", "pull"],
        "repo",
        "get-pull-request-info.ts:31-33 takes {pull, repo}",
    ),
]


@pytest.mark.unit
def test_github_forge_structurally_satisfies_the_forge_protocol(forge: Any) -> None:
    """Research README section 5 item 7 -- the seam exists on day one.

    changesets is hard-wired to GitHub, and issue #879 ("GitLab support?") drew 35 reactions
    and zero maintainer replies in four years because retrofitting the abstraction is a
    rewrite. ``website/docs/forges/gitlab-gitea-others.md`` promises "adding GitLab is
    implementing a backend against a protocol GitHub already satisfies".

    Structural (``isinstance`` against a ``@runtime_checkable`` ``Protocol``), not nominal: a
    third-party backend must not have to import and subclass a molt base class.
    """
    assert getattr(Forge, "_is_protocol", False), "molt.forge.Forge must be a typing.Protocol"
    try:
        conforms = isinstance(forge, Forge)
    except TypeError as exc:  # pragma: no cover - only on a mis-declared protocol
        pytest.fail(f"Forge must be decorated @runtime_checkable so backends can be checked: {exc}")
    assert conforms, "GitHubForge must satisfy Forge structurally"

    declared = {n for n in dir(Forge) if not n.startswith("_")}
    declared |= set(getattr(Forge, "__annotations__", {}))
    missing = (PROTOCOL_METHODS | PROTOCOL_ATTRIBUTES) - declared
    assert not missing, f"Forge is missing {sorted(missing)}"


@pytest.mark.unit
@pytest.mark.parametrize(("method", "positional", "keyword", "why"), PROTOCOL_SIGNATURES)
def test_the_protocol_methods_have_the_signature_a_second_backend_must_implement(
    method: str, positional: list[str], keyword: str | None, why: str
) -> None:
    """Pin the call shape, not just the names.

    ``isinstance`` against a ``runtime_checkable`` protocol only checks that an attribute
    exists -- it does not look at signatures at all, so a backend with
    ``commit_info(self, **kwargs)`` would "conform" and then fail at the call site. These
    assertions are what actually makes the protocol implementable from the outside.
    """
    signature = inspect.signature(getattr(GitHubForge, method))
    parameters = list(signature.parameters.values())
    assert [p.name for p in parameters[: len(positional)]] == positional, why
    if keyword is None:
        assert len(parameters) == len(positional), f"{method} takes no extra parameters"
        return
    extra = parameters[len(positional) :]
    assert [p.name for p in extra] == [keyword], why
    assert extra[0].kind is inspect.Parameter.KEYWORD_ONLY, (
        f"{method}'s {keyword!r} must be keyword-only so a backend can add parameters later"
    )
    assert extra[0].default is None, f"{method}'s {keyword!r} defaults to the forge's own repo"


@pytest.mark.unit
def test_the_protocol_surface_carries_no_host_specific_vocabulary() -> None:
    """``website/docs/forges/overview.md``: "The engine, the changelog generators, and the CI
    loop call these; they never assume the host is GitHub."

    A protocol member named ``graphql_url`` or ``github_token`` would push GitHub's transport
    into every future backend's signature -- which is precisely how changesets ended up
    unportable. The endpoint lives on the protocol as ``api_url`` and its *value* is
    configuration.
    """
    members = {n for n in dir(Forge) if not n.startswith("_")}
    members |= set(getattr(Forge, "__annotations__", {}))
    offenders = sorted(
        name for name in members if any(token in name.lower() for token in HOST_SPECIFIC_TOKENS)
    )
    assert not offenders, f"host-specific names on the forge protocol: {offenders}"


@pytest.mark.unit
def test_the_backend_reports_its_own_identity_and_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configuration half of the protocol (``env.ts:22-34``;
    ``website/docs/forges/github.md``, "Configuration").

    ``api_url`` and ``server_url`` are read back off the instance rather than only observed on
    the wire, because config resolution is what a second backend has to reimplement, and
    ``name`` is how config selects between backends once there is more than one.
    """
    forge = build_forge(
        monkeypatch, server_url=CUSTOM_SERVER_URL, graphql_url=CUSTOM_GRAPHQL_URL, repo=RS_REPO
    )

    assert forge.name == "github"
    assert forge.api_url == CUSTOM_GRAPHQL_URL
    assert forge.server_url == CUSTOM_SERVER_URL
    assert forge.repo == RS_REPO
