"use client";

import { useRef, useState, type FormEvent } from "react";
import { Check, GraduationCap, Link2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, inputClassName } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, errorMessage, resolveException } from "@/lib/api";
import type { ExceptionDetail, ResolveAction, ResolveRequest, ResolveResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACTIONS = [
  { id: "teach", label: "Teach", icon: GraduationCap },
  { id: "approve_hypothesis", label: "Approve", icon: Check },
  { id: "manual_match", label: "Manual match", icon: Link2 },
  { id: "dismiss", label: "Dismiss", icon: X },
] as const;

function resolutionError(error: unknown): string {
  if (error instanceof ApiError && error.body && typeof error.body === "object" && "detail" in error.body) {
    const detail: unknown = error.body.detail;
    if (Array.isArray(detail)) {
      const messages = detail.flatMap((entry: unknown) =>
        entry && typeof entry === "object" && "msg" in entry && typeof entry.msg === "string" ? [entry.msg] : [],
      );
      if (messages.length) return messages.join(". ");
    }
  }
  return errorMessage(error, "The resolution could not be saved.");
}

export function ResolveForm({ detail, onResolved }: { detail: ExceptionDetail; onResolved: (result: ResolveResponse) => void }) {
  const [action, setAction] = useState<ResolveAction>("teach");
  const [explanation, setExplanation] = useState("");
  const [hypothesisId, setHypothesisId] = useState(detail.hypotheses.find((hypothesis) => hypothesis.passed)?.id ?? detail.hypotheses[0]?.id ?? "");
  const [matched, setMatched] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submitting = useRef(false);
  const explanationRef = useRef<HTMLTextAreaElement>(null);
  const candidateIds = [...new Set([
    ...detail.hypotheses.flatMap((hypothesis) => hypothesis.candidate_ids),
    ...detail.related.flatMap((row) => typeof row.id === "string" ? [row.id] : []),
  ])];
  const matchedIds = [...new Set(matched.split(/[\s,;]+/).filter(Boolean))];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || saved) return;
    setError(null);
    if (!explanation.trim()) { setError("Add an explanation so the audit trail captures your judgment."); explanationRef.current?.focus(); return; }
    if (action === "approve_hypothesis" && !detail.hypotheses.some((hypothesis) => hypothesis.id === hypothesisId)) { setError("Choose an available hypothesis to approve."); return; }
    if (action === "manual_match" && !matchedIds.length) { setError("Enter at least one invoice or transaction reference."); return; }
    const request: ResolveRequest = { action, explanation: explanation.trim(), by: "human" };
    if (action === "approve_hypothesis") request.hypothesis_id = hypothesisId;
    if (action === "manual_match") request.matched_ids = matchedIds;
    submitting.current = true;
    setBusy(true);
    try {
      const result = await resolveException(detail.exception.id, request);
      setSaved(true);
      onResolved(result);
    } catch (err) {
      setError(resolutionError(err));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.035] p-4">
      <div><h3 className="text-sm font-semibold">Your judgment becomes memory</h3><p className="mt-1 text-xs leading-relaxed text-muted">Every review keeps its explanation and source evidence.</p></div>
      <fieldset disabled={busy || saved} className="space-y-4 disabled:opacity-60">
        <legend className="sr-only">Resolution action</legend>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {ACTIONS.map(({ id, label, icon: Icon }) => <button type="button" key={id} aria-pressed={action === id}
            className={cn("flex items-center justify-center gap-1.5 rounded-lg border px-2 py-2 text-xs transition-colors", action === id ? "border-emerald-400/40 bg-emerald-500/15 text-emerald-200" : "border-border-strong text-muted hover:bg-surface-2")}
            onClick={() => { setAction(id); setError(null); }}><Icon className="size-3.5" aria-hidden />{label}</button>)}
        </div>

        {action === "teach" && <p className="text-xs leading-relaxed text-muted">Describe the business rule in plain language, including the rate or amount and direction. The backend parses it and checks sibling exceptions. Example: “Stripe deducts a 3% processing fee.”</p>}
        {action === "approve_hypothesis" && <label className="block space-y-2 text-xs text-muted"><span>Hypothesis to approve</span>
          <select className={inputClassName} value={hypothesisId} onChange={(event) => setHypothesisId(event.target.value)} required aria-describedby="approval-help">
            <option value="">Select a hypothesis</option>{detail.hypotheses.map((hypothesis) => <option key={hypothesis.id} value={hypothesis.id}>{Math.round(hypothesis.confidence * 100)}% · {hypothesis.description}</option>)}
          </select><span id="approval-help" className="block">Review the evidence above before approving. Backend validation still applies.</span>
        </label>}
        {action === "manual_match" && <div className="space-y-2">
          <label className="block space-y-2 text-xs text-muted"><span>Matched invoice / transaction IDs</span><Input value={matched} onChange={(event) => setMatched(event.target.value)} placeholder="Paste exact IDs, separated by commas" required aria-describedby="match-help" /></label>
          <p id="match-help" className="text-xs text-muted">Use exact references from the evidence. Select a candidate to add or remove it.</p>
          <div className="flex flex-wrap gap-1.5">{candidateIds.map((id) => <button type="button" key={id} aria-pressed={matchedIds.includes(id)}
            onClick={() => setMatched(matchedIds.includes(id) ? matchedIds.filter((value) => value !== id).join(", ") : [...matchedIds, id].join(", "))}
            className={cn("break-all rounded border px-2 py-1 font-mono text-[10px]", matchedIds.includes(id) ? "border-emerald-400/40 bg-emerald-500/10 text-emerald-200" : "border-border-strong text-muted")}>{id}</button>)}</div>
        </div>}
        {action === "dismiss" && <p className="text-xs text-amber-200">Dismissal closes this review without teaching a matching rule. Explain why no further action is needed.</p>}
        <label className="block space-y-2 text-xs text-muted"><span>{action === "teach" ? "Teach a rule in your own words" : "Reason for this decision"}</span>
          <Textarea ref={explanationRef} value={explanation} onChange={(event) => setExplanation(event.target.value)} rows={4} required maxLength={4000}
            placeholder={action === "teach" ? "Describe the fee, discount, tolerance, or approval policy…" : "Explain the evidence behind your decision…"}
            aria-invalid={!!error} aria-describedby={error ? "resolution-error" : undefined} />
        </label>
      </fieldset>
      {error && <p id="resolution-error" role="alert" className="rounded-lg border border-rose-400/30 bg-rose-500/10 p-3 text-sm text-rose-200">{error}</p>}
      {saved ? <p role="status" className="text-sm text-emerald-200">Review saved. Refreshing the evidence…</p> :
        <Button type="submit" loading={busy} disabled={action === "approve_hypothesis" && !detail.hypotheses.length} variant={action === "dismiss" ? "destructive" : "default"} className="w-full">
          {busy ? "Saving and checking related exceptions…" : action === "teach" ? "Teach & apply to related exceptions" : action === "approve_hypothesis" ? "Approve selected hypothesis" : action === "manual_match" ? "Confirm manual match" : "Dismiss exception"}
        </Button>}
    </form>
  );
}
