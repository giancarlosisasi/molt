"""Conformance tests for the outer release loop -- ``select_mode`` / ``build_pull_request_body`` /
``run_action``.

Port source
-----------
``changesets/action`` **v1.9.0**, the stable tag behind ``uses: changesets/action@v1``, fetched and
read on 2026-07-31:

* ``src/index.ts:57-176`` -- the four-case mode matrix, and the rule that a run is never both
  phases.
* ``src/run.ts:106-148`` -- the version loop (already shipped as ``molt.action.run_version``).
* ``src/run.ts:189-243`` -- ``getVersionPrBody``: the header, ``# Releases``, one
  ``## <name>@<version>`` section per package, and the three-tier truncation.
* ``src/run.ts:257-258`` -- the ``Version Packages`` title and commit-message defaults.
* ``src/run.ts:333-407`` -- the changed-package -> body-entry mapping, the ``pulls.list`` lookup and
  the create-or-update branch.
* ``src/run.ts:30-58`` and ``:118-149`` -- one host release per published package, a missing
  ``CHANGELOG.md`` skipped and a changelog missing the version treated as a failure.

What molt changes, and why these rows must FAIL against a literal port
----------------------------------------------------------------------
1. **The open-pull-request lookup runs before ``run_version``**, not between the version script and
   the push (design D3). Upstream's own comment gives the reason -- look the pull requests up
   "before we push any changes that may inadvertently close the existing PRs" -- and molt's
   ``run_version`` owns the branch prep, the script, the commit *and* the force-push in one pinned
   function, so the only faithful place left is earlier. One row asserts the recorded call order
   across both doubles; moving the lookup after the push fails it while every content assertion
   still passes.
2. **The publish loop pushes no tags** (design D6). ``run_publish`` already pushed all of them in
   one ``git push origin --tags`` before returning.
3. **``ActionResult`` is snake_case** -- ``published`` / ``published_packages`` /
   ``has_changesets`` / ``pull_request_number`` -- against upstream's camelCase ``action.yml``
   outputs, matching every other machine-readable payload molt emits.
4. **There is no pre mode** (``openspec/GAPS.md`` ``AP-1``): molt keeps no ``pre.json`` state, so
   nothing suffixes the title or prepends a banner, and no row asks for one.

Doubles, and what they are allowed to touch
--------------------------------------------
Every forge call goes through :class:`RecordingForge`; **no row constructs a real forge and no row
opens a socket**. The version rows drive a real ``git`` through :class:`RecordingGit`, a recording
proxy over the injected :class:`molt.git.Git` seam, on the same ``git_repo`` + ``shallow_clone``
harness ``tests/action/test_run.py`` uses -- so the force-push lands on a local ``file://`` origin
and the call *order* is still observable. Both doubles append to one shared journal, which is what
makes "the lookup happened before the push" an assertion rather than an intention.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("molt.action", reason="molt.action.orchestrate is this suite's target")

from molt.action import (
    MAX_BODY_CHARACTERS,
    ActionFailed,
    ActionResult,
    CommitMode,
    Mode,
    build_pull_request_body,
    pull_request_entry,
    run_action,
    select_mode,
)
from molt.action.body import MOLT_REPOSITORY_URL
from molt.errors import ExitError, MoltError, MoltForgeError
from molt.forge import NO_FILE_ADDITIONS, CommitRef, PullRef, ReleaseInfo
from molt.git import Git

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from tests.conftest import GitRepo, ProjectBuilder

# ======================================================================================
# Doubles
# ======================================================================================


@dataclass
class Call:
    """One recorded call on a double: what was called, and with what."""

    name: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


class RecordingForge:
    """A forge double that records every call, in order, and answers programmed values.

    Shaped like :class:`molt.forge.Forge` and nothing more: the loop must not be able to reach a
    member the protocol does not declare, which is what keeps a second backend able to serve it.
    ``journal`` is shared with :class:`RecordingGit` so the two orders can be compared.
    """

    name = "recording"
    api_url = "https://forge.invalid/api"
    server_url = "https://forge.invalid"

    def __init__(
        self,
        journal: list[str],
        *,
        repo: str | None = "molt/molt",
        open_pull: PullRef | None = None,
        created_number: int = 7,
        duplicate_releases: bool = False,
        failing_releases: Sequence[str] = (),
    ) -> None:
        self.repo = repo
        self.journal = journal
        self.calls: list[Call] = []
        self._open_pull = open_pull
        self._created_number = created_number
        self._duplicate_releases = duplicate_releases
        #: Tags whose ``create_release`` raises after recording the call. Additive and empty by
        #: default, so every construction site written before ``AP-7`` is untouched.
        self._failing_releases = frozenset(failing_releases)

    # -- the protocol ------------------------------------------------------------------

    def validate_repo(self, repo: str) -> None:
        self._record("validate_repo", repo)

    def commit_info(self, commit: str, *, repo: str | None = None) -> None:
        self._record("commit_info", commit, repo=repo)
        return None

    def pull_request_info(self, pull: int, *, repo: str | None = None) -> None:
        self._record("pull_request_info", pull, repo=repo)
        return None

    def create_release(
        self,
        tag: str,
        *,
        name: str,
        body: str,
        prerelease: bool = False,
        repo: str | None = None,
    ) -> ReleaseInfo | None:
        self._record("create_release", tag, name=name, body=body, prerelease=prerelease, repo=repo)
        # Recorded first, then raised: `AP-7`'s rows assert that molt *attempted* the releases
        # after the failing one, which is only observable if a failure is still a recorded call.
        if tag in self._failing_releases:
            raise MoltForgeError(f"the host refused the release for {tag}")
        if self._duplicate_releases:
            return None
        return ReleaseInfo(tag=tag, name=name, url=f"{self.server_url}/releases/{tag}", id=1)

    def find_open_pull_request(
        self, head: str, *, base: str, repo: str | None = None
    ) -> PullRef | None:
        self._record("find_open_pull_request", head, base=base, repo=repo)
        return self._open_pull

    def create_pull_request(
        self, head: str, *, base: str, title: str, body: str, repo: str | None = None
    ) -> PullRef:
        self._record("create_pull_request", head, base=base, title=title, body=body, repo=repo)
        return PullRef(
            number=self._created_number,
            url=f"{self.server_url}/molt/molt/pull/{self._created_number}",
        )

    def update_pull_request(
        self, number: int, *, title: str, body: str, repo: str | None = None
    ) -> PullRef:
        self._record("update_pull_request", number, title=title, body=body, repo=repo)
        return PullRef(number=number, url=f"{self.server_url}/molt/molt/pull/{number}")

    def create_commit(
        self,
        branch: str,
        *,
        base: str,
        message: str,
        additions: Mapping[str, bytes] = NO_FILE_ADDITIONS,
        deletions: Sequence[str] = (),
        repo: str | None = None,
    ) -> CommitRef | None:
        self._record(
            "create_commit",
            branch,
            base=base,
            message=message,
            additions=dict(additions),
            deletions=tuple(deletions),
            repo=repo,
        )
        return CommitRef(sha="c0ffee0", url=f"{self.server_url}/molt/molt/commit/c0ffee0")

    # -- recording ---------------------------------------------------------------------

    def _record(self, member: str, /, *args: Any, **kwargs: Any) -> None:
        """Record one call. ``member`` is positional-only: ``create_release`` has a ``name=``
        keyword of its own, and a second parameter spelled ``name`` would collide with it."""
        self.calls.append(Call(name=member, args=args, kwargs=kwargs))
        self.journal.append(f"forge.{member}")

    def named(self, name: str) -> list[Call]:
        """Every recorded call to ``name``."""
        return [call for call in self.calls if call.name == name]

    def only(self, name: str) -> Call:
        """The one recorded call to ``name``; fails if there is not exactly one."""
        matches = self.named(name)
        assert len(matches) == 1, f"expected exactly one {name} call, got {len(matches)}"
        return matches[0]


class RecordingGit:
    """A recording proxy over the **real** :class:`molt.git.Git`.

    A pure fake would make "the lookup happened before the push" observable but would stop proving
    that the push lands, and the ``git_repo`` + ``shallow_clone`` harness exists precisely so it
    does. Delegating through ``__getattr__`` keeps the double in step with the seam automatically:
    a method added to ``Git`` is recorded without editing this class, and one removed fails loudly
    rather than being silently swallowed by a hand-written stub.
    """

    def __init__(self, inner: Any, journal: list[str]) -> None:
        self._inner = inner
        self._journal = journal

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def recorded(*args: Any, **kwargs: Any) -> Any:
            self._journal.append(f"git.{name}")
            return attribute(*args, **kwargs)

        return recorded


class FakeGit:
    """A git seam for the rows that must not touch a repository at all.

    ``run_publish`` asks the seam for exactly one thing -- push the tags the publish command
    created -- and the publish rows are about what happens *after* that. Driving them through a
    real clone would add a second-per-row for no assertion.
    """

    def __init__(self, journal: list[str]) -> None:
        self._journal = journal

    def push_tags(self, *, remote: str = "origin") -> None:
        self._journal.append("git.push_tags")


# ======================================================================================
# Workspace fixtures
# ======================================================================================


def write_bytes(path: Path, content: str) -> None:
    """LF-only write, matching the ``git_repo`` fixture's own convention (Windows-correctness)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def root_manifest(*, workspace: bool = True) -> str:
    lines = ["[project]", 'name = "root-pkg"', 'version = "0.0.0"']
    if workspace:
        lines += ["", "[tool.uv.workspace]", 'members = ["packages/*"]']
    return "\n".join(lines) + "\n"


def member_manifest(name: str, version: str, *, private: bool = False) -> str:
    lines = ["[project]", f'name = "{name}"', f'version = "{version}"', "dependencies = []"]
    if private:
        lines.append('classifiers = ["Private :: Do Not Upload"]')
    return "\n".join(lines) + "\n"


def changeset(*releases: tuple[str, str], summary: str = "Something happened") -> str:
    """A changeset document naming ``(package, bump)`` pairs; no pairs means an empty changeset."""
    lines = ["---"]
    lines += [f'"{name}": {bump}' for name, bump in releases]
    lines += ["---", "", summary, ""]
    return "\n".join(lines)


def stub_script(tmp_path: Path, body: str, *, name: str = "stub.py") -> list[str]:
    """Write a python stub and return the argv that runs it.

    Stands in for the ``molt version`` / ``molt publish`` CLI, the same escape hatch upstream's own
    ``script=`` / ``command=`` inputs provide, and the shape ``tests/action/test_run.py`` uses.
    """
    script = tmp_path / name
    script.write_bytes(body.encode("utf-8"))
    return [sys.executable, str(script)]


#: Bumps all three members and writes each one's ``CHANGELOG.md`` -- what ``molt version`` would do
#: for the seeded changesets. The three bump levels differ on purpose: the body's ordering rule is
#: "public first, then highest level", and a fixture where visibility and level agree cannot tell
#: that rule from "highest level first".
STUB_VERSION = """
import pathlib

root = pathlib.Path.cwd()
BUMPS = {
    "pkg-a": ("1.0.1", "Patch Changes", "A small fix"),
    "pkg-b": ("2.0.0", "Major Changes", "A breaking change"),
    "pkg-c": ("1.1.0", "Minor Changes", "A new feature"),
}
for name, (version, heading, summary) in BUMPS.items():
    manifest = root / "packages" / name / "pyproject.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace('version = "1.0.0"', 'version = "%s"' % version)
    manifest.write_bytes(text.encode("utf-8"))
    changelog = root / "packages" / name / "CHANGELOG.md"
    changelog.write_bytes(
        ("# %s\\n\\n## %s\\n\\n### %s\\n\\n- %s\\n" % (name, version, heading, summary)).encode(
            "utf-8"
        )
    )
"""


@dataclass
class ReleaseRepo:
    """A cloned workspace with pending changesets, plus the two doubles and one shared journal."""

    origin: GitRepo
    clone: GitRepo
    script: list[str]
    journal: list[str]
    forge: RecordingForge
    git: RecordingGit

    def run(self, **overrides: Any) -> ActionResult:
        """Drive ``run_action`` against the clone with this harness's seams."""
        options: dict[str, Any] = {
            "cwd": self.clone.root,
            "version_script": self.script,
            "forge": self.forge,
            "git": self.git,
        }
        options.update(overrides)
        return run_action(**options)

    def head_message(self) -> str:
        """The subject of the commit the version run made on the release branch."""
        return self.clone.run("log", "-1", "--format=%s").stdout.strip()


def build_release_repo(
    tmp_path: Path, git_repo: GitRepo, *, open_pull: PullRef | None = None
) -> ReleaseRepo:
    """Seed a three-member workspace with one pending changeset and clone it.

    ``pkg-b`` carries the ``Private :: Do Not Upload`` classifier -- the Python analogue of npm's
    ``"private": true``, and the marker :func:`molt.ecosystem.is_private` reads. It takes the
    **largest** bump of the three, so a body ordered by level alone would put it first.
    """
    origin = git_repo
    write_bytes(origin.root / "pyproject.toml", root_manifest())
    write_bytes(
        origin.root / "packages" / "pkg-a" / "pyproject.toml", member_manifest("pkg-a", "1.0.0")
    )
    write_bytes(
        origin.root / "packages" / "pkg-b" / "pyproject.toml",
        member_manifest("pkg-b", "1.0.0", private=True),
    )
    write_bytes(
        origin.root / "packages" / "pkg-c" / "pyproject.toml", member_manifest("pkg-c", "1.0.0")
    )
    write_bytes(
        origin.root / ".changeset" / "strange-words-combine.md",
        changeset(("pkg-a", "patch"), ("pkg-b", "major"), ("pkg-c", "minor")),
    )
    origin.run("add", ".")
    origin.commit("chore: seed the workspace")
    clone = origin.shallow_clone(tmp_path / "clone")

    journal: list[str] = []
    return ReleaseRepo(
        origin=origin,
        clone=clone,
        script=stub_script(tmp_path, STUB_VERSION),
        journal=journal,
        forge=RecordingForge(journal, open_pull=open_pull),
        git=RecordingGit(Git(clone.root), journal),
    )


@pytest.fixture
def release_repo(tmp_path: Path, git_repo: GitRepo) -> ReleaseRepo:
    """The standard version-phase harness; see :func:`build_release_repo`."""
    return build_release_repo(tmp_path, git_repo)


def sections_of(body: str) -> list[str]:
    """Every ``## <name>@<version>`` heading in a pull-request body, in order."""
    return [line for line in body.splitlines() if line.startswith("## ")]


# ======================================================================================
# Rows 1-5: the mode matrix (index.ts:57-176)
# ======================================================================================


@pytest.mark.functional
def test_nothing_pending_and_nothing_to_publish_does_nothing(tmp_project: ProjectBuilder) -> None:
    """``index.ts:67-72`` -- the ordinary push that is not a release at all.

    Most pushes to the base branch land here, so this branch must be cheap and must touch nothing:
    no version script, no publish command, and above all no pull request. A loop that opened one
    "just in case" would put a permanent, empty release pull request on every repository that
    installs the action.
    """
    tmp_project.add_package("pkg-a")
    (tmp_project.root / ".changeset").mkdir(parents=True, exist_ok=True)
    journal: list[str] = []
    forge = RecordingForge(journal)

    result = run_action(cwd=tmp_project.root, forge=forge, git=FakeGit(journal))

    assert select_mode(tmp_project.root, publish=False) is Mode.NOTHING
    assert result == ActionResult(
        published=False, published_packages=(), has_changesets=False, pull_request_number=None
    )
    assert journal == [], "nothing to do means nothing is called"


@pytest.mark.integration
def test_nothing_pending_with_a_publish_command_publishes(
    tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """``index.ts:73-149`` -- the publish branch is reachable **only** with no changesets pending.

    That is exactly the state right after the release pull request merges and consumes them, which
    is the whole mechanism behind "one workflow file, both phases": the same job runs on every push
    and what the merge left behind decides which half it does.
    """
    tmp_project.add_package("pkg-a", version="1.1.0")
    (tmp_project.root / ".changeset").mkdir(parents=True, exist_ok=True)
    write_bytes(
        tmp_project.root / "packages" / "pkg-a" / "CHANGELOG.md",
        "# pkg-a\n\n## 1.1.0\n\n### Minor Changes\n\n- A new feature\n",
    )
    journal: list[str] = []
    forge = RecordingForge(journal)

    result = run_action(
        cwd=tmp_project.root,
        publish=stub_script(tmp_path, 'print("New tag: pkg-a@1.1.0")\n'),
        forge=forge,
        git=FakeGit(journal),
    )

    assert select_mode(tmp_project.root, publish=True) is Mode.PUBLISH
    assert result.published is True
    assert [(p.name, p.version) for p in result.published_packages] == [("pkg-a", "1.1.0")]
    assert result.pull_request_number is None, "the publish phase opens no pull request"


@pytest.mark.functional
@pytest.mark.parametrize("with_publish_command", [False, True])
def test_changesets_that_release_nothing_do_nothing(
    with_publish_command: bool, tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """``index.ts:150-152`` -- pending changesets that name no package open no pull request.

    An empty changeset is what ``molt add --empty`` writes, to record "this change needs no
    release". Deciding the branch on ``changesets.length`` instead of on whether any of them
    releases anything opens a release pull request with an empty ``# Releases`` section, and the
    version script would have nothing to do.

    Parametrized over both publish configurations because the answer is the same either way: this
    branch is tested *before* the publish branch upstream, so a mis-ordered matrix would publish
    on a repository that still has changesets pending.
    """
    tmp_project.add_package("pkg-a")
    write_bytes(tmp_project.root / ".changeset" / "quiet-lions-give.md", changeset())
    journal: list[str] = []
    forge = RecordingForge(journal)
    publish = stub_script(tmp_path, "pass\n") if with_publish_command else None

    result = run_action(cwd=tmp_project.root, publish=publish, forge=forge, git=FakeGit(journal))

    assert select_mode(tmp_project.root, publish=with_publish_command) is Mode.NOTHING
    assert result.published is False
    assert result.pull_request_number is None
    assert journal == [], "an empty changeset triggers neither phase"


@pytest.mark.functional
def test_a_changeset_with_releases_selects_the_version_phase(tmp_project: ProjectBuilder) -> None:
    """``index.ts:153-175`` -- one changeset naming one package is enough, and it wins.

    The version branch is tested last upstream and shadows the publish branch: a run that has
    something to release must **never** also publish, because the versions on disk are the ones the
    previous release wrote, not the ones this changeset asks for.
    """
    tmp_project.add_package("pkg-a")
    write_bytes(
        tmp_project.root / ".changeset" / "strange-words-combine.md", changeset(("pkg-a", "minor"))
    )

    assert select_mode(tmp_project.root, publish=False) is Mode.VERSION
    assert select_mode(tmp_project.root, publish=True) is Mode.VERSION, (
        "a configured publish command must not divert a run that has something to version"
    )


@pytest.mark.functional
def test_pending_changesets_are_reported_even_when_nothing_runs(
    tmp_project: ProjectBuilder,
) -> None:
    """Design D1 -- "are changesets pending?" and "is there anything to release?" are two questions.

    Upstream reports the output from ``changesets.length !== 0`` and branches on
    ``changesets.some(c => c.releases.length > 0)``. A repository holding only empty changesets
    therefore reports **pending** and opens no pull request. Collapsing the two answers either
    claims nothing is pending while ``.changeset/`` is not empty, or opens an empty release pull
    request.
    """
    tmp_project.add_package("pkg-a")
    write_bytes(tmp_project.root / ".changeset" / "quiet-lions-give.md", changeset())
    journal: list[str] = []

    result = run_action(cwd=tmp_project.root, forge=RecordingForge(journal), git=FakeGit(journal))

    assert result.has_changesets is True, "the changeset is there, empty or not"
    assert result.pull_request_number is None, "and it still opens no pull request"


# ======================================================================================
# Rows 6-12: the version phase (run.ts:333-407)
# ======================================================================================


@pytest.mark.integration
@pytest.mark.git
@pytest.mark.slow
def test_a_version_run_with_no_open_pull_request_creates_one(release_repo: ReleaseRepo) -> None:
    """``run.ts:363-370`` -- the first run of a release cycle opens the pull request.

    The head is the branch ``run_version`` just force-pushed and the base is the branch being
    released; getting either wrong opens a pull request that merges the wrong direction. The number
    is reported so a workflow can comment on it.
    """
    result = release_repo.run()

    created = release_repo.forge.only("create_pull_request")
    assert created.args == ("changeset-release/main",)
    assert created.kwargs["base"] == "main"
    assert created.kwargs["title"] == "Version Packages"
    assert "# Releases" in created.kwargs["body"]
    assert release_repo.forge.named("update_pull_request") == []
    assert result.pull_request_number == 7
    assert result.has_changesets is True
    assert result.published is False, "a version run publishes nothing"


@pytest.mark.integration
@pytest.mark.git
@pytest.mark.slow
def test_a_version_run_with_an_open_pull_request_updates_that_number(
    tmp_path: Path, git_repo: GitRepo
) -> None:
    """``run.ts:361-374`` -- the release pull request is reused, never re-created.

    This is the product behavior, not an optimization: the release pull request accumulates
    changesets over days, and its number, its review comments, its approvals and its subscribers
    all have to survive every update. Opening a second one strands the first as a stale, open,
    conflicting pull request that a maintainer must close by hand.
    """
    repo = build_release_repo(
        tmp_path, git_repo, open_pull=PullRef(number=42, url="https://forge.invalid/pull/42")
    )

    result = repo.run()

    updated = repo.forge.only("update_pull_request")
    assert updated.args == (42,)
    assert updated.kwargs["title"] == "Version Packages"
    assert "## pkg-a@1.0.1" in updated.kwargs["body"]
    assert repo.forge.named("create_pull_request") == [], "reuse means no second pull request"
    assert result.pull_request_number == 42


@pytest.mark.integration
@pytest.mark.git
@pytest.mark.slow
def test_the_open_pull_request_lookup_precedes_any_push(release_repo: ReleaseRepo) -> None:
    """Design D3 -- the ordering, asserted rather than intended.

    Upstream looks open pull requests up "before we push any changes that may inadvertently close
    the existing PRs" (``run.ts:346``). A force-push onto the release branch can close the pull
    request, and a closed one is not found by an ``state=open`` query -- so a lookup that ran
    afterwards would find nothing, open a **second** pull request, and orphan the first one's
    review history.

    Every content assertion in this file still passes with the lookup moved after ``run_version``,
    which is exactly why this row records the call order across both doubles instead.
    """
    release_repo.run()

    journal = release_repo.journal
    assert "forge.find_open_pull_request" in journal
    lookup = journal.index("forge.find_open_pull_request")
    for write in ("git.switch_to_maybe_existing_branch", "git.commit", "git.push"):
        assert write in journal, f"the version run must have called {write}"
        assert lookup < journal.index(write), f"the lookup must precede {write}: {journal}"


@pytest.mark.integration
@pytest.mark.git
@pytest.mark.slow
def test_the_title_and_commit_message_default_to_version_packages(
    release_repo: ReleaseRepo,
) -> None:
    """``run.ts:257-258`` -- one default string, used in two places.

    The commit message is asserted off the repository rather than off the call, because it is
    ``run_version`` that writes it and this loop only chooses it.
    """
    release_repo.run()

    assert release_repo.forge.only("create_pull_request").kwargs["title"] == "Version Packages"
    assert release_repo.head_message() == "Version Packages"


@pytest.mark.integration
@pytest.mark.git
@pytest.mark.slow
@pytest.mark.parametrize(
    ("overrides", "expected_title", "expected_commit"),
    [
        pytest.param({"title": "chore: release"}, "chore: release", "Version Packages", id="title"),
        pytest.param(
            {"commit_message": "chore(release): version packages"},
            "Version Packages",
            "chore(release): version packages",
            id="commit-message",
        ),
    ],
)
def test_a_custom_title_and_a_custom_commit_message_are_independent(
    overrides: dict[str, str],
    expected_title: str,
    expected_commit: str,
    tmp_path: Path,
    git_repo: GitRepo,
) -> None:
    """``run.ts:257-258`` -- two separate settings, and they stay separate.

    A repository with a commit-message convention (conventional commits, a ticket prefix) needs the
    commit renamed **without** renaming the pull request its reviewers recognise, and a repository
    that renames the pull request must not silently rewrite its commit history's subject line. One
    input feeding both would make either impossible.
    """
    repo = build_release_repo(tmp_path, git_repo)

    repo.run(**overrides)

    assert repo.forge.only("create_pull_request").kwargs["title"] == expected_title
    assert repo.head_message() == expected_commit


@pytest.mark.integration
@pytest.mark.git
@pytest.mark.slow
def test_the_body_carries_one_section_per_changed_package(release_repo: ReleaseRepo) -> None:
    """``run.ts:213-221`` and ``:333-344`` -- a section per package, with its changelog entry.

    The sections are a **permutation** of the packages ``run_version`` reported releasing, never a
    filtered subset (design D4): that answer is already "these were released", and filtering it a
    second time here would drop a release out of a public document. The private member is in the
    body for the same reason -- it was released, and the pull request records what merging it does.
    """
    release_repo.run()

    body = release_repo.forge.only("create_pull_request").kwargs["body"]
    assert sorted(sections_of(body)) == [
        "## pkg-a@1.0.1",
        "## pkg-b@2.0.0",
        "## pkg-c@1.1.0",
    ]
    assert "- A small fix" in body
    assert "- A breaking change" in body
    assert "- A new feature" in body
    assert "main" in body, "the header names the branch the pull request targets"


@pytest.mark.integration
@pytest.mark.git
@pytest.mark.slow
def test_the_sections_are_ordered_public_first_then_highest_bump(
    release_repo: ReleaseRepo,
) -> None:
    """``utils.ts:86-97`` -- the already-shipped ordering rule, reused rather than re-derived.

    The fixture is built so that the two halves of the rule disagree: the private member takes the
    **major** bump and the public ones take minor and patch. Ordering by level alone puts
    ``pkg-b`` first; ordering public-first but ascending puts ``pkg-a`` before ``pkg-c``. Only the
    real rule produces this sequence.
    """
    release_repo.run()

    body = release_repo.forge.only("create_pull_request").kwargs["body"]
    assert sections_of(body) == [
        "## pkg-c@1.1.0",
        "## pkg-a@1.0.1",
        "## pkg-b@2.0.0",
    ]


#: Every local git write the version phase performs in the default mode. API mode must perform
#: **none** of them, which is the whole of the "no local branch, no local commit and no push"
#: requirement -- and the reason it is a list rather than a single assertion is that dropping any
#: one of them individually would still leave a run that writes locally.
LOCAL_GIT_WRITES = (
    "git.switch_to_maybe_existing_branch",
    "git.reset",
    "git.add",
    "git.commit",
    "git.push",
)


@pytest.mark.integration
@pytest.mark.git
@pytest.mark.slow
def test_api_mode_makes_no_local_git_writes_and_sends_one_api_commit(
    release_repo: ReleaseRepo,
) -> None:
    """The whole of "API mode makes no local branch, no local commit and no push", in one row.

    Ports ``changesets/action`` v1.9.0 ``git.ts``: ``prepareBranch`` returns early -- *"Preparing a
    new local branch is not necessary when using the API"* -- and ``pushChanges`` replaces
    ``git add .`` + ``git commit`` + ``git push --force`` with one API call.

    Skipping the branch prep is not tidiness. A local branch switch would change which tree the
    changes are measured against, and molt measures them against the commit that was checked out
    before the script ran.

    The pull-request half is asserted here too, because it is what a reader will assume changed and
    it did not: the same lookup runs first and the same create-or-update follows.
    """
    result = release_repo.run(commit_mode=CommitMode.API)

    journal = release_repo.journal
    for write in LOCAL_GIT_WRITES:
        assert write not in journal, f"API mode must not call {write}: {journal}"
    assert journal.count("forge.create_commit") == 1, journal

    commit = release_repo.forge.only("create_commit")
    assert commit.args == ("changeset-release/main",)
    assert commit.kwargs["message"] == "Version Packages"
    assert commit.kwargs["base"] == release_repo.clone.run("rev-parse", "HEAD").stdout.strip()
    assert result.pull_request_number == 7, "the release pull request is opened exactly as before"


@pytest.mark.integration
@pytest.mark.git
@pytest.mark.slow
def test_api_mode_commits_what_the_version_run_wrote(release_repo: ReleaseRepo) -> None:
    """The commit carries the release, not an empty change set.

    The three manifests the stub rewrote arrive as additions and each new ``CHANGELOG.md`` arrives
    with them, which is the half that only works because the untracked read exists. The consumed
    changeset is a deletion.

    Asserted on the *payload* rather than on "a commit happened", because a run that sent an empty
    ``fileChanges`` would satisfy the row above and would put an empty commit on the release
    branch.
    """
    release_repo.run(commit_mode=CommitMode.API)

    commit = release_repo.forge.only("create_commit")
    additions = commit.kwargs["additions"]

    assert sorted(additions) == [
        "packages/pkg-a/CHANGELOG.md",
        "packages/pkg-a/pyproject.toml",
        "packages/pkg-b/CHANGELOG.md",
        "packages/pkg-b/pyproject.toml",
        "packages/pkg-c/CHANGELOG.md",
        "packages/pkg-c/pyproject.toml",
    ]
    assert b'version = "1.0.1"' in additions["packages/pkg-a/pyproject.toml"]
    assert isinstance(additions["packages/pkg-a/CHANGELOG.md"], bytes)
    assert commit.kwargs["deletions"] == ()


@pytest.mark.integration
@pytest.mark.git
@pytest.mark.slow
def test_the_default_mode_still_commits_and_pushes_locally(release_repo: ReleaseRepo) -> None:
    """The negative control for the row above -- and the point of the default.

    Without it, "API mode makes no local git writes" would also pass against a version phase that
    had stopped writing locally in **both** modes. The two rows share one fixture on purpose, so
    the only difference between them is the mode.
    """
    release_repo.run()

    journal = release_repo.journal
    for write in LOCAL_GIT_WRITES:
        assert write in journal, f"the default mode must still call {write}: {journal}"
    assert release_repo.forge.named("create_commit") == [], "and must not reach the commit API"


# ======================================================================================
# Rows 13-14: the truncation tiers (run.ts:222-242)
# ======================================================================================


def oversized_entries(count: int, content_characters: int) -> list[dict[str, Any]]:
    """``count`` entries, each carrying ``content_characters`` of changelog content."""
    return [
        pull_request_entry(
            name=f"pkg-{index:03d}",
            version="1.0.0",
            content="x" * content_characters,
            highest_level=1,
            private=False,
        )
        for index in range(count)
    ]


@pytest.mark.unit
def test_an_oversized_body_degrades_to_headers_and_a_note() -> None:
    """``run.ts:222-233`` -- tier 2 keeps every heading and drops every changelog entry.

    GitHub rejects a body over 65536 characters, so without this a large monorepo release opens
    **no pull request at all** -- the API refuses the body and the version phase fails on exactly
    the release that matters most. The headings survive because "which packages does merging this
    release?" is the question the body exists to answer; the changelog text is what a reader can
    still find in the files.
    """
    entries = oversized_entries(40, 2000)

    body = build_pull_request_body(entries=entries, base_branch="main", has_publish_command=True)

    assert len(body) <= MAX_BODY_CHARACTERS
    assert "omitted from this message" in body
    assert len(sections_of(body)) == 40, "every heading survives tier 2"
    assert "xxxx" not in body, "and every changelog entry is gone"


@pytest.mark.unit
def test_a_still_oversized_body_degrades_to_a_single_note() -> None:
    """``run.ts:234-242`` -- tier 3, when even the headings do not fit.

    A release of thousands of packages is the case, and the alternative is not a shorter pull
    request but no pull request. The explanatory header and the ``# Releases`` heading stay in
    every tier so the reader always learns what this pull request is and why it looks empty.
    """
    entries = oversized_entries(4000, 10)

    body = build_pull_request_body(entries=entries, base_branch="main", has_publish_command=False)

    assert len(body) <= MAX_BODY_CHARACTERS
    assert "All release information have been omitted" in body
    assert sections_of(body) == [], "tier 3 keeps no per-package information at all"
    assert "# Releases" in body


# ======================================================================================
# Rows 15-19: the publish phase (run.ts:30-58, :118-149; index.ts:137-147)
# ======================================================================================


PUBLISH_STUB = """
print("New tag: pkg-a@1.1.0")
print("New tag: pkg-b@2.0.0")
"""


def seed_published_workspace(
    project: ProjectBuilder, *, changelogs: dict[str, str] | None = None
) -> None:
    """Two members at their just-published versions, each with a changelog for that version."""
    project.add_package("pkg-a", version="1.1.0")
    project.add_package("pkg-b", version="2.0.0")
    written = changelogs if changelogs is not None else DEFAULT_PUBLISHED_CHANGELOGS
    for name, text in written.items():
        write_bytes(project.root / "packages" / name / "CHANGELOG.md", text)


DEFAULT_PUBLISHED_CHANGELOGS = {
    "pkg-a": "# pkg-a\n\n## 1.1.0\n\n### Minor Changes\n\n- A new feature\n",
    "pkg-b": "# pkg-b\n\n## 2.0.0\n\n### Major Changes\n\n- A breaking change\n",
}


@pytest.mark.integration
def test_a_publish_run_creates_one_release_per_published_package(
    tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """``run.ts:118-149`` -- one host release per package, carrying **that** package's notes.

    The body is each package's own changelog section, sliced back out of the file the version run
    wrote. A loop that sent the same body to every release -- the whole changelog, or the first
    package's entry -- would pass a single-package fixture and be wrong on every monorepo.

    No tag is pushed here (design D6): ``run_publish`` already pushed all of them in one
    ``git push origin --tags``, which is why ``git.push_tags`` appears once in the journal and
    nothing else does.
    """
    seed_published_workspace(tmp_project)
    journal: list[str] = []
    forge = RecordingForge(journal)

    result = run_action(
        cwd=tmp_project.root,
        publish=stub_script(tmp_path, PUBLISH_STUB),
        forge=forge,
        git=FakeGit(journal),
    )

    releases = forge.named("create_release")
    assert [call.args[0] for call in releases] == ["pkg-a@1.1.0", "pkg-b@2.0.0"]
    assert releases[0].kwargs["name"] == "pkg-a@1.1.0"
    assert releases[0].kwargs["body"] == "### Minor Changes\n\n- A new feature"
    assert releases[1].kwargs["body"] == "### Major Changes\n\n- A breaking change"
    assert releases[0].kwargs["prerelease"] is False
    assert result.published is True
    assert journal.count("git.push_tags") == 1, "the loop pushes no tags of its own"


@pytest.mark.integration
def test_host_releases_can_be_switched_off_without_losing_the_publish_report(
    tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """Upstream's ``createGithubReleases`` input, default **true**.

    The switch is about the code host, not about the release: a repository that keeps its release
    notes somewhere else still publishes, and the run must still report what went out or the
    workflow's downstream steps have nothing to act on.
    """
    seed_published_workspace(tmp_project)
    journal: list[str] = []
    forge = RecordingForge(journal)

    result = run_action(
        cwd=tmp_project.root,
        publish=stub_script(tmp_path, PUBLISH_STUB),
        create_releases=False,
        forge=forge,
        git=FakeGit(journal),
    )

    assert forge.calls == [], "switched off means the host is never called"
    assert result.published is True
    assert [(p.name, p.version) for p in result.published_packages] == [
        ("pkg-a", "1.1.0"),
        ("pkg-b", "2.0.0"),
    ]


@pytest.mark.integration
@pytest.mark.parametrize("workspace", [True, False], ids=["workspace", "single-package"])
def test_the_release_tag_follows_the_repository_shape(
    workspace: bool, tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """``molt git-tag`` design D2 -- ``<name>@<version>`` in a workspace, ``v<version>`` alone.

    The release points at a tag that another part of molt created, so the two spellings must agree
    exactly: a release created against a tag that does not exist is a broken link on the host, and
    the single-package shape is the one a repository with no workspace declaration gets.
    """
    if workspace:
        seed_published_workspace(tmp_project)
        command = stub_script(tmp_path, PUBLISH_STUB)
        expected = ["pkg-a@1.1.0", "pkg-b@2.0.0"]
    else:
        write_bytes(
            tmp_project.root / "pyproject.toml",
            '[project]\nname = "single-package"\nversion = "1.4.0"\n',
        )
        write_bytes(
            tmp_project.root / "CHANGELOG.md",
            "# single-package\n\n## 1.4.0\n\n### Minor Changes\n\n- A new feature\n",
        )
        command = stub_script(tmp_path, 'print("New tag: v1.4.0")\n')
        expected = ["v1.4.0"]
    journal: list[str] = []
    forge = RecordingForge(journal)

    run_action(cwd=tmp_project.root, publish=command, forge=forge, git=FakeGit(journal))

    assert [call.args[0] for call in forge.named("create_release")] == expected


@pytest.mark.integration
@pytest.mark.parametrize("keeps_changelog", [False, True], ids=["no-changelog", "wrong-version"])
def test_a_published_package_without_its_changelog_section(
    keeps_changelog: bool, tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """``run.ts:30-58`` -- two rules that pull in opposite directions, and both are ported.

    **No ``CHANGELOG.md`` at all** is what "this project switched changelogs off" looks like on
    disk, so the package is skipped and the run carries on; failing there would make changelogs
    effectively mandatory. **A ``CHANGELOG.md`` with no section for the published version** is a
    failure: the file exists, so somebody meant to write release notes and they are not there, and
    a release published with an empty body buries that silently.

    The failure names the package as well as the version -- "no section for 2.0.0" in a release of
    thirty packages is not an actionable sentence on its own.
    """
    changelogs = dict(DEFAULT_PUBLISHED_CHANGELOGS)
    if keeps_changelog:
        changelogs["pkg-b"] = "# pkg-b\n\n## 1.0.0\n\n### Patch Changes\n\n- An older release\n"
    else:
        del changelogs["pkg-b"]
    seed_published_workspace(tmp_project, changelogs=changelogs)
    journal: list[str] = []
    forge = RecordingForge(journal)

    def publish() -> ActionResult:
        return run_action(
            cwd=tmp_project.root,
            publish=stub_script(tmp_path, PUBLISH_STUB),
            forge=forge,
            git=FakeGit(journal),
        )

    if keeps_changelog:
        with pytest.raises(MoltError) as excinfo:
            publish()
        message = str(excinfo.value)
        assert "pkg-b" in message and "2.0.0" in message
    else:
        result = publish()
        assert [call.args[0] for call in forge.named("create_release")] == ["pkg-a@1.1.0"]
        assert result.published is True, "the package still published; only its release is skipped"


#: A publish command that announces one package and then exits 3 -- the half-failed publish every
#: ``AP-14`` / ``AP-7`` row below is written against.
FAILING_PUBLISH_STUB = 'import sys\nprint("New tag: pkg-a@1.1.0")\nsys.exit(3)\n'


@pytest.mark.integration
def test_a_failing_publish_command_fails_the_run(
    tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """``index.ts:137-147`` -- a failing publish fails the run, **after** reporting what went out.

    Renegotiated by owner ruling 2026-07-31 (session 6), closing gap ``AP-14``. molt used to raise
    inside ``run_publish`` the moment the command exited non-zero, which happened before the tag
    push and before the output scrape -- so a publish that uploaded three packages out of five
    reported none of them and created no release for any of them. That was molt's divergence, not
    upstream's: ``index.ts:137-147`` reports the partial publish.

    The run still fails, and still with the publish command's own status. What changed is
    everything that happens first: the tags are pushed, the ``New tag:`` lines are read, and a host
    release is created for every package that did reach the index -- because by then those packages
    are public and a release is only a pointer to them.
    """
    seed_published_workspace(tmp_project)
    journal: list[str] = []
    forge = RecordingForge(journal)
    failing = stub_script(tmp_path, FAILING_PUBLISH_STUB)

    with pytest.raises(ExitError) as excinfo:
        run_action(cwd=tmp_project.root, publish=failing, forge=forge, git=FakeGit(journal))

    assert excinfo.value.code == 3, "the publish command's own status reaches the workflow"
    assert [call.args[0] for call in forge.named("create_release")] == ["pkg-a@1.1.0"], (
        "the package that did go out still gets its release"
    )
    assert "git.push_tags" in journal, "and the tags it did create are still pushed"


@pytest.mark.integration
def test_a_failing_publish_that_published_nothing_creates_no_release(
    tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """A publish that announced nothing has nothing to report (``AP-14``, ruled 2026-07-31).

    The other end of the row above: reporting a package molt never observed would be the same
    mistake in the other direction.
    """
    seed_published_workspace(tmp_project)
    journal: list[str] = []
    forge = RecordingForge(journal)
    failing = stub_script(tmp_path, "import sys\nsys.exit(4)\n")

    with pytest.raises(ExitError) as excinfo:
        run_action(cwd=tmp_project.root, publish=failing, forge=forge, git=FakeGit(journal))

    assert excinfo.value.code == 4
    assert forge.named("create_release") == []


@pytest.mark.integration
def test_a_failing_publish_with_releases_switched_off_still_fails(
    tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """``create_releases=False`` changes the host, never the run's status (``AP-14``)."""
    seed_published_workspace(tmp_project)
    journal: list[str] = []
    forge = RecordingForge(journal)
    failing = stub_script(tmp_path, FAILING_PUBLISH_STUB)

    with pytest.raises(ExitError) as excinfo:
        run_action(
            cwd=tmp_project.root,
            publish=failing,
            create_releases=False,
            forge=forge,
            git=FakeGit(journal),
        )

    assert excinfo.value.code == 3
    assert forge.named("create_release") == []


@pytest.mark.integration
def test_one_failed_release_does_not_stop_the_others(
    tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """``AP-7``, ruled 2026-08-01 -- keep going, then fail naming every failure.

    Upstream's ``run.ts:118-149`` has no ``try`` around its release loop either, so molt shipped a
    faithful port; the ruling trades that faithfulness for the operator's own question, which is
    "which releases am I missing?". By the time one ``create_release`` raises, every package in the
    loop is already on the index, so stopping loses information that cannot be recovered.

    The **first** release is the one that fails on purpose. Failing the last would pass even with
    no ``try`` at all.
    """
    seed_published_workspace(tmp_project)
    journal: list[str] = []
    forge = RecordingForge(journal, failing_releases=["pkg-a@1.1.0"])

    with pytest.raises(ExitError) as excinfo:
        run_action(
            cwd=tmp_project.root,
            publish=stub_script(tmp_path, PUBLISH_STUB),
            forge=forge,
            git=FakeGit(journal),
        )

    assert [call.args[0] for call in forge.named("create_release")] == [
        "pkg-a@1.1.0",
        "pkg-b@2.0.0",
    ], "the release after the failing one was still attempted"
    assert excinfo.value.code == 1, "the publish itself succeeded, so this is molt's own failure"
    assert "pkg-a" in str(excinfo.value), "the failed package is named"


@pytest.mark.integration
def test_a_failing_release_and_a_failing_publish_report_both(
    tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """When both failed, the **publish command's** status wins and the release is still named.

    A child's status is propagated verbatim -- that contract predates ``AP-7`` and CI branches on
    it -- so the code is 3, not molt's own 1.
    """
    seed_published_workspace(tmp_project)
    journal: list[str] = []
    forge = RecordingForge(journal, failing_releases=["pkg-a@1.1.0"])

    with pytest.raises(ExitError) as excinfo:
        run_action(
            cwd=tmp_project.root,
            publish=stub_script(tmp_path, FAILING_PUBLISH_STUB),
            forge=forge,
            git=FakeGit(journal),
        )

    assert excinfo.value.code == 3
    assert "pkg-a" in str(excinfo.value)


@pytest.mark.integration
def test_a_failing_publish_carries_its_partial_result(
    tmp_path: Path, tmp_project: ProjectBuilder
) -> None:
    """The failure carries what the run observed, so the entry point can still write the outputs.

    Owner ruling 2026-08-01: after a release that half-published, an ``if: always()`` step must be
    able to read the packages that did go out. :class:`molt.action.ActionFailed` is the carrier and
    this row pins it one layer below the file writer -- ``tests/action/test_cli.py`` pins the write
    itself.
    """
    seed_published_workspace(tmp_project)
    journal: list[str] = []
    forge = RecordingForge(journal)

    with pytest.raises(ActionFailed) as excinfo:
        run_action(
            cwd=tmp_project.root,
            publish=stub_script(tmp_path, FAILING_PUBLISH_STUB),
            forge=forge,
            git=FakeGit(journal),
        )

    failure = excinfo.value
    assert isinstance(failure, ExitError), "every pinned row asserting ExitError still holds"
    assert failure.code == 3
    assert [package.name for package in failure.result.published_packages] == ["pkg-a"]


def test_the_body_header_links_to_molts_repository() -> None:
    """``AP-8``, ruled 2026-07-31 -- the header names molt and links to its repository.

    Asserted against the imported constant rather than a copied URL: a copy here would let the
    two drift and still pass.
    """
    body = build_pull_request_body(entries=[], base_branch="main", has_publish_command=False)

    assert MOLT_REPOSITORY_URL in body


@pytest.mark.integration
@pytest.mark.parametrize("with_publish", [True, False], ids=["publish", "no-publish"])
def test_a_repository_with_no_changeset_directory_has_nothing_pending(
    tmp_path: Path, tmp_project: ProjectBuilder, with_publish: bool
) -> None:
    """``AP-15``, ratified as shipped 2026-08-01 -- an absent ``.changeset/`` is "nothing pending".

    Every ``molt`` verb refuses a missing changeset directory and says to run ``molt init``,
    because a person typing a command is working in a project they believe is initialized. The
    action is the one caller for which that is wrong: a repository whose release pull request
    merged with ``.changeset/`` deleted -- or one that has never used changesets at all -- must
    still run the publish half.

    A malformed changeset **file** still raises; that half is ``read_changesets``' own contract and
    is pinned in ``tests/changeset``.
    """
    seed_published_workspace(tmp_project)
    changeset_dir = tmp_project.root / ".changeset"
    if changeset_dir.is_dir():
        for entry in changeset_dir.iterdir():
            entry.unlink()
        changeset_dir.rmdir()
    assert not changeset_dir.exists()

    journal: list[str] = []
    forge = RecordingForge(journal)
    publish = stub_script(tmp_path, PUBLISH_STUB) if with_publish else None

    result = run_action(cwd=tmp_project.root, publish=publish, forge=forge, git=FakeGit(journal))

    assert result.has_changesets is False
    if with_publish:
        assert result.published is True, "the publish half still runs"
    else:
        assert result.published is False, "and with no publish command the run does nothing"
        assert forge.calls == []
