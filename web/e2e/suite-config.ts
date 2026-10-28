import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { devices, type PlaywrightTestConfig } from "@playwright/test";

/**
 * End-to-end suites: the real API (demo mode, no model key) and a production build of the console.
 * Two suites run on that stack, because the API's settings differ and the console is built
 * against exactly one API (`NEXT_PUBLIC_API_BASE` is inlined at build time):
 *
 * - `fixed` (playwright.config.ts, `pnpm e2e:fixed`): the fixed flows, with open-ended questions
 *   off, so a question no report covers falls back to chat. Every spec in e2e/ except e2e/explore/.
 * - `explore` (playwright.explore.config.ts, `pnpm e2e:explore`): the settings docker-compose.yml
 *   gives the API, `TWOTRACK_EXPLORER=pi` and `TWOTRACK_ALLOW_OPEN_TASK=1`, still on the mock
 *   model. A question no report covers runs in the real Pi worker, so harness/pi must be
 *   installed (`npm ci --ignore-scripts` there); the API refuses to start without it, which fails
 *   the run instead of skipping the specs. The specs are in e2e/explore/.
 *
 * `pnpm e2e` runs both, one after the other, each on fresh state; the second runs even when the
 * first fails, and the command fails if either did. Each suite keeps what a failed spec leaves
 * (trace, screenshot) in its own folder under web/test-results/: `fixed` and `explore`.
 *
 * By default a suite starts both servers:
 * - API: `uv run twotrack serve` on 127.0.0.1:$E2E_API_PORT, a new temp data dir (seeded on
 *   start), the drop-in plugin directory `examples/plugins` (cold chain, supplier scorecard), CORS
 *   for the web origin, and the mock provider (no model key).
 * - Web: `next build` against that API, then `next start` on 127.0.0.1:$E2E_WEB_PORT, so no dev
 *   overlay is in play.
 * The ports default to 18787 and 13100 for `fixed`, 18788 and 13101 for `explore`.
 *
 * Escape hatches (each applies to the suite being run):
 * - `E2E_API_URL`: use an API that is already running (start it with demo mode, the plugin path,
 *   no model, the suite's settings above and `TWOTRACK_CORS_ORIGINS` naming the web origin);
 *   nothing is started for it.
 * - `E2E_WEB_URL`: use a console that is already running, built against the API above.
 * - `E2E_API_ROOT`: start the API from another checkout (e.g. a clean export of HEAD while the
 *   working tree's backend is mid-edit). Defaults to this repository. For `explore`, point
 *   `TWOTRACK_PI_HARNESS_DIR` at an installed harness/pi when that checkout has none.
 */

export type Suite = "fixed" | "explore";

/**
 * Name prefix of the temp data dir a run gives its API. global-teardown.ts removes a directory
 * only when it sits directly in the OS temp dir and carries this prefix.
 */
export const DATA_DIR_PREFIX = "twotrack-e2e-";

interface SuiteSettings {
  testDir: string;
  /** Specs of other suites that live under `testDir`. */
  testIgnore: string[];
  outputDir: string;
  apiPort: number;
  webPort: number;
  /** One spec may start a Pi worker (a Node process per question) and wait on it. */
  timeout: number;
  /** What the API reads, on top of the settings every suite shares. */
  api: Record<string, string>;
}

const SUITES: Record<Suite, SuiteSettings> = {
  fixed: {
    testDir: "e2e",
    testIgnore: ["**/explore/**"],
    outputDir: path.join("test-results", "fixed"),
    apiPort: 18787,
    webPort: 13100,
    timeout: 60_000,
    api: {
      // no model, no open-ended agent loop: a question no report covers is answered as chat
      TWOTRACK_ALLOW_OPEN_TASK: "0",
      // moot while open tasks are off; pinned so a shell that exports `pi` cannot make this suite need Node
      TWOTRACK_EXPLORER: "builtin",
    },
  },
  explore: {
    testDir: path.join("e2e", "explore"),
    testIgnore: [],
    outputDir: path.join("test-results", "explore"),
    apiPort: 18788,
    webPort: 13101,
    timeout: 120_000,
    api: {
      // what docker-compose.yml fixes on the api service: open tasks on, run by Pi's agent loop
      // (the mock model's script supplies the plan)
      TWOTRACK_ALLOW_OPEN_TASK: "1",
      TWOTRACK_EXPLORER: "pi",
      // empty reads as unset: the API's own budgets and its full tool set, whatever the shell exports
      TWOTRACK_EXPLORE_MAX_TURNS: "",
      TWOTRACK_EXPLORE_MAX_CALLS: "",
      TWOTRACK_EXPLORE_TIMEOUT_SECONDS: "",
      TWOTRACK_EXPLORE_TOOL_TIMEOUT_SECONDS: "",
      TWOTRACK_EXPLORE_TOOLS: "",
      TWOTRACK_EXPLORE_STARTUP_TIMEOUT_SECONDS: "",
    },
  },
};

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(webDir, "..");

const port = (raw: string | undefined, fallback: number) => {
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : fallback;
};
const external = (raw: string | undefined) => (raw?.trim() ? new URL(raw.trim()).origin : null);

export function suiteConfig(suite: Suite): PlaywrightTestConfig {
  const settings = SUITES[suite];
  const apiPort = port(process.env.E2E_API_PORT, settings.apiPort);
  const webPort = port(process.env.E2E_WEB_PORT, settings.webPort);
  const externalApi = external(process.env.E2E_API_URL);
  const externalWeb = external(process.env.E2E_WEB_URL);
  const apiURL = externalApi ?? `http://127.0.0.1:${apiPort}`;
  const webURL = externalWeb ?? `http://127.0.0.1:${webPort}`;
  const apiRoot = path.resolve(process.env.E2E_API_ROOT?.trim() || repoRoot);

  // Test workers load the config too; only the runner starts servers, so only it names a data dir.
  // The API makes the directory itself (mode 0700) when it starts, so a load that never runs the
  // suite (`--list`, an editor) leaves nothing behind. E2E_DATA_DIR hands the name to
  // global-teardown.ts, which removes it: it is set for exactly this dir and cleared when there
  // is none, so a value the shell exports is never deleted.
  const isRunner = process.env.TEST_WORKER_INDEX === undefined;
  const dataDir =
    !externalApi && isRunner ? path.join(tmpdir(), `${DATA_DIR_PREFIX}${randomUUID()}`) : "";
  if (isRunner) {
    if (dataDir) process.env.E2E_DATA_DIR = dataDir;
    else delete process.env.E2E_DATA_DIR;
  }

  const servers: NonNullable<PlaywrightTestConfig["webServer"]> = [];
  if (!externalApi) {
    servers.push({
      name: "api",
      command: `uv run twotrack serve --host 127.0.0.1 --port ${apiPort}`,
      cwd: apiRoot,
      url: `${apiURL}/api/health`,
      env: {
        TWOTRACK_DEMO_MODE: "1",
        TWOTRACK_DATA_DIR: dataDir,
        TWOTRACK_PLUGIN_PATHS: path.join(apiRoot, "examples", "plugins"),
        TWOTRACK_CORS_ORIGINS: webURL,
        // zero API key, whatever the shell exports
        TWOTRACK_PROVIDER: "mock",
        ...settings.api,
      },
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
      stdout: "ignore",
      stderr: "pipe",
      timeout: 180_000,
    });
  }
  if (!externalWeb) {
    servers.push({
      name: "web",
      command: `pnpm build && pnpm start --hostname 127.0.0.1 --port ${webPort}`,
      cwd: webDir,
      url: `${webURL}/login`,
      env: { NEXT_PUBLIC_API_BASE: apiURL, NEXT_TELEMETRY_DISABLED: "1" },
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
      stdout: "ignore",
      stderr: "pipe",
      timeout: 300_000,
    });
  }

  return {
    testDir: path.join(webDir, settings.testDir),
    testIgnore: settings.testIgnore,
    globalTeardown: path.join(webDir, "e2e", "global-teardown.ts"),
    // one shared API: flows run one after another, so each sees only its own writes land
    fullyParallel: false,
    workers: 1,
    forbidOnly: Boolean(process.env.CI),
    retries: 0,
    timeout: settings.timeout,
    expect: { timeout: 15_000 },
    reporter: process.env.CI ? [["list"], ["github"]] : "list",
    outputDir: path.join(webDir, settings.outputDir),
    use: {
      baseURL: webURL,
      locale: "en-US",
      trace: "retain-on-failure",
      screenshot: "only-on-failure",
    },
    projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
    webServer: servers,
  };
}
