---
title: Versioning & PEP 440
---

# Versioning and PEP 440

molt's version arithmetic is **PEP 440**, the Python standard. The bump *names* are the familiar `patch` / `minor` / `major`, but the numbers they produce, and the ranges they are checked against, follow Python's rules.

Molt is built on `packaging`, the PyPA reference implementation of PEP 440 (`Version`) and PEP 508 (`SpecifierSet`). It hand-rolls no version parsing and uses no SemVer library, so its answer to "does this version satisfy this constraint?" is the answer `pip` and `uv` give at install time.

## Bump arithmetic

A [bump type](/concepts/glossary#bump-type) moves one component of the release number and clears everything to its right:

| Old version | `patch` | `minor` | `major` |
|---|---|---|---|
| `1.4.2` | `1.4.3` | `1.5.0` | `2.0.0` |

The arithmetic is monotone: a bigger bump always yields a bigger version, and no bump ever produces a prerelease. That strict-increase property is what the [release plan](/concepts/release-plan)'s fixpoint loop depends on to terminate.

### No `0.x` special-casing in the bump

SemVer tooling often treats pre-1.0 versions specially. molt's bumping does **not**. A `major` on a `0.x` version goes straight to `1.0.0`:

| Old version | Bump | New version |
|---|---|---|
| `0.1.0` | `major` | `1.0.0` |
| `0.1.0` | `minor` | `0.2.0` |
| `0.1.0` | `patch` | `0.1.1` |
| `0.0.1` | `major` | `1.0.0` |
| `0.0.0` | `minor` | `0.1.0` |

If you want a `0.x` package to stay below `1.0.0`, choose `minor` and `patch` -- do not expect `major` to be reinterpreted as a minor. The `0.x` asymmetry is real, but it lives on the *range* side, not the bump side (below).

### Epochs, post-releases, and local versions

Python versions carry components SemVer has no concept of, and molt handles each of them:

| Old version | `patch` | Why |
|---|---|---|
| `1!2.0.0` | `1!2.0.1` | The epoch (`1!`) dominates ordering and is preserved -- dropping it would silently reorder every release. |
| `1.0.0.post1` | `1.0.1` | A post-release is not a prerelease, so `patch` increments and `.postN` is cleared. |
| `1.0.0+local.1` | `1.0.1` | The local segment (`+local.1`) is dropped on a bump. |
| `1.0.1.dev3` | `1.0.1` | `.devN` counts as a prerelease, so `patch` strips it rather than incrementing (see below). |

Release numbers with four or more components (`1.2.3.4`) have undefined bump semantics, so molt rejects them loudly rather than guessing.

### Prerelease-aware increments

When the current version is already a prerelease, a bump often just **strips the prerelease** instead of moving a digit -- because the stable release the prerelease was heading toward has already "used up" the increment:

| Old version | Bump | New version | Why |
|---|---|---|---|
| `1.0.1rc2` | `patch` | `1.0.1` | Already a pre-patch, so drop the prerelease only. |
| `1.1.0rc3` | `minor` | `1.1.0` | `patch == 0` and there is a prerelease, so `minor` stays put. |
| `2.0.0rc0` | `major` | `2.0.0` | `minor == 0`, `patch == 0`, prerelease present -> `major` stays put. |
| `1.0.1rc0` | `minor` | `1.1.0` | `patch != 0`, so `minor` actually increments. |

This is precisely the property that makes exiting [prerelease mode](/concepts/prerelease) "just work": running a plain `molt version` on `1.1.0rc3` with a pending `minor` lands you on the stable `1.1.0`.

## Ranges are PEP 508 specifier sets

A dependency constraint in molt is a **PEP 508 requirement** with a PEP 440 `SpecifierSet` -- `acme-core>=1.4.0,<2.0.0`, not `^1.4.0`. This is what the [release plan](/concepts/release-plan) checks when it decides whether a dependent has been left out of range by a moving dependency.

molt exposes a single predicate for that question, `satisfies(version, specifier_set)`, and it is where the `0.x` asymmetry actually lives.

### The `0.x` asymmetry is a range concern

SemVer's caret (`^`) and tilde (`~`) expand into PEP 440 ranges like this:

| SemVer shape | PEP 440 range | Note |
|---|---|---|
| `^1.2.3` | `>=1.2.3,<2.0.0` | caret allows up to the next major |
| `^0.2.3` | `>=0.2.3,<0.3.0` | at `0.x`, the **minor** is the breaking component |
| `^0.0.3` | `>=0.0.3,<0.0.4` | at `0.0.x`, the **patch** is the breaking component |
| `~1.2.3` | `>=1.2.3,<1.3.0` | tilde allows up to the next minor |
| `~0.2.3` | `>=0.2.3,<0.3.0` | |

So `^0.2.3` accepts `0.2.9` but **not** `0.3.0` -- the classic `0.x` trap, and molt reproduces it faithfully. This asymmetry is entirely on the range side; it never changes how a version is *bumped*.

### Prerelease opt-in

Both Python and JavaScript agree that a plain constraint like `>=1.0.0,<2.0.0` should not silently hand you a release candidate. They scope prerelease **opt-in** differently.

| Constraint | Version | node-semver | PEP 440 / molt |
|---|---|---|---|
| `>=1.0.0,<2.0.0` | `1.5.0rc1` | out of range | **out of range** |
| `>=1.0.0rc0,<2.0.0` | `1.0.1rc0` | out of range | **in range** |
| `>=1.0.1rc0,<2.0.0` | `1.0.1rc0` | in range | **in range** |

The rule molt follows: **prerelease opt-in is set-wide.** If any specifier in the set names a prerelease, the set accepts prereleases across its whole interval. node-semver requires the opt-in to be on the same release tuple, which is why the middle row differs.

Molt's question is always *"does this dependent's constraint still accept its dependency's new version?"*, and PEP 440 is what answers it, because that is the rule `pip` and `uv` resolve by. A constraint of `>=1.0.0rc0,<2.0.0` does accept `1.0.1rc0` and will install it, so the dependent is not broken and does not need a release. Applying the node-semver rule here would re-release dependents on every prerelease bump for no reason.

> **Note.** One consequence, by design: in prerelease mode a dependent that already opted into prereleases is **not** re-released as its dependency moves `rc0 -> rc1 -> rc2`. If you want that lockstep movement, it is an explicit config choice ([`update_internal_dependents = "always"`](/concepts/glossary#updateinternaldependents)), not something smuggled into the matching semantics.

There is a subtlety worth calling out. `packaging`'s own `SpecifierSet.contains` defaults to PEP 440's "match prereleases when there are no other versions" fallback -- which answers a *resolver* question ("what should I install when nothing else exists?"). molt's question is a *constraint-validity* question, so `satisfies` pins the prerelease flag to the constraint's own opt-in rather than inheriting that fallback. In practice that means `1.5.0rc1` is **not** in `>=1.0.0,<2.0.0` for molt, even though `packaging`'s bare default would say otherwise.

### Ranges molt does not treat as ranges

Not every dependency string is a version range. Direct references and workspace markers are **skipped, not rewritten** -- there is nothing to satisfy:

```text
file:../pkg        # a path/editable dependency
workspace:*        # a workspace marker
```

molt recognizes these and leaves them untouched, exactly as it should. See [Ecosystems](/ecosystems/overview) for how each backend expresses intra-workspace dependencies.

## Where to go next

- [Prerelease mode](/concepts/prerelease) -- how `a` / `b` / `rc` / `dev` prereleases are cut and exited.
- [The release plan](/concepts/release-plan) -- where range satisfaction drives cross-package propagation.
- [Linked vs fixed packages](/concepts/linked-vs-fixed) -- aligning versions across a group.
- [Options](/config/options) -- the config knobs, including `update_internal_dependents`.
- [Glossary](/concepts/glossary) -- bump type, prerelease, specifier, and the rest.
