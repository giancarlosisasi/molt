"""PEP 440 bump arithmetic.

Ports node-semver's ``SemVer.inc`` semantics onto PEP 440 versions.

Reference: ``roadmap/research/changesets-01-core-versioning-engine.md`` §3.2, §3.3, §15.2, §15.3.

The prerelease special cases in :func:`inc` are the entire reason exiting prerelease mode
"just works" upstream (``1.1.0rc3`` + minor == ``1.1.0``). Do not replace this with a
library ``bump_*`` helper — none of them reproduce these cases.
"""

from __future__ import annotations

from enum import StrEnum

from packaging.version import Version

__all__ = ["BumpType", "highest", "inc", "next_pre_number"]


class BumpType(StrEnum):
    """A release bump, ordered ``NONE < PATCH < MINOR < MAJOR``.

    ``NONE`` is a real, selectable bump type upstream: it produces a changelog entry
    without a version change. It is also *undocumented* upstream (research doc 05 §3).
    """

    NONE = "none"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK: dict[BumpType, int] = {
    BumpType.NONE: 0,
    BumpType.PATCH: 1,
    BumpType.MINOR: 2,
    BumpType.MAJOR: 3,
}


def highest(bumps: list[BumpType]) -> BumpType:
    """Return the highest bump in ``bumps``, or ``NONE`` when empty.

    Upstream's ``getHighestReleaseType`` *throws* on an empty list, but both call sites
    guard against it, making the branch unreachable (research doc 01 §3.1). Returning
    ``NONE`` is the same behaviour without the unreachable error path.
    """
    return max(bumps, key=lambda b: b.rank, default=BumpType.NONE)


def _split_release(version: Version) -> tuple[int, int, int]:
    """Pad or reject a PEP 440 release segment to exactly ``(major, minor, patch)``.

    PEP 440 permits arbitrary-length release segments (``1``, ``1.2``, ``1.2.3.4``), but
    major/minor/patch bumping is only defined for three. Short segments are zero-padded;
    longer ones are rejected loudly rather than silently truncated.
    """
    release = version.release
    if len(release) > 3:
        raise ValueError(
            f"Cannot bump {version!s}: it has {len(release)} release components, "
            "but major/minor/patch bumping is only defined for up to 3."
        )
    padded = (*release, 0, 0)[:3]
    return padded[0], padded[1], padded[2]


def inc(version: Version, kind: BumpType) -> Version:
    """Increment ``version`` by ``kind``, reproducing node-semver's ``inc`` exactly.

    The prerelease rules (research doc 01 §3.3)::

        major: if minor != 0 or patch != 0 or not prerelease: major += 1; minor = patch = 0
        minor: if patch != 0 or not prerelease:               minor += 1; patch = 0
        patch: if not prerelease:                             patch += 1

    In every case the prerelease, post-release and local segments are cleared, mirroring
    semver's "clear everything below the bumped component".

    There is deliberately **no 0.x special case** — ``major`` on ``0.1.0`` gives ``1.0.0``
    (research doc 01 §3.2). The 0.x asymmetry lives on the *range* side instead; see
    :func:`molt.versioning.ranges.caret`.
    """
    if kind is BumpType.NONE:
        return version

    major, minor, patch = _split_release(version)
    # packaging's `is_prerelease` is True for both a/b/rc *and* .devN, which is exactly
    # node-semver's `prerelease.length !== 0`. Post-releases are correctly excluded.
    is_pre = version.is_prerelease

    if kind is BumpType.MAJOR:
        if minor != 0 or patch != 0 or not is_pre:
            major += 1
        minor = patch = 0
    elif kind is BumpType.MINOR:
        if patch != 0 or not is_pre:
            minor += 1
        patch = 0
    else:  # PATCH
        if not is_pre:
            patch += 1

    epoch = f"{version.epoch}!" if version.epoch else ""
    return Version(f"{epoch}{major}.{minor}.{patch}")


def next_pre_number(version: Version) -> int:
    """Return the next prerelease counter for ``version``.

    ``0`` means "this package is not currently a prerelease" — the prerelease-exit backfill
    depends on that sentinel (research doc 01 §15.3).

    Note the ordering: ``.devN`` is checked first because a version may carry *both* a
    prerelease and a dev segment (``1.0.0rc1.dev2``), and the dev counter is the more
    specific one.
    """
    if version.dev is not None:
        return version.dev + 1
    if version.pre is not None:
        return version.pre[1] + 1
    return 0
