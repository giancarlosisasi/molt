---
title: molt doctor
---

# molt doctor

Check whether this workspace is set up correctly for molt -- and which of your packages a release will actually version.

## Synopsis

```bash
molt doctor [OPTIONS]
```

## Description

`molt doctor` runs a fixed set of checks over your project and reports each one as **ok**, **warn** or **fail**, with a one-line remedy for anything that is not ok. It writes nothing, contacts nothing by default, and finishes in well under a second.

Run it when you have just set molt up, when you have just migrated a `config.json` from changesets, when a release did something you did not expect, or before you open an issue -- the report is designed to be pasted into one.

Two things it answers that nothing else in molt does:

- **Every configuration problem at once.** Molt's configuration is strict: an unknown key at any depth is a hard error. Other commands stop at the first thing that blocks them, so a migration can take several runs to clear. `doctor` reports every error and every warning in a single pass, each naming the key and, where molt knows it, the key to use instead.
- **Which packages a release would skip.** A package with no version is skipped silently, and a package with a dynamic version molt cannot resolve is skipped with one line mid-run. Both are invisible until a release is already going. `doctor` names every package and what will happen to it *before* you start.

### What it checks

| Group | Reports |
|---|---|
| **Environment** | The molt version, the Python version, the platform, and whether this console can render molt's output. |
| **Workspace** | Where the workspace root is, which backend discovered it, how many packages were found, and any manifest that failed to parse -- named with its path. |
| **Configuration** | Whether a configuration exists and where it lives, plus every error and every warning it produces. |
| **Versions** | One row per package: the version and where it was read from, or why the package will be skipped. |
| **Changesets** | Whether `.changeset/` exists, how many changesets are pending, any file that cannot be parsed, and any changeset naming a package that is not in the workspace. |
| **Groups and filters** | What each `ignore`, `fixed` and `linked` entry resolves to in *this* workspace. |
| **Publishing** | Whether `uv` and `git` are on `PATH` and their versions, and whether an upload credential is available. |

### The four version outcomes

This is the check worth reading the output of. Each package lands in exactly one of these:

| Outcome | Meaning | Status |
|---|---|---|
| **resolved** | Molt found a version, and the row says where it was read from. | ok |
| **versionless** | The package declares no version and no dynamic version, so it is not a distribution and a release skips it. Usually correct -- an application, a docs site, a workspace root. | warn |
| **unresolvable dynamic** | The package declares `dynamic = ["version"]` but molt could not work out where that version comes from. It will be skipped, and that is a mistake rather than a choice. | fail |
| **refused source** | The version derives from a git tag (setuptools-scm, hatch-vcs, versioningit). Molt recognises the source and declines to release it -- a known limit, reported deliberately. | fail |

The middle two look identical in a manifest: both have no `[project].version`. Only `dynamic = ["version"]` tells them apart, and only one of them is a problem.

### Credentials are never disclosed

The publishing check reports **whether** a credential is present and **which** source it came from -- an environment variable name, or a Trusted Publishing identity. It never emits the value, any part of it, its length, or a hash of it, in either the human report or the JSON document. It does not read the value at all.

That holds because it cannot do otherwise: the check asks whether a name is set and reports a boolean, and a report row has no field a value could travel in. `molt doctor` output is the artifact people paste into public issue trackers, so this is a property of the code rather than a convention.

### No network unless you ask

By default `molt doctor` makes **no** network request and works fully offline. `--online` adds one check: that the package index answers. Nothing else contacts the network, and an unreachable index is a **warning** -- a proxy, an outage or an air-gapped machine is not a defect in your setup. The probe has a short timeout, so it cannot hang.

`molt doctor` never checks whether a version is already published. That is [`molt publish-plan`](/cli/publish).

### It runs on a project that is not set up yet

Every other molt command refuses a missing `.changeset/` and tells you to run `molt init`. `doctor` reports it as a row and keeps going, because "I am not sure this project is set up" is the exact reason to run it.

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--online` | flag | off | Also check that the configured package index answers. Without it, no network request is made at all. |
| `--output json` | string | human-readable | Print the report as a JSON document on stdout instead of the rendered view. |
| `--cwd <path>` | path | current directory | Directory to run in; root discovery starts here. |
| `--non-interactive`, `--yes` | flag | off | Accepted as a global. `doctor` never prompts. |
| `-h`, `--help` | flag | -- | Show help and exit. |

`molt doctor` takes no `--dry-run`: it never mutates, so every run is already a dry run.

## Exit codes

| Situation | Exit code |
|---|---|
| No check reported `fail` | 0 |
| At least one check reported `fail` | 1 |

**Warnings never affect the exit code**, and there is no mode that promotes them to failures. A glob that legitimately matches no package today is a fact about your workspace, not a mistake -- and a build that fails when somebody adds a package tomorrow is what such a mode would produce.

## Machine-readable output

```bash
molt doctor --output json
```

The document goes to **stdout** and everything human goes to **stderr**, so `molt doctor --output json | jq` reads exactly one document. Keys are snake_case, matching molt's plan documents.

```json
{
  "status": "fail",
  "exit_code": 1,
  "summary": { "ok": 12, "warn": 2, "fail": 1 },
  "rows": [
    {
      "group": "versions",
      "check": "versions.resolvable",
      "status": "fail",
      "subject": "acme-plugin",
      "message": "\"acme-plugin\" declares dynamic = [\"version\"] but molt could not work out where its version comes from: ...",
      "remedy": "Declare where acme-plugin's version lives, then run `molt doctor` again."
    }
  ]
}
```

`status` is `ok` or `fail` and matches `exit_code`; both come from the same rows, so the document and the process status cannot disagree.

## Examples

```bash
molt doctor
```

```text
Environment
  ok   molt: 0.1.4
  ok   python: 3.12.13 (CPython)
  ok   platform: Linux-6.8.0-x86_64
  ok   console encoding: utf-8 renders molt's output in full

Workspace
  ok   root: /repos/acme
  ok   packages: 4 found from a uv workspace declaration

Configuration
  ok   source: /repos/acme/pyproject.toml
  ok   options: the configuration is valid

Versions
  ok   acme-core: 1.4.0, from [project].version
  ok   acme-cli: 1.4.0, from src/acme_cli/__about__.py
  warn acme-docs: declares no version and no dynamic version, so a release skips it.
       -> Leave it as is if acme-docs is not a distribution, or give it a [project].version.

Changesets
  ok   directory: /repos/acme/.changeset
  ok   pending: 2 changesets waiting

Groups and filters
  ok   fixed group 1: acme-core, acme-cli release at one shared version

Publishing
  ok   uv: uv 0.11.25
  ok   git: git version 2.47.0
  ok   token: set in UV_PUBLISH_TOKEN
  ok   index: https://pypi.org was not contacted; `molt doctor --online` checks that it answers
```

Check the index too:

```bash
molt doctor --online
```

Use it as a setup gate in CI:

```bash
molt doctor || exit 1
```

## `doctor` or `status`?

They answer different questions, and a workflow that wires up the wrong one gets no gate at all:

| Command | Question | Fails when |
|---|---|---|
| [`molt status`](/cli/status) | *Is this change releasable?* | A package changed and no changeset covers it. |
| `molt doctor` | *Is my setup sane?* | A configuration, a manifest, a version source or a changeset file is broken. |

`molt doctor` is not a substitute for the changeset gate. Run `molt status` in pull-request CI; run `molt doctor` when something is wrong, or on a schedule.

## See also

- [Migrating from changesets](/guide/migrating-from-changesets) -- run `molt doctor` first if your `config.json` is rejected.
- [molt status](/cli/status) -- the changeset CI gate.
- [Configuration](/config/config-file) -- every option `doctor` validates.
