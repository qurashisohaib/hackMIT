import assert from "node:assert/strict";
import test from "node:test";
import { auditThreads, evidenceLinks, mergeEvents } from "./trace-data.ts";

const event = (id, seq, kind = "agent.step", data = {}) => ({
  id,
  seq,
  kind,
  data,
  run_id: "run-1",
  ts: `2026-01-31T12:00:${String(seq).padStart(2, "0")}Z`,
  agent: "audit",
  title: id,
  detail: "",
});

test("persisted and live events merge in sequence order without replay duplicates", () => {
  const stored = [event("second", 2), event("first", 1)];
  const live = [{ ...event("second", 2), detail: "complete evidence" }, event("third", 3)];
  const merged = mergeEvents(stored, live);
  assert.deepEqual(merged.map((item) => item.id), ["first", "second", "third"]);
  assert.equal(merged[1].detail, "complete evidence");
  assert.deepEqual(stored.map((item) => item.id), ["second", "first"]);
});

test("merging a bounded live buffer retains the older persisted history", () => {
  const persisted = Array.from({ length: 2100 }, (_, index) => event(`event-${index}`, index));
  const live = [...persisted.slice(-2000), event("latest", 2100)];
  const merged = mergeEvents(persisted, live);
  assert.equal(merged.length, 2101);
  assert.equal(merged[0].id, "event-0");
  assert.equal(merged.at(-1).id, "latest");
});

test("nested hypotheses and tool results preserve deduplicated provenance links", () => {
  const links = evidenceLinks({
    exception_id: "EXC/a?b&c",
    result: {
      hypotheses: [{ id: "HYP-1", rule_id: "RULE-1", evidence: [{ data: { node_id: "OBS-1" } }] }],
      evidence_ids: ["OBS-1", "DEC-1"],
      decision: { id: "DEC-1" },
    },
  });
  assert.deepEqual(links.map((link) => link.href), [
    "/exceptions?id=EXC%2Fa%3Fb%26c",
    "/memory?focus=HYP-1",
    "/memory?focus=RULE-1",
    "/memory?focus=OBS-1",
    "/memory?focus=DEC-1",
  ]);
});

test("ordinary payload ids and absent references do not invent memory links", () => {
  assert.deepEqual(evidenceLinks({
    id: "EV-1",
    invoice: { id: "INV-1" },
    title: "RULE-1",
    rule_id: null,
    exception_id: "",
  }), []);
});

test("interleaved audit conversations stay correlated to their decision", () => {
  const challengeA = event("challenge-a", 1, "audit.challenge", { decision_id: "DEC-a" });
  const challengeB = event("challenge-b", 2, "audit.challenge", { decision_id: "DEC-b" });
  const responseA = event("response-a", 3, "audit.response", { decision: { id: "DEC-a" } });
  const responseB = event("response-b", 4, "audit.response", { decision_id: "DEC-b" });
  const threads = auditThreads([challengeA, challengeB, event("tool", 3, "tool.result"), responseA, responseB]);
  assert.deepEqual(threads, [
    { id: "DEC-a", events: [challengeA, responseA] },
    { id: "DEC-b", events: [challengeB, responseB] },
  ]);
});

test("unreferenced sequential challenges start separate replay conversations", () => {
  const events = [
    event("challenge-1", 1, "audit.challenge"),
    event("response-1", 2, "audit.response"),
    event("verdict-1", 3, "audit.verdict"),
    event("challenge-2", 4, "audit.challenge"),
    event("response-2", 5, "audit.response"),
  ];
  assert.deepEqual(auditThreads(events).map((thread) => thread.events.map((item) => item.id)), [
    ["challenge-1", "response-1", "verdict-1"],
    ["challenge-2", "response-2"],
  ]);
});
