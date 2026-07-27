"""Conformance tests for the CLI **shell** -- the cross-cutting contract every command inherits.

This is the only module that drives the Typer application itself; the three per-command modules in
this package test command logic behind the ``run(...)`` seam. Everything the shell owns lives here:
the command table, default-command dispatch, the deprecated ``tag`` alias, banner gating, the error
funnel, the exit-code contract, option normalization, the optional-value ``--snapshot``, and the
global ``--non-interactive``/``--yes`` flag.

Primary source: the **accepted** OpenSpec change ``openspec/changes/adopt-typer-cli-shell/``
(``specs/cli-shell/spec.md``, ``specs/terminal-ui/spec.md``, ``design.md`` D1-D7,
``tasks.md`` section 7).
Upstream reference: ``@changesets/cli@3.0.0-next.9`` ``packages/cli/src/cli.ts`` +
``packages/cli/src/index.ts``, extracted in
``roadmap/research/changesets-03-cli-and-ux.md`` section 1 (1.1 program setup, 1.2
bootstrap/error funnel, 1.3 option normalisation, 1.4 the flag matrix, 1.5 ``pre``
validation, 1.6 the ``tag`` alias), sections 9-10 and 11.1/11.6/11.7. Row-by-row map:
``roadmap/research/test-suite/06-cli-commands.md``, the ``packages/cli/src/cli.test.ts`` section.
The user-facing statement of the same contract is ``website/docs/cli/overview.md``.

Deviations from the phase brief, all deliberate and reported to the owner:

* **Command logic modules are ``molt.commands.<name>``**, not ``molt.cli.commands.<name>``
  (``design.md`` D7 + ``tasks.md`` 1.2 + research section 11.5 all agree; ``MILESTONES.md``
  and the P5 phase file say otherwise and are superseded).
* **Assumed command seam:** each command module exposes ``run(...)``, called with ``cwd=<Path>``
  plus one keyword per long flag with dashes turned into underscores (``--non-interactive`` ->
  ``non_interactive``). Routing rows monkeypatch that ``run`` with a recorder. The lazy per-command
  import (D7) is what makes the monkeypatch visible to the wrapper.
* **``pre`` is a signpost.** The accepted ``cli-shell`` spec registers ``pre`` in the command table
  while ``website/docs/cli/pre.md`` states molt has no ``pre enter``/``pre exit``. Reconciliation
  pinned here: ``molt pre ...`` exists and exits non-zero with migration guidance naming
  ``molt version --pre {a,b,rc,dev}``. **Needs owner sign-off** -- the two sources disagree.
* **``--snapshot-name`` collapses into ``snapshot``** -- ADJUDICATED, no longer an open question.
  The command seam is a single keyword ``run(..., snapshot: str | bool | None = None)``: the three
  accepted spellings all produce one effective ``snapshot`` value (``True`` unnamed, ``str`` named,
  ``None`` absent), mirroring upstream's ``snapshot?: string | boolean``
  (``cli.test.ts:106-123``). The alternative -- handing the command a structured
  ``SnapshotParams`` -- was rejected because its ``commit`` field cannot be filled at the shell
  boundary: upstream resolves it at ``version/index.ts:105-114`` by reading the configured
  prerelease template and calling ``git.getCurrentCommitId()`` only when that template contains
  ``{commit}``/``{commit-short}``. A config read plus a git call are command-layer work and
  explicit Non-Goals of the shell change, so a shell-built ``SnapshotParams`` would be a
  half-built object the command must rebuild -- and it would drag an engine type across the CLI
  seam, defeating D7's lazy-import rule. ``str | bool | None`` admits no illegal state either.
  The routing rows below therefore expect ``{"snapshot": True}`` / ``{"snapshot": "pr-123"}``, and
  ``test_an_alias_collapses_instead_of_duplicating`` pins the *absence* of a second keyword --
  which the subset-projecting routing rows structurally cannot.
* **``--snapshot-prerelease-template`` is not a CLI flag in molt** (config-only): the accepted docs
  (``website/docs/cli/version.md:39-47``) do not declare it. Recorded as a drop.

Guards (section 3 of the writer contract): ``src/molt/cli.py`` already exists as a
placeholder that prints "not implemented yet", so ``importorskip("molt.cli")`` alone would run
this module red against the stub. ``importorskip("typer")`` is what skips today;
``require_cli_app()`` skips the whole module while ``molt.cli`` has no ``app`` attribute. Both
are required. Tests that additionally need ``molt.errors`` or ``molt.ui.console`` carry their own
in-body ``importorskip``.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.util
import io
import json
import platform
import sys
from pathlib import Path, PurePath
from typing import Any

import pytest
from tests.cli.fake_cli import (
    PromptExhausted,
    RecordingConsole,
    ScriptedPrompts,
    invoke_cli,
    require_cli_app,
    strip_ansi,
)

# The two ``molt`` imports sit BELOW the guard on purpose. ``src/molt/cli.py`` imports typer at
# module level once the shell lands, so an environment without typer would turn this module's
# collection into an ERROR rather than the SKIP the guard exists to produce -- and a collection
# error in one module aborts the whole run.
pytest.importorskip(
    "typer", reason="build step 6 - typer lands with the adopt-typer-cli-shell change"
)

from molt import __version__
from molt import cli as molt_cli

app = require_cli_app()

pytestmark = pytest.mark.functional


# --------------------------------------------------------------------------------------
# The command table (cli-shell spec, "Command surface registration"; research section 1.4)
# --------------------------------------------------------------------------------------

#: Every command ``molt --help`` must list. ``pack`` is deliberately absent: ``design.md`` Open
#: Questions adopts ``build`` as the name with **no** ``pack`` alias.
#:
#: ``yank`` is here even though the accepted ``cli-shell`` spec's command table omits it.
#: ``website/docs/cli/overview.md:22`` lists it in the command surface and
#: ``website/docs/cli/yank.md`` is a complete reference page (two positionals plus
#: ``--reason``/``--undo``/``--repository``/``--cwd``). Under "docs win" the spec is the document
#: that is wrong here, and the gap is reported to the owner: `yank` appears nowhere under
#: ``openspec/``. It is **not** in :data:`MUTATING_COMMANDS`: PyPI has no yank API, so the command
#: only reads the index and prints the browser steps (owner ruling, Session 6 addendum).
COMMANDS = (
    "init",
    "add",
    "version",
    "status",
    "publish",
    "publish-plan",
    "build",
    "yank",
    "git-tag",
    "pre",
)

#: Command names the app MAY register beyond :data:`COMMANDS`. Only the deprecated ``git-tag``
#: alias qualifies (``cli-shell`` spec, "Deprecated command alias"); whether it is a registered
#: command or rewritten during parsing is an implementation choice.
ALLOWED_EXTRA_COMMANDS = frozenset({"tag"})

#: Commands that mutate the working tree, and therefore carry ``--dry-run``
#: (``website/docs/cli/overview.md:44``). ``yank`` is deliberately **absent**: PyPI exposes no API
#: for yanking, so ``molt yank`` only reads the index and prints the browser steps. It never
#: mutates, so every run is already a dry run and ``--dry-run`` would be noise
#: (``website/docs/cli/yank.md``, "Why this is a manual step").
MUTATING_COMMANDS = ("add", "version", "publish", "git-tag", "build")

#: CLI command name -> the ``molt.commands`` module that implements it (pinned decision 1).
COMMAND_MODULES = {
    "init": "init",
    "add": "add",
    "version": "version",
    "status": "status",
    "publish": "publish",
    "publish-plan": "publish_plan",
    "build": "build",
    "yank": "yank",
    "git-tag": "git_tag",
    "pre": "pre",
}

#: Module prefixes that ``import molt.cli`` must NOT pull in (D7 lazy command imports). The bare
#: ``molt.commands`` package is allowed -- it is an empty namespace; only its *members* are heavy.
HEAVY_MODULE_PREFIXES = (
    "molt.commands.",
    "molt.engine",
    "molt.config",
    "molt.apply",
    "molt.changelog",
    "molt.changeset",
    "molt.publish",
    "molt.forge",
    "molt.versioning",
)

#: The startup banner (cli-shell spec, "Startup banner gating"). The glyph is written as an escape
#: on purpose: a raw non-ASCII character in this file would be unreadable on a cp1252 console and
#: violates the project's ASCII-only test-source rule.
BANNER_GLYPH = "\U0001f98b"
BANNER_TEXT = f"molt v{__version__}"

#: What the glyph may degrade to when the console cannot encode it (overview.md:50, terminal-ui
#: spec "Emoji banner on a legacy code page"). ``errors="replace"`` yields U+FFFD on decode and
#: ``?`` on a cp1252 encode; both are visible placeholders. Dropping the glyph *silently* is not
#: a degradation, it is a different banner.
DEGRADED_GLYPH_MARKERS = (chr(0xFFFD), "?")


# --------------------------------------------------------------------------------------
# Harness -- local doubles and helpers (the shared harness in fake_cli.py is frozen)
# --------------------------------------------------------------------------------------


class RunRecorder:
    """Stands in for ``molt.commands.<name>.run`` and captures how the shell called it.

    Pinned decision 2: every command entry point is ``run(...)``, receiving ``cwd=<Path>`` plus one
    keyword per long flag. This recorder is the molt equivalent of the reference's
    ``vi.mock("./commands/<x>/index.ts")`` + ``expect(fn).toHaveBeenCalledWith(options)``
    (``cli.test.ts:13-21, 289-290``).
    """

    def __init__(
        self,
        *,
        result: Any = None,
        raises: BaseException | None = None,
        prints: str | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.positional: list[tuple[Any, ...]] = []
        self._result = result
        self._raises = raises
        self._prints = prints

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.positional.append(args)
        self.calls.append(dict(kwargs))
        if self._prints is not None:
            # Stands in for a command writing its machine-readable document to stdout
            # (`status --output json`, `publish-plan --output json`). Written with a raw
            # `sys.stdout.write` on purpose: a real command's JSON payload does NOT go through the
            # Console protocol, which is exactly what makes the banner's stream matter.
            sys.stdout.write(self._prints)
        if self._raises is not None:
            raise self._raises
        return self._result

    @property
    def kwargs(self) -> dict[str, Any]:
        """The keyword arguments of the single expected call."""
        assert len(self.calls) == 1, f"expected exactly one run() call, got {len(self.calls)}"
        return self.calls[0]


def record_run(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    *,
    result: Any = None,
    raises: BaseException | None = None,
    prints: str | None = None,
) -> RunRecorder:
    """Replace ``molt.commands.<command>.run`` with a :class:`RunRecorder`.

    Patching the *module attribute* only works because each command wrapper imports its
    implementation lazily inside the body (``design.md`` D7 / ``tasks.md`` 3.3); a module-level
    ``from molt.commands.add import run`` in ``cli.py`` would bind the original and make every
    routing row below untestable.
    """
    module_name = f"molt.commands.{COMMAND_MODULES[command]}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:  # pragma: no cover - lights up when the module lands
        pytest.skip(f"{module_name} not implemented yet (pinned decision 1: molt.commands.<name>)")
    recorder = RunRecorder(result=result, raises=raises, prints=prints)
    monkeypatch.setattr(module, "run", recorder)
    return recorder


def split_streams(result: Any) -> tuple[str, str]:
    """``(stdout, stderr)`` from a ``CliRunner`` result, VT-stripped, or skip if merged.

    Click separated the two buffers in 8.2; Typer 0.20+ vendors its own copy of Click, so which
    behavior applies depends on what ``uv.lock`` resolves. A merged runner cannot observe the
    stream split at all, so a row that depends on it skips rather than passing vacuously.
    """
    try:
        stderr = result.stderr
    except ValueError:  # pragma: no cover - depends on the resolved click version
        pytest.skip("this CliRunner merges stdout and stderr; the stream split is unobservable")
    return strip_ansi(result.stdout), strip_ansi(stderr)


def console_singleton() -> Any:
    """``molt.ui.console.console`` -- the one adapter instance the shell writes through.

    ASSUMED SEAM (terminal-ui spec, "Console output adapter"; ``tasks.md`` 2.2): the module
    exposes a module-level ``console`` object implementing the protocol. Skips the calling test
    while the ``ui/`` seam is unbuilt, the same way the ``RichConsole`` rows do.
    """
    module = pytest.importorskip(
        "molt.ui.console", reason="build step 6 - the ui/ seam lands with the cli-shell change"
    )
    instance = getattr(module, "console", None)
    if instance is None:
        pytest.skip("molt.ui.console.console not implemented yet (assumed seam, tasks.md 2.2)")
    return instance


def cli_text(result: Any) -> str:
    """Everything the user would see from a ``CliRunner`` result, VT-stripped.

    Click's runner split ``stdout`` and ``stderr`` into separate buffers in 8.2 (``result.output``
    used to carry both); Typer 0.20+ vendors its own copy of Click, so which behavior applies
    depends on what ``uv.lock`` resolves. Human output lives on stderr under the stream contract
    pinned below, so a helper that reads only ``result.output`` would see nothing on a split
    runner; this one merges both. Use :func:`split_streams` where the *split itself* is the
    assertion.
    """
    text = strip_ansi(result.output)
    with contextlib.suppress(ValueError):
        stderr = strip_ansi(result.stderr)
        if stderr and stderr not in text:
            text = f"{text}\n{stderr}"
    return text


def run_main(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], argv: list[str]
) -> tuple[int, str]:
    """Drive ``molt.cli.main()`` (the console-script entry) and return ``(exit_code, output)``.

    The error funnel lives in ``main()``, not in the Typer app (``design.md`` D5), so
    ``CliRunner`` -- which catches exceptions itself -- cannot observe it. ``capfd`` captures at the
    file-descriptor level so output is seen even if the console adapter cached a stream reference.
    """
    monkeypatch.setattr(sys, "argv", ["molt", *argv])
    raw: Any = None
    try:
        raw = molt_cli.main()
    except SystemExit as exc:  # a funnel that exits rather than returning
        raw = exc.code
    captured = capfd.readouterr()
    code = 0 if raw is None else raw if isinstance(raw, int) else 1
    return code, strip_ansi(captured.out + captured.err)


def click_group() -> Any:
    """The Click group behind the Typer app, for parameter introspection.

    Reading the parser rather than the rendered help text: ``rich`` wraps the options column, so a
    long flag such as ``--from-publish-plan`` can be split across lines and make a substring
    assertion on the help output silently false.
    """
    # pyrefly: ignore[missing-import]  -- typer lands with the adopt-typer-cli-shell change.
    from typer.main import get_command

    return get_command(app)


def option_flags(command: str) -> set[str]:
    """Every option spelling declared by ``command``, including negations and short aliases.

    Click synthesizes ``--help`` at parse time, so it is deliberately absent from ``params`` --
    ``-h``/``--help`` are asserted by invoking them instead.
    """
    group = click_group()
    subcommand = group.commands[command]
    flags: set[str] = set()
    for param in subcommand.params:
        flags.update(param.opts)
        flags.update(param.secondary_opts)
    return flags


def comparable(value: Any) -> Any:
    """Render a recorded option value comparable to the literal a routing row spells.

    Path-typed options (``--cwd``, ``--out-dir``) arrive as ``Path``; the rows assert the *string*
    the user typed. Numbers are deliberately left alone so the "numeric values stay str" rule
    (research section 1.3 rule 4) cannot be satisfied by an accidental coercion here.
    """
    return str(value) if isinstance(value, PurePath) else value


def observed(recorder: RunRecorder, expected: dict[str, Any]) -> dict[str, Any]:
    """Project the recorded keywords onto the keys ``expected`` names; absent reads as ``None``.

    A subset projection, not an equality check: every command also receives ``cwd``, ``dry_run``,
    ``non_interactive`` and its own defaults, which a routing row has no business enumerating.
    Presence *is* asserted -- an expectation of ``"pkg-a"`` fails when the keyword is missing.
    """
    return {key: comparable(recorder.kwargs.get(key)) for key in expected}


@pytest.fixture(autouse=True)
def _stable_cli_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the ambient environment the shell reads.

    ``MOLT_OUTPUT`` back-fills ``--output`` (research section 1.3 rule 6), so a developer who
    exports it would silently change every routing row. ``COLUMNS`` keeps ``rich`` from wrapping the
    issue-report URL mid-token, which the error-funnel assertions read.
    """
    monkeypatch.delenv("MOLT_OUTPUT", raising=False)
    monkeypatch.setenv("COLUMNS", "500")
    monkeypatch.setenv("NO_COLOR", "1")


# --------------------------------------------------------------------------------------
# Command table + help (cli-shell spec: "All commands are discoverable", "Help is available")
# --------------------------------------------------------------------------------------


def test_help_lists_every_command_in_the_table() -> None:
    """cli-shell spec, "All commands are discoverable"; research section 1.4 command table.

    Two assertions, and the second is the one that matters. A subset check ("every name in
    ``COMMANDS`` appears in the help text") passes just as happily when the app registers a
    command this file has never heard of -- which is exactly how ``yank`` shipped documented,
    reachable and completely unspecified. The equality check over the registered command map makes
    a new verb impossible to add without adding its row here.
    """
    result = invoke_cli(app, ["--help"])

    assert result.exit_code == 0
    text = cli_text(result)
    assert [name for name in COMMANDS if name not in text] == []
    registered = set(click_group().commands) - ALLOWED_EXTRA_COMMANDS
    assert registered == set(COMMANDS), (
        "every registered command needs a row in COMMANDS (and its flags, routing and module "
        "mapping below); an undeclared command is an unspecified command"
    )


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_has_help_and_exits_zero(command: str) -> None:
    """cli-shell spec, "Help is available for the program and every command"."""
    result = invoke_cli(app, [command, "--help"])

    assert result.exit_code == 0, cli_text(result)
    assert command in cli_text(result)


def test_pack_is_not_a_command() -> None:
    """``design.md`` Open Questions: ``pack`` is renamed ``build`` with **no** alias.

    Asserted on the registered command map rather than the help text, because ``build``'s own help
    legitimately mentions "the pack stage" (``website/docs/cli/pack.md``).
    """
    registered = click_group().commands

    assert "build" in registered
    assert "pack" not in registered
    assert invoke_cli(app, ["pack"]).exit_code != 0


# The flag matrix each command must declare, from the accepted docs + `tasks.md` 3.4. Global
# options (`--cwd`, `--non-interactive`/`--yes`, `-h/--help`) are asserted separately, for every
# command, so they cannot silently regress on one of them.
COMMAND_FLAG_CASES = [
    # (command,        flags,                                                     why)
    (
        "add",
        ("--empty", "--open", "--since", "--message", "-m", "--major", "--minor", "--patch"),
        "research section 1.4 add row; cli.ts:73-93",
    ),
    (
        "add",
        ("--package", "-p", "--bump", "--stdin"),
        "molt-NEW non-interactive selection (add.md:55-62; 06-cli-commands.md molt-NEW list)",
    ),
    (
        "version",
        ("--ignore", "--snapshot", "--snapshot-name", "--pre"),
        "version.md:41-47; --snapshot* per design.md D4; --pre replaces the `pre` command",
    ),
    (
        "status",
        ("--since", "--verbose", "-v", "--output"),
        "status.md:28-32; tasks.md 3.4",
    ),
    (
        "status",
        ("-o",),
        "DOCS GAP (reported): cli.ts:117 and cli.test.ts:221-227 pin `-o` as the --output alias "
        "and the -v/-o pair is the ported row; status.md:30 lists only the long form. Kept, "
        "because the routing row below types `-o` -- the docs need the alias added",
    ),
    (
        "publish",
        ("--filter", "--repository", "--index-url", "--from-pack-dir", "--git-tag", "--output"),
        "publish.md:42-50",
    ),
    (
        "publish-plan",
        ("--filter", "--repository", "--index-url", "--output"),
        "publish.md:52 -- publish-plan takes --filter, --repository/--index-url, --output, --cwd",
    ),
    ("build", ("--out-dir", "--from-publish-plan"), "pack.md:29-33"),
    (
        "yank",
        ("--reason", "--undo", "--repository"),
        "yank.md:24-33 -- read-only, so no --dry-run and no --yes; --cwd is a global",
    ),
    ("git-tag", ("--output",), "git-tag.md:41-44"),
]


@pytest.mark.parametrize(("command", "flags", "why"), COMMAND_FLAG_CASES)
def test_each_command_declares_its_flags(command: str, flags: tuple[str, ...], why: str) -> None:
    """Even a placeholder command SHALL declare its flags (cli-shell spec, "Declared but
    unimplemented command"). Read from the parser, not the rendered help, so a wrapped help
    column cannot make the assertion vacuous."""
    declared = option_flags(command)

    assert [flag for flag in flags if flag not in declared] == [], why


def test_publish_declares_the_negated_git_tag_flag() -> None:
    """``--git-tag`` / ``--no-git-tag`` with default ``True`` (research section 1.4;
    publish.md:46)."""
    assert {"--git-tag", "--no-git-tag"} <= option_flags("publish")


def test_init_declares_only_the_global_options() -> None:
    """init.md:34-36 -- ``init`` adds nothing of its own to the global set.

    Asserted as an upper bound, not as an empty ``for`` over an empty tuple: a row spelled
    ``("init", (), ...)`` in the matrix above makes its comprehension trivially empty, so it can
    never fail no matter what ``init`` declares. This form fails the moment ``init`` grows an
    undocumented flag.
    """
    assert option_flags("init") <= {"--cwd", "--non-interactive", "--yes"}


@pytest.mark.parametrize("command", COMMANDS)
def test_global_options_are_on_every_command(command: str) -> None:
    """overview.md:41-48 -- ``--cwd``, ``--non-interactive``/``--yes`` and ``-h/--help`` are global.

    ``--non-interactive`` is molt-NEW: the JS CLI has no ``--yes`` anywhere (research section
    9.3, section 11.7 item 7). Declaring it once per command is what makes the single prompt
    choke point reachable.

    ``-h`` is asserted by *invoking* it: Click synthesizes the help option at parse time, so it
    never appears in ``Command.params`` and an introspection check would be unsatisfiable.
    """
    declared = option_flags(command)

    assert "--cwd" in declared
    assert "--non-interactive" in declared
    assert "--yes" in declared
    assert invoke_cli(app, [command, "-h"]).exit_code == 0, "-h is an alias of --help everywhere"


@pytest.mark.parametrize("command", MUTATING_COMMANDS)
def test_dry_run_is_on_every_mutating_command(command: str) -> None:
    """overview.md:44 -- ``--dry-run`` prints the plan and writes nothing."""
    assert "--dry-run" in option_flags(command)


def test_program_version_flag_is_not_declared_on_a_command() -> None:
    """``--version`` is program-level only (overview.md:47); commands must not shadow it."""
    for command in COMMANDS:
        assert "--version" not in option_flags(command), command
    assert invoke_cli(app, ["status", "--version"]).exit_code != 0


# --------------------------------------------------------------------------------------
# Import-light (cli-shell spec: "Importing the CLI module is import-light"; design.md D7)
# --------------------------------------------------------------------------------------


def test_importing_the_cli_does_not_import_command_implementations() -> None:
    """``tasks.md`` 7.2 -- ``import molt.cli`` must not pull in the engine, config or commands.

    Technique: snapshot ``sys.modules``, evict every ``molt*`` entry, re-import ``molt.cli``
    fresh, read the leak set, then put the snapshot back. This is sound because the restore puts
    back the *same module objects*, so the module-level ``app`` this file holds and every other
    test's imports keep referring to exactly what they referred to before. A subprocess would also
    work but would not see ``uv``'s ephemeral environment consistently.

    The restore deletes only what the re-import *added* rather than calling ``sys.modules.clear()``.
    Clearing evicts third-party modules too -- ``molt.cli`` importing ``rich.box`` for the first
    time inside the window would leave ``rich.box`` in the snapshot but absent from the live table
    only until the ``update``, and any module imported by a *later* test would then be a second,
    distinct object for the same name. Deleting the delta cannot desynchronize anything.
    """
    saved = dict(sys.modules)
    try:
        for name in [n for n in sys.modules if n == "molt" or n.startswith("molt.")]:
            del sys.modules[name]
        importlib.invalidate_caches()
        importlib.import_module("molt.cli")
        leaked = sorted(n for n in sys.modules if n.startswith(HEAVY_MODULE_PREFIXES))
    finally:
        for name in set(sys.modules) - set(saved):
            del sys.modules[name]
        sys.modules.update(saved)

    assert leaked == [], "command implementations must be imported inside the command body (D7)"


# --------------------------------------------------------------------------------------
# Default-command dispatch (cli-shell spec: "Default command dispatch"; design.md D1)
# --------------------------------------------------------------------------------------


def test_bare_invocation_runs_add(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare ``molt`` runs ``add`` via the ``!`` default-command alias (cli.ts:78; section
    10.26)."""
    add = record_run(monkeypatch, "add")

    result = invoke_cli(app, [])

    assert result.exit_code == 0, cli_text(result)
    assert len(add.calls) == 1


def test_a_leading_option_with_no_command_runs_add(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli-shell spec, "Leading option with no command runs add"; ``molt --open`` == ``molt add
    --open`` (design.md D1). Click's group parser rejects an unknown option *before*
    ``resolve_command`` runs, so this cannot be satisfied by the D1 snippet alone."""
    add = record_run(monkeypatch, "add")

    result = invoke_cli(app, ["--open"])

    assert result.exit_code == 0, cli_text(result)
    assert observed(add, {"open": True}) == {"open": True}


def test_an_explicit_command_is_not_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli-shell spec, "An explicit command is not overridden": ``molt version`` is not ``add``."""
    add = record_run(monkeypatch, "add")
    version = record_run(monkeypatch, "version")

    result = invoke_cli(app, ["version"])

    assert result.exit_code == 0, cli_text(result)
    assert len(version.calls) == 1
    assert add.calls == []


def test_the_default_command_does_not_swallow_help_or_version() -> None:
    """``--help`` and ``--version`` are group options, not ``add`` options.

    Rerouting *every* leading option to ``add`` (the naive reading of D1) would send ``--help`` to
    ``add``'s help and ``--version`` to an unknown-option error.
    """
    help_result = invoke_cli(app, ["--help"])
    version_result = invoke_cli(app, ["--version"])

    assert help_result.exit_code == 0
    assert "add" in cli_text(help_result), "group help lists commands, not add's own options"
    assert version_result.exit_code == 0
    assert cli_text(version_result).strip() == __version__


# --------------------------------------------------------------------------------------
# Deprecated `tag` alias (cli-shell spec: "Deprecated command alias"; research section 1.6)
# --------------------------------------------------------------------------------------


def test_deprecated_tag_alias_warns_and_runs_git_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli.ts:166-178 / section 9.2 -- "The 'tag' command is deprecated. Please use
    'git-tag' instead."."""
    git_tag = record_run(monkeypatch, "git-tag")

    result = invoke_cli(app, ["tag"])

    assert result.exit_code == 0, cli_text(result)
    assert len(git_tag.calls) == 1, "the alias executes the git-tag behavior, it does not just warn"
    text = cli_text(result)
    assert "deprecated" in text.lower()
    assert "git-tag" in text


def test_the_canonical_git_tag_name_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli-shell spec, "Canonical name does not warn"."""
    git_tag = record_run(monkeypatch, "git-tag")

    result = invoke_cli(app, ["git-tag"])

    assert result.exit_code == 0, cli_text(result)
    assert len(git_tag.calls) == 1
    assert "deprecated" not in cli_text(result).lower()


def test_the_alias_is_resolved_before_the_default_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """``resolve_command`` ordering (design.md D1): the alias branch runs first.

    ``molt tag --output x`` must reach ``git-tag`` carrying its option -- not be treated as a
    leading-option invocation of the default command, and not lose the deprecation warning once
    an option is present.
    """
    add = record_run(monkeypatch, "add")
    git_tag = record_run(monkeypatch, "git-tag")

    result = invoke_cli(app, ["tag", "--output", "events.ndjson"])

    assert result.exit_code == 0, cli_text(result)
    assert add.calls == [], "an alias is never rewritten into the default command"
    assert observed(git_tag, {"output": "events.ndjson"}) == {"output": "events.ndjson"}
    assert "deprecated" in cli_text(result).lower()


# --------------------------------------------------------------------------------------
# Banner gating (cli-shell spec: "Startup banner gating"; design.md D2; research section 1.2)
# --------------------------------------------------------------------------------------


def test_banner_is_printed_once_before_a_matched_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """index.ts:21-23 -- ``intro()`` fires for every matched command, after argument validation.

    "Exactly once" is the assertion that matters: a banner printed from both the group callback
    and the command wrapper (the obvious D2 mistake) would still contain the string.

    The glyph is asserted too. ``BANNER_TEXT`` alone is satisfied by a shell that prints a bare
    ``molt v0.1.0``, but overview.md:50 pins "``molt v<version>``, prefixed with a butterfly
    glyph" and the terminal-ui spec makes degrading it a *Windows encoding* concession, not a
    free choice -- so a run whose stream can encode the glyph must show it, and one that cannot
    must still show a visible placeholder rather than nothing.
    """
    record_run(monkeypatch, "status")

    result = invoke_cli(app, ["status"])

    assert result.exit_code == 0, cli_text(result)
    text = cli_text(result)
    assert text.count(BANNER_TEXT) == 1
    prefix = text.split(BANNER_TEXT, 1)[0].rsplit("\n", 1)[-1]
    assert BANNER_GLYPH in prefix or any(marker in prefix for marker in DEGRADED_GLYPH_MARKERS), (
        f"the banner leads with the butterfly glyph (overview.md:50); got {prefix!r}"
    )


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_no_banner_for_help_or_version(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], flag: str
) -> None:
    """cli-shell spec, "No banner for help or version" -- no matched command, no banner.

    A deliberate divergence from upstream, which *replaces* the help header with the banner
    (``cli.ts:11-14``); molt's accepted spec emits help text only.

    Checked through **both** entry points. Driving only the Typer app would miss a banner emitted
    from ``main()`` before the app is invoked at all -- the one placement that leaks it into every
    invocation, and the mutation that survived the first pass of this file.
    """
    assert BANNER_TEXT not in cli_text(invoke_cli(app, [flag]))

    code, text = run_main(monkeypatch, capfd, [flag])

    assert code == 0
    assert BANNER_TEXT not in text, "the banner must not be printed ahead of argument parsing"


def test_version_prints_the_bare_version_string_and_exits_zero() -> None:
    """cli-shell spec, "``--version`` prints the bare version"; cli.ts:17 (``outputVersion``).

    Exactly the version, no prefix -- so an eager ``--version`` that ran *after* the group
    callback (leaking the banner) fails here, which is D2's stated risk.
    """
    result = invoke_cli(app, ["--version"])

    assert result.exit_code == 0
    assert cli_text(result).strip() == __version__


def test_the_banner_glyph_is_not_encodable_on_cp1252() -> None:
    """Sanity check for the degradation test below: the glyph really is the hard case.

    Kept as an escape rather than a literal so this source file stays ASCII-only (project rule).
    """
    with pytest.raises(UnicodeEncodeError):
        BANNER_GLYPH.encode("cp1252")


def test_console_degrades_the_banner_on_a_cp1252_stream() -> None:
    """terminal-ui spec, "Emoji banner on a legacy code page"; ``tasks.md`` 2.2 / 7.8.

    Assumed seam: ``molt.ui.console.RichConsole(file=...)`` is the concrete implementation of the
    ``Console`` protocol (a Protocol cannot be instantiated, so the test needs the class name).
    Flagged for owner sign-off.
    """
    console_module = pytest.importorskip(
        "molt.ui.console", reason="build step 6 - the ui/ seam lands with the cli-shell change"
    )
    factory = getattr(console_module, "RichConsole", None)
    if factory is None:
        pytest.skip("molt.ui.console.RichConsole not implemented yet (assumed seam, tasks.md 2.2)")

    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="")
    console = factory(file=stream)
    console.info(f"{BANNER_GLYPH} {BANNER_TEXT}")
    stream.flush()

    written = stream.buffer.getvalue().decode("cp1252")
    assert BANNER_TEXT in written, "the text survives; only the unencodable glyph degrades"


# --------------------------------------------------------------------------------------
# Stream contract + the console adapter (terminal-ui spec: "Console output adapter",
# "Levelled messages are distinguishable"; overview.md:45 + :50)
#
# PINNED DECISION (needs owner sign-off -- nothing currently states it):
#
#   Every human-facing byte -- the startup banner and all levelled Console output
#   (info/success/warn/error/note, spinners, progress) -- goes to **stderr**.
#   **stdout carries machine-readable payloads only**: the `--output json` document of
#   `status`/`publish-plan`, and nothing else.
#
# Two accepted statements collide without it. overview.md:45 promises `--output json` emits the
# plan "to stdout"; overview.md:50 says the banner "prints once before a matched command's
# output". A conforming implementation that reads both literally emits `<glyph> molt v0.1.0`
# ahead of the JSON on the same stream, and `molt status --output json | jq` -- the documented CI
# recipe -- dies on `parse error: Invalid numeric literal`. Routing human output to stderr fixes
# it without a per-command `--quiet`, keeps `2>/dev/null` meaningful, and is what every CLI with
# a machine payload on stdout does.
# --------------------------------------------------------------------------------------


def test_shell_output_goes_through_the_console_adapter(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """terminal-ui spec, "Shell output goes through the console adapter"; ``tasks.md`` 2.2.

    Previously deferred as "architectural" and therefore untested. It is not: swapping the single
    ``molt.ui.console.console`` instance for a recorder and then asserting the banner reached the
    recorder *and* nothing reached the raw file descriptors is a complete runtime check. A shell
    that called ``print()`` or built its own ``rich.Console`` writes to the fd and fails here.
    """
    console_module = pytest.importorskip(
        "molt.ui.console", reason="build step 6 - the ui/ seam lands with the cli-shell change"
    )
    if getattr(console_module, "console", None) is None:
        pytest.skip("molt.ui.console.console not implemented yet (assumed seam, tasks.md 2.2)")
    recorder = RecordingConsole()
    monkeypatch.setattr(console_module, "console", recorder)
    if getattr(molt_cli, "console", None) is not None:
        # A shell that bound the singleton at import time keeps its own reference; patch that too
        # rather than failing this row for a legal (if less swappable) import style.
        monkeypatch.setattr(molt_cli, "console", recorder)
    record_run(monkeypatch, "status")

    code, text = run_main(monkeypatch, capfd, ["status"])

    assert code == 0, text
    assert recorder.contains(BANNER_TEXT), "the banner is emitted through the Console protocol"
    assert BANNER_TEXT not in text, "... and never with a bare print() or a private rich.Console"


def test_every_console_level_writes_to_stderr(capfd: pytest.CaptureFixture[str]) -> None:
    """terminal-ui spec, "Levelled messages are distinguishable" -- the ``error``-to-stderr half.

    Asserted against the **product** adapter, not the ``RecordingConsole`` double: the double's
    own self-test (``test_fake_cli.py::test_console_routes_each_level_to_its_own_bucket``) proves
    the double buckets levels, which says nothing about where ``RichConsole`` actually writes.
    That gap is the root cause of the ``--output json | jq`` collision above.

    All five levels are asserted, not only ``error``, because the pinned decision in this
    section's header is "human output is stderr output" -- an ``info`` on stdout would put the
    banner back in the JSON.
    """
    console = console_singleton()
    capfd.readouterr()

    console.info("INFO-MARKER")
    console.success("SUCCESS-MARKER")
    console.warn("WARN-MARKER")
    console.error("ERROR-MARKER")
    console.note("NOTE-TITLE", "NOTE-MARKER")

    captured = capfd.readouterr()
    out, err = strip_ansi(captured.out), strip_ansi(captured.err)
    for marker in ("INFO-MARKER", "SUCCESS-MARKER", "WARN-MARKER", "ERROR-MARKER", "NOTE-MARKER"):
        assert marker in err, f"{marker} is human output and belongs on stderr"
        assert marker not in out, f"{marker} must not pollute the machine-readable stream"


def test_console_levels_are_visually_distinguishable(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """terminal-ui spec: "each renders its message with a distinct level style".

    The four levels are called with the **same** message text, so the only thing that can make the
    rendered lines differ is the level styling itself. Colour is forced on (``FORCE_COLOR``, no
    ``NO_COLOR``) because that is the condition the scenario describes; the autouse fixture sets
    ``NO_COLOR=1`` for every other row in this file, which would erase exactly what is under test.
    """
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    console = console_singleton()
    capfd.readouterr()

    for level in ("info", "success", "warn", "error"):
        getattr(console, level)("SAME-MESSAGE")

    captured = capfd.readouterr()
    rendered = [
        line for line in (captured.out + captured.err).splitlines() if "SAME-MESSAGE" in line
    ]

    assert len(rendered) == 4, "one line per level"
    assert len(set(rendered)) == 4, f"levels must not render identically: {rendered}"


def test_the_banner_is_written_to_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """The banner is human output, so it goes to stderr (pinned decision, this section)."""
    record_run(monkeypatch, "status")

    result = invoke_cli(app, ["status"])

    assert result.exit_code == 0, cli_text(result)
    stdout, stderr = split_streams(result)
    assert BANNER_TEXT in stderr
    assert BANNER_TEXT not in stdout


def test_output_json_leaves_stdout_parseable_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """``molt status --output json | jq`` -- the recipe ``guides/status.md`` tells CI to run.

    ``json.loads`` over the **whole** of stdout is the assertion, exactly as
    ``test_status.py::plan_json`` does it at the ``run()`` seam. The difference is that this row
    drives the real shell, so it also sees everything the shell adds around the command: the
    banner, a deprecation warning, a spinner frame. Any of them on stdout and this fails.

    The command's payload is faked (`prints=`) rather than computed, deliberately: what is under
    test is the shell's stream discipline, which must hold before `molt.commands.status` exists.
    """
    payload = '{"changesets": [], "releases": []}\n'
    record_run(monkeypatch, "status", prints=payload)

    result = invoke_cli(app, ["status", "--output", "json"])

    assert result.exit_code == 0, cli_text(result)
    stdout, stderr = split_streams(result)
    assert json.loads(stdout) == {"changesets": [], "releases": []}, (
        "stdout is exactly one JSON document; the banner and every warning belong on stderr"
    )
    assert BANNER_TEXT in stderr, "the banner is still printed -- it just moved streams"


def test_commands_depend_on_the_prompts_protocol_not_the_library() -> None:
    """terminal-ui spec, "Commands depend on the protocol, not the library"; ``tasks.md`` 2.3.

    Previously deferred as "architectural". It is a static property of the source, so it is
    testable the same way the import-light row is: parse every module under ``molt/commands/`` and
    assert none of them reaches for ``questionary``. Both an AST scan (``import questionary``,
    ``from questionary import ...``) and a plain substring check are applied -- the AST catches
    the ordinary spellings with no false positives, the substring catches
    ``importlib.import_module("questionary")``, which no AST walk over import nodes would see.
    """
    spec = importlib.util.find_spec("molt.commands")
    if spec is None or not spec.submodule_search_locations:
        pytest.skip("molt.commands not implemented yet (pinned decision 1)")
    package_dir = Path(next(iter(spec.submodule_search_locations)))
    sources = sorted(package_dir.glob("*.py"))
    if not sources:
        pytest.skip("molt.commands is still an empty namespace")

    offenders: list[str] = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "questionary" or name.startswith("questionary.") for name in names):
                offenders.append(f"{path.name}: imports questionary")
        if "questionary" in text:
            offenders.append(f"{path.name}: names questionary in its source")

    assert offenders == [], (
        "command code prompts through the Prompts protocol only, so the prompt library stays "
        f"swappable without touching commands: {offenders}"
    )


# --------------------------------------------------------------------------------------
# Error funnel (cli-shell spec: "Error funnel"; design.md D5; research section 1.2)
# --------------------------------------------------------------------------------------


def test_main_reports_an_integer_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli-shell spec: ``main()`` returns an integer and never lets an exception escape."""
    record_run(monkeypatch, "status")
    monkeypatch.setattr(sys, "argv", ["molt", "status"])

    outcome: Any = None
    try:
        outcome = molt_cli.main()
    except SystemExit as exc:
        outcome = exc.code

    assert isinstance(outcome, int), "main() reports an integer exit code, never None or a string"


def test_internal_error_reports_a_cwd_redacted_issue_url(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """index.ts:27-49 -- a pre-filled "new issue" URL with the CLI + runtime versions, cwd redacted.

    The raised message embeds the real cwd so redaction is *observable*: an implementation that
    only redacts the traceback, or that forgets entirely, leaks an absolute path into a URL the
    user is invited to paste into a public issue tracker.
    """
    errors = pytest.importorskip(
        "molt.errors", reason="build step 6 - InternalError lands with the cli-shell change"
    )
    leaky = str(Path.cwd() / "pyproject.toml")
    record_run(monkeypatch, "status", raises=errors.InternalError(f"boom while reading {leaky}"))

    code, text = run_main(monkeypatch, capfd, ["status"])

    assert code == 1
    assert str(Path.cwd()) not in text, "the working directory must be redacted"
    assert "<cwd>" in text
    report = text.split(BANNER_TEXT, 1)[-1]
    assert "http" in report and "issues" in report, "an issue-report URL is offered"
    assert __version__ in report, "the URL carries the CLI version"
    assert platform.python_version() in report, "the URL carries the Python version"


def test_an_unexpected_exception_prints_a_traceback_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """index.ts:56-58 -- anything that is neither ``InternalError`` nor a user-facing exit error."""
    record_run(monkeypatch, "status", raises=RuntimeError("kaboom"))

    code, text = run_main(monkeypatch, capfd, ["status"])

    assert code == 1
    assert "Traceback" in text
    assert "kaboom" in text


def test_an_unexpected_exception_is_never_the_only_output(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """overview.md:86 -- molt never lets a bare traceback escape as the program's only output.

    Upstream closes with ``outro(c.red("Exited with code 1"))`` (index.ts:57-58); molt keeps that
    human-readable trailer so a user sees a sentence, not only a stack.
    """
    record_run(monkeypatch, "status", raises=RuntimeError("kaboom"))

    _, text = run_main(monkeypatch, capfd, ["status"])

    assert "Exited with code 1" in text


def test_a_user_facing_exit_error_exits_with_its_own_code(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """index.ts:51-54 -- ``ExitError`` carries the code; the funnel does not flatten it to 1."""
    errors = pytest.importorskip(
        "molt.errors", reason="build step 6 - the error hierarchy lands with the cli-shell change"
    )
    record_run(monkeypatch, "status", raises=errors.ExitError(3))

    code, _ = run_main(monkeypatch, capfd, ["status"])

    assert code == 3


# --------------------------------------------------------------------------------------
# Exit-code contract (cli-shell spec "Exit-code contract"; research section 11.6; overview.md:72-86)
# --------------------------------------------------------------------------------------

# `behavior` is resolved by `command_outcome` below. One row per *distinct shell behavior*, not
# one row per line of the documented table: the shell's whole job here is to carry an outcome out
# of `run()` and turn it into a process exit code, and it cannot see which *condition* produced
# that outcome.
EXIT_CODE_CASES = [
    # (command,  behavior,   code, why)
    ("status", "return", 0, "success -> 0"),
    ("add", "cancel", 0, "Ctrl-C at a prompt -> 0 (deliberate divergence from POSIX 130)"),
    ("add", "exit-1", 1, "any validation failure / user-facing exit error -> 1"),
    ("status", "exit-3", 3, "an exit error carries its own code, not a flattened 1"),
    ("status", "boom", 1, "unexpected exception -> 1"),
    ("status", "internal", 1, "InternalError -> 1"),
]

#: The remaining lines of overview.md:72-86. Each is **delegated**: at the shell seam it reduces to
#: a row above, because the shell only ever sees "``run`` returned" or "``run`` raised X". Listed
#: rather than parametrized because four more rows spelling `ExitError(1) -> 1` read as four
#: contract lines while testing one behavior -- which is how this table came to look like ten
#: assertions covering four. The *conditions* are pinned where they are decided:
#:
#: * status CI gate: changed packages, no changesets -> 1
#:       tests/cli/test_status.py::test_ci_gate_exits_one          (delegates to "exit-1")
#: * status: nothing relevant changed -> 0
#:       tests/cli/test_status.py::test_ci_gate_does_not_fire      (delegates to "return")
#: * version with no unreleased changesets -> 1 (v3 semantics)
#:       tests/cli/test_version.py                                 (delegates to "exit-1")
#: * publish with nothing to publish -> 0
#:       not yet written: `publish` is a later build step          (delegates to "return")
#: * a command whose implementation has not landed -> non-zero
#:       test_pre_is_a_migration_signpost_that_exits_non_zero


def command_outcome(behavior: str, errors: Any) -> BaseException | None:
    """Turn an exit-code row's behavior name into what the command's ``run`` does."""
    if behavior == "return":
        return None
    if behavior == "cancel":
        return KeyboardInterrupt()
    if behavior == "boom":
        return RuntimeError("kaboom")
    if behavior == "internal":
        return errors.InternalError("internal boom")
    return errors.ExitError(int(behavior.removeprefix("exit-")))


@pytest.mark.parametrize(("command", "behavior", "code", "why"), EXIT_CODE_CASES)
def test_exit_code_contract(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    command: str,
    behavior: str,
    code: int,
    why: str,
) -> None:
    """One row per distinct shell behavior (research section 11.6 / overview.md:72-86).

    The semantic lines of the documented table (``status`` CI gate, ``version`` with no
    changesets, ``publish`` with nothing to publish) are **not** rows here: the shell's job is to
    carry a code out of ``run()``, and it cannot see the condition that produced it. Each of them
    is listed in the "delegated lines" comment above, with the behavior it reduces to and the
    module that pins the condition.
    """
    errors = pytest.importorskip(
        "molt.errors", reason="build step 6 - the error hierarchy lands with the cli-shell change"
    )
    record_run(monkeypatch, command, raises=command_outcome(behavior, errors))

    observed_code, _ = run_main(monkeypatch, capfd, [command])

    assert observed_code == code, why


def test_a_cancelled_prompt_says_so_before_exiting_zero(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """cli-shell spec, "Cancelling a prompt exits 0"; ``cli-utilities.ts:14-21`` prints "Canceled".

    Exit 0 alone is indistinguishable from success -- section 10.5's footgun is precisely that
    CI reads a cancelled ``add`` as a pass -- so the message is part of the contract.

    ORACLE FINDING: typer's ``_main`` converts a ``KeyboardInterrupt`` into ``Exit(130)``
    (``typer/core.py``), so ``command.main(..., standalone_mode=False)`` *returns* 130 rather than
    letting the interrupt reach the funnel -- and 130 is exactly the POSIX code overview.md:77
    says molt does not use. ``main()`` must therefore drive the app through
    ``make_context``/``invoke`` (where the ``KeyboardInterrupt`` propagates and the funnel can own
    it) or explicitly map 130 back to 0. ``cancelable`` covers only the other half, the sentinel
    path where questionary *returns* ``None``; a raw interrupt never goes through it.
    """
    record_run(monkeypatch, "add", raises=KeyboardInterrupt())

    code, text = run_main(monkeypatch, capfd, ["add"])

    assert code == 0
    assert "Cancel" in text


def test_cancelable_is_the_single_uniform_cancellation_contract(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """terminal-ui spec, "Cancellation is a single, uniform contract"; ``tasks.md`` 2.3.

    The other half of the Ctrl-C story: ``questionary`` reports a cancelled prompt by *returning*
    ``None`` (research section 11.2), so one ``cancelable(value)`` helper -- not each command --
    turns that sentinel into "Canceled" + a clean exit 0, identically for every prompt type.
    """
    # pyrefly: ignore[missing-import]  -- typer lands with the adopt-typer-cli-shell change.
    import typer

    prompts_module = pytest.importorskip(
        "molt.ui.prompts", reason="build step 6 - the ui/ seam lands with the cli-shell change"
    )
    cancelable = getattr(prompts_module, "cancelable", None)
    if cancelable is None:
        pytest.skip("molt.ui.prompts.cancelable not implemented yet (tasks.md 2.3)")

    assert cancelable("an answer") == "an answer", "a real answer passes straight through"

    with pytest.raises(typer.Exit) as excinfo:
        cancelable(None)

    captured = capfd.readouterr()
    assert "Cancel" in strip_ansi(captured.out + captured.err)
    assert excinfo.value.exit_code == 0, "a cancellation is a clean exit 0"


def test_a_declared_but_unimplemented_command_still_declares_its_flags() -> None:
    """cli-shell spec, "Declared but unimplemented command" -- help works even without an impl.

    ``pre`` is molt's permanent instance of that contract: it is registered so the migration
    message has somewhere to live, and it never grows an implementation.
    """
    result = invoke_cli(app, ["pre", "--help"])

    assert result.exit_code == 0
    text = cli_text(result)
    assert "enter" in text or "exit" in text, "the positional argument is still declared"


def test_pre_is_a_migration_signpost_that_exits_non_zero() -> None:
    """DECISION (needs owner sign-off): reconciling two accepted-but-conflicting sources.

    ``specs/cli-shell/spec.md`` registers ``pre`` in the command table; ``website/docs/cli/pre.md``
    states molt has no pre mode to enter or exit (research README section 4.2 -- ``pre.json`` is
    dropped and its ``-next.N`` tags are illegal under PEP 440). Pinned reconciliation: the
    command exists only to point at the replacement.
    """
    result = invoke_cli(app, ["pre", "enter", "next"])

    assert result.exit_code != 0
    assert "molt version --pre" in cli_text(result)


# --------------------------------------------------------------------------------------
# Option normalization -- the pure function (design.md D3; research section 1.3)
# --------------------------------------------------------------------------------------


def normalize(raw: dict[str, Any], array: tuple[str, ...] = ()) -> dict[str, Any]:
    """Call ``molt.cli.normalize_options`` (assumed seam: ``(raw, *, array=())``, D3/tasks 5.1)."""
    func = getattr(molt_cli, "normalize_options", None)
    if func is None:
        pytest.skip("molt.cli.normalize_options not implemented yet (tasks.md 5.1)")
    return dict(func(raw, array=array))


NORMALIZE_CASES = [
    # (raw,                          array,      expected,               why)
    (
        {"message": "hello", "m": "hello"},
        (),
        {"message": "hello"},
        "cli.ts:28-31 rule 1 -- single-character alias keys are stripped, the long name survives",
    ),
    (
        {"since": ["main", "next"]},
        (),
        {"since": "next"},
        "cli.ts:45-47 / cli.test.ts:67-72 rule 2 -- repeated scalar takes the LAST value, silently",
    ),
    ({"since": "main"}, (), {"since": "main"}, "a single scalar is passed through untouched"),
    (
        {"major": "pkg-a"},
        ("major",),
        {"major": ["pkg-a"]},
        "cli.ts:34-43 / cli.test.ts:73-89 rule 3 -- a repeatable flag is a list even for one value",
    ),
    (
        {"patch": ["pkg-c", "pkg-d"]},
        ("patch",),
        {"patch": ["pkg-c", "pkg-d"]},
        "cli.test.ts:73-89 -- an already-repeated flag keeps every value, in order",
    ),
    (
        {"ignore": [1, 2]},
        ("ignore",),
        {"ignore": ["1", "2"]},
        "cli.ts:36-42 -- list members are stringified, never left numeric",
    ),
    (
        {"snapshot": 123},
        (),
        {"snapshot": "123"},
        "cli.ts:51-53 rule 4 -- numeric-looking values stay str (version/tag names)",
    ),
    (
        {"--": ["extra", "args"], "empty": True},
        (),
        {"empty": True},
        "cli.ts:24 / section 10.3 rule 5 -- everything after a bare -- is dropped; no passthrough",
    ),
    (
        {"since": None},
        (),
        {},
        "cli.ts:28-31 -- an unset option is removed rather than passed through as None",
    ),
    (
        {"message": ""},
        (),
        {"message": ""},
        "section 10.15 -- `-m ''` is an explicit empty summary, not an absent one",
    ),
    ({"empty": False}, (), {"empty": False}, "False is a value; only null/undefined is an absence"),
    ({}, (), {}, "an empty option set normalizes to itself"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("raw", "array", "expected", "why"), NORMALIZE_CASES)
def test_normalize_options(
    raw: dict[str, Any], array: tuple[str, ...], expected: dict[str, Any], why: str
) -> None:
    """``normalizeOptions`` (cli.ts:19-56) as a pure post-parse pass -- design.md D3.

    Kept unit-testable in isolation from Typer on purpose: the end-to-end routing rows below only
    observe the *combination* of Click's parser and this pass, so a rule deleted here could hide
    behind a Click default.
    """
    assert normalize(raw, array) == expected, why


@pytest.mark.unit
def test_normalize_options_does_not_mutate_its_input() -> None:
    """A pure function (D3): re-normalizing the same dict must be safe."""
    raw = {"since": ["main", "next"], "--": ["x"]}

    normalize(raw)

    assert raw == {"since": ["main", "next"], "--": ["x"]}


@pytest.mark.unit
def test_molt_output_backfills_the_output_option(monkeypatch: pytest.MonkeyPatch) -> None:
    """``withEnvOptions`` (cli.ts:59-63) rule 6, with ``CHANGESETS_OUTPUT`` renamed ``MOLT_OUTPUT``.

    One env var back-fills **both** meanings of ``--output`` (pinned decision 5): the JSON-on-stdout
    selector of ``status``/``publish-plan`` and the NDJSON file target of ``publish``/``git-tag``.
    """
    monkeypatch.setenv("MOLT_OUTPUT", "from-env.ndjson")

    assert normalize({}) == {"output": "from-env.ndjson"}
    assert normalize({"output": None}) == {"output": "from-env.ndjson"}


@pytest.mark.unit
def test_an_explicit_output_option_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """``options.output == null`` gates the back-fill (cli.ts:60): the flag always wins."""
    monkeypatch.setenv("MOLT_OUTPUT", "from-env.ndjson")

    assert normalize({"output": "explicit.json"}) == {"output": "explicit.json"}


# --------------------------------------------------------------------------------------
# Option routing -- the same rules end to end through the app (cli.test.ts:276-294)
# --------------------------------------------------------------------------------------

ROUTING_CASES = [
    # (command,     argv,                                     expected,          why)
    ("init", ["init"], {}, "cli.test.ts:41-49 -- init with no args"),
    ("add", ["add"], {}, "cli.test.ts:53-57 -- add with no args"),
    ("add", [], {}, "cli.ts:78 / section 10.26 -- bare `molt` is the default command"),
    ("add", ["--open"], {"open": True}, "a leading option with no command word still runs add"),
    (
        "add",
        ["add", "--empty", "--open", "--since", "main", "-m", "hello"],
        {"empty": True, "open": True, "since": "main", "message": "hello"},
        "cli.test.ts:58-66 -- -m is an alias; only the long name reaches the command",
    ),
    (
        "add",
        ["add", "--since", "main", "--since", "next"],
        {"since": "next"},
        "cli.test.ts:67-72 -- repeated scalar: last wins",
    ),
    (
        "add",
        ["add", "--major", "pkg-a", "--minor", "pkg-b", "--patch", "pkg-c", "--patch", "pkg-d"],
        {"major": ["pkg-a"], "minor": ["pkg-b"], "patch": ["pkg-c", "pkg-d"]},
        "cli.test.ts:73-89 -- repeatable flags collect; a single value is still a list",
    ),
    (
        "add",
        ["add", "--major", "pkg-a"],
        {"major": ["pkg-a"]},
        "the always-list rule, isolated: one occurrence must not degrade to a bare string",
    ),
    (
        "add",
        ["add", "-p", "pkg-a", "--package", "pkg-b", "--bump", "minor"],
        {"package": ["pkg-a", "pkg-b"], "bump": "minor"},
        "molt-NEW non-interactive selection (add.md:55-56); --package is a repeatable list flag",
    ),
    ("version", ["version"], {}, "cli.test.ts:96-99 -- version with no args"),
    (
        "version",
        ["version", "--ignore", "pkg-a", "--ignore", "pkg-b"],
        {"ignore": ["pkg-a", "pkg-b"]},
        "cli.test.ts:100-105 -- --ignore is repeatable",
    ),
    (
        "version",
        ["version", "--ignore", "pkg-a"],
        {"ignore": ["pkg-a"]},
        "one --ignore is still a list",
    ),
    (
        "version",
        ["version", "--snapshot"],
        {"snapshot": True},
        "cli.test.ts:106-111 / design.md D4 -- bare --snapshot means an unnamed snapshot",
    ),
    (
        "version",
        ["version", "--snapshot=pr-123"],
        {"snapshot": "pr-123"},
        "design.md D4 form (b) -- the equals spelling binds the optional value",
    ),
    (
        "version",
        ["version", "--snapshot-name", "pr-123"],
        {"snapshot": "pr-123"},
        "design.md D4 form (a) -- the dedicated flag collapses into the same `snapshot` keyword",
    ),
    (
        "version",
        ["version", "--snapshot-name", "123"],
        {"snapshot": "123"},
        "cli.ts:51-53 / version.md:43 -- a numeric-looking snapshot name stays a string",
    ),
    (
        "version",
        ["version", "--pre", "rc"],
        {"pre": "rc"},
        "research README section 4.2 -- --pre replaces the whole `pre` command; PEP 440 phase only",
    ),
    (
        "version",
        ["version", "--dry-run"],
        {"dry_run": True},
        "overview.md:44 -- --dry-run on every mutating command",
    ),
    ("status", ["status"], {}, "cli.test.ts:208-212 -- status with no args"),
    (
        "status",
        ["status", "--since", "main", "--verbose", "--output", "status.json"],
        {"since": "main", "verbose": True, "output": "status.json"},
        "cli.test.ts:213-220 -- the long spellings",
    ),
    (
        "status",
        ["status", "-v", "-o", "status.json"],
        {"verbose": True, "output": "status.json"},
        "cli.test.ts:221-227 / section 10.2 -- -v is --verbose here, NOT --version",
    ),
    (
        "status",
        ["status", "--output", "json"],
        {"output": "json"},
        "overview.md:58 -- on a read command --output selects the JSON document form",
    ),
    (
        "publish",
        ["publish"],
        {"git_tag": True},
        "cli.test.ts:129-133 -- --git-tag defaults to true",
    ),
    (
        "publish",
        ["publish", "--no-git-tag"],
        {"git_tag": False},
        "cli.test.ts:134-137 -- the negated form (research section 1.4, publish.md:46)",
    ),
    (
        "publish",
        ["publish", "--output", "events.ndjson"],
        {"output": "events.ndjson"},
        "molt-NEW: upstream has NO --output flag on publish, only the env var (section 10.1)",
    ),
    ("publish-plan", ["publish-plan"], {}, "cli.test.ts:169-172"),
    (
        "publish-plan",
        ["publish-plan", "--output", "publish-plan.json"],
        {"output": "publish-plan.json"},
        "cli.test.ts:173-178",
    ),
    (
        "build",
        ["build", "--out-dir", ".packed"],
        {"out_dir": ".packed"},
        "cli.test.ts:184-190 -- `pack` renamed `build` (research section 11.5)",
    ),
    (
        "build",
        ["build", "--from-publish-plan", "publish-plan.json", "--out-dir", ".packed"],
        {"from_publish_plan": "publish-plan.json", "out_dir": ".packed"},
        "cli.test.ts:191-202 -- dashes in a long flag become underscores in the keyword",
    ),
    ("git-tag", ["git-tag"], {}, "cli.test.ts:233-237"),
    (
        "git-tag",
        ["git-tag", "--output", "events.ndjson"],
        {"output": "events.ndjson"},
        "molt-NEW: upstream git-tag has NO --output flag either (section 10.1)",
    ),
    (
        "yank",
        ["yank", "acme-core", "1.1.0"],
        {"package": "acme-core", "version": "1.1.0"},
        "yank.md:27-28 -- two required positionals, in order; molt-NEW (PEP 592, no JS analogue)",
    ),
    (
        "yank",
        ["yank", "acme-core", "1.1.0", "--reason", "Corrupt wheel", "--undo"],
        {"package": "acme-core", "version": "1.1.0", "reason": "Corrupt wheel", "undo": True},
        "yank.md:29-30 -- the positionals survive alongside the options",
    ),
    (
        "yank",
        ["yank", "acme-core", "1.1.0", "--repository", "testpypi"],
        {"repository": "testpypi"},
        "yank.md:31 -- --repository names the index the version lives on",
    ),
    (
        "add",
        ["add", "--non-interactive"],
        {"non_interactive": True},
        "molt-NEW global flag (section 11.7 item 7); dashes become underscores",
    ),
    (
        "add",
        ["add", "--yes"],
        {"non_interactive": True},
        "overview.md:43 -- --yes is an alias, and the alias must not create a second keyword",
    ),
]


@pytest.mark.parametrize(("command", "argv", "expected", "why"), ROUTING_CASES)
def test_option_routing(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    argv: list[str],
    expected: dict[str, Any],
    why: str,
) -> None:
    """``cli.test.ts:276-294`` -- one row per (command, args) pair, asserting the parsed options.

    Upstream drives ``cli.parse(argv, {run:false})`` then ``runMatchedCommand()`` and asserts the
    mocked command fn was called once with the exact options object. molt's equivalent is Typer's
    ``CliRunner`` plus a recorder over the command module's ``run``.
    """
    recorder = record_run(monkeypatch, command)

    result = invoke_cli(app, argv)

    assert result.exit_code == 0, cli_text(result)
    assert len(recorder.calls) == 1, "the command runs exactly once"
    assert observed(recorder, expected) == expected, why


# An alias must *collapse*, not duplicate: the command sees one keyword, and the spelling the user
# typed is invisible to it. `observed()` above is a subset projection, so a shell that passed BOTH
# `snapshot="pr-123"` and `snapshot_name="pr-123"` (or both `non_interactive=True` and `yes=True`)
# satisfies every routing row while breaking the contract those rows' `why` columns state. That is
# what this table asserts and they cannot: the absence of the second keyword.
ALIAS_COLLAPSE_CASES = [
    # (command,  argv,                                  kept,  value,  collapsed, why)
    (
        "add",
        ["add", "--yes"],
        "non_interactive",
        True,
        "yes",
        "overview.md:43 -- --yes is an alias of --non-interactive, not a second option",
    ),
    (
        "add",
        ["add", "-m", "hello"],
        "message",
        "hello",
        "m",
        "cli.ts:28-31 rule 1 -- the single-character alias key is stripped, the long name survives",
    ),
    (
        "version",
        ["version", "--snapshot=pr-123"],
        "snapshot",
        "pr-123",
        "snapshot_name",
        "design.md D4 form (b) -- the equals spelling binds the value to `snapshot`",
    ),
    (
        "version",
        ["version", "--snapshot-name", "pr-123"],
        "snapshot",
        "pr-123",
        "snapshot_name",
        "design.md D4 form (a) -- the dedicated flag collapses into the same `snapshot` keyword",
    ),
    (
        "version",
        ["version", "--snapshot-name", "123"],
        "snapshot",
        "123",
        "snapshot_name",
        "cli.ts:51-53 / version.md:43 -- a numeric-looking snapshot name stays a string",
    ),
]


@pytest.mark.parametrize(
    ("command", "argv", "kept", "value", "collapsed", "why"), ALIAS_COLLAPSE_CASES
)
def test_an_alias_collapses_instead_of_duplicating(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    argv: list[str],
    kept: str,
    value: Any,
    collapsed: str,
    why: str,
) -> None:
    """The command receives the canonical keyword and **only** that one.

    ``type(...) is`` rather than ``==`` for the string cases: ``{"snapshot": True} ==
    {"snapshot": 1}`` in Python, and ``"123" == 123`` is False but ``True == 1`` is not -- so an
    equality-only assertion cannot tell a bool from an int, and a coerced numeric snapshot name
    would need the ``str`` check to be caught at all (research section 1.3 rule 4).
    """
    recorder = record_run(monkeypatch, command)

    result = invoke_cli(app, argv)

    assert result.exit_code == 0, cli_text(result)
    assert recorder.kwargs[kept] == value, why
    assert type(recorder.kwargs[kept]) is type(value), why
    assert collapsed not in recorder.kwargs, why


def test_an_absent_snapshot_flag_reaches_the_command_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``molt version`` with no snapshot flag: ``snapshot`` is absent or ``None``, never falsy-ish.

    The other half of the D4 contract. ``{}`` as an expectation (the plain ``["version"]`` routing
    row) asserts nothing about ``snapshot`` at all, so a shell that always passed
    ``snapshot=False`` -- or the empty string the bare ``--snapshot`` rewrite produces -- would
    look identical there while making the command's ``if snapshot is not None`` branch fire on
    every run.
    """
    recorder = record_run(monkeypatch, "version")

    result = invoke_cli(app, ["version"])

    assert result.exit_code == 0, cli_text(result)
    assert recorder.kwargs.get("snapshot") is None, "absent means None, not False and not ''"
    assert "snapshot_name" not in recorder.kwargs


def test_a_bare_snapshot_flag_is_exactly_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--snapshot`` with no value is the boolean ``True`` (design.md D4, cli.test.ts:106-111).

    ``is True`` and not ``== True``: ``1 == True`` in Python, so an implementation that handed the
    command an index, a count, or the string ``"1"`` coerced to ``int`` would satisfy the routing
    row's ``{"snapshot": True}`` expectation.
    """
    recorder = record_run(monkeypatch, "version")

    result = invoke_cli(app, ["version", "--snapshot"])

    assert result.exit_code == 0, cli_text(result)
    assert recorder.kwargs["snapshot"] is True
    assert "snapshot_name" not in recorder.kwargs


ENV_ROUTING_CASES = [
    # (command,      argv,             why)
    ("git-tag", ["git-tag"], "cli.test.ts:238-246 -- the env var back-fills the event stream path"),
    ("publish", ["publish"], "cli.test.ts:153-162 -- same back-fill on publish"),
    ("status", ["status"], "overview.md:60 -- the back-fill also covers the read commands"),
    ("publish-plan", ["publish-plan"], "overview.md:60 -- ... and publish-plan"),
]


@pytest.mark.parametrize(("command", "argv", "why"), ENV_ROUTING_CASES)
def test_molt_output_backfills_the_output_flag_end_to_end(
    monkeypatch: pytest.MonkeyPatch, command: str, argv: list[str], why: str
) -> None:
    """``withEnvOptions`` (cli.ts:59-63) with ``CHANGESETS_OUTPUT`` renamed to ``MOLT_OUTPUT``."""
    recorder = record_run(monkeypatch, command)

    result = invoke_cli(app, argv, env={"MOLT_OUTPUT": "from-env.ndjson"})

    assert result.exit_code == 0, cli_text(result)
    assert observed(recorder, {"output": "from-env.ndjson"}) == {"output": "from-env.ndjson"}, why


def test_an_explicit_output_flag_beats_molt_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli.ts:60 -- the env var only fills an *absent* option."""
    recorder = record_run(monkeypatch, "git-tag")

    result = invoke_cli(
        app, ["git-tag", "--output", "explicit.ndjson"], env={"MOLT_OUTPUT": "from-env.ndjson"}
    )

    assert result.exit_code == 0, cli_text(result)
    assert observed(recorder, {"output": "explicit.ndjson"}) == {"output": "explicit.ndjson"}


def test_arguments_after_a_bare_double_dash_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli.ts:24 / section 10.3 -- there is no pass-through args feature anywhere in the CLI.

    Click would otherwise hand ``extra``/``stuff`` to the command as positional arguments (or fail
    with "got unexpected extra arguments"), so dropping them is real work the shell must do.
    """
    add = record_run(monkeypatch, "add")

    result = invoke_cli(app, ["add", "--empty", "--", "extra", "stuff"])

    assert result.exit_code == 0, cli_text(result)
    assert observed(add, {"empty": True}) == {"empty": True}
    flattened = [
        item
        for value in add.kwargs.values()
        for item in (value if isinstance(value, list) else [value])
    ]
    assert "extra" not in flattened
    assert "stuff" not in flattened


def test_run_is_called_with_keyword_arguments_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pinned decision 2: ``run(cwd=..., **options)`` -- nothing is passed positionally.

    Positional passing would make every command's signature order part of the CLI contract.
    """
    status = record_run(monkeypatch, "status")

    invoke_cli(app, ["status"])

    assert status.positional == [()]
    assert "cwd" in status.kwargs


def test_cwd_defaults_to_the_process_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """overview.md:46 -- ``--cwd`` defaults to the current directory."""
    status = record_run(monkeypatch, "status")

    invoke_cli(app, ["status"])

    assert Path(status.kwargs["cwd"]).resolve() == Path.cwd().resolve()


def test_cwd_is_passed_through_as_a_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """overview.md:46 -- root discovery walks up from ``--cwd``, so it must arrive as a path."""
    status = record_run(monkeypatch, "status")

    result = invoke_cli(app, ["status", "--cwd", str(tmp_path)])

    assert result.exit_code == 0, cli_text(result)
    assert Path(status.kwargs["cwd"]).resolve() == tmp_path.resolve()


# --------------------------------------------------------------------------------------
# Optional-value `--snapshot` (cli-shell spec; design.md D4; version.md:49-59)
# --------------------------------------------------------------------------------------


def test_the_snapshot_space_form_is_rejected_with_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cli-shell spec, "Space form is rejected with guidance"; design.md D4 / version.md:59.

    Upstream accepts ``--snapshot pr-123`` (cli.test.ts:112-123); neither Click nor cyclopts can
    bind a space-separated optional value, so molt rejects it *loudly* rather than silently
    reading it as an unnamed snapshot plus a stray positional.
    """
    version = record_run(monkeypatch, "version")

    result = invoke_cli(app, ["version", "--snapshot", "pr-123"])

    assert result.exit_code != 0
    text = cli_text(result)
    assert "--snapshot=pr-123" in text, "the message suggests the equals form with the given name"
    assert "--snapshot-name" in text, "... and the dedicated flag"
    assert version.calls == [], "nothing runs after a rejected invocation"


# --------------------------------------------------------------------------------------
# `--pre` accepts only PEP 440 phases (research README section 4.2; pre.md:28-38)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("phase", ["a", "b", "rc", "dev"])
def test_pre_accepts_every_pep440_phase(monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    """pre.md:30-37 -- the four identifiers, each mapping to a fixed PEP 440 spelling.

    The exact-type check is load-bearing. Typer models a closed choice as an ``Enum``, and a
    ``str``-based ``Enum`` member compares equal to its value -- so an equality assertion alone
    cannot tell ``"rc"`` from ``Pre.RC``. The contract (test-contract section 5) is that ``pre``
    reaches the engine as a **plain** PEP 440 phase string; the shell unwraps the framework type.
    """
    version = record_run(monkeypatch, "version")

    result = invoke_cli(app, ["version", "--pre", phase])

    assert result.exit_code == 0, cli_text(result)
    assert observed(version, {"pre": phase}) == {"pre": phase}
    assert type(version.kwargs["pre"]) is str, "the framework's Enum must not leak to the command"


def test_pre_rejects_a_free_form_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--pre`` is a PEP 440 phase, never changesets' arbitrary tag (research README section 4.2).

    ``changeset pre enter next`` produced ``1.0.1-next.0``, which is not a legal PEP 440 version;
    accepting ``next`` here would resurrect exactly the shape molt exists to avoid.
    """
    version = record_run(monkeypatch, "version")

    result = invoke_cli(app, ["version", "--pre", "next"])

    assert result.exit_code != 0
    assert version.calls == []


# --------------------------------------------------------------------------------------
# Global `--non-interactive` / `--yes` (cli-shell spec; research section 11.7 item 7)
# --------------------------------------------------------------------------------------


def test_short_v_is_verbose_while_long_version_is_the_program_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 10.2 / cli.test.ts:221-227 -- the real trap, pinned so it cannot regress.

    ``-v`` on ``status`` shadows the global version flag upstream and molt keeps that (tasks.md
    3.4, overview.md:52). If ``-v`` were wired to an eager ``--version`` the command body would
    never run, which is what the call count below detects.
    """
    status = record_run(monkeypatch, "status")

    verbose = invoke_cli(app, ["status", "-v"])

    assert verbose.exit_code == 0, cli_text(verbose)
    assert len(status.calls) == 1, "-v ran the command; it is not an eager --version"
    assert observed(status, {"verbose": True}) == {"verbose": True}

    version = invoke_cli(app, ["--version"])

    assert version.exit_code == 0
    assert cli_text(version).strip() == __version__
    assert len(status.calls) == 1, "--version short-circuits before any command body runs"


def test_non_interactive_never_reaches_a_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli-shell spec, "Non-interactive run never blocks on a prompt"; section 9.3 lists the hazard.

    The empty :class:`~tests.cli.fake_cli.ScriptedPrompts` is the loud-failure harness: with no
    scripted answers, *any* prompt raises ``PromptExhausted`` instead of blocking on a TTY that
    does not exist -- which is what upstream does today, since the JS CLI has no ``--yes`` at all.
    """
    prompts = ScriptedPrompts()

    def run(*, non_interactive: bool = False, **kwargs: Any) -> None:
        del kwargs
        if not non_interactive:
            prompts.multiselect("Which packages were affected by the changes you made?", ())

    module = importlib.import_module("molt.commands.add")
    monkeypatch.setattr(module, "run", run)

    quiet = invoke_cli(app, ["add", "--non-interactive"], stdin="")

    assert quiet.exit_code == 0, cli_text(quiet)
    assert prompts.calls == [], "--non-interactive reached the choke point, so nothing prompted"

    blocking = invoke_cli(app, ["add"], stdin="")

    assert isinstance(blocking.exception, PromptExhausted), "an unscripted prompt fails loudly"
