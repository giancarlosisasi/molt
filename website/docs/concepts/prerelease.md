---
title: Prerelease mode
---

# Prerelease mode

In molt, a prerelease is cut with a **stateless invocation flag** -- `molt version --pre rc` -- not a persistent mode you enter and later have to remember to exit. There is no `pre.json`, no branch state, and nothing to clean up.

PEP 440 defines a closed set of prerelease spellings, so the arbitrary tag names a persistent mode exists to carry have no Python form. Making the phase a per-run flag is what fits the standard.

## The flag

You pass `--pre` on the one run where you want a prerelease, and the next plain run is back to normal:

```bash
molt version --pre rc      # cut release candidates this run
molt version               # back to a stable release -- no leftover state
```

The only state is the version number itself. Because a PEP 440 version *is* `1.2.1rc0`, the prerelease phase and its counter live in the number, on disk, where you can read them.

## PEP 440 identifiers only

PEP 440 defines a **fixed** set of prerelease spellings, and `--pre` accepts exactly those phases:

| `--pre` value | PEP 440 form | Meaning |
|---|---|---|
| `a` | `1.2.0a0` | alpha |
| `b` | `1.2.0b0` | beta |
| `rc` | `1.2.0rc0` | release candidate |
| `dev` | `1.2.0.dev0` | developmental release |

A named channel such as `1.2.0-next.0` is **not expressible in PEP 440** and is rejected. There is no `--pre next`, because PyPI would reject or renormalize it. If you are used to named prerelease channels, map yours onto one of `a` / `b` / `rc` / `dev`.

## How the counter advances

The prerelease number is derived from the package's **current** version, so successive prerelease runs walk the counter forward on their own -- no separate bookkeeping.

Starting from `acme-core 1.2.0` with a pending `patch` changeset:

```bash
molt version --pre rc      # 1.2.0  ->  1.2.1rc0
molt version --pre rc      # 1.2.1rc0  ->  1.2.1rc1
molt version --pre rc      # 1.2.1rc1  ->  1.2.1rc2
```

Each run computes the target *stable* version from the pending changesets (here `1.2.1`, a patch), then attaches the prerelease phase. When the current version is already a prerelease of that target, molt reads its number and increments it: `rc0` -> `rc1` -> `rc2`. The rule is simply "if the current version is already a prerelease, advance the counter; otherwise start at `0`."

Your changesets stay in place while you iterate -- they describe the stable release you are heading toward, and each prerelease is a preview of it.

## Exiting: a plain `molt version` lands on stable

There is no exit command because there is no mode to leave. When you are ready for the real release, run `molt version` with no `--pre`:

```bash
molt version               # 1.2.1rc2  ->  1.2.1  (consumes the changesets)
```

This works because of PEP 440's [prerelease-aware bump arithmetic](/concepts/versioning-pep440#prerelease-aware-increments): a bump applied to a version that is *already* a prerelease of the target simply strips the prerelease. `patch` on `1.2.1rc2` is `1.2.1`, not `1.2.2` -- the increment was already "spent" when the prerelease was first cut. The pending changesets are consumed on this run, exactly as in a normal release, and their summaries fold into the changelog.

So the full arc reads naturally, with the version number carrying all the state:

```text
1.2.0            ->  molt version --pre rc  ->  1.2.1rc0
1.2.1rc0         ->  molt version --pre rc  ->  1.2.1rc1
1.2.1rc1         ->  molt version           ->  1.2.1   (stable, changesets consumed)
```

## Prereleases and dependent propagation

Prerelease versions interact with the [release plan](/concepts/release-plan) through the same [PEP 440 range rules](/concepts/versioning-pep440#prerelease-opt-in) as everything else. Two consequences worth knowing:

- A dependent whose constraint does **not** opt into prereleases is not satisfied by a prerelease of its dependency. Python resolvers skip prereleases by default, so molt does too, and a prerelease cannot reach an install that did not ask for one.
- A dependent that *has* opted into prereleases (its constraint already names one) is **not** re-released each time its dependency steps `rc0 -> rc1`, because that constraint genuinely still accepts the new prerelease. If you want lockstep movement instead, that is [`update_internal_dependents = "always"`](/concepts/glossary#updateinternaldependents), an explicit choice.

## Where to go next

- [`molt version`](/cli/version) -- the command and its `--pre` flag.
- [`molt pre`](/cli/pre) -- prerelease-related helpers.
- [Versioning and PEP 440](/concepts/versioning-pep440) -- the bump arithmetic that makes exiting a prerelease land on the stable version.
- [Snapshot releases](/concepts/snapshots) -- the other short-lived release shape, for testing an exact commit.
- [Glossary](/concepts/glossary) -- prerelease, snapshot, and the rest.
