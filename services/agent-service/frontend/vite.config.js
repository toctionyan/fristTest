import { defineConfig, loadEnv } from "vite";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import react from "@vitejs/plugin-react";

// Vite does not automatically place `.env` values in `process.env` while this
// config file is evaluated.  loadEnv makes VITE_AGENT_DEV_TARGET work from the
// checked-in `.env.example`/local `.env` pair, not only from a shell export.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const junitPath = process.env.VITEST_JUNIT_PATH;
  const coverageDirectory = process.env.VITEST_COVERAGE_DIR || "./coverage";
  // The quality controller stores evidence outside the frontend directory.
  // Vitest creates its coverage directory, but the JUnit reporter expects its
  // parent directory to exist before it opens the report file.
  if (junitPath) {
    mkdirSync(dirname(junitPath), { recursive: true });
  }
  return {
    base: "/web/",
    plugins: [react()],
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.js",
      reporters: junitPath ? ["default", "junit"] : ["default"],
      outputFile: junitPath ? { junit: junitPath } : undefined,
      coverage: {
        provider: "v8",
        reportsDirectory: coverageDirectory,
        reporter: ["text", "json-summary", "lcov"],
        include: ["src/**/*.{js,jsx}"],
        exclude: ["src/test/**", "**/*.test.{js,jsx}"]
      }
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": {
          target: env.VITE_AGENT_DEV_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true
        }
      }
    }
  };
});
