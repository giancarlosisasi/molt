"""Glob expansion and the cross-option validation rules.

Ports ``packages/config/src/rules.ts`` -- six of its seven rules; the seventh
(``noPrivateTagWithoutPrivateVersion``, ``rules.ts:174-182``) exists only to reject a combination
involving the dropped npm dist-channel sub-key, so with that key gone the rule has no inputs
(``tests/config/test_deliberately_not_ported.py``).

Two deliberate divergences from upstream live here, both recorded in research README section 3.4 as
bugs molt does **not** port:

1. **``fixed`` / ``linked`` globs are expanded** (design D4). changesets 3.0 documents and warns
   about globs in these options but stores the groups verbatim, so a glob silently does nothing and
   then throws ``InternalError`` at release time from ``matchFixedConstraint``'s literal
   ``.includes()``. Expanding at parse time means the engine only ever sees resolved names.
   Expansion runs **after** discovery, because it matches against real package names.
2. **A duplicate is reported once** (design D6). Upstream's ``duplicatedNames`` set is declared
   outside the group loop while the ``errors.push`` is inside it, so a name in N groups is reported
   N-1 times. With the two groups its own tests use that looks correct; three groups expose it.

Every name comparison goes through PEP 503 normalization on **both** sides (design D5), which is
new surface with no changesets analogue -- npm names carry no punctuation equivalence.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from molt.config.result import ConfigIssue
from molt.globs import compile_pattern, glob_match, unmatched_patterns
from molt.names import normalize_name

if TYPE_CHECKING:
    from molt.config.models import Config
    from molt.ecosystem import Package, Workspace

__all__ = [
    "check_dependents_of_ignored",
    "check_fixed_and_linked_disjoint",
    "expand_groups",
    "expand_ignore",
]


def expand_ignore(
    patterns: Sequence[str], package_names: Sequence[str]
) -> tuple[tuple[str, ...], list[ConfigIssue]]:
    """Resolve ``ignore`` to concrete package names, warning on any pattern that matches nothing.

    Row 24 of the reference suite stays intact -- an unmatched entry resolves to nothing and is
    **not** an error, so a stale entry never blocks a release. What molt adds is the diagnostic:
    upstream drops the entry with no signal at all (``config.ts:181-183`` -> ``utils.ts:13-41``),
    which is the config-level instance of the silent skip in changesets issue #2101 (research doc 08
    section D.2).

    The workspace's own spelling is what lands on ``Config``, not the pattern's (research doc 02
    section 12.4, "preserve the original spelling for display").
    """
    resolved = glob_match(package_names, patterns, key=normalize_name)
    warnings = [
        ConfigIssue(("ignore",), _unmatched_message(pattern))
        for pattern in unmatched_patterns(patterns, package_names, key=normalize_name)
    ]
    return tuple(resolved), warnings


def expand_groups(
    option: str, groups: Sequence[Sequence[str]], package_names: Sequence[str]
) -> tuple[tuple[tuple[str, ...], ...], list[ConfigIssue], list[ConfigIssue]]:
    """Expand one option's groups and validate membership. Returns ``(groups, warnings, errors)``.

    Ports ``fixedGroupsExist`` / ``linkedGroupsExist`` (warnings, ``rules.ts:22-37``, ``:62-77``)
    and ``noDuplicateFixedPackages`` / ``noDuplicateLinkedPackages`` (errors, ``rules.ts:39-60``,
    ``:79-100``), with expansion in front of both.

    A group left with no members after expansion is **dropped** rather than kept as an empty list,
    so the engine never has to guard a degenerate group. A group that still has members keeps them
    in package-discovery order.
    """
    warnings: list[ConfigIssue] = []
    errors: list[ConfigIssue] = []
    resolved_groups: list[tuple[str, ...]] = []
    # normalized name -> index of the group that first claimed it.
    owner: dict[str, int] = {}
    reported: set[str] = set()

    for index, group in enumerate(groups):
        for pattern in unmatched_patterns(group, package_names, key=normalize_name):
            warnings.append(ConfigIssue((option, index), _unmatched_message(pattern)))
        members = glob_match(package_names, group, key=normalize_name)
        for member in members:
            key = normalize_name(member)
            if key not in owner:
                owner[key] = index
                continue
            if key in reported:
                # Design D6: one error per duplicated package, however many groups follow.
                continue
            reported.add(key)
            errors.append(ConfigIssue((option, index), _duplicate_message(option, member, group)))
        if members:
            resolved_groups.append(tuple(members))
    return tuple(resolved_groups), warnings, errors


def check_fixed_and_linked_disjoint(
    fixed: Sequence[Sequence[str]], linked: Sequence[Sequence[str]]
) -> list[ConfigIssue]:
    """``noFixedAndLinkedPackages`` (``rules.ts:102-115``) -- untested upstream, ported anyway.

    Research doc 02 lists it as rule 5 of 7. Without a test it would silently disappear in the
    port, and the two grouping strategies would then fight over the same package.
    """
    if not fixed or not linked:
        return []
    linked_names = {normalize_name(name) for group in linked for name in group}
    errors: list[ConfigIssue] = []
    seen: set[str] = set()
    for group in fixed:
        for name in group:
            key = normalize_name(name)
            if key in linked_names and key not in seen:
                seen.add(key)
                errors.append(
                    ConfigIssue(
                        (),
                        f'Invalid group: the package "{name}" is in both a fixed and a linked '
                        f"group. A package can only be either fixed or linked.",
                    )
                )
    return errors


def check_dependents_of_ignored(config: Config, workspace: Workspace) -> list[ConfigIssue]:
    """``alsoSkipDependentsOfSkipped`` (``rules.ts:124-172``) -- the trickiest rule upstream has.

    Releasing a public package whose dependency is frozen would publish a constraint pointing at a
    version that is never going to exist, so a **public** dependent of a skipped package is an
    error. A **private** dependent is exempt (``rules.ts:164-166``): it is never uploaded, so a
    stale pin on a skipped dependency cannot break a consumer.

    Development dependencies are excluded, deliberately and for the same reason -- upstream builds
    this graph with ``ignoreDevDependencies: true`` while ``assemble-release-plan`` uses a
    *different* graph that includes them, because that one still has to rewrite the ranges.

    ``ignore`` has already been expanded to concrete names by the time this runs, so the membership
    test is exact and globs never reach it.
    """
    version_private = config.private_packages.version
    if not config.ignore and version_private:
        # Nothing can be skipped, so nothing can depend on something skipped (rules.ts:125-126).
        return []

    ignored = {normalize_name(name) for name in config.ignore}

    def is_skipped(package: Package) -> bool:
        """``shouldSkipPackage`` (``should-skip-package/src/index.ts:3-22``).

        The third clause -- a package with no version is always skipped -- is upstream's, and it is
        why the version-source abstraction matters for Python: ``dynamic = ["version"]`` is common
        here and unrepresentable there.
        """
        if package.normalized_name in ignored:
            return True
        if package.private and not version_private:
            return True
        return package.version is None

    errors: list[ConfigIssue] = []
    for package in workspace.packages:
        if package.private or is_skipped(package):
            continue
        requirements = (*package.dependencies, *package.optional_dependencies)
        for dependency_name in _requirement_names(requirements):
            dependency = workspace.get(dependency_name)
            if dependency is None or not is_skipped(dependency):
                continue
            errors.append(
                ConfigIssue(
                    ("ignore",),
                    f'Invalid tree: "{package.name}" depends on the skipped package '
                    f'"{dependency.name}", but "{package.name}" is not skipped. Add '
                    f'"{package.name}" to the "ignore" option.',
                )
            )
    return errors


def _requirement_names(requirements: Sequence[str]) -> list[str]:
    """Distribution names of PEP 508 requirement strings, skipping unparseable ones.

    A malformed requirement is a *manifest* defect, and diagnosing it belongs to the dependency
    graph (change 06) which has to parse the specifier anyway. Reporting it twice, from a layer
    that only wants the name, would put the same error in two voices.
    """
    from packaging.requirements import InvalidRequirement, Requirement

    names: list[str] = []
    for text in requirements:
        try:
            names.append(Requirement(text).name)
        except InvalidRequirement:
            continue
    return names


def _unmatched_message(pattern: str) -> str:
    return (
        f'The package or glob "{pattern}" does not match any package in the project. '
        f"It was ignored."
    )


def _duplicate_message(option: str, member: str, group: Sequence[str]) -> str:
    """Name the resolved package *and*, when a glob produced it, the pattern the user wrote.

    Expanding first means one glob in two groups yields one error per resolved name where upstream
    printed one per glob. That is the true cardinality of the conflict, but it is a UX regression
    unless the report also points back at text that appears in the config file -- upstream's
    message does (``rules.ts:56``), so molt's does too.
    """
    source = _source_pattern(member, group)
    via = "" if source is None or source == member else f' (matched by "{source}")'
    return (
        f'Invalid group: the package "{member}"{via} is defined in multiple groups of {option} '
        f"packages. A package can only belong to one group."
    )


def _source_pattern(member: str, group: Sequence[str]) -> str | None:
    """The first positive pattern in ``group`` that matches ``member``."""
    for pattern in group:
        matcher = compile_pattern(pattern, normalize_name)
        if not matcher.negated and matcher.hits(normalize_name(member)):
            return pattern
    return None
