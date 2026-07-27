"""CLI behaviors molt deliberately does **not** port, recorded so every divergence stays auditable.

One skipping test per dropped behavior (``tests/README.md``, "Drops"), each carrying the reason
from its group-file Drop row plus the research citation. Greppable with ``-m not_ported``.

Coverage of this file
---------------------
- ``roadmap/research/test-suite/05-cli-version.md`` -- **21 Drop rows**: the 19-row
  ``describe("pre")`` block (rows 45-63) and the two ``peerDependencies`` rows (28, 29).
- ``roadmap/research/test-suite/06-cli-commands.md`` -- **7 Drop rows**: the five
  ``packages/cli/src/commands/pre/index.test.ts`` cases (``enterPre`` x3, ``exitPre`` x2) and the
  two ``cli.test.ts`` ``changeset pre`` routing cases (rows 11, 12).
- **Structural drops**: divergences this suite makes that are not a single upstream ``it`` -- the
  **six** upstream bugs of research doc 03 section 11.7 items 1-6 that molt fixes rather than
  reproduces (item 7, the ``--non-interactive`` flag, is an *addition* rather than a fix and is
  pinned as real behavior in ``tests/cli/test_add.py``), the asymmetric ``init`` re-run refusal of
  gotcha 10.16, plus the npm-only constructs (dist-tags, ``--allow-empty`` commits, the illegal
  snapshot version shape) and the JSON-generator trivia of gotcha 10.17, none of which have a
  Python meaning.

Every drop another module's docstring claims to have recorded must exist here; that cross-check is
what added the 10.16 / 10.17 / 10.19 records.

Three of the dropped ``pre`` rows (59, 60, 63) actually pin **non-pre** behavior. They are
**re-ported as real tests** in ``tests/cli/test_version.py``; their records below say which test
took over, so the audit trail does not read as though the behavior was lost.

Rows 45-58 pin prerelease *arithmetic* (counter increment, highest-bump retention across
prereleases, in-range dependency handling, star-range replacement). That arithmetic is still
required and is covered by ``tests/versioning/test_pre.py`` (net-new, PEP 440 identifiers) and by
the ``--pre`` tests in ``tests/cli/test_version.py``. What dies is the ``pre.json`` state machine
that upstream wraps around it, not the maths.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.not_ported


# ======================================================================================
# 05-cli-version.md -- describe("pre"), rows 45-63 (19 rows)
#
# molt replaces `pre enter`/`pre exit` + `.changeset/pre.json` with the stateless invocation flag
# `molt version --pre {a,b,rc,dev}` (research README section 4.2; website/docs/cli/pre.md).
# `1.0.1-next.0` is unrepresentable in PEP 440 -- only aN/bN/rcN/.devN are legal -- so the tag half
# of the design was a non-starter before any UX discussion; independently, removing pre.json is the
# community's #6 most-wanted change (research README section 5).
# ======================================================================================


def test_pre_row_45_full_enter_version_exit_lifecycle() -> None:
    pytest.skip(
        "version.test.ts:2374-2570 'pre > should work': enter -> 1.0.1-next.0/.1/.2 -> "
        "1.1.0-next.3 -> exit -> 1.1.0, with the changelog accreting every -next.N header. The "
        "lifecycle is gone with pre.json (research README section 4.2); the counter arithmetic "
        "survives as "
        "`molt version --pre rc` run repeatedly -- tests/versioning/test_pre.py and "
        "tests/cli/test_version.py::test_the_pre_counter_advances_and_a_plain_run_finalizes."
    )


def test_pre_row_46_adding_a_package_while_in_pre_mode() -> None:
    pytest.skip(
        "version.test.ts:2572-2658 'should work with adding a package while in pre mode': a "
        "package added mid-pre starts its own counter at 0. There is no 'mid-pre' in molt -- each "
        "--pre run reads the counter from the version on disk, so a package at a stable version "
        "starts at 0 "
        "by construction (website/docs/concepts/prerelease.md, 'How the counter moves')."
    )


def test_pre_row_47_prerelease_off_a_just_released_stable_base() -> None:
    pytest.skip(
        "version.test.ts:2660-2725 'should work for my weird case': normal minor to 1.1.0, then "
        "enter pre + patch to 1.1.1-next.0. Stateless in molt: `molt version` then "
        "`molt version --pre rc` needs no transition (research README section 4.2)."
    )


def test_pre_row_48_caret_dependency_still_in_range_during_pre() -> None:
    pytest.skip(
        "version.test.ts:2727-2790 'should bump patch version for packages that had prereleases, "
        "but caret dependencies are still in range'. The pre.json exit collapse is gone; PEP 440 "
        "scopes prerelease opt-in to the whole specifier set rather than the release tuple, which "
        "is a deliberate semantic divergence (research README section 4.1) pinned by "
        "tests/versioning/test_ranges.py."
    )


def test_pre_row_49_highest_bump_type_across_prereleases() -> None:
    pytest.skip(
        "version.test.ts:2792-2857 'should use the highest bump type between all prereleases when "
        "versioning a package': major then minor stays 2.0.0-next.N. Behavior retained without "
        "pre.json -- the changesets stay on disk across --pre runs, so the highest bump is "
        "recomputed from all of them every time (website/docs/concepts/prerelease.md:46); pinned "
        "by tests/versioning/test_pre.py."
    )


def test_pre_row_50_highest_bump_type_for_a_dependent_across_prereleases() -> None:
    pytest.skip(
        "version.test.ts:2859-2926 'should use the highest bump type between all prereleases when "
        "versioning a dependent package'. Same as row 49, one hop down the dependency graph; "
        "covered by tests/engine/test_assemble.py plus tests/versioning/test_pre.py."
    )


def test_pre_row_51_dev_dependencies_do_not_propagate_a_bump_in_pre() -> None:
    pytest.skip(
        "version.test.ts:2928-2971 'should not bump packages through devDependencies'. The rule is "
        "not prerelease-specific and is ported outside pre by "
        "tests/cli/test_version.py::test_a_dev_dependent_is_pin_rewritten_but_never_bumped_and_"
        "propagation_stops (determine-dependents.ts:108-117)."
    )


def test_pre_row_52_ignore_beats_prerelease_propagation() -> None:
    pytest.skip(
        "version.test.ts:2973-3027 'should not bump ignored packages through dependencies'. Not "
        "prerelease-specific; ported outside pre by tests/cli/test_version.py::"
        "test_an_ignored_package_is_frozen_but_its_pin_on_a_released_dependency_still_moves."
    )


def test_pre_row_53_workspace_tilde_dependent_under_prerelease() -> None:
    pytest.skip(
        "version.test.ts:3029-3075 'should bump dependent of prerelease package when bumping a "
        "`workspace:~` dependency'. Doubly adapted away: pre.json is gone and `workspace:~` has no "
        "PEP 508 spelling (it becomes `~=<old>` plus a [tool.uv.sources] marker -- research doc 04 "
        "section 2.6). The non-pre form is tests/cli/test_version.py::"
        "test_an_out_of_range_minor_patch_bumps_a_tilde_style_dependent."
    )


def test_pre_row_54_star_range_replaced_on_first_prerelease() -> None:
    pytest.skip(
        "version.test.ts:3077-3154 'should replace star range for dependency in dependant package "
        "when that dependency has its first prerelease in an already active pre mode'. Python has "
        "no `*` range; the analogue is an unconstrained requirement, whose prerelease exception "
        "(pin it exactly, or the tree is uninstallable) is pinned by tests/apply/test_apply.py::"
        "test_an_unconstrained_dependency_is_pinned_when_the_new_version_is_a_prerelease."
    )


def test_pre_row_55_star_range_becomes_an_exact_prerelease_pin() -> None:
    pytest.skip(
        "version.test.ts:3156-3206 'bumping dependency in pre mode should result in dependant with "
        "star range ... replaced with exact version'. Same substitution as row 54; the surviving "
        "rule lives in tests/apply/test_apply.py."
    )


def test_pre_row_56_workspace_star_survives_a_prerelease_untouched() -> None:
    pytest.skip(
        "version.test.ts:3208-3258 'bumping dependency in pre mode ... workspace:* ... without "
        "changing the dependency range'. pre.json is gone and `workspace:*` becomes a bare "
        "requirement plus a uv workspace source; the non-pre form is tests/cli/test_version.py::"
        "test_a_bare_workspace_source_patch_bumps_its_dependent_without_changing_the_pin."
    )


def test_pre_row_57_workspace_caret_survives_a_prerelease_untouched() -> None:
    pytest.skip(
        "version.test.ts:3260-3310 'bumping dependency in pre mode ... workspace:^ ... without "
        "changing the dependency range'. As row 56, with the caret marker."
    )


def test_pre_row_58_workspace_tilde_survives_a_prerelease_untouched() -> None:
    pytest.skip(
        "version.test.ts:3312-3362 'bumping dependency in pre mode ... workspace:~ ... without "
        "changing the dependency range'. As row 56, with the tilde marker."
    )


def test_pre_row_59_version_less_private_package_skipped_cleanly() -> None:
    pytest.skip(
        "version.test.ts:3364-3403 'should version successfully when skipping a private package "
        "without a version field'. The pre wrapper is dropped, but the behavior is NOT: re-ported "
        "as tests/cli/test_version.py::"
        "test_a_private_package_without_a_version_field_is_skipped_cleanly."
    )


def test_pre_row_60_private_package_versioned_but_not_tagged() -> None:
    pytest.skip(
        "version.test.ts:3405-3443 'should version successfully a private package when tagging for "
        "them is disabled' (privatePackages: {tag: false, version: true}). The `tag` sub-key "
        "itself is dropped -- PyPI has no dist-tags (website/docs/config/options.md; recorded in "
        "tests/config/test_deliberately_not_ported.py) -- so the surviving rule is stronger: "
        "`molt version` never tags at all. Re-ported as tests/cli/test_version.py::"
        "test_version_never_creates_a_git_tag."
    )


def test_pre_row_61_linked_group_convergence_under_prerelease() -> None:
    pytest.skip(
        "version.test.ts:3446-3554 'pre > linked > should work with linked': normal -> pre -> "
        "multiple, converging on 1.1.1-next.0/.1/.2. Linked convergence itself is ported by "
        "tests/cli/test_version.py::test_linked_group_members_converge_to_the_group_maximum; only "
        "the pre.json lifecycle around it is dropped."
    )


def test_pre_row_62_linked_group_max_under_prerelease_across_dependents() -> None:
    pytest.skip(
        "version.test.ts:3556-3635 'pre > linked > should use the highest bump type between all "
        "prereleases for a linked package when versioning a dependent package'. Linked-group max "
        "across dependents is pinned by tests/engine/test_assemble.py; the prerelease wrapper dies "
        "with pre.json."
    )


def test_pre_row_63_linked_devdep_release_does_not_cobump() -> None:
    pytest.skip(
        "version.test.ts:3637-3684 'pre > linked > should not bump a linked package if its linked "
        "devDep gets released'. Despite the `pre > linked` nesting this case never enters pre "
        "mode, so it is NOT dropped: re-ported as tests/cli/test_version.py::"
        "test_a_linked_member_is_not_cobumped_by_a_dev_dependency_release."
    )


# ======================================================================================
# 05-cli-version.md -- peerDependencies, rows 28 and 29 (2 rows)
#
# Python has no peer dependencies (research README section 4.4). The concept, the
# `onlyUpdatePeerDependentsWhenOutOfRange` knob and the `___experimentalUnsafeOptions` wrapper that
# carries it all disappear together; the engine-side drops are already recorded in
# tests/engine/test_deliberately_not_ported.py and tests/apply/test_deliberately_not_ported.py.
# ======================================================================================


def test_version_row_28_peer_dependent_patch_bumped_on_a_minor() -> None:
    pytest.skip(
        "version.test.ts:1390-1441 'should patch bump peer-dependent when workspace:~ dependency "
        "gets a minor bump (without onlyUpdatePeerDependentsWhenOutOfRange)'. peerDependencies do "
        "not exist in Python (research README section 4.4), so the default 'peers always bump' "
        "rule has nothing to apply to."
    )


def test_version_row_29_peer_dependent_not_bumped_when_in_range() -> None:
    pytest.skip(
        "version.test.ts:1443-1492 'should not bump peer-dependent when workspace:~ dependency "
        "gets a minor bump (with onlyUpdatePeerDependentsWhenOutOfRange)'. Drops with the config "
        "knob itself (research README section 4.4); v3 had already reduced it to gating range "
        "rewriting only (research README section 3.2)."
    )


# ======================================================================================
# 06-cli-commands.md -- packages/cli/src/commands/pre/index.test.ts, rows 1-5 (5 rows)
#
# The whole `pre` command is removed. `molt pre ...` still exists as a signpost that exits non-zero
# with migration guidance naming `molt version --pre {a,b,rc,dev}` (cli-shell spec registers `pre`
# in the command table; website/docs/cli/pre.md documents that there is nothing to enter or exit).
# The signpost's behavior is owned by tests/cli/test_cli.py, not by this file.
# ======================================================================================


def test_pre_command_row_1_enter_writes_pre_json() -> None:
    pytest.skip(
        "pre/index.test.ts 'enterPre > should enter': writes .changeset/pre.json "
        '{changesets: [], mode: "pre", tag: "next"} and logs "Entered pre mode with tag". molt '
        "writes no state file at all (research README section 4.2); the equivalent is a single "
        "`molt version --pre rc` run."
    )


def test_pre_command_row_2_enter_throws_when_already_in_pre() -> None:
    pytest.skip(
        "pre/index.test.ts 'enterPre > should throw if already in pre': ExitError naming "
        "`changeset pre exit`. The global-mode invariant vanishes with pre.json -- there is no "
        "state for a second invocation to conflict with."
    )


def test_pre_command_row_3_enter_after_exit() -> None:
    pytest.skip(
        "pre/index.test.ts 'enterPre > should enter if already exited pre mode': mode exit -> pre. "
        "A state transition on a state machine molt does not have."
    )


def test_pre_command_row_4_exit_rewrites_pre_json() -> None:
    pytest.skip(
        "pre/index.test.ts 'exitPre > should exit': pre.json mode -> 'exit', tag retained. molt "
        "has no exit step -- a plain `molt version` finalizes to the stable version "
        "(website/docs/cli/pre.md, 'Migrating from changeset pre enter / pre exit')."
    )


def test_pre_command_row_5_exit_throws_when_not_in_pre() -> None:
    pytest.skip(
        "pre/index.test.ts 'exitPre > should throw if not in pre': ExitError naming "
        "`changeset pre enter`. No mode implies no error condition; the corresponding molt errors "
        "PreEnterButInPreModeError / PreExitButNotInPreModeError are kept only as ported hierarchy "
        "members (tests/test_errors.py) and are never raised."
    )


# ======================================================================================
# 06-cli-commands.md -- cli.test.ts pre routing, rows 11 and 12 (2 rows)
# ======================================================================================


def test_cli_row_11_routes_pre_enter_beta() -> None:
    pytest.skip(
        "cli.test.ts 'changeset pre > `enter beta`' -> pre({command: 'enter', tag: 'beta'}). molt "
        "registers `pre` as a signpost that exits non-zero pointing at "
        "`molt version --pre {a,b,rc,dev}`; `beta` is not even a legal PEP 440 identifier (`b` is)."
    )


def test_cli_row_12_routes_pre_exit() -> None:
    pytest.skip(
        "cli.test.ts 'changeset pre > `exit`' -> pre({command: 'exit'}). Dropped with the command; "
        "the sum-typed `pre <enter|exit>` argument that motivated cyclopts in research doc 03 "
        "section 11.1 disappears with it."
    )


# ======================================================================================
# Structural drops -- upstream bugs molt FIXES rather than ports
# (research doc 03 section 11.7; research README section 3.4)
#
# Each of these needs owner sign-off. The corrected behavior is pinned by a real test; the record
# below preserves the upstream spelling so the decision stays reviewable.
# ======================================================================================


def test_upstream_editor_summary_strips_markdown_headings() -> None:
    pytest.skip(
        "doc 03 section 10.6 / 11.7 item 1 (askWithEditor.ts:31): the editor summary "
        "post-processor strips every line beginning with '#', destroying Markdown headings in a "
        "changeset description. molt does not strip; the corrected behavior belongs to `molt add` "
        "(tests/cli/test_add.py). Recorded here because it is a deliberate divergence, not an "
        "oversight."
    )


def test_upstream_first_major_decline_differs_by_repo_shape() -> None:
    pytest.skip(
        "doc 03 section 10.8 / 11.7 item 2 (createChangeset.ts:203-209 vs :264-269): declining the "
        "first-major confirmation falls through to the minor prompt in a monorepo but aborts with "
        "exit 1 in a single-package repo. molt behaves the same in both shapes (falls through); "
        "pinned by tests/cli/test_add.py."
    )


def test_upstream_release_type_flags_bypass_the_first_major_guard() -> None:
    pytest.skip(
        "doc 03 section 10.7 / 11.7 item 3 (createChangeset.ts:148-170 never calls "
        "confirmMajorRelease): `--major` skips the first-major confirmation entirely. molt applies "
        "the guard to flag-selected majors too, and `--non-interactive` proceeds without "
        "prompting; pinned by tests/cli/test_add.py."
    )


def test_upstream_status_output_is_not_written_when_the_ci_gate_trips() -> None:
    pytest.skip(
        "doc 03 section 10.11 / 11.7 item 4 (status/index.ts:43-59): `status --output` is skipped "
        "when the CI gate exits 1, so the machine-readable artifact is missing exactly when CI "
        "needs it. molt writes the output first, then trips the gate; pinned by "
        "tests/cli/test_status.py."
    )


def test_upstream_failed_version_commit_exits_zero() -> None:
    pytest.skip(
        "doc 03 section 10.10 / 11.7 item 5 (version/index.ts:145-147): a failed `git commit` is "
        "logged as 'Changesets ran into trouble committing your files' and the process still exits "
        "0 -- a silent CI failure. molt exits non-zero; pinned by "
        "tests/cli/test_version.py::test_a_failed_commit_is_fatal."
    )


def test_upstream_remote_tag_lookup_is_one_ls_remote_per_package() -> None:
    pytest.skip(
        "doc 03 section 10.19 / 11.7 item 6 (packages/git/src/index.ts:328-339, called from "
        "utils/getUntaggedPackages.ts:18-19): `getUntaggedPackages` asks `tagExists` AND "
        "`remoteTagExists` once per package, and `remoteTagExists` shells out to "
        "`git ls-remote --tags origin` every time -- an unbatched network round-trip per package. "
        "molt reads the tag set once and answers every question from it; pinned by "
        "tests/cli/test_git_tag.py::test_existing_tag_lookup_is_batched."
    )


def test_upstream_init_refuses_to_rerun_yet_still_creates_a_missing_readme() -> None:
    pytest.skip(
        "doc 03 section 10.16 (init/index.ts:95-122): `init` refuses to re-run once config.json "
        "exists, yet happily creates README.md when only that file is missing -- so an existing "
        "config permanently suppresses the README the same command promises to write. molt's "
        "contract is symmetric, 'creates only the pieces that are missing' "
        "(website/docs/cli/init.md); pinned by tests/cli/test_init.py. The asymmetric refusal is "
        "the dropped behavior."
    )


# ======================================================================================
# Structural drops -- npm-only constructs with no Python meaning
# ======================================================================================


def test_upstream_version_commit_uses_allow_empty() -> None:
    pytest.skip(
        "doc 03 section 10.18 (packages/git/src/index.ts:21): every changesets commit passes "
        "--allow-empty, so a `version` run that changed nothing still creates a commit. molt does "
        "not; pinned by "
        "tests/cli/test_version.py::test_the_version_commit_does_not_use_allow_empty."
    )


def test_upstream_snapshot_version_shape_is_illegal_in_pep440() -> None:
    pytest.skip(
        "doc 03 section 3.4 / version.test.ts:1832-1908: upstream composes "
        "`0.0.0-<tag>-<datetime>` and templates it freely. PEP 440 has no free-form field in the "
        "public part of a version, so molt composes "
        "`0.0.0.dev<14-digit-datetime>[+<local>]` instead (harness Session 2 "
        "decision 3, closing research open decision #2; website/docs/concepts/snapshots.md). The "
        "9-row template matrix is re-derived, not ported."
    )


def test_upstream_snapshot_prerelease_template_is_a_cli_flag() -> None:
    pytest.skip(
        "version/index.ts:24,51-53 exposes `--snapshot-prerelease-template` as an invocation "
        "option. website/docs/cli/version.md's option table does not list it, so molt keeps the "
        "template as configuration only (`[tool.molt.snapshot] prerelease_template`); pinned "
        "through the config in tests/cli/test_version.py::test_the_snapshot_template_matrix."
    )


def test_upstream_npm_dist_tag_ranges() -> None:
    pytest.skip(
        "version.test.ts:549-596, :2225-2291, :2293-2371 use an npm dist-tag (`latest`, "
        "`bulbasaur`) as a dependency range. PyPI has no dist-tags (research README section 4.4), "
        "so the rows are reframed around a PEP 508 direct reference -- a specifier molt cannot "
        "resolve to a local package -- in tests/cli/test_version.py::"
        "test_a_direct_reference_dependency_never_makes_its_holder_a_dependent."
    )


def test_upstream_version_never_updates_a_lockfile() -> None:
    pytest.skip(
        "doc 03 section 3.2 (boxed note): there is no install/lock step anywhere in the v3 CLI, so "
        "`changeset version` leaves the lockfile stale. Cosmetic in npm, load-bearing in Python "
        "(research README section 4.5), so molt ADDS the step rather than porting its absence; "
        "pinned by tests/cli/test_version.py::test_the_lockfile_is_refreshed."
    )


def test_upstream_init_writes_a_comment_free_json_config() -> None:
    pytest.skip(
        "doc 03 section 10.17 (generated config, section 7.3): the docs promise a commented "
        "config file while `init` emits comment-free JSON -- JSON cannot carry comments at all. "
        "Nothing to port and nothing to fix: molt's default target is a `[tool.molt]` table in "
        "pyproject.toml, and TOML takes comments (website/docs/cli/init.md, "
        "website/docs/config/config-file.md). No molt doc promises comments in the generated "
        "config either, so molt is free to write it either way; tests/cli/test_init.py asserts "
        "the keys, not the trivia."
    )


def test_upstream_pre_mode_note_and_snapshot_conflict_message() -> None:
    pytest.skip(
        "version/index.ts:72-90: the `IMPORTANT` 'You are in prerelease mode!' note and the "
        "'Snapshot release is not allowed in pre mode. To resolve this exit the pre mode ...' "
        "error are both state checks against pre.json. molt's equivalent is an argument check "
        "(tests/cli/test_version.py::test_pre_and_snapshot_cannot_be_combined); there is no mode "
        "to warn about."
    )
