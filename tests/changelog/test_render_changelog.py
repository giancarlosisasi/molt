"""Conformance tests for ``render_changelog`` -- molt's Jinja2 **entry** template.

This is the seam nothing else in the P4 slice covers. Before this file,
``grep -rn "render_changelog|release.dependencies|config.dates" tests/`` returned nothing,
even though the P4 shared brief (section 8) lists ``render_changelog(...)`` as a
``molt.changelog`` seam and research README section 5 item 5 makes it a headline feature --
changelog dates are changesets issue #109, open seven years with a pull request that waited
six of them, and the blocker upstream is structural: the entry layout is welded into
``get-changelog-entry.ts:111-118`` with no seam for a user to change it.

Two Jinja2 template layers, and they are different seams
---------------------------------------------------------
``tests/changelog/test_render_template.py`` covers the **line** template -- the upstream
``render-template.ts`` per-bullet token engine that lives in a *generator's* options table
(``changelog = ["github", { template = "..." }]``) and shapes one bullet.

This file covers the **entry** template -- ``[tool.molt.changelog] template = "..."``,
documented in ``website/docs/guides/changelog-templates.md``, which shapes everything
*around* the bullets: the ``## <version>`` heading, the section headings, their order, and
dates. ``website/docs/extending/changelog-plugins.md`` states the split under "The contract":
"You return lines, not layout."

The oracle for this file is ``website/docs/guides/changelog-templates.md`` in full. There is
no upstream counterpart to port, because upstream has none -- that is the point.

The seam this file designs against (a TDD target; build step 7)
----------------------------------------------------------------
::

    render_changelog(
        release, releases, changesets, generator, *,
        template=None, config=None, date=None,
        deps=(), dev_deps=(), optional_deps=(),
        update_internal_dependencies=BumpType.PATCH, options=None, forge=None,
    ) -> str | None

Everything from ``deps`` rightward is spelled exactly as ``get_changelog_entry`` is pinned in
``tests/apply/test_changelog_entry.py``, deliberately: the two functions answer the same
question about the same inputs and must not drift apart. The three additions:

``template: str | None``
    The Jinja2 **source text**, not a path. The docs spell the config value as a filename
    (``template = "changelog-entry.md.jinja"``), so *something* reads that file -- but making
    it this function's job would put filesystem I/O inside a pure renderer and force every
    test here through a tmp workspace. Resolution belongs to the config layer.
    ADAPTATION DECISION, flagged for the owner.
``config``
    What the template sees as ``config``. The docs example uses ``{% if config.dates %}``
    while the config snippet declares ``dates`` under ``[tool.molt.changelog]``, so ``config``
    in the template context is the **changelog section**, not the whole ``[tool.molt]`` table.
    ADAPTATION DECISION -- if ``config`` is meant to be the whole molt config, the documented
    example is wrong and would need ``config.changelog.dates``.
``date: datetime | None``
    Surfaces as ``release.date``. The docs describe it as "a ``datetime`` for this release
    (one timestamp per ``version`` run)", and ``molt.engine.Release`` carries no date
    (P4 shared brief section 8), so the one-per-run timestamp is injected here rather than
    read off the release. This is what the ``frozen_clock`` fixture exists for.

The architecture question this file is written to survive
----------------------------------------------------------
``get_changelog_entry`` is currently pinned (in ``tests/apply/test_changelog_entry.py``) as
returning a finished ``str`` with the headings baked in and the dependency line **merged into
the patch bucket**. The docs page hands a template ``release.patch`` and ``release.dependencies``
as **separate** lists and says at the end of "Customizing the template" that "the section
order and headings live in the template". Both cannot own the headings. Two resolutions:

(a) ``get_changelog_entry`` becomes bucket-producing (e.g. ``ChangelogSections(major, minor,
    patch, dependencies)``) and ``render_changelog`` owns every heading; or
(b) ``get_changelog_entry`` stays the no-template default path and ``render_changelog`` is a
    parallel seam that must agree with it.

This file holds under both. Every assertion is against a hand-written literal, so the pinned
*output* is independent of which function produces it. Exactly one test --
``test_the_default_template_agrees_with_get_changelog_entry`` -- also calls
``get_changelog_entry``, and it is the single test the owner retargets if (a) is chosen.

research README section 5 item 5; P4 shared brief sections 8 and 9;
``website/docs/guides/changelog-templates.md``.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from packaging.version import Version

from molt.versioning import BumpType

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from tests.conftest import FrozenClock

#: This suite guards on the **seam**, not the package.
#:
#: ``pytest.importorskip("molt.changelog")`` was the original guard, on the usual assumption that
#: "the module exists" means "the behavior is implemented". The ``implement-changelog-entry`` change
#: broke that assumption the same way ``implement-toml-editing`` broke it for ``molt.apply`` (see
#: the note above that suite's guard in ``tests/apply/test_apply.py``): ``molt.changelog`` had to
#: exist to hold the entry assembler, which lands a build step before ``render_changelog`` does.
#: Asking the package for the attribute keeps this module a clean *skip* rather than a collection
#: error. The two sibling suites in this directory need no such change -- they already guard on
#: ``molt.changelog.template`` and ``molt.changelog.git`` / ``.github``, which do not exist yet.
if not hasattr(
    pytest.importorskip(
        "molt.changelog", reason="build step 7 - changelog not yet implemented (TDD target)"
    ),
    "render_changelog",
):
    pytest.skip(
        "molt.changelog.render_changelog not yet implemented (TDD target); "
        "molt.changelog currently holds only the entry assembler",
        allow_module_level=True,
    )

from molt.changelog import (
    get_changelog_entry,
    render_changelog,  # pyrefly: ignore[missing-module-attribute]
)

pytestmark = pytest.mark.unit

PATCH = BumpType.PATCH
MINOR = BumpType.MINOR
MAJOR = BumpType.MAJOR
NONE = BumpType.NONE


# ======================================================================================
# Local test doubles -- the same shapes tests/apply/test_changelog_entry.py uses.
#
# Redeclared rather than imported: that module is `pytest.importorskip`-guarded, so importing
# from it would make this file's collection depend on another guarded module's import state.
# ======================================================================================


@dataclasses.dataclass(frozen=True)
class ChangesetRelease:
    """One ``name: bump`` row of a changeset's frontmatter."""

    name: str
    type: BumpType


@dataclasses.dataclass(frozen=True)
class Changeset:
    """``molt.changeset.Changeset`` stand-in: ``.id``, ``.summary``, ``.releases``."""

    id: str
    summary: str
    releases: tuple[ChangesetRelease, ...]


@dataclasses.dataclass(frozen=True)
class Release:
    """``molt.engine.Release`` stand-in. Note: no ``.date`` -- see the module docstring."""

    name: str
    type: BumpType
    old_version: Version
    new_version: Version
    changesets: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ChangelogConfig:
    """Stand-in for the ``[tool.molt.changelog]`` table the template sees as ``config``."""

    dates: bool = False
    template: str | None = None


class GitStyleGenerator:
    """``molt.changelog.git``: bare ``- ...`` lines, no leading or trailing newline hints."""

    def get_release_line(
        self,
        changeset: Changeset,
        bump: BumpType,
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        first, *rest = (line.rstrip() for line in changeset.summary.split("\n"))
        line = f"- {first}"
        if rest:
            line += "\n" + "\n".join(f"  {text}" if text else "" for text in rest)
        return line

    def get_dependency_release_line(
        self,
        changesets: Sequence[Changeset],
        dependencies_updated: Sequence[Release],
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        if not dependencies_updated:
            return ""
        links = ["- Updated dependencies" for _ in changesets]
        listed = [f"  - {dep.name}@{dep.new_version}" for dep in dependencies_updated]
        return "\n".join(links + listed)


class GitHubStyleGenerator:
    """``molt.changelog.github``: ``"\\n\\n- ...\\n"``, the OTHER spacing shape.

    Its leading and trailing newlines are *hints* that the newline clamp
    (``get-changelog-entry.ts:122-149``) turns into blank-line-separated bullets. They are the
    reason this file pins that the template context carries generator output verbatim.
    """

    def get_release_line(
        self,
        changeset: Changeset,
        bump: BumpType,
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        return f"\n\n- {changeset.summary}\n"

    def get_dependency_release_line(
        self,
        changesets: Sequence[Changeset],
        dependencies_updated: Sequence[Release],
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        if not dependencies_updated:
            return ""
        listed = "\n".join(f"  - {dep.name}@{dep.new_version}" for dep in dependencies_updated)
        return f"- Updated dependencies:\n{listed}"


@dataclasses.dataclass
class RecordingGenerator:
    """Records the ``(options, forge)`` pairs the renderer forwards."""

    release_calls: list[tuple[Mapping[str, object] | None, object | None]] = dataclasses.field(
        default_factory=list
    )
    dependency_calls: list[tuple[Mapping[str, object] | None, object | None]] = dataclasses.field(
        default_factory=list
    )

    def get_release_line(
        self,
        changeset: Changeset,
        bump: BumpType,
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        self.release_calls.append((options, forge))
        return f"- {changeset.summary}"

    def get_dependency_release_line(
        self,
        changesets: Sequence[Changeset],
        dependencies_updated: Sequence[Release],
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        self.dependency_calls.append((options, forge))
        if not dependencies_updated:
            return ""
        listed = "\n".join(f"  - {dep.name}@{dep.new_version}" for dep in dependencies_updated)
        return f"- Updated dependencies\n{listed}"


class StubForge:
    """A ``molt.forge.Forge`` placeholder; identity is all these tests need from it."""


GIT = GitStyleGenerator()
GITHUB = GitHubStyleGenerator()
NO_DATES = ChangelogConfig()


def v(text: str) -> Version:
    return Version(text)


# ======================================================================================
# The documentation fixture -- website/docs/guides/changelog-templates.md, verbatim
# ======================================================================================

#: ``changelog-templates.md`` lines 71-88, copied character for character. Reformatting this
#: string is a documentation change; treat it as a fixture, not as code to tidy.
DOCUMENTED_TEMPLATE = """\
## {{ release.new_version }}{% if config.dates %} ({{ release.date.strftime("%Y-%m-%d") }})\
{% endif %}

{% if release.major %}### Major Changes

{% for line in release.major %}{{ line }}
{% endfor %}
{% endif %}{% if release.minor %}### Minor Changes

{% for line in release.minor %}{{ line }}
{% endfor %}
{% endif %}{% if release.patch or release.dependencies %}### Patch Changes

{% for line in release.patch %}{{ line }}
{% endfor %}{% for line in release.dependencies %}{{ line }}
{% endfor %}
{% endif %}"""

#: ``changelog-templates.md`` lines 92-104 -- what the page says the template above renders.
DOCUMENTED_OUTPUT = (
    "## 2.1.0 (2026-07-23)\n"
    "\n"
    "### Minor Changes\n"
    "\n"
    "- Add streaming support to the export API\n"
    "\n"
    "### Patch Changes\n"
    "\n"
    "- Fix a crash when the input file is empty\n"
    "- Updated dependencies\n"
    "  - acme-core@1.4.1"
)

#: ``changelog-templates.md`` lines 17-29 -- "The default structure", i.e. the same entry
#: with no date. Identical to DOCUMENTED_OUTPUT minus the parenthesized date.
DEFAULT_STRUCTURE_OUTPUT = DOCUMENTED_OUTPUT.replace(" (2026-07-23)", "")

DOCUMENTED_DATE = datetime(2026, 7, 23)


def documented_fixture() -> tuple[Release, list[Release], list[Changeset], tuple[str, ...]]:
    """The exact release the docs page's worked example renders.

    ``acme-widgets`` takes a minor and a patch changeset of its own and depends on
    ``acme-core``, which is patched to 1.4.1 -- reproducing all three bullets on the page.
    """
    widgets = Release("acme-widgets", MINOR, v("2.0.0"), v("2.1.0"), ("cs-minor", "cs-patch"))
    core = Release("acme-core", PATCH, v("1.4.0"), v("1.4.1"), ("cs-core",))
    changesets = [
        Changeset(
            "cs-minor",
            "Add streaming support to the export API",
            (ChangesetRelease("acme-widgets", MINOR),),
        ),
        Changeset(
            "cs-patch",
            "Fix a crash when the input file is empty",
            (ChangesetRelease("acme-widgets", PATCH),),
        ),
        Changeset("cs-core", "Tighten a bound", (ChangesetRelease("acme-core", PATCH),)),
    ]
    return widgets, [widgets, core], changesets, ("acme-core>=1.4.0,<2.0.0",)


# ======================================================================================
# The compatibility constraint: the DEFAULT template is the documented default structure
# ======================================================================================


def test_the_default_template_renders_the_documented_default_structure() -> None:
    """``changelog-templates.md`` lines 13-29 -- "Out of the box, molt reproduces ...".

    ``template=None`` selects molt's built-in entry template, and its output is the block the
    page prints under "The default structure". This is the load-bearing compatibility
    constraint of the whole templating layer: making entry layout configurable must not change
    what an unconfigured project gets, or every changelog in every repo re-flows on upgrade.

    Note this is not merely "a template exists" -- the four rules the page calls load-bearing
    are all visible in the expected string: one ``## <version>`` heading, sections in Major /
    Minor / Patch order with empty ones omitted (there is no ``### Major Changes`` here), the
    dependency bullet last and inside Patch, and the nested dependency list indented by two.
    """
    release, releases, changesets, deps = documented_fixture()

    rendered = render_changelog(release, releases, changesets, GIT, deps=deps, config=NO_DATES)

    assert rendered == DEFAULT_STRUCTURE_OUTPUT


def test_the_default_template_agrees_with_get_changelog_entry() -> None:
    """The seam that keeps ``tests/apply/test_changelog_entry.py`` valid.

    ``get_changelog_entry`` is the no-template path and ``render_changelog(template=None)`` is
    the templated one; for the same inputs they must produce the same bytes, or a project would
    get a different changelog depending on whether it had ever set ``[tool.molt.changelog]``.

    THIS IS THE ONE TEST IN THIS FILE THAT DEPENDS ON THE ARCHITECTURE CHOICE described in the
    module docstring. Under resolution (a) -- ``get_changelog_entry`` becomes bucket-producing
    -- the second assertion moves to whatever the bucket type is, and nothing else in this file
    changes, because every other assertion here is against a hand-written literal.
    """
    release, releases, changesets, deps = documented_fixture()

    rendered = render_changelog(release, releases, changesets, GIT, deps=deps, config=NO_DATES)
    entry = get_changelog_entry(release, releases, changesets, GIT, deps=deps)

    assert rendered == DEFAULT_STRUCTURE_OUTPUT
    assert entry == DEFAULT_STRUCTURE_OUTPUT
    assert rendered == entry


def test_the_default_template_reconciles_both_generator_spacing_shapes() -> None:
    """The newline clamp survives the trip through Jinja2 (research doc 04 section 3.2).

    ``changelog-git`` returns ``"- x"`` and ``changelog-github`` returns ``"\\n\\n- x\\n"``;
    fed through one assembler with no generator-specific branching, the first comes out
    adjacent and the second blank-line separated. The templating layer has to preserve that,
    which is *why* the context carries generator output verbatim (see the test below): if molt
    pre-trimmed the lines before handing them to the template, the hint would be gone and both
    generators would render identically.
    """
    release = Release("pkg-a", MINOR, v("1.0.0"), v("1.1.0"), ("cs-1", "cs-2"))
    changesets = [
        Changeset("cs-1", "one", (ChangesetRelease("pkg-a", MINOR),)),
        Changeset("cs-2", "two", (ChangesetRelease("pkg-a", MINOR),)),
    ]

    git_entry = render_changelog(release, [release], changesets, GIT, config=NO_DATES)
    github_entry = render_changelog(release, [release], changesets, GITHUB, config=NO_DATES)

    assert git_entry == "## 1.1.0\n\n### Minor Changes\n\n- one\n- two"
    assert github_entry == "## 1.1.0\n\n### Minor Changes\n\n- one\n\n- two"


def test_a_none_release_renders_no_entry() -> None:
    """Same rule as ``get_changelog_entry`` (``get-changelog-entry.ts:31``): ``None``.

    P4 shared brief section 9: ``type == "none"`` means **no CHANGELOG.md write at all**, not
    an empty entry. A templating layer that returned ``""`` instead would make the caller write
    a file for a version that was never published -- the template has no say in this, because
    by the time a template could run the decision is already wrong.
    """
    release = Release("pkg-a", NONE, v("1.0.0"), v("1.0.0"))

    assert render_changelog(release, [release], [], GIT, config=NO_DATES) is None


# ======================================================================================
# The documented template context
# ======================================================================================

# Every key the docs page's field table names (changelog-templates.md lines 62-68), plus
# `config.dates` from the worked example (line 72) and the config snippet (line 56).
#
# The page prefixes the table with "The most useful fields", so it is explicitly
# NON-EXHAUSTIVE and this table pins a floor, not a ceiling. Keys the page implies but never
# names: `release.old_version`, `release.type` and `release.changesets` (implied by "The
# template receives the release for one package"), and every `config` key other than `dates`.
# Note also that the page spells the version `release.new_version`; `release.version` is NOT
# a documented spelling and is not pinned here.
CONTEXT_KEY_CASES = [
    # (template, expected, why)
    (
        "{{ release.name }}",
        "acme-widgets",
        "line 63: 'release.name -- The distribution name'",
    ),
    (
        "{{ release.new_version }}",
        "2.1.0",
        "line 64: 'release.new_version -- The version being released'. Rendered through str(), "
        "so a packaging.Version formats as the PEP 440 string a heading needs",
    ),
    (
        "{{ release.date.strftime('%Y-%m-%d') }}",
        "2026-07-23",
        "line 65: 'release.date -- A datetime for this release'. It must be a real datetime, "
        "not a preformatted string, or the .strftime() the page's own example calls fails",
    ),
    (
        "{{ release.major | join('#') }}",
        "",
        "line 66: 'release.major/.minor/.patch -- The rendered lines for each bump size'. "
        "Empty here, and empty as a LIST -- an implementation that used None for an empty "
        "section would blow up in the page's own `{% for line in release.major %}` loop",
    ),
    (
        "{{ release.minor | join('#') }}",
        "- Add streaming support to the export API",
        "line 66, the populated case",
    ),
    (
        "{{ release.patch | join('#') }}",
        "- Fix a crash when the input file is empty",
        "line 66 -- and note what is NOT here: the dependency line has its own key",
    ),
    (
        "{{ release.dependencies | join('#') }}",
        "- Updated dependencies\n  - acme-core@1.4.1",
        "line 67: 'release.dependencies -- The rendered dependency-bump lines'",
    ),
    (
        "{% if config.dates %}on{% else %}off{% endif %}",
        "on",
        "line 72 and line 56: config.dates is what the documented template branches on. It is "
        "the only `config` key the page uses, and it is spelled `config.dates`, NOT "
        "`config.changelog.dates` -- so `config` in the context is the [tool.molt.changelog] "
        "table, not the whole [tool.molt] one",
    ),
]


@pytest.mark.parametrize(("template", "expected", "why"), CONTEXT_KEY_CASES)
def test_the_documented_template_context_keys_are_available(
    template: str, expected: str, why: str
) -> None:
    """``changelog-templates.md`` lines 59-72 -- the public template surface.

    This is a documentation lock in the strict sense: every one of these keys appears in a
    published table or in the page's own worked example, so renaming any of them silently
    breaks user templates that the docs told people to write. Asserted one key per row so a
    failure names the key rather than diffing a wall of text.
    """
    release, releases, changesets, deps = documented_fixture()

    rendered = render_changelog(
        release,
        releases,
        changesets,
        GIT,
        deps=deps,
        template=template,
        config=ChangelogConfig(dates=True),
        date=DOCUMENTED_DATE,
    )

    assert rendered == expected, why


def test_dependency_lines_are_a_bucket_of_their_own_not_part_of_patch() -> None:
    """The structural fact that separates this seam from ``get_changelog_entry``.

    ``get_changelog_entry`` pushes the dependency line **into** the patch bucket
    (``get-changelog-entry.ts:95-103``) because it owns the headings and has nowhere else to
    put it. The template context cannot do that: the docs page hands ``release.patch`` and
    ``release.dependencies`` to the template separately and its example iterates them as two
    loops inside one ``### Patch Changes`` block. That separation is what lets a user "move
    dependency bumps into their own section" (line 106) -- impossible if they arrive premerged.

    Both directions are asserted, so an implementation that put the dependency line in *both*
    buckets (which would render it twice under the documented template) fails too.
    """
    release, releases, changesets, deps = documented_fixture()

    patch = render_changelog(
        release,
        releases,
        changesets,
        GIT,
        deps=deps,
        template="{{ release.patch | join('#') }}",
        config=NO_DATES,
    )
    dependencies = render_changelog(
        release,
        releases,
        changesets,
        GIT,
        deps=deps,
        template="{{ release.dependencies | join('#') }}",
        config=NO_DATES,
    )

    assert patch == "- Fix a crash when the input file is empty"
    assert "Updated dependencies" not in patch
    assert dependencies == "- Updated dependencies\n  - acme-core@1.4.1"


def test_the_context_carries_generator_output_verbatim() -> None:
    """The lines are the generator's return values, untrimmed -- hints and all.

    "The rendered lines for each bump size, in plan order" (line 66). Verbatim is not a
    stylistic choice, it is forced by
    ``test_the_default_template_reconciles_both_generator_spacing_shapes``: the difference
    between ``changelog-git`` and ``changelog-github`` output lives *entirely* in the leading
    and trailing newlines of the returned string, so a context that stripped them would erase
    the distinction before any template could act on it.

    Delimited with ``<``/``>`` rather than asserted through ``join`` so the newlines are
    visible in the failure diff.
    """
    release = Release("pkg-a", MINOR, v("1.0.0"), v("1.1.0"), ("cs-1",))
    changesets = [Changeset("cs-1", "one", (ChangesetRelease("pkg-a", MINOR),))]

    rendered = render_changelog(
        release,
        [release],
        changesets,
        GITHUB,
        template="{% for line in release.minor %}<{{ line }}>{% endfor %}",
        config=NO_DATES,
    )

    assert rendered == "<\n\n- one\n>"


def test_options_and_forge_reach_the_generator_from_the_renderer_too() -> None:
    """Same four-parameter generator protocol as ``get_changelog_entry``.

    ``website/docs/extending/changelog-plugins.md`` declares ``get_release_line(changeset,
    bump, options, forge)``. Pinned at this seam as well because ``render_changelog`` calls the
    generator itself -- a renderer that forwarded ``forge=None`` would render a perfectly valid
    entry made of un-linkified ``git``-style bullets while ``github`` was configured, which is
    the kind of failure nobody notices until it is published.
    """
    forge = StubForge()
    options: Mapping[str, object] = {"repo": "acme/widgets"}
    generator = RecordingGenerator()
    release, releases, changesets, deps = documented_fixture()

    render_changelog(
        release,
        releases,
        changesets,
        generator,
        deps=deps,
        config=NO_DATES,
        options=options,
        forge=forge,
    )

    assert generator.release_calls == [(options, forge), (options, forge)]
    assert generator.dependency_calls == [(options, forge)]
    assert generator.release_calls[0][1] is forge, "the forge instance itself, not a copy"


# ======================================================================================
# Dates -- the seven-year feature (research README section 5 item 5)
# ======================================================================================


def test_the_dates_config_gates_the_date_in_the_default_template(
    frozen_clock: FrozenClock,
) -> None:
    """``changelog-templates.md`` lines 51-57 and 72 -- ``dates`` is off by default.

    changesets issue #109 asks for exactly this and has been open since 2019. molt's answer is
    a config flag the entry template branches on, so the two outputs differ by precisely the
    parenthesized date and nothing else -- pinned by deriving one expected string from the
    other rather than writing both out, which makes any *other* drift a failure.

    ``frozen_clock`` supplies the timestamp: the docs call ``release.date`` "one timestamp per
    ``version`` run", so it is injected, not read from the wall clock inside the renderer. A
    renderer that called ``datetime.now()`` itself would produce entries whose dates disagree
    across packages in one run, and would be untestable here.
    """
    release, releases, changesets, deps = documented_fixture()
    stamp = frozen_clock.moment

    without = render_changelog(
        release, releases, changesets, GIT, deps=deps, config=ChangelogConfig(dates=False)
    )
    with_dates = render_changelog(
        release,
        releases,
        changesets,
        GIT,
        deps=deps,
        config=ChangelogConfig(dates=True),
        date=stamp,
    )

    assert without == DEFAULT_STRUCTURE_OUTPUT, "dates default to off"
    assert with_dates is not None
    assert with_dates == DEFAULT_STRUCTURE_OUTPUT.replace(
        "## 2.1.0", f"## 2.1.0 ({stamp.strftime('%Y-%m-%d')})", 1
    ), "dates=true adds a parenthesized ISO date to the heading and changes nothing else"
    assert with_dates.startswith("## 2.1.0 (2021-12-13)"), (
        "the frozen upstream timestamp 1639354050879 ms is 2021-12-13 in UTC "
        "(tests/conftest.py FROZEN_DATETIME)"
    )


def test_the_documented_worked_example_renders_the_documented_output() -> None:
    """A documentation lock on ``changelog-templates.md`` lines 71-104, both halves verbatim.

    The page prints a template and the exact Markdown it produces. If molt's context keys, the
    default spacing normalization, or the ``dates`` gate drift, this fails and the docs page is
    the thing that has to change -- which is the point of writing it down.

    It also pins that a user template reaching *the same* result as the default is possible at
    all: the page describes its example as "otherwise matching the default", so this output and
    ``DEFAULT_STRUCTURE_OUTPUT`` differ only by the date.
    """
    release, releases, changesets, deps = documented_fixture()

    rendered = render_changelog(
        release,
        releases,
        changesets,
        GIT,
        deps=deps,
        template=DOCUMENTED_TEMPLATE,
        config=ChangelogConfig(dates=True),
        date=DOCUMENTED_DATE,
    )

    assert rendered == DOCUMENTED_OUTPUT


# ======================================================================================
# Section order and headings live in the template (changelog-templates.md line 106)
# ======================================================================================

REORDERED_TEMPLATE = """\
## {{ release.new_version }}
{% if release.patch %}
### Fixes

{% for line in release.patch %}{{ line }}
{% endfor %}{% endif %}
{% if release.minor %}
### Features

{% for line in release.minor %}{{ line }}
{% endfor %}{% endif %}
{% if release.dependencies %}
### Dependencies

{% for line in release.dependencies %}{{ line }}
{% endfor %}{% endif %}"""


def test_section_order_and_headings_are_controlled_by_the_template() -> None:
    """``changelog-templates.md`` line 106, asserted rather than trusted.

    "Because the section order and headings live in the template, you can rename
    ``### Minor Changes`` to ``### Features``, split lines into your own categories, or move
    dependency bumps into their own section -- all without touching molt or writing a plugin."

    Three claims, all exercised at once: renamed headings, reversed section order (patch before
    minor), and dependency bumps promoted out of Patch into a section of their own. An
    implementation that kept ANY of the layout in Python -- the fixed Major/Minor/Patch order,
    the ``capitalize(type) + " Changes"`` heading, or the dependency-line-goes-in-patch rule --
    fails this, and it is the only test here that can catch that, because every other test uses
    a template that agrees with the built-in layout.
    """
    release, releases, changesets, deps = documented_fixture()

    rendered = render_changelog(
        release,
        releases,
        changesets,
        GIT,
        deps=deps,
        template=REORDERED_TEMPLATE,
        config=NO_DATES,
    )

    assert rendered == (
        "## 2.1.0\n"
        "\n"
        "### Fixes\n"
        "\n"
        "- Fix a crash when the input file is empty\n"
        "\n"
        "### Features\n"
        "\n"
        "- Add streaming support to the export API\n"
        "\n"
        "### Dependencies\n"
        "\n"
        "- Updated dependencies\n"
        "  - acme-core@1.4.1"
    )


def test_a_template_may_drop_a_section_entirely() -> None:
    """The corollary: if layout is the template's, a template can omit part of it.

    Not a hypothetical -- a project that treats dependency bumps as noise wants exactly this.
    It also proves the renderer is not post-processing the template's output back towards the
    default shape (re-adding a missing section, say), which is the failure mode a "normalize
    the spacing" step can quietly grow into.
    """
    release, releases, changesets, deps = documented_fixture()

    rendered = render_changelog(
        release,
        releases,
        changesets,
        GIT,
        deps=deps,
        template="## {{ release.new_version }}\n{% for line in release.minor %}{{ line }}\n"
        "{% endfor %}",
        config=NO_DATES,
    )

    assert rendered == "## 2.1.0\n- Add streaming support to the export API"
    assert "Updated dependencies" not in rendered
    assert "Patch" not in rendered


# ======================================================================================
# Failure DX: StrictUndefined, consistent with tests/changelog/test_render_template.py
# ======================================================================================

UNDEFINED_CASES = [
    # (template, offender, why)
    (
        "{{ relase.new_version }}",
        "relase",
        "a typo in the top-level name. Under Jinja2's default Undefined this renders an empty "
        "string and ships a changelog entry with no version heading",
    ),
    (
        "{{ release.new_verison }}",
        "new_verison",
        "a typo in an attribute -- the likelier mistake, since the top-level names are only "
        "`release` and `config`",
    ),
    (
        "{% if release.dats %}x{% endif %}",
        "dats",
        "StrictUndefined raises on __bool__ too, so a mistyped name inside an {% if %} is a "
        "hard error rather than a section that silently never renders. This is the row that "
        "fails against `Undefined` even though the other two could be caught by a linter",
    ),
    (
        "{{ config.date }}",
        "date",
        "the singular of the one documented config key -- an easy slip, and one that would "
        "otherwise turn the date on silently-never rather than reporting a bad config",
    ),
]


@pytest.mark.parametrize(("template", "offender", "why"), UNDEFINED_CASES)
def test_an_undefined_name_in_a_template_is_a_hard_error(
    template: str, offender: str, why: str
) -> None:
    """``StrictUndefined`` semantics, matching ``tests/changelog/test_render_template.py``.

    Same DX rule as the line-template engine: a template mistake fails the ``version`` run and
    names the offender, rather than rendering an empty string into a published changelog.

    ``ValueError`` is asserted deliberately, not a bare ``Exception``: ``jinja2.UndefinedError``
    derives from ``jinja2.TemplateError``, which is not a ``ValueError``, so an unwrapped Jinja2
    exception leaking out of ``molt.changelog`` fails here. That matters beyond tidiness --
    ``website/docs/extending/changelog-plugins.md`` promises "Errors abort before any write",
    and an error class the ``version`` command does not know to catch cannot be turned into a
    clean abort with a useful message.
    """
    release, releases, changesets, deps = documented_fixture()

    with pytest.raises(ValueError) as excinfo:
        render_changelog(
            release,
            releases,
            changesets,
            GIT,
            deps=deps,
            template=template,
            config=NO_DATES,
        )

    assert offender in str(excinfo.value), f"{why}; the error must name the offending token"


def test_a_malformed_template_is_a_hard_error_too() -> None:
    """A syntax error in a user template is a config error, reported as one.

    ``jinja2.TemplateSyntaxError`` also derives from ``TemplateError``, so the same wrapping
    rule applies: it must not reach the CLI as a raw Jinja2 exception.
    """
    release, releases, changesets, deps = documented_fixture()

    with pytest.raises(ValueError):
        render_changelog(
            release,
            releases,
            changesets,
            GIT,
            deps=deps,
            template="## {{ release.new_version }{% endfor %}",
            config=NO_DATES,
        )


# ======================================================================================
# Spacing normalization (changelog-templates.md line 106, last sentence)
# ======================================================================================

SLOPPY_TEMPLATE = """\


## {{ release.new_version }}



### Changes


{% for line in release.minor %}{{ line }}


{% endfor %}


"""


def test_output_is_normalized_no_matter_how_the_template_is_indented() -> None:
    """ "molt still normalizes the final spacing so the output stays valid Markdown no matter
    how the template is indented" -- ``changelog-templates.md`` line 106.

    This is what makes user templating safe. Jinja2 block tags leave newlines behind wherever a
    ``{% for %}`` or ``{% if %}`` sat, so a template written to be *readable* almost always
    renders with stray blank runs and trailing spaces. Without a normalization pass, every
    project that customized its template would get subtly broken Markdown -- and molt's whole
    "no formatter pass" claim (research README section 5 item 13) would be false for exactly
    the users who did the most work.

    Three properties, each independently breakable:

    - no run of three or more newlines (never more than one blank line);
    - no trailing whitespace on any line -- two trailing spaces is a hard line break in
      Markdown, which is precisely the ``"  "`` garbage upstream leaves for prettier;
    - no leading or trailing blank lines on the entry, because the caller inserts the entry
      into an existing ``CHANGELOG.md`` and owns the separators around it.
    """
    release, releases, changesets, deps = documented_fixture()

    rendered = render_changelog(
        release,
        releases,
        changesets,
        GIT,
        deps=deps,
        template=SLOPPY_TEMPLATE,
        config=NO_DATES,
    )

    assert rendered is not None
    assert not re.search(r"\n{3,}", rendered), "at most one blank line anywhere"
    assert not re.search(r"[ \t]+\n", rendered), "no trailing whitespace on any line"
    assert rendered == rendered.strip("\n"), "no leading or trailing blank lines"
    assert rendered == ("## 2.1.0\n\n### Changes\n\n- Add streaming support to the export API")


def test_a_summary_is_data_not_a_template() -> None:
    """Adversarial: an entry template renders ONCE, and a summary is never part of it.

    A changeset summary is untrusted author prose that reaches the context through the
    generator's line. If ``render_changelog`` interpolated the lines into the template source
    and then rendered -- or rendered the result a second time -- ``{{ 7*7 }}`` in a summary
    would become ``49`` and a changeset would be a template-injection vector into the release
    process. ``website/docs/extending/custom-generators.md`` promises the opposite: "Summaries
    are passed through literally."

    The ``$1``/``\\g<0>`` payload is the same guard against the replacement-string bug research
    README section 3.4 records: in Python the fix is an ``re.sub`` replacement **callable**,
    never a replacement string.
    """
    hostile = r"fix {{ 7*7 }} and {% raw %}x{% endraw %} and $1 and \g<0>"
    release = Release("pkg-a", MINOR, v("1.0.0"), v("1.1.0"), ("cs-1",))
    changesets = [Changeset("cs-1", hostile, (ChangesetRelease("pkg-a", MINOR),))]

    rendered = render_changelog(release, [release], changesets, GIT, config=NO_DATES)

    assert rendered == f"## 1.1.0\n\n### Minor Changes\n\n- {hostile}"
    # `is not None` narrows `str | None` for the type checker; `in` cannot take None. The equality
    # above already proves it, but pyrefly does not narrow through an `==` comparison.
    assert rendered is not None
    assert "49" not in rendered
