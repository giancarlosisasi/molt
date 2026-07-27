---
title: The release plan
---

# The release plan

The release plan is the calculated object that says exactly what a set of changesets will release: which packages move, to what version, and why -- including every package that has to move because *something it depends on* moved.

This is the heart of molt. Turning "`acme-core` gets a minor" into "...and therefore `acme-http` needs a patch, and its dependency pin has to be rewritten, and `acme-cli` needs one too" is the hard 80% of release automation. It is where the two prior Python attempts stalled, and it is the reason molt exists. See [Why Molt?](/introduction/why-molt) for that history.

## The plan is a value, not a side effect

Every mutating molt command computes a plan *before* it touches disk. The plan is an inspectable, machine-readable object, and `--dry-run` prints it and writes nothing:

```bash
molt version --dry-run
```

```json
{
  "releases": [
    {
      "name": "acme-core",
      "type": "major",
      "oldVersion": "1.4.2",
      "newVersion": "2.0.0",
      "changesets": ["slow-lions-cough"]
    },
    {
      "name": "acme-http",
      "type": "patch",
      "oldVersion": "2.1.0",
      "newVersion": "2.1.1",
      "changesets": []
    },
    {
      "name": "acme-cli",
      "type": "patch",
      "oldVersion": "0.8.0",
      "newVersion": "0.8.1",
      "changesets": []
    }
  ],
  "changesets": ["slow-lions-cough"]
}
```

Only one changeset was written (a `major` on `acme-core`), yet three packages release. `acme-http` and `acme-cli` carry `changesets: []` -- their releases were *derived*, not requested. Working out those derived releases correctly is the whole job of the engine. You can inspect the same plan on `add`, `publish`, and `build`; see [Dry runs and plans](/guides/dry-run-and-plans).

## A worked example

Take a three-package workspace:

```toml
# acme-core/pyproject.toml
[project]
name = "acme-core"
version = "1.4.2"

# acme-http/pyproject.toml   (depends on acme-core)
[project]
name = "acme-http"
version = "2.1.0"
dependencies = ["acme-core>=1.4.0,<2.0.0"]

# acme-cli/pyproject.toml   (depends on both)
[project]
name = "acme-cli"
version = "0.8.0"
dependencies = ["acme-http>=2.0.0,<3.0.0", "acme-core>=1.4.0,<2.0.0"]
```

Now a single changeset lands a **major** on `acme-core`:

```md
---
"acme-core": major
---

Rename the `Client.fetch` method to `Client.request`.
```

Here is what the engine reasons through:

1. `acme-core` moves `1.4.2` -> `2.0.0` (the requested major).
2. `acme-http` requires `acme-core>=1.4.0,<2.0.0`. The new `2.0.0` falls **outside** that range, so `acme-http` in development would no longer match a real install of `acme-http` from PyPI. It gets a **patch**: `2.1.0` -> `2.1.1`. molt also rewrites its pin so the published `acme-http` requires the new `acme-core`.
3. `acme-cli` requires `acme-core>=1.4.0,<2.0.0` too. Same story: `2.0.0` is out of range, so `acme-cli` gets a **patch**: `0.8.0` -> `0.8.1`.
4. `acme-cli` also requires `acme-http>=2.0.0,<3.0.0`. The new `acme-http` `2.1.1` is still *inside* that range, so that edge alone would not force a release -- but `acme-cli` is already releasing because of `acme-core`, so nothing new happens.

The output is the plan shown above. Two rules are doing the work here, and both are worth stating precisely:

- **Propagation is by out-of-range, and it is always a patch.** A dependent is released only when a dependency's new version leaves the dependent's declared range, and the resulting bump is always `patch` -- never more. If you want a bigger bump on the dependent, add a changeset for it explicitly. When "out of range" fires depends on the range shape: an exact pin breaks on any change, `~=` (tilde) breaks on minor or major, and a caret-style `>=x,<x+1` range breaks only on major. That table is exactly [PEP 440 range satisfaction](/concepts/versioning-pep440), which is the same math throughout molt.
- **Whether the pin is rewritten is a separate decision.** Releasing a dependent and rewriting the version constraint in its manifest are two different questions, governed by two different (unhappily similar) options -- [`update_internal_dependents`](/concepts/glossary#updateinternaldependents) and [`update_internal_dependencies`](/concepts/glossary#updateinternaldependencies).

For a guide-level walkthrough of propagation across a larger graph, see [Dependency propagation](/guides/dependency-propagation).

## The engine: three passes to a fixpoint

The example above needed only one sweep, but real workspaces have cycles of consequence: releasing one package raises a group, which pushes another package out of range, which raises another group. molt handles this the way the reference engine does -- by running three passes in a fixed order and **repeating until nothing changes**:

```
repeat until a full round changes nothing:
    1. propagate to dependents   # release packages left out of range by a mover
    2. raise fixed groups        # pull every member of a fixed group up to the group max
    3. align linked groups       # pull releasing members of a linked group up to the group max
```

Each pass reads the current set of releases and may add to it or raise existing entries. The passes feed each other, which is exactly why the loop is needed:

- Pass 1 can release `acme-http` because `acme-core` left its range.
- If `acme-http` is in a [fixed group](/concepts/linked-vs-fixed) with `acme-billing`, pass 2 raises `acme-billing` to the group's version -- even though nothing changed it directly.
- `acme-billing` moving may now push *its* dependents out of range -- work that only pass 1 of the **next** iteration will discover.

So a single pass is not enough; the engine loops until a complete round -- all three passes -- reports no change. That converged state is the release plan.

### Why order is load-bearing

The three passes run in that order on purpose, and the order is observable in the output. `fixed` and `linked` alignment runs *after* dependent propagation within each iteration, so a group can absorb a propagated patch: if propagation gives `acme-http` a `patch` but its fixed group is moving by `minor`, the group's `minor` overwrites the `patch` in the same iteration, and `acme-http` lands on the group version rather than a stray patch. Reorder the passes and you get different -- wrong -- numbers. The plan's release ordering is insertion order (requested releases first, then dependents in discovery order, then group members), and molt preserves it deterministically.

### Why it terminates

A loop that "repeats until nothing changes" only halts if change cannot go on forever. molt's does, because **every mutation is monotone up a finite lattice**:

- A release's bump type only ever climbs `none < patch < minor < major`, and a pass refuses to re-assign a type a release already has. It can go up; it can never come back down.
- The `oldVersion` a group aligns to is `max(current versions of the group's members)`, read **from disk** -- never from the evolving plan. It is a fixed target, not a moving one.
- The set of releasing packages only grows, and it is bounded by the size of the workspace.

So the number of state changes is bounded (roughly "packages times bump levels"), and the loop provably reaches a fixpoint. This is not a detail to gloss over: a naive implementation that reports "changed" on every pass unconditionally never terminates. molt's property tests pin both **termination** (the loop always halts) and **confluence** (the final version numbers do not depend on the order changesets were fed in, even though the *output* order is insertion-defined). The monotonicity that makes the bump arithmetic strictly increasing is the same property the loop's termination rests on -- see [Versioning and PEP 440](/concepts/versioning-pep440).

## Grouping mechanisms in the plan

Two config-driven mechanisms let packages move together, and both are resolved inside the fixpoint loop:

- **fixed** -- every member of the group always releases together, at one shared version, even members nothing changed.
- **linked** -- only members that are *already* releasing get aligned to the group's version; members with no reason to release are left alone.

They look almost identical and are constantly confused. The precise difference -- and why the same setup produces three releases under `fixed` but two under `linked` -- is spelled out in [Linked vs fixed packages](/concepts/linked-vs-fixed).

## Where to go next

- [Dependency propagation](/guides/dependency-propagation) -- a hands-on tour of how bumps travel the workspace graph.
- [Dry runs and plans](/guides/dry-run-and-plans) -- inspecting the plan on every command before you run it.
- [Linked vs fixed packages](/concepts/linked-vs-fixed) -- the two grouping mechanisms, precisely distinguished.
- [Versioning and PEP 440](/concepts/versioning-pep440) -- the bump arithmetic and range math the engine runs on.
- [Glossary](/concepts/glossary) -- release plan, dependent, `update_internal_*`, and the rest.
