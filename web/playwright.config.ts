import { defineConfig } from "@playwright/test";

import { suiteConfig } from "./e2e/suite-config";

/**
 * The fixed flows: every spec in e2e/ except e2e/explore/. The stack, its settings and the
 * escape hatches (E2E_API_URL, E2E_WEB_URL, E2E_API_ROOT) are described in e2e/suite-config.ts.
 */
export default defineConfig(suiteConfig("fixed"));
