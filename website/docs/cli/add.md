---
title: molt add
---

# molt add

Record a changeset -- which packages a change affects, how much each should bump, and what the changelog should say.

`molt add` is also the **default command**: running `molt` with no command word runs `add` (see [CLI reference](/cli/overview#the-default-command)).

## Synopsis

```bash
molt add [OPTIONS]
molt                       # same as: molt add
```

## Description

`molt add` writes one changeset file into `.changeset/`, named with a random three-word slug (for example `.changeset/tidy-eels-return.md`). The file records a bump type per affected package plus a human-written summary. You commit it alongside your code. See [Changesets](/concepts/changesets) and [Adding a changeset](/guides/adding-a-changeset) for the full walkthrough, and [Changeset file format](/config/changeset-format) for the grammar.

There are three ways to drive it.

### Interactive

With no selection flags, `molt add` prompts:

1. **Which packages changed.** In a workspace, packages you have changed since the base branch are listed first. In a single-package project this step is skipped.
2. **How much each bumps** -- a `major`, `minor`, or `patch`. Anything not chosen as major or minor is patched.
3. **A summary** for the changelog. Submitting an empty summary opens `$EDITOR`. Markdown headings you type there are preserved.

Cancelling any prompt with Ctrl-C exits 0 and writes nothing.

### The first-major confirmation

Choosing a **major** bump for a package still **below `1.0.0`** asks one extra yes/no question before the changeset is written. Below 1.0.0 a major release is the package's *first* major: it takes the version to `1.0.0` and declares the API stable, which is not something to do by accident during a repo-wide sweep. At or above 1.0.0 the question is not asked.

**Declining does not abort -- in the interactive flow.** The package simply falls back into the next prompt, so you can pick `minor` instead and keep the changeset you were writing.

The confirmation is not limited to the interactive flow: **`--major` triggers it too.** `molt add --major pkg-a -m "..."` asks it, even though every other part of that run is non-interactive. But on the flag path there is no next prompt to fall back into, so **declining there aborts the whole run**: nothing is written, the command exits `0`, and one line reports that nothing was written for that package.

To take a first major with no question -- from CI, a bot, or a script -- add `--non-interactive`:

```bash
molt add --major pkg-a -m "Stable API." --non-interactive
```

### Non-interactive (flags)

Select packages and bumps entirely on the command line -- the path for Dependabot, Renovate, and code generation, none of which can answer prompts.

- `--package pkg-a --bump minor` selects packages and applies one bump type to all of them.
- `--major pkg-a --minor pkg-b --patch pkg-c` selects per bump type, changesets-style. Both `--package`/`--bump` and the per-type flags are repeatable.

Pair either with `--message` for a fully non-interactive run. Without `--message`, molt still prompts for the summary. If any selected package would take its **first major**, add `--non-interactive` as well -- see [the first-major confirmation](#the-first-major-confirmation).

This form does no "changed packages" detection: you named the packages, so molt does not run git to guess at them.

### Non-interactive (stdin)

`molt add --stdin` reads a JSON payload from standard input and writes the changeset with no TTY:

```json
{ "releases": [ { "name": "acme-core", "bump": "minor" } ], "summary": "Add streaming export." }
```

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `-p`, `--package <name>` | list | -- | Package to include. Repeatable. Combined with `--bump`. |
| `--bump {major,minor,patch,none}` | string | -- | Bump type applied to every `--package` selection. |
| `--major <name>` | list | -- | Package(s) to bump major. Repeatable. Asks the [first-major confirmation](#the-first-major-confirmation) for any package below `1.0.0`, unless `--non-interactive` is also given. |
| `--minor <name>` | list | -- | Package(s) to bump minor. Repeatable. |
| `--patch <name>` | list | -- | Package(s) to bump patch. Repeatable. |
| `-m`, `--message <text>` | string | -- | Changelog summary. Skips the summary prompt. `-m ""` writes an empty summary and still skips the prompt. |
| `--empty` | flag | off | Write a changeset with no releases (records "no release needed"). Skips every prompt. Combine with `-m` for a note. |
| `--stdin` | flag | off | Read a JSON changeset payload from standard input. |
| `--open` | flag | off | Open the written changeset in `$EDITOR` afterward. |
| `--since <ref>` | string | base branch | Which packages are listed first in the interactive prompt (those changed since `<ref>`). Does not change what a changeset can contain. |
| `--non-interactive`, `--yes` | flag | off | Require a complete selection via flags or `--stdin`; never prompt -- including the first-major confirmation. Exits non-zero if input is missing. |
| `--dry-run` | flag | off | Print the changeset that would be written; write nothing. |
| `--cwd <path>` | path | current directory | Directory to run in; root discovery starts here. |
| `-h`, `--help` | flag | -- | Show help and exit. |

Repeatable flags always yield a list, and repeated scalars keep the last value (see [Option-normalization rules](/cli/overview#option-normalization-rules)).

## Validation

`molt add` rejects an invalid selection before writing anything:

- A package named in `--major`/`--minor`/`--patch`/`--package` that is **not in the project** produces `The package <name> is passed to the --<type> option but it is not found in the project.`
- The **same package under two bump types** produces `The package <name> is passed to multiple release type options ... Please select only one release type for this package.`
- A project with **no versionable packages** exits 1 with guidance to check `ignore` and that manifests carry a version.

## Exit codes

| Situation | Exit code |
|---|---|
| Changeset written (or `--dry-run` printed) | 0 |
| Prompt cancelled with Ctrl-C | 0 |
| Unknown or duplicated package, or no versionable packages | 1 |
| `.changeset/` missing (run `molt init`) | 1 |
| `--non-interactive` with an incomplete selection | 1 |

## Examples

Interactive (or just `molt`):

```bash
molt add
```

Fully non-interactive, one package:

```bash
molt add --package acme-core --bump minor -m "Add streaming export."
```

changesets-style per-type selection, several packages:

```bash
molt add --minor acme-core --patch acme-cli -m "Streaming export plus CLI progress bar."
```

An empty changeset for a change that needs no release:

```bash
molt add --empty -m "Docs-only change."
```

From a machine, over stdin:

```bash
echo '{"releases":[{"name":"acme-core","bump":"patch"}],"summary":"Fix off-by-one."}' | molt add --stdin
```

## See also

- [Adding a changeset](/guides/adding-a-changeset) -- the hands-on guide.
- [Changesets](/concepts/changesets) and [Changeset file format](/config/changeset-format).
- [molt version](/cli/version) -- consume the changesets `add` writes.
