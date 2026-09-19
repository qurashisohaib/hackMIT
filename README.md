# AI Office of the CFO

A working finance-agent demo for **Lumen Robotics Inc.** Specialized agents close the books,
investigate exceptions, audit evidence, forecast cash and write a close report. A controller
teaches a rule once; the Financial Memory Graph preserves its evidence and applies it to later
transactions. January starts cold, February uses those precedents, and March exposes a changed
vendor fee rather than silently forcing a match.

The default demo requires **no API keys, hosted database, or LLM connection**. Install dependencies
once with internet access; subsequent runs use local SQLite, deterministic reasoning and local
hashed embeddings. The frontend uses system fonts and makes same-origin API requests.

## Quick start

Validated on Ubuntu 22.04 with **Python 3.12.11**, **uv 0.8.22**, **Node 24.19.0**, and
**npm 10.8.3**. Python and Node are pinned in `backend/.python-version` and `frontend/.nvmrc`;
`backend/uv.lock` and `frontend/package-lock.json` lock dependencies.
You also need Git, Bash, Make and curl. Native Windows users should use WSL.

```bash
git clone https://github.com/qurashisohaib/hackMIT.git
cd hackMIT
# If uv is not installed:
python3 -m pip install --user uv==0.8.22
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12.11

# With nvm installed:
source "$HOME/.nvm/nvm.sh"
cd frontend
nvm install
nvm use
npm install --global npm@10.8.3
cd ..

make setup
make demo
```

Open **http://localhost:3001**. API docs are at **http://localhost:8000/docs**.
`make demo` reseeds the synthetic company, starts both servers, waits for the frontend's proxied
health check, and stops both process groups on Ctrl+C. It defaults to `CFO_LLM_ENABLED=false`.
Unset `NEO4J_URI` if your shell configures an external graph and you want the embedded demo.
Logs stay in ignored local files: `backend/data/backend.log` and `frontend/frontend.log`.

To keep existing runs and learned memory, start the servers separately in two terminals:

```bash
make backend
make frontend
```

An empty database is automatically seeded at backend startup. The default database lives at
`backend/data/cfo_office.db`. `make seed` resets it while servers are stopped.
While servers are running, use the dashboard **Reset** control or `make reset`; this also
clears in-memory traces, graph data and run scheduling state. Reset is intentionally destructive
to this synthetic demo's history.

## Present the learning loop

1. **Run January Close** on the dashboard. Watch the live trace, then inspect unresolved
   exceptions. A fee with no learned rule is held at 42% confidence; the engine never
   guesses its rate from the hidden labels.
2. In **Exceptions**, select a January bank exception with the corresponding counterparty.
   Open its teaching form and enter each instruction below. The response names sibling
   exceptions resolved through propagation.
3. Optionally select **Re-run January with memory** before running February. It restores
   January's financial baseline but keeps rules, so the same data can demonstrate learning.
   Previous run traces remain available; the dashboard uses the latest run per period.
4. **Run February Close**. Inspect precedent hits, the audit challenge/response, and the
   decision's links back to the January correction.
5. **Run March Close**. Both CloudSpan items must remain open with precedent drift:
   the observed surcharge is 2.5%, but January taught 2.0%. Teach one item
   **“CloudSpan now adds a 2.5% surcharge”**. Its sibling resolves and the new rule
   supersedes the old version.
6. Visit **Memory**, **Trace**, **Forecast** and **Reports**. Follow a decision to its rule and
   correction; inspect the 13-week cash forecast and outstanding close checklist.

### Five January teaching instructions

| Select an unresolved bank item | Paste this instruction | Expected sibling propagation |
| --- | --- | --- |
| Stripe (`V-STRIPE`) | `Stripe deducts a 3% processing fee` | 2 |
| CloudSpan (`V001`) | `CloudSpan adds a 2% surcharge` | 1 |
| An outgoing wire with a $25 difference | `Outgoing wire transfers add a $25 bank fee` | 1 |
| Apex Manufacturing (`C001`) | `Apex Manufacturing takes a 2% early payment discount` | 1 |
| Bosch (`V002`) | `Accept up to 1.5% FX variance for Bosch` | 0; there is one such January case |

The heuristic parser supports these phrases without OpenAI. A correction must also pass
financial evidence checks before it can create a rule or change settlement state.
Teaching all five resolves ten bank exceptions; the remaining three items require controller
judgment. They are not silently approved to improve the metrics.

Run periods chronologically. Once a later period has run, an earlier rerun is rejected to
protect cross-period payments. Reset the whole demo before repeating the full sequence.

## Architecture

```text
Next.js 16 / React 19
  dashboard · exceptions · memory · trace · forecast · reports
           │ same-origin /api + Server-Sent Events
           ▼
FastAPI + single-writer RunManager
           │
     CFO orchestrator
           ├─ AP/AR: PO, receipt, invoice and payment checks
           ├─ Reconciliation: hypotheses → tools → evidence → confidence
           ├─ Audit: independently re-perform checks; undo invalid execution
           ├─ Close: checklist and unresolved-item state
           ├─ Forecast: 13-week cash flow with assumptions and actuals
           └─ Report: close narrative and evidence links
           │
     MemoryService ── SQLite property graph + NetworkX mirror
           │          (optional Neo4j adapter)
           └─ exceptions, hypotheses, decisions, corrections, rules,
              trust, rule versions, provenance edges and run traces
```

Financial tables and the default graph are persisted in SQLite. Deterministic seed 42 supplies
January–March, each with **200 bank transactions**, 78 AP invoices, 45 AR invoices and
46 POs. Clean installment payments preserve the underlying invoice obligations and cash totals.
The generator also plants split/combined payments, fees, discounts, FX variance, timing lags,
duplicates, missing POs, price variances and unknown deposits.

- **High confidence ≥ 0.85:** execute; selected decisions also receive audit sampling.
- **Medium confidence ≥ 0.60 and < 0.85:** audit before final acceptance.
- **Low confidence < 0.60:** controller review.
- Trust is `clamp(0.5 + 0.25 × human_verified + 0.05 × confirmations − 0.15 × refutations, 0, 1)`.
- Audit uses ledger evidence, not hidden labels. A rejected settlement can be rolled back.
- Rules are scoped to counterparties or global patterns. A shared payment processor does not
  grant a customer-specific rule access to another customer's invoices.
- Historical decisions retain the exact rule ID/version; a replacement adds `SUPERSEDES`.
- Only CFO evaluation and tests read hidden `GroundTruth`. Reasoning tools do not expose it.
- API writes are serialized; a second run, reset or teaching request gets HTTP 409 during a run.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the contracts and [HANDOFF.md](HANDOFF.md) for module
ownership and original implementation guidance. The installed frontend is Next.js **16.3.5**;
the architecture's original Next 15 label predates the locked dependency.

## Measured metrics and their meaning

The isolated, offline smoke evaluates January → five teachings → February → March → correction.
These seed-42 values were measured with zero display pacing:

| Snapshot | Accuracy on decided items | Coverage | Pending human reviews | Automatic precedent hits |
| --- | ---: | ---: | ---: | ---: |
| January, cold close | 100% | 93.69% | 13 | 0 |
| January, after all five teachings | 100% | 98.54% | 3 | 5 |
| February, with January rules | 100% | 98.54% | 3 | 10 |
| March, before drift correction | 100% | 97.57% | 5 | 8 |
| March, after 2.5% correction | 100% | 98.54% | 3 | 9 |

**Accuracy is not coverage.** Accuracy measures correct executed decisions against the hidden
expected match, pattern and rule parameters. Undecided items are excluded from its denominator.
Coverage is decided truth-backed items divided by all evaluated items (206 per period here:
200 bank rows plus six exceptional AP items). Thus 100% accuracy does not mean every item closed.

The dashboard learning curve and `/api/metrics` retain **initial completion snapshots**, before
subsequent human teaching. Latest report/forecast observations and the exception queue reflect
current post-teaching state. A rerun produces another snapshot and replaces that period's point
on the dashboard; the old run remains in history. For the cold-to-learned comparison above, do not
insert a January rerun into the three-period sequence.

`human_reviews` counts items still requiring a controller at the time of measurement.
`precedent_hits` counts executed, non-human decisions that used a rule; the human-taught
item itself is excluded. Post-teaching gains also include sibling propagation.
`avg_steps_per_exception` and `avg_ms_per_exception` average traced exceptional investigations,
not every clean bank row. Different period cases mean those averages need not fall after learning.
`wall_ms` includes the entire close and display pacing; post-teaching snapshots retain the
original close's wall time. UUIDs/timestamps and elapsed timings vary, while the synthetic
financial data and offline decision outcomes are deterministic.

Forecast error is only calculated for weeks with actual synthetic transactions; it is `null`
when no actuals exist. The cold January forecast measured **89.31% cash-flow WAPE** against
observed February weeks: it projects existing open invoices and recurring payroll, so it does
not predict new future business. It is not a claim of out-of-sample forecasting performance.

The zero-pacing smoke measured close wall times of **9.40s / 11.52s / 10.79s** for
January / February / March on the validation VM. An actual January API run at the default
**120ms pacing took 34.20s**. These are observations, not timing assertions.

## Maximor demonstration criteria mapping

| Project goal | What this demo provides |
| --- | --- |
| Autonomous finance execution | Specialized agents use financial tools and post balanced reconciliation entries. |
| Learning from controller feedback | Five January corrections become scoped, trusted rules and improve February coverage. |
| Reliable controls | Confidence gates, independent audit, rollback and explicit unresolved policy items. |
| Traceability | Evidence, tool results, audit conversations and rule-version provenance in a persistent graph. |
| Adaptation | March's two changed-fee cases trigger drift; a corrected rule supersedes the earlier version. |
| Business deliverables | Close checklist/report, 13-week cash forecast and measured accuracy/coverage/review counts. |
| Reproducible presentation | Deterministic seed, no-key offline flow, reset/replay and automated acceptance tests. |

## Validation

Run from the repository root unless a subshell changes directories:

```bash
make test
(cd backend && CFO_STEP_DELAY_MS=0 CFO_LLM_ENABLED=false uv run --frozen python -m scripts.smoke_pipeline)
(cd backend && uv tool run --from ruff==0.13.1 ruff check app tests scripts)
(cd backend && uv run --frozen python -m compileall -q app tests scripts)
(cd frontend && npm run typecheck && npx tsc --noEmit && npm run lint && npm test && npm run build)
git diff --check
```

Pytest and the smoke runner use isolated databases; they do not modify the interactive demo DB.
The smoke prints machine-readable stage metrics, propagation counts and a final acceptance result.
It also supports the default presentation pacing:

```bash
(cd backend && CFO_LLM_ENABLED=false uv run --frozen python -m scripts.smoke_pipeline --step-delay-ms 120)
```

Tests cover data scale and determinism, rule scope, evidence validation, audit rollback,
settlement replay, learning and drift, graph focus/provenance, API scheduling/SSE/persistence,
and frontend trace merging. Browser interaction testing is a separate validation step.

## Configuration and optional services

Put backend settings in `backend/.env`, a root `.env`, or the shell. Environment files are ignored
by Git. Useful settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CFO_DB_PATH` | `backend/data/cfo_office.db` | Local database path; absolute paths are easiest. |
| `CFO_SEED` | `42` | Synthetic dataset seed. |
| `CFO_STEP_DELAY_MS` | `120` | Presentation pacing; use `0` for tests. |
| `CFO_LLM_ENABLED` | `true` in app, `false` in demo script | A key is also required to enable OpenAI. |
| `CFO_API_PORT` | `8000` | Backend port used by the demo script. |
| `CFO_BACKEND_URL` | `http://127.0.0.1:8000` | Server-side Next rewrite destination. |
| `NEXT_PUBLIC_API_URL` | empty | Optional browser-visible origin, without `/api`. Build-time value. |

The normal browser path is `/api`, proxied by Next. This works when only the frontend is exposed
through a preview URL. `CFO_BACKEND_URL` is read by the Next configuration; restart dev or rebuild
production after changing it. An explicit `NEXT_PUBLIC_API_URL` must be reachable by the viewer's
browser, not just the server.

**OpenAI (optional):** Set `OPENAI_API_KEY` and `CFO_LLM_ENABLED=true`.
Defaults are `OPENAI_MODEL=gpt-4.1-mini` and `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`.
The OpenAI Agents SDK provides optional proposals, explanations and audit narratives;
financial evidence still controls execution. Failures fall back to deterministic behavior.
Costs/tokens are recorded where available. Reset between embedding-provider changes.

**Neo4j (optional):** Set `NEO4J_URI`, `NEO4J_USER` and `NEO4J_PASSWORD` for an existing instance,
then restart the backend. Financial truth stays in SQLite. Do not set `NEO4J_URI` for the normal
offline demo. These optional hosted integrations are not required by, or exercised in, offline
acceptance testing.

## Troubleshooting and limits

- `uv` not found: add `$HOME/.local/bin` to PATH. `npm` not found: load nvm and run `nvm use`
  from `frontend`. `make setup` must complete before `make demo`.
- If ports 8000/3001 are occupied, stop the existing app. The demo does not kill unrelated
  processes. For another backend port, use `CFO_API_PORT=8002 make demo`.
- A 409 indicates an active mutation or a later completed period. Let the active run finish;
  reset before changing chronological order.
- Use the API reset while running. Deleting a live SQLite file leaves the graph mirror stale.
- API unavailable through a preview: use the same-origin default and check `CFO_BACKEND_URL`;
  avoid a browser-facing `localhost` override on a remote preview.
- If no forecast/report exists yet, run that period first. A close may finish with
  `pending_review`; open policy items are represented explicitly in its report.
- Frozen database models still emit Python `datetime.utcnow` deprecation warnings. Node's
  native TypeScript trace tests may emit a non-failing module-type warning.
- This is a single-process synthetic demo: no authentication, real bank/ERP connectors,
  production authorization, or multi-worker scheduler. It does not transfer real funds.
- The heuristic correction parser recognizes supported financial patterns; arbitrary prose
  and novel conditions may require an explicit rule or optional LLM assistance.
- UI/browser flows and optional OpenAI/Neo4j connections require separate environment validation.
