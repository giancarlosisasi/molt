"""Folding one changelog entry into a package's ``CHANGELOG.md``.

Ports ``apply-release-plan/src/index.ts:333-364`` (research doc 04 section 3.3). Three cases, and
the rule is the same one in all three -- **a new entry goes directly above the newest existing
version heading**, which is what makes the file newest-first
(``website/docs/guide/changelog-templates.md``, "The default structure"):

=====================================  =====================================================
No file yet                            ``# <package>``, a blank line, the entry
Starts with ``## <version>``           The entry, a blank line, then the whole existing file
Starts with ``# <package>``            The title, the entry, a blank line, then the rest
=====================================  =====================================================

The insertion point is **located in the existing text** rather than the document being rebuilt
(design D7). A changelog is a hand-editable file: it collects preambles, migration notes and
badges above the first entry, and a renderer that reconstructed it from parsed headings would
delete all of that on the first ``molt version``.

Deliberate divergence -- the separating blank line
--------------------------------------------------
Upstream writes ``templateString.trimStart() + fileData`` (``index.ts:353-357``), so a file that
opens with a version heading comes back as ``...testing!\\n## 1.0.0`` -- two headings welded to the
last bullet of the entry above. It is repaired by upstream's prettier pass. molt does not run a
formatter (``format`` defaults to false; research README section 5 item 13) and does not need one:
the blank line is emitted here. Pinned by
``tests/apply/test_apply.py::test_new_entry_is_inserted_before_an_existing_version_heading``, which
fails against upstream's output.

Relationship to ``molt.changelog.render_changelog``
---------------------------------------------------
That function -- the Jinja2 **entry** template, still owed by the changelog-rendering build step --
shapes what one entry *contains*. This module only decides where the finished entry goes in the
file. They compose; neither replaces the other.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["insert_changelog_entry"]

#: The first line that opens a version entry. ``^## `` is anchored per line, so a ``## `` appearing
#: inside a fenced code block in someone's preamble would match -- accepted, because the
#: alternative is parsing markdown to write one line into it.
_VERSION_HEADING: Final = re.compile(r"^## ", re.MULTILINE)

#: Entries are separated by exactly one blank line.
_ENTRY_SEPARATOR: Final = "\n\n"


def insert_changelog_entry(existing: str | None, *, package_name: str, entry: str) -> str:
    """Return the full ``CHANGELOG.md`` text with ``entry`` inserted at the top.

    ``existing`` is the file's current contents, or ``None`` when there is no file yet. ``entry``
    is a finished ``## <version>`` block with no trailing newline -- what
    :func:`molt.changelog.get_changelog_entry` returns.

    The result always ends in exactly one newline when the file is created, and otherwise keeps
    whatever the existing file ended with: molt owns the entry it inserts, not the bytes around it.
    """
    if existing is None or not existing.strip():
        return f"# {package_name}\n\n{entry}\n"

    heading = _VERSION_HEADING.search(existing)
    if heading is not None:
        cut = heading.start()
        return f"{existing[:cut]}{entry}{_ENTRY_SEPARATOR}{existing[cut:]}"

    # A file with a title (or a preamble) but no entries yet: the new entry is the first one, so it
    # goes at the end rather than above a heading that is not there.
    return f"{existing.rstrip(chr(10))}{_ENTRY_SEPARATOR}{entry}\n"
