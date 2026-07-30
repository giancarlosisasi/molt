r"""The forge-backed changelog generator: commit and pull-request links, credits, issue links.

Ports ``packages/changelog-github/src/index.ts`` (research doc 04 section 5). It is what
``changelog = ["github", { repo = "acme/widgets" }]`` selects, and it is the reason a published
molt changelog reads ``- [#1613](...) [`a085003`](...) Thanks [@Andarist](...)! - Fix the thing``
instead of a bare summary.

Forge-agnostic where it can be
------------------------------
changesets hard-codes GitHub: its generator imports a GitHub client, reads ``GITHUB_*`` itself, and
concatenates ``https://github.com/<repo>/...`` URLs by hand. molt puts :class:`molt.forge.Forge`
underneath instead (research README section 5 item 7), so this module never opens a socket, never
names a host, and never builds an entity URL -- ``markdown_link`` on the forge's result objects
does, from the API response, which is what makes GitHub Enterprise Server work with no change here
(forge design D8).

Two host-shaped URLs are still built locally, and both are unavoidable: an author's profile
(``<server>/<login>``) for an ``author:`` directive, which names a user the forge was never asked
about, and an issue reference (``<server>/<repo>/issues/<n>``) for a number that may not exist at
all. Both are composed from :attr:`molt.forge.Forge.server_url`, so a GHES instance still gets its
own host.

Three properties that are easy to lose, and what each protects
--------------------------------------------------------------
1. **Directives are anchored, never searched for** (design D4). ``pr:``, ``commit:``, ``author:``
   and their synonyms are recognised only at the start of a line. A summary that *discusses* "the
   author of this change" must not silently rewrite who the release is credited to, and a
   substring search is how that happens.

2. **Linkification substitutes through a callable** (design D5; research README section 3.4). A
   summary is author prose that may legitimately contain ``$1``, ``\1``, ``$&`` or ``\g<0>`` -- a
   regex tip, a sed snippet. Those are metacharacters in ``re.sub``'s *replacement* position only,
   so nothing here ever builds one.

3. **A summary is data, never template source** (design D5, and change 11's design D5 before it).
   The summary reaches :func:`molt.changelog.render_template` as a **value** in the render context.
   Interpolating it into the template *text* first would turn every pull request into a
   template-injection vector.

Where this and the ``git`` generator genuinely differ
-----------------------------------------------------
Beyond the links: a dependency bullet here is **one bullet total** whose bracket group lists every
relevant changeset's commit (doc 04 section 5.8), while ``molt.changelog.git`` emits one bullet
**per changeset** (design D8). A one-changeset fixture cannot tell the two apart, which is why the
conformance suite feeds several.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from molt.changelog.template import build_release_line_tokens, render_template
from molt.errors import MoltForgeError, MoltForgeRepoError, MoltTemplateError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from molt.forge import Forge
    from molt.versioning import BumpType

__all__ = ["SHORT_COMMIT_LENGTH", "GitHubChangelogGenerator", "generator"]

#: How many characters of a sha an unlinked reference prints. The same 7 as
#: :data:`molt.forge.SHORT_SHA_LENGTH`, restated for the same reason
#: :data:`molt.changelog.git.SHORT_COMMIT_LENGTH` is: importing the forge's copy costs ``httpx`` at
#: import time, and this module is pure text assembly.
SHORT_COMMIT_LENGTH = 7

#: Two spaces: the markdown continuation indent that keeps a summary's later paragraphs inside the
#: same list item (``index.ts:165-167``).
_CONTINUATION_INDENT = "  "

# The three summary directives (``index.ts:108-120``). Each is anchored with ``^`` under
# ``re.MULTILINE`` so it is recognised only where the documentation puts it -- at the start of a
# line -- and never in the middle of a sentence (design D4). ``[^\s]`` upstream is spelled ``\S``
# here; it is the same class.
#
# The ``pr`` alternation order is upstream's and is kept: ``pull`` is tried before
# ``pull\s+request``, so the engine has to backtrack for the two-word spelling. Python's ``re``
# backtracks the same way, which is why all three spellings share one pattern.
_PR_DIRECTIVE = re.compile(
    r"^\s*(?:pr|pull|pull\s+request):\s*#?(\d+)", re.IGNORECASE | re.MULTILINE
)
_COMMIT_DIRECTIVE = re.compile(r"^\s*commit:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_AUTHOR_DIRECTIVE = re.compile(r"^\s*(?:author|user):\s*@?(\S+)", re.IGNORECASE | re.MULTILINE)

#: ``index.ts:8`` -- ``/\[.*?\]\(.*?\)|\B#([1-9]\d*)\b/g``, transcribed.
#:
#: "Match what you skip, capture what you want." The **left** alternative swallows a whole markdown
#: link so its innards can never be linked again -- that is the only thing keeping
#: ``[fix for #99](https://example.com)`` from growing a nested link -- and only the **right**
#: alternative captures. ``\B`` rejects a ``#`` glued to a word character (``foo#123``),
#: ``[1-9]\d*`` rejects ``#0``, and the trailing ``\b`` keeps ``#42a`` out while letting
#: ``#42.`` through.
_ISSUE_REFERENCE = re.compile(r"\[.*?\]\(.*?\)|\B#([1-9]\d*)\b")


@dataclass(frozen=True, slots=True)
class _Links:
    """The three rendered links a release line may carry, each already markdown or ``None``.

    Rendered rather than structured on purpose: every one of them comes from a
    ``markdown_link`` property on a forge result, so carrying the objects further would invite a
    second place that formats them.
    """

    pull: str | None = None
    commit: str | None = None
    author: str | None = None


class GitHubChangelogGenerator:
    """The ``github`` generator (``changelog-github/src/index.ts:52-190``)."""

    def get_release_line(
        self,
        changeset: Any,
        bump: BumpType,
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        """One bullet for a changeset that released this package.

        The default shape is ``\\n\\n- <pull> <commit> Thanks <authors>! - <summary>\\n``
        (``index.ts:181-187``), with each leading segment dropped when it is absent -- so a
        changeset with no commit and no directives renders the same bare ``- <summary>`` the
        ``git`` generator would. The leading ``\\n\\n`` and trailing ``\\n`` are spacing *hints*
        for :func:`molt.changelog.generate_markdown_for_version_type`'s clamp, not stray
        whitespace: they are the whole reason github bullets end up blank-line separated while git
        bullets sit adjacent (doc 04 section 3.2).

        A ``template`` option replaces that shape for the **first line only**
        (``index.ts:169-179``). Continuation lines keep their two-space indent either way, so a
        template author cannot accidentally template a paragraph away.

        ``bump`` is not read -- the section a bullet lands in is the entry assembler's decision.
        """
        del bump
        host = _require_forge(forge)
        repo = _repo(options, host)

        summary, pull, commit, declared_authors = _read_directives(changeset.summary)
        first, *rest = (line.rstrip() for line in summary.split("\n"))
        links = _resolve_links(
            host, repo, pull=pull, commit=commit, fallback=getattr(changeset, "commit", None)
        )
        authors = _attribution(links.author, declared_authors, options, host.server_url)

        linkify = _linkifier(host.server_url, repo)
        continuations = "\n".join(f"{_CONTINUATION_INDENT}{linkify(line)}" for line in rest)
        template = _template(options)
        if template is not None:
            tokens = build_release_line_tokens(
                summary_linked=linkify(first),
                pull=links.pull,
                commit=links.commit,
                authors=authors,
            )
            # `rstrip` is upstream's `trimEnd` (``index.ts:175-177``) and it is behavior, not
            # tidiness: a token that renders empty at the end of a line leaves a trailing space,
            # and a trailing space in Markdown is a hard line break (design D7). Trimming at
            # render time is what spares template authors a conditional around every optional
            # token.
            return f"{render_template(template, tokens).rstrip()}\n{continuations}"

        prefix = " ".join(
            segment
            for segment in (links.pull, links.commit, f"Thanks {authors}!" if authors else None)
            if segment
        )
        bullet = f"- {prefix} - {linkify(first)}" if prefix else f"- {linkify(first)}"
        return f"\n\n{bullet}\n{continuations}"

    def get_dependency_release_line(
        self,
        changesets: Any,
        dependencies_updated: Any,
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        """One bullet listing the internal dependencies that moved (``index.ts:71-97``).

        Three details separate this from the ``git`` generator's version, and all three are pinned:
        **one** bullet however many changesets are relevant (design D8), a bracket group of commit
        links, and a **trailing colon**.

        The empty string when nothing moved, so
        :func:`molt.changelog.collect_changelog_sections` -- which calls this unconditionally --
        can drop the contribution rather than emit a dangling heading.

        Divergence from upstream, recorded in
        ``tests/changelog/test_deliberately_not_ported.py``: when no relevant changeset carries a
        commit, ``index.ts:79-90`` still writes the brackets and produces the literal
        ``- Updated dependencies []:``. molt drops the empty group. That is the **common** local
        ``molt version`` shape, not a corner case.
        """
        host = _require_forge(forge)
        repo = _repo(options, host)
        updated: Sequence[Any] = list(dependencies_updated)
        if not updated:
            return ""

        marks = [
            _commit_link(host, repo, sha)
            for sha in (getattr(changeset, "commit", None) for changeset in changesets)
            if sha
        ]
        group = f" [{', '.join(marks)}]" if marks else ""
        entries = [
            f"{_CONTINUATION_INDENT}- {dependency.name}@{dependency.new_version}"
            for dependency in updated
        ]
        return "\n".join([f"- Updated dependencies{group}:", *entries])


# ======================================================================================
# Configuration: the forge, the repository, the line template
# ======================================================================================


def _require_forge(forge: object | None) -> Forge:
    """The injected forge, or a refusal naming what is missing.

    Checked first, in both entry points, and deliberately not degraded to "render without links".
    This is the *forge-backed* generator: a project that selected it and silently got the ``git``
    generator's output would discover the missing links in a published changelog. The same
    reasoning as :mod:`molt.forge.github`'s refusal to fall back to an anonymous request.
    """
    if forge is None:
        raise MoltForgeError(
            'The "github" changelog generator needs a forge and none was supplied. Configure a '
            'forge, or use the "git" generator, which needs none'
        )
    return cast("Forge", forge)


def _repo(options: Mapping[str, object] | None, forge: Forge) -> str:
    """The repository this line is about: an explicit option, else the environment (design D3).

    An **empty** ``repo`` option is an error rather than a fallback (``index.ts:54-66``). Silently
    substituting ``GITHUB_REPOSITORY`` for a repository the author explicitly blanked would put
    links to a *different* repository in a published changelog, which is worse than failing.
    """
    if options is not None and "repo" in options:
        repo = options["repo"]
        if not isinstance(repo, str) or not repo:
            raise MoltForgeRepoError(
                'The "github" changelog generator was given an empty "repo" option. Remove the '
                "option to fall back to GITHUB_REPOSITORY, or set it to userOrOrg/repoName"
            )
        return repo
    if not forge.repo:
        raise MoltForgeRepoError(
            'The "github" changelog generator has no repository: set GITHUB_REPOSITORY to '
            'userOrOrg/repoName, or give the generator a "repo" option'
        )
    return forge.repo


def _template(options: Mapping[str, object] | None) -> str | None:
    """The per-line ``template`` option, validated as source text.

    A generator validates its own options (``website/docs/extending/changelog-plugins.md``), and a
    non-string here would otherwise reach Jinja2 and fail with a message about the engine rather
    than about the config key the author actually wrote.
    """
    if options is None:
        return None
    template = options.get("template")
    if template is None or isinstance(template, str):
        return template
    raise MoltTemplateError(
        'The "github" changelog generator\'s "template" option must be a Jinja2 template string, '
        f"not {type(template).__name__}"
    )


# ======================================================================================
# Summary directives (``index.ts:108-120``)
# ======================================================================================


def _read_directives(summary: str) -> tuple[str, int | None, str | None, list[str]]:
    """Strip the directives out of ``summary`` and return what they declared.

    Order is upstream's -- pull request, then commit, then authors -- and each search runs over
    what the previous one left, so a directive line is never counted twice.

    ``pr`` and ``commit`` take the **first** match only; ``author`` is the one global directive,
    because several people may be credited for one change.
    """
    pull_text, summary = _take_first(_PR_DIRECTIVE, summary)
    commit, summary = _take_first(_COMMIT_DIRECTIVE, summary)
    authors, summary = _take_all(_AUTHOR_DIRECTIVE, summary)
    return summary.strip(), int(pull_text) if pull_text is not None else None, commit, authors


def _take_first(pattern: re.Pattern[str], text: str) -> tuple[str | None, str]:
    """The first capture of ``pattern`` in ``text``, and ``text`` with that match cut out."""
    match = pattern.search(text)
    if match is None:
        return None, text
    return match.group(1), text[: match.start()] + text[match.end() :]


def _take_all(pattern: re.Pattern[str], text: str) -> tuple[list[str], str]:
    """Every capture of ``pattern`` in ``text``, and ``text`` with all of them cut out.

    The removal goes through a **callable** replacement like every other substitution in this
    module. An empty replacement string could not misbehave today, but the rule "no replacement
    strings here" is worth more than the one saved lambda: it leaves nothing for a later edit to
    grow a backreference into.
    """
    values = [match.group(1) for match in pattern.finditer(text)]
    if not values:
        return [], text
    return values, pattern.sub(lambda _match: "", text)


def _attribution(
    resolved: str | None,
    declared: Sequence[str],
    options: Mapping[str, object] | None,
    server_url: str,
) -> str | None:
    """Who the line credits: the declared authors, else whoever the forge resolved.

    A declared ``author:`` **replaces** the resolved one rather than joining it
    (``index.ts:151-160``) -- the directive exists precisely for the case where the commit's
    account is wrong, so adding to it would defeat the point. Several declared authors are joined
    with ``", "``.

    ``disable_thanks`` wins over both, and blanks the ``authors`` template token too, so a template
    referencing it does not reintroduce what the option removed.
    """
    if options is not None and options.get("disable_thanks"):
        return None
    if declared:
        return ", ".join(f"[@{name}]({server_url}/{name})" for name in declared)
    return resolved


# ======================================================================================
# Forge lookups (``index.ts:122-160``)
# ======================================================================================


def _resolve_links(
    forge: Forge,
    repo: str,
    *,
    pull: int | None,
    commit: str | None,
    fallback: str | None,
) -> _Links:
    """Resolve whatever the changeset points at into rendered links.

    Precedence (``index.ts:133-149``): a declared **pull request** wins outright and is looked up
    on its own -- the changeset's own commit is not fetched at all, which is what makes a wrong or
    absent ``changeset.commit`` harmless once the author has named the PR. Otherwise a declared
    ``commit:`` beats ``changeset.commit``.

    With neither, nothing is looked up. That is not an optimization: a local ``molt version`` on an
    uncommitted changeset must not fire a speculative request, and the bullet it produces is
    correctly link-free.
    """
    if pull is not None:
        pull_info = forge.pull_request_info(pull, repo=repo)
        links = _Links()
        if pull_info is not None:
            links = _Links(
                pull=pull_info.pull.markdown_link,
                commit=pull_info.commit.markdown_link if pull_info.commit is not None else None,
                author=pull_info.author.markdown_link if pull_info.author is not None else None,
            )
        # Both directives given: the author named a PR *and* pinned a commit, so the pinned one
        # replaces the PR's merge commit. It is resolved through the forge rather than pasted onto
        # a base URL, so a GHES instance still gets its own links (forge design D8).
        return replace(links, commit=_commit_link(forge, repo, commit)) if commit else links

    target = commit or fallback
    if not target:
        return _Links()
    commit_info = forge.commit_info(target, repo=repo)
    if commit_info is None:
        return _Links(commit=_bare_sha(target))
    return _Links(
        pull=commit_info.pull.markdown_link if commit_info.pull is not None else None,
        commit=commit_info.commit.markdown_link,
        author=commit_info.author.markdown_link if commit_info.author is not None else None,
    )


def _commit_link(forge: Forge, repo: str, sha: str) -> str:
    """A commit's markdown link, or a bare code span when the forge knows nothing about it.

    The fallback is doc 04 section 5.8's: the sha the author recorded is still worth printing, and
    an invented ``<server>/<repo>/commit/<sha>`` URL for a commit the API could not find is a link
    that 404s in a published changelog.
    """
    info = forge.commit_info(sha, repo=repo)
    return info.commit.markdown_link if info is not None else _bare_sha(sha)


def _bare_sha(sha: str) -> str:
    """```<sha7>``` -- a code span, no link."""
    return f"`{sha[:SHORT_COMMIT_LENGTH]}`"


# ======================================================================================
# Issue linkification (``index.ts:8, 12-21``)
# ======================================================================================


def _linkifier(server_url: str, repo: str) -> Callable[[str], str]:
    """A one-pass ``#123 -> [#123](<issues>/123)`` substitution for one repository.

    Built per call rather than applied globally so the issues base is resolved once for a whole
    release line instead of once per reference.

    **One pass, callable replacement** (design D5). One pass, because ``re.sub`` walks the subject
    left to right and never rescans what it substituted -- so a reference that has just become a
    markdown link cannot be linked again. Callable, because a replacement *string* would expand
    ``\\1`` and ``\\g<0>`` out of the author's own prose.
    """
    issues = f"{server_url}/{repo}/issues"

    def substitute(match: re.Match[str]) -> str:
        number = match.group(1)
        # No capture means the left alternative matched -- a whole markdown link, returned
        # untouched so nothing nests inside it.
        return match.group(0) if number is None else f"[#{number}]({issues}/{number})"

    return lambda text: _ISSUE_REFERENCE.sub(substitute, text)


#: The instance molt resolves ``changelog = "github"`` to, and the object the
#: ``[project.entry-points."molt.changelog"]`` registration points at. Module-level for the same
#: reason :data:`molt.changelog.git.generator` is: a generator holds no state, and every resolution
#: path looks for the same ``generator`` attribute.
generator = GitHubChangelogGenerator()
