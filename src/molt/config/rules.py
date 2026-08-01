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

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from molt.config.result import ConfigIssue
from molt.globs import compile_pattern, glob_match, unmatched_patterns
from molt.names import normalize_name

if TYPE_CHECKING:
    from molt.config.models import Config
    from molt.ecosystem import Package, Workspace

__all__ = [
    "SNAPSHOT_PLACEHOLDERS",
    "VERSION_PATTERN_GROUP",
    "check_changelog_coherent",
    "check_dependents_of_ignored",
    "check_fixed_and_linked_disjoint",
    "check_snapshot_placeholders",
    "check_version_source",
    "expand_groups",
    "expand_ignore",
]

#: The named group a ``version_source.pattern`` must capture. It is the span molt replaces when it
#: writes a new version (version-sources design D4), so a pattern without it locates a line molt
#: could not edit -- which is a configuration that cannot mean anything, and therefore an error
#: rather than a discovery-time surprise.
VERSION_PATTERN_GROUP = "version"

#: The closed set of placeholders ``snapshot.prerelease_template`` understands (research doc 01
#: section 11.1).
#:
#: **This is a second list**, and that is a known hazard rather than an oversight. The engine spells
#: the same five names inside a compiled regex, ``molt.engine.assemble._PLACEHOLDERS`` -- a private
#: pattern, not a shareable constant -- and :mod:`molt.config` must not import :mod:`molt.engine`
#: anyway: config is the layer the engine is configured *by*, and it is a lazy facade that may not
#: drag heavy modules in. The two lists disagreeing would refuse a template the engine renders
#: correctly, which is worse than the hole this rule closes, so they are pinned against each other
#: by ``tests/config/test_parse.py``'s reuse of ``tests/engine/test_assemble.py``'s template
#: matrix. Recorded as a gap (``CFG-7``).
SNAPSHOT_PLACEHOLDERS: tuple[str, ...] = (
    "tag",
    "commit",
    "commit-short",
    "timestamp",
    "datetime",
)

#: Every ``{...}`` token in a template, whether molt knows it or not -- the unknown ones are the
#: point. Deliberately wider than :data:`SNAPSHOT_PLACEHOLDERS`'s alternation, which only ever
#: matches what molt already accepts.
_TOKEN = re.compile(r"\{([^{}]*)\}")


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


def check_changelog_coherent(config: Config) -> list[ConfigIssue]:
    """Refuse a ``changelog`` table that switches the generator off and still configures it.

    Owner ruling 2026-07-31 (session 6), closing gap ``CFG-3``. ``generator = false`` means no
    ``CHANGELOG.md`` is written at all -- ``molt.apply.apply._plan_changelogs`` returns before any
    entry is assembled -- so a ``template`` beside it is read off disk by the command and then
    discarded, and ``dates = true`` decorates an entry that is never rendered. The user configured
    an entry template and got no changelog, which is precisely the impossible state the ruling
    removes.

    Molt-native: there is no changesets analogue, because upstream has no entry template at all
    (research README section 5 item 5).
    """
    from molt.config.models import ChangelogOptions

    changelog = config.changelog
    if not isinstance(changelog, ChangelogOptions) or changelog.generator is not False:
        return []
    written = (("template", changelog.template is not None), ("dates", changelog.dates))
    configured = [name for name, present in written if present]
    if not configured:
        return []
    return [
        ConfigIssue(
            ("changelog",),
            f"changelog sets generator = false, which writes no CHANGELOG.md at all, and also "
            f"sets {' and '.join(configured)}, which only a written changelog can use. Remove "
            f"{' and '.join(configured)}, or name a generator instead of false.",
        )
    ]


def check_snapshot_placeholders(config: Config) -> list[ConfigIssue]:
    """Refuse a ``{token}`` in ``snapshot.prerelease_template`` that molt cannot substitute.

    Owner ruling 2026-07-31 (session 6). The placeholder set is closed
    (:data:`SNAPSHOT_PLACEHOLDERS`), and an unrecognised token is rendered as **literal text into a
    version string** -- which is permanent the moment an index stores it. That is the same class of
    harm the engine's invocation-time checks exist to prevent, caught one layer earlier, where the
    message can name the option instead of the run.

    What this rule deliberately does **not** decide is whether the template is coherent with a given
    *invocation* -- a ``{tag}`` with no ``--snapshot <name>``, or the inverse. That depends on the
    command line, not on the document, and stays where it is.
    """
    template = config.snapshot.prerelease_template
    if not template:
        return []
    accepted = set(SNAPSHOT_PLACEHOLDERS)
    known = ", ".join(f"{{{name}}}" for name in SNAPSHOT_PLACEHOLDERS)
    errors: list[ConfigIssue] = []
    reported: set[str] = set()
    for match in _TOKEN.finditer(template):
        token = match.group(1)
        if token in accepted or token in reported:
            continue
        reported.add(token)
        errors.append(
            ConfigIssue(
                ("snapshot", "prerelease_template"),
                f'snapshot.prerelease_template uses "{{{token}}}", which molt does not recognize '
                f"and would write into the version verbatim. molt accepts {known}.",
            )
        )
    return errors


def check_version_source(config: Config) -> list[ConfigIssue]:
    """Refuse a ``version_source`` table molt could not act on, naming the fix.

    Two combinations cannot mean anything, and both are hard errors rather than warnings for the
    reason the 2026-07-31 (session 6) strictness ruling gives: a version source molt half
    understands would write a version into the wrong place, and a version an index has stored is
    permanent.

    * ``kind = "file"`` with no ``path`` -- there is no file to read or splice.
    * a ``pattern`` that does not compile, or that compiles without a group named ``version`` --
      the group **is** the span molt replaces, so a pattern lacking it has located a line molt
      cannot edit.

    A missing ``kind`` is pydantic's own required-field error and is not restated here; two
    messages for one mistake is exactly what the pre-pass exists to avoid.
    """
    source = config.version_source
    if source is None:
        return []
    errors: list[ConfigIssue] = []
    if source.kind == "file" and not source.path:
        errors.append(
            ConfigIssue(
                ("version_source", "path"),
                'version_source sets kind = "file" but names no path, so molt has no file to read '
                'the version from. Add path = "src/<package>/__about__.py" (or wherever the '
                "version literal lives), relative to this package's own directory.",
            )
        )
    errors.extend(_check_version_pattern(source.pattern))
    return errors


def _check_version_pattern(pattern: str | None) -> list[ConfigIssue]:
    """The two ways a ``version_source.pattern`` can be unusable, each with its own sentence."""
    if not pattern:
        return []
    location = ("version_source", "pattern")
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        return [
            ConfigIssue(
                location,
                f"version_source.pattern is not a valid regular expression ({error}). Fix the "
                f"expression, or remove the line to use molt's default pattern.",
            )
        ]
    if VERSION_PATTERN_GROUP not in compiled.groupindex:
        return [
            ConfigIssue(
                location,
                f"version_source.pattern must capture a group named "
                f'"{VERSION_PATTERN_GROUP}" -- that group is the text molt replaces when it '
                f"writes a new version, so without it molt cannot edit the file. Write "
                f'something like __version__ = "(?P<{VERSION_PATTERN_GROUP}>[^"]+)".',
            )
        ]
    return []


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
        """``shouldSkipPackage`` (``should-skip-package/src/index.ts:3-22``), read off the manifest.

        The third clause -- a package with no version is always skipped -- is upstream's, and it is
        the one ``implement-version-sources`` had to narrow. ``molt.config`` runs **before** any
        version source does and may not drive one (it is a lazy facade, and resolution opens a file
        per member), so this rule can only ask the manifest. A package that declares
        ``dynamic = ["version"]`` is therefore treated as **not** skipped: the version may well
        resolve, and refusing to load the configuration of a workspace whose members use hatch's
        ``__about__.py`` idiom would make a correct project unusable.

        The accurate check still happens, one layer up and with a real resolution:
        ``molt.commands.version._unskipped_dependents`` asks
        :func:`molt.engine.skipped_package_names` the same question against
        :func:`molt.ecosystem.resolve_workspace_versions`'s answer, and refuses there. So a dynamic
        package molt genuinely cannot resolve is still caught -- at the point where molt knows it,
        rather than guessed at here.
        """
        if package.normalized_name in ignored:
            return True
        if package.private and not version_private:
            return True
        return package.version is None and not package.dynamic_version

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
