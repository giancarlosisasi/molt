"""Version arithmetic and range matching for molt.

The foundation the release-plan engine is built on. Both halves follow **PEP 440**, not
semver: :mod:`~molt.versioning.bump` ports node-semver's increment *rules* onto PEP 440
versions, while :mod:`~molt.versioning.ranges` deliberately uses Python's own prerelease
semantics rather than node-semver's. See each module's docstring for the reasoning.
"""

from molt.versioning.bump import BumpType, highest, inc, next_pre_number
from molt.versioning.ranges import (
    accepts_prereleases,
    caret,
    is_unconstrained,
    parse_range,
    satisfies,
    tilde,
)

__all__ = [
    "BumpType",
    "accepts_prereleases",
    "caret",
    "highest",
    "inc",
    "is_unconstrained",
    "next_pre_number",
    "parse_range",
    "satisfies",
    "tilde",
]
