r"""Conformance tests for the pull-request lifecycle on molt's forge seam.

Port source
-----------
``changesets/action`` **v1.9.0** -- the stable tag behind ``uses: changesets/action@v1``:

* ``src/run.ts:347-378`` -- ``octokit.rest.pulls.list({..., state: "open", head:
  "<owner>:<branch>", base})`` and ``searchResult.data[0]``: the open release pull request for a
  branch, first match wins.
* ``src/run.ts:363-370`` -- ``octokit.rest.pulls.create({base, head, title, body})``.
* ``src/run.ts:391-407`` -- a GraphQL ``updatePullRequest`` mutation carrying ``title``, ``body``
  and ``state: OPEN``.

Why this file is separate from ``test_github.py`` and ``test_releases.py``
--------------------------------------------------------------------------
``tests/forge/test_github.py`` is a frozen conformance suite ported row-for-row from upstream's
``get-github-info`` fixtures, and no change edits it. Its protocol row computes ``missing =
(PROTOCOL_METHODS | PROTOCOL_ATTRIBUTES) - declared``, a **subset** check, so new protocol members
land additively -- and this file carries its own signature row, as ``test_releases.py`` does, so
the "implementable by a second backend" guarantee extends to the three members added here.

What molt changes, and why these rows must FAIL against a literal port
----------------------------------------------------------------------
1. **An update is a REST ``PATCH``, not a GraphQL mutation** (design D2). Upstream needs the
   mutation only because the same call optionally flips the pull request to draft, which needs the
   node id; draft modes are out of scope by owner ruling, so the node id buys nothing and would
   mean carrying a ``node_id`` field on ``PullRef`` that no other caller wants. ``PATCH`` takes the
   number the lookup already returned.
2. **``state: "open"`` is ported anyway.** It is the half of the mutation that is *not* about
   drafts: a force-push onto the release branch can close the pull request, and the next run must
   reopen that one rather than open a second and orphan its review history.
3. **The same resilience shell as every other request.** Upstream sets no timeout and never
   retries (research README section 3.4); these calls go through the one ``_send`` loop, so the
   bounded retry budget, the ``Retry-After`` floor, the 403 ruling and the mandatory token apply
   to them unchanged.

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

# httpx and respx are imported BELOW the guard on purpose, mirroring test_github.py:107-113 and
# test_releases.py: tests/conftest.py imports the httpx stack lazily so a not-yet-synced
# environment gets a readable RuntimeError from the fixture instead of a collection-time
# ImportError.
import httpx
import respx

from molt.forge import Forge, GitHubForge, PullRef

# ======================================================================================
# Fixture data
# ======================================================================================

REPO = "emotion-js/emotion"
# The per-call override target: a different repository, so a routing bug cannot hide behind the
# configured one.
OTHER_REPO = "JedWatson/react-select"
TOKEN = "ghp-molt-conformance-token"

#: The release branch molt pushes and looks its pull request up by (``run.ts:112``).
HEAD = "changeset-release/main"
BASE = "main"

TITLE = "Version Packages"
BODY = "# Releases\n\n## molt-release@1.4.0\n\n### Minor Changes\n\n- Add a `--pre` flag\n"

DEFAULT_REST_BASE = "https://api.github.com"
PULLS_URL_RE = r".+/repos/.+/pulls(\?.*)?$"

#: What the host answers a listing with. Two entries, so "the first match wins" is a real choice
#: rather than the only possibility.
FIRST_PULL = {
    "number": 1613,
    "html_url": "https://enterprise.invalid/emotion-js/emotion/pull/1613",
    "url": "https://enterprise.invalid/api/repos/emotion-js/emotion/pulls/1613",
    "state": "open",
}
SECOND_PULL = {
    "number": 1799,
    "html_url": "https://enterprise.invalid/emotion-js/emotion/pull/1799",
    "url": "https://enterprise.invalid/api/repos/emotion-js/emotion/pulls/1799",
    "state": "open",
}

# The bounds this suite pins, identical to test_github.py:1282-1286 and test_releases.py -- the
# retry budget is shared, so the ceilings asserted here must be the same numbers, not a second set.
MAX_ATTEMPTS = 5
MAX_SINGLE_SLEEP_SECONDS = 60.0


# ======================================================================================
# Helpers (never named test_* -- `python_functions = "test"` is a PREFIX match)
# ======================================================================================


@pytest.fixture(autouse=True)
def isolated_forge_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Strip every ambient GitHub variable and run from an empty directory.

    Same guard as ``test_releases.py::isolated_forge_env``: without it a CI run of the suite would
    exercise a different endpoint from a developer's laptop, and a developer's ``.env`` would leak
    a real token into a conformance row.
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
) -> Any:
    """Construct a ``GitHubForge`` from the environment (``env.ts:22-34``).

    ``None`` means "leave the variable unset", which is how the missing-token row is expressed.
    """
    if token is not None:
        monkeypatch.setenv("GITHUB_TOKEN", token)
    if repo is not None:
        monkeypatch.setenv("GITHUB_REPOSITORY", repo)
    return GitHubForge()


@pytest.fixture
def forge(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The common case: a forge for ``emotion-js/emotion`` on the default endpoint."""
    return build_forge(monkeypatch)


def assert_actionable(exc: BaseException, *needles: str) -> None:
    """The raised error must be a deliberate, informative one -- not a crash.

    ``AttributeError`` / ``TypeError`` / ``NameError`` / ``IndexError`` from a half-written parser
    would satisfy a bare ``pytest.raises(Exception)`` and make the "is it retried?" rows pass for
    the wrong reason, so they are excluded by name.
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
) -> Iterator[tuple[Any, Any, list[float]]]:
    """A forge over a **scripted sequence** of pull-request responses, plus the recorded naps.

    The shared ``forge_rest_api`` fixture serves one programmed response per endpoint, so it cannot
    express "fail, then succeed" -- which is the entire retry contract. This drives respx directly,
    exactly as ``test_releases.py::scripted_forge`` does for the release endpoint.

    ``time.sleep`` is patched by *name*, matching the seam ``molt.forge.retry.sleep_for``
    deliberately leaves open, and the arguments are recorded so the backoff bounds are asserted
    rather than tolerated.
    """
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(float(seconds)))
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=PULLS_URL_RE).mock(
            side_effect=list(responses) if isinstance(responses, (list, tuple)) else responses
        )
        yield build_forge(monkeypatch), route, sleeps


def query_of(request: httpx.Request) -> dict[str, str]:
    """The request's query parameters, as a plain dict."""
    return dict(request.url.params)


# ======================================================================================
# Rows: finding the open release pull request (run.ts:347-378)
# ======================================================================================


@pytest.mark.unit
@pytest.mark.network
def test_the_lookup_filters_on_state_head_and_base(
    forge: Any, forge_rest_api: ForgeRestAPI
) -> None:
    """``run.ts:347-352`` -- ``state: "open"``, ``head: "<owner>:<branch>"``, ``base``.

    All three filters are load-bearing and each fails differently when dropped. Without ``state``
    a **closed** release pull request is found and updated into a zombie. Without ``head`` the
    query answers with every open pull request in the repository and the first one -- somebody's
    unrelated feature branch -- gets the release body written over it. Without ``base`` a release
    branch targeting a maintenance branch matches the one targeting ``main``.

    The ``head`` value carries the owner prefix GitHub requires; molt derives it from the target
    slug rather than from an ambient context object it does not have.
    """
    forge.find_open_pull_request(HEAD, base=BASE)

    assert len(forge_rest_api.requests) == 1, "one lookup means one request"
    request = forge_rest_api.requests[0]
    assert request.method == "GET"
    assert str(request.url).startswith(f"{DEFAULT_REST_BASE}/repos/emotion-js/emotion/pulls")
    assert query_of(request) == {"state": "open", "head": f"emotion-js:{HEAD}", "base": BASE}


@pytest.mark.unit
@pytest.mark.network
def test_the_first_open_pull_request_wins(forge: Any, forge_rest_api: ForgeRestAPI) -> None:
    """``run.ts:378`` -- ``searchResult.data[0]``, faithfully.

    Two open pull requests match only if somebody opened one by hand, and the order is then the
    host's rather than molt's (``openspec/GAPS.md`` ``AP-9``). Taking the *last*, or the
    lowest-numbered, would pass a single-entry fixture identically, which is why this row programs
    two.
    """
    forge_rest_api.set_open_pull_requests([FIRST_PULL, SECOND_PULL])

    pull = forge.find_open_pull_request(HEAD, base=BASE)

    assert isinstance(pull, PullRef)
    assert pull.number == 1613
    assert pull.url == FIRST_PULL["html_url"], "the human page, not the API endpoint"
    assert pull.markdown_link == f"[#1613]({FIRST_PULL['html_url']})"


@pytest.mark.unit
@pytest.mark.network
def test_no_open_pull_request_resolves_to_none(forge: Any, forge_rest_api: ForgeRestAPI) -> None:
    """An empty list is the ordinary first run, not a failure.

    Absence is a value here, exactly as it is for a missing commit: the version loop's whole
    create-or-update branch rests on this returning ``None`` rather than raising.
    """
    forge_rest_api.set_open_pull_requests([])

    assert forge.find_open_pull_request(HEAD, base=BASE) is None


# ======================================================================================
# Rows: opening and updating (run.ts:363-370, run.ts:391-407)
# ======================================================================================


@pytest.mark.unit
@pytest.mark.network
def test_creating_a_pull_request_posts_base_head_title_and_body(
    forge: Any, forge_rest_api: ForgeRestAPI
) -> None:
    """``run.ts:363-370`` -- the four fields upstream sends, and the number that comes back.

    The payload is read back off the *serialized* request rather than off the arguments, so a
    backend that renamed a field, or that transposed ``title`` and ``body`` -- two strings no type
    checker can tell apart -- fails here.
    """
    forge_rest_api.set_pull_request(FIRST_PULL)

    pull = forge.create_pull_request(HEAD, base=BASE, title=TITLE, body=BODY)

    request = forge_rest_api.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{DEFAULT_REST_BASE}/repos/emotion-js/emotion/pulls"
    assert json.loads(request.content) == {
        "base": BASE,
        "head": HEAD,
        "title": TITLE,
        "body": BODY,
    }
    assert pull.number == 1613
    assert pull.url == FIRST_PULL["html_url"]


@pytest.mark.unit
@pytest.mark.network
def test_updating_a_pull_request_patches_it_open_with_the_new_title_and_body(
    forge: Any, forge_rest_api: ForgeRestAPI
) -> None:
    """``run.ts:391-407`` -- title, body **and** ``state: "open"``, on the numbered pull request.

    The state field is the ported half of upstream's mutation that is *not* about draft modes: a
    force-push onto the release branch can close the pull request, and a run that only replaced the
    title and body would leave a closed pull request carrying the new release notes where nobody
    looks. The URL carries the number the lookup returned, which is what makes this an update
    rather than a second pull request.
    """
    forge_rest_api.set_pull_request(FIRST_PULL)

    pull = forge.update_pull_request(1613, title=TITLE, body=BODY)

    request = forge_rest_api.requests[0]
    assert request.method == "PATCH"
    assert str(request.url) == f"{DEFAULT_REST_BASE}/repos/emotion-js/emotion/pulls/1613"
    assert json.loads(request.content) == {"title": TITLE, "body": BODY, "state": "open"}
    assert pull.number == 1613


# ======================================================================================
# Rows: the shared guards apply to all three members
# ======================================================================================


def call_find(forge: Any, **kwargs: Any) -> Any:
    return forge.find_open_pull_request(HEAD, base=BASE, **kwargs)


def call_create(forge: Any, **kwargs: Any) -> Any:
    return forge.create_pull_request(HEAD, base=BASE, title=TITLE, body=BODY, **kwargs)


def call_update(forge: Any, **kwargs: Any) -> Any:
    return forge.update_pull_request(1613, title=TITLE, body=BODY, **kwargs)


PULL_REQUEST_CALLS = [
    pytest.param(call_find, id="find"),
    pytest.param(call_create, id="create"),
    pytest.param(call_update, id="update"),
]


@pytest.mark.unit
@pytest.mark.network
@pytest.mark.parametrize("call", PULL_REQUEST_CALLS)
def test_the_per_call_repo_override_routes_every_member(
    call: Any, forge: Any, forge_rest_api: ForgeRestAPI
) -> None:
    """Forge design D2 -- ``repo`` is a keyword-only per-call override, not a second instance.

    Constructing a second backend per repository would throw away the attribution cache and the
    token resolution, which is why every member takes the override instead. Parametrized over all
    three because the override is easy to wire into one member and forget in the next.
    """
    forge_rest_api.set_pull_request(FIRST_PULL)

    call(forge, repo=OTHER_REPO)

    url = str(forge_rest_api.requests[0].url)
    assert "/repos/JedWatson/react-select/pulls" in url, url
    assert "emotion" not in url, "the configured repo must not win over an explicit one"


@pytest.mark.unit
@pytest.mark.network
def test_an_invalid_repo_is_rejected_before_any_pull_request_call(
    monkeypatch: pytest.MonkeyPatch, forge_rest_api: ForgeRestAPI
) -> None:
    """``utils.ts:1-9`` -- the slug is validated **before** a socket is opened.

    The load-bearing half is the ordering: the slug is interpolated into the request URL, so
    validating after the request would have leaked the token to whatever the interpolation
    produced.
    """
    forge = build_forge(monkeypatch, repo="https://github.com/JedWatson/react-select")

    with pytest.raises(Exception) as excinfo:
        forge.find_open_pull_request(HEAD, base=BASE)

    assert_actionable(excinfo.value, "userOrOrg/repoName")
    assert forge_rest_api.requests == [], "a malformed slug must never reach the network"


@pytest.mark.unit
@pytest.mark.network
def test_a_missing_token_raises_before_any_pull_request_call(
    monkeypatch: pytest.MonkeyPatch, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D7 -- no token is a hard, instructive failure, never an anonymous request.

    Anonymous GitHub access is 60 requests an hour and then answers with errors that look like a
    missing repository, so falling back to it turns one clear configuration mistake into an
    intermittent failure much later. A pull-request *write* has no anonymous form at all, which
    would surface as a 404 on a repository that plainly exists.
    """
    forge = build_forge(monkeypatch, token=None)

    with pytest.raises(Exception) as excinfo:
        forge.create_pull_request(HEAD, base=BASE, title=TITLE, body=BODY)

    assert_actionable(excinfo.value, "GITHUB_TOKEN")
    assert forge_rest_api.requests == [], "no token must mean no request, not an anonymous one"


@pytest.mark.unit
@pytest.mark.network
def test_a_transient_lookup_failure_is_retried_and_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared budget covers the pull-request calls too (design D3).

    A release pull request lost to a single GitHub blip means the next run opens a *second* one,
    because the lookup is how molt recognises the first. That is the failure this retry prevents,
    and it is why the third transport reuses the one ``_send`` loop rather than growing a weaker
    copy.
    """
    with scripted_forge(
        monkeypatch,
        [
            httpx.Response(500, json={"message": "boom"}),
            httpx.Response(200, json=[FIRST_PULL]),
        ],
    ) as (forge, route, sleeps):
        pull = forge.find_open_pull_request(HEAD, base=BASE)

    assert route.call_count == 2
    assert pull is not None and pull.number == 1613
    assert len(sleeps) == 1, "one retry means exactly one nap"
    assert 0 < sleeps[0] <= MAX_SINGLE_SLEEP_SECONDS, "a retry with no wait is a hammer"
    for call in route.calls:
        assert call.request.headers.get("Authorization") == f"Token {TOKEN}", (
            "every attempt carries the token, retries included"
        )


@pytest.mark.unit
@pytest.mark.network
def test_a_permanent_lookup_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrying a 4xx is a bug, not resilience.

    Without this row "retry everything" would pass the transient row above, and a repository the
    token cannot read would turn one clean 404 into ``MAX_ATTEMPTS`` of them plus minutes of
    sleeping before the same failure.
    """
    with (
        scripted_forge(
            monkeypatch, lambda _request: httpx.Response(404, json={"message": "no"})
        ) as (forge, route, sleeps),
        pytest.raises(Exception) as excinfo,
    ):
        forge.find_open_pull_request(HEAD, base=BASE)

    assert route.call_count == 1, "a permanent failure is one request"
    assert route.call_count <= MAX_ATTEMPTS
    assert sleeps == [], "a permanent failure must not sleep at all"
    assert_actionable(excinfo.value, "404")


# ======================================================================================
# Row: the seam stays implementable by a second backend
# ======================================================================================


PULL_REQUEST_SIGNATURES = {
    "find_open_pull_request": (
        "head",
        {"base": inspect.Parameter.empty, "repo": None},
    ),
    "create_pull_request": (
        "head",
        {
            "base": inspect.Parameter.empty,
            "title": inspect.Parameter.empty,
            "body": inspect.Parameter.empty,
            "repo": None,
        },
    ),
    "update_pull_request": (
        "number",
        {"title": inspect.Parameter.empty, "body": inspect.Parameter.empty, "repo": None},
    ),
}


@pytest.mark.unit
def test_the_pull_request_members_have_the_signature_a_second_backend_must_implement() -> None:
    """Pin the call shape, not just the name (the same guarantee ``PROTOCOL_SIGNATURES`` gives).

    ``isinstance`` against a ``runtime_checkable`` protocol only checks that an attribute exists,
    so a backend spelling ``update_pull_request(self, **kwargs)`` would "conform" and then fail at
    the call site. One positional subject each -- the branch, or the number -- with everything else
    keyword-only, because ``title`` and ``body`` are both strings and a positional pair invites
    transposing them silently. ``repo`` is last and defaults to ``None`` so a backend can add
    parameters later without breaking callers.

    **Both** the protocol declaration and the implementation are inspected, so a drift between
    them fails here rather than waiting for a second backend (``openspec/GAPS.md`` ``FS-4`` covers
    the three older members, which are inspected on the implementation only).
    """
    for name, (subject, expected_keywords) in PULL_REQUEST_SIGNATURES.items():
        for owner in (Forge, GitHubForge):
            signature = inspect.signature(getattr(owner, name))
            parameters = list(signature.parameters.values())
            assert [p.name for p in parameters[:2]] == ["self", subject], (
                f"{owner.__name__}.{name} takes {subject} positionally"
            )
            assert parameters[1].kind is not inspect.Parameter.KEYWORD_ONLY
            extra = parameters[2:]
            assert [p.name for p in extra] == list(expected_keywords), f"{owner.__name__}.{name}"
            for parameter in extra:
                assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
                    f"{owner.__name__}.{name}'s {parameter.name!r} must be keyword-only"
                )
                assert parameter.default == expected_keywords[parameter.name], (
                    f"{owner.__name__}.{name}'s {parameter.name!r} default"
                )
