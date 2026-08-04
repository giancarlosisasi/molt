import { useCallback, useEffect, useState } from 'react';
import {
  COOKIE_SETTINGS_EVENT,
  type Consent,
  disableAnalytics,
  enableAnalytics,
  readConsent,
  writeConsent,
} from './consent';
import styles from './styles.module.css';

/**
 * The consent banner, mounted on every route through `globalUIComponents`.
 *
 * It renders nothing until the effect has read the stored answer. That is what keeps the static
 * HTML rspress writes at build time identical to the first client render: the build has no
 * `localStorage` to read, so any markup decided before mount would be markup hydration has to
 * throw away.
 */
export default function CookieConsent() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const stored = readConsent();
    if (stored === 'granted') {
      enableAnalytics();
    }
    setOpen(stored === null);

    const reopen = () => setOpen(true);
    window.addEventListener(COOKIE_SETTINGS_EVENT, reopen);
    return () => window.removeEventListener(COOKIE_SETTINGS_EVENT, reopen);
  }, []);

  const choose = useCallback((consent: Consent) => {
    writeConsent(consent);
    if (consent === 'granted') {
      enableAnalytics();
    } else {
      disableAnalytics();
    }
    setOpen(false);
  }, []);

  if (!open) {
    return null;
  }

  return (
    <div className={styles.banner} role="dialog" aria-label="Cookies">
      {/* One sentence, naming the purpose. The vendor, the cookie names and the retention live on
          the privacy page, which is the layered notice regulators ask for. */}
      <p className={styles.text}>
        This site uses cookies to measure traffic.{' '}
        <a className={styles.link} href="/reference/privacy">
          Privacy
        </a>
      </p>
      {/* Both buttons carry one class. Rejecting has to look exactly as easy as accepting, and a
          filled "Accept" beside an outlined "Reject" is the pattern EU regulators name most often
          as an invalid choice. */}
      <div className={styles.actions}>
        <button type="button" className={styles.choice} onClick={() => choose('denied')}>
          Reject
        </button>
        <button type="button" className={styles.choice} onClick={() => choose('granted')}>
          Accept
        </button>
      </div>
    </div>
  );
}

/**
 * Reopens the banner from a page. Withdrawing an answer has to be as easy as giving one, and the
 * banner is gone once either button is pressed, so something has to bring it back.
 */
export function CookieSettings() {
  return (
    <button
      type="button"
      className={styles.settings}
      onClick={() => window.dispatchEvent(new Event(COOKIE_SETTINGS_EVENT))}
    >
      Change your cookie choice
    </button>
  );
}
