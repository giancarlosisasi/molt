---
title: The changesets model
---

# The changesets model

Molt is built on one core idea: **you declare the intent of a change when you make it, and a separate step turns accumulated intent into concrete releases.** That single decision -- separating *what changed* from *how the version should move* -- is what makes the whole workflow predictable.

## A changeset is a record of intent

A changeset is a small Markdown file that answers three questions about a change:

- **Which packages does it affect?**
- **How significant is it for each one** -- a `patch`, a `minor`, or a `major` change?
- **What should the changelog say?**

You create one with `molt add`, and it lands in the `.changeset/` directory next to your code:

```md
---
"acme-core": minor
"acme-cli": patch
---

Add streaming support to the export API. The CLI now shows a progress bar
for large exports.
```

The front matter maps each affected package to a bump type; the body is the changelog entry, written by a human who understands the change. You commit this file in the same pull request as the code. It is intentionally boring and reviewable -- a reviewer can see, in one glance, that this PR intends a minor bump to `acme-core` and a patch to `acme-cli`.

See [Changesets](/concepts/changesets) for the full file format and [`molt add`](/cli/add) for the interactive flow that writes them.

## Two clocks: adding intent vs consuming it

The mental model that makes everything click is that there are **two independent clocks**:

- **Adding intent** happens continuously, once per meaningful change, by whoever wrote it. Ten pull requests over two weeks produce ten changesets sitting in `.changeset/`.
- **Consuming intent** happens in a batch, whenever you decide to release. `molt version` reads all pending changesets at once, computes the correct version for every affected package, applies the bumps, folds the summaries into each changelog, and deletes the consumed changesets.

The `.changeset/` directory is the buffer between the two clocks. Contributors keep dropping intent into it; the release manager drains it on their own schedule. Nobody has to decide the final version number at the moment they open a pull request, because the number depends on *everything* that ships in the release, which is not known until release time.

```bash
# Two weeks of development produce a pile of intent...
.changeset/
  slow-lions-cough.md      # minor: acme-core
  brave-mugs-sing.md       # patch: acme-cli
  tidy-eels-return.md      # patch: acme-core

# ...consumed in one deliberate step
molt version
```

If `acme-core` has a pending `minor` and a pending `patch`, molt takes the **highest** bump and moves it once. Three changesets do not mean three releases; they mean one release that reflects the most significant change among them, with all three summaries in the changelog.

## Why not derive versions from commits?

The popular alternative is to infer versions from commit history -- parse `feat:` / `fix:` / `BREAKING CHANGE:` prefixes and let a tool decide the bump. Molt deliberately does **not** do this, and treats full commit-derived versioning as a non-goal. The intent-based model wins on the things that actually cause release bugs:

- **Context is captured when it exists.** The person writing the change knows whether it is breaking; a regex reading `git log` three weeks later does not. You record the decision at the moment you have the most information.
- **"A commit" is the wrong unit.** A one-character typo fix and a signature-breaking API change are both commits. Only a human knows which is which, and changesets let them say so explicitly instead of encoding it in a message prefix.
- **Changelogs are written, not scraped.** The changelog entry is prose a human wrote for other humans, not a dump of commit subjects.
- **No convention to police.** Contributors do not have to memorize and correctly apply a commit-message grammar, and you do not need CI to reject PRs whose subject lines are malformed. The changeset is a reviewable file, checked like any other code.
- **Intent accumulates cleanly.** Many changes pile up and collapse into exactly the right per-package bump, including for packages a change touched only indirectly through the [dependency graph](/concepts/release-plan).

Molt does offer a one-time *seeding* path -- generating changesets from existing commit history -- purely as a [migration aid](/guides/migrating-from-changesets) when you adopt the tool. That is a bootstrap convenience, not the ongoing model.

## The lifecycle: add, version, publish

Three verbs cover the entire loop:

| Step | Command | What it does |
|---|---|---|
| **Add** | [`molt add`](/cli/add) | Record a changeset: affected packages, bump types, and a summary. |
| **Version** | [`molt version`](/cli/version) | Consume all pending changesets: bump versions, propagate to dependents, write changelogs, update the lockfile. |
| **Publish** | [`molt publish`](/cli/publish) | Build and upload exactly the packages whose versions changed. |

Two supporting verbs round it out: [`molt status`](/cli/status) shows what a release *would* contain before you run it, and every mutating command accepts `--dry-run` to print its [plan](/concepts/release-plan) without touching disk.

## Where to go next

- [Changesets](/concepts/changesets) -- the file format and bump types in detail.
- [The release plan](/concepts/release-plan) -- how `molt version` turns pending changesets into version numbers, including cross-package propagation.
- [Adding a changeset](/guides/adding-a-changeset) -- a hands-on walkthrough of `molt add`.
- [Why Molt?](/introduction/why-molt) -- how molt's take on this model differs from changesets and why.
