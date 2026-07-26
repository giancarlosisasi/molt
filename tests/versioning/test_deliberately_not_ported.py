"""Group 1 (versioning core) behaviors molt deliberately does NOT port.

Audit record, not coverage. One skipping test per dropped upstream behavior, each citing the
research row it comes from, so every divergence stays greppable (`uv run pytest -m not_ported`).

Source: `roadmap/research/test-suite/01-versioning-core.md`, section
`packages/get-version-range-type/src/index.test.ts` (6 rows, all Drop).

`getVersionRangeType` (get-version-range-type/src/index.ts:1-10) is pure prefix-sniffing: it
returns only the *leading* operator of a range and has no `<` case at all, which is the exact
mechanism by which changesets silently widens constraints (`>=1.0.0 <2.0.0` flattens to
`>=1.0.4`). molt does not port the function: range rewriting operates on the whole
`packaging.SpecifierSet` and preserves every comparator. See research README section 3.3.

The one Port row in this group (increment.test.ts row 1, the `none` short-circuit) is already
covered by `tests/versioning/test_bump.py::test_none_bump_is_identity`; its pre-mode wrapper
assertion lands with the engine's prerelease composition step.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.not_ported


def test_get_version_range_type_caret_is_dropped() -> None:
    pytest.skip(
        "getVersionRangeType('^1.0.0') -> '^' is prefix extraction; PEP 440 has no caret, and "
        "molt.versioning.ranges.caret expands to '>=1.0.0,<2.0.0' instead (group 1 row 1; "
        "index.ts:4-5)."
    )


def test_get_version_range_type_tilde_is_dropped() -> None:
    pytest.skip(
        "getVersionRangeType('~1.0.0') -> '~' is prefix extraction; molt.versioning.ranges.tilde "
        "expands to '>=1.0.0,<1.1.0' instead (group 1 row 2; index.ts:6)."
    )


def test_get_version_range_type_gte_is_dropped() -> None:
    pytest.skip(
        "getVersionRangeType('>=1.0.0') -> '>=': a SpecifierSet already carries its operator, so "
        "molt never strips it to reprepend it (group 1 row 3; index.ts:6)."
    )


def test_get_version_range_type_lte_is_dropped() -> None:
    pytest.skip(
        "getVersionRangeType('<=1.0.0') -> '<=': PEP 440 preserves '<=' natively (group 1 row 4; "
        "index.ts:7)."
    )


def test_get_version_range_type_gt_is_dropped() -> None:
    pytest.skip(
        "getVersionRangeType('>1.0.0') -> '>': PEP 440 preserves '>' natively (group 1 row 5; "
        "index.ts:8)."
    )


def test_get_version_range_type_bare_version_is_dropped() -> None:
    pytest.skip(
        "getVersionRangeType('1.0.0') -> '' (no operator): a bare version is not a valid PEP 440 "
        "specifier, so the 'no operator' concept does not map. This same return-'' fallthrough is "
        "where the missing '<' case -- the constraint-widening bug -- lives; molt does not "
        "reproduce it (group 1 row 6; index.ts:9; research README section 3.3)."
    )
