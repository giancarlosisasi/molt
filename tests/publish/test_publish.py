"""Conformance tests for ``molt publish`` -- the upload stage of the publish pipeline.

Ports the surviving rows of ``packages/cli/src/commands/publish/__tests__/index.test.ts`` (rows 1,
3, 4, 5, 6, 7) and ``.../publish/__tests__/e2e.test.ts`` (rows 1, 2, 5, 6, 7, 8, 9, 10, 11) as
catalogued in ``roadmap/research/test-suite/07-publish-pack.md``, plus the two molt-native rows that
group file lists under "New in molt": exhaustive pre-flight validation before the first upload, and
an authentication path with nothing to prompt for. Website docs: ``website/docs/cli/publish.md``.

The dropped rows (``--tag`` / dist-tag routing, the ``only-pre`` heuristic, OTP / 2FA / web-auth /
AuthState, the 5-package-manager matrix) are recorded as skips in
``tests/publish/test_deliberately_not_ported.py``; they are deliberately absent here.

Seams pinned by this file (report them to the owner if any is overruled)
-----------------------------------------------------------------------
- ``molt.publish.publish(*, cwd, from_pack_dir=None, output=None, repository=None, git_tag=True,
  console, git, builder, uploader, oidc)``. The ``console=``/``git=`` keywords follow the CLI
  suite's frozen injection convention (``tests/cli/fake_cli.py``); ``builder=``/``uploader=``/
  ``oidc=`` follow the frozen ``tests/publish/fake_publish.py``.
- **Failure is signalled by raising**, and these tests assert on the *effects* (what was uploaded,
  what was tagged, what was written) rather than on an exception type. The CLI funnel owns exit
  codes and ``tests/cli/test_cli.py`` already pins it, so pinning a second name here would be
  guesswork. Every failure row therefore carries a state assertion -- "an error was raised" alone
  cannot tell a correct implementation from a broken one.
- **A duplicate upload is classified structurally, but the status code is not sufficient on its
  own.** The frozen doubles raise their own exception classes
  (``tests.publish.fake_publish.DuplicateUpload``), which no product module can import, so the
  ``400 File already exists`` case has to be recognised duck-typed --
  ``DuplicateUpload.status_code == DUPLICATE_UPLOAD_STATUS`` is the hook the harness provides.
  **The status alone is not the rule.** Upstream's ``isDuplicatePublishError``
  (``npm-utils.ts:339-354``) is a conjunction: an accepted error *code* **and** a message
  containing "cannot publish over the previously published version". On PyPI that second half
  matters more, not less: ``POST upload.pypi.org/legacy/`` answers 400 for every rejected upload,
  duplicate or not. ``test_a_400_that_is_not_a_duplicate_is_a_failure_not_a_skip`` pins the
  difference, because the frozen ``FakeUploader`` has no knob for it.
- ``pypi_registry.stale`` (``tests/conftest.py``) drives the *HTTP* upload endpoint, which an
  injected uploader never reaches; the stale-read race is therefore driven through
  ``FakeUploader(already_published=...)``, which the frozen harness provides for exactly this row.

Deliberate divergences from the group file
------------------------------------------
- **NDJSON keys are snake_case** (``package_name``, not upstream's ``packageName``,
  ``utils/output.ts:6-10``). ``tests/cli/test_git_tag.py`` already pins the event shape and a
  publish-emitted ``git-tag`` event must be the same document.
- **Two artifacts per release.** ``npm pack`` produces one ``.tgz``; ``python -m build`` produces an
  sdist *and* a wheel, so the entry's ``tarball`` object becomes an ``artifacts`` list (group 7,
  "New in molt" #4).
- **PEP 440 prerelease spellings** (``1.0.0rc1``) replace upstream's ``1.0.0-beta.0``: the npm
  spelling is not a legal PEP 440 version at all.
"""

from __future__ import annotations

import inspect
import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from tests.cli.fake_cli import FakeGit, RecordingConsole, read_ndjson
from tests.publish.fake_publish import (
    ARTIFACT_KINDS,
    DUPLICATE_UPLOAD_STATUS,
    PUBLISH_PLAN_VERSION,
    FakeBuilder,
    FakeOIDC,
    FakeUploader,
    UploadFailed,
    integrity_of,
    publish_entry,
    publish_plan,
    require_publish,
    sdist_name,
    tag_only_entry,
    wheel_name,
)

#: The command under test, taken from the frozen two-stage guard rather than imported with a
#: ``from molt.publish import publish`` line. That spelling would sit *after* a non-``importorskip``
#: statement, and ruff's ``E402`` exemption only recognises a literal bare
#: ``pytest.importorskip(...)`` -- verified against this repo's ruff config, not assumed. Binding
#: the symbol off the guard's return value keeps both stages (module absent -> skip; module present
#: but still a placeholder -> skip) and leaves the import block clean.
publish: Any = require_publish().publish

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from tests.conftest import ProjectBuilder, PyPIRegistry

pytestmark = [pytest.mark.functional, pytest.mark.network]


# --------------------------------------------------------------------------------------
# Local scaffolding
#
# `tests/cli/conftest.py` keeps the `console`/`fake_git` fixtures inside the CLI package, so this
# package constructs the doubles inline. Nothing here belongs in the frozen harness: it is workspace
# shaping, which every group builds differently.
# --------------------------------------------------------------------------------------

CHANGESET_README = "# Changesets\n\nThis folder holds molt changeset files.\n"


@dataclass(frozen=True)
class Pkg:
    """One workspace member: name, version, PEP 508 dependency strings, privacy."""

    name: str
    version: str = "1.0.0"
    deps: tuple[str, ...] = ()
    private: bool = False


#: A three-link dependency chain whose topological order (`mike`, `zulu`, `alpha`) differs from
#: alphabetical order, from reverse-alphabetical order, and from declaration order. Two packages
#: would not be enough: with `pkg-a` depending on `pkg-b`, "reverse alphabetical" and "declaration
#: order reversed" both produce the right answer by accident, and the moat row would pass against an
#: implementation that does no graph work at all.
CHAIN: tuple[Pkg, ...] = (
    Pkg("alpha", deps=("zulu==1.0.0",)),
    Pkg("zulu", deps=("mike==1.0.0",)),
    Pkg("mike"),
)

#: The only correct upload order for :data:`CHAIN` -- a dependency uploads before its dependents.
TOPOLOGICAL: list[str] = ["mike", "zulu", "alpha"]


def workspace(project: ProjectBuilder, *packages: Pkg, **config: Any) -> Path:
    """Add ``packages`` to ``project``, apply ``[tool.molt]`` ``config``, seed ``.changeset/``."""
    for pkg in packages:
        project.add_package(pkg.name, pkg.version, deps=list(pkg.deps), private=pkg.private)
    if config:
        project.set_config(**config)
    changeset_dir = project.root / ".changeset"
    changeset_dir.mkdir(parents=True, exist_ok=True)
    (changeset_dir / "README.md").write_bytes(CHANGESET_README.encode("utf-8"))
    return project.root


def write_artifacts(pack_dir: Path, name: str, version: str) -> list[dict[str, str]]:
    """Write a fake sdist+wheel under ``pack_dir/packages`` and return the plan's artifact list.

    Mirrors what :class:`~tests.publish.fake_publish.FakeBuilder` writes, so a ``--from-pack-dir``
    run sees exactly the bytes a ``molt build`` run would have left behind. Distinct content per
    file keeps a per-artifact integrity assertion from being satisfied by one shared digest.
    """
    directory = pack_dir / "packages"
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, str]] = []
    for filename in (sdist_name(name, version), wheel_name(name, version)):
        path = directory / filename
        path.write_bytes(f"fake artifact for {filename}".encode())
        artifacts.append({"path": f"packages/{filename}", "integrity": integrity_of(path)})
    return artifacts


def packed(pack_dir: Path, name: str, version: str = "1.0.0") -> dict[str, Any]:
    """A ``publish`` plan entry whose artifacts are already on disk under ``pack_dir``."""
    return publish_entry(name, version, artifacts=write_artifacts(pack_dir, name, version))


def write_plan_file(pack_dir: Path, plan: dict[str, Any]) -> Path:
    """Serialize ``plan`` to ``pack_dir/publish-plan.json``; return ``pack_dir``."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "publish-plan.json").write_bytes(
        (json.dumps(plan, indent=2) + "\n").encode("utf-8")
    )
    return pack_dir


def run_failing(call: Callable[[], object]) -> BaseException:
    """Run ``call``, return the exception it raised; fail the test if it returned normally.

    The *type* is deliberately not asserted (see the module docstring): every caller pairs this
    with an assertion about what was uploaded, tagged or written, which is what actually
    distinguishes a correct implementation from a broken one.
    """
    try:
        call()
    except Exception as exc:
        return exc
    pytest.fail("expected the publish to fail, but it returned normally")


def event(tag: str, package_name: str) -> dict[str, Any]:
    """One NDJSON ``git-tag`` event, snake_case -- identical to ``tests/cli/test_git_tag.py``."""
    return {"type": "git-tag", "tag": tag, "package_name": package_name}


def pypi_reads(registry: PyPIRegistry) -> list[str]:
    """Project names queried on ``GET pypi.org/pypi/<name>/json``, in call order.

    Same reach through ``PyPIRegistry._router`` as ``tests/publish/test_plan.py::pypi_reads`` --
    the frozen fixture exposes no call log of its own, which is a harness gap reported to the
    orchestrator. Duplicated rather than imported because importing a sibling *test* module would
    run its module-level guard a second time.
    """
    names: list[str] = []
    for call in registry._router.calls:
        segments = [part for part in call.request.url.path.split("/") if part]
        if len(segments) >= 3 and segments[0] == "pypi" and segments[-1] == "json":
            names.append(segments[-2])
    return names


class BadRequestUploader(FakeUploader):
    """A twine double that fails with HTTP 400 for a reason that is **not** a duplicate.

    The frozen :class:`~tests.publish.fake_publish.FakeUploader` has no knob for this, and it is
    the one case that separates a faithful port of ``isDuplicatePublishError`` from a lossy one:
    upstream requires the status code **and** the message
    (``npm-utils.ts:339-354`` -- ``(code === "E403" || ...) && isAlreadyPublishedError(message)``).
    PyPI's legacy upload endpoint answers 400 for many things that are not "File already exists"
    -- an invalid classifier, a disallowed project name, an oversized file -- so a classifier
    keyed on the status alone silently reports a dropped release as a skip.
    """

    def __init__(self, *, reject: set[str]) -> None:
        super().__init__()
        self.reject: set[str] = set(reject)

    def upload(
        self,
        artifacts: list[Path] | tuple[Path, ...],
        *,
        package: str,
        version: str,
        repository: str | None = None,
    ) -> None:
        super().upload(artifacts, package=package, version=version, repository=repository)
        if package in self.reject:
            failure = UploadFailed(
                f"{package}: 400 Bad Request -- 'Foo :: Bar' is not a valid classifier"
            )
            failure.status_code = DUPLICATE_UPLOAD_STATUS  # pyrefly: ignore[missing-attribute]
            raise failure


def tags_for(names: Sequence[str], version: str = "1.0.0") -> list[str]:
    return [f"{name}@{version}" for name in names]


# --------------------------------------------------------------------------------------
# publish/index.test.ts row 3 - "publishes release chunks sequentially" (the moat)
# --------------------------------------------------------------------------------------


def test_publishes_release_chunks_in_topological_order(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 3, a straight Port (`publish/index.ts:98-215`; `index.test.ts:119-165`).

    Upstream asserts the *order* of the ``publish`` exec calls (`index.test.ts:153-160`) and then
    the order of the ``git.tag`` calls (`:161-164`). molt asserts the same two orders against the
    twine double's :attr:`~tests.publish.fake_publish.FakeUploader.order` and ``FakeGit.tags``.

    Order, not membership: dependency-order chunking is the monorepo moat (group 7 header, "What
    ports vs what dies"), and a set-equality assertion would pass against an implementation that
    uploads a dependent before its dependency -- the one failure mode that cannot be undone on an
    immutable index. The two "not sorted" assertions exist so a lucky alphabetical implementation
    cannot pass either.
    """
    root = workspace(tmp_project, *CHAIN)
    git, uploader, builder = FakeGit(), FakeUploader(), FakeBuilder()

    publish(
        cwd=root,
        console=RecordingConsole(),
        git=git,
        builder=builder,
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert uploader.order == TOPOLOGICAL
    assert git.tags == tags_for(TOPOLOGICAL)
    assert builder.built == TOPOLOGICAL
    assert uploader.order != sorted(uploader.order), "alphabetical order is not topological order"
    assert uploader.order != sorted(uploader.order, reverse=True)


def test_every_release_uploads_both_the_sdist_and_the_wheel(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Group 7, "New in molt" #4: ``python -m build`` produces **two** artifacts per release.

    Upstream's plan entry carries a single ``tarball`` (`getPublishPlan.ts:30-33,43-48`) because
    ``npm pack`` produces one ``.tgz``. On PyPI a release is an sdist *and* a wheel, and uploading
    only one of them ships a broken release that immutability makes permanent. Asserted as an
    ordered pair, since :data:`ARTIFACT_KINDS` fixes the order as (sdist, wheel).
    """
    root = workspace(tmp_project, Pkg("alpha"))
    uploader = FakeUploader()

    publish(
        cwd=root,
        console=RecordingConsole(),
        git=FakeGit(),
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert ARTIFACT_KINDS == ("sdist", "wheel")
    assert [call.artifacts for call in uploader.calls] == [
        (sdist_name("alpha", "1.0.0"), wheel_name("alpha", "1.0.0"))
    ]


# --------------------------------------------------------------------------------------
# publish/index.test.ts row 1 - ignored / private packages are neither uploaded nor tagged
# --------------------------------------------------------------------------------------

# (packages, ignore, why)
IGNORE_CASES: list[tuple[tuple[Pkg, ...], list[str], str]] = [
    (
        (Pkg("alpha", private=True),),
        ["alpha"],
        "upstream's fixture exactly: the package is both ignored and private (index.test.ts:66-96)",
    ),
    (
        (Pkg("alpha"),),
        ["alpha"],
        "ignore alone is enough - a publishable package on the ignore list is still untouched",
    ),
    (
        (Pkg("Foo_Bar"),),
        ["foo-bar"],
        "PEP 503: the ignore list is matched against the normalized name (research README 4.5)",
    ),
]


@pytest.mark.parametrize(("packages", "ignore", "why"), IGNORE_CASES)
def test_ignored_packages_are_neither_uploaded_nor_tagged(
    tmp_project: ProjectBuilder,
    pypi_registry: PyPIRegistry,
    packages: tuple[Pkg, ...],
    ignore: list[str],
    why: str,
) -> None:
    """Row 1, Adapt (`getPublishPlan.ts:106-109` shouldSkipPackage; `index.test.ts:66-96`).

    Upstream asserts three negatives -- ``tagExists``, ``remoteTagExists`` and ``tag`` were never
    called. molt collapses the two lookups into one batched read (pinned by
    ``tests/cli/test_git_tag.py::test_existing_tag_lookup_is_batched``), so the surviving invariant
    is "don't tag what you don't touch": no git mutation at all, and -- the half upstream gets for
    free from its exec mock -- no upload either.

    ``git.calls`` rather than ``git.tags`` so an ``add``/``commit`` would fail this too.
    """
    root = workspace(tmp_project, *packages, ignore=ignore)
    git, uploader = FakeGit(), FakeUploader()

    publish(
        cwd=root,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert uploader.calls == [], why
    assert git.calls == [], why


# --------------------------------------------------------------------------------------
# publish/index.test.ts row 7 - tag-only releases are tagged inside their chunk
# --------------------------------------------------------------------------------------


def test_tags_tag_only_releases_within_their_chunk(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 7, Adapt (`publish/index.ts:105-176`; `index.test.ts:297-344`).

    Upstream's trigger is ``"private": true`` plus ``privatePackages.tag``; molt has no ``tag``
    sub-option at all (``tests/config/test_json_schema.py`` pins its absence), so the concept is
    re-framed the way the frozen harness states it: a ``tag-only`` entry is a release that is
    git-tagged but never uploaded. The plan is supplied directly through ``--from-pack-dir`` so the
    row asserts what ``publish`` does with such an entry, not how the planner decides to emit one
    (that decision belongs to ``tests/publish/test_plan.py``).

    Order is load-bearing and matches upstream (`:340-343`): within one chunk the uploaded releases
    are tagged first, then the tag-only ones -- upstream tags after the publishes precisely so the
    tag is never created for something that failed to upload.
    """
    root = workspace(tmp_project, Pkg("alpha"), Pkg("bravo", private=True))
    pack_dir = write_plan_file(
        root / ".packed",
        publish_plan([packed(root / ".packed", "alpha"), tag_only_entry("bravo", "1.0.0")]),
    )
    git, uploader = FakeGit(), FakeUploader()

    publish(
        cwd=root,
        from_pack_dir=pack_dir,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert uploader.order == ["alpha"], "a tag-only release is never uploaded"
    assert git.tags == ["alpha@1.0.0", "bravo@1.0.0"]


# --------------------------------------------------------------------------------------
# publish/index.test.ts rows 4 + 5 - the NDJSON output reporter
# --------------------------------------------------------------------------------------


def test_writes_one_ndjson_git_tag_event_per_tag(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry, tmp_path: Path
) -> None:
    """Row 4, Port (`publish/index.ts:190-215` + ``utils/output.ts``; `index.test.ts:167-214`).

    Upstream pins the exact bytes: two ``JSON.stringify`` lines joined with ``""`` -- i.e. a
    terminating newline (`index.test.ts:199-213`, ``utils/output.ts:29``). molt keeps the format and
    renames ``packageName`` to ``package_name``; the negative assertion on the camelCase spelling is
    what stops the rename from silently regressing, and matches
    ``tests/cli/test_git_tag.py::test_writes_one_ndjson_event_per_tag`` byte for byte so the two
    producers of a ``git-tag`` event cannot drift apart.

    The CRLF assertion is not cosmetic on Windows: an NDJSON consumer splitting on LF must not see a
    stray CR, whatever platform wrote the file.
    """
    root = workspace(tmp_project, Pkg("alpha"), Pkg("bravo"))
    out = tmp_path / "output.ndjson"

    publish(
        cwd=root,
        output=out,
        console=RecordingConsole(),
        git=FakeGit(),
        builder=FakeBuilder(),
        uploader=FakeUploader(),
        oidc=FakeOIDC(),
    )

    assert read_ndjson(out) == [event("alpha@1.0.0", "alpha"), event("bravo@1.0.0", "bravo")]
    raw = out.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r\n" not in raw
    assert b"packageName" not in raw, "upstream's camelCase key does not survive the port"


def test_creates_an_empty_output_file_when_there_is_nothing_to_publish_or_tag(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry, tmp_path: Path
) -> None:
    """Row 5, Adapt (`publish/index.ts:93-96`; `index.test.ts:216-247`).

    ``createOutputReport`` opens the stream before the ``plan.length === 0`` early return, so the
    disposable still flushes an empty file (``utils/output.ts:17-36``). The deciding query adapts
    from ``npm info`` to ``GET pypi.org/pypi/<name>/json``: the local version is already in the
    registry's ``releases`` map, so there is nothing to do.

    Both halves are asserted because "no file" and "empty file" must stay distinguishable -- a
    consumer that reads a missing file as "zero events" cannot tell a skipped run from a crash.
    """
    root = workspace(tmp_project, Pkg("alpha", version="1.0.0"))
    pypi_registry.set_versions("alpha", ["1.0.0"])
    out = tmp_path / "output.ndjson"
    git, uploader = FakeGit(), FakeUploader()

    publish(
        cwd=root,
        output=out,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert uploader.calls == []
    assert git.tags == []
    assert out.is_file(), "the reporter creates the file even with zero events"
    assert read_ndjson(out) == []
    assert out.read_bytes() == b""


# --------------------------------------------------------------------------------------
# publish/index.test.ts row 6 - sequential stop-on-failure
# --------------------------------------------------------------------------------------


def test_stops_publishing_after_a_failed_first_chunk(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 6, Port (`publish/index.ts:98-187`; `index.test.ts:249-295`).

    Upstream: the first ``publish`` exits 1 (E403) and the assertions are "exactly one publish
    call", "``git.tag`` never called", "the command threw" (`:289-294`). molt's E403 becomes a
    generic twine ``UploadFailed`` -- deliberately *not* the ``DuplicateUpload`` subclass, which is
    a skip rather than a failure (see the next test but one).

    The group file flags this as the last line of defence rather than the primary one: on an
    immutable index the primary defence is the pre-flight pass below.
    """
    root = workspace(tmp_project, *CHAIN)
    git = FakeGit()
    uploader = FakeUploader(fail_on={"mike"})

    run_failing(
        lambda: publish(
            cwd=root,
            console=RecordingConsole(),
            git=git,
            builder=FakeBuilder(),
            uploader=uploader,
            oidc=FakeOIDC(),
        )
    )

    assert uploader.order == ["mike"], "chunks 2 and 3 must never be attempted"
    assert git.tags == []


def test_a_failed_chunk_keeps_earlier_chunks_tagged_and_abandons_later_ones(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """Row 6, second half -- ``publish.md``: "molt tags the successes in that chunk and then stops".

    Upstream's fixture only has one chunk, so it cannot distinguish "stops after the failure" from
    "never got started". Failing in the *middle* chunk of three makes all three sub-behaviours
    observable at once: chunk 1 uploaded and was tagged, chunk 2 was attempted and left untagged,
    chunk 3 was never touched (`publish/index.ts:127-187` -- the tag block runs before the throw).
    """
    root = workspace(tmp_project, *CHAIN)
    git = FakeGit()
    uploader = FakeUploader(fail_on={"zulu"})

    run_failing(
        lambda: publish(
            cwd=root,
            console=RecordingConsole(),
            git=git,
            builder=FakeBuilder(),
            uploader=uploader,
            oidc=FakeOIDC(),
        )
    )

    assert uploader.order == ["mike", "zulu"], "alpha's chunk must never be attempted"
    assert git.tags == ["mike@1.0.0"], "the failed release is not tagged; the earlier one is"


# --------------------------------------------------------------------------------------
# publish/e2e.test.ts rows 1, 2, 10 - upload a new version, a first version, skip a known one
# --------------------------------------------------------------------------------------


@pytest.mark.e2e
def test_publishes_a_new_version_of_a_package(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """e2e row 1, Adapt (`e2e.test.ts:860-914`).

    Upstream seeds ``0.0.1``, publishes ``1.0.0`` and asserts a single bearer-authenticated ``PUT``
    returning 201 plus the packument's new ``dist-tags.latest``. molt keeps the first half and drops
    the second outright: PyPI has no dist-tags (research README 4.4). Bearer auth becomes the
    Trusted-Publishing token exchange, asserted through ``FakeOIDC.exchanges``.
    """
    root = workspace(tmp_project, Pkg("alpha", version="1.0.0"))
    pypi_registry.set_versions("alpha", ["0.0.1"])
    git, uploader, oidc = FakeGit(), FakeUploader(), FakeOIDC()

    publish(
        cwd=root,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=oidc,
    )

    assert uploader.uploaded == ["alpha@1.0.0"]
    assert git.tags == ["alpha@1.0.0"]
    assert oidc.exchanges == ["pypi"], "the token is exchanged once, against the default index"


@pytest.mark.e2e
def test_publishes_a_first_version_when_the_index_returns_404(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """e2e row 2, Adapt (`e2e.test.ts:916-961`).

    A package nobody has ever released: ``GET pypi.org/pypi/alpha/json`` is a 404 (the
    ``infoAllow404`` boundary, ``npm-utils.ts``), which means "never published", not "error". The
    upstream row also snapshots npm-flavoured stdout ("Received 404 for npm info..."); that text is
    re-baselined away rather than ported, and this file asserts behaviour instead of log prose --
    see the module docstring of ``tests/publish/test_plan.py`` for the snapshot policy.
    """
    root = workspace(tmp_project, Pkg("alpha", version="1.0.0"))
    git, uploader = FakeGit(), FakeUploader()

    publish(
        cwd=root,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert uploader.uploaded == ["alpha@1.0.0"]
    assert git.tags == ["alpha@1.0.0"]


# (local version, published versions, should_publish, why)
SKIP_CASES: list[tuple[str, list[str], bool, str]] = [
    ("1.0.0", ["1.0.0"], False, "e2e row 10: the exact local version is already on the index"),
    ("1.1.0", ["1.0.0"], True, "getPublishPlan row 6: published, but not this version"),
    ("1.0.0rc1", ["1.0.0rc1"], False, "e2e rows 5/6: prereleases skip on the same rule"),
    ("1.0.0rc2", ["1.0.0rc1"], True, "a newer prerelease is simply absent from the released set"),
    ("1.0.0", ["0.9.0", "1.0.0", "1.1.0"], False, "a *newer* release exists; still a skip"),
]


@pytest.mark.e2e
@pytest.mark.parametrize(("local", "published", "should_publish", "why"), SKIP_CASES)
def test_skip_already_published_compares_the_local_version_to_the_released_set(
    tmp_project: ProjectBuilder,
    pypi_registry: PyPIRegistry,
    local: str,
    published: list[str],
    should_publish: bool,
    why: str,
) -> None:
    """e2e rows 5, 6 and 10, Adapt (`e2e.test.ts:1111-1179`, `:1181-1241`, `:1424-1476`).

    All three upstream rows reduce to one rule once the dist-tag scaffolding is removed
    (`getPublishPlan.ts:152-160`): include the local version iff it is absent from the registry's
    released set. Upstream needs four rows because ``only-pre`` reroutes a first prerelease to the
    ``latest`` dist-tag and each combination of seeded tags behaves differently; PyPI has no
    dist-tags, so the prerelease rows collapse into the general one and only the PEP 440 spelling
    changes (``1.0.0-beta.1`` is not a legal PEP 440 version; ``1.0.0rc1`` is).

    A skip is **not** an error: no upload, no git tag, and the call returns normally (upstream
    asserts ``exitCode`` 0 and ``tagExists`` false, `:1145-1151`, and zero uploads, `:1152-1159`).
    """
    root = workspace(tmp_project, Pkg("alpha", version=local))
    pypi_registry.set_versions("alpha", published)
    git, uploader = FakeGit(), FakeUploader()

    publish(
        cwd=root,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert uploader.uploaded == ([f"alpha@{local}"] if should_publish else []), why
    assert git.tags == ([f"alpha@{local}"] if should_publish else []), why


@pytest.mark.e2e
def test_a_published_package_does_not_suppress_its_unpublished_neighbour(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """The skip decision is per package, not per run.

    Not an upstream row: upstream's e2e fixtures are single-package, so "skip everything because one
    package was already published" would pass every one of rows 5, 6 and 10. In a monorepo that
    failure mode silently drops half a release, which on an immutable index means the half that did
    go out can never be walked back.
    """
    root = workspace(tmp_project, Pkg("alpha"), Pkg("bravo"))
    pypi_registry.set_versions("alpha", ["1.0.0"])
    git, uploader = FakeGit(), FakeUploader()

    publish(
        cwd=root,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert uploader.uploaded == ["bravo@1.0.0"]
    assert git.tags == ["bravo@1.0.0"]


# --------------------------------------------------------------------------------------
# publish/e2e.test.ts row 11 - the stale-read race: 400 File already exists is a SKIP
# --------------------------------------------------------------------------------------


@pytest.mark.e2e
def test_a_duplicate_upload_is_skipped_and_the_run_continues(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """e2e row 11, Adapt (`e2e.test.ts:1478-1569`; ``npm-utils.ts:300-354``).

    The registry read at plan time can be stale, so a version that looked absent can be rejected at
    upload time. npm answers 403 "cannot publish over the previously published version" and
    ``isDuplicatePublishError`` maps it to *skipped*; PyPI answers **400 "File already exists"**
    (:data:`~tests.publish.fake_publish.DUPLICATE_UPLOAD_STATUS`) and molt must map it the same way.

    Three assertions, and the middle one is why this test is not vacuous:

    * the call returns normally -- a duplicate is not a failure (upstream: ``exitCode`` 0);
    * ``bravo`` was still uploaded -- treating the duplicate as fatal would abort the run here, and
      an implementation that catches the base ``UploadFailed`` cannot tell the two apart;
    * ``alpha`` is **not** tagged -- it was skipped, not published (upstream asserts
      ``tagExists("pkg-a@1.0.0")`` is false, `:1544`), so the tag belongs to whoever won the race.
    """
    root = workspace(tmp_project, Pkg("alpha"), Pkg("bravo"))
    git = FakeGit()
    uploader = FakeUploader(already_published={"alpha"})

    publish(
        cwd=root,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert uploader.order == ["alpha", "bravo"]
    assert git.tags == ["bravo@1.0.0"]


def test_a_400_that_is_not_a_duplicate_is_a_failure_not_a_skip(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """e2e row 11, second half -- the half ``isDuplicatePublishError`` spends its body on.

    Upstream's predicate is a **conjunction** (``npm-utils.ts:339-354``): the error code must be
    one of E403 / ERR_PNPM_FAILED_TO_PUBLISH / YN0035 **and** the message must contain "cannot
    publish over the previously published version". Dropping the message half is not a
    simplification, it is a different rule -- and on PyPI the status half is far weaker than on
    npm, because ``POST upload.pypi.org/legacy/`` answers **400** for every rejected upload:
    a classifier PyPI does not recognise, a project name too similar to an existing one, a file
    over the size limit. Every one of those is a release that did **not** go out.

    The fault is planted in the FIRST chunk of three so the two designs are separated by state,
    not just by whether something was raised: mapping this 400 to *skip* would carry on and
    publish ``zulu`` and ``alpha`` and tag them, then exit 0 with one release silently missing
    from the monorepo -- and on an immutable index the two that did go out cannot be walked back.
    """
    root = workspace(tmp_project, *CHAIN)
    git = FakeGit()
    uploader = BadRequestUploader(reject={"mike"})

    run_failing(
        lambda: publish(
            cwd=root,
            console=RecordingConsole(),
            git=git,
            builder=FakeBuilder(),
            uploader=uploader,
            oidc=FakeOIDC(),
        )
    )

    assert uploader.order == ["mike"], (
        "a 400 that is not 'File already exists' is an upload failure: the run stops, it does not "
        "carry on to the dependent chunks"
    )
    assert git.tags == [], "nothing was published, so nothing may be tagged"


# --------------------------------------------------------------------------------------
# publish/e2e.test.ts row 7 - publishing prebuilt artifacts from a pack directory
# --------------------------------------------------------------------------------------


@pytest.mark.e2e
def test_publishes_prebuilt_artifacts_from_a_pack_directory(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """e2e row 7, Adapt (`e2e.test.ts:1243-1294`).

    The pipeline stage that survives whole: build once (``molt build``), upload the artifacts
    (``molt publish --from-pack-dir``), which is what lets CI build in one job and upload from a
    credentialed other one (``cli/publish.md``, "Split build and upload").

    ``builder.calls == []`` is the assertion that makes this row mean something: without it the test
    passes against an implementation that ignores the pack directory and rebuilds everything, which
    would defeat the entire point of the split *and* would not reproduce the artifacts that were
    reviewed. Upstream gets this for free from its ``changesetsPackedManifest`` marker (`:1289`).
    """
    root = workspace(tmp_project, Pkg("alpha"))
    pack_dir = write_plan_file(root / ".packed", publish_plan([packed(root / ".packed", "alpha")]))
    git, uploader, builder = FakeGit(), FakeUploader(), FakeBuilder()

    publish(
        cwd=root,
        from_pack_dir=pack_dir,
        console=RecordingConsole(),
        git=git,
        builder=builder,
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert builder.calls == [], "artifact mode uploads what is on disk; it never rebuilds"
    assert uploader.uploaded == ["alpha@1.0.0"]
    assert uploader.calls[0].artifacts == (
        sdist_name("alpha", "1.0.0"),
        wheel_name("alpha", "1.0.0"),
    )
    assert git.tags == ["alpha@1.0.0"]


# --------------------------------------------------------------------------------------
# publish/e2e.test.ts rows 8 + 9 - authentication failures
# --------------------------------------------------------------------------------------


@pytest.mark.e2e
def test_surfaces_an_upload_rejected_for_bad_credentials(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """e2e row 8, Adapt (`e2e.test.ts:1296-1354`).

    Upstream sends a bad bearer token, the registry answers 401, the CLI exits 1 and the packument
    never gains ``1.0.0`` (`:1349-1353`). molt's analogue is a token the index rejects at upload
    time: an ``AuthFailed`` from twine. It is a *failure*, never a skip -- the ``bravo`` assertion
    is what separates it from the duplicate row above, where the run continues.

    Note the asymmetry with the next test: credentials that exist but are rejected can only be
    discovered by uploading, so this one is caught late; credentials that are absent are caught by
    pre-flight, before anything is uploaded at all.
    """
    root = workspace(tmp_project, Pkg("alpha"), Pkg("bravo"))
    pack_dir = write_plan_file(
        root / ".packed",
        publish_plan([packed(root / ".packed", "alpha")], [packed(root / ".packed", "bravo")]),
    )
    git = FakeGit()
    uploader = FakeUploader(auth_failure={"alpha"})

    run_failing(
        lambda: publish(
            cwd=root,
            from_pack_dir=pack_dir,
            console=RecordingConsole(),
            git=git,
            builder=FakeBuilder(),
            uploader=uploader,
            oidc=FakeOIDC(),
        )
    )

    assert uploader.order == ["alpha"], "a rejected credential aborts; it is not skipped"
    assert git.tags == []


@pytest.mark.e2e
def test_missing_credentials_fail_before_the_first_upload(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """e2e row 9, Adapt (`e2e.test.ts:1356-1422`) -- and half of the pre-flight requirement.

    Upstream's row is mostly a per-package-manager table: with no token configured, pnpm 11 still
    sends the ``PUT`` while the others fail locally, and each spelling gets its own ``ENEEDAUTH``
    snapshot. That branching is pure npm-matrix surface and dies (group 7, "Dies"); molt has exactly
    one behaviour, and research README 4.3 fixes what it is -- with no Trusted-Publishing identity
    and no token, the run fails **before uploading anything**.

    Three chunks, so "fails before the first upload" and "fails somewhere" are different assertions:
    a per-package auth check would have uploaded two packages before noticing.
    """
    root = workspace(tmp_project, *CHAIN)
    pack_dir = write_plan_file(
        root / ".packed",
        publish_plan(*([packed(root / ".packed", name)] for name in TOPOLOGICAL)),
    )
    git, uploader = FakeGit(), FakeUploader()

    run_failing(
        lambda: publish(
            cwd=root,
            from_pack_dir=pack_dir,
            console=RecordingConsole(),
            git=git,
            builder=FakeBuilder(),
            uploader=uploader,
            oidc=FakeOIDC(available=False),
        )
    )

    assert uploader.calls == [], "nothing may be uploaded when authentication is unavailable"
    assert git.tags == []


# --------------------------------------------------------------------------------------
# molt-NEW - exhaustive pre-flight validation before the first upload (research README 4.3)
# --------------------------------------------------------------------------------------

# (damage, why)
PREFLIGHT_CASES: list[tuple[str, str]] = [
    (
        "missing-artifact",
        "an artifact the plan names is not on disk - uploading the first two would strand them",
    ),
    (
        "corrupt-artifact",
        "the artifact's bytes no longer match the integrity the plan recorded",
    ),
    (
        "unknown-package",
        "the plan names a distribution the workspace does not contain (sortReleases:219-223)",
    ),
]


@pytest.mark.parametrize(("damage", "why"), PREFLIGHT_CASES)
def test_the_whole_plan_is_validated_before_the_first_upload(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry, damage: str, why: str
) -> None:
    """molt-NEW: research README 4.3; ``cli/publish.md``, "PyPI is immutable, so molt validates
    first"; group 7, "New in molt" #1.

    changesets validates per package as it goes -- ``publishPackages`` discovers a bad package when
    it reaches it -- which is survivable on npm because ``npm unpublish`` exists for 72 hours. On
    PyPI a version cannot be overwritten or withdrawn, and a partial monorepo publish cannot be
    rolled back, so the whole plan is validated first and the run either starts clean or does not
    start.

    **The fault is planted in the LAST chunk on purpose.** A per-package implementation would upload
    ``mike`` and ``zulu`` and only then discover the problem, so ``uploader.calls == []`` is what
    distinguishes the two designs; asserting merely that an error was raised cannot, and is the
    vacuous version of this test.
    """
    root = workspace(tmp_project, *CHAIN)
    pack_dir = root / ".packed"
    chunks = [[packed(pack_dir, name)] for name in TOPOLOGICAL]
    last = chunks[-1][0]

    if damage == "missing-artifact":
        (pack_dir / last["artifacts"][1]["path"]).unlink()
    elif damage == "corrupt-artifact":
        (pack_dir / last["artifacts"][1]["path"]).write_bytes(b"tampered")
    else:
        last["name"] = "nosuchpackage"
    write_plan_file(pack_dir, publish_plan(*chunks))
    git, uploader = FakeGit(), FakeUploader()

    run_failing(
        lambda: publish(
            cwd=root,
            from_pack_dir=pack_dir,
            console=RecordingConsole(),
            git=git,
            builder=FakeBuilder(),
            uploader=uploader,
            oidc=FakeOIDC(),
        )
    )

    assert uploader.calls == [], why
    assert git.tags == [], why


# (damage, why)
UNREADABLE_PLAN_CASES: list[tuple[str, str]] = [
    (
        "envelope-version",
        "a plan file written by a different build of the tool. `readPlanFile` is the only thing "
        "standing between a foreign document and an irreversible upload "
        "(`getPublishPlan.ts:58-76`)",
    ),
    (
        "sdist-only",
        "an entry that lists one artifact where a Python release needs two. A wheel-less release "
        "reaches the index and the version number is spent",
    ),
]


@pytest.mark.parametrize(("damage", "why"), UNREADABLE_PLAN_CASES)
def test_a_plan_the_reader_rejects_is_never_uploaded_from(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry, damage: str, why: str
) -> None:
    """molt-NEW: ``--from-pack-dir`` goes through ``read_publish_plan``, not around it.

    ``tests/publish/test_plan.py`` pins both halves of the reader -- the envelope version guard
    (`getPublishPlan.ts:58-76`, group 7 pack row 3, a straight Port) and molt's two-artifact rule
    -- and its docstrings name ``molt publish --from-pack-dir`` as one of the two consumers that
    go through it (`publish/index.ts:88-92` reads the plan out of the artifact directory). Nothing
    proved it. ``tests/publish/test_pack.py`` exercises the same guard through ``molt build``,
    which is the route where a rejected plan costs nothing; **this** is the route where it costs a
    permanent version number, and it is the one the two files between them left uncovered.

    Verified by mutation: moving the version check out of ``read_publish_plan`` and into
    ``pack()`` -- or having ``publish`` parse ``publish-plan.json`` itself -- passed every other
    test in this package.

    The damage is planted in the LAST chunk, and the assertion is **zero** uploads, for the same
    reason as the pre-flight row above: an implementation that validated lazily would have
    uploaded ``mike`` and ``zulu`` before noticing.
    """
    root = workspace(tmp_project, *CHAIN)
    pack_dir = root / ".packed"
    chunks = [[packed(pack_dir, name)] for name in TOPOLOGICAL]

    if damage == "sdist-only":
        entry = chunks[-1][0]
        entry["artifacts"] = entry["artifacts"][:1]
    envelope = publish_plan(*chunks)
    if damage == "envelope-version":
        envelope["version"] = PUBLISH_PLAN_VERSION + 1
    write_plan_file(pack_dir, envelope)
    git, uploader = FakeGit(), FakeUploader()

    run_failing(
        lambda: publish(
            cwd=root,
            from_pack_dir=pack_dir,
            console=RecordingConsole(),
            git=git,
            builder=FakeBuilder(),
            uploader=uploader,
            oidc=FakeOIDC(),
        )
    )

    assert uploader.calls == [], why
    assert git.tags == [], why


# --------------------------------------------------------------------------------------
# molt-NEW - the OIDC path has nothing to prompt for
# --------------------------------------------------------------------------------------


class NoInput:
    """A stdin replacement that fails on any read.

    ``ScriptedPrompts`` cannot be used here: it only observes a command that *takes* a prompts seam,
    and the whole point of this row is that ``publish`` has no reason to have one. Poisoning stdin
    catches a prompt raised through any route -- ``input()``, ``click.prompt``, ``rich``'s
    ``Console.input`` -- all of which bottom out in a read.
    """

    def read(self, *args: Any) -> str:
        raise AssertionError("molt publish read stdin: the OIDC flow has nothing to prompt for")

    def readline(self, *args: Any) -> str:
        raise AssertionError("molt publish prompted: the OIDC flow has nothing to prompt for")

    def isatty(self) -> bool:
        return False


def test_publish_never_prompts_for_an_otp_or_a_token(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """molt-NEW: research README 4.4, ``cli/publish.md`` "Authentication: OIDC Trusted Publishing".

    The dropped rows (e2e 12, 13, 14) delete the *machinery* -- env OTP, web-auth OTP, interactive
    retry, TTY-gated concurrency 1<->10, the whole ``AuthState`` re-queue -- and
    ``test_deliberately_not_ported.py`` records each. This row asserts the consequence those records
    cannot: that no prompt survives anywhere in the flow, by any route.

    The signature check is the second half. ``PublishOptions.otp`` is a real upstream field
    (`publish/index.ts:42-50`), threaded down to ``publishPackages`` (`publishPackages.ts:22-51`);
    if it reappears on molt's seam, the AuthState machine has started growing back.
    """
    root = workspace(tmp_project, Pkg("alpha"))
    monkeypatch.setattr(sys, "stdin", NoInput())
    monkeypatch.setattr(
        "builtins.input",
        lambda *args: pytest.fail("molt publish called input(): there is nothing to prompt for"),
    )
    oidc = FakeOIDC()

    publish(
        cwd=root,
        console=RecordingConsole(),
        git=FakeGit(),
        builder=FakeBuilder(),
        uploader=FakeUploader(),
        oidc=oidc,
    )

    assert oidc.exchanges == ["pypi"], "authentication happened - the negative is not vacuous"
    parameters = inspect.signature(publish).parameters
    assert [name for name in parameters if "otp" in name.lower()] == []
    assert [name for name in parameters if "2fa" in name.lower()] == []


# --------------------------------------------------------------------------------------
# molt-NEW - repository routing, git-tag opt-out, PEP 503 tag names
# --------------------------------------------------------------------------------------


def test_repository_routes_both_the_token_exchange_and_the_upload(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """molt-NEW: ``cli/publish.md`` ``--repository``; research README 4.3 + open decision 2.

    Snapshot releases burn a public version number permanently on PyPI, so molt's answer is to send
    them somewhere else -- "default snapshots to a non-PyPI index". That is only possible if a
    single ``--repository`` selects the index for **all three** things an index is used for: the
    credential exchange, the upload, and the *read* that decides what to skip. A token minted for
    pypi.org and an artifact pushed to the snapshot index (or the reverse) is how a snapshot ends
    up on the public index by accident, and it cannot be undone.

    The third assertion is the one added by review. ``tests/publish/test_plan.py::
    test_a_snapshot_release_is_allowed_against_an_explicit_private_index`` pins "an explicitly
    named index is the index; pypi.org is not consulted as well" for the *plan* builder, and
    nothing pinned it here -- verified by mutation: having ``publish`` pass ``repository=None``
    down to the plan builder passed every other test in this package. The consequence is not
    cosmetic: the skip decision would be read from a project of the same name on pypi.org, so a
    release already on the private index gets re-uploaded, or a fresh one gets skipped, depending
    on what a stranger happens to have published under that name.

    How molt reads an alternative index is an open design question (PEP 691 JSON simple API? no
    read at all?) and the frozen ``pypi_registry`` fixture only mocks pypi.org, so a conforming
    implementation may fail here on an unmocked request. That is the correct failure: what this
    row refuses to tolerate is pypi.org being consulted behind the user's back.
    """
    root = workspace(tmp_project, Pkg("alpha"))
    uploader, oidc = FakeUploader(), FakeOIDC()

    publish(
        cwd=root,
        repository="snapshots",
        console=RecordingConsole(),
        git=FakeGit(),
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=oidc,
    )

    assert oidc.exchanges == ["snapshots"]
    assert [call.repository for call in uploader.calls] == ["snapshots"]
    assert pypi_reads(pypi_registry) == [], (
        "an explicitly named index is the index; pypi.org is not consulted as well"
    )


def test_git_tag_false_uploads_without_tagging(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """``cli/publish.md`` ``--git-tag`` / ``--no-git-tag`` (`publish/index.ts:138`, default true).

    Both halves are asserted: an implementation that treats ``--no-git-tag`` as "publish nothing"
    would satisfy the negative alone.
    """
    root = workspace(tmp_project, Pkg("alpha"))
    git, uploader = FakeGit(), FakeUploader()

    publish(
        cwd=root,
        git_tag=False,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert uploader.uploaded == ["alpha@1.0.0"]
    assert git.calls == []


# (declared name, expected tag, why)
PEP503_CASES: list[tuple[str, str, str]] = [
    ("foo-bar", "foo-bar@1.0.0", "already normalized: the identity case"),
    ("Foo_Bar", "foo-bar@1.0.0", "underscore -> hyphen, and case folded"),
    ("Foo.Bar", "foo-bar@1.0.0", "a dot is a separator too"),
    ("FOO--BAR", "foo-bar@1.0.0", "runs of separators collapse to one"),
]


@pytest.mark.parametrize(("declared", "expected", "why"), PEP503_CASES)
def test_publish_tags_use_pep_503_normalized_names(
    tmp_project: ProjectBuilder,
    pypi_registry: PyPIRegistry,
    declared: str,
    expected: str,
    why: str,
) -> None:
    """molt-NEW: research README 4.5, "Flat global namespace + PEP 503 normalization".

    ``publish`` builds its tags in ``tagPublish`` (`publish/index.ts:190-215`), a *different* code
    path from ``molt git-tag`` -- which is why this repeats the normalization rows already pinned in
    ``tests/cli/test_git_tag.py`` rather than trusting them. If the two paths normalize differently,
    a release tagged by ``publish`` is invisible to the idempotency check in ``git-tag`` and every
    subsequent run re-tags it.
    """
    root = workspace(tmp_project, Pkg(declared))
    git, uploader = FakeGit(), FakeUploader()

    publish(
        cwd=root,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert git.tags == [expected], why
    assert len(uploader.calls) == 1, why


def test_a_plan_entry_with_a_denormalized_name_is_still_tagged_normalized(
    tmp_project: ProjectBuilder, pypi_registry: PyPIRegistry
) -> None:
    """The normalization has to survive the plan file, not just the planner.

    The row above cannot see the difference: ``molt publish`` computes its own plan there, so an
    implementation that normalizes once while *building* the plan and never again still emits the
    right tag. ``--from-pack-dir`` hands ``publish`` a plan somebody else wrote -- possibly an older
    molt, possibly a hand-edited file -- and a denormalized ``name`` in it must not become a
    denormalized tag. Verified by mutation: normalizing only at plan time passes the row above and
    fails this one.
    """
    root = workspace(tmp_project, Pkg("Foo_Bar"))
    pack_dir = root / ".packed"
    entry = publish_entry(
        "Foo_Bar", "1.0.0", artifacts=write_artifacts(pack_dir, "Foo_Bar", "1.0.0")
    )
    write_plan_file(pack_dir, publish_plan([entry]))
    git, uploader = FakeGit(), FakeUploader()

    publish(
        cwd=root,
        from_pack_dir=pack_dir,
        console=RecordingConsole(),
        git=git,
        builder=FakeBuilder(),
        uploader=uploader,
        oidc=FakeOIDC(),
    )

    assert git.tags == ["foo-bar@1.0.0"]
    assert len(uploader.calls) == 1
