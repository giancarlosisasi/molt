"""Conformance tests for ``molt git-tag``.

Ports the 6 rows of ``packages/cli/src/commands/git-tag/__tests__/index.test.ts`` catalogued in
``roadmap/research/test-suite/06-cli-commands.md`` (git-tag section), against
``roadmap/research/changesets-03-cli-and-ux.md`` section 4.6 (tag naming, annotated tags, the NDJSON
event) plus gotchas 10.12 and 10.19. Website doc: ``website/docs/cli/git-tag.md``.

Deviations from the group file, all deliberate
----------------------------------------------
- **Module path.** ``molt.commands.git_tag``, not ``molt.cli.commands.git_tag``
  (``openspec/changes/adopt-typer-cli-shell/design.md`` D7 + ``tasks.md`` 1.2 + research doc 03
  section 11.5). ``MILESTONES.md`` and the phase brief say otherwise and are superseded.
- **NDJSON keys are snake_case.** Upstream emits ``{"type","tag","packageName"}``
  (``utils/output.ts:6-10``). molt's plan and event documents are snake_case throughout
  (``guides/dry-run-and-plans.md``, ``guides/status.md``), so the field is ``package_name``.
  ``cli/git-tag.md`` still shows ``packageName`` -- a verbatim carryover of upstream's schema, and
  a docs bug rather than a spec.
- **PEP 503 normalization** is applied to the package name before it reaches the tag, so
  ``Foo_Bar`` and ``foo-bar`` produce one tag (``cli/git-tag.md``; research README section 4.5,
  "Flat global namespace + PEP 503 normalization"). There is no changesets analogue: npm names are
  already case-restricted and have no punctuation equivalence.
- **``private_packages.tag`` does not exist.** Upstream gates private-package tagging on
  ``privatePackages.tag`` (`git-tag/index.ts:42`, default ``false``). molt's schema has no ``tag``
  sub-option -- ``tests/config/test_parse.py`` lists ``tag`` in ``DROPPED_KEYS`` and pins
  ``private_packages == {"version": True}``, and ``config/options.md`` records the drop. So row 6
  is asserted at the *default*, and there is no configuration that turns it back on.
- **``--dry-run``** is molt-native (``cli/git-tag.md`` options table); upstream's ``git-tag`` has no
  such flag. Every mutating command takes one (``guides/dry-run-and-plans.md``).

Routing is **not** tested here: the deprecated ``tag`` -> ``git-tag`` alias and its warning belong
to the CLI shell (``cli-shell`` spec, "Deprecated command alias") and are covered in
``tests/cli/test_cli.py``. Likewise ``MOLT_OUTPUT`` back-fill, which the group file assigns to
``test_cli.py::test_git_tag_output_from_env``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from tests.cli.fake_cli import FakeGit, RecordingConsole, read_ndjson

pytest.importorskip("molt.commands.git_tag", reason="build step 6 - `molt git-tag` is a TDD target")

from molt.commands.git_tag import run

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

pytestmark = [pytest.mark.functional]


# --------------------------------------------------------------------------------------
# Local project scaffolding
#
# `ProjectBuilder` always writes `[tool.uv.workspace]` into its root, so it cannot express the
# single-package shape rows 5 and 6 need (upstream's `tool.type === "root"`). These helpers write
# both shapes as LF-only bytes.
# --------------------------------------------------------------------------------------

WORKSPACE_PYPROJECT = """\
[project]
name = "workspace-root"
version = "0.0.0"
requires-python = ">=3.11"

[tool.uv.workspace]
members = ["packages/*"]
"""

CHANGESET_README = "# Changesets\n\nThis folder holds molt changeset files.\n"


@dataclass(frozen=True)
class Pkg:
    """One package: name, version, private flag."""

    name: str
    version: str = "1.0.0"
    private: bool = False


def package_toml(pkg: Pkg) -> str:
    lines = [
        "[project]",
        f'name = "{pkg.name}"',
        f'version = "{pkg.version}"',
        "dependencies = []",
    ]
    if pkg.private:
        # The Python analogue of npm's `"private": true` (research doc 02 section 12.1).
        lines.append('classifiers = ["Private :: Do Not Upload"]')
    return "\n".join(lines) + "\n"


def write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def make_workspace(root: Path, packages: Sequence[Pkg], *, config: str = "") -> Path:
    """A uv workspace with ``packages`` as members; ``config`` is the ``[tool.molt]`` body."""
    text = WORKSPACE_PYPROJECT
    if config:
        text += f"\n[tool.molt]\n{config.strip()}\n"
    write_lf(root / "pyproject.toml", text)
    write_lf(root / ".changeset" / "README.md", CHANGESET_README)
    for pkg in packages:
        write_lf(root / "packages" / pkg.name / "pyproject.toml", package_toml(pkg))
    return root


def make_single_package(root: Path, pkg: Pkg, *, config: str = "") -> Path:
    """A root-only project -- no workspace table, so the tag shape is ``v<version>``."""
    text = package_toml(pkg)
    if config:
        text += f"\n[tool.molt]\n{config.strip()}\n"
    write_lf(root / "pyproject.toml", text)
    write_lf(root / ".changeset" / "README.md", CHANGESET_README)
    return root


class CountingGit(FakeGit):
    """A :class:`FakeGit` that also counts the *read* calls the shared double does not record.

    Defined locally rather than in ``tests/cli/fake_cli.py`` (which is frozen and shared by four
    parallel writers) because only the batching row below needs it. The name cannot collide: the
    shared module exports no ``CountingGit``.
    """

    def __init__(self, *, existing_tags: Sequence[str] = ()) -> None:
        super().__init__(existing_tags=existing_tags)
        self.reads: dict[str, int] = {"get_all_tags": 0, "tag_exists": 0}

    def get_all_tags(self) -> set[str]:
        self.reads["get_all_tags"] += 1
        return super().get_all_tags()

    def tag_exists(self, name: str) -> bool:
        self.reads["tag_exists"] += 1
        return super().tag_exists(name)


def event(tag: str, package_name: str) -> dict[str, Any]:
    """One NDJSON ``git-tag`` event, snake_case (see the module docstring)."""
    return {"type": "git-tag", "tag": tag, "package_name": package_name}


# --------------------------------------------------------------------------------------
# Rows 1 + 4 - workspace tagging, in package order, idempotently
# --------------------------------------------------------------------------------------


def test_tags_every_workspace_package(
    tmp_path: Path, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 1 (`git-tag/__tests__/index.test.ts:14-42`): monorepo tags are ``<name>@<version>``.

    ``buildTag`` (`git-tag/index.ts:11-15`): ``tool.type !== "root"`` gives ``name@version``. In
    molt the branch is "workspace vs single package" (research doc 03 section 11.4: the tag shape is
    the *only* behaviour that must branch on the detected ecosystem). Order is the workspace's
    package order, which upstream pins by indexing ``mock.calls[0]`` / ``[1]``.
    """
    root = make_workspace(tmp_path / "project", [Pkg("pkg-a"), Pkg("pkg-b")])

    run(cwd=root, console=console, git=fake_git)

    assert fake_git.tags == ["pkg-a@1.0.0", "pkg-b@1.0.0"]


def test_tags_are_annotated_with_the_tag_name_as_the_message(
    tmp_path: Path, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Tags are annotated: ``git tag <t> -m <t>`` (``packages/git/src/index.ts:40-47``).

    Not cosmetic -- ``git push --follow-tags`` ignores lightweight tags, so a lightweight tag never
    reaches the remote (``cli/git-tag.md``: "Tags are annotated ... so that ``git push
    --follow-tags`` picks them up; lightweight tags would be skipped").

    ASSUMED SEAM: ``git.tag(name, message)`` with ``message == name``. That is the only way an
    injected double can observe annotated-ness; if the message were left to the git layer's default
    this assertion would need to move to an integration test over a real repository.
    """
    root = make_workspace(tmp_path / "project", [Pkg("pkg-a")])

    run(cwd=root, console=console, git=fake_git)

    assert [call.args for call in fake_git.of("tag")] == [("pkg-a@1.0.0", "pkg-a@1.0.0")]


def test_skips_tags_that_already_exist(tmp_path: Path, console: RecordingConsole) -> None:
    """Row 4 (`index.test.ts:107-140`): tagging is idempotent.

    ``cli/git-tag.md``: "a tag that already exists is skipped, so re-running is safe". Seeded
    through ``FakeGit(existing_tags=...)`` rather than by mutating the shared fixture, as the
    double's own docstring prescribes.
    """
    root = make_workspace(tmp_path / "project", [Pkg("pkg-a"), Pkg("pkg-b")])
    git = FakeGit(existing_tags=["pkg-a@1.0.0"])

    run(cwd=root, console=console, git=git)

    assert git.tags == ["pkg-b@1.0.0"]


def test_existing_tag_lookup_is_batched(tmp_path: Path, console: RecordingConsole) -> None:
    """DELIBERATE DIVERGENCE -- research doc 03 section 10.19, fixed per section 11.7 item 6.

    Upstream's ``getUntaggedPackages`` calls ``git.tagExists`` **and** ``git.remoteTagExists`` once
    per package (`utils/getUntaggedPackages.ts:12-19`; ``packages/git/src/index.ts:328-339`), and
    ``remoteTagExists`` shells out to ``git ls-remote --tags origin`` every time -- an unbatched
    network round-trip per package. molt reads the tag set once and answers every question from it.

    Three packages make the difference observable: a per-package implementation would show three
    lookups where a batched one shows one.
    """
    root = make_workspace(tmp_path / "project", [Pkg("pkg-a"), Pkg("pkg-b"), Pkg("pkg-c")])
    git = CountingGit()

    run(cwd=root, console=console, git=git)

    assert git.tags == ["pkg-a@1.0.0", "pkg-b@1.0.0", "pkg-c@1.0.0"]
    assert git.reads["get_all_tags"] == 1
    assert git.reads["tag_exists"] == 0, "one batched read, not one lookup per package"


# --------------------------------------------------------------------------------------
# Rows 2 + 3 - the NDJSON event stream
# --------------------------------------------------------------------------------------


def test_writes_one_ndjson_event_per_tag(
    tmp_path: Path, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 2 (`index.test.ts:44-82`), adapted to snake_case.

    Upstream pins the exact bytes -- two ``JSON.stringify`` lines plus a trailing ``""`` element,
    i.e. a terminating newline (``utils/output.ts:29``). molt keeps the format and renames
    ``packageName`` to ``package_name`` (see the module docstring). The trailing newline and the
    absence of CRLF are asserted on bytes: an NDJSON stream a consumer reads line-by-line must not
    depend on the writer's platform.
    """
    root = make_workspace(tmp_path / "project", [Pkg("pkg-a"), Pkg("pkg-b")])
    out = tmp_path / "output.ndjson"

    run(cwd=root, output=out, console=console, git=fake_git)

    assert read_ndjson(out) == [event("pkg-a@1.0.0", "pkg-a"), event("pkg-b@1.0.0", "pkg-b")]
    raw = out.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r\n" not in raw
    assert b"packageName" not in raw, "upstream's camelCase key does not survive the port"


def test_creates_an_empty_output_file_when_there_is_nothing_to_tag(
    tmp_path: Path, console: RecordingConsole
) -> None:
    """Row 3 (`index.test.ts:84-105`): the file exists and is empty.

    ``createOutputReport`` opens the stream before the early return, so the disposable still
    flushes an empty file (`utils/output.ts:17-33`; ``git-tag/index.ts:56-59``). ``cli/git-tag.md``
    keeps the guarantee: "The output file is always created, even when there is nothing to tag."

    Both halves are asserted because "no file" and "empty file" must stay distinguishable -- a
    consumer that treats a missing file as "zero events" cannot tell a skipped run from a crashed
    one.
    """
    root = make_workspace(tmp_path / "project", [Pkg("pkg-a")])
    out = tmp_path / "output.ndjson"
    git = FakeGit(existing_tags=["pkg-a@1.0.0"])

    run(cwd=root, output=out, console=console, git=git)

    assert git.tags == []
    assert out.is_file(), "the reporter creates the file even with zero events"
    assert read_ndjson(out) == []
    assert out.read_bytes() == b""


# --------------------------------------------------------------------------------------
# Rows 5 + 6 - the single-package project
# --------------------------------------------------------------------------------------


def test_single_package_project_uses_a_v_prefixed_tag(
    tmp_path: Path, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 5 (`index.test.ts:143-164`): a root-only project tags ``v<version>``.

    ``buildTag``'s ``tool.type === "root"`` branch (`git-tag/index.ts:11-15`); ``cli/git-tag.md``:
    "Single-package project: ``v<version>``". Most Python projects are single-package -- the ratio
    is inverted versus JS (research README section 5, item 9) -- so this is a first-class path, not
    a degenerate case.

    Upstream's fixture needs ``privatePackages: {version: true, tag: true}`` only because its root
    ``package.json`` is ``private: true``. molt's root here is publishable, so no config is needed:
    the ``tag`` sub-option molt dropped is not what makes this row work.
    """
    root = make_single_package(tmp_path / "project", Pkg("acme", version="1.0.0"))

    run(cwd=root, console=console, git=fake_git)

    assert fake_git.tags == ["v1.0.0"]


def test_a_private_root_package_is_not_tagged(
    tmp_path: Path, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 6 (`index.test.ts:165-179`): under the default config a private root gets no tag.

    Upstream gates this on ``privatePackages.tag`` (default ``false``, `git-tag/index.ts:38-45`).
    molt has no ``tag`` sub-option at all -- ``config/options.md`` records the drop and
    ``tests/config/test_parse.py`` pins both ``private_packages == {"version": True}`` and ``tag``
    in ``DROPPED_KEYS`` -- so the *default* behaviour is the whole behaviour: private packages are
    versioned (their internal pins must stay correct) but never tagged.
    """
    root = make_single_package(tmp_path / "project", Pkg("acme", version="1.0.0", private=True))

    run(cwd=root, console=console, git=fake_git)

    assert fake_git.tags == []


def test_single_package_project_emits_an_ndjson_event(
    tmp_path: Path, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Closes a carried test gap (tasks.md 6.1): no row previously asserted an NDJSON event for a
    ``v``-prefixed single-package tag -- every NDJSON row above uses the workspace ``name@version``
    shape. Upstream emits an event for this shape too (``utils/output.ts:6-10`` is shape-agnostic),
    so the event's ``tag`` carries no package name even though ``package_name`` still does.
    """
    root = make_single_package(tmp_path / "project", Pkg("acme", version="1.0.0"))
    out = tmp_path / "output.ndjson"

    run(cwd=root, output=out, console=console, git=fake_git)

    assert read_ndjson(out) == [event("v1.0.0", "acme")]


# --------------------------------------------------------------------------------------
# molt-NEW - PEP 503 tag normalization and --dry-run
# --------------------------------------------------------------------------------------

# (declared name, expected tag, why)
PEP503_CASES: list[tuple[str, str, str]] = [
    ("foo-bar", "foo-bar@1.0.0", "already normalized: the identity case"),
    ("Foo_Bar", "foo-bar@1.0.0", "underscore -> hyphen, and case folded"),
    ("Foo.Bar", "foo-bar@1.0.0", "a dot is a separator too"),
    ("FOO--BAR", "foo-bar@1.0.0", "runs of separators collapse to one"),
]


@pytest.mark.parametrize(("declared", "expected", "why"), PEP503_CASES)
def test_tag_names_are_pep_503_normalized(
    tmp_path: Path,
    console: RecordingConsole,
    fake_git: FakeGit,
    declared: str,
    expected: str,
    why: str,
) -> None:
    """molt-NEW: one distribution, one tag, whatever the manifest spells it.

    ``re.sub(r"[-_.]+", "-", name).lower()`` -- PEP 503. ``cli/git-tag.md`` states it outright
    ("with the name normalized per PEP 503 (``Foo_Bar`` and ``foo-bar`` produce the same tag)") and
    research README section 4.5 lists the flat namespace plus PEP 503 folding as new surface with
    "zero prior art". It matters here specifically because a tag is a *lookup key*: if the tag
    written on release day and the tag looked up on the next run disagree by so much as a case fold,
    every release re-tags and ``--follow-tags`` pushes duplicates.

    Research doc 02 sections 12.4/12.5 name tag construction alongside ``ignore``/``fixed``/
    ``linked`` matching as consumers of the normalized index; the same rule is pinned for config
    matching in ``tests/config/test_parse.py``.
    """
    root = make_workspace(tmp_path / "project", [Pkg(declared)])

    run(cwd=root, console=console, git=fake_git)

    assert fake_git.tags == [expected], why


def test_dry_run_reports_the_tags_without_creating_them(
    tmp_path: Path, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """molt-NEW: ``--dry-run`` prints the plan and mutates nothing.

    ``cli/git-tag.md`` options table ("Print the tags that would be created; create nothing") and
    ``guides/dry-run-and-plans.md`` ("``--dry-run`` prints that plan and executes nothing ... writes
    nothing, uploads nothing, tags nothing"). Upstream has no such flag on ``git-tag`` at all.
    """
    root = make_workspace(tmp_path / "project", [Pkg("pkg-a"), Pkg("pkg-b")])

    run(cwd=root, dry_run=True, console=console, git=fake_git)

    assert fake_git.calls == [], "a dry run performs no git mutation whatsoever"
    text = console.text()
    assert "pkg-a@1.0.0" in text
    assert "pkg-b@1.0.0" in text


def test_dry_run_still_writes_the_output_stream(
    tmp_path: Path, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """A dry run is a *faithful preview*, so the machine-readable stream is the real one.

    ``guides/dry-run-and-plans.md``: "Because it is the *same value* either way, a dry run is a
    faithful preview, never an approximation." The events describe what would be created; only the
    git mutation is withheld.
    """
    root = make_workspace(tmp_path / "project", [Pkg("pkg-a")])
    out = tmp_path / "output.ndjson"

    run(cwd=root, output=out, dry_run=True, console=console, git=fake_git)

    assert fake_git.tags == []
    assert read_ndjson(out) == [event("pkg-a@1.0.0", "pkg-a")]


def test_output_events_are_json_objects_one_per_line(
    tmp_path: Path, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """NDJSON, not a JSON array: one self-delimiting object per line.

    ``cli/git-tag.md``: "an NDJSON stream ... one per line". Pinned separately from
    ``read_ndjson`` because that helper would happily parse a pretty-printed object spread over
    several lines as several failures -- here the line count is the assertion.
    """
    root = make_workspace(tmp_path / "project", [Pkg("pkg-a"), Pkg("pkg-b")])
    out = tmp_path / "output.ndjson"

    run(cwd=root, output=out, console=console, git=fake_git)

    lines = out.read_bytes().decode("utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line) for line in lines] == [
        event("pkg-a@1.0.0", "pkg-a"),
        event("pkg-b@1.0.0", "pkg-b"),
    ]


def test_a_file_sourced_package_is_tagged_at_its_resolved_version(
    tmp_path: Path, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new (``implement-version-sources``): the tag carries the version the plan named.

    ``GTC-1`` recorded a package with no ``[project].version`` as *silently excluded* from tagging,
    the same shape as ``VC-1`` and ``RPE-4``, and owed it to whoever built the version-source
    abstraction. This is that: a member whose version lives in a file is tagged, and only a member
    molt genuinely has no version for stays excluded. The tag shape itself is unchanged --
    ``<pep503-name>@<version>`` in a workspace.
    """
    root = make_workspace(tmp_path / "project", [Pkg("pkg-b")])
    write_lf(
        root / "packages" / "pkg-a" / "pyproject.toml",
        "[project]\n"
        'name = "pkg-a"\n'
        'dynamic = ["version"]\n'
        "dependencies = []\n"
        "\n"
        "[tool.molt.version_source]\n"
        'kind = "file"\n'
        'path = "about.py"\n',
    )
    write_lf(root / "packages" / "pkg-a" / "about.py", '__version__ = "2.5.0"\n')

    run(cwd=root, console=console, git=fake_git)

    assert sorted(fake_git.tags) == ["pkg-a@2.5.0", "pkg-b@1.0.0"]
