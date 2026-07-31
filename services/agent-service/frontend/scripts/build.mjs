import path from "node:path";
import process from "node:process";

import { build } from "vite";


function optionValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}


const explicitOutDir = optionValue("--outDir");
const evidenceDir = String(process.env.QUALITY_EVIDENCE_DIR || "").trim();
const outDir = explicitOutDir
  ? path.resolve(explicitOutDir)
  : evidenceDir
    ? path.join(path.resolve(evidenceDir), "artifacts", "frontend-dist")
    : "dist";

// A normal developer/release build still produces frontend/dist.  A Quality
// Loop Gate receives QUALITY_EVIDENCE_DIR from the controller and writes the
// exact same production bundle under immutable run evidence, keeping the
// governed workspace read-only during verification.
await build({
  build: {
    outDir,
    emptyOutDir: process.argv.includes("--emptyOutDir") || Boolean(evidenceDir),
  },
});
