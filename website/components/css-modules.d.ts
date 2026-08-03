// CSS Modules are resolved by Rsbuild, which strips types rather than checking them. This keeps an
// editor and a `tsc --noEmit` run from reporting the imports as missing modules.
declare module '*.module.css' {
  const classes: Record<string, string>;
  export default classes;
}
