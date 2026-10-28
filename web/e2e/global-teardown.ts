import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { DATA_DIR_PREFIX } from "./suite-config";

/** A directory directly in the OS temp dir with the config's own prefix, and nothing else. */
const isRunDataDir = (dir: string) =>
  path.dirname(path.resolve(dir)) === path.resolve(tmpdir()) &&
  path.basename(dir).startsWith(DATA_DIR_PREFIX);

/**
 * Removes the temp data dir suite-config.ts named for the API of this run. Only that: the
 * variable has to name a directory the config could have named, so a stray E2E_DATA_DIR can never
 * point this at anything else.
 */
export default function globalTeardown(): void {
  const dir = process.env.E2E_DATA_DIR;
  if (!dir) return;
  if (!isRunDataDir(dir)) {
    console.warn(`global-teardown: E2E_DATA_DIR is not a ${DATA_DIR_PREFIX}* dir in the temp dir; left alone`);
    return;
  }
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    // still held open (Windows): the OS temp cleaner takes it later
  }
}
