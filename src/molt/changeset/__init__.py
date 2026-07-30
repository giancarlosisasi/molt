"""Changeset IO -- the atomic unit of intent, read and written.

A changeset is a markdown file with YAML frontmatter naming packages and bump types, plus a
free-text summary. Three seams, one per direction of travel:

- :func:`parse_changeset` -- text to a :class:`Changeset` (the grammar and its rejection rules);
- :func:`read_changesets` -- a ``.changeset`` directory to an ordered list;
- :func:`write_changeset` -- a :class:`Changeset` back to disk, byte-exactly.

Plus the name a new changeset is filed under: :func:`generate_changeset_id` (the ``human-id`` port,
research doc 02 section 6) and :func:`unique_changeset_id`, which is that generator with the
collision guard upstream never added. ``write_changeset`` deliberately does **not** call either --
choosing an id is ``molt add``'s decision, and a caller re-writing a changeset it already read must
keep the id it already has.

``write`` then ``parse`` is the identity on releases and summary. That invariant is what lets
``molt add`` and ``molt version`` agree about a file neither of them wrote, so it is pinned twice in
``tests/changeset``: once against the exact template bytes and once as a property over arbitrary
names, bump types and summaries.

Upstream splits this across ``@changesets/parse``, ``@changesets/read`` and ``@changesets/write``.
The legacy **v1** changeset format was removed in changesets v3 and molt never accepted it
(research README section 3.2).
"""

from __future__ import annotations

from molt.changeset.human_id import generate_changeset_id, unique_changeset_id
from molt.changeset.parse import Changeset, Release, parse_changeset
from molt.changeset.read import CHANGESET_DIR, IGNORED_MD_FILES, read_changesets
from molt.changeset.write import format_files, render_changeset, write_changeset

__all__ = [
    "CHANGESET_DIR",
    "IGNORED_MD_FILES",
    "Changeset",
    "Release",
    "format_files",
    "generate_changeset_id",
    "parse_changeset",
    "read_changesets",
    "render_changeset",
    "unique_changeset_id",
    "write_changeset",
]
