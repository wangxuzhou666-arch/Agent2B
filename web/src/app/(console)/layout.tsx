import type { ReactNode } from "react";

import { ConsoleFrame } from "@/components/shell/console-frame";

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return <ConsoleFrame>{children}</ConsoleFrame>;
}
