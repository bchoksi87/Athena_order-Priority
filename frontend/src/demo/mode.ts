/** Build-time switch for DEMO MODE (`.env.demo`, `npm run build:demo`); dependency-free so any module may ask. */
export function isDemoMode(): boolean {
  return import.meta.env.VITE_DEMO_MODE === "true";
}
