"""Shared, product-code-free test fixtures for the molt conformance suite.

These fixtures are pure infrastructure: none import a ``molt`` product module, so the suite
collects green while ``src/molt`` is still a stub and each fixture lights up the moment the
matching module lands. They port the reference harness described in
``roadmap/research/changesets-07-test-suite.md`` §4 (vitest -> pytest translation) and §5
(fixtures shopping list, priority order):

- ``tmp_project``  -> §4 ``testdir()`` + §5.3 (tomlkit-round-tripped ``pyproject.toml`` workspace).
- ``git_repo``     -> §4 ``gitdir()`` + §5.2 (real ``git``, Windows-safe), see also doc 08.
- ``frozen_clock`` -> §4 frozen ``Date.now()`` = ``1639354050879`` (one timestamp per plan).
- ``seeded_ids``   -> §4 ``vi.mock("human-id")`` (deterministic changeset ids).
- ``pypi_registry``-> §5.6 PyPI JSON registry mock with a stale-read knob (doc 07 §"stale").
- ``forge_api``    -> §5.7 GitHub GraphQL mock capturing the query + auth header (doc 08).

Downstream test agents rely on the public fixture names verbatim; do not rename them.
Optional third-party deps (``tomlkit``, ``respx``/``httpx``) are imported lazily inside the
fixtures and guarded so a not-yet-synced environment still collects the tree cleanly.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import re
import stat
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self

import pytest

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    import httpx
    import respx

__all__ = [
    "FROZEN_DATETIME",
    "FROZEN_EPOCH_MS",
    "ForgeAPI",
    "FrozenClock",
    "GitRepo",
    "ProjectBuilder",
    "PyPIRegistry",
    "SeededIds",
    "forge_api",
    "frozen_clock",
    "git_repo",
    "pypi_registry",
    "seeded_ids",
    "tmp_project",
]


# ======================================================================================
# Optional-dependency guards (clear errors instead of collection-time ImportError)
# ======================================================================================


def _require_tomlkit() -> ModuleType:
    try:
        import tomlkit
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "tomlkit is required for the tmp_project fixture. "
            "Add tomlkit>=0.13 to [dependency-groups].dev and run `uv sync`."
        ) from exc
    return tomlkit


def _require_httpx_stack() -> tuple[ModuleType, ModuleType]:
    try:
        import httpx
        import respx
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "respx + httpx are required for the pypi_registry and forge_api fixtures. "
            "Add respx>=0.22 to [dependency-groups].dev and run `uv sync`."
        ) from exc
    return respx, httpx


def _normalize_project_name(name: str) -> str:
    """PEP 503 name normalization (``Foo_Bar`` == ``foo-bar``); research README §4.5."""
    return re.sub(r"[-_.]+", "-", name).lower()


# ======================================================================================
# tmp_project -- a tomlkit-round-tripped Python workspace builder
# ======================================================================================


def _iter_releases(
    releases: Mapping[str, str] | Sequence[tuple[str, str]],
) -> list[tuple[str, str]]:
    if isinstance(releases, Mapping):
        return list(releases.items())
    return [(name, bump) for name, bump in releases]


class ProjectBuilder:
    """Scaffolds a uv-style workspace under ``root`` and mutates it via tomlkit.

    Ports the reference ``testdir`` helper (doc 07 §4) plus the §5.3 ``tmp_project`` shopping-list
    item: a comment-preserving root ``pyproject.toml``, member packages under
    ``packages/<name>/pyproject.toml``, ``.changeset/*.md`` files, and config in either
    ``[tool.molt]`` or ``.molt/config.json`` (research README §6, open decision #7: error if both).
    """

    def __init__(
        self,
        root: Path,
        tomlkit_mod: ModuleType,
        *,
        name: str = "workspace-root",
        version: str = "0.0.0",
    ) -> None:
        self.root = root
        self._toml = tomlkit_mod
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_root_pyproject(name, version)

    @property
    def _pyproject_path(self) -> Path:
        return self.root / "pyproject.toml"

    def _write_root_pyproject(self, name: str, version: str) -> None:
        doc = self._toml.document()
        project = self._toml.table()
        project["name"] = name
        project["version"] = version
        project["requires-python"] = ">=3.11"
        doc["project"] = project
        tool = self._toml.table()
        uv = self._toml.table()
        workspace = self._toml.table()
        workspace["members"] = ["packages/*"]
        uv["workspace"] = workspace
        tool["uv"] = uv
        doc["tool"] = tool
        self._pyproject_path.write_text(self._toml.dumps(doc), encoding="utf-8")

    def add_package(
        self,
        name: str,
        version: str = "0.1.0",
        deps: Sequence[str] | None = None,
        dev_deps: Sequence[str] | None = None,
        optional_deps: Mapping[str, Sequence[str]] | None = None,
        private: bool = False,
    ) -> Self:
        """Write ``packages/<name>/pyproject.toml``. ``deps`` are PEP 508 strings (a list).

        ``private=True`` marks the package unpublishable with the ``Private :: Do Not Upload``
        classifier -- the Python analogue of npm's ``"private": true`` (research doc 02 section
        12.1). Private packages are exempt from the skip-tree config rule and from publishing.
        """
        pkg_dir = self.root / "packages" / name
        pkg_dir.mkdir(parents=True, exist_ok=True)
        doc = self._toml.document()
        project = self._toml.table()
        project["name"] = name
        project["version"] = version
        project["dependencies"] = list(deps) if deps else []
        if private:
            project["classifiers"] = ["Private :: Do Not Upload"]
        if optional_deps:
            opt = self._toml.table()
            for extra, reqs in optional_deps.items():
                opt[extra] = list(reqs)
            project["optional-dependencies"] = opt
        doc["project"] = project
        if dev_deps:
            groups = self._toml.table()
            groups["dev"] = list(dev_deps)
            doc["dependency-groups"] = groups
        (pkg_dir / "pyproject.toml").write_text(self._toml.dumps(doc), encoding="utf-8")
        return self

    def write_changeset(
        self,
        id: str,
        releases: Mapping[str, str] | Sequence[tuple[str, str]],
        summary: str = "",
    ) -> Path:
        """Write a ``.changeset/<id>.md`` file with YAML frontmatter + a summary body.

        Written with ``write_bytes``, deliberately: ``Path.write_text`` applies newline
        translation, so on Windows every fixture changeset would land on disk as CRLF and any
        test asserting exact bytes through this helper would be wrong there. molt writes changeset
        files LF-only on every platform (research doc 02 section 12.6), so the fixture does too.
        """
        cs_dir = self.root / ".changeset"
        cs_dir.mkdir(parents=True, exist_ok=True)
        lines = ["---"]
        lines.extend(f'"{pkg}": {bump}' for pkg, bump in _iter_releases(releases))
        lines.append("---")
        lines.append("")
        lines.append(summary)
        if summary and not summary.endswith("\n"):
            lines.append("")
        path = cs_dir / f"{id}.md"
        path.write_bytes("\n".join(lines).encode("utf-8"))
        return path

    def set_config(self, *, as_json: bool = False, **options: Any) -> Self:
        """Write molt config to ``[tool.molt]`` (default) or ``.molt/config.json`` (``as_json``)."""
        if as_json:
            cfg_dir = self.root / ".molt"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            (cfg_dir / "config.json").write_text(
                json.dumps(options, indent=2) + "\n", encoding="utf-8"
            )
            return self
        doc = self._toml.parse(self._pyproject_path.read_text(encoding="utf-8"))
        tool = doc.get("tool")
        if tool is None:
            tool = self._toml.table()
            doc["tool"] = tool
        molt_table = self._toml.table()
        for key, value in options.items():
            molt_table[key] = value
        tool["molt"] = molt_table
        self._pyproject_path.write_text(self._toml.dumps(doc), encoding="utf-8")
        return self


@pytest.fixture
def tmp_project(tmp_path: Path) -> ProjectBuilder:
    """A workspace builder rooted at ``tmp_path``; see :class:`ProjectBuilder`."""
    return ProjectBuilder(tmp_path / "project", _require_tomlkit())


# ======================================================================================
# git_repo -- a real, Windows-safe git repository
# ======================================================================================


def _apply_repo_config(repo: GitRepo) -> None:
    """Windows-safe git config per doc 07 §4 / doc 08: fixed identity, no gpg, no background GC."""
    repo.run("config", "user.name", "Molt Test")
    repo.run("config", "user.email", "test@molt.invalid")
    repo.run("config", "commit.gpgsign", "false")
    repo.run("config", "tag.gpgsign", "false")
    repo.run("config", "tag.forceSignAnnotated", "false")
    # gc.auto=0 + maintenance.auto=false stop background GC racing tmp cleanup on Windows.
    repo.run("config", "gc.auto", "0")
    repo.run("config", "maintenance.auto", "false")


@dataclass
class GitRepo:
    """Thin wrapper over a real ``git`` working tree; every assertion runs against the binary."""

    root: Path

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def commit(self, message: str, *, allow_empty: bool = False) -> str:
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        self.run(*args)
        return self.run("rev-parse", "HEAD").stdout.strip()

    def tag(self, name: str, message: str | None = None) -> None:
        # Annotated tag so `push --follow-tags` works (doc 08, git test #8).
        self.run("tag", "-a", name, "-m", message or name)

    def file_url(self) -> str:
        """``file://`` URL of this repo; required so ``git clone --depth`` actually shallows."""
        return self.root.resolve().as_uri()

    def shallow_clone(self, dest: Path, depth: int = 1) -> GitRepo:
        subprocess.run(
            ["git", "clone", "--depth", str(depth), self.file_url(), str(dest)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        clone = GitRepo(dest)
        _apply_repo_config(clone)
        return clone


def _init_git_repo(root: Path) -> GitRepo:
    root.mkdir(parents=True, exist_ok=True)
    repo = GitRepo(root)
    # Force branch `main` explicitly rather than trusting the user's init.defaultBranch.
    repo.run("init", "-b", "main")
    _apply_repo_config(repo)
    # `* text=auto eol=lf` makes line-ending normalization deterministic cross-platform.
    (root / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
    repo.run("add", ".gitattributes")
    repo.run("commit", "-m", "chore: initial commit")
    return repo


def _make_tree_writable(root: Path) -> None:
    """Best-effort: clear read-only bits so Windows tmp cleanup does not raise on .git objects."""
    if not root.exists():
        return
    for path in root.rglob("*"):
        with contextlib.suppress(OSError):  # best-effort teardown aid
            path.chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.fixture
def git_repo(tmp_path: Path) -> Iterator[GitRepo]:
    """A real git repo on branch ``main`` under ``tmp_path/repo``; see :class:`GitRepo`."""
    root = tmp_path / "repo"
    repo = _init_git_repo(root)
    try:
        yield repo
    finally:
        _make_tree_writable(root)


# ======================================================================================
# frozen_clock -- the single per-plan timestamp (snapshot determinism)
# ======================================================================================

# Upstream freezes Date.now() = 1639354050879 ms. The millisecond constant is authoritative --
# it is exactly what Date.now() returns. Rendered in UTC that epoch is 2021-12-13T00:07:30.879Z
# (verified via datetime.fromtimestamp(1639354050879/1000, UTC)). One timestamp per plan is
# load-bearing for snapshot determinism (research README §3.3).
FROZEN_EPOCH_MS: int = 1639354050879
FROZEN_DATETIME: datetime = datetime(2021, 12, 13, 0, 7, 30, 879000, tzinfo=UTC)


@dataclass
class FrozenClock:
    """A fixed clock value plus a helper to patch molt's (future) clock seam.

    ``target`` is a **provisional** dotted path — the clock seam is not built yet, so
    :meth:`freeze` no-ops safely until ``molt.clock`` exists, then patches ``molt.clock.now``.
    """

    moment: datetime
    epoch_ms: int
    target: str = "molt.clock.now"  # provisional; adjust when the seam lands

    def freeze(self, monkeypatch: pytest.MonkeyPatch) -> datetime:
        module_name, _, attr = self.target.rpartition(".")
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            return self.moment
        monkeypatch.setattr(module, attr, lambda: self.moment, raising=False)
        return self.moment


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """The frozen upstream timestamp; see :class:`FrozenClock` and ``FROZEN_DATETIME``."""
    return FrozenClock(moment=FROZEN_DATETIME, epoch_ms=FROZEN_EPOCH_MS)


# ======================================================================================
# seeded_ids -- deterministic changeset-id generator (mirrors vi.mock("human-id"))
# ======================================================================================

# A fixed pool of human-id-shaped names; `strange-words-combine` matches the reference default
# changeset id (research doc 07 §5 / doc 02 FakeFullState seed).
_DEFAULT_SEED_IDS: tuple[str, ...] = (
    "strange-words-combine",
    "quiet-lions-give",
    "brave-mangos-cheer",
    "gentle-poems-fly",
    "witty-carrots-rush",
    "clever-otters-sing",
)


@dataclass
class SeededIds:
    """Yields a fixed, repeatable id sequence so changeset filenames/snapshots stay stable."""

    seed: tuple[str, ...] = _DEFAULT_SEED_IDS
    _index: int = field(default=0, init=False)

    def __call__(self) -> str:
        return self.next()

    def next(self) -> str:
        value = (
            self.seed[self._index] if self._index < len(self.seed) else f"seeded-id-{self._index}"
        )
        self._index += 1
        return value

    def take(self, n: int) -> list[str]:
        return [self.next() for _ in range(n)]

    def reset(self) -> None:
        self._index = 0


@pytest.fixture
def seeded_ids() -> SeededIds:
    """A deterministic changeset-id generator; see :class:`SeededIds`."""
    return SeededIds()


# ======================================================================================
# pypi_registry -- respx mock of the PyPI JSON API + upload endpoint (with a stale knob)
# ======================================================================================


class PyPIRegistry:
    """Mocks ``GET https://pypi.org/pypi/<name>/json`` and the upload endpoint.

    ``set_versions`` seeds the published versions returned by the read path; the ``stale`` knob
    makes the upload endpoint reject with ``400 File already exists`` so a version that looked
    absent at plan time is rejected at upload time (research doc 07, publish e2e #11).
    """

    # Non-capturing group: a named group would make respx inject `name=` into the side effect.
    _JSON_URL_RE = r"https?://pypi\.org/pypi/[^/]+/json/?"
    _UPLOAD_URL = "https://upload.pypi.org/legacy/"

    def __init__(self, router: respx.MockRouter, httpx_mod: ModuleType) -> None:
        self._router = router
        self._httpx = httpx_mod
        self._versions: dict[str, list[str]] = {}
        self.stale: bool = False
        router.get(url__regex=self._JSON_URL_RE).mock(side_effect=self._read)
        router.post(self._UPLOAD_URL).mock(side_effect=self._upload)

    def set_versions(self, name: str, versions: Sequence[str]) -> None:
        self._versions[_normalize_project_name(name)] = list(versions)

    def _read(self, request: httpx.Request) -> httpx.Response:
        # path is /pypi/<name>/json -> the <name> segment is second from the end.
        segments = [s for s in request.url.path.split("/") if s]
        name = segments[-2] if len(segments) >= 2 else ""
        norm = _normalize_project_name(name)
        if norm not in self._versions:
            return self._httpx.Response(404, json={"message": "Not Found"})
        releases = {version: [] for version in self._versions[norm]}
        return self._httpx.Response(200, json={"info": {"name": norm}, "releases": releases})

    def _upload(self, request: httpx.Request) -> httpx.Response:
        if self.stale:
            return self._httpx.Response(400, text="File already exists.")
        return self._httpx.Response(200, text="OK")


@pytest.fixture
def pypi_registry() -> Iterator[PyPIRegistry]:
    """A respx-backed PyPI JSON registry mock; see :class:`PyPIRegistry`."""
    respx, httpx = _require_httpx_stack()
    with respx.mock(assert_all_called=False) as router:
        yield PyPIRegistry(router, httpx)


# ======================================================================================
# forge_api -- respx mock of the GitHub GraphQL endpoint (captures query + auth header)
# ======================================================================================


class ForgeAPI:
    """Mocks the GitHub GraphQL endpoint and records outgoing requests.

    The URL regex matches ``.../graphql`` on any host so the ``GITHUB_GRAPHQL_URL`` override is
    covered. Tests snapshot :attr:`last_query` and assert :meth:`auth_header` (research doc 08,
    get-github-info tests).
    """

    _GRAPHQL_URL_RE = r".+/graphql/?$"

    def __init__(self, router: respx.MockRouter, httpx_mod: ModuleType) -> None:
        self._router = router
        self._httpx = httpx_mod
        self.requests: list[httpx.Request] = []
        self._response: dict[str, Any] = {"data": {}}
        router.post(url__regex=self._GRAPHQL_URL_RE).mock(side_effect=self._handle)

    def set_response(self, data: dict[str, Any]) -> None:
        self._response = data

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._httpx.Response(200, json=self._response)

    @property
    def last_request(self) -> httpx.Request | None:
        return self.requests[-1] if self.requests else None

    @property
    def last_query(self) -> str | None:
        request = self.last_request
        if request is None:
            return None
        body = json.loads(request.content)
        return body.get("query")

    def auth_header(self) -> str | None:
        request = self.last_request
        return None if request is None else request.headers.get("Authorization")


@pytest.fixture
def forge_api() -> Iterator[ForgeAPI]:
    """A respx-backed GitHub GraphQL mock; see :class:`ForgeAPI`."""
    respx, httpx = _require_httpx_stack()
    with respx.mock(assert_all_called=False) as router:
        yield ForgeAPI(router, httpx)
