"""Conformance tests for the publish **plan** -- ``molt.publish.build_publish_plan``.

Ports the 9 rows the plan owns in ``roadmap/research/test-suite/07-publish-pack.md``:

* ``packages/cli/src/commands/publish-plan/__tests__/index.test.ts`` -- all 3 rows
  (compose, empty, ``--output`` envelope).
* ``packages/cli/src/commands/publish-plan/__tests__/getPublishPlan.test.ts`` -- all 6 rows
  (compose, ignore short-circuit, topological chunking, cycle collapse, unpublished prerelease,
  version-diff inclusion).

plus the molt-native rows the group file lists under "New in molt" (two-artifact packing, the
snapshot guardrail) and the negative assertions that keep npm vocabulary out of the plan document.

**The plan is a list of chunks, not a flat list, and the chunk boundaries are the topological
order** (``getPublishPlan.ts:204-252``). That is the monorepo moat; every equality assertion below
is written against the nested shape on purpose, so an implementation that returns the right entries
in the wrong grouping fails.

Deviations from the group file / upstream, all deliberate
--------------------------------------------------------
* **One entry point, two upstream units.** Upstream splits ``publishPlan()`` (the command,
  ``publish-plan/index.ts:19-75``) from ``getPublishPlan()`` (the core,
  ``getPublishPlan.ts:254-288``); the command only flattens the plan for display and serializes the
  envelope. molt has one library function, ``molt.publish.build_publish_plan``, and
  ``molt.commands.publish_plan.run`` is a shell over it (``tests/cli/test_cli.py`` pins the shell
  route). The three ``index.test.ts`` rows are therefore ported against the same function, with
  fixtures chosen so no two tests assert the same thing twice.
* **Assumed seam:** ``build_publish_plan(cwd=<Path>, *, console, git, repository=None,
  output=None) -> list[list[dict]]``. ``cwd=``/``console=``/``git=`` mirror the injection
  convention the CLI suite froze (``tests/cli/test_git_tag.py``: ``run(cwd=..., console=...,
  git=...)``); ``repository=`` is the ``--repository``/``--index-url`` flag ``tests/cli/
  test_cli.py`` already pins on ``publish-plan``. Config is read from the workspace root rather
  than passed in, because ``molt.config`` is itself a TDD target and ``tmp_project.set_config``
  writes it in place.
* **Entries are plain JSON-shaped dicts**, not dataclasses: the frozen harness
  (``tests/publish/fake_publish.py``) builds the expected values with ``publish_entry`` /
  ``tag_only_entry`` / ``publish_plan``, which return dicts, and the plan's whole reason to exist
  is that it survives a round trip through ``publish-plan.json``.
* **``access`` and ``tag`` are gone.** Upstream stamps every publish entry with
  ``access: "restricted"`` and ``tag: "latest"`` (``getPublishPlan.ts:157-158``). PyPI has neither
  per-package access nor dist-tags (research README section 4.4), so the fields do not exist;
  :func:`test_no_plan_entry_carries_an_npm_only_or_camelcase_key` asserts their absence rather
  than leaving it implied by an equality that could drift.
* **No ``private_packages.tag`` gate.** Upstream only emits tag-only entries when
  ``config.privatePackages.tag`` is on (``getPublishPlan.ts:271-281``, default off), which is why
  its fixtures set ``privatePackages: {version: true, tag: true}``. molt's schema has no ``tag``
  sub-key at all (``tests/config/test_parse.py`` lists it in ``DROPPED_KEYS``), so tag-only entries
  are the default and the fixtures below need no config.
* **``peerDependencies`` are gone** (research README section 4.4). Upstream's chunking and cycle
  fixtures use a peer dependency for the back edge; molt uses an ordinary PEP 508 requirement,
  which is the only kind of edge Python has.
* **Prerelease spelling.** ``1.0.0-next.1`` is unrepresentable in PEP 440; the molt spelling of
  upstream's only-pre fixture is ``1.0.0a1`` (test-contract section 5: ``pre`` is ``a|b|rc|dev``).
* **``name`` is stored verbatim, matched normalized.** PEP 503 folding happens at *comparison*
  time -- registry lookup and ``ignore`` matching -- not at parse time, so the entry keeps the name
  the manifest declares while ``Foo_Bar`` and ``foo-bar`` remain one distribution
  (research README section 4.5). ``molt git-tag`` normalizes at tag-construction time for the same
  reason (``tests/cli/test_git_tag.py::test_tag_names_are_pep_503_normalized``).

Guard
-----
``require_publish_plan()`` is called through its module object rather than followed by a
``from molt.publish import ...``: ruff's E402 exemption covers a bare ``pytest.importorskip(...)``
call but **not** the harness wrapper (verified with ``ruff check`` -- the ``from`` form reports
``E402``, and the contract forbids ``# noqa: E402`` because ``RUF100`` then fails). Resolving the
two functions off the module object keeps the file lint-clean and still fails loudly if
``molt.publish`` ever lands without them.

The guard is keyed on ``build_publish_plan``, not on ``publish``: ``molt.publish`` lands across two
changes, and the upload half is the later one. See :func:`tests.publish.fake_publish
.require_publish_plan` for why each stage guards on the attribute it actually drives.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import pytest
from tests.cli.fake_cli import FakeGit, RecordingConsole
from tests.publish.fake_publish import (
    ARTIFACT_KINDS,
    INTEGRITY_PREFIX,
    PUBLISH_PLAN_VERSION,
    FakeBuilder,
    integrity_of,
    publish_entry,
    publish_plan,
    require_publish_plan,
    sdist_name,
    tag_only_entry,
    wheel_name,
)

molt_publish = require_publish_plan()

#: ``molt.publish.build_publish_plan`` -- see the module docstring's "Guard" section for why these
#: are attribute lookups instead of a late ``from molt.publish import ...``.
build_publish_plan = molt_publish.build_publish_plan
#: ``molt.publish.read_publish_plan`` -- the molt analogue of ``readPlanFile``
#: (``getPublishPlan.ts:58-76``), shared by ``molt publish --from-pack-dir`` and
#: ``molt build --from-publish-plan``.
read_publish_plan = molt_publish.read_publish_plan

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import ProjectBuilder, PyPIRegistry

pytestmark = [pytest.mark.functional, pytest.mark.network]


# --------------------------------------------------------------------------------------
# Local helpers
#
# Named `read_*`/`kind_of`/`entries_of`, never `test_*`: `python_functions = "test"` is a PREFIX
# match, so a helper called `test_setup` would be collected as a test case.
# --------------------------------------------------------------------------------------

#: A snapshot version in molt's pinned shape: release ``0.0.0`` plus a 14-digit ``.dev`` stamp
#: (``tests/cli/test_deliberately_not_ported.py::
#: test_upstream_snapshot_version_shape_is_illegal_in_pep440``). The digits are ``FROZEN_DATETIME``
#: (``tests/conftest.py``, 2021-12-13T00:07:30.879Z) rendered ``%Y%m%d%H%M%S``, written as a
#: literal so this module never imports ``tests.conftest`` at runtime.
SNAPSHOT_VERSION = "0.0.0.dev20211213000730"

#: PP-1 (owner ruling, 2026-07-31): molt's OTHER snapshot shape -- release ``0.0.0`` plus a 13-digit
#: **millisecond epoch** ``.dev`` counter, what a ``{timestamp}`` snapshot prerelease template
#: renders (``molt.engine.assemble._snapshot_suffix``:
#: ``str(int(moment.timestamp() * 1000))``), as opposed to the 14-digit ``YYYYMMDDHHMMSS`` datetime
#: the ``{datetime}`` placeholder (and the no-template default) renders. The digits are
#: ``FROZEN_EPOCH_MS`` (``tests/conftest.py``, 1639354050879 ms = 2021-12-13T00:07:30.879Z), written
#: as a literal for the same reason ``SNAPSHOT_VERSION`` is.
SNAPSHOT_VERSION_MS_COUNTER = "0.0.0.dev1639354050879"

#: A stand-in for a private index. Deliberately not a ``pypi.org`` host: the whole point of the
#: guardrail is that a snapshot must not burn a version number on the public index.
PRIVATE_INDEX = "https://packages.internal.example/simple/"

#: Plan entry keys must be snake_case (frozen harness ``publish_entry`` docstring; progress.md
#: Session 5, decision 6).
SNAKE_CASE = re.compile(r"[a-z][a-z0-9_]*")


def pypi_reads(registry: PyPIRegistry) -> list[str]:
    """Project names queried on ``GET pypi.org/pypi/<name>/json``, in call order.

    Reaches through ``PyPIRegistry._router`` because the frozen fixture exposes no call log of its
    own -- reported to the orchestrator as a harness gap. The names come off the recorded request
    URL, so they are what the implementation actually asked for; the fixture normalizes internally
    when it answers, which would otherwise hide a non-normalizing lookup
    (:func:`test_the_registry_is_queried_under_the_pep_503_normalized_name` depends on that).
    """
    names: list[str] = []
    for call in registry._router.calls:
        segments = [part for part in call.request.url.path.split("/") if part]
        if len(segments) >= 3 and segments[0] == "pypi" and segments[-1] == "json":
            names.append(segments[-2])
    return names


def entries_of(plan: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every entry of every chunk, flattened -- upstream's ``plan.flat()``."""
    return [entry for chunk in plan for entry in chunk]


def kind_of(path: str) -> str:
    """Map a built artifact's filename onto its :data:`ARTIFACT_KINDS` member."""
    if path.endswith(".tar.gz"):
        return "sdist"
    if path.endswith(".whl"):
        return "wheel"
    return f"unknown ({path})"


def write_plan_file(path: Path, envelope: Any) -> Path:
    """Serialize a plan envelope the way ``--output`` does (LF bytes, 2-space indent).

    ``envelope`` is deliberately ``Any`` rather than a mapping: the rows that prove the reader's
    envelope guard have to be able to write a document that is *not* an envelope at all.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(envelope, indent=2) + "\n").encode("utf-8"))
    return path


def build_enriched_plan_file(tmp_path: Path, *, artifact_kinds: tuple[str, ...]) -> Path:
    """Write an enriched ``publish-plan.json``: pkg-a with ``artifact_kinds``, pkg-b tag-only.

    The artifacts are produced by the frozen :class:`FakeBuilder`, so their bytes (and therefore
    their digests) are the ones ``molt build`` would have written.
    """
    out_dir = tmp_path / ".packed"
    produced = FakeBuilder().build(
        tmp_path / "packages" / "pkg-a", out_dir, package="pkg-a", version="1.0.0"
    )
    by_kind = {kind_of(path.name): path for path in produced}
    artifacts = [
        {"path": f".packed/{by_kind[kind].name}", "integrity": integrity_of(by_kind[kind])}
        for kind in artifact_kinds
    ]
    envelope = publish_plan(
        [
            publish_entry("pkg-a", "1.0.0", artifacts=artifacts),
            tag_only_entry("pkg-b", "1.0.0"),
        ]
    )
    return write_plan_file(tmp_path / "publish-plan.json", envelope)


# ======================================================================================
# publish-plan/__tests__/getPublishPlan.test.ts -- the core, 6 rows
# ======================================================================================


def test_a_public_package_publishes_and_a_private_one_is_tag_only(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 1, Adapt (`getPublishPlan.test.ts:21-69`; ``getPublishPlan.ts:254-288``).

    Upstream: ``infoAllow404`` -> not published, public pkg-a + private pkg-b, and the expected
    plan is ``[[publish pkg-a (access:"restricted", tag:"latest"), tag-only pkg-b]]``. molt drops
    both npm fields (research README section 4.4) and swaps the deciding query for
    ``GET pypi.org/pypi/pkg-a/json``, which 404s because the fixture was never seeded.

    Two things this row pins beyond the entry shape: the two kinds share **one** chunk (nothing
    depends on anything, so nothing forces an order), and the private package is **never looked
    up** -- upstream filters ``!pkg.packageJson.private`` before the query
    (``getPublishPlan.ts:106-109``) and molt filters on the ``Private :: Do Not Upload`` classifier
    (research doc 02 section 12.1).
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0", private=True)
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == [[publish_entry("pkg-a", "1.0.0"), tag_only_entry("pkg-b", "1.0.0")]]
    assert pypi_reads(pypi_registry) == ["pkg-a"], (
        "a package that is never uploaded has nothing to look up; upstream filters private "
        "packages out before the registry call, not after"
    )


def test_an_ignored_package_short_circuits_before_the_registry_is_queried(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 2, Port (`getPublishPlan.test.ts:71-93`; ``getPublishPlan.ts:106-109``).

    ``ignore: ["pkg-a"]`` produces an empty plan **and** ``infoAllow404`` is never called. The
    second half is the whole row: an assertion that only checked the empty result would pass
    against an implementation that queries the registry first and filters afterwards -- same
    answer, one wasted network round trip per ignored package, and on a monorepo that is the
    difference between a plan that runs offline and one that does not.

    ``shouldSkipPackage`` is shared with the versioning path, where ``ignore`` is already pinned
    (``tests/cli/test_version.py``); what is new here is *when* it runs.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(ignore=["pkg-a"])
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == []
    assert pypi_reads(pypi_registry) == [], (
        "the ignore filter runs BEFORE the registry query (getPublishPlan.ts:106-109)"
    )


def test_releases_are_chunked_in_dependency_order(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 3, Port -- **the moat** (`getPublishPlan.test.ts:95-164`; ``getPublishPlan.ts:204-252``).

    pkg-a depends on pkg-b, private pkg-c depends on pkg-a, so the plan is three chunks:
    ``[[pkg-b], [pkg-a], [tag-only pkg-c]]``. Chunk boundaries *are* the publish order -- a
    dependency has to exist on the index before the release that pins it, or an installer resolving
    the dependent mid-run gets a version that is not there yet.

    The packages are declared a, b, c and come back b, a, c: an implementation that emits workspace
    order, or that puts every release in its own chunk in declaration order, fails here. Upstream's
    pkg-c edge is a ``peerDependencies`` entry, which Python does not have (research README section
    4.4) -- an ordinary requirement carries the same edge.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b==1.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.add_package("pkg-c", "1.0.0", deps=["pkg-a==1.0.0"], private=True)
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == [
        [publish_entry("pkg-b", "1.0.0")],
        [publish_entry("pkg-a", "1.0.0")],
        [tag_only_entry("pkg-c", "1.0.0")],
    ]


def test_a_dependency_cycle_collapses_into_one_chunk_with_a_warning(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 4, Port (`getPublishPlan.test.ts:166-216`; ``getPublishPlan.ts:241-251``).

    A cycle is **not** an error. ``graphSequencer`` returns the cycle members as one chunk and
    upstream logs ``Publish plan contains cyclic dependencies: ...``; refusing to plan would leave
    a workspace that has a cycle unable to release at all, which is worse than an unordered upload
    of the packages inside it.

    Both directions are ordinary requirements here (upstream uses a peer dependency for the back
    edge only because npm forbids a true dependency cycle). The warning is asserted, not just the
    chunking: silently collapsing a cycle hides the one fact the user needs to fix it.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b==1.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0", deps=["pkg-a==1.0.0"])
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == [[publish_entry("pkg-a", "1.0.0"), publish_entry("pkg-b", "1.0.0")]]
    assert any("pkg-a" in warning and "pkg-b" in warning for warning in console.warnings), (
        f"the cycle must be named, not just collapsed; warnings were {console.warnings}"
    )


def test_a_local_prerelease_absent_from_the_published_set_is_included(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 5, Adapt (`getPublishPlan.test.ts:218-263`; ``getPublishPlan.ts:80-92,120-134``).

    Upstream's row is really two claims: (a) a local prerelease that is not in the published set
    gets published, and (b) because *every* published version is a prerelease of the current pre
    tag, the release is routed to the ``latest`` dist-tag instead of the pre tag -- the ``only-pre``
    heuristic. **(b) dies**: PyPI has no dist-tags and therefore no ``latest`` pointer to steer
    (research README section 4.4), so ~80 lines of upstream's hairiest logic have no analogue. Only
    (a) ports, and the entry carries no ``tag`` key for the assertion to mention.

    ``1.0.0-next.1/2/3`` is not a PEP 440 version; the molt spelling is ``1.0.0a1/a2/a3``
    (test-contract section 5). The comparison is against the ``releases`` keys of the PyPI JSON
    document, so a prerelease is no different from any other version at this layer.
    """
    tmp_project.add_package("pkg-a", "1.0.0a3")
    pypi_registry.set_versions("pkg-a", ["1.0.0a1", "1.0.0a2"])
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == [[publish_entry("pkg-a", "1.0.0a3")]]
    assert "tag" not in plan[0][0], "the only-pre routing to `latest` has no PyPI analogue"


# (published versions, local version, why)
VERSION_DIFF_CASES: list[tuple[list[str], str, str]] = [
    (["1.0.0"], "1.1.0", "upstream's fixture: the local version is simply newer than the only one"),
    (
        ["1.0.0", "1.2.0"],
        "1.1.0",
        "a backport released between two published versions. `1.1.0` is absent from the set but is "
        "NOT the highest, so an implementation comparing against the maximum publishes nothing",
    ),
]


@pytest.mark.parametrize(("published", "local", "why"), VERSION_DIFF_CASES)
def test_a_package_published_at_other_versions_is_included_for_the_local_one(
    tmp_project: ProjectBuilder,
    pypi_registry: PyPIRegistry,
    published: list[str],
    local: str,
    why: str,
) -> None:
    """Row 6, Adapt (`getPublishPlan.test.ts:265-301`; ``getPublishPlan.ts:152-160``).

    The inverse of skip-already-published. Upstream's test is the first row: published
    ``["1.0.0"]``, local ``1.1.0``, so pkg-a is included.

    The rule is ``!publishedVersions.includes(localVersion)`` -- **membership**, not "is the local
    version the newest". The second row is molt-native and is what makes the distinction
    observable: upstream's fixture passes under either reading, so on its own it would let a
    highest-version comparison through, and that reading silently refuses to publish a backport
    (a real Python release pattern -- a 1.1.x maintenance line while 1.2 is already out).
    """
    tmp_project.add_package("pkg-a", local)
    pypi_registry.set_versions("pkg-a", published)
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == [[publish_entry("pkg-a", local)]], why


def test_a_private_package_that_is_already_tagged_is_not_listed_again(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """molt-NEW seam pin (``getPublishPlan.ts:185-202``; ``utils/getUntaggedPackages.ts:11-18``).

    ``getUntaggedPrivatePackages`` does not list *every* private package -- it lists the ones with
    no tag yet, which is why upstream's fixtures all stub ``git.tagExists`` and
    ``git.remoteTagExists`` to ``false``. None of the nine ported rows exercises the ``true``
    branch, so without this row the ``git=`` argument would be a seam that no assertion depends on:
    an implementation that ignored it entirely would pass every other test in this module and then
    re-tag the whole workspace on the second run.

    Tags are read in one batch (``git.get_all_tags()``), the divergence
    ``tests/cli/test_git_tag.py::test_existing_tag_lookup_is_batched`` pins for the same reason,
    and are matched on the PEP 503-normalized name, which is the spelling ``molt git-tag`` writes.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0", private=True)
    console = RecordingConsole()
    git = FakeGit(existing_tags=["pkg-b@1.0.0"])

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == [[publish_entry("pkg-a", "1.0.0")]], (
        "pkg-b is already tagged at 1.0.0, so there is nothing left to do for it"
    )


# ======================================================================================
# publish-plan/__tests__/index.test.ts -- the command wrapper, 3 rows
# ======================================================================================


def test_releases_with_no_edge_between_them_share_a_single_chunk(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """index row 1, Adapt (`publish-plan/index.test.ts:22-69`; ``publish-plan/index.ts:19-75``).

    Upstream's wrapper row repeats ``getPublishPlan``'s composition and adds one structural fact
    the core row cannot show: the returned plan is still **chunked**. ``publishPlan`` flattens the
    plan internally (``index.ts:30-32``) only to build its two log lists, and returns the nested
    value untouched -- flattening what it returns would erase the publish order that the whole
    pipeline downstream depends on.

    Two independent public packages (rather than upstream's public+private pair, whose composition
    is already pinned by :func:`test_a_public_package_publishes_and_a_private_one_is_tag_only`)
    make the arity discriminating in both directions: one chunk here, three chunks in
    :func:`test_releases_are_chunked_in_dependency_order`, from implementations that differ only in
    the sequencer.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == [[publish_entry("pkg-a", "1.0.0"), publish_entry("pkg-b", "1.0.0")]]
    assert len(plan) == 1, (
        "no edge between the two releases means nothing constrains their order, so they belong in "
        "one chunk -- one-chunk-per-release would serialize an entire monorepo needlessly"
    )


def test_a_package_whose_local_version_is_already_published_is_absent_from_the_plan(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """index row 2, Adapt (`publish-plan/index.test.ts:71-97`; ``publish-plan/index.ts:51-54``).

    Published ``["1.0.0"]`` == local ``1.0.0`` gives ``[]`` -- **``[]``, not ``[[]]``**: "nothing
    to do" is an empty plan, not a plan with an empty chunk, and the difference is visible to
    ``publish``, which treats ``plan.length === 0`` as "nothing to publish"
    (``publish/index.ts:93-96``).

    The registry read is asserted too, which is what separates this row from the ignore row: both
    return ``[]``, but only one of them is allowed to have asked.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    pypi_registry.set_versions("pkg-a", ["1.0.0"])
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == []
    assert pypi_reads(pypi_registry) == ["pkg-a"], (
        "unlike the ignore row, this emptiness is a decision the registry answered"
    )


def test_output_writes_the_versioned_plan_envelope(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """index row 3, Port (`publish-plan/index.test.ts:99-136`; ``publish-plan/index.ts:34-49``).

    ``--output`` writes ``{"version": CURRENT_PUBLISH_PLAN_VERSION, "plan": <plan>}`` and the
    function still returns the plan. The envelope is what makes the three-stage pipeline
    (``publish-plan`` -> ``build`` -> ``publish``) safe across process boundaries: the version field
    is the guard that lets a future shape change be rejected instead of misread
    (``getPublishPlan.ts:58-76``).

    The byte-level checks are molt-native. A plan file is consumed by another process -- often on
    another machine, often by CI -- so it is written LF-only on every platform (research doc 02
    section 12.6), exactly as the changeset writer and the NDJSON reporter are.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    out = tmp_project.root / "publish-plan.json"
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git, output=out)

    assert plan == [[publish_entry("pkg-a", "1.0.0")]]
    raw = out.read_bytes()
    written = json.loads(raw.decode("utf-8"))
    assert written == publish_plan(plan[0])
    assert written["version"] == PUBLISH_PLAN_VERSION
    assert b"\r\n" not in raw, "a plan file crosses machines; it is LF-only on every platform"


def test_output_writes_an_empty_envelope_when_there_is_nothing_to_publish(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """index row 3, second half (`publish-plan/index.ts:34-49` vs `:51-54`).

    The ``--output`` branch returns before the "nothing to publish or tag" branch, so upstream
    writes ``{version: 1, plan: []}`` whether or not there is anything in it. The ordering is not
    incidental -- it is the same guarantee ``publish --output`` gives for its NDJSON stream
    (``utils/output.ts:17-36`` opens the file before ``plan.length === 0`` returns, pinned by
    ``tests/publish/test_publish.py::
    test_creates_an_empty_output_file_when_there_is_nothing_to_publish_or_tag``), and for the same
    reason: the next CI job reads this file unconditionally, and "no file" and "an empty plan" must
    not look alike to it.

    Added by review; verified by mutation -- skipping the write on an empty plan passed every other
    test in this package.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    pypi_registry.set_versions("pkg-a", ["1.0.0"])
    out = tmp_project.root / "publish-plan.json"
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git, output=out)

    assert plan == []
    assert out.is_file(), "the consumer of this file reads it unconditionally"
    assert json.loads(out.read_bytes().decode("utf-8")) == publish_plan()


# ======================================================================================
# molt-NEW -- the plan document's own vocabulary
# ======================================================================================

# (forbidden key, why)
FORBIDDEN_ENTRY_KEYS: list[tuple[str, str]] = [
    (
        "access",
        "npm `publishConfig.access` / `--access` (getPublishPlan.ts:157). PyPI has no per-package "
        "access setting -- a project is public or it does not exist",
    ),
    (
        "tag",
        "npm dist-tag routing (getPublishPlan.ts:158, getReleaseTag :80-92). PyPI has no dist-tags "
        "and no `latest` pointer, so there is nothing to route (research README section 4.4)",
    ),
    (
        "tarball",
        "upstream's singular `tarball: {path, integrity}` (getPublishPlan.ts:30-33,43-48). "
        "`python -m build` emits TWO artifacts per release, so molt's key is the plural "
        "`artifacts`",
    ),
    ("packageName", "camelCase; the plan document is snake_case throughout"),
    ("oldVersion", "camelCase; the snake_case spelling is `old_version`"),
    ("newVersion", "camelCase; the snake_case spelling is `new_version`"),
    ("distTag", "camelCase and npm-only, twice over"),
]


@pytest.mark.parametrize(("key", "why"), FORBIDDEN_ENTRY_KEYS)
def test_no_plan_entry_carries_an_npm_only_or_camelcase_key(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry, key: str, why: str
) -> None:
    """molt-NEW: asserted negatively, because equality alone would not survive a schema addition.

    Every equality assertion in this module would keep passing if a future implementation added an
    ``access`` field *and* somebody widened the expected value to match. These rows make the
    absence itself the contract: the npm vocabulary is not "unused", it is gone (research README
    section 4.4), and the document's casing is snake_case (frozen harness ``publish_entry``;
    progress.md Session 5, decision 6, which four assertions in the CLI suite already depend on).
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0", private=True)
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    entries = entries_of(plan)
    assert len(entries) == 2, "the fixture must produce both entry kinds or the row proves nothing"
    for entry in entries:
        assert key not in entry, f"{entry['name']}: {why}"


def test_every_plan_entry_key_is_snake_case(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """molt-NEW: the whole-document rule the row above spot-checks.

    ``FORBIDDEN_ENTRY_KEYS`` can only name spellings somebody thought of; this one fails on any
    camelCase key at all, including one added later. Same rule as the NDJSON reporter, where
    upstream's ``packageName`` becomes ``package_name``
    (``tests/cli/test_git_tag.py``, ``utils/output.ts:6-10``).
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0", private=True)
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    offenders = [
        key for entry in entries_of(plan) for key in entry if not SNAKE_CASE.fullmatch(key)
    ]
    assert offenders == [], f"plan entry keys must be snake_case; found {offenders}"


# ======================================================================================
# molt-NEW -- PEP 503: one distribution, however the manifest spells it
# ======================================================================================


def test_the_registry_is_queried_under_the_pep_503_normalized_name(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """molt-NEW (research README section 4.5): ``Foo_Bar`` and ``foo-bar`` are one distribution.

    No changesets analogue -- npm names are already lowercase and have no punctuation equivalence,
    so upstream can compare names verbatim everywhere. In Python the *lookup key* is
    ``re.sub(r"[-_.]+", "-", name).lower()``: query ``pypi.org/pypi/Foo_Bar/json`` instead and a
    project that IS published answers 404 on some mirrors and 301 on pypi.org, either of which
    reads as "never published" and re-uploads a version that already exists.

    The assertion is on the recorded request URL rather than on the answer, deliberately: the
    ``pypi_registry`` fixture normalizes the name it receives before looking it up, so a
    non-normalizing implementation would still get the right answer from the double and the test
    would prove nothing.

    The entry keeps the declared spelling. Normalization happens at comparison time, not parse
    time, so the plan still points at ``packages/Foo_Bar`` on disk and ``molt git-tag`` remains the
    component that folds the name into a tag
    (``tests/cli/test_git_tag.py::test_tag_names_are_pep_503_normalized``).
    """
    tmp_project.add_package("Foo_Bar", "1.0.0")
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert pypi_reads(pypi_registry) == ["foo-bar"], "PEP 503 normalization at lookup time"
    assert plan == [[publish_entry("Foo_Bar", "1.0.0")]], (
        "the document keeps the declared name and the on-disk directory; only the comparison folds"
    )


def test_the_ignore_filter_matches_a_package_by_its_normalized_name(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """molt-NEW: ``ignore = ["foo-bar"]`` skips a package declared ``Foo_Bar``.

    Config matching runs through the same normalized index as the registry lookup (research doc 02
    sections 12.4/12.5; the rule is already pinned for ``fixed`` in ``tests/config/test_parse.py``,
    which passes ``package_names=["Foo_Bar", ...]`` against a ``foo-bar`` pattern). A verbatim
    match here would publish a package the user asked to ignore -- and on PyPI that cannot be
    undone, only yanked.

    The registry assertion carries over from the ignore short-circuit row: normalization must not
    cost the round trip that filtering early saves.
    """
    tmp_project.add_package("Foo_Bar", "1.0.0")
    tmp_project.set_config(ignore=["foo-bar"])
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == []
    assert pypi_reads(pypi_registry) == []


# ======================================================================================
# molt-NEW -- the snapshot guardrail (research README section 4.3, open decision 2)
# ======================================================================================


def test_a_snapshot_release_is_refused_before_the_public_index_is_touched(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """molt-NEW (research README section 4.3): a snapshot must not target pypi.org.

    changesets publishes snapshots as ``0.0.0-canary-<sha>`` to npm and relies on being able to
    unpublish and on dist-tags keeping them out of everyone's way. PyPI has neither: every upload
    **permanently burns a public version number** and cannot be rolled back, only yanked. So a
    snapshot plan aimed at the default index is refused, and the user points ``--repository`` at a
    private index instead.

    **Shape pinned here** (this closes research open decision 2 for the publish path, and needs
    owner confirmation): the plan builder has no memory of the ``molt version --snapshot`` run that
    produced the version -- ``publish-plan`` is a separate invocation -- so the trigger is the
    version itself, molt's pinned snapshot shape ``0.0.0.dev<14-digit-datetime>``
    (``tests/cli/test_deliberately_not_ported.py``). Refusal, not silent rerouting: molt cannot
    invent the URL of a private index.

    Two assertions beyond the raise. Nothing is queried, because a guardrail that fires after the
    round trip is a guardrail that fails when the network is down; and nothing is written, because
    a plan file on disk is an artifact a later ``molt publish --from-pack-dir`` would happily
    consume.
    """
    tmp_project.add_package("pkg-a", SNAPSHOT_VERSION)
    out = tmp_project.root / "publish-plan.json"
    console, git = RecordingConsole(), FakeGit()

    with pytest.raises(Exception, match=r"(?i)snapshot"):
        build_publish_plan(cwd=tmp_project.root, console=console, git=git, output=out)

    assert pypi_reads(pypi_registry) == [], "refuse before the round trip, not after"
    assert not out.exists(), "a refused plan leaves no artifact for a later stage to pick up"


# (local version, why this is NOT molt's snapshot shape)
NON_SNAPSHOT_VERSIONS: list[tuple[str, str]] = [
    (
        "1.2.3.dev5",
        "an ordinary PEP 440 developmental release. `.dev` is a legal release segment on any "
        "version, so a trigger that keys on `.dev` alone refuses a version a user chose by hand",
    ),
    (
        "0.0.0.dev1",
        "the `0.0.0.dev` prefix WITHOUT the 14-digit datetime stamp. A trigger written as a "
        "prefix match rather than the full pinned shape cannot tell this from a snapshot",
    ),
    (
        "0.0.0",
        "a plain 0.0.0 release: the release segment on its own carries no snapshot meaning",
    ),
]


@pytest.mark.parametrize(("version", "why"), NON_SNAPSHOT_VERSIONS)
def test_a_version_that_is_not_the_snapshot_shape_is_planned_normally(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry, version: str, why: str
) -> None:
    """molt-NEW: the negative control for the guardrail above -- the half review found missing.

    The refusal row pins that molt's snapshot shape ``0.0.0.dev<14-digit-datetime>``
    (``tests/cli/test_deliberately_not_ported.py::
    test_upstream_snapshot_version_shape_is_illegal_in_pep440``) is refused. Nothing pinned the
    other side, and every other fixture in this module uses ``1.0.0`` / ``1.0.0a3`` / ``2.3.4`` --
    none of which any plausible over-broad trigger would match either. Verified by mutation:
    widening the trigger to ``\\.dev\\d+$|^0\\.0\\.0`` passed every test in this package while
    making ``molt publish`` refuse to release any developmental version at all.

    That failure mode is not hypothetical. ``.devN`` is a first-class PEP 440 release segment
    (research README section 4.1) and ``molt version --pre dev`` produces one deliberately
    (test-contract section 5: ``pre`` is ``a|b|rc|dev``), so refusing the whole family would break
    the one prerelease phase molt itself hands users.

    The registry assertion is what makes this row non-vacuous: it proves the plan was *decided*
    rather than merely non-empty, so an implementation that short-circuits before the round trip
    for these versions still fails.
    """
    tmp_project.add_package("pkg-a", version)
    console, git = RecordingConsole(), FakeGit()

    plan = build_publish_plan(cwd=tmp_project.root, console=console, git=git)

    assert plan == [[publish_entry("pkg-a", version)]], why
    assert pypi_reads(pypi_registry) == ["pkg-a"], why


def test_a_snapshot_release_is_allowed_against_an_explicit_private_index(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """molt-NEW: the escape hatch. ``--repository`` naming a non-PyPI index clears the guardrail.

    Without this row the guardrail above is satisfiable by a plan builder that refuses *every*
    snapshot, which would make ``molt version --snapshot`` unpublishable anywhere -- the opposite
    of the documented intent ("snapshots default to a non-PyPI index", research README section 4.3;
    ``--repository``/``--index-url`` is already pinned on ``publish-plan`` in
    ``tests/cli/test_cli.py``).

    Written defensively on purpose. **How molt queries a non-PyPI index for already-published
    versions is an open design question** (PEP 691 JSON simple API? no query at all?), and the
    frozen ``pypi_registry`` fixture only mocks pypi.org, so a conforming implementation may well
    fail here on an unmocked request. What this row refuses to tolerate is (a) a signature that
    does not accept ``repository=`` -- ``TypeError`` is re-raised rather than swallowed -- (b) the
    snapshot guardrail firing anyway, and (c) pypi.org being consulted behind the user's back after
    they named a different index.
    """
    tmp_project.add_package("pkg-a", SNAPSHOT_VERSION)
    console, git = RecordingConsole(), FakeGit()

    try:
        plan = build_publish_plan(
            cwd=tmp_project.root, console=console, git=git, repository=PRIVATE_INDEX
        )
    except TypeError:
        raise
    except Exception as exc:
        assert "snapshot" not in str(exc).lower(), (
            f"the guardrail must not fire once an explicit non-PyPI index is named: {exc}"
        )
    else:
        assert plan == [[publish_entry("pkg-a", SNAPSHOT_VERSION)]]

    assert pypi_reads(pypi_registry) == [], (
        "an explicitly named index is the index; pypi.org is not consulted as well"
    )


# ----------------------------------------------------------------------------------
# PP-1 (owner ruling, 2026-07-31) -- the OTHER dev-counter shape molt itself produces
# ----------------------------------------------------------------------------------


def test_a_millisecond_counter_snapshot_is_also_refused_before_the_public_index_is_touched(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """PP-1 (owner ruling, 2026-07-31): the guardrail must catch BOTH dev-counter styles.

    ``test_a_snapshot_release_is_refused_before_the_public_index_is_touched`` above only pins the
    14-digit ``{datetime}`` shape. A ``{timestamp}`` snapshot prerelease template produces a
    **13-digit** millisecond-epoch counter instead (``molt.engine.assemble._snapshot_suffix``), and
    it escaped the original guardrail entirely -- recorded as ``PP-1`` in ``openspec/GAPS.md`` until
    this ruling. Same two assertions as the 14-digit row, for the same reasons: refuse before the
    round trip, and leave nothing on disk for a later stage to pick up.

    A calculated-version snapshot (``config.snapshot_use_calculated_version``) is deliberately NOT
    covered by this row or by the widened pattern: its release segment is the real computed version,
    not ``0.0.0``, so it is indistinguishable in shape from an ordinary release and stays the user's
    own responsibility to route away from the public index (documented, not enforced -- see
    ``website/docs/cli/publish.md``).
    """
    tmp_project.add_package("pkg-a", SNAPSHOT_VERSION_MS_COUNTER)
    out = tmp_project.root / "publish-plan.json"
    console, git = RecordingConsole(), FakeGit()

    with pytest.raises(Exception, match=r"(?i)snapshot"):
        build_publish_plan(cwd=tmp_project.root, console=console, git=git, output=out)

    assert pypi_reads(pypi_registry) == [], "refuse before the round trip, not after"
    assert not out.exists(), "a refused plan leaves no artifact for a later stage to pick up"


def test_a_millisecond_counter_snapshot_is_allowed_against_an_explicit_private_index(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """PP-1: the escape hatch holds for the 13-digit shape too -- ``--repository`` clears it.

    The twin of ``test_a_snapshot_release_is_allowed_against_an_explicit_private_index`` for the
    millisecond-counter shape, so the widened pattern is proven to refuse only the *destination*,
    never the shape by itself.
    """
    tmp_project.add_package("pkg-a", SNAPSHOT_VERSION_MS_COUNTER)
    console, git = RecordingConsole(), FakeGit()

    try:
        plan = build_publish_plan(
            cwd=tmp_project.root, console=console, git=git, repository=PRIVATE_INDEX
        )
    except TypeError:
        raise
    except Exception as exc:
        assert "snapshot" not in str(exc).lower(), (
            f"the guardrail must not fire once an explicit non-PyPI index is named: {exc}"
        )
    else:
        assert plan == [[publish_entry("pkg-a", SNAPSHOT_VERSION_MS_COUNTER)]]

    assert pypi_reads(pypi_registry) == [], (
        "an explicitly named index is the index; pypi.org is not consulted as well"
    )


# ======================================================================================
# molt-NEW -- two artifacts per release (group file "New in molt" #4)
# ======================================================================================


def test_an_enriched_publish_entry_carries_both_artifacts_in_a_stable_order(
    tmp_path: Path,
) -> None:
    """molt-NEW: ``npm pack`` writes one ``.tgz``; ``python -m build`` writes an sdist AND a wheel.

    Upstream's enriched entry gains a singular ``tarball: {path, integrity}``
    (``getPublishPlan.ts:30-33``, ``pack/index.ts:74-228``). molt's gains a **list**, one element
    per :data:`ARTIFACT_KINDS`, and the order is (sdist, wheel) -- fixed, because the plan is
    compared, diffed and reviewed as text, and an implementation that emits directory order would
    produce a different document on a different filesystem.

    Both halves matter for correctness, not tidiness: a Python release with no wheel forces every
    consumer to build from source, and a release with no sdist is undistributable to platforms
    without a matching wheel tag.

    The digests are asserted to be distinct, which is what proves ``integrity`` is per-artifact
    rather than per-release -- the frozen ``FakeBuilder`` writes different bytes per artifact
    exactly so this assertion cannot be vacuous. Tag-only entries pass through untouched and carry
    no ``artifacts`` key at any stage (frozen harness ``tag_only_entry``; ``pack/index.ts``).
    """
    path = build_enriched_plan_file(tmp_path, artifact_kinds=ARTIFACT_KINDS)

    plan = read_publish_plan(path)

    entry, tag_only = plan[0]
    artifacts = entry["artifacts"]
    assert [kind_of(a["path"]) for a in artifacts] == list(ARTIFACT_KINDS)
    assert [PurePosixPath(a["path"]).name for a in artifacts] == [
        sdist_name("pkg-a", "1.0.0"),
        wheel_name("pkg-a", "1.0.0"),
    ]
    assert all(a["integrity"].startswith(INTEGRITY_PREFIX) for a in artifacts)
    assert len({a["integrity"] for a in artifacts}) == 2, (
        "integrity is per artifact, not per release"
    )
    assert "artifacts" not in tag_only, "a tag-only release is never built and never uploaded"


# (artifact kinds the entry lists, why the plan must be refused)
BAD_ARTIFACT_LIST_CASES: list[tuple[tuple[str, ...], str]] = [
    (("sdist",), "an sdist-only release forces every consumer to build from source"),
    (("wheel",), "a wheel-only release is uninstallable wherever no wheel tag matches"),
    (
        ("wheel", "sdist"),
        "both artifacts, unstable order: the plan is a document that gets diffed and reviewed, so "
        "the same release must serialize the same way on every filesystem",
    ),
]


@pytest.mark.parametrize(("kinds", "why"), BAD_ARTIFACT_LIST_CASES)
def test_a_publish_entry_with_a_bad_artifact_list_is_rejected(
    tmp_path: Path, kinds: tuple[str, ...], why: str
) -> None:
    """molt-NEW: the two-artifact rule is enforced when the plan is read, not when it is uploaded.

    PyPI is immutable, so validation has to be exhaustive **before the first upload** rather than
    per-package during it (research README section 4.3; group file "New in molt" #1). Half a
    release discovered halfway through a monorepo publish cannot be rolled back -- only yanked,
    and only after the version number is already spent.

    ``read_publish_plan`` is the choke point every consumer goes through: ``molt publish
    --from-pack-dir`` (``publish/index.ts:89-91`` reads the plan out of the artifact directory) and
    ``molt build --from-publish-plan``. An entry with no ``artifacts`` key at all is *not* an error
    -- that is simply a plan that has not been built yet, which is what ``publish-plan --output``
    writes.
    """
    path = build_enriched_plan_file(tmp_path, artifact_kinds=kinds)

    with pytest.raises(Exception, match=r"(?i)artifact") as excinfo:
        read_publish_plan(path)

    assert "pkg-a" in str(excinfo.value), (
        f"pre-flight validation names the release it refuses, or the operator cannot act on it "
        f"({why})"
    )


# (envelope, why the reader must refuse it)
UNSUPPORTED_ENVELOPES: list[tuple[Any, str]] = [
    ({"version": PUBLISH_PLAN_VERSION + 1, "plan": []}, "a newer envelope than this build knows"),
    (
        {"version": PUBLISH_PLAN_VERSION - 1, "plan": []},
        "older: the guard is equality, not a floor",
    ),
    ({"plan": []}, "no version key at all (`getPublishPlan.ts:69-73`)"),
    ([], "the envelope must be an object (`getPublishPlan.ts:61-63`)"),
]


@pytest.mark.parametrize(("envelope", "why"), UNSUPPORTED_ENVELOPES)
def test_the_envelope_version_guard_lives_in_the_plan_reader(
    tmp_path: Path, envelope: Any, why: str
) -> None:
    """molt-NEW, added by review: the guard's **location** is the contract, not just its effect.

    ``tests/publish/test_pack.py::test_rejects_an_unsupported_plan_file_version`` drives the same
    guard through ``molt build``, so it passes just as happily against an implementation that
    inlines the check in ``pack()``. That implementation then leaves ``molt publish
    --from-pack-dir`` -- which reads the plan out of the artifact directory, ``publish/index.ts``
    :89-91 -- completely unguarded, and a foreign plan reaching *that* stage is an irreversible
    upload from a document molt did not produce (design D6; the risk this change's ``design.md``
    names first).

    Calling :func:`read_publish_plan` directly is the only way to pin that, because it is the one
    function both consumers go through.
    """
    path = write_plan_file(tmp_path / "publish-plan.json", envelope)

    with pytest.raises(Exception, match=r"(?i)publish plan") as excinfo:
        read_publish_plan(path)

    assert path.name in str(excinfo.value), (
        f"the refusal names the file it refuses, or an operator holding three plan files cannot "
        f"tell which one is wrong ({why})"
    )
