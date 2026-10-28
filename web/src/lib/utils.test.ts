import { describe, expect, it } from "vitest";

import { cn } from "./utils";

describe("cn", () => {
  it("keeps a named type size next to a text colour", () => {
    expect(cn("text-caption text-muted-foreground", "text-faint")).toBe("text-caption text-faint");
    expect(cn("text-nav", "text-foreground/80")).toBe("text-nav text-foreground/80");
  });

  it("still resolves real conflicts", () => {
    expect(cn("text-sm", "text-caption")).toBe("text-caption");
    expect(cn("px-2", "px-4")).toBe("px-4");
  });
});
