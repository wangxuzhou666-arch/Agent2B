import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "test/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
    // A whole-flow test (upload → preview → confirm → done) runs in ~1.5 s alone but crossed the
    // 5 s default on a loaded machine, failing without testing anything extra (cf. test/setup.ts).
    testTimeout: 15_000,
  },
});
