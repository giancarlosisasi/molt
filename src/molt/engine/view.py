"""The release plan as a reportable value: string versions, and a JSON-ready payload.

:class:`molt.engine.ReleasePlan` is the engine's *working* shape -- its versions are
:class:`packaging.version.Version` objects, because everything upstream of it does version
arithmetic. What a command reports is a different thing: a document a human reads, a payload
``json.dumps`` can serialize, and a value a test can compare against a literal. ``Version`` is none
of those -- ``Version("1.0.0") == "1.0.0"`` is ``False``, so a plan carrying parsed versions cannot
be compared to the plan a user sees.

So this module owns the one conversion, and every command that reports a plan (``status`` today,
``version --dry-run`` and ``publish-plan`` later) shares it. Two properties are load-bearing:

- **The keys are snake_case.** ``old_version`` / ``new_version``, not upstream's ``oldVersion`` /
  ``newVersion`` (research doc 03 section 5.3 transcribes the JS spelling; ``guides/status.md`` and
  ``guides/dry-run-and-plans.md`` already show molt's). Four negative assertions in
  ``tests/cli/test_status.py`` depend on the camelCase spellings being **absent**.
- **There is no ``pre_state``.** ``pre.json`` and global pre mode are not ported (research README
  section 4.2); prerelease is the per-invocation ``molt version --pre`` flag. The field's absence is
  asserted, not merely unused, so an implementation that carried it as a permanent ``None`` would
  resurrect a concept molt does not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from molt.versioning import BumpType

__all__ = [
    "ChangesetReleaseView",
    "ChangesetView",
    "PlanView",
    "ReleaseView",
    "plan_payload",
    "plan_view",
]


# ======================================================================================
# What a plan has to look like to be reportable (read structurally, like everything else)
# ======================================================================================


class _ReleaseLike(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def type(self) -> BumpType: ...
    @property
    def old_version(self) -> object: ...
    @property
    def new_version(self) -> object: ...
    @property
    def changesets(self) -> Sequence[str]: ...


class _ChangesetReleaseLike(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def type(self) -> BumpType: ...


class _ChangesetLike(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def summary(self) -> str: ...
    @property
    def releases(self) -> Sequence[_ChangesetReleaseLike]: ...


class _PlanLike(Protocol):
    @property
    def changesets(self) -> Sequence[_ChangesetLike]: ...
    @property
    def releases(self) -> Sequence[_ReleaseLike]: ...


# ======================================================================================
# The reportable plan
# ======================================================================================


@dataclass(frozen=True)
class ChangesetReleaseView:
    """One ``package: bump`` entry of a changeset, as reported."""

    name: str
    type: BumpType


@dataclass(frozen=True)
class ChangesetView:
    """A pending changeset: the intent *as authored*."""

    id: str
    summary: str
    releases: tuple[ChangesetReleaseView, ...]


@dataclass(frozen=True)
class ReleaseView:
    """One planned release: the plan *as computed*.

    ``changesets`` is empty for a release created by propagation or by a fixed group -- nobody
    wrote a changeset for it, and that emptiness is what ``--verbose`` renders as
    ``(dependency bump)``.

    ``type`` stays a :class:`~molt.versioning.BumpType` member rather than becoming a plain string:
    it is a ``StrEnum``, so it serializes and compares as its value while a consumer that switches
    on it still gets the enum.
    """

    name: str
    type: BumpType
    old_version: str
    new_version: str
    changesets: tuple[str, ...]


@dataclass(frozen=True)
class PlanView:
    """A release plan, ready to report. Deliberately has no prerelease-state field."""

    changesets: tuple[ChangesetView, ...]
    releases: tuple[ReleaseView, ...]


def plan_view(plan: _PlanLike) -> PlanView:
    """Convert an assembled :class:`molt.engine.ReleasePlan` into its reportable form.

    Order is preserved on both arrays: changesets in read order (sorted on the id) and releases in
    the engine's insertion order -- changeset order, then dependents in discovery order, then
    fixed-group members. That order reaches the changelog, so re-sorting here would make the
    preview disagree with the release it previews.
    """
    return PlanView(
        changesets=tuple(
            ChangesetView(
                id=changeset.id,
                summary=changeset.summary,
                releases=tuple(
                    ChangesetReleaseView(name=release.name, type=release.type)
                    for release in changeset.releases
                ),
            )
            for changeset in plan.changesets
        ),
        releases=tuple(
            ReleaseView(
                name=release.name,
                type=release.type,
                old_version=str(release.old_version),
                new_version=str(release.new_version),
                changesets=tuple(release.changesets),
            )
            for release in plan.releases
        ),
    )


def plan_payload(view: PlanView) -> dict[str, Any]:
    """The plan as plain JSON types -- what ``--output json`` serializes.

    Written out field by field rather than through ``dataclasses.asdict`` so the wire format is
    visible in one place and cannot drift when a field is added to the dataclasses for an internal
    reason. ``type`` is rendered as its ``.value`` for the same reason: relying on ``StrEnum``
    serializing itself would make the payload depend on an enum base class.
    """
    return {
        "changesets": [
            {
                "id": changeset.id,
                "summary": changeset.summary,
                "releases": [
                    {"name": release.name, "type": release.type.value}
                    for release in changeset.releases
                ],
            }
            for changeset in view.changesets
        ],
        "releases": [
            {
                "name": release.name,
                "type": release.type.value,
                "old_version": release.old_version,
                "new_version": release.new_version,
                "changesets": list(release.changesets),
            }
            for release in view.releases
        ],
    }
