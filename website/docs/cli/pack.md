---
title: molt build
---

# molt build

Build the distribution artifacts -- an sdist and a wheel per package -- for the packages a publish plan will ship.

> This is the **pack** stage of the publish pipeline. The command you type is `molt build`; the artifacts it produces are what [`molt publish --from-pack-dir`](/cli/publish) uploads.

## Synopsis

```bash
molt build [OPTIONS]
```

## Description

On PyPI, building is **mandatory and produces two artifacts** -- a source distribution (`.tar.gz`) and a wheel (`.whl`) -- for every package. There is no implicit build at upload time. `molt build` runs the ecosystem's build (for example `uv build` / `python -m build`) for each package a plan will publish, writes the artifacts to an output directory, and records an **enriched publish plan** that adds each artifact's path and a `sha256` integrity hash to the plan entries.

`molt build` can compute the plan itself, or take a precomputed one from [`molt publish-plan`](/cli/publish) via `--from-publish-plan`. Splitting build from upload lets CI build once and upload from a separate, credentialed job.

If a build fails, molt **surfaces the error and writes no plan file** -- there is no half-built output to clean up.

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--out-dir <dir>` | path | `dist` | Directory to write artifacts and the enriched `publish-plan.json` into. |
| `--from-publish-plan <file>` | string | -- | Build from a precomputed publish plan instead of computing one. |
| `--dry-run` | flag | off | Print what would be built; build nothing. |
| `--cwd <path>` | path | current directory | Directory to run in; root discovery starts here. |
| `-h`, `--help` | flag | -- | Show help and exit. |

The publish plan is a versioned envelope (`{ "version": 1, "plan": [...] }`); a plan file with an unrecognized version is rejected. The check is equality, not a floor -- an older envelope is a different document, not a compatible one -- and it also rejects a missing `version`, a `plan` that is not a list of chunks, and an envelope that is not an object at all. Nothing is built before the file is accepted.

Each publish entry gains an `artifacts` list of **exactly two** entries, sdist first and wheel second, each with a `path` relative to the output directory and an `integrity` of the form `sha256=<hex>`. Paths are written with forward slashes on every platform, so a plan built on Windows is readable by the Linux job that uploads it. **Tag-only entries pass through untouched** and never gain an `artifacts` key -- they are never built and never uploaded.

`molt build` has no `--repository` flag. When it computes the plan itself it therefore targets the default index, which means the [snapshot guardrail](/cli/publish) applies. To build a snapshot release, compute the plan against your own index first and hand the file over:

```bash
molt publish-plan --repository https://packages.internal.example/simple/ --output publish-plan.json
molt build --from-publish-plan publish-plan.json --out-dir dist
```

## Exit codes

| Situation | Exit code |
|---|---|
| Artifacts built and plan written (or `--dry-run` printed) | 0 |
| A build failed (no plan written) | 1 |
| Plan file has an unsupported version | 1 |

## Examples

Build everything in the current plan into `dist/`:

```bash
molt build
```

Build from a precomputed plan, into a chosen directory:

```bash
molt publish-plan --output publish-plan.json
molt build --from-publish-plan publish-plan.json --out-dir artifacts
```

Then upload the built artifacts from another job:

```bash
molt publish --from-pack-dir artifacts
```

## See also

- [molt publish](/cli/publish) -- uploads what `molt build` produces.
- [Publishing](/guide/publishing).
