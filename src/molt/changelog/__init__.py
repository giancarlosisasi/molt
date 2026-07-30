"""Changelog assembly -- turning a release plan into the markdown a user reads.

Today this package holds the **entry assembler** only: :func:`get_changelog_entry` builds one
``## <version>`` entry from a release and the plan's changesets, and
:func:`generate_markdown_for_version_type` renders one ``### <Bump> Changes`` section under the
newline clamp that removes changesets' dependency on a formatter pass.

Three siblings are still owed, each by its own build step, and each is the reason a test module in
``tests/changelog/`` guards on a *submodule* rather than on this package:

- ``molt.changelog.template`` -- Jinja2 entry templating and the release-line token set.
- ``molt.changelog.render`` -- ``render_changelog``, which folds an entry into an existing file.
- ``molt.changelog.git`` / ``molt.changelog.github`` -- the two default generators, registered as
  entry points in the ``molt.changelog`` group.

Nothing in this package touches the filesystem, the network or git. A generator's contribution
arrives as an already-rendered string, and writing ``CHANGELOG.md`` is :mod:`molt.apply`'s job.
"""

from __future__ import annotations

from molt.changelog.entry import (
    ChangelogGenerator,
    ChangesetLike,
    ChangesetReleaseLike,
    ReleaseLike,
    generate_markdown_for_version_type,
    get_changelog_entry,
)

__all__ = [
    "ChangelogGenerator",
    "ChangesetLike",
    "ChangesetReleaseLike",
    "ReleaseLike",
    "generate_markdown_for_version_type",
    "get_changelog_entry",
]
