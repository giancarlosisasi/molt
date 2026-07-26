"""Conformance tests for PEP 440 bump arithmetic.

Fixtures ported from ``roadmap/research/changesets-01-core-versioning-engine.md`` §3.2/§3.3,
which were in turn extracted from changesets' own test suite.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from packaging.version import Version

from molt.versioning import BumpType, highest, inc, next_pre_number


def v(text: str) -> Version:
    return Version(text)


# --------------------------------------------------------------------------------------
# §3.3 — node-semver `inc` prerelease semantics. This is THE table.
# --------------------------------------------------------------------------------------

# Each row is the semver fixture with its prerelease respelled in PEP 440
# (`-next.N` is not expressible in PEP 440 — see research README §4.2 — so `rcN` stands in;
# the arithmetic under test is identical).
INC_CASES = [
    # (old,           bump,   expected,  why)
    ("1.0.0", BumpType.PATCH, "1.0.1", "normal patch"),
    ("1.0.1rc2", BumpType.PATCH, "1.0.1", "already a pre-patch -> strip prerelease only"),
    ("1.1.0rc3", BumpType.MINOR, "1.1.0", "patch==0 and prerelease -> minor stays"),
    ("2.0.0rc0", BumpType.MAJOR, "2.0.0", "minor==0, patch==0, prerelease -> major stays"),
    ("2.0.0rc0", BumpType.MINOR, "2.0.0", "patch==0 and prerelease -> minor stays"),
    ("1.0.1rc0", BumpType.MINOR, "1.1.0", "patch != 0 -> minor increments"),
    ("1.1.0rc0", BumpType.PATCH, "1.1.0", "prerelease present -> patch stays"),
]


@pytest.mark.parametrize(("old", "bump", "expected", "why"), INC_CASES)
def test_inc_prerelease_semantics(old: str, bump: BumpType, expected: str, why: str) -> None:
    assert inc(v(old), bump) == v(expected), why


def test_exiting_prerelease_mode_lands_on_the_stable_version() -> None:
    """The property that makes prerelease exit work at all (research doc 01 §3.3)."""
    assert inc(v("1.1.0rc3"), BumpType.MINOR) == v("1.1.0")


# --------------------------------------------------------------------------------------
# §3.2 — there is NO 0.x special-casing
# --------------------------------------------------------------------------------------

ZERO_X_CASES = [
    ("0.1.0", BumpType.MAJOR, "1.0.0"),
    ("0.1.0", BumpType.MINOR, "0.2.0"),
    ("0.1.0", BumpType.PATCH, "0.1.1"),
    ("0.0.1", BumpType.MAJOR, "1.0.0"),
    ("0.0.0", BumpType.MINOR, "0.1.0"),
]


@pytest.mark.parametrize(("old", "bump", "expected"), ZERO_X_CASES)
def test_no_zero_major_special_case(old: str, bump: BumpType, expected: str) -> None:
    """`major` on a 0.x version gives 1.0.0. The 0.x asymmetry is a *range* concern."""
    assert inc(v(old), bump) == v(expected)


# --------------------------------------------------------------------------------------
# PEP 440 specifics with no semver counterpart
# --------------------------------------------------------------------------------------


def test_epoch_is_preserved() -> None:
    """Epochs dominate comparison, so losing one silently reorders every release."""
    assert inc(v("1!2.0.0"), BumpType.MINOR) == v("1!2.1.0")


def test_post_release_is_cleared() -> None:
    """A post-release is not a prerelease, so patch increments and `.postN` is dropped."""
    assert inc(v("1.0.0.post1"), BumpType.PATCH) == v("1.0.1")


def test_local_version_is_cleared() -> None:
    assert inc(v("1.0.0+local.1"), BumpType.PATCH) == v("1.0.1")
    assert inc(v("1.0.0+local.1"), BumpType.PATCH).local is None


def test_dev_release_counts_as_a_prerelease() -> None:
    """`.devN` satisfies node-semver's `prerelease.length !== 0`, so patch does not bump."""
    assert inc(v("1.0.1.dev3"), BumpType.PATCH) == v("1.0.1")


def test_short_release_segments_are_padded() -> None:
    assert inc(v("1"), BumpType.PATCH) == v("1.0.1")
    assert inc(v("1.2"), BumpType.MINOR) == v("1.3.0")


def test_overlong_release_segment_is_rejected_loudly() -> None:
    """Bump semantics are undefined for 4+ components; fail rather than truncate."""
    with pytest.raises(ValueError, match="release components"):
        inc(v("1.2.3.4"), BumpType.PATCH)


def test_none_bump_is_identity() -> None:
    assert inc(v("1.2.3"), BumpType.NONE) == v("1.2.3")


# --------------------------------------------------------------------------------------
# §3.1 — bump precedence
# --------------------------------------------------------------------------------------


def test_bump_ordering() -> None:
    assert BumpType.NONE.rank < BumpType.PATCH.rank < BumpType.MINOR.rank < BumpType.MAJOR.rank


@pytest.mark.parametrize(
    ("bumps", "expected"),
    [
        ([], BumpType.NONE),
        ([BumpType.NONE], BumpType.NONE),
        ([BumpType.PATCH, BumpType.MINOR], BumpType.MINOR),
        ([BumpType.MAJOR, BumpType.PATCH], BumpType.MAJOR),
        ([BumpType.NONE, BumpType.PATCH, BumpType.NONE], BumpType.PATCH),
    ],
)
def test_highest(bumps: list[BumpType], expected: BumpType) -> None:
    assert highest(bumps) == expected


def test_none_never_overrides_a_real_bump() -> None:
    """Asserted upstream at index.test.ts:115-161."""
    assert highest([BumpType.NONE, BumpType.MINOR, BumpType.NONE]) == BumpType.MINOR


def test_highest_of_empty_returns_none_rather_than_raising() -> None:
    """Upstream throws here, but both call sites guard it (research doc 01 §3.1)."""
    assert highest([]) == BumpType.NONE


# --------------------------------------------------------------------------------------
# §15.3 — the prerelease counter, where 0 is a load-bearing sentinel
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.0.0", 0),  # sentinel: not a prerelease
        ("1.0.0rc0", 1),
        ("1.0.0rc4", 5),
        ("1.0.0a0", 1),
        ("1.0.0b2", 3),
        ("1.0.0.dev0", 1),
        ("1.0.0.dev7", 8),
        ("1.0.0.post1", 0),  # post-releases are not prereleases
    ],
)
def test_next_pre_number(version: str, expected: int) -> None:
    assert next_pre_number(v(version)) == expected


def test_dev_segment_wins_over_pre_segment() -> None:
    """`1.0.0rc1.dev2` carries both; the dev counter is the more specific one."""
    assert next_pre_number(v("1.0.0rc1.dev2")) == 3


# --------------------------------------------------------------------------------------
# Property tests — the invariants the fixpoint loop's termination depends on
# --------------------------------------------------------------------------------------

versions = st.builds(
    lambda a, b, c: Version(f"{a}.{b}.{c}"),
    st.integers(0, 500),
    st.integers(0, 500),
    st.integers(0, 500),
)
real_bumps = st.sampled_from([BumpType.PATCH, BumpType.MINOR, BumpType.MAJOR])


@given(version=versions, bump=real_bumps)
def test_inc_is_strictly_increasing_for_stable_versions(version: Version, bump: BumpType) -> None:
    """Monotonicity is the *only* termination guarantee the fixpoint loop has.

    See research doc 01 §2.4 — if a bump could ever lower a version, the engine would not
    converge.
    """
    assert inc(version, bump) > version


@given(version=versions, bump=real_bumps)
def test_inc_never_produces_a_prerelease(version: Version, bump: BumpType) -> None:
    assert not inc(version, bump).is_prerelease


@given(version=versions)
def test_bump_magnitude_is_ordered(version: Version) -> None:
    """A bigger bump type always yields a bigger version."""
    assert (
        inc(version, BumpType.PATCH) <= inc(version, BumpType.MINOR) <= inc(version, BumpType.MAJOR)
    )
