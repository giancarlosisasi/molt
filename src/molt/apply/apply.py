"""Applying a release plan to a working tree: buffer every side effect, then flush once.

Ports ``packages/apply-release-plan/src/index.ts`` and ``version-package.ts`` (research doc 04
sections 1-3), and **does not port its transaction model, because it has none**. Upstream writes
each manifest inside a per-release loop (``index.ts:150-172``) and deletes changeset files in an
unordered ``Promise.all`` (``:197-227``). A failure on package 3 of 10 therefore leaves packages
1-2 bumped with their changesets still on disk; the next release plan is computed from the versions
now on disk, so the obvious recovery -- run it again -- **double-bumps** them. Research README
section 3.4 lists this as a bug molt does not port; section 5 item 12 makes buffer-then-flush a
stated differentiator. Nothing here writes until every byte of the outcome is known.

The shape (design D1 / D2)
--------------------------
1. **Plan.** Read manifests, join releases to packages, compute every edit, assemble every
   changelog entry. Pure computation over strings; the only I/O is reading. Everything that can
   fail on *user input* -- a release naming a package that is not in the workspace
   (``index.ts:92-102``), a changelog generator that raises (``:314-322``) -- fails here, which is
   what makes "no file was affected" true by construction rather than by a pre-flight check that
   could drift from the real one.
2. **Capture.** Read the prior bytes of every destination, recording "did not exist" as ``None``.
3. **Flush**, in the documented order below.
4. **Restore** every captured destination if any step raised, then re-raise.

Capture-and-restore rather than temp-file + rename, deliberately: rename gives atomicity **per
file**, and the invariant here is that the *whole plan* lands or none of it does. A rollback that
restored the manifests and left a ``CHANGELOG.md`` behind would be an entry for a release that
never happened.

The flush order (design D3, doc 04 section 1.2)
------------------------------------------------
:data:`SIDE_EFFECT_ORDER` states it. Two properties are load-bearing, and both are invisible on a
successful run -- they only decide whether a *failed* run is recoverable:

* **Content before consumption.** Changesets are deleted after every content write, so a run that
  dies mid-flush can never have consumed the input that produced the output.
* **The lockfile last.** It is derived from the final manifests, so it cannot be written from
  intermediate ones.

Where this diverges from ``uv lock`` (recorded as a gap)
---------------------------------------------------------
``tasks.md`` 7.3 and design D9 specify the lockfile refresh as a real ``uv lock`` subprocess. The
conformance suite requires the opposite and wins: ``tests/apply/test_apply.py``'s ``_WriteFailer``
observes the flush by patching ``Path.write_bytes`` / ``os.replace`` **in-process**, so a subprocess
write is invisible to it and ``test_side_effects_are_flushed_in_the_documented_order`` cannot see
``uv.lock`` at all; and the atomicity fixture declares a dependency across two members with no
``[tool.uv.sources]`` entry, which a real ``uv lock`` rejects outright. molt therefore edits the
recorded version of each released member in place, through the same byte-minimal primitive it uses
for manifests. The consequence is real and is why this is a gap and not a decision: a rewritten
dependency range also appears in ``uv.lock``'s ``requires-dist`` metadata, and this refresh does not
touch it.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from molt.apply.changelog_file import insert_changelog_entry
from molt.apply.edit_toml import edit_toml, set_dependency_specifier, specifier_region
from molt.apply.generators import resolve_generator
from molt.apply.ranges import exact_pin, rewrite_specifier
from molt.changelog import get_changelog_entry
from molt.ecosystem import is_private
from molt.errors import MoltError
from molt.names import normalize_name
from molt.versioning import BumpType, is_unconstrained, satisfies

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

__all__ = ["CHANGELOG_ESCAPE_LINES", "SIDE_EFFECT_ORDER", "apply_release_plan"]

_LOGGER = logging.getLogger("molt.apply")

#: The flush order, stated once so it is reviewable rather than emerging from call order
#: (design D3). ``tests/apply/test_apply.py::test_side_effects_are_flushed_in_the_documented_order``
#: pins it by observing real writes.
SIDE_EFFECT_ORDER: Final = (
    "manifests",
    "changelogs",
    "changeset deletions",
    "lockfile",
)

#: What ``apply`` reports before re-raising a changelog-generation failure
#: (``index.ts:314-322``). The second line is a promise this module keeps: generation runs entirely
#: within the planning phase, so at the moment it fails nothing has been written.
CHANGELOG_ESCAPE_LINES: Final = (
    "The following error was encountered while generating changelog entries",
    "We have escaped applying the changesets, and no files should have been affected",
)

#: The workspace lockfile, refreshed last.
LOCKFILE_NAME: Final = "uv.lock"

#: Where changeset files live, relative to the project root.
CHANGESET_DIRNAME: Final = ".changeset"

_PROJECT_VERSION_PATH: Final = ("project", "version")
_RUNTIME_SECTION: Final = ("project", "dependencies")


# ======================================================================================
# The shapes this module reads, structurally
# ======================================================================================
#
# Read structurally for the same reason `molt.engine` does: the conformance suite drives `apply`
# with the ported `@changesets/types` shapes (`tests/apply/fake_release_plan.py`), while
# `molt.ecosystem.Workspace` and `molt.config.Config` are different shapes again. The adapter
# between them is owed by whichever change first drives apply from real discovery -- the `version`
# command. Naming the fields in protocols is what keeps that adapter's job written down.


class ReleaseLike(Protocol):
    """One planned release.

    The two versions are ``Version | str`` because both spellings genuinely reach here:
    :class:`molt.engine.Release` carries parsed :class:`~packaging.version.Version` objects, while a
    plan read back from JSON -- or from the conformance fixture -- carries the strings it was
    serialised as. :func:`_version` is the one place that difference is resolved, and
    :class:`_PlannedRelease` is what the changelog layer sees, so nothing downstream has to know
    which it was handed.
    """

    @property
    def name(self) -> str: ...
    @property
    def type(self) -> BumpType: ...
    @property
    def old_version(self) -> Version | str: ...
    @property
    def new_version(self) -> Version | str: ...
    @property
    def changesets(self) -> Sequence[str]: ...


class ChangesetLike(Protocol):
    """A parsed changeset, as the plan carries it."""

    @property
    def id(self) -> str: ...
    @property
    def summary(self) -> str: ...
    @property
    def releases(self) -> Sequence[Any]: ...


class PlanLike(Protocol):
    """``molt.engine.ReleasePlan``: insertion-ordered releases and the changesets behind them."""

    @property
    def releases(self) -> Sequence[ReleaseLike]: ...
    @property
    def changesets(self) -> Sequence[ChangesetLike]: ...


class PackageLike(Protocol):
    """One discovered workspace member."""

    @property
    def name(self) -> str: ...
    @property
    def version(self) -> str: ...
    @property
    def dir(self) -> Path: ...
    @property
    def manifest_path(self) -> Path: ...


class PackagesLike(Protocol):
    """The discovered workspace: the root, the root's own manifest, and the members."""

    @property
    def root_dir(self) -> Path: ...
    @property
    def root_package(self) -> PackageLike | None: ...
    @property
    def packages(self) -> Sequence[PackageLike]: ...


class ApplyConfigLike(Protocol):
    """The five config keys ``apply`` reads.

    ``ignore`` arrives **already expanded to concrete workspace names** -- globbing happens in
    :mod:`molt.config`, and re-matching here is what produced upstream's ``matchFixedConstraint``
    bug (research README section 3.4). Literal membership under PEP 503 normalization is correct.
    """

    @property
    def changelog(self) -> object: ...
    @property
    def ignore(self) -> Sequence[str]: ...
    @property
    def private_packages_version(self) -> bool: ...
    @property
    def update_internal_dependencies(self) -> str: ...
    @property
    def bump_workspace_sources_only(self) -> bool: ...


# ======================================================================================
# Buffered side effects (design D1)
# ======================================================================================


@dataclass(frozen=True)
class _PlannedRelease:
    """A release with its version parsed -- what :mod:`molt.changelog` is typed against.

    :class:`molt.changelog.ReleaseLike` requires a real :class:`~packaging.version.Version`, and it
    is right to: its out-of-range rule compares the new version against a declared specifier, and a
    string only works there by accident of ``SpecifierSet.contains`` coercing it. Parsing once, at
    the boundary, is what keeps that accident from becoming the contract.
    """

    name: str
    type: BumpType
    new_version: Version
    changesets: tuple[str, ...]


@dataclass(frozen=True)
class _Write:
    """Replace ``destination``'s bytes."""

    destination: Path
    data: bytes

    def execute(self) -> None:
        self.destination.write_bytes(self.data)


@dataclass(frozen=True)
class _Delete:
    """Remove ``destination`` from the working tree."""

    destination: Path

    def execute(self) -> None:
        self.destination.unlink()


_Operation = _Write | _Delete


@dataclass
class _Manifests:
    """Every manifest text ``apply`` touches, buffered as strings until the flush.

    Insertion order is the order manifests are first read, and that is deliberately the order they
    are written in: the planning walk visits released packages in plan order and the workspace root
    last, so :meth:`pending` needs no separate sort to produce
    :data:`SIDE_EFFECT_ORDER`'s first group.
    """

    _original: dict[Path, str] = field(default_factory=dict)
    _current: dict[Path, str] = field(default_factory=dict)

    def text(self, path: Path) -> str:
        """The current buffered text of ``path``, reading it from disk on first use."""
        if path not in self._current:
            original = path.read_bytes().decode("utf-8")
            self._original[path] = original
            self._current[path] = original
        return self._current[path]

    def document(self, path: Path) -> dict[str, Any]:
        """``path`` **as found on disk**, parsed with ``tomllib`` -- the read path, as always.

        Deliberately the original and never the buffer. Every reader here asks the same question --
        what did the author declare -- and the changelog's out-of-range rule is only answerable
        against the pre-run text: once ``pkg-c==2.0.0`` has been rewritten to ``==2.0.1`` in the
        buffer, the dependency no longer looks like it left its range, and the "listed even below
        the gate" line silently disappears.
        """
        if path not in self._original:
            self.text(path)
        return tomllib.loads(self._original[path])

    def set(self, path: Path, text: str) -> None:
        self._current[path] = text

    def pending(self) -> Iterator[tuple[Path, str]]:
        """Every manifest whose bytes actually changed, in first-touched order.

        The comparison is what implements "a ``none`` release changes nothing": the version edit is
        computed, compared, and dropped, so no write happens and the path is never reported as
        touched. Upstream writes the byte-identical file anyway and still reports it
        (``index.ts:153-164``); that difference is asserted by
        ``test_a_none_type_release_changes_nothing``.
        """
        for path, text in self._current.items():
            if text != self._original[path]:
                yield path, text


# ======================================================================================
# The entry point
# ======================================================================================


def apply_release_plan(
    plan: PlanLike,
    packages: PackagesLike,
    config: ApplyConfigLike,
    *,
    cwd: Path,
    snapshot: str | None = None,
    pre: str | None = None,
) -> list[Path]:
    """Apply ``plan`` to the working tree and return every path it touched.

    ``snapshot`` names the snapshot tag when this is a snapshot release, and ``pre`` the prerelease
    tag when it is a ``--pre`` run; both are ``None`` for an ordinary release. A plan already
    carries its computed versions, so neither changes *what* a version becomes -- ``snapshot``
    switches dependency pins to exact ones (``version-package.ts:103-105``) and ``pre`` keeps the
    changeset files on disk (design D6), and those are the only two things they do.

    The returned list is the caller's ``git add`` argument -- ``molt version`` stages exactly these
    paths (``cli/src/commands/version/index.ts:132-138``) -- so it includes **deleted** changeset
    files. A deletion missing from the list is a deletion that never gets staged, and the release
    commit would silently keep the consumed changeset.

    Nothing is committed (design D8). Committing is the caller's decision, so ``--dry-run``, CI
    flows and the ``version`` command can each choose differently.

    Raises:
        MoltError: a release names a package that is not in the workspace, or the configured
            changelog generator cannot be loaded.
        Exception: whatever a changelog generator raises, after the two
            :data:`CHANGELOG_ESCAPE_LINES` are reported. Nothing has been written at that point.
    """
    root = Path(packages.root_dir)
    project_root = Path(cwd)
    manifests = _Manifests()

    matched = _match_releases(plan, packages)
    skipped = {
        release.name
        for release, package in matched
        if _should_skip(package, config, manifests=manifests)
    }
    live = [(release, package) for release, package in matched if release.name not in skipped]

    operations: list[_Operation] = []

    _plan_manifest_edits(
        live,
        packages=packages,
        config=config,
        manifests=manifests,
        snapshot=snapshot is not None,
    )
    operations.extend(_Write(path, text.encode("utf-8")) for path, text in manifests.pending())
    operations.extend(
        _plan_changelogs(
            live,
            plan=plan,
            config=config,
            manifests=manifests,
            packages=packages,
            project_root=project_root,
        )
    )
    operations.extend(
        _plan_changeset_deletions(plan, project_root=project_root, skipped=skipped, pre=pre)
    )
    operations.extend(_plan_lockfile(live, root=root))

    _flush(operations)
    return [operation.destination for operation in operations]


# ======================================================================================
# Planning: joining the plan to the workspace
# ======================================================================================


def _match_releases(
    plan: PlanLike, packages: PackagesLike
) -> list[tuple[ReleaseLike, PackageLike]]:
    """Pair every release with its workspace member, or fail naming the one that is missing.

    Runs before anything is read or written (``index.ts:92-102``), so an unknown package name
    aborts with a pristine tree. Names resolve under PEP 503: a changeset written against the PyPI
    display name ``Foo_Bar`` has to find the member declaring ``foo-bar``, or it silently releases
    nothing.
    """
    members = {normalize_name(package.name): package for package in packages.packages}
    matched: list[tuple[ReleaseLike, PackageLike]] = []
    for release in plan.releases:
        package = members.get(normalize_name(release.name))
        if package is None:
            raise MoltError(f"Could not find matching package for release of: {release.name}")
        matched.append((release, package))
    return matched


def _should_skip(package: PackageLike, config: ApplyConfigLike, *, manifests: _Manifests) -> bool:
    """Whether ``package`` is excluded from this release (``index.ts:214-221``).

    Two guards, and both keep the package's **changesets on disk** so the release can still happen
    once the exclusion is lifted: an ignored package, and -- with
    ``private_packages = { version = false }`` -- a private one. "Private" is the
    ``Private :: Do Not Upload`` classifier, the Python analogue of npm's ``"private": true``, not
    a ``private`` key.
    """
    normalized = normalize_name(package.name)
    if any(normalized == normalize_name(entry) for entry in config.ignore):
        return True
    if config.private_packages_version:
        return False
    project = manifests.document(package.manifest_path).get("project", {})
    classifiers = [entry for entry in project.get("classifiers", []) if isinstance(entry, str)]
    return is_private(classifiers)


# ======================================================================================
# Planning: manifest edits (``version-package.ts``)
# ======================================================================================


def _plan_manifest_edits(
    live: Sequence[tuple[ReleaseLike, PackageLike]],
    *,
    packages: PackagesLike,
    config: ApplyConfigLike,
    manifests: _Manifests,
    snapshot: bool,
) -> None:
    """Buffer the version and dependency-range edits for every manifest ``apply`` may rewrite.

    Which manifests those are is a closed set: the packages **in the release plan**, plus the
    workspace root (``index.ts:150-188``). A dependent left out of the plan is never visited, which
    is why ``test_upper_bound_is_preserved_when_the_lower_bound_moves`` puts its dependent in the
    plan explicitly.

    The root is visited last and **never versioned** -- it takes dependency edits only. When the
    root *is* the single package of a non-workspace repository it is already in ``live`` and its
    version moves like any other member's.
    """
    versions = {normalize_name(release.name): release for release, _ in live}
    for release, package in live:
        written = str(release.new_version)
        if written != package.version:
            manifests.set(
                package.manifest_path,
                edit_toml(manifests.text(package.manifest_path), _PROJECT_VERSION_PATH, written),
            )
        _rewrite_dependencies(
            package,
            versions=versions,
            config=config,
            manifests=manifests,
            snapshot=snapshot,
        )

    root_package = packages.root_package
    if root_package is not None and not any(
        package.manifest_path == root_package.manifest_path for _, package in live
    ):
        _rewrite_dependencies(
            root_package,
            versions=versions,
            config=config,
            manifests=manifests,
            snapshot=snapshot,
        )


def _rewrite_dependencies(
    package: PackageLike,
    *,
    versions: dict[str, ReleaseLike],
    config: ApplyConfigLike,
    manifests: _Manifests,
    snapshot: bool,
) -> None:
    """Rewrite every stale internal pin in ``package``'s manifest.

    Every dependency section is walked, not only the runtime one: a dev-group or extras pin goes
    stale exactly like a runtime pin, and the rewrite loop runs regardless of the dependent's own
    bump type (``version-package.ts:44-47``) -- a dev-only dependent takes ``none`` and still has
    its pin moved.
    """
    document = manifests.document(package.manifest_path)
    sources = _workspace_sources(document)
    gate = _gate(config)
    own = normalize_name(package.name)
    done: set[tuple[tuple[str, ...], str]] = set()

    for section, declared in _dependency_sections(document):
        for entry in declared:
            requirement = _parse_requirement(entry)
            if requirement is None:
                continue
            target = normalize_name(requirement.name)
            release = versions.get(target)
            if release is None or (section, target) in done:
                continue
            specifier = _new_specifier(
                requirement=requirement,
                entry=entry,
                release=release,
                own=own,
                workspace_sourced=target in sources,
                bump_workspace_sources_only=config.bump_workspace_sources_only,
                gate=gate,
                snapshot=snapshot,
            )
            if specifier is None:
                continue
            done.add((section, target))
            manifests.set(
                package.manifest_path,
                set_dependency_specifier(
                    manifests.text(package.manifest_path),
                    section=section,
                    name=requirement.name,
                    specifier=specifier,
                ),
            )


def _new_specifier(
    *,
    requirement: Requirement,
    entry: str,
    release: ReleaseLike,
    own: str,
    workspace_sourced: bool,
    bump_workspace_sources_only: bool,
    gate: BumpType,
    snapshot: bool,
) -> str | None:
    """The specifier to splice in, or ``None`` when this pin is left alone.

    The skip rules, in the order they must be tested (``version-package.ts:48-105``):

    1. **Self-reference.** A package pinning itself is pathological, and silently rewriting the pin
       is not obviously the right repair. **This rule is molt's, not a port** -- upstream's
       ``getDependencyVersionEdits`` has no self-name check at all, and the upstream test named for
       one is really exercising its ``file:`` guard. Owner decision, recorded as a gap.
    2. **Direct reference.** ``pkg @ git+https://...`` carries a location, not a constraint, and
       PEP 508 forbids combining a URL with a specifier -- there is no region to splice. This must
       be tested **before** rule 4, because a direct reference also has an empty specifier and
       would otherwise take the prerelease exception and produce an invalid requirement.
    3. **``bump_workspace_sources_only``.** With the flag on, a pin that is not backed by a
       ``[tool.uv.sources]`` workspace entry is not molt's business.
    4. **Unconstrained.** No specifier at all is the PEP 440 analogue of npm ``*``: the author
       meant it to float, so it is left alone -- **except** when the new version is a prerelease,
       which pip will not select for a bare requirement, making the tree uninstallable. Then, and
       only then, it is pinned exactly.
    5. **The gate**, with its exception first (design D5): a dependency that has **left the range**
       is updated whatever ``update_internal_dependencies`` says, because a constraint that no
       longer accepts the installed version is broken at any threshold. Written the other way
       round -- filtering by gate first -- the exception is unreachable and most rows still pass.
    """
    if normalize_name(requirement.name) == own:
        return None
    if requirement.url is not None:
        return None
    if bump_workspace_sources_only and not workspace_sourced:
        return None

    new_version = _version(release.new_version)
    if is_unconstrained(requirement.specifier):
        return exact_pin(new_version) if new_version.is_prerelease else None

    left_the_range = not satisfies(new_version, requirement.specifier)
    if not (left_the_range or release.type.rank >= gate.rank):
        return None
    if snapshot:
        return exact_pin(new_version)
    start, end = specifier_region(entry)
    return rewrite_specifier(entry[start:end], new_version)


# ======================================================================================
# Planning: changelog files
# ======================================================================================


def _plan_changelogs(
    live: Sequence[tuple[ReleaseLike, PackageLike]],
    *,
    plan: PlanLike,
    config: ApplyConfigLike,
    manifests: _Manifests,
    packages: PackagesLike,
    project_root: Path,
) -> list[_Operation]:
    """Buffer one ``CHANGELOG.md`` write per released package.

    Wrapped whole, because this is the only step that runs **user code** -- a third-party generator
    -- and it is placed before every write precisely so that failure window stays clean
    (doc 04 section 1.4). The two escape lines are reported and the original exception re-raised
    unchanged; a caller matching on the generator's own message still matches.
    """
    try:
        generator = resolve_generator(
            config.changelog,
            changeset_dir=project_root / CHANGESET_DIRNAME,
            project_root=project_root,
        )
        if generator is None:
            return []
        return _changelog_writes(
            live,
            plan=plan,
            config=config,
            manifests=manifests,
            packages=packages,
            generator=generator.generator,
            options=generator.options,
        )
    except Exception:
        for line in CHANGELOG_ESCAPE_LINES:
            _LOGGER.error(line)
        raise


def _changelog_writes(
    live: Sequence[tuple[ReleaseLike, PackageLike]],
    *,
    plan: PlanLike,
    config: ApplyConfigLike,
    manifests: _Manifests,
    packages: PackagesLike,
    generator: Any,
    options: Any,
) -> list[_Operation]:
    """Assemble every entry and fold it into the package's existing changelog."""
    releases = [_planned(release) for release in plan.releases]
    by_name = {release.name: release for release in releases}
    changesets = list(plan.changesets)
    gate = _gate(config)
    on_disk = _versions_on_disk(plan, packages)
    writes: list[_Operation] = []

    for release, package in live:
        document = manifests.document(package.manifest_path)
        entry = get_changelog_entry(
            by_name[release.name],
            releases,
            changesets,
            generator,
            deps=_resolved_runtime_deps(document, on_disk),
            dev_deps=_flatten(document.get("dependency-groups")),
            optional_deps=_flatten(document.get("project", {}).get("optional-dependencies")),
            update_internal_dependencies=gate,
            options=options,
        )
        if entry is None:
            continue
        destination = Path(package.dir) / "CHANGELOG.md"
        existing = destination.read_bytes().decode("utf-8") if destination.is_file() else None
        text = insert_changelog_entry(existing, package_name=package.name, entry=entry)
        if text == existing:
            continue
        writes.append(_Write(destination, text.encode("utf-8")))
    return writes


def _planned(release: ReleaseLike) -> _PlannedRelease:
    """Adapt one plan entry to the shape :mod:`molt.changelog` is typed against."""
    return _PlannedRelease(
        name=release.name,
        type=release.type,
        new_version=_version(release.new_version),
        changesets=tuple(release.changesets),
    )


def _resolved_runtime_deps(document: dict[str, Any], on_disk: dict[str, str]) -> list[str]:
    """Runtime requirements with workspace sources resolved to the constraint they stand for.

    Design D3, and the discriminator is **constrainedness, not whether the pin text changed**. A
    bare ``pkg-b`` pin gets no changelog line because it can never force a release. A bare
    ``pkg-b`` pin *backed by a workspace source* gets one, because ``[tool.uv.sources]`` resolves
    it to ``==<old version>`` -- a constrained edge that genuinely did force the release, even
    though the manifest text never moved. Reading the rule as a byte diff gets the second case
    wrong.
    """
    sources = _workspace_sources(document)
    resolved: list[str] = []
    for entry in _requirement_strings(document.get("project", {}).get("dependencies")):
        requirement = _parse_requirement(entry)
        if requirement is None:
            continue
        target = normalize_name(requirement.name)
        if (
            requirement.url is None
            and is_unconstrained(requirement.specifier)
            and target in sources
            and target in on_disk
        ):
            resolved.append(f"{requirement.name}=={on_disk[target]}")
            continue
        resolved.append(entry)
    return resolved


def _versions_on_disk(plan: PlanLike, packages: PackagesLike) -> dict[str, str]:
    """Every workspace member's version **before** this run, by normalized name.

    A released package's ``old_version`` wins over what discovery read, so a repeat run over an
    already-rewritten tree still resolves a workspace source to the constraint that was live when
    the plan was computed.
    """
    versions = {
        normalize_name(package.name): package.version
        for package in packages.packages
        if package.version
    }
    versions.update(
        {normalize_name(release.name): str(release.old_version) for release in plan.releases}
    )
    return versions


# ======================================================================================
# Planning: changeset consumption and the lockfile
# ======================================================================================


def _plan_changeset_deletions(
    plan: PlanLike, *, project_root: Path, skipped: set[str], pre: str | None
) -> list[_Operation]:
    """Buffer the deletion of every changeset this run consumed.

    Two guards:

    * **The skip guard** (``index.ts:214-223``). A changeset is removed only when **none** of its
      releases names a skipped package, so an ignored or unversioned-private package keeps its
      changeset and can still be released once the exclusion is lifted.
    * **Prerelease runs consume nothing** (design D6). ``concepts/prerelease.md:51`` and
      ``concepts/snapshots.md:61`` say the files stay, and they have to: the changesets *are* the
      counter, so a second ``--pre`` run over a consumed buffer would exit on "nothing to release".
      ``cli/docs/pre.md`` said the opposite and is the document that was wrong.
    """
    if pre is not None:
        return []
    skipped_names = {normalize_name(name) for name in skipped}
    deletions: list[_Operation] = []
    for changeset in plan.changesets:
        if any(normalize_name(released.name) in skipped_names for released in changeset.releases):
            continue
        path = project_root / CHANGESET_DIRNAME / f"{changeset.id}.md"
        if path.is_file():
            deletions.append(_Delete(path))
    return deletions


def _plan_lockfile(
    live: Sequence[tuple[ReleaseLike, PackageLike]], *, root: Path
) -> list[_Operation]:
    """Buffer the ``uv.lock`` refresh -- the last write, when there is a lockfile at all.

    Cosmetic in npm, **load-bearing in Python** (research README section 5 item 2): ``uv.lock``
    records every member's version, so a ``version`` run that left it stale makes the very next
    ``uv sync --locked`` fail. The record is **replaced**, never appended to; a stale entry left
    above the new one is precisely the shape that breaks a locked install.

    See the module docstring for why this is an in-place edit rather than a ``uv lock`` subprocess,
    and what that costs.
    """
    lockfile = root / LOCKFILE_NAME
    if not lockfile.is_file():
        return []
    original = lockfile.read_bytes().decode("utf-8")
    text = original
    records = tomllib.loads(original).get("package", [])
    positions = {
        normalize_name(record["name"]): index
        for index, record in enumerate(records)
        if isinstance(record, dict) and isinstance(record.get("name"), str)
    }
    for release, package in live:
        index = positions.get(normalize_name(package.name))
        if index is None:
            continue
        text = edit_toml(text, ("package", index, "version"), str(release.new_version))
    if text == original:
        return []
    return [_Write(lockfile, text.encode("utf-8"))]


# ======================================================================================
# The flush and its rollback (design D2)
# ======================================================================================


def _flush(operations: Sequence[_Operation]) -> None:
    """Execute every buffered side effect, restoring all of them if any one fails.

    The capture is taken for **every** destination before the first one is touched, so a failure
    anywhere in the sequence can undo everything ahead of it -- including a file that did not exist
    before, which is restored by removing it again. That is the difference between whole-plan
    atomicity and the per-file kind: a rollback that only restored manifests would leave a
    changelog entry for a release that never happened, and re-running would then double-bump.
    """
    captured = _capture(operations)
    try:
        for operation in operations:
            operation.execute()
    except BaseException:
        _restore(captured)
        raise


def _capture(operations: Sequence[_Operation]) -> list[tuple[Path, bytes | None]]:
    """The prior bytes of every destination, ``None`` meaning "did not exist"."""
    captured: list[tuple[Path, bytes | None]] = []
    seen: set[Path] = set()
    for operation in operations:
        destination = operation.destination
        if destination in seen:
            continue
        seen.add(destination)
        captured.append((destination, destination.read_bytes() if destination.is_file() else None))
    return captured


def _restore(captured: Sequence[tuple[Path, bytes | None]]) -> None:
    """Put every captured destination back exactly as it was found."""
    for destination, previous in reversed(captured):
        if previous is None:
            destination.unlink(missing_ok=True)
        else:
            destination.write_bytes(previous)


# ======================================================================================
# Manifest reading helpers
# ======================================================================================


def _dependency_sections(document: dict[str, Any]) -> Iterator[tuple[tuple[str, ...], list[str]]]:
    """Every dependency list in a manifest, as ``(key path, requirement strings)``.

    The key paths are spelled exactly as
    :func:`~molt.apply.edit_toml.set_dependency_specifier`'s ``section=`` argument, so what is read
    here and what is spliced there cannot disagree. ``[project.optional-dependencies]`` is included
    although upstream's ``DEPENDENCY_TYPES`` loop has no row for it: extras are a real declared
    constraint, they go stale like any other, and the engine already bumps optional-dependency
    dependents like runtime ones.
    """
    project = document.get("project")
    if isinstance(project, dict):
        yield _RUNTIME_SECTION, _requirement_strings(project.get("dependencies"))
        extras = project.get("optional-dependencies")
        if isinstance(extras, dict):
            for extra, entries in extras.items():
                yield ("project", "optional-dependencies", extra), _requirement_strings(entries)
    groups = document.get("dependency-groups")
    if isinstance(groups, dict):
        for group, entries in groups.items():
            yield ("dependency-groups", group), _requirement_strings(entries)


def _requirement_strings(value: object) -> list[str]:
    """The string entries of a dependency list.

    A PEP 735 ``{include-group = "..."}`` entry is an inline table, not a requirement, and is
    stepped over by shape before anything tries to parse it.
    """
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _flatten(value: object) -> list[str]:
    """Flatten an extras or dependency-groups table into one requirement list."""
    if not isinstance(value, dict):
        return []
    return [entry for entries in value.values() for entry in _requirement_strings(entries)]


def _workspace_sources(document: dict[str, Any]) -> set[str]:
    """Normalized names of the dependencies backed by a ``[tool.uv.sources]`` workspace entry.

    Only ``workspace = true`` counts. A ``{ path = ... }`` or ``{ git = ... }`` source names a
    location outside the workspace graph, and treating it as a workspace source would make
    ``bump_workspace_sources_only`` rewrite pins molt does not own.
    """
    sources = document.get("tool", {}).get("uv", {}).get("sources")
    if not isinstance(sources, dict):
        return set()
    return {
        normalize_name(name)
        for name, source in sources.items()
        if isinstance(source, dict) and source.get("workspace")
    }


def _parse_requirement(entry: str) -> Requirement | None:
    """Parse one PEP 508 requirement, or ``None`` when the entry is not one.

    An unreadable entry is stepped over rather than raised on: it is provably not a dependency on a
    released package, and one malformed line must not block the rewrite of a different one.
    """
    try:
        return Requirement(entry)
    except InvalidRequirement:
        return None


def _version(value: Version | str) -> Version:
    """Parse a planned version, naming the plan when it is not PEP 440.

    Accepts an already-parsed :class:`~packaging.version.Version` and re-parses its canonical form,
    which costs nothing and means every caller can stop asking which spelling the plan carried.
    """
    if isinstance(value, Version):
        return value
    try:
        return Version(value)
    except InvalidVersion as exc:
        raise MoltError(f"The release plan carries an invalid version: {value!r}") from exc


def _gate(config: ApplyConfigLike) -> BumpType:
    """``update_internal_dependencies`` as a :class:`~molt.versioning.BumpType`."""
    value = config.update_internal_dependencies
    return value if isinstance(value, BumpType) else BumpType(value)
