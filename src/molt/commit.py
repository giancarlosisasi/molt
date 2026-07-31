"""Default commit-message generation for changeset and version commits.

Ports ``packages/cli/src/commit/index.ts:1-31`` (test-suite doc 08, commit group: 7 Port --
message strings port 1:1; only the plugin-contract wrapper adapts). Two pure functions, no git, no
filesystem, no clock (design D5):

- :func:`get_add_message` -- the message for a ``molt add`` commit.
- :func:`get_version_message` -- the message for a ``molt version`` commit, summarising a release
  plan.

Both take a ``skip_ci`` flag that is either ``False`` or the literal string naming the command it
should apply to (``"add"`` / ``"version"``) -- upstream's ``commit`` config option shape
(research doc 05, "Documented config options", ``commit`` row). ``get_add_message`` appends the
block when ``skip_ci`` is ``"add"``; ``get_version_message`` appends it when ``skip_ci`` is
``"version"``. Passing the *other* command's name is a no-op on purpose: it is what lets one
``skipCI`` config value control only one of the two commit points (``commit/index.ts:6-19``).

**A bare ``True`` never reaches either function** (owner ruling 2026-07-30, closing ``CM-2``).
``skip_ci`` used to accept ``bool | str``, with an untested ``skip_ci is True`` branch in both
functions: nothing in the 8-row gate ever passed one, so an implementation that ignored ``True``
entirely would have passed too. The resolution layer now normalizes the written value through
:func:`normalize_skip_ci` before calling, which is where the ambiguity belongs -- upstream
normalizes ``commit: true`` into a concrete shape in the same place (``config.ts:177-179``).

Registered as an entry point (design D3, ``[project.entry-points."molt.commit"]``) so a project can
swap in its own commit-message convention without forking molt, mirroring the ``molt.changelog``
generator mechanism.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from molt.versioning import BumpType

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "COMMIT_GROUP",
    "SkipCI",
    "get_add_message",
    "get_version_message",
    "load_provider",
    "normalize_skip_ci",
]

#: The entry-point group a distribution registers a commit-message convention in, and the prefix
#: ``molt.config``'s ``BUILTIN_COMMIT`` spells the built-in with: ``molt.commit.default`` names the
#: entry point ``default`` of the group ``molt.commit``.
COMMIT_GROUP = "molt.commit"

#: Which commit point a ``skip_ci`` setting applies to, or ``False`` for neither. Narrowed from the
#: original ``bool | str`` by the owner ruling of 2026-07-30 (gap ``CM-2``): the two commands are
#: the only callers, and both resolve the written configuration to one of these three values.
SkipCI = Literal["add", "version"] | Literal[False]

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


def load_provider(ref: str, *, method: str) -> object:
    """Load the commit-message provider ``ref`` names, checking it offers ``method``.

    Resolution order, most specific first: an entry point of the ``molt.commit`` group; the same
    name with the group prefix stripped, which is how ``molt.commit.default`` reaches the entry
    point registered as ``default``; then a dotted module path. The built-in resolves through the
    *same* mechanism a third-party convention uses -- no privileged path, mirroring how
    :mod:`molt.apply.generators` resolves changelog generators.

    Lives here rather than in either command because **both** commands resolve the same
    ``config.commit`` value, and two copies of a resolution order is how the two commit points start
    disagreeing about which provider a project configured (gap ``AC-7``).
    """
    import importlib
    import importlib.metadata

    from molt.errors import MoltError

    candidates = [ref]
    prefix = f"{COMMIT_GROUP}."
    if ref.startswith(prefix):
        candidates.append(ref[len(prefix) :])
    for entry_point in importlib.metadata.entry_points(group=COMMIT_GROUP):
        if entry_point.name in candidates:
            return _require(entry_point.load(), ref, method)
    try:
        loaded = importlib.import_module(ref)
    except ImportError as exc:
        raise MoltError(
            f'Could not resolve the commit-message provider "{ref}": it is not registered in the '
            f'"{COMMIT_GROUP}" entry-point group and could not be imported. If it was just '
            "installed, run `uv sync` so its entry points are visible."
        ) from exc
    return _require(loaded, ref, method)


def _require(loaded: object, ref: str, method: str) -> object:
    """Accept a module or an object, as long as it can produce the message that was asked for."""
    from molt.errors import MoltError

    provider = getattr(loaded, "generator", loaded)
    if not callable(getattr(provider, method, None)):
        raise MoltError(f'The commit-message provider "{ref}" does not implement {method}().')
    return provider


def normalize_skip_ci(value: object, *, command: Literal["add", "version"]) -> SkipCI:
    """Resolve a written ``skip_ci`` option into the concrete value the two functions accept.

    The written form comes from ``commit = ["<ref>", { skip_ci = ... }]`` and is whatever the user
    typed, so ``True`` -- "skip CI for this command" -- has to be turned into that command's own
    literal *here*, before either message function sees it. Anything unrecognized (including
    ``None`` and an unknown string) resolves to ``False``: a mistyped target must not silently
    enable a marker that stops a release pipeline.
    """
    if value is True:
        return command
    if value in ("add", "version"):
        # `value` is one of the two literals; the cast is what the narrowing above proves.
        return "add" if value == "add" else "version"
    return False


def get_add_message(changeset: ChangesetLike, skip_ci: SkipCI) -> str:
    """The commit message for a ``molt add`` commit (``commit/index.ts:6-10``).

    A simple changeset message carries no ``[skip ci]`` suffix; the suffix is appended only when
    ``skip_ci`` is the string ``"add"`` -- passing ``"version"`` (the *other* command's target) is
    deliberately a no-op, which is what lets one config value gate only one commit point.
    """
    message = f"docs(changeset): {changeset.summary}"
    if skip_ci == "add":
        message += _ADD_SKIP_CI_BLOCK
    return message


def get_version_message(release_plan: ReleasePlanLike, skip_ci: SkipCI) -> str:
    """The commit message for a ``molt version`` commit (``commit/index.ts:13-19``).

    ``none``-type releases are filtered **once, here** (design D2), before either the release
    count or the release list is built -- so the two always agree on what was "released". Filtering
    separately at each use is how a message ends up claiming three packages while listing two.

    The ``[skip ci]`` suffix is appended only when ``skip_ci`` is the string ``"version"``.
    """
    releases = [release for release in release_plan.releases if release.type is not BumpType.NONE]
    release_lines = "\n".join(f"  {release.name}@{release.new_version}" for release in releases)
    message = f"RELEASING: Releasing {len(releases)} package(s)\n\nReleases:\n{release_lines}\n"
    if skip_ci == "version":
        message += _VERSION_SKIP_CI_BLOCK
    return message
