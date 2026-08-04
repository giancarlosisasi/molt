/*
 * Analytics, and the reader's answer about it.
 *
 * Nothing here runs at import time. The Google tag is fetched, and the first cookie written, by
 * `enableAnalytics` alone, which the banner calls on "Accept" and on a later visit that finds a
 * stored "granted". Until then the site makes no request to Google and sets no analytics cookie,
 * which is what "consent before loading" has to mean to be worth anything.
 */

const MEASUREMENT_ID = 'G-WK4TPRME0B';

const STORAGE_KEY = 'molt:analytics-consent';

/** Dispatch this on `window` to reopen the banner after a choice is stored. */
export const COOKIE_SETTINGS_EVENT = 'molt:cookie-settings';

export type Consent = 'granted' | 'denied';

declare global {
  interface Window {
    dataLayer?: unknown[];
  }
}

/**
 * gtag.js drains the queue by reading each entry as an `arguments` object, so this pushes that
 * rather than an array. A zero-parameter function is assignable to the call signature, so the
 * queue stays faithful to Google's snippet without a cast.
 */
const gtag: (...args: unknown[]) => void = function () {
  window.dataLayer?.push(arguments);
};

/**
 * `localStorage` throws rather than returning null when a browser blocks storage, which is the
 * same browser most likely to be running a strict privacy mode. Treating that as "no answer yet"
 * keeps the banner working there; the cost is that it asks again next visit.
 */
export function readConsent(): Consent | null {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === 'granted' || stored === 'denied' ? stored : null;
  } catch {
    return null;
  }
}

export function writeConsent(consent: Consent): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, consent);
  } catch {
    // Storage is blocked. The choice still applies to this page view.
  }
}

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '']);

/** `rspress dev` and `rspress preview` both serve from here, and neither should report traffic. */
function isLocalHost(): boolean {
  return LOCAL_HOSTS.has(window.location.hostname);
}

let loaded = false;

export function enableAnalytics(): void {
  if (loaded || isLocalHost()) {
    return;
  }
  loaded = true;

  window.dataLayer = window.dataLayer ?? [];

  // Consent Mode v2. The defaults deny every storage class, so the tag cannot fall back to an
  // advertising cookie on the strength of the analytics answer alone. Both calls have to be
  // queued before the tag script loads.
  gtag('consent', 'default', {
    ad_storage: 'denied',
    ad_user_data: 'denied',
    ad_personalization: 'denied',
    analytics_storage: 'denied',
  });
  gtag('consent', 'update', { analytics_storage: 'granted' });

  gtag('js', new Date());
  // Route changes are counted by GA4 enhanced measurement, which listens for the History API
  // calls rspress's router makes. Sending a page_view here as well would double every route.
  gtag('config', MEASUREMENT_ID);

  const tag = document.createElement('script');
  tag.async = true;
  tag.src = `https://www.googletagmanager.com/gtag/js?id=${MEASUREMENT_ID}`;
  document.head.append(tag);
}

/**
 * Withdrawal has to stop collection in the tab it happens in, not at the next reload. The
 * `ga-disable-*` flag is the tag's own opt-out switch and is read before every hit, so it holds
 * even though the script is already in the page.
 */
export function disableAnalytics(): void {
  (window as unknown as Record<string, boolean>)[`ga-disable-${MEASUREMENT_ID}`] = true;
  clearAnalyticsCookies();
}

const ANALYTICS_COOKIE = /^_(ga|gid|gat)/;

function clearAnalyticsCookies(): void {
  const { hostname } = window.location;
  // The tag writes `_ga` on the registrable domain, so an expiry sent for the exact hostname
  // alone leaves it in place.
  const domains = ['', hostname, `.${hostname}`, `.${hostname.split('.').slice(-2).join('.')}`];

  for (const entry of document.cookie.split(';')) {
    const name = entry.split('=')[0]?.trim();
    if (!name || !ANALYTICS_COOKIE.test(name)) {
      continue;
    }
    for (const domain of domains) {
      const scope = domain ? `; domain=${domain}` : '';
      document.cookie = `${name}=; path=/${scope}; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
    }
  }
}
