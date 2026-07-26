"""Conformance tests for ``molt build`` -- the pack stage of the publish pipeline.

Ports ``packages/cli/src/commands/pack/__tests__/index.test.ts`` (3 rows) and
``.../pack/__tests__/e2e.test.ts`` (2 rows) as catalogued in
``roadmap/research/test-suite/07-publish-pack.md``. Website doc: ``website/docs/cli/pack.md``.

Naming, deliberate and cross-checked
------------------------------------
The CLI *command* is ``molt build`` (``tests/cli/test_cli.py`` pins the route as
``molt.commands.build``, with no ``pack`` alias); the *library* module stays ``molt.pack`` per
test-contract section 5 and the frozen :func:`~tests.publish.fake_publish.require_pack` guard,
because ``molt.build`` would read as a build backend sitting next to ``python -m build``.

Seams pinned by this file (report them to the owner if any is overruled)
------------------------------------------------------------------------
- ``molt.pack.pack(*, cwd, out_dir, from_publish_plan=None, console, builder)``. Test-contract
  section 5 sketches ``pack(package, *, out_dir) -> list[Path]``, a *per package* helper; the
  command-level entry point has to take a plan, because the plan is what decides which packages are
  built at all and what the enriched output must contain. Both can exist -- this file pins the one
  ``molt.commands.build`` calls, which is the one :func:`require_pack` guards.
- **The artifact layout is not pinned.** Assertions resolve every artifact through the ``path`` the
  plan itself recorded, so ``dist/packages/x.whl`` and ``dist/x.whl`` are both acceptable as long as
  the plan is self-consistent. What *is* pinned is that the path is relative and POSIX-spelled -- a
  plan written on Windows must be readable by the Linux job that uploads it.

Deliberate divergences from upstream
------------------------------------
- **Two artifacts, not one.** ``npm pack`` writes a single ``.tgz`` and the entry carries a
  ``tarball: {path, integrity}`` object (`getPublishPlan.ts:30-33`); ``python -m build`` writes an
  sdist *and* a wheel, so the entry carries an ``artifacts`` **list**, ordered
  :data:`~tests.publish.fake_publish.ARTIFACT_KINDS` = (sdist, wheel).
- **Integrity is hex, not base64.** Upstream emits an npm SRI string ``sha256-<base64>``
  (`pack/index.ts:68-72`); molt emits ``sha256=<hex>``, the spelling twine sends and the PEP 503
  simple index publishes. The single source of truth is
  :data:`~tests.publish.fake_publish.INTEGRITY_PREFIX`.
- **No inline snapshot.** Upstream pins the whole ``publish-plan.json`` with
  ``toMatchInlineSnapshot`` (`index.test.ts:103-127`). A guarded module never runs, so a snapshot
  written here would later be blessed from whatever the implementation first emitted -- including
  on the very fields that exist to diverge from upstream. Explicit literals instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from tests.cli.fake_cli import RecordingConsole
from tests.publish.fake_publish import (
    ARTIFACT_KINDS,
    INTEGRITY_PREFIX,
    PUBLISH_PLAN_VERSION,
    FakeBuilder,
    integrity_of,
    publish_entry,
    publish_plan,
    require_pack,
    sdist_name,
    tag_only_entry,
    wheel_name,
)

#: The command under test, bound off the frozen two-stage guard rather than imported with a
#: ``from molt.pack import pack`` line: that spelling would sit after a non-``importorskip``
#: statement, and ruff's ``E402`` exemption only recognises a literal bare
#: ``pytest.importorskip(...)``. See ``tests/publish/test_publish.py`` for the same note.
pack: Any = require_pack().pack

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import ProjectBuilder, PyPIRegistry

pytestmark = [pytest.mark.functional, pytest.mark.network]


# --------------------------------------------------------------------------------------
# Local scaffolding
# --------------------------------------------------------------------------------------

CHANGESET_README = "# Changesets\n\nThis folder holds molt changeset files.\n"


@dataclass(frozen=True)
class Pkg:
    """One workspace member."""

    name: str
    version: str = "1.0.0"
    private: bool = False


def workspace(project: ProjectBuilder, *packages: Pkg, **config: Any) -> Path:
    """Add ``packages`` to ``project``, apply ``[tool.molt]`` ``config``, seed ``.changeset/``."""
    for pkg in packages:
        project.add_package(pkg.name, pkg.version, private=pkg.private)
    if config:
        project.set_config(**config)
    changeset_dir = project.root / ".changeset"
    changeset_dir.mkdir(parents=True, exist_ok=True)
    (changeset_dir / "README.md").write_bytes(CHANGESET_README.encode("utf-8"))
    return project.root


def write_plan_file(path: Path, document: Any) -> Path:
    """Serialize ``document`` (any JSON value) to ``path``; return ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(document, indent=2) + "\n").encode("utf-8"))
    return path


def read_plan(out_dir: Path) -> dict[str, Any]:
    """The enriched envelope ``molt build`` wrote."""
    return json.loads((out_dir / "publish-plan.json").read_text(encoding="utf-8"))


def entries_of(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Every entry of every chunk, flattened, in plan order."""
    return [entry for chunk in plan["plan"] for entry in chunk]


def run_failing(call: Callable[[], object]) -> BaseException:
    """Run ``call``, return the exception it raised; fail the test if it returned normally."""
    try:
        call()
    except Exception as exc:
        return exc
    pytest.fail("expected the build to fail, but it returned normally")


def assert_artifacts(out_dir: Path, entry: dict[str, Any], name: str, version: str) -> None:
    """Every requirement the enriched ``artifacts`` list has to satisfy, in one place.

    Kept as a helper rather than a fixture so the *set* of requirements is visible next to the rows
    that use it: two artifacts in (sdist, wheel) order, each one actually on disk under the path the
    plan recorded, each digest computed over that file's own bytes, and every path relative and
    POSIX-spelled so a plan written on Windows survives the trip to the upload job.
    """
    artifacts = entry["artifacts"]
    assert [Path(artifact["path"]).name for artifact in artifacts] == [
        sdist_name(name, version),
        wheel_name(name, version),
    ], f"expected one artifact per {ARTIFACT_KINDS}, in that order"
    digests = set()
    for artifact in artifacts:
        recorded = artifact["path"]
        assert "\\" not in recorded, "plan paths are POSIX-spelled, on every platform"
        assert not Path(recorded).is_absolute(), "plan paths are relative to the output directory"
        built = out_dir / recorded
        assert built.is_file(), f"{recorded} is named by the plan but absent from disk"
        assert built.stat().st_size > 0
        assert artifact["integrity"].startswith(INTEGRITY_PREFIX)
        assert artifact["integrity"] == integrity_of(built)
        digests.add(artifact["integrity"])
    assert len(digests) == len(artifacts), "one digest per artifact, not one digest reused"


# --------------------------------------------------------------------------------------
# pack/index.test.ts row 1 - build the publish releases, write an enriched plan
# --------------------------------------------------------------------------------------


def test_packs_publish_releases_and_writes_an_enriched_plan(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 1, Adapt (`pack/index.ts:74-228`; `index.test.ts:64-131`).

    ``molt build`` with no ``--from-publish-plan`` computes the plan itself, which means querying
    the index -- upstream's ``npm info`` mock boundary becomes ``GET pypi.org/pypi/<name>/json``.
    ``bravo`` is already released at its local version, so it never reaches the builder: building an
    artifact nobody will upload is wasted work, and its presence in the enriched plan would make
    ``publish --from-pack-dir`` re-attempt a published version.

    Upstream compares the whole file against an inline snapshot; this asserts the fields instead
    (see the module docstring on why a snapshot would be blessed rather than checked here).
    """
    root = workspace(tmp_project, Pkg("alpha"), Pkg("bravo"))
    pypi_registry.set_versions("bravo", ["1.0.0"])
    out_dir = root / ".packed"
    builder = FakeBuilder()

    pack(cwd=root, out_dir=out_dir, console=RecordingConsole(), builder=builder)

    assert builder.built == ["alpha"], "an already-published release is not built"
    plan = read_plan(out_dir)
    assert plan["version"] == PUBLISH_PLAN_VERSION
    entries = entries_of(plan)
    assert [(entry["kind"], entry["name"], entry["version"]) for entry in entries] == [
        ("publish", "alpha", "1.0.0")
    ]
    assert_artifacts(out_dir, entries[0], "alpha", "1.0.0")


def test_tag_only_entries_pass_through_the_enriched_plan_untouched(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 1, second half (`pack/index.ts:188-203` -- ``if (release.kind !== "publish") return``).

    A ``tag-only`` release is git-tagged and never uploaded, so there is nothing to build for it and
    nothing to record. The assertion is byte-for-byte equality with the entry that went in, not just
    "has no artifacts key": rewriting the entry (dropping ``directory``, re-ordering keys, adding an
    empty ``artifacts: []``) would make ``publish --from-pack-dir`` see a different document from
    the one ``publish-plan`` produced, and the three stages of the pipeline only compose because the
    envelope survives each of them unchanged.
    """
    root = workspace(tmp_project, Pkg("alpha"), Pkg("bravo", private=True))
    source = write_plan_file(
        root / "publish-plan.json",
        publish_plan([publish_entry("alpha", "1.0.0"), tag_only_entry("bravo", "1.0.0")]),
    )
    out_dir = root / ".packed"
    builder = FakeBuilder()

    pack(
        cwd=root,
        out_dir=out_dir,
        from_publish_plan=source,
        console=RecordingConsole(),
        builder=builder,
    )

    assert builder.built == ["alpha"], "nothing is built for a tag-only release"
    entries = entries_of(read_plan(out_dir))
    assert entries[1] == tag_only_entry("bravo", "1.0.0")
    assert "artifacts" not in entries[1]


def test_the_enriched_plan_preserves_the_chunk_boundaries(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """The envelope is a list of **chunks**, and the chunking is the topological order.

    ``pack/index.ts:188-203`` maps group-by-group precisely so the shape survives; flattening it
    would erase the only property ``publish`` needs from the file it is handed
    (``tests/publish/test_publish.py`` asserts upload order against exactly these boundaries).
    """
    root = workspace(tmp_project, Pkg("mike"), Pkg("zulu"), Pkg("alpha"))
    source = write_plan_file(
        root / "publish-plan.json",
        publish_plan(
            [publish_entry("mike", "1.0.0")],
            [publish_entry("zulu", "1.0.0")],
            [publish_entry("alpha", "1.0.0")],
        ),
    )
    out_dir = root / ".packed"

    pack(
        cwd=root,
        out_dir=out_dir,
        from_publish_plan=source,
        console=RecordingConsole(),
        builder=FakeBuilder(),
    )

    plan = read_plan(out_dir)
    assert [[entry["name"] for entry in chunk] for chunk in plan["plan"]] == [
        ["mike"],
        ["zulu"],
        ["alpha"],
    ]


# --------------------------------------------------------------------------------------
# pack/index.test.ts row 2 + pack/e2e.test.ts row 1 - build from an existing plan file
# --------------------------------------------------------------------------------------


@pytest.mark.e2e
def test_packs_from_an_existing_plan_file_and_recomputes_integrity(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 2, Adapt (`pack/index.ts:81-83`; `index.test.ts:133-189`) + e2e row 1 (`e2e.test.ts:35`).

    The two rows assert the same thing at different fidelities -- the unit row checks the plan
    contains the computed integrity, the e2e row checks the artifact is non-empty on disk and the
    integrity matches ``/^sha256-/`` -- so molt covers both in one place with
    :func:`assert_artifacts`, which checks the digest against the file's *own* bytes rather than
    against a prefix. A prefix match would pass against an implementation that hashed the filename.

    Splitting plan from build is what makes ``molt publish-plan | molt build | molt publish``
    composable across CI jobs (``cli/pack.md``, "Splitting build from upload").
    """
    root = workspace(tmp_project, Pkg("alpha", version="2.3.4"))
    source = write_plan_file(
        root / "publish-plan.json", publish_plan([publish_entry("alpha", "2.3.4")])
    )
    out_dir = root / "artifacts"

    pack(
        cwd=root,
        out_dir=out_dir,
        from_publish_plan=source,
        console=RecordingConsole(),
        builder=FakeBuilder(),
    )

    entries = entries_of(read_plan(out_dir))
    assert len(entries) == 1
    assert_artifacts(out_dir, entries[0], "alpha", "2.3.4")


# --------------------------------------------------------------------------------------
# pack/index.test.ts row 3 - the versioned plan-envelope guard
# --------------------------------------------------------------------------------------

# (document, why)
BAD_ENVELOPES: list[tuple[Any, str]] = [
    ({"version": 2, "plan": []}, "a newer envelope than this build understands"),
    ({"version": 0, "plan": []}, "an older envelope: the guard is equality, not a floor"),
    ({"version": "1", "plan": []}, "the version is an integer; the string form is a different doc"),
    ({"plan": []}, "no version key at all (`getPublishPlan.ts:69-73`)"),
    ({"version": PUBLISH_PLAN_VERSION}, "no plan key (`getPublishPlan.ts:65-67`)"),
    ({"version": PUBLISH_PLAN_VERSION, "plan": {}}, "plan must be a list of chunks"),
    ([], "the envelope must be an object (`getPublishPlan.ts:61-63`)"),
    (None, "JSON null is not an envelope (`getPublishPlan.ts:61-63`)"),
]


@pytest.mark.parametrize(("document", "why"), BAD_ENVELOPES)
def test_rejects_an_unsupported_plan_file_version(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry, document: Any, why: str
) -> None:
    """Row 3, a straight Port (`getPublishPlan.ts:58-76`; `index.test.ts:191-218`).

    ``readPlanFile`` is the only thing standing between a plan file written by one version of the
    tool and an upload performed by another, and on an immutable index a misread plan is not
    recoverable. Upstream matches ``/Invalid publish plan file version/``; molt asserts the two
    effects instead -- the call fails, and **nothing was built** -- because the message text is the
    product's to choose while "reject before doing any work" is the contract.

    Parametrized beyond upstream's single ``version: 2`` row: the same guard rejects a missing
    version, a string version, a missing plan and a non-object envelope, and a guard that only
    catches the one case upstream tested is not the guard `readPlanFile` implements.
    """
    root = workspace(tmp_project, Pkg("alpha"))
    source = write_plan_file(root / "publish-plan.json", document)
    out_dir = root / ".packed"
    builder = FakeBuilder()

    run_failing(
        lambda: pack(
            cwd=root,
            out_dir=out_dir,
            from_publish_plan=source,
            console=RecordingConsole(),
            builder=builder,
        )
    )

    assert builder.calls == [], why
    assert not (out_dir / "publish-plan.json").exists(), why


def test_the_current_envelope_version_is_accepted(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """The positive control for the row above.

    Without it, ``test_rejects_an_unsupported_plan_file_version`` passes just as happily against an
    implementation that rejects *every* plan file, which is the failure mode a table of negatives
    invites.
    """
    root = workspace(tmp_project, Pkg("alpha"))
    source = write_plan_file(
        root / "publish-plan.json",
        publish_plan([publish_entry("alpha", "1.0.0")], version=PUBLISH_PLAN_VERSION),
    )
    out_dir = root / ".packed"

    pack(
        cwd=root,
        out_dir=out_dir,
        from_publish_plan=source,
        console=RecordingConsole(),
        builder=FakeBuilder(),
    )

    assert read_plan(out_dir)["version"] == PUBLISH_PLAN_VERSION


# --------------------------------------------------------------------------------------
# pack/e2e.test.ts row 2 - a build failure surfaces and writes nothing
# --------------------------------------------------------------------------------------


@pytest.mark.e2e
def test_a_build_failure_surfaces_the_error_and_writes_no_plan(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """e2e row 2, Adapt (`e2e.test.ts:103-160`); ``cli/pack.md``: "surfaces the error and writes no
    plan file -- there is no half-built output to clean up".

    Upstream's ``prepack`` script exits 1 and the assertion is that ``.packed/publish-plan.json``
    cannot be opened (`:157-159`). Upstream gets "wrote nothing" for free because ``writeFile``
    happens after ``Promise.all`` resolves (`pack/index.ts:97-215`); an implementation that enriched
    the plan incrementally would not.

    **The failure is planted on the second build, not the first.** With ``fail_on={"alpha"}`` the
    run would die before any artifact existed, and "no plan file" would be true of an implementation
    that never writes one at all. Failing after ``alpha`` has already been built means a partial
    plan was genuinely writable at that moment -- which is what the assertion has to rule out. The
    ``builder.built`` check keeps that premise honest.
    """
    root = workspace(tmp_project, Pkg("alpha"), Pkg("bravo"))
    source = write_plan_file(
        root / "publish-plan.json",
        publish_plan([publish_entry("alpha", "1.0.0")], [publish_entry("bravo", "1.0.0")]),
    )
    out_dir = root / ".packed"
    builder = FakeBuilder(fail_on={"bravo"})

    run_failing(
        lambda: pack(
            cwd=root,
            out_dir=out_dir,
            from_publish_plan=source,
            console=RecordingConsole(),
            builder=builder,
        )
    )

    assert builder.built == ["alpha", "bravo"], "the first build succeeded: a partial write was on"
    assert not (out_dir / "publish-plan.json").exists()
