"""Conformance tests for range matching.

These assert **PEP 440 semantics**, which is a deliberate divergence from changesets'
node-semver behaviour. See the module docstring of :mod:`molt.versioning.ranges` for the
rationale; the short version is that a Python constraint of ``>=1.0.0rc0,<2.0.0`` really
does accept ``1.0.1rc0``, so a dependent holding that constraint is not broken and needs no
release.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from molt.versioning import (
    accepts_prereleases,
    caret,
    is_unconstrained,
    parse_range,
    satisfies,
    tilde,
)


def v(text: str) -> Version:
    return Version(text)


def s(text: str) -> SpecifierSet:
    return SpecifierSet(text)


# --------------------------------------------------------------------------------------
# Core semantics
# --------------------------------------------------------------------------------------

CONFORMANCE = [
    # (version,      specifier,             expected, why)
    ("1.5.0", ">=1.0.0,<2.0.0", True, "stable version inside the interval"),
    ("2.0.0", ">=1.0.0,<2.0.0", False, "at the exclusive upper bound"),
    ("0.9.0", ">=1.0.0,<2.0.0", False, "below the lower bound"),
    ("1.0.0", "==1.0.0", True, "exact match"),
    # Prerelease opt-in: the constraint decides.
    (
        "1.5.0rc1",
        ">=1.0.0,<2.0.0",
        False,
        "constraint names no prerelease -> does not opt in",
    ),
    (
        "1.0.1rc0",
        ">=1.0.0rc0,<2.0.0",
        True,
        "PEP 440: opt-in anywhere in the set covers the set "
        "(node-semver would say False -- we deliberately differ)",
    ),
    ("1.0.1rc0", ">=1.0.1rc0,<2.0.0", True, "opt-in on the same tuple"),
    ("1.0.1rc0", "", False, "unconstrained set opts into nothing"),
    ("1.0.1rc0", ">=0", False, "'>=0' names no prerelease"),
    ("1.0.1.dev1", ">=1.0.0.dev0,<2.0.0", True, "dev segments opt in the same way"),
    ("1.0.1.dev1", ">=1.0.0,<2.0.0", False, "dev release without opt-in"),
    ("1.0.1.post1", ">=1.0.0,<2.0.0", True, "post-releases are not prereleases"),
]


@pytest.mark.parametrize(("version", "spec", "expected", "why"), CONFORMANCE)
def test_pep440_conformance(version: str, spec: str, expected: bool, why: str) -> None:
    assert satisfies(v(version), s(spec)) is expected, why


def test_prerelease_fallback_is_suppressed() -> None:
    """The one adjustment we make to ``packaging``'s default.

    ``SpecifierSet.contains`` follows PEP 440's "match prereleases, as there are no other
    versions" recommendation. That fallback answers a *resolver* question (what should I
    install when nothing else exists?). Ours is a *constraint validity* question, where the
    fallback does not apply — so we pin the flag rather than inherit the default.
    """
    version, spec = v("1.5.0rc1"), s(">=1.0.0,<2.0.0")
    assert satisfies(version, spec) is False, "molt: constraint does not opt into prereleases"
    assert (version in spec) is True, "packaging's default applies the no-other-versions fallback"


def test_documented_divergence_from_changesets() -> None:
    """Pin the single row where we intentionally differ from node-semver.

    node-semver requires the prerelease opt-in to be on the *same release tuple*; PEP 440
    applies it set-wide. Following Python here means a dependent that already opted into
    prereleases is not re-released as its dependency moves rc0 -> rc1.
    """
    assert satisfies(v("1.0.1rc0"), s(">=1.0.0rc0,<2.0.0")) is True


# --------------------------------------------------------------------------------------
# The prerelease opt-in predicate
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (">=1.0.0,<2.0.0", False),
        (">=1.0.0rc0,<2.0.0", True),
        (">=1.0.0.dev0", True),
        ("", False),
        (">=0", False),
        ("==1.*", False),
    ],
)
def test_accepts_prereleases(spec: str, expected: bool) -> None:
    assert accepts_prereleases(s(spec)) is expected


def test_accepts_prereleases_handles_the_tristate() -> None:
    """``SpecifierSet.prereleases`` returns None (not False) when nothing opts in."""
    assert s(">=1.0.0").prereleases is None
    assert accepts_prereleases(s(">=1.0.0")) is False


# --------------------------------------------------------------------------------------
# Semver range shapes expressed in PEP 440
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.2.3", ">=1.2.3,<2.0.0"),
        ("0.2.3", ">=0.2.3,<0.3.0"),  # 0.x: minor is the breaking component
        ("0.0.3", ">=0.0.3,<0.0.4"),  # 0.0.x: patch is the breaking component
    ],
)
def test_caret_preserves_the_zero_x_special_cases(version: str, expected: str) -> None:
    assert caret(v(version)) == s(expected)


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.2.3", ">=1.2.3,<1.3.0"), ("0.2.3", ">=0.2.3,<0.3.0")],
)
def test_tilde(version: str, expected: str) -> None:
    assert tilde(v(version)) == s(expected)


def test_caret_bounds_are_exclusive_at_the_top() -> None:
    rng = caret(v("1.2.3"))
    assert satisfies(v("1.9.9"), rng) is True
    assert satisfies(v("2.0.0"), rng) is False


def test_caret_on_zero_minor_does_not_admit_the_next_minor() -> None:
    """`^0.2.3` must not match 0.3.0 — the classic semver 0.x trap."""
    assert satisfies(v("0.3.0"), caret(v("0.2.3"))) is False
    assert satisfies(v("0.2.9"), caret(v("0.2.3"))) is True


# --------------------------------------------------------------------------------------
# Range parsing predicates
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [">=1.0.0", ">=1.0.0,<2.0.0", "==1.*", "~=1.2.3", ""])
def test_parse_range_accepts_valid_specifiers(text: str) -> None:
    assert parse_range(text) is not None


@pytest.mark.parametrize("text", ["file:../pkg", "not a range", "workspace:*", "latest"])
def test_parse_range_rejects_non_ranges(text: str) -> None:
    """Direct references and workspace markers must be skipped, not rewritten."""
    assert parse_range(text) is None


def test_is_unconstrained() -> None:
    assert is_unconstrained(s("")) is True
    assert is_unconstrained(s(">=0")) is False
    assert is_unconstrained(s(">=1.0.0")) is False


# --------------------------------------------------------------------------------------
# Properties
# --------------------------------------------------------------------------------------

versions = st.builds(
    lambda a, b, c: Version(f"{a}.{b}.{c}"),
    st.integers(0, 100),
    st.integers(0, 100),
    st.integers(0, 100),
)


@given(version=versions)
def test_a_stable_version_always_satisfies_its_own_caret_range(version: Version) -> None:
    assert satisfies(version, caret(version)) is True


@given(version=versions)
def test_a_stable_version_always_satisfies_its_own_tilde_range(version: Version) -> None:
    assert satisfies(version, tilde(version)) is True


@given(version=versions)
def test_prereleases_never_match_a_constraint_that_does_not_opt_in(version: Version) -> None:
    assert satisfies(Version(f"{version}rc1"), s(">=0.0.0")) is False
