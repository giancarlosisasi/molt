import path from 'node:path';
import { defineConfig } from '@rspress/core';

// Navigation & sidebar are file-driven (rspress v2 "auto nav/sidebar"):
//   - Top nav bar   -> docs/_nav.json
//   - Section sidebars -> docs/<section>/_meta.json  (ordering + labels)
//
// Do NOT set `themeConfig.nav` or `themeConfig.sidebar` here: in @rspress/core
// 2.x, defining either one in the config short-circuits the auto nav/sidebar
// walk (see auto-nav-sidebar/index.ts `haveNavSidebarConfig`), which would make
// every `_meta.json` inert and blank the sidebars. Keeping them out of the
// config is what lets the per-directory `_meta.json` files remain the single
// source of truth that downstream doc pages are written against.
export default defineConfig({
  root: 'docs',
  title: 'Molt',
  description: 'Changeset-driven versioning and changelogs for Python monorepos',
  // `cleanUrls` only changes the links rspress *renders*; the SSG still writes one
  // flat file per route (`docs/cli/add.md` -> `doc_build/cli/add.html`, see
  // @rspress/core `ssg/htmlFile.js`). It must stay in lockstep with `cleanUrls` in
  // vercel.json, which is what makes Vercel serve `cli/add.html` at `/cli/add` and
  // 308 the old `/cli/add.html` onto it. Turn one off without the other and either
  // every internal link eats a redirect, or every link 404s.
  route: {
    cleanUrls: true,
  },
  globalStyles: path.join(import.meta.dirname, 'theme/molt.css'),
  themeConfig: {
    socialLinks: [
      {
        icon: 'github',
        mode: 'link',
        content: 'https://github.com/giancarlosisasi/molt',
      },
    ],
  },
});
