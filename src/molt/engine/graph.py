"""The workspace dependents graph -- who depends on whom, and how tightly.

Ports ``packages/get-dependents-graph/src/index.ts`` and its inner
``get-dependency-graph.ts`` (research test-suite doc 01, the ``get-dependency-graph`` section,
rows 1-7; research doc 02, "Engine mechanics confirmed from source", the graph-construction
bullets; ``roadmap/research/changesets-01-core-versioning-engine.md`` sections 5.1-5.3 and 4.4).

This module answers one question per dependency declaration -- *is this an edge, and is it
version-constrained?* -- and nothing else. It does **not** decide who gets released; that is the
fixpoint loop's job (build step 7). Getting an edge wrong here is invisible and expensive: an edge
that should not exist suppresses a dependent's release, and one that should not be missing
over-releases, so every branch of :func:`classify_dependency` is pinned by its own test row in
``tests/engine/test_dependents_graph.py``.

Three deliberate divergences from upstream
-------------------------------------------
1. **Diagnostics are values, not console writes** (design D6). ``get-dependency-graph.ts:124,138``
   carries a ``// TODO: replace with returning errors/warnings`` and writes to ``console.error``;
   :func:`get_dependents_graph` returns ``(graph, valid, errors)`` and ``valid`` is *derived* as
   ``not errors``, so the two can never disagree.

2. **One classifier, four outcomes** (design D1). Upstream sprinkles the rules through the walk.
   Here every rule is a branch of :func:`classify_dependency`, because the rules interact: a
   dev-group path source is dropped while a runtime path source is an error, and a workspace
   marker is an edge without validation while a workspace source *carrying* a specifier is
   validated like any other.

3. **Unparseable is not invalid** (design D2). A specifier molt cannot parse is skipped in
   silence; a specifier molt *can* parse and which provably excludes the dependency's on-disk
   version is an error. Conflating them makes molt reject workspaces whose syntax it has simply
   not learned yet.

Cycles are **not** an error (design D8). A cyclic Python workspace is legal; the publish step
chunks it topologically (build step 20).

The input shape
---------------
The workspace is read **structurally**, exactly as ``tests/engine/test_assemble.py`` declares:
``packages.root_dir``, ``packages.packages[i].dir`` and
``.manifest.{name, version, dependencies, dev_dependencies, optional_dependencies}``. That is the
ported ``@changesets/types`` ``Packages`` shape. :class:`molt.ecosystem.Workspace` is *not* that
shape -- it carries PEP 508 requirement lists and ``pathlib`` directories -- so an adapter between
the two is owed by whichever change first drives the engine from real discovery. The
``[tool.uv.sources]`` spellings this module classifies (``workspace:*``, a bare path) likewise
arrive already normalized into the constraint slot; surfacing them out of a manifest is the
ecosystem layer's job, not this one's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal, Protocol

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from molt.names import normalize_name
from molt.versioning import caret, is_unconstrained, parse_range, satisfies, tilde

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

__all__ = [
    "WORKSPACE_PREFIX",
    "Classification",
    "DependencyKind",
    "EdgeKind",
    "GraphConfig",
    "GraphError",
    "ManifestLike",
    "PackageLike",
    "PackagesLike",
    "classify_dependency",
    "get_dependents_graph",
    "relative_package_path",
    "resolve_workspace_range",
]

#: The marker a ``[tool.uv.sources]`` workspace entry normalizes to. Upstream's spelling is pnpm's
#: ``workspace:`` protocol (``get-dependency-graph.ts:109``); uv has no protocol string of its own,
#: so this is the normalized form the ecosystem layer is expected to produce for
#: ``{workspace = true}`` and for ``{path = ...}`` pointing inside the workspace.
WORKSPACE_PREFIX = "workspace:"

#: The three bare workspace markers. Each is an edge with nothing to validate; what it *resolves*
#: to is :func:`resolve_workspace_range`'s answer, and ``*`` is an exact pin rather than a
#: wildcard (``determine-dependents.ts:199-202``).
_WORKSPACE_MARKERS = ("*", "^", "~")

#: The only location spellings the dev-group drop rule recognises, standing in for upstream's
#: literal ``link:`` / ``file:`` prefix test (``get-dependency-graph.ts:26-31``). Anything else
#: carrying a scheme falls through to the runtime rule and is an error even in a dev group -- that
#: asymmetry is upstream's, and it is ported rather than smoothed over.
_BARE_PATH_PREFIXES = ("file:", "link:")

_LOCATION_REASON = "a location is not a version constraint"
_MISMATCH_REASON = "the constraint does not accept that version"
_WORKSPACE_PATH_REASON = "the workspace path does not point at that package"

#: Which manifest field a declaration came from. ``optional`` (extras) shares ``runtime``'s bump
#: semantics upstream (``determine-dependents.ts:101-107``); ``dev`` is the odd one out
#: (``:108-117``) and is the only kind ``ignore_dev`` and the path-source drop rule single out.
DependencyKind = Literal["runtime", "optional", "dev"]


class EdgeKind(Enum):
    """The four outcomes of classifying one dependency declaration (design D1)."""

    NOT_AN_EDGE = "not-an-edge"
    EDGE_UNCONSTRAINED = "edge-unconstrained"
    EDGE_CONSTRAINED = "edge-constrained"
    INVALID = "invalid"


@dataclass(frozen=True)
class Classification:
    """What one dependency declaration turned out to be.

    ``version_range`` is set only for :attr:`EdgeKind.EDGE_CONSTRAINED`, ``reason`` only for
    :attr:`EdgeKind.INVALID`. ``workspace_source`` records that the declaration came from
    ``[tool.uv.sources]``, which the propagation pass needs: an unconstrained *workspace* edge
    resolves through :func:`resolve_workspace_range` and can very much force a bump, while an
    unconstrained *plain* edge never can (design D3). Collapsing the two is how ``workspace:*``
    would silently stop churning its dependents.
    """

    kind: EdgeKind
    version_range: SpecifierSet | None = None
    reason: str | None = None
    workspace_source: bool = False

    @property
    def is_edge(self) -> bool:
        """Whether this declaration belongs in the graph at all."""
        return self.kind in (EdgeKind.EDGE_CONSTRAINED, EdgeKind.EDGE_UNCONSTRAINED)


@dataclass(frozen=True)
class GraphError:
    """One provably broken dependency declaration (design D6).

    Carries the dependent, the dependency, the version on disk and the range as written, so a
    caller can render it however it likes -- and so a test can assert on the facts rather than on
    molt's wording. Upstream's equivalent is a ``console.error`` string nobody can inspect.
    """

    dependent: str
    dependency: str
    dependency_version: str
    version_range: str
    reason: str

    def __str__(self) -> str:
        return (
            f'"{self.dependent}" declares "{self.dependency}" as "{self.version_range}", '
            f'but "{self.dependency}" is at {self.dependency_version} -- {self.reason}'
        )


class ManifestLike(Protocol):
    """The ``[project]`` view the graph reads off a workspace member.

    Each dependency map takes a declared name to the constraint or source text as written -- the
    PEP 508 *tail*, so ``""`` for a bare requirement and ``"@ <url>"`` for a direct reference.
    """

    @property
    def name(self) -> str: ...
    @property
    def version(self) -> str | None: ...
    @property
    def dependencies(self) -> Mapping[str, str]: ...
    @property
    def dev_dependencies(self) -> Mapping[str, str]: ...
    @property
    def optional_dependencies(self) -> Mapping[str, str]: ...


class PackageLike(Protocol):
    """A workspace member: its manifest plus its directory, in POSIX form."""

    @property
    def manifest(self) -> ManifestLike: ...
    @property
    def dir(self) -> str: ...


class PackagesLike(Protocol):
    """The discovered workspace."""

    @property
    def root_package(self) -> PackageLike | None: ...
    @property
    def root_dir(self) -> str: ...
    @property
    def packages(self) -> Sequence[PackageLike]: ...


class GraphConfig(Protocol):
    """The only configuration key graph construction reads.

    Typed structurally on purpose: both :class:`molt.config.Config` and the engine suite's
    ``FakeConfig`` satisfy it, so the graph does not depend on which one a caller holds.
    """

    @property
    def bump_workspace_sources_only(self) -> bool: ...


@dataclass(frozen=True)
class _Node:
    """A workspace member, normalized to what the walk actually needs."""

    name: str
    version: Version | None
    path: str
    manifest: ManifestLike


@dataclass(frozen=True)
class _Declaration:
    """One dependency declaration, after the manifest sections are flattened."""

    name: str
    version_range: str
    kind: DependencyKind


# ======================================================================================
# The classifier -- design D1: every rule below is a branch of this one function
# ======================================================================================


def classify_dependency(
    *,
    dependency: str,
    version_range: str,
    dependency_version: Version | None,
    dependency_path: str,
    kind: DependencyKind,
    bump_workspace_sources_only: bool = False,
) -> Classification:
    """Classify one declaration of ``dependency`` into exactly one :class:`EdgeKind`.

    ``version_range`` is the constraint or source text as written: a PEP 508 tail (``""``,
    ``"==1.0.0"``, ``"@ git+https://..."``), a normalized ``[tool.uv.sources]`` marker
    (``"workspace:*"``), or a bare location (``"file:../pkg-a"``). ``dependency_path`` is the
    dependency's directory relative to the workspace root, POSIX-normalized.

    ``dependency_version`` is the version **on disk** -- never a planned one (design D4). ``None``
    means the distribution is dynamically versioned, so there is nothing to validate against; the
    edge is recorded and no claim of invalidity is made, since absence of knowledge is not evidence
    of breakage.

    The branch order is load-bearing and mirrors upstream's: the dev-group drop happens inside
    ``getAllDependencies`` before anything else looks at the value
    (``get-dependency-graph.ts:26-31``), and the workspace-sources-only skip sits between the
    workspace block and the location check (``:107-132``).
    """
    raw = version_range.strip()

    # 1. A dev-group path source never reaches validation: developing against a checkout is a
    #    normal local convenience, not a packaging defect (research doc 01, row 1).
    if kind == "dev" and raw.startswith(_BARE_PATH_PREFIXES):
        return _NOT_AN_EDGE

    # 2. A workspace source has its own rules, and they run before every flag below.
    if raw.startswith(WORKSPACE_PREFIX):
        return _classify_workspace_source(
            raw=raw,
            dependency_version=dependency_version,
            dependency_path=dependency_path,
        )

    # 3. With the flag on, a dependency that is not workspace-sourced is not molt's business --
    #    the edge goes, matched or not (``get-dependency-graph.ts:130-132``).
    if bump_workspace_sources_only:
        return _NOT_AN_EDGE

    # 4. A value carrying a URL scheme is a *location*. Upstream's discriminator is the same
    #    (``isProtocolRange``, ``:41``): a runtime dependency pinned to a location cannot state
    #    which version it needs, so it is broken rather than merely unreadable.
    if _is_location(raw):
        return Classification(EdgeKind.INVALID, reason=_LOCATION_REASON)

    # 5. Everything left is a PEP 508 requirement tail (research README section 4.5).
    requirement = _parse_requirement(dependency, raw)
    if requirement is None:
        return _NOT_AN_EDGE  # design D2: molt cannot read it, which is not the same as broken
    if requirement.url is not None:
        # A direct reference has an *empty* specifier, so a ``parse_range(...) is None`` test would
        # fall straight through to the unconstrained rule and silently invent an edge with no
        # constraint. Step 4 normally catches these; this is the belt for that brace.
        return Classification(EdgeKind.INVALID, reason=_LOCATION_REASON)

    specifier = requirement.specifier
    if is_unconstrained(specifier):
        # The PEP 440 analogue of npm ``*``: a real edge that can never force a release (D3).
        return _EDGE_UNCONSTRAINED
    if dependency_version is None or satisfies(dependency_version, specifier):
        return Classification(EdgeKind.EDGE_CONSTRAINED, version_range=specifier)
    return Classification(EdgeKind.INVALID, reason=_MISMATCH_REASON)


def _classify_workspace_source(
    *,
    raw: str,
    dependency_version: Version | None,
    dependency_path: str,
) -> Classification:
    """Classify a ``workspace:`` source (``get-dependency-graph.ts:107-129``).

    Four shapes, in the order they are tested: a bare marker (edge, nothing to validate), a real
    specifier (validated like any other), a matching relative path (edge), and anything else --
    a path that points somewhere the named package is not, which is provably wrong.
    """
    rest = raw[len(WORKSPACE_PREFIX) :].strip()
    if not rest or rest in _WORKSPACE_MARKERS:
        return _WORKSPACE_EDGE

    specifier = parse_range(rest)
    if specifier is not None:
        if is_unconstrained(specifier):
            return _WORKSPACE_EDGE
        if dependency_version is None or satisfies(dependency_version, specifier):
            return Classification(
                EdgeKind.EDGE_CONSTRAINED, version_range=specifier, workspace_source=True
            )
        return Classification(EdgeKind.INVALID, reason=_MISMATCH_REASON)

    if _same_path(rest, dependency_path):
        return _WORKSPACE_EDGE
    return Classification(EdgeKind.INVALID, reason=_WORKSPACE_PATH_REASON)


_NOT_AN_EDGE = Classification(EdgeKind.NOT_AN_EDGE)
_EDGE_UNCONSTRAINED = Classification(EdgeKind.EDGE_UNCONSTRAINED)
_WORKSPACE_EDGE = Classification(EdgeKind.EDGE_UNCONSTRAINED, workspace_source=True)


# ======================================================================================
# What a workspace marker resolves to (``determine-dependents.ts:173-228``)
# ======================================================================================


def resolve_workspace_range(
    version_range: str, old_version: Version, *, dependency_path: str
) -> SpecifierSet | None:
    """Resolve a ``workspace:`` source against the dependency's **old** version (design D4).

    ``old_version`` is read once, off disk, and held constant for the whole dependent search --
    one of the three reasons the fixpoint loop terminates (research README section 3.1). Resolving
    against a *planned* version would make graph construction depend on the plan it feeds.

    ``workspace:*`` is an exact pin, **not** a wildcard, which is precisely why it maximises
    dependent churn (research README section 3.3, doc 01 section 14.5). A path that matches no
    package directory resolves to ``None`` rather than guessing.

    Returns ``None`` for anything that is not a workspace source.
    """
    raw = version_range.strip()
    if not raw.startswith(WORKSPACE_PREFIX):
        return None

    rest = raw[len(WORKSPACE_PREFIX) :].strip()
    if not rest or rest == "*":
        return _exact(old_version)
    if rest == "^":
        return caret(old_version)
    if rest == "~":
        return tilde(old_version)

    specifier = parse_range(rest)
    if specifier is not None:
        return specifier
    return _exact(old_version) if _same_path(rest, dependency_path) else None


# ======================================================================================
# The walk
# ======================================================================================


def get_dependents_graph(
    packages: PackagesLike,
    config: GraphConfig,
    *,
    ignore_dev: bool = False,
) -> tuple[dict[str, list[str]], bool, list[GraphError]]:
    """Build the dependents graph: ``{package name: [names that depend on it]}``.

    Returns ``(graph, valid, errors)``. ``valid`` is derived as ``not errors`` so validity and
    diagnostics cannot drift apart (design D6), and nothing is ever written to the console.

    Keys are insertion-ordered with the **root package first**, then the workspace members in
    discovery order; every member gets a key even with no dependents. The order is observable --
    it reaches the release plan and then the changelog (research doc 01 section 5.1;
    ``get-dependents-graph/src/index.ts:24-38,52-54``).

    Only workspace *members* are scanned as dependents, so the root package is never recorded as
    one: a root-level range bumps nothing, and ``apply-release-plan`` rewrites the root manifest
    separately (``apply-release-plan/src/index.ts:175-186``). Note this follows from the input
    shape -- a backend that lists the root among ``packages`` is declaring it a real member, and it
    is treated as one.

    ``ignore_dev`` drops every dev-group declaration before any other check. It is the *only*
    thing separating the two graphs in the system (research doc 01 section 5.3): the release engine
    always leaves it false so dev edges reach ``apply``, while the config skip-tree rule sets it
    true, because a stale dev range on a skipped package is harmless.
    """
    root_dir = packages.root_dir
    nodes = [_node(package, root_dir) for package in packages.packages]

    graph: dict[str, list[str]] = {}
    root_package = packages.root_package
    if root_package is not None:
        graph[root_package.manifest.name] = []
    for node in nodes:
        graph.setdefault(node.name, [])

    index = {normalize_name(node.name): node for node in nodes}
    errors: list[GraphError] = []

    for node in nodes:
        for declaration in _declarations(node.manifest, ignore_dev=ignore_dev):
            target = index.get(normalize_name(declaration.name))
            if target is None:
                continue  # external distribution: no edge, no error
            classification = classify_dependency(
                dependency=target.name,
                version_range=declaration.version_range,
                dependency_version=target.version,
                dependency_path=target.path,
                kind=declaration.kind,
                bump_workspace_sources_only=config.bump_workspace_sources_only,
            )
            if classification.kind is EdgeKind.INVALID:
                errors.append(
                    GraphError(
                        dependent=node.name,
                        dependency=target.name,
                        dependency_version=str(target.version),
                        version_range=declaration.version_range,
                        reason=classification.reason or "",
                    )
                )
                continue
            if classification.is_edge:
                dependents = graph[target.name]
                if node.name not in dependents:
                    dependents.append(node.name)

    return graph, not errors, errors


def _declarations(manifest: ManifestLike, *, ignore_dev: bool) -> Iterator[_Declaration]:
    """Flatten the manifest's dependency sections into one declaration per name.

    Upstream's ``getAllDependencies`` folds every section into a single map, so later sections
    overwrite earlier ones and a doubly-declared package has its edge decided by **one** range
    (``get-dependency-graph.ts:19-36``). molt puts the dev group last, so the dev range wins
    (design D5). There is no upstream case to be faithful to -- an npm map cannot hold a duplicate
    key -- and the dev group is the more specific, more local declaration, the one a developer
    edits. Pinned by ``test_the_dev_group_range_wins_when_a_dependency_is_declared_twice``.
    """
    sections: tuple[tuple[DependencyKind, Mapping[str, str]], ...] = (
        ("runtime", manifest.dependencies),
        ("optional", manifest.optional_dependencies),
        ("dev", manifest.dev_dependencies),
    )
    flattened: dict[str, _Declaration] = {}
    for kind, declarations in sections:
        if kind == "dev" and ignore_dev:
            continue
        for name, version_range in declarations.items():
            flattened[normalize_name(name)] = _Declaration(
                name=name, version_range=version_range, kind=kind
            )
    return iter(flattened.values())


def _node(package: PackageLike, root_dir: str) -> _Node:
    manifest = package.manifest
    return _Node(
        name=manifest.name,
        version=_version(manifest.version),
        path=relative_package_path(package.dir, root_dir),
        manifest=manifest,
    )


# ======================================================================================
# Small shared predicates
# ======================================================================================


def _is_location(text: str) -> bool:
    """Whether ``text`` is a location rather than a constraint.

    Upstream's ``isProtocolRange`` is ``range.indexOf(":") !== -1`` (``get-dependency-graph.ts:41``)
    and the Python reading is identical: no PEP 440 specifier contains a colon, while every URL and
    every path spelling that molt has to reject carries a scheme.
    """
    return ":" in text


def _parse_requirement(name: str, tail: str) -> Requirement | None:
    """Reassemble ``name`` and ``tail`` into a PEP 508 requirement, or ``None`` if it is not one."""
    try:
        return Requirement(f"{name} {tail}" if tail else name)
    except InvalidRequirement:
        return None


def _version(text: str | None) -> Version | None:
    """Parse an on-disk version; ``None`` for a dynamically-versioned distribution."""
    if text is None:
        return None
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _exact(version: Version) -> SpecifierSet:
    return SpecifierSet(f"=={version}")


def _posix(path: str) -> str:
    """Normalize a path for comparison: POSIX separators, no leading or trailing slash.

    Both sides of every path comparison go through this, which is what keeps the
    ``workspace:<relpath>`` rules correct on Windows (research README section 5, item 15).
    """
    return PurePosixPath(path.replace("\\", "/")).as_posix().strip("/")


def _same_path(left: str, right: str) -> bool:
    return _posix(left) == _posix(right)


def relative_package_path(directory: str, root: str) -> str:
    """``directory`` relative to ``root``, POSIX-normalized; unchanged when it is not below it.

    Public because it is one half of a two-sided comparison: this produces the ``dependency_path``
    that :func:`resolve_workspace_range` matches a ``workspace:<relpath>`` source against, and the
    release-plan engine has to derive the same value from the same input. Two independent
    normalizations would drift on Windows -- separators, a trailing slash, a root of ``"/"`` -- and
    the failure mode is a silently missing dependent bump, not an error.
    """
    package_path = _posix(directory)
    root_path = _posix(root)
    if not root_path:
        return package_path
    if package_path == root_path:
        return ""
    prefix = f"{root_path}/"
    return package_path[len(prefix) :] if package_path.startswith(prefix) else package_path
