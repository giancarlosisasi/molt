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
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from molt.errors import MoltForgeRepoError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "NO_FILE_ADDITIONS",
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

#: The default ``additions`` for :meth:`Forge.create_commit` -- an **immutable** empty mapping, not
#: a ``{}`` literal. A mutable default is shared by every call that omits the argument, so a
#: backend that ever wrote into it would corrupt the next caller's commit; a
#: :class:`~types.MappingProxyType` makes that unrepresentable rather than a bug to find later.
#: It is a module-level constant so :class:`Forge` and every backend can share the *same* default
#: object, which is what lets a signature row compare the two defaults for equality.
NO_FILE_ADDITIONS: Mapping[str, bytes] = MappingProxyType({})

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

    def find_open_pull_request(
        self, head: str, *, base: str, repo: str | None = None
    ) -> PullRef | None:
        """The **open** pull request from ``head`` onto ``base``, or ``None`` if there is none.

        Ports ``changesets/action`` v1.9.0 ``run.ts:347-378``, whose ``octokit.rest.pulls.list``
        filters on ``state: "open"`` and takes the first result. The release pull request is looked
        up rather than remembered because molt keeps no state between runs: the branch name is the
        only identifier, which is why :data:`molt.action.VERSION_BRANCH_PREFIX` is fixed rather
        than configurable.

        Absence is a value, exactly as it is for :meth:`commit_info`: "no release pull request yet"
        is the ordinary first run, not a failure.
        """
        ...

    def create_pull_request(
        self, head: str, *, base: str, title: str, body: str, repo: str | None = None
    ) -> PullRef:
        """Open a pull request from ``head`` onto ``base`` (``run.ts:363-370``).

        ``head`` is positional because it is the subject; everything else is keyword-only, so a
        caller cannot transpose ``title`` and ``body`` -- two strings whose order no type checker
        can police.
        """
        ...

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
        """Put ``additions`` and ``deletions`` on ``branch``, on top of ``base``, host-side.

        Ports ``changesets/action`` v1.9.0 ``src/git.ts::pushChanges`` -- which, in its
        ``commitMode: github-api`` arm, replaces ``git add .`` + ``git commit`` + ``git push
        --force`` with one ``commitChangesFromRepo({..., base: {commit: context.sha}, force:
        true})`` call into ``@changesets/ghcommit``. molt keeps the whole choreography that library
        performs -- create the branch when it is absent, replace it without ever emptying it, clean
        up afterwards -- **inside the backend**, so the protocol stays one method.

        ``branch`` ends carrying exactly one commit on top of ``base``, whatever it carried before.
        ``base`` is the commit the caller measured its changes against; a host that supports it
        SHOULD refuse the write when the branch has moved since (see "a branch that moved under the
        caller is reported, not retried" in the ``forge-seam`` spec).

        **This member does not promise a signature** (design D1). It says "create a commit on a
        branch through the host's API"; whether the host signs what it authors is a property of that
        host's implementation, documented on the backend and in
        ``website/docs/forges/github.md``. A member called ``sign_commit`` would be a GitHub-shaped
        hole in a host-neutral protocol -- and every candidate backend
        (``website/docs/forges/gitlab-gitea-others.md``) has a multi-file commit endpoint that fits
        behind this shape: GitLab ``POST /projects/:id/repository/commits`` with ``actions[]``,
        Gitea/Forgejo ``POST /repos/{owner}/{repo}/contents``, Bitbucket
        ``POST /2.0/repositories/{workspace}/{repo}/src``.

        ``None`` means **no commit was needed** -- there was nothing to commit -- not that
        something failed. Same idiom as :meth:`create_release` returning ``None`` for a release the
        host already has and :meth:`commit_info` returning ``None`` for a commit that is not there.

        Shape notes, each deliberate (design D3):

        * ``additions`` maps a repository-relative POSIX path to the file's **bytes**. Bytes, not
          text: the encoding a host wants on the wire is that host's business, and decoding to
          ``str`` at the seam would corrupt a file that is not UTF-8 and silently translate CRLF
          on a Windows checkout. A **mapping**, not a list of records, so "the same path twice" is
          unrepresentable rather than a silent last-one-wins on the host.
        * ``branch`` is the only positional argument. ``base`` and ``message`` are two strings and
          a positional pair invites transposing them where no type checker can help.
        * Required, not optional (owner ruling 2026-07-31/2026-08-01, session 6, on the same terms
          it ruled :meth:`create_release` required -- ``openspec/GAPS.md`` ``FR-6``). molt's action
          commits, so a backend that cannot serve the action is not a complete backend.
        """
        ...

    def update_pull_request(
        self, number: int, *, title: str, body: str, repo: str | None = None
    ) -> PullRef:
        """Replace an existing pull request's title and body, and make sure it is open.

        Ports ``run.ts:391-407``. Reusing the pull request rather than opening a second one is the
        product behavior: the release pull request accumulates changesets over days, and its
        number, its review comments and its subscribers have to survive every update.

        **Re-opening is part of the contract, not a detail.** Upstream's GraphQL mutation sends
        ``state: OPEN`` alongside the title and body because a force-push onto the release branch
        can close the pull request; the next run must bring that same one back rather than orphan
        its history.
        """
        ...
