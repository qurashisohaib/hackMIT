"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

interface TabsContextValue {
  value: string;
  setValue: (v: string) => void;
  baseId: string;
}

const TabsContext = React.createContext<TabsContextValue | null>(null);

function useTabs(component: string): TabsContextValue {
  const ctx = React.useContext(TabsContext);
  if (!ctx) throw new Error(`<${component}> must be used inside <Tabs>`);
  return ctx;
}

export interface TabsProps extends Omit<React.ComponentProps<"div">, "defaultValue" | "onChange"> {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
}

/**
 * Accessible tabs (roving tabindex, arrow-key navigation). Controlled via
 * `value`/`onValueChange` or uncontrolled via `defaultValue`.
 */
export function Tabs({ value, defaultValue, onValueChange, className, children, ...props }: TabsProps) {
  const [internal, setInternal] = React.useState(defaultValue ?? "");
  const isControlled = value !== undefined;
  const current = isControlled ? value : internal;
  const baseId = React.useId();
  const setValue = React.useCallback(
    (v: string) => {
      if (!isControlled) setInternal(v);
      onValueChange?.(v);
    },
    [isControlled, onValueChange],
  );
  const ctx = React.useMemo(() => ({ value: current, setValue, baseId }), [current, setValue, baseId]);
  return (
    <TabsContext.Provider value={ctx}>
      <div data-slot="tabs" className={cn("flex flex-col gap-3", className)} {...props}>
        {children}
      </div>
    </TabsContext.Provider>
  );
}

export function TabsList({ className, onKeyDown, ...props }: React.ComponentProps<"div">) {
  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    onKeyDown?.(e);
    if (e.defaultPrevented) return;
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(e.key)) return;
    const tabs = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not([disabled])'));
    if (tabs.length === 0) return;
    const idx = tabs.findIndex((t) => t === document.activeElement);
    let next = idx;
    if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
    if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = tabs.length - 1;
    e.preventDefault();
    tabs[next]?.focus();
    tabs[next]?.click();
  };
  return (
    <div
      role="tablist"
      data-slot="tabs-list"
      className={cn("inline-flex h-9 w-fit items-center gap-1 rounded-lg border border-border bg-surface-2 p-1 text-muted", className)}
      onKeyDown={handleKeyDown}
      {...props}
    />
  );
}

export interface TabsTriggerProps extends React.ComponentProps<"button"> {
  value: string;
}

export function TabsTrigger({ value, className, onClick, ...props }: TabsTriggerProps) {
  const { value: current, setValue, baseId } = useTabs("TabsTrigger");
  const active = current === value;
  return (
    <button
      type="button"
      role="tab"
      id={`${baseId}-tab-${value}`}
      aria-selected={active}
      aria-controls={`${baseId}-panel-${value}`}
      tabIndex={active ? 0 : -1}
      data-state={active ? "active" : "inactive"}
      data-slot="tabs-trigger"
      className={cn(
        "inline-flex h-7 items-center justify-center gap-1.5 rounded-md px-3 text-xs font-medium whitespace-nowrap transition-colors",
        "hover:text-foreground disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-3.5",
        active ? "bg-surface-3 text-foreground shadow-[0_0_0_1px_var(--color-border-strong)]" : "text-muted",
        className,
      )}
      onClick={(e) => {
        onClick?.(e);
        if (!e.defaultPrevented) setValue(value);
      }}
      {...props}
    />
  );
}

export interface TabsContentProps extends React.ComponentProps<"div"> {
  value: string;
  /** Keep the panel mounted while hidden. Default false. */
  forceMount?: boolean;
}

export function TabsContent({ value, forceMount = false, className, ...props }: TabsContentProps) {
  const { value: current, baseId } = useTabs("TabsContent");
  const active = current === value;
  if (!active && !forceMount) return null;
  return (
    <div
      role="tabpanel"
      id={`${baseId}-panel-${value}`}
      aria-labelledby={`${baseId}-tab-${value}`}
      hidden={!active}
      tabIndex={0}
      data-slot="tabs-content"
      className={cn("outline-none", className)}
      {...props}
    />
  );
}
