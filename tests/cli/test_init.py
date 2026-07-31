"""Conformance tests for ``molt init``.

Ports the 11 rows of ``packages/cli/src/commands/init/__tests__/command.test.ts`` catalogued in
``roadmap/research/test-suite/06-cli-commands.md`` (init section), against
``roadmap/research/changesets-03-cli-and-ux.md`` section 7 -- 7.1 (behaviour), 7.2 (the interactive
prompt sequence), 7.3/7.4 (the generated files) -- plus gotchas 10.16 and 10.17. Website docs:
``website/docs/cli/init.md``, ``website/docs/config/config-file.md`` and
``website/docs/config/options.md``.

What ``init`` *writes* is asserted here; what the keys *mean* is pinned by
``tests/config/test_parse.py`` (the config schema, defaults, aliases and dropped keys). This module
does not re-litigate any of that -- it cites it.

Deviations from the group file, all deliberate
----------------------------------------------
- **Module path.** ``molt.commands.init``, not ``molt.cli.commands.init``
  (``openspec/changes/adopt-typer-cli-shell/design.md`` D7 + ``tasks.md`` 1.2 + research doc 03
  section 11.5). ``MILESTONES.md`` and the phase brief say otherwise and are superseded.
- **Config location.** Upstream writes ``.changeset/config.json``. molt writes a ``[tool.molt]``
  table into the root ``pyproject.toml`` by default and ``.molt/config.json`` only when a standalone
  file is preferred (``cli/init.md`` step 3; ``config/config-file.md``, research README section 6
  decision 7). Rows 1/3/4 adapt accordingly.
- **``access`` is dropped.** npm's public/restricted publish access has no PyPI analogue
  (``config/options.md``, "Dropped from changesets"), so upstream's ``askList`` step and the
  ``access`` key of rows 9 and 11 are dropped, not ported.
- **Prompt order** follows ``cli/init.md`` ("base branch, changelog integration, whether to
  auto-commit"), not upstream's (GitHub first, base branch last). See ``test_prompt_sequence``.
- **Gotcha 10.16 is fixed.** Upstream refuses to re-run once ``config.json`` exists yet happily
  creates a missing ``README.md`` when only *that* is absent -- the asymmetry means an existing
  config permanently suppresses the README. molt's contract is "creates only the pieces that are
  missing" (``cli/init.md``), which is symmetric.
- **Gotcha 10.17 does not apply.** Upstream's docs promise a commented config and the generator
  emits comment-free JSON. molt's default target is TOML, which takes comments, and no molt doc
  promises them -- so there is nothing to port and nothing to fix; recorded as a drop.

The ``.molt/config.json`` *branch* is deliberately not exercised: no molt doc states the trigger for
choosing it (``cli/init.md`` says "if you prefer", and its option table lists no flag). Only the
default is pinned, plus the negative assertion that the JSON file is not created.
"""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Any

import pytest
from tests.cli.fake_cli import RecordingConsole, ScriptedPrompts

pytest.importorskip("molt.commands.init", reason="build step 6 - `molt init` is a TDD target")
pytest.importorskip("molt.errors", reason="build step 6 - molt.errors lands with the cli shell")

from molt.commands.init import run
from molt.errors import ExitError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

pytestmark = [pytest.mark.functional]


# --------------------------------------------------------------------------------------
# Local project scaffolding
#
# Written with `write_bytes` on purpose: `ProjectBuilder` writes its TOML through
# `Path.write_text`, which applies newline translation and so lands CRLF on Windows. Row 6 asserts
# LF *bytes*, and a fixture that pre-seeded CRLF would make that row untestable here.
# --------------------------------------------------------------------------------------

SINGLE_PACKAGE_PYPROJECT = """\
[project]
name = "acme"
version = "0.1.0"
requires-python = ">=3.11"
"""

WORKSPACE_PYPROJECT = """\
[project]
name = "workspace-root"
version = "0.0.0"
requires-python = ">=3.11"

[tool.uv.workspace]
members = ["packages/*"]
"""

MEMBER_PYPROJECT = """\
[project]
name = "{name}"
version = "1.0.0"
dependencies = []
"""

#: Upstream's fixed key order (`init/index.ts:59-70`, pinned by `command.test.ts:295-306`) is
#: `$schema, baseBranch, access, format, changelog, commit, ignore, fixed, linked,
#: updateInternalDependencies`. molt's is that list with `$schema` dropped (it is a JSON-editor
#: affordance with no TOML meaning -- `config/json-schema.md`), `access` dropped (no PyPI analogue),
#: and the survivors respelled snake_case. Row 11 asserts the *relative* order of whatever keys are
#: written rather than demanding the full set, so `init` stays free to omit keys that are already
#: their default; the ordering is the part that must be deterministic.
#:
#: The list continues past upstream's eight because row 11 also asserts `set(keys) <=
#: set(MOLT_KEY_ORDER)` -- "no unexpected keys" -- and stopping at eight would reject a *correct*
#: `init`. `tests/config/test_parse.py` (the config-schema anchor) and `config/options.md` pin
#: seven more keys, and `cli/init.md` step 2 ("detect the ecosystem backend and workspace layout")
#: makes `init` writing `ecosystem` very plausible. Upstream's order comes first; the rest are
#: appended in schema order, which is the position they would take in a generated file.
MOLT_KEY_ORDER: tuple[str, ...] = (
    # Upstream's generator order (`init/index.ts:59-70`), minus `$schema` and `access`.
    "base_branch",
    "format",
    "changelog",
    "commit",
    "ignore",
    "fixed",
    "linked",
    "update_internal_dependencies",
    # In upstream's *schema* but never written by its generator (`config.ts`); molt keeps all three
    # (`tests/config/test_parse.py`: `changed_file_patterns == ["**"]`, `private_packages ==
    # {"version": True}`, `snapshot == {...}`).
    "changed_file_patterns",
    "private_packages",
    "snapshot",
    # Promoted out of `___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH` (research doc 03 row 28).
    "update_internal_dependents",
    # molt-NEW: no changesets analogue at all (`config/options.md`).
    "bump_workspace_sources_only",
    "ecosystem",
    "forge",
)


#: Prompt script for a run that takes every default: empty base branch, no GitHub changelog, no
#: auto-commit. Mirrors upstream's `beforeEach` defaults (`command.test.ts:18-47`) minus `access`.
def default_prompts() -> ScriptedPrompts:
    return ScriptedPrompts(text=[""], confirm=[False, False])


def make_project(root: Path, *, pyproject: str = SINGLE_PACKAGE_PYPROJECT) -> Path:
    """Write a project root with LF-only bytes and return it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_bytes(pyproject.encode("utf-8"))
    return root


def make_workspace(root: Path, members: Sequence[str]) -> Path:
    make_project(root, pyproject=WORKSPACE_PYPROJECT)
    for name in members:
        member = root / "packages" / name
        member.mkdir(parents=True, exist_ok=True)
        (member / "pyproject.toml").write_bytes(MEMBER_PYPROJECT.format(name=name).encode("utf-8"))
    return root


def molt_table(root: Path) -> dict[str, Any]:
    """The ``[tool.molt]`` table of the root ``pyproject.toml`` (``{}`` when absent).

    ``tomllib`` preserves file order in the returned dict, which is what row 11 keys on.
    """
    data = tomllib.loads((root / "pyproject.toml").read_bytes().decode("utf-8"))
    table = data.get("tool", {}).get("molt")
    return dict(table) if isinstance(table, dict) else {}


def has_molt_table(root: Path) -> bool:
    return bool(molt_table(root))


def changeset_readme(root: Path) -> Path:
    return root / ".changeset" / "README.md"


# --------------------------------------------------------------------------------------
# Rows 1-4 - scaffolding from scratch
# --------------------------------------------------------------------------------------


def test_scaffolds_config_and_changeset_folder_from_scratch(
    tmp_path: Path, console: RecordingConsole
) -> None:
    """Row 1 (`command.test.ts:53-69`), adapted to molt's layout.

    Upstream asserts ``.changeset/README.md`` and ``.changeset/config.json`` both exist. molt's
    config goes into ``[tool.molt]`` (``cli/init.md`` step 3) and ``.changeset/`` gets a short
    ``README.md`` explaining the folder (step 4).
    """
    root = make_project(tmp_path / "project")
    assert not has_molt_table(root)
    assert not changeset_readme(root).exists()

    run(cwd=root, console=console, prompts=default_prompts())

    assert has_molt_table(root)
    assert changeset_readme(root).is_file()
    assert changeset_readme(root).read_bytes().strip(), "the README is not empty"


def test_config_goes_into_pyproject_not_a_standalone_json_file(
    tmp_path: Path, console: RecordingConsole
) -> None:
    """``[tool.molt]`` is the default home; ``.molt/config.json`` is opt-in only.

    ``cli/init.md`` step 3: "By default molt writes a ``[tool.molt]`` table into the root
    ``pyproject.toml``. If you prefer a standalone file, molt writes ``.molt/config.json``
    instead." Creating both would be the one state ``load_config`` refuses outright
    (``config/config-file.md``, "One source, never merged").
    """
    root = make_project(tmp_path / "project")

    run(cwd=root, console=console, prompts=default_prompts())

    assert has_molt_table(root)
    assert not (root / ".molt" / "config.json").exists()


def test_defaults_to_the_current_working_directory(
    tmp_path: Path, console: RecordingConsole, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 2 (`command.test.ts:71-91`): no ``cwd`` argument means ``Path.cwd()``.

    Upstream spies on ``process.cwd()``; the Python equivalent is a default argument, so this
    asserts the observable consequence instead of the call. ``--cwd`` is a global flag on every
    command (``cli/init.md`` options table), and its default is the working directory.
    """
    root = make_project(tmp_path / "project")
    monkeypatch.chdir(root)

    run(console=console, prompts=default_prompts())

    assert has_molt_table(root)
    assert changeset_readme(root).is_file()


def test_writes_the_documented_defaults(tmp_path: Path, console: RecordingConsole) -> None:
    """Row 3 (`command.test.ts:93-110`), adapted: molt's schema, not changesets'.

    Upstream compares the written file to ``defaultWrittenConfig``. molt's equivalents are pinned by
    ``tests/config/test_parse.py`` (``base_branch = "main"``, ``commit`` false, ``format`` false, a
    normalized changelog generator ref); what is asserted here is only that ``init`` *writes* them.
    ``access`` must never appear -- it is a dropped key (``config/options.md``).

    ``format`` is required, not merely allowed, and that is load-bearing for the row-11 ordering
    test below. Without it the written set is ``base_branch``/``changelog``/``commit``, whose fixed
    order happens to be *also* its alphabetical order -- so an ``init`` that sorted its keys would
    pass the determinism row vacuously. With ``format`` in the set the two orders diverge
    (``base_branch, format, changelog`` vs ``base_branch, changelog, format``).
    """
    root = make_project(tmp_path / "project")

    run(cwd=root, console=console, prompts=default_prompts())

    written = molt_table(root)
    assert written["base_branch"] == "main"
    assert written["commit"] is False
    assert written["format"] is False, "molt defaults formatting OFF (tests/config/test_parse.py)"
    assert "changelog" in written
    assert "access" not in written
    assert "$schema" not in written, "a TOML table has no JSON-schema affordance"


def test_fills_an_existing_but_empty_changeset_folder(
    tmp_path: Path, console: RecordingConsole
) -> None:
    """Row 4 (`command.test.ts:112-128`): an empty ``.changeset/`` is not "already initialized"."""
    root = make_project(tmp_path / "project")
    (root / ".changeset").mkdir()

    run(cwd=root, console=console, prompts=default_prompts())

    assert has_molt_table(root)
    assert changeset_readme(root).is_file()


# --------------------------------------------------------------------------------------
# Rows 5-7 + gotcha 10.16 - idempotency and never clobbering
# --------------------------------------------------------------------------------------


def test_never_clobbers_an_existing_readme(tmp_path: Path, console: RecordingConsole) -> None:
    """Row 5 (`command.test.ts:130-149`): the README survives verbatim; the config is created."""
    root = make_project(tmp_path / "project")
    readme = changeset_readme(root)
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_bytes(b"custom readme content\n")

    run(cwd=root, console=console, prompts=default_prompts())

    assert has_molt_table(root)
    assert readme.read_bytes() == b"custom readme content\n"


def test_an_existing_config_is_left_untouched(tmp_path: Path, console: RecordingConsole) -> None:
    """Row 7 (`command.test.ts:170-189`): ``init`` is idempotent.

    Asserted on bytes, not on the parsed table: re-running must not reformat, reorder or re-quote a
    file the user owns. ``cli/init.md``: "If a config already exists, molt leaves it untouched and
    reports that the project is already initialized." The empty ``ScriptedPrompts`` is part of the
    assertion -- any prompt at all would raise ``PromptExhausted``.
    """
    root = make_project(
        tmp_path / "project",
        pyproject=SINGLE_PACKAGE_PYPROJECT + "\n[tool.molt]\nchangelog = false\n",
    )
    before = (root / "pyproject.toml").read_bytes()

    run(cwd=root, console=console, prompts=ScriptedPrompts())

    assert (root / "pyproject.toml").read_bytes() == before
    assert molt_table(root) == {"changelog": False}
    assert console.contains("already"), "the user is told why nothing happened"


def test_an_existing_config_still_gets_a_missing_readme(
    tmp_path: Path, console: RecordingConsole
) -> None:
    """DELIBERATE DIVERGENCE -- research doc 03 section 10.16.

    Upstream returns at ``init/index.ts:95-103`` the moment ``config.json`` exists, so the
    README-creation branch at ``:117-122`` becomes unreachable: an existing config permanently
    suppresses a missing README, while a missing config happily creates one. molt's contract is
    symmetric -- "it creates only the pieces that are missing" (``cli/init.md``) -- so the README
    lands and the config is still not touched.
    """
    root = make_project(
        tmp_path / "project",
        pyproject=SINGLE_PACKAGE_PYPROJECT + '\n[tool.molt]\nbase_branch = "trunk"\n',
    )
    before = (root / "pyproject.toml").read_bytes()
    assert not changeset_readme(root).exists()

    run(cwd=root, console=console, prompts=ScriptedPrompts())

    assert changeset_readme(root).is_file()
    assert (root / "pyproject.toml").read_bytes() == before


def test_the_written_config_ends_with_a_single_lf_newline(
    tmp_path: Path, console: RecordingConsole
) -> None:
    """Row 6 (`command.test.ts:151-168`), hardened for Windows.

    Upstream only checks the last character is ``\\n``. molt asserts the *bytes* of the whole file:
    ``Path.read_text`` would translate CRLF to LF on the way in and silently satisfy the row on the
    exact platform it exists to protect. Being Windows-correct from day one is a deliberate
    differentiator (research README section 5, item 15).
    """
    root = make_project(tmp_path / "project")

    run(cwd=root, console=console, prompts=default_prompts())

    raw = (root / "pyproject.toml").read_bytes()
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n"), "exactly one trailing newline"
    assert b"\r\n" not in raw, "LF, never CRLF, on every platform"
    assert b"\r\n" not in changeset_readme(root).read_bytes()


# --------------------------------------------------------------------------------------
# Rows 8-10 - the interactive prompt sequence (research doc 03 section 7.2)
# --------------------------------------------------------------------------------------


def test_prompt_sequence(tmp_path: Path, console: RecordingConsole) -> None:
    """The questions ``init`` asks, in order, and the one it must not ask.

    ``cli/init.md``: "``molt init`` asks a few questions (base branch, changelog integration,
    whether to auto-commit changesets)". That ordering is molt's, and it differs from upstream's
    (``init/index.ts:20-87`` asks GitHub first and the base branch last) -- adopted here because
    the molt doc is the spec and the upstream order carried no meaning.

    ``select`` must never fire: the only ``askList`` upstream runs is the npm ``access`` question
    (``init/index.ts:44-50``), which molt drops (``config/options.md``).
    """
    root = make_project(tmp_path / "project")
    prompts = ScriptedPrompts(text=["", "my-org/my-repo"], confirm=[True, False])

    run(cwd=root, console=console, prompts=prompts)

    assert prompts.kinds == ["text", "confirm", "text", "confirm"]
    assert not prompts.called("select"), "npm's public/restricted access question is dropped"
    assert prompts.remaining == {}, "every scripted answer was consumed"


def test_github_changelog_records_the_repository(tmp_path: Path, console: RecordingConsole) -> None:
    """Row 8 (`command.test.ts:191-224`), adapted to molt's generator refs.

    Upstream writes ``["@changesets/changelog-github", {repo}]`` (`init/index.ts:29-34`). molt
    resolves generators through entry points rather than module paths (research README section 5,
    anti-features), so only the shape and the captured repo are pinned -- ``config/options.md``
    renders the equivalent as ``changelog = ["molt.changelog.github", { repo = "acme/acme" }]``.
    """
    root = make_project(tmp_path / "project")
    prompts = ScriptedPrompts(text=["", "my-org/my-repo"], confirm=[True, False])

    run(cwd=root, console=console, prompts=prompts)

    changelog = molt_table(root)["changelog"]
    assert isinstance(changelog, list) and len(changelog) == 2
    assert "github" in changelog[0]
    assert changelog[1] == {"repo": "my-org/my-repo"}
    repo_prompt = prompts.of("text")[1]
    assert "repo" in repo_prompt.message.lower()


def test_commit_enabled_and_a_custom_base_branch(tmp_path: Path, console: RecordingConsole) -> None:
    """Row 9 (`command.test.ts:225-256`), minus ``access``.

    Upstream asserts ``commit: true``, ``access: "public"`` and ``baseBranch: "master"``. The
    ``access`` third has no PyPI analogue and is dropped (``config/options.md``, "Dropped from
    changesets"); the other two port directly.
    """
    root = make_project(tmp_path / "project")
    prompts = ScriptedPrompts(text=["master"], confirm=[False, True])

    run(cwd=root, console=console, prompts=prompts)

    written = molt_table(root)
    assert written["base_branch"] == "master"
    # Not `is not False`: that also accepts None, 0, "maybe" and {}. `commit` is either the boolean
    # True or a normalized `[generator_ref, options]` pair -- the same two shapes `changelog` takes
    # (`config/options.md`; `tests/config/test_parse.py`).
    commit = written["commit"]
    assert commit is True or (isinstance(commit, list) and len(commit) == 2), (
        f"commit must be True or a 2-element generator ref, got {commit!r}"
    )
    assert "access" not in written


def test_an_empty_base_branch_falls_back_to_main(tmp_path: Path, console: RecordingConsole) -> None:
    """Row 10 (`command.test.ts:257-278`; ``init/index.ts:57``): ``"" -> "main"``.

    v3 changed the default from ``master`` to ``main`` (research README section 3.2), and
    ``config/options.md`` documents ``base_branch`` defaulting to ``"main"``.
    """
    root = make_project(tmp_path / "project")

    run(cwd=root, console=console, prompts=default_prompts())

    assert molt_table(root)["base_branch"] == "main"


def test_non_interactive_accepts_every_default_without_asking(
    tmp_path: Path, console: RecordingConsole, prompts: ScriptedPrompts
) -> None:
    """molt-NEW: ``--non-interactive`` / ``--yes`` never blocks (``cli-shell`` spec).

    The JS CLI has no such flag at all; adding one is research doc 03 section 11.7 item 7 ("table
    stakes for CI ergonomics"). The empty ``prompts`` fixture is the assertion mechanism: any
    prompt raises ``PromptExhausted``, so a run that reached one would fail loudly rather than hang.
    """
    root = make_project(tmp_path / "project")

    run(cwd=root, non_interactive=True, console=console, prompts=prompts)

    assert prompts.calls == []
    written = molt_table(root)
    assert written["base_branch"] == "main"
    assert written["commit"] is False


# --------------------------------------------------------------------------------------
# Row 11 - deterministic key order
# --------------------------------------------------------------------------------------


def test_config_keys_are_written_in_a_fixed_order(
    tmp_path: Path, console: RecordingConsole
) -> None:
    """Row 11 (`command.test.ts:280-306`), adapted to molt's schema.

    The point of the upstream row is determinism, not the particular list: a config whose key order
    depends on answer order produces noisy diffs across machines. molt's order is upstream's minus
    ``$schema`` and ``access`` (see ``MOLT_KEY_ORDER``). Asserting the *relative* order of the keys
    actually written -- rather than an exact list -- keeps ``init`` free to omit keys that are
    already at their default without weakening the determinism claim.
    """
    root = make_project(tmp_path / "project")

    run(cwd=root, console=console, prompts=default_prompts())

    keys = list(molt_table(root))
    assert keys, "init writes at least one option"
    assert set(keys) <= set(MOLT_KEY_ORDER), f"unexpected keys written: {keys}"
    assert keys == [key for key in MOLT_KEY_ORDER if key in keys]


# --------------------------------------------------------------------------------------
# molt-NEW - config-location conflict and ecosystem/workspace detection
# --------------------------------------------------------------------------------------


def test_two_config_locations_is_a_hard_error(tmp_path: Path, console: RecordingConsole) -> None:
    """molt-NEW: ``[tool.molt]`` *and* ``.molt/config.json`` present -> report and exit 1.

    Research README section 6, decision 7 ("Never merge two sources"); ``cli/init.md``: "Molt
    refuses to run when configuration is ambiguous ... reports the conflict and exits 1 rather than
    guessing which one wins." ``config/config-file.md`` names both paths in the message, and
    ``tests/config/test_parse.py::test_defining_config_in_both_locations_is_an_error`` pins the same
    rule at the loader; ``init`` must not paper over it by picking one.
    """
    root = make_project(
        tmp_path / "project",
        pyproject=SINGLE_PACKAGE_PYPROJECT + '\n[tool.molt]\nbase_branch = "main"\n',
    )
    (root / ".molt").mkdir()
    (root / ".molt" / "config.json").write_bytes(b'{"baseBranch": "main"}\n')

    with pytest.raises(ExitError) as excinfo:
        run(cwd=root, console=console, prompts=ScriptedPrompts())

    assert excinfo.value.code == 1
    text = console.text()
    assert "pyproject.toml" in text
    assert "config.json" in text


def test_detects_a_uv_workspace_and_reports_its_members(
    tmp_path: Path, console: RecordingConsole
) -> None:
    """molt-NEW: ecosystem + layout detection (``cli/init.md`` step 2).

    No changesets equivalent -- upstream's ``getPackages`` returns a ``tool.type`` that only the git
    tag shape ever branches on (research doc 03 section 8.2). In molt the ecosystem backend is the
    headline feature (research README section 5, item 1), so ``init`` reports what it found:
    ``[tool.uv.workspace]`` selects the uv backend (``ecosystems/overview.md``, "How molt detects
    the backend") and the members are the packages it will version.

    ASSUMED SEAM: the docs promise the detection but not its exact wording, so this asserts only
    that the backend name and every member name reach the user.
    """
    root = make_workspace(tmp_path / "project", ["pkg-a", "pkg-b"])

    run(cwd=root, console=console, prompts=default_prompts())

    text = console.text()
    assert "uv" in text
    assert "pkg-a" in text
    assert "pkg-b" in text


def test_detects_a_single_package_project(tmp_path: Path, console: RecordingConsole) -> None:
    """molt-NEW: the single-package case is first-class, not a degenerate monorepo.

    ``config/options.md``: ``ecosystem = "auto"`` "detects a uv workspace and otherwise treats the
    root as a single package". Most Python projects are single-package -- the ratio is inverted
    versus JS (research README section 5, item 9) -- so the report must name the root package and
    must not invent members.
    """
    root = make_project(tmp_path / "project")

    run(cwd=root, console=console, prompts=default_prompts())

    text = console.text()
    assert "acme" in text
    assert "packages/" not in text
