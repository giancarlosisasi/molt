---
title: Dry runs and plans
---

# Dry runs and plans

Every command in molt that changes something first builds a **plan** -- a machine-readable description of what it will do -- and `--dry-run` prints that plan and executes nothing.

This is one of molt's defining ideas. `add`, `version`, `publish`, `build`, and `git-tag` all produce the same shape of object: a plan. Running the command executes the plan; `--dry-run` prints it and stops. Because it is the *same value* either way, a dry run is a faithful preview, never an approximation.

## `--dry-run` on any mutating command

```bash
molt version --dry-run
molt publish --dry-run
molt add --package acme-core --bump minor -m "..." --dry-run
molt build --dry-run
```

Each prints, in human-readable form, exactly what it would do -- and writes nothing, uploads nothing, tags nothing.

To get the plan as **structured data**, use the `--output json` flag of the matching read command; each command's option table says whether it has one of its own. For `version` that command is [`molt status --output json`](/guides/status), which computes and prints the identical release plan -- `molt version` deliberately has no `--output`, because a script should never have to invoke a mutating verb to find out what it would do.

## The plan object

A plan always carries which command produced it and whether it was executed, plus the command-specific detail.

### `version`

The `version` plan is the release plan: the changesets it would consume and the releases it would compute. (This is exactly what [`molt status`](/guides/status) prints, since `status` is a permanent dry run of `version`.)

```json
{
  "command": "version",
  "dry_run": true,
  "changesets": [
    {
      "id": "proud-taxis-drum",
      "summary": "Rename export() and drop the legacy positional API.",
      "releases": [{ "name": "acme-core", "type": "major" }]
    }
  ],
  "releases": [
    { "name": "acme-core", "type": "major", "old_version": "1.2.0", "new_version": "2.0.0", "changesets": ["proud-taxis-drum"] },
    { "name": "acme-cli",  "type": "patch", "old_version": "0.5.0", "new_version": "0.5.1", "changesets": [] }
  ]
}
```

### `publish`

The `publish` plan lists exactly which packages and versions would be uploaded, to which index, in dependency order. Packages in the same group can publish concurrently; groups run in sequence.

```json
{
  "command": "publish",
  "dry_run": true,
  "index": "https://upload.pypi.org/legacy/",
  "groups": [
    [ { "name": "acme-core", "version": "2.0.0" } ],
    [ { "name": "acme-cli",  "version": "0.5.1" } ]
  ]
}
```

### `add`

The `add` plan is the changeset file that would be written -- its generated name, path, and contents:

```json
{
  "command": "add",
  "dry_run": true,
  "changeset": {
    "id": "late-mangos-cheer",
    "path": ".changeset/late-mangos-cheer.md",
    "releases": [{ "name": "acme-core", "type": "minor" }],
    "summary": "Add a --stream flag to the export API."
  }
}
```

### `yank`

`molt yank` has no plan and no `--dry-run`, because it never mutates anything: PyPI exposes no API
for yanking, so the command verifies the version and prints the steps for you to complete in a
browser. Every run is already a dry run. See [`molt yank`](/cli/yank).

## One plan shape on every command

Every mutating verb uses the same envelope, the same `--dry-run` switch, and the same snake_case JSON document wherever a plan is emitted as data. That uniformity is what makes molt scriptable:

- **Preview safely.** See every version bump, upload, or tag before it happens.
- **Gate in CI.** Parse the JSON and decide whether to proceed. For example, refuse to publish when a plan contains a major bump without human sign-off.
- **Split build from publish.** Compute a plan in one job and hand it to another. This is how the [publish flow](/guides/publishing) separates building artifacts from uploading them.
- **Diff releases.** Two plans are two JSON documents, so diffing them shows exactly what changed.

The plan is a value molt computes internally on every run. The only difference between a dry run and a real run is whether molt executes the plan after printing it.

## Where to go next

- [Checking status](/guides/status) -- the read-only plan for the next `version`.
- [Versioning](/guides/versioning) and [Publishing](/guides/publishing) -- the commands whose plans you preview.
- [The release plan](/concepts/release-plan) -- how the `version` plan is computed.
