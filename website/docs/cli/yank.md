---
title: molt yank
---

# molt yank

Mark a released version as yanked on PyPI (PEP 592) -- a recovery path for a bad release that changesets structurally cannot offer.

## Synopsis

```bash
molt yank <package> <version> [OPTIONS]
```

## Description

PyPI is immutable: a published version cannot be deleted or overwritten. But PEP 592 lets you **yank** it. A yanked version is still installable by an exact pin that already depends on it, so nothing that already resolved to it breaks -- but resolvers stop *selecting* it for new installs. It is the correct response to a release that is broken but must remain downloadable for reproducibility.

`molt yank` turns that into a first-class verb. It marks one `<package>` at one `<version>` as yanked (optionally with a reason that PyPI displays), and `--undo` reverses it. Because yanking changes what every downstream resolver picks, molt **asks for confirmation** before acting unless you pass `--yes`.

Yank is a plan-producing command like the rest: `--dry-run` prints exactly what would be yanked and contacts nothing.

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `<package>` | string (positional) | -- | Distribution name to yank. Required. |
| `<version>` | string (positional) | -- | Exact version to yank. Required. |
| `--reason <text>` | string | -- | Human-readable reason, shown by PyPI and resolvers. |
| `--undo` | flag | off | Un-yank instead of yank -- restore the version to normal selection. |
| `--repository <name>` | string | `pypi` | Named repository/index the version lives on. |
| `--yes`, `--non-interactive` | flag | off | Skip the confirmation prompt. Required in CI. |
| `--dry-run` | flag | off | Print what would be yanked; change nothing. |
| `--cwd <path>` | path | current directory | Directory to run in; root discovery starts here. |
| `-h`, `--help` | flag | -- | Show help and exit. |

## Exit codes

| Situation | Exit code |
|---|---|
| Version yanked or un-yanked (or `--dry-run` printed) | 0 |
| Confirmation declined at the prompt (nothing changed) | 1 |
| Package or version not found, or auth failure | 1 |
| Prompt cancelled with Ctrl-C | 0 |

## Examples

Yank a broken patch, with a reason:

```bash
molt yank acme-core 1.1.0 --reason "Corrupt wheel; use 1.1.1."
```

Preview first:

```bash
molt yank acme-core 1.1.0 --dry-run
```

Un-yank once the record is corrected:

```bash
molt yank acme-core 1.1.0 --undo
```

Non-interactive, in CI:

```bash
molt yank acme-core 1.1.0 --reason "Bad release" --yes
```

## See also

- [Yanking a release](/guides/yank) -- when and how to yank safely.
- [Why Molt?](/introduction/why-molt) -- why PyPI immutability makes yank a feature.
- [molt publish](/cli/publish).
