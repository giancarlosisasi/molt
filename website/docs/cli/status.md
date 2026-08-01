---
title: molt status
---

# molt status

Show what the next release would contain -- the pending changesets and the versions they project -- without changing anything.

## Synopsis

```bash
molt status [OPTIONS]
```

## Description

`molt status` reads the pending changesets, assembles the [release plan](/concepts/release-plan), and reports it. It writes nothing, so it is safe to run any time. It has two jobs:

- **A preview** of the next `molt version` for a human, grouped by bump type. `--verbose` adds the projected new version and the source changeset for each package.
- **A CI gate.** When at least one versionable package has changed but **no changeset covers it**, `molt status` exits 1 with guidance to run `molt add` (or `molt add --empty` if the change needs no release). This is the check you run on every pull request.

`--output json` emits the plan as a machine-readable document to stdout, for tooling that decides what to do next. `json` is the only value it accepts: molt never writes the plan to a file, so redirect stdout when you want one. See [Status checks](/guides/status).

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--since <ref>` | string | base branch | Compare against `<ref>` to decide which packages and changesets are "new". |
| `-v`, `--verbose` | flag | off | Also print each projected new version and the changeset files behind it. |
| `-o`, `--output json` | string | human-readable | Emit the release plan as JSON on stdout instead of the rendered view. `json` is the only accepted value -- `status` writes no files. Back-filled by `MOLT_OUTPUT`. |
| `--cwd <path>` | path | current directory | Directory to run in; root discovery starts here. |
| `-h`, `--help` | flag | -- | Show help and exit. |

> On `molt status`, `-v` means `--verbose`, not `--version` -- a deliberate carry-over from changesets. Use the long `--version` on `molt` itself for the version string.

The rendered view and the CI gate's guidance go to **stderr**; only the `--output json` payload goes to stdout. That is what makes `molt status --output json | jq` work -- see [the stream contract](/cli/overview#the-stream-contract).

## Exit codes

| Situation | Exit code |
|---|---|
| Reported the status | 0 |
| Changed packages exist but no changesets cover them (CI gate) | 1 |
| Nothing relevant changed, or only ignored / private / unmatched files changed | 0 |

## Examples

Rendered preview:

```bash
molt status
```

```text
Packages to be bumped:
- minor
  - acme-core
- patch
  - acme-cli
```

Verbose, with projected versions and sources:

```bash
molt status --verbose
```

```text
Packages to be bumped:
- minor
  - acme-core -> 1.1.0
    - .changeset/tidy-eels-return.md
- patch
  - acme-cli -> 0.4.2
    - .changeset/brave-mugs-sing.md
```

Machine-readable, for CI:

```bash
molt status --output json
```

```json
{
  "changesets": [
    {
      "id": "tidy-eels-return",
      "summary": "Add streaming export.",
      "releases": [ { "name": "acme-core", "type": "minor" } ]
    }
  ],
  "releases": [
    {
      "name": "acme-core",
      "type": "minor",
      "old_version": "1.0.0",
      "new_version": "1.1.0",
      "changesets": [ "tidy-eels-return" ]
    }
  ]
}
```

As a pull-request gate:

```bash
molt status --since origin/main
```

## See also

- [Status checks](/guides/status) -- using `status` as a CI gate.
- [CI: GitHub Action](/guides/ci-github-action).
- [The release plan](/concepts/release-plan) -- the object `--output json` prints.
