# Documentation style

Rules for every page under `docs/`, for the repository `README.md`, and for any user-facing prose
molt prints. Check a change against this file before merging it.

## Voice

Write from shipped. Present tense, indicative mood. `molt version refreshes uv.lock in the same
commit.` No roadmaps, no "planned", no "coming soon", no "pre-alpha", no status badges, no
`status:` frontmatter. A changelog, an upgrade guide, and a deprecation notice are the only places
a version or a release state belongs, because that is their subject.

Second person for the reader, third for the product. Avoid "we" outside the contributing guide.
they/them for any unspecified person.

American spelling. `molt` lowercase in prose, including at the start of a sentence where the
sentence can be reworded to avoid it; `Molt` when it must start one.

## Titles are nouns

Every page title, `##` heading, card title, and nav label names a thing. A heading that is a claim,
a question, or a slogan cannot be scanned for.

| Not this | This |
|---|---|
| Why the forge is a seam | The reason for the seam |
| Why intent beats commit-derived versioning | Changesets and commit-derived versioning |
| Will `molt version` delete my comments? | Comments and formatting in `pyproject.toml` |
| Caching and backoff, done right | Caching and backoff |
| What molt will not do | Out of scope |

## changesets, and every other tool

Molt's workflow comes from [changesets](https://github.com/changesets/changesets). That is
credited on `/reference/acknowledgements`, and it is not re-argued anywhere else.

Do not write:

- issue numbers, issue ages, reply counts, or "requested for N years"
- "rejected", "ignored", "stretched thin", "most-disliked", "abandoned", "died", "stalled"
- "the moat", "prior Python ports", "what changesets cannot do", "a fix over changesets"
- "faithful port", "a port of changesets" as a description of what molt is
- any sentence whose subject is another project's decision-making

Do write, when a reader genuinely needs the comparison:

- the two behaviors side by side, as facts, in a table
- the ecosystem reason they differ: `PyPI versions are permanent`, `PEP 440 has no arbitrary
  prerelease tags`, `Python has five workspace conventions`
- `If you are migrating from changesets:` as a lead-in, followed by what to type instead

The one page that compares the two tools is `/reference/comparison-with-changesets`. It uses the
support matrix and the ✅ / ⬜ / ➖ / 🚫 legend defined there, and no adjectives.

## Tells to hunt

A tell is a construction that reads as machine-written. Grep for these before merging.

| Tell | Do instead |
|---|---|
| An em dash used as a drum beat before a punchline | A full stop, a comma, or a colon |
| `Not X, but Y` / `X, not Y` | Say Y |
| `It is not that X. It is that Y.` | Say Y |
| `deliberately`, `intentionally`, `on purpose` | Delete. The reader did not challenge the decision |
| `simply`, `just`, `merely`, `of course`, `obviously` | Delete |
| `powerful`, `seamless`, `first-class`, `robust`, `real`, `honest` | Name the concrete thing |
| `the whole point`, `that is the entire surface` | Delete |
| A fragment for emphasis. `Every time.` | Join it to the sentence before |
| Three parallel items used for rhythm | One, or a list |
| A rhetorical question as a heading | A noun phrase |
| `Think of it as…` | Describe it directly |

**Two em dashes per page, maximum.** Count them before saving. The site writes them as `--`,
so the check is `grep -c -- ' -- '`.

## Facts

Every claim traces to the source: `openspec/specs/`, the CLI's `--help`, or the code. A measured
number is a plain fact (`945 pipelines take about four seconds`), never an argument (`measured on
the real API, with nothing rounded up`).

## Page shapes

- **Feature page:** lede, what you see, what you can do, one or two specific mechanics, related links.
- **Recipe page:** what you want on screen, steps as a numbered table, variations, related links.
- **Reference page:** a two-line lede, then tables. Completeness beats readability here.
- **Section index:** a short lede and cards. No essay.

## Checklist before merging a page

1. Em dashes: two or fewer.
2. Every heading names a thing.
3. No sentence about the project's state, a version, or the future.
4. No sentence whose subject is another project's decision-making.
5. First sentence answers "what is this page about", not "why should you care".
6. Every factual claim traces to a source.
