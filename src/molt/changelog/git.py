"""The default changelog generator: a changeset summary, rendered as a markdown bullet.

Ports ``packages/changelog-git/src/index.ts`` (research doc 04 section 3.4). It is the generator
``changelog = "git"`` selects and the one every ``molt version`` run uses unless a project names
another. It reads nothing: no forge, no network, no git log. The name is historical -- upstream
calls the package ``@changesets/changelog-git`` because a *commit* hash is the only enrichment it
can offer, and molt keeps the name so a migrating ``config.json`` keeps working.

Deliberate divergence -- the blank separator line
-------------------------------------------------
Upstream indents **every** continuation line of a multi-paragraph summary by two spaces
(``changelog-git/src/index.ts:14``), so the empty line between two paragraphs is emitted as a line
containing two spaces. That is trailing whitespace: ``markdownlint`` MD009 flags it, and only
upstream's prettier pass removes it. molt's ``format`` defaults to false and "correct markdown, the
first time" is a stated differentiator (research README section 5 item 13), so an empty
continuation line is emitted **empty**. Pinned by
``tests/apply/test_apply.py::test_multi_line_same_type_summaries_are_listed_with_clean_blank_lines``,
which asserts no line in a generated changelog ends in whitespace.

The four-parameter call shape is :class:`molt.changelog.ChangelogGenerator`'s and is not negotiable
here: ``options`` and ``forge`` are accepted and ignored, which is what lets one assembler drive
both this generator and the forge-backed ``github`` one with no branching (design D4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from molt.versioning import BumpType

__all__ = ["GitChangelogGenerator", "generator"]

#: Two spaces: the markdown continuation indent that keeps a summary's later paragraphs inside the
#: same list item (``changelog-git/src/index.ts:14``).
_CONTINUATION_INDENT = "  "


def _commit_marker(changeset: Any) -> str:
    """`` [`<sha>`]`` when the changeset carries a commit, else the empty string.

    ``commit`` is populated only once a changeset file has been committed and molt has looked its
    sha up, so it is read with :func:`getattr`: a changeset parsed straight off a dirty working
    tree has no such attribute, and that is the normal case during ``molt version``.
    """
    commit = getattr(changeset, "commit", None)
    return f" [`{commit}`]" if commit else ""


class GitChangelogGenerator:
    """The ``git`` generator (``changelog-git/src/index.ts:1-35``)."""

    def get_release_line(
        self,
        changeset: Any,
        bump: BumpType,
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        """One bullet carrying the changeset summary verbatim.

        ``bump`` is not read: which ``### ... Changes`` section the bullet lands in is the entry
        assembler's decision, and a generator that re-stated it could disagree with the heading it
        is printed under.
        """
        del bump, options, forge
        first, *rest = (line.rstrip() for line in changeset.summary.split("\n"))
        bullet = f"- {first}{_commit_marker(changeset)}"
        if not rest:
            return bullet
        # See the module docstring: an empty continuation line stays empty rather than becoming
        # two spaces of trailing whitespace.
        continued = "\n".join(f"{_CONTINUATION_INDENT}{line}" if line else "" for line in rest)
        return f"{bullet}\n{continued}"

    def get_dependency_release_line(
        self,
        changesets: Any,
        dependencies_updated: Any,
        options: Mapping[str, object] | None = None,
        forge: object | None = None,
    ) -> str:
        """``- Updated dependencies`` per relevant changeset, then the moved dependencies.

        The empty string when nothing moved -- :func:`molt.changelog.get_changelog_entry` appends
        this line to the patch bucket unconditionally, and the section renderer drops a
        whitespace-only entry, so "no dependency moved" has to be expressible as no text at all.

        The dependency list is indented two spaces so it nests under the ``- Updated dependencies``
        bullet. When there is no such bullet -- a dependency bumped by a changeset that never
        named it -- the section renderer trims the indent off the first line, which is why
        ``- pkg-b@2.0.0`` appears unindented in that case rather than as an orphaned sub-list.
        """
        del options, forge
        updated: Sequence[Any] = list(dependencies_updated)
        if not updated:
            return ""
        headers = [f"- Updated dependencies{_commit_marker(changeset)}" for changeset in changesets]
        entries = [
            f"{_CONTINUATION_INDENT}- {dependency.name}@{dependency.new_version}"
            for dependency in updated
        ]
        return "\n".join([*headers, *entries])


#: The instance molt resolves ``changelog = "git"`` to. Module-level, because a generator holds no
#: state and every resolution path -- entry point, dotted module path, file path -- looks for the
#: same ``generator`` attribute.
generator = GitChangelogGenerator()
