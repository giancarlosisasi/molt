import { useEffect } from 'react';
import { inject } from '@vercel/analytics';

/**
 * Vercel Web Analytics, mounted on every route through `globalUIComponents`.
 *
 * `inject` has to run in the browser, so it is called from an effect rather than at module scope —
 * the same reason this renders nothing: the static HTML rspress writes at build time stays
 * identical to the first client render.
 *
 * Route changes need no wiring here. The injected script counts `pushState` navigations itself,
 * which is what makes one call cover an SPA router. Locally there is nothing to send to: `dev`
 * loads Vercel's debug script, which only logs, and the production endpoint exists only on Vercel.
 */
export default function Analytics() {
  useEffect(() => {
    inject();
  }, []);

  return null;
}
