"""Conformance tests for `molt version --pre {a,b,rc,dev}`.

**This file is net-new, not a port.** It is the replacement for the entire dropped `pre.json`
state machine: 32 upstream rows die (6 in `packages/pre/src/index.test.ts`, plus the pre-mode rows
of the engine suite), and this file is what stands in their place.

Why they die (research README section 4.2, doc 02 "packages/pre/src/index.test.ts"):

* `1.0.1-next.0` is **unrepresentable in PEP 440** -- only `aN`, `bN`, `rcN` and `.devN` are legal
  prerelease spellings, so an arbitrary `tag` is a non-starter before any design discussion begins.
* Independently, removing `pre.json` is the community's #6 most-wanted change; its global branch
  state is the loudest complaint in the v3 umbrella thread (README section 5, row 6).

So prerelease in molt is an **invocation flag**, not persistent state: no `pre.json`, no `mode`, no
`tag`, no used-changeset list, no initial-versions map, and therefore no enter/exit transitions to
get wrong. Everything the flag does is a function of (current version on disk, this invocation's
bump, this invocation's phase).

Layout
------
The file is split in two, and is **deliberately unguarded at module level** so the arithmetic half
runs today: `molt.versioning` is implemented, `molt.engine` is not. The composed tests each call
`pytest.importorskip("molt.engine")` from inside the test body, so a missing engine skips four
tests instead of the whole file (test contract section 1).

Citations: research README sections 4.1/4.2, `roadmap/research/test-suite/02-release-plan-engine.md`
(engine row 33 and the `packages/pre` table, including its "Replacement work" spec),
`roadmap/research/test-suite/01-versioning-core.md` (increment.test.ts row 1);
upstream `assemble-release-plan/src/increment.ts:5-25` and `index.ts:getPreVersion`.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from packaging.version import Version
from tests.engine.fake_state import FakeFullState

from molt.config import default_config
from molt.versioning import BumpType, inc, next_pre_number

pytestmark = pytest.mark.unit

#: The engine lands in two changes, so the guard names the *submodule* that owns the target rather
#: than the package. `molt.engine` exists from build step 6 (the dependents graph); the plan half
#: -- `assemble_release_plan` -- is build step 7 and lands as `molt.engine.assemble`. Guarding on
#: the package would have turned these rows from skipped to erroring the moment the graph shipped,
#: which is the same "module exists => implemented" assumption `tests/cli/conftest.py` documents.
ENGINE_TARGET = "molt.engine.assemble"
ENGINE_REASON = "build step 7 - the release-plan engine is not implemented yet (TDD target)"

#: The four legal PEP 440 prerelease phases, i.e. the accepted values of `--pre`.
PHASES = ["a", "b", "rc", "dev"]

#: How each phase is spelled onto a release. `dev` is the odd one out: it is a *development
#: release* segment (`.devN`, dot-prefixed) rather than a *pre-release* segment (`aN`/`bN`/`rcN`).
#: It nonetheless travels the same code path -- `packaging.Version.is_prerelease` is true for it,
#: which is what `inc` keys off, and `next_pre_number` reads `Version.dev` before `Version.pre`.
#: The one thing that is NOT interchangeable is ordering: `.devN` sorts *below* `aN`.
PHASE_SUFFIX = {"a": "a", "b": "b", "rc": "rc", "dev": ".dev"}


def v(text: str) -> Version:
    return Version(text)


# --------------------------------------------------------------------------------------
# Part (a): the arithmetic. Unguarded -- `molt.versioning` is implemented.
# --------------------------------------------------------------------------------------


def apply_pre(old: Version, bump: BumpType, phase: str | None) -> Version:
    """Reference composition of one `molt version [--pre <phase>]` invocation.

    This is the executable statement of the target algorithm -- the engine must produce the same
    numbers. It is deliberately tiny, which is the point of killing `pre.json`::

        base = inc(old, bump)                       # the stable version this invocation targets
        if bump is none or no phase:  return base   # increment.ts:9-10 -- `none` short-circuits
        return base + phase + next_pre_number(old)  # counter comes from disk, not from state

    Two `molt.versioning` behaviours carry the whole design:

    * `inc` leaves the release tuple alone when the old version is already a prerelease
      (`1.2.1rc0` + patch == `1.2.1`), so repeated invocations stay on one target release and
      exiting is just "run without the flag" (research doc 01 section 3.3).
    * `next_pre_number` returns `0` for a non-prerelease, which is the sentinel that makes the
      first `--pre` invocation produce `rc0` rather than `rc1` (doc 01 section 15.3).
    """
    base = inc(old, bump)
    if bump is BumpType.NONE or phase is None:
        return base
    return Version(f"{base}{PHASE_SUFFIX[phase]}{next_pre_number(old)}")


PRE_CASES = [
    # (old,        bump,             phase,  expected,      why)
    (
        "1.2.0",
        BumpType.PATCH,
        "rc",
        "1.2.1rc0",
        "the replacement spec, case (b): --pre rc on 1.2.0 with a patch changeset",
    ),
    (
        "1.2.1rc0",
        BumpType.PATCH,
        "rc",
        "1.2.1rc1",
        "case (c): re-running increments the counter; the release tuple does not move",
    ),
    (
        "1.2.1rc1",
        BumpType.PATCH,
        "rc",
        "1.2.1rc2",
        "and keeps incrementing -- there is no state to consult, only the version on disk",
    ),
    (
        "1.2.1rc1",
        BumpType.PATCH,
        None,
        "1.2.1",
        "case (d): exiting is just omitting the flag; the same pending patch lands stable",
    ),
    (
        "1.2.0",
        BumpType.MINOR,
        "rc",
        "1.3.0rc0",
        "a minor changeset targets 1.3.0 and prereleases that",
    ),
    (
        "1.3.0rc0",
        BumpType.MINOR,
        "rc",
        "1.3.0rc1",
        "patch == 0 and already a prerelease -> minor stays put (doc 01 section 3.3)",
    ),
    (
        "1.2.0",
        BumpType.MAJOR,
        "rc",
        "2.0.0rc0",
        "major targets 2.0.0; there is no 0.x special case anywhere in bump math",
    ),
    (
        "2.0.0rc0",
        BumpType.MAJOR,
        "rc",
        "2.0.0rc1",
        "minor == patch == 0 and already a prerelease -> major stays put",
    ),
]


@pytest.mark.parametrize(("old", "bump", "phase", "expected", "why"), PRE_CASES)
def test_pre_arithmetic(
    old: str, bump: BumpType, phase: str | None, expected: str, why: str
) -> None:
    assert apply_pre(v(old), bump, phase) == v(expected), why


PHASE_CASES = [
    # (phase, expected,      why)
    ("a", "1.2.1a0", "alpha"),
    ("b", "1.2.1b0", "beta"),
    ("rc", "1.2.1rc0", "release candidate"),
    ("dev", "1.2.1.dev0", "development release -- dot-prefixed, and sorts BELOW a/b/rc"),
]


@pytest.mark.parametrize(("phase", "expected", "why"), PHASE_CASES)
def test_all_four_phases_use_one_code_path(phase: str, expected: str, why: str) -> None:
    """All four `--pre` values are produced by the same composition, `dev` included.

    `dev` was the open question: PEP 440 classes `.devN` as a *development release* rather than a
    pre-release, so it could have needed its own branch. It does not. `Version.is_prerelease` is
    true for `.devN` (which is what `inc` reads) and `next_pre_number` checks `Version.dev` first
    (doc 01 section 15.3, mirrored in `tests/versioning/test_bump.py`). Only the suffix spelling
    differs: `.dev` carries a leading dot, `a`/`b`/`rc` do not.
    """
    assert apply_pre(v("1.2.0"), BumpType.PATCH, phase) == v(expected), why


@pytest.mark.parametrize(("phase", "expected", "_why"), PHASE_CASES)
def test_the_asserted_spellings_are_the_ones_packaging_normalises_to(
    phase: str, expected: str, _why: str
) -> None:
    """Guard the fixtures themselves: these strings are canonical PEP 440, not our preference.

    `packaging` normalises `alpha`/`beta`/`c`/`pre`/`-rc.0` spellings onto `a`/`b`/`rc`, and
    `1.2.1dev0` onto `1.2.1.dev0`. If the CLI ever accepted `--pre alpha`, it would still have to
    write these.
    """
    assert str(v(expected)) == expected
    assert str(apply_pre(v("1.2.0"), BumpType.PATCH, phase)) == expected


def test_a_none_release_ignores_the_pre_flag() -> None:
    """`none` short-circuits *before* any prerelease suffixing -- the one ported increment row.

    This is the only assertion in `increment.test.ts`, and it is the reason a `none` changeset
    never smuggles a version change into a prerelease run.

    research test-suite doc 01 (increment.test.ts, row 1); increment.ts:9-10.
    """
    assert apply_pre(v("1.2.0"), BumpType.NONE, "rc") == v("1.2.0")
    assert apply_pre(v("1.2.1rc0"), BumpType.NONE, "rc") == v("1.2.1rc0")


def test_a_plain_invocation_ignores_prerelease_bookkeeping() -> None:
    """Without the flag, the arithmetic is exactly what it was before prerelease existed."""
    assert apply_pre(v("1.2.0"), BumpType.PATCH, None) == v("1.2.1")
    assert apply_pre(v("1.2.0"), BumpType.MAJOR, None) == v("2.0.0")


def test_the_reported_bump_is_this_invocations_bump_not_the_accumulated_one() -> None:
    """The PEP 440 analogue of engine row 33.

    Upstream: a package already at `2.0.0-next.0` from a *previous* major prerelease, with only a
    `minor` changeset pending now, releases `2.0.0-next.1` and reports type `minor`. The version
    keeps the higher target (it is already there), but the reported bump describes what changed in
    *this* invocation.

    molt gets the same result with no state at all: `inc` refuses to move a release tuple that is
    already a prerelease, so the target survives on disk instead of in `pre.json`. The dropped half
    of row 33 is the used-changeset tracking, which only existed to stop `pre.json` re-counting old
    changesets.

    research test-suite doc 02 (engine row 33), README section 4.2; increment.ts:5-25.
    """
    assert apply_pre(v("2.0.0rc0"), BumpType.MINOR, "rc") == v("2.0.0rc1")


def test_exiting_prerelease_drops_the_suffix_for_every_phase() -> None:
    """Whatever phase was used, the plain run lands on the same stable version."""
    for phase in PHASES:
        pre = apply_pre(v("1.2.0"), BumpType.PATCH, phase)
        assert apply_pre(pre, BumpType.PATCH, None) == v("1.2.1"), phase


def test_phase_transitions_share_one_counter_and_only_move_forward() -> None:
    """Two hazards of *changing* `--pre` phase mid-release, both open CLI decisions.

    Neither is a defect in the arithmetic -- both fall out of `next_pre_number` reading whatever
    prerelease counter is on disk without caring which phase produced it (doc 01 section 15.3).
    They are pinned here so the CLI's `--pre` handling is designed against them rather than
    discovering them in production.

    **Hazard 1 -- the counter is shared across phases, so a forward switch skips a number.**
    Going `--pre a` then `--pre rc` yields `1.2.1a0` then `1.2.1rc1`: `rc0` is never published.
    Harmless for ordering (it still climbs) but surprising in a changelog, and it means the
    counter reads as a per-release-tuple sequence, not a per-phase one. Resetting it to `0` on a
    phase change would be the alternative -- and would need its own guard, since PEP 440 orders
    `a` below `rc` only within the same counter-agnostic comparison.

    **Hazard 2 -- a backward switch produces a version that sorts below what is published.**
    PEP 440 orders `.devN < aN < bN < rcN < final`, so `--pre dev` after `--pre rc` on the same
    target release goes backwards. On an immutable index (research README section 4.3) that is
    unrecoverable, which argues for the CLI refusing it outright.
    """
    assert v("1.2.1.dev0") < v("1.2.1a0") < v("1.2.1b0") < v("1.2.1rc0") < v("1.2.1")

    forwards = apply_pre(v("1.2.1a0"), BumpType.PATCH, "rc")
    assert forwards == v("1.2.1rc1"), "hazard 1: a0 -> rc skips rc0; the counter is shared"
    assert forwards > v("1.2.1a0"), "a forward switch still climbs, so it is safe to publish"

    backwards = apply_pre(v("1.2.1rc0"), BumpType.PATCH, "dev")
    assert backwards == v("1.2.1.dev1")
    assert backwards < v("1.2.1rc0"), "hazard 2: rc -> dev moves BACKWARDS; the CLI must refuse"


def test_documented_divergence_pre_json_is_not_ported() -> None:
    """Pin the divergence itself, not just its consequences.

    changesets stores prerelease state in `.changeset/pre.json` (`{mode, tag, changesets,
    initialVersions}`) and drives it with `enterPre` / `exitPre` / `readPreState`. molt has none of
    it. The forcing function is PEP 440 -- an arbitrary `tag` such as `next` cannot be written at
    all -- and the payoff is that the loudest complaint about changesets (~15 open issues, the #6
    community request) simply has no surface here.

    research README sections 4.2 and 5 (row 6); test-suite doc 02, `packages/pre` table.
    """
    with pytest.raises(Exception, match="Invalid version"):
        Version("1.0.1-next.0")

    for phase in PHASES:
        assert apply_pre(v("1.0.0"), BumpType.PATCH, phase).is_prerelease, (
            f"--pre {phase} is expressible in PEP 440; a free-form tag is not"
        )


# --------------------------------------------------------------------------------------
# Properties
# --------------------------------------------------------------------------------------

stable_versions = st.builds(
    lambda a, b, c: Version(f"{a}.{b}.{c}"),
    st.integers(0, 50),
    st.integers(0, 50),
    st.integers(0, 50),
)
real_bumps = st.sampled_from([BumpType.PATCH, BumpType.MINOR, BumpType.MAJOR])
phases = st.sampled_from(PHASES)


@pytest.mark.property
@given(base=stable_versions, bump=real_bumps, phase=phases, rounds=st.integers(2, 8))
def test_repeated_pre_invocations_climb_inside_one_release_tuple(
    base: Version, bump: BumpType, phase: str, rounds: int
) -> None:
    """Re-running `--pre <phase>` is strictly increasing and never escapes its target release.

    Both halves matter. Strictly increasing is what makes every prerelease uploadable to an
    immutable index (research README section 4.3). Never escaping the target tuple is what makes
    exiting land where the changesets said it would -- if the tuple crept, `--pre` would silently
    inflate the release.
    """
    target = inc(base, bump).release
    current = base
    for _ in range(rounds):
        nxt = apply_pre(current, bump, phase)
        assert nxt > current, "the counter must never stall or regress"
        assert nxt.release == target, "the target release tuple is fixed by the changesets"
        assert nxt.is_prerelease
        current = nxt


@pytest.mark.property
@given(base=stable_versions, bump=real_bumps, phase=phases, rounds=st.integers(1, 8))
def test_exiting_pre_always_lands_on_the_version_the_changesets_asked_for(
    base: Version, bump: BumpType, phase: str, rounds: int
) -> None:
    """After any number of prerelease rounds, a plain run yields `inc(base, bump)` exactly."""
    current = base
    for _ in range(rounds):
        current = apply_pre(current, bump, phase)
    assert apply_pre(current, bump, None) == inc(base, bump)


# --------------------------------------------------------------------------------------
# Part (b): the composed flow. Guarded per test so the arithmetic above still runs today.
# --------------------------------------------------------------------------------------


def _single_package_plan(engine: Any, version: str, bump: BumpType, **kwargs: Any) -> Any:
    """One package at `version` with one pending changeset, planned with `kwargs`."""
    state = FakeFullState(changesets=[])
    state.update_package("pkg-a", version)
    state.add_changeset(releases=[("pkg-a", bump)])
    return engine.assemble_release_plan(
        state.changesets, state.packages, default_config(), **kwargs
    )


def test_composed_pre_rc_produces_the_first_release_candidate() -> None:
    engine = pytest.importorskip(ENGINE_TARGET, reason=ENGINE_REASON)

    plan = _single_package_plan(engine, "1.2.0", BumpType.PATCH, pre="rc")

    (release,) = plan.releases
    assert str(release.old_version) == "1.2.0"
    assert str(release.new_version) == "1.2.1rc0"
    assert release.type == BumpType.PATCH


def test_composed_pre_rc_rerun_increments_the_counter() -> None:
    """Row 33's mechanism, without `pre.json`: the counter is read off the current version."""
    engine = pytest.importorskip(ENGINE_TARGET, reason=ENGINE_REASON)

    plan = _single_package_plan(engine, "1.2.1rc0", BumpType.PATCH, pre="rc")

    (release,) = plan.releases
    assert str(release.new_version) == "1.2.1rc1"
    assert release.type == BumpType.PATCH, "the current invocation's bump, not the accumulated one"


@pytest.mark.parametrize(("phase", "expected", "why"), PHASE_CASES)
def test_composed_plan_supports_every_phase(phase: str, expected: str, why: str) -> None:
    engine = pytest.importorskip(ENGINE_TARGET, reason=ENGINE_REASON)

    plan = _single_package_plan(engine, "1.2.0", BumpType.PATCH, pre=phase)

    (release,) = plan.releases
    assert str(release.new_version) == expected, why


def test_composed_plan_without_the_flag_exits_prerelease() -> None:
    engine = pytest.importorskip(ENGINE_TARGET, reason=ENGINE_REASON)

    plan = _single_package_plan(engine, "1.2.1rc1", BumpType.PATCH)

    (release,) = plan.releases
    assert str(release.new_version) == "1.2.1", "no flag, no state to consult, no suffix"


def test_composed_none_release_ignores_the_flag() -> None:
    engine = pytest.importorskip(ENGINE_TARGET, reason=ENGINE_REASON)

    plan = _single_package_plan(engine, "1.2.0", BumpType.NONE, pre="rc")

    (release,) = plan.releases
    assert str(release.new_version) == "1.2.0", "increment.ts:9-10 -- `none` short-circuits first"


def test_exiting_pre_does_not_backfill_a_release_for_an_untouched_package() -> None:
    """Do NOT reproduce the upstream pre-exit backfill bug (research README section 3.4).

    Upstream, leaving pre mode walks every package and tests `preVersions.get(name) !== 0`. A
    package that was skipped has no entry at all, so `undefined !== 0` is true and it is handed a
    `patch` release with `newVersion: null` -- a release that cannot be applied. molt has no
    `preVersions` map to be absent from, so the corrected behaviour is asserted here instead of
    the bug.

    The fixture is shaped to be a *candidate* for that bug rather than merely adjacent to it:
    `pkg-b` carries a prerelease version (`2.0.0rc3`), which is what makes an engine believe it
    was mid-prerelease and therefore owes a backfill, and it has no changeset and no dependency
    edge to `pkg-a`, so a correct engine has nothing to say about it. A stable `pkg-b` would make
    this test vacuous -- no plausible spelling of the bug could reach it.
    """
    engine = pytest.importorskip(ENGINE_TARGET, reason=ENGINE_REASON)

    state = FakeFullState(changesets=[])
    state.update_package("pkg-a", "1.2.1rc1")
    state.add_package("pkg-b", "2.0.0rc3")
    state.add_changeset(releases=[("pkg-a", BumpType.PATCH)])

    plan = engine.assemble_release_plan(state.changesets, state.packages, default_config())

    assert [release.name for release in plan.releases] == ["pkg-a"], (
        "a prerelease-versioned package with no changesets is not a release candidate"
    )
    assert str(plan.releases[0].new_version) == "1.2.1"
    assert all(release.new_version is not None for release in plan.releases)
