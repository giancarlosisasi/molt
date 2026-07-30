"""The release plan -- changesets plus a workspace, turned into version numbers.

Ports ``packages/assemble-release-plan/src`` (``index.ts``, ``flatten-releases.ts``,
``determine-dependents.ts``, ``match-fixed-constraint.ts``, ``apply-links.ts``, ``increment.ts``)
from ``changesets@3.0.0-next.9``. Mechanics and every ``file:line`` citation below come from
``roadmap/research/changesets-01-core-versioning-engine.md`` sections 2-4, 7 and 11.

This is the moat. Both abandoned Python ports of changesets stopped before it, and everything here
produces a version number -- the one output whose wrongness a user cannot detect by reading it.

The shape
---------
Three passes repeat until none of them reports a change (``index.ts:161-183``)::

    determine_dependents()      # propagate bumps to dependents
    match_fixed_constraint()    # raise every member of a fixed group to the group maximum
    apply_links()               # raise the *releasing* members of a linked group

**The order is observable**, because ``determine_dependents`` and ``match_fixed_constraint`` both
*create* releases and the plan is insertion-ordered; it is pinned by
``test_determine_dependents_runs_before_match_fixed_constraint``. The
``match_fixed_constraint`` / ``apply_links`` order is not observable -- ``apply_links`` only mutates
releases that already exist, so it can never win an insertion race.

**Termination rests on monotonicity, not on a counter** (design D2, research README section 3.1).
Every pass returns ``changed=True`` only when a bump type actually climbed
``none < patch < minor < major`` or a group re-aligned onto its constant highest version, and
``old_version`` is read from disk once, in :func:`_index_workspace`, and never re-derived from a
mutated release. There is deliberately **no iteration cap**: a cap turns a non-termination bug into
a silently truncated plan, which is worse because the output still looks plausible.

**``none`` is a value, not an absence** (design D3). Upstream leans on ``"none"`` being *truthy* in
JavaScript -- that is what materialises an ignored or dev-only dependent as a real release
(``determine-dependents.ts:131-136``). In Python the analogous shortcut inverts, so nothing in this
module tests a bump for truthiness; every check names :class:`~molt.versioning.BumpType` members
explicitly or compares ``.rank``.

Deliberate divergences from upstream
------------------------------------
1. **A dependent bump merges into an existing ``none`` release instead of replacing it** (design D4;
   upstream bug #4, research README section 3.4). ``determine-dependents.ts:151-161`` builds a
   replacement with ``changesets: []``, so a package that carries a ``none`` changeset *and* is
   dragged in as a dependent loses that changeset id -- and with it, its summary in the changelog.
   Do not "fix" this back.

2. **``fixed`` and ``linked`` group entries are resolved against the workspace, so globs expand**
   (upstream bug #1). ``match-fixed-constraint.ts:20,32`` and ``apply-links.ts:28`` compare with a
   literal ``.includes()``, so a documented glob matches nothing and then throws ``InternalError``
   from the lookup that follows. ``molt.config`` already expands these groups at load time, which
   makes the resolution here a no-op for real configuration -- a concrete name is a glob that
   matches only itself. It is kept because the engine's contract is "a group names packages", not
   "a group is a pre-expanded list", and the conformance suite feeds an unexpanded ``pkg-*``.
   ``ignore``, which has no such conformance row, is matched literally per the project contract.

3. **Snapshots and prerelease are PEP 440, not semver** (research README sections 4.2, 4.3).
   ``0.0.0-canary-abcdefg`` is not a legal Python version and PyPI burns every version permanently,
   so a snapshot is ``0.0.0.dev<14-digit datetime>`` with any free-form part -- the tag, or a
   rendered ``prerelease_template`` -- carried in the **local** segment, the only free-form field
   PEP 440 defines. ``pre.json`` is gone: ``pre`` is an invocation flag that reports the current
   bump and advances the counter, with no persistent state read or written.

4. **``peerDependencies`` and ``___experimentalUnsafeOptions`` are dropped**, with the half of
   ``determineDependents`` that served them (research README section 4.4).

What is still owed
------------------
- **Private-package skipping.** Upstream's ``shouldSkipPackage`` also skips ``"private": true``
  packages when ``privatePackages.version`` is false. The Python marker is the
  ``Private :: Do Not Upload`` classifier (:func:`molt.ecosystem.is_private`), which the ported
  ``Packages`` shape this module reads does not carry. Only ``ignore`` is honoured here.
- **A ``molt.config.Config`` adapter.** :class:`PlanConfig` is spelled with the flat attribute
  names the engine conformance suite uses; the real ``Config`` nests ``snapshot`` and
  ``private_packages``. The same adapter debt :mod:`molt.engine.graph` records for
  ``molt.ecosystem.Workspace``.
- **Dynamically-versioned packages.** ``version is None`` means ``dynamic = ["version"]``; there is
  nothing to bump until the version-source abstraction lands (research doc 02 section 12.5), so it
  is refused loudly rather than skipped silently.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from molt import clock
from molt.engine.graph import (
    WORKSPACE_PREFIX,
    DependencyKind,
    ManifestLike,
    PackageLike,
    PackagesLike,
    get_dependents_graph,
    relative_package_path,
    resolve_workspace_range,
)
from molt.errors import InternalError, MoltError
from molt.globs import glob_match
from molt.names import normalize_name
from molt.versioning import BumpType, highest, inc, next_pre_number, satisfies

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from datetime import datetime

    from packaging.specifiers import SpecifierSet

__all__ = [
    "ChangesetLike",
    "ChangesetReleaseLike",
    "PlanConfig",
    "PrePhase",
    "Release",
    "ReleasePlan",
    "SnapshotParams",
    "assemble_release_plan",
]

#: The PEP 440 prerelease phases ``--pre`` accepts. ``a``/``b``/``rc`` are the spec's three
#: prerelease spellings and ``dev`` its development-release segment; there is no free-form tag,
#: which is exactly why ``pre.json``'s ``1.0.1-next.0`` cannot be ported (README section 4.2).
PrePhase = Literal["a", "b", "rc", "dev"]

#: Dependency sections, in the order ``getDependencyVersionRanges`` walks them
#: (``determine-dependents.ts:180-186``), minus ``peerDependencies``. The order is not observable --
#: the switch below takes a maximum, not a first hit -- but it is kept so the two files read alike.
_SECTION_ORDER: tuple[DependencyKind, ...] = ("runtime", "dev", "optional")

#: Sections whose dependents take a real version bump. ``optional`` (extras) shares
#: ``dependencies``' switch case upstream (``determine-dependents.ts:101-107``); ``dev`` is the odd
#: one out (``:108-117``) and yields ``none`` -- a dev dependency cannot break an install.
_BUMPING_SECTIONS: frozenset[DependencyKind] = frozenset({"runtime", "optional"})

#: The placeholders ``snapshot.prerelease_template`` understands (research doc 01 section 11.1).
_PLACEHOLDERS = re.compile(r"\{(tag|commit|commit-short|timestamp|datetime)\}")

#: Everything PEP 440 forbids in a local version label, collapsed to the canonical ``.`` separator.
_NOT_LOCAL_SAFE = re.compile(r"[^A-Za-z0-9]+")

#: ``YYYYMMDDHHMMSS`` -- upstream's ``toISOString()`` with the separators and milliseconds stripped
#: (``index.ts:47-50``), which is the value that becomes the ``.devN`` counter.
_DATETIME_FORMAT = "%Y%m%d%H%M%S"


# ======================================================================================
# Public value types
# ======================================================================================


@dataclass(frozen=True)
class SnapshotParams:
    """The ``--snapshot`` invocation, or ``None`` when this is not a snapshot release.

    Upstream's ``SnapshotReleaseParameters`` (``index.ts:22-25``). A dedicated object is what
    distinguishes "not a snapshot release" (``snapshot=None``) from "``--snapshot`` with no tag"
    (``snapshot=SnapshotParams()``); a bare ``str | None`` cannot say both.
    """

    tag: str | None = None
    commit: str | None = None


@dataclass(frozen=True)
class Release:
    """One package's planned release -- upstream's ``ComprehensiveRelease`` (``types:19-25``).

    ``changesets`` holds **every** changeset id that named this package, in changeset order,
    including ``none`` ones: that list is what the changelog renderer reads, so an id dropped here
    is a summary missing from the release notes.

    ``changesets`` is empty for a release created by propagation or by a fixed group -- the package
    is released, but nobody wrote a changeset for it.
    """

    name: str
    type: BumpType
    old_version: Version
    new_version: Version
    changesets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReleasePlan:
    """The assembled plan: what to release, and the changesets that asked for it.

    ``releases`` is **insertion-ordered** (``Array.from(releases.values())``, ``index.ts:218``) --
    changeset order first, then dependents in FIFO discovery order, then fixed-group members. That
    order reaches the changelog, so a sorted or set-backed implementation would churn every diff.
    """

    changesets: list[ChangesetLike]
    releases: list[Release]


# ======================================================================================
# The input shapes, read structurally
# ======================================================================================


class ChangesetReleaseLike(Protocol):
    """One ``package: bump`` entry of a changeset's frontmatter."""

    @property
    def name(self) -> str: ...
    @property
    def type(self) -> BumpType: ...


class ChangesetLike(Protocol):
    """A parsed changeset. Satisfied by :class:`molt.changeset.Changeset`.

    ``summary`` is declared even though assembly never reads it: the plan carries its changesets
    straight through to the changelog (:class:`molt.apply.apply.ChangesetLike` requires it) and to
    ``--output json`` (:mod:`molt.engine.view`), so a plan whose changesets cannot state their
    summary is not a plan anything downstream can use.
    """

    @property
    def id(self) -> str: ...
    @property
    def summary(self) -> str: ...
    @property
    def releases(self) -> Sequence[ChangesetReleaseLike]: ...


class PlanConfig(Protocol):
    """The configuration keys release-plan assembly reads.

    Typed structurally, like :class:`molt.engine.graph.GraphConfig`, so the engine does not depend
    on which config object a caller holds. The names are the flat ones the conformance suite uses;
    see "What is still owed" in the module docstring for the ``molt.config.Config`` adapter.
    """

    @property
    def ignore(self) -> Sequence[str]: ...
    @property
    def fixed(self) -> Sequence[Sequence[str]]: ...
    @property
    def linked(self) -> Sequence[Sequence[str]]: ...
    @property
    def bump_workspace_sources_only(self) -> bool: ...
    @property
    def update_internal_dependents(self) -> str: ...
    @property
    def snapshot_use_calculated_version(self) -> bool: ...
    @property
    def snapshot_prerelease_template(self) -> str | None: ...


# ======================================================================================
# Internal state -- mutated in place for the whole walk (design D5)
# ======================================================================================


@dataclass
class _InternalRelease:
    """``ComprehensiveRelease`` minus ``new_version`` (``types.ts:3-8``).

    Mutable and mutated in place, which upstream documents as intentional
    (``determine-dependents.ts:16-27``) and which is *observable*: raising a release's type has to
    leave it where it was inserted. ``new_version`` is absent because it is computed once, at the
    end, from the settled ``type`` and ``old_version`` (``index.ts:216-232``) -- deriving it during
    the walk would make ``old_version`` a moving target and take termination with it.
    """

    name: str
    type: BumpType
    old_version: Version
    changesets: list[str]


@dataclass(frozen=True)
class _Member:
    """A workspace member, read once: its name, its on-disk version and its relative directory."""

    name: str
    normalized: str
    version: Version
    path: str
    manifest: ManifestLike


@dataclass(frozen=True)
class _SnapshotSuffix:
    """The two PEP 440 fields a snapshot suffix is split across.

    ``dev`` becomes the ``.devN`` counter and carries the timestamp -- the only numeric prerelease
    segment that sorts below every real release, which is the property upstream wanted from its
    ``0.0.0`` base. ``local`` carries the free-form part (the tag, or a rendered template) because
    the local segment is the only free-form field PEP 440 defines.
    """

    dev: str
    local: str | None


# ======================================================================================
# Entry point
# ======================================================================================


def assemble_release_plan(
    changesets: Sequence[ChangesetLike],
    packages: PackagesLike,
    config: PlanConfig,
    *,
    pre: PrePhase | None = None,
    snapshot: SnapshotParams | None = None,
) -> ReleasePlan:
    """Turn ``changesets`` plus a workspace into a :class:`ReleasePlan`.

    Ports ``assembleReleasePlan`` (``index.ts:123-233``). Nothing is written to disk; applying the
    plan is :mod:`molt.apply`'s job.

    ``pre`` selects a PEP 440 prerelease phase for this invocation only -- the release reports the
    bump its changesets asked for and the prerelease counter advances from the version on disk.
    ``snapshot`` asks for a throwaway version instead; the timestamp behind it is read **once**,
    here, so every snapshot version in one plan shares it (design D7). Computing it per release
    would let a plan straddle a second boundary and permanently burn two version numbers for one
    snapshot.

    Raises :class:`~molt.errors.MoltError` when a changeset names a package the workspace does not
    contain, or mixes ignored and non-ignored packages -- both are "split this changeset" problems
    the user has to see before any version is computed.
    """
    moment = clock.now()

    members, order = _index_workspace(packages)
    relevant = _relevant_changesets(changesets, members, config.ignore)
    releases = _flatten_releases(relevant, members, config.ignore)
    graph, _valid, _errors = get_dependents_graph(packages, config)

    # `valid` is deliberately discarded: upstream never reads it either (``index.ts:156-159``), so a
    # workspace with a runtime path source still gets a plan. Reporting broken declarations is the
    # CLI's job (research doc 01 section 14.11).
    while True:
        dependents_added = _determine_dependents(
            releases=releases, members=members, graph=graph, config=config, pre=pre
        )
        fixed_updated = _match_fixed_constraint(
            releases=releases, members=members, order=order, config=config
        )
        links_updated = _apply_links(releases=releases, members=members, order=order, config=config)
        if not (dependents_added or fixed_updated or links_updated):
            break

    suffix = None if snapshot is None else _snapshot_suffix(snapshot, config, moment)
    return ReleasePlan(
        changesets=list(relevant),
        releases=[
            _materialise(release, config=config, pre=pre, suffix=suffix)
            for release in releases.values()
        ],
    )


# ======================================================================================
# Reading the workspace -- old_version is read here, once, and never again (task 2.3)
# ======================================================================================


def _index_workspace(packages: PackagesLike) -> tuple[dict[str, _Member], list[_Member]]:
    """Index the workspace members by PEP 503 name, and keep their discovery order.

    The root package is deliberately absent: upstream's ``packagesByName`` is built from
    ``packages.packages`` alone (``index.ts:134-136``), so a root manifest is never released and
    never force-joined to a group.
    """
    order = [_member(package, packages.root_dir) for package in packages.packages]
    return {member.normalized: member for member in order}, order


def _member(package: PackageLike, root_dir: str) -> _Member:
    manifest = package.manifest
    return _Member(
        name=manifest.name,
        normalized=normalize_name(manifest.name),
        version=_on_disk_version(manifest),
        path=relative_package_path(package.dir, root_dir),
        manifest=manifest,
    )


def _on_disk_version(manifest: ManifestLike) -> Version:
    """Parse a member's declared version, refusing the two cases that cannot be bumped.

    ``None`` means ``dynamic = ["version"]``. Upstream's ``shouldSkipPackage`` would silently skip
    such a package; in Python that would drop half a repository from the plan, so it is an error
    until the version-source abstraction lands (research doc 02 section 12.5).
    """
    raw = manifest.version
    if raw is None:
        raise MoltError(
            f'Cannot plan a release for "{manifest.name}": its version is dynamic, and molt '
            "cannot yet resolve a dynamic version source."
        )
    try:
        return Version(raw)
    except InvalidVersion as exc:
        raise MoltError(
            f'Cannot plan a release for "{manifest.name}": "{raw}" is not a PEP 440 version.'
        ) from exc


def _is_ignored(name: str, ignore: Sequence[str]) -> bool:
    """Whether ``name`` is listed in ``ignore``.

    Literal PEP 503 membership, not a glob match: ``molt.config`` expands ``ignore`` globs at load
    time, and matching again here is exactly what the project contract forbids. Both sides are
    normalized, because one forgotten call would let ``Foo_Bar`` escape its own ``ignore`` entry.
    """
    key = normalize_name(name)
    return any(key == normalize_name(pattern) for pattern in ignore)


# ======================================================================================
# Validation and flattening (``index.ts:235-284``, ``flatten-releases.ts:16-63``)
# ======================================================================================


def _relevant_changesets(
    changesets: Sequence[ChangesetLike],
    members: Mapping[str, _Member],
    ignore: Sequence[str],
) -> list[ChangesetLike]:
    """Validate every changeset up front and return the ones that apply.

    Ports ``getRelevantChangesets`` (``index.ts:235-284``) minus its pre-mode filter, which has
    nothing to filter against now that ``pre.json`` is gone (research README section 4.2). The
    validation half is unchanged and runs before any version is computed, so the failure names the
    offending changeset rather than surfacing later as an internal error.
    """
    for changeset in changesets:
        ignored: list[str] = []
        not_ignored: list[str] = []
        for release in changeset.releases:
            if normalize_name(release.name) not in members:
                raise MoltError(
                    f"Found changeset {changeset.id} for package {release.name} "
                    "which is not in the workspace"
                )
            (ignored if _is_ignored(release.name, ignore) else not_ignored).append(release.name)
        if ignored and not_ignored:
            raise MoltError(
                f"Found mixed changeset {changeset.id}\n"
                f"Found ignored packages: {' '.join(ignored)}\n"
                f"Found not ignored packages: {' '.join(not_ignored)}\n"
                "Mixed changesets that contain both ignored and not ignored packages "
                "are not allowed"
            )
    return list(changesets)


def _flatten_releases(
    changesets: Sequence[ChangesetLike],
    members: Mapping[str, _Member],
    ignore: Sequence[str],
) -> dict[str, _InternalRelease]:
    """Collapse the changesets into one release per package (``flatten-releases.ts:16-63``).

    Two rules, both monotone: the bump type keeps the **maximum** across changesets, and the
    changeset id is appended **every** time regardless of whether its type won. The second is what
    keeps a ``none`` changeset's summary in the changelog of a package released for another reason.

    An ignored package is filtered out here, so its own changeset produces nothing
    (``flatten-releases.ts:34-38``); it can still reappear later as a ``none`` dependent.
    """
    releases: dict[str, _InternalRelease] = {}
    for changeset in changesets:
        for requested in changeset.releases:
            member = members[normalize_name(requested.name)]
            if _is_ignored(member.name, ignore):
                continue
            existing = releases.get(member.normalized)
            if existing is None:
                releases[member.normalized] = _InternalRelease(
                    name=member.name,
                    type=requested.type,
                    old_version=member.version,
                    changesets=[changeset.id],
                )
                continue
            if requested.type.rank > existing.type.rank:
                existing.type = requested.type
            existing.changesets.append(changeset.id)
    return releases


# ======================================================================================
# Pass 1 -- determine_dependents (``determine-dependents.ts:28-166``)
# ======================================================================================


def _determine_dependents(
    *,
    releases: dict[str, _InternalRelease],
    members: Mapping[str, _Member],
    graph: Mapping[str, Sequence[str]],
    config: PlanConfig,
    pre: PrePhase | None,
) -> bool:
    """Propagate every release to its dependents, transitively. Returns whether anything changed.

    FIFO (``deque``) traversal, seeded with a snapshot of the current releases and extended with
    each release the walk creates (``determine-dependents.ts:45-49``). Breadth-first is not an
    optimisation here -- it fixes the order dependents are inserted in, which the plan preserves.
    """
    updated = False
    queue: deque[_InternalRelease] = deque(releases.values())
    while queue:
        next_release = queue.popleft()
        dependents = graph.get(next_release.name)
        if dependents is None:
            raise InternalError(
                "Error in determining dependents - could not find package in repository: "
                f"{next_release.name}"
            )
        dependency = members[normalize_name(next_release.name)]
        for dependent_name in dependents:
            dependent = members[normalize_name(dependent_name)]
            bump = _dependent_bump(
                dependent=dependent,
                dependency=dependency,
                next_release=next_release,
                releases=releases,
                config=config,
                pre=pre,
            )
            if bump is None:
                continue
            updated = True
            existing = releases.get(dependent.normalized)
            if existing is None:
                created = _InternalRelease(
                    name=dependent.name,
                    type=bump,
                    old_version=dependent.version,
                    changesets=[],
                )
                releases[dependent.normalized] = created
                queue.append(created)
            else:
                # Design D4 / upstream bug #4: ``determine-dependents.ts:151-161`` *replaces* the
                # release object with one built ``changesets: []``, discarding a ``none`` release's
                # ids and losing its summary from the changelog. molt raises the type in place and
                # keeps the list. Do not restore the replacement.
                existing.type = bump
                queue.append(existing)
    return updated


def _dependent_bump(
    *,
    dependent: _Member,
    dependency: _Member,
    next_release: _InternalRelease,
    releases: Mapping[str, _InternalRelease],
    config: PlanConfig,
    pre: PrePhase | None,
) -> BumpType | None:
    """The bump ``dependent`` takes because ``next_release`` moved, or ``None`` for "no change".

    Ports ``determine-dependents.ts:57-130``. Three rules do all the work:

    - An **ignored** dependent short-circuits to ``none`` (``:67-73``). That is a real release at an
      unchanged version, not an omission: ``apply_release_plan`` still has to rewrite the pin, and
      "deliberately not released" is different information from "not considered" (design D9).
    - A ``none`` **dependency** never propagates (``:88``), and that check sits *before* the
      ``update_internal_dependents`` branch, so ``"always"`` cannot resurrect it.
    - A dependent that already carries a real bump is never re-evaluated (``:91-92``); only a
      ``none`` release can still be upgraded.

    A dependent is never bumped by more than a **patch**. Upstream's ``major``/``minor`` guards in
    the switch are vestigial, and the 216-cell conformance matrix pins that they stay so.
    """
    existing = releases.get(dependent.normalized)
    if _is_ignored(dependent.name, config.ignore):
        bump: BumpType | None = BumpType.NONE
    else:
        bump = None
        if next_release.type is not BumpType.NONE and (
            existing is None or existing.type is BumpType.NONE
        ):
            incremented = _new_version(next_release, pre)
            always = config.update_internal_dependents == "always"
            for section, specifier in _dependency_version_ranges(
                dependent, dependency, next_release
            ):
                if not always and satisfies(incremented, specifier):
                    continue  # the constraint still accepts it, so nothing is broken
                if section in _BUMPING_SECTIONS:
                    if bump not in (BumpType.MAJOR, BumpType.MINOR):
                        bump = BumpType.PATCH
                elif bump not in (BumpType.MAJOR, BumpType.MINOR, BumpType.PATCH):
                    bump = BumpType.NONE

    if bump is None:
        return None
    if existing is not None and bump.rank <= existing.type.rank:
        # ``determine-dependents.ts:122-124`` drops an unchanged type. Comparing ``rank`` rather
        # than equality states the invariant the loop's termination rests on -- a bump may only
        # climb -- instead of leaving it to hold by accident (design D2).
        return None
    return bump


def _dependency_version_ranges(
    dependent: _Member,
    dependency: _Member,
    release: _InternalRelease,
) -> Iterator[tuple[DependencyKind, SpecifierSet]]:
    """Every constraint ``dependent`` declares on ``dependency``, one per manifest section.

    Ports ``getDependencyVersionRanges`` (``determine-dependents.ts:173-228``). A package declared
    in two sections yields two entries and the switch runs for each -- that is what makes a prod
    edge bump a dependent a dev edge alone would not.

    Note what a ``workspace:`` source resolves against: ``release.old_version``, which
    ``apply_links`` or ``match_fixed_constraint`` may already have raised to a group's version.
    That is the mechanism by which a group alignment cascades into dependents outside the group.
    A declaration that is a *location* rather than a constraint -- a PEP 508 direct reference, a
    bare path -- yields nothing, because there is no range a release can violate.
    """
    sections: dict[DependencyKind, Mapping[str, str]] = {
        "runtime": dependent.manifest.dependencies,
        "dev": dependent.manifest.dev_dependencies,
        "optional": dependent.manifest.optional_dependencies,
    }
    for section in _SECTION_ORDER:
        raw = _declared_range(sections[section], dependency.normalized)
        if not raw:
            continue  # absent, or an empty specifier: nothing that can ever be violated
        if raw.strip().startswith(WORKSPACE_PREFIX):
            resolved = resolve_workspace_range(
                raw, release.old_version, dependency_path=dependency.path
            )
            if resolved is not None:
                yield section, resolved
            continue
        specifier = _plain_specifier(dependency.name, raw)
        if specifier is not None:
            yield section, specifier


def _declared_range(section: Mapping[str, str], normalized: str) -> str | None:
    """Look up ``normalized`` in one dependency section, PEP 503-aware on both sides."""
    for declared, text in section.items():
        if normalize_name(declared) == normalized:
            return text
    return None


def _plain_specifier(name: str, tail: str) -> SpecifierSet | None:
    """Read a PEP 508 requirement tail as a specifier, or ``None`` when it is not one.

    The Python analogue of upstream's ``semver.validRange(range) === null`` guard. Going through
    :class:`~packaging.requirements.Requirement` rather than parsing the tail directly is what makes
    an environment marker or an extra survive; a direct reference is rejected by the ``url`` check,
    because a location states no version a release could fall outside of.
    """
    try:
        requirement = Requirement(f"{name} {tail}")
    except InvalidRequirement:
        return None
    if requirement.url is not None:
        return None
    return requirement.specifier


# ======================================================================================
# Passes 2 and 3 -- fixed forces, linked aligns (design D6: two functions, no shared path)
# ======================================================================================


def _match_fixed_constraint(
    *,
    releases: dict[str, _InternalRelease],
    members: Mapping[str, _Member],
    order: Sequence[_Member],
    config: PlanConfig,
) -> bool:
    """Raise **every** member of a releasing fixed group to the group maximum.

    Ports ``matchFixedConstraint`` (``match-fixed-constraint.ts:10-73``). ``fixed`` *forces*: a
    member with no changeset of its own is created and released anyway. Contrast
    :func:`_apply_links`, which only realigns members that are already releasing -- the two are
    kept as separate functions on purpose, because implementing linked as "fixed minus a flag"
    produces a plan that is right for fixed and wrong for linked (design D6).

    A group with nothing releasing in it short-circuits (``:23``), which is what stops group
    configuration alone from manufacturing a release.
    """
    updated = False
    for group in config.fixed:
        group_members = _group_members(group, order, members)
        releasing = _releasing(releases, group_members)
        if not releasing:
            continue
        group_type = highest([release.type for release in releasing])
        group_version = _current_highest_version(group_members)
        for member in group_members:
            if _is_ignored(member.name, config.ignore):
                continue
            release = releases.get(member.normalized)
            if release is None:
                releases[member.normalized] = _InternalRelease(
                    name=member.name,
                    type=group_type,
                    old_version=group_version,
                    changesets=[],
                )
                updated = True
                continue
            updated = _realign(release, group_type, group_version) or updated
    return updated


def _apply_links(
    *,
    releases: dict[str, _InternalRelease],
    members: Mapping[str, _Member],
    order: Sequence[_Member],
    config: PlanConfig,
) -> bool:
    """Align the **already-releasing** members of a linked group. Never creates a release.

    Ports ``applyLinks`` (``apply-links.ts:17-56``). The loop runs over ``releasing`` rather than
    over the group (``:43``), which is the whole practical difference from ``fixed``: a member with
    no changeset contributes its current version to the group maximum and stays out of the plan.
    """
    updated = False
    for group in config.linked:
        group_members = _group_members(group, order, members)
        releasing = _releasing(releases, group_members)
        if not releasing:
            continue
        group_type = highest([release.type for release in releasing])
        group_version = _current_highest_version(group_members)
        for release in releasing:
            updated = _realign(release, group_type, group_version) or updated
    return updated


def _group_members(
    group: Sequence[str],
    order: Sequence[_Member],
    members: Mapping[str, _Member],
) -> list[_Member]:
    """Resolve one ``fixed`` / ``linked`` group to concrete members, in workspace order.

    Group entries are matched with :func:`molt.globs.glob_match` -- the picomatch port, negation
    included -- rather than compared literally, which is upstream bug #1 (research README section
    3.4): ``match-fixed-constraint.ts:32`` uses ``.includes()``, so a documented glob silently
    matches nothing. A concrete name is a pattern that matches only itself, so a group that
    ``molt.config`` already expanded resolves to exactly itself.

    An entry matching no package contributes nothing. Upstream throws ``InternalError`` there;
    ``molt.config``'s ``fixedGroupsExist`` / ``linkedGroupsExist`` rules already warn about it at
    load time, which is both earlier and actionable.
    """
    matched = glob_match([member.name for member in order], group, key=normalize_name)
    return [members[normalize_name(name)] for name in matched]


def _releasing(
    releases: Mapping[str, _InternalRelease], group_members: Sequence[_Member]
) -> list[_InternalRelease]:
    """The group's releases that actually move a version, in plan order.

    ``none`` releases are excluded (``apply-links.ts:29``, ``match-fixed-constraint.ts:22``): a
    dev-only or ignored member is in the plan but is not "releasing", so it neither contributes to
    the group's bump nor gets dragged up by it.
    """
    keys = {member.normalized for member in group_members}
    return [
        release
        for key, release in releases.items()
        if key in keys and release.type is not BumpType.NONE
    ]


def _current_highest_version(group_members: Sequence[_Member]) -> Version:
    """The highest **on-disk** version across the whole group (``utils.ts:35-57``).

    Over every member, not just the releasing ones -- an unreleased member sitting at a higher
    version drags the group up to it. Read from the manifest and never from the evolving plan,
    which is what makes it a fixed target the loop converges on rather than one it chases.
    """
    return max(member.version for member in group_members)


def _realign(release: _InternalRelease, group_type: BumpType, group_version: Version) -> bool:
    """Raise one release onto its group's bump and version. Returns whether anything moved."""
    updated = False
    if release.type is not group_type:
        release.type = group_type
        updated = True
    if release.old_version != group_version:
        release.old_version = group_version
        updated = True
    return updated


# ======================================================================================
# Materialising new_version (``index.ts:86-121, 216-232``)
# ======================================================================================


def _materialise(
    release: _InternalRelease,
    *,
    config: PlanConfig,
    pre: PrePhase | None,
    suffix: _SnapshotSuffix | None,
) -> Release:
    """Freeze one settled internal release into its public :class:`Release`."""
    if suffix is None:
        new_version = _new_version(release, pre)
    else:
        new_version = _snapshot_version(release, pre=pre, config=config, suffix=suffix)
    return Release(
        name=release.name,
        type=release.type,
        old_version=release.old_version,
        new_version=new_version,
        changesets=list(release.changesets),
    )


def _new_version(release: _InternalRelease, pre: PrePhase | None) -> Version:
    """The version this release lands on -- ``incrementVersion`` (``increment.ts:5-25``).

    A ``none`` release keeps its old version verbatim, which is what makes it a changelog entry
    with no version change.

    ``pre`` is molt's replacement for the ``pre.json`` state machine (research README section 4.2):
    the counter comes from the version **on disk** via
    :func:`~molt.versioning.next_pre_number`, so the invocation reads no state and writes none. All
    the arithmetic is :mod:`molt.versioning`'s -- ``inc`` already reproduces node-semver's
    prerelease rules, which is why ``2.0.0rc0`` plus a minor is ``2.0.0rc1`` and not ``2.1.0rc1``
    (design D10).
    """
    if release.type is BumpType.NONE:
        return release.old_version
    base = inc(release.old_version, release.type)
    if pre is None:
        return base
    counter = next_pre_number(release.old_version)
    separator = "." if pre == "dev" else ""
    return Version(f"{base}{separator}{pre}{counter}")


def _snapshot_version(
    release: _InternalRelease,
    *,
    pre: PrePhase | None,
    config: PlanConfig,
    suffix: _SnapshotSuffix,
) -> Version:
    """The throwaway version for a snapshot release (``index.ts:86-110``, adapted to PEP 440).

    A ``none`` release keeps its old version and takes **no** suffix, exactly as upstream.

    The base is ``0.0.0`` unless ``snapshot.use_calculated_version`` is set. That default is
    upstream's and its reasoning survives the port: a snapshot built on the calculated ``1.0.0``
    would outrank a legitimate ``1.0.0b0`` for anyone who opted into prereleases of that release.
    """
    if release.type is BumpType.NONE:
        return release.old_version
    base = (
        _new_version(release, pre) if config.snapshot_use_calculated_version else Version("0.0.0")
    )
    local = "" if suffix.local is None else f"+{suffix.local}"
    return Version(f"{base}.dev{suffix.dev}{local}")


def _snapshot_suffix(
    snapshot: SnapshotParams, config: PlanConfig, moment: datetime
) -> _SnapshotSuffix:
    """Compose the snapshot suffix once for the whole plan (``getSnapshotSuffix``, ``:37-84``).

    ``moment`` is the single timestamp read at entry, so a multi-package plan cannot straddle a
    second boundary and emit two suffixes -- on PyPI that would burn two version numbers for one
    snapshot, permanently (design D7).

    Where upstream joins tag and datetime into one semver prerelease string, PEP 440 splits them:
    the datetime becomes the ``.devN`` counter and the free-form part becomes the local segment. A
    ``prerelease_template`` therefore renders **into the local segment**, the only free-form field
    PEP 440 defines; its placeholders and its two validation errors are ported unchanged.
    """
    datetime_digits = moment.strftime(_DATETIME_FORMAT)
    template = config.snapshot_prerelease_template
    if template is None:
        return _SnapshotSuffix(
            dev=datetime_digits,
            local=None if snapshot.tag is None else _local_segment(snapshot.tag),
        )

    if "{tag}" not in template and snapshot.tag is not None:
        raise MoltError(
            'Failed to compose snapshot version: "{tag}" placeholder is missing, but the '
            f"snapshot parameter is defined (value: '{snapshot.tag}')"
        )
    values: dict[str, str | None] = {
        "commit": snapshot.commit,
        "commit-short": None if snapshot.commit is None else snapshot.commit[:7],
        "tag": snapshot.tag,
        "timestamp": str(int(moment.timestamp() * 1000)),
        "datetime": datetime_digits,
    }
    return _SnapshotSuffix(dev=datetime_digits, local=_local_segment(_render(template, values)))


def _render(template: str, values: Mapping[str, str | None]) -> str:
    """Substitute the snapshot placeholders, using a replacement **function**.

    Design D8 / research README section 3.3: with a replacement *string*, a commit sha or tag
    containing ``\\1`` or ``\\g<0>`` would be expanded as a backreference and corrupt the version.
    A function replacement is inserted verbatim, which is the same reason upstream passes a callback
    to ``String.replace``.
    """

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        value = values[key]
        if value is None:
            raise MoltError(
                f'Failed to compose snapshot version: "{{{key}}}" placeholder is used '
                "without having a value defined!"
            )
        return value

    return _PLACEHOLDERS.sub(substitute, template)


def _local_segment(text: str) -> str | None:
    """Fold arbitrary text into a legal PEP 440 local version label, or ``None`` if nothing is left.

    A local label is alphanumerics separated by ``.``; every other run of characters collapses to
    the separator. Snapshots already target a non-PyPI index -- PyPI rejects ``+local`` outright --
    which is what makes the local segment usable here at all (research README section 4.3).
    """
    cleaned = _NOT_LOCAL_SAFE.sub(".", text).strip(".")
    return cleaned or None
