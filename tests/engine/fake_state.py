"""Python port of the changesets ``FakeFullState`` engine-test builder.

Ported from ``roadmap/research/test-suite/02-release-plan-engine.md`` section
"FakeFullState API -> Python plan-builder" (lines 153-201); upstream source
``assemble-release-plan/src/test-utils.ts:9-178``.

This module is **not** guarded by ``importorskip``: it is pure test infrastructure with no
dependency on ``molt.engine``, so it imports and runs today (its own sanity checks live in
``tests/engine/test_fake_state.py``). Only ``molt.versioning`` -- which is implemented -- is
imported, for :class:`~molt.versioning.BumpType`.

What is faithful to upstream
----------------------------
- **The default seed** (``getSimpleSetup``, ``test-utils.ts:52-68``): root package ``root@0.0.0``
  at dir ``/``, ``root_dir="/"``, one package ``pkg-a@1.0.0``, and exactly one changeset
  ``strange-words-combine`` carrying a ``patch`` on ``pkg-a``. A bare ``FakeFullState()``
  therefore already has a pending patch; tests wanting a clean slate pass
  ``FakeFullState(changesets=[])``. Many ported rows depend on that implicit patch.
- **The dir rule** (``getPackage``, ``test-utils.ts:21``):
  ``dir = "/packages/" + name`` with a single leading ``@`` stripped and ``/`` replaced by ``-``,
  so ``@ex/core`` -> ``/packages/ex-core``. This is load-bearing: for a ``workspace:<relpath>``
  dependency the engine compares the normalized relpath against the package dir taken relative to
  ``root_dir`` (``determine-dependents.ts:207-217``), which is what rows #34 and #50 exercise.
- ``add_package`` / ``add_changeset`` raise on duplicates and ``update_package`` raises when the
  package is missing, exactly like their JS counterparts (``ValueError`` stands in for ``Error``).

Deliberate molt divergences (research README section 4.4)
---------------------------------------------------------
- **``update_peer_dependency`` is dropped entirely.** ``peerDependencies`` have no Python
  analogue, so the concept -- and with it ``onlyUpdatePeerDependentsWhenOutOfRange`` -- is gone.
  ``update_dependencies`` therefore accepts ``kind`` in ``{"direct", "dev", "optional"}``.
- The manifest is ``pyproject.toml``-shaped rather than ``package.json``-shaped:
  :class:`PackageManifest` models ``[project].dependencies``, the PEP 735 ``[dependency-groups]``
  analogue of ``devDependencies`` (``dev_dependencies``), and ``[project.optional-dependencies]``
  -- extras -- as the analogue of ``optionalDependencies`` (``optional_dependencies``). Those last
  two are **not** interchangeable: upstream bumps an ``optionalDependencies`` dependent exactly
  like a ``dependencies`` one (a patch, ``determine-dependents.ts:101-107``) while a
  ``devDependencies``-only dependent yields ``none`` (``:108-117``). Research README section 4.5
  keeps them apart for the same reason, as does ``tests/conftest.py``'s ``ProjectBuilder``
  (``dev_deps`` vs ``optional_deps``). The dependency maps stay ``name -> constraint`` strings for
  ergonomics; real molt splices PEP 508 list entries.
- ``tool={"type": "yarn"}`` becomes ``tool={"type": "uv"}``. The engine ignores this field (the
  graph builder only reads ``root_package``, ``root_dir`` and ``packages``); it is kept so the
  shape stays recognisable against the reference fixture.

:class:`FakeConfig` is a **temporary stand-in for the not-yet-built ``molt.config.Config``**
(build step 3). Its field names are the canonical molt config keys recorded in
``roadmap/tdd-ddd/progress.md`` ("Config key names") -- notably ``bump_workspace_sources_only``
and a top-level ``update_internal_dependents``, promoted out of upstream's
``___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH`` wrapper. When ``molt.config`` lands, delete
this class and re-point ``default_config`` at ``molt.config.default_config``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Self

from molt.versioning import BumpType

__all__ = [
    "BARE_PATH_SOURCES",
    "DEFAULT_CHANGESET_ID",
    "DEFAULT_CHANGESET_SUMMARY",
    "DIRECT_REFERENCE_SOURCES",
    "PATH_SOURCES",
    "VCS_SOURCE",
    "ChangesetRelease",
    "DepEntry",
    "FakeConfig",
    "FakeFullState",
    "NewChangeset",
    "Package",
    "PackageManifest",
    "Packages",
    "default_config",
    "package_dir",
]

#: Every spelling a *location-pinned* dependency can take in Python -- the analogue of npm's
#: ``link:`` and ``file:`` protocols, which have no direct Python equivalent. Shared by
#: ``test_assemble.py`` (engine rows 11 + 12) and ``test_dependents_graph.py`` (graph rows 1 + 3)
#: so the two suites cannot drift apart on what counts as a path source.
#:
#: **The three families are not interchangeable, and that asymmetry is upstream's, not ours.**
#: ``getAllDependencies`` drops a *dev* dependency only when its range literally starts with
#: ``link:`` or ``file:`` (``get-dependency-graph.ts:26-31``); anything else falls through to
#: ``isProtocolRange`` (range contains ``:``) and makes the graph invalid
#: (``get-dependency-graph.ts:41,136-143``). So a bare ``file:`` dev dependency is silently
#: dropped, while a VCS URL in the same position is an error. Pick the family that matches the
#: rule under test -- do not merge them into one parametrize set.
#:
#: In a ``name -> spec`` map a PEP 508 direct reference keeps its ``@`` separator, while a
#: ``[tool.uv.sources]`` path/editable entry normalizes to a bare URL or relative path.

#: Bare location strings that start with ``file:`` -- the closest analogue of upstream's literal
#: ``link:``/``file:`` values, and the only family the dev-group drop rule recognises.
BARE_PATH_SOURCES: Final[tuple[str, ...]] = (
    "file:///workspace/packages/pkg-a",
    "file:../pkg-a",
)

#: PEP 508 direct references (``pkg-a @ <url>``) -- what a uv path/editable source resolves to.
DIRECT_REFERENCE_SOURCES: Final[tuple[str, ...]] = (
    "@ file:///workspace/packages/pkg-a",
    "@ git+https://example.invalid/pkg-a.git",
)

#: A VCS URL: a location, but not a ``file:`` one, so the dev-group drop rule does not apply.
VCS_SOURCE: Final[str] = "git+https://example.invalid/pkg-a.git@v1.0.0"

#: The union -- every spelling that is a location rather than a version constraint. Correct for
#: any rule that treats all of them alike (e.g. "a runtime location dependency is invalid").
PATH_SOURCES: Final[tuple[str, ...]] = (
    *BARE_PATH_SOURCES,
    *DIRECT_REFERENCE_SOURCES,
    VCS_SOURCE,
)

#: ``getChangeset`` defaults, ``test-utils.ts:32-33``. The id also matches the first value of the
#: shared ``seeded_ids`` fixture, which mirrors the reference ``vi.mock("human-id")``.
DEFAULT_CHANGESET_ID = "strange-words-combine"
DEFAULT_CHANGESET_SUMMARY = "base summary whatever"


# ======================================================================================
# Value objects -- the pyproject-shaped stand-ins for the reference package.json tree
# ======================================================================================


@dataclass(frozen=True)
class ChangesetRelease:
    """One ``{name, type}`` entry of a changeset's frontmatter (``types``: ``Release``)."""

    name: str
    type: BumpType


@dataclass
class NewChangeset:
    """A parsed changeset file: id, summary body, and the packages it releases."""

    id: str
    summary: str
    releases: list[ChangesetRelease]


@dataclass
class PackageManifest:
    """``[project]`` of a ``pyproject.toml``, standing in for ``package.json``.

    Each map takes a dependency name to its constraint string. The three fields carry **different
    bump semantics** and must not be collapsed (research README section 4.5;
    ``determine-dependents.ts:100-117``):

    - ``dependencies`` -> ``[project.dependencies]``, upstream ``dependencies``: a dependent that
      falls out of range takes a **patch**.
    - ``optional_dependencies`` -> ``[project.optional-dependencies]`` (extras), upstream
      ``optionalDependencies``: same switch case as ``dependencies``, so also a **patch**.
    - ``dev_dependencies`` -> PEP 735 ``[dependency-groups]``, upstream ``devDependencies``: a
      dev-only dependent yields **none**, because a dev dependency cannot break an install.

    Constraint strings may also be non-version markers (a ``workspace:`` source, a PEP 508 direct
    reference) -- the engine has to recognise and skip those rather than rewrite them.
    """

    name: str
    version: str
    dependencies: dict[str, str] = field(default_factory=dict)
    dev_dependencies: dict[str, str] = field(default_factory=dict)
    optional_dependencies: dict[str, str] = field(default_factory=dict)


@dataclass
class Package:
    """A workspace member: its manifest plus its POSIX-style directory."""

    manifest: PackageManifest
    dir: str


@dataclass
class Packages:
    """The whole workspace, mirroring ``@changesets/types`` ``Packages``."""

    root_package: Package
    root_dir: str
    packages: list[Package]
    tool: dict[str, str]


@dataclass(frozen=True)
class DepEntry:
    """One entry of the :meth:`FakeFullState.update_dependencies` bulk form.

    Upstream's ``type`` is ``"direct" | "peer" | "dev"`` (``test-utils.ts:108-130``); molt drops
    ``peer`` (research README section 4.4) and adds ``optional`` for the extras field, which
    upstream's builder had no writer for even though the engine handles ``optionalDependencies``
    (``determine-dependents.ts:102``).
    """

    name: str
    version_range: str
    kind: Literal["direct", "dev", "optional"] = "direct"


# ======================================================================================
# FakeConfig -- stand-in for molt.config.Config (build step 3)
# ======================================================================================


@dataclass(frozen=True)
class FakeConfig:
    """The ported ``@changesets/config`` ``defaultConfig``, in molt's key names.

    Frozen and tuple-valued so a test can derive a variant with
    ``dataclasses.replace(default_config, ignore=("pkg-b",))`` without mutating anyone else's
    fixture.

    Reference shape (research doc 01 section 1 "Default config"). It is split across two upstream
    files, and only five of these eleven fields come from the written defaults:
    ``packages/config/src/defaults.ts:7-18`` (``baseBranch``, ``ignore``, ``fixed``, ``linked``,
    ``updateInternalDependencies``) plus the schema defaults applied by
    ``normalizeWrittenConfig`` in ``packages/config/src/config.ts`` -- ``changedFilePatterns``
    ``:57-60``, ``privatePackages`` ``:95-105``, ``bumpVersionsWithWorkspaceProtocolOnly``
    ``:106-110``, ``snapshot.useCalculatedVersion`` / ``snapshot.prereleaseTemplate``
    ``:111-131``, and ``___experimentalUnsafeOptions.updateInternalDependents`` ``:148-156``.
    """

    ignore: tuple[str, ...] = ()
    fixed: tuple[tuple[str, ...], ...] = ()
    linked: tuple[tuple[str, ...], ...] = ()
    bump_workspace_sources_only: bool = False
    update_internal_dependents: Literal["out-of-range", "always"] = "out-of-range"
    update_internal_dependencies: Literal["patch", "minor"] = "patch"
    snapshot_use_calculated_version: bool = False
    snapshot_prerelease_template: str | None = None
    base_branch: str = "main"
    changed_file_patterns: tuple[str, ...] = ("**",)
    private_packages_version: bool = True


def default_config() -> FakeConfig:
    """Return the ported changesets default config (research doc 02, "default_config fixture")."""
    return FakeConfig()


# ======================================================================================
# The builder
# ======================================================================================


def package_dir(name: str) -> str:
    """Return the workspace-relative dir upstream's ``getPackage`` derives (``test-utils.ts:21``).

    ``"/packages/" + name.replace(/^@/, "").replace(/\\//g, "-")`` -- note that exactly **one**
    leading ``@`` is stripped, which is why this uses ``removeprefix`` rather than ``lstrip``.
    ``@ex/core`` -> ``/packages/ex-core``.
    """
    return "/packages/" + name.removeprefix("@").replace("/", "-")


def _make_package(name: str, version: str) -> Package:
    return Package(manifest=PackageManifest(name=name, version=version), dir=package_dir(name))


def _default_packages() -> Packages:
    """``getSimpleSetup().packages`` (``test-utils.ts:53-64``), with ``yarn`` -> ``uv``."""
    return Packages(
        root_package=_make_root_package(),
        root_dir="/",
        packages=[_make_package("pkg-a", "1.0.0")],
        tool={"type": "uv"},
    )


def _make_root_package() -> Package:
    return Package(manifest=PackageManifest(name="root", version="0.0.0"), dir="/")


def _default_changesets() -> list[NewChangeset]:
    """``getSimpleSetup().changesets`` (``test-utils.ts:65-67``): one patch on ``pkg-a``."""
    return [
        NewChangeset(
            id=DEFAULT_CHANGESET_ID,
            summary=DEFAULT_CHANGESET_SUMMARY,
            releases=[ChangesetRelease(name="pkg-a", type=BumpType.PATCH)],
        )
    ]


class FakeFullState:
    """Mutable builder over a :class:`Packages` tree and a changeset list.

    Every mutator returns ``self`` so setups read as one chained expression; upstream's methods
    mutate in place and return ``undefined`` (``test-utils.ts:80-177``).
    """

    def __init__(
        self,
        *,
        packages: Packages | None = None,
        changesets: list[NewChangeset] | None = None,
    ) -> None:
        """Shallow-merge ``packages`` / ``changesets`` over the default seed.

        Mirrors ``new FakeFullState(custom?)`` (``test-utils.ts:74-78``). Passing
        ``changesets=[]`` is the documented way to start without the seeded ``pkg-a`` patch.
        """
        self.packages: Packages = _default_packages() if packages is None else packages
        self.changesets: list[NewChangeset] = (
            _default_changesets() if changesets is None else changesets
        )

    # -- lookup -------------------------------------------------------------------------

    def _find_package(self, name: str) -> Package:
        """Return the member named ``name``; upstream throws ``No "<name>" package``."""
        for pkg in self.packages.packages:
            if pkg.manifest.name == name:
                return pkg
        raise ValueError(f'no "{name}" package in the fake workspace')

    # -- packages -----------------------------------------------------------------------

    def add_package(self, name: str, version: str) -> Self:
        """Append a member at :func:`package_dir`; raise on a duplicate name."""
        if any(pkg.manifest.name == name for pkg in self.packages.packages):
            raise ValueError(f"tried to add a second package with same name: {name}")
        self.packages.packages.append(_make_package(name, version))
        return self

    def update_package(self, name: str, version: str) -> Self:
        """Set an existing member's version; raise when it does not exist."""
        self._find_package(name).manifest.version = version
        return self

    # -- changesets ---------------------------------------------------------------------

    def add_changeset(
        self,
        *,
        id: str = DEFAULT_CHANGESET_ID,
        summary: str = DEFAULT_CHANGESET_SUMMARY,
        releases: Sequence[tuple[str, BumpType | str]] = (),
    ) -> Self:
        """Append a changeset; raise on a duplicate id (``test-utils.ts:88-93``).

        ``releases`` takes ``(name, bump)`` pairs where ``bump`` is a :class:`BumpType` or its
        string value, so a ported fixture can stay spelled ``("pkg-a", "patch")``.
        """
        if any(changeset.id == id for changeset in self.changesets):
            raise ValueError(f"tried to add a second changeset with same id: {id}")
        self.changesets.append(
            NewChangeset(
                id=id,
                summary=summary,
                releases=[
                    ChangesetRelease(name=name, type=BumpType(bump)) for name, bump in releases
                ],
            )
        )
        return self

    # -- dependencies -------------------------------------------------------------------

    def update_dependency(self, dependent: str, dependency: str, version_range: str) -> Self:
        """Write ``dependent``'s runtime constraint on ``dependency`` (``[project]`` deps)."""
        self._find_package(dependent).manifest.dependencies[dependency] = version_range
        return self

    def update_dev_dependency(self, dependent: str, dependency: str, version_range: str) -> Self:
        """Write ``dependent``'s dev-only constraint (PEP 735 ``[dependency-groups]``).

        This is the field that yields a ``none`` dependent bump; extras do **not** go here, see
        :meth:`update_optional_dependency`.
        """
        self._find_package(dependent).manifest.dev_dependencies[dependency] = version_range
        return self

    def update_optional_dependency(
        self, dependent: str, dependency: str, version_range: str
    ) -> Self:
        """Write ``dependent``'s extras constraint (``[project.optional-dependencies]``).

        The analogue of upstream ``optionalDependencies``, which bumps its dependents exactly like
        a runtime dependency (``determine-dependents.ts:101-107``).
        """
        self._find_package(dependent).manifest.optional_dependencies[dependency] = version_range
        return self

    def update_dependencies(self, dependent: str, entries: Sequence[DepEntry]) -> Self:
        """Bulk form of the three writers above, routed by :attr:`DepEntry.kind`."""
        manifest = self._find_package(dependent).manifest
        targets = {
            "dev": manifest.dev_dependencies,
            "optional": manifest.optional_dependencies,
            "direct": manifest.dependencies,
        }
        for entry in entries:
            targets[entry.kind][entry.name] = entry.version_range
        return self
