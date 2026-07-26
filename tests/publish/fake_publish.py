"""Frozen shared harness for the publish/pack conformance suite (test-suite group 7).

Written by the orchestrator and **frozen before the phase writers were dispatched**, for the same
reason ``tests/cli/fake_cli.py`` was: two writers needing the same build/upload doubles will
otherwise invent two incompatible ones, and every test resting on the loser has to be rewritten
(progress.md Session 4, finding R2 B1; Session 5, practice 1).

Nothing here imports a ``molt`` product module, so the package collects green while
``molt.publish`` and ``molt.pack`` do not exist. The doubles are **structural** stand-ins --
duck-typed, never subclassing a (not yet existing) protocol -- so they need no guard.

What this module owns (do not redefine these in a test file):

* :data:`PUBLISH_PLAN_VERSION` and the plan-envelope/entry shape (:func:`publish_entry`,
  :func:`tag_only_entry`, :func:`publish_plan`).
* :class:`FakeBuilder` -- stands in for ``python -m build`` (research doc 07 group file,
  "Fixtures & tooling to build" #2).
* :class:`FakeUploader` -- stands in for the twine upload (#3), including the
  ``400 File already exists`` and auth-failure responses.
* :class:`FakeOIDC` -- stands in for the PyPI Trusted-Publishing token exchange (#4).
* :func:`require_publish` / :func:`require_pack` -- the two-stage import guards.

NDJSON reading is **not** redefined here: ``tests.cli.fake_cli.read_ndjson`` is already the frozen
reader for the ``--output`` event stream and ``tests/cli/test_git_tag.py`` asserts against it.
Import it from there so a publish ``git-tag`` event and a ``molt git-tag`` event cannot drift.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

__all__ = [
    "ARTIFACT_KINDS",
    "DUPLICATE_UPLOAD_STATUS",
    "INTEGRITY_PREFIX",
    "PUBLISH_PLAN_VERSION",
    "AuthFailed",
    "BuildCall",
    "BuildFailed",
    "DuplicateUpload",
    "FakeBuilder",
    "FakeOIDC",
    "FakeUploader",
    "UploadCall",
    "UploadFailed",
    "integrity_of",
    "publish_entry",
    "publish_plan",
    "require_pack",
    "require_publish",
    "sdist_name",
    "tag_only_entry",
    "wheel_name",
]


# --------------------------------------------------------------------------------------
# Plan envelope -- the versioned publish-plan.json contract
# --------------------------------------------------------------------------------------

#: ``CURRENT_PUBLISH_PLAN_VERSION``. Group 7 pack row 3 (``getPublishPlan.ts:58-76``) is a straight
#: Port: a plan file whose version is not this integer must be rejected, so the guard is only
#: meaningful while exactly one version is current. Bump it here, in one place, if the shape ever
#: changes.
PUBLISH_PLAN_VERSION = 1

#: The two artifacts ``python -m build`` produces per release. Upstream ``npm pack`` produces a
#: single ``.tgz``, so the plan's ``tarball`` field becomes a **list** in molt (group 7,
#: "New in molt" #4). Order is (sdist, wheel) and is asserted -- a plan that lists only one
#: artifact, or lists them in an unstable order, is the defect these tests exist to catch.
ARTIFACT_KINDS = ("sdist", "wheel")

#: Integrity prefix for a built artifact.
#:
#: **Documented divergence.** Upstream computes an npm SRI string (``sha256-<base64>``,
#: ``pack/index.ts:74-228``) and the group file's expectation reads ``/^sha256-/``. molt targets
#: PyPI, where the digest is spelled as lowercase **hex** -- twine sends ``sha256_digest`` as hex
#: and the PEP 503 simple index publishes ``#sha256=<hex>``. Matching the ecosystem molt actually
#: uploads to beats matching the ecosystem it was ported from. If the owner rules SRI instead,
#: this constant and :func:`integrity_of` are the only two places to change.
INTEGRITY_PREFIX = "sha256="

#: The status PyPI returns when a file with that name already exists. Mapping this to *skip*
#: rather than *fail* is the race-safety requirement (group 7 publish/e2e row 11; upstream
#: ``npm-utils.ts:300-354`` ``isDuplicatePublishError``). npm's analogue is 403.
DUPLICATE_UPLOAD_STATUS = 400


def _filename_stem(name: str) -> str:
    """PEP 503-normalize ``name``, then spell it for a distribution filename.

    PEP 503 normalization is ``re.sub(r"[-_.]+", "-", name).lower()``; PEP 625 then requires the
    sdist filename to carry that normalized name with ``-`` replaced by ``_``. The lowercasing is
    the part that is easy to miss: a bare ``name.replace("-", "_")`` leaves ``Foo_Bar`` as
    ``Foo_Bar`` where ``python -m build`` actually writes ``foo_bar``. Reported by writer P6a
    against the first revision of this harness.
    """
    return re.sub(r"[-_.]+", "-", name).lower().replace("-", "_")


def sdist_name(name: str, version: str) -> str:
    """The sdist filename ``python -m build`` writes (PEP 625: normalized name, underscores)."""
    return f"{_filename_stem(name)}-{version}.tar.gz"


def wheel_name(name: str, version: str) -> str:
    """The wheel filename ``python -m build`` writes (PEP 427, pure-python tag)."""
    return f"{_filename_stem(name)}-{version}-py3-none-any.whl"


def integrity_of(path: Path) -> str:
    """``sha256=<hex>`` over the file's bytes -- see :data:`INTEGRITY_PREFIX`."""
    return f"{INTEGRITY_PREFIX}{hashlib.sha256(path.read_bytes()).hexdigest()}"


def publish_entry(
    name: str,
    version: str,
    *,
    directory: str | None = None,
    artifacts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """One ``publish`` entry of the plan.

    Keys are **snake_case** (progress.md Session 5, decision 6, which four negative assertions in
    the CLI suite already depend on). ``access`` and ``tag`` are deliberately absent: both are npm
    concepts (per-package access, dist-tags) and PyPI has neither (group 7,
    ``getPublishPlan`` rows 1/5/6).

    ``artifacts`` is absent until ``molt build`` enriches the plan, and is then a **list** of
    ``{"path", "integrity"}`` -- one per entry of :data:`ARTIFACT_KINDS`.
    """
    entry: dict[str, Any] = {
        "kind": "publish",
        "name": name,
        "version": version,
        "directory": directory if directory is not None else f"packages/{name}",
    }
    if artifacts is not None:
        entry["artifacts"] = artifacts
    return entry


def tag_only_entry(name: str, version: str, *, directory: str | None = None) -> dict[str, Any]:
    """One ``tag-only`` entry -- a release that is git-tagged but never uploaded.

    Upstream's trigger is ``"private": true``; molt's is a package that is not published to an
    index (group 7 publish/index rows 1/7). ``molt build`` passes these through untouched, which
    is why they carry no ``artifacts`` key at any stage.
    """
    return {
        "kind": "tag-only",
        "name": name,
        "version": version,
        "directory": directory if directory is not None else f"packages/{name}",
    }


def publish_plan(*chunks: list[dict[str, Any]], version: int = PUBLISH_PLAN_VERSION) -> dict:
    """The versioned envelope: ``{"version": N, "plan": [[entry, ...], ...]}``.

    The plan is a list of **chunks**, not a flat list, and the chunk boundaries are the
    topological order (group 7 ``getPublishPlan`` rows 3/4). Flattening it here would erase the
    one property the moat tests exist to assert, so the shape is preserved end to end.

    ``version=`` is a parameter purely so the version-guard row can build a rejectable plan.
    """
    return {"version": version, "plan": [list(chunk) for chunk in chunks]}


# --------------------------------------------------------------------------------------
# Build double -- stands in for `python -m build`
# --------------------------------------------------------------------------------------


class BuildFailed(RuntimeError):
    """A non-zero ``python -m build``. Carries the stderr the command must surface."""

    def __init__(self, package: str, stderr: str = "build failed") -> None:
        super().__init__(f"{package}: {stderr}")
        self.package = package
        self.stderr = stderr


@dataclass(frozen=True)
class BuildCall:
    """One recorded build invocation."""

    source_dir: Path
    out_dir: Path
    package: str


class FakeBuilder:
    """Records build calls and writes deterministic fake sdist+wheel files.

    Injected as ``builder=`` (mirroring the ``console=``/``prompts=``/``git=`` convention the CLI
    suite froze). It never spawns a subprocess -- group 7's brief is explicit that these tests must
    not actually build.

    ``fail_on`` makes a package's build raise :class:`BuildFailed`, which drives the
    "surface the error and write **nothing**" rows (pack/index, pack/e2e row 2). The failure is
    raised *before* any artifact is written for that package, so a test asserting no partial
    output is asserting something real.
    """

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.calls: list[BuildCall] = []
        self.fail_on: set[str] = set(fail_on or ())

    def build(self, source_dir: Path, out_dir: Path, *, package: str, version: str) -> list[Path]:
        """Write ``<name>-<version>.tar.gz`` and the matching wheel into ``out_dir``."""
        self.calls.append(BuildCall(Path(source_dir), Path(out_dir), package))
        if package in self.fail_on:
            raise BuildFailed(package)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        produced: list[Path] = []
        for filename in (sdist_name(package, version), wheel_name(package, version)):
            path = out / filename
            # Distinct bytes per artifact so two artifacts of one release cannot share a digest,
            # which would make an "integrity is per-artifact" assertion vacuous.
            path.write_bytes(f"fake artifact for {filename}".encode())
            produced.append(path)
        return produced

    @property
    def built(self) -> list[str]:
        """Package names in build order."""
        return [call.package for call in self.calls]


# --------------------------------------------------------------------------------------
# Upload double -- stands in for twine
# --------------------------------------------------------------------------------------


class UploadFailed(RuntimeError):
    """A generic upload failure (network, 5xx, malformed artifact)."""


class AuthFailed(UploadFailed):
    """Credentials missing or rejected. Must surface and exit non-zero, never be swallowed."""


class DuplicateUpload(UploadFailed):
    """PyPI ``400 File already exists``.

    Distinct from :class:`UploadFailed` on purpose: this one means the version is *already there*,
    so the correct handling is **skip and continue**, not abort (group 7 publish/e2e row 11). A
    test that catches the base class would treat the two identically and prove nothing.
    """

    status_code = DUPLICATE_UPLOAD_STATUS


@dataclass
class UploadCall:
    """One recorded upload."""

    package: str
    version: str
    artifacts: tuple[str, ...]
    repository: str | None = None


class FakeUploader:
    """Records ordered uploads and scripts per-package outcomes.

    Injected as ``uploader=``. :attr:`order` is the assertion surface for the moat row
    ("publishes release chunks sequentially", group 7 publish/index row 3): it is the package
    names in the order the uploader was actually called, so a plan that is chunked correctly but
    *uploaded* in the wrong order still fails.

    Scripting knobs, all keyed by package name:

    * ``already_published`` -- raise :class:`DuplicateUpload` (the stale-read race).
    * ``auth_failure`` -- raise :class:`AuthFailed`.
    * ``fail_on`` -- raise :class:`UploadFailed` (drives stop-on-failure).
    """

    def __init__(
        self,
        *,
        already_published: set[str] | None = None,
        auth_failure: set[str] | None = None,
        fail_on: set[str] | None = None,
    ) -> None:
        self.calls: list[UploadCall] = []
        self.already_published: set[str] = set(already_published or ())
        self.auth_failure: set[str] = set(auth_failure or ())
        self.fail_on: set[str] = set(fail_on or ())

    def upload(
        self,
        artifacts: list[Path] | tuple[Path, ...],
        *,
        package: str,
        version: str,
        repository: str | None = None,
    ) -> None:
        self.calls.append(
            UploadCall(
                package=package,
                version=version,
                artifacts=tuple(Path(a).name for a in artifacts),
                repository=repository,
            )
        )
        if package in self.auth_failure:
            raise AuthFailed(f"{package}: credentials rejected")
        if package in self.already_published:
            raise DuplicateUpload(f"{package}-{version}: File already exists")
        if package in self.fail_on:
            raise UploadFailed(f"{package}: upload failed")

    @property
    def order(self) -> list[str]:
        """Package names in upload order -- the topological-order assertion surface."""
        return [call.package for call in self.calls]

    @property
    def uploaded(self) -> list[str]:
        """``name@version`` for every upload that was *attempted*."""
        return [f"{call.package}@{call.version}" for call in self.calls]


# --------------------------------------------------------------------------------------
# OIDC double -- stands in for PyPI Trusted Publishing
# --------------------------------------------------------------------------------------


@dataclass
class FakeOIDC:
    """The Trusted-Publishing token exchange (tech-stack section 11).

    Replaces the entire npm OTP/2FA/AuthState machine, which molt drops (group 7 publish/e2e rows
    12-14). There is nothing to prompt for mid-run, so this double has no interactive surface at
    all -- that absence is itself part of the contract.

    ``available=False`` models "no OIDC and no token configured", which must fail **before** the
    first upload, not partway through one.
    """

    token: str = "pypi-fake-oidc-token"
    available: bool = True
    exchanges: list[str] = field(default_factory=list)

    def fetch_token(self, *, repository: str | None = None) -> str:
        self.exchanges.append(repository or "pypi")
        if not self.available:
            raise AuthFailed("no Trusted Publishing identity and no API token configured")
        return self.token


# --------------------------------------------------------------------------------------
# Import guards
# --------------------------------------------------------------------------------------


def require_publish() -> Any:
    """Import ``molt.publish`` or skip the whole module.

    Two-stage on purpose (progress.md Session 5, practice 2): ``importorskip`` only skips while the
    module is *absent*, so the moment a placeholder ``publish.py`` lands the guard stops guarding
    and every test in the file goes red. The attribute check keeps the suite green until the
    module is real. ``src/molt/`` has no publish stub today -- this is insurance, and it costs one
    call.
    """
    module = pytest.importorskip(
        "molt.publish", reason="build step 8 - molt.publish is a TDD target"
    )
    if getattr(module, "publish", None) is None:
        pytest.skip(
            "molt.publish exists but exposes no publish() yet (build step 8)",
            allow_module_level=True,
        )
    return module


def require_pack() -> Any:
    """Import ``molt.pack`` or skip the whole module.

    **Naming note, deliberate and cross-checked.** The CLI *command* is ``molt build`` with no
    ``pack`` alias (progress.md Session 5, decision 5), and ``tests/cli/test_cli.py`` pins the
    shell route as ``molt.commands.build``. The *library* module stays ``molt.pack`` per
    test-contract section 5, because ``molt.build`` would read as a build backend next to
    ``python -m build``. So: command ``build`` -> ``molt.commands.build`` -> ``molt.pack.pack()``.
    Flagged for the owner; one constant here and one docstring change if it is overruled.
    """
    module = pytest.importorskip("molt.pack", reason="build step 8 - molt.pack is a TDD target")
    if getattr(module, "pack", None) is None:
        pytest.skip(
            "molt.pack exists but exposes no pack() yet (build step 8)",
            allow_module_level=True,
        )
    return module
