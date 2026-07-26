r"""Conformance tests for molt's two built-in changelog generators.

Two sources, one file:

1. **``packages/changelog-github/src/index.test.ts``** -- all 25 rows of that section of
   ``roadmap/research/test-suite/04-apply-changelog.md`` (15 Port / 10 Adapt / 0 Drop).
   Upstream every one of them is network-mocked via ``vi.mock("@changesets/get-github-info")``;
   here the *transport* is mocked instead (the shared ``forge_api`` respx fixture) so the
   query molt actually sends is itself under test. Mechanics: research doc 04 sections 5.1-5.9.
2. **``packages/changelog-git/src/index.ts``** -- the default generator, which has **no
   upstream test file at all**. Fully specified in research doc 04 sections 3.4 and 3.5;
   authored fresh here.

Upstream's mock data is reproduced verbatim -- commit ``a085003``, author ``Andarist``,
PR ``#1613``, repo ``emotion-js/emotion`` (``index.test.ts:16-31, 101-106``) -- so every
expected string can be diffed against the reference suite line for line.

Forge-agnostic in molt
-----------------------
changesets hard-codes GitHub. molt puts a forge seam under the generator (research README
section 5 item 7: changesets issue #879, 35 reactions, **zero maintainer comments in four
years**, and the only open issue on the dead pychangeset). Practically that means the
generator asks an injected ``Forge`` for attribution instead of importing a GitHub client
(``website/docs/extending/changelog-plugins.md``: "Forge info is injected, not imported"),
and these tests drive a real ``GitHubForge`` whose HTTP transport is stubbed. The
directive-parsing, linkification and line-assembly rules under test are forge-agnostic and
port unchanged; only the URL shapes are GitHub's.

Upstream bugs deliberately NOT ported (research README section 3.4) -- pinned as corrected
--------------------------------------------------------------------------------------------
- **The cache that never hits.** ``get-github-info``'s DataLoader builds a fresh
  ``{...options, kind}`` object per call with no ``cacheKeyFn``, so its map is keyed by
  object identity and every lookup misses (``dataloader.ts:64-72``; research doc 04 section
  5.4). molt keys by ``(kind, repo, id)`` -- ``website/docs/forges/github.md`` section
  "Caching and resilience". Pinned by the three ``*_request*`` cache tests below, including
  the two that prove the key is not degenerate.
- **No retry, no rate-limit handling at all** (research doc 04 section 5.5: "no inspection
  of ``x-ratelimit-*``, no ``Retry-After``, no backoff, no retry, no 5xx handling, no
  timeout"). molt retries transient 5xx with backoff.
- **Function-based regex replacement.** A sha or summary containing ``$``, ``\1`` or
  ``\g<0>`` must not corrupt substituted output; in Python that means an ``re.sub``
  **callable**, never a replacement string.
- **No ``#``-heading stripping.** Upstream's summary post-processing drops every line
  starting with ``#``, destroying Markdown headings inside changeset descriptions. molt
  preserves them.
- **Trailing-space "blank" lines.** ``changelog-git`` indents every continuation line by two
  spaces, so a blank paragraph separator becomes ``"  "`` -- garbage only a prettier pass
  cleans up (research doc 04 section 3.5, note under the worked example). molt emits a clean
  empty line (research README section 5 item 13, "correct changelog markdown").
- **``Content-Type``-less POST.** Upstream sends a JSON body as ``text/plain`` because
  ``fetch`` defaults that way (research doc 04 section 5.5). molt sends
  ``application/json`` (section 5.9).

Fixture divergence worth knowing when diffing against upstream
---------------------------------------------------------------
``index.test.ts:81-99`` builds its changeset from an **indented** heredoc, so upstream's
summaries carry two leading spaces on every continuation line and its inline snapshots show
four-space continuation indents. molt's summaries here are clean, so continuation lines are
indented by exactly the two spaces the generator adds (``index.ts:165-167``).

TDD targets declared by this file (they do not exist yet)
----------------------------------------------------------
- ``molt.changelog.github.generator`` / ``molt.changelog.git.generator`` -- build step 7.
  Each satisfies the ``ChangelogGenerator`` protocol from
  ``website/docs/extending/changelog-plugins.md``::

      get_release_line(changeset, bump, options, forge) -> str
      get_dependency_release_line(changesets, dependencies, options, forge) -> str

  Both are **synchronous**. The protocol permits ``async def`` plugins, but the two built-ins
  are sync (httpx sync client): no async test plugin is in ``[dependency-groups].dev``, and
  CLAUDE.md rates async as rarely needed here. Flagged as an adaptation decision.
- ``molt.forge.GitHubForge()`` -- build step 8. Resolves ``GITHUB_TOKEN``,
  ``GITHUB_REPOSITORY``, ``GITHUB_SERVER_URL`` and ``GITHUB_GRAPHQL_URL`` from the
  environment exactly as ``website/docs/forges/github.md`` documents.
- Entry-point registration under ``[project.entry-points."molt.changelog"]`` with the names
  ``git`` (the default) and ``github`` -- ``website/docs/extending/changelog-plugins.md``.
"""

from __future__ import annotations

import importlib.metadata
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from molt.versioning import BumpType

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from tests.conftest import ForgeAPI

pytest.importorskip(
    "molt.changelog.github",
    reason="build step 7 -- molt.changelog not yet implemented (TDD target)",
)
pytest.importorskip(
    "molt.changelog.git",
    reason="build step 7 -- molt.changelog not yet implemented (TDD target)",
)
pytest.importorskip(
    "molt.forge", reason="build step 8 -- molt.forge not yet implemented (TDD target)"
)

# httpx and respx are imported BELOW the guards on purpose. `tests/conftest.py:77-86` imports
# the httpx stack lazily so an environment without respx gets a readable RuntimeError from the
# fixture rather than a collection-time ImportError; importing them at the top of this module
# would defeat that and turn a missing optional dev dependency into a raw collection error for
# the whole file instead of the SKIP the guards above are there to produce.
import httpx
import respx

# pyrefly: ignore[missing-import]  -- molt.changelog is the TDD target of build step 7.
from molt.changelog.git import generator as git_generator

# pyrefly: ignore[missing-import]  -- molt.changelog is the TDD target of build step 7.
from molt.changelog.github import generator as github_generator

# pyrefly: ignore[missing-import]  -- molt.forge is the TDD target of build step 8.
from molt.forge import GitHubForge

# Markers are applied PER TEST, not module-wide. An earlier revision set
# `pytestmark = pytest.mark.snapshot` here, which tagged all 92 cases in this file -- including
# the 24 pure-unit `changelog-git` cases that touch no snapshot at all -- and
# `roadmap/research/test-suite/MILESTONES.md` selects conformance gates by marker, so `-m
# snapshot` over-selected by ~74. Exactly 7 test functions in this file take the `snapshot`
# fixture (18 cases once the linkify table is expanded); those carry `@pytest.mark.snapshot`.
# The rest carry `network` or `unit`.

# ======================================================================================
# Upstream mock data, verbatim (index.test.ts:16-31, 101-106)
# ======================================================================================

COMMIT_SHA = "a085003"
AUTHOR_LOGIN = "Andarist"
PULL_NUMBER = 1613
REPO = "emotion-js/emotion"
SERVER_URL = "https://github.com"

# A non-routable stand-in for api.github.com; the forge_api fixture matches any `.../graphql`.
GRAPHQL_URL = "https://api.github.invalid/graphql"
TOKEN = "ghp-molt-conformance-token"

COMMIT_URL = f"{SERVER_URL}/{REPO}/commit/{COMMIT_SHA}"
PULL_URL = f"{SERVER_URL}/{REPO}/pull/{PULL_NUMBER}"
AUTHOR_URL = f"{SERVER_URL}/{AUTHOR_LOGIN}"
ISSUES_URL = f"{SERVER_URL}/{REPO}/issues"

COMMIT_LINK = f"[`{COMMIT_SHA}`]({COMMIT_URL})"
PULL_LINK = f"[#{PULL_NUMBER}]({PULL_URL})"
AUTHOR_LINK = f"[@{AUTHOR_LOGIN}]({AUTHOR_URL})"

# index.ts:181-187 -- pull link, commit link, then attribution, space-separated.
DEFAULT_PREFIX = f"{PULL_LINK} {COMMIT_LINK} Thanks {AUTHOR_LINK}!"
OPTIONS: dict[str, Any] = {"repo": REPO}


# ======================================================================================
# Test doubles for the two value objects a generator sees
# ======================================================================================


@dataclass(frozen=True)
class FakeChangeset:
    """The ``NewChangeset & { commit?: string }`` a generator receives (doc 04 section 4.1).

    Field names follow ``website/docs/extending/custom-generators.md``, which reads
    ``changeset.summary``, ``changeset.commit`` and ``changeset.front_matter``.
    """

    id: str
    summary: str
    commit: str | None = None
    releases: tuple[tuple[str, str], ...] = (("pkg", "minor"),)
    front_matter: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FakeDependencyRelease:
    """One entry of ``dependenciesUpdated`` (doc 04 section 4.1).

    ``website/docs/extending/custom-generators.md`` reads ``d.name`` and ``d.new_version``.
    """

    name: str
    new_version: str
    old_version: str = "0.0.1"
    type: str = "patch"


def changeset(summary: str, *, commit: str | None = None, cs_id: str = "some-id") -> FakeChangeset:
    return FakeChangeset(id=cs_id, summary=summary, commit=commit)


# ======================================================================================
# Forge plumbing
# ======================================================================================


def make_forge(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repo: str = REPO,
    token: str = TOKEN,
    server_url: str = SERVER_URL,
) -> Any:
    """Build a ``GitHubForge`` pointed at the stubbed GraphQL endpoint.

    Every knob is an environment variable because that is the documented contract
    (``website/docs/forges/github.md`` section "Configuration"), which in turn ports
    ``get-github-info/src/env.ts:1-34`` (research doc 04 section 5.2).
    """
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.setenv("GITHUB_REPOSITORY", repo)
    monkeypatch.setenv("GITHUB_SERVER_URL", server_url)
    monkeypatch.setenv("GITHUB_GRAPHQL_URL", GRAPHQL_URL)
    return GitHubForge()


def commit_node(sha: str, *, author: str = AUTHOR_LOGIN, pull: int | None = PULL_NUMBER) -> Any:
    """The ``CommitFragment`` payload shape locked by research doc 04 section 5.4."""
    nodes: list[dict[str, Any]] = []
    if pull is not None:
        nodes.append(
            {
                "number": pull,
                "url": f"{SERVER_URL}/{REPO}/pull/{pull}",
                "mergedAt": "2019-11-01T00:00:00Z",
                "author": {"login": author, "url": f"{SERVER_URL}/{author}"},
            }
        )
    return {
        "commitUrl": f"{SERVER_URL}/{REPO}/commit/{sha}",
        "associatedPullRequests": {"nodes": nodes},
        "author": {"user": {"login": author, "url": f"{SERVER_URL}/{author}"}},
    }


def pull_node(number: int, *, author: str = AUTHOR_LOGIN, sha: str = COMMIT_SHA) -> Any:
    """The ``PullFragment`` payload shape locked by research doc 04 section 5.4."""
    return {
        "url": f"{SERVER_URL}/{REPO}/pull/{number}",
        "author": {"login": author, "url": f"{SERVER_URL}/{author}"},
        "mergeCommit": {
            "commitUrl": f"{SERVER_URL}/{REPO}/commit/{sha}",
            "abbreviatedOid": sha,
        },
    }


def forge_payload(
    *,
    commits: Sequence[str] = (COMMIT_SHA,),
    pulls: Sequence[int] = (PULL_NUMBER,),
    commit_found: bool = True,
) -> dict[str, Any]:
    """A GraphQL response carrying every alias a test might legitimately ask for.

    Aliases follow ``repo__<n>`` / ``commit__<sha>`` / ``pull__<number>``
    (``dataloader.ts:94-98, 146-183``; research doc 04 section 5.4). A lookup molt is NOT
    supposed to make (e.g. ``commit__wrongcommit``) finds no alias here, resolves to
    ``None`` like a missing entity upstream (``get-commit-info.ts:40``), and the expected
    release line then fails -- which is exactly how the "wrong commit was fetched" rows
    discriminate.
    """
    block: dict[str, Any] = {}
    for sha in commits:
        block[f"commit__{sha}"] = commit_node(sha) if commit_found else None
    for number in pulls:
        block[f"pull__{number}"] = pull_node(number)
    return {"data": {"repo__0": block}}


def sent_queries(forge_api: ForgeAPI) -> list[str]:
    return [json.loads(request.content).get("query", "") for request in forge_api.requests]


def release_line(first_line: str, *continuation: str, prefix: str | None = DEFAULT_PREFIX) -> str:
    """Assemble the expected ``getReleaseLine`` output (``index.ts:181-187``).

    The leading ``\\n\\n`` and the trailing ``\\n`` are deliberate: they are *hints* the
    newline-clamp in ``get-changelog-entry.ts:122-149`` consumes, and they are why
    ``changelog-github``'s bullets end up blank-line separated while ``changelog-git``'s end
    up adjacent (research doc 04 section 3.2). Do not "tidy" them away.
    """
    head = f"\n\n- {first_line}" if prefix is None else f"\n\n- {prefix} - {first_line}"
    tail = "\n".join(f"  {line}" for line in continuation)
    return f"{head}\n{tail}"


# ======================================================================================
# Rows 1-2 + research doc 04 section 5.1 -- repo resolution
# ======================================================================================


@pytest.mark.network
@pytest.mark.snapshot
def test_repo_falls_back_to_the_environment_when_no_repo_option_is_given(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch, snapshot: Any
) -> None:
    """Row 1 (``index.test.ts:108-115``; ``index.ts:52-68``).

    PORTED MECHANISM, not adapted away: ``website/docs/forges/github.md`` keeps
    ``GITHUB_REPOSITORY`` as the environment slug molt reads, and states the GitHub-flavored
    generator "can take an explicit ``repo`` in its options instead". So the upstream
    precedence rule and its spelling both survive; what molt adds is that the resolution
    happens behind the forge seam rather than inside a GitHub-only generator.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch, repo=REPO)

    line = github_generator.get_release_line(
        changeset("something", commit=COMMIT_SHA), BumpType.MINOR, None, forge
    )

    assert line == release_line("something")
    assert line == snapshot
    assert f'name: "{REPO.split("/")[1]}"' in (forge_api.last_query or "")


@pytest.mark.network
def test_an_explicit_repo_option_beats_the_environment(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 2 (``index.test.ts:117-123``; ``index.ts:54-61``).

    The environment names a different repo, so an implementation that ignored the option
    would produce ``other/repo`` URLs *and* query the wrong repository. Both are asserted --
    checking only the rendered line would let a generator that renders from the option but
    queries from the environment pass.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch, repo="other/repo")

    line = github_generator.get_release_line(
        changeset("something", commit=COMMIT_SHA), BumpType.MINOR, OPTIONS, forge
    )

    assert line == release_line("something")
    query = forge_api.last_query or ""
    assert 'owner: "emotion-js"' in query, "the lookup must target the option's repo"
    assert 'name: "emotion"' in query
    assert "other" not in query, "the environment repo must not leak into the query"


@pytest.mark.network
def test_an_empty_repo_option_is_an_error_rather_than_an_environment_fallback(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Research doc 04 section 5.1, the "subtle" note (``index.ts:54-66``).

    If the options table *has* a ``repo`` key but it is empty, upstream throws instead of
    falling back to ``GITHUB_REPOSITORY``: an explicitly blank repo is a config mistake, not
    an opt-out. Ported deliberately -- silently using a different repo than the one
    configured would put wrong links in a published changelog.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch, repo=REPO)

    with pytest.raises(ValueError) as excinfo:
        github_generator.get_release_line(
            changeset("something", commit=COMMIT_SHA), BumpType.MINOR, {"repo": ""}, forge
        )

    assert "repo" in str(excinfo.value).lower()
    assert forge_api.requests == [], "the guard must fire before any network call"


# ======================================================================================
# Row 3 + research doc 04 section 5.8 -- the dependency release line
# ======================================================================================


@pytest.mark.network
@pytest.mark.snapshot
def test_dependency_release_line_links_the_commit_and_ends_with_a_colon(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch, snapshot: Any
) -> None:
    """Row 3 (``index.test.ts:125-159``; ``index.ts:71-97``).

    Two things separate this from ``changelog-git``'s version (research doc 04 section 5.8):
    **one** bullet regardless of changeset count, and a **trailing colon**. Both are pinned.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)
    dependency = FakeDependencyRelease(name="pkg", new_version="1.0.0")

    line = github_generator.get_dependency_release_line(
        [changeset("something", commit=COMMIT_SHA)], [dependency], OPTIONS, forge
    )

    assert line == f"- Updated dependencies [{COMMIT_LINK}]:\n  - pkg@1.0.0"
    assert line == snapshot


@pytest.mark.network
def test_dependency_release_line_uses_one_bullet_for_many_changesets(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Research doc 04 section 5.8 -- the shape that separates ``github`` from ``git``.

    ``changelog-git`` emits one ``- Updated dependencies`` bullet **per changeset**
    (``changelog-git/src/index.ts:22-27``); ``changelog-github`` emits **one** bullet whose
    bracket group lists every changeset's commit link, comma-separated
    (``index.ts:79-90``). With a single changeset the two shapes are indistinguishable, so
    this second changeset is what actually discriminates them -- a mutation run against a
    reference implementation confirmed that the one-changeset row alone does not.
    """
    other_sha = "b1c2d3e"
    forge_api.set_response(forge_payload(commits=(COMMIT_SHA, other_sha)))
    forge = make_forge(monkeypatch)
    other_link = f"[`{other_sha}`]({SERVER_URL}/{REPO}/commit/{other_sha})"

    line = github_generator.get_dependency_release_line(
        [
            changeset("first", commit=COMMIT_SHA, cs_id="cs-1"),
            changeset("second", commit=other_sha, cs_id="cs-2"),
        ],
        [FakeDependencyRelease(name="pkg", new_version="1.0.0")],
        OPTIONS,
        forge,
    )

    assert line == f"- Updated dependencies [{COMMIT_LINK}, {other_link}]:\n  - pkg@1.0.0"
    assert line.count("- Updated dependencies") == 1, "one bullet, not one per changeset"


@pytest.mark.network
def test_dependency_release_line_is_empty_without_updated_dependencies(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``index.ts:77`` -- no dependencies moved, no line (and so no section contribution)."""
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_dependency_release_line(
        [changeset("something", commit=COMMIT_SHA)], [], OPTIONS, forge
    )

    assert line == ""


@pytest.mark.network
def test_dependency_release_line_falls_back_to_a_bare_code_span(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``index.ts:84`` -- when the forge knows nothing about the commit, no link.

    Research doc 04 section 5.8: "Falls back to ``sha7`` (plain code span, no link) when
    ``getCommitInfo`` returns nothing." A missing entity resolves to ``null`` upstream
    (``get-commit-info.ts:40``), reproduced here with ``commit_found=False``.
    """
    forge_api.set_response(forge_payload(commit_found=False))
    forge = make_forge(monkeypatch)

    line = github_generator.get_dependency_release_line(
        [changeset("something", commit=COMMIT_SHA)],
        [FakeDependencyRelease(name="pkg", new_version="1.0.0")],
        OPTIONS,
        forge,
    )

    assert line == f"- Updated dependencies [`{COMMIT_SHA}`]:\n  - pkg@1.0.0"


@pytest.mark.network
def test_dependency_release_line_omits_empty_brackets_when_no_changeset_has_a_commit(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DIVERGENCE (molt clean markdown, research README section 5 item 13).

    ``index.ts:79-90`` filters commit-less changesets out of the bracket group but keeps the
    brackets, so a changeset with no commit renders the literal ``- Updated dependencies []:``
    (verified against the reference clone). molt drops the empty group instead.

    OWNER SIGN-OFF NEEDED. This is on neither research README section 3.4's bug table nor the
    P4 shared brief section 9 load-bearing list; ``[]:`` is valid Markdown rather than broken
    output, so the case is weaker than the two blank-line divergences it was reasoned from;
    and the no-commit path is the COMMON local ``molt version`` case, not a corner case, so
    this changes the github generator's default output outside CI. The decision is recorded
    as an auditable drop in
    ``tests/changelog/test_deliberately_not_ported.py::test_empty_updated_dependencies_bracket_group_is_dropped``
    -- revert the two together if the owner prefers upstream fidelity.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_dependency_release_line(
        [changeset("something", commit=None)],
        [FakeDependencyRelease(name="pkg", new_version="1.0.0")],
        OPTIONS,
        forge,
    )

    assert line == "- Updated dependencies:\n  - pkg@1.0.0"
    assert forge_api.requests == [], "no commit -> nothing to look up"


# ======================================================================================
# Rows 4-6 -- summary directives. Parsing is forge-agnostic and ports unchanged.
# ======================================================================================

# Row 4 is 18 cases: 3 outer-commit variants x 3 directive spellings x with/without `#`
# (index.test.ts:161-180). The outer commit is deliberately varied -- including a WRONG one --
# because a declared PR must win over whatever `changeset.commit` says (index.ts:133-142).
OUTER_COMMITS: list[str | None] = [COMMIT_SHA, "wrongcommit", None]
PR_KEYWORDS = ["pr", "pull request", "pull"]
HASH_PREFIXES = [("with #", "#"), ("without #", "")]

PR_DIRECTIVE_CASES = [
    (outer, keyword, marker, f"row 4: outer commit {outer!r}, '{keyword}:' {label}")
    for outer in OUTER_COMMITS
    for keyword in PR_KEYWORDS
    for label, marker in HASH_PREFIXES
]


@pytest.mark.network
@pytest.mark.parametrize(("outer_commit", "keyword", "marker", "why"), PR_DIRECTIVE_CASES)
def test_a_pr_directive_overrides_the_changeset_commit(
    outer_commit: str | None,
    keyword: str,
    marker: str,
    why: str,
    forge_api: ForgeAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""Row 4, 18 cases (``index.test.ts:161-180``; ``index.ts:108-112, 133-138``).

    The directive regex is ``^\s*(?:pr|pull|pull\s+request):\s*#?(\d+)`` with ``i``/``m``
    flags, first match only. Note the alternation order: ``pull`` is tried before
    ``pull\s+request``, so the engine must backtrack for the two-word spelling -- Python's
    ``re`` backtracks identically, which is why all three spellings can share one pattern.

    ``assert forge_api.requests`` counts the lookups: a declared PR means exactly one
    ``pull__`` query and **no** commit query, so the ``wrongcommit`` rows would fail loudly
    if the changeset's own commit were fetched anyway.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset(f"something\n{keyword}: {marker}{PULL_NUMBER}", commit=outer_commit),
        BumpType.MINOR,
        OPTIONS,
        forge,
    )

    assert line == release_line("something"), why
    query = forge_api.last_query or ""
    assert f"pull__{PULL_NUMBER}: pullRequest(number: {PULL_NUMBER})" in query, why
    assert "commit__" not in query, f"{why}; a declared PR must not trigger a commit lookup"


@pytest.mark.network
@pytest.mark.parametrize("outer_commit", OUTER_COMMITS)
def test_a_commit_directive_overrides_the_changeset_commit(
    outer_commit: str | None, forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    r"""Row 5, 3 cases (``index.test.ts:181-189``; ``index.ts:113-116, 143-149``).

    ``^\s*commit:\s*([^\s]+)`` with ``i``/``m``, first match only. The declared sha wins over
    ``changeset.commit`` -- the ``wrongcommit`` row is the one that proves it, because the
    stub only knows ``commit__a085003``.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset(f"something\ncommit: {COMMIT_SHA}", commit=outer_commit),
        BumpType.MINOR,
        OPTIONS,
        forge,
    )

    assert line == release_line("something")
    query = forge_api.last_query or ""
    assert f'commit__{COMMIT_SHA}: object(expression: "{COMMIT_SHA}")' in query
    assert "wrongcommit" not in query, "the directive sha wins over the changeset's own"


AUTHOR_DIRECTIVE_CASES = [
    ("author", "@", "row 6: 'author:' with @"),
    ("author", "", "row 6: 'author:' without @ -- the @ is optional in the directive"),
    ("user", "@", "row 6: 'user:' is an accepted synonym of 'author:'"),
    ("user", "", "row 6: 'user:' without @"),
]


@pytest.mark.network
@pytest.mark.parametrize(("keyword", "marker", "why"), AUTHOR_DIRECTIVE_CASES)
def test_an_author_directive_replaces_the_resolved_attribution(
    keyword: str, marker: str, why: str, forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    r"""Row 6, 4 cases (``index.test.ts:193-209``; ``index.ts:117-120, 151-160``).

    ``^\s*(?:author|user):\s*@?([^\s]+)`` is the one directive regex that is **global** --
    several authors may be credited. The declared name replaces the forge-resolved author
    entirely, and the profile URL is built from the server URL, not fetched.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset(f"something\n{keyword}: {marker}other", commit=COMMIT_SHA),
        BumpType.MINOR,
        OPTIONS,
        forge,
    )

    expected_prefix = f"{PULL_LINK} {COMMIT_LINK} Thanks [@other]({SERVER_URL}/other)!"
    assert line == release_line("something", prefix=expected_prefix), why


@pytest.mark.network
@pytest.mark.snapshot
def test_multiple_author_directives_are_joined(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch, snapshot: Any
) -> None:
    """Row 16 (``index.test.ts:317-331``; ``index.ts:151-160``) -- joined with ``", "``."""
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset(
            f"something\nauthor: @{AUTHOR_LOGIN}\nauthor: @mitchellhamilton", commit=COMMIT_SHA
        ),
        BumpType.MINOR,
        OPTIONS,
        forge,
    )

    others = f"{AUTHOR_LINK}, [@mitchellhamilton]({SERVER_URL}/mitchellhamilton)"
    assert line == release_line("something", prefix=f"{PULL_LINK} {COMMIT_LINK} Thanks {others}!")
    assert line == snapshot


@pytest.mark.network
@pytest.mark.snapshot
def test_disable_thanks_removes_the_attribution_segment(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch, snapshot: Any
) -> None:
    """Row 17 (``index.test.ts:333-349``; ``index.ts:151-152``).

    ADAPTED SPELLING: upstream's option is ``disableThanks``; molt's config keys are
    canonically ``snake_case`` with the camelCase spelling accepted as a migration alias
    (``website/docs/config/options.md``), so the generator option is ``disable_thanks``.
    ``website/docs/`` documents no generator option other than ``repo``, so this spelling is
    an adaptation decision, flagged in the run report.

    The links survive -- only the ``Thanks ...!`` segment goes.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset(f"something\nauthor: @{AUTHOR_LOGIN}", commit=COMMIT_SHA),
        BumpType.MINOR,
        {**OPTIONS, "disable_thanks": True},
        forge,
    )

    assert line == release_line("something", prefix=f"{PULL_LINK} {COMMIT_LINK}")
    assert "Thanks" not in line
    assert line == snapshot


# ======================================================================================
# Rows 7-15 -- issue-reference linkification. The load-bearing regex.
# ======================================================================================

# index.ts:8 -- ISSUE_REF_REGEX = /\[.*?\]\(.*?\)|\B#([1-9]\d*)\b/g
#
# "Match what you skip, capture what you want": the LEFT alternative swallows a whole
# markdown link so its innards can never be re-linked, and only the RIGHT alternative
# captures. A naive `\B#([1-9]\d*)\b` alone passes rows 7 and 10-15 and fails 8, 9 and 14 --
# which is exactly why those three rows exist. Research doc 04 section 5.7 step 4.
LINKIFY_CASES = [
    # (summary_tail, expected_line, why)
    (
        "fixes #1234 and #5678",
        f"fixes [#1234]({ISSUES_URL}/1234) and [#5678]({ISSUES_URL}/5678)",
        "row 7 (index.test.ts:211-220): every bare ref on a line, not just the first",
    ),
    (
        f"see [#1234]({ISSUES_URL}/1234)",
        f"see [#1234]({ISSUES_URL}/1234)",
        "row 8 (index.test.ts:222-236): an existing link is swallowed whole -- no nesting",
    ),
    (
        "see [fix for #99](https://example.com)",
        "see [fix for #99](https://example.com)",
        "row 9 (index.test.ts:238-249): a ref inside link TEXT is inside the swallowed match",
    ),
    (
        "foo#123",
        "foo#123",
        r"row 10 (index.test.ts:251-259): \B rejects a # preceded by a word character",
    ),
    (
        "see #0",
        "see #0",
        r"row 11 (index.test.ts:261-269): [1-9]\d* -- there is no issue #0",
    ),
    (
        "#42 was fixed",
        f"[#42]({ISSUES_URL}/42) was fixed",
        r"row 12 (index.test.ts:271-279): at line start \B still holds (# is a non-word char)",
    ),
    (
        "fixed (#99)",
        f"fixed ([#99]({ISSUES_URL}/99))",
        "row 13 (index.test.ts:281-289): punctuation before the # is not a word character",
    ),
    (
        f"fixes [#1]({ISSUES_URL}/1) and #2",
        f"fixes [#1]({ISSUES_URL}/1) and [#2]({ISSUES_URL}/2)",
        "row 14 (index.test.ts:291-305): mixed -- link untouched, bare ref linkified",
    ),
    (
        "this fixes #42.",
        f"this fixes [#42]({ISSUES_URL}/42).",
        r"row 15 (index.test.ts:307-315): \b holds between '2' and '.', so the dot stays out",
    ),
    (
        "#42a is not a ref",
        "#42a is not a ref",
        r"molt-native: \b fails between '2' and 'a', and backtracking to #4 fails too",
    ),
    (
        "v2#7 mentions nothing",
        "v2#7 mentions nothing",
        r"molt-native: \B guard again, this time after a digit",
    ),
    (
        "# Heading stays a heading",
        "# Heading stays a heading",
        "molt-native divergence (research README section 3.4): upstream strips lines starting "
        "with '#'; molt preserves Markdown headings inside a summary",
    ),
]


@pytest.mark.network
@pytest.mark.snapshot
@pytest.mark.parametrize(("tail", "expected", "why"), LINKIFY_CASES)
def test_issue_references_are_linkified_on_continuation_lines(
    tail: str,
    expected: str,
    why: str,
    forge_api: ForgeAPI,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: Any,
) -> None:
    """Rows 7-15 (``index.ts:12-21, 165-167``) applied to a continuation line."""
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset(f"something\n{tail}", commit=COMMIT_SHA), BumpType.MINOR, OPTIONS, forge
    )

    assert line == release_line("something", expected), why
    assert line == snapshot


@pytest.mark.network
@pytest.mark.parametrize(("tail", "expected", "why"), LINKIFY_CASES)
def test_issue_references_are_linkified_on_the_first_line(
    tail: str, expected: str, why: str, forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``index.ts:163`` -- the first line goes through the same linkifier.

    Upstream's whole matrix only ever exercises continuation lines, because its fixture
    hard-codes ``something`` as the first line (``index.test.ts:81-99``). Running the same
    table against the first line catches an implementation that linkifies only the
    continuation (or only the first line) -- a split upstream never tests.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset(tail, commit=COMMIT_SHA), BumpType.MINOR, OPTIONS, forge
    )

    assert line == release_line(expected), why


@pytest.mark.network
def test_regex_replacement_syntax_in_a_summary_survives_linkification(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    r"""Upstream bug NOT ported (research README section 3.4, item "function-based regex").

    ``re.sub`` with a replacement **string** expands ``\1`` and ``\g<0>``; a summary is
    author-controlled prose that may legitimately contain them (a regex tip, a sed snippet).
    molt must substitute through a **callable**, so this payload lands byte-for-byte while
    the ``#42`` beside it is still linkified. ``website/docs/guides/changelog-templates.md``
    promises exactly this: "Your summaries are preserved literally."

    Scope note, from a mutation run against a reference implementation: in Python the
    ``\1``-expansion hazard lives entirely in the **replacement** position, so the test that
    actually discriminates it is
    ``test_render_template_does_not_interpret_regex_replacement_syntax`` in
    ``tests/changelog/test_render_template.py`` (a replacement-string ``render_template``
    fails it). ``re.sub`` never interprets its *subject*, so this test is a regression guard
    on that promise rather than the discriminator -- what it does catch on its own is
    linkification applied to only one of the two lines.
    """
    payload = r"keep \1 and \g<0> and $1 and $& and \\ intact, fixes #42"
    expected = r"keep \1 and \g<0> and $1 and $& and \\ intact, fixes " + f"[#42]({ISSUES_URL}/42)"
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset(f"{payload}\n{payload}", commit=COMMIT_SHA), BumpType.MINOR, OPTIONS, forge
    )

    assert line == release_line(expected, expected), "both lines go through the same sub"


@pytest.mark.network
def test_a_summary_with_no_links_renders_a_bare_bullet(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``index.ts:181-187`` -- "With no links at all: ``- something``" (doc 04 section 5.7).

    No commit and no directives means no lookup at all, so this also pins that molt does not
    fire a speculative request.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(changeset("something"), BumpType.MINOR, OPTIONS, forge)

    assert line == release_line("something", prefix=None)
    assert forge_api.requests == []


# ======================================================================================
# Rows 18-25 -- the per-line `template` option (Jinja2 in molt)
# ======================================================================================

# ADAPTED: changesets' bespoke `{token}` engine (render-template.ts:4) becomes Jinja2
# (research README section 5 item 5), so every template is respelled `{{ token }}`. See
# tests/changelog/test_render_template.py for the engine-level rows. `website/docs/` documents
# the *entry*-structure template but not this per-line one, so the Jinja2 spelling of the four
# row-25 doc-lock templates is an adaptation decision flagged in the run report.
COMPACT_TEMPLATE = "\n- {{ summary }} {{ ref }}"
COMPACT_OPTIONS: dict[str, Any] = {"repo": REPO, "template": COMPACT_TEMPLATE}


@pytest.mark.network
def test_template_renders_the_compact_single_line_form(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 18 (``index.test.ts:357-367``; ``index.ts:169-179``).

    ``{{ ref }}`` resolves to the parenthesized PR reference because a PR beats a commit
    (``render-template.ts:43-47``). The trailing ``\\n`` is the empty continuation.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset("fix the thing", commit=COMMIT_SHA), BumpType.MINOR, COMPACT_OPTIONS, forge
    )

    assert line == f"\n- fix the thing ({PULL_LINK})\n"


@pytest.mark.network
def test_template_keeps_multi_line_summaries_with_indented_continuations(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 19 (``index.test.ts:369-379``; ``index.ts:177-178``).

    Only the FIRST line goes through the template; the rest keep the plain two-space
    continuation indent. A template author cannot accidentally template away a paragraph.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset("first line\nsecond line", commit=COMMIT_SHA),
        BumpType.MINOR,
        COMPACT_OPTIONS,
        forge,
    )

    assert line == f"\n- first line ({PULL_LINK})\n  second line"


@pytest.mark.network
def test_template_omits_attribution_when_no_token_references_it(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 20 (``index.test.ts:381-390``).

    The hard-coded ``Thanks ...!`` segment belongs to the *default* line shape
    (``index.ts:181-187``); once a template is supplied the author decides. Asserted as an
    exact equality as well as the upstream ``not.toContain("Thanks")``, because a
    ``not in`` assertion alone would also pass on an empty string.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset("fix the thing", commit=COMMIT_SHA), BumpType.MINOR, COMPACT_OPTIONS, forge
    )

    assert "Thanks" not in line
    assert AUTHOR_LINK not in line
    assert line == f"\n- fix the thing ({PULL_LINK})\n"


TEMPLATE_ATTRIBUTION_CASES = [
    (
        "\n- {{ summary }} Thanks {{ authors }}!",
        f"\n- fix the thing Thanks {AUTHOR_LINK}!\n",
        "row 21 (index.test.ts:392-407): literal text plus the authors token",
    ),
    (
        "\n- {{ summary }} {{ authors }}",
        f"\n- fix the thing {AUTHOR_LINK}\n",
        "row 22 (index.test.ts:409-422): the authors token bare, no 'Thanks' anywhere",
    ),
]


@pytest.mark.network
@pytest.mark.parametrize(("template", "expected", "why"), TEMPLATE_ATTRIBUTION_CASES)
def test_template_renders_attribution_from_the_authors_token(
    template: str,
    expected: str,
    why: str,
    forge_api: ForgeAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows 21-22 -- attribution is opt-in via ``{{ authors }}``."""
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset("fix the thing", commit=COMMIT_SHA),
        BumpType.MINOR,
        {**OPTIONS, "template": template},
        forge,
    )

    assert line == expected, why


@pytest.mark.network
def test_template_trims_a_trailing_space_left_by_an_empty_token(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 23 (``index.test.ts:424-437``; ``index.ts:175-177``).

    With no commit and no directives there is no PR and no commit, so ``{{ ref }}`` renders
    empty (``render-template.ts:43-47``) and the space before it would dangle. A trailing
    space in Markdown is a hard line break, so the rendered line is ``trimEnd``-ed. This is
    also the only template row that makes no network call.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset("fix the thing", commit=None), BumpType.MINOR, COMPACT_OPTIONS, forge
    )

    assert line == "\n- fix the thing\n"
    assert forge_api.requests == []


@pytest.mark.network
def test_template_summary_token_is_pre_linkified(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 24 (``index.test.ts:440-460``; ``index.ts:163, 170-174``).

    ``summaryLinked`` -- not the raw summary -- is what reaches the template, so a template
    author gets linkification for free and cannot lose it by omitting a token.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset("did a thing (fixes #99) and also #1234", commit=COMMIT_SHA),
        BumpType.MINOR,
        {**OPTIONS, "template": "\n- {{ summary }}"},
        forge,
    )

    assert line == (
        f"\n- did a thing (fixes [#99]({ISSUES_URL}/99)) and also [#1234]({ISSUES_URL}/1234)\n"
    )


DOCUMENTED_TEMPLATE_CASES = [
    (
        "\n- {{ pull }} {{ commit }} Thanks {{ authors }}! - {{ summary }}",
        f"\n- {PULL_LINK} {COMMIT_LINK} Thanks {AUTHOR_LINK}! - fix the thing\n",
        "row 25a: the default line shape, spelled as a template",
    ),
    (
        "\n- {{ summary }} {{ ref }}",
        f"\n- fix the thing ({PULL_LINK})\n",
        "row 25b: the compact form -- summary then a parenthesized reference. Byte-identical "
        "to COMPACT_TEMPLATE above, and that duplication is FAITHFUL, not an oversight: "
        "upstream repeats the same template in its doc-lock block (index.test.ts:475) that it "
        "already exercised as the standalone compact-form row (index.test.ts:354). Kept so "
        "this table stays a line-for-line mirror of the documentation lock",
    ),
    (
        "\n- {{ summary }} (thanks {{ authors }}!)",
        f"\n- fix the thing (thanks {AUTHOR_LINK}!)\n",
        "row 25c: attribution folded into the author's own parenthetical",
    ),
    (
        "\n- {{ summary }} {{ pull }}",
        f"\n- fix the thing {PULL_LINK}\n",
        "row 25d: the bare pull token, unparenthesized (contrast 25b's {{ ref }})",
    ),
]


@pytest.mark.network
@pytest.mark.parametrize(("template", "expected", "why"), DOCUMENTED_TEMPLATE_CASES)
def test_documented_template_examples_render_exactly(
    template: str,
    expected: str,
    why: str,
    forge_api: ForgeAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row 25, 4 cases (``index.test.ts:462-492``) -- a documentation lock.

    CONFLICT, flagged for the owner: the brief asks for these four to be taken from
    ``website/docs/``, but no molt docs page documents the per-line ``template`` option --
    ``guides/changelog-templates.md`` documents the *entry*-structure template instead. The
    four upstream examples are therefore carried over, respelled in Jinja2. When the docs
    page lands, this table is the thing it must agree with.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset("fix the thing", commit=COMMIT_SHA),
        BumpType.MINOR,
        {**OPTIONS, "template": template},
        forge,
    )

    assert line == expected, why


@pytest.mark.network
def test_a_summary_is_data_not_a_template(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    r"""molt-native, adversarial: Jinja2 must render exactly once.

    A changeset summary is untrusted author prose. If it were substituted into the template
    string before rendering (or rendered a second time), ``{{ 7*7 }}`` would become ``49``
    and a changeset would be a template-injection vector. Companion to the ``\1`` payload
    test above: same class of bug, other engine.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    line = github_generator.get_release_line(
        changeset("fix {{ 7*7 }} and {% raw %}x{% endraw %}", commit=COMMIT_SHA),
        BumpType.MINOR,
        {**OPTIONS, "template": "\n- {{ summary }}"},
        forge,
    )

    assert line == "\n- fix {{ 7*7 }} and {% raw %}x{% endraw %}\n"


# ======================================================================================
# Transport contract -- the outgoing GraphQL query and the Authorization header
# ======================================================================================


@pytest.mark.network
@pytest.mark.snapshot
def test_commit_lookup_sends_the_documented_graphql_query(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch, snapshot: Any
) -> None:
    """Research doc 04 section 5.4 -- the query locked upstream by
    ``get-commit-info.test.ts:153-187``.

    Snapshotted *and* asserted piecewise, in that order deliberately. The piecewise
    assertions run FIRST and the ``== snapshot`` comparison runs LAST, because
    ``--snapshot-update`` mints a baseline from whatever the implementation happens to emit.
    With the snapshot first, the very run that creates the baseline never reaches the
    discriminating checks -- an unreviewed query gets frozen as "correct" and every later run
    then compares it against itself. Ordering the fragments first means a wrong query fails
    the update run too, so no baseline is recorded until the load-bearing parts are right:
    the ``repo__0`` / ``commit__<sha>`` aliasing (which is how batching works at all) and
    ``associatedPullRequests(first: 50)`` (the PR set attribution is chosen from).
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    github_generator.get_release_line(
        changeset("something", commit=COMMIT_SHA), BumpType.MINOR, OPTIONS, forge
    )

    query = forge_api.last_query or ""
    assert "repo__0: repository(" in query
    assert 'owner: "emotion-js"' in query
    assert 'name: "emotion"' in query
    assert f'commit__{COMMIT_SHA}: object(expression: "{COMMIT_SHA}")' in query
    assert "fragment CommitFragment on Commit" in query
    assert "associatedPullRequests(first: 50)" in query
    assert query == snapshot


@pytest.mark.network
@pytest.mark.snapshot
def test_pull_request_lookup_sends_the_documented_graphql_query(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch, snapshot: Any
) -> None:
    """Research doc 04 section 5.4 -- locked upstream by
    ``get-pull-request-info.test.ts:103-126``.

    Same ordering rule as the commit-query test above: the piecewise ``in`` checks come
    before ``== snapshot`` so that a ``--snapshot-update`` run cannot freeze an unreviewed
    query as the baseline.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    github_generator.get_release_line(
        changeset(f"something\npr: #{PULL_NUMBER}", commit=None), BumpType.MINOR, OPTIONS, forge
    )

    query = forge_api.last_query or ""
    assert f"pull__{PULL_NUMBER}: pullRequest(number: {PULL_NUMBER})" in query
    assert "fragment PullFragment on PullRequest" in query
    assert "mergeCommit" in query
    assert query == snapshot


@pytest.mark.network
def test_the_forge_authenticates_with_the_github_token_scheme(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dataloader.ts:100-138`` (research doc 04 sections 5.3, 5.5, 5.9).

    Upstream sends ``Authorization: Token <GITHUB_TOKEN>`` and, because ``fetch`` defaults a
    string body to ``text/plain``, **no** ``Content-Type``. molt keeps the ``token`` scheme
    (``website/docs/forges/github.md``: "sent as a GitHub ``Authorization`` token") but sends
    a correct ``application/json`` content type -- the section 5.9 recommendation.

    The scheme is compared case-insensitively on purpose: HTTP auth schemes are
    case-insensitive per RFC 7235, so ``Token`` vs ``token`` is not a behavior. The token
    value is compared exactly, and ``bearer`` is excluded explicitly, so the assertion still
    fails for every way this can actually be wrong.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)

    github_generator.get_release_line(
        changeset("something", commit=COMMIT_SHA), BumpType.MINOR, OPTIONS, forge
    )

    header = forge_api.auth_header() or ""
    scheme, _, value = header.partition(" ")
    assert scheme.lower() == "token", f"expected the GitHub token scheme, got {header!r}"
    assert scheme.lower() != "bearer"
    assert value == TOKEN, "the GITHUB_TOKEN value must reach the wire unmodified"

    content_type = forge_api.requests[-1].headers.get("Content-Type", "")
    assert content_type.startswith("application/json"), (
        "research doc 04 section 5.9: molt sends a correct content type where fetch did not"
    )


# ======================================================================================
# Corrected upstream bugs -- caching and retry (research README section 3.4)
# ======================================================================================


@pytest.mark.network
def test_the_same_commit_is_fetched_once(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream bug NOT ported: the identity-keyed DataLoader cache (doc 04 section 5.4).

    ``loadCommitData`` builds a fresh ``{...options, kind}`` object per call and configures
    no ``cacheKeyFn``, so DataLoader's map is keyed by object identity and **never hits**.
    molt keys by ``(kind, repo, id)`` (``website/docs/forges/github.md``), so two lookups of
    one commit are one request. Both results are compared too: a "cache" that returns a
    different answer the second time is worse than no cache.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)
    cs = changeset("something", commit=COMMIT_SHA)

    first = github_generator.get_release_line(cs, BumpType.MINOR, OPTIONS, forge)
    second = github_generator.get_release_line(cs, BumpType.MINOR, OPTIONS, forge)

    assert first == second == release_line("something")
    assert len(forge_api.requests) == 1, "one distinct commit -> exactly one HTTP call"


@pytest.mark.network
def test_two_distinct_commits_are_fetched_separately(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anti-vacuity guard for the cache test above.

    A cache keyed on a constant would also pass "same commit -> one request", and would then
    happily attribute one commit's PR to another. Two different shas must be two lookups.
    """
    other_sha = "b1c2d3e"
    forge_api.set_response(forge_payload(commits=(COMMIT_SHA, other_sha)))
    forge = make_forge(monkeypatch)

    github_generator.get_release_line(
        changeset("something", commit=COMMIT_SHA), BumpType.MINOR, OPTIONS, forge
    )
    github_generator.get_release_line(
        changeset("something else", commit=other_sha), BumpType.MINOR, OPTIONS, forge
    )

    assert len(forge_api.requests) == 2
    queries = sent_queries(forge_api)
    assert f"commit__{COMMIT_SHA}" in queries[0]
    assert f"commit__{other_sha}" in queries[1]


@pytest.mark.network
def test_the_cache_key_includes_the_repository(
    forge_api: ForgeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anti-vacuity guard: ``(kind, repo, id)``, not ``(kind, id)``.

    Shas are repo-scoped; a cache that ignored the repo would serve one repository's commit
    metadata for another's -- silently wrong links in a published changelog, and the exact
    failure a monorepo with vendored history would hit first.
    """
    forge_api.set_response(forge_payload())
    forge = make_forge(monkeypatch)
    cs = changeset("something", commit=COMMIT_SHA)

    github_generator.get_release_line(cs, BumpType.MINOR, {"repo": REPO}, forge)
    github_generator.get_release_line(cs, BumpType.MINOR, {"repo": "other/repo"}, forge)

    assert len(forge_api.requests) == 2
    queries = sent_queries(forge_api)
    assert 'name: "emotion"' in queries[0]
    assert 'name: "repo"' in queries[1]


@pytest.mark.network
def test_a_transient_forge_failure_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Upstream bug NOT ported: zero retry and zero rate-limit handling (doc 04 section 5.5).

    Upstream has "no inspection of ``x-ratelimit-*``, no ``Retry-After``, no backoff, no
    retry, no 5xx handling, no timeout"; a 503 mid-release simply fails the run.
    ``website/docs/forges/github.md`` promises molt "retries transient 5xx responses with
    jittered backoff".

    This test drives respx directly rather than through ``forge_api`` because the shared
    fixture serves one fixed response and a retry test needs a *sequence*. ``time.sleep`` is
    neutralized so the backoff does not slow the suite -- tenacity's default nap and a
    hand-rolled loop both go through it.
    """
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__regex=r".+/graphql/?$").mock(
            side_effect=[
                httpx.Response(503, json={"message": "Service Unavailable"}),
                httpx.Response(200, json=forge_payload()),
            ]
        )
        forge = make_forge(monkeypatch)
        line = github_generator.get_release_line(
            changeset("something", commit=COMMIT_SHA), BumpType.MINOR, OPTIONS, forge
        )

    assert route.call_count == 2, "the 503 must be retried, not surfaced"
    assert line == release_line("something")


# ======================================================================================
# changelog-git -- the DEFAULT generator. No upstream test file; doc 04 sections 3.4-3.5.
# ======================================================================================

GIT_RELEASE_LINE_CASES = [
    # (summary, commit, expected, why)
    (
        "Hey, let's have fun with testing!",
        "a085003c0ffeebabe",
        "- a085003: Hey, let's have fun with testing!",
        "changelog-git/src/index.ts:9-11 -- the sha is sliced to 7 and suffixed with ': '",
    ),
    (
        "Hey, let's have fun with testing!",
        None,
        "- Hey, let's have fun with testing!",
        "changelog-git/src/index.ts:10 -- no commit means the prefix vanishes entirely, not "
        "an empty ': ' -- the case a repo with no git history hits",
    ),
    (
        "first line\nsecond line\nthird line",
        None,
        "- first line\n  second line\n  third line",
        "changelog-git/src/index.ts:13-15 -- continuations indented by exactly two spaces",
    ),
    (
        "first line   \nsecond line\t",
        None,
        "- first line\n  second line",
        "changelog-git/src/index.ts:5-7 -- every line is trimEnd()ed before indenting",
    ),
    (
        "Random stuff\n\nget it while it's hot!",
        None,
        "- Random stuff\n\n  get it while it's hot!",
        "DIVERGENCE (research README section 3.4 / section 5 item 13): upstream indents the "
        "blank separator too, emitting a trailing-space line '  ' that only prettier cleans "
        "up (doc 04 section 3.5). molt emits a clean empty line",
    ),
    (
        r"a $1 and \g<0> and #42 survive",
        "a085003",
        r"- a085003: a $1 and \g<0> and #42 survive",
        "molt-native: the default generator neither linkifies nor regex-substitutes, so an "
        "author's literal text is untouched (research README section 3.4)",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("summary", "commit", "expected", "why"), GIT_RELEASE_LINE_CASES)
def test_git_generator_release_line(
    summary: str, commit: str | None, expected: str, why: str
) -> None:
    """``changelog-git/src/index.ts:4-18`` (research doc 04 section 3.4).

    Note what is NOT here: no leading ``\\n\\n`` and no trailing ``\\n``. That asymmetry with
    ``changelog-github`` is deliberate and load-bearing -- fed through the newline clamp
    (doc 04 section 3.2) it is precisely why git-generated bullets sit adjacent while
    github-generated bullets are blank-line separated. Do not "harmonize" them.
    """
    line = git_generator.get_release_line(
        changeset(summary, commit=commit), BumpType.PATCH, None, None
    )
    assert line == expected, why


GIT_DEPENDENCY_LINE_CASES = [
    # (commits, dependencies, expected, why)
    (
        [None],
        [("pkg-b", "1.2.1")],
        "- Updated dependencies\n  - pkg-b@1.2.1",
        "doc 04 section 3.4 -- the common no-commit case; the nested list is indented 2sp",
    ),
    (
        ["abc1234def"],
        [("pkg-b", "1.2.1")],
        "- Updated dependencies [abc1234]\n  - pkg-b@1.2.1",
        "changelog-git/src/index.ts:22-27 -- the sha is bracketed and sliced to 7",
    ),
    (
        ["abc1234def", "def5678abc"],
        [("pkg-b", "1.2.1"), ("pkg-c", "2.0.1")],
        (
            "- Updated dependencies [abc1234]\n"
            "- Updated dependencies [def5678]\n"
            "  - pkg-b@1.2.1\n"
            "  - pkg-c@2.0.1"
        ),
        "doc 04 section 3.4 -- ONE bullet PER CHANGESET, then the dependency list once. "
        "Contrast changelog-github (section 5.8): one bullet total, plus a trailing colon",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("commits", "dependencies", "expected", "why"), GIT_DEPENDENCY_LINE_CASES)
def test_git_generator_dependency_release_line(
    commits: list[str | None],
    dependencies: list[tuple[str, str]],
    expected: str,
    why: str,
) -> None:
    """``changelog-git/src/index.ts:19-34`` (research doc 04 section 3.4)."""
    changesets = [
        changeset("dep bump", commit=commit, cs_id=f"cs-{index}")
        for index, commit in enumerate(commits)
    ]
    updated = [
        FakeDependencyRelease(name=name, new_version=version) for name, version in dependencies
    ]

    line = git_generator.get_dependency_release_line(changesets, updated, None, None)

    assert line == expected, why


@pytest.mark.unit
def test_git_generator_dependency_release_line_is_empty_without_updates() -> None:
    """``changelog-git/src/index.ts:20`` -- an empty list yields ``""``, so the assembler's
    filter drops the contribution rather than emitting a dangling heading."""
    line = git_generator.get_dependency_release_line(
        [changeset("dep bump", commit="abc1234")], [], None, None
    )
    assert line == ""


@pytest.mark.unit
def test_git_generator_needs_no_forge() -> None:
    """``website/docs/extending/changelog-plugins.md`` -- ``git`` is the default precisely
    because it "works with no configuration and no network". Passing ``forge=None`` must be
    a supported call, not an accident that happens to work.
    """
    assert git_generator.get_release_line(changeset("x"), BumpType.PATCH, None, None) == "- x"
    assert (
        git_generator.get_dependency_release_line(
            [changeset("x")], [FakeDependencyRelease(name="p", new_version="1.0.0")], None, None
        )
        == "- Updated dependencies\n  - p@1.0.0"
    )


# ======================================================================================
# Plugin resolution -- the entry-point seam (research doc 04 section 4.4)
# ======================================================================================


@pytest.mark.unit
def test_the_built_in_generators_are_registered_as_entry_points() -> None:
    """``website/docs/extending/changelog-plugins.md``: "molt's own ``git`` and ``github``
    generators are registered exactly this way, so there is no privileged built-in path."

    Upstream's default config value is the string ``"@changesets/cli/changelog"`` -- a
    one-line re-export of ``changelog-git`` (research doc 04 section 3.4). molt's analogue is
    the entry-point name ``git`` in the ``molt.changelog`` group, which is what makes a
    third-party generator a first-class peer of the defaults.

    CONFLICT, flagged for the owner: ``website/docs/config/options.md`` line 88 shows
    ``changelog = ["molt.changelog.github", { repo = "acme/acme" }]`` (a dotted path) while
    ``extending/changelog-plugins.md`` shows ``changelog = ["github", ...]`` (the entry-point
    name) and calls ``git`` the default. Both resolution forms are documented as supported;
    this test pins the entry-point *registration*, which both forms depend on.
    """
    entry_points = importlib.metadata.entry_points(group="molt.changelog")
    names = {entry_point.name for entry_point in entry_points}

    assert {"git", "github"} <= names, (
        f"expected the two built-ins to be registered, got {names}. "
        "NOTE: importlib.metadata reads INSTALLED distribution metadata, not pyproject.toml, "
        'so adding [project.entry-points."molt.changelog"] does NOT turn this green on its '
        "own -- the editable install has to be rebuilt (`uv sync --reinstall`, or "
        "`uv sync --reinstall-package molt-cli`) before the new entry points appear."
    )
    for name, expected in (("git", git_generator), ("github", github_generator)):
        loaded = entry_points[name].load()
        assert loaded is expected, f"the {name!r} entry point must resolve to the built-in object"
        assert callable(loaded.get_release_line)
        assert callable(loaded.get_dependency_release_line)
