import {
  Briefcase,
  Cpu,
  FileText,
  ListChecks,
  Receipt,
  Scale,
  ShieldCheck,
  TrendingUp,
  User,
  type LucideIcon,
} from "lucide-react";
import type { AgentId } from "./types";

/**
 * Visual identity for every agent. Colours are fixed per agent (never by rank)
 * so a reader learns "recon is emerald" once and it holds across every page.
 *
 * Tailwind class strings are written out in full so the v4 compiler can see them.
 */
export interface AgentMeta {
  id: AgentId;
  /** Display name, e.g. "Reconciliation". */
  name: string;
  /** Short label for chips, e.g. "Recon". */
  short: string;
  /** One-line role description for tooltips. */
  role: string;
  /** Tailwind colour family name (violet, sky, …). */
  color: string;
  /** Solid hex, for SVG/cytoscape/charts (400-step, readable on the dark surface). */
  hex: string;
  /** Muted hex (500-step) for fills at lower alpha. */
  hexDeep: string;
  icon: LucideIcon;
  /** Tailwind classes for a chip: subtle background + border + tinted text. */
  chip: string;
  /** Tailwind text colour class. */
  text: string;
  /** Tailwind background class for a solid dot. */
  dot: string;
  /** Tailwind border colour class. */
  border: string;
  /** Tailwind background class at low alpha (lane backgrounds). */
  wash: string;
}

export const AGENTS: Record<AgentId, AgentMeta> = {
  cfo: {
    id: "cfo",
    name: "CFO",
    short: "CFO",
    role: "Orchestrates the period close and computes metrics",
    color: "violet",
    hex: "#a78bfa",
    hexDeep: "#8b5cf6",
    icon: Briefcase,
    chip: "bg-violet-500/12 border-violet-400/30 text-violet-200",
    text: "text-violet-300",
    dot: "bg-violet-400",
    border: "border-violet-400/40",
    wash: "bg-violet-500/8",
  },
  ap_ar: {
    id: "ap_ar",
    name: "AP / AR",
    short: "AP/AR",
    role: "3-way match, duplicates, policy checks, AR aging",
    color: "sky",
    hex: "#38bdf8",
    hexDeep: "#0ea5e9",
    icon: Receipt,
    chip: "bg-sky-500/12 border-sky-400/30 text-sky-200",
    text: "text-sky-300",
    dot: "bg-sky-400",
    border: "border-sky-400/40",
    wash: "bg-sky-500/8",
  },
  recon: {
    id: "recon",
    name: "Reconciliation",
    short: "Recon",
    role: "Hypothesis → evidence → confidence loop over bank transactions",
    color: "emerald",
    hex: "#34d399",
    hexDeep: "#10b981",
    icon: Scale,
    chip: "bg-emerald-500/12 border-emerald-400/30 text-emerald-200",
    text: "text-emerald-300",
    dot: "bg-emerald-400",
    border: "border-emerald-400/40",
    wash: "bg-emerald-500/8",
  },
  audit: {
    id: "audit",
    name: "Audit",
    short: "Audit",
    role: "Re-performs medium-confidence decisions and challenges the recon agent",
    color: "amber",
    hex: "#fbbf24",
    hexDeep: "#f59e0b",
    icon: ShieldCheck,
    chip: "bg-amber-500/12 border-amber-400/30 text-amber-200",
    text: "text-amber-300",
    dot: "bg-amber-400",
    border: "border-amber-400/40",
    wash: "bg-amber-500/8",
  },
  close: {
    id: "close",
    name: "Close",
    short: "Close",
    role: "Close checklist, accruals, period status",
    color: "indigo",
    hex: "#818cf8",
    hexDeep: "#6366f1",
    icon: ListChecks,
    chip: "bg-indigo-500/12 border-indigo-400/30 text-indigo-200",
    text: "text-indigo-300",
    dot: "bg-indigo-400",
    border: "border-indigo-400/40",
    wash: "bg-indigo-500/8",
  },
  forecast: {
    id: "forecast",
    name: "Forecast",
    short: "Forecast",
    role: "13-week cash forecast from open AR/AP and memory observations",
    color: "cyan",
    hex: "#22d3ee",
    hexDeep: "#06b6d4",
    icon: TrendingUp,
    chip: "bg-cyan-500/12 border-cyan-400/30 text-cyan-200",
    text: "text-cyan-300",
    dot: "bg-cyan-400",
    border: "border-cyan-400/40",
    wash: "bg-cyan-500/8",
  },
  report: {
    id: "report",
    name: "Report",
    short: "Report",
    role: "Writes the close report narrative with evidence links",
    color: "fuchsia",
    hex: "#e879f9",
    hexDeep: "#d946ef",
    icon: FileText,
    chip: "bg-fuchsia-500/12 border-fuchsia-400/30 text-fuchsia-200",
    text: "text-fuchsia-300",
    dot: "bg-fuchsia-400",
    border: "border-fuchsia-400/40",
    wash: "bg-fuchsia-500/8",
  },
  human: {
    id: "human",
    name: "Human",
    short: "Human",
    role: "Controller review and teaching",
    color: "rose",
    hex: "#fb7185",
    hexDeep: "#f43f5e",
    icon: User,
    chip: "bg-rose-500/12 border-rose-400/30 text-rose-200",
    text: "text-rose-300",
    dot: "bg-rose-400",
    border: "border-rose-400/40",
    wash: "bg-rose-500/8",
  },
  system: {
    id: "system",
    name: "System",
    short: "System",
    role: "Runtime, brain fallback and infrastructure events",
    color: "zinc",
    hex: "#a1a1aa",
    hexDeep: "#71717a",
    icon: Cpu,
    chip: "bg-zinc-500/12 border-zinc-400/30 text-zinc-300",
    text: "text-zinc-400",
    dot: "bg-zinc-400",
    border: "border-zinc-400/40",
    wash: "bg-zinc-500/8",
  },
};

/** Canonical display order (matches the pipeline). */
export const AGENT_ORDER: AgentId[] = [
  "cfo",
  "ap_ar",
  "recon",
  "audit",
  "close",
  "forecast",
  "report",
  "human",
  "system",
];

/** Resolve any string (including unknown values from the backend) to an AgentMeta. */
export function getAgent(id: string | null | undefined): AgentMeta {
  if (!id) return AGENTS.system;
  const key: string = id.toLowerCase().replace(/[\s/-]+/g, "_");
  if (isAgentId(key)) return AGENTS[key];
  if (key === "apar" || key === "ap" || key === "ar") return AGENTS.ap_ar;
  if (key === "reconciliation") return AGENTS.recon;
  if (key === "controller" || key.startsWith("human")) return AGENTS.human;
  return AGENTS.system;
}

export function isAgentId(id: string): id is AgentId {
  return id in AGENTS;
}

/** Pipeline step indicator for a period close (ARCHITECTURE §4). */
export interface PipelineStep {
  key: string;
  label: string;
  agent: AgentId;
}

export const PIPELINE_STEPS: PipelineStep[] = [
  { key: "plan", label: "CFO plan", agent: "cfo" },
  { key: "ap_ar", label: "AP / AR", agent: "ap_ar" },
  { key: "recon", label: "Recon", agent: "recon" },
  { key: "audit", label: "Audit", agent: "audit" },
  { key: "close", label: "Close", agent: "close" },
  { key: "forecast", label: "Forecast", agent: "forecast" },
  { key: "report", label: "Report", agent: "report" },
];
