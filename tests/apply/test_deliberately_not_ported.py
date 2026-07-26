"""Group 4 (``apply-release-plan``) behaviors molt deliberately does NOT port.

Audit record, not coverage. One skipping test per dropped upstream behavior, each citing the row it
comes from, so every divergence stays greppable (``uv run pytest -m not_ported``).

Source: ``roadmap/research/test-suite/04-apply-changelog.md``, section
``packages/apply-release-plan/src/index.test.ts`` -- 45 rows, of which **2 are marked Drop**
(rows 26 and 37). The remaining records below are **structural**: behavior that has no row of its
own because upstream never had a separate test for it, but that ``tests/apply/test_apply.py``
deliberately does not reproduce. Each names the upstream source and the research section that
authorises the omission.

The two upstream tests that pin *broken* blank lines (rows 29 and 32) are **not** drops -- they are
adapted to molt's corrected clean-markdown output and asserted in ``test_apply.py``
(research README section 3.4 / section 5 item 13).

The ``edit-json.test.ts`` and ``get-changelog-entry.test.ts`` rows of group 4 belong to
``tests/apply/test_edit_toml.py`` and ``tests/apply/test_changelog_entry.py``; the
``changelog-github`` and ``render-template`` rows to ``tests/changelog/``. Drops for those files
are recorded by their own authors, not here.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.not_ported


# --------------------------------------------------------------------------------------
# The two Drop rows of index.test.ts
# --------------------------------------------------------------------------------------


def test_peer_dependency_in_range_gate_is_dropped() -> None:
    """Group 4 index row 26 (Drop)."""
    pytest.skip(
        "onlyUpdatePeerDependentsWhenOutOfRange gates whether an in-range peerDependencies pin is "
        "rewritten (utils.ts:77-79; index.test.ts:1991-2090). Python has no peerDependencies, so "
        "the option, the ___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH wrapper it lived in, "
        "and the whole dep-type branch go with it (group 4 index row 26; research README section "
        "4.4; website/docs/config/options.md, 'Dropped from changesets')."
    )


def test_duplicate_package_in_a_release_plan_is_not_ported_as_written() -> None:
    """Group 4 index row 37 (Drop) -- upstream ``it.todo``, never implemented."""
    pytest.skip(
        "'should error and not write if a package appears twice' is an it.todo at "
        "index.test.ts:2914, with the comment 'for now we are assuming we have been passed valid "
        "releasePlans'. There is no upstream behavior to port. RECOMMENDATION for the owner: molt "
        "SHOULD add this as a validation test once molt.apply's validate phase exists -- a "
        "duplicate release is exactly the class of bug buffer-then-flush is meant to catch before "
        "the first write (research README section 5 item 12), and it is cheap next to the "
        "'Could not find matching package' check apply already performs (index.ts:92-102, ported "
        "as test_a_release_for_a_missing_package_fails_before_any_write)."
    )


# --------------------------------------------------------------------------------------
# Structural drops -- no upstream row, but deliberately not reproduced
# --------------------------------------------------------------------------------------


def test_pre_json_write_is_dropped() -> None:
    """Group 4 index row 45 (Port upstream) -- the ``pre.json`` half drops, the rest is adapted."""
    pytest.skip(
        "index.ts:113-126 writes (or deletes) .changeset/pre.json as apply's first write, and "
        "index.test.ts:3340-3388 asserts git status shows it modified. molt has no pre.json at "
        "all: prerelease is an invocation flag (molt version --pre rc), not persistent branch "
        "state, because -next.N tags are unrepresentable in PEP 440 and pre.json is the "
        "community's #6 most-wanted removal (research README section 4.2). The half of row 45 "
        "that is about "
        "apply -- the bump is still applied and changesets are not consumed -- is ported as "
        "test_a_pre_release_applies_the_bump_and_keeps_the_changesets."
    )


def test_workspace_caret_and_tilde_aliases_are_dropped() -> None:
    """Group 4 index row 8 (Adapt) -- two of its three alias forms have no Python spelling."""
    pytest.skip(
        "version-package.ts:83-90 skips workspace:*, workspace:^ and workspace:~ alike. In uv the "
        "constraint lives in the PEP 508 string and the source marker carries no version, so "
        "there is exactly one such spelling -- a workspace source with no specifier, the analogue "
        "of workspace:* (research doc 04 section 2.6). workspace:^ / workspace:~ mean 'resolve a "
        "caret or tilde against the current version', and PEP 440 has no such token to resolve "
        "(group 1 rows 1-2 are already dropped for the same reason). Ported as "
        "test_a_workspace_source_without_a_constraint_is_not_rewritten."
    )


def test_javascript_formatter_detection_is_dropped() -> None:
    """``index.ts:42-61`` -- the ``format: "auto"`` detection matrix."""
    pytest.skip(
        "getFormatter detects prettier/oxfmt/deno/dprint (excluding biome, which cannot format "
        "markdown) and runs it over the written CHANGELOG.md files. molt's format option defaults "
        "to false and never pulls in a Node toolchain: it emits correct Markdown on the first pass "
        "instead of repairing it afterwards (research README section 5 item 13; "
        "website/docs/config/options.md; harness progress log Session 2 decision 1). The JS values "
        "are accepted as inert migration aliases by molt.config, which is where that is tested."
    )


def test_import_meta_resolve_changelog_loading_is_dropped() -> None:
    """``index.ts:28-30, 259-281`` -- Node module resolution for the changelog plugin."""
    pytest.skip(
        "applyReleasePlan resolves config.changelog[0] with import-meta-resolve, first relative to "
        ".changeset/ and then relative to a contextDir argument, then dynamic-imports it and "
        "unwraps up to two levels of CJS/ESM default interop. molt resolves generators as Python "
        "entry points in the molt.changelog group, with a dotted path and a file path as fallbacks "
        "(website/docs/extending/changelog-plugins.md, 'Choosing a generator'). The contextDir "
        "parameter and the interop unwrapping have no Python analogue; the error path they guard "
        "('Could not resolve changelog generation functions', index.ts:280) belongs to "
        "molt.changelog's resolver tests, not to apply's."
    )


def test_leading_operator_range_flattening_is_dropped_here_too() -> None:
    """Cross-reference: the 6 ``get-version-range-type`` drops bind ``apply`` as well."""
    pytest.skip(
        "getVersionRangeType (version-package.ts:116-125) reduces a range to its leading operator, "
        "which is how '>=1.0.0 <2.0.0' flattens to '>=1.0.4' and silently widens the constraint. "
        "The six rows are already recorded in tests/versioning/test_deliberately_not_ported.py; "
        "this record exists so the drop is greppable from the apply side too, because apply is "
        "where the widening would actually be written to disk. molt splices the whole SpecifierSet "
        "(research README section 3.3); pinned by "
        "test_upper_bound_is_preserved_when_the_lower_bound_moves."
    )
