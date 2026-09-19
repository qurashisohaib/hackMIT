# HANDOFF — AI Office of the CFO (HackMIT 2026, Maximor track)

Written 2026-09-19 mid-build so another agent (or human) can pick this up cold.
**Read `ARCHITECTURE.md` first — it is the binding contract every module was written against.**
Then read this file top to bottom. Pitch + demo script are in ARCHITECTURE §0 and §9.

## 0. Where things are
- Repo: `/Users/sohaibqurashi/Documents/hackMIT/cfo-office` (local git; commit often).
- Backend: `backend/` Python 3.12 via **uv** → `cd backend && uv run …`. FastAPI on **port 8000**.
- Frontend: `frontend/` Next.js **16** (app router, `src/`, Tailwind v4). Dev server **port 3001** (3000 is taken by another process on this Mac). Read `frontend/AGENTS.md` + `node_modules/next/dist/docs/` before touching Next code — Next 16 differs from training data.
- No Docker / Postgres / Neo4j / API keys on this machine → SQLite + embedded graph, deterministic "HeuristicBrain". Optional `OPENAI_API_KEY` switches to OpenAI Agents SDK; optional `NEO4J_URI` switches graph backend. Demo must never depend on either.
- `make setup | seed | backend | frontend | demo | test`, `scripts/demo.sh` (one-command demo).

## 1. Status at handoff (check `git log` / file tree — later commits may have advanced this)
| Module (owner letter in ARCHITECTURE §1) | State |
|---|---|
| Core contract: `app/schemas.py`, `app/db/models.py`, `app/db/session.py`, `app/config.py`, `app/events.py` | ✅ done, FROZEN (don't change shapes; other modules import them) |
| A `app/data/generator.py`, `seed.py` | ✅ written — verify `uv run python -m app.data.seed --reset` prints per-period counts matching ARCHITECTURE §2 |
| B `app/memory/{graph,service,embeddings,neo4j_store,common}.py` | ✅ written — verify learn/supersede/find_precedents/provenance/reload |
| C1 `app/agents/hypotheses.py`, `tools.py` | ✅ written; `base.py`, `brain.py` were **in progress** (may be absent/partial — check) |
| C2 `app/agents/{cfo,ap_ar,recon,audit,close,forecast,report}.py` | ❌ not started |
| D `app/main.py`, `app/api/*` | ❌ not started |
| E1 frontend foundation (`src/lib/*`, `src/components/{ui,shared,layout,dashboard}/*`, `src/app/page.tsx`, `layout.tsx`) | ✅ written (not yet type-checked/build-gated) |
| E2 `/exceptions`, `/memory` pages | ❌ not started |
| E3 `/trace`, `/forecast`, `/reports` pages | ❌ not started |
| E4 frontend build gate (tsc, lint, `next build`, empty states offline) | ❌ not started |
| F `backend/tests/*`, root `README.md` | ❌ not started |
| Integration: live E2E in browser, demo rehearsal, polish | ❌ not started |

A background workflow (run id `wf_3a58f85c-d5c`, script at
`~/.claude/projects/-Users-sohaibqurashi-Documents-hackMIT-cfo-office/916e5ae2-5565-43ea-b249-2dae4187743a/workflows/scripts/build-cfo-office-wf_3a58f85c-d5c.js`)
was executing these phases; if it finished, most of the ❌ rows are actually done — **trust the file tree and `git status`, not this table**. That script contains the full, detailed prompt for every remaining module; reuse those prompts verbatim if re-spawning agents.

## 2. Remaining work, in order (each bullet = one agent task; specs in ARCHITECTURE.md)
1. **Finish C1** — `app/agents/base.py` (AgentContext, Tool/ToolRegistry, BaseAgent with emit/call_tool/handoff) and `app/agents/brain.py` (Brain protocol, HeuristicBrain incl. `parse_correction` regex for "Stripe deducts a 3% processing fee", "$25 wire fee", "2% early payment discount", "accept up to 1.5% FX variance", "2.5% surcharge"; OpenAIBrain on `openai-agents` with fallback). Check what `hypotheses.py`/`tools.py` already import from `base`/`brain` and satisfy those names exactly. Verify with the script described in ARCHITECTURE §4 (Stripe case → observed_pattern 0.42 → needs_human; add percentage_fee rule → rule_percentage_fee).
2. **C2 agents + pipeline** (§4): `cfo.run_period_close`, `reset_period_state`, `compute_metrics` vs `ground_truth` (only module allowed to read it), `resolve_exception` with **propagation** to sibling open exceptions, `make_context`; `ap_ar` (3-way match, duplicates, no-PO, price variance, days-to-pay observations), `recon` (hypothesis loop per bank txn + in-transit), `audit` (challenge → re-perform → verdict; confirm/refute rules; 20% sample of rule-based HIGH decisions), `close` (checklist + accruals), `forecast.build_forecast` (13-week, memory assumptions, error vs next period), `report.build_report`. Deliver `backend/scripts/smoke_pipeline.py`: seed → run Jan → teach 5 rules → run Feb → run Mar → teach CloudSpan 2.5% (drift → rule v2 SUPERSEDES v1) → print Jan/Feb/Mar table (accuracy, human_reviews, precedent_hits, avg_steps, avg_ms). Targets: Jan human_reviews ≥ 8 & precedent_hits 0; Feb precedent_hits ≥ 10, human_reviews ≤ 4, accuracy ≥ 0.9; Mar drift on both CloudSpan items.
3. **D API** (§7): `app/main.py` (`create_app`, lifespan: init_db, auto-seed if empty, `bus.bind_loop`, rehydrate runs from AgentRun nodes, `/api/health` → `{status, brain, llm_available, company, version}`), `app/api/runs.py` RunManager (one run at a time, background asyncio task, live counts, persist trace), routers for periods/runs(+SSE via sse-starlette)/exceptions(+resolve)/memory(graph, nodes, provenance, rules, stats)/metrics(latest per period + deltas + history)/forecast/reports/data/reset. Verify with curl end-to-end (start run, stream events, teach, metrics deltas, reset).
4. **E2 pages**: `/exceptions` (list + detail + Teach form + propagation celebration) and `/memory` (Cytoscape + fcose, dynamic client-only import, provenance highlight animation, `?focus=`). **E3 pages**: `/trace` (agent lanes + event log + audit-conversation panel), `/forecast` (recharts stacked bars + assumptions from memory), `/reports` (close report document, print). Load the `dataviz` skill before charts. Reuse `src/lib/{api,types,sse,utils,agents}.ts` and `src/components/{ui,shared}`.
5. **E4 build gate**: `npx tsc --noEmit`, `npm run lint`, `npm run build` green; add `not-found.tsx`, `error.tsx`; all `/memory?focus=` and `/exceptions?id=` deep links consistent; every route returns 200 with the backend offline.
6. **F tests + README**: pytest suite (data counts/determinism, memory, hypotheses, the learning-curve pipeline test, API via httpx ASGITransport) and root `README.md` (pitch, architecture, quickstart, demo script, metrics methodology, Maximor-criteria mapping).
7. **Integration & rehearsal (do this yourself, in the browser)**: `make seed && make backend` + `make frontend`, then walk ARCHITECTURE §9: Run Jan → open a needs_human Stripe exception (should show ~42% "difference is exactly 3.0%…") → Teach "3% processing fee" → see propagated siblings + new Rule node in /memory → Run Feb (precedent hits, audit approvals in /trace) → dashboard learning-curve chart → Run Mar (CloudSpan drift 2%→2.5%) → teach → rule v2 → /memory provenance from a March decision back to the January human correction. Fix whatever breaks; screenshot each step for the pitch deck.
8. **Polish / stretch (only after 1–7 are green)**: cinematic pacing default (`CFO_STEP_DELAY_MS`) tuned so a period run takes ~25–40 s on stage; "Re-run January with memory" button to show the same period jump from ~80% → ~95%; LLM mode smoke test if an OpenAI key becomes available at the venue (`OPENAI_MODEL` default `gpt-4.1-mini`); Neo4j adapter smoke test if Docker is available elsewhere; 60-second pitch video.

## 3. Commands
```bash
cd ~/Documents/hackMIT/cfo-office
make setup                                   # uv sync + npm install
cd backend && uv run python -m app.data.seed --reset      # seed + print ground-truth counts
cd backend && uv run python scripts/smoke_pipeline.py     # (after C2) full learning curve offline
make backend                                 # http://localhost:8000/docs
make frontend                                # http://localhost:3001
make test                                    # pytest
./scripts/demo.sh                            # one-command demo
```
Sanity imports: `cd backend && uv run python -c "import app.data.generator, app.memory.service, app.agents.hypotheses, app.agents.tools"`.

## 4. Invariants that must not be broken (they are the pitch)
- Rate-based fixes (fees, discounts, FX) are never guessed by built-in logic; only a learned Rule can resolve them → visible learning.
- Tiers: HIGH ≥ 0.85 execute, MEDIUM 0.60–0.85 Audit re-performs, LOW < 0.60 human. Rule trust grows: Jan human → Feb audit-verified → Mar autonomous.
- Teaching once propagates to sibling open exceptions immediately.
- Drift (rule nearly fits) is flagged, never forced; new rule version supersedes the old with a SUPERSEDES edge.
- Metrics are computed against hidden `ground_truth` (never exposed through agent tools; API only reveals with `?reveal=1`).
- Works fully offline; LLM only augments.

## 5. Known risks / gotchas
- Next 16: client-only libs (cytoscape) must be dynamically imported; `useSearchParams` needs a Suspense boundary; no `metadata` export from client files.
- `app/events.py` bus must be bound to the running loop in the FastAPI lifespan (`bus.bind_loop`) or SSE from background tasks silently drops.
- Re-running a period must call `reset_period_state` first (bank txns → unreconciled, invoices → open, agent ledger entries deleted, previous Exception nodes marked superseded) but must keep Rules/HumanCorrections.
- `ground_truth` counts per period are the acceptance test for the data generator (ARCHITECTURE §2 table).
- Keep `step_delay_ms=0` in tests/smoke; default 120 ms for the live demo.
