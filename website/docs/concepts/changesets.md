---
title: Changesets
---

# Changesets

A changeset is a small, human-written file that records the **intent** of a change: which packages it affects, how much each one moves, and what the changelog should say.

You never edit a version number by hand in molt. Instead, as part of the work itself, you drop a changeset into the `.changeset/` directory. It sits there, committed alongside your code, until release time -- when [`molt version`](/cli/version) reads every accumulated changeset at once and turns that pile of intent into concrete version bumps and changelog entries. See [The changeset workflow](/guide/the-changeset-workflow) for why that two-step split is the whole point.

## What a changeset contains

A changeset answers three questions about a change:

- **Which packages does it affect?**
- **How significant is it for each one** -- `patch`, `minor`, or `major`?
- **What should the changelog say?**

That is the entire data model. A changeset is an *intent to change*, and the intent carries exactly two payloads: the per-package bump type (for versioning) and a prose summary (for the changelog).

## The file format at a glance

A changeset is a Markdown file with a YAML front matter block. The front matter maps each affected package to a [bump type](/concepts/glossary#bump-type); the body is the changelog entry.

```md
---
"acme-core": minor
"acme-cli": patch
---

Add streaming support to the export API. The CLI now shows a progress bar
for large exports.
```

You create one with [`molt add`](/cli/add), which writes it to `.changeset/` under a random, human-readable three-word name:

```bash
.changeset/
  slow-lions-cough.md
```

The file is boring and reviewable. A reviewer can see, in one glance, that this pull request intends a `minor` bump to `acme-core` and a `patch` to `acme-cli`, with a summary written by the person who actually made the change. The full grammar -- every field, how names are normalized, and the rules the parser enforces -- lives in [Changeset format](/config/changeset-format).

### Bump types, including `none`

molt uses PEP 440 arithmetic, but the bump *vocabulary* is the familiar `major` / `minor` / `patch` -- plus a fourth, first-class value: **`none`**.

| Bump | Effect |
|---|---|
| `major` | The most significant move (`1.4.2` -> `2.0.0`). |
| `minor` | A backward-compatible feature (`1.4.2` -> `1.5.0`). |
| `patch` | A backward-compatible fix (`1.4.2` -> `1.4.3`). |
| `none` | **A changelog entry with no version bump.** The package is mentioned in the changelog if it is released for some other reason, but this changeset does not move its version by itself. |

> **`none` in practice.** Use it when you want a note recorded against a package without forcing a release of that package on its own. See the [Glossary](/concepts/glossary#bump-type).

How bump levels map onto real version numbers -- and the ways PEP 440 differs from SemVer -- is covered in [Versioning and PEP 440](/concepts/versioning-pep440).

## Changesets stack

You can add as many changesets as you like, in as many pull requests as you like. They are designed to accumulate.

When `molt version` consumes them, everything mentioning the same package collapses into **one** release at the **highest** bump among them:

```bash
# Three weeks of development leave three changesets...
.changeset/
  slow-lions-cough.md      # minor: acme-core
  brave-mugs-sing.md       # patch: acme-cli
  tidy-eels-return.md      # patch: acme-core

# ...and acme-core is released once, as a minor (the highest of minor + patch),
# with both summaries in its changelog.
molt version
```

Three changesets do not mean three releases. They mean one release at the right version, with every summary preserved in the changelog. The bump is the *maximum*; the changelog is the *union*. That separation is the key invariant of the whole model.

## Changesets and git history

Because the intent lives in a committed file rather than in commit messages, squash-merging, amending, and rebasing a branch cannot change what a release does. The file survives every history rewrite, and it is reviewed like any other file in the pull request.

Molt reads commit history once, as a [migration aid](/guide/migrating-from-changesets), to propose a starting pile of changesets when you adopt it mid-project. It does not derive versions from commit messages on an ongoing basis. [The changeset workflow](/guide/the-changeset-workflow#changesets-and-commit-derived-versioning) sets the two models side by side.

## The lifecycle of a changeset

A changeset has a short, one-way life:

1. **Written** -- you run [`molt add`](/cli/add) (or write the file by hand, or generate it non-interactively in a bot) while the change is fresh.
2. **Committed** -- it lands in `.changeset/` in the same pull request as the code and is reviewed with it.
3. **Accumulated** -- it waits in `.changeset/` alongside any others, for however long until you decide to release.
4. **Consumed** -- [`molt version`](/cli/version) reads it (together with everything else pending), folds its bump into the [release plan](/concepts/release-plan), writes its summary into the changelog, and then **deletes the file**. Its intent is now baked into version numbers and changelogs, so the file has done its job.

The `.changeset/` directory is the buffer between the two clocks of the workflow: contributors keep dropping intent in, and the release manager drains it on their own schedule.

## Where to go next

- [Changeset format](/config/changeset-format) -- the full file grammar and parsing rules.
- [`molt add`](/cli/add) -- the interactive (and non-interactive) flow that writes changesets.
- [Adding a changeset](/guide/adding-a-changeset) -- a hands-on walkthrough.
- [The release plan](/concepts/release-plan) -- how accumulated changesets become version numbers, including cross-package propagation.
- [Glossary](/concepts/glossary) -- precise definitions for changeset, bump type, and the rest.
