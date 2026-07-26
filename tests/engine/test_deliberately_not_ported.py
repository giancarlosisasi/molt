"""Audit record of every group-2 behavior molt deliberately does NOT port.

One skipping test per dropped behavior, each carrying the reason from the Drop column of
`roadmap/research/test-suite/02-release-plan-engine.md`. Nothing here executes product code; the
file exists so that every divergence stays visible and greppable (`uv run pytest -m not_ported`)
instead of quietly vanishing from the port.

What is recorded, and how it reconciles with the group file
-----------------------------------------------------------
* **10 engine row-level drops** -- rows 15, 23, 28, 30, 31, 32, 41, 42, 47, 48 of the 50-row
  `packages/assemble-release-plan/src/index.test.ts` table (header: Port 27 / Adapt 13 / Drop 10).
* **6 `packages/pre/src/index.test.ts` drops** -- the whole file (Port 0 / Adapt 0 / Drop 6).
* **3 structural drops** -- the 72 peer cells of the 216-case dependent-bump matrix, the 48
  dep+dev cells of the two dropped config suites, and `updatePeerDependency` on the fixture
  builder. Recorded per dropped *suite*, not per cell.

Two root causes cover all of it: `peerDependencies` has no Python analogue (research README
section 4.4) and `pre.json` is replaced by the stateless `molt version --pre` flag (README
section 4.2). Replacement coverage lives in `tests/versioning/test_pre.py`.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.not_ported


# --------------------------------------------------------------------------------------
# packages/assemble-release-plan/src/index.test.ts -- 10 row-level drops
# --------------------------------------------------------------------------------------


def test_engine_row_15_none_release_for_an_ignored_peer_dependency_is_dropped() -> None:
    pytest.skip(
        "row 15: peerDependencies are removed entirely (research README section 4.4); the "
        "'ignored dependent is still emitted as a none release' assertion is already covered by "
        "row 14 (runtime dep) and row 16 (dev dep), so no coverage is lost."
    )


def test_engine_row_23_fixed_forced_bump_reaching_a_peer_dependent_is_dropped() -> None:
    pytest.skip(
        "row 23: peerDependencies removed (research README section 4.4); the underlying mechanism "
        "-- a fixed-group forced bump propagating to a dependent -- is kept as an adapted test "
        "with a normal runtime dep in tests/engine/test_assemble.py."
    )


def test_engine_row_28_linked_forced_bump_reaching_a_peer_dependent_is_dropped() -> None:
    pytest.skip(
        "row 28: peerDependencies removed (research README section 4.4); redundant with row 23's "
        "mechanism, which is the one kept in adapted form."
    )


def test_engine_row_30_exit_pre_mode_emits_no_release_without_changesets_is_dropped() -> None:
    pytest.skip(
        "row 30: the pre.json exit-mode state machine is removed -- molt's --pre is an invocation "
        "flag with no persistent mode (research README section 4.2); replacement coverage lives "
        "in tests/versioning/test_pre.py."
    )


def test_engine_row_31_dev_dependents_bump_when_exiting_pre_mode_is_dropped() -> None:
    pytest.skip(
        "row 31: exit-mode state machine plus a `1.0.1-next.0` tag that is illegal in PEP 440 "
        "(research README section 4.2); molt only has a/b/rc/dev phases and no exit transition."
    )


def test_engine_row_32_ignored_dev_dependents_when_exiting_pre_mode_is_dropped() -> None:
    pytest.skip(
        "row 32: exit-mode state machine (research README section 4.2); with no persistent pre "
        "mode there is no exit pass in which an ignored package could be backfilled."
    )


def test_engine_row_41_only_update_peer_dependents_when_out_of_range_suite_is_dropped() -> None:
    pytest.skip(
        "row 41: the onlyUpdatePeerDependentsWhenOutOfRange config option dies with peers "
        "(research README section 4.4) and its 36 cases are identical to the default suite -- "
        "the load-bearing fact is that the flag gates range REWRITING only, never the bump matrix."
    )


def test_engine_row_42_only_update_peer_dependents_combined_suite_is_dropped() -> None:
    pytest.skip(
        "row 42: same removed config option (research README section 4.4); its 36 cases are "
        "identical to the updateInternalDependents='always' suite for dep and dev kinds, so "
        "keeping row 40 loses nothing."
    )


def test_engine_row_47_peer_tilde_dependent_not_bumped_by_none_is_dropped() -> None:
    pytest.skip(
        "row 47: peerDependencies removed (research README section 4.4); the `none` dependency "
        "short-circuit it pins is covered without peers by row 46."
    )


def test_engine_row_48_peer_caret_dependent_not_bumped_by_none_is_dropped() -> None:
    pytest.skip(
        "row 48: peerDependencies removed (research README section 4.4); duplicate of row 47's "
        "assertion with a different range, and likewise covered by row 46."
    )


# --------------------------------------------------------------------------------------
# packages/pre/src/index.test.ts -- all 6 rows
# --------------------------------------------------------------------------------------


def test_pre_json_row_1_enter_pre_writes_the_state_file_is_dropped() -> None:
    pytest.skip(
        "pre row 1: enterPre writing .changeset/pre.json is dropped -- molt has no pre.json and "
        "no `molt pre enter` verb; prerelease is the stateless `molt version --pre` flag "
        "(research README section 4.2)."
    )


def test_pre_json_row_2_enter_pre_while_already_in_pre_mode_raises_is_dropped() -> None:
    pytest.skip(
        "pre row 2: PreEnterButInPreModeError cannot exist without a persistent mode "
        "(research README section 4.2); re-running `molt version --pre rc` is simply the "
        "counter-increment case."
    )


def test_pre_json_row_3_re_entering_pre_after_exit_preserves_changesets_is_dropped() -> None:
    pytest.skip(
        "pre row 3: re-entry preserving the used-changeset list is dropped with the whole "
        "state machine (research README section 4.2); molt never tracks consumed changesets "
        "across invocations."
    )


def test_pre_json_row_4_exit_pre_rewrites_the_mode_is_dropped() -> None:
    pytest.skip(
        "pre row 4: there is no exit transition to rewrite -- exiting prerelease in molt is just "
        "running `molt version` without --pre (research README section 4.2)."
    )


def test_pre_json_row_5_exit_pre_without_pre_state_raises_is_dropped() -> None:
    pytest.skip(
        "pre row 5: PreExitButNotInPreModeError has no analogue; with no pre.json there is no "
        "state whose absence could be an error (research README section 4.2)."
    )


def test_pre_json_row_6_read_pre_state_and_its_v2_to_v3_migration_are_dropped() -> None:
    pytest.skip(
        "pre row 6: readPreState plus its silent v2->v3 initialVersions migration "
        "(migratePreState) are dropped -- molt has no on-disk prerelease state to read or "
        "migrate (research README section 4.2)."
    )


# --------------------------------------------------------------------------------------
# The 216-case dependent-bump matrix -- 120 dropped cells, recorded per dropped suite
# --------------------------------------------------------------------------------------


def test_matrix_72_peer_dependency_cells_are_dropped() -> None:
    pytest.skip(
        "matrix: 72 peer cells (12 per suite x 6 suites, 3 ranges x 4 bumps) are dropped with "
        "peerDependencies (research README section 4.4); in v3 peers share the dep expectation "
        "table exactly, so the 72 cells duplicate the 72 dep cells and no coverage is lost."
    )


def test_matrix_48_only_update_peer_dependents_config_cells_are_dropped() -> None:
    pytest.skip(
        "matrix: the 48 dep+dev cells of suites 41 and 42 (24 each) are dropped with the "
        "onlyUpdatePeerDependentsWhenOutOfRange option (research README section 4.4); suite 41 "
        "equals the default suite and suite 42 equals the 'always' suite because the flag gates "
        "range rewriting only, never the bump matrix -- that identity is the fact being recorded."
    )


# --------------------------------------------------------------------------------------
# Fixture-builder surface
# --------------------------------------------------------------------------------------


def test_fake_state_update_peer_dependency_is_dropped() -> None:
    pytest.skip(
        "fixture: FakeFullState.updatePeerDependency and the DepEntry kind 'peer' are dropped "
        "from tests/engine/fake_state.py (research README section 4.4, test-suite doc 02 "
        "'FakeFullState API' table); only 'direct' and 'dev' kinds exist in the Python builder."
    )
