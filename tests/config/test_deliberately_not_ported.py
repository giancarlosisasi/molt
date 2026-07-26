"""Config behaviors molt deliberately does NOT port - kept as a skipped audit record.

Source rows: ``roadmap/research/test-suite/03-config-changeset-io.md``,
``packages/config/src/parse.test.ts`` section, the four Drop rows 12, 13, 33 and 52 (its Totals
table: 57 cases = 6 Port / 47 Adapt / 4 Drop). The reference file is changesets v3.0.0-next.9
``packages/config/src/parse.test.ts``.

The four Drop *rows* only cover the two options that upstream happens to test. Dropping an option
also drops the surface around it, and some of that surface has no reference test at all, so the
tests below split into two blocks:

- ``test_row_*`` - one per Drop row in the group file (4).
- the rest - structural drops implied by those rows plus the config surface molt replaces. These
  are net-new records, not rows from the 57; they are counted separately in the phase report so
  the group file's "4 Drop" total still reconciles.

Every reason cites research README section 4.4 (the two deleted subsystems) or section 5.

Sibling records: the option-level view of the same decisions lives in the "Dropped from
changesets" table of ``website/docs/config/options.md``; the *runtime* tolerance for these keys
(accepted with a warning so an old ``config.json`` still loads) is asserted in
``tests/config/test_parse.py::test_unknown_and_dropped_keys_warn_but_do_not_fail``. Tolerated on
input is not the same as supported, which is why they are recorded here.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.not_ported


# --------------------------------------------------------------------------------------
# The four Drop rows of the group file
# --------------------------------------------------------------------------------------


def test_row_12_access_restricted_is_dropped() -> None:
    pytest.skip(
        "`access: restricted` is npm public/restricted scope; PyPI has no per-package access "
        "setting and no scopes (research README section 4.4). parse.test.ts:216-220."
    )


def test_row_13_access_public_is_dropped() -> None:
    pytest.skip(
        "`access: public` - same key, same reason; the whole `access` option is dropped rather "
        "than remapped, because repository selection is a publish-time flag in molt "
        "(research README section 4.4). parse.test.ts:221-225."
    )


def test_row_33_access_invalid_string_is_dropped() -> None:
    pytest.skip(
        "The `access` enum-rejection case goes with the option itself; there is no enum left to "
        "reject (research README section 4.4). parse.test.ts:417-422."
    )


def test_row_52_only_update_peer_dependents_when_out_of_range_is_dropped() -> None:
    pytest.skip(
        "`onlyUpdatePeerDependentsWhenOutOfRange` gates peerDependencies range rewriting; Python "
        "has no peer-dependency concept, so the option and half of determineDependents go with "
        "it (research README section 4.4). parse.test.ts:535-543."
    )


# --------------------------------------------------------------------------------------
# Structural drops implied by the rows above (no reference test pins these)
# --------------------------------------------------------------------------------------


def test_experimental_unsafe_options_wrapper_is_dropped() -> None:
    pytest.skip(
        "The `___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH` wrapper existed to hold the two "
        "options above; one is dropped and `updateInternalDependents` is promoted to a plain "
        "top-level option, so the wrapper is not a molt config surface - it is accepted only as "
        "a migration alias (research README section 4.4; group file row 28). config.ts:148-158."
    )


def test_private_packages_tag_subkey_is_dropped() -> None:
    pytest.skip(
        "`privatePackages.tag` controlled npm dist-tags; PyPI has no dist-tags, so there is "
        "nothing for it to do (research README section 4.4). config.ts:95-105."
    )


def test_no_private_tag_without_private_version_rule_is_dropped() -> None:
    pytest.skip(
        "The `noPrivateTagWithoutPrivateVersion` rule only exists to reject `tag:true` with "
        "`version:false`; with `tag` gone the rule has no inputs (research README section 4.4). "
        "rules.ts:174-182."
    )


def test_js_formatter_backend_matrix_is_dropped() -> None:
    pytest.skip(
        "`format`'s prettier/oxfmt/deno/dprint detection matrix is a JS-toolchain feature and "
        "would pull a Node toolchain into a Python release tool; molt emits correct, "
        "deterministic Markdown with no formatter pass (research README section 5, item 13). "
        "The `format` option itself is kept, but it defaults to false and its only live backend "
        "is mdformat (research doc 02 section 12.1); the JS values are accepted as inert "
        "migration aliases that warn and normalize to false. config.ts:62-73."
    )
