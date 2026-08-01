"""The release pull request's body, and the truncation that keeps the host from rejecting it.

Ports ``changesets/action`` v1.9.0 ``src/run.ts:189-243`` (``getVersionPrBody``) and the
``changedPackagesInfo`` shape it consumes (``run.ts:333-344``). Pure string work: no filesystem, no
git, no host access, so the part of the loop most likely to be subtly wrong is the cheapest to
test -- the same split :mod:`molt.action.run` and :mod:`molt.action.utils` already use.

Why the truncation is not optional
-----------------------------------
GitHub rejects a pull-request body over 65536 characters. Without the degradation a large monorepo
release opens **no pull request at all**: the API refuses the body and the whole version phase
fails, on exactly the release that matters most. The three tiers drop the changelog content first,
then the per-package headers, keeping the explanatory header and the ``# Releases`` heading in
every case so the reader always learns what the pull request is and why it looks empty.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from molt.action.utils import sort_changelog_entries

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "DEFAULT_PR_TITLE",
    "MAX_BODY_CHARACTERS",
    "MOLT_REPOSITORY_URL",
    "OMITTED_CONTENT_NOTE",
    "OMITTED_RELEASES_NOTE",
    "RELEASES_HEADING",
    "build_pull_request_body",
    "pull_request_entry",
]

#: ``run.ts:222`` -- the ceiling the body is measured against, well under the host's own 65536.
#: The margin is deliberate: the measured string is what molt assembles, while what the host counts
#: is what it stores after its own normalization, and a limit set at the exact rejection point
#: leaves nothing for that difference.
MAX_BODY_CHARACTERS = 60000

#: ``run.ts:257-258`` -- the pull-request title, and the default commit message that goes with it.
#: The two are separate settings and stay separate: a repository with a commit-message convention
#: needs the commit renamed without renaming the pull request its reviewers recognise.
DEFAULT_PR_TITLE = "Version Packages"

#: Where the header sentence points a reader who wants to know what opened their pull request
#: (owner ruling 2026-07-31, closing gap ``AP-8``). A module constant rather than an input, and
#: upstream does the same (``run.ts:190-197``): the body is built by a pure function with no host
#: context, and deriving the link from ``github.action_repository`` would have every fork of the
#: action advertise the fork in every pull request it opens.
MOLT_REPOSITORY_URL = "https://github.com/giancarlosisasi/molt"

#: ``run.ts:200`` -- the heading every tier keeps.
RELEASES_HEADING = "# Releases"

#: ``run.ts:229-231``, verbatim. Upstream's sentence, kept word for word so a changesets migrant
#: reading a molt pull request sees the message they already know.
OMITTED_CONTENT_NOTE = (
    "> The changelog information of each package has been omitted from this message, as the "
    "content exceeds the size limit."
)

#: ``run.ts:238-240``, verbatim -- including "have been", which is upstream's own wording.
OMITTED_RELEASES_NOTE = (
    "> All release information have been omitted from this message, as the content exceeds the "
    "size limit."
)


def pull_request_entry(
    *, name: str, version: str, content: str, highest_level: int, private: bool
) -> dict[str, Any]:
    """One package's section, in the shape :func:`build_pull_request_body` orders and renders.

    A mapping rather than a dataclass because :func:`molt.action.sort_changelog_entries` -- the
    already-shipped ordering rule this body reuses rather than re-deriving -- reads ``private`` and
    ``highest_level`` by key (``utils.ts:86-97``). Building the entry here keeps the key spellings
    in one place instead of at every call site.

    ``highest_level`` is a value of :data:`molt.action.BUMP_LEVELS`; ``private`` comes from
    :func:`molt.ecosystem.is_private` -- the ``Private :: Do Not Upload`` classifier, never a
    ``private`` key.
    """
    return {
        "name": name,
        "version": version,
        "content": content,
        "highest_level": highest_level,
        "private": private,
    }


def build_pull_request_body(
    *, entries: Sequence[Mapping[str, Any]], base_branch: str, has_publish_command: bool
) -> str:
    """Assemble the release pull request's body, degrading if it is too long.

    Structure (``run.ts:189-221``): an explanatory header, the ``# Releases`` heading, then one
    ``## <name>@<version>`` section per changed package followed by that package's changelog entry
    for that version.

    ``has_publish_command`` is upstream's only use of ``hasPublishScript`` and it changes what the
    sentence **promises the reader**: with a publish command configured, merging this pull request
    releases the packages; without one, merging it only writes the versions and somebody still has
    to publish. Getting that backwards tells a maintainer their release went out when it did not.

    Entries are ordered by the already-shipped :func:`molt.action.sort_changelog_entries` -- public
    first, then highest bump. The ordering rule is **not** re-derived here; the body's job is to
    render it.

    Truncation is three tiers (``run.ts:222-242``), measured against
    :data:`MAX_BODY_CHARACTERS`. Tier 3 has no floor: a pathological base-branch name could push
    even the "all release information omitted" body over the limit. Upstream has the same hole and
    molt ports it rather than inventing a fourth tier (``openspec/GAPS.md`` ``AP-6``).
    """
    header = _header(base_branch=base_branch, has_publish_command=has_publish_command)
    ordered = sort_changelog_entries(entries)

    full = "\n".join([header, RELEASES_HEADING, *(_section(entry) for entry in ordered)])
    if len(full) <= MAX_BODY_CHARACTERS:
        return full

    headers_only = "\n".join(
        [
            header,
            RELEASES_HEADING,
            OMITTED_CONTENT_NOTE,
            *(f"{_heading(entry)}\n\n" for entry in ordered),
        ]
    )
    if len(headers_only) <= MAX_BODY_CHARACTERS:
        return headers_only

    return "\n".join([header, RELEASES_HEADING, OMITTED_RELEASES_NOTE])


def _header(*, base_branch: str, has_publish_command: bool) -> str:
    """The opening paragraph (``run.ts:190-197``).

    The sentence links to :data:`MOLT_REPOSITORY_URL`, exactly as upstream links to
    ``changesets/action`` -- owner ruling 2026-07-31, closing gap ``AP-8``, which is what settled
    where molt's own action lives. Keep the sentence short: the header survives every truncation
    tier, so a longer one eats into the release information a large monorepo can fit.
    """
    outcome = (
        "the packages will be published automatically"
        if has_publish_command
        else "publish the packages yourself, or configure a publish command so this action does it"
    )
    return (
        f"This pull request was opened by [molt]({MOLT_REPOSITORY_URL})'s release action. "
        "It carries every version bump and "
        f"changelog entry the pending changesets add up to. When you are ready to release, merge "
        f"it and {outcome}. If you are not ready yet, that is fine: every time a changeset lands "
        f"on {base_branch}, this pull request is updated.\n"
    )


def _heading(entry: Mapping[str, Any]) -> str:
    """``## <name>@<version>`` (``run.ts:341``)."""
    return f"## {entry['name']}@{entry['version']}"


def _section(entry: Mapping[str, Any]) -> str:
    """One package's heading and its changelog entry (``run.ts:219``).

    A package with no changelog content still gets its heading: the sections are a permutation of
    the packages the version run reported releasing, never a filtered subset (design D4), because
    this body is a public record of what merging the pull request releases.
    """
    return f"{_heading(entry)}\n\n{entry['content']}"
