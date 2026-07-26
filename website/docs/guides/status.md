---
title: Checking status
---

# Checking status

`molt status` shows what a release *would* contain -- the pending changesets and the exact set of version bumps they produce -- without changing anything.

It is the read-only view of the [release plan](/concepts/release-plan): same computation as [`molt version`](/guides/versioning), but it writes nothing. That makes it the natural CI gate and the natural "what am I about to release?" check.

```bash
molt status
```

## Reading the output

```text
Packages to be bumped:
- major
  - acme-core
- minor
  - acme-api
- patch
  - acme-cli
```

Packages are grouped by their final bump. A package can appear here without a changeset of its own -- if it depends on something being bumped and the new version falls outside its constraint, it shows up as a patch. That is [dependency propagation](/guides/dependency-propagation) at work.

For more detail -- the resulting version and which changesets drive each bump -- add `--verbose`:

```bash
molt status --verbose
```

```text
Packages to be bumped:
- major
  - acme-core -> 2.0.0
    - .changeset/proud-taxis-drum.md
- patch
  - acme-cli -> 0.5.1
    - (dependency bump)
```

When there is nothing pending, the header prints with an empty body:

```text
Packages to be bumped:
```

## Machine-readable output

Pass `--output json` to emit the plan as a structured object instead of prose. This is a molt differentiator -- a plan you can gate on, diff, or feed to another tool:

```bash
molt status --output json
```

```json
{
  "changesets": [
    {
      "id": "proud-taxis-drum",
      "summary": "Rename export() to export_all() and drop the legacy positional API.",
      "releases": [{ "name": "acme-core", "type": "major" }]
    }
  ],
  "releases": [
    {
      "name": "acme-core",
      "type": "major",
      "old_version": "1.2.0",
      "new_version": "2.0.0",
      "changesets": ["proud-taxis-drum"]
    },
    {
      "name": "acme-cli",
      "type": "patch",
      "old_version": "0.5.0",
      "new_version": "0.5.1",
      "changesets": []
    }
  ]
}
```

Two arrays, and the distinction between them is the whole point:

- **`changesets`** -- the intent *as authored*. Each entry is a changeset file: its id, summary, and the bumps its front matter declared.
- **`releases`** -- the plan *as computed*. Every package that will actually move, with its old and new version. A release driven only by propagation (like `acme-cli` above) carries an empty `changesets` array -- nobody wrote a changeset for it; the plan derived it.

Redirect it to a file with `> plan.json`, or read it directly in a script. The plan is the same shape molt uses everywhere; see [Dry runs and plans](/guides/dry-run-and-plans).

## The CI gate

`molt status` doubles as a pull-request check that no change slips out without a changelog entry. Its exit code encodes exactly one rule:

- Exit **1** when a versionable package has changed *and* no changeset covers it. The message tells the contributor to run [`molt add`](/guides/adding-a-changeset), or `molt add --empty` if the change needs no release.
- Exit **0** otherwise -- including when nothing changed, or when only ignored, private, or non-versionable packages changed.

```bash
# in CI, on a pull request
molt status --since origin/main
```

`--since <ref>` scopes "what changed" to the diff against a base branch, which is what you want on a PR. Wire this into the [GitHub Action](/guides/ci-github-action) and every PR is checked automatically.

## Where to go next

- [Dry runs and plans](/guides/dry-run-and-plans) -- the machine-readable plan across every command.
- [Versioning](/guides/versioning) -- apply the plan `status` previews.
- [The release plan](/concepts/release-plan) -- how the computed `releases` array is derived.
- [`molt status`](/cli/status) -- every flag.
