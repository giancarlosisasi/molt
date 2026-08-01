"""Changelog assembly -- turning a release plan into the markdown a user reads.

Two rendering paths, two template layers, and one shared set of lines:

- :func:`get_changelog_entry` is the **no-template** path. It builds one ``## <version>`` entry with
  the changesets layout baked in, and :func:`generate_markdown_for_version_type` renders one
  ``### <Bump> Changes`` section under the newline clamp that removes changesets' dependency on a
  formatter pass.
- :func:`render_changelog` is the **templated** path -- molt's answer to changesets issue #109, open
  since 2019. The heading, the section headings, their order, whether a section appears at all and
  the date all move into a Jinja2 template. Its built-in default renders exactly what
  :func:`get_changelog_entry` renders, so making layout configurable did not change what an
  unconfigured project gets.
- :func:`render_template` is the inner, **per-line** template: five documented tokens, one pass, a
  closed key set. It is a generator *option*, not a project setting.

Both entry paths take their lines from :func:`collect_changelog_sections`, which asks the generator
once per changeset plus once for dependencies and returns the four buckets. Sharing that step is
what keeps the two layouts from drifting apart (design D3); the fourth bucket -- dependencies kept
separate from patch -- is what lets a template place dependency bumps anywhere (design D4).

The two built-in generators live in submodules and are **not** re-exported here:
:mod:`molt.changelog.git`, the default, which needs no forge and no network, and
:mod:`molt.changelog.github`, which turns an injected :class:`molt.forge.Forge` into commit,
pull-request and issue links. Both are registered under ``[project.entry-points."molt.changelog"]``
as ``git`` and ``github``, so molt resolves its own defaults through exactly the mechanism a
third-party generator uses -- there is no privileged built-in path (change 13, design D1).
Importing them by name is what keeps a project that never writes a changelog from paying for
either.

Nothing in this package touches the filesystem, the network or git, with one bounded exception: the
default entry template is read from a package asset. A generator's contribution arrives as an
already-rendered string, and writing ``CHANGELOG.md`` is :mod:`molt.apply`'s job.
"""

from __future__ import annotations

from molt.changelog.entry import (
    ChangelogGenerator,
    ChangelogSections,
    ChangesetLike,
    ChangesetReleaseLike,
    DependencyReleaseLike,
    ReleaseLike,
    collect_changelog_sections,
    generate_markdown_for_version_type,
    get_changelog_entry,
)
from molt.changelog.render import (
    RenderReleaseLike,
    TemplateRelease,
    default_template,
    normalize_entry,
    render_changelog,
)
from molt.changelog.template import (
    RELEASE_LINE_TOKENS,
    build_release_line_tokens,
    render_template,
    resolve_ref,
)

__all__ = [
    "RELEASE_LINE_TOKENS",
    "ChangelogGenerator",
    "ChangelogSections",
    "ChangesetLike",
    "ChangesetReleaseLike",
    "DependencyReleaseLike",
    "ReleaseLike",
    "RenderReleaseLike",
    "TemplateRelease",
    "build_release_line_tokens",
    "collect_changelog_sections",
    "default_template",
    "generate_markdown_for_version_type",
    "get_changelog_entry",
    "normalize_entry",
    "render_changelog",
    "render_template",
    "resolve_ref",
]
