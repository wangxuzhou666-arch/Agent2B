import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Views that chain two fetches (spec, then records) can take over a second on a busy machine;
// the 1 s default made `findBy*` flaky under load without testing anything extra.
configure({ asyncUtilTimeout: 4000 });

afterEach(() => {
  if (typeof window === "undefined") return; // node-environment suites
  cleanup();
  window.sessionStorage.clear();
});

// jsdom has no layout engine: give components the browser APIs they probe.
if (typeof window !== "undefined") {
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    });
  }
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  if (!("ResizeObserver" in window)) {
    class ResizeObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    Object.defineProperty(window, "ResizeObserver", { writable: true, value: ResizeObserverStub });
  }
}
