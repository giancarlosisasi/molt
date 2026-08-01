"""Conformance tests for ``molt add`` -- the flagship interactive command.

Ports ``packages/cli/src/commands/add/__tests__/add.test.ts`` (23 cases: rows 1, 2a-2d, 3-20 of
``roadmap/research/test-suite/06-cli-commands.md``, scored *Port (all; prompts Adapt, +molt-NEW
non-interactive)*), plus the three molt-native drive modes documented in
``website/docs/cli/add.md``: release-type/``--package`` flags, ``--stdin``, and ``--dry-run``.
The behavioral source of truth is ``roadmap/research/changesets-03-cli-and-ux.md`` section 2
(2.1 prompt primitives ... 2.9 ``--empty``), plus section 9.2 (failure catalogue) and section 10
(gotchas).

Module naming deviation
-----------------------
The command logic lives in ``molt.commands.add`` (research doc 03 section 11.5,
``openspec/changes/adopt-typer-cli-shell/design.md`` D7, ``tasks.md`` 1.2).
``roadmap/research/test-suite/MILESTONES.md`` and the P5 phase brief say ``molt.cli.commands.add``;
they are the outlier and are superseded.

Assumed seams (flagged for the owner; used consistently across the whole P5 group)
---------------------------------------------------------------------------------
* ``run(*, cwd: Path, <one keyword per long flag>, console=, prompts=, git=)`` -- each flag keyword
  is the long flag with dashes turned into underscores (``--non-interactive`` ->
  ``non_interactive``, ``--dry-run`` -> ``dry_run``). The three injectables default to the real
  implementations, so the Typer shell passes none of them (design D6: "Commands and the shell
  depend on the protocols only").
* ``--stdin`` reads ``sys.stdin`` (monkeypatched here); it is not an injected seam.
* ``--open`` is asserted through ``Prompts.editor`` -- the only editor seam the frozen harness
  exposes. If molt grows a separate ``molt.ui.editor.launch_editor``, re-point
  :func:`test_open_launches_the_editor_after_the_success_message` at it.
* The changeset id generator is monkeypatched through :func:`seed_changeset_ids`, which names the
  candidate seams and fails loudly if none exists (only one test depends on the id).
* The **grouped** multiselect must receive either an ordered sequence of ``(group label, choices)``
  pairs positionally, **or** a ``groups=`` mapping keyword -- :func:`multiselect_groups` accepts
  both and normalizes them. What it cannot accept is a Mapping passed *positionally*: the frozen
  harness records ``tuple(choices)``, and ``tuple(mapping)`` keeps only the group names, so every
  group member would be discarded before the assertion sees it. Its failure message says so.

Upstream bugs molt fixes here (each pinned to molt's behavior below, each a divergence needing
owner sign-off -- research doc 03 section 11.7):
* section 10.6 -- the editor summary stripping every line starting with ``#``. molt does not strip;
  Markdown headings survive (``website/docs/config/changeset-format.md`` "Markdown headings in
  summaries are preserved"). See :func:`test_editor_summary_preserves_markdown_headings`.
* section 10.8 -- declining the first-major confirmation aborts with exit 1 in a single-package repo
  but falls through to the minor prompt in a monorepo. molt falls through in both shapes.
* section 10.7 -- ``--major/--minor/--patch`` bypassing the first-major guard entirely. molt applies
  the guard to flag-selected majors too; ``--non-interactive`` proceeds without prompting.

Deliberate molt divergences that are *not* upstream bugs:
* changed-package detection is skipped entirely on the flag-driven path
  (``website/docs/guides/adding-a-changeset.md``: "this form does no 'changed packages' detection
  and never blocks on a prompt"). Upstream runs it unconditionally (``add/index.ts:72-89``).
* package-name matching is PEP 503-normalized at lookup time (research README section 4.5).
"""

from __future__ import annotations

import importlib
import io
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import pytest
from tests.cli.fake_cli import (
    CANCEL,
    FakeGit,
    GitCall,
    RecordingConsole,
    ScriptedPrompts,
    changeset_ids,
)
from tests.cli.fake_questionary import FakeQuestionary, pick

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

    from tests.cli.fake_cli import PromptCall
    from tests.conftest import ProjectBuilder, SeededIds

pytest.importorskip("molt.commands.add", reason="build step 6 - `molt add` is a TDD target")
pytest.importorskip("molt.errors", reason="build step 9 - molt.errors is a TDD target")

from molt.commands.add import run
from molt.errors import ExitError, GitError

pytestmark = pytest.mark.functional


# --------------------------------------------------------------------------------------
# Constants shared with the reference suite
# --------------------------------------------------------------------------------------

#: The summary ``mockUserResponses`` supplies by default (``add.test.ts:29``).
SUMMARY: Final = "summary message mock"

#: Candidate homes for the ``human-id`` equivalent. Every one that exists gets patched; the
#: assertion in :func:`seed_changeset_ids` fires when none does, so a renamed seam is loud.
ID_SEAM_CANDIDATES: Final = (
    ("molt.commands.add", "generate_changeset_id"),
    ("molt.changeset", "generate_changeset_id"),
    ("molt.changeset.write", "generate_changeset_id"),
)


# --------------------------------------------------------------------------------------
# Local doubles -- named so they cannot collide with the frozen shared harness
# --------------------------------------------------------------------------------------


class GitWithChanges(FakeGit):
    """:class:`FakeGit` plus the one read the ``--since`` partition needs.

    The frozen harness has no changed-package reader because the shared rows do not need one;
    ``add`` row 10 (``add.test.ts:380-435``) does. Both plausible spellings of the seam are
    provided -- the low-level ``diff_name_only`` from the ``molt.git`` surface in the test
    contract section 5, and the port of upstream's ``git.getChangedPackagesSinceRef``
    (``utils/versionablePackages.ts:5-27``) -- so the row asserts the *partition*, which is the
    behavior under test, rather than the shape of a seam nobody has committed to yet.
    """

    def __init__(
        self,
        *,
        changed_files: Sequence[str] = (),
        existing_tags: Sequence[str] = (),
    ) -> None:
        super().__init__(existing_tags=existing_tags)
        self.changed_files: tuple[str, ...] = tuple(changed_files)

    def diff_name_only(self, ref: str, **kwargs: Any) -> list[str]:
        del kwargs
        self.calls.append(GitCall("diff_name_only", (ref,)))
        return list(self.changed_files)

    def get_changed_packages_since_ref(self, ref: str, **kwargs: Any) -> list[str]:
        del kwargs
        self.calls.append(GitCall("get_changed_packages_since_ref", (ref,)))
        return sorted(
            {
                path.split("/")[1]
                for path in self.changed_files
                if path.startswith("packages/") and "/" in path[len("packages/") :]
            }
        )

    @property
    def refs(self) -> list[str]:
        """Every ref the changed-package detection asked about, in call order."""
        return [
            str(call.args[0])
            for call in self.calls
            if call.name in {"diff_name_only", "get_changed_packages_since_ref"}
        ]


class TimelineConsole(RecordingConsole):
    """A :class:`RecordingConsole` that also appends to a shared ordering timeline.

    Only ``--open`` needs it: section 10.24 pins that the editor fires *after* the success
    message, and neither shared double records a global order.
    """

    def __init__(self, timeline: list[str]) -> None:
        super().__init__()
        self._timeline = timeline

    def success(self, message: str) -> None:
        self._timeline.append("success")
        super().success(message)


class TimelinePrompts(ScriptedPrompts):
    """A :class:`ScriptedPrompts` that appends editor calls to a shared ordering timeline."""

    def __init__(self, timeline: list[str], **script: Sequence[Any]) -> None:
        super().__init__(**script)
        self._timeline = timeline

    def editor(self, message: str = "", **kwargs: Any) -> Any:
        self._timeline.append("editor")
        return super().editor(message, **kwargs)


# --------------------------------------------------------------------------------------
# Workspace helpers (never named test_* -- `python_functions = "test"` is a prefix match)
# --------------------------------------------------------------------------------------


def monorepo(tmp_project: ProjectBuilder, *names: str, version: str = "1.0.0") -> Path:
    """A uv workspace with ``names`` as members and an empty ``.changeset/``.

    The package set is the ``[tool.uv.workspace] members`` glob and nothing else --
    ``website/docs/ecosystems/uv.md``: "Molt reads that glob to enumerate the workspace members
    ... These are the packages ``molt add`` offers you." So the workspace-root manifest
    ``ProjectBuilder`` writes is **not** a selectable package, which is why no prompt expectation
    below ever mentions ``workspace-root``. (Upstream gets the same effect from its root
    ``package.json`` being ``private`` with no version.)
    """
    for name in names:
        tmp_project.add_package(name, version=version)
    (tmp_project.root / ".changeset").mkdir(parents=True, exist_ok=True)
    return tmp_project.root


def single_package_repo(
    tmp_project: ProjectBuilder,
    name: str = "single-package",
    version: str = "1.0.0",
) -> Path:
    """Rewrite the root manifest as one package with no ``[tool.uv.workspace]`` table.

    ``ProjectBuilder`` always writes the workspace table, and the single-package flow (Flow B,
    ``createChangeset.ts:254-271``) is selected by there being exactly one package -- so the table
    has to go.
    """
    (tmp_project.root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (tmp_project.root / ".changeset").mkdir(parents=True, exist_ok=True)
    return tmp_project.root


def strip_version(root: Path, name: str | None = None) -> None:
    """Delete the ``version`` line from a manifest -- ``add_package`` always writes one.

    "No ``version`` field" is one of the three things ``shouldSkipPackage`` filters on
    (``should-skip-package/src/index.ts:13-22``), and rows 12/14/15 all turn on it.
    """
    path = root / "pyproject.toml" if name is None else root / "packages" / name / "pyproject.toml"
    kept = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("version =")
    ]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def normalize(name: str) -> str:
    """PEP 503 name normalization -- the comparison form (research README section 4.5)."""
    return re.sub(r"[-_.]+", "-", name).lower()


# --------------------------------------------------------------------------------------
# Reading back what was written (no dependency on molt.changeset, a separate TDD target)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WrittenChangeset:
    """One changeset file on disk, parsed straight from bytes."""

    id: str
    releases: list[tuple[str, str]]
    summary: str


def read_changeset(root: Path, changeset_id: str) -> WrittenChangeset:
    """Parse ``root/.changeset/<id>.md`` without importing ``molt.changeset``.

    The byte-level template (LF only, names double-quoted, ``<summary>\\n`` trailer) is pinned by
    ``tests/changeset/test_write.py``; this reader deliberately re-implements only enough of the
    grammar to assert *content*, so ``add`` and ``write`` stay independently testable.
    """
    raw = (root / ".changeset" / f"{changeset_id}.md").read_bytes().decode("utf-8")
    lines = raw.splitlines()
    assert lines and lines[0].strip() == "---", f"missing opening frontmatter fence: {raw!r}"
    closing = -1
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing = index
            break
    assert closing != -1, f"missing closing frontmatter fence: {raw!r}"
    releases: list[tuple[str, str]] = []
    for line in lines[1:closing]:
        if not line.strip():
            continue
        name, _, bump = line.partition(":")
        releases.append((name.strip().strip('"'), bump.strip()))
    return WrittenChangeset(changeset_id, releases, "\n".join(lines[closing + 1 :]).strip())


def read_only_changeset(root: Path) -> WrittenChangeset:
    """The one changeset ``add`` just wrote; asserts there is exactly one."""
    ids = changeset_ids(root)
    assert len(ids) == 1, f"expected exactly one changeset, found {ids}"
    return read_changeset(root, ids[0])


# --------------------------------------------------------------------------------------
# Prompt-argument helpers -- rows 10/11/12/13 assert on what the prompt was *called with*
# --------------------------------------------------------------------------------------


def choice_value(choice: Any) -> str:
    """The value of one prompt choice, whatever shape the implementation chose.

    Accepts a bare string, a mapping with ``value`` (upstream's ``{label, value}`` shape), a
    ``(label, value)`` pair, or any object exposing ``.value`` (questionary's ``Choice``).
    """
    if isinstance(choice, str):
        return choice
    if isinstance(choice, dict):
        return str(choice.get("value", choice.get("label", choice)))
    if isinstance(choice, (list, tuple)) and len(choice) == 2:
        return str(choice[1])
    return str(getattr(choice, "value", choice))


def choice_values(choices: Any) -> list[str]:
    return [choice_value(choice) for choice in choices or ()]


def is_group(entry: Any) -> bool:
    """True when ``entry`` looks like a ``(group label, choices)`` pair."""
    return (
        isinstance(entry, (list, tuple))
        and len(entry) == 2
        and isinstance(entry[0], str)
        and isinstance(entry[1], (list, tuple))
        and not isinstance(entry[1], str)
    )


def multiselect_groups(call: PromptCall) -> list[tuple[str, list[str]]]:
    """The grouped multiselect's groups, in display order, each with its values in order."""
    groups = call.kwargs.get("groups")
    if isinstance(groups, dict):
        return [(str(label), choice_values(choices)) for label, choices in groups.items()]
    raw = list(call.choices or ())
    assert raw, "the package multiselect was called with no choices"
    assert all(is_group(entry) for entry in raw), (
        "the grouped multiselect must receive either an ordered sequence of "
        "(group label, choices) pairs positionally, or a `groups=` mapping keyword -- both are "
        "accepted here. What arrived is neither. The shared harness records `tuple(choices)`, so "
        "a Mapping passed *positionally* arrives with every group member discarded, which is the "
        f"one shape this helper cannot reconstruct. Got: {raw!r}"
    )
    return [(str(entry[0]), choice_values(entry[1])) for entry in raw]


def multiselect_values(call: PromptCall) -> list[str]:
    """Every selectable value of a multiselect, flattened across groups, in display order."""
    groups = call.kwargs.get("groups")
    if isinstance(groups, dict):
        return [value for choices in groups.values() for value in choice_values(choices)]
    raw = list(call.choices or ())
    if raw and all(is_group(entry) for entry in raw):
        return [value for entry in raw for value in choice_values(entry[1])]
    return choice_values(raw)


# --------------------------------------------------------------------------------------
# Driving the command
# --------------------------------------------------------------------------------------


def add_changeset(
    root: Path,
    *,
    console: RecordingConsole | None = None,
    prompts: ScriptedPrompts | None = None,
    git: FakeGit | None = None,
    **options: Any,
) -> None:
    """Call ``molt.commands.add.run`` with the three injectable seams always supplied.

    Defaulting ``prompts`` to an **empty** :class:`ScriptedPrompts` is deliberate: any prompt a
    "should not prompt" row does not expect raises ``PromptExhausted`` immediately instead of
    blocking, which is the assertion those rows actually want.
    """
    run(
        cwd=root,
        console=console if console is not None else RecordingConsole(),
        prompts=prompts if prompts is not None else ScriptedPrompts(),
        git=git if git is not None else GitWithChanges(),
        **options,
    )


def exit_code_of(call: Callable[[], object]) -> int:
    """The exit code ``call`` produced -- 0 when it returns normally.

    Used only where the contract is "exit 0" (a cancelled prompt, research doc 03 section 10.5):
    a command that signals success by returning and one that raises ``ExitError(0)`` are both
    correct, and the row is about the code, not the mechanism. Failure rows use
    ``pytest.raises(ExitError)`` directly so a silent success cannot pass.
    """
    try:
        call()
    except ExitError as exc:
        return int(exc.code)
    return 0


def seed_changeset_ids(monkeypatch: pytest.MonkeyPatch, seeded_ids: SeededIds) -> None:
    """Point molt's changeset-id generator at the deterministic ``seeded_ids`` fixture.

    The equivalent of ``vi.mock("human-id")``. The assertion is load-bearing: without it a
    renamed seam would leave the filename assertion asserting against a real random id and
    quietly failing for the wrong reason.
    """
    patched: list[str] = []
    for module_name, attr in ID_SEAM_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        if hasattr(module, attr):
            monkeypatch.setattr(module, attr, seeded_ids)
            patched.append(f"{module_name}.{attr}")
    assert patched, (
        "no changeset-id seam found to stand in for `human-id`; tried "
        f"{['.'.join(pair) for pair in ID_SEAM_CANDIDATES]}"
    )


def feed_stdin(monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
    """Make ``sys.stdin`` yield ``payload`` for the ``--stdin`` drive mode."""
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))


def cancel_sentinel() -> Any:
    """The value that models a Ctrl-C at a prompt.

    Prefers molt's own sentinel when ``molt.ui.prompts`` exports one, and only falls back to the
    harness's :data:`CANCEL`. That fallback matters: the accepted ``terminal-ui`` spec routes
    cancellation through one ``cancelable`` helper, and a helper that checks ``value is
    molt.ui.prompts.CANCEL`` would not recognize a *different* object handed to it by a test
    double -- the cancellation rows would then fail for a reason that has nothing to do with the
    behavior under test.
    """
    try:
        module = importlib.import_module("molt.ui.prompts")
    except ModuleNotFoundError:
        return CANCEL
    return getattr(module, "CANCEL", CANCEL)


#: Evaluated once at import: the module only reaches this point when ``molt.commands.add`` exists.
CANCEL_ANSWER: Final = cancel_sentinel()


# ======================================================================================
# Flow A -- interactive monorepo (createChangeset.ts:171-253)
# ======================================================================================


def test_generates_a_changeset_to_patch_a_single_package(tmp_project: ProjectBuilder) -> None:
    """Row 1 (``add.test.ts:84-114``) -- the core happy path.

    Also pins the *shape* of Flow A: three multiselects (packages, majors, minors) then the
    summary text prompt (research doc 03 section 2.4). Everything left after the major and minor
    prompts is patched with no prompt of its own (``createChangeset.ts:234-253``).
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    prompts = ScriptedPrompts(multiselect=[["pkg-a"], [], []], text=[SUMMARY])

    add_changeset(root, prompts=prompts)

    written = read_only_changeset(root)
    assert written.releases == [("pkg-a", "patch")]
    assert written.summary == SUMMARY
    assert prompts.kinds == ["multiselect", "multiselect", "multiselect", "text"]
    assert prompts.remaining == {}


# (console answers, editor answers, expected summary, editor calls, why)
SUMMARY_CASES: Final = [
    (
        ["summary on step 1"],
        [],
        "summary on step 1",
        0,
        "a non-empty console answer is the summary; the editor is never opened",
    ),
    (
        [""],
        ["summary in external editor"],
        "summary in external editor",
        1,
        "an empty console answer opens $EDITOR and the editor value wins",
    ),
    (
        ["", "summary after editor cancelled"],
        [""],
        "summary after editor cancelled",
        1,
        "an empty editor result re-prompts the console ('Did not find a summary...')",
    ),
    (
        ["", "summary after error"],
        [RuntimeError("editor exploded")],
        "summary after error",
        1,
        "an editor that raises is non-fatal: the flow falls back to the console prompt",
    ),
]


@pytest.mark.parametrize(
    ("console_answers", "editor_answers", "expected", "editor_calls", "why"), SUMMARY_CASES
)
def test_summary_capture_matrix(
    tmp_project: ProjectBuilder,
    console_answers: list[str],
    editor_answers: list[Any],
    expected: str,
    editor_calls: int,
    why: str,
) -> None:
    """Rows 2a-2d (``add.test.ts:116-158``) -- the summary/editor fallback chain.

    The exact matrix is reproduced from research doc 03 section 2.4 (Prompt 5b table). Upstream
    scripts the failing editor as the literal ``1``; the shared harness raises any scripted answer
    that is an exception instance, so 2d scripts a ``RuntimeError``.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    prompts = ScriptedPrompts(
        multiselect=[["pkg-a"], [], []], text=console_answers, editor=editor_answers
    )

    add_changeset(root, prompts=prompts)

    written = read_only_changeset(root)
    assert written.summary == expected, why
    assert written.releases == [("pkg-a", "patch")], why
    assert len(prompts.of("editor")) == editor_calls, why
    assert prompts.remaining == {}, why


def test_editor_summary_preserves_markdown_headings(tmp_project: ProjectBuilder) -> None:
    """molt FIX of research doc 03 section 10.6 (``askWithEditor.ts:31``).

    Upstream post-processes the edited file with ``replace(/^#.*\\n?/gm, "")``, which deletes
    **every** line starting with ``#`` -- so Markdown headings typed into the editor vanish from
    the changeset. molt keeps them: ``website/docs/config/changeset-format.md`` ("Markdown
    headings in summaries are preserved") and research doc 03 section 11.7 item 1.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    summary = "## What changed\n\nAdded a `--stream` flag.\n\n### Migration\n\nSwitch to streams."
    prompts = ScriptedPrompts(multiselect=[["pkg-a"], [], []], text=[""], editor=[summary])

    add_changeset(root, prompts=prompts)

    assert read_only_changeset(root).summary == summary


def test_written_changeset_filename_uses_the_generated_id(
    tmp_project: ProjectBuilder, seeded_ids: SeededIds, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The id comes from the ``human-id`` equivalent, one per changeset (``write/src/index.ts:41``).

    The filename carries no meaning (``website/docs/config/changeset-format.md``), so exactly one
    test depends on it -- enough to pin the seam, not enough to make the suite brittle.
    """
    seed_changeset_ids(monkeypatch, seeded_ids)
    root = monorepo(tmp_project, "pkg-a", "pkg-b")

    add_changeset(root, prompts=ScriptedPrompts(multiselect=[["pkg-a"], [], []], text=[SUMMARY]))

    assert changeset_ids(root) == ["strange-words-combine"]


# ======================================================================================
# Flow B -- single-package repo (createChangeset.ts:254-271)
# ======================================================================================


def test_single_package_repo_uses_a_select_for_the_bump(tmp_project: ProjectBuilder) -> None:
    """Row 3 (``add.test.ts:162-188``) -- one ``select``, never a multiselect.

    The choice order is pinned by ``createChangeset.ts:257-262`` (patch, minor, major) and the
    message carries the package name and its current version -- both are what make the prompt
    answerable without leaving the terminal (research doc 03 section 2.5).
    """
    root = single_package_repo(tmp_project, "single-package", "1.2.3")
    prompts = ScriptedPrompts(select=["minor"], text=[""], editor=[SUMMARY])

    add_changeset(root, prompts=prompts)

    written = read_only_changeset(root)
    assert written.releases == [("single-package", "minor")]
    assert written.summary == SUMMARY
    assert not prompts.called("multiselect")
    select = prompts.of("select")[0]
    assert choice_values(select.choices) == ["patch", "minor", "major"]
    assert "single-package" in select.message
    assert "1.2.3" in select.message


# ======================================================================================
# The first-major guard -- confirmMajorRelease (createChangeset.ts:15-29)
# ======================================================================================


def test_first_major_confirmation_fires_below_1_0_0(tmp_project: ProjectBuilder) -> None:
    """The guard exists so a repo-wide sweep cannot accidentally cut a 1.0.0.

    ``semverLt(version, "1.0.0")`` gates it (``createChangeset.ts:16``); PEP 440 comparison is
    the molt equivalent. Accepting it produces the major release.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b", version="0.4.0")
    prompts = ScriptedPrompts(multiselect=[["pkg-a"], ["pkg-a"]], confirm=[True], text=[SUMMARY])

    add_changeset(root, prompts=prompts)

    assert read_only_changeset(root).releases == [("pkg-a", "major")]
    assert prompts.called("confirm")
    assert "pkg-a" in prompts.of("confirm")[0].message


def test_first_major_confirmation_is_skipped_at_or_above_1_0_0(tmp_project: ProjectBuilder) -> None:
    """A package already past 1.0.0 has no first major to guard (``createChangeset.ts:16``).

    The empty ``confirm`` script is the assertion: a confirmation here raises ``PromptExhausted``.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b", version="1.2.0")
    prompts = ScriptedPrompts(multiselect=[["pkg-a"], ["pkg-a"]], text=[SUMMARY])

    add_changeset(root, prompts=prompts)

    assert read_only_changeset(root).releases == [("pkg-a", "major")]
    assert not prompts.called("confirm")


def test_declining_the_first_major_falls_through_to_the_minor_prompt(
    tmp_project: ProjectBuilder,
) -> None:
    """Monorepo shape (``createChangeset.ts:197-209``): a declined package is not dropped.

    It stays in ``pkgsLeftToGetBumpTypeFor``, so the minor multiselect offers it again -- the
    user gets a second choice rather than losing the changeset. molt keeps this half of the
    behavior; the single-package half is the bug it fixes (see the next test).
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b", version="0.4.0")
    prompts = ScriptedPrompts(
        multiselect=[["pkg-a"], ["pkg-a"], ["pkg-a"]], confirm=[False], text=[SUMMARY]
    )

    add_changeset(root, prompts=prompts)

    written = read_only_changeset(root)
    assert written.releases == [("pkg-a", "minor")]
    assert "pkg-a" in multiselect_values(prompts.of("multiselect")[2])


def test_declining_the_first_major_in_a_single_package_repo_does_not_abort(
    tmp_project: ProjectBuilder,
) -> None:
    """molt FIX of research doc 03 section 10.8 (``createChangeset.ts:264-269``).

    Upstream throws ``ExitError(1)`` here while the monorepo path silently falls through -- the
    same answer to the same question destroys the session in one repo shape and not the other.
    molt falls through in both (research doc 03 section 11.7 item 2), so the assertion is: the
    command still succeeds and the release is *not* a major. Whether the fall-through re-asks the
    bump select or downgrades in place is the implementation's choice; both satisfy this.
    """
    root = single_package_repo(tmp_project, "single-package", "0.4.0")
    prompts = ScriptedPrompts(
        select=["major", "minor"], confirm=[False], text=[""], editor=[SUMMARY]
    )

    code = exit_code_of(lambda: add_changeset(root, prompts=prompts))

    assert code == 0, "upstream exits 1 here; molt does not (section 10.8)"
    written = read_only_changeset(root)
    assert written.releases[0][0] == "single-package"
    assert written.releases[0][1] in {"minor", "patch"}, "a declined major must not be written"
    assert prompts.called("confirm")


def test_release_type_flags_still_ask_the_first_major_confirmation(
    tmp_project: ProjectBuilder,
) -> None:
    """molt FIX of research doc 03 section 10.7 (``createChangeset.ts:148-170``).

    Upstream never calls ``confirmMajorRelease`` on the flag path, so ``changeset --major pkg-a``
    silently bypasses the one safety net that exists for first majors. molt applies the guard to
    flag-selected majors too (research doc 03 section 11.7 item 3). ``--non-interactive`` is the
    documented escape hatch -- see the next test.

    Note: ``website/docs/cli/add.md`` does not document this prompt at all. Docs follow-up.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b", version="0.4.0")
    prompts = ScriptedPrompts(confirm=[True])

    add_changeset(root, prompts=prompts, major=["pkg-a"], message=SUMMARY)

    assert read_only_changeset(root).releases == [("pkg-a", "major")]
    assert prompts.kinds == ["confirm"], "only the guard prompts; -m skips the summary"


def test_declining_the_first_major_on_the_flag_path_aborts_and_writes_nothing(
    tmp_project: ProjectBuilder,
) -> None:
    """AC-3 (owner ruling, 2026-07-30): the flag path has no minor prompt to fall through to, so a
    declined flag-selected first major now **aborts** -- exit 0, nothing written, one line reported
    -- instead of being silently downgraded to a minor. Only the *accepted* answer was tested before
    this ruling (the previous test). The **interactive** flow is unchanged: see
    ``test_declining_the_first_major_falls_through_to_the_minor_prompt`` and
    ``test_declining_the_first_major_in_a_single_package_repo_does_not_abort``, both still pinning
    fall-through.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b", version="0.4.0")
    console = RecordingConsole()
    prompts = ScriptedPrompts(confirm=[False])

    code = exit_code_of(
        lambda: add_changeset(
            root, console=console, prompts=prompts, major=["pkg-a"], message=SUMMARY
        )
    )

    assert code == 0, "declining aborts cleanly, it does not fail the run"
    assert changeset_ids(root) == [], "nothing may be written when a first major is declined"
    assert console.contains("pkg-a")
    assert console.contains("nothing was written")


def test_non_interactive_major_flag_proceeds_without_confirmation(
    tmp_project: ProjectBuilder,
) -> None:
    """``--non-interactive`` turns every prompt into a documented default (section 11.7 item 7).

    The empty prompt script is the assertion: any prompt at all raises ``PromptExhausted``. This
    is the CI path for the guard added by the previous test -- without it, molt's fix would make
    ``--major`` unusable from a bot.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b", version="0.4.0")
    prompts = ScriptedPrompts()

    add_changeset(root, prompts=prompts, major=["pkg-a"], message=SUMMARY, non_interactive=True)

    assert read_only_changeset(root).releases == [("pkg-a", "major")]
    assert prompts.calls == []


# ======================================================================================
# "Changed packages first" detection -- add/index.ts:72-89
# ======================================================================================


def test_since_partitions_the_multiselect_choices(tmp_project: ProjectBuilder) -> None:
    """Row 10 (``add.test.ts:380-435``) -- the assertion is on the prompt *arguments*.

    Upstream asserts ``askMultiselect`` received
    ``{"changed packages": [pkg-b], "unchanged packages": [pkg-a]}`` with ``{required: true}``.
    Asserting only the resulting changeset would pass against an implementation with no
    partitioning at all, which is why this row reads the recorded ``PromptCall``.

    Two things are pinned: changed packages come **first** (object insertion order is display
    order upstream, ``createChangeset.ts:59-64``), and each group is sorted by name
    (``toSorted`` on ``localeCompare``, ``createChangeset.ts:41-43``) -- the packages are added
    out of order here so the sort cannot pass by accident.
    """
    root = monorepo(tmp_project, "pkg-c", "pkg-a", "pkg-b")
    git = GitWithChanges(changed_files=["packages/pkg-b/b.py"])
    prompts = ScriptedPrompts(multiselect=[["pkg-b"], [], []], text=[SUMMARY])

    add_changeset(root, prompts=prompts, git=git, since="foo")

    call = prompts.of("multiselect")[0]
    assert multiselect_groups(call) == [
        ("changed packages", ["pkg-b"]),
        ("unchanged packages", ["pkg-a", "pkg-c"]),
    ]
    assert call.kwargs.get("required") is True, "an empty package selection cannot be submitted"
    assert "Which packages" in call.message
    assert git.refs == ["foo"], "--since is the ref the detection compares against"
    assert read_only_changeset(root).releases == [("pkg-b", "patch")]


def test_without_changed_packages_only_one_group_is_offered(tmp_project: ProjectBuilder) -> None:
    """Empty groups are omitted (``createChangeset.ts:59-64``), so nothing renders an empty box.

    Also the **default** half of ``--since``, which the previous row only pins in its explicit
    form: ``website/docs/cli/add.md:64`` gives the flag's default as "base branch", and
    ``website/docs/config/options.md:15`` names that as ``base_branch`` -- "Git ref used as the
    comparison base for 'what changed'. Overridable per command with ``--since``." A distinctive
    value is configured so the assertion cannot pass against an implementation that hardcodes
    ``main`` (the ``base_branch`` *default* is pinned by ``tests/config/test_parse.py``, not here),
    nor against one that skips the detection entirely and reports "no changed packages" by
    accident -- which is the very state this row is otherwise asserting.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    tmp_project.set_config(base_branch="trunk")
    git = GitWithChanges()
    prompts = ScriptedPrompts(multiselect=[["pkg-a"], [], []], text=[SUMMARY])

    add_changeset(root, prompts=prompts, git=git)

    groups = multiselect_groups(prompts.of("multiselect")[0])
    assert len(groups) == 1
    assert groups[0][1] == ["pkg-a", "pkg-b"]
    assert git.refs == ["trunk"], "with no --since, the detection compares against base_branch"


def test_release_type_flags_skip_changed_package_detection(tmp_project: ProjectBuilder) -> None:
    """Deliberate molt divergence: the flag path does no changed-package detection.

    ``website/docs/guides/adding-a-changeset.md``: "Because you name the packages explicitly, this
    form does no 'changed packages' detection and never blocks on a prompt -- safe to run in CI."
    Upstream runs the detection unconditionally for non-``--empty`` runs (``add/index.ts:72-89``)
    and simply ignores the result in Flow C, paying a git round-trip for nothing.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    git = GitWithChanges(changed_files=["packages/pkg-a/a.py"])

    add_changeset(root, git=git, patch=["pkg-a"], message=SUMMARY)

    assert git.refs == []
    assert read_only_changeset(root).releases == [("pkg-a", "patch")]


# ======================================================================================
# Which packages the prompt offers -- shouldSkipPackage (add/index.ts:44-50)
# ======================================================================================


def test_prompt_excludes_ignored_packages(tmp_project: ProjectBuilder) -> None:
    """Row 11 (``add.test.ts:437-472``) -- ``ignore``d packages never reach the prompt.

    A package that can never be released must not be selectable, or the changeset it lands in is
    unapplyable. Config key is molt's snake_case ``ignore`` (``website/docs/config/options.md``).
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b", "pkg-c")
    tmp_project.set_config(ignore=["pkg-b"])
    prompts = ScriptedPrompts(multiselect=[["pkg-a"], [], []], text=[SUMMARY])

    add_changeset(root, prompts=prompts)

    assert multiselect_values(prompts.of("multiselect")[0]) == ["pkg-a", "pkg-c"]


def test_prompt_excludes_packages_without_a_version(tmp_project: ProjectBuilder) -> None:
    """Row 12 (``add.test.ts:473-505``), **adapted**.

    Upstream's fixture marks pkg-b ``{private: true}`` *and* omits its version, and
    ``shouldSkipPackage`` skips it for the missing version
    (``should-skip-package/src/index.ts:13-22``). Python has no ``private`` manifest field, so the
    version half is the part that ports literally: a manifest with no ``[project].version`` is not
    versionable. (The private half is row 13.)
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b", "pkg-c")
    strip_version(root, "pkg-b")
    prompts = ScriptedPrompts(multiselect=[["pkg-a"], [], []], text=[SUMMARY])

    add_changeset(root, prompts=prompts)

    assert multiselect_values(prompts.of("multiselect")[0]) == ["pkg-a", "pkg-c"]


# (config kwargs, expected choices, why)
PRIVATE_CASES: Final = [
    (
        {},
        ["pkg-a", "pkg-b", "pkg-c"],
        "private_packages.version defaults to true, so a private package is still versionable",
    ),
    (
        {"private_packages": {"version": False}},
        ["pkg-a", "pkg-c"],
        "private_packages.version = false takes private packages out of versioning entirely",
    ),
]


@pytest.mark.parametrize(("config", "expected", "why"), PRIVATE_CASES)
def test_prompt_respects_private_package_versioning(
    tmp_project: ProjectBuilder, config: dict[str, Any], expected: list[str], why: str
) -> None:
    """Row 13 (``add.test.ts:506-545``), **adapted**.

    npm's ``"private": true`` becomes the ``Private :: Do Not Upload`` classifier in Python
    (research doc 02 section 12.1, ``ProjectBuilder.add_package(private=True)``); the config knob
    keeps its meaning under molt's snake_case spelling ``private_packages.version``
    (``website/docs/config/options.md``). The default row is included because a filter that
    excluded private packages unconditionally would pass the upstream row alone.

    Three packages, like upstream's fixture: excluding one has to leave **two** versionable, or
    the run drops into the single-package flow (Flow B) and there is no multiselect to inspect.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-c")
    tmp_project.add_package("pkg-b", version="1.0.0", private=True)
    if config:
        tmp_project.set_config(**config)
    prompts = ScriptedPrompts(multiselect=[["pkg-a"], [], []], text=[SUMMARY])

    add_changeset(root, prompts=prompts)

    assert multiselect_values(prompts.of("multiselect")[0]) == expected, why


# ======================================================================================
# Preconditions -- add/index.ts:33-61, research doc 03 section 2.2
# ======================================================================================

# (build the repo, why) -- rows 14 and 15 assert the same message in both repo shapes.
NO_VERSIONABLE_CASES: Final = [
    ("single", "row 14: a single-package repo whose root manifest has no version"),
    ("monorepo", "row 15: a workspace whose members all lack a version"),
]


@pytest.mark.parametrize(("shape", "why"), NO_VERSIONABLE_CASES)
def test_no_versionable_packages_exits_1(tmp_project: ProjectBuilder, shape: str, why: str) -> None:
    """Rows 14/15 (``add.test.ts:546-598``) -- nothing to version is a hard stop.

    Upstream snapshots the three-line message (``add/index.ts:52-61``); it is asserted here as
    explicit literals rather than a syrupy snapshot, because a guarded module records no baseline
    until the implementation lands and the first ``--snapshot-update`` would bless whatever it
    emits. The third line is adapted from ``package.json`` to ``pyproject.toml``.
    """
    if shape == "single":
        root = single_package_repo(tmp_project)
        strip_version(root)
    else:
        root = monorepo(tmp_project, "pkg-a", "pkg-b")
        strip_version(root, "pkg-a")
        strip_version(root, "pkg-b")
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(root, console=console)

    assert excinfo.value.code == 1, why
    assert len(console.errors) == 1, "one collected emission, not one per package"
    error = console.errors[0]
    assert "No versionable packages found" in error
    assert "Ensure the packages to version are not ignored by the config" in error
    assert "pyproject.toml" in error
    assert "`version`" in error
    assert changeset_ids(root) == []


def test_missing_changeset_folder_exits_1_with_guidance(tmp_project: ProjectBuilder) -> None:
    """Row 20 (``add.test.ts:737-764``) -- ``.changeset/`` is a precondition, not something add
    creates.

    ``ensureChangesetFolder`` (``commands/shared.ts:7-20``) runs before any prompt and before the
    "no packages" check; the three-line message is quoted in research doc 03 section 2.2, with
    ``changeset init`` adapted to ``molt init``. Silently creating the folder would hide a
    deleted-config accident, which is exactly what the third line warns about.
    """
    tmp_project.add_package("pkg-a")
    root = tmp_project.root
    assert not (root / ".changeset").exists()
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(root / "packages" / "pkg-a", console=console, message=SUMMARY)

    assert excinfo.value.code == 1
    error = "\n".join(console.errors)
    assert "There is no .changeset folder." in error
    assert "molt init" in error


def test_adds_a_changeset_from_a_subdirectory(tmp_project: ProjectBuilder) -> None:
    """Row 19 (``add.test.ts:703-735``) -- root discovery walks up from ``cwd``.

    Upstream leans on ``getPackages(cwd)``; molt walks up to the uv workspace root
    (research doc 03 section 11.4). The changeset must land in the **root** ``.changeset/``: a
    per-package one would never be read (``website/docs/config/changeset-format.md``, "Where
    changesets live").
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    prompts = ScriptedPrompts(multiselect=[["pkg-a"], [], []], text=[SUMMARY])

    add_changeset(root / "packages" / "pkg-a", prompts=prompts)

    assert read_only_changeset(root).releases == [("pkg-a", "patch")]
    assert changeset_ids(root / "packages" / "pkg-a") == []


# ======================================================================================
# Cancellation -- cancelable() (cli-utilities.ts:14-21), research doc 03 section 10.5
# ======================================================================================

# (script, why) -- every prompt in the flow routes through the same cancelable helper.
CANCEL_CASES: Final = [
    (
        {"multiselect": [CANCEL_ANSWER]},
        "Ctrl-C at the package multiselect, before anything is chosen",
    ),
    (
        {"multiselect": [["pkg-a"], [], []], "text": [CANCEL_ANSWER]},
        "Ctrl-C at the summary prompt, after the whole selection is made",
    ),
]


@pytest.mark.parametrize(("script", "why"), CANCEL_CASES)
def test_cancelling_a_prompt_exits_0_and_writes_nothing(
    tmp_project: ProjectBuilder, script: dict[str, Any], why: str
) -> None:
    """Cancelling exits **0** -- a deliberate, documented divergence from POSIX 130.

    ``cli-utilities.ts:14-21`` prints ``Canceled...`` and calls ``process.exit(0)``; molt keeps
    the code (research doc 03 section 11.6, ``website/docs/cli/add.md`` exit-code table) because a
    user who aborts a prompt has not failed at anything, and CI never reaches an interactive
    prompt. The other half is that a cancelled run leaves **no** partial changeset behind.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    console = RecordingConsole()
    prompts = ScriptedPrompts(**script)

    code = exit_code_of(lambda: add_changeset(root, console=console, prompts=prompts))

    assert code == 0, why
    assert changeset_ids(root) == [], why
    assert console.contains("Canceled"), why


# ======================================================================================
# Flow C -- release-type flags (createChangeset.ts:148-170) and the two validators
# ======================================================================================


def test_release_type_flags_validate_unknown_package_names(tmp_project: ProjectBuilder) -> None:
    """Row 16 (``add.test.ts:599-635``) -- one message per unknown name, in flag order.

    All messages collect into a single ``log.error`` emission joined by newlines, then
    ``ExitError(1)`` (``createChangeset.ts:154-160``): a bot fixing one typo at a time would
    otherwise need three runs to see three typos.
    """
    root = monorepo(tmp_project, "pkg-a")
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(
            root,
            console=console,
            message="test",
            major=["pkg-missing-major"],
            minor=["pkg-missing-minor"],
            patch=["pkg-missing-patch"],
        )

    assert excinfo.value.code == 1
    assert len(console.errors) == 1, "one collected emission, not one per name"
    error = console.errors[0]
    for name, flag in [
        ("pkg-missing-major", "--major"),
        ("pkg-missing-minor", "--minor"),
        ("pkg-missing-patch", "--patch"),
    ]:
        assert (
            f"The package {name} is passed to the `{flag}` option "
            "but it is not found in the project." in error
        )
    assert error.index("--major") < error.index("--minor") < error.index("--patch")
    assert changeset_ids(root) == []


def test_release_type_flags_reject_one_package_under_two_types(
    tmp_project: ProjectBuilder,
) -> None:
    """Row 17 (``add.test.ts:636-667``) -- a package cannot carry two bump types.

    The flags are listed in ``--major, --minor, --patch`` order in the message regardless of the
    order they appeared on the command line (``createChangeset.ts:128-138``).
    """
    root = monorepo(tmp_project, "pkg-a")
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(root, console=console, message="test", major=["pkg-a"], minor=["pkg-a"])

    assert excinfo.value.code == 1
    assert len(console.errors) == 1
    assert (
        "The package pkg-a is passed to multiple release type options: `--major`, `--minor`. "
        "Please select only one release type for this package." in console.errors[0]
    )
    assert changeset_ids(root) == []


def test_unknown_package_names_are_not_also_reported_as_duplicates(
    tmp_project: ProjectBuilder,
) -> None:
    """Row 18 (``add.test.ts:668-701``) -- the existence check gates the duplicate check.

    ``validateDuplicatePackageNames`` intersects with the *known* package names first
    (``createChangeset.ts:118-121``), so a name that is both unknown and duplicated produces two
    "not found" errors and no duplicate error. Reporting both would tell the user to remove one
    flag when the real fix is to spell the name correctly.
    """
    root = monorepo(tmp_project, "pkg-a")
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(
            root, console=console, message="test", major=["pkg-missing"], minor=["pkg-missing"]
        )

    assert excinfo.value.code == 1
    error = console.errors[0]
    assert "multiple release type options" not in error
    for flag in ("--major", "--minor"):
        assert (
            f"The package pkg-missing is passed to the `{flag}` option "
            "but it is not found in the project." in error
        )


#: (config, package_kwargs, reason substring) -- a package the project *discovers* but does not
#: consider releasable, and the reason class the new message must name (AC-4, owner ruling
#: 2026-07-30). Distinct from `test_release_type_flags_validate_unknown_package_names`, where the
#: name matches nothing discovered at all.
NOT_RELEASABLE_CASES: Final = [
    (
        {"ignore": ["pkg-b"]},
        {},
        "ignored",
    ),
    (
        {"private_packages": {"version": False}},
        {"private": True},
        "private",
    ),
]


@pytest.mark.parametrize(("config", "package_kwargs", "reason_fragment"), NOT_RELEASABLE_CASES)
def test_a_discovered_but_not_releasable_package_explains_why(
    tmp_project: ProjectBuilder,
    config: dict[str, Any],
    package_kwargs: dict[str, Any],
    reason_fragment: str,
) -> None:
    """AC-4 (owner ruling, 2026-07-30): a name that resolves to a package the project *discovers*
    but does not consider releasable reports the package and the reason -- distinct from the
    blanket "not found in the project" message, which stays reserved for a name matching nothing
    discovered at all (see the row above).

    ``src/molt/commands/add.py::_validate`` built ``known`` from the *versionable* set only, so
    before this ruling both cases printed the same "not found" message as a genuinely unknown name
    -- true of the releasable set, false of the repository.
    """
    root = monorepo(tmp_project, "pkg-a")
    tmp_project.add_package("pkg-b", version="1.0.0", **package_kwargs)
    tmp_project.set_config(**config)
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(root, console=console, message="test", patch=["pkg-b"])

    assert excinfo.value.code == 1
    assert len(console.errors) == 1
    error = console.errors[0]
    assert "pkg-b" in error
    assert reason_fragment in error
    assert "not found in the project" not in error, "pkg-b exists; that message is for unknowns"
    assert changeset_ids(root) == []


def test_release_type_flags_write_a_changeset_without_prompting(
    tmp_project: ProjectBuilder,
) -> None:
    """molt-NEW success path for rows 16-18's flag surface (group file, molt-NEW list).

    Upstream only ever tests the *failures* of ``--major/--minor/--patch``; the fully
    non-interactive form ``--minor pkg-a --patch pkg-b -m "..."`` is documented at
    ``packages/cli/README.md:138-160`` and is the shape ``website/docs/cli/add.md`` promotes for
    Dependabot/Renovate. The empty prompt script is the assertion that nothing prompts.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    prompts = ScriptedPrompts()

    add_changeset(root, prompts=prompts, minor=["pkg-a"], patch=["pkg-b"], message=SUMMARY)

    written = read_only_changeset(root)
    assert written.releases == [("pkg-a", "minor"), ("pkg-b", "patch")]
    assert written.summary == SUMMARY
    assert prompts.calls == []


def test_package_and_bump_write_a_changeset_without_prompting(
    tmp_project: ProjectBuilder,
) -> None:
    """molt-NEW: ``--package ... --bump <type>`` (``website/docs/cli/add.md`` Options table).

    ``--bump`` is a **single** value applied to every ``--package`` selection. Source conflict:
    ``website/docs/guides/adding-a-changeset.md`` describes repeating the ``--package``/``--bump``
    pair "matched in order"; ``cli/add.md`` is the authoritative flag surface and says
    "Bump type applied to every ``--package`` selection", so that is what is pinned here.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    prompts = ScriptedPrompts()

    add_changeset(root, prompts=prompts, package=["pkg-a", "pkg-b"], bump="minor", message=SUMMARY)

    written = read_only_changeset(root)
    assert written.releases == [("pkg-a", "minor"), ("pkg-b", "minor")]
    assert prompts.calls == []


def test_package_flag_without_bump_is_a_hard_error(tmp_project: ProjectBuilder) -> None:
    """AC-2 (owner ruling, 2026-07-30): ``--package`` names *which* packages, ``--bump`` says *how
    much*; naming packages with ``--package`` and no ``--bump`` is refused rather than falling into
    the interactive bump prompts for exactly those packages.
    """
    root = monorepo(tmp_project, "pkg-a")
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(root, console=console, package=["pkg-a"])

    assert excinfo.value.code == 1
    assert len(console.errors) == 1
    assert "--bump" in console.errors[0]
    assert changeset_ids(root) == []


# (project name, flag spelling, why) -- PEP 503 says these are all the same package.
PEP503_CASES: Final = [
    ("acme-core", "Acme_Core", "case and separator differences are erased by normalization"),
    ("Acme_Core", "acme-core", "normalization applies to the project side too"),
    ("acme.core", "acme-core", "a dot is a separator like - and _"),
]


@pytest.mark.parametrize(("project_name", "flag_name", "why"), PEP503_CASES)
def test_flag_package_names_match_pep503_normalized(
    tmp_project: ProjectBuilder, project_name: str, flag_name: str, why: str
) -> None:
    """molt-NEW: name matching normalizes both sides (research README section 4.5).

    Python has a flat, normalized namespace, so ``Acme_Core`` and ``acme-core`` are one package.
    Matching at *lookup* time (not parse time) is the pinned rule -- the literal spelling the
    author typed is preserved in the file, and only the comparison is normalized. Without this the
    "not found in the project" validator would reject a perfectly valid name.
    """
    root = monorepo(tmp_project, project_name)
    tmp_project.add_package("other-pkg", version="1.0.0")

    add_changeset(root, patch=[flag_name], message=SUMMARY)

    written = read_only_changeset(root)
    assert len(written.releases) == 1, why
    assert normalize(written.releases[0][0]) == normalize(project_name), why
    assert written.releases[0][1] == "patch", why


# ======================================================================================
# --message / --empty (rows 5-9), research doc 03 sections 2.9 and 10.15
# ======================================================================================


def test_message_skips_the_summary_prompt_but_keeps_the_bump_selection(
    tmp_project: ProjectBuilder,
) -> None:
    """Row 6 (``add.test.ts:263-288``) -- ``-m`` replaces the summary, not the whole flow.

    ``createChangeset.ts:273-278`` returns early with ``optionsFromCli.message`` *after* the bump
    prompts have run, so the select still fires and neither the text prompt nor the editor does.
    """
    root = single_package_repo(tmp_project, "single-package", "1.0.0")
    prompts = ScriptedPrompts(select=["minor"])

    add_changeset(root, prompts=prompts, message="a message from the flag")

    written = read_only_changeset(root)
    assert written.releases == [("single-package", "minor")]
    assert written.summary == "a message from the flag"
    assert prompts.kinds == ["select"]


def test_empty_message_is_honoured_and_still_skips_the_prompt(
    tmp_project: ProjectBuilder,
) -> None:
    """Row 7 (``add.test.ts:289-313``) / research doc 03 section 10.15.

    ``-m ""`` is a *provided* empty summary and is distinct from ``-m`` being unset: only
    ``undefined`` triggers prompting (``createChangeset.ts:273``). Getting this wrong turns every
    scripted empty message into a hung CI job waiting on ``$EDITOR``.
    """
    root = single_package_repo(tmp_project, "single-package", "1.0.0")
    prompts = ScriptedPrompts(select=["minor"])

    add_changeset(root, prompts=prompts, message="")

    written = read_only_changeset(root)
    assert written.summary == ""
    assert written.releases == [("single-package", "minor")]
    assert prompts.kinds == ["select"]


def test_message_in_a_monorepo_skips_only_the_summary_prompt(
    tmp_project: ProjectBuilder,
) -> None:
    """Row 8 (``add.test.ts:315-348``) -- the same skip contract in the monorepo shape.

    The three multiselects still run; only the summary text prompt and the editor are skipped.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    prompts = ScriptedPrompts(multiselect=[["pkg-a"], [], []])

    add_changeset(root, prompts=prompts, message=SUMMARY)

    written = read_only_changeset(root)
    assert written.releases == [("pkg-a", "patch")]
    assert written.summary == SUMMARY
    assert prompts.kinds == ["multiselect", "multiselect", "multiselect"]


def test_empty_flag_writes_an_empty_changeset_with_no_prompts(
    tmp_project: ProjectBuilder,
) -> None:
    """Row 5 (``add.test.ts:236-262``) / research doc 03 section 2.9.

    ``--empty`` short-circuits everything (``add/index.ts:66-71``): no changed-package detection,
    no prompts, no summary panel. It is the CI-safe way to record "no release needed" and satisfy
    the ``status`` gate (``website/docs/guides/adding-a-changeset.md``).
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    prompts = ScriptedPrompts()
    git = GitWithChanges(changed_files=["packages/pkg-a/a.py"])

    add_changeset(root, prompts=prompts, git=git, empty=True)

    written = read_only_changeset(root)
    assert written.releases == []
    assert written.summary == ""
    assert prompts.calls == []
    assert git.refs == [], "--empty short-circuits before changed-package detection"


def test_empty_flag_combines_with_message(tmp_project: ProjectBuilder) -> None:
    """Row 9 (``add.test.ts:349-379``) -- ``--empty -m "..."`` records why nothing releases."""
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    prompts = ScriptedPrompts()

    add_changeset(root, prompts=prompts, empty=True, message="no release needed")

    written = read_only_changeset(root)
    assert written.releases == []
    assert written.summary == "no release needed"
    assert prompts.calls == []


# ======================================================================================
# --stdin (molt-NEW, website/docs/cli/add.md "Non-interactive (stdin)")
# ======================================================================================


def test_stdin_payload_writes_a_changeset_without_prompting(
    tmp_project: ProjectBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """molt-NEW: the documented JSON payload, straight from a pipe.

    Schema from ``website/docs/cli/add.md``:
    ``{"releases": [{"name": ..., "bump": ...}], "summary": ...}``. Source conflict:
    ``website/docs/guides/adding-a-changeset.md`` shows a mapping form
    (``{"releases": {"pip-audit": "patch"}}``); ``cli/add.md`` is authoritative for the payload
    surface, so the list form is what is pinned. Flagged for the owner as a docs fix.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    feed_stdin(
        monkeypatch,
        json.dumps(
            {
                "releases": [
                    {"name": "pkg-a", "bump": "minor"},
                    {"name": "pkg-b", "bump": "patch"},
                ],
                "summary": "Add streaming export.",
            }
        ),
    )
    prompts = ScriptedPrompts()

    add_changeset(root, prompts=prompts, stdin=True)

    written = read_only_changeset(root)
    assert written.releases == [("pkg-a", "minor"), ("pkg-b", "patch")]
    assert written.summary == "Add streaming export."
    assert prompts.calls == []


# (payload, why)
BAD_STDIN_CASES: Final = [
    ("", "an empty pipe is not a payload"),
    ("not json at all", "a non-JSON body must not be guessed at"),
    ('{"releases": "pkg-a", "summary": "x"}', "releases must be a list of objects"),
    ('{"releases": [{"name": "pkg-a"}], "summary": "x"}', "a release without a bump is incomplete"),
    (
        '{"releases": [{"name": "pkg-a", "bump": "huge"}], "summary": "x"}',
        "the bump must be one of major/minor/patch/none",
    ),
]


@pytest.mark.parametrize(("payload", "why"), BAD_STDIN_CASES)
def test_stdin_malformed_payload_exits_1(
    tmp_project: ProjectBuilder, monkeypatch: pytest.MonkeyPatch, payload: str, why: str
) -> None:
    """molt-NEW: a malformed payload fails loudly instead of writing a half-changeset.

    ``website/docs/cli/add.md``, "Non-interactive (stdin)": ``molt add --stdin`` "reads a JSON
    payload from standard input and writes the changeset with no TTY", and its option table
    (``add.md:62``) documents the flag as exactly that. ``--stdin`` is therefore the bot-facing
    surface, so a wrong payload has to surface as a non-zero exit in the bot's log, not as a
    silently empty changeset. The payload *grammar* -- ``releases[]`` of ``{name, bump}`` plus
    ``summary`` -- is the one shown in the same section's example (``add.md:119``); each row below
    breaks exactly one part of it.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    feed_stdin(monkeypatch, payload)
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(root, console=console, stdin=True)

    assert excinfo.value.code == 1, why
    assert console.errors, why
    assert changeset_ids(root) == [], why


def test_stdin_unknown_package_name_is_rejected(
    tmp_project: ProjectBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """molt-NEW: the payload goes through the same existence check as the flags (row 16).

    ``website/docs/cli/add.md``, "Non-interactive (stdin)", describes ``--stdin`` as one more way
    to spell the same changeset, not a second grammar -- so one validator, three drive modes.
    Otherwise ``--stdin`` becomes the way to smuggle a typo into ``.changeset/`` and fail much
    later, at ``molt version``.
    """
    root = monorepo(tmp_project, "pkg-a")
    feed_stdin(
        monkeypatch,
        json.dumps({"releases": [{"name": "pkg-missing", "bump": "minor"}], "summary": "x"}),
    )
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(root, console=console, stdin=True)

    assert excinfo.value.code == 1
    error = "\n".join(console.errors)
    assert "pkg-missing" in error
    assert "not found in the project" in error
    assert changeset_ids(root) == []


# ======================================================================================
# --non-interactive, --dry-run, --open, and the commit hook
# ======================================================================================


def test_non_interactive_without_a_selection_exits_1_without_prompting(
    tmp_project: ProjectBuilder,
) -> None:
    """molt-NEW (research doc 03 section 11.7 item 7): the CI escape hatch upstream lacks.

    Without a flag or a piped payload there is nothing to write and nowhere to ask, so the run
    fails naming the inputs that would have completed it. The empty prompt script is the "never
    prompts" assertion -- a prompt here raises instead of hanging a build for six hours
    (research doc 03 section 9.3: clack in a non-TTY either throws or never resolves).
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    prompts = ScriptedPrompts()
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(root, console=console, prompts=prompts, non_interactive=True)

    assert excinfo.value.code == 1
    assert prompts.calls == []
    error = "\n".join(console.errors)
    assert "non-interactive" in error.lower()
    assert any(flag in error for flag in ("--package", "--major", "--stdin", "--empty"))
    assert changeset_ids(root) == []


def test_non_interactive_without_a_message_exits_1_naming_it(
    tmp_project: ProjectBuilder,
) -> None:
    """molt-NEW: a complete package selection with no summary is still incomplete.

    ``website/docs/cli/add.md``: without ``--message`` molt still prompts for the summary, and
    ``--non-interactive`` "Exits non-zero if input is missing". ``-m ""`` is the documented way to
    ask for an empty summary, so falling back to one silently would make that flag meaningless.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    prompts = ScriptedPrompts()
    console = RecordingConsole()

    with pytest.raises(ExitError) as excinfo:
        add_changeset(
            root,
            console=console,
            prompts=prompts,
            non_interactive=True,
            package=["pkg-a"],
            bump="patch",
        )

    assert excinfo.value.code == 1
    assert prompts.calls == []
    assert "--message" in "\n".join(console.errors)
    assert changeset_ids(root) == []


def test_dry_run_prints_the_changeset_and_writes_nothing(tmp_project: ProjectBuilder) -> None:
    """molt-NEW (``website/docs/cli/add.md``: "Print the changeset that would be written").

    ``--dry-run`` is only useful if the preview is specific enough to check, so the package, its
    bump and the summary all have to appear -- and ``.changeset/`` must be untouched.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    console = RecordingConsole()

    code = exit_code_of(
        lambda: add_changeset(root, console=console, minor=["pkg-a"], message=SUMMARY, dry_run=True)
    )

    assert code == 0
    assert changeset_ids(root) == []
    output = console.text()
    assert "pkg-a" in output
    assert "minor" in output
    assert SUMMARY in output


def test_commit_config_stages_and_commits_the_changeset(tmp_project: ProjectBuilder) -> None:
    """Row 4 (``add.test.ts:190-235``), **adapted** to the injected git seam.

    Upstream configures ``commit: [commitFn, null]`` and asserts ``git log -1`` contains
    ``docs(changeset): summary message mock``. molt takes ``commit`` as a config boolean
    (``website/docs/config/options.md``) and drives a git seam, so the assertion moves to the
    recorded ``add``/``commit`` calls: the changeset is staged **and** committed, in that order
    (``add/index.ts:117-125``). The message wording is molt's own (an Adapt), so only the summary
    is asserted inside it -- upstream's template is ``docs(changeset): <summary>``
    (``commit/index.ts:6-10``).
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    tmp_project.set_config(commit=True)
    git = FakeGit()

    add_changeset(root, git=git, patch=["pkg-a"], message=SUMMARY)

    written = read_only_changeset(root)
    staged = [path.replace("\\", "/") for path in git.added]
    assert len(staged) == 1
    assert staged[0].endswith(f".changeset/{written.id}.md")
    assert len(git.commits) == 1
    assert SUMMARY in git.commits[0]
    assert [call.name for call in git.calls] == ["add", "commit"]


def test_no_commit_without_the_config(tmp_project: ProjectBuilder) -> None:
    """``commit`` defaults to ``false`` (``website/docs/config/options.md``), so add stages nothing.

    ``getCommitFunctions`` returns ``[{}, null]`` for a falsy ``config.commit``
    (``add/index.ts:109-113``), so the ``if (getAddMessage)`` git block at ``:117-125`` never runs.
    Committing by default would surprise anyone running ``molt add`` mid-review with unrelated
    staged work.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    git = FakeGit()

    add_changeset(root, git=git, patch=["pkg-a"], message=SUMMARY)

    assert git.calls == []


def test_open_launches_the_editor_after_the_success_message(tmp_project: ProjectBuilder) -> None:
    """Research doc 03 section 10.24 (``add/index.ts:168-170``) -- ``--open`` fires last.

    The file is written and reported *before* the editor is launched, and the launch is
    fire-and-forget: opening an editor must never be able to lose the changeset. The ordering is
    captured with a shared timeline because neither shared double records a global call order.

    Asserted as a **relative** order, not as ``timeline == ["success", "editor"]``: the exact-list
    form additionally forbids any second ``console.success`` anywhere in the flow, which is a
    claim about how chatty ``add`` is and has nothing to do with ``--open``.
    ``add/index.ts:126-166`` builds ``finalLogMessageLines`` and emits it as one block, but that is
    upstream's choice of emission granularity, not a molt contract -- nothing in
    ``website/docs/cli/add.md`` pins it.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    timeline: list[str] = []
    console = TimelineConsole(timeline)
    prompts = TimelinePrompts(timeline, editor=[""])

    add_changeset(
        root, console=console, prompts=prompts, patch=["pkg-a"], message=SUMMARY, open=True
    )

    assert read_only_changeset(root).releases == [("pkg-a", "patch")]
    assert timeline.count("editor") == 1, f"--open launches the editor exactly once: {timeline}"
    assert "success" in timeline, f"the changeset is reported before it is opened: {timeline}"
    assert timeline.index("success") < timeline.index("editor"), (
        f"--open fires after the success message (add/index.ts:168-170): {timeline}"
    )


# ======================================================================================
# The guided flow over the REAL prompt adapter (gap AC-1) -- added rows only
# ======================================================================================
#
# Everything above drives an injected `ScriptedPrompts`, which is why `AC-1` -- the shipped
# `Prompts` implementation raising `NotImplementedError` -- stayed invisible to this suite for two
# changes. The rows below pass **no** `prompts=` argument at all, so `molt.commands.add.run` builds
# the real `molt.ui.prompts.QuestionaryPrompts` and the only thing doubled is the prompt library
# underneath it (`tests/cli/fake_questionary.py`).
#
# Nothing above this line is edited, renamed, reordered or re-parametrized: every pre-existing row
# stays green untouched, which is this change's regression contract.


class GitThatCannotDetect(GitWithChanges):
    """A git double whose changed-package query fails, for gap ``AC-9``.

    The real failure it models is ordinary: a base branch that was never fetched in a shallow CI
    clone, or a ``base_branch`` with a typo in it.
    """

    def get_changed_packages_since_ref(self, ref: str, **kwargs: Any) -> list[str]:
        del kwargs
        self.calls.append(GitCall("get_changed_packages_since_ref", (ref,)))
        raise GitError(128, f"fatal: bad revision '{ref}'")


@pytest.fixture
def real_prompts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make the real adapter usable in-process, and leave no process-global behind.

    Requested explicitly by the new rows rather than made autouse: an autouse fixture in this
    module would change the environment every pre-existing row runs in, and those must stay exactly
    as they were.

    Two guards, and the rows are order-dependent garbage without them. The ``--non-interactive``
    flag is a module global written once per run by the CLI shell, so an earlier test that
    dispatched through ``molt.cli`` leaves it set. And under pytest ``sys.stdin`` is
    ``DontReadFromInput``, whose ``isatty()`` is ``False``, so the adapter's terminal probe has to
    be pointed at a terminal or every row takes the refusal path (design D5, seam note).
    """
    from molt.ui import prompts as adapter

    adapter.set_non_interactive(False)
    monkeypatch.setattr(adapter, "_stdin_is_a_terminal", lambda: True)
    yield
    adapter.set_non_interactive(False)


def guided_run(
    root: Path,
    *,
    console: RecordingConsole,
    git: FakeGit,
    **options: Any,
) -> None:
    """Call ``molt.commands.add.run`` with **no** ``prompts=``, so the shipped adapter is built."""
    run(cwd=root, console=console, git=git, **options)


def test_a_guided_run_writes_a_changeset_with_no_prompt_object(
    tmp_project: ProjectBuilder, real_prompts: None
) -> None:
    """The whole monorepo interview, end to end, with nothing injected at the prompt seam.

    **This is the row that would have caught ``AC-1``**: every other row in this file supplies a
    prompt double, so a shipped implementation that raises ``NotImplementedError`` passed them all.

    It also exercises the group header for real -- selecting ``changed packages`` selects both of
    its members (design D4) -- then the major pass, the minor pass, the implicit patch for
    everything left (``createChangeset.ts:234-253``) and the summary prompt.
    """
    del real_prompts
    root = monorepo(tmp_project, "pkg-a", "pkg-b", "pkg-c")
    console = RecordingConsole()
    git = GitWithChanges(changed_files=["packages/pkg-a/a.py", "packages/pkg-b/b.py"])
    library = FakeQuestionary(
        checkbox=[pick("changed packages"), [], pick("pkg-a")], text=[SUMMARY]
    )

    with pytest.MonkeyPatch.context() as patch:
        library.install(patch)
        guided_run(root, console=console, git=git)

    written = read_only_changeset(root)
    assert sorted(written.releases) == [("pkg-a", "minor"), ("pkg-b", "patch")]
    assert written.summary == SUMMARY
    assert library.kinds == ["checkbox", "checkbox", "checkbox", "text"]
    assert library.remaining == {}


# (library script, why) -- every question in the guided flow routes through the same contract.
GUIDED_CANCEL_CASES: Final = [
    ({"checkbox": [None]}, "Ctrl-C at the package multiselect, before anything is chosen"),
    ({"checkbox": [pick("  pkg-a"), None]}, "Ctrl-C at the major multiselect"),
    ({"checkbox": [pick("  pkg-a"), [], None]}, "Ctrl-C at the minor multiselect"),
    (
        {"checkbox": [pick("  pkg-a"), pick("pkg-a")], "confirm": [None]},
        "Ctrl-C at the first-major confirmation",
    ),
    (
        {"checkbox": [pick("  pkg-a"), [], []], "text": [None]},
        "Ctrl-C at the summary prompt, after the whole selection is made",
    ),
]


@pytest.mark.parametrize(("script", "why"), GUIDED_CANCEL_CASES)
def test_cancelling_at_every_interactive_step_writes_nothing(
    tmp_project: ProjectBuilder, real_prompts: None, script: dict[str, Any], why: str
) -> None:
    """Cancelling exits **0** and leaves ``.changeset/`` empty, at every question in the flow.

    Exit 0 is a deliberate, documented divergence from POSIX 130 (research doc 03 section 11.6):
    a user who aborted a prompt has not failed at anything. The library reports a cancellation by
    returning ``None`` from ``Question.ask()``, which the shared ``is_cancel`` predicate already
    accepts -- so this is also the row that proves ``.ask()``, not ``unsafe_ask()``, is what the
    adapter drives.

    A second, wider table beside :data:`CANCEL_CASES` rather than an extension of it, so no pinned
    row's identity changes. The packages are pre-1.0 so the first-major confirmation is reachable.
    """
    del real_prompts
    root = monorepo(tmp_project, "pkg-a", "pkg-b", version="0.1.0")
    console = RecordingConsole()
    git = GitWithChanges(changed_files=["packages/pkg-a/a.py"])
    library = FakeQuestionary(**script)

    with pytest.MonkeyPatch.context() as patch:
        library.install(patch)
        code = exit_code_of(lambda: guided_run(root, console=console, git=git))

    assert code == 0, why
    assert changeset_ids(root) == [], why
    assert console.contains("Canceled"), why


def test_declining_a_first_major_in_the_guided_flow_falls_through_to_minor(
    tmp_project: ProjectBuilder, real_prompts: None
) -> None:
    """The interactive path keeps upstream's fall-through (``createChangeset.ts:197-209``).

    Owner ruling ``AC-3`` (2026-07-30) made a declined **flag-selected** first major abort the run;
    it deliberately left the interactive path alone, because the interactive path always has a next
    question to ask. A declined package is not dropped -- it stays in ``pkgsLeftToGetBumpTypeFor``
    and the minor prompt offers it again, so the user gets a second choice instead of losing the
    changeset.
    """
    del real_prompts
    root = monorepo(tmp_project, "pkg-a", "pkg-b", version="0.1.0")
    console = RecordingConsole()
    git = GitWithChanges(changed_files=["packages/pkg-a/a.py"])
    library = FakeQuestionary(
        checkbox=[pick("  pkg-a"), pick("pkg-a"), pick("pkg-a")],
        confirm=[False],
        text=[SUMMARY],
    )

    with pytest.MonkeyPatch.context() as patch:
        library.install(patch)
        guided_run(root, console=console, git=git)

    assert library.of("checkbox")[2].titles == ["pkg-a"], (
        "a declined first major is offered again at the minor selection, not dropped"
    )
    assert read_only_changeset(root).releases == [("pkg-a", "minor")]


def test_with_no_changed_packages_the_one_group_keeps_its_own_label(
    tmp_project: ProjectBuilder, real_prompts: None
) -> None:
    """The single group offered is labelled ``unchanged packages``. **Closes gap ``AC-11``.**

    ``test_without_changed_packages_only_one_group_is_offered`` asserts the group's members and the
    group count but never its label, so calling the one group ``packages`` was untested. It is a
    label the user reads, and it is the only thing in that question that says why every package is
    listed together.
    """
    del real_prompts
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    console = RecordingConsole()
    git = GitWithChanges()
    library = FakeQuestionary(checkbox=[pick("  pkg-a"), [], []], text=[SUMMARY])

    with pytest.MonkeyPatch.context() as patch:
        library.install(patch)
        guided_run(root, console=console, git=git)

    assert library.of("checkbox")[0].titles == ["unchanged packages", "  pkg-a", "  pkg-b"]
    assert read_only_changeset(root).releases == [("pkg-a", "patch")]


def test_a_failed_detection_is_reported_and_the_run_continues(
    tmp_project: ProjectBuilder, real_prompts: None
) -> None:
    """A git failure during changed-package detection warns and degrades. **Closes gap ``AC-9``.**

    Detection only decides which group a package is listed under, never what a changeset may
    contain, so a fresh repository or a shallow CI clone must not stop the run
    (``website/docs/cli/add.md``, the ``--since`` row). But silence made a mistyped ``base_branch``
    look exactly like a branch with no changes; upstream reports it (``add/index.ts:80-89``) and
    molt did not. The reference is named because it is the thing the user has to fix.
    """
    del real_prompts
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    tmp_project.set_config(base_branch="trunk")
    console = RecordingConsole()
    git = GitThatCannotDetect()
    library = FakeQuestionary(checkbox=[pick("  pkg-a"), [], []], text=[SUMMARY])

    with pytest.MonkeyPatch.context() as patch:
        library.install(patch)
        guided_run(root, console=console, git=git)

    assert len(console.warnings) == 1, console.warnings
    assert "trunk" in console.warnings[0], "the warning names the reference that could not be read"
    assert git.refs == ["trunk"], "detection is still attempted"
    assert library.of("checkbox")[0].titles == ["unchanged packages", "  pkg-a", "  pkg-b"]
    assert read_only_changeset(root).releases == [("pkg-a", "patch")]


def test_the_flag_path_issues_no_detection_warning(tmp_project: ProjectBuilder) -> None:
    """``AC-9``'s discriminator: a warning emitted unconditionally would pass the row above.

    The flag path does no changed-package detection at all (design D7), so there is nothing to
    fail and nothing to report -- even with a git double that cannot answer the query.
    """
    root = monorepo(tmp_project, "pkg-a", "pkg-b")
    console = RecordingConsole()
    git = GitThatCannotDetect()

    add_changeset(root, console=console, git=git, patch=["pkg-a"], message=SUMMARY)

    assert git.refs == [], "no detection runs on the flag path"
    assert console.warnings == []
    assert read_only_changeset(root).releases == [("pkg-a", "patch")]
