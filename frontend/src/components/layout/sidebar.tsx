"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, FileText, LayoutDashboard, TrendingUp, TriangleAlert, Waypoints, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  hint: string;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard, hint: "Periods, live runs, learning curve" },
  { href: "/exceptions", label: "Exceptions", icon: TriangleAlert, hint: "Queue, hypotheses, teach the agents" },
  { href: "/memory", label: "Memory", icon: Waypoints, hint: "Financial Memory Graph & provenance" },
  { href: "/trace", label: "Trace", icon: Activity, hint: "Agent timeline, audit challenges" },
  { href: "/forecast", label: "Forecast", icon: TrendingUp, hint: "13-week cash forecast" },
  { href: "/reports", label: "Reports", icon: FileText, hint: "Close reports & checklist" },
];

export function isActivePath(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function Wordmark({ collapsed }: { collapsed?: boolean }) {
  return (
    <Link href="/" className="flex items-center gap-2.5 rounded-md px-2 py-1.5 outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="Office of the CFO — home">
      <span className="relative inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-emerald-400/40 bg-emerald-500/15 text-emerald-300">
        <svg viewBox="0 0 16 16" className="size-4" aria-hidden>
          <path d="M2 11.5 6 7l3 3 5-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="14" cy="4" r="1.6" fill="currentColor" />
        </svg>
      </span>
      {collapsed ? null : (
        <span className="min-w-0 leading-tight">
          <span className="block truncate text-[13px] font-semibold tracking-tight">Office of the CFO</span>
          <span className="block truncate font-mono text-[10px] tracking-[0.16em] text-muted uppercase">AI finance agents</span>
        </span>
      )}
    </Link>
  );
}

/** Left navigation rail. Full width on lg+, icon rail on sm–lg, hidden below sm (see MobileNav). */
export function Sidebar() {
  const pathname = usePathname() ?? "/";
  return (
    <aside
      data-slot="sidebar"
      className="sticky top-0 hidden h-dvh w-14 shrink-0 flex-col border-r border-border bg-surface/60 backdrop-blur sm:flex lg:w-60"
    >
      <div className="flex h-14 items-center border-b border-border px-2 lg:px-3">
        <div className="hidden lg:block">
          <Wordmark />
        </div>
        <div className="lg:hidden">
          <Wordmark collapsed />
        </div>
      </div>

      <nav aria-label="Primary" className="flex flex-1 flex-col gap-1 p-2 lg:p-3">
        {NAV_ITEMS.map((item) => {
          const active = isActivePath(pathname, item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              title={item.hint}
              className={cn(
                "group relative flex h-9 items-center gap-3 rounded-md px-2.5 text-sm font-medium transition-colors outline-none",
                "focus-visible:ring-2 focus-visible:ring-ring",
                active ? "bg-surface-3 text-foreground" : "text-muted hover:bg-surface-2 hover:text-foreground",
              )}
            >
              <span
                className={cn(
                  "absolute inset-y-1.5 -left-2 w-0.5 rounded-full bg-emerald-400 transition-opacity lg:-left-3",
                  active ? "opacity-100" : "opacity-0",
                )}
                aria-hidden
              />
              <Icon className={cn("size-4 shrink-0", active ? "text-emerald-300" : "text-muted group-hover:text-foreground")} aria-hidden />
              <span className="hidden truncate lg:inline">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="hidden border-t border-border p-3 lg:block">
        <p className="font-mono text-[10px] leading-relaxed tracking-wide text-subtle">
          Human corrections in January
          <br />
          change behaviour in February.
        </p>
      </div>
    </aside>
  );
}

/** Bottom tab bar for phones (< sm). */
export function MobileNav() {
  const pathname = usePathname() ?? "/";
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 flex h-14 items-stretch border-t border-border bg-surface/90 backdrop-blur sm:hidden"
    >
      {NAV_ITEMS.map((item) => {
        const active = isActivePath(pathname, item.href);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex flex-1 flex-col items-center justify-center gap-0.5 text-[10px] font-medium",
              active ? "text-emerald-300" : "text-muted",
            )}
          >
            <Icon className="size-4" aria-hidden />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
