"""What a dependency's version specifier becomes once the dependency has been released.

This is the module that replaces upstream's ``getVersionRangeType``
(``version-package.ts:116-125``), and replacing it is the point. Upstream reduces a whole range to
its **leading operator** and re-renders it against the new version, so ``">=1.0.0 <2.0.0"`` comes
back as ``">=1.0.4"`` -- the upper bound the author wrote to exclude the next breaking major is
silently deleted. Research README section 3.3 records that as a bug molt does not port, and the six
``getVersionRangeType`` rows are a standing Drop in
``tests/versioning/test_deliberately_not_ported.py``.

molt rewrites **comparator by comparator** instead (research doc 04 section 2.6):

============================  ==============  =========================  =========================
Declared                      New version     molt                       upstream
============================  ==============  =========================  =========================
``>=1.0.3,<2.0.0``            ``1.0.4``       ``>=1.0.4,<2.0.0``         ``>=1.0.4``
``>=1.0.3,<2.0.0``            ``2.0.0``       ``>=2.0.0,<3.0.0``         ``>=2.0.0``
``~=1.2.0``                   ``1.3.0``       ``~=1.3.0``                ``~=1.3.0``
``==1.0.0``                   ``2.0.0``       ``==2.0.0``                ``2.0.0``
============================  ==============  =========================  =========================

The escaped two-sided row is the one that has to move **both** bounds. Rewriting only the lower one
yields the unsatisfiable ``>=2.0.0,<2.0.0``; dropping the upper one reintroduces the widening bug.
``>=2.0.0,<3.0.0`` is the answer ``website/docs/guide/dependency-propagation.md`` ("Out of range:
cascade + rewrite") documents, and it is the PEP 440 spelling of upstream's ``^1.0.3 -> ^2.0.0``.

Comparator **order is the author's** and is preserved, which is why the region text is read through
:func:`molt.apply.edit_toml.specifier_region` rather than off a ``SpecifierSet``: that class is a
set, and ``str()`` on it sorts.
"""

from __future__ import annotations

from packaging.specifiers import InvalidSpecifier, Specifier, SpecifierSet
from packaging.version import Version

from molt.versioning import caret

__all__ = ["exact_pin", "rewrite_specifier"]

#: Comparators that state a **floor** -- the oldest version the dependent accepts. Each one follows
#: the dependency forward. ``>`` is normalized to ``>=`` on the way: leaving it exclusive would make
#: the rewritten constraint reject the very version that was just published.
_FLOOR_OPERATORS = frozenset({"==", "===", "~=", ">=", ">"})

#: Comparators that state a **ceiling**. These move only when the new version has escaped them.
_CEILING_OPERATORS = frozenset({"<", "<="})


def exact_pin(new_version: Version) -> str:
    """``==<new_version>`` -- the specifier with every range modifier dropped.

    Two callers, one spelling. A **snapshot** release pins exactly because a snapshot version is
    not orderable against the release stream it was cut from (``version-package.ts:103-105``), and
    an **unconstrained** dependency is pinned when its new version is a prerelease because pip does
    not select a prerelease for a bare requirement, so leaving the pin bare would produce a tree
    that cannot be installed (``version-package.ts:99-101``).
    """
    return f"=={new_version}"


def rewrite_specifier(region: str, new_version: Version) -> str:
    """Return ``region`` rewritten so it accepts, and floors at, ``new_version``.

    ``region`` is the specifier text exactly as the author wrote it -- the slice
    :func:`~molt.apply.edit_toml.specifier_region` locates inside a PEP 508 requirement string.
    Comparators are returned in the same order, joined by ``,``; whitespace *inside* the region is
    not preserved, because the region is spliced back wholesale.

    An empty region returns an exact pin: there is nothing to move, so the only rewrite that means
    anything is a pin. Callers reach that branch only for the prerelease exception -- an
    unconstrained dependency is otherwise left alone entirely, and this function is not called.
    """
    parts = [part.strip() for part in region.split(",") if part.strip()]
    if not parts:
        return exact_pin(new_version)
    return ",".join(_rewrite_comparator(part, new_version) for part in parts)


def _rewrite_comparator(part: str, new_version: Version) -> str:
    """Rewrite one comparator, or return it untouched when it does not have to move."""
    try:
        specifier = Specifier(part)
    except InvalidSpecifier:
        # Provably not something this module understands. Splicing a guess over it would be worse
        # than leaving a constraint the caller can still read in a diff.
        return part

    operator = specifier.operator
    if operator in _FLOOR_OPERATORS:
        return _rewrite_floor(specifier, new_version)
    if operator in _CEILING_OPERATORS:
        return part if _accepts(part, new_version) else f"<{_next_breaking(new_version)}"
    # `!=` and anything else: an exclusion says nothing about where the floor is, and moving it
    # would exclude a version the author never mentioned.
    return part


def _rewrite_floor(specifier: Specifier, new_version: Version) -> str:
    """Move a floor comparator onto ``new_version``, keeping the shape the author chose."""
    if specifier.version.endswith(".*"):
        # A wildcard pin (`==1.2.*`) is a window, not a point. Re-render it at the same precision
        # so the author's "any 1.2" stays "any 1.3" instead of collapsing to a single version.
        return f"{specifier.operator}{_at_precision(new_version, specifier.version)}"
    operator = ">=" if specifier.operator == ">" else specifier.operator
    return f"{operator}{new_version}"


def _at_precision(new_version: Version, wildcard: str) -> str:
    """``1.3.0`` + ``1.2.*`` -> ``1.3.*``: the new version truncated to the wildcard's width."""
    width = len(wildcard.removesuffix(".*").split("."))
    return ".".join(str(part) for part in (*new_version.release, 0, 0)[:width]) + ".*"


def _accepts(part: str, new_version: Version) -> bool:
    """Whether ``part`` still admits ``new_version``, as a pure ordering question.

    ``prereleases=True`` deliberately, and this is the one place it differs from
    :func:`molt.versioning.satisfies`. A ceiling asks "is the new version below this bound"; PEP
    440's prerelease exclusion answers a different question -- whether a resolver may *select* a
    prerelease -- and letting it decide here would widen ``<1.5.0`` to ``<2.0.0`` the first time a
    dependency published a ``1.2.0rc1`` that the bound never excluded.
    """
    return SpecifierSet(part).contains(new_version, prereleases=True)


def _next_breaking(new_version: Version) -> str:
    """The first version that would break a dependent on ``new_version``.

    Read off :func:`molt.versioning.caret` rather than computed here, so the 0.x asymmetry
    (``^0.2.3`` breaks at ``0.3.0``, ``^0.0.3`` at ``0.0.4``) has exactly one implementation.
    """
    for specifier in caret(new_version):
        if specifier.operator == "<":
            return specifier.version
    raise AssertionError(  # pragma: no cover - caret() always emits an upper bound
        f"caret({new_version}) produced no upper bound"
    )
