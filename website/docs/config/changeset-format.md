---
title: Changeset file format
---

# Changeset file format

A changeset is a Markdown file in `.changeset/` whose YAML frontmatter maps package names to bump levels, followed by a Markdown summary that becomes the changelog entry.

You rarely write these by hand -- [`molt add`](/cli/add) generates them for you -- but the format is plain text, reviewable in a pull request, and fully specified here.

## A single-package changeset

```md
---
"acme-core": minor
---

Add streaming support to the export API.
```

The block between the two `---` fences is YAML frontmatter: one entry per affected package, mapping the package name to its bump level. Everything after the closing fence is the summary body -- free-form Markdown that lands in the changelog.

## A multi-package changeset

One changeset can bump several packages at once, each at its own level:

```md
---
"acme-core": minor
"acme-cli": patch
"acme-legacy": none
---

Add streaming support to the export API. The CLI now shows a progress bar
for large exports, and the legacy shim is documented as deprecated.
```

The order of entries in the frontmatter is preserved. A single pull request can also contain several changeset files; [`molt version`](/cli/version) consumes them all at once and takes the highest bump per package.

## The rules

### Bump levels

Each package maps to exactly one of four values:

| Level | Effect |
|---|---|
| `major` | Bump the major component (`1.4.2` -> `2.0.0`). |
| `minor` | Bump the minor component (`1.4.2` -> `1.5.0`). |
| `patch` | Bump the patch component (`1.4.2` -> `1.4.3`). |
| `none` | Record a changelog entry without changing the version. |

`none` is a real, first-class level, distinct from an empty changeset. The package keeps its current version, but the summary still appears in its changelog -- useful for noting a change that ships alongside a release without warranting a bump of its own. Molt applies PEP 440 bump arithmetic with no `0.x` special-casing: `major` on `0.1.0` is `1.0.0`. See [Versioning and PEP 440](/concepts/versioning-pep440).

### Package names are normalized (PEP 503)

Package names are matched using PEP 503 normalization, so casing and the choice of separator do not matter. These all refer to the same package:

```md
---
"Acme_Core": patch
"acme-core": patch
"acme.core": patch
---
```

Molt normalizes both the name in the frontmatter and your real package names before matching (`re.sub(r"[-_.]+", "-", name).lower()`), and preserves the original spelling for display. Python has no `@scope/name` packages, so there are no scope subtleties to worry about -- just a flat, normalized namespace.

Names are always double-quoted in the frontmatter. Molt writes them quoted, and you should too: quoting keeps names that could otherwise be misread as YAML safe and unambiguous.

### Markdown headings in summaries are preserved

The summary body is real Markdown, and molt keeps it intact -- including headings:

```md
---
"acme-core": minor
---

## What changed

Added a `--stream` flag.

### Migration

Callers passing `buffer=True` should switch to `stream=False`.
```

Lines beginning with `#` survive into the changelog unchanged. Molt treats a summary as literal prose and never post-processes it, so a heading in your description reaches the changelog as a heading. If you are migrating from changesets, note that it strips those lines.

### Empty changesets

An empty changeset has frontmatter with no entries and no summary:

```md
---
---
```

This is valid and records "no release intended here" -- for example, a placeholder so a pull request satisfies a CI check that requires a changeset. Generate one with `molt add --empty`.

## Where changesets live

Changeset files live in the `.changeset/` directory at your workspace root, one `.md` file per changeset. Molt reads every `.md` file there except:

- files whose name starts with `.` -- the documented way to *temporarily* disable a changeset is to prefix it with a dot;
- `README.md` and the agent instruction files `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, all matched case-insensitively.

The filename itself carries no meaning -- `orange-foxes-waggle.md` and `fix-export-bug.md` are read identically. Molt reads them in sorted order so changelog output is deterministic across machines.

## How they get created

You do not typically write changeset files by hand. [`molt add`](/cli/add) walks you through selecting the affected packages and their bump levels, then writes a correctly-formatted file with a generated name. For automation -- Dependabot, Renovate, codegen -- `molt add` also has a fully non-interactive mode that produces the same file without prompting.

## Where to go next

- [`molt add`](/cli/add) -- the command that writes these files.
- [Changesets](/concepts/changesets) -- the concept and the two-clock model behind them.
- [Versioning and PEP 440](/concepts/versioning-pep440) -- how bump levels turn into version numbers.
- [Options reference](/config/options) -- config that governs how changesets are consumed.
