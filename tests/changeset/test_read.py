"""Conformance tests for reading ``.changeset/*.md`` from disk (``read_changesets``).

Ports ``packages/read/src/index.test.ts`` -- see
``roadmap/research/test-suite/03-config-changeset-io.md``, section
``packages/read/src/index.test.ts``, rows 1-10. The group file scores them **9 Port / 1 Adapt**;
the honest count for what is asserted here is **8 Port / 2 Adapt**, because row 4 is scored Port
upstream but its own Notes column tells molt to diverge on ordering (see below), and this file
follows that instruction. Implementation reference: ``read/src/index.ts:25-65``; the semantics
are written up in ``roadmap/research/changesets-02-config-and-data-model.md`` section 7.

What ``read_changesets`` owes its callers (``read/src/index.ts:50-63``):

* it reads ``<root>/.changeset``, not ``<root>`` -- a missing directory is a friendly error, not
  an ``ENOENT`` traceback (row 6, ``read/src/index.ts:34-39``);
* it skips names starting with ``.``, names not ending in ``.md``, and the fixed ignore list
  ``[/^README\\.md$/i, "AGENTS.md", "CLAUDE.md", "GEMINI.md"]`` (``read/src/index.ts:9``);
* the changeset ``id`` is the filename minus ``.md`` -- the filename format is explicitly **not**
  part of the spec (row 3, and the comment at ``read/src/index.test.ts:51-52``);
* parse errors propagate unchanged (row 7).

**molt divergence (row 4).** Upstream returns ``fs.readdir`` order and its test asserts that raw
order. ``readdir`` order is unstable across platforms and filesystems, so molt **sorts** -- doc 02
section 12.6, *"Sort the changeset filenames (sorted()), unlike changesets. Deterministic
changelogs are worth the divergence."* The assertion below pins sorted-by-id.

**molt divergence (row 10).** ``sinceRef`` is implemented by
``git.getChangedChangesetFilesSinceRef`` in JS; molt uses a ``git`` subprocess (test contract
section 5, ``molt.git``). The *behavior*
ports; only the mechanism differs. That row uses the real ``git`` binary via the ``git_repo``
fixture, so it is additionally marked ``integration`` + ``git``. The upstream fixture holds a
single changeset, which makes the filter unobservable -- molt's version adds a second, already
committed one so filtering and not filtering give different answers.

Assumed signature: ``read_changesets(root_dir, *, since_ref=None)`` -- ``root_dir`` is the project
root and ``.changeset`` is appended, mirroring ``readChangesets(rootDir, sinceRef?)``.

Fixture files go to disk through :func:`write_raw` (or ``write_bytes`` directly), never
``Path.write_text`` -- see that helper for why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from molt.versioning import BumpType

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import GitRepo, ProjectBuilder

pytest.importorskip(
    "molt.changeset", reason="build step 3 - changeset IO not yet implemented (TDD target)"
)

from molt.changeset import read_changesets

pytestmark = pytest.mark.functional


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def releases_of(changeset: Any) -> list[tuple[str, BumpType]]:
    """Normalize ``Changeset.releases`` to ``(name, type)`` pairs; see ``test_parse.py``."""
    return [(release.name, release.type) for release in changeset.releases]


def write_raw(project: ProjectBuilder, filename: str, contents: str) -> Path:
    """Drop a literal file into ``.changeset/``, byte for byte.

    ``tmp_project.write_changeset`` always produces a *valid* changeset, so the ignore-list,
    broken-file and dotfile rows need this lower-level escape hatch.

    ``write_bytes``, never ``write_text``: text mode opens with ``newline=None``, which
    translates every ``\\n`` to ``os.linesep`` -- so on Windows a fixture written with
    ``write_text`` lands on disk as CRLF and no longer contains the bytes the test says it does.
    """
    directory = project.root / ".changeset"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_bytes(contents.encode("utf-8"))
    return path


SIMPLE_CHANGESET = '---\n"cool-package": minor\n---\n\nNice simple summary\n'


# --------------------------------------------------------------------------------------
# Row 1 -- the baseline
# --------------------------------------------------------------------------------------


def test_reads_a_changeset_from_disc(tmp_project: ProjectBuilder) -> None:
    """Row 1 (``read/src/index.test.ts:12-29``): ``id`` is the filename minus ``.md``.

    doc 02 section 12.6 pins the mechanism: ``path.stem`` / ``removesuffix(".md")``, **not**
    ``replace(".md", "")`` -- upstream's ``replace`` mangles a file named ``a.md.md``.
    """
    tmp_project.write_changeset(
        "cool-computer-club", {"cool-package": "minor"}, "Nice simple summary"
    )

    changesets = read_changesets(tmp_project.root)

    assert [changeset.id for changeset in changesets] == ["cool-computer-club"]
    assert releases_of(changesets[0]) == [("cool-package", BumpType.MINOR)]
    assert changesets[0].summary == "Nice simple summary"


# --------------------------------------------------------------------------------------
# Row 2 -- the ignore list
# --------------------------------------------------------------------------------------

IGNORED_FILENAMES = [
    pytest.param("README.md", id="readme"),
    pytest.param("Readme.md", id="readme-case-variant"),
    pytest.param("AGENTS.md", id="agents"),
    pytest.param("CLAUDE.md", id="claude"),
    pytest.param("GEMINI.md", id="gemini"),
]


@pytest.mark.parametrize("filename", IGNORED_FILENAMES)
def test_ignores_the_fixed_ignore_list(tmp_project: ProjectBuilder, filename: str) -> None:
    """Row 2 (``read/src/index.test.ts:30-49``) and the list at ``read/src/index.ts:9``.

    ``README.md`` is matched by a case-insensitive regex; the other three are exact string
    comparisons upstream. doc 02 section 12.6 recommends molt make **all four** case-insensitive
    ("fixes gotcha 14"); only the ``README`` case-variant is pinned here, because extending
    case-insensitivity to the other three is an owner decision that is not yet recorded in the
    group file. The variant is ``Readme.md`` and not ``ReadMe.MD`` on purpose: upstream's
    ``file.endsWith(".md")`` is itself case-sensitive, so a ``.MD`` extension would be filtered
    out before the ignore list ever ran and the case-insensitivity would go untested.

    None of these files is a valid changeset, so a reader that fails to skip them raises rather
    than merely returning an extra entry -- the test would fail loudly either way.
    """
    write_raw(tmp_project, filename, "Changesets are great for monorepos\n")
    tmp_project.write_changeset("one-chance", {"cool-package": "minor"}, "Nice simple summary")

    changesets = read_changesets(tmp_project.root)

    assert [changeset.id for changeset in changesets] == ["one-chance"]


# --------------------------------------------------------------------------------------
# Row 3 -- the filename format is not part of the spec
# --------------------------------------------------------------------------------------


def test_reads_a_changeset_whose_name_is_not_a_three_word_id(tmp_project: ProjectBuilder) -> None:
    """Row 3 (``read/src/index.test.ts:50-69``).

    The upstream test carries the comment *"I just want it enshrined in the tests that the file
    name's format is in no way part of the changeset spec"*. Enshrined here too: molt's id
    generator produces ``adjective-noun-verb`` (doc 02 section 6), but the reader must never
    depend on it.
    """
    write_raw(tmp_project, "basic-changeset.md", SIMPLE_CHANGESET)

    changesets = read_changesets(tmp_project.root)

    assert [changeset.id for changeset in changesets] == ["basic-changeset"]


def test_the_id_strips_only_the_final_md_suffix(tmp_project: ProjectBuilder) -> None:
    """molt-side hardening of row 3: the id is the **suffix** stripped, not ``.md`` deleted.

    Upstream computes the id as ``file.replace(".md", "")`` (``read/src/index.ts:62``), and JS's
    single-argument ``String.replace`` only removes the *first* occurrence -- so upstream happens
    to get ``some.md.md -> some.md`` by luck. A naive Python transcription reaches for
    ``str.replace``, which removes **every** occurrence and silently yields ``some``. doc 02
    section 12.6 pins the fix: *"id from filename: ``path.stem`` / ``removesuffix('.md')``, not
    ``replace('.md','')``"*.

    The id is not cosmetic: ``apply_release_plan`` deletes ``<id>.md`` after versioning
    (doc 02 section 6), so an id that has lost characters leaves the consumed changeset on disk
    and the next ``molt version`` double-bumps.
    """
    write_raw(tmp_project, "some.md.md", SIMPLE_CHANGESET)

    changesets = read_changesets(tmp_project.root)

    assert [changeset.id for changeset in changesets] == ["some.md"]


# --------------------------------------------------------------------------------------
# Row 4 -- many changesets, in a DETERMINISTIC order (molt divergence)
# --------------------------------------------------------------------------------------


def test_reads_many_changesets_in_a_deterministic_sorted_order(
    tmp_project: ProjectBuilder,
) -> None:
    """Row 4 (``read/src/index.test.ts:70-99``), **adapted**.

    Upstream asserts raw ``fs.readdir`` order. That order is unstable across platforms and
    filesystems (NTFS returns names sorted, ext4 returns hash order), so asserting it would make
    the suite platform-dependent and would leak nondeterminism straight into changelog ordering.
    molt sorts by id -- doc 02 section 12.6, "Read order: **Sort** the changeset filenames
    (``sorted()``), unlike changesets. Deterministic changelogs are worth the divergence."

    The ids below are written in deliberately non-alphabetical order so a reader that just
    forwards ``os.listdir`` cannot pass by accident on a sorted filesystem.
    """
    tmp_project.write_changeset("zebra-tigers-run", {"perfect-package": "patch"}, "Last")
    tmp_project.write_changeset("alpha-cats-jump", {"cool-package": "minor"}, "First")
    tmp_project.write_changeset("middle-dogs-walk", {"other-package": "major"}, "Middle")

    changesets = read_changesets(tmp_project.root)

    assert [changeset.id for changeset in changesets] == [
        "alpha-cats-jump",
        "middle-dogs-walk",
        "zebra-tigers-run",
    ]
    assert [changeset.summary for changeset in changesets] == ["First", "Middle", "Last"]


# --------------------------------------------------------------------------------------
# Rows 5, 6 -- the two "nothing here" cases, which are NOT the same case
# --------------------------------------------------------------------------------------


def test_an_empty_changeset_directory_yields_an_empty_list(tmp_project: ProjectBuilder) -> None:
    """Row 5 (``read/src/index.test.ts:101-107``): the directory exists but holds nothing."""
    (tmp_project.root / ".changeset").mkdir(parents=True, exist_ok=True)

    assert read_changesets(tmp_project.root) == []


def test_a_missing_changeset_directory_is_a_friendly_error(tmp_project: ProjectBuilder) -> None:
    """Row 6 (``read/src/index.test.ts:109-122``): ``ENOENT`` becomes a sentence.

    The message is ported verbatim (``read/src/index.ts:35``) because ``molt init`` and the
    action's error handling both key off it, and users search for it.
    """
    assert not (tmp_project.root / ".changeset").exists()

    with pytest.raises(Exception, match=r"There is no \.changeset directory in this project"):
        read_changesets(tmp_project.root)


# --------------------------------------------------------------------------------------
# Rows 7, 8 -- broken vs empty. Only one of them is an error.
# --------------------------------------------------------------------------------------


def test_a_broken_changeset_propagates_the_parse_error(tmp_project: ProjectBuilder) -> None:
    """Row 7 (``read/src/index.test.ts:124-153``): read adds no error handling of its own.

    The fixture closes with ``--`` instead of ``---``, so the frontmatter regex finds no closing
    fence and ``parse_changeset`` raises "missing or invalid frontmatter" (see
    ``tests/changeset/test_parse.py``). ``read_changesets`` must let it through unchanged --
    wrapping it would hide which file is broken.
    """
    write_raw(
        tmp_project,
        "broken-changeset.md",
        '---\n\n"cool-package": minor\n\n--\n\nEverything is wrong',
    )

    with pytest.raises(Exception, match="missing or invalid frontmatter"):
        read_changesets(tmp_project.root)


def test_an_empty_changeset_file_is_valid(tmp_project: ProjectBuilder) -> None:
    """Row 8 (``read/src/index.test.ts:155-169``): mirrors parse rows 10/11.

    ``molt changeset add --empty`` writes exactly this file, and it must survive a read.
    """
    write_raw(tmp_project, "empty-like-void.md", "---\n---")

    changesets = read_changesets(tmp_project.root)

    assert [changeset.id for changeset in changesets] == ["empty-like-void"]
    assert releases_of(changesets[0]) == []
    assert changesets[0].summary == ""


# --------------------------------------------------------------------------------------
# Row 9 -- dotfile filtering (the upstream test name is misleading)
# --------------------------------------------------------------------------------------


def test_dotfiles_are_excluded(tmp_project: ProjectBuilder) -> None:
    """Row 9 (``read/src/index.test.ts:171-212``).

    **The upstream test name is misleading.** It is called "should filter out ignored changesets",
    which reads as if config ``ignore`` were involved. It is not: the mechanism is
    ``!file.startsWith(".")`` at ``read/src/index.ts:52``, i.e. plain dotfile filtering, and the
    workspace fixture in the upstream test is incidental. Config ``ignore`` filters *packages*
    during release-plan assembly, never files during read. Renaming a changeset to
    ``.<name>.md`` to park it is a real, used feature (doc 02 section 12.6, "Keep the leading-dot
    rule -- it's a real, used feature"), which is why it is pinned here.
    """
    tmp_project.add_package("pkg-a", deps=["pkg-b>=1.0.0"]).add_package("pkg-b")
    write_raw(
        tmp_project,
        "changesets-are-beautiful.md",
        '---\n"pkg-a": minor\n---\n\nNice simple summary, much wow\n',
    )
    write_raw(
        tmp_project,
        ".ignored-temporarily.md",
        '---\n"pkg-b": minor\n---\n\nAwesome feature, hidden behind a feature flag\n',
    )

    changesets = read_changesets(tmp_project.root)

    assert [changeset.id for changeset in changesets] == ["changesets-are-beautiful"]
    assert releases_of(changesets[0]) == [("pkg-a", BumpType.MINOR)]


# --------------------------------------------------------------------------------------
# Row 10 (Adapt) -- since_ref, against a real git repository
# --------------------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.git
def test_reads_a_nested_changeset_relative_to_the_git_root(git_repo: GitRepo) -> None:
    """Row 10 (``read/src/index.test.ts:213-247``), **adapted**: real ``git``, not a JS helper.

    Upstream filters through ``git.getChangedChangesetFilesSinceRef`` (``read/src/index.ts:11-23``)
    and only keeps changesets whose file changed since ``ref``; molt runs a ``git`` subprocess
    instead (test contract section 5, ``molt.git``). Three things are being pinned:

    1. ``.changeset`` is resolved relative to the directory passed in (``<repo>/library``), not to
       the git root -- a monorepo nested inside a larger repo must still work;
    2. the ``since_ref`` filter sees a **staged, uncommitted** file, exactly like the reference
       (which calls ``git add`` and then reads) -- so ``molt status --since main`` works on a
       dirty working tree;
    3. the filter actually **excludes** something. The fixture therefore holds *two* changesets:
       one already committed on ``main`` and one staged on top. A reader that ignored ``since_ref``
       entirely would return both and fail here -- with only one changeset on disk (the shape of
       the upstream test) this row is vacuous, because filtering and not filtering give the same
       answer.

    The unfiltered read is asserted alongside it so the two code paths are distinguished rather
    than merely exercised.

    The ``git_repo`` fixture is Windows-safe (branch ``main``, gpg off, background GC off,
    ``* text=auto eol=lf``), so this row is deterministic on cp1252 hosts too. Fixtures are
    written with ``write_bytes`` for the reason given on :func:`write_raw`.
    """
    library = git_repo.root / "library"
    changeset_dir = library / ".changeset"
    changeset_dir.mkdir(parents=True)

    (changeset_dir / "already-released.md").write_bytes(
        b'---\n"pkg-a": patch\n---\n\nAlready on main\n'
    )
    git_repo.run("add", "library/.changeset")
    git_repo.commit("chore: land an existing changeset")

    (changeset_dir / "awesome-summary.md").write_bytes(
        b'---\n"pkg-a": minor\n---\n\nAwesome summary\n'
    )
    git_repo.run("add", "library/.changeset")

    changesets = read_changesets(library, since_ref="main")

    assert [changeset.id for changeset in changesets] == ["awesome-summary"]
    assert releases_of(changesets[0]) == [("pkg-a", BumpType.MINOR)]
    assert changesets[0].summary == "Awesome summary"

    unfiltered = read_changesets(library)
    assert [changeset.id for changeset in unfiltered] == [
        "already-released",
        "awesome-summary",
    ]
