"""The forge seam: a host-neutral protocol plus the result shape every backend returns.

Research README section 5 item 7 makes forge-agnosticism a differentiator. changesets is
hard-wired to GitHub, and its "GitLab support?" request (issue #879) collected 35 reactions and
zero maintainer comments in four years because retrofitting the abstraction is a rewrite. molt
therefore ships the seam on day one: :class:`Forge` is what a GitLab, Gitea, Bitbucket or Azure
DevOps backend implements, and ``website/docs/forges/gitlab-gitea-others.md`` promises exactly
that -- "adding GitLab is implementing a backend against a protocol GitHub already satisfies".

Three rules this module exists to keep, each pinned by ``tests/forge/test_github.py``
-------------------------------------------------------------------------------------
* **No host vocabulary on the surface** (design D1). The endpoint is ``api_url``, not
  ``graphql_url``: GitHub's transport is GraphQL and GitLab's is REST, so a protocol member named
  after either forces every other backend to pretend. ``test_the_protocol_surface_carries_no_host
  _specific_vocabulary`` fails on ``github``, ``graphql``, ``octokit``, ``gitlab``, ``gitea`` and
  ``bitbucket`` anywhere in a member name.
* **Signatures, not just names.** ``@runtime_checkable`` checks attribute *existence* only, so
  ``isinstance(x, Forge)`` is close to vacuous on its own -- a backend spelling
  ``commit_info(self, **kwargs)`` would "conform" and then fail at the call site. The suite pins
  the call shape separately, which is what makes this protocol implementable from outside molt.
* **``repo`` is instance state and a per-call override** (design D2). A forge is constructed for
  one repository, but a changelog generator sometimes needs another one, and constructing a second
  client per repository would throw away the cache and the token resolution. The override is
  keyword-only so a backend can add parameters later without breaking callers.

Result shape (``get-commit-info.ts:9-25``, ``get-pull-request-info.ts:9-25``)
-----------------------------------------------------------------------------
Respelled in snake_case per this repo's style, and reached by attribute rather than by mapping
key. ``markdown_link`` is a property rather than a stored string because it is derived: the sha
slice (design D9) has to happen at render time, never on the field, or a changeset written by
``molt add`` -- which records the full 40-character sha -- would lose the sha it recorded.

**Every URL on these objects comes from the API response, never from concatenating a base URL.**
That is what makes ``GITHUB_SERVER_URL`` / ``GITHUB_GRAPHQL_URL`` overrides work for GitHub
Enterprise Server for free (design D8): a backend that built ``https://github.com/{repo}/commit/
{sha}`` itself would pass every other conformance row and fail
``test_a_custom_graphql_url_redirects_the_request_and_the_rendered_links``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from molt.errors import MoltForgeRepoError

__all__ = [
    "SHORT_SHA_LENGTH",
    "AuthorRef",
    "CommitInfo",
    "CommitRef",
    "Forge",
    "PullRef",
    "PullRequestInfo",
    "ReleaseInfo",
    "validate_repo_name",
]

#: How many characters of a sha a rendered link label carries (design D9;
#: ``get-commit-info.ts:63``, ``get-pull-request-info.ts:56``). Fixed rather than git's variable
#: ``--short`` abbreviation, matching :data:`molt.git.SHORT_COMMIT_ID_LENGTH`, so a changelog line
#: renders identically on every machine.
SHORT_SHA_LENGTH = 7

#: ``get-github-info/src/utils.ts:1`` is ``/^[\w.-]+\/[\w.-]+$/``. Transcribed literally into
#: Python that pattern is wrong **twice** (design D3), and both hazards have conformance rows:
#:
#: * ``\w`` is Unicode-aware in Python and ASCII-only in JavaScript, so the literal transcription
#:   accepts a slug containing U+00E9 (LATIN SMALL LETTER E WITH ACUTE) or U+0441 (CYRILLIC SMALL
#:   LETTER ES, a homoglyph of ASCII ``c``) -- slugs GitHub itself cannot name, and a phishing
#:   shape. Hence ``re.ASCII``. The characters are named rather than written: this repo's sources
#:   are ASCII-only because a cp1252 console corrupts anything else.
#: * ``$`` also matches just before a trailing newline in Python, so ``"owner/name\n"`` -- and,
#:   with ``re.MULTILINE`` anywhere in the picture, an injected second line -- would validate.
#:   Hence ``\A``/``\Z``.
_REPO_PATTERN = re.compile(r"\A[\w.-]+/[\w.-]+\Z", re.ASCII)


def validate_repo_name(repo: str) -> None:
    """Raise :class:`~molt.errors.MoltForgeRepoError` unless ``repo`` is ``owner/name``.

    Host-neutral and shared: ``utils.ts:3`` is called by both upstream entry points, and every
    molt backend calls this one before it opens a socket. The rejected value is echoed into the
    message because the usual mistake is pasting a URL or an scp-style remote, and an error that
    only states the expected form leaves the operator guessing which of their variables carries
    the wrong thing.
    """
    if _REPO_PATTERN.match(repo) is None:
        raise MoltForgeRepoError(
            f"Invalid repository name {repo!r}: expected the form userOrOrg/repoName"
        )


@dataclass(frozen=True, slots=True)
class CommitRef:
    """A commit and its host-side URL (``get-commit-info.ts:9-13``)."""

    #: Exactly what the caller asked for on the commit path (``get-commit-info.ts:61`` echoes
    #: ``options.commit``), or ``mergeCommit.abbreviatedOid`` on the pull path
    #: (``get-pull-request-info.ts:54``). Never truncated -- see :attr:`markdown_link`.
    sha: str
    #: ``commitUrl`` as the API returned it.
    url: str

    @property
    def markdown_link(self) -> str:
        """``[`<sha7>`](<url>)`` -- the label slices, the field does not.

        The asymmetry is the point: a full sha on the field keeps ``molt add``'s
        ``git rev-parse HEAD`` value intact for anything that needs to look the commit up again,
        while the changelog reads at the width a human scans.
        """
        return f"[`{self.sha[:SHORT_SHA_LENGTH]}`]({self.url})"


@dataclass(frozen=True, slots=True)
class AuthorRef:
    """The account a change is credited to (``get-commit-info.ts:15-19``)."""

    login: str
    url: str

    @property
    def markdown_link(self) -> str:
        """``[@<login>](<url>)`` -- what a ``Thanks {author}!`` template renders."""
        return f"[@{self.login}]({self.url})"


@dataclass(frozen=True, slots=True)
class PullRef:
    """A pull request -- a merge request, on a host that calls it that."""

    number: int
    url: str

    @property
    def markdown_link(self) -> str:
        """``[#<number>](<url>)``."""
        return f"[#{self.number}]({self.url})"


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """What a commit lookup resolves to (``get-commit-info.ts:9-25``).

    :attr:`commit` is always present -- the lookup found the commit, or the whole result is
    ``None``. The other two are optional and their absence is ordinary: a directly-pushed commit
    has no pull request, and a commit whose email GitHub cannot map to an account (a bot, or
    rewritten history) has no author.
    """

    commit: CommitRef
    pull: PullRef | None = None
    author: AuthorRef | None = None


@dataclass(frozen=True, slots=True)
class PullRequestInfo:
    """What a pull-request lookup resolves to (``get-pull-request-info.ts:9-25``).

    :attr:`pull` is always present. :attr:`commit` is ``None`` for an unmerged pull request and
    :attr:`author` is ``None`` when the account that opened it is gone.
    """

    pull: PullRef
    author: AuthorRef | None = None
    commit: CommitRef | None = None


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    """A release the host now carries (``changesets/action`` v1.9.0 ``run.ts::createRelease``).

    Upstream discards the API response entirely. molt returns it, because the caller that creates
    one release per released package has to be able to report *which* releases it created --
    especially when some of them resolved to ``None`` because the host already had them.

    :attr:`tag` and :attr:`name` echo what the caller asked for, the same way
    :attr:`CommitRef.sha` echoes the caller's sha. :attr:`url` and :attr:`id` come **off the
    response** and are never concatenated from a base URL, which is the rule that makes a GitHub
    Enterprise Server install work with no backend change (design D8).
    """

    #: The tag the release points at, as the caller spelled it.
    tag: str
    #: The release's human-facing title.
    name: str
    #: The release page on the host, as the API returned it.
    url: str
    #: The host's own identifier for the release, as the API returned it.
    id: int


@runtime_checkable
class Forge(Protocol):
    """What molt asks of a code host.

    Structural, not nominal: a third-party backend satisfies this by shape and never has to import
    or subclass anything from molt. The engine, the changelog generators and the CI loop call these
    members and never assume the host is GitHub (``website/docs/forges/overview.md``).
    """

    #: The backend id config selects on, e.g. ``"github"``.
    name: str
    #: The endpoint the backend sends requests to. **Configuration, not a constant** -- this is
    #: the GitHub Enterprise Server escape hatch, and generalized, the reason a second forge is a
    #: config change plus a backend rather than a rewrite.
    api_url: str
    #: The human-facing host, for profile and issue URLs.
    server_url: str
    #: The default ``owner/name`` slug, or ``None`` when nothing is configured.
    repo: str | None

    def validate_repo(self, repo: str) -> None:
        """Raise unless ``repo`` identifies a repository on this host."""
        ...

    def commit_info(self, commit: str, *, repo: str | None = None) -> CommitInfo | None:
        """Resolve a commit to its pull request and author, or ``None`` if it does not exist."""
        ...

    def pull_request_info(self, pull: int, *, repo: str | None = None) -> PullRequestInfo | None:
        """Resolve a pull request to its author and merge commit, or ``None`` if it is absent."""
        ...

    def create_release(
        self,
        tag: str,
        *,
        name: str,
        body: str,
        prerelease: bool = False,
        repo: str | None = None,
    ) -> ReleaseInfo | None:
        """Publish a release for an **existing** tag, or ``None`` if the host already has one.

        Ports ``changesets/action`` v1.9.0 ``run.ts::createRelease``, which sends ``name``,
        ``tag_name``, ``body`` and ``prerelease`` once per released package, after the tag is
        pushed. ``website/docs/forges/overview.md`` ("What a forge backend provides") has promised
        this member since the seam shipped.

        Three parts of the shape are deliberate:

        * **``prerelease`` is passed in, never derived.** A backend must not parse PEP 440 -- that
          is :func:`molt.versioning.is_prerelease`'s job, and a second backend would otherwise
          re-implement it (design D1/D4).
        * **``name`` and ``body`` are keyword-only.** Upstream passes the tag as the name too, so a
          positional pair of strings invites transposing them silently.
        * **A duplicate resolves to ``None``, it does not raise** (design D2). Re-running a
          publish that half-failed is a normal operation: the packages that succeeded already have
          their releases, and failing the whole run on them strands the ones that did not. Same
          idiom as :meth:`commit_info` returning ``None`` for a missing commit. Every *other*
          rejection is raised.
        """
        ...
