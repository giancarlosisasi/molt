---
title: molt init
---

# molt init

Scaffold molt into a project: create the config and the `.changeset/` directory, and detect the ecosystem backend and workspace layout.

## Synopsis

```bash
molt init [OPTIONS]
```

## Description

`molt init` is the one-time setup command. It:

1. Walks up from the working directory to find the workspace root.
2. Detects the **ecosystem backend** -- uv, Poetry, Hatch, PDM, or setuptools -- and the workspace layout (single package, or a workspace with members). See [Ecosystems](/ecosystems/overview).
3. Writes a molt **config** as a `[tool.molt]` table in the root `pyproject.toml`, with **every** option present at its default. `.molt/config.json` is read if you have one -- it is the migration path from a changesets `config.json` -- but `molt init` never produces one. See [The config file](/config/config-file).
4. Creates the `.changeset/` directory with a short `README.md` explaining what the folder is for.

Run interactively, `molt init` asks a few questions (base branch, changelog integration, whether to auto-commit changesets) and fills in sensible defaults for everything else. Run with `--non-interactive`, it accepts every default without prompting.

The file it writes lists **every** option molt has, each set to its default, rather than the handful the prompts asked about. A TOML table carries no `$schema` line, so your editor cannot offer you the options that are missing from the file -- which makes the file itself the place you discover what molt can do. Delete the lines you do not care about; molt reads a missing key as its default either way.

`molt init` is **idempotent and safe to re-run**. If a config already exists, molt leaves it untouched and reports that the project is already initialized. It never clobbers an existing `README.md`, and it creates only the pieces that are missing.

Molt refuses to run when configuration is ambiguous: if **both** a `[tool.molt]` table and a `.molt/config.json` file exist, `molt init` reports the conflict and exits 1 rather than guessing which one wins.

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--non-interactive`, `--yes` | flag | off | Accept all defaults; ask no questions. Use in CI or scripted setup. |
| `--cwd <path>` | path | current directory | Directory to initialize; root discovery starts here. |
| `-h`, `--help` | flag | -- | Show help and exit. |

## Running without a terminal

`molt init` needs a terminal to ask its questions on. When there is none -- a container with no
tty, a CI step, a cron job -- it **stops immediately and names the question it could not ask**
rather than blocking:

```text
error molt cannot prompt: standard input is not a terminal, and this run needs an answer to:
Which branch should molt treat as the base branch?. Supply it as a command-line flag, or run molt
from a terminal.
```

Pass `--non-interactive` (or `--yes`) for a scripted setup: every question takes its default and no
prompt is built. The two situations report differently on purpose -- a run that passed the flag is
told about the flag, and a run that did not is told about the terminal.

## Exit codes

| Situation | Exit code |
|---|---|
| Initialized, or already initialized | 0 |
| Both a `[tool.molt]` table and `.molt/config.json` exist (ambiguous config) | 1 |
| Not a recognizable project / no writable workspace root | 1 |
| No terminal to ask a question on | 1 |

## Examples

Interactive setup in the current project:

```bash
molt init
```

Non-interactive setup for CI or a scripted bootstrap:

```bash
molt init --non-interactive
```

Initialize a project in another directory:

```bash
molt init --cwd ./packages/workspace-root
```

## See also

- [The config file](/config/config-file) -- what `init` writes and where.
- [Options reference](/config/options) -- every config key.
- [Ecosystems](/ecosystems/overview) -- how the backend is detected.
- [Quickstart: single package](/guide/quickstart-single-package) and [Quickstart: monorepo](/guide/quickstart-monorepo).
