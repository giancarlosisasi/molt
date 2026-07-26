"""Sanity checks for the CLI test harness itself.

``tests/cli/fake_cli.py`` is the single pinned seam the four parallel P5 writers share, and it is
the one piece of this phase that must be correct **today**: every other module in ``tests/cli`` is
guarded by ``require_cli_app()`` and skips until the Typer shell lands, so a broken double would
silently weaken ~130 conformance rows without a single red test.

Same role, and the same shape, as ``tests/engine/test_fake_state.py`` -- see its module docstring
for why a harness gets its own unguarded self-test file.

The assertions below come from the "Fixtures & tooling to build" sections of
``roadmap/research/test-suite/05-cli-version.md`` and ``06-cli-commands.md``, plus the upstream
behaviors each double stands in for (``vi.mock("@changesets/git")``, ``mockedLogger`` /
``silenceLogsInBlock``, ``replaceHumanIds`` in ``status.test.ts``).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from tests.cli.fake_cli import (
    CANCEL,
    CHANGELOG_HASH,
    FROZEN_COMMIT,
    FROZEN_COMMIT_SHORT,
    ConsoleCall,
    FakeGit,
    Manifest,
    PromptExhausted,
    RecordingConsole,
    ScriptedPrompts,
    changeset_ids,
    read_changelog,
    read_manifest,
    read_manifests,
    read_ndjson,
    scrub_ids,
    strip_ansi,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import ProjectBuilder

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------------------
# Frozen constants -- the values ported snapshots are written against
# --------------------------------------------------------------------------------------


def test_frozen_commit_short_is_the_first_seven_characters() -> None:
    """``{commit-short}`` in the snapshot prerelease template is a 7-char prefix.

    research doc 03 section 3.4; ``version.test.ts`` snapshotPrereleaseTemplate group. A test
    asserting ``1.0.0-abcdefg`` only holds if the two constants stay in lockstep.
    """
    assert FROZEN_COMMIT[:7] == FROZEN_COMMIT_SHORT
    assert len(FROZEN_COMMIT_SHORT) == 7
    assert CHANGELOG_HASH == "g1th4sh"


# --------------------------------------------------------------------------------------
# strip_ansi
# --------------------------------------------------------------------------------------

ANSI_CASES = [
    # (raw,                              expected,        why)
    ("\x1b[31mred\x1b[0m", "red", "SGR color pair is removed, text survives"),
    ("\x1b[1;33;40mwarn\x1b[0m", "warn", "multi-parameter SGR sequence"),
    ("plain", "plain", "text with no escapes is returned unchanged"),
    ("\x1b[2K\rline", "\rline", "erase-line is stripped; a bare CR is not an escape"),
    ("a\x1b[?25lb", "ab", "private-mode (hide cursor) sequence is stripped"),
    (
        "\x1b]8;;https://example.test/issues/new?title=x\x1b\\report it\x1b]8;;\x1b\\",
        "report it",
        "OSC-8 hyperlink: rich wraps every URL it prints in one, ST-terminated",
    ),
    (
        "\x1b]0;molt\x07done",
        "done",
        "OSC window-title sequence, BEL-terminated",
    ),
]


@pytest.mark.parametrize(("raw", "expected", "why"), ANSI_CASES)
def test_strip_ansi(raw: str, expected: str, why: str) -> None:
    """The reference strips VT codes before asserting on ``clack.log.error`` text.

    ``06-cli-commands.md`` shared-harness notes; molt's console adapter may colorize through
    ``rich``, so every text assertion in the CLI suite goes through this first.
    """
    assert strip_ansi(raw) == expected, why


# --------------------------------------------------------------------------------------
# RecordingConsole -- the `Console` protocol of the terminal-ui spec
# --------------------------------------------------------------------------------------


def test_console_routes_each_level_to_its_own_bucket() -> None:
    """terminal-ui spec, "Levelled messages are distinguishable".

    ``at(level)`` is what the ported rows assert on: upstream spies a *distinct* mock per
    ``clack.log.*`` level, so a console that merged warn into info would make every
    "warns but does not fail" row pass vacuously.
    """
    console = RecordingConsole()
    console.info("i")
    console.success("s")
    console.warn("w")
    console.error("e")

    assert console.at("info") == ["i"]
    assert console.at("success") == ["s"]
    assert console.warnings == ["w"]
    assert console.errors == ["e"]
    assert console.messages == ["i", "s", "w", "e"], "emission order is preserved across levels"
    assert console.at("nope") == [], "an unknown level is empty, not an error"


def test_console_strips_vt_codes_from_every_level() -> None:
    """Recorded text is VT-stripped so assertions never depend on rich colorizing."""
    console = RecordingConsole()
    console.error("\x1b[31mboom\x1b[0m")
    console.note("\x1b[1mIMPORTANT\x1b[0m", "\x1b[33mbody\x1b[0m")

    assert console.errors == ["boom"]
    assert console.calls[-1] == ConsoleCall("note", "body", "IMPORTANT")


def test_console_note_records_title_and_body_separately() -> None:
    """``importantWarning`` is a titled box (``cli-utilities.ts:23-25``), not a flat line."""
    console = RecordingConsole()
    console.note("IMPORTANT", "the dependents note")

    call = console.calls[0]
    assert call.level == "note"
    assert call.title == "IMPORTANT"
    assert call.message == "the dependents note"
    assert console.at("note") == ["the dependents note"], "at() yields the body, not the title"


def test_console_note_body_defaults_to_empty() -> None:
    console = RecordingConsole()
    console.note("title only")

    assert console.calls[0] == ConsoleCall("note", "", "title only")


def test_console_text_and_contains_query_the_whole_transcript() -> None:
    console = RecordingConsole()
    console.info("first line")
    console.error("second line")

    assert console.text() == "first line\nsecond line"
    assert console.contains("second")
    assert console.contains("second", level="error")
    assert not console.contains("second", level="info"), "level filter must actually filter"
    assert not console.contains("third")


def test_console_reset_clears_the_transcript() -> None:
    console = RecordingConsole()
    console.info("before")
    console.reset()
    console.info("after")

    assert console.messages == ["after"]


def test_console_spinner_and_progress_record_their_message_and_yield() -> None:
    """``spinner()`` (publish git tags) and ``progress({max})`` (git-tag) -- doc 03 section 9.1.

    They are context managers in the real adapter, so the double must be usable in a ``with``
    block; the body running is the part a command depends on.
    """
    console = RecordingConsole()
    entered: list[str] = []

    with console.spinner("tagging"):
        entered.append("spinner")
    with console.progress("writing", total=3):
        entered.append("progress")

    assert entered == ["spinner", "progress"]
    assert console.at("info") == ["tagging", "writing"]


def test_console_spinner_and_progress_record_nothing_without_a_message() -> None:
    """A message-less handle must not inject a phantom line into ``messages``."""
    console = RecordingConsole()

    with console.spinner():
        pass
    with console.progress():
        pass

    assert console.calls == []


# --------------------------------------------------------------------------------------
# ScriptedPrompts -- the `Prompts` protocol of the terminal-ui spec
# --------------------------------------------------------------------------------------


def test_prompts_consume_their_script_in_order_per_kind() -> None:
    """``mockUserResponses`` (``add.test.ts``) feeds a queue per prompt primitive.

    The queues are independent: the ``add`` flow interleaves one multiselect with N selects and
    a text, and a shared queue would silently reorder them.
    """
    prompts = ScriptedPrompts(
        multiselect=[["pkg-a", "pkg-b"]],
        select=["minor", "patch"],
        text=["summary message mock"],
    )

    assert prompts.multiselect("which packages?", ["pkg-a", "pkg-b", "pkg-c"]) == [
        "pkg-a",
        "pkg-b",
    ]
    assert prompts.select("bump for pkg-a?", ["major", "minor", "patch"]) == "minor"
    assert prompts.select("bump for pkg-b?", ["major", "minor", "patch"]) == "patch"
    assert prompts.text("summary?") == "summary message mock"

    assert prompts.kinds == ["multiselect", "select", "select", "text"]
    assert prompts.remaining == {}, "every scripted answer was consumed"


def test_prompts_record_choices_and_kwargs() -> None:
    """``add`` row 10 asserts the *arguments* of ``askMultiselect``, not just the outcome.

    ``06-cli-commands.md`` add row 10: the grouped choices ``{"changed packages": [...],
    "unchanged packages": [...]}`` and ``{required: True}`` are the assertion.
    """
    prompts = ScriptedPrompts(multiselect=[["pkg-b"]])
    groups = {"changed packages": ["pkg-b"], "unchanged packages": ["pkg-a"]}

    prompts.multiselect("Which packages were affected?", [groups], required=True)

    call = prompts.of("multiselect")[0]
    assert call.kind == "multiselect"
    assert call.message == "Which packages were affected?"
    assert call.choices == (groups,), "choices are stored as a tuple, positionally"
    assert call.kwargs == {"required": True}
    assert prompts.called("multiselect")
    assert not prompts.called("select")


def test_prompts_without_choices_record_none_not_an_empty_tuple() -> None:
    """``confirm``/``text``/``editor`` take no choice list; ``None`` keeps that distinguishable
    from a multiselect that was handed an empty list of packages."""
    prompts = ScriptedPrompts(confirm=[True], text=[""], editor=["from the editor"])

    assert prompts.confirm("is this a major?", initial=True) is True
    assert prompts.text("summary?", placeholder="Summary") == ""
    assert prompts.editor() == "from the editor"

    assert [call.choices for call in prompts.calls] == [None, None, None]
    assert prompts.of("confirm")[0].kwargs == {"initial": True}
    assert prompts.of("text")[0].kwargs == {"placeholder": "Summary"}


def test_prompts_raise_prompt_exhausted_when_over_prompted() -> None:
    """An unscripted prompt is a **test** failure, not a product error.

    Upstream gets this free -- an unmocked clack prompt hangs -- and a hang is exactly what
    ``--non-interactive`` exists to prevent (research doc 03 section 9.3).
    """
    prompts = ScriptedPrompts(select=["minor"])
    prompts.select("bump?", ["minor"])

    with pytest.raises(PromptExhausted, match="select"):
        prompts.select("bump again?", ["minor"])

    assert issubclass(PromptExhausted, AssertionError), "an unscripted prompt fails the test"


def test_prompt_exhausted_names_the_prompt_and_the_flow_so_far() -> None:
    prompts = ScriptedPrompts(text=["only one"])
    prompts.text("first")

    with pytest.raises(PromptExhausted) as excinfo:
        prompts.text("second question")

    message = str(excinfo.value)
    assert "second question" in message, "the failing prompt is named"
    assert "text" in message


def test_prompts_with_an_empty_script_fail_on_the_very_first_prompt() -> None:
    """The ``prompts`` fixture is empty on purpose: the "should not prompt" rows
    (``add`` rows 6/7/9 -- ``--message`` skips the summary prompt) assert by *not* failing."""
    prompts = ScriptedPrompts()

    with pytest.raises(PromptExhausted):
        prompts.multiselect("anything?", [])


def test_a_scripted_exception_instance_is_raised() -> None:
    """``add`` row 2d: ``editor=[1]`` rejects, the flow catches it and re-prompts.

    Modelling a failing editor needs the double to *raise* rather than return, so the ported row
    can assert the failure is non-fatal.
    """
    boom = RuntimeError("editor exploded")
    prompts = ScriptedPrompts(editor=[boom, "second try"])

    with pytest.raises(RuntimeError, match="editor exploded"):
        prompts.editor()
    assert prompts.editor() == "second try", "the queue advances past the raised answer"
    assert prompts.kinds == ["editor", "editor"], "the failed prompt is still recorded"


def test_cancel_sentinel_is_falsy_and_identifiable() -> None:
    """The ``cancelable`` contract (terminal-ui spec) requires an explicit sentinel check.

    ``CANCEL`` is deliberately falsy so a command that writes ``if not answer:`` instead of
    ``if answer is CANCEL:`` cannot pass by accident -- it must treat it as a cancellation
    (print "Canceled", exit 0 -- research doc 03 section 11.6), not as an empty answer.
    """
    prompts = ScriptedPrompts(text=[CANCEL])

    answer = prompts.text("summary?")
    assert answer is CANCEL
    assert not answer, "falsy on purpose"
    assert repr(CANCEL) == "CANCEL"


def test_prompts_remaining_reports_unconsumed_answers() -> None:
    """A non-empty ``remaining`` means the flow prompted *less* than the row expected."""
    prompts = ScriptedPrompts(select=["minor", "patch"], confirm=[True])
    prompts.select("bump?", ["minor"])

    assert prompts.remaining == {"select": 1, "confirm": 1}


# --------------------------------------------------------------------------------------
# FakeGit -- vi.mock("@changesets/git")
# --------------------------------------------------------------------------------------


def test_fake_git_records_mutations_in_call_order() -> None:
    """The commit-config rows spy on ``add``/``commit``; ``git-tag`` rows spy on ``tag``."""
    git = FakeGit()
    git.add("packages/pkg-a/pyproject.toml", "CHANGELOG.md")
    sha = git.commit("RELEASING: Releasing 1 package(s)", allow_empty=True)
    git.tag("pkg-a@1.0.0")

    assert [call.name for call in git.calls] == ["add", "commit", "tag"]
    assert git.added == ["packages/pkg-a/pyproject.toml", "CHANGELOG.md"]
    assert git.commits == ["RELEASING: Releasing 1 package(s)"]
    assert git.tags == ["pkg-a@1.0.0"]
    assert sha == FROZEN_COMMIT, "commit returns the frozen sha the snapshots are written against"


def test_fake_git_commit_records_its_keyword_arguments() -> None:
    """``git commit --allow-empty`` (research doc 03 section 10.18) has to stay observable."""
    git = FakeGit()
    git.commit("msg", allow_empty=True)

    assert git.of("commit")[0].args == ("msg", (("allow_empty", True),))


def test_fake_git_reads_are_frozen() -> None:
    """``getCurrentCommitId`` and ``getCommitsThatAddFiles`` are mocked to fixed values.

    The default changelog generator prefixes every summary with the short hash, so ported
    changelog expectations read ``- g1th4sh: This is a summary``.
    """
    git = FakeGit()

    assert git.get_current_commit_id() == FROZEN_COMMIT
    assert git.get_current_commit_id(cwd="/anywhere") == FROZEN_COMMIT
    assert git.get_commits_that_add_files([".changeset/a.md", ".changeset/b.md"]) == {
        ".changeset/a.md": CHANGELOG_HASH,
        ".changeset/b.md": CHANGELOG_HASH,
    }
    assert git.get_commits_that_add_files([]) == {}


def test_fake_git_existing_tags_seed_the_idempotency_rows() -> None:
    """``06-cli-commands.md`` git-tag rows 3/4: a tag already present must not be recreated."""
    git = FakeGit(existing_tags=["pkg-a@1.0.0"])

    assert git.tag_exists("pkg-a@1.0.0")
    assert not git.tag_exists("pkg-b@1.0.0")
    assert git.get_all_tags() == {"pkg-a@1.0.0"}
    assert git.tags == [], "seeded tags are not reported as created by this run"


def test_fake_git_tagging_updates_the_existence_check_but_not_the_seed() -> None:
    git = FakeGit(existing_tags=["pkg-a@1.0.0"])
    git.tag("pkg-b@1.0.0", "pkg-b@1.0.0")

    assert git.tag_exists("pkg-b@1.0.0"), "a tag created in-run is visible to a later check"
    assert git.tags == ["pkg-b@1.0.0"], "only tags created by this run"
    assert git.of("tag")[0].args == ("pkg-b@1.0.0", "pkg-b@1.0.0")


def test_fake_git_get_all_tags_returns_a_copy() -> None:
    """A row that mutates the returned set must not corrupt the double."""
    git = FakeGit(existing_tags=["v1.0.0"])
    tags = git.get_all_tags()
    tags.add("v9.9.9")

    assert git.get_all_tags() == {"v1.0.0"}


# --------------------------------------------------------------------------------------
# Manifest readers -- getPackages().packages.map(x => x.packageJson), over pyproject.toml
# --------------------------------------------------------------------------------------


def test_read_manifests_covers_every_member_and_excludes_the_root(
    tmp_project: ProjectBuilder,
) -> None:
    """``read_manifests`` is keyed by package name and skips the workspace root.

    The root is read separately (``read_manifest(root)``) because 05 group row 24 asserts on
    root-level references while the member rows assert on ``packages/*`` only.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "2.0.0", deps=["pkg-a==1.0.0"])

    manifests = read_manifests(tmp_project.root)

    assert sorted(manifests) == ["pkg-a", "pkg-b"]
    assert manifests["pkg-b"].dependencies == ("pkg-a==1.0.0",)
    assert "workspace-root" not in manifests
    assert read_manifest(tmp_project.root).name == "workspace-root"


def test_read_manifests_of_an_empty_workspace_is_empty(tmp_project: ProjectBuilder) -> None:
    assert read_manifests(tmp_project.root) == {}


def test_read_manifest_separates_runtime_dev_and_optional_dependencies(
    tmp_project: ProjectBuilder,
) -> None:
    """Python dependency lists are PEP 508 **strings in file order**, not npm's name->range map.

    research doc 04 section 2.6. The three fields stay separate because they bump dependents
    differently (dev groups -> none, extras -> patch); a reader that merged them would make the
    propagation rows unfalsifiable.
    """
    tmp_project.add_package(
        "pkg-b",
        "2.0.0",
        deps=["pkg-a==1.0.0", "httpx>=0.27"],
        dev_deps=["pytest>=9.0"],
        optional_deps={"cli": ["typer>=0.15"], "docs": ["mkdocs>=1.6"]},
    )

    manifest = read_manifest(tmp_project.root, "pkg-b")

    assert manifest.dependencies == ("pkg-a==1.0.0", "httpx>=0.27"), "file order is preserved"
    assert manifest.dev_dependencies == ("pytest>=9.0",)
    assert manifest.optional_dependencies == {"cli": ("typer>=0.15",), "docs": ("mkdocs>=1.6",)}
    assert manifest.private is False


def test_read_manifest_detects_the_private_classifier(tmp_project: ProjectBuilder) -> None:
    """``Private :: Do Not Upload`` is the Python analogue of npm ``"private": true``.

    research doc 02 section 12.1; drives the ``privatePackages.version``/``.tag`` rows.
    """
    tmp_project.add_package("pkg-private", "1.0.0", private=True)
    tmp_project.add_package("pkg-public", "1.0.0")

    manifests = read_manifests(tmp_project.root)

    assert manifests["pkg-private"].private is True
    assert manifests["pkg-public"].private is False


def test_read_manifest_reports_a_missing_version_as_none(tmp_project: ProjectBuilder) -> None:
    """``shouldSkipPackage`` drops any package with no ``version`` field
    (``should-skip-package/src/index.ts:13-22``), so "absent" must not collapse to ``""``.

    ``ProjectBuilder.add_package`` always writes a version, so this row writes the manifest by
    hand -- the one shape the builder cannot express.
    """
    package = tmp_project.root / "packages" / "pkg-noversion"
    package.mkdir(parents=True, exist_ok=True)
    (package / "pyproject.toml").write_bytes(b'[project]\nname = "pkg-noversion"\n')

    manifest = read_manifest(tmp_project.root, "pkg-noversion")

    assert manifest.version is None
    assert manifest.name == "pkg-noversion"
    assert manifest.dependencies == ()


def test_manifest_to_dict_is_snapshot_shaped() -> None:
    """``to_dict`` is what a syrupy snapshot or an ``==`` against a literal sees."""
    manifest = Manifest(
        name="pkg-a",
        version="1.0.0",
        dependencies=("pkg-b==1.0.0",),
        dev_dependencies=("pytest>=9.0",),
        optional_dependencies={"cli": ("typer>=0.15",)},
        private=True,
    )

    assert manifest.to_dict() == {
        "name": "pkg-a",
        "version": "1.0.0",
        "dependencies": ["pkg-b==1.0.0"],
        "dev_dependencies": ["pytest>=9.0"],
        "optional_dependencies": {"cli": ["typer>=0.15"]},
        "private": True,
    }
    assert json.dumps(manifest.to_dict()), "the dict form must be JSON-serializable"


# --------------------------------------------------------------------------------------
# read_changelog
# --------------------------------------------------------------------------------------


def test_read_changelog_returns_none_when_the_file_is_absent(tmp_project: ProjectBuilder) -> None:
    """ "No changelog was written" and "an empty changelog was written" are different outcomes."""
    tmp_project.add_package("pkg-a", "1.0.0")

    assert read_changelog(tmp_project.root, "pkg-a") is None
    assert read_changelog(tmp_project.root) is None


def test_read_changelog_does_not_translate_newlines(tmp_project: ProjectBuilder) -> None:
    """Read as bytes, decoded -- never ``Path.read_text``.

    Text mode opens with ``newline=None``, which would translate CRLF to LF on read, so a row
    asserting molt writes LF-only output (research doc 02 section 12.6) would pass on Windows
    even against an implementation emitting CRLF.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    (tmp_project.root / "packages" / "pkg-a" / "CHANGELOG.md").write_bytes(
        b"# pkg-a\r\n\r\n## 1.0.0"
    )

    contents = read_changelog(tmp_project.root, "pkg-a")

    assert contents == "# pkg-a\r\n\r\n## 1.0.0", "CRLF survives the read verbatim"


def test_read_changelog_reads_the_root_changelog_when_name_is_none(
    tmp_project: ProjectBuilder,
) -> None:
    (tmp_project.root / "CHANGELOG.md").write_bytes(b"# root\n")

    assert read_changelog(tmp_project.root) == "# root\n"


# --------------------------------------------------------------------------------------
# changeset_ids
# --------------------------------------------------------------------------------------


def test_changeset_ids_lists_stems_sorted_including_dotfiles(tmp_project: ProjectBuilder) -> None:
    """05 group row 11 asserts ``.ignored-temporarily.md`` is neither applied nor deleted.

    The assertion needs the helper to *see* the dot-prefixed file, even though
    ``read_changesets`` deliberately ignores it (research doc 03 section 10.20).
    """
    tmp_project.write_changeset("quiet-lions-give", {"pkg-a": "minor"})
    tmp_project.write_changeset("strange-words-combine", {"pkg-a": "patch"})
    (tmp_project.root / ".changeset" / ".ignored-temporarily.md").write_bytes(b"---\n---\n")

    assert changeset_ids(tmp_project.root) == [
        ".ignored-temporarily",
        "quiet-lions-give",
        "strange-words-combine",
    ]


def test_changeset_ids_is_empty_without_a_changeset_directory(tmp_project: ProjectBuilder) -> None:
    """A missing ``.changeset/`` must not blow up -- ``add`` row 20 asserts on the error message,
    not on a traceback from the harness."""
    assert changeset_ids(tmp_project.root) == []


def test_changeset_ids_ignores_non_markdown_files(tmp_project: ProjectBuilder) -> None:
    tmp_project.write_changeset("quiet-lions-give", {"pkg-a": "minor"})
    (tmp_project.root / ".changeset" / "config.json").write_bytes(b"{}\n")

    assert changeset_ids(tmp_project.root) == ["quiet-lions-give"]


# --------------------------------------------------------------------------------------
# read_ndjson -- the `--output <file>` event stream
# --------------------------------------------------------------------------------------


def test_read_ndjson_parses_one_object_per_line(tmp_path: Path) -> None:
    """``git-tag`` row 2: one ``{"type":"git-tag", tag, packageName}`` object per line."""
    path = tmp_path / "events.ndjson"
    path.write_bytes(
        b'{"type": "git-tag", "tag": "pkg-a@1.0.0"}\n{"type": "git-tag", "tag": "pkg-b@1.0.0"}\n'
    )

    assert read_ndjson(path) == [
        {"type": "git-tag", "tag": "pkg-a@1.0.0"},
        {"type": "git-tag", "tag": "pkg-b@1.0.0"},
    ]


def test_read_ndjson_distinguishes_an_empty_file_from_a_missing_one(tmp_path: Path) -> None:
    """``git-tag`` row 3: the reporter always creates the file, even with zero events.

    "no events" (``[]``) and "no file" (an error) must not collapse into the same result, or the
    row cannot assert the file was created.
    """
    empty = tmp_path / "empty.ndjson"
    empty.write_bytes(b"")

    assert read_ndjson(empty) == []
    with pytest.raises(FileNotFoundError):
        read_ndjson(tmp_path / "never-written.ndjson")


def test_read_ndjson_skips_blank_lines(tmp_path: Path) -> None:
    """A trailing newline after the last event must not parse as an empty record."""
    path = tmp_path / "events.ndjson"
    path.write_bytes(b'{"type": "git-tag"}\n\n')

    assert read_ndjson(path) == [{"type": "git-tag"}]


def test_read_ndjson_splits_on_lf_only(tmp_path: Path) -> None:
    """NDJSON is LF-delimited; ``str.splitlines`` breaks on six more characters.

    ``U+2028`` (LINE SEPARATOR) is legal **unescaped** inside a JSON string -- it is not a JSON
    control character -- and a changeset summary pasted from a word processor can carry one.
    ``splitlines`` would tear that single event into two fragments, neither of them valid JSON, so
    this reader would raise where every real NDJSON consumer succeeds. ``U+0085`` (NEL) is the
    same trap one code point away.

    Both separators are spelled with ``chr()`` so this source file stays ASCII-only (project rule).
    """
    line_separator = chr(0x2028)
    next_line = chr(0x0085)
    first = '{"type": "git-tag", "summary": "before' + line_separator + 'after"}'
    second = '{"type": "git-tag", "summary": "x' + next_line + 'y"}'
    path = tmp_path / "events.ndjson"
    path.write_bytes((first + "\n" + second + "\n").encode("utf-8"))

    assert read_ndjson(path) == [
        {"type": "git-tag", "summary": "before" + line_separator + "after"},
        {"type": "git-tag", "summary": "x" + next_line + "y"},
    ]


# --------------------------------------------------------------------------------------
# scrub_ids -- replaceHumanIds (status.test.ts)
# --------------------------------------------------------------------------------------


def plan(*ids: str) -> dict[str, Any]:
    """A minimal plan-shaped dict carrying ``ids`` as its changesets, snake_case keys."""
    return {
        "changesets": [{"id": cid, "summary": f"summary for {cid}"} for cid in ids],
        "releases": [{"name": "pkg-a", "new_version": "1.1.0", "changesets": list(ids)}],
    }


def test_scrub_ids_numbers_ids_in_first_appearance_order() -> None:
    """Port of ``replaceHumanIds``: the random id becomes ``~changeset-N~`` before snapshotting.

    Numbering follows ``changesets[].id`` order, so a plan whose changeset order is part of the
    contract keeps that order visible in the snapshot.
    """
    scrubbed = scrub_ids(plan("zebra-id", "alpha-id"))

    assert [entry["id"] for entry in scrubbed["changesets"]] == ["~changeset-0~", "~changeset-1~"]


def test_scrub_ids_rewrites_every_occurrence_including_release_backrefs() -> None:
    """``releases[].changesets`` holds the same ids; a scrubber that only touched
    ``changesets[].id`` would leave the random ids in the snapshot and make it non-deterministic."""
    scrubbed = scrub_ids(plan("zebra-id", "alpha-id"))

    assert scrubbed["releases"][0]["changesets"] == ["~changeset-0~", "~changeset-1~"]
    assert scrubbed["changesets"][0]["summary"] == "summary for ~changeset-0~"


def test_scrub_ids_rewrites_ids_inside_file_paths() -> None:
    """``version --dry-run`` plans name the changeset files that would be consumed."""
    scrubbed = scrub_ids(
        {
            "changesets": [{"id": "zebra-id"}],
            "consumed": [".changeset/zebra-id.md"],
        }
    )

    assert scrubbed["consumed"] == [".changeset/~changeset-0~.md"]


def test_scrub_ids_is_key_casing_agnostic() -> None:
    """It must work whether the plan JSON is snake_case (molt, pinned) or camelCase (upstream).

    Only the values are rewritten; keys are copied verbatim, so a casing regression stays visible
    in the snapshot instead of being normalized away.
    """
    scrubbed = scrub_ids(
        {
            "changesets": [{"id": "zebra-id"}],
            "releases": [{"newVersion": "1.1.0", "changesets": ["zebra-id"]}],
        }
    )

    assert scrubbed["releases"][0] == {"newVersion": "1.1.0", "changesets": ["~changeset-0~"]}


def test_scrub_ids_rejects_a_duplicate_changeset_id() -> None:
    """``status.test.ts:22-24`` throws ``"Duplicate changeset id found"``; so does this port.

    Two changesets sharing an id is a **corrupt plan**, not a shape to normalize. Quietly reusing
    one placeholder would make a plan that dropped a changeset compare equal to one that did not,
    which is precisely the difference the ported ``status`` snapshot rows exist to detect.
    """
    with pytest.raises(ValueError, match="Duplicate changeset id"):
        scrub_ids(plan("zebra-id", "zebra-id"))


def test_scrub_ids_numbers_from_zero_not_one() -> None:
    """DOCUMENTED DIVERGENCE from ``replaceHumanIds``.

    Upstream's ``~changeset-${++counter}~`` template (``status.test.ts:26``) numbers from **1**;
    this port numbers from **0**. Harmless -- molt asserts against explicit literals rather than
    snapshot text -- but every call site (``test_status.py::EXPECTED_PLAN``) spells
    ``~changeset-0~``, so the choice is pinned here rather than left to drift.
    """
    scrubbed = scrub_ids(plan("only-id"))

    assert scrubbed["changesets"][0]["id"] == "~changeset-0~"


def test_scrub_ids_does_not_corrupt_an_id_that_prefixes_another() -> None:
    """Sequential ``str.replace`` over an unordered dict is order-dependent and lossy.

    With ids ``cat`` and ``cathode``, replacing ``cat`` first rewrites ``cathode`` into
    ``~changeset-0~hode`` -- and which of the two happens depends on dict insertion order, so the
    bug is invisible until a real human-id pair happens to share a prefix. Longest-match-first,
    single-pass substitution is the fix.
    """
    scrubbed = scrub_ids(
        {
            "changesets": [{"id": "cat"}, {"id": "cathode"}],
            "releases": [{"name": "pkg-a", "changesets": ["cat", "cathode"]}],
            "consumed": [".changeset/cathode.md", ".changeset/cat.md"],
        }
    )

    assert [entry["id"] for entry in scrubbed["changesets"]] == ["~changeset-0~", "~changeset-1~"]
    assert scrubbed["releases"][0]["changesets"] == ["~changeset-0~", "~changeset-1~"]
    assert scrubbed["consumed"] == [".changeset/~changeset-1~.md", ".changeset/~changeset-0~.md"]


def test_scrub_ids_leaves_binary_payloads_as_bytes() -> None:
    """``bytes``/``bytearray`` are ``Sequence``s of ``int``.

    An unguarded ``isinstance(value, Sequence)`` branch recurses into them and returns
    ``list[int]``, so a plan carrying a digest or an artifact blob would come back as a list of
    integers -- an assertion failure whose cause is nowhere near the assertion.
    """
    scrubbed = scrub_ids(
        {"changesets": [{"id": "zebra-id"}], "digest": b"\x00zebra-id", "buf": bytearray(b"ab")}
    )

    assert scrubbed["digest"] == b"\x00zebra-id"
    assert isinstance(scrubbed["buf"], bytearray)


def test_scrub_ids_leaves_a_plan_without_changesets_untouched() -> None:
    """``status`` row 4: an empty plan is not an error, and must snapshot as itself."""
    empty: dict[str, Any] = {"changesets": [], "releases": []}

    assert scrub_ids(empty) == empty
    assert scrub_ids({"releases": [{"name": "pkg-a"}]}) == {"releases": [{"name": "pkg-a"}]}


def test_scrub_ids_preserves_non_string_scalars() -> None:
    """Booleans/numbers/None must survive: a plan asserting ``"private": False`` still has to."""
    scrubbed = scrub_ids(
        {"changesets": [{"id": "zebra-id"}], "private": False, "count": 2, "pre": None}
    )

    assert scrubbed["private"] is False
    assert scrubbed["count"] == 2
    assert scrubbed["pre"] is None


# --------------------------------------------------------------------------------------
# ProjectBuilder (tests/conftest.py) -- the shared workspace fixture, byte-exact
# --------------------------------------------------------------------------------------


def test_project_builder_writes_lf_only_toml_on_every_platform(
    tmp_project: ProjectBuilder,
) -> None:
    """The root manifest, member manifests and the config table are all LF, never CRLF.

    ``Path.write_text`` opens in text mode with ``newline=None``, which translates the line
    feed to ``os.linesep`` -- so on Windows this fixture used to emit CRLF while the identical
    fixture on Linux emitted LF. Every downstream suite builds its workspace through this class
    (``tests/apply``, ``tests/engine``, ``tests/config``, ``tests/changeset``,
    ``tests/changelog``), so a byte-level assertion anywhere below it was silently
    platform-dependent -- on the one platform molt exists to get right. ``write_changeset``
    already used ``write_bytes`` and said why; the other three writers now match it.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b==1.0.0"], dev_deps=["pytest>=9.0"])
    tmp_project.set_config(base_branch="main")

    root_manifest = (tmp_project.root / "pyproject.toml").read_bytes()
    member_manifest = (tmp_project.root / "packages" / "pkg-a" / "pyproject.toml").read_bytes()
    changeset = tmp_project.write_changeset("quiet-lions-give", {"pkg-a": "minor"}).read_bytes()

    assert b"\r\n" not in root_manifest, "the root pyproject.toml is LF-only"
    assert b"\r\n" not in member_manifest, "member manifests are LF-only"
    assert b"\r\n" not in changeset, "changeset files are LF-only (already were)"
    assert b"\r" not in root_manifest + member_manifest + changeset


def test_project_builder_json_config_is_lf_only(tmp_project: ProjectBuilder) -> None:
    """``set_config(as_json=True)`` writes ``.molt/config.json``; same newline rule."""
    tmp_project.set_config(as_json=True, base_branch="main")

    raw = (tmp_project.root / ".molt" / "config.json").read_bytes()

    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
