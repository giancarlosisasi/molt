---
title: "Quickstart: single package"
---

# Quickstart: single package

Take one package from a code change to a published release with `molt init`, `molt add`, `molt version`, and `molt publish`.

Most Python projects ship a single package, and molt treats that as the default path -- not a stripped-down special case. This walkthrough is copy-pasteable end to end. If you manage a workspace of several packages, read the [monorepo quickstart](/guide/quickstart-monorepo) instead.

We will use a package called `acme-core` that starts at version `1.2.0`.

## 0. Set up

Install molt (see [Installation](/guide/installation)) and scaffold the project from its root:

```bash
molt init
```

That creates `.changeset/` and a `[tool.molt]` block in your `pyproject.toml`. Your starting manifest looks like this:

```toml
[project]
name = "acme-core"
version = "1.2.0"
```

## 1. Make a code change

Edit whatever you are editing. Say you add a `--stream` flag to the export API -- a backward-compatible feature, so a **minor** change.

## 2. Record a changeset

Run `molt add` and describe the change:

```bash
molt add
```

In a single-package repo, molt skips straight to two questions -- what kind of change is this, and what should the changelog say:

```text
? What kind of change is this for acme-core? (current version is 1.2.0)
> minor  (X.Y.X)
? Please enter a summary for this change (this will be in the changelogs).
> Add a --stream flag to the export API for large datasets.
```

That writes a small Markdown file into `.changeset/` with a random, human-readable name:

```md
# .changeset/late-mangos-cheer.md
---
"acme-core": minor
---

Add a --stream flag to the export API for large datasets.
```

Commit this file in the same pull request as your code. It is the *intent* of the change, captured while you still have the context. Prefer to skip the prompts entirely (in CI, a script, or a bot)? Use the non-interactive form:

```bash
molt add --package acme-core --bump minor --message "Add a --stream flag to the export API for large datasets."
```

See [Adding a changeset](/guide/adding-a-changeset) for the full flow, interactive and non-interactive.

## 3. Version the package

When you are ready to cut a release, consume every pending changeset in one step:

```bash
molt version
```

`molt version` reads the `.changeset/` directory, works out the correct next version, and applies it. Three files change and one is deleted:

**`pyproject.toml`** -- the version is bumped (a minor, `1.2.0 -> 1.3.0`):

```diff
 [project]
 name = "acme-core"
-version = "1.2.0"
+version = "1.3.0"
```

**`CHANGELOG.md`** -- created if absent, with your summary folded in under the new version:

```md
# acme-core

## 1.3.0

### Minor Changes

- Add a --stream flag to the export API for large datasets.
```

**`uv.lock`** -- refreshed so the lockfile matches the new version. A `--frozen` install against a stale lock fails, so the refresh happens in the same commit.

**`.changeset/late-mangos-cheer.md`** -- deleted. The intent has been consumed into a concrete release, so the changeset is gone. Re-running `molt version` now would exit with code 1 and "No pending changesets found." -- there is nothing left to do, and molt never double-bumps.

Preview all of this before it touches disk with `molt version --dry-run`; see [Dry runs and plans](/guide/dry-run-and-plans).

Now commit the release:

```bash
git commit -am "Release acme-core 1.3.0"
```

## 4. Publish

```bash
molt publish
```

`molt publish` builds the package (an sdist and a wheel), uploads exactly the versions that are not already on the index, and creates an annotated git tag. In a single-package repo the tag is `v1.3.0`.

In CI, molt publishes through PyPI [Trusted Publishing](/guide/publishing) (OIDC), so there is no long-lived token to manage. Locally you can use an API token. Either way, push your tag afterward:

```bash
git push --follow-tags
```

## The whole loop

```bash
molt init                 # one time
# ... make a change ...
molt add                  # record intent
molt version              # apply: bump, changelog, lockfile
git commit -am "Release"
molt publish              # build, upload, tag
```

## Where to go next

- [The release workflow](/guide/the-release-workflow) -- the mental model behind these four commands.
- [Adding a changeset](/guide/adding-a-changeset) and [Versioning](/guide/versioning) -- the two verbs you run most.
- [Quickstart: monorepo](/guide/quickstart-monorepo) -- when one package becomes several.
- [Publishing](/guide/publishing) -- tokens, Trusted Publishing, and what gets uploaded.
