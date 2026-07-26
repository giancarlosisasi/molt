"""Range matching for dependency constraints, using PEP 440 semantics.

This module answers exactly one question for the release engine:

    *Does a dependent's declared constraint still accept its dependency's new version?*

If yes, the dependent needs no release. If no, the dependent must be released with a
widened constraint.

Relationship to changesets
--------------------------
Changesets answers this with node-semver, whose prerelease rule differs from Python's in
one specific way:

===================================  =====================  ====================
Prerelease opt-in                    node-semver            PEP 440 / pip
===================================  =====================  ====================
Scope of the opt-in                  same release tuple     anywhere in the set
``1.0.1rc0`` vs ``>=1.0.0rc0,<2``    out of range           **in range**
===================================  =====================  ====================

**We deliberately follow PEP 440, not node-semver.** In Python a constraint of
``>=1.0.0rc0,<2.0.0`` genuinely does accept ``1.0.1rc0`` — pip will resolve and install it —
so the dependent is *not* broken and does not need a release. Reproducing node-semver's
stricter rule would invent a JavaScript-ism with no meaning in this ecosystem and would
over-release dependents on every prerelease bump.

Consequence to be aware of: in prerelease mode, a dependent that already opted into
prereleases will not be auto-bumped as its dependency moves rc0 -> rc1 -> rc2. If you want
every internal dependent to move in lockstep regardless of range, that is what the
``update_internal_dependents = "always"`` config option is for — an explicit choice, rather
than something smuggled into the matching semantics.

Why not just call ``SpecifierSet.contains``
-------------------------------------------
``packaging`` is correct and spec-conforming; this module is a thin, intentional wrapper
over it, not a replacement. The one adjustment: ``contains()`` defaults to PEP 440's
"match prereleases, as there are no other versions" recommendation (``specifiers.py:1753``,
implemented as ``filter()`` over a one-item list at ``specifiers.py:1785``). That fallback
is a *resolver* concern — it decides what to install when nothing else is available. Our
question is about *constraint validity*, where the fallback does not apply, so we pin the
``prereleases`` flag explicitly instead of inheriting the default.
"""

from __future__ import annotations

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

__all__ = [
    "accepts_prereleases",
    "caret",
    "is_unconstrained",
    "parse_range",
    "satisfies",
    "tilde",
]


def accepts_prereleases(specifier_set: SpecifierSet) -> bool:
    """Return whether ``specifier_set`` opts into prereleases.

    True when any comparator names a prerelease, which is pip's per-package opt-in rule:
    ``>=1.0.0`` does not accept prereleases, ``>=1.0.0rc0`` accepts them for that package.

    ``SpecifierSet.prereleases`` is tri-state (``True`` / ``None`` / an explicit override),
    so compare against ``True`` rather than relying on truthiness.
    """
    return specifier_set.prereleases is True


def satisfies(version: Version, specifier_set: SpecifierSet) -> bool:
    """Return whether ``version`` is accepted by ``specifier_set`` under PEP 440.

    A stable version matches on plain interval containment. A prerelease matches only if
    the constraint opted into prereleases — deliberately *without* PEP 440's "there are no
    other versions" fallback, which is a resolver-time policy rather than a statement about
    whether the constraint is still valid.
    """
    return specifier_set.contains(version, prereleases=accepts_prereleases(specifier_set))


def parse_range(text: str) -> SpecifierSet | None:
    """Parse ``text`` as a PEP 440 specifier set, or ``None`` if it is not one.

    Mirrors node-semver's ``validRange(r) === null`` predicate, which upstream uses to tell
    real version constraints apart from paths and protocols. In Python the same guard is
    needed for direct references (``file:///…``, a git URL) and workspace markers, which
    must be skipped rather than rewritten.
    """
    try:
        return SpecifierSet(text)
    except InvalidSpecifier:
        return None


def is_unconstrained(specifier_set: SpecifierSet) -> bool:
    """True when the set imposes no constraint (the PEP 440 analogue of semver ``*``).

    An unconstrained dependency accepts anything, so it never needs rewriting when the
    dependency's version changes.
    """
    return len(specifier_set) == 0


def caret(version: Version) -> SpecifierSet:
    """Convert a semver caret range to PEP 440, preserving the 0.x special cases.

    ``^`` is where semver's zero-major asymmetry actually lives::

        ^1.2.3  ->  >=1.2.3,<2.0.0
        ^0.2.3  ->  >=0.2.3,<0.3.0     # 0.x:   minor is the breaking component
        ^0.0.3  ->  >=0.0.3,<0.0.4     # 0.0.x: patch is the breaking component

    Python has no caret operator. This exists to translate changesets' semver-written test
    fixtures into PEP 440 so ported conformance matrices keep their meaning, and to offer
    users migrating from a JS monorepo a familiar shorthand.
    """
    major, minor, patch = (*version.release, 0, 0)[:3]
    if major != 0:
        upper = f"{major + 1}.0.0"
    elif minor != 0:
        upper = f"0.{minor + 1}.0"
    else:
        upper = f"0.0.{patch + 1}"
    return SpecifierSet(f">={version},<{upper}")


def tilde(version: Version) -> SpecifierSet:
    """Convert a semver tilde range to PEP 440: ``~1.2.3`` -> ``>=1.2.3,<1.3.0``.

    Equivalent to PEP 440's ``~=1.2.3``, spelled as an explicit two-sided range so the lower
    bound stays rewritable by the same code path as :func:`caret`.
    """
    major, minor, _ = (*version.release, 0, 0)[:3]
    return SpecifierSet(f">={version},<{major}.{minor + 1}.0")
