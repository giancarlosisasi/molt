"""Default commit-message generation for changeset and version commits.

Ports ``packages/cli/src/commit/index.ts:1-31`` (test-suite doc 08, commit group: 7 Port --
message strings port 1:1; only the plugin-contract wrapper adapts). Two pure functions, no git, no
filesystem, no clock (design D5):

- :func:`get_add_message` -- the message for a ``molt add`` commit.
- :func:`get_version_message` -- the message for a ``molt version`` commit, summarising a release
  plan.

Both take a ``skip_ci`` flag that is either a bool or the literal string naming the command it
should apply to (``"add"`` / ``"version"``) -- upstream's ``commit`` config option shape
(research doc 05, "Documented config options", ``commit`` row). ``get_add_message`` appends the
block when ``skip_ci`` is ``True`` or ``"add"``; ``get_version_message`` appends it when ``skip_ci``
is ``True`` or ``"version"``. Passing the *other* command's name is a no-op on purpose: it is what
lets one ``skipCI`` config value control only one of the two commit points
(``commit/index.ts:6-19``).

Registered as an entry point (design D3, ``[project.entry-points."molt.commit"]``) so a project can
swap in its own commit-message convention without forking molt, mirroring the ``molt.changelog``
generator mechanism.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from molt.versioning import BumpType

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["get_add_message", "get_version_message"]

#: The suffix appended to a ``molt add`` commit message when its skip-CI target is enabled
#: (design D4 -- a trailing block, not an inline token, so a CI matcher on the literal
#: ``[skip ci]`` token never collides with a multi-line summary).
_ADD_SKIP_CI_BLOCK = "\n\n[skip ci]\n"

#: The suffix appended to a ``molt version`` commit message -- one fewer leading newline than the
#: add block, because the release list already ends with a trailing newline
#: (``commit/index.ts:13-19``); the extra blank line still lands, from that trailing newline plus
#: this block's own leading one.
_VERSION_SKIP_CI_BLOCK = "\n[skip ci]\n"


class ChangesetLike(Protocol):
    """The one field :func:`get_add_message` reads. Satisfied by :class:`molt.changeset.Changeset`.

    Upstream's ``getAddMessage`` never touches ``.releases`` or ``.id`` either
    (``commit/index.ts:6-10``); only the summary that becomes the commit body.
    """

    @property
    def summary(self) -> str: ...


class ReleaseLike(Protocol):
    """One planned release, as :func:`get_version_message` reads it.

    ``new_version`` is a plain ``str`` here, not :class:`packaging.version.Version` -- upstream's
    release objects already carry ``newVersion`` as a string (``commit/index.ts:13-19``), and this
    function only ever interpolates it into text.
    """

    @property
    def name(self) -> str: ...
    @property
    def type(self) -> BumpType: ...
    @property
    def new_version(self) -> str: ...


class ReleasePlanLike(Protocol):
    """A release plan, as :func:`get_version_message` reads it: the flattened release list.

    Upstream builds the message from the plan's ``releases`` array directly
    (``commit/index.ts:13``), so it makes no difference whether those releases were requested by
    one changeset or merged from several -- the message has no notion of "changeset" at all.
    """

    @property
    def releases(self) -> Sequence[ReleaseLike]: ...


def get_add_message(changeset: ChangesetLike, skip_ci: bool | str) -> str:
    """The commit message for a ``molt add`` commit (``commit/index.ts:6-10``).

    A simple changeset message carries no ``[skip ci]`` suffix; the suffix is appended only when
    ``skip_ci`` is ``True`` or the string ``"add"`` -- passing ``"version"`` (the *other* command's
    target) is deliberately a no-op, which is what lets one config value gate only one commit
    point.
    """
    message = f"docs(changeset): {changeset.summary}"
    if skip_ci is True or skip_ci == "add":
        message += _ADD_SKIP_CI_BLOCK
    return message


def get_version_message(release_plan: ReleasePlanLike, skip_ci: bool | str) -> str:
    """The commit message for a ``molt version`` commit (``commit/index.ts:13-19``).

    ``none``-type releases are filtered **once, here** (design D2), before either the release
    count or the release list is built -- so the two always agree on what was "released". Filtering
    separately at each use is how a message ends up claiming three packages while listing two.

    The ``[skip ci]`` suffix is appended only when ``skip_ci`` is ``True`` or the string
    ``"version"``.
    """
    releases = [release for release in release_plan.releases if release.type is not BumpType.NONE]
    release_lines = "\n".join(f"  {release.name}@{release.new_version}" for release in releases)
    message = f"RELEASING: Releasing {len(releases)} package(s)\n\nReleases:\n{release_lines}\n"
    if skip_ci is True or skip_ci == "version":
        message += _VERSION_SKIP_CI_BLOCK
    return message
