"""Conformance tests for the release-plan engine -- ``assemble_release_plan``.

This is the moat. Every test here maps to a row of
``roadmap/research/test-suite/02-release-plan-engine.md`` (the 50-row table at lines 50-101 and the
dependent-bumping matrix at lines 103-151), which was extracted from
``packages/assemble-release-plan/src/index.test.ts`` in ``changesets@v3.0.0-next.9``. Engine
mechanics and the ``file:line`` citations come from
``roadmap/research/changesets-01-core-versioning-engine.md``.

Load-bearing facts these tests pin (research README sections 3.1 and 3.3):

- The engine is a **3-pass fixpoint loop** -- ``determine_dependents`` -> ``match_fixed_constraint``
  -> ``apply_links``, repeated until no pass reports a change (``index.ts:161-183``).
  ``determine_dependents`` running **first** is directly observable and is pinned by
  :func:`test_determine_dependents_runs_before_match_fixed_constraint`: both it and
  ``match_fixed_constraint`` *create* releases, so whichever runs first inserts first. The
  ``match_fixed_constraint`` vs ``apply_links`` order, by contrast, is **not** observable --
  ``apply_links`` only mutates releases that already exist (``apply-links.ts:43``), so it can
  never win an insertion race, and a package in both a ``fixed`` and a ``linked`` group would not
  converge at all. Nothing here claims to pin that relative order.
- Output is **insertion-ordered** (``Array.from(releases.values())``, ``index.ts:218``), so rows 4,
  20 and 21 assert an exact list of names. Those three orders are fixed by changeset order rather
  than by pass order -- see the pass-order test above for the discriminating case.
- ``fixed`` force-releases every group member; ``linked`` only aligns members that are already
  releasing. Rows 19 and 25 are the same setup under the two configs and disagree on the release
  *count*; they cite each other.
- ``none`` is truthy in the dependent filter, so an ignored or dev-only dependent is still
  materialised as a ``none`` release (rows 14, 16) -- but a dependency whose own type is ``none``
  short-circuits and never propagates (rows 38, 46).
- The snapshot suffix is computed **once per plan** (``index.ts:213``), so every snapshot version in
  one plan shares a timestamp; the shared ``frozen_clock`` fixture makes that assertable.

Deliberate divergences from the reference suite
-----------------------------------------------
- **npm range spellings are respelled in PEP 440** through the implemented
  ``molt.versioning.ranges`` helpers: ``^1.0.0`` -> ``caret(Version("1.0.0"))``, ``~1.0.0`` ->
  ``tilde(...)``, bare ``1.0.0`` (npm exact) -> ``==1.0.0``. See :func:`_render_range`.
- **``peerDependencies`` are dropped** (research README section 4.4). Rows 15, 23, 28, 30, 31, 32,
  41, 42, 47 and 48 are Drops recorded in ``tests/engine/test_deliberately_not_ported.py``. Rows 23
  and 28 additionally get a peer-free adapted variant here, labelled as such.
- **No upstream bug from research README section 3.4 is baked into an expected value.** In
  particular no double-bump, no range widening from dropping a trailing constraint, no
  ``none``-release changeset loss (bug #4: ``determine_dependents`` replacing a ``none`` release and
  discarding its ``changesets[]``), and no inert ``fixed`` / ``linked`` globs (bug #1). The two
  divergences are asserted, not merely noted -- see the tests that name them.
- **The matrix gains an ``optional`` dependency kind** upstream never had. Upstream's builder has
  no ``optionalDependencies`` writer even though the engine handles the field, treating it exactly
  like ``dependencies`` (``determine-dependents.ts:101-107``). In Python that field is extras,
  which is a distinct thing from a PEP 735 dev group (research README section 4.5), so conflating
  the two would silently assert "extras never bump". Those 48 cells are net-new at
  upstream-parity semantics.
- **No syrupy snapshots**, contra ``02-release-plan-engine.md`` "Fixtures & tooling" item 4 and
  phase brief line 47. Release order is asserted as an explicit list of names instead, which is
  reviewable in the diff and cannot be silently re-recorded; a snapshot file also cannot be
  generated while this module is skipped. The ``snapshot`` marker therefore goes unused here.

TDD targets declared by this file (they do not exist yet)
----------------------------------------------------------
``molt.engine.assemble_release_plan(changesets, packages, config, *, pre=None, snapshot=None)``
returning ``ReleasePlan`` with ``.releases: list[Release]`` and ``.changesets``; ``Release`` with
``.name``, ``.type``, ``.old_version``, ``.new_version``, ``.changesets``. Two shapes are *chosen*
here rather than inherited and are flagged for the owner:

1. ``snapshot`` takes ``molt.engine.SnapshotParams(tag=None, commit=None)`` -- upstream's
   ``SnapshotReleaseParameters`` (``index.ts:22-25``). A dedicated object is what distinguishes
   "not a snapshot release" (``None``) from "``--snapshot`` with no tag" (``SnapshotParams()``).
2. ``pre`` takes the PEP 440 phase string (``"a" | "b" | "rc" | "dev"``), because molt replaces the
   ``pre.json`` state machine with an invocation flag (research README section 4.2).

The engine is expected to read the workspace structurally: ``packages.root_dir``,
``packages.packages[i].dir`` and
``.manifest.{name, version, dependencies, dev_dependencies, optional_dependencies}``, plus the
config attributes on :class:`~tests.engine.fake_state.FakeConfig`.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from packaging.version import Version
from tests.engine.fake_state import (
    DEFAULT_CHANGESET_ID,
    PATH_SOURCES,
    DepEntry,
    FakeConfig,
    FakeFullState,
)
from tests.engine.fake_state import default_config as ported_default_config

from molt.versioning import BumpType, caret, highest, inc, parse_range, satisfies, tilde

if TYPE_CHECKING:
    from tests.conftest import FrozenClock

# The guard names the *submodule* that owns the target, not the package. `molt.engine` exists from
# build step 6 (the dependents graph, `tests/engine/test_dependents_graph.py`); the plan half lands
# as `molt.engine.assemble` in build step 7. Guarding on the package would have turned this whole
# module from skipped to a collection *error* the moment the graph shipped -- the same
# "module exists => implemented" assumption `tests/cli/conftest.py` documents for the command
# suites. Nothing below this line runs until the plan half exists.
pytest.importorskip(
    "molt.engine.assemble",
    reason="build step 7 - the release-plan engine is not implemented yet (TDD target)",
)

# These four were build step 7's TDD target and now exist, so the `missing-module-attribute`
# suppression this line used to carry is gone, as its own note instructed.
from molt.engine import Release, ReleasePlan, SnapshotParams, assemble_release_plan

pytestmark = pytest.mark.unit


# ======================================================================================
# Helpers
# ======================================================================================

_ONE = Version("1.0.0")

#: Canonical range kinds, spelled as their npm operators so the ported expectation tables stay
#: legible; :func:`_render_range` is the only place the PEP 440 translation happens.
_RANGE_KINDS: tuple[str, ...] = ("^", "~", "=")

#: Which manifest field the dependency is written to. ``dep`` and ``optional`` share the same
#: switch case upstream (``determine-dependents.ts:101-107``) and so share an expectation table;
#: ``dev`` is the odd one out (``:108-117``). ``peer`` is dropped (research README section 4.4).
_DEP_KINDS: tuple[str, ...] = ("dep", "dev", "optional")

#: Kinds whose dependents actually take a version bump.
_BUMPING_DEP_KINDS: frozenset[str] = frozenset({"dep", "optional"})

_BUMPS: tuple[BumpType, ...] = (BumpType.NONE, BumpType.PATCH, BumpType.MINOR, BumpType.MAJOR)


def _render_range(kind: str, version: Version) -> str:
    """Respell an npm range operator in PEP 440 (research README section 4.1).

    ``^`` and ``~`` have no Python operators, so they go through the implemented
    ``molt.versioning.ranges`` helpers; npm's bare ``1.0.0`` (an exact pin) becomes ``==1.0.0``.
    """
    if kind == "^":
        return str(caret(version))
    if kind == "~":
        return str(tilde(version))
    return f"=={version}"


def _render_workspace_modifier(kind: str, version: Version) -> str:
    """``workspace:^`` / ``workspace:~`` / ``workspace:*`` (``index.test.ts:1320``).

    The modifier carries no version: the engine resolves it against the dependency's current
    version, with ``workspace:*`` meaning an **exact** pin rather than a wildcard
    (``determine-dependents.ts:196-206``; research README section 3.3).
    """
    del version  # resolved by the engine, not written into the constraint
    return "workspace:" + ("*" if kind == "=" else kind)


def _render_workspace_versioned(kind: str, version: Version) -> str:
    """``workspace:<pep440 range>`` (``index.test.ts:1325``, modifier+version form)."""
    return "workspace:" + _render_range(kind, version)


CARET_1 = _render_range("^", _ONE)
TILDE_1 = _render_range("~", _ONE)
EXACT_1 = _render_range("=", _ONE)


def _plan(state: FakeFullState, config: FakeConfig, **kwargs: Any) -> ReleasePlan:
    """Run the engine over a builder's state."""
    return assemble_release_plan(state.changesets, state.packages, config, **kwargs)


def _names(plan: ReleasePlan) -> list[str]:
    """Release names in plan order -- the insertion order the engine is required to preserve."""
    return [release.name for release in plan.releases]


def _versions(plan: ReleasePlan) -> dict[str, str]:
    """``{name: new_version}``, stringified.

    Stringifying keeps these tests honest whether molt models a version as ``str`` or as
    ``packaging.version.Version``; the engine's contract only fixes the value, not the carrier.
    """
    return {release.name: str(release.new_version) for release in plan.releases}


def _release(plan: ReleasePlan, name: str) -> Release:
    for release in plan.releases:
        if release.name == name:
            return release
    raise AssertionError(f"no release for {name}; plan holds {_names(plan)}")


@pytest.fixture
def setup() -> FakeFullState:
    """The reference ``beforeEach`` (``index.test.ts:12-18``).

    The default seed (``pkg-a@1.0.0`` plus its ``strange-words-combine`` patch changeset) with
    pkg-b / pkg-c / pkg-d added at 1.0.0. Rows that need a clean slate build their own
    ``FakeFullState(changesets=[])`` instead, exactly as upstream does.
    """
    return (
        FakeFullState()
        .add_package("pkg-b", "1.0.0")
        .add_package("pkg-c", "1.0.0")
        .add_package("pkg-d", "1.0.0")
    )


# ======================================================================================
# Rows 1-10 -- baseline, snapshots, flattening, dependent cascade, validation
# ======================================================================================


def test_assembles_a_plan_for_the_basic_setup(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 1 (Port). ``index.test.ts:20-36``.

    The single-package baseline: one patch release carrying its changeset id.

    Also the only place ``ReleasePlan.changesets`` is asserted. It is not a copy of the input:
    ``get_relevant_changesets`` (``index.ts:235-284``) is what validates and filters the list, and
    it is what the changelog renderer reads, so a plan that dropped or reordered it would emit the
    wrong release notes without any version being wrong.
    """
    plan = _plan(setup, default_config)

    assert len(plan.releases) == 1
    release = plan.releases[0]
    assert release.name == "pkg-a"
    assert release.type == BumpType.PATCH
    assert str(release.old_version) == "1.0.0"
    assert str(release.new_version) == "1.0.1"
    assert release.changesets == [DEFAULT_CHANGESET_ID]
    assert [changeset.id for changeset in plan.changesets] == [DEFAULT_CHANGESET_ID]


def test_determine_dependents_runs_before_match_fixed_constraint(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Pass-order guard. ``index.ts:164-179`` -- ``determine_dependents`` runs FIRST each turn.

    Both passes *create* releases, so which one runs first is visible in the insertion order --
    and this is the only test that discriminates it. determine-first reaches pkg-b (pkg-a's
    dependent, released by the seeded patch) before ``match_fixed_constraint`` force-creates
    pkg-d; swapping the two passes yields ``a, c, d, b`` instead.

    Rows 20/21/26 look like they pin this, but they do not: their orders fall out of changeset
    order alone and survive any permutation of the three passes. See the module docstring for why
    the ``match_fixed_constraint`` vs ``apply_links`` order is genuinely unobservable.
    """
    setup.update_dependency("pkg-b", "pkg-a", EXACT_1)
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-c", BumpType.MAJOR)])
    config = dataclasses.replace(default_config, fixed=(("pkg-c", "pkg-d"),))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-c", "pkg-b", "pkg-d"]
    assert _versions(plan) == {
        "pkg-a": "1.0.1",
        "pkg-c": "2.0.0",
        "pkg-b": "1.0.1",
        "pkg-d": "2.0.0",
    }


def test_snapshot_release_uses_a_pep440_dev_segment(
    setup: FakeFullState,
    default_config: FakeConfig,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row 2 (Adapt). ``index.test.ts:38-51``; research doc 01 section 11.1.

    Upstream asserts ``/0\\.0\\.0-\\d{14}/``. ``0.0.0-<datetime>`` is not a legal PEP 440 version,
    so molt spells the same thing as ``0.0.0.dev<14-digit-datetime>``: ``.devN`` is the only
    numeric prerelease segment that sorts *below* every real release, which is exactly the property
    upstream wanted from the ``0.0.0`` base (``index.ts:96-104``). Snapshots default to a non-PyPI
    index because PyPI burns the version permanently (research README section 4.3).

    The 14-digit datetime is the frozen clock's, which also proves the suffix is derived from a
    single injectable "now" rather than from ``datetime.now()`` at each release.
    """
    frozen_clock.freeze(monkeypatch)
    digits = frozen_clock.moment.strftime("%Y%m%d%H%M%S")

    plan = _plan(setup, default_config, snapshot=SnapshotParams())

    assert len(plan.releases) == 1
    new_version = str(plan.releases[0].new_version)
    assert re.fullmatch(r"0\.0\.0\.dev\d{14}", new_version), new_version
    assert new_version == f"0.0.0.dev{digits}"


def test_snapshot_release_with_a_tag_keeps_the_tag_in_the_local_segment(
    setup: FakeFullState,
    default_config: FakeConfig,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row 3 (Adapt). ``index.test.ts:53-66``; research doc 01 section 11.1.

    **OPEN DECISION -- flagged for the owner** (``roadmap/tdd-ddd/progress.md``, "Snapshot model",
    research open decision #2). Upstream produces ``0.0.0-foo-<datetime>``. PEP 440 has no
    free-form prerelease tag: ``aN``/``bN``/``rcN``/``.devN`` are all numeric, so the tag cannot
    live in the prerelease segment at all.

    The shape asserted here -- ``0.0.0.dev<datetime>+<tag>`` -- puts the tag in the **local version
    segment**, the only free-form field PEP 440 defines. Rationale: it preserves the tag verbatim,
    it keeps the ordering property of the untagged form (local segments never reorder releases
    across different ``.devN`` values), and snapshots already target a non-PyPI index, which is the
    one place local versions are legal (research README section 4.3 -- PyPI rejects ``+local``).

    Alternatives rejected: dropping the tag (loses information the user typed) and encoding it in
    the ``.devN`` counter (PEP 440 requires it to be an integer). If the owner picks a different
    composition, this is the single test to change.
    """
    frozen_clock.freeze(monkeypatch)
    digits = frozen_clock.moment.strftime("%Y%m%d%H%M%S")

    plan = _plan(setup, default_config, snapshot=SnapshotParams(tag="foo"))

    assert len(plan.releases) == 1
    assert str(plan.releases[0].new_version) == f"0.0.0.dev{digits}+foo"


def test_every_snapshot_version_in_one_plan_shares_the_timestamp(
    setup: FakeFullState,
    default_config: FakeConfig,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Net-new. Pins ``getSnapshotSuffix`` being called once per plan (``index.ts:213``).

    Research doc 01 section 14.16 / README section 3.3: the suffix is computed once and reused, so
    a multi-package snapshot cannot straddle a second boundary. Without this, a plan assembled at
    ``...:59.999`` would emit two different suffixes and the release would be unreproducible.

    The assertion compares against the **frozen** value rather than merely counting distinct
    suffixes: an engine that called ``datetime.now()`` per release and ignored the clock seam
    entirely still yields one distinct suffix for any plan that assembles inside a single second,
    so a ``len(...) == 1`` check passes vacuously.
    """
    frozen_clock.freeze(monkeypatch)
    digits = frozen_clock.moment.strftime("%Y%m%d%H%M%S")
    setup.add_changeset(
        id="big-cats-delight",
        releases=[("pkg-b", BumpType.PATCH), ("pkg-c", BumpType.MAJOR)],
    )

    plan = _plan(setup, default_config, snapshot=SnapshotParams())

    suffixes = {str(release.new_version).split(".dev", 1)[1] for release in plan.releases}
    assert len(plan.releases) == 3
    assert suffixes == {digits}, f"one frozen timestamp per plan, got {suffixes}"


def test_assembles_a_plan_with_multiple_packages_in_insertion_order(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 4 (Port). ``index.test.ts:68-94``.

    The order is the primary assertion: ``flatten_releases`` inserts in changeset order and the
    plan is materialised with ``Array.from(releases.values())`` (``index.ts:218``), so a set-backed
    or sorted implementation would silently reorder every changelog molt writes.
    """
    setup.add_changeset(
        id="big-cats-delight",
        releases=[("pkg-b", BumpType.PATCH), ("pkg-c", BumpType.PATCH), ("pkg-d", BumpType.MAJOR)],
    )

    plan = _plan(setup, default_config)

    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-c", "pkg-d"]
    assert _versions(plan) == {
        "pkg-a": "1.0.1",
        "pkg-b": "1.0.1",
        "pkg-c": "1.0.1",
        "pkg-d": "2.0.0",
    }


def test_two_changesets_for_one_package_merge_to_the_highest_bump(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 5 (Port). ``index.test.ts:96-113``; ``flatten-releases.ts:40-59``.

    One release, the highest bump wins, and **both** changeset ids survive on it in changeset
    order. Keeping the id list is what stops a summary vanishing from the changelog.
    """
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-a", BumpType.MAJOR)])

    plan = _plan(setup, default_config)

    assert len(plan.releases) == 1
    release = plan.releases[0]
    assert release.name == "pkg-a"
    assert release.type == BumpType.MAJOR
    assert str(release.new_version) == "2.0.0"
    assert release.changesets == [DEFAULT_CHANGESET_ID, "big-cats-delight"]


def test_none_never_lowers_another_release_type(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 6 (Port). ``index.test.ts:115-161``; research doc 01 section 3.1.

    ``none`` is stacked before *and* after the real bumps to prove the merge takes a maximum
    rather than a last-write.
    """
    nones = [("pkg-a", BumpType.NONE), ("pkg-b", BumpType.NONE), ("pkg-c", BumpType.NONE)]
    setup.add_changeset(id="big-cats-delight", releases=nones)
    setup.add_changeset(
        id="big-cats-wonder",
        releases=[("pkg-a", BumpType.PATCH), ("pkg-b", BumpType.MINOR), ("pkg-c", BumpType.MAJOR)],
    )
    setup.add_changeset(id="big-cats-yelp", releases=nones)

    plan = _plan(setup, default_config)

    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-c"]
    assert [release.type for release in plan.releases] == [
        BumpType.PATCH,
        BumpType.MINOR,
        BumpType.MAJOR,
    ]
    assert _versions(plan) == {"pkg-a": "1.0.1", "pkg-b": "1.1.0", "pkg-c": "2.0.0"}


def test_updates_multiple_dependents_of_a_single_package(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 7 (Port). ``index.test.ts:163-181``.

    npm's bare ``"1.0.0"`` is an exact pin, spelled ``==1.0.0`` in PEP 440. Both dependents break
    on a patch and both get one.
    """
    setup.update_dependency("pkg-b", "pkg-a", EXACT_1)
    setup.update_dependency("pkg-c", "pkg-a", EXACT_1)

    plan = _plan(setup, default_config)

    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-c"]
    assert set(_versions(plan).values()) == {"1.0.1"}


def test_updates_dependents_all_the_way_down_the_dependency_tree(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 8 (Port). ``index.test.ts:183-204``; ``determine-dependents.ts:45-49``.

    The transitive cascade: each newly created dependent release is pushed onto the FIFO worklist,
    so d is reached through c through b. The FIFO order is what fixes the output order here.
    """
    setup.update_dependency("pkg-b", "pkg-a", EXACT_1)
    setup.update_dependency("pkg-c", "pkg-b", EXACT_1)
    setup.update_dependency("pkg-d", "pkg-c", EXACT_1)

    plan = _plan(setup, default_config)

    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-c", "pkg-d"]
    assert set(_versions(plan).values()) == {"1.0.1"}


@pytest.mark.parametrize(
    ("unconstrained", "why"),
    [
        (
            "",
            "empty string: doubly degenerate -- the falsy guard at "
            "determine-dependents.ts:194 drops it before any range check even runs",
        ),
        (
            ">=0",
            "the real case: a present, valid, satisfiable-by-everything specifier, so the "
            "edge IS considered and the range check is what declines to bump",
        ),
    ],
)
def test_an_unconstrained_dependency_is_a_graph_edge_that_never_bumps(
    setup: FakeFullState, default_config: FakeConfig, unconstrained: str, why: str
) -> None:
    """Row 9 (Port). ``index.test.ts:206-223``; research doc 02 line 33.

    The distinction matters: an unconstrained dependency **is** a valid range, so the edge exists
    in the dependents graph (unlike a path source, which is dropped from the graph entirely) --
    it simply can never be violated, so it never produces a bump. ``>=0`` is the spelling that
    actually exercises that path; the empty string is kept as the second, weaker case because it
    is what a bare PEP 508 requirement with no specifier parses to.
    """
    setup.update_dependency("pkg-b", "pkg-a", unconstrained)
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-a", BumpType.MAJOR)])

    plan = _plan(setup, default_config)

    assert _names(plan) == ["pkg-a"], why
    assert _versions(plan) == {"pkg-a": "2.0.0"}


def test_raises_when_a_changeset_names_a_package_outside_the_workspace(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 10 (Port). ``index.test.ts:225-245``; ``index.ts:248-252``.

    Validation happens up front in ``get_relevant_changesets`` so the failure names the offending
    changeset **and** the offending package, rather than surfacing later as an internal error.
    """
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-a", BumpType.MAJOR)])
    setup.add_changeset(id="small-dogs-sad", releases=[("pkg-z", BumpType.MINOR)])

    # TODO(molt.errors): narrow once the hierarchy lands (test-contract section 5).
    with pytest.raises(Exception, match="small-dogs-sad") as excinfo:
        _plan(setup, default_config)

    assert "pkg-z" in str(excinfo.value)


@pytest.mark.parametrize(
    ("kind", "constraint", "why"),
    [
        (kind, source, f"{source} as a {kind} dependency is a location, not a constraint")
        for kind in ("dep", "dev")
        for source in PATH_SOURCES
    ],
)
def test_a_path_or_direct_reference_dependency_is_not_a_version_constraint(
    setup: FakeFullState, default_config: FakeConfig, kind: str, constraint: str, why: str
) -> None:
    """Rows 11 + 12 folded into one (Adapt). ``index.test.ts:247-287``.

    Upstream has two suites for npm's ``link:`` and ``file:`` protocols. Neither spelling exists in
    Python, and both test the same rule -- a dependency pinned to a *location* rather than to a
    *version* carries no constraint that a release can violate, so the graph builder drops the edge
    and the dependent is never even considered (research doc 02 lines 30-32). Python spells the
    same thing as a PEP 508 direct reference (``pkg @ file:///...``, ``pkg @ git+https://...``),
    which is what a uv path/editable source resolves to.

    Adapted: the two npm protocol suites collapse into this one parametrized rule. Note the
    ``dep`` variants are **net-new** -- upstream only ever writes ``link:``/``file:`` into
    ``devDependencies`` (``index.test.ts:249``, ``:270``). They also pin a second, previously
    unstated decision: a runtime path source makes the dependents graph report ``valid=False``
    with an error, and planning must carry on regardless, because ``index.ts:156-159`` never reads
    ``valid`` (research doc 01 section 14.11). Validation of that graph is the CLI's job, not the
    engine's.
    """
    if kind == "dev":
        setup.update_dev_dependency("pkg-b", "pkg-a", constraint)
    else:
        setup.update_dependency("pkg-b", "pkg-a", constraint)
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-a", BumpType.MAJOR)])

    plan = _plan(setup, default_config)

    assert _names(plan) == ["pkg-a"], why
    assert _versions(plan) == {"pkg-a": "2.0.0"}


# ======================================================================================
# Rows 13-17 -- ignored packages
# ======================================================================================


def test_ignored_packages_do_not_get_releases_from_their_own_changesets(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 13 (Port). ``index.test.ts:290-312``; ``flatten-releases.ts:34-38``.

    An ignored package is filtered out during flattening, so its changeset produces nothing.
    """
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-a", BumpType.MAJOR)])
    setup.add_changeset(id="small-dogs-sad", releases=[("pkg-b", BumpType.MINOR)])
    config = dataclasses.replace(default_config, ignore=("pkg-b",))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a"]
    assert _versions(plan) == {"pkg-a": "2.0.0"}


def test_an_ignored_dependent_is_still_materialised_as_a_none_release(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 14 (Port). ``index.test.ts:314-340``; ``determine-dependents.ts:67-73, 131-136``.

    The skipped dependent gets ``type = "none"``, and ``none`` is a **truthy string** in the
    ``filter(d => !!d.type)`` that follows (research doc 01 section 14.2), so it survives into the
    plan at its unchanged version. That is deliberate: ``apply_release_plan`` still has to rewrite
    the ignored package's dependency pin even though its own version does not move.
    """
    setup.update_dependency("pkg-b", "pkg-a", EXACT_1)
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-a", BumpType.MAJOR)])
    setup.add_changeset(id="small-dogs-sad", releases=[("pkg-b", BumpType.MINOR)])
    config = dataclasses.replace(default_config, ignore=("pkg-b",))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-b"]
    assert _versions(plan) == {"pkg-a": "2.0.0", "pkg-b": "1.0.0"}
    assert _release(plan, "pkg-b").type == BumpType.NONE


def test_an_ignored_dev_dependent_is_still_materialised_as_a_none_release(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 16 (Port). ``index.test.ts:370-396``.

    Same rule as row 14 through the dev-group edge. Row 15 (the ``peerDependencies`` twin) is a
    Drop: with peers gone it adds nothing over this pair (research doc 02 row 15).
    """
    setup.update_dev_dependency("pkg-b", "pkg-a", EXACT_1)
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-a", BumpType.MAJOR)])
    setup.add_changeset(id="small-dogs-sad", releases=[("pkg-b", BumpType.MINOR)])
    config = dataclasses.replace(default_config, ignore=("pkg-b",))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-b"]
    assert _versions(plan) == {"pkg-a": "2.0.0", "pkg-b": "1.0.0"}
    assert _release(plan, "pkg-b").type == BumpType.NONE


def test_raises_on_a_changeset_mixing_ignored_and_non_ignored_packages(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 17 (Port). ``index.test.ts:398-423``; ``index.ts:266-273``.

    Upstream asserts an inline snapshot of the whole four-line message. The invariant, not the
    wording, is what ports: the error has to name the changeset and list both sides, because the
    fix is always "split this changeset".
    """
    setup.add_changeset(
        id="big-cats-delight",
        releases=[("pkg-a", BumpType.MAJOR), ("pkg-b", BumpType.MINOR)],
    )
    config = dataclasses.replace(default_config, ignore=("pkg-b",))

    # TODO(molt.errors): narrow once the hierarchy lands (test-contract section 5).
    with pytest.raises(Exception, match="big-cats-delight") as excinfo:
        _plan(setup, config)

    message = str(excinfo.value)
    assert "pkg-a" in message
    assert "pkg-b" in message


# ======================================================================================
# Rows 18-23 -- fixed packages
# ======================================================================================


def test_fixed_packages_bump_together(setup: FakeFullState, default_config: FakeConfig) -> None:
    """Row 18 (Port). ``index.test.ts:427-446``; ``match-fixed-constraint.ts:32-69``.

    The group's highest bump wins for every member -- pkg-b has no changeset of its own and is
    force-released anyway. Contrast row 24, where ``linked`` produces the same two releases only
    because both members were already releasing.
    """
    setup.add_changeset(id="just-some-umbrellas", releases=[("pkg-a", BumpType.MINOR)])
    config = dataclasses.replace(default_config, fixed=(("pkg-a", "pkg-b"),))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-b"]
    assert set(_versions(plan).values()) == {"1.1.0"}


def test_fixed_group_aligns_on_an_unreleased_members_higher_version(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 19 (Port). ``index.test.ts:448-473``; ``match-fixed-constraint.ts:26-29``.

    **Half of the fixed-vs-linked contrast -- read with**
    :func:`test_linked_group_does_not_force_release_a_non_releasing_member`, **which is the same
    setup under ``linked`` and yields 2 releases instead of 3.**

    ``fixed`` takes the highest *current* version across the whole group (pkg-c's 2.0.0, even
    though pkg-c has no changeset) as everyone's ``old_version``, then applies the highest bump
    (minor) -- and force-creates a release for pkg-c so it moves too.
    """
    setup.add_changeset(
        id="just-some-umbrellas",
        releases=[("pkg-b", BumpType.MINOR), ("pkg-a", BumpType.PATCH)],
    )
    setup.update_package("pkg-c", "2.0.0")
    config = dataclasses.replace(default_config, fixed=(("pkg-a", "pkg-b", "pkg-c"),))

    plan = _plan(setup, config)

    assert len(plan.releases) == 3, "fixed force-releases every member of the group"
    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-c"]
    assert set(_versions(plan).values()) == {"2.1.0"}


def test_chained_fixed_groups_converge_in_a_specific_order(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 20 (Port). ``index.test.ts:475-514`` (see its narration comment at ``:476-480``).

    Two fixed groups where a member of the second depends on a member of the first. The output
    order **a, b, d, c** is a direct read-out of the 3-pass loop: pkg-c is only reached after
    ``match_fixed_constraint`` has raised pkg-a, which is what pushes pkg-a's new version outside
    pkg-c's caret range on the next ``determine_dependents`` pass.
    """
    setup.add_changeset(id="just-some-umbrellas", releases=[("pkg-b", BumpType.MAJOR)])
    setup.add_changeset(id="totally-average-verbiage", releases=[("pkg-d", BumpType.MINOR)])
    setup.update_dependency("pkg-c", "pkg-a", CARET_1)
    config = dataclasses.replace(default_config, fixed=(("pkg-a", "pkg-b"), ("pkg-c", "pkg-d")))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-d", "pkg-c"]
    assert _versions(plan) == {
        "pkg-a": "2.0.0",
        "pkg-b": "2.0.0",
        "pkg-d": "1.1.0",
        "pkg-c": "1.1.0",
    }


def test_chained_fixed_groups_order_changes_with_the_dependency_edge(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 21 (Port). ``index.test.ts:516-550``.

    Same two groups as row 20, but the changeset lands on pkg-a and pkg-c depends on pkg-b. The
    order flips to **a, d, b, c**. Pinning both rows is the point: the order is not incidental, it
    is a function of which pass first materialises each release.
    """
    setup.add_changeset(id="just-some-umbrellas", releases=[("pkg-a", BumpType.MAJOR)])
    setup.add_changeset(id="totally-average-verbiage", releases=[("pkg-d", BumpType.MINOR)])
    setup.update_dependency("pkg-c", "pkg-b", CARET_1)
    config = dataclasses.replace(default_config, fixed=(("pkg-a", "pkg-b"), ("pkg-c", "pkg-d")))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-d", "pkg-b", "pkg-c"]
    assert _versions(plan) == {
        "pkg-a": "2.0.0",
        "pkg-d": "1.1.0",
        "pkg-b": "2.0.0",
        "pkg-c": "1.1.0",
    }


def test_fixed_config_alone_produces_no_releases(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 22 (Port). ``index.test.ts:552-567``; ``match-fixed-constraint.ts:23``.

    With no changesets there is nothing "releasing" in the group, so the pass short-circuits.
    A ``fixed`` group must never manufacture a release on its own.
    """
    config = dataclasses.replace(default_config, fixed=(("pkg-a", "pkg-b"), ("pkg-c", "pkg-d")))

    plan = assemble_release_plan([], setup.packages, config)

    assert plan.releases == []


def test_a_fixed_forced_bump_propagates_to_that_members_dependents(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Adapted from row 23, which is a **Drop** (peerDependencies).

    ``index.test.ts:569-601`` writes pkg-b as a *peer* dependent of pkg-c. Peers are gone
    (research README section 4.4), but the mechanism underneath is not peer-specific and is worth
    guarding: pkg-c is only released because ``match_fixed_constraint`` dragged it along with
    pkg-a, and that forced bump still has to propagate to pkg-c's own dependents on a later pass.
    Rewritten with pkg-b as an ordinary dependency of pkg-c.
    """
    setup.update_dependency("pkg-b", "pkg-c", EXACT_1)
    setup.add_changeset(id="some-id", releases=[("pkg-a", BumpType.MINOR)])
    config = dataclasses.replace(default_config, fixed=(("pkg-a", "pkg-c"),))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-c", "pkg-b"]
    assert _versions(plan) == {"pkg-a": "1.1.0", "pkg-c": "1.1.0", "pkg-b": "1.0.1"}


def test_a_fixed_group_glob_expands_to_the_matching_packages(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Net-new, and a **deliberate divergence**: upstream bug #1 (research README section 3.4).

    ``fixed`` and ``linked`` are documented as accepting globs, and ``config.ts`` even warns about
    them, but ``3.0.0-next.9`` never expands them: ``match-fixed-constraint.ts:20`` and ``:32``
    iterate the raw strings and compare with a literal ``.includes()``. A glob therefore matches
    nothing, silently does nothing, and then throws ``InternalError`` from the ``mapGetOrThrow``
    at ``:33-39`` when the group name is looked up as a package.

    molt expands the globs. This is consistent with the ``molt.config`` side of the port, which
    independently landed on expansion (P3a), so the group value the engine receives is already
    the concrete member list. Here ``pkg-*`` covers all four members, so the seeded patch on
    pkg-a force-releases the whole workspace at 1.0.1.
    """
    config = dataclasses.replace(default_config, fixed=(("pkg-*",),))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-c", "pkg-d"]
    assert set(_versions(plan).values()) == {"1.0.1"}


# ======================================================================================
# Rows 24-29 -- linked packages
# ======================================================================================


def test_linked_packages_bump_together(setup: FakeFullState, default_config: FakeConfig) -> None:
    """Row 24 (Port). ``index.test.ts:605-624``; ``apply-links.ts:43-52``.

    Both members are already releasing (pkg-a from the seeded patch, pkg-b from the new major), so
    ``apply_links`` raises the lower one to the group maximum.
    """
    setup.add_changeset(id="just-some-umbrellas", releases=[("pkg-b", BumpType.MAJOR)])
    config = dataclasses.replace(default_config, linked=(("pkg-a", "pkg-b"),))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-b"]
    assert set(_versions(plan).values()) == {"2.0.0"}


def test_linked_group_does_not_force_release_a_non_releasing_member(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 25 (Port). ``index.test.ts:626-650``; ``apply-links.ts:27-34``.

    **The other half of the fixed-vs-linked contrast -- read with**
    :func:`test_fixed_group_aligns_on_an_unreleased_members_higher_version`. **Identical setup,
    ``linked`` instead of ``fixed``: 2 releases here, 3 there.**

    ``apply_links`` iterates only ``releasing_linked_packages``, so pkg-c contributes its 2.0.0 to
    the group's ``old_version`` (both movers land on 2.1.0) without being released itself. This is
    the whole practical difference between the two config options (research doc 01 section 14.9).
    """
    setup.add_changeset(
        id="just-some-umbrellas",
        releases=[("pkg-b", BumpType.MINOR), ("pkg-a", BumpType.PATCH)],
    )
    setup.update_package("pkg-c", "2.0.0")
    config = dataclasses.replace(default_config, linked=(("pkg-a", "pkg-b", "pkg-c"),))

    plan = _plan(setup, config)

    assert len(plan.releases) == 2, "linked never force-releases a non-releasing member"
    assert _names(plan) == ["pkg-a", "pkg-b"]
    assert set(_versions(plan).values()) == {"2.1.0"}
    assert "pkg-c" not in _versions(plan)


def test_chained_linked_groups_converge(setup: FakeFullState, default_config: FakeConfig) -> None:
    """Row 26 (Port). ``index.test.ts:652-689`` (narration at ``:653-658``).

    The ``linked`` twin of row 20: pkg-a is raised by its group, which pushes it out of pkg-c's
    caret range, which drags pkg-c into pkg-d's group. Upstream asserts versions only; the name
    order follows from the same insertion rule as row 20 and is pinned here too.
    """
    setup.add_changeset(id="just-some-umbrellas", releases=[("pkg-b", BumpType.MAJOR)])
    setup.add_changeset(id="totally-average-verbiage", releases=[("pkg-d", BumpType.MINOR)])
    setup.update_dependency("pkg-c", "pkg-a", CARET_1)
    config = dataclasses.replace(default_config, linked=(("pkg-a", "pkg-b"), ("pkg-c", "pkg-d")))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-d", "pkg-c"]
    assert [str(release.new_version) for release in plan.releases] == [
        "2.0.0",
        "2.0.0",
        "1.1.0",
        "1.1.0",
    ]


def test_linked_config_alone_produces_no_releases(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 27 (Port). ``index.test.ts:691-706``; ``apply-links.ts:34``."""
    config = dataclasses.replace(default_config, linked=(("pkg-a", "pkg-b"), ("pkg-c", "pkg-d")))

    plan = assemble_release_plan([], setup.packages, config)

    assert plan.releases == []


def test_a_linked_alignment_propagates_to_that_members_dependents(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Adapted from row 28, which is a **Drop** (peerDependencies).

    ``index.test.ts:708-740`` uses a peer dependent. Rewritten with pkg-b as an ordinary dependency
    of pkg-a. The guard is the ``linked`` mirror of the adapted row 23: pkg-a is raised from patch
    to minor purely by ``apply_links``, and that raise must still be seen by
    ``determine_dependents`` on the next pass.
    """
    setup.update_dependency("pkg-b", "pkg-a", EXACT_1)
    setup.add_changeset(id="some-id", releases=[("pkg-c", BumpType.MINOR)])
    config = dataclasses.replace(default_config, linked=(("pkg-a", "pkg-c"),))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-c", "pkg-b"]
    assert _versions(plan) == {"pkg-a": "1.1.0", "pkg-c": "1.1.0", "pkg-b": "1.0.1"}


def test_a_linked_group_glob_expands_to_the_matching_packages(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Net-new, and a **deliberate divergence**: upstream bug #1 (research README section 3.4).

    The ``linked`` half of :func:`test_a_fixed_group_glob_expands_to_the_matching_packages`;
    ``apply-links.ts:28`` has the same literal ``.includes()``. Unexpanded, ``pkg-*`` matches no
    release, ``apply_links`` short-circuits at ``:34`` and the two movers stay at 1.0.1 / 1.1.0.
    Expanded, the group also picks up pkg-c's 2.0.0 as the highest current version, so both
    movers land on 2.1.0 -- while pkg-c and pkg-d are still not released, because ``linked``
    never force-releases (rows 24-25).
    """
    setup.add_changeset(id="just-some-umbrellas", releases=[("pkg-b", BumpType.MINOR)])
    setup.update_package("pkg-c", "2.0.0")
    config = dataclasses.replace(default_config, linked=(("pkg-*",),))

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-b"]
    assert set(_versions(plan).values()) == {"2.1.0"}


def test_a_fixed_group_over_an_internal_dependency_web_settles_on_one_version(
    default_config: FakeConfig,
) -> None:
    """Row 29 (Adapt). ``index.test.ts:742-788`` (issues 963 and 1759).

    Adapted: upstream's regression was that a *peer* dependent re-bumped members of a fixed group
    past the aligned version. Peers are gone, so the specific regression is moot -- but the general
    guard is exactly what the fixpoint loop must satisfy and is kept: a five-package fixed group
    wired together by internal dependencies must reach a fixpoint where **every** member lands on
    the same version, with no member bumped twice by its own dependents. ``some-peer`` becomes an
    ordinary dependency.
    """
    state = FakeFullState(changesets=[])
    for name in ("@ex/core", "@ex/errors", "@ex/api", "some-peer", "@ex/components"):
        state.add_package(name, "0.1.0")
    pin = "==0.1.0"
    state.update_dependencies(
        "@ex/api",
        [
            DepEntry(name="@ex/core", version_range=pin),
            DepEntry(name="@ex/errors", version_range=pin),
            DepEntry(name="some-peer", version_range=pin),
        ],
    )
    state.update_dependencies(
        "@ex/components",
        [
            DepEntry(name="@ex/api", version_range=pin),
            DepEntry(name="some-peer", version_range=pin),
        ],
    )
    state.add_changeset(releases=[("@ex/core", BumpType.MINOR)])
    group = ("@ex/core", "@ex/errors", "@ex/api", "some-peer", "@ex/components")
    config = dataclasses.replace(default_config, fixed=(group,))

    plan = _plan(state, config)

    assert len(plan.releases) == 5
    assert set(_versions(plan).values()) == {"0.2.0"}, "every member lands on the aligned version"


# ======================================================================================
# Row 33 -- prerelease (adapted: molt has no pre.json)
# ======================================================================================


def test_pre_release_reports_the_current_bump_and_increments_the_counter(
    default_config: FakeConfig,
) -> None:
    """Row 33 (Adapt). ``index.test.ts:862-902``; ``index.ts:27-35`` + ``increment.ts``.

    Adapted twice over:

    1. ``-next.0`` is unrepresentable in PEP 440, so the prerelease is spelled ``rc0`` and the
       phase is passed as the invocation flag ``pre="rc"`` -- molt has no ``pre.json`` mode
       (research README section 4.2, the #6 community request).
    2. The used-changeset bookkeeping upstream reads from ``pre.json`` is dropped with the file,
       so this row keeps only the arithmetic it was really about: from ``2.0.0rc0`` a *minor*
       changeset yields ``2.0.0rc1`` -- the counter advances, the release *type* reported is the
       current invocation's bump (minor), and the major carried by the earlier prerelease does not
       leak into it.

    Both halves are already green in ``molt.versioning``: ``inc(Version("2.0.0rc0"), MINOR)`` is
    ``2.0.0`` (patch==0 and a prerelease is present, so minor holds) and
    ``next_pre_number(Version("2.0.0rc0"))`` is ``1``.
    """
    state = FakeFullState(changesets=[])
    state.update_package("pkg-a", "2.0.0rc0")
    state.add_changeset(id="minor-bumping-one", releases=[("pkg-a", BumpType.MINOR)])

    plan = _plan(state, default_config, pre="rc")

    assert len(plan.releases) == 1
    release = plan.releases[0]
    assert release.name == "pkg-a"
    assert release.type == BumpType.MINOR
    assert str(release.new_version) == "2.0.0rc1"


# ======================================================================================
# Rows 34-36 -- workspace sources
# ======================================================================================


def test_a_workspace_path_source_resolves_to_the_dependencys_exact_version(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 34 (Adapt). ``index.test.ts:905-923``; ``determine-dependents.ts:207-217``.

    Adapted spelling only: ``workspace:<relpath>`` stands in for a uv workspace source
    (``[tool.uv.sources] pkg-a = {workspace = true}``). The rule is the maximum-churn one -- the
    engine resolves the path against ``package.dir`` relative to ``root_dir`` and substitutes the
    dependency's **exact current version**, so *any* bump breaks the dependent (research README
    section 3.3).
    """
    setup.update_dependency("pkg-b", "pkg-a", "workspace:packages/pkg-a")

    plan = _plan(setup, default_config)

    assert _names(plan) == ["pkg-a", "pkg-b"]
    dependent = _release(plan, "pkg-b")
    assert str(dependent.old_version) == "1.0.0"
    assert str(dependent.new_version) == "1.0.1"


def test_bump_workspace_sources_only_skips_plain_version_dependents(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 35 (Adapt). ``index.test.ts:926-950``.

    Adapted: the config key is molt's ``bump_workspace_sources_only`` and the range inside the
    marker is PEP 440. With the flag on, the graph only carries workspace-source edges, so pkg-b's
    ordinary caret dependency is not merely "in range" -- it is invisible. Note the derived
    dependent carries an **empty** changeset list: it is released by propagation, not by a
    changeset of its own.
    """
    setup.update_dependency("pkg-b", "pkg-a", CARET_1)
    setup.update_dependency("pkg-c", "pkg-a", f"workspace:{CARET_1}")
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-a", BumpType.MAJOR)])
    config = dataclasses.replace(default_config, bump_workspace_sources_only=True)

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-c"], "pkg-b has no workspace source"
    assert _versions(plan) == {"pkg-a": "2.0.0", "pkg-c": "1.0.1"}
    assert _release(plan, "pkg-c").changesets == []


def test_workspace_modifier_sources_resolve_against_the_current_version(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 36 (Adapt). ``index.test.ts:952-982``; ``determine-dependents.ts:203-206``.

    ``workspace:^`` and ``workspace:~`` carry no version: the engine expands them against the
    dependency's current 1.0.0, giving caret and tilde ranges that a major breaks. Both dependents
    therefore get a propagated patch with no changesets of their own.
    """
    setup.update_dependency("pkg-b", "pkg-a", "workspace:~")
    setup.update_dependency("pkg-c", "pkg-a", "workspace:^")
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-a", BumpType.MAJOR)])
    config = dataclasses.replace(default_config, bump_workspace_sources_only=True)

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-c"]
    assert _versions(plan) == {"pkg-a": "2.0.0", "pkg-b": "1.0.1", "pkg-c": "1.0.1"}
    assert _release(plan, "pkg-b").changesets == []
    assert _release(plan, "pkg-c").changesets == []


# ======================================================================================
# Rows 37-38 -- update_internal_dependents: always
# ======================================================================================


def test_update_internal_dependents_always_bumps_transitive_dependents(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 37 (Port). ``index.test.ts:986-1010``; ``determine-dependents.ts:93-98``.

    Under ``always`` the range check is bypassed entirely, so an in-range dependent still gets a
    patch -- and because the new release is pushed onto the worklist, the effect is transitive.
    """
    setup.update_dependency("pkg-b", "pkg-a", CARET_1)
    setup.update_dependency("pkg-c", "pkg-b", CARET_1)
    config = dataclasses.replace(default_config, update_internal_dependents="always")

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-c"]
    assert set(_versions(plan).values()) == {"1.0.1"}


def test_a_none_dependency_never_propagates_even_under_always(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Row 38 (Port). ``index.test.ts:1012-1037``; ``determine-dependents.ts:88-89``.

    The ``none`` short-circuit sits *before* the ``always`` branch, so ``always`` cannot resurrect
    it. Pairs with row 14: ``none`` on a *dependent* is materialised, ``none`` on a *dependency*
    stops the walk.
    """
    setup.update_dependency("pkg-b", "pkg-c", CARET_1)
    setup.add_changeset(id="stuff-and-nonsense", releases=[("pkg-c", BumpType.NONE)])
    config = dataclasses.replace(default_config, update_internal_dependents="always")

    plan = _plan(setup, config)

    assert _names(plan) == ["pkg-a", "pkg-c"], "pkg-b must not be bumped"
    assert _versions(plan) == {"pkg-a": "1.0.1", "pkg-c": "1.0.0"}


# ======================================================================================
# Rows 45-50 -- mixed edges and changetype none
# ======================================================================================


def test_a_prod_edge_bumps_a_dependent_that_a_dev_edge_alone_would_not(
    default_config: FakeConfig,
) -> None:
    """Row 45 (Port). ``index.test.ts:1329-1358``.

    pkg-b sees pkg-a break through a **dev** edge (worth ``none``) and pkg-c break through a
    **prod** edge (worth ``patch``); the maximum wins, so pkg-b is released as a patch. This is the
    row that proves the two edge kinds are combined rather than short-circuited.
    """
    state = FakeFullState(changesets=[])
    state.add_package("pkg-b", "1.0.0").add_package("pkg-c", "1.0.0")
    state.update_dev_dependency("pkg-b", "pkg-a", CARET_1)
    state.update_dependency("pkg-b", "pkg-c", CARET_1)
    state.add_changeset(
        id="big-cats-delight",
        releases=[("pkg-a", BumpType.MAJOR), ("pkg-c", BumpType.MAJOR)],
    )

    plan = _plan(state, default_config)

    assert _names(plan) == ["pkg-a", "pkg-c", "pkg-b"]
    assert _versions(plan) == {"pkg-a": "2.0.0", "pkg-c": "2.0.0", "pkg-b": "1.0.1"}
    assert str(_release(plan, "pkg-b").old_version) == "1.0.0"


def test_a_none_changeset_yields_a_no_op_plan(default_config: FakeConfig) -> None:
    """Row 46 (Port). ``index.test.ts:1361-1382``.

    A ``none`` release exists (so the changelog can carry its summary) but does not move the
    version and does not reach pkg-c. Rows 47 and 48 are the ``peerDependencies`` twins of this
    one and are Drops -- they add nothing once peers are gone.
    """
    state = FakeFullState(changesets=[])
    state.add_package("pkg-b", "1.0.0").add_package("pkg-c", "1.0.0")
    state.update_dependency("pkg-c", "pkg-b", CARET_1)
    state.add_changeset(id="big-cats-delight", releases=[("pkg-b", BumpType.NONE)])

    plan = _plan(state, default_config)

    assert _names(plan) == ["pkg-b"]
    release = plan.releases[0]
    assert str(release.old_version) == "1.0.0"
    assert str(release.new_version) == "1.0.0"
    assert release.changesets == ["big-cats-delight"], "the none release carries its summary"


def test_a_dependent_bump_must_not_discard_an_existing_none_releases_changesets(
    setup: FakeFullState, default_config: FakeConfig
) -> None:
    """Net-new, and a **deliberate divergence**: upstream bug #4 (research README section 3.4).

    ``determine-dependents.ts:151-161`` *replaces* the existing release object whenever a
    dependent's computed type differs from what is already recorded, and the replacement is built
    with ``changesets: []``. A package that has a ``none`` changeset of its own **and** is dragged
    in as a dependent therefore loses that changeset id -- and with it, its summary in the
    changelog.

    Here pkg-b has a ``none`` changeset and depends on pkg-c, which goes major and breaks pkg-b's
    exact pin. Upstream would emit ``pkg-b`` as a patch with ``changesets == []``. molt must
    **merge** into the existing release instead of replacing it, so the id survives. This is the
    expectation the engine has to satisfy; do not "fix" it back to upstream's behavior.
    """
    setup.update_dependency("pkg-b", "pkg-c", EXACT_1)
    setup.add_changeset(id="quiet-lions-give", releases=[("pkg-b", BumpType.NONE)])
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-c", BumpType.MAJOR)])

    plan = _plan(setup, default_config)

    assert _names(plan) == ["pkg-a", "pkg-b", "pkg-c"]
    release = _release(plan, "pkg-b")
    assert release.type == BumpType.PATCH
    assert str(release.old_version) == "1.0.0"
    assert str(release.new_version) == "1.0.1"
    assert release.changesets == ["quiet-lions-give"], (
        "upstream discards this id (bug #4); molt keeps it"
    )


@pytest.mark.parametrize(
    ("constraint", "why"),
    [
        ("workspace:*", "exact-pin modifier form (upstream's code, not its title)"),
        ("workspace:packages/pkg-b", "path form, matched against the package dir"),
    ],
)
def test_a_none_dependency_does_not_bump_a_workspace_source_dependent(
    setup: FakeFullState, default_config: FakeConfig, constraint: str, why: str
) -> None:
    """Rows 49 + 50 (Adapt). ``index.test.ts:1429-1476``.

    Adapted: uv workspace sources stand in for the pnpm ``workspace:`` protocol. Both forms resolve
    to the dependency's exact version -- the strictest possible constraint -- so this is the
    sharpest test that the ``none`` short-circuit fires *before* any range comparison. If it did
    not, an exact pin would break on every ``none`` release.

    Note on row 49: upstream's title says ``workspace:^`` while its body writes ``workspace:*``
    (research doc 02 row 49). The code is authoritative, so ``workspace:*`` is what is ported.
    """
    setup.update_dependency("pkg-c", "pkg-b", constraint)
    setup.add_changeset(id="big-cats-delight", releases=[("pkg-b", BumpType.NONE)])

    plan = _plan(setup, default_config)

    assert _names(plan) == ["pkg-a", "pkg-b"], why
    assert _versions(plan) == {"pkg-a": "1.0.1", "pkg-b": "1.0.0"}


# ======================================================================================
# Rows 39, 40, 43, 44 -- the dependent-bumping matrix
# ======================================================================================
#
# Setup per case (``index.test.ts:1165-1201``): ``pkg-a`` depends on ``pkg-a-b`` with a range of
# the given kind; ``pkg-a-b`` gets a changeset of the given bump; nothing else. The cell value is
# the resulting version of the **dependent** (``pkg-a``); ``1.0.0`` means "not bumped", which the
# engine may express either as an absent release or as a ``none`` release at the old version.
#
# Expectation tables, verbatim from research doc 02 lines 114-135:
#
#   dep:  none  ^ 1.0.0   ~ 1.0.0   = 1.0.0        dev: never bumps (all 1.0.0)
#         patch ^ 1.0.0   ~ 1.0.0   = 1.0.1
#         minor ^ 1.0.0   ~ 1.0.1   = 1.0.1        "always" overrides (dep + optional):
#         major ^ 1.0.1   ~ 1.0.1   = 1.0.1          patch: ^ -> 1.0.1, ~ -> 1.0.1
#                                                    minor: ^ -> 1.0.1
#
# Kinds: upstream runs dep / dev / peer. molt runs dep / dev / **optional**. ``peer`` is dropped
# (research README section 4.4) -- 72 cells across the six upstream suites. ``optional``
# (``[project.optional-dependencies]``, i.e. extras) is net-new here: upstream's builder has no
# writer for ``optionalDependencies`` even though ``determine-dependents.ts:102`` handles the
# field in the same switch case as ``dependencies``, so it shares ``_DEP_EXPECTED`` and the
# ``always`` overrides. Keeping it distinct from ``dev`` is the point -- extras and PEP 735 dev
# groups have opposite bump semantics (research README section 4.5).
#
# The two ``onlyUpdatePeerDependentsWhenOutOfRange`` suites (rows 41, 42) are Drops recorded in
# ``test_deliberately_not_ported.py``; they are behaviourally identical to default/always for
# non-peer kinds, so no coverage is lost (research doc 02 rows 41-42).

_DEP_EXPECTED: dict[BumpType, dict[str, str]] = {
    BumpType.NONE: {"^": "1.0.0", "~": "1.0.0", "=": "1.0.0"},
    BumpType.PATCH: {"^": "1.0.0", "~": "1.0.0", "=": "1.0.1"},
    BumpType.MINOR: {"^": "1.0.0", "~": "1.0.1", "=": "1.0.1"},
    BumpType.MAJOR: {"^": "1.0.1", "~": "1.0.1", "=": "1.0.1"},
}

_DEV_EXPECTED: dict[BumpType, dict[str, str]] = {
    bump: {"^": "1.0.0", "~": "1.0.0", "=": "1.0.0"} for bump in _BUMPS
}

_ALWAYS_OVERRIDES: dict[BumpType, dict[str, str]] = {
    BumpType.PATCH: {"^": "1.0.1", "~": "1.0.1"},
    BumpType.MINOR: {"^": "1.0.1"},
}


@dataclasses.dataclass(frozen=True)
class _MatrixSuite:
    """One upstream ``describeDependentBumping`` suite."""

    name: str
    always: bool
    render: Callable[[str, Version], str]


_MATRIX_SUITES: tuple[_MatrixSuite, ...] = (
    # Row 39 (Port) -- default config, plain PEP 440 ranges.
    _MatrixSuite(name="default", always=False, render=_render_range),
    # Row 40 (Port) -- update_internal_dependents="always".
    _MatrixSuite(name="always", always=True, render=_render_range),
    # Row 43 (Adapt) -- workspace source, modifier only.
    _MatrixSuite(name="workspace-modifier", always=False, render=_render_workspace_modifier),
    # Row 44 (Adapt) -- workspace source, modifier + version.
    _MatrixSuite(name="workspace-versioned", always=False, render=_render_workspace_versioned),
)

_MATRIX_SUITES_BY_NAME = {suite.name: suite for suite in _MATRIX_SUITES}


def _matrix_cases() -> list[tuple[str, str, str, BumpType, str, str]]:
    """Flatten the expectation tables into one row per cell, with a ``why`` column."""
    cases: list[tuple[str, str, str, BumpType, str, str]] = []
    for suite in _MATRIX_SUITES:
        for dep_kind in _DEP_KINDS:
            bumping = dep_kind in _BUMPING_DEP_KINDS
            table = _DEP_EXPECTED if bumping else _DEV_EXPECTED
            for bump in _BUMPS:
                for range_kind in _RANGE_KINDS:
                    expected = table[bump][range_kind]
                    if not bumping:
                        why = "dev-only edge: a dev dependency cannot break an install"
                    elif bump is BumpType.NONE:
                        why = (
                            "a none dependency short-circuits before any range check "
                            "(determine-dependents.ts:88-89), so the range is irrelevant"
                        )
                    elif expected == "1.0.0":
                        why = f"{bump.value} keeps the dependency inside the {range_kind} range"
                    else:
                        why = f"{bump.value} pushes the dependency out of {range_kind} -> patch"
                    if suite.always and bumping:
                        override = _ALWAYS_OVERRIDES.get(bump, {}).get(range_kind)
                        if override is not None:
                            why = (
                                f"always: in-range {bump.value} still bumps "
                                f"(base table said {expected})"
                            )
                            expected = override
                    cases.append((suite.name, range_kind, dep_kind, bump, expected, why))
    return cases


MATRIX_CASES = _matrix_cases()


def _matrix_state(dep_kind: str, version_range: str, bump: BumpType) -> FakeFullState:
    """``pkg-a`` (the dependent) depends on ``pkg-a-b`` (the bumped dependency)."""
    state = FakeFullState(changesets=[])
    state.add_package("pkg-a-b", "1.0.0")
    state.add_changeset(id="matrix-case", releases=[("pkg-a-b", bump)])
    writer = {
        "dev": state.update_dev_dependency,
        "optional": state.update_optional_dependency,
        "dep": state.update_dependency,
    }[dep_kind]
    writer("pkg-a", "pkg-a-b", version_range)
    return state


@pytest.mark.parametrize(
    ("suite", "range_kind", "dep_kind", "bump", "expected", "why"), MATRIX_CASES
)
def test_dependent_bumping_matrix(
    suite: str, range_kind: str, dep_kind: str, bump: BumpType, expected: str, why: str
) -> None:
    """Rows 39/40 (Port) and 43/44 (Adapt). ``index.test.ts:1041-1327``.

    144 generated cases: 4 suites x 3 range kinds x 4 bumps x 3 dependency kinds. A dependent
    bumps (always by a *patch*, never more) exactly when the dependency's new version falls
    outside the declared range -- or, under ``update_internal_dependents="always"``, whenever the
    dependency moves at all. Runtime and extras edges behave identically; dev-group edges never
    bump.
    """
    spec = _MATRIX_SUITES_BY_NAME[suite]
    config = dataclasses.replace(
        ported_default_config(),
        update_internal_dependents="always" if spec.always else "out-of-range",
    )
    state = _matrix_state(dep_kind, spec.render(range_kind, _ONE), bump)

    plan = _plan(state, config)
    versions = _versions(plan)

    # Sanity check, as upstream does: the dependency itself moved as requested.
    assert versions["pkg-a-b"] == str(inc(_ONE, bump)), why

    dependent = versions.get("pkg-a")
    if expected == "1.0.0":
        assert dependent in (None, "1.0.0"), why
    else:
        assert dependent == expected, why


# ======================================================================================
# Property tests -- the invariants the loop's correctness rests on
# ======================================================================================

_PROP_SIZE = 5
_prop_edges = st.lists(
    st.tuples(
        st.integers(0, _PROP_SIZE - 1),
        st.integers(0, _PROP_SIZE - 1),
        st.sampled_from(_RANGE_KINDS),
    ),
    max_size=8,
)
_prop_requests = st.lists(
    st.tuples(st.integers(0, _PROP_SIZE - 1), st.sampled_from(_BUMPS)), min_size=1, max_size=6
)


def _numbered(requests: Sequence[tuple[int, BumpType]]) -> list[tuple[str, int, BumpType]]:
    return [(f"prop-changeset-{i}", target, bump) for i, (target, bump) in enumerate(requests)]


def _prop_state(
    edges: Sequence[tuple[int, int, str]],
    requests: Sequence[tuple[str, int, BumpType]],
) -> FakeFullState:
    """A small acyclic workspace: ``prop-<i>`` may only depend on a lower-numbered package."""
    state = FakeFullState(changesets=[])
    for index in range(_PROP_SIZE):
        state.add_package(f"prop-{index}", "1.0.0")
    for dependent, dependency, kind in edges:
        if dependent > dependency:
            state.update_dependency(
                f"prop-{dependent}", f"prop-{dependency}", _render_range(kind, _ONE)
            )
    for changeset_id, target, bump in requests:
        state.add_changeset(id=changeset_id, releases=[(f"prop-{target}", bump)])
    return state


@pytest.mark.property
@given(edges=_prop_edges, requests=_prop_requests)
@settings(max_examples=30, deadline=None)
def test_the_fixpoint_loop_always_terminates(
    edges: list[tuple[int, int, str]], requests: list[tuple[int, BumpType]]
) -> None:
    """Termination is guaranteed only by monotonicity (research README section 3.1).

    ``index.ts:161-183`` loops until all three passes report no change. That halts only because
    every mutation climbs the ``none < patch < minor < major`` ladder and ``old_version`` is read
    once and never re-read from a mutated release. Both observable consequences are asserted here:
    the call returns, every release keeps its manifest ``old_version``, and no release ends up
    *below* the bump its changesets asked for. A regression to "always report updated" would hang
    this test rather than fail it -- which is exactly the signal wanted.
    """
    state = _prop_state(edges, _numbered(requests))

    plan = _plan(state, ported_default_config())

    declared: dict[str, BumpType] = {}
    for target, bump in requests:
        name = f"prop-{target}"
        declared[name] = highest([declared.get(name, BumpType.NONE), bump])
    for release in plan.releases:
        assert str(release.old_version) == "1.0.0", "old_version is held constant during the walk"
        wanted = declared.get(release.name, BumpType.NONE)
        assert BumpType(release.type).rank >= wanted.rank, "bump types only ever climb"


@pytest.mark.property
@given(edges=_prop_edges, requests=_prop_requests)
@settings(max_examples=30, deadline=None)
def test_the_fixpoint_is_confluent_in_changeset_order(
    edges: list[tuple[int, int, str]], requests: list[tuple[int, BumpType]]
) -> None:
    """Final versions must not depend on the order changesets are read off disk.

    Changesets arrive in filesystem order, which is not stable across platforms -- so if the
    resulting versions depended on it, molt would produce different releases on Windows and Linux.
    Note the assertion is over ``{name: new_version}``, not over the release *list*: output order
    is insertion-defined and genuinely does vary with input order (rows 20 vs 21).
    """
    numbered = _numbered(requests)
    config = ported_default_config()

    forward = _versions(_plan(_prop_state(edges, numbered), config))
    backward = _versions(_plan(_prop_state(edges, list(reversed(numbered))), config))

    assert forward == backward


@pytest.mark.property
@given(
    version=st.builds(
        lambda a, b, c: Version(f"{a}.{b}.{c}"),
        st.integers(0, 20),
        st.integers(0, 20),
        st.integers(0, 20),
    ),
    range_kind=st.sampled_from(_RANGE_KINDS),
    dep_kind=st.sampled_from(_DEP_KINDS),
    bump=st.sampled_from(_BUMPS),
    always=st.booleans(),
)
@settings(max_examples=60, deadline=None)
def test_a_dependent_bumps_exactly_when_its_constraint_breaks(
    version: Version, range_kind: str, dep_kind: str, bump: BumpType, always: bool
) -> None:
    """The whole dependent-bumping matrix, stated as one rule over arbitrary versions.

    A dependent takes a **patch** iff its dependency actually moves and either the new version
    falls outside the declared constraint or ``update_internal_dependents == "always"``; a
    dev-group edge never bumps, while runtime and extras edges behave identically. Expressing it
    against ``molt.versioning.satisfies`` rather than a hand-written table is what keeps it honest
    for 0.x versions, where ``caret`` changes shape (research doc 02 lines 123-126).
    """
    rendered = _render_range(range_kind, version)
    specifier = parse_range(rendered)
    assert specifier is not None, rendered

    state = FakeFullState(changesets=[])
    state.add_package("pkg-a-b", str(version))
    state.add_changeset(id="prop-case", releases=[("pkg-a-b", bump)])
    writer = {
        "dev": state.update_dev_dependency,
        "optional": state.update_optional_dependency,
        "dep": state.update_dependency,
    }[dep_kind]
    writer("pkg-a", "pkg-a-b", rendered)
    config = dataclasses.replace(
        ported_default_config(),
        update_internal_dependents="always" if always else "out-of-range",
    )

    plan = _plan(state, config)

    moved = bump is not BumpType.NONE
    breaks = not satisfies(inc(version, bump), specifier)
    should_bump = dep_kind in _BUMPING_DEP_KINDS and moved and (always or breaks)

    dependent = _versions(plan).get("pkg-a")
    if should_bump:
        assert dependent == "1.0.1"
    else:
        assert dependent in (None, "1.0.0")
