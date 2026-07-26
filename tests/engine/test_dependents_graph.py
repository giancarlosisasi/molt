"""Conformance tests for the dependents graph -- the engine's edge set.

Ports `packages/get-dependents-graph/src/get-dependency-graph.test.ts` (7 rows) as mapped in
`roadmap/research/test-suite/01-versioning-core.md` (get-dependency-graph section, rows 1-7), plus
the graph-construction facts collected in `roadmap/research/test-suite/02-release-plan-engine.md`
("Engine mechanics confirmed from source", the graph-construction bullets) and
`roadmap/research/changesets-01-core-versioning-engine.md` sections 5.1-5.3 and 4.4.

The graph is where several "does-not-bump" outcomes actually originate, so the rows below are not
cosmetic: an edge that should not exist silently suppresses a dependent release, and an edge that
should exist silently over-releases.

Three deliberate deviations from the reference, each recorded in the research
-----------------------------------------------------------------------------
1. **Structured errors instead of `console.error`.** Upstream carries a
   `// TODO: replace with returning errors/warnings` at `get-dependency-graph.ts:124` and `:138`
   and its tests assert on a mocked `console.error` behind `temporarilySilenceLogs`. molt does the
   TODO: `get_dependents_graph(packages, config) -> (graph, valid, errors)` and the assertions read
   the returned errors, so no log-capture machinery exists here (test contract section 5;
   research test-suite doc 01, rows 3/4/7 "mock -> unit"). Error *wording* is molt's, so the
   message assertions are structural: the dependent, the dependency, the expected version and the
   raw range must all be recoverable from the error.

2. **The returned graph is the *dependents* graph** -- `{package name: [names depending on it]}`,
   insertion-ordered with the root package first. That transpose
   (`get-dependents-graph/src/index.ts:4-63`) is what the fixpoint loop consumes; upstream's inner
   `getDependencyGraph` is not a second public entry point in molt. So "pkg-b depends on pkg-a" is
   asserted here as `"pkg-b" in graph["pkg-a"]`.

3. **npm range spellings become their Python analogues** (research README section 4.1, doc 01
   section 15.7):

   =========================  ==============================================================
   npm fixture                molt fixture
   =========================  ==============================================================
   `link:../bar`/`file:../b`  a PEP 508 direct reference (`file:///...`), which is how the
                              ecosystem backend normalises a uv path/editable source
                              (`[tool.uv.sources] pkg = {path = ...}` / `{editable = true}`)
   `0.9.0` (bare)             `==0.9.0` -- a bare version is not a PEP 440 specifier
   `latest` (dist-tag)        any non-specifier string with no scheme; PyPI has no dist-tags
   `*` (wildcard)             the **empty** specifier, i.e. a bare requirement with no version
   =========================  ==============================================================

Naming and signature deviations (test contract section 5)
---------------------------------------------------------
* **The entry point.** `roadmap/research/test-suite/01-versioning-core.md` ("Fixtures & tooling to
  build") proposes `build_dependency_graph(packages, root, *, ignore_dev=False,
  workspace_protocol_only=False)`, while `contracts/test-contract.md` section 5 pins
  `get_dependents_graph(packages, config)`. **The contract wins** -- it names the transpose the
  engine consumes, and it routes options through the config object rather than a second parameter
  list. The research spelling is recorded here so the divergence is not mistaken for drift.
* **`workspace_protocol_only` -> `config.bump_workspace_sources_only`**, since it is a real
  user-facing config option (upstream `bumpVersionsWithWorkspaceProtocolOnly`).
* **`ignore_dev` stays an argument, not a config field.** It is not user-facing upstream either:
  it is set by exactly one caller, the config rule `alsoSkipDependentsOfSkipped`
  (`packages/config/src/rules.ts:127-131`), while `assembleReleasePlan` always passes it false so
  dev edges reach `apply` (doc 01 section 5.3, "Two different graphs in the system"). These tests
  drive it as a keyword-only argument defaulted to `False`; see the dev-group section below.
* **`resolve_workspace_range`.** The workspace-marker resolution of `getDependencyVersionRanges`
  (`determine-dependents.ts:173-228`) has no name in the contract, so these tests drive it through
  a `molt.engine.resolve_workspace_range` seam.

The two invented names are reached via attribute access on the imported module rather than a
`from molt.engine import ...` line, so a missing seam fails only its own tests instead of erroring
collection for the whole file.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from tests.engine.fake_state import (
    BARE_PATH_SOURCES,
    PATH_SOURCES,
    DepEntry,
    FakeConfig,
    FakeFullState,
)

from molt.versioning import caret, tilde

engine = pytest.importorskip(
    "molt.engine", reason="build step 4 - engine not yet implemented (TDD target)"
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def v(text: str) -> Version:
    return Version(text)


def s(text: str) -> SpecifierSet:
    return SpecifierSet(text)


def two_package_workspace() -> FakeFullState:
    """Clean slate: root@0.0.0 at `/`, pkg-a@1.0.0, pkg-b@1.0.0, no changesets.

    The `dir` rule (`/packages/<mangled name>`, root at `/`) is load-bearing for the
    `workspace:<relpath>` rows -- see `roadmap/research/test-suite/02-release-plan-engine.md`,
    "Package.dir rule" (`get-dependency-graph.ts:87-89`).
    """
    return FakeFullState(changesets=[]).add_package("pkg-b", "1.0.0")


def build_graph(
    state: FakeFullState, config: FakeConfig, **options: Any
) -> tuple[Mapping[str, list[str]], bool, list[Any]]:
    """Call the TDD target and name the triple (test contract section 5).

    `options` carries the one non-config argument, `ignore_dev` -- see the module docstring.
    """
    graph, valid, errors = engine.get_dependents_graph(state.packages, config, **options)
    return graph, valid, errors


def error_text(errors: list[Any]) -> str:
    """Flatten the returned errors so wording-agnostic assertions can be made on them."""
    return " | ".join(str(error) for error in errors)


# The path-source spellings live in `fake_state` (imported above) so this file and
# `test_assemble.py` cannot drift on what counts as one. Which family a test uses is load-bearing:
# the dev-group drop rule recognises only `BARE_PATH_SOURCES` (upstream keys off a literal
# `link:`/`file:` prefix, `get-dependency-graph.ts:26-31`), while the runtime-dependency rule
# rejects every location alike (`PATH_SOURCES`, via `isProtocolRange`, `:41,136-143`).


# --------------------------------------------------------------------------------------
# Rows 1, 3 -- path / editable sources are not version constraints
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("source", BARE_PATH_SOURCES)
def test_a_path_source_in_a_dev_group_is_dropped_from_the_graph(
    source: str, default_config: FakeConfig
) -> None:
    """A dev-group path source is dropped before validation: no edge, still valid.

    Upstream drops devDependencies whose range starts with `link:`/`file:` inside
    `getAllDependencies`, so the dependent is never even considered -- which is exactly why the
    `link:`/`file:` engine rows (11, 12) end up with a single release.

    research test-suite doc 01 (get-dependency-graph, row 1) + doc 02 (graph construction);
    get-dependency-graph.ts:26-31; get-dependency-graph.test.ts:19-54.
    """
    state = two_package_workspace().update_dev_dependency("pkg-b", "pkg-a", source)

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == [], "a path source is not a version constraint -> no edge"
    assert valid is True
    assert errors == []


@pytest.mark.parametrize("source", PATH_SOURCES)
def test_a_path_source_in_a_runtime_dependency_is_invalid(
    source: str, default_config: FakeConfig
) -> None:
    """The same source in a runtime dependency is an error, not a silent skip.

    Upstream's discriminator is `isProtocolRange` -- the range contains a `:`. The Python reading
    is the same: a value carrying a URL scheme is a *source*, not a constraint, and a runtime
    dependency pinned to a local path cannot state which version it needs.

    research test-suite doc 01 (get-dependency-graph, row 3); get-dependency-graph.ts:41,136-143;
    get-dependency-graph.test.ts:93-135.
    """
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", source)

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == [], "an invalid constraint must not leave an edge behind"
    assert valid is False
    assert len(errors) == 1
    text = error_text(errors)
    for fragment in ("pkg-b", "pkg-a", "1.0.0", source):
        assert fragment in text, f"the error must name {fragment!r}"


# --------------------------------------------------------------------------------------
# Row 2 -- unparseable but scheme-less values are skipped silently
# --------------------------------------------------------------------------------------

NOT_A_CONSTRAINT = [
    # (range,   why)
    ("latest", "npm dist-tag; PyPI has none, but the branch is the same: not a specifier"),
    ("*", "npm's wildcard is not PEP 440 -- the Python spelling is the empty specifier"),
    ("main", "a git branch name someone wrote by hand; not a constraint, not a scheme"),
]


@pytest.mark.parametrize(("version_range", "why"), NOT_A_CONSTRAINT)
def test_an_unparseable_range_is_skipped_without_an_error(
    version_range: str, why: str, default_config: FakeConfig
) -> None:
    """No edge and no error: the author meant something, molt just cannot act on it.

    This is the branch that separates "cannot parse" (skip, stay valid) from "carries a scheme"
    (error). `molt.versioning.ranges.parse_range` returns `None` for both; the graph must not
    collapse them.

    research test-suite doc 01 (get-dependency-graph, row 2); get-dependency-graph.ts:43-53,147-149;
    get-dependency-graph.test.ts:56-91.
    """
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", version_range)

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == [], why
    assert valid is True, why
    assert errors == []


def test_a_bare_requirement_is_an_edge(default_config: FakeConfig) -> None:
    """The PEP 440 analogue of npm `*`: an unconstrained requirement is a real edge.

    Plain `*` **is** a valid range upstream, so the edge exists; it simply never bumps, because
    every new version satisfies it. The "never bumps" half of the fact is asserted end-to-end in
    `test_assemble.py` (engine row 9) -- here we only pin that the edge is present, since an
    absent edge would suppress the release for the wrong reason.

    research doc 02 (graph construction: "plain `*` is a graph edge"), README section 3.3;
    get-dependency-graph.ts:134-151; index.test.ts (engine row 9).
    """
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", "")

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == ["pkg-b"]
    assert valid is True
    assert errors == []


# --------------------------------------------------------------------------------------
# Rows 4, 5 -- a local constraint must accept the dependency's current version
# --------------------------------------------------------------------------------------


def test_a_constraint_that_rejects_the_current_version_is_invalid(
    default_config: FakeConfig,
) -> None:
    """The core rule: `satisfies(dep.version, dependent's range)` decides validity.

    Fixture adaptation only -- upstream writes the bare `0.9.0`, which is not a PEP 440
    specifier, so molt writes `==0.9.0`.

    research test-suite doc 01 (get-dependency-graph, row 4); get-dependency-graph.ts:134-143;
    get-dependency-graph.test.ts:137-178.
    """
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", "==0.9.0")

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == []
    assert valid is False
    assert len(errors) == 1
    text = error_text(errors)
    for fragment in ("pkg-b", "pkg-a", "1.0.0", "==0.9.0"):
        assert fragment in text, f"the error must name {fragment!r}"


def test_bump_workspace_sources_only_turns_the_mismatch_into_a_silent_skip(
    default_config: FakeConfig,
) -> None:
    """With the flag on, a non-workspace dependency is not molt's business at all.

    Same fixture as the row above; the flag flips the error into a `continue`.

    research test-suite doc 01 (get-dependency-graph, row 5); get-dependency-graph.ts:130-132;
    get-dependency-graph.test.ts:180-220.
    """
    config = dataclasses.replace(default_config, bump_workspace_sources_only=True)
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", "==0.9.0")

    graph, valid, errors = build_graph(state, config)

    assert graph["pkg-a"] == [], "the flag suppresses the edge, not just the error"
    assert valid is True
    assert errors == []


def test_bump_workspace_sources_only_keeps_workspace_edges(default_config: FakeConfig) -> None:
    """The flag skips *non*-workspace deps only -- workspace sources still produce edges.

    Without this the previous row would pass for the wrong reason (a flag that disabled the graph
    entirely).

    research doc 01 section 5.2 (the `bumpVersionsWithWorkspaceProtocolOnly` row of the branch
    table); get-dependency-graph.ts:107-132.
    """
    config = dataclasses.replace(default_config, bump_workspace_sources_only=True)
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", "workspace:*")

    graph, valid, errors = build_graph(state, config)

    assert graph["pkg-a"] == ["pkg-b"]
    assert valid is True
    assert errors == []


# --------------------------------------------------------------------------------------
# Rows 6, 7 + doc 02 -- workspace sources
# --------------------------------------------------------------------------------------

WORKSPACE_MARKERS = [
    # (range,        why)
    ("workspace:*", "exact pin of the current version, NOT a wildcard"),
    ("workspace:^", "caret of the current version"),
    ("workspace:~", "tilde of the current version"),
]


@pytest.mark.parametrize(("version_range", "why"), WORKSPACE_MARKERS)
def test_workspace_markers_are_edges_without_validation(
    version_range: str, why: str, default_config: FakeConfig
) -> None:
    """A bare workspace marker short-circuits to an edge; there is nothing to validate.

    The marker carries no version of its own, so it cannot disagree with the dependency's current
    version. What it *resolves to* is pinned separately below.

    research doc 02 (graph construction, workspace bullet), doc 01 section 5.2;
    get-dependency-graph.ts:109-115.
    """
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", version_range)

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == ["pkg-b"], why
    assert valid is True
    assert errors == []


def test_a_workspace_path_source_is_an_edge(default_config: FakeConfig) -> None:
    """A workspace path source pointing at the dependency's own directory is a local edge.

    `pkg-a` sits at `/packages/pkg-a` and the workspace root is `/`, so its posix relpath is
    `packages/pkg-a`. The comparison is posix-normalised on both sides, which is what keeps this
    correct on Windows (research README section 5, item 15).

    research test-suite doc 01 (get-dependency-graph, row 6); get-dependency-graph.ts:107-120;
    get-dependency-graph.test.ts:222-258.
    """
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", "workspace:packages/pkg-a")

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == ["pkg-b"]
    assert valid is True
    assert errors == []


def test_a_mismatched_workspace_path_source_is_invalid_with_no_edge(
    default_config: FakeConfig,
) -> None:
    """A workspace path that points somewhere else is an error and produces no edge.

    research test-suite doc 01 (get-dependency-graph, row 7); get-dependency-graph.ts:117-129;
    get-dependency-graph.test.ts:260-303.
    """
    state = two_package_workspace().update_dependency(
        "pkg-b", "pkg-a", "workspace:packages/not-pkg-a"
    )

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == []
    assert valid is False
    assert len(errors) == 1
    text = error_text(errors)
    for fragment in ("pkg-b", "pkg-a", "1.0.0", "workspace:packages/not-pkg-a"):
        assert fragment in text, f"the error must name {fragment!r}"


WORKSPACE_SPECIFIERS = [
    # (range,               edge,  valid, why)
    ("workspace:==1.0.0", True, True, "a workspace source may still carry a real specifier"),
    ("workspace:==0.9.0", False, False, "and that specifier is validated like any other"),
]


@pytest.mark.parametrize(("version_range", "edge", "valid_expected", "why"), WORKSPACE_SPECIFIERS)
def test_a_workspace_source_with_a_specifier_is_validated(
    version_range: str,
    edge: bool,
    valid_expected: bool,
    why: str,
    default_config: FakeConfig,
) -> None:
    """`workspace:<specifier>` falls through to normal validation after the prefix is stripped.

    Adaptation: upstream writes `workspace:^1.0.0`, which is not PEP 440. The molt spelling of a
    workspace source with an explicit constraint is `workspace:` plus a real specifier set; the
    branch being pinned (strip prefix -> not a marker -> not a path -> validate) is identical.

    research doc 01 section 5.2 (branch table) and section 6.3 (two-sided workspace semantics);
    get-dependency-graph.ts:109-143.
    """
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", version_range)

    graph, valid, errors = build_graph(state, default_config)

    assert (graph["pkg-a"] == ["pkg-b"]) is edge, why
    assert valid is valid_expected, why
    assert (errors == []) is valid_expected


# --------------------------------------------------------------------------------------
# The (graph, valid, errors) triple and its ordering
# --------------------------------------------------------------------------------------


def test_a_healthy_workspace_is_valid_with_no_errors(default_config: FakeConfig) -> None:
    """The happy path, stated once so the failure rows above cannot pass vacuously."""
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", "==1.0.0")

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == ["pkg-b"]
    assert valid is True
    assert errors == []


def test_graph_keys_are_insertion_ordered_with_the_root_first(default_config: FakeConfig) -> None:
    """Insertion order is observable, here and in the release plan.

    Every package gets a key even with zero dependents, the root package is keyed first, and the
    workspace packages follow in declaration order.

    research doc 01 section 5.1; get-dependents-graph/src/index.ts:24-38,52-54.
    """
    state = two_package_workspace().add_package("pkg-c", "1.0.0")

    graph, valid, errors = build_graph(state, default_config)

    assert list(graph) == ["root", "pkg-a", "pkg-b", "pkg-c"]
    assert all(graph[name] == [] for name in graph), "no edges were declared"
    assert valid is True
    assert errors == []


def test_the_root_package_is_never_a_dependent(default_config: FakeConfig) -> None:
    """Only workspace packages are scanned as dependents; a root-level range never bumps anything.

    `apply-release-plan` still rewrites the root manifest separately
    (`apply-release-plan/src/index.ts:175-186`), so dropping root as a *dependent* loses nothing.

    research doc 01 section 5.1; get-dependents-graph/src/index.ts:40-50.
    """
    state = two_package_workspace()
    state.packages.root_package.manifest.dependencies["pkg-a"] = "==1.0.0"

    graph, valid, errors = build_graph(state, default_config)

    assert "root" in graph, "the root package still gets a key"
    assert graph["pkg-a"] == [], "root is never recorded as a dependent"
    assert valid is True
    assert errors == []


def test_external_dependencies_produce_no_edge_and_no_error(default_config: FakeConfig) -> None:
    """A dependency that is not a workspace package is skipped before any validation."""
    state = two_package_workspace().update_dependency("pkg-b", "requests", ">=2.0,<3.0")

    graph, valid, errors = build_graph(state, default_config)

    assert "requests" not in graph
    assert graph["pkg-a"] == []
    assert valid is True
    assert errors == []


# --------------------------------------------------------------------------------------
# Dev-group edges
# --------------------------------------------------------------------------------------


def test_a_dev_group_edge_is_still_in_the_graph(default_config: FakeConfig) -> None:
    """Dev edges must exist even though they never bump.

    They are in the graph so `apply` can rewrite their ranges; the "never bumps" half is a
    `determine-dependents` concern and is asserted in `test_assemble.py` (matrix `dev` rows).
    Upstream comments this at `assemble-release-plan/src/index.ts:152-155`.

    research doc 01 section 5.3 (the two different graphs).
    """
    state = two_package_workspace().update_dev_dependency("pkg-b", "pkg-a", "==1.0.0")

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == ["pkg-b"]
    assert valid is True
    assert errors == []


def test_ignore_dev_drops_every_dev_group_edge(default_config: FakeConfig) -> None:
    """The second graph mode: `ignore_dev=True` skips dev entries before any other check.

    This is the branch at `get-dependency-graph.ts:26-27` (`ignoreDevDependencies ||`) and it is
    the *only* thing that separates the two graphs in the system. It has one caller upstream --
    the config rule `alsoSkipDependentsOfSkipped` (`packages/config/src/rules.ts:127-131`), where
    a stale dev range on a skipped package is harmless -- while `assembleReleasePlan` always
    leaves it false (`assemble-release-plan/src/index.ts:152-155`).

    Contrast with the row above: the range here is a perfectly valid, satisfied constraint, so
    only the flag can be responsible for the edge disappearing.

    research doc 01 section 5.3 ("Two different graphs in the system"), section 5.2.
    """
    state = two_package_workspace().update_dev_dependency("pkg-b", "pkg-a", "==1.0.0")

    graph, valid, errors = build_graph(state, default_config, ignore_dev=True)

    assert graph["pkg-a"] == [], "the dev edge is gone, though the same range is an edge by default"
    assert valid is True
    assert errors == []


def test_ignore_dev_leaves_runtime_edges_alone(default_config: FakeConfig) -> None:
    """Guard the flag's blast radius: it must not disable the graph wholesale.

    research doc 01 section 5.3; get-dependency-graph.ts:19-36.
    """
    state = two_package_workspace().update_dependency("pkg-b", "pkg-a", "==1.0.0")

    graph, valid, errors = build_graph(state, default_config, ignore_dev=True)

    assert graph["pkg-a"] == ["pkg-b"]
    assert valid is True
    assert errors == []


def test_the_dev_group_range_wins_when_a_dependency_is_declared_twice(
    default_config: FakeConfig,
) -> None:
    """Faithfulness pin: the graph flattens the dep sections, last section wins.

    `getAllDependencies` folds every dependency section into one map in a fixed order, so a
    package declared in both the runtime dependencies and the dev group has its graph-edge
    validity decided by the *dev* range alone. This is deliberately asymmetric with
    `getDependencyVersionRanges`, which keeps every entry and lets the strongest win -- the
    asymmetry is documented, not a bug in the section-3.4 sense, so it is ported.

    research doc 01 section 5.2 ("later sections overwrite earlier ones") and section 4.4;
    get-dependency-graph.ts:19-36.
    """
    state = two_package_workspace().update_dependencies(
        "pkg-b",
        [
            DepEntry(name="pkg-a", version_range="==1.0.0"),
            DepEntry(name="pkg-a", version_range="==0.9.0", kind="dev"),
        ],
    )

    graph, valid, errors = build_graph(state, default_config)

    assert graph["pkg-a"] == [], "the satisfied runtime range does not rescue the edge"
    assert valid is False
    assert "==0.9.0" in error_text(errors), "the dev range is the one reported"


# --------------------------------------------------------------------------------------
# What a workspace marker resolves to (`determine-dependents.ts:173-228`)
# --------------------------------------------------------------------------------------
#
# The graph only decides whether an edge exists. Which *range* that edge carries is resolved
# later, and it is the reason `workspace:*` maximises dependent churn: it is an exact pin of the
# dependency's current version, not a wildcard (README section 3.3, doc 01 section 14.5).

OLD_VERSION = v("1.0.0")
DEPENDENCY_PATH = "packages/pkg-a"

WORKSPACE_RESOLUTION = [
    # (range,                       expected,             why)
    (
        "workspace:*",
        s("==1.0.0"),
        "exact pin of oldVersion -- explicitly NOT a wildcard (determine-dependents.ts:199-202)",
    ),
    (
        "workspace:packages/pkg-a",
        s("==1.0.0"),
        "a matching relpath resolves to the same exact pin (determine-dependents.ts:207-217)",
    ),
    ("workspace:^", caret(OLD_VERSION), "caret of oldVersion, expanded to PEP 440"),
    ("workspace:~", tilde(OLD_VERSION), "tilde of oldVersion, expanded to PEP 440"),
]


@pytest.mark.parametrize(("version_range", "expected", "why"), WORKSPACE_RESOLUTION)
def test_workspace_markers_resolve_against_the_dependencys_old_version(
    version_range: str, expected: SpecifierSet, why: str
) -> None:
    """Pin what each workspace marker means once the dependency's old version is known.

    `oldVersion` is read once and held constant for the whole dependent search, which is one of
    the three reasons the fixpoint loop terminates (research README section 3.1).

    research doc 01 section 4.4 and section 6.3; determine-dependents.ts:196-217.
    """
    assert (
        engine.resolve_workspace_range(version_range, OLD_VERSION, dependency_path=DEPENDENCY_PATH)
        == expected
    ), why


def test_an_unrecognised_workspace_path_resolves_to_nothing() -> None:
    """A workspace path that matches no package directory drops the edge rather than guessing.

    research doc 01 section 4.4; determine-dependents.ts:207-217.
    """
    assert (
        engine.resolve_workspace_range(
            "workspace:packages/not-pkg-a", OLD_VERSION, dependency_path=DEPENDENCY_PATH
        )
        is None
    )
