// Loads the captured dataset as a lazily imported chunk. This is a plain JS module (typed by
// demoData.d.ts) so `tsc` never parses the multi-megabyte JSON; Vite still bundles the JSON
// into its own chunk that is loaded with a relative URL — no runtime fetch of a .json file.
export async function loadDemoDataset() {
  const mod = await import("./demo-data.json");
  return mod.default;
}
