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
3. Writes a molt **config**. By default molt writes a `[tool.molt]` table into the root `pyproject.toml`. If you prefer a standalone file, molt writes `.molt/config.json` instead. See [The config file](/config/config-file).
4. Creates the `.changeset/` directory with a short `README.md` explaining what the folder is for.

Run interactively, `molt init` asks a few questions (base branch, changelog integration, whether to auto-commit changesets) and fills in sensible defaults for everything else. Run with `--non-interactive`, it accepts every default without prompting.

`molt init` is **idempotent and safe to re-run**. If a config already exists, molt leaves it untouched and reports that the project is already initialized. It never clobbers an existing `README.md`, and it creates only the pieces that are missing.

Molt refuses to run when configuration is ambiguous: if **both** a `[tool.molt]` table and a `.molt/config.json` file exist, `molt init` reports the conflict and exits 1 rather than guessing which one wins.

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--non-interactive`, `--yes` | flag | off | Accept all defaults; ask no questions. Use in CI or scripted setup. |
| `--cwd <path>` | path | current directory | Directory to initialize; root discovery starts here. |
| `-h`, `--help` | flag | -- | Show help and exit. |

## Exit codes

| Situation | Exit code |
|---|---|
| Initialized, or already initialized | 0 |
| Both a `[tool.molt]` table and `.molt/config.json` exist (ambiguous config) | 1 |
| Not a recognizable project / no writable workspace root | 1 |

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
- [Quickstart: single package](/getting-started/quickstart-single-package) and [Quickstart: monorepo](/getting-started/quickstart-monorepo).
