import { useId, useState } from 'react';
import styles from './styles.module.css';

/**
 * The release loop, drawn.
 *
 * Two variants, because the shape is the same but three details are not: what a changeset names,
 * what the release pull request contains, and what the publish phase uploads and tags. Readers
 * arrive knowing which of the two they have, so the diagram asks once and then commits, rather
 * than hedging every line with "or, in a monorepo".
 */

type Actor = 'you' | 'molt';

interface FileLine {
  /** Rendered in monospace, as it appears on disk. */
  path: string;
  /** Plain-language note shown beside the path. */
  note?: string;
  /** Marks the line the step exists for, so the eye lands on it first. */
  accent?: boolean;
}

interface Phase {
  actor: Actor;
  title: string;
  /** One sentence under the title. Keep it to what the step does. */
  detail: string;
  files?: FileLine[];
  bullets?: string[];
  /** The connector text below the card. */
  arrow?: string;
  /** Draws the step as the moment of release. */
  release?: boolean;
}

interface Variant {
  id: string;
  label: string;
  /** Shown under the tabs, naming the project this column describes. */
  summary: string;
  before: Phase[];
  after: Phase[];
}

const FORK = {
  question: 'Does molt find pending changesets?',
  yes: {
    answer: 'Yes',
    label: 'Version phase',
    detail: 'Open or update the release pull request. Nothing is published.',
  },
  no: {
    answer: 'No',
    label: 'Publish phase',
    detail: 'Everything on disk is released. This is the push that merged the release pull request.',
  },
} as const;

const VARIANTS: Variant[] = [
  {
    id: 'single',
    label: 'Single package',
    summary: 'One distribution, one version, one tag.',
    before: [
      {
        actor: 'you',
        title: 'Open a pull request',
        detail: 'Your change, plus one file saying what it releases.',
        files: [
          { path: 'src/acme/export.py', note: 'your actual change' },
          { path: '.changeset/lucky-pandas-sing.md', note: 'written by molt add', accent: true },
        ],
        arrow: 'CI runs your checks, and molt status fails the run if no changeset covers the change',
      },
      {
        actor: 'you',
        title: 'Merge it',
        detail: 'The push to your base branch starts the loop.',
      },
    ],
    after: [
      {
        actor: 'molt',
        title: 'Version phase',
        detail:
          'A pull request titled "Version Packages", on the branch changeset-release/main. No approval is asked for and nothing leaves the repository.',
        files: [
          { path: 'pyproject.toml', note: 'version 1.2.0 -> 1.2.1', accent: true },
          { path: 'CHANGELOG.md', note: 'a new 1.2.1 section' },
          { path: 'uv.lock', note: 'refreshed' },
          { path: '.changeset/lucky-pandas-sing.md', note: 'consumed, deleted' },
        ],
        arrow: 'Every further pull request that merges updates this same pull request, never a new one',
      },
      {
        actor: 'you',
        title: 'Merge the release pull request',
        detail: 'This is the release. You never pick the version; the changesets did.',
        release: true,
      },
      {
        actor: 'molt',
        title: 'Publish phase',
        detail: 'One package goes out.',
        bullets: [
          'Build the sdist and the wheel',
          'Upload to PyPI over OIDC, with no stored token',
          'Push the tag v1.2.1',
          'Create the release, with the changelog entry as its body',
        ],
      },
    ],
  },
  {
    id: 'monorepo',
    label: 'Monorepo',
    summary: 'Many distributions in one workspace, versioned together and released in order.',
    before: [
      {
        actor: 'you',
        title: 'Open a pull request',
        detail: 'One changeset can name several packages, each with its own bump.',
        files: [
          { path: 'packages/acme-core/src/...', note: 'your actual change' },
          { path: '.changeset/lucky-pandas-sing.md', note: 'acme-core: major', accent: true },
        ],
        arrow: 'CI runs your checks, and molt status fails the run if no changeset covers the change',
      },
      {
        actor: 'you',
        title: 'Merge it',
        detail: 'The push to your base branch starts the loop.',
      },
    ],
    after: [
      {
        actor: 'molt',
        title: 'Version phase',
        detail:
          'The same "Version Packages" pull request, carrying the packages you named and the ones that depend on them.',
        files: [
          { path: 'packages/acme-core/pyproject.toml', note: '1.2.0 -> 2.0.0', accent: true },
          { path: 'packages/acme-cli/pyproject.toml', note: '0.5.0 -> 0.5.1, pin rewritten', accent: true },
          { path: 'packages/*/CHANGELOG.md', note: 'one entry each' },
          { path: 'uv.lock', note: 'refreshed' },
        ],
        bullets: [
          'acme-cli had no changeset of its own. 2.0.0 fell outside its pin, so it needs a release.',
          'It gets the smallest bump that does the job, and its pin becomes >=2.0.0,<3.0.0.',
          'A bump that stayed inside the pin would have released nothing extra.',
        ],
        arrow: 'Every further pull request that merges updates this same pull request, never a new one',
      },
      {
        actor: 'you',
        title: 'Merge the release pull request',
        detail: 'This is the release. The whole set goes out together.',
        release: true,
      },
      {
        actor: 'molt',
        title: 'Publish phase',
        detail: 'Every changed package, in dependency order, so nothing is briefly unresolvable.',
        bullets: [
          'Build each changed package',
          'Upload in dependency order: acme-core, then acme-cli',
          'Push one tag per package: acme-core@2.0.0, acme-cli@0.5.1',
          'Create one release per package, each with its own changelog entry',
        ],
      },
    ],
  },
];

function ActorBadge({ actor }: { actor: Actor }) {
  return (
    <span className={actor === 'you' ? styles.badgeYou : styles.badgeMolt}>
      {actor === 'you' ? 'You' : 'molt'}
    </span>
  );
}

function PhaseCard({ phase, connector = true }: { phase: Phase; connector?: boolean }) {
  return (
    <>
      <div className={phase.release ? `${styles.card} ${styles.cardRelease}` : styles.card}>
        <div className={styles.cardHead}>
          <ActorBadge actor={phase.actor} />
          <h4 className={styles.cardTitle}>{phase.title}</h4>
          {phase.release ? <span className={styles.releaseTag}>the release</span> : null}
        </div>
        <p className={styles.cardDetail}>{phase.detail}</p>

        {phase.files ? (
          <ul className={styles.files}>
            {phase.files.map((file) => (
              <li key={file.path} className={file.accent ? styles.fileAccent : styles.file}>
                <code className={styles.filePath}>{file.path}</code>
                {file.note ? <span className={styles.fileNote}>{file.note}</span> : null}
              </li>
            ))}
          </ul>
        ) : null}

        {phase.bullets ? (
          <ul className={styles.bullets}>
            {phase.bullets.map((bullet) => (
              <li key={bullet}>{bullet}</li>
            ))}
          </ul>
        ) : null}
      </div>

      {connector ? <Connector label={phase.arrow} /> : null}
    </>
  );
}

function Connector({ label }: { label?: string }) {
  return (
    <div className={styles.connector} aria-hidden="true">
      <span className={styles.line} />
      {label ? <span className={styles.connectorLabel}>{label}</span> : null}
      <span className={styles.line} />
      <span className={styles.chevron} />
    </div>
  );
}

export function ReleaseFlow() {
  const [active, setActive] = useState(VARIANTS[0].id);
  const tabsId = useId();
  const variant = VARIANTS.find((candidate) => candidate.id === active) ?? VARIANTS[0];

  return (
    <div className={styles.wrap}>
      <div className={styles.tabs} role="tablist" aria-label="Project shape">
        {VARIANTS.map((candidate) => (
          <button
            key={candidate.id}
            type="button"
            role="tab"
            id={`${tabsId}-${candidate.id}`}
            aria-selected={candidate.id === active}
            aria-controls={`${tabsId}-${candidate.id}-panel`}
            className={candidate.id === active ? styles.tabActive : styles.tab}
            onClick={() => setActive(candidate.id)}
          >
            {candidate.label}
          </button>
        ))}
      </div>

      <div
        className={styles.panel}
        role="tabpanel"
        id={`${tabsId}-${variant.id}-panel`}
        aria-labelledby={`${tabsId}-${variant.id}`}
      >
        <p className={styles.summary}>{variant.summary}</p>

        {variant.before.map((phase) => (
          <PhaseCard key={phase.title} phase={phase} />
        ))}

        <div className={styles.fork}>
          <p className={styles.forkQuestion}>{FORK.question}</p>
          <div className={styles.branches}>
            {[FORK.yes, FORK.no].map((branch) => (
              <div key={branch.answer} className={styles.branch}>
                <span className={styles.branchAnswer}>{branch.answer}</span>
                <span className={styles.branchLabel}>{branch.label}</span>
                <span className={styles.branchDetail}>{branch.detail}</span>
              </div>
            ))}
          </div>
        </div>

        <Connector />

        {variant.after.map((phase, index) => (
          <PhaseCard
            key={phase.title}
            phase={phase}
            connector={index < variant.after.length - 1}
          />
        ))}

        <p className={styles.footnote}>
          Only one phase runs per push, never both. molt decides which by looking for pending
          changesets.
        </p>
      </div>
    </div>
  );
}

export default ReleaseFlow;
