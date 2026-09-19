"use client";

import * as React from "react";
import { HealthProvider } from "@/components/layout/health-provider";
import { MobileNav, Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { OfflineBanner } from "@/components/layout/offline-banner";

/** Application chrome: health polling, sidebar, topbar, offline banner, content well. */
export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <HealthProvider>
      <div className="flex min-h-dvh">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar />
          <OfflineBanner />
          <main id="main" className="mx-auto w-full max-w-[1480px] flex-1 px-4 pt-6 pb-24 sm:px-6 sm:pb-10 lg:px-8">
            {children}
          </main>
        </div>
      </div>
      <MobileNav />
    </HealthProvider>
  );
}
