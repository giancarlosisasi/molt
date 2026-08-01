"""Conformance tests for the PEP 440 prerelease predicate (``molt.versioning.is_prerelease``).

Port source
-----------
``changesets/action`` **v1.9.0**, ``src/run.ts::createRelease``, which marks a host release as a
prerelease with ``prerelease: pkg.packageJson.version.includes("-")``.

Why molt does not port that line
--------------------------------
``includes("-")`` is npm semver's rule, where a prerelease is spelled ``1.0.0-rc.1``. PEP 440
spells the same thing ``1.0.0rc1`` -- **with no hyphen at all** -- and puts the only hyphen a
molt-written version can carry inside the *local* segment, which is not a prerelease. So the
literal port is wrong in **both** directions:

* every ``molt version --pre rc`` release and every snapshot (``0.0.0.dev20260731120000``) would
  be published to the host as a full release, which is what users subscribe to;
* a local version such as ``1.0.0+local.build-1`` would be published as a prerelease.

The failure is silent and it is only visible after publication, which is why the truth table below
is a test rather than a comment. Divergence classification: research README section 4 (Python
divergences), not an upstream bug being refused.

The predicate itself is ``packaging.version.Version.is_prerelease`` -- true for ``a``/``b``/``rc``
**and** ``.devN``, false for a post-release. That is the same predicate ``molt.versioning.bump``
already relies on, for the same reason (``src/molt/versioning/bump.py:93-95``).
"""

from __future__ import annotations

import pytest
from packaging.version import InvalidVersion, Version

from molt.versioning import is_prerelease

# (version, expected, why) -- the truth table from the change's design D4, one row per line.
PRERELEASE_CASES = [
    ("1.0.0", False, "a plain release is not a prerelease"),
    ("1.0.0rc1", True, "a release candidate: PEP 440 spells it with no hyphen"),
    ("1.0.0a1", True, "an alpha"),
    ("1.0.0b2", True, "a beta"),
    ("1.0.0.dev1", True, "a development release -- packaging counts .devN as a prerelease"),
    (
        "0.0.0.dev20260731120000",
        True,
        "a molt snapshot version: the case the npm reading gets most damagingly wrong",
    ),
    ("1.0.0.post1", False, "a post-release ships after the release; it is not ahead of one"),
    (
        "1.0.0+local.build-1",
        False,
        "a local version -- and the only row in this table that carries a hyphen",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("version", "expected", "why"), PRERELEASE_CASES)
def test_the_prerelease_flag_follows_pep_440(version: str, expected: bool, why: str) -> None:
    """The truth table a release is marked from (design D4).

    Both accepted spellings are asserted on the same row: molt's own callers hold a
    ``packaging.Version`` (the engine's plan does), while a version read back out of a manifest or
    a tag is a ``str``.
    """
    assert is_prerelease(version) is expected, why
    assert is_prerelease(Version(version)) is expected, f"{why} (as a parsed Version)"


@pytest.mark.unit
def test_the_npm_hyphen_reading_is_wrong_in_both_directions() -> None:
    """The negative control: pin upstream's rule as **wrong**, not merely as not-used.

    Without this row the table above would still pass against a hyphen test for six of its eight
    rows, so the two rows where the readings disagree are asserted directly against each other.
    This is what makes the divergence a rule rather than an accident of which versions the table
    happens to list.
    """
    candidate = "1.0.0rc1"
    assert ("-" in candidate) is False, "upstream reads a PEP 440 release candidate as final"
    assert is_prerelease(candidate) is True, "molt reads it as what it is"

    local = "1.0.0+local.build-1"
    assert ("-" in local) is True, "upstream reads a local version as a prerelease"
    assert is_prerelease(local) is False, "molt does not"


@pytest.mark.unit
def test_an_unparseable_version_is_refused() -> None:
    """A version molt itself wrote always parses, so an unparseable one is a caller mistake.

    Guessing here would mark a release wrongly and say nothing; ``packaging``'s own
    ``InvalidVersion`` names the offending text.
    """
    with pytest.raises(InvalidVersion):
        is_prerelease("not-a-version")
