"""Conformance tests for the git-orchestration half of ``molt.action`` -- ``run_version`` /
``run_publish``.

Ports ``packages/release-utils/src/run.test.ts`` (5 rows, all Adapt) -- the first ``release-utils``
section of ``roadmap/research/test-suite/08-infra-forge-utils.md`` and research doc 04 section 6.1
(``runVersion``) / 6.2 (``runPublish``). These are the heaviest integration tests in the group: a
real tmp repo is **cloned** so there is a working remote to push to, the version/publish CLI is
stubbed, and results are read back off the *origin*, not the clone -- exactly the shape upstream's
``gitdir()`` + ``shallowClone()`` harness uses.

Provenance (research README section 8; phase brief section 5)
----------------------------------------------------------------
``runVersion`` / ``runPublish`` -- including the ``changeset-release/${branch}`` branch-naming
rule (``run.ts:112``) and the force-push (``run.ts:146``) -- are **in the local checkout** and
documented in research doc 04 section 6.1/6.2, cited by ``file:line`` below. They are *not* part of
the unverified outer PR loop (doc 04 section 6.4-6.6: PR creation/update, PR title/body,
``hasChangesets`` branching, ``setup-git-user`` -- the genuinely un-local part, reconstructed from
the separate ``changesets/action`` repository). None of the 5 rows in this file touch that outer
loop, so **none of them carry the ``xfail`` marker** -- they assert only what ``release-utils``
itself documents and this checkout contains.

npm -> uv substitutions (phase brief section 4)
-------------------------------------------------
``package.json`` -> ``pyproject.toml``; ``@manypkg/get-packages`` -> a workspace discovery
keyed on ``[tool.uv.workspace]``; the npm-object ``changedPackages`` shape -> ``ChangedPackage``
(``dir`` / ``relative_dir`` / ``manifest``, snake_case per project decision 6); the ``node -e``
publish/version stub -> a ``python`` script file, invoked the same way upstream invokes its stub:
as an explicit ``script=`` / ``command=`` argv the action shells out to. Unlike upstream (whose own
``run.test.ts`` version rows omit ``script`` and thereby exercise the *real* changesets CLI end to
end), every row here supplies an explicit stub: ``molt.engine`` / ``molt.apply`` / the ``molt
version`` CLI are separate, later TDD targets (build steps 4-6), and ``run_version``'s own job --
branch switching, before/after version snapshotting, committing, force-pushing -- is fully
observable without them. ``run_version``'s ``script`` accepts an **argv list**, not a bare command
string, which is a deliberate, documented fix of upstream bug 9.21 (``run.ts:121`` runs a
bare-string script with no arguments).

ASSUMED SEAM, flagged for owner sign-off
-------------------------------------------
``ChangedPackage`` / ``PublishedPackage`` / ``RunVersionResult`` / ``RunPublishResult`` are accessed
by **attribute** (``.dir``, ``.relative_dir``, ``.manifest``, ``.name``, ``.version``,
``.version_branch``, ``.changed_packages``, ``.published``, ``.published_packages``), matching the
dataclass-attribute style every other ``molt`` domain object in this codebase uses (``Release``,
``ReleasePlan``, etc. -- test-contract section 5). ``.manifest`` is the parsed ``pyproject.toml``
document (a plain ``dict``), the direct analogue of upstream's ``packageJson`` field.

Why no ``ignore=`` parameter on ``run_version``
--------------------------------------------------
Upstream's ``getChangedPackages`` (``utils.ts:16-31``) takes no ``ignore`` argument at all --
excluding an ignored package is a side effect of ``changeset version`` (owned by
``molt.engine``/``molt.apply``) never bumping that package's version in the first place, not
something ``run_version`` filters for itself. Row 3 below is written the same way: the stub leaves
the ignored package's manifest completely untouched, so ``run_version``'s own before/after version
diff excludes it with no special-case logic -- the same mechanism upstream relies on.

Markers: ``integration`` + ``git`` + ``slow`` (every row clones a repo), per the phase brief and
``pyproject.toml``'s own description of ``slow`` ("expensive test excluded from the fast default
loop").
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("molt.action", reason="build step 9 - molt.action is a TDD target")

from molt.action import run_publish, run_version
from molt.errors import ExitError

if TYPE_CHECKING:
    from tests.conftest import GitRepo

pytestmark = [pytest.mark.integration, pytest.mark.git, pytest.mark.slow]


# ======================================================================================
# Manifest builders -- a uv-workspace analogue of upstream's inline `JSON.stringify(...)` fixtures.
# ======================================================================================


def root_manifest(*, name: str = "root-pkg", workspace: bool = True) -> str:
    """A root ``pyproject.toml``; ``workspace=True`` adds ``[tool.uv.workspace]`` (a monorepo)."""
    lines = ["[project]", f'name = "{name}"', 'version = "0.0.0"']
    if workspace:
        lines += ["", "[tool.uv.workspace]", 'members = ["packages/*"]']
    return "\n".join(lines) + "\n"


def member_manifest(name: str, version: str, *, dependencies: list[str] | None = None) -> str:
    """A ``packages/<name>/pyproject.toml``. ``dependencies`` are PEP 508 strings (a list)."""
    lines = [
        "[project]",
        f'name = "{name}"',
        f'version = "{version}"',
        f"dependencies = {json.dumps(list(dependencies) if dependencies else [])}",
    ]
    return "\n".join(lines) + "\n"


#: pkg-b for row 2 -- carries a hand-placed comment so "preserved un-bumped manifest formatting"
#: (row 2's own wording) is an assertion, not a coincidence of a formatter-free writer.
PKG_B_WITH_COMMENT = (
    '[project]\nname = "pkg-b"\nversion = "1.0.0"  # do not touch this file\ndependencies = []\n'
)


def write_bytes(path: Path, content: str) -> None:
    """LF-only write, matching the ``git_repo`` fixture's own convention (Windows-correctness)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def seed_workspace(repo: GitRepo, members: dict[str, str], *, root_text: str | None = None) -> None:
    """Write a root manifest plus every ``packages/<name>/pyproject.toml`` and commit them."""
    write_bytes(
        repo.root / "pyproject.toml", root_text if root_text is not None else root_manifest()
    )
    for name, manifest in members.items():
        write_bytes(repo.root / "packages" / name / "pyproject.toml", manifest)
    repo.run("add", ".")
    repo.commit("chore: seed packages")


def stub_script(tmp_path: Path, body: str, *, name: str = "stub.py") -> list[str]:
    """Write a python stub file and return the argv that invokes it.

    Stands in for the ``molt version`` / ``molt publish`` CLI (npm's ``node -e`` in
    ``run.test.ts:314-324``, ``:369-371``), the same escape hatch upstream's own ``script=`` /
    ``command=`` parameters provide.
    """
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


def manifest_at(root: Path, *parts: str) -> dict[str, Any]:
    return tomllib.loads(Path(root, *parts).read_text(encoding="utf-8"))


def changed_names(result: Any) -> list[str]:
    return [changed.manifest["project"]["name"] for changed in result.changed_packages]


# ======================================================================================
# Row 1 (Adapt) -- version returns changed packages and pushes to the remote.
# run.test.ts:32-142; run.ts:106-148.
# ======================================================================================

#: Bumps pkg-a and pkg-b 1.0.0 -> 1.1.0, splices pkg-a's dependency pin on pkg-b, and writes a
#: CHANGELOG.md for each -- standing in for what `molt version` would do for this changeset.
STUB_BOTH_BUMPED = """
import pathlib

root = pathlib.Path.cwd()
for name in ("pkg-a", "pkg-b"):
    manifest = root / "packages" / name / "pyproject.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace('version = "1.0.0"', 'version = "1.1.0"')
    text = text.replace('"pkg-b==1.0.0"', '"pkg-b==1.1.0"')
    manifest.write_bytes(text.encode("utf-8"))
    changelog = root / "packages" / name / "CHANGELOG.md"
    changelog.write_bytes(
        ("# " + name + "\\n\\n## 1.1.0\\n\\n### Minor Changes\\n\\n- Awesome feature\\n").encode(
            "utf-8"
        )
    )
"""


def test_version_returns_changed_packages_and_pushes_to_the_remote(
    tmp_path: Path, git_repo: GitRepo
) -> None:
    """Row 1 (Adapt) -- ``run.test.ts:32-142`` ('returns the right changed packages and pushes to
    the remote').

    Both packages are bumped, pkg-a's dependency pin on pkg-b is spliced, and each gets a
    ``CHANGELOG.md``. Asserted on the **origin** after checking out
    ``changeset-release/main`` there (``run.ts:112``'s branch rule) -- proving the push actually
    landed, not just that the clone's working tree changed locally.
    """
    origin = git_repo
    seed_workspace(
        origin,
        {
            "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b==1.0.0"]),
            "pkg-b": member_manifest("pkg-b", "1.0.0"),
        },
    )
    clone = origin.shallow_clone(tmp_path / "clone")

    result = run_version(cwd=clone.root, script=stub_script(tmp_path, STUB_BOTH_BUMPED))

    assert result.version_branch == "changeset-release/main"
    assert changed_names(result) == ["pkg-a", "pkg-b"], "insertion order, matching directory order"
    pkg_a_changed = result.changed_packages[0]
    assert pkg_a_changed.dir == clone.root / "packages" / "pkg-a"
    assert pkg_a_changed.relative_dir == Path("packages", "pkg-a")
    assert pkg_a_changed.manifest == {
        "project": {"name": "pkg-a", "version": "1.1.0", "dependencies": ["pkg-b==1.1.0"]}
    }
    pkg_b_changed = result.changed_packages[1]
    assert pkg_b_changed.manifest == {
        "project": {"name": "pkg-b", "version": "1.1.0", "dependencies": []}
    }

    origin.run("checkout", "changeset-release/main")
    assert manifest_at(origin.root, "packages", "pkg-a", "pyproject.toml") == {
        "project": {"name": "pkg-a", "version": "1.1.0", "dependencies": ["pkg-b==1.1.0"]}
    }
    assert manifest_at(origin.root, "packages", "pkg-b", "pyproject.toml") == {
        "project": {"name": "pkg-b", "version": "1.1.0", "dependencies": []}
    }
    changelog_a = (origin.root / "packages" / "pkg-a" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "# pkg-a\n\n## 1.1.0\n\n### Minor Changes\n\n" in changelog_a
    changelog_b = (origin.root / "packages" / "pkg-b" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "# pkg-b\n\n## 1.1.0\n\n### Minor Changes\n\n" in changelog_b


# ======================================================================================
# Row 2 (Adapt) -- only bumped packages are in the returned changed set.
# run.test.ts:144-246.
# ======================================================================================

#: Bumps ONLY pkg-a; pkg-b's manifest and CHANGELOG.md are left completely untouched.
STUB_ONLY_PKG_A_BUMPED = """
import pathlib

manifest = pathlib.Path.cwd() / "packages" / "pkg-a" / "pyproject.toml"
text = manifest.read_text(encoding="utf-8")
text = text.replace('version = "1.0.0"', 'version = "1.1.0"')
manifest.write_bytes(text.encode("utf-8"))
changelog = pathlib.Path.cwd() / "packages" / "pkg-a" / "CHANGELOG.md"
changelog.write_bytes(
    b"# pkg-a\\n\\n## 1.1.0\\n\\n### Minor Changes\\n\\n- Awesome feature\\n"
)
"""


def test_version_only_includes_bumped_packages_in_the_returned_changed_set(
    tmp_path: Path, git_repo: GitRepo
) -> None:
    """Row 2 (Adapt) -- ``run.test.ts:144-246`` ('only includes bumped packages...').

    pkg-a's dependency pin on pkg-b stays ``"1.0.0"`` (pkg-b never bumped, so there is nothing to
    splice); pkg-b's own manifest -- including its hand-placed comment -- must come back
    byte-identical, and it must get **no** ``CHANGELOG.md`` at all (the ENOENT case).
    """
    origin = git_repo
    seed_workspace(
        origin,
        {
            "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b==1.0.0"]),
            "pkg-b": PKG_B_WITH_COMMENT,
        },
    )
    clone = origin.shallow_clone(tmp_path / "clone")

    result = run_version(cwd=clone.root, script=stub_script(tmp_path, STUB_ONLY_PKG_A_BUMPED))

    assert changed_names(result) == ["pkg-a"]
    assert result.changed_packages[0].manifest == {
        "project": {"name": "pkg-a", "version": "1.1.0", "dependencies": ["pkg-b==1.0.0"]}
    }, "pkg-b never bumped, so pkg-a's pin on it is not spliced"

    origin.run("checkout", "changeset-release/main")
    assert (origin.root / "packages" / "pkg-b" / "pyproject.toml").read_bytes() == (
        PKG_B_WITH_COMMENT.encode("utf-8")
    ), "pkg-b's manifest, comment included, must be byte-identical"
    assert not (origin.root / "packages" / "pkg-b" / "CHANGELOG.md").exists(), (
        "an untouched package gets no CHANGELOG.md at all"
    )


# ======================================================================================
# Row 3 (Adapt) -- an ignored package that got a dependency update is excluded.
# run.test.ts:248-294.
# ======================================================================================

#: Bumps only pkg-b (1.0.0 -> 1.1.0). pkg-a is `ignore`d upstream (config.json `ignore: ["pkg-a"]`)
#: so `molt version` would freeze it entirely -- both its own version AND its dependency pin on
#: pkg-b stay untouched (research doc 04 section 6.1's `getChangedPackages` has no `ignore`
#: parameter at all; the exclusion is a side effect of the underlying version step never bumping
#: pkg-a, which is exactly what this stub simulates).
STUB_ONLY_PKG_B_BUMPED = """
import pathlib

manifest = pathlib.Path.cwd() / "packages" / "pkg-b" / "pyproject.toml"
text = manifest.read_text(encoding="utf-8")
text = text.replace('version = "1.0.0"', 'version = "1.1.0"')
manifest.write_bytes(text.encode("utf-8"))
changelog = pathlib.Path.cwd() / "packages" / "pkg-b" / "CHANGELOG.md"
changelog.write_bytes(
    b"# pkg-b\\n\\n## 1.1.0\\n\\n### Minor Changes\\n\\n- Awesome feature\\n"
)
"""


def test_version_excludes_an_ignored_package_that_got_a_dependency_update(
    tmp_path: Path, git_repo: GitRepo
) -> None:
    """Row 3 (Adapt) -- ``run.test.ts:248-294`` ("doesn't include ignored package that got a
    dependency update in returned versions").

    ``ignore`` itself is ``molt.engine``/``molt.apply`` config, not something ``run_version`` reads
    -- the stub models what the ignore config makes the underlying version step do (freeze pkg-a
    completely), and this test's real assertion is that ``run_version``'s before/after version diff
    naturally excludes a package whose version never moved, with no ``ignore=`` parameter needed on
    ``run_version`` itself.
    """
    origin = git_repo
    seed_workspace(
        origin,
        {
            "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b==1.0.0"]),
            "pkg-b": member_manifest("pkg-b", "1.0.0"),
        },
    )
    clone = origin.shallow_clone(tmp_path / "clone")

    result = run_version(cwd=clone.root, script=stub_script(tmp_path, STUB_ONLY_PKG_B_BUMPED))

    assert changed_names(result) == ["pkg-b"]
    assert result.changed_packages[0].manifest == {
        "project": {"name": "pkg-b", "version": "1.1.0", "dependencies": []}
    }


# ======================================================================================
# Row 4 (Adapt) -- publish in a single-package repo.
# run.test.ts:298-332; run.ts:29-98 (root/single-package path, run.ts:67-85).
# ======================================================================================

#: The stub for a single-package publish: creates the tag itself (the real `changeset publish`'s
#: job, run.ts:41's push only re-publishes tags the command already made) and prints the line
#: `run_publish` scrapes.
STUB_PUBLISH_SINGLE = """
import subprocess

subprocess.run(["git", "tag", "-a", "v1.0.0", "-m", "v1.0.0"], check=True)
print("New tag: v1.0.0")
"""


def test_publish_publishes_a_single_package_repo(tmp_path: Path, git_repo: GitRepo) -> None:
    """Row 4 (Adapt) -- ``run.test.ts:298-332`` ('publishes packages in a single package repo').

    Root repo, no ``[tool.uv.workspace]``: the ``New tag:`` regex (no package name,
    ``run.ts:75``) marks the single root package as released. Origin is switched to a different
    branch before publishing (matching ``run.test.ts:307-310``) purely so the push from the clone
    lands cleanly: pushing to a ref that is not the currently checked-out branch of a non-bare
    remote needs no special handling, while pushing to the checked-out one does.
    """
    origin = git_repo
    write_bytes(
        origin.root / "pyproject.toml",
        '[project]\nname = "single-package"\nversion = "1.0.0"\n',
    )
    origin.run("add", ".")
    origin.commit("chore: seed single package")
    clone = origin.shallow_clone(tmp_path / "clone")
    origin.run("checkout", "-b", "some-other-branch")

    result = run_publish(command=stub_script(tmp_path, STUB_PUBLISH_SINGLE), cwd=clone.root)

    assert result.published is True
    assert [(p.name, p.version) for p in result.published_packages] == [("single-package", "1.0.0")]
    tags = origin.run("tag").stdout.strip()
    assert tags == "v1.0.0", "the tag the stub created must have been pushed to origin"


# ======================================================================================
# Row 5 (Adapt) -- publish in a multi-package repo.
# run.test.ts:334-385; run.ts:47 (monorepo `New tag: name@version` regex).
# ======================================================================================

STUB_PUBLISH_MULTI = """
import subprocess

for tag in ("pkg-a@1.0.0", "pkg-b@1.0.0"):
    subprocess.run(["git", "tag", "-a", tag, "-m", tag], check=True)
    print("New tag: " + tag)
"""


def test_publish_publishes_a_multi_package_repo(tmp_path: Path, git_repo: GitRepo) -> None:
    """Row 5 (Adapt) -- ``run.test.ts:334-385`` ('publishes packages in a multi package repo').

    Two ``New tag: <name>@<version>`` lines -> both packages in ``published_packages``, both tags
    on the origin. The monorepo regex (``run.ts:47``) is matched against **PEP 503-normalized**
    names at comparison time (phase brief load-bearing fact 1); both names here are already
    canonical, so this row exercises the happy path and the normalization itself is not
    re-asserted here (it belongs to the changelog/dependency-matching suite, which already pins it).
    """
    origin = git_repo
    seed_workspace(
        origin,
        {
            "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b==1.0.0"]),
            "pkg-b": member_manifest("pkg-b", "1.0.0"),
        },
    )
    clone = origin.shallow_clone(tmp_path / "clone")
    origin.run("checkout", "-b", "some-other-branch")

    result = run_publish(command=stub_script(tmp_path, STUB_PUBLISH_MULTI), cwd=clone.root)

    assert result.published is True
    assert [(p.name, p.version) for p in result.published_packages] == [
        ("pkg-a", "1.0.0"),
        ("pkg-b", "1.0.0"),
    ]
    tags = origin.run("tag").stdout.strip().splitlines()
    assert tags == ["pkg-a@1.0.0", "pkg-b@1.0.0"]


# ======================================================================================
# Row 6 (Port) -- a failing publish still reports what went out.
# `changesets/action` v1.9.0 `src/index.ts:137-147`; owner ruling 2026-07-31 (gap `AP-14`).
# ======================================================================================

#: Announces one package and then exits 3. The tag is created first, exactly as a real publish that
#: uploaded one member and then failed on the next would leave the repository.
STUB_PUBLISH_PARTIAL = """
import subprocess
import sys

subprocess.run(["git", "tag", "-a", "pkg-a@1.0.0", "-m", "pkg-a@1.0.0"], check=True)
print("New tag: pkg-a@1.0.0")
sys.exit(3)
"""


def test_publish_reports_what_a_failing_command_published(
    tmp_path: Path, git_repo: GitRepo
) -> None:
    """``run_publish`` never raises on a status: it carries it (owner ruling 2026-07-31).

    The unit-level pin for the split of the old ``_run_script``. Before the ruling the raise
    happened before the tag push and before the output scrape, so a publish that uploaded three
    packages out of five was unobservable -- molt reported nothing and created no release for the
    three that were already public. Failing the run is the **caller's** job now, after it has
    reported what did go out.
    """
    origin = git_repo
    seed_workspace(
        origin,
        {"pkg-a": member_manifest("pkg-a", "1.0.0"), "pkg-b": member_manifest("pkg-b", "1.0.0")},
    )
    clone = origin.shallow_clone(tmp_path / "clone")
    origin.run("checkout", "-b", "some-other-branch")

    result = run_publish(command=stub_script(tmp_path, STUB_PUBLISH_PARTIAL), cwd=clone.root)

    assert result.exit_status == 3, "the command's own status travels on the result"
    assert result.published is True
    assert [(p.name, p.version) for p in result.published_packages] == [("pkg-a", "1.0.0")]
    tags = origin.run("tag").stdout.strip().splitlines()
    assert tags == ["pkg-a@1.0.0"], "the tags the failing command did create were still pushed"


def test_version_fails_when_the_script_exits_non_zero(tmp_path: Path, git_repo: GitRepo) -> None:
    """``run_version`` still refuses a failing version script (owner ruling 2026-07-31).

    The regression guard for ``_require_success``: the two callers of the script runner disagree
    about what a non-zero status means, and only ``run_publish``'s half changed. A version script
    that failed halfway leaves a partly-rewritten tree, and committing that is research README
    section 3.4's silent CI failure.
    """
    origin = git_repo
    seed_workspace(origin, {"pkg-a": member_manifest("pkg-a", "1.0.0")})
    clone = origin.shallow_clone(tmp_path / "clone")

    with pytest.raises(ExitError) as excinfo:
        run_version(cwd=clone.root, script=stub_script(tmp_path, "import sys\nsys.exit(7)\n"))

    assert excinfo.value.code == 7


# ======================================================================================
# The event stream -- the channel a molt publish actually reports on.
#
# molt diverges from `run.ts:41-47` here, and the divergence is why these rows exist. Upstream has
# one output channel: `changeset publish` writes `New tag:` to stdout with `console.log`, so
# scraping stdout is correct there. molt's `console.success` writes to **stderr**, because
# `molt.ui.console`'s streams contract keeps stdout for machine-readable payloads only. The scrape
# was ported anyway and found nothing on every real run: `molt-release` 0.1.0, 0.1.1 and 0.1.2 each
# published to PyPI, pushed a tag, reported an empty released set, created no GitHub Release and
# exited green (run 30780112459).
#
# Every publish stub above writes its `New tag:` line with `print`, i.e. to stdout -- which is why
# this suite passed while production was broken. `test_publish_reads_the_real_molt_publish_command`
# is the row that could not have passed.
# ======================================================================================


def event_line(tag: str, package_name: str) -> str:
    """One NDJSON git-tag event, in ``molt.events.write_ndjson``'s exact compact form."""
    return json.dumps(
        {"type": "git-tag", "tag": tag, "package_name": package_name}, separators=(",", ":")
    )


#: Tags two packages and reports them **only** through the event stream, printing nothing on
#: stdout. This is the shape of a real `molt publish`: its human line went to stderr.
STUB_PUBLISH_EVENTS = """
import os
import subprocess

for tag in ("pkg-a@1.0.0", "pkg-b@1.0.0"):
    subprocess.run(["git", "tag", "-a", tag, "-m", tag], check=True)

with open(os.environ["MOLT_OUTPUT"], "w", encoding="utf-8") as stream:
    stream.write(EVENT_A + "\\n")
    stream.write(EVENT_B + "\\n")
"""


def test_publish_reads_the_published_set_from_the_event_stream(
    tmp_path: Path, git_repo: GitRepo
) -> None:
    """A publish reporting only through the stream is fully reported.

    Spec: "The published set comes from the event stream". Nothing is printed on stdout, so the
    scrape returns nothing and this row fails against the code that shipped 0.1.2. Each package
    also carries the tag its own event named, which is what stops ``_release_tag`` re-deriving one.
    """
    origin = git_repo
    seed_workspace(
        origin,
        {"pkg-a": member_manifest("pkg-a", "1.0.0"), "pkg-b": member_manifest("pkg-b", "1.0.0")},
    )
    clone = origin.shallow_clone(tmp_path / "clone")
    origin.run("checkout", "-b", "some-other-branch")
    body = STUB_PUBLISH_EVENTS.replace("EVENT_A", repr(event_line("pkg-a@1.0.0", "pkg-a"))).replace(
        "EVENT_B", repr(event_line("pkg-b@1.0.0", "pkg-b"))
    )

    result = run_publish(command=stub_script(tmp_path, body), cwd=clone.root)

    assert result.published is True
    assert [(p.name, p.version, p.tag) for p in result.published_packages] == [
        ("pkg-a", "1.0.0", "pkg-a@1.0.0"),
        ("pkg-b", "1.0.0", "pkg-b@1.0.0"),
    ]


#: Writes an **empty** stream and reports on stdout -- what a publish command that is not molt
#: leaves behind once ``run_publish`` has pre-created the path for it.
STUB_PUBLISH_EMPTY_STREAM = """
import os
import subprocess

subprocess.run(["git", "tag", "-a", "pkg-a@1.0.0", "-m", "pkg-a@1.0.0"], check=True)
open(os.environ["MOLT_OUTPUT"], "w", encoding="utf-8").close()
print("New tag: pkg-a@1.0.0")
"""


def test_publish_falls_back_to_the_scrape_when_the_stream_is_empty(
    tmp_path: Path, git_repo: GitRepo
) -> None:
    """An empty stream is not "nothing was published".

    Spec: "An empty event stream falls back to the scrape". An empty file is what ``molt publish``
    writes when it published nothing **and** what a non-molt command leaves behind, and the two
    cannot be told apart from the file. Falling through answers both, because a molt run that
    published nothing prints no ``New tag:`` line either.
    """
    origin = git_repo
    seed_workspace(origin, {"pkg-a": member_manifest("pkg-a", "1.0.0")})
    clone = origin.shallow_clone(tmp_path / "clone")
    origin.run("checkout", "-b", "some-other-branch")

    result = run_publish(command=stub_script(tmp_path, STUB_PUBLISH_EMPTY_STREAM), cwd=clone.root)

    assert result.published is True
    assert [(p.name, p.tag) for p in result.published_packages] == [("pkg-a", None)]


#: Reports through **both** channels, disagreeing about how many packages went out.
STUB_PUBLISH_BOTH_CHANNELS = """
import os
import subprocess

for tag in ("pkg-a@1.0.0", "pkg-b@1.0.0"):
    subprocess.run(["git", "tag", "-a", tag, "-m", tag], check=True)

with open(os.environ["MOLT_OUTPUT"], "w", encoding="utf-8") as stream:
    stream.write(EVENT_A + "\\n")

print("New tag: pkg-a@1.0.0")
print("New tag: pkg-b@1.0.0")
"""


def test_publish_prefers_the_stream_over_the_scrape_and_never_merges_them(
    tmp_path: Path, git_repo: GitRepo
) -> None:
    """A non-empty stream is the whole answer (design D1).

    Merging the two sources would report ``pkg-a`` twice, and de-duplicating them means matching a
    tag string against a package name -- the guesswork the stream removes. ``pkg-b`` is on stdout
    only and is deliberately **not** reported: the command's machine-readable answer wins outright.
    """
    origin = git_repo
    seed_workspace(
        origin,
        {"pkg-a": member_manifest("pkg-a", "1.0.0"), "pkg-b": member_manifest("pkg-b", "1.0.0")},
    )
    clone = origin.shallow_clone(tmp_path / "clone")
    origin.run("checkout", "-b", "some-other-branch")
    body = STUB_PUBLISH_BOTH_CHANNELS.replace("EVENT_A", repr(event_line("pkg-a@1.0.0", "pkg-a")))

    result = run_publish(command=stub_script(tmp_path, body), cwd=clone.root)

    assert [p.name for p in result.published_packages] == ["pkg-a"]


#: Writes to whatever ``MOLT_OUTPUT`` names, which the row below sets before the loop runs.
STUB_PUBLISH_CALLER_STREAM = """
import os
import subprocess

subprocess.run(["git", "tag", "-a", "pkg-a@1.0.0", "-m", "pkg-a@1.0.0"], check=True)
with open(os.environ["MOLT_OUTPUT"], "w", encoding="utf-8") as stream:
    stream.write(EVENT_A + "\\n")
"""


def test_publish_keeps_a_stream_destination_the_caller_already_set(
    tmp_path: Path, git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``MOLT_OUTPUT`` the caller set wins.

    Spec: "A caller's own stream destination is not overridden". A workflow that exports it is
    collecting the stream for a later step; overriding it would break that step in silence, and
    reading the caller's file hands molt the same events. The file must survive the run -- molt
    removes only the temporary one it created itself.
    """
    origin = git_repo
    seed_workspace(origin, {"pkg-a": member_manifest("pkg-a", "1.0.0")})
    clone = origin.shallow_clone(tmp_path / "clone")
    origin.run("checkout", "-b", "some-other-branch")
    destination = tmp_path / "caller-owned.ndjson"
    monkeypatch.setenv("MOLT_OUTPUT", str(destination))
    body = STUB_PUBLISH_CALLER_STREAM.replace("EVENT_A", repr(event_line("pkg-a@1.0.0", "pkg-a")))

    result = run_publish(command=stub_script(tmp_path, body), cwd=clone.root)

    assert [p.name for p in result.published_packages] == ["pkg-a"]
    assert destination.is_file(), "molt must not delete a file the caller asked to have written"
    assert "pkg-a" in destination.read_text(encoding="utf-8")


#: A single package carrying the `Private :: Do Not Upload` classifier. `molt publish` plans it as
#: **tag-only**: no artifact is uploaded, so the run needs no index, no credential and no network,
#: and it still takes the real tagging path that emits the event and writes the `New tag:` line.
PRIVATE_PACKAGE_MANIFEST = """[project]
name = "single-package"
version = "1.0.0"
requires-python = ">=3.11"
classifiers = ["Private :: Do Not Upload"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""


def test_publish_reads_the_real_molt_publish_command(tmp_path: Path, git_repo: GitRepo) -> None:
    """**The row that was missing.** The real ``molt publish``, through the real ``run_publish``.

    Every other publish row in this file drives a stub that prints ``New tag:`` with ``print``, so
    on stdout. The real command writes that line through :mod:`molt.ui.console`, so on **stderr**,
    and the producer and the consumer had therefore never met in a test. The suite stayed green
    through three releases that created no host release at all.

    A private package keeps this offline and still exercises the whole tagging path: the plan is
    tag-only, so nothing is uploaded, no credential is resolved and no index is read, while
    ``_upload_plan`` still creates the tag, appends the git-tag event and writes the stream.

    The assertion is only that the loop learned something. That is exactly what it failed to do in
    production, and the diagnosis is in the message.
    """
    origin = git_repo
    write_bytes(origin.root / "pyproject.toml", PRIVATE_PACKAGE_MANIFEST)
    write_bytes(origin.root / "src" / "single_package" / "__init__.py", "")
    origin.run("add", ".")
    origin.commit("chore: seed a private single package")
    clone = origin.shallow_clone(tmp_path / "clone")
    origin.run("checkout", "-b", "some-other-branch")

    result = run_publish(command=[sys.executable, "-m", "molt", "publish"], cwd=clone.root)

    assert [(p.name, p.tag) for p in result.published_packages] == [("single-package", "v1.0.0")], (
        "the real `molt publish` reports its tags on the event stream, never on stdout -- an empty "
        "set here is the 0.1.2 bug: uploaded, tagged, no host release, exit 0"
    )
