import styles from './styles.module.css';

/**
 * The two clocks, and the buffer between them.
 *
 * The point the prose has to make three times and a picture makes once: intent accumulates on one
 * clock and is consumed on another, so the number of changesets is not the number of releases.
 * `acme-core` carries a minor and a patch here, and moves once, by the minor. That single row is
 * the reason the diagram exists; everything else is scaffolding around it.
 */

type Bump = 'major' | 'minor' | 'patch';

interface Intent {
  pr: string;
  file: string;
  pkg: string;
  bump: Bump;
  /** Marks the two entries that collapse into one release, which is the thing to notice. */
  collapses?: boolean;
}

interface Release {
  pkg: string;
  from: string;
  to: string;
  bump: Bump;
  note?: string;
}

const INTENTS: Intent[] = [
  { pr: 'PR #41', file: 'slow-lions-cough.md', pkg: 'acme-core', bump: 'minor', collapses: true },
  { pr: 'PR #42', file: 'brave-mugs-sing.md', pkg: 'acme-cli', bump: 'patch' },
  { pr: 'PR #43', file: 'tidy-eels-return.md', pkg: 'acme-core', bump: 'patch', collapses: true },
];

const RELEASES: Release[] = [
  {
    pkg: 'acme-core',
    from: '1.2.0',
    to: '1.3.0',
    bump: 'minor',
    note: 'two changesets, one release: the minor wins',
  },
  { pkg: 'acme-cli', from: '0.5.0', to: '0.5.1', bump: 'patch' },
];

function BumpChip({ bump, muted }: { bump: Bump; muted?: boolean }) {
  return <span className={muted ? styles.chipMuted : styles.chip}>{bump}</span>;
}

function Arrow() {
  return <div className={styles.arrow} aria-hidden="true" />;
}

export function ChangesetClocks() {
  return (
    <figure className={styles.wrap}>
      <div className={styles.grid}>
        <section className={styles.col}>
          <header className={styles.colHead}>
            <h4 className={styles.colTitle}>Development clock</h4>
            <p className={styles.colSub}>Ticks once per change, by whoever wrote it.</p>
          </header>
          <ul className={styles.list}>
            {INTENTS.map((intent) => (
              <li key={intent.file} className={styles.intent}>
                <span className={styles.pr}>{intent.pr}</span>
                <code className={styles.cmd}>molt add</code>
                <span className={styles.intentPkg}>
                  <code className={styles.pkg}>{intent.pkg}</code>
                  <BumpChip bump={intent.bump} muted={!intent.collapses} />
                </span>
              </li>
            ))}
          </ul>
        </section>

        <Arrow />

        <section className={styles.colBuffer}>
          <header className={styles.colHead}>
            <h4 className={styles.colTitle}>
              <code className={styles.dir}>.changeset/</code>
            </h4>
            <p className={styles.colSub}>The buffer. Nothing is released while it fills.</p>
          </header>
          <ul className={styles.bufferList}>
            {INTENTS.map((intent) => (
              <li key={intent.file} className={styles.bufferFile}>
                <code className={styles.file}>{intent.file}</code>
              </li>
            ))}
          </ul>
        </section>

        <Arrow />

        <section className={styles.col}>
          <header className={styles.colHead}>
            <h4 className={styles.colTitle}>Release clock</h4>
            <p className={styles.colSub}>
              Ticks when you decide. <code className={styles.cmd}>molt version</code> drains the
              whole buffer at once.
            </p>
          </header>
          <ul className={styles.list}>
            {RELEASES.map((release) => (
              <li key={release.pkg} className={styles.release}>
                <code className={styles.pkg}>{release.pkg}</code>
                <span className={styles.versions}>
                  <span className={styles.from}>{release.from}</span>
                  <span className={styles.to}>{release.to}</span>
                </span>
                {release.note ? <span className={styles.releaseNote}>{release.note}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      </div>

      <figcaption className={styles.caption}>
        Three changesets, two releases. Nobody picked a version number when they opened a pull
        request, because the number depends on everything shipping in the release.
      </figcaption>
    </figure>
  );
}

export default ChangesetClocks;
