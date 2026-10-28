import { defineConfig } from "@playwright/test";

import { suiteConfig } from "./e2e/suite-config";

/**
 * The exploration case: the specs in e2e/explore/, on an API that plans open-ended questions in
 * the Pi worker (needs harness/pi installed). Run with `pnpm e2e:explore`; see e2e/suite-config.ts.
 */
export default defineConfig(suiteConfig("explore"));
