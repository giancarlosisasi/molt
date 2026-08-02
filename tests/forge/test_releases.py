r"""Conformance tests for release publication on molt's forge seam (``Forge.create_release``).

Port source
-----------
``changesets/action`` **v1.9.0**, ``src/run.ts::createRelease`` -- the stable tag behind
``uses: changesets/action@v1``::

    await octokit.rest.repos.createRelease({
      name: tagName,
      tag_name: tagName,
      body: changelogEntry.content,
      prerelease: pkg.packageJson.version.includes("-"),
      ...github.context.repo,
    });

Upstream calls it once per released package, after the tag is pushed, and discards the response.

Why this file is separate from ``test_github.py``
-------------------------------------------------
``tests/forge/test_github.py`` is a frozen conformance suite ported row-for-row from upstream's
``get-github-info`` fixtures, and this change does not edit it (design D6). Two things make that
safe: its protocol row computes ``missing = (PROTOCOL_METHODS | PROTOCOL_ATTRIBUTES) - declared``,
a **subset** check, and its signature row is parametrized over a fixed list. A new protocol member
therefore lands additively -- and this file carries its own signature row so the "implementable by
a second backend" guarantee extends to it.

What molt changes, and why these rows must FAIL against a literal port
----------------------------------------------------------------------
1. **REST, with the same resilience shell as everything else.** Upstream sets no timeout and never
   retries (research README section 3.4). GitHub's GraphQL schema has no release-creation
   mutation, so this is the one call that leaves the GraphQL transport -- and it inherits the
   bounded, jittered, ``Retry-After``-aware budget rather than growing a second, weaker one
   (design D3).
2. **A duplicate release is a value, not an exception** (design D2). Upstream throws whatever the
   API returns, so re-running a publish that half-failed fails again on the packages that already
   succeeded. Owner ruling 2026-07-31: carry on quietly; a retry must complete the missing
   releases, not fail on the finished ones.
3. **The prerelease flag is PEP 440** (design D4). Pinned separately, and against the npm reading,
   in ``tests/versioning/test_prerelease_flag.py``.

The transport is stubbed with ``respx`` over ``httpx`` -- the shared ``forge_rest_api`` fixture in
``tests/conftest.py``, plus a local scripted router for the rows that need a *sequence* of
responses. No row opens a real socket.
"""

from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from tests.conftest import ForgeRestAPI

pytest.importorskip("molt.forge", reason="molt.forge is this suite's target")

# httpx and respx are imported BELOW the guard on purpose, mirroring test_github.py:107-113:
# tests/conftest.py imports the httpx stack lazily so a not-yet-synced environment gets a readable
# RuntimeError from the fixture instead of a collection-time ImportError.
import httpx
import respx

from molt.forge import Forge, GitHubForge, ReleaseInfo

# ======================================================================================
# Fixture data
# ======================================================================================

REPO = "emotion-js/emotion"
# The per-call override target: a different repository, so a routing bug cannot hide behind the
# configured one.
OTHER_REPO = "JedWatson/react-select"
TOKEN = "ghp-molt-conformance-token"
SERVER_URL = "https://github.com"

# Upstream passes `name: tagName` -- the same string in both fields. They are asserted as two
# distinct values here so a backend that transposed them would fail rather than pass by accident.
TAG = "molt-release@1.4.0"
RELEASE_NAME = "molt-release 1.4.0"

# A realistic changelog section, the shape `molt.action.get_changelog_entry` slices out of a
# just-written CHANGELOG.md. Markdown, several blank lines, links and a trailing newline: every
# character has to survive the round trip to the wire.
BODY = """\
## 1.4.0

### Minor Changes

- [`a085003`](https://gh.invalid/e/e/commit/a085003) Thanks [@Andarist](https://gh.invalid/A)! -
  Add a `--pre` flag to `molt version`

### Patch Changes

- Updated dependencies [`c7e9c69`]
  - molt-core@2.1.1
"""

# The documented default REST base (GitHub's own API host), used when nothing is overridden.
DEFAULT_REST_BASE = "https://api.github.com"
# A self-hosted stand-in. `.invalid` is reserved by RFC 2606, so a request that escaped respx
# would fail to resolve rather than reach a real host -- the same convention test_github.py uses.
SELF_HOSTED_API_URL = "https://ghes.invalid/api/v3"
RELEASES_URL_RE = r".+/repos/.+/releases/?$"

# What GitHub answers a repeated `tag_name` with: 422 plus an `already_exists` error code.
DUPLICATE_BODY = {
    "message": "Validation Failed",
    "errors": [{"resource": "Release", "code": "already_exists", "field": "tag_name"}],
}
# A 422 that is NOT a duplicate. Same status, different code -- which is the whole reason the
# duplicate rule is a conjunction rather than a status check (design D2; compare
# molt.publish.is_duplicate_upload_error).
OTHER_422_BODY = {
    "message": "Validation Failed",
    "errors": [{"resource": "Release", "code": "invalid", "field": "tag_name"}],
}

# The created-release document, with a host URL that no base-URL concatenation could produce:
# `html_url` must be read off the response (design D8), never built.
CREATED_BODY = {
    "id": 176938321,
    "html_url": "https://enterprise.invalid/emotion-js/emotion/releases/tag/molt-release%401.4.0",
    "tag_name": TAG,
    "name": RELEASE_NAME,
}

# The bounds this suite pins, identical to test_github.py:1282-1286 -- the retry budget is shared,
# so the ceilings asserted on the REST path must be the same numbers, not a second set.
MAX_ATTEMPTS = 5
MAX_SINGLE_SLEEP_SECONDS = 60.0
MAX_TOTAL_SLEEP_SECONDS = 120.0


# ======================================================================================
# Helpers (never named test_* -- `python_functions = "test"` is a PREFIX match)
# ======================================================================================


@pytest.fixture(autouse=True)
def isolated_forge_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Strip every ambient GitHub variable and run from an empty directory.

    Same guard as ``test_github.py::isolated_forge_env``, with ``GITHUB_API_URL`` added: it is
    read on the same terms as the other four (including from a ``.env``), and every GitHub Actions
    runner exports it -- so without this a CI run of the suite would exercise a different endpoint
    from a developer's laptop.
    """
    for var in (
        "GITHUB_TOKEN",
        "GITHUB_REPOSITORY",
        "GITHUB_SERVER_URL",
        "GITHUB_GRAPHQL_URL",
        "GITHUB_API_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


def build_forge(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repo: str | None = REPO,
    token: str | None = TOKEN,
    server_url: str = SERVER_URL,
    api_url: str | None = None,
    graphql_url: str | None = None,
) -> Any:
    """Construct a ``GitHubForge`` from the environment (``env.ts:22-34``).

    ``None`` means "leave the variable unset", which is how the default-endpoint and missing-token
    rows are expressed.
    """
    if token is not None:
        monkeypatch.setenv("GITHUB_TOKEN", token)
    if repo is not None:
        monkeypatch.setenv("GITHUB_REPOSITORY", repo)
    monkeypatch.setenv("GITHUB_SERVER_URL", server_url)
    if graphql_url is not None:
        monkeypatch.setenv("GITHUB_GRAPHQL_URL", graphql_url)
    if api_url is not None:
        monkeypatch.setenv("GITHUB_API_URL", api_url)
    return GitHubForge()


@pytest.fixture
def forge(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The common case: a forge for ``emotion-js/emotion`` on the default endpoint."""
    return build_forge(monkeypatch)


def assert_actionable(exc: BaseException, *needles: str) -> None:
    """The raised error must be a deliberate, informative one -- not a crash.

    ``AttributeError`` / ``TypeError`` / ``NameError`` / ``IndexError`` from a half-written parser
    would satisfy a bare ``pytest.raises(Exception)`` and make the "is it retried?" rows pass for
    the wrong reason, so they are excluded by name. Copied from ``test_github.py:373`` rather than
    imported: that file is frozen, and a helper import would couple the two suites' lifetimes.
    """
    assert not isinstance(exc, (AttributeError, TypeError, NameError, IndexError)), (
        f"expected a deliberate forge error, got a programming error: {exc!r}"
    )
    text = str(exc)
    for needle in needles:
        assert needle.lower() in text.lower(), f"expected {needle!r} in the error message: {text!r}"


def assert_every_request_authorized(
    requests: Sequence[httpx.Request], token: str = TOKEN, *, expected: int | None = None
) -> None:
    """``Authorization: Token <token>`` on EVERY attempt, retries included.

    Asserted per request rather than on the last one: a retry path that rebuilt the request
    without the header would pass every other row here and then run as an anonymous, heavily
    throttled request in production.

    The scheme is compared case-insensitively (RFC 7235 makes auth schemes case-insensitive) but
    the token value is compared exactly.
    """
    assert requests, "expected at least one request to assert the header on"
    if expected is not None:
        assert len(requests) == expected
    for index, request in enumerate(requests):
        header = request.headers.get("Authorization") or ""
        scheme, _, value = header.partition(" ")
        assert scheme.lower() == "token", f"attempt {index}: expected token scheme, got {header!r}"
        assert value == token, f"attempt {index}: the token must reach the wire unmodified"


@contextmanager
def scripted_forge(
    monkeypatch: pytest.MonkeyPatch,
    responses: Sequence[httpx.Response] | Callable[[httpx.Request], httpx.Response],
    **forge_kwargs: Any,
) -> Iterator[tuple[Any, Any, list[float]]]:
    """A forge over a **scripted sequence** of release responses, plus the recorded backoff naps.

    The shared ``forge_rest_api`` fixture serves one programmed response per row, so it cannot
    express "fail, then succeed" -- which is the entire retry contract. This drives respx directly
    instead, exactly as ``test_github.py::scripted_forge`` does for the GraphQL endpoint.

    ``time.sleep`` is patched by *name*, matching the seam ``molt.forge.retry.sleep_for``
    deliberately leaves open, and the arguments are recorded so the backoff bounds are asserted
    rather than tolerated.
    """
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(float(seconds)))
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__regex=RELEASES_URL_RE).mock(
            side_effect=list(responses) if isinstance(responses, (list, tuple)) else responses
        )
        yield build_forge(monkeypatch, **forge_kwargs), route, sleeps


def created(**overrides: Any) -> dict[str, Any]:
    """The created-release document, with per-row overrides."""
    return {**CREATED_BODY, **overrides}


# ======================================================================================
# Rows: the request molt sends (run.ts::createRelease)
# ======================================================================================


@pytest.mark.network
def test_a_release_is_posted_to_the_releases_endpoint_for_the_configured_repo(
    forge: Any, forge_rest_api: ForgeRestAPI
) -> None:
    """``run.ts::createRelease`` -- one POST per released package, carrying four fields.

    This row also pins the **default** endpoint: with no override configured, the call goes to
    GitHub's documented REST base, which is the fallback derived from the GraphQL URL. A backend
    that hard-coded a path, or that posted to the GraphQL endpoint, fails here.
    """
    result = forge.create_release(TAG, name=RELEASE_NAME, body=BODY)

    assert len(forge_rest_api.requests) == 1, "one release means one request"
    request = forge_rest_api.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{DEFAULT_REST_BASE}/repos/emotion-js/emotion/releases"
    assert forge_rest_api.last_payload == {
        "tag_name": TAG,
        "name": RELEASE_NAME,
        "body": BODY,
        "prerelease": False,
    }
    assert result is not None, "a created release is reported back"


@pytest.mark.network
def test_the_release_body_is_forwarded_verbatim(forge: Any, forge_rest_api: ForgeRestAPI) -> None:
    """The body is a changelog section sliced out of a written ``CHANGELOG.md``.

    Byte-compared, and read back out of the *serialized* request rather than off the argument, so
    a backend that re-wrapped, stripped or re-encoded the Markdown fails. The literal newlines
    matter: a release body rendered as one long line is unreadable on the host and is exactly what
    a naive `.strip()` or a line-joining helper produces.
    """
    forge.create_release(TAG, name=RELEASE_NAME, body=BODY)

    payload = json.loads(forge_rest_api.requests[0].content)
    assert payload["body"] == BODY
    assert payload["body"].count("\n") == BODY.count("\n"), "the blank lines are structure"


@pytest.mark.network
def test_every_release_attempt_carries_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """``dataloader.ts:104-106`` -- ``Authorization: Token <GITHUB_TOKEN>`` on every attempt.

    The REST call is a second transport, so "the header is attached" has to be re-asserted here:
    a split that built headers only on the GraphQL path would leave every release request
    unauthenticated while all 95 attribution rows stayed green.
    """
    with scripted_forge(
        monkeypatch,
        [
            httpx.Response(503, json={"message": "down"}),
            httpx.Response(201, json=created()),
        ],
    ) as (forge, route, _sleeps):
        assert forge.create_release(TAG, name=RELEASE_NAME, body=BODY) is not None

        assert_every_request_authorized([call.request for call in route.calls], expected=2)


@pytest.mark.network
def test_a_missing_token_raises_before_any_release_request(
    monkeypatch: pytest.MonkeyPatch, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D7 -- no token is a hard, instructive failure, never an anonymous request.

    Anonymous GitHub access is 60 requests an hour and then answers with errors that look like a
    missing repository, so falling back to it turns one clear configuration mistake into an
    intermittent failure much later.
    """
    forge = build_forge(monkeypatch, token=None)

    with pytest.raises(Exception) as excinfo:
        forge.create_release(TAG, name=RELEASE_NAME, body=BODY)

    assert_actionable(excinfo.value, "GITHUB_TOKEN")
    assert forge_rest_api.requests == [], "no token must mean no request, not an anonymous one"


@pytest.mark.network
def test_an_invalid_repo_is_rejected_before_any_release_request(
    monkeypatch: pytest.MonkeyPatch, forge_rest_api: ForgeRestAPI
) -> None:
    """``utils.ts:1-9`` -- the slug is validated **before** a socket is opened.

    The load-bearing half is the ordering: the slug is interpolated into the request URL, so
    validating after the request would have leaked the token to whatever the interpolation
    produced.
    """
    forge = build_forge(monkeypatch, repo="https://github.com/JedWatson/react-select")

    with pytest.raises(Exception) as excinfo:
        forge.create_release(TAG, name=RELEASE_NAME, body=BODY)

    assert_actionable(excinfo.value, "userOrOrg/repoName")
    assert forge_rest_api.requests == [], "a malformed slug must never reach the network"


@pytest.mark.network
def test_the_per_call_repo_override_routes_to_the_other_repository(
    forge: Any, forge_rest_api: ForgeRestAPI
) -> None:
    """Forge design D2 -- ``repo`` is a keyword-only per-call override, not a second instance.

    Constructing a second backend per repository would throw away the attribution cache and the
    token resolution, which is why every member takes the override instead.
    """
    forge.create_release(TAG, name=RELEASE_NAME, body=BODY, repo=OTHER_REPO)

    assert str(forge_rest_api.requests[0].url).endswith("/repos/JedWatson/react-select/releases")
    assert "emotion" not in str(forge_rest_api.requests[0].url), "the configured repo must not win"


@pytest.mark.network
@pytest.mark.parametrize("prerelease", [True, False])
def test_the_prerelease_flag_is_sent_as_given(
    prerelease: bool, forge: Any, forge_rest_api: ForgeRestAPI
) -> None:
    """The backend forwards the flag; it does **not** derive it (design D1/D4).

    A backend that parsed the version itself would force every future backend to re-implement PEP
    440. The decision lives in ``molt.versioning.is_prerelease`` and is pinned by
    ``tests/versioning/test_prerelease_flag.py``.
    """
    forge.create_release(TAG, name=RELEASE_NAME, body=BODY, prerelease=prerelease)

    payload = forge_rest_api.last_payload
    assert payload is not None
    assert payload["prerelease"] is prerelease


@pytest.mark.network
def test_a_created_release_is_reported_with_the_hosts_url_and_id(
    forge: Any, forge_rest_api: ForgeRestAPI
) -> None:
    """Forge design D8 -- every URL comes off the response, never from concatenating a base.

    That is what makes GitHub Enterprise Server work with no backend change: the response's
    ``html_url`` here is on a host that no concatenation of ``server_url`` and the repo slug could
    produce, so a backend that built the link fails this row while passing every other one.
    """
    forge_rest_api.set_response(created(), status=201)

    info = forge.create_release(TAG, name=RELEASE_NAME, body=BODY)

    assert isinstance(info, ReleaseInfo)
    assert info.tag == TAG
    assert info.name == RELEASE_NAME
    assert info.url == CREATED_BODY["html_url"]
    assert info.id == CREATED_BODY["id"]
    assert not info.url.startswith(SERVER_URL), "the URL is the response's, not a built one"


# ======================================================================================
# Rows: a release that already exists is an answer, not a failure (design D2)
# ======================================================================================


@pytest.mark.network
def test_a_duplicate_release_resolves_to_none(forge: Any, forge_rest_api: ForgeRestAPI) -> None:
    """Owner ruling 2026-07-31: carry on quietly.

    Upstream throws whatever the API returns, so re-running a publish that half-failed fails again
    on the packages that already succeeded -- and strands the ones that did not. A retry must
    complete the missing releases, not fail on the finished ones. This matches the seam's existing
    idiom: a missing commit resolves to ``None`` rather than raising.
    """
    forge_rest_api.set_response(DUPLICATE_BODY, status=422)

    assert forge.create_release(TAG, name=RELEASE_NAME, body=BODY) is None
    assert len(forge_rest_api.requests) == 1, "a duplicate is not retried either"


@pytest.mark.network
def test_a_validation_failure_that_is_not_a_duplicate_is_raised(
    forge: Any, forge_rest_api: ForgeRestAPI
) -> None:
    """The duplicate rule is a **conjunction**: the status *and* the ``already_exists`` code.

    Matching on the status alone would also have satisfied the row above, and would silently read
    an ordinary validation failure -- a tag that does not exist, a body over the size limit -- as
    "already released", which drops a release with no error anywhere. Anything unrecognised stays
    a failure, which is the safe direction (compare ``molt.publish.is_duplicate_upload_error``).
    """
    forge_rest_api.set_response(OTHER_422_BODY, status=422)

    with pytest.raises(Exception) as excinfo:
        forge.create_release(TAG, name=RELEASE_NAME, body=BODY)

    assert_actionable(excinfo.value, "422")


# ======================================================================================
# Rows: the shared resilience shell applies to the REST call too (design D3)
# ======================================================================================


@pytest.mark.network
def test_a_permanent_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrying a 4xx is a bug, not resilience.

    Without this row "retry everything" would pass the transient row below, and a repository the
    token cannot write to would turn one clean 404 into ``MAX_ATTEMPTS`` of them plus minutes of
    sleeping before the same failure.
    """
    with (
        scripted_forge(
            monkeypatch, lambda _request: httpx.Response(404, json={"message": "no"})
        ) as (forge, route, sleeps),
        pytest.raises(Exception) as excinfo,
    ):
        forge.create_release(TAG, name=RELEASE_NAME, body=BODY)

    assert route.call_count == 1, "a permanent failure is one request"
    assert sleeps == [], "a permanent failure must not sleep at all"
    assert_actionable(excinfo.value, "404")


@pytest.mark.network
def test_a_transient_failure_is_retried_and_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Upstream sets no timeout and never retries (research README section 3.4).

    A release created after a single GitHub blip is the whole point of putting this call through
    the same shell as the attribution queries rather than writing a second, weaker one.
    """
    with scripted_forge(
        monkeypatch,
        [
            httpx.Response(500, json={"message": "boom"}),
            httpx.Response(201, json=created()),
        ],
    ) as (forge, route, sleeps):
        info = forge.create_release(TAG, name=RELEASE_NAME, body=BODY)

    assert route.call_count == 2
    assert info is not None and info.url == CREATED_BODY["html_url"]
    assert len(sleeps) == 1, "one retry means exactly one nap"
    assert 0 < sleeps[0] <= MAX_SINGLE_SLEEP_SECONDS, "a retry with no wait is a hammer"


@pytest.mark.network
def test_a_permanently_failing_endpoint_gives_up_within_a_bounded_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same three ceilings the attribution path asserts, on the same shared budget.

    The naps-per-attempt identity is the property most easily lost when a retry loop is
    refactored to serve two transports: the loop must never sleep after the final attempt.
    """
    with (
        scripted_forge(
            monkeypatch, lambda _request: httpx.Response(503, json={"message": "down"})
        ) as (forge, route, sleeps),
        pytest.raises(Exception) as excinfo,
    ):
        forge.create_release(TAG, name=RELEASE_NAME, body=BODY)

    assert route.call_count >= 2, "a transient status must be retried at least once"
    assert route.call_count <= MAX_ATTEMPTS, f"unbounded retry: {route.call_count} attempts"
    assert len(sleeps) == route.call_count - 1, "one nap between each pair of attempts"
    assert all(0 < nap <= MAX_SINGLE_SLEEP_SECONDS for nap in sleeps), f"unbounded nap: {sleeps!r}"
    assert sum(sleeps) <= MAX_TOTAL_SLEEP_SECONDS, f"retry budget blown: {sleeps!r}"
    assert_actionable(excinfo.value, "503")


# ======================================================================================
# Rows: the endpoint follows the configured host, and the seam stays implementable
# ======================================================================================


@pytest.mark.network
def test_a_self_hosted_api_url_routes_the_release_call(
    monkeypatch: pytest.MonkeyPatch, forge_rest_api: ForgeRestAPI
) -> None:
    """A GitHub Enterprise Server install works with no backend change.

    ``GITHUB_API_URL`` is what every GitHub Actions runner exports, GHES included, and it is read
    on the same terms as the four variables the backend already reads. A hard-coded
    ``https://api.github.com`` would pass every other row in this file.
    """
    forge = build_forge(monkeypatch, api_url=SELF_HOSTED_API_URL)

    forge.create_release(TAG, name=RELEASE_NAME, body=BODY)

    url = str(forge_rest_api.requests[0].url)
    assert url.startswith(SELF_HOSTED_API_URL), url
    assert url == f"{SELF_HOSTED_API_URL}/repos/emotion-js/emotion/releases"


@pytest.mark.unit
def test_create_release_has_the_signature_a_second_backend_must_implement() -> None:
    """Pin the call shape, not just the name (the same guarantee ``PROTOCOL_SIGNATURES`` gives).

    ``isinstance`` against a ``runtime_checkable`` protocol only checks that an attribute exists,
    so a backend spelling ``create_release(self, **kwargs)`` would "conform" and then fail at the
    call site. ``tag`` is positional (one subject, like ``commit_info(commit, ...)``); ``name`` and
    ``body`` are keyword-only because upstream always passes the same string for the tag and the
    name, so a positional pair invites transposing them silently; ``repo`` defaults to ``None`` so
    a backend can add parameters later without breaking callers.

    Unlike ``test_github.py``'s row, **both** the protocol declaration and the implementation are
    inspected, so a drift between them fails here rather than waiting for a second backend
    (``openspec/GAPS.md`` ``FS-4`` covers the other three members, which are inspected on the
    implementation only).
    """
    expected_keywords = {
        "name": inspect.Parameter.empty,
        "body": inspect.Parameter.empty,
        "prerelease": False,
        "repo": None,
    }

    for owner in (Forge, GitHubForge):
        signature = inspect.signature(owner.create_release)
        parameters = list(signature.parameters.values())
        assert [p.name for p in parameters[:2]] == ["self", "tag"], (
            f"{owner.__name__}.create_release takes the tag positionally"
        )
        assert parameters[1].kind is not inspect.Parameter.KEYWORD_ONLY
        extra = parameters[2:]
        assert [p.name for p in extra] == list(expected_keywords), owner.__name__
        for parameter in extra:
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
                f"{owner.__name__}.create_release's {parameter.name!r} must be keyword-only"
            )
            assert parameter.default == expected_keywords[parameter.name], (
                f"{owner.__name__}.create_release's {parameter.name!r} default"
            )
