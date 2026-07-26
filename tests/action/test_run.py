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

from molt.action import run_publish, run_version  # pyrefly: ignore[missing-import]

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
