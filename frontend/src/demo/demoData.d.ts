import type { DemoData } from "./types";

/** Loads `demo-data.json` (its own build chunk) once; see demoData.js. */
export function loadDemoDataset(): Promise<DemoData>;
