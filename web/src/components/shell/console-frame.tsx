"use client";

import type { ReactNode } from "react";

import { ConversationProvider } from "@/components/hub/conversation-provider";
import { ApprovalsProvider } from "@/components/providers/approvals-provider";

import { AppShell } from "./app-shell";
import { AuthGuard } from "./auth-guard";

/** Everything behind sign-in. Providers live here so they survive navigation between windows. */
export function ConsoleFrame({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <ApprovalsProvider>
        <ConversationProvider>
          <AppShell>{children}</AppShell>
        </ConversationProvider>
      </ApprovalsProvider>
    </AuthGuard>
  );
}
