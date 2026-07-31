"""The pack stage -- produce the distributions a plan will publish, and enrich the plan with them.

Ports ``packages/cli/src/commands/pack/index.ts`` @ v3.0.0-next.9 against
``roadmap/research/test-suite/07-publish-pack.md``. Website doc: ``website/docs/cli/pack.md``. The
conformance suite is ``tests/publish/test_pack.py``.

**The command is ``molt build``; the library is ``molt.pack``** (design D7). The names differ
deliberately: ``molt.build`` would read as a build backend sitting next to ``python -m build``,
while ``build`` is what the Python packaging ecosystem calls this stage. There is no ``pack`` alias
on the CLI -- ``tests/cli/test_cli.py::test_pack_is_not_a_command`` pins that.

Two artifacts, not one
----------------------
``npm pack`` writes a single ``.tgz`` and upstream's enriched entry carries one
``tarball: {path, integrity}`` object. ``python -m build`` writes an sdist **and** a wheel, so the
entry carries an ``artifacts`` list of exactly two, sdist then wheel, each with its own digest. The
order is fixed because the plan is a document that gets diffed and reviewed; the digest is
per artifact because two files of one release are two different uploads.

Nothing is written until everything is built (design D8)
--------------------------------------------------------
Every artifact is produced first and the enriched plan is serialized once, at the end. Upstream gets
that for free because its ``writeFile`` follows a ``Promise.all``; molt states it, because an
implementation that enriched the plan incrementally would leave a plan on disk claiming artifacts
that do not exist -- and ``publish --from-pack-dir`` would then fail partway through an irreversible
upload sequence, having already put half a monorepo on an index that cannot take it back.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from molt.errors import MoltError
from molt.publish.plan import (
    ARTIFACT_KINDS,
    INTEGRITY_PREFIX,
    PLAN_FILENAME,
    PUBLISH_PLAN_VERSION,
    Plan,
    PlanEntry,
    build_publish_plan,
    read_publish_plan,
    write_publish_plan,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "ARTIFACT_KINDS",
    "INTEGRITY_PREFIX",
    "PLAN_FILENAME",
    "PUBLISH_PLAN_VERSION",
    "UvBuilder",
    "integrity_of",
    "pack",
]

#: The build backend molt shells out to when no ``builder=`` is injected. ``uv build`` produces both
#: artifacts in one invocation and is already this project's toolchain; ``python -m build`` is the
#: portable equivalent and produces the same two files.
_BUILD_COMMAND = ("uv", "build", "--out-dir")

#: How long one package's build may take before molt gives up. Generous: a C-extension build is
#: legitimately slow, and killing one halfway leaves a partial wheel behind.
_BUILD_TIMEOUT_SECONDS = 900.0

_PUBLISH = "publish"


def integrity_of(path: Path) -> str:
    """``sha256=<hex>`` over a file's bytes (design D5).

    Hex, not npm's SRI ``sha256-<base64>``: twine sends a hex ``sha256_digest`` and the PEP 503
    simple index publishes ``#sha256=<hex>``. Matching the ecosystem molt uploads to beats matching
    the one it was ported from.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"{INTEGRITY_PREFIX}{digest.hexdigest()}"


def pack(
    *,
    cwd: Path,
    out_dir: Path,
    from_publish_plan: Path | str | None = None,
    console: Any = None,
    builder: Any = None,
) -> Plan:
    """Build every publish release in the plan and write the enriched plan into ``out_dir``.

    ``from_publish_plan`` reads an existing plan file instead of computing one; the envelope guard
    that rejects an unsupported version lives in :func:`molt.publish.read_publish_plan`, so it
    protects this path and ``molt publish --from-pack-dir`` alike (design D6). Integrity is always
    recomputed over the bytes actually produced, never carried across from the input plan.

    **There is no ``git=`` parameter, although the plan builder this calls takes one.** The only
    thing the plan builder asks git is which private packages are already tagged, and that question
    is about *tagging*, which pack does not do -- so the compute path passes ``git=None``, which
    means "no tag lookup" and lists every private release as tag-only. A pack run that needed the
    real answer would be deciding something it does not own.

    ``builder`` is the injectable build seam: any object with
    ``build(source_dir, out_dir, *, package, version) -> list[Path]``. It defaults to
    :class:`UvBuilder`, which shells out. Nothing here uploads.

    Returns the enriched plan. Raises without writing a plan file if any build fails (design D8).
    """
    from pathlib import Path as _Path

    root = _Path(cwd)
    destination = _Path(out_dir)
    if from_publish_plan is not None:
        plan = read_publish_plan(from_publish_plan)
    else:
        plan = build_publish_plan(cwd=root, console=console, git=None)

    if builder is None:
        builder = UvBuilder()

    # Design D8: buffer every enrichment, flush the document once, at the end.
    enriched: Plan = []
    for chunk in plan:
        enriched.append(
            [_enrich(entry, root=root, out_dir=destination, builder=builder) for entry in chunk]
        )

    written = write_publish_plan(destination / PLAN_FILENAME, enriched)
    if console is not None:
        console.success(f"Built {_release_count(enriched)} release(s); wrote {written.name}.")
    return enriched


def _enrich(entry: PlanEntry, *, root: Path, out_dir: Path, builder: Any) -> PlanEntry:
    """Build one entry's artifacts and record them, or pass a tag-only entry through untouched.

    "Untouched" is literal (``pack/index.ts:188-203``, ``if (release.kind !== "publish") return``).
    Rewriting a tag-only entry -- reordering its keys, adding an empty ``artifacts`` list -- makes
    ``publish --from-pack-dir`` see a different document from the one ``publish-plan`` wrote, and
    the three stages compose only because the envelope survives each of them unchanged.
    """
    if entry.get("kind") != _PUBLISH:
        return entry
    name = str(entry["name"])
    version = str(entry["version"])
    source = root / str(entry["directory"])
    produced = builder.build(source, out_dir, package=name, version=version)
    return {**entry, "artifacts": _artifacts(produced, name=name, out_dir=out_dir)}


def _artifacts(produced: Sequence[Path], *, name: str, out_dir: Path) -> list[dict[str, str]]:
    """The ``artifacts`` list for one release: sdist first, wheel second, each digested.

    Paths are recorded **relative to the output directory and POSIX-spelled**, so a plan written on
    Windows is readable by the Linux job that uploads it. The layout inside the directory is the
    builder's business -- the plan records whatever it produced -- but the spelling is not.
    """
    from pathlib import Path as _Path

    by_kind: dict[str, Path] = {}
    for item in produced:
        path = _Path(item)
        kind = _kind_of(path.name)
        if kind in ARTIFACT_KINDS and kind not in by_kind:
            by_kind[kind] = path
    missing = [kind for kind in ARTIFACT_KINDS if kind not in by_kind]
    if missing:
        raise MoltError(
            f"The build of {name} produced no {' and no '.join(missing)}. A Python release is both "
            f"({' then '.join(ARTIFACT_KINDS)}): an sdist-only release forces every consumer to "
            "build from source, and a wheel-only release is uninstallable wherever no wheel tag "
            "matches."
        )
    return [
        {
            "path": _relative_to(by_kind[kind], out_dir),
            "integrity": integrity_of(by_kind[kind]),
        }
        for kind in ARTIFACT_KINDS
    ]


def _relative_to(path: Path, out_dir: Path) -> str:
    """``path`` as a POSIX string relative to ``out_dir``, falling back to its bare filename."""
    try:
        return path.resolve().relative_to(out_dir.resolve()).as_posix()
    except ValueError:  # pragma: no cover - a builder writing outside the directory it was given
        return path.name


def _kind_of(filename: str) -> str:
    """Which member of :data:`ARTIFACT_KINDS` a produced filename is."""
    lowered = filename.lower()
    if lowered.endswith((".tar.gz", ".zip")):
        return "sdist"
    if lowered.endswith(".whl"):
        return "wheel"
    return "unknown"


def _release_count(plan: Plan) -> int:
    """How many entries of ``plan`` are publish releases -- what the console reports as built."""
    return sum(1 for chunk in plan for entry in chunk if entry.get("kind") == _PUBLISH)


class UvBuilder:
    """The default build seam: ``uv build --out-dir <out> <source>``.

    A subprocess rather than an in-process call to a build backend, deliberately: a package's build
    runs its own ``[build-system].requires`` in its own environment, and importing a backend into
    molt's interpreter would make one project's build dependencies molt's problem.

    Never exercised by the conformance suite, which injects its own builder -- group 7's brief is
    explicit that those tests must not actually build anything.
    """

    def build(self, source_dir: Path, out_dir: Path, *, package: str, version: str) -> list[Path]:
        """Build ``source_dir`` into ``out_dir`` and return the files that appeared."""
        import subprocess

        out_dir.mkdir(parents=True, exist_ok=True)
        before = {path for path in out_dir.iterdir() if path.is_file()}
        command = [*_BUILD_COMMAND, str(out_dir), str(source_dir)]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=_BUILD_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MoltError(f"Could not build {package} {version}: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise MoltError(f"Building {package} {version} failed:\n{detail}")
        after = {path for path in out_dir.iterdir() if path.is_file()}
        return sorted(after - before, key=lambda path: (_kind_of(path.name) != "sdist", path.name))
