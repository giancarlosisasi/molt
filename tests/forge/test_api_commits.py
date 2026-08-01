r"""Conformance tests for ``Forge.create_commit`` -- commit creation over the host's API.

Port source
-----------
Fetched and read 2026-08-01:

* ``changesets/action`` **v1.9.0** ``src/git.ts::pushChanges`` -- in its ``commitMode:
  github-api`` arm, ``git add .`` + ``git commit`` + ``git push --force`` become one
  ``commitChangesFromRepo({..., base: {commit: context.sha}, force: true})`` call.
* ``@changesets/ghcommit`` ``src/core.ts::commitChanges`` -- the ref choreography: create the
  branch when it is absent, and under ``force`` commit onto a temporary branch, then
  ``updateRef(..., force: true)``, then ``deleteRef`` in a ``finally``. Its own comment states the
  reason: *"We cannot reset the branch and then commit because if the branch has an existing PR,
  GitHub will auto-close as it sees there's no changes with the base."*
* GitHub GraphQL reference, "Commits" -- ``createCommitOnBranch(input:
  CreateCommitOnBranchInput!)``; ``branch: CommittableBranch!`` takes either a node id or
  ``repositoryNameWithOwner`` + ``branchName``; ``expectedHeadOid``, ``message: {headline, body}``
  and ``fileChanges``, whose ``FileAddition.contents`` must be RFC 4648 base64 with correct
  padding. *"Commits made using this mutation are automatically signed by GitHub if supported and
  will be marked as verified."*

Why this file is separate from the three forge suites beside it
---------------------------------------------------------------
``tests/forge/test_github.py`` is a frozen conformance suite ported row-for-row from upstream's
``get-github-info`` fixtures, and no change edits it. Its protocol row computes ``missing =
(PROTOCOL_METHODS | PROTOCOL_ATTRIBUTES) - declared``, a **subset** check, so a new protocol member
lands additively -- and this file carries its own signature row over **both** ``Forge`` and
``GitHubForge``, the shape ``test_releases.py`` (``FR-7``) set the precedent for.

What molt changes, and why these rows must FAIL against a literal port
----------------------------------------------------------------------
1. **The branch is addressed by ``{repositoryNameWithOwner, branchName}``**, not by a node id
   upstream resolves with a preliminary query. GitHub's ``CommittableBranch`` accepts either; the
   pair saves a round trip and a third hand-written query to maintain.
2. **Everything travels in one GraphQL variable.** ``fileChanges`` carries base64 file contents and
   ``message`` carries whatever a workflow put in its ``commit`` input; interpolating either into
   query text is how a payload becomes a query injection.
3. **An empty change set sends no mutation at all** and answers ``None``. Upstream does not guard
   it, so a version script that no-ops would write an empty commit onto the release branch -- whose
   diff is a public document -- on every run.
4. **The temporary branch is ``molt/tmp/<branch>``**, not ``changesets-ghcommit-temp/<branch>``: it
   is molt's ref in a molt user's repository, and one prefix lets a repository protect or ignore
   the whole namespace with a single rule (``openspec/GAPS.md`` ``ACM-3``).
5. **The same resilience shell as every other request.** Upstream sets no timeout and never retries
   (research README section 3.4); every call here -- the mutation and all four ref calls -- goes
   through the one ``_send`` loop, so the bounded retry budget, the ``Retry-After`` floor, the 403
   ruling and the mandatory token apply unchanged.

The transport is stubbed with ``respx`` over ``httpx``: the shared ``forge_api`` (GraphQL) and
``forge_rest_api`` (refs) fixtures from ``tests/conftest.py``, plus a local scripted router for the
rows that need a *sequence* of responses. **No row opens a real socket.**

One harness note worth reading before the ordering rows: the two shared fixtures each record into
their own ``requests`` list, and the commit choreography interleaves them -- refs, mutation, refs.
:func:`shared_log` points both lists at the same object, which works because every handler in both
classes appends to ``self.requests``, and gives one ordered log across both transports.
"""

from __future__ import annotations

import base64
import inspect
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from tests.conftest import ForgeAPI, ForgeRestAPI

pytest.importorskip("molt.forge", reason="molt.forge is this suite's target")

# httpx and respx are imported BELOW the guard on purpose, mirroring test_github.py:107-113,
# test_releases.py and test_pull_requests.py: tests/conftest.py imports the httpx stack lazily so a
# not-yet-synced environment gets a readable RuntimeError from the fixture instead of a
# collection-time ImportError.
import httpx
import respx

from molt.forge import NO_FILE_ADDITIONS, CommitRef, Forge, GitHubForge
from molt.forge.github import TEMPORARY_BRANCH_PREFIX

# ======================================================================================
# Fixture data
# ======================================================================================

REPO = "emotion-js/emotion"
# The per-call override target: a different repository, so a routing bug cannot hide behind the
# configured one.
OTHER_REPO = "JedWatson/react-select"
TOKEN = "ghp-molt-conformance-token"

#: The release branch molt commits onto (``run.ts:112``), and the base commit it measured its
#: changes against -- the commit the workflow checked out.
BRANCH = "changeset-release/main"
TEMP_BRANCH = f"{TEMPORARY_BRANCH_PREFIX}{BRANCH}"
BASE = "9a5d0a5e0f4f9d4a2f1d3c6b8e7a0c1d2e3f4a5b"
#: What the branch points at when a previous release already put a commit on it.
STALE_HEAD = "1111111111111111111111111111111111111111"
#: What the mutation reports back. Deliberately unlike both of the above.
NEW_COMMIT = "abcdef1234567890abcdef1234567890abcdef12"
COMMIT_URL = "https://enterprise.invalid/emotion-js/emotion/commit/abcdef1"

MESSAGE = "Version Packages"
MULTILINE_MESSAGE = "Version Packages\n\nReleases pkg-a@1.4.0 and pkg-b@2.0.0.\n"

DEFAULT_REST_BASE = "https://api.github.com"
DEFAULT_GRAPHQL = "https://api.github.com/graphql"
# A self-hosted stand-in. `.invalid` is reserved by RFC 2606, so a request that escaped respx would
# fail to resolve rather than reach a real host -- the convention every forge suite uses.
SELF_HOSTED_API_URL = "https://ghes.invalid/api/v3"
SELF_HOSTED_GRAPHQL_URL = "https://ghes.invalid/api/graphql"
GRAPHQL_URL_RE = r".+/graphql/?$"

#: A realistic release: two rewritten manifests, a changelog and a consumed changeset.
ADDITIONS = {
    "packages/pkg-a/pyproject.toml": b'[project]\nname = "pkg-a"\nversion = "1.4.0"\n',
    "packages/pkg-a/CHANGELOG.md": b"# pkg-a\n\n## 1.4.0\n",
}
DELETIONS = (".changeset/strange-words-combine.md",)

# The bounds this suite pins, identical to test_github.py:1282-1286, test_releases.py and
# test_pull_requests.py -- the retry budget is shared, so the ceilings asserted here must be the
# same numbers, not a second set.
MAX_ATTEMPTS = 5
MAX_SINGLE_SLEEP_SECONDS = 60.0


def commit_response(oid: str = NEW_COMMIT, url: str = COMMIT_URL) -> dict[str, Any]:
    """What GitHub answers a successful ``createCommitOnBranch`` with."""
    return {"data": {"createCommitOnBranch": {"commit": {"oid": oid, "commitUrl": url}}}}


#: A GraphQL failure: HTTP 200 with an ``errors`` array. This is the shape that makes a status-only
#: check wrong (design D6), and the specific message GitHub sends when the branch moved.
STALE_HEAD_RESPONSE = {
    "data": None,
    "errors": [{"message": "Expected branch to point to " + BASE}],
}


# ======================================================================================
# Helpers (never named test_* -- `python_functions = "test"` is a PREFIX match)
# ======================================================================================


@pytest.fixture(autouse=True)
def isolated_forge_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Strip every ambient GitHub variable and run from an empty directory.

    Same guard as ``test_releases.py::isolated_forge_env`` and its pull-request twin: without it a
    CI run of the suite would exercise a different endpoint from a developer's laptop, and a
    developer's ``.env`` would leak a real token into a conformance row.
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
    api_url: str | None = None,
    graphql_url: str | None = None,
) -> Any:
    """Construct a ``GitHubForge`` from the environment (``env.ts:22-34``).

    ``None`` means "leave the variable unset", which is how the missing-token row is expressed.
    """
    if token is not None:
        monkeypatch.setenv("GITHUB_TOKEN", token)
    if repo is not None:
        monkeypatch.setenv("GITHUB_REPOSITORY", repo)
    if graphql_url is not None:
        monkeypatch.setenv("GITHUB_GRAPHQL_URL", graphql_url)
    if api_url is not None:
        monkeypatch.setenv("GITHUB_API_URL", api_url)
    return GitHubForge()


@pytest.fixture
def forge(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The common case: a forge for ``emotion-js/emotion`` on the default endpoints."""
    return build_forge(monkeypatch)


def shared_log(graphql: ForgeAPI, rest: ForgeRestAPI) -> list[httpx.Request]:
    """Point both fixtures' ``requests`` lists at one object and return it.

    An API commit interleaves the two transports -- ref calls are REST, the commit itself is the
    GraphQL mutation -- so "create the temporary branch, commit on it, move the release branch,
    delete the temporary branch" is only assertable as **one** ordered sequence. Two lists cannot
    be merged after the fact: nothing in an ``httpx.Request`` records when it was sent.

    Aliasing is safe because every handler in both fixture classes appends to ``self.requests`` and
    neither ever rebinds it.
    """
    rest.requests = graphql.requests
    return graphql.requests


def steps(log: Sequence[httpx.Request]) -> list[str]:
    """One readable ``"<METHOD> <what>"`` line per recorded request, in order.

    The URL is reduced to the part that identifies the call, so an assertion reads as the
    choreography rather than as a wall of absolute URLs.
    """
    rendered: list[str] = []
    for request in log:
        path = request.url.path
        if path.endswith("/graphql"):
            rendered.append("POST graphql")
        elif "/git/ref/" in path:
            rendered.append(f"GET ref {path.split('/git/ref/heads/', 1)[-1]}")
        elif path.endswith("/git/refs"):
            rendered.append(f"POST refs {json.loads(request.content)['ref']}")
        else:
            rendered.append(f"{request.method} refs {path.split('/git/refs/heads/', 1)[-1]}")
    return rendered


def mutation_input(log: Sequence[httpx.Request]) -> dict[str, Any]:
    """The ``input`` variable of the one GraphQL request in ``log``."""
    posted = [request for request in log if request.url.path.endswith("/graphql")]
    assert len(posted) == 1, f"expected exactly one mutation, got {len(posted)}"
    body = json.loads(posted[0].content)
    return dict(body["variables"]["input"])


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


@contextmanager
def scripted_mutation(
    monkeypatch: pytest.MonkeyPatch,
    responses: Sequence[httpx.Response] | Callable[[httpx.Request], httpx.Response],
) -> Iterator[tuple[Any, Any, list[float]]]:
    """A forge over a **scripted sequence** of mutation responses, plus the recorded naps.

    The shared ``forge_api`` fixture serves one programmed response, so it cannot express "fail,
    then succeed" -- which is the entire retry contract. This drives respx directly, exactly as
    ``test_releases.py::scripted_forge`` and ``test_pull_requests.py::scripted_forge`` do for their
    own endpoints, and it also answers every ref call so the choreography can run to completion.

    ``time.sleep`` is patched by *name*, matching the seam ``molt.forge.retry.sleep_for``
    deliberately leaves open, and the arguments are recorded so the backoff bounds are asserted
    rather than tolerated.
    """
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(float(seconds)))
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".+/git/ref/.+$").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        router.post(url__regex=r".+/git/refs$").mock(
            return_value=httpx.Response(201, json={"object": {"sha": BASE}})
        )
        route = router.post(url__regex=GRAPHQL_URL_RE).mock(
            side_effect=list(responses) if isinstance(responses, (list, tuple)) else responses
        )
        yield build_forge(monkeypatch), route, sleeps


def call(forge: Any, **overrides: Any) -> Any:
    """``create_commit`` with this file's standard release, minus whatever a row replaces."""
    arguments: dict[str, Any] = {
        "base": BASE,
        "message": MESSAGE,
        "additions": ADDITIONS,
        "deletions": DELETIONS,
    }
    arguments.update(overrides)
    branch = arguments.pop("branch", BRANCH)
    return forge.create_commit(branch, **arguments)


# ======================================================================================
# Rows 1-6: what goes on the wire
# ======================================================================================


@pytest.mark.unit
@pytest.mark.network
def test_the_mutation_addresses_the_branch_by_name_and_pins_the_expected_head(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D2 -- ``{repositoryNameWithOwner, branchName}``, ``expectedHeadOid``, ``message``.

    Addressing the branch by the owner/name pair is what removes upstream's preliminary
    ``repository { ref { id } }`` query; GitHub's ``CommittableBranch`` accepts either form.

    ``expectedHeadOid`` is the half that makes the write **safe**: it means "commit only if nothing
    else has touched this branch since I measured it". A mutation that omitted it would pass every
    other row here and would let two concurrent runs silently overwrite each other.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_api.set_response(commit_response())

    call(forge)

    assert mutation_input(log)["branch"] == {
        "repositoryNameWithOwner": REPO,
        "branchName": BRANCH,
    }
    assert mutation_input(log)["expectedHeadOid"] == BASE
    assert mutation_input(log)["message"] == {"headline": MESSAGE}
    assert str(forge_api.requests[-1].url) == DEFAULT_GRAPHQL


@pytest.mark.unit
@pytest.mark.network
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param(MESSAGE, {"headline": MESSAGE}, id="single-line-sends-no-body"),
        pytest.param(
            MULTILINE_MESSAGE,
            {"headline": "Version Packages", "body": "Releases pkg-a@1.4.0 and pkg-b@2.0.0."},
            id="multi-line-splits",
        ),
    ],
)
def test_a_multi_line_message_splits_into_a_headline_and_a_body(
    message: str,
    expected: dict[str, str],
    forge: Any,
    forge_api: ForgeAPI,
    forge_rest_api: ForgeRestAPI,
) -> None:
    """GraphQL's ``CommitMessage`` is ``{headline, body}``; git's is one string.

    The first line becomes the headline and the remainder the body, which is git's own convention
    read the other way round. ``body`` is **omitted** rather than sent empty for a one-line message,
    so a repository with a conventional-commit subject gets exactly the commit it would have got
    from the git command line.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_api.set_response(commit_response())

    call(forge, message=message)

    assert mutation_input(log)["message"] == expected


@pytest.mark.unit
@pytest.mark.network
@pytest.mark.parametrize("message", ["", "   ", "\n", "\n\n  \t"])
def test_a_message_with_no_content_is_refused_before_any_request(
    message: str, forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """``CommitMessage.headline`` is ``String!`` -- a required, non-empty scalar.

    The case is reachable rather than theoretical: ``CO-17`` ruled that an empty action input means
    "not supplied", but a workflow can pass a ``commit`` input that is one newline, and molt would
    otherwise discover that only after creating a temporary branch on the user's repository.
    Refusing first is what keeps the failure free of side effects (``openspec/GAPS.md`` ``ACM-5``).
    """
    shared_log(forge_api, forge_rest_api)

    with pytest.raises(Exception) as excinfo:
        call(forge, message=message)

    assert_actionable(excinfo.value, "message")
    assert forge_api.requests == [], "a message molt cannot send must never reach the network"


@pytest.mark.unit
@pytest.mark.network
@pytest.mark.parametrize(
    "contents",
    [
        pytest.param(b"a", id="one-byte-two-padding-characters"),
        pytest.param(b"ab", id="two-bytes-one-padding-character"),
        pytest.param(b"abc", id="three-bytes-no-padding"),
    ],
)
def test_addition_contents_are_rfc_4648_base64_with_padding(
    contents: bytes, forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """``FileAddition.contents`` must be base64 **with correct padding**, GitHub's own words.

    Parametrized over all three lengths modulo 3 so both padding shapes appear: a base64 encoder
    that stripped ``=`` would pass the three-byte row and fail the other two, and GitHub rejects an
    unpadded payload outright. The assertion decodes back and compares bytes, so an encoder that
    used the URL-safe alphabet -- same length, same padding, different characters for two of the
    64 -- fails too.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_api.set_response(commit_response())

    call(forge, additions={"a.txt": contents}, deletions=())

    sent = mutation_input(log)["fileChanges"]["additions"][0]["contents"]
    assert sent == base64.b64encode(contents).decode("ascii")
    assert base64.b64decode(sent) == contents


@pytest.mark.unit
@pytest.mark.network
def test_bytes_that_are_not_text_survive_the_round_trip(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D3 -- the seam takes bytes, so a file molt cannot decode still commits correctly.

    Two hazards in one row. A byte sequence that is not valid UTF-8 would raise on any path that
    decoded it to text, and CRLF would be *silently translated* -- which is worse, because the
    release would succeed and the committed file would differ from the one on the runner's disk.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_api.set_response(commit_response())
    payload = {"binary.bin": b"\xff\xfe\x00\x01", "crlf.txt": b"first\r\nsecond\r\n"}

    call(forge, additions=payload, deletions=())

    sent = {
        entry["path"]: base64.b64decode(entry["contents"])
        for entry in mutation_input(log)["fileChanges"]["additions"]
    }
    assert sent == payload


@pytest.mark.unit
@pytest.mark.network
def test_additions_and_deletions_are_sorted_so_the_request_is_deterministic(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """``@changesets/ghcommit`` sorts both lists by path, and molt does too.

    The same release computed twice must produce byte-identical requests, whatever order the
    working-tree scan happened to yield -- which is what lets this suite compare serialized bodies
    instead of re-parsing and re-sorting them, and what keeps a diff of two workflow logs readable.

    The two payloads below hold the same entries in opposite orders.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_api.set_response(commit_response())
    forward = {"a/one.txt": b"1", "b/two.txt": b"2", "c/three.txt": b"3"}
    backward = {"c/three.txt": b"3", "b/two.txt": b"2", "a/one.txt": b"1"}

    call(forge, additions=forward, deletions=("z.md", "a.md"))
    first = json.loads(log[-1].content)
    log.clear()
    call(forge, additions=backward, deletions=("a.md", "z.md"))
    second = json.loads(log[-1].content)

    assert first == second
    changes = first["variables"]["input"]["fileChanges"]
    assert [entry["path"] for entry in changes["additions"]] == [
        "a/one.txt",
        "b/two.txt",
        "c/three.txt",
    ]
    assert [entry["path"] for entry in changes["deletions"]] == ["a.md", "z.md"]


# ======================================================================================
# Rows 7-12: the ref choreography (design D4 / D5)
# ======================================================================================


@pytest.mark.unit
@pytest.mark.network
def test_a_branch_the_host_does_not_have_is_created_at_the_base_and_committed_onto(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """The first release run of a repository: there is no release branch yet.

    ``@changesets/ghcommit``'s own first branch. There is nothing to force past, so no temporary
    branch is created at all -- creating one anyway would work but would leave a second ref on
    every first run, and the ordered log is what makes that visible.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_rest_api.set_ref(None)
    forge_api.set_response(commit_response())

    commit = call(forge)

    assert steps(log) == [
        f"GET ref {BRANCH}",
        f"POST refs refs/heads/{BRANCH}",
        "POST graphql",
    ]
    assert mutation_input(log)["branch"]["branchName"] == BRANCH
    assert json.loads(log[1].content) == {"ref": f"refs/heads/{BRANCH}", "sha": BASE}
    assert commit is not None and commit.sha == NEW_COMMIT


@pytest.mark.unit
@pytest.mark.network
def test_an_existing_branch_is_replaced_through_a_temporary_one_and_never_emptied(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D4 -- the whole reason this method is not two API calls.

    The obvious shortcut is to force the release branch back onto ``base`` and commit on it.
    ``@changesets/ghcommit``'s comment says why molt must not: *"if the branch has an existing PR,
    GitHub will auto-close as it sees there's no changes with the base."* molt is more exposed than
    upstream, because reusing one release pull request across days is pinned product behaviour --
    and with "automatically delete head branches" on, an auto-close can take the branch with it and
    the mutation then fails against a ref that no longer exists.

    Two assertions, and the second is the load-bearing one: the ordered log, **and** that no
    request ever points the release branch at ``base``.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_rest_api.set_ref(STALE_HEAD)
    forge_api.set_response(commit_response())

    call(forge)

    assert steps(log) == [
        f"GET ref {BRANCH}",
        f"POST refs refs/heads/{TEMP_BRANCH}",
        "POST graphql",
        f"PATCH refs {BRANCH}",
        f"DELETE refs {TEMP_BRANCH}",
    ]
    assert mutation_input(log)["branch"]["branchName"] == TEMP_BRANCH
    assert json.loads(log[3].content) == {"sha": NEW_COMMIT, "force": True}
    for request in log:
        if request.method in {"PATCH", "POST"} and "/git/ref" in request.url.path:
            body = json.loads(request.content)
            if body.get("ref") == f"refs/heads/{BRANCH}" or request.url.path.endswith(BRANCH):
                assert body.get("sha") != BASE, (
                    "the release branch must never point at its own base, even for an instant"
                )


@pytest.mark.unit
@pytest.mark.network
def test_a_temporary_branch_left_by_a_crashed_run_is_force_updated_not_fatal(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """A create that answers 422 means "the ref is already there", which is the normal state.

    A run killed between creating the temporary branch and deleting it leaves one behind. Treating
    that as fatal would let one crashed run block every release afterwards, with a message about a
    branch the operator has never heard of -- so the create falls through to a forced update.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_rest_api.set_ref(STALE_HEAD)
    forge_rest_api.set_ref_statuses(create=422)
    forge_api.set_response(commit_response())

    commit = call(forge)

    assert steps(log) == [
        f"GET ref {BRANCH}",
        f"POST refs refs/heads/{TEMP_BRANCH}",
        f"PATCH refs {TEMP_BRANCH}",
        "POST graphql",
        f"PATCH refs {BRANCH}",
        f"DELETE refs {TEMP_BRANCH}",
    ]
    assert json.loads(log[2].content) == {"sha": BASE, "force": True}
    assert commit is not None


@pytest.mark.unit
@pytest.mark.network
def test_the_temporary_branch_is_deleted_even_when_the_mutation_fails(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """The ``finally``, asserted rather than intended.

    Cleanup that only runs on the happy path is cleanup that never runs when it matters: the run
    that fails is exactly the run that leaves a ref behind. The failure still propagates -- this
    row asserts both halves, because a ``finally`` that swallowed the error would also pass a
    delete-only assertion.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_rest_api.set_ref(STALE_HEAD)
    forge_api.set_response({"errors": [{"message": "Something went wrong"}]})

    with pytest.raises(Exception) as excinfo:
        call(forge)

    assert_actionable(excinfo.value, "Something went wrong")
    assert steps(log)[-1] == f"DELETE refs {TEMP_BRANCH}"
    assert f"PATCH refs {BRANCH}" not in steps(log), "a failed commit must not move the branch"


@pytest.mark.unit
@pytest.mark.network
def test_a_failure_to_delete_the_temporary_branch_does_not_fail_the_call(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """The commit already landed, so failing here would report a failure that did not happen.

    The next run force-updates the temporary ref rather than creating it, so a survivor is harmless
    -- which is what makes best-effort the right answer rather than a shortcut.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_rest_api.set_ref(STALE_HEAD)
    forge_rest_api.set_ref_statuses(delete=404)
    forge_api.set_response(commit_response())

    commit = call(forge)

    assert commit is not None and commit.sha == NEW_COMMIT
    assert steps(log)[-1] == f"DELETE refs {TEMP_BRANCH}", "the attempt is still made"


@pytest.mark.unit
@pytest.mark.network
def test_no_additions_and_no_deletions_send_no_mutation_at_all(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D5 -- a version script that changed nothing still resets the branch, quietly.

    ``git-cli`` mode has exactly this asymmetry and it is easy to miss: ``run_version`` commits only
    when the tree is dirty, but force-pushes unconditionally. Sending the mutation with an empty
    ``fileChanges`` instead would write an **empty commit** onto the release branch on every no-op
    run, and that branch's diff is a public document.

    ``None`` is molt's established idiom for "nothing was created and that is the right answer" --
    the same one ``create_release`` uses for a duplicate and ``commit_info`` for a missing commit.
    """
    log = shared_log(forge_api, forge_rest_api)
    # The release branch already exists -- the state a repeat run is actually in -- so the create
    # is refused and the force-update is what moves it back onto the base.
    forge_rest_api.set_ref(STALE_HEAD)
    forge_rest_api.set_ref_statuses(create=422)

    assert call(forge, additions={}, deletions=()) is None

    assert all(not request.url.path.endswith("/graphql") for request in log), (
        "an empty change set must not reach the mutation"
    )
    assert steps(log) == [f"POST refs refs/heads/{BRANCH}", f"PATCH refs {BRANCH}"]
    assert json.loads(log[-1].content) == {"sha": BASE, "force": True}


# ======================================================================================
# Rows 13-14: failure semantics specific to this path (design D9)
# ======================================================================================


@pytest.mark.unit
@pytest.mark.network
def test_a_stale_expected_head_is_reported_and_never_retried(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D9 -- ``expectedHeadOid`` refused means the branch moved, which is an answer.

    GitHub reports it as HTTP 200 with an ``errors`` array, so the shared retry budget never sees
    it: a 200 is not a transient status. Retrying past it anyway would commit changes measured
    against a base that is no longer the head -- which is how a release silently discards somebody
    else's work, with no error anywhere.

    The error names the branch and the expected commit, because GitHub's own sentence names
    neither in a form an operator can act on.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_rest_api.set_ref(STALE_HEAD)
    forge_api.set_response(STALE_HEAD_RESPONSE)

    with pytest.raises(Exception) as excinfo:
        call(forge)

    assert_actionable(excinfo.value, TEMP_BRANCH, BASE)
    assert len([r for r in log if r.url.path.endswith("/graphql")]) == 1, (
        "a stale head is an answer, not a blip: exactly one mutation is sent"
    )


@pytest.mark.unit
@pytest.mark.network
def test_the_created_commit_is_reported_with_the_response_sha_and_url(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D8 -- every URL comes off the response, never from concatenating a base.

    The fixture's ``commitUrl`` is on a host no base-URL concatenation of ``api.github.com`` could
    produce, so a backend that built the link itself fails here. That is the rule that makes a
    GitHub Enterprise Server install work with no backend change.
    """
    shared_log(forge_api, forge_rest_api)
    forge_rest_api.set_ref(None)
    forge_api.set_response(commit_response())

    commit = call(forge)

    assert isinstance(commit, CommitRef)
    assert commit.sha == NEW_COMMIT
    assert commit.url == COMMIT_URL
    assert commit.markdown_link == f"[`abcdef1`]({COMMIT_URL})", "the label slices, not the field"


@pytest.mark.unit
@pytest.mark.network
def test_a_branch_that_vanished_between_the_read_and_the_update_is_reported_by_name(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D9 -- a ref that disappears mid-run is reported, not silently recreated.

    This is the "automatically delete head branches" case the temporary branch exists to avoid, and
    it is still reachable if a human deletes the release branch while the run is in flight.
    Recreating it would hide the fact that something else is writing to this repository.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_rest_api.set_ref(STALE_HEAD)
    forge_rest_api.set_ref_statuses(update=422)
    forge_api.set_response(commit_response())

    with pytest.raises(Exception) as excinfo:
        call(forge)

    assert_actionable(excinfo.value, BRANCH)
    assert steps(log)[-1] == f"DELETE refs {TEMP_BRANCH}", "cleanup still happens"


# ======================================================================================
# Rows 15-19: the shared guards apply to this member too
# ======================================================================================


@pytest.mark.unit
@pytest.mark.network
def test_every_request_carries_the_token(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """``Authorization: Token <token>`` on **every** call, refs and mutation alike.

    Asserted per request rather than on the last one: a ref helper that built its own request
    without the header would pass every other row here and then run as an anonymous, heavily
    throttled request in production -- on the one call that writes to a user's repository.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_rest_api.set_ref(STALE_HEAD)
    forge_api.set_response(commit_response())

    call(forge)

    assert len(log) == 5
    for index, request in enumerate(log):
        header = request.headers.get("Authorization") or ""
        scheme, _, value = header.partition(" ")
        assert scheme.lower() == "token", f"request {index}: expected token scheme, got {header!r}"
        assert value == TOKEN, f"request {index}: the token must reach the wire unmodified"


@pytest.mark.unit
@pytest.mark.network
def test_a_missing_token_raises_before_any_request(
    monkeypatch: pytest.MonkeyPatch, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D7 -- no token is a hard, instructive failure, never an anonymous request.

    There is no anonymous form of a write at all, so falling back would surface as a 404 on a
    repository that plainly exists -- after molt had already decided what to commit.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge = build_forge(monkeypatch, token=None)

    with pytest.raises(Exception) as excinfo:
        call(forge)

    assert_actionable(excinfo.value, "GITHUB_TOKEN")
    assert log == [], "no token must mean no request, not an anonymous one"


@pytest.mark.unit
@pytest.mark.network
def test_an_invalid_repo_is_rejected_before_any_request(
    monkeypatch: pytest.MonkeyPatch, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """``utils.ts:1-9`` -- the slug is validated **before** a socket is opened.

    Load-bearing because the slug is interpolated into every ref URL and travels in the mutation's
    own ``repositoryNameWithOwner``: validating afterwards would have leaked the token to whatever
    the interpolation produced.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge = build_forge(monkeypatch, repo="https://github.com/JedWatson/react-select")

    with pytest.raises(Exception) as excinfo:
        call(forge)

    assert_actionable(excinfo.value, "userOrOrg/repoName")
    assert log == [], "a malformed slug must never reach the network"


@pytest.mark.unit
@pytest.mark.network
def test_the_per_call_repo_override_routes_the_refs_and_the_mutation(
    forge: Any, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """Forge design D2 -- ``repo`` is a per-call override, not a second instance.

    Easy to wire into the mutation and forget in the four ref helpers, which would commit to one
    repository and move a branch in another. Both halves are asserted.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge_rest_api.set_ref(STALE_HEAD)
    forge_api.set_response(commit_response())

    call(forge, repo=OTHER_REPO)

    assert mutation_input(log)["branch"]["repositoryNameWithOwner"] == OTHER_REPO
    for request in log:
        if request.url.path.endswith("/graphql"):
            continue
        assert "/repos/JedWatson/react-select/" in str(request.url), str(request.url)
        assert "emotion" not in str(request.url), "the configured repo must not win"


@pytest.mark.unit
@pytest.mark.network
def test_a_transient_mutation_failure_is_retried_and_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared budget covers the commit too (design D3), with the same constants.

    A release commit lost to a single GitHub blip means the release pull request is never updated,
    which the workflow reports as a success with nothing in it. Re-asserting the same ceilings here
    rather than inventing a second set is the point: there is one retry policy.
    """
    with scripted_mutation(
        monkeypatch,
        [
            httpx.Response(500, json={"message": "boom"}),
            httpx.Response(200, json=commit_response()),
        ],
    ) as (forge, route, sleeps):
        commit = call(forge)

    assert route.call_count == 2
    assert commit is not None and commit.sha == NEW_COMMIT
    assert len(sleeps) == 1, "one retry means exactly one nap"
    assert 0 < sleeps[0] <= MAX_SINGLE_SLEEP_SECONDS, "a retry with no wait is a hammer"
    for recorded in route.calls:
        assert recorded.request.headers.get("Authorization") == f"Token {TOKEN}", (
            "every attempt carries the token, retries included"
        )


@pytest.mark.unit
@pytest.mark.network
def test_a_permanent_mutation_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrying a 4xx is a bug, not resilience.

    Without this row "retry everything" would pass the transient row above, and a repository the
    token cannot write to would turn one clean refusal into ``MAX_ATTEMPTS`` of them plus minutes
    of sleeping before the same failure.
    """
    with (
        scripted_mutation(
            monkeypatch, lambda _request: httpx.Response(404, json={"message": "no"})
        ) as (forge, route, sleeps),
        pytest.raises(Exception) as excinfo,
    ):
        call(forge)

    assert route.call_count == 1, "a permanent failure is one request"
    assert route.call_count <= MAX_ATTEMPTS
    assert sleeps == [], "a permanent failure must not sleep at all"
    assert_actionable(excinfo.value, "404")


@pytest.mark.unit
@pytest.mark.network
def test_a_self_hosted_install_routes_the_mutation_and_the_refs_to_their_own_bases(
    monkeypatch: pytest.MonkeyPatch, forge_api: ForgeAPI, forge_rest_api: ForgeRestAPI
) -> None:
    """Design D8 -- GitHub Enterprise Server works with no backend change, on **both** transports.

    A GHES install splits them: ``/api/graphql`` and ``/api/v3``. Every runner exports both
    ``GITHUB_GRAPHQL_URL`` and ``GITHUB_API_URL``, so a backend that honoured one and hardcoded the
    other would half-work -- committing against the enterprise host while creating refs on
    github.com, or the reverse.
    """
    log = shared_log(forge_api, forge_rest_api)
    forge = build_forge(
        monkeypatch, api_url=SELF_HOSTED_API_URL, graphql_url=SELF_HOSTED_GRAPHQL_URL
    )
    forge_rest_api.set_ref(None)
    forge_api.set_response(commit_response())

    call(forge)

    urls = [str(request.url) for request in log]
    assert any(url == SELF_HOSTED_GRAPHQL_URL for url in urls), urls
    assert all(url.startswith((SELF_HOSTED_API_URL, SELF_HOSTED_GRAPHQL_URL)) for url in urls), urls
    assert not any(url.startswith(DEFAULT_REST_BASE) for url in urls), urls


# ======================================================================================
# Row 20: the seam stays implementable by a second backend
# ======================================================================================


COMMIT_SIGNATURE = (
    "branch",
    {
        "base": inspect.Parameter.empty,
        "message": inspect.Parameter.empty,
        "additions": NO_FILE_ADDITIONS,
        "deletions": (),
        "repo": None,
    },
)


@pytest.mark.unit
def test_create_commit_has_the_signature_a_second_backend_must_implement() -> None:
    """Pin the call shape, not just the name -- the ``FR-7`` shape, inspecting **both** owners.

    ``isinstance`` against a ``runtime_checkable`` protocol only checks that an attribute exists,
    so a backend spelling ``create_commit(self, **kwargs)`` would "conform" and then fail at the
    call site. One positional subject -- the branch -- with everything else keyword-only, because
    ``base`` and ``message`` are both strings and a positional pair invites transposing them
    silently.

    The ``additions`` default is asserted to be the **shared** immutable constant, not merely equal
    to an empty mapping: a ``{}`` literal there would be one mutable object shared by every call
    that omits the argument.
    """
    subject, expected_keywords = COMMIT_SIGNATURE
    for owner in (Forge, GitHubForge):
        signature = inspect.signature(owner.create_commit)
        parameters = list(signature.parameters.values())
        assert [p.name for p in parameters[:2]] == ["self", subject], (
            f"{owner.__name__}.create_commit takes {subject} positionally"
        )
        assert parameters[1].kind is not inspect.Parameter.KEYWORD_ONLY
        extra = parameters[2:]
        assert [p.name for p in extra] == list(expected_keywords), f"{owner.__name__}.create_commit"
        for parameter in extra:
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
                f"{owner.__name__}.create_commit's {parameter.name!r} must be keyword-only"
            )
            assert parameter.default == expected_keywords[parameter.name], (
                f"{owner.__name__}.create_commit's {parameter.name!r} default"
            )
        assert signature.parameters["additions"].default is NO_FILE_ADDITIONS, (
            f"{owner.__name__}.create_commit must share the immutable empty-mapping constant"
        )


@pytest.mark.unit
def test_the_new_member_carries_no_host_specific_vocabulary() -> None:
    """The protocol's rule (forge design D1), re-asserted for the member this change adds.

    ``test_github.py``'s vocabulary row is frozen and enumerates the members it knew about, so a
    member named ``create_github_commit`` -- or one that leaked ``graphql`` into its parameter
    names -- would not fail it. Signing is the specific temptation here: it is a property of
    GitHub's implementation, not of the seam.
    """
    forbidden = ("github", "graphql", "octokit", "gitlab", "gitea", "bitbucket", "sign")
    names = ["create_commit", *inspect.signature(Forge.create_commit).parameters]

    for name in names:
        assert not any(token in name.lower() for token in forbidden), name
