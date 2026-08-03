---
title: CLI reference
---

# CLI reference

The complete `molt` command surface: every verb, its flags, its exit codes, and the cross-cutting rules that apply to all of them.

You install the distribution `molt-release` and type the command `molt`. See [Installation](/getting-started/installation) for why the two names differ.

## Command surface at a glance

| Command | What it does | Reference |
|---|---|---|
| `molt init` | Scaffold config and the `.changeset/` directory; detect the ecosystem backend and workspace layout. | [molt init](/cli/init) |
| `molt add` | Record a changeset: affected packages, bump types, and a summary. **The default command** -- bare `molt` runs this. | [molt add](/cli/add) |
| `molt version` | Consume pending changesets: bump versions, propagate to dependents, rewrite pins, write changelogs, update the lockfile. | [molt version](/cli/version) |
| `molt status` | Report pending changesets and the projected release, as text or JSON. Doubles as a CI gate. | [molt status](/cli/status) |
| `molt publish` | Build, then upload changed packages to PyPI via OIDC Trusted Publishing, in dependency order. | [molt publish](/cli/publish) |
| `molt publish-plan` | Print or emit the resolved publish plan without uploading anything. | [molt publish](/cli/publish) |
| `molt build` | Build sdist and wheel artifacts for the packages a plan will publish. (The "pack" stage.) | [molt build](/cli/pack) |
| `molt yank` | Check a released version and print the steps to yank it on PyPI (PEP 592). Read-only -- PyPI has no yank API, so you finish it in the browser. | [molt yank](/cli/yank) |
| `molt git-tag` | Create annotated git tags for released packages. | [molt git-tag](/cli/git-tag) |
| `molt pre` | Prerelease control. Molt has no persistent pre-mode: prereleases are the stateless `molt version --pre` flag. | [Prerelease control](/cli/pre) |

The distribution also installs a second command, `molt-action`, which runs the whole release loop for a CI workflow. It is **not** part of the `molt` command table and is not documented as a verb: molt's [composite GitHub Action](/guides/ci-github-action) is what invokes it, and everything it does is reachable from the commands above. You never need to type it.

## The default command

`molt` with no command word runs [`molt add`](/cli/add). An option that belongs to `add` also works with no command word:

```bash
molt              # same as: molt add
molt --open       # same as: molt add --open
```

An explicit command is never overridden -- `molt version` runs `version`, not `add`.

## Global options

These options are accepted by every command (subject to the command actually having prompts, mutations, or machine-readable output to control).

| Option | Type | Default | Description |
|---|---|---|---|
| `--non-interactive`, `--yes` | flag | off | Never block on a prompt. Each prompt resolves to its documented default, or the command exits non-zero naming the missing input. Required for CI, Dependabot, Renovate, and codegen. |
| `--dry-run` | flag | off | On any mutating command (`add`, `version`, `publish`, `git-tag`, `build`), print the [plan](/concepts/release-plan) the command would execute and write nothing. (`yank` has none: it never mutates, so every run is already a dry run.) See [Dry runs and plans](/guides/dry-run-and-plans). |
| `--output json` | string | human-readable | Where supported (`status`, `publish-plan`), emit the plan as a JSON document to stdout instead of the rendered view. See [Machine-readable output](#machine-readable-output). |
| `--cwd <path>` | path | current directory | Directory to run in. Root discovery walks up from here to the workspace root. |
| `--version` | flag | -- | Print the bare version string (for example `0.1.0`) and exit 0. No banner, no prefix. |
| `-h`, `--help` | flag | -- | Show help for the program or a command and exit 0. |

The startup banner (`molt v<version>`, prefixed with a snake glyph) prints once before a matched command's output. It is **not** printed for `--help` or `--version`. On a Windows console that cannot encode the glyph, the banner degrades to plain text rather than raising an error.

> `-v` is **not** a global alias for `--version`. On [`molt status`](/cli/status), `-v` means `--verbose`. Use the long `--version` form for the version string.

## The stream contract

**Every human-facing byte goes to stderr. stdout carries machine-readable payloads only.**

That covers the startup banner, every levelled message (`info`, `success`, `warn`, `error`), notes, spinners and progress -- all stderr. The only things molt writes to stdout are the `--output json` payload and the bare `molt --version` string.

The split is what makes the documented CI recipe work:

```bash
molt status --output json | jq '.releases[].name'
```

With the banner on stdout, `jq` (or `json.loads`) reads it first and the pipeline dies. Redirecting the payload to a file behaves the same way:

```bash
molt status --output json > plan.json   # plan.json is exactly one JSON document
```

So a script may treat stdout as parseable without filtering it, and may show stderr to a human without stripping data out of it.

## Machine-readable output

Molt produces a machine-readable [plan object](/concepts/release-plan) on every mutating command, and structured output on read commands:

- **`molt status` and `molt publish-plan`** accept `--output json` (short: `-o`) to print the plan as a JSON document to stdout. Plan keys are **snake_case** -- `old_version`, `new_version`, `package_name`.
- **`molt publish` and `molt git-tag`** accept `--output <file>` to write an NDJSON event stream -- one `{"type":"git-tag", ...}` object per line -- to a file.
- The **`MOLT_OUTPUT`** environment variable back-fills `--output` when the flag is not passed, so CI can set it once for the whole pipeline.

## Option-normalization rules

The same argument-handling rules apply across every command:

- **Repeated scalar options: last wins.** `--since main --since next` resolves to `next`, with no warning.
- **Repeatable list options always yield a list**, even for a single occurrence. This covers `--major`, `--minor`, `--patch`, `--package`, and `--ignore`.
- **Numeric-looking values stay strings.** `--snapshot-name 123` is the string `"123"`, so version and tag names that look numeric are never coerced to integers.
- **Arguments after a bare `--` are dropped.** There is no pass-through.
- **`--snapshot` takes an optional value** through a small, documented divergence from changesets. Because the parser cannot bind a space-separated optional value, use `--snapshot` (unnamed), `--snapshot=<name>`, or `--snapshot-name <name>`. The space form `--snapshot <name>` is rejected with guidance. See [molt version](/cli/version).

## Exit-code contract

| Situation | Exit code |
|---|---|
| Command succeeds | 0 |
| Prompt cancelled with Ctrl-C | 0 (a deliberate, documented divergence from POSIX 130) |
| Any validation failure or user-facing error | 1 |
| `molt status` finds changed packages but no changesets (CI gate) | 1 |
| `molt status` when nothing relevant changed (or only ignored, private, or unmatched files) | 0 |
| `molt version` when there are no unreleased changesets | 1 |
| `molt publish` when there is nothing to publish | 0 |
| Unexpected internal error | 1 |

On an unexpected internal error, molt prints a pre-filled issue-report URL that includes the CLI version and the Python version, with the working directory redacted to `<cwd>`, then exits 1. Any other uncaught exception prints a traceback and exits 1. `molt` never lets a bare traceback escape as the program's only output.

## See also

- [The changeset workflow](/introduction/the-changeset-workflow) -- the add / version / publish loop these commands implement.
- [The release plan](/concepts/release-plan) -- the plan object that `--dry-run` and `--output json` expose.
- [Dry runs and plans](/guides/dry-run-and-plans) -- previewing any mutating command.
- [Configuration](/config/config-file) -- the settings these commands read.
