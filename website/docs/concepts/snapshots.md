---
title: Snapshot releases
---

# Snapshot releases

A snapshot release is a throwaway version cut from an exact commit -- so a teammate or a CI job can `pip install` the code as it stands right now, without going through the real release flow. You cut one with `molt version --snapshot`.

Snapshots look simple in npm-land, but Python's package index reshapes them. PyPI's constraints are not a footnote here -- they are the reason molt's snapshot design differs from changesets', and understanding them is the point of this page.

## PyPI changes the rules

changesets publishes snapshots as `0.0.0-canary-abcdefg` on npm and hides them behind a **dist-tag** (`npm install pkg@canary`), so the junk version never becomes the default and can be forgotten. That trick relies on four npm behaviors PyPI simply does not have:

| npm | PyPI |
|---|---|
| dist-tags (`latest`, `canary`, ...) | **no dist-tags at all** |
| unpublish / overwrite a version | **immutable -- a version number can never be reused** |
| tolerant of throwaway versions | every upload is a permanent public version |
| accepts arbitrary local suffixes | **rejects local-version suffixes (`+local`)** on upload |

Put together, this means **every snapshot published to PyPI permanently burns a real, public version number**, and there is no tag to hide it behind. changesets' approach cannot work as-is. So molt makes two design choices to keep snapshots from polluting your public release history.

## molt's design

### Snapshots target a non-PyPI index by default

Because a snapshot on PyPI is forever, molt does **not** send snapshots to PyPI by default. They target a **separate index** -- a private or dev index, or TestPyPI -- so testing an exact commit never consumes a public version number. Publishing a snapshot to real PyPI is possible but deliberately not the default path; see [Publishing](/guides/publishing).

### Snapshot versions are `0.0.0.dev<datetime>`

Since local suffixes are not uploadable and there is no dist-tag to lean on, molt shapes snapshot versions out of the parts PEP 440 *does* allow. The default is a `.devN` release built from a timestamp:

```text
0.0.0.dev20211213000730
```

Two properties make this the right shape:

- **The `0.0.0` base sorts below every real release.** A snapshot can never accidentally out-rank a legitimate `1.2.0` for a resolver -- it is always the lowest thing on the shelf. (changesets uses `0.0.0` for the same reason.)
- **`.devN` is monotonic and PyPI-uploadable.** The `<datetime>` is a 14-digit `YYYYMMDDHHMMSS` stamp, so later snapshots sort after earlier ones, and the version is legal to upload to any index.

A `type: none` release gets **no suffix at all** -- it keeps its old version, exactly as in a normal plan. And, as with every plan, the timestamp is computed **once per run**, so all snapshots in a single invocation share one `<datetime>` and move together.

### The tag-vs-`.dev` composition is an area of care

changesets lets you decorate a snapshot with a free-form tag (`0.0.0-experimental-<datetime>`) and template it however you like. PEP 440 has no free-form field in the *public* part of a version, which forces a genuine trade-off molt calls out openly:

| Snapshot idea | PEP 440-legal rendering | Catch |
|---|---|---|
| datetime only | `0.0.0.dev20211213000730` | clean and uploadable -- the default |
| datetime + tag | `0.0.0.dev20211213000730+experimental` | the tag lives in the **local** segment, which PyPI will **not** accept |
| commit hash | `0.0.0+abcdefg` | hex only fits the local segment -- **not uploadable** |

So the honest rule is: **`{timestamp}` / `{datetime}` snapshots stay as `.devN` and are uploadable everywhere; a `{tag}` or `{commit}` decoration can only live in the local segment**, which means it is fine for a private index or local testing but cannot be published to PyPI. molt keeps tag/commit decoration behind an explicit opt-in and warns loudly when the resulting version is not PyPI-uploadable. Exactly how tags survive alongside `.devN` is an area still being refined -- treat the datetime `.devN` form as the stable default.

## What a snapshot run does

When molt rewrites manifests for a snapshot, a dependent's pin on a snapshotted package is written as the **bare snapshot version**, not a range -- there is no meaningful range around a `0.0.0.dev...` build.

A snapshot run also **consumes the pending changesets** (it does not preserve them the way [prerelease mode](/concepts/prerelease) does), and it does not commit or tag anything. The expectation is that you run `molt version --snapshot` on a **throwaway checkout** in CI -- build, publish to the dev index, discard -- rather than on the branch you are going to release for real.

## Snapshot vs prerelease

Both are short-lived, non-final releases, but they answer different questions:

- A [prerelease](/concepts/prerelease) (`--pre rc`) is a *staged step toward a specific stable version* -- `1.2.1rc0` on its way to `1.2.1`, with a counter that advances and an exit that lands on the real number.
- A **snapshot** (`--snapshot`) is a *disposable build of an exact commit* -- `0.0.0.dev<datetime>`, sorting below everything, not heading anywhere, meant to be installed and thrown away.

## Where to go next

- [Publishing](/guides/publishing) -- targeting an index, and where snapshots go by default.
- [Prerelease mode](/concepts/prerelease) -- the other non-final release shape.
- [Versioning and PEP 440](/concepts/versioning-pep440) -- why `.devN` sorts where it does.
- [Glossary](/concepts/glossary) -- snapshot, prerelease, and the rest.
