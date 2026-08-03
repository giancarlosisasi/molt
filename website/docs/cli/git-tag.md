---
title: molt git-tag
---

# molt git-tag

Create annotated git tags for released packages, without publishing.

## Synopsis

```bash
molt git-tag [OPTIONS]
```

## Description

`molt git-tag` creates one git tag per released package at its current version. [`molt publish`](/cli/publish) tags automatically after a successful upload; `molt git-tag` is the standalone verb for when you tag separately -- for example a release job that publishes and a later job that tags, or a repo that publishes elsewhere.

Tag shape depends on the project:

- **Workspace / monorepo:** `<name>@<version>`, with the name normalized per PEP 503 (`Foo_Bar` and `foo-bar` produce the same tag).
- **Single-package project:** `v<version>`.

Tags are **annotated** (`git tag <t> -m <t>`) so that `git push --follow-tags` picks them up; lightweight tags would be skipped. Tagging is **idempotent** -- a tag that already exists is skipped, so re-running is safe.

`--output` (or `MOLT_OUTPUT`) writes an NDJSON stream of `{"type":"git-tag", "tag": ..., "package_name": ...}` events, one per line. The output file is always created, even when there is nothing to tag.

## The deprecated `tag` alias

`molt tag` is accepted as a **deprecated alias** of `molt git-tag`. It prints a warning advising you to use `git-tag`, then runs the same behavior. The canonical `molt git-tag` prints no warning.

```bash
molt tag        # warns: use 'git-tag' instead; then tags
molt git-tag    # no warning
```

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--output <file>` | string | -- | Write an NDJSON `git-tag` event stream to a file. Back-filled by `MOLT_OUTPUT`. |
| `--dry-run` | flag | off | Print the tags that would be created; create nothing. |
| `--cwd <path>` | path | current directory | Directory to run in; root discovery starts here. |
| `-h`, `--help` | flag | -- | Show help and exit. |

## Exit codes

| Situation | Exit code |
|---|---|
| Tags created, or nothing to tag (all already exist) | 0 |
| Not a git repository / tag creation failed | 1 |

## Examples

Tag every released package:

```bash
molt git-tag
```

Preview the tags:

```bash
molt git-tag --dry-run
```

Record tag events for CI:

```bash
molt git-tag --output tags.ndjson
```

Push the annotated tags:

```bash
molt git-tag
git push --follow-tags
```

## See also

- [molt publish](/cli/publish) -- tags automatically after upload.
- [The release workflow](/guide/the-release-workflow).
