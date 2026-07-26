---
title: Adding a changeset
---

# Adding a changeset

`molt add` records the intent of a change -- which packages it affects, how much, and what the changelog should say -- either through an interactive prompt or fully non-interactively for scripts and bots.

A changeset is a small Markdown file in `.changeset/`. You commit it in the same pull request as your code, and [`molt version`](/guides/versioning) consumes it at release time. This guide covers both ways to write one. For the file format itself, see [Changeset format](/config/changeset-format).

## Interactive: `molt add`

Run it with no arguments:

```bash
molt add
```

What you are asked depends on the shape of your repository.

### Single-package repositories

molt skips straight to the bump and the summary:

```text
? What kind of change is this for acme-core? (current version is 1.2.0)
> patch  (X.X.Y)
  minor  (X.Y.X)
  major  (Y.X.X)
? Please enter a summary for this change (this will be in the changelogs).
> Fix a race condition in the export writer.
```

### Workspaces

In a workspace, molt first asks *which* packages changed, then *how much*. The package list puts the packages that actually changed since your base branch first, so the common case is a couple of keystrokes:

```text
? Which packages were affected by the changes you made?
  changed packages
> [x] acme-core
  unchanged packages
  [ ] acme-cli
  [ ] acme-utils
```

Then, for the packages you selected, molt asks for the major bumps, then the minor bumps; anything you do not mark as major or minor is patched. You end on the same summary prompt.

If a summary is a breaking change, molt reminds you to spell out **what** broke, **why**, and **how** to migrate -- that text is what your users read.

Submitting an empty summary opens your `$EDITOR` so you can write a longer entry, including Markdown headings (molt preserves them).

The result is a file like this, with a random, human-readable name:

```md
# .changeset/late-mangos-cheer.md
---
"acme-core": minor
"acme-cli": patch
---

Add streaming support to the export API. The CLI now shows a progress bar
for large exports.
```

Package names are always quoted; the front matter maps each affected package to a bump type; the body is the changelog entry. Commit it with your code.

## Non-interactive: flags

Interactive prompts are great for humans and useless for automation. Every changeset you can create by hand, you can create without a prompt -- this is a capability changesets does not have, and it is what unlocks Dependabot, Renovate, and code-generation workflows.

Pass the package, the bump, and the message directly:

```bash
molt add --package acme-core --bump minor --message "Add a --stream flag to the export API."
```

For several packages in one changeset, repeat the `--package` / `--bump` pair (they are matched in order):

```bash
molt add \
  --package acme-core --bump minor \
  --package acme-cli  --bump patch \
  --message "Add streaming support and wire it through the CLI."
```

`--bump` accepts `patch`, `minor`, or `major`. `--message` (short `-m`) supplies the summary; passing it is what makes the command fully non-interactive. Because you name the packages explicitly, this form does no "changed packages" detection and never blocks on a prompt -- safe to run in CI.

## Non-interactive: piped JSON

For tools that generate changesets programmatically, pipe a JSON object carrying the same information the file does:

```bash
echo '{"releases": {"pip-audit": "patch"}, "summary": "Bump pip-audit for GHSA-xxxx."}' \
  | molt add --stdin
```

This is the form a dependency bot uses: it opens a PR that bumps a dependency and, in the same PR, pipes a changeset so the change is never released without a changelog entry. The exact schema is documented under [`molt add`](/cli/add).

## No release needed

Some changes -- a README fix, a test-only tweak -- should ship no version bump at all. Record that intent explicitly with an empty changeset:

```bash
molt add --empty
```

This writes a changeset with no releases and an optional message. It satisfies the [`molt status`](/guides/status) CI gate ("a package changed but no changeset was found") without forcing a release. Use it instead of skipping `molt add`, so the decision is visible in the pull request.

## Where to go next

- [Changeset format](/config/changeset-format) -- the front matter and body in detail.
- [Versioning](/guides/versioning) -- how `molt version` consumes what you added.
- [The release workflow](/getting-started/the-release-workflow) -- where `add` sits in the bigger loop.
- [`molt add`](/cli/add) -- every flag and the JSON schema.
