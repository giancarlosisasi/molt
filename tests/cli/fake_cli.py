"""Shared CLI test doubles and filesystem readers for the ``tests/cli`` conformance suite.

Pure test infrastructure: nothing here imports a ``molt`` product module at import time, so the
package collects green while ``molt.cli`` is still a stub and ``molt.commands`` does not exist.
Product imports (the Typer app, ``molt.cli``) happen lazily inside the two helper functions that
need them, both of which are only called from already-guarded test modules.

This module is the **single pinned seam** for the four parallel P5 writer agents. It ports the
reference harness described in ``roadmap/research/test-suite/05-cli-version.md`` ("Fixtures &
tooling to build") and ``06-cli-commands.md`` ("Fixtures & tooling to build"):

- :class:`RecordingConsole` -> ``vi.mock("@clack/prompts")`` ``mockedLogger`` plus
  ``silenceLogsInBlock``. Structural stand-in for the ``Console`` protocol of the accepted
  ``terminal-ui`` spec (``openspec/changes/adopt-typer-cli-shell/specs/terminal-ui/spec.md``).
- :class:`ScriptedPrompts` -> ``vi.mock("../../../utils/cli-utilities")`` +
  ``vi.mock(".../askWithEditor")``. Structural stand-in for the ``Prompts`` protocol of the same
  spec; method names are the spec's (``multiselect``/``select``/``confirm``/``text``/``editor``),
  not upstream's ``askMultiselect``/``askList``/``askQuestion``/``askConfirm``.
- :class:`FakeGit` -> ``vi.mock("@changesets/git")``: recording ``add``/``commit``/``tag`` plus the
  frozen ``getCurrentCommitId``/``getCommitsThatAddFiles`` values the snapshots depend on.
- :func:`read_manifests` / :func:`read_changelog` -> the reference's
  ``getPackages().packages.map(x => x.packageJson)`` assertions, over ``pyproject.toml``.
- :func:`scrub_ids` -> ``replaceHumanIds`` (status.test.ts), for deterministic plan snapshots.

Fixtures wrapping these live in ``tests/cli/conftest.py``; import the classes and functions by
their full dotted path (``--import-mode=importlib`` does not put test dirs on ``sys.path``)::

    from tests.cli.fake_cli import CHANGELOG_HASH, RecordingConsole, read_manifests
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "CANCEL",
    "CHANGELOG_HASH",
    "FROZEN_COMMIT",
    "FROZEN_COMMIT_SHORT",
    "ConsoleCall",
    "FakeGit",
    "GitCall",
    "Manifest",
    "PromptCall",
    "PromptExhausted",
    "RecordingConsole",
    "ScriptedPrompts",
    "changeset_ids",
    "invoke_cli",
    "read_changelog",
    "read_manifest",
    "read_manifests",
    "read_ndjson",
    "require_cli_app",
    "scrub_ids",
    "strip_ansi",
]

# ======================================================================================
# Frozen values the reference suite bakes into its snapshots
# ======================================================================================

#: ``getCommitsThatAddFiles`` mock return (version.test.ts). The reference's modified default
#: changelog generator prefixes every summary with this short hash, so ported changelog
#: expectations read ``- g1th4sh: This is a summary``.
CHANGELOG_HASH: Final = "g1th4sh"

#: ``getCurrentCommitId`` mock return (version.test.ts snapshotPrereleaseTemplate group).
FROZEN_COMMIT: Final = "abcdefghijklmnopqrstuvwxyz"

#: The ``{commit-short}`` token: the first 7 characters of :data:`FROZEN_COMMIT`.
FROZEN_COMMIT_SHORT: Final = FROZEN_COMMIT[:7]

#: Two escape families, both of which ``rich`` emits:
#:
#: * **CSI** -- ``ESC [`` ... final byte. SGR colors, cursor moves, erase-line, private modes.
#: * **OSC** -- ``ESC ]`` ... terminated by BEL or ST (``ESC \``). ``rich`` wraps every URL it
#:   prints in an OSC-8 hyperlink (``ESC ]8;;<url>ESC \<label>ESC ]8;;ESC \``) whenever the
#:   terminal advertises support. Stripping only CSI would leave the raw URL *and* its escape
#:   frame in the text, which is exactly what
#:   ``test_cli.py::test_internal_error_reports_a_cwd_redacted_issue_url`` asserts on.
_ANSI_RE: Final = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC, BEL- or ST-terminated
)


def strip_ansi(text: str) -> str:
    """Remove VT escape sequences (CSI and OSC, including OSC-8 hyperlinks).

    The reference strips color codes before asserting on ``clack.log.error`` text
    (``06-cli-commands.md``, shared-harness notes); molt's console adapter may emit ``rich``
    markup through a real terminal in some environments, so every text assertion goes through
    this first.

    This is a **safety net, not the primary defence**. ``tests/cli/test_cli.py``'s autouse
    ``_stable_cli_environment`` fixture sets ``NO_COLOR=1`` (so ``rich`` emits no SGR at all) and
    ``COLUMNS=500`` (so ``rich`` does not hard-wrap a long token such as the issue-report URL
    mid-string). Both are load-bearing: this function removes escapes, but nothing here can
    re-join a URL that ``rich`` already split across two lines with a newline. Do not drop either
    environment variable on the assumption that ``strip_ansi`` covers it.
    """
    return _ANSI_RE.sub("", text)


class _Cancel:
    """Sentinel type for :data:`CANCEL`."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "CANCEL"

    def __bool__(self) -> bool:
        # Guards against a command treating a cancellation as a falsy "no answer": the
        # `cancelable` contract (terminal-ui spec) requires an explicit sentinel check.
        return False


#: Scripted-answer sentinel meaning "the user hit Ctrl-C at this prompt". The accepted
#: ``terminal-ui`` spec routes every cancellation through one ``cancelable`` helper that prints
#: "Canceled" and exits **0** (research doc 03 section 11.6 -- a deliberate, documented
#: divergence from POSIX 130).
CANCEL: Final = _Cancel()


# ======================================================================================
# Console double -- the `Console` protocol of the terminal-ui spec
# ======================================================================================


@dataclass(frozen=True)
class ConsoleCall:
    """One recorded console emission."""

    level: str
    """``info`` | ``success`` | ``warn`` | ``error`` | ``note``."""

    message: str
    title: str | None = None


class RecordingConsole:
    """Records every console emission instead of writing to a terminal.

    Structurally implements the ``Console`` protocol required by the ``terminal-ui`` spec:
    ``info``, ``success``, ``warn``, ``error`` (stderr in the real implementation), ``note``,
    ``spinner`` and ``progress``. Text is stored VT-stripped so assertions never depend on
    whether ``rich`` decided to colorize.
    """

    def __init__(self) -> None:
        self.calls: list[ConsoleCall] = []

    # -- protocol surface ---------------------------------------------------------------
    def info(self, message: str) -> None:
        self._record("info", message)

    def success(self, message: str) -> None:
        self._record("success", message)

    def warn(self, message: str) -> None:
        self._record("warn", message)

    def error(self, message: str) -> None:
        self._record("error", message)

    def note(self, title: str, body: str = "") -> None:
        self.calls.append(ConsoleCall("note", strip_ansi(body), strip_ansi(title)))

    @contextlib.contextmanager
    def spinner(self, message: str = "") -> Iterator[None]:
        if message:
            self._record("info", message)
        yield

    @contextlib.contextmanager
    def progress(self, message: str = "", total: int | None = None) -> Iterator[None]:
        del total
        if message:
            self._record("info", message)
        yield

    # -- assertion helpers --------------------------------------------------------------
    def _record(self, level: str, message: str) -> None:
        self.calls.append(ConsoleCall(level, strip_ansi(message)))

    def at(self, level: str) -> list[str]:
        """Every message emitted at ``level``, in order."""
        return [call.message for call in self.calls if call.level == level]

    @property
    def errors(self) -> list[str]:
        return self.at("error")

    @property
    def warnings(self) -> list[str]:
        return self.at("warn")

    @property
    def messages(self) -> list[str]:
        """Every message at every level, in emission order."""
        return [call.message for call in self.calls]

    def text(self) -> str:
        """All output as one newline-joined block -- for substring assertions."""
        return "\n".join(self.messages)

    def contains(self, needle: str, *, level: str | None = None) -> bool:
        """True when ``needle`` appears in any recorded message (optionally at one level)."""
        pool = self.at(level) if level is not None else self.messages
        return any(needle in message for message in pool)

    def reset(self) -> None:
        self.calls.clear()


# ======================================================================================
# Prompt double -- the `Prompts` protocol of the terminal-ui spec
# ======================================================================================


class PromptExhausted(AssertionError):
    """A prompt fired that the test did not script an answer for.

    Deliberately an ``AssertionError``: an unscripted prompt means the command asked a question
    the test did not expect, which is a test failure rather than a product error. Upstream gets
    this for free -- an unmocked ``clack`` prompt hangs -- and a hang is exactly what
    ``--non-interactive`` exists to prevent.
    """


@dataclass(frozen=True)
class PromptCall:
    """One recorded prompt invocation, including the arguments it was called with.

    The reference asserts on prompt *arguments*, not just on the resulting changeset -- e.g.
    ``add`` row 10 asserts ``askMultiselect`` received ``{"changed packages": [...], "unchanged
    packages": [...]}``. Keep :attr:`choices` and :attr:`kwargs` populated for those rows.
    """

    kind: str
    """``multiselect`` | ``select`` | ``confirm`` | ``text`` | ``editor``."""

    message: str
    choices: tuple[Any, ...] | None = None
    kwargs: Mapping[str, Any] = field(default_factory=dict)


class ScriptedPrompts:
    """Answers prompts from a pre-loaded script and records how it was called.

    Each keyword takes a sequence of answers consumed in order by the matching method. An answer
    of :data:`CANCEL` models a Ctrl-C at that prompt. Running past the end of a script raises
    :class:`PromptExhausted` naming the prompt, so an over-prompting command fails loudly instead
    of blocking.

    Method names follow the accepted ``terminal-ui`` spec, not upstream's ``ask*`` spelling.
    """

    def __init__(
        self,
        *,
        multiselect: Sequence[Any] = (),
        select: Sequence[Any] = (),
        confirm: Sequence[Any] = (),
        text: Sequence[Any] = (),
        editor: Sequence[Any] = (),
    ) -> None:
        self._script: dict[str, list[Any]] = {
            "multiselect": list(multiselect),
            "select": list(select),
            "confirm": list(confirm),
            "text": list(text),
            "editor": list(editor),
        }
        self.calls: list[PromptCall] = []

    # -- protocol surface ---------------------------------------------------------------
    def multiselect(self, message: str, choices: Sequence[Any] = (), **kwargs: Any) -> Any:
        return self._answer("multiselect", message, choices, kwargs)

    def select(self, message: str, choices: Sequence[Any] = (), **kwargs: Any) -> Any:
        return self._answer("select", message, choices, kwargs)

    def confirm(self, message: str, **kwargs: Any) -> Any:
        return self._answer("confirm", message, None, kwargs)

    def text(self, message: str, **kwargs: Any) -> Any:
        return self._answer("text", message, None, kwargs)

    def editor(self, message: str = "", **kwargs: Any) -> Any:
        return self._answer("editor", message, None, kwargs)

    # -- assertion helpers --------------------------------------------------------------
    def _answer(
        self,
        kind: str,
        message: str,
        choices: Sequence[Any] | None,
        kwargs: Mapping[str, Any],
    ) -> Any:
        self.calls.append(
            PromptCall(kind, message, tuple(choices) if choices is not None else None, dict(kwargs))
        )
        queue = self._script[kind]
        if not queue:
            raise PromptExhausted(
                f"unscripted {kind!r} prompt: {message!r} "
                f"(scripted answers exhausted; prompts so far: {[c.kind for c in self.calls]})"
            )
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            # Models an editor that fails: add row 2d requires the failure to be non-fatal.
            raise answer
        return answer

    def of(self, kind: str) -> list[PromptCall]:
        """Every recorded call of one prompt kind, in order."""
        return [call for call in self.calls if call.kind == kind]

    def called(self, kind: str) -> bool:
        return any(call.kind == kind for call in self.calls)

    @property
    def kinds(self) -> list[str]:
        """The prompt kinds in the order they fired -- the flow's shape."""
        return [call.kind for call in self.calls]

    @property
    def remaining(self) -> dict[str, int]:
        """Unconsumed answers per kind; a non-zero count means the flow prompted less."""
        return {kind: len(queue) for kind, queue in self._script.items() if queue}


# ======================================================================================
# Git double -- vi.mock("@changesets/git")
# ======================================================================================


@dataclass(frozen=True)
class GitCall:
    """One recorded git mutation."""

    name: str
    args: tuple[Any, ...] = ()


class FakeGit:
    """Records git mutations and returns the reference suite's frozen read values.

    Mirrors ``vi.mock("@changesets/git")``: ``add``/``commit``/``tag`` are recording no-ops (the
    commit-config rows spy on them), ``get_current_commit_id`` returns :data:`FROZEN_COMMIT`, and
    ``get_commits_that_add_files`` returns :data:`CHANGELOG_HASH` for every path.

    Seed :attr:`existing_tags` for the ``git-tag`` idempotency rows (06 group, rows 3/4): a tag
    already present must not be created again.
    """

    def __init__(self, *, existing_tags: Sequence[str] = ()) -> None:
        self.calls: list[GitCall] = []
        self.existing_tags: set[str] = set(existing_tags)

    # -- mutations ----------------------------------------------------------------------
    def add(self, *paths: str) -> None:
        self.calls.append(GitCall("add", tuple(paths)))

    def commit(self, message: str, **kwargs: Any) -> str:
        self.calls.append(GitCall("commit", (message, tuple(sorted(kwargs.items())))))
        return FROZEN_COMMIT

    def tag(self, name: str, message: str | None = None) -> None:
        self.calls.append(GitCall("tag", (name, message)))
        self.existing_tags.add(name)

    # -- reads --------------------------------------------------------------------------
    def get_current_commit_id(self, **kwargs: Any) -> str:
        del kwargs
        return FROZEN_COMMIT

    def get_commits_that_add_files(self, paths: Sequence[str], **kwargs: Any) -> dict[str, str]:
        del kwargs
        return {path: CHANGELOG_HASH for path in paths}

    def get_all_tags(self) -> set[str]:
        return set(self.existing_tags)

    def tag_exists(self, name: str) -> bool:
        return name in self.existing_tags

    # -- assertion helpers --------------------------------------------------------------
    def of(self, name: str) -> list[GitCall]:
        return [call for call in self.calls if call.name == name]

    @property
    def added(self) -> list[str]:
        """Every path passed to ``add``, flattened, in call order."""
        return [str(path) for call in self.of("add") for path in call.args]

    @property
    def commits(self) -> list[str]:
        """Every commit message, in order."""
        return [str(call.args[0]) for call in self.of("commit")]

    @property
    def tags(self) -> list[str]:
        """Every tag name created **by this run**, in order (not the seeded ones)."""
        return [str(call.args[0]) for call in self.of("tag")]


# ======================================================================================
# Filesystem readers -- the reference's manifest/changelog assertions, over pyproject.toml
# ======================================================================================


@dataclass(frozen=True)
class Manifest:
    """A member ``pyproject.toml`` normalized for equality and snapshot assertions.

    Replaces ``getPackages().packages.map(x => x.packageJson)``. ``dependencies`` and
    ``dev_dependencies`` are PEP 508 requirement **strings in file order** (Python dependency
    lists are lists, not npm's name->range object -- research doc 04 section 2.6), and
    ``optional_dependencies`` maps each extra to its list.
    """

    name: str
    version: str | None
    dependencies: tuple[str, ...] = ()
    dev_dependencies: tuple[str, ...] = ()
    optional_dependencies: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    private: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form for ``==`` against a literal, or for a syrupy snapshot."""
        return {
            "name": self.name,
            "version": self.version,
            "dependencies": list(self.dependencies),
            "dev_dependencies": list(self.dev_dependencies),
            "optional_dependencies": {k: list(v) for k, v in self.optional_dependencies.items()},
            "private": self.private,
        }


def _load_toml(path: Path) -> dict[str, Any]:
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))


def _manifest_from_toml(path: Path) -> Manifest:
    data = _load_toml(path)
    project = data.get("project", {})
    optional = {
        str(extra): tuple(str(req) for req in reqs)
        for extra, reqs in (project.get("optional-dependencies") or {}).items()
    }
    dev = tuple(str(req) for req in (data.get("dependency-groups", {}).get("dev") or ()))
    classifiers = [str(c) for c in (project.get("classifiers") or ())]
    return Manifest(
        name=str(project.get("name", "")),
        version=None if project.get("version") is None else str(project["version"]),
        dependencies=tuple(str(req) for req in (project.get("dependencies") or ())),
        dev_dependencies=dev,
        optional_dependencies=optional,
        private="Private :: Do Not Upload" in classifiers,
    )


def read_manifests(root: Path) -> dict[str, Manifest]:
    """Every workspace member manifest under ``root/packages/*``, keyed by package name.

    The workspace-root ``pyproject.toml`` is **not** included -- read it with
    :func:`read_manifest` (``name=None``) when a row asserts on root references (05 group row 24).
    """
    manifests: dict[str, Manifest] = {}
    for pyproject in sorted((root / "packages").glob("*/pyproject.toml")):
        manifest = _manifest_from_toml(pyproject)
        manifests[manifest.name] = manifest
    return manifests


def read_manifest(root: Path, name: str | None = None) -> Manifest:
    """One manifest: the workspace root when ``name`` is None, else ``packages/<name>``."""
    path = root / "pyproject.toml" if name is None else root / "packages" / name / "pyproject.toml"
    return _manifest_from_toml(path)


def read_changelog(root: Path, name: str | None = None) -> str | None:
    """``CHANGELOG.md`` contents for a member (or the root), or None when absent.

    Returned with ``newline=""`` disabled -- read as bytes and decoded -- so a row asserting LF
    output is not silently satisfied by Python translating CRLF on Windows.
    """
    base = root if name is None else root / "packages" / name
    path = base / "CHANGELOG.md"
    if not path.exists():
        return None
    return path.read_bytes().decode("utf-8")


def changeset_ids(root: Path) -> list[str]:
    """Ids of the changeset files still on disk under ``.changeset/``, sorted.

    Dot-prefixed files are included: 05 group row 11 asserts a ``.ignored-temporarily.md`` file
    is neither applied nor deleted, so the assertion needs to see it.
    """
    directory = root / ".changeset"
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.md"))


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    """Parse an NDJSON event stream (the ``--output <file>`` form of ``git-tag``/``publish``).

    An empty file yields ``[]`` -- 06 group git-tag row 3 requires the file to exist and be empty
    when there is nothing to tag, so "no events" and "no file" must stay distinguishable.

    Split on ``"\\n"`` and **not** ``str.splitlines``: NDJSON is delimited by LF alone, while
    ``splitlines`` also breaks on CR, VT, FF, NEL, LS and PS. ``U+2028`` in particular is legal
    *unescaped* inside a JSON string, so a summary containing one would be torn into two invalid
    fragments and this reader would raise where a real NDJSON consumer succeeds.
    """
    raw = path.read_bytes().decode("utf-8")
    return [json.loads(line) for line in raw.split("\n") if line.strip()]


def scrub_ids(value: Any) -> Any:
    """Replace random changeset ids with ``~changeset-N~`` for deterministic snapshots.

    Port of the reference's ``replaceHumanIds`` (status.test.ts). Ids are numbered in the order
    they first appear under ``changesets[].id``; every later occurrence of the same string --
    including inside ``releases[].changesets`` and changeset file paths -- is rewritten to the
    same placeholder. Key-casing agnostic, so it works whether the plan JSON is snake_case or
    camelCase.

    Two documented divergences from ``replaceHumanIds``:

    * **Numbering starts at 0**, where upstream's ``` `~changeset-${++counter}~` ``` starts at 1
      (``status.test.ts:26``). Harmless because molt asserts against explicit literals rather
      than ported snapshot text, and call sites already spell ``~changeset-0~``; recorded here so
      the difference is deliberate rather than a silent porting slip. Do not renumber.
    * **A duplicate id raises.** Upstream throws ``"Duplicate changeset id found"``
      (``status.test.ts:22-24``): two changesets sharing an id is a corrupt plan, not a shape to
      normalize away. Silently reusing one placeholder would let a plan that lost a changeset
      compare equal to one that did not.
    """
    mapping: dict[str, str] = {}
    for entry in _as_sequence(value, "changesets"):
        if isinstance(entry, Mapping):
            raw_id = entry.get("id")
            if isinstance(raw_id, str):
                if raw_id in mapping:
                    raise ValueError(f"Duplicate changeset id found: {raw_id!r}")
                mapping[raw_id] = f"~changeset-{len(mapping)}~"
    return _rewrite(value, mapping, _alternation(mapping))


def _as_sequence(value: Any, key: str) -> Sequence[Any]:
    if isinstance(value, Mapping):
        found = value.get(key)
        if isinstance(found, Sequence) and not isinstance(found, str):
            return found
    return ()


def _alternation(mapping: Mapping[str, str]) -> re.Pattern[str] | None:
    """One longest-match-first regex over every id, or None when there is nothing to rewrite.

    Sequential ``str.replace`` calls are wrong here: with ids ``cat`` and ``cathode``, replacing
    ``cat`` first turns ``cathode`` into ``~changeset-0~hode``, and dict order decides whether the
    corruption happens. Longest-first alternation in a single pass cannot interleave, and cannot
    rewrite text a previous replacement produced.
    """
    if not mapping:
        return None
    return re.compile("|".join(re.escape(key) for key in sorted(mapping, key=len, reverse=True)))


def _rewrite(value: Any, mapping: Mapping[str, str], pattern: re.Pattern[str] | None) -> Any:
    if isinstance(value, str):
        return value if pattern is None else pattern.sub(lambda m: mapping[m.group(0)], value)
    if isinstance(value, Mapping):
        return {key: _rewrite(item, mapping, pattern) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        # `bytes`/`bytearray` are Sequences of *ints*; recursing would return `list[int]` and
        # silently destroy any binary payload a plan carried (and `str` is excluded above).
        return [_rewrite(item, mapping, pattern) for item in value]
    return value


# ======================================================================================
# Product-facing helpers (lazy imports; only called from already-guarded test modules)
# ======================================================================================


def require_cli_app() -> Any:
    """Return the Typer ``app`` from ``molt.cli``, skipping the calling module if it is a stub.

    ``pytest.importorskip("molt.cli")`` is **not** sufficient on its own: ``src/molt/cli.py``
    already exists as a placeholder that prints "not implemented yet", so the import succeeds and
    the module would run red against the stub. Call this after
    ``pytest.importorskip("typer")`` at the top of any module that drives the CLI::

        pytest.importorskip("typer", reason="typer lands with the cli-shell change")
        app = require_cli_app()
    """
    import pytest

    module = pytest.importorskip("molt.cli", reason="build step 6 - the CLI shell is a TDD target")
    app = getattr(module, "app", None)
    if app is None:
        pytest.skip(
            "molt.cli is still the placeholder stub: no Typer `app` attribute "
            "(build step 6 - openspec change adopt-typer-cli-shell)",
            allow_module_level=True,
        )
    return app


def invoke_cli(
    app: Any,
    args: Sequence[str] = (),
    *,
    env: Mapping[str, str] | None = None,
    stdin: str | None = None,
) -> Any:
    """Run a Typer app in-process and return the ``click.testing.Result``.

    ``typer`` is imported lazily because it is not a declared dependency yet -- it lands with the
    ``adopt-typer-cli-shell`` change. Exceptions are caught (``catch_exceptions=True``) so the
    error-funnel rows can assert on ``result.exit_code`` and ``result.exception`` rather than
    letting the exception escape into the test.
    """
    # pyrefly: ignore[missing-import]  -- typer lands with the adopt-typer-cli-shell change.
    from typer.testing import CliRunner

    runner = CliRunner()
    return runner.invoke(app, list(args), env=dict(env) if env else None, input=stdin)
