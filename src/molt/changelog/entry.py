"""One changelog entry: a version heading, its bump sections, and the newline clamp.

Ports ``packages/apply-release-plan/src/get-changelog-entry.ts`` (research doc 04 sections
3.1-3.2; research README section 3.3). Both functions here are pure: no filesystem, no network,
no git. Everything a changelog generator contributes arrives as an already-rendered string, which
is what lets the conformance suite pin the exact bytes.

The newline clamp is why this layer exists
------------------------------------------
changesets emits markdown with broken blank lines and relies on a prettier pass to clean it up.
molt's ``format`` defaults to false and research README section 5 item 13 makes "correct markdown,
the first time" a differentiator, so the spacing rule lives here rather than downstream::

    start with new_lines = 2 (the blank line under the section heading);
    per line: add that line's *leading* newline count, clamp the total to [1, 2], emit that many
    newlines, then the *trimmed* line; carry that line's *trailing* newline count forward.

That arithmetic is the whole reason the ``git`` generator's bare ``"- ..."`` lines come out
adjacent while the ``github`` generator's ``"\\n\\n- ...\\n"`` lines come out blank-line separated,
from one assembler with no generator-specific branching (research doc 04 section 3.2).

Deliberate divergences from upstream
------------------------------------
1. **Whitespace-only release lines are dropped.** Upstream filters on JavaScript truthiness
   (``get-changelog-entry.ts:126``), so a line of spaces survives, consumes a gap, contributes no
   text, and welds two gaps into a three-newline run -- breaking the very clamp the function
   exists to enforce. molt filters on the *stripped* line, which makes the ``[1, 2]`` bound
   unconditional for arbitrary generator output.

2. **A summary is literal prose.** Upstream's post-processing strips every line beginning with
   ``#``, destroying headings inside a changeset body. Nothing here rewrites author text at all
   (design D7), so headings and regex metacharacters both survive byte-for-byte.

3. **An unconstrained dependency is never listed.** Upstream's ``*`` passes ``validRange`` and is
   recorded under "Updated dependencies" even though ``version-package.ts:93-102`` refuses to
   rewrite it -- the changelog claims an update the manifest never made. molt keeps the two
   consistent.

4. **A ``none`` bump has no section.** :func:`generate_markdown_for_version_type` refuses it with
   a :class:`ValueError` rather than growing a fourth heading, because ``None`` already means
   "this section is empty, omit it" and cannot also mean "there is no such section".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from packaging.requirements import InvalidRequirement, Requirement

from molt.names import normalize_name
from molt.versioning import BumpType, is_unconstrained, satisfies

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

__all__ = [
    "ChangelogGenerator",
    "ChangelogSections",
    "ChangesetLike",
    "ChangesetReleaseLike",
    "DependencyReleaseLike",
    "ReleaseLike",
    "collect_changelog_sections",
    "generate_markdown_for_version_type",
    "get_changelog_entry",
    "normalize_lines",
]

#: The section heading for each bump type that has one, in the order they render
#: (``get-changelog-entry.ts:112-118``). ``none`` is absent on purpose -- see
#: :func:`_section_heading`. Level three is fixed: the file title is ``#`` and the version heading
#: is ``##`` (research doc 04 section 3.1).
_HEADINGS: dict[BumpType, str] = {
    BumpType.MAJOR: "### Major Changes",
    BumpType.MINOR: "### Minor Changes",
    BumpType.PATCH: "### Patch Changes",
}

#: The clamp's bounds. One newline is the floor because zero would weld two bullets into one; two
#: is the ceiling because three is the blank-line run a formatter pass would have to remove.
_MIN_GAP = 1
_MAX_GAP = 2

#: The gap the clamp starts from, which is what makes the blank line under a section heading
#: unconditional -- and therefore what makes ``### ...`` a Markdown heading rather than a paragraph
#: of literal hashes.
_HEADING_GAP = 2

#: Sections are joined by a blank line, and so is the version heading to the first section
#: (``get-changelog-entry.ts:117``).
_SECTION_SEPARATOR = "\n\n"


# ======================================================================================
# The shapes this layer reads, structurally
# ======================================================================================


class ChangesetReleaseLike(Protocol):
    """One ``package: bump`` row of a changeset's frontmatter.

    Satisfied by :class:`molt.changeset.Release`. ``name`` is the author's literal spelling; every
    comparison here normalizes it (PEP 503), and nothing here rewrites it.
    """

    @property
    def name(self) -> str: ...
    @property
    def type(self) -> BumpType: ...


class ChangesetLike(Protocol):
    """A parsed changeset. Satisfied by :class:`molt.changeset.Changeset`."""

    @property
    def id(self) -> str: ...
    @property
    def summary(self) -> str: ...
    @property
    def releases(self) -> Sequence[ChangesetReleaseLike]: ...


class ReleaseLike(Protocol):
    """One planned release. Satisfied by :class:`molt.engine.Release`.

    ``changesets`` holds every changeset id that named the package, including ``none`` ones; it is
    what the dependency line's "one bullet per relevant changeset" rule is computed from.
    """

    @property
    def name(self) -> str: ...
    @property
    def type(self) -> BumpType: ...
    @property
    def new_version(self) -> Version: ...
    @property
    def changesets(self) -> Sequence[str]: ...


@runtime_checkable
class DependencyReleaseLike(Protocol):
    """The **minimum** a generator may read off an element of ``dependencies_updated``.

    Owner ruling 2026-07-31 (session 6), closing gap ``CE-5``: the second argument of
    :meth:`ChangelogGenerator.get_dependency_release_line` had no declared shape at all, so
    ``new_version`` was a :class:`~packaging.version.Version` from the real assembler and a plain
    ``str`` from a test double, and nothing noticed -- the two render identically through an
    f-string.

    Two members and no more. :class:`ReleaseLike` satisfies this structurally, which is why the
    assembler is free to keep passing the richer objects it already has; what a generator may
    **rely** on is this pair.

    It is deliberately not written into ``get_dependency_release_line``'s signature -- protocol
    method parameters are contravariant, so naming it there would break every third-party generator
    that annotates its own element class. Annotating against it is optional and does not break the
    seam (design D9).

    ``@runtime_checkable`` so a generator (and the conformance suite) can assert the shape it was
    handed. Like every runtime-checkable protocol it checks member *presence*, not member types --
    the ``Version`` half is a static claim, and ``tests/apply/test_changelog_entry.py`` asserts it
    separately.
    """

    @property
    def name(self) -> str: ...
    @property
    def new_version(self) -> Version: ...


class ChangelogGenerator(Protocol):
    """The changelog generator seam -- four parameters, always supplied (design D4).

    Declared in ``website/docs/extending/changelog-plugins.md`` under "The contract" and
    implemented that way in ``website/docs/extending/custom-generators.md``. The arity is
    load-bearing: a three-parameter signature cannot drive the forge-backed ``github`` generator,
    whose whole job is turning a ``forge`` into commit and pull-request links, so an assembler
    built against one is structurally unable to produce a GitHub changelog. A generator that needs
    neither ``options`` nor ``forge`` -- the default ``git`` generator -- simply ignores them, so
    the contract costs nothing.

    The two data positions are typed :data:`~typing.Any` rather than :class:`ChangesetLike` /
    :class:`ReleaseLike` on purpose, and it is not laziness. Protocol *method parameters* are
    contravariant, so naming a protocol there would oblige every implementation to annotate that
    parameter with molt's protocol type -- not with its own changeset class, and not with ``object``
    either -- or silently fail to satisfy this one. That would make the seam a straitjacket for
    exactly the third-party generators it exists to admit. What this protocol pins is the **call
    shape**: two methods, four parameters each, in this order, with ``options`` and ``forge`` last
    (design D4). What the assembler passes into those positions is pinned where it reads the data,
    by :class:`ChangesetLike` and :class:`ReleaseLike` above.
    """

    def get_release_line(
        self,
        changeset: Any,
        bump: BumpType,
        options: Mapping[str, object] | None,
        forge: object | None,
    ) -> str: ...

    def get_dependency_release_line(
        self,
        changesets: Any,
        dependencies_updated: Any,
        options: Mapping[str, object] | None,
        forge: object | None,
    ) -> str:
        """One "Updated dependencies" line, or the empty string.

        ``dependencies_updated`` is typed :data:`~typing.Any` for the contravariance reason the
        class docstring gives for the changeset position, and for no other reason. What its
        elements actually guarantee is written down as :class:`DependencyReleaseLike` -- ``name``
        and a parsed ``new_version``, nothing else (owner ruling 2026-07-31, gap ``CE-5``) -- and a
        generator that prefers to annotate its parameter with that protocol still satisfies this
        one, because ``Any`` is compatible in both directions.
        """
        ...


# ======================================================================================
# The section renderer and its clamp (``get-changelog-entry.ts:122-149``)
# ======================================================================================


def generate_markdown_for_version_type(bump: BumpType, lines: Sequence[str]) -> str | None:
    """Render one ``### <Bump> Changes`` section, or ``None`` when it has no content.

    ``None`` is load-bearing downstream: :func:`get_changelog_entry` pushes a dependency line into
    the patch bucket unconditionally (``get-changelog-entry.ts:95-103``), so without it most
    entries would carry a bare ``### Patch Changes`` heading with nothing under it.

    Assembly is a **single clamping pass** over a list of parts (design D1), not the rule applied
    at each concatenation site. Applying it per site is how a whitespace-only line escapes: it
    consumes one gap, renders nothing, and the next gap runs straight into the previous one.

    Newlines are written as ``\\n`` only, never ``os.linesep`` -- whoever writes the file decides
    line endings, and building an entry from the platform separator would make the same release
    produce different bytes on Windows.

    Raises :class:`ValueError` for ``BumpType.NONE``: there is no ``### None Changes`` heading
    anywhere, and ``None`` is already this function's answer for "empty section, omit it".
    """
    heading = _section_heading(bump)

    # Divergence 1: the filter is on the *stripped* line. See the module docstring.
    kept = [line for line in lines if line.strip()]
    if not kept:
        return None

    parts = [heading]
    gap = _HEADING_GAP
    for line in kept:
        gap = min(max(gap + _leading_newlines(line), _MIN_GAP), _MAX_GAP)
        # The trim is what lets a generator express preferred spacing through leading and trailing
        # newlines without that whitespace reaching the file; `normalize_lines` then applies the
        # same [0, 1] blank-line rule *inside* the line -- see its own docstring for why.
        parts.append("\n" * gap)
        parts.append(normalize_lines(line.strip()))
        gap = _trailing_newlines(line)
    return "".join(parts)


def normalize_lines(text: str) -> str:
    """Collapse blank-line runs and strip trailing whitespace, line by line.

    The interior half of the clamp, and the one place both rendering paths share it. Owner ruling
    2026-07-30, closing gap ``CT-6``: the outer clamp works *between* release lines and never
    reaches inside one, so a summary containing two consecutive blank lines used to emit exactly
    the three-newline run this module's docstring says the clamp exists to prevent -- while
    :func:`molt.changelog.render_changelog` collapsed it. Design D3 requires the two paths to
    agree; before this they only agreed for summaries that happened to be single-paragraph.

    Interior indentation is preserved (a nested ``  - pkg@1.2.3`` is structure, not stray
    whitespace) and no substitution is used, so a ``\\1`` or a ``$&`` living in an author's summary
    cannot be expanded (research README section 3.4).
    """
    kept: list[str] = []
    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line and (not kept or not kept[-1]):
            # Drop a blank line that opens the text or follows another blank line; both bounds fall
            # out of the one condition.
            continue
        kept.append(line)
    return "\n".join(kept).strip("\n")


def _section_heading(bump: BumpType) -> str:
    """The heading for ``bump``, refusing ``none`` deliberately rather than by ``KeyError``.

    A bare ``_HEADINGS[bump]`` lookup would raise too, but only by accident of dict indexing --
    any later refactor to ``.get(bump, ...)`` would silently grow a fourth section. The guard is
    written on purpose so the failure names the bump and stays legible to whoever hits it.
    """
    heading = _HEADINGS.get(bump)
    if heading is None:
        raise ValueError(
            f'There is no changelog section for a "{bump.value}" bump. A "none" release is a '
            "changelog entry without a version change, not a fourth section."
        )
    return heading


def _leading_newlines(line: str) -> int:
    return len(line) - len(line.lstrip("\n"))


def _trailing_newlines(line: str) -> int:
    return len(line) - len(line.rstrip("\n"))


# ======================================================================================
# The four buckets both rendering paths are built from
# ======================================================================================


@dataclass(frozen=True)
class ChangelogSections:
    """The rendered lines behind one changelog entry, grouped and not yet laid out.

    Four buckets, and the fourth one is the point: **dependency lines are their own collection**
    (design D4). :func:`get_changelog_entry` owns the headings and therefore has nowhere to put a
    dependency line except inside patch (``get-changelog-entry.ts:95-103``), but that is a
    *presentation* decision, not a structural fact. Handing the two premerged to a template would
    make "move dependency bumps into their own section"
    (``website/docs/guide/changelog-templates.md``) impossible, which is half of what templating is
    for.

    Every line is the generator's return value **verbatim**, leading and trailing newlines included.
    Those newlines are the generator's spacing hint -- the whole difference between
    ``molt.changelog.git``'s ``"- x"`` and a github-style ``"\\n\\n- x\\n"`` lives in them -- so
    trimming here would erase the distinction before either renderer could act on it. What *is*
    filtered out is a line with no text at all: the section renderer drops those anyway, and keeping
    them would make an empty bucket look populated to a template's ``{% if %}``.
    """

    major: tuple[str, ...]
    minor: tuple[str, ...]
    patch: tuple[str, ...]
    dependencies: tuple[str, ...]


def collect_changelog_sections(
    release: ReleaseLike,
    releases: Sequence[ReleaseLike],
    changesets: Sequence[ChangesetLike],
    generator: ChangelogGenerator,
    *,
    deps: Sequence[str] = (),
    dev_deps: Sequence[str] = (),
    optional_deps: Sequence[str] = (),
    update_internal_dependencies: BumpType = BumpType.PATCH,
    options: Mapping[str, object] | None = None,
    forge: object | None = None,
) -> ChangelogSections:
    """Ask ``generator`` for every line this release's entry needs, bucketed by bump type.

    Extracted so :func:`get_changelog_entry` and :func:`molt.changelog.render_changelog` are two
    layouts over **one** set of lines rather than two independent computations of the same thing.
    Design D3 requires the default template to agree with the assembler byte for byte; sharing the
    collection step makes that agreement structural instead of a coincidence two conformance suites
    have to keep verifying.

    ``get_dependency_release_line`` is called exactly **once**, unconditionally, whatever this
    release's own bump type is and whether or not anything moved. That is the generator contract
    upstream established (``get-changelog-entry.ts:95-103``) and a generator may count on it.

    ``dev_deps`` and ``optional_deps`` are accepted and never read, exactly as
    :func:`get_changelog_entry` documents: taking all three is what makes "dev and optional
    dependencies are invisible in the changelog" a rule this layer enforces (design D6) rather than
    an accident of what the caller forwarded.
    """
    del dev_deps, optional_deps

    lines: dict[BumpType, list[str]] = {bump: [] for bump in _HEADINGS}
    own = normalize_name(release.name)
    for changeset in changesets:
        bump = _requested_bump(changeset, own)
        if bump is None or bump is BumpType.NONE:
            # Absent: the changeset does not release this package (``:44``). ``none``: there is no
            # section for it to land in (``:45``), so the summary is simply not rendered here.
            continue
        lines[bump].append(generator.get_release_line(changeset, bump, options, forge))

    updated = _dependencies_updated(
        releases=releases,
        deps=deps,
        update_internal_dependencies=update_internal_dependencies,
    )
    relevant = _relevant_changesets(changesets, updated)
    # Narrowed at the call site rather than in the protocol's signature (design D9, gap `CE-5`).
    # `ReleaseLike` satisfies `DependencyReleaseLike` structurally, so nothing changes at runtime
    # -- but a later change that has a generator read `.type` or `.changesets` off a dependency
    # element now fails pyrefly here, instead of working for the two built-ins and breaking every
    # third-party plugin.
    elements: Sequence[DependencyReleaseLike] = updated
    dependency_line = generator.get_dependency_release_line(relevant, elements, options, forge)

    return ChangelogSections(
        major=tuple(_with_text(lines[BumpType.MAJOR])),
        minor=tuple(_with_text(lines[BumpType.MINOR])),
        patch=tuple(_with_text(lines[BumpType.PATCH])),
        dependencies=tuple(_with_text([dependency_line])),
    )


def _with_text(lines: Sequence[str]) -> list[str]:
    """``lines`` minus the entries that carry no text.

    The filter is on the **stripped** line, matching :func:`generate_markdown_for_version_type` --
    see divergence 1 in the module docstring. It is what lets "no dependency moved" be expressed as
    the empty string by every generator, and what keeps a whitespace-only line from welding two gaps
    into a three-newline run.
    """
    return [line for line in lines if line.strip()]


# ======================================================================================
# The entry assembler (``get-changelog-entry.ts:24-120``)
# ======================================================================================


def get_changelog_entry(
    release: ReleaseLike,
    releases: Sequence[ReleaseLike],
    changesets: Sequence[ChangesetLike],
    generator: ChangelogGenerator,
    *,
    deps: Sequence[str] = (),
    dev_deps: Sequence[str] = (),
    optional_deps: Sequence[str] = (),
    update_internal_dependencies: BumpType = BumpType.PATCH,
    options: Mapping[str, object] | None = None,
    forge: object | None = None,
) -> str | None:
    """Assemble one ``## <version>`` changelog entry, or ``None`` when there is nothing to write.

    ``releases`` is the whole plan and ``changesets`` the whole plan's changeset list, both in
    release-plan order; the filters below are what keep another package's summary out of this
    entry. Order is preserved end to end, because the plan is insertion-ordered (research README
    section 3.1) and that order is what makes a changelog diff reviewable.

    A ``none`` release returns ``None`` -- and therefore writes no ``CHANGELOG.md`` at all
    (``get-changelog-entry.ts:31``). The consequence is larger than an empty section: an
    ``## <version>`` heading for a version that was never published must not appear.

    The three dependency sections arrive as sequences of PEP 508 requirement strings, using the
    same ``deps`` / ``dev_deps`` / ``optional_deps`` vocabulary as the workspace builder. All three
    are taken rather than only the runtime ones so that "dev and optional dependencies are
    invisible in the changelog" is a rule this function *enforces* (design D6) instead of an
    accident of what the caller chose to forward.

    A ``[tool.uv.sources]`` workspace marker is **not** a PEP 508 requirement and must be resolved
    by the caller -- :func:`molt.engine.resolve_workspace_range` -- into a specifier before it
    reaches ``deps``. That is what "after workspace-source resolution" in design D3 means here: the
    resolution happens upstream, and this function sees only its result.

    ``options`` and ``forge`` are pure pass-through. Nothing here reads either; both are handed to
    the generator on **every** call, to both methods (design D4).
    """
    if release.type is BumpType.NONE:
        return None

    sections = collect_changelog_sections(
        release,
        releases,
        changesets,
        generator,
        deps=deps,
        dev_deps=dev_deps,
        optional_deps=optional_deps,
        update_internal_dependencies=update_internal_dependencies,
        options=options,
        forge=forge,
    )
    # Design D5: the dependency line is **appended** to the patch section, after every direct
    # line, whatever this release's own bump type is. "Always last, always patch" is a positional
    # rule; implementing it with a sort key invites a tie-break that reorders the direct lines.
    # This is where the no-template path spends the freedom :class:`ChangelogSections` preserves.
    lines: dict[BumpType, Sequence[str]] = {
        BumpType.MAJOR: sections.major,
        BumpType.MINOR: sections.minor,
        BumpType.PATCH: (*sections.patch, *sections.dependencies),
    }

    parts = [f"## {release.new_version}"]
    for bump in _HEADINGS:
        section = generate_markdown_for_version_type(bump, lines[bump])
        if section is not None:
            parts.append(section)
    # With every section empty this leaves one element, so the entry is just the version heading --
    # reachable in practice for a package pulled into a release by ``fixed`` whose own changesets
    # are all ``none`` and whose dependency line came back empty.
    return _SECTION_SEPARATOR.join(parts)


def _requested_bump(changeset: ChangesetLike, normalized: str) -> BumpType | None:
    """The bump ``changeset`` asked for on this package, or ``None`` if it names it at all.

    Both sides are normalized (PEP 503): a changeset written against ``Foo_Bar`` has to resolve
    against a release the plan calls ``foo-bar``. One forgotten call is a summary silently missing
    from a changelog.
    """
    for requested in changeset.releases:
        if normalize_name(requested.name) == normalized:
            return requested.type
    return None


# ======================================================================================
# The dependency line (research doc 04 section 3.1; research README section 3.3)
# ======================================================================================


def _dependencies_updated(
    *,
    releases: Sequence[ReleaseLike],
    deps: Sequence[str],
    update_internal_dependencies: BumpType,
) -> list[ReleaseLike]:
    """The released dependencies this package's changelog should name, in manifest order.

    **Design D3 -- the filter rule, stated once.** A dependency is listed **iff its edge is
    version-constrained after workspace-source resolution**; equivalently, iff the edge *could
    have forced* this release. It is explicitly **not** "iff the pin text changed". The byte-diff
    reading is the intuitive one and it is wrong: it makes ``tests/cli/test_version.py`` row 27
    red, and it is recorded as having previously been stated that way in
    ``tests/apply/test_apply.py``'s
    ``test_an_unconstrained_dependency_is_not_listed_in_the_changelog_either`` docstring.

    Only ``deps`` reaches this function. Dev and optional edges are filtered by **kind, first**
    (design D6), before any name matching, so a dev edge sharing a name with a runtime edge cannot
    leak a line -- and so a package can be bumped through an optional edge without its changelog
    naming the dependency, which is the changelog rule only and does not contradict the engine's
    decision that extras propagate like runtime dependencies.

    Two rules ratified as shipped on 2026-07-31 (session 6), each previously unpinned:

    * **Order is manifest order** (``CE-3``): the loop walks ``deps`` as the manifest declares
      them, never the release plan's order. A manifest is a document its author arranged, and a
      changelog that reordered it would read as though molt knew better.
    * **There is no self-dependency guard** (``CE-4``): a package that declares itself in ``deps``
      appears in its own "Updated dependencies" line, exactly as upstream would. A guard was
      considered and declined -- it would hide a manifest mistake behind a changelog that looks
      right.
    """
    by_name = {normalize_name(candidate.name): candidate for candidate in releases}
    updated: list[ReleaseLike] = []
    for declared in deps:
        constraint = _live_constraint(declared)
        if constraint is None:
            continue
        name, specifier = constraint
        dependency = by_name.get(name)
        if dependency is None or dependency.type is BumpType.NONE:
            continue
        if not _should_update_dependency(dependency, specifier, update_internal_dependencies):
            continue
        updated.append(dependency)
    return updated


def _live_constraint(declared: str) -> tuple[str, SpecifierSet] | None:
    """The normalized name and specifier of a declaration that can go stale, else ``None``.

    Three shapes carry no live constraint, and the manifest is left untouched for all three
    (research doc 04 section 2.4 rules 1, 4 and 6), so the changelog has nothing to report:

    - **Not a requirement at all** -- the PEP 508 analogue of upstream's ``bulbasaur`` row.
    - **A direct reference** (``pkg @ file:///...``, ``pkg @ git+https://...``) -- upstream's
      ``validRange(versionRange) != null`` guard (``get-changelog-entry.ts:62``) skips npm dist
      tags and ``file:`` / ``link:`` protocols, and a direct reference is the Python analogue.
    - **An unconstrained requirement** -- divergence 3 in the module docstring: satisfied by every
      version, so it "never triggers a bump" and is never recorded either.

    The name is normalized (PEP 503) and returned separately, because matching happens on the
    normalized form while the bullet renders the **release's** own declared spelling (design D8).
    """
    try:
        requirement = Requirement(declared)
    except InvalidRequirement:
        return None
    if requirement.url is not None:
        return None
    if is_unconstrained(requirement.specifier):
        return None
    return normalize_name(requirement.name), requirement.specifier


def _should_update_dependency(
    dependency: ReleaseLike, specifier: SpecifierSet, minimum: BumpType
) -> bool:
    """Port of ``shouldUpdateDependencyBasedOnConfig`` (``utils.ts:69-75``).

    Leaving the declared range always wins over the minimum gate (``:69-72``): an exact pin is
    invalidated by any bump at all, however small. Otherwise the dependency's own bump has to reach
    ``update_internal_dependencies`` (``:74-75``).

    The changelog and the manifest share this predicate deliberately (research doc 04 sections
    2.5 / 3.1). A dependency line claiming an update the manifest did not make would be a lie in a
    published artifact.
    """
    if not satisfies(dependency.new_version, specifier):
        return True
    return dependency.type.rank >= minimum.rank


def _relevant_changesets(
    changesets: Sequence[ChangesetLike], updated: Sequence[ReleaseLike]
) -> list[ChangesetLike]:
    """The changesets behind ``updated``, in plan order (``get-changelog-entry.ts:83-93``).

    One bullet per relevant changeset is what lets the ``github`` generator attribute a dependency
    bump to the right commits, and it must not include changesets belonging to a dependency that
    was filtered out -- hence the filter runs over ``updated``, not over ``releases``.
    """
    ids = {identifier for dependency in updated for identifier in dependency.changesets}
    return [changeset for changeset in changesets if changeset.id in ids]
