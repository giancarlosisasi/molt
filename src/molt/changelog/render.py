"""The changelog **entry** template: molt's answer to changesets issue #109.

This is the outer of molt's two template layers. It shapes everything *around* the bullets -- the
``## <version>`` heading, the section headings, their order, whether a section appears at all, and
the date -- while :mod:`molt.changelog.template` shapes one bullet.
``website/docs/extending/changelog-plugins.md`` states the split: "You return lines, not layout."
The entry template is configured on the project::

    [tool.molt]
    changelog = { template = "changelog-entry.md.jinja", dates = true }

Both settings are members of the ``changelog`` table, alongside the ``generator`` member that
carries what used to be the whole value of that key (owner ruling 2026-07-30, closing ``VC-4``;
they were flat ``changelog_template`` / ``changelog_dates`` keys before it). Inside a template
``dates`` arrives as ``config.dates``, which is the surface
``website/docs/guides/changelog-templates.md`` documents: a template's ``config`` is the changelog
scope of the configuration, narrowed by :class:`molt.apply.apply._ChangelogConfigView`, not the
whole document.

There is nothing to port here
-----------------------------
Upstream has no counterpart. Dates in the changelog have been
`an open changesets request since 2019 <https://github.com/changesets/changesets/issues/109>`_ --
seven years, with a pull request that waited six of them -- and the blocker is structural: the entry
layout is welded into ``get-changelog-entry.ts:111-118`` with no seam for a user to reach. Research
README section 5 item 5 makes replacing that weld with a real template one of the four features that
make molt more than a port, and prices it at exactly one library.

The two paths that must agree
-----------------------------
:func:`molt.changelog.get_changelog_entry` is the no-template path and
``render_changelog(template=None)`` is the templated one. For the same inputs they produce the same
bytes, or a project would get a different changelog depending on whether it had ever set
``[tool.molt.changelog]`` -- and every CHANGELOG.md in every repo would re-flow on the upgrade that
introduced templating. Two things hold that line: both build their lines from
:func:`molt.changelog.collect_changelog_sections`, so the *content* cannot drift; and the default
template (``templates/default-entry.md.jinja``) is asserted against the hand-written documented
structure, so the *layout* cannot either.

Three parameters this seam adds, each an adaptation decision
------------------------------------------------------------
``template``
    The Jinja2 **source text**, not a path. The documented config value is a filename, so something
    reads that file -- but not this function: filesystem I/O inside a pure renderer would make every
    caller build a workspace to render a string. Resolution belongs to the config layer.
``config``
    What the template sees as ``config``, and it is the ``[tool.molt.changelog]`` **table**, not the
    whole ``[tool.molt]`` one. Forced by the documented example, which writes
    ``{% if config.dates %}`` against a ``dates`` key declared under ``[tool.molt.changelog]``.
``date``
    Surfaces as ``release.date``. The docs call it "a ``datetime`` for this release (one timestamp
    per ``version`` run)", so it is injected by the caller rather than read off the release or off
    the wall clock: a renderer calling :func:`datetime.now` itself would stamp two packages in one
    run with different dates, and would be untestable.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Protocol

from jinja2 import StrictUndefined, Undefined

from molt.changelog.entry import ReleaseLike, collect_changelog_sections, normalize_lines
from molt.changelog.template import new_environment, render_source
from molt.versioning import BumpType

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from packaging.version import Version

    from molt.changelog.entry import ChangelogGenerator, ChangesetLike

__all__ = [
    "RenderReleaseLike",
    "TemplateRelease",
    "default_template",
    "normalize_entry",
    "render_changelog",
]

#: The default layout, shipped as a package asset rather than a Python string constant (design D8):
#: a project can copy it as the starting point for its own template, and comparing the built-in path
#: against a real file is what makes the "agrees with the assembler" guard meaningful.
_DEFAULT_TEMPLATE_ASSET = "templates/default-entry.md.jinja"

#: The package the asset lives in. Spelled out rather than taken from ``__package__``, which is
#: ``str | None`` and would need a fallback that could silently point somewhere else.
_ASSET_PACKAGE = "molt.changelog"

#: Raised through ``release.date`` when the caller supplied no timestamp. An :class:`Undefined` and
#: not ``None`` on purpose: "the caller did not supply a date" is exactly what undefined means, so
#: ``{{ release.date.strftime(...) }}`` reports a missing date instead of an ``AttributeError`` on
#: ``None``, and the report travels the same wrapping path as every other template failure.
_NO_DATE_HINT = (
    "release.date is not available: no date was passed to render_changelog(). A template that "
    "renders a date needs one -- set `changelog = { dates = true }` under [tool.molt] so the "
    "`version` run supplies its single per-run timestamp."
)


class RenderReleaseLike(ReleaseLike, Protocol):
    """A planned release, as the entry renderer reads it.

    :class:`molt.changelog.ReleaseLike` plus ``old_version``. The extra field is not used by the
    default template; it is in the context because a user template that reports "1.4.0 -> 1.4.1"
    needs it, and :class:`molt.engine.Release` already carries it.
    """

    @property
    def old_version(self) -> Version: ...


@dataclass(frozen=True)
class TemplateRelease:
    """What a template sees as ``release`` -- the documented public template surface.

    Every field here appears in ``website/docs/guides/changelog-templates.md``'s field table or in
    its worked example, so **renaming one silently breaks user templates the docs told people to
    write**. ``tests/changelog/test_render_changelog.py::
    test_the_documented_template_context_keys_are_available`` pins them one per row.

    A dedicated object rather than a plain dict, for two reasons. Jinja2 resolves ``release.items``
    on a dict to the *method*, so a dict quietly hands template authors Python's mapping API as
    if it were changelog data; and an unknown attribute on this class produces an ``Undefined``
    naming the field, which under ``StrictUndefined`` is the hard error design D6 asks for.

    The four line buckets are tuples, never ``None``: the documented example iterates them with
    ``{% for line in release.major %}``, which an empty section has to survive.
    """

    name: str
    type: BumpType
    old_version: Version
    new_version: Version
    changesets: tuple[str, ...]
    date: datetime | Undefined
    major: tuple[str, ...]
    minor: tuple[str, ...]
    patch: tuple[str, ...]
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class _NoChangelogConfig:
    """The ``config`` a template sees when the caller passed none.

    ``dates`` is present and false so the default template renders without a configuration object at
    all; every other name is absent, so a template asking for one gets the same undefined-name error
    it would get from a real config table.
    """

    dates: bool = False


@lru_cache(maxsize=1)
def default_template() -> str:
    """molt's built-in entry template, read once from the package asset.

    CRLF is folded to LF on read. The repository stores the asset with LF (``.gitattributes``), but
    a wheel unpacked from a CRLF working copy would otherwise render entries whose blank lines carry
    a stray carriage return -- invisible in a diff, wrong in every changelog the project publishes.
    """
    asset = resources.files(_ASSET_PACKAGE).joinpath(_DEFAULT_TEMPLATE_ASSET)
    return asset.read_text(encoding="utf-8").replace("\r\n", "\n")


def render_changelog(
    release: RenderReleaseLike,
    releases: Sequence[ReleaseLike],
    changesets: Sequence[ChangesetLike],
    generator: ChangelogGenerator,
    *,
    template: str | None = None,
    config: object | None = None,
    date: datetime | None = None,
    deps: Sequence[str] = (),
    dev_deps: Sequence[str] = (),
    optional_deps: Sequence[str] = (),
    update_internal_dependencies: BumpType = BumpType.PATCH,
    options: Mapping[str, object] | None = None,
    forge: object | None = None,
) -> str | None:
    """Render one ``## <version>`` changelog entry from a template, or ``None`` for no entry.

    ``template`` is Jinja2 source text; ``None`` selects the built-in default, which reproduces the
    changesets layout Python users already recognize. Everything from ``deps`` rightward is spelled
    exactly as :func:`molt.changelog.get_changelog_entry` spells it, deliberately: the two functions
    answer the same question about the same inputs and must not drift apart.

    A ``none`` release returns ``None``, and the template has no say in it. By the time a template
    could run, the decision is already wrong: ``type == "none"`` means **no CHANGELOG.md write at
    all** (``get-changelog-entry.ts:31``), so returning ``""`` instead would make the caller write a
    file for a version that was never published.

    ``options`` and ``forge`` are pure pass-through to the generator, on every call, to both methods
    -- the same four-parameter contract the assembler uses (design D4). A renderer that forwarded
    ``forge=None`` would quietly produce un-linkified ``git``-style bullets while ``github`` was
    configured, which is a failure nobody notices until it is published.

    Raises :class:`molt.errors.MoltTemplateError` (a :class:`ValueError`) for a malformed template
    and for any name the template asks for that the context does not carry.
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
    context: dict[str, object] = {
        "release": TemplateRelease(
            name=release.name,
            type=release.type,
            old_version=release.old_version,
            new_version=release.new_version,
            changesets=tuple(release.changesets),
            date=date if date is not None else StrictUndefined(hint=_NO_DATE_HINT),
            major=sections.major,
            minor=sections.minor,
            patch=sections.patch,
            dependencies=sections.dependencies,
        ),
        "config": config if config is not None else _NoChangelogConfig(),
    }

    source = template if template is not None else default_template()
    rendered = render_source(new_environment(), source, context, what="changelog entry template")
    return normalize_entry(rendered)


def normalize_entry(rendered: str) -> str:
    """Clean the rendered entry up to publishable Markdown, whatever the template looked like.

    Design D7: normalize **after** rendering rather than constraining the template. Jinja2 leaves a
    newline behind wherever a ``{% for %}`` or ``{% if %}`` sat, so a template written to be
    *readable* almost always renders with stray blank runs and trailing spaces. Without this pass,
    every project that customized its template would get subtly broken Markdown -- and molt's "no
    formatter pass" claim (research README section 5 item 13) would be false for exactly the users
    who did the most work.

    Three properties, each independently breakable:

    - **At most one blank line anywhere.** Three newlines is the run a formatter would have to
      remove; it is also what changesets' text surgery leaves behind.
    - **No trailing whitespace on any line.** Two trailing spaces is a hard line break in Markdown,
      so it is not cosmetic -- it changes how the entry renders.
    - **No leading or trailing blank lines.** The caller inserts this entry into an existing
      ``CHANGELOG.md`` and owns the separators around it
      (:func:`molt.apply.insert_changelog_entry`).

    This is the same discipline as the assembler's newline clamp, reached differently: the clamp
    works forward from a gap counter because it is *building* the text, while a template's output is
    already text, so collapsing runs is both simpler and equivalent. Every step is a plain string
    operation -- no ``re.sub``, therefore no replacement string that could expand a ``$1`` or a
    ``\\g<0>`` living in an author's summary (research README section 3.4).

    Interior indentation is preserved: a nested ``  - pkg@1.2.3`` under an
    ``- Updated dependencies`` bullet is structure, not stray whitespace.

    The body is :func:`molt.changelog.entry.normalize_lines`, which the assembler now applies
    inside each release line as well (owner ruling 2026-07-30, gap ``CT-6``). One definition, two
    callers: that is what makes "the two paths agree" structural rather than a coincidence two
    separate implementations happen to preserve.
    """
    return normalize_lines(rendered)
