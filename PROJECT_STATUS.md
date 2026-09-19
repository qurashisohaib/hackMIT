# CFO Office — Current Project State

Updated: September 19, 2026

## Handoff summary

The missing application modules from `HANDOFF.md` are implemented. The core offline
demo has passed automated checks and a recorded browser rehearsal. Integration is
in an open pull request; it has not been merged.

- Repository: https://github.com/qurashisohaib/hackMIT
- Pull request: https://github.com/qurashisohaib/hackMIT/pull/1
- Branch: `devin/1789849708-complete-cfo-office`
- Last fully browser-tested commit: `d6e4249`
- Setup and demo guide: [`README.md`](README.md)
- Original requirements: [`HANDOFF.md`](HANDOFF.md)
- Design and contracts: [`ARCHITECTURE.md`](ARCHITECTURE.md)

The project is a synthetic HackMIT demo for Lumen Robotics Inc., with SQLite-backed
financial records and persistent graph memory. Its default mode needs no API keys,
OpenAI connection, or Neo4j service.

## Implemented

### Finance pipeline

- CFO orchestration across AP/AR, reconciliation, independent audit, period close,
  a 13-week cash forecast, and close reporting.
- Evidence-based reconciliation with confidence gates: high confidence executes,
  medium confidence requires audit, and low confidence requires human review.
- Human teaching creates scoped rules and propagates successful resolutions to
  matching sibling exceptions.
- Rule trust, confirmations/refutations, versioning, and provenance.
- February reuses January knowledge; March detects CloudSpan's change from a 2%
  surcharge to 2.5% and learns a superseding rule after correction.
- Deterministic seed data, including 200 bank transactions in each demo period.
- Hidden ground truth for benchmark evaluation, separated from normal agent tools.

### API and persistence

- FastAPI endpoints for periods, runs, exceptions, teaching, graph memory, rules,
  provenance, metrics, forecasts, reports, financial data, and reset.
- Background close runs and a single-writer guard against competing mutations.
- Persisted traces, replay and live server-sent events, and run rehydration.
- Empty-database seeding and same-origin frontend API routing.
- Period reruns restore financial state while retaining learned rules.

### Frontend

- Dashboard, exception workspace, memory graph/rule library, agent trace,
  forecast, and printable report pages.
- Human teaching, propagation feedback, rule trust/version inspection, decision
  provenance, and audit conversations.
- Deep links for exceptions, graph nodes, runs, and periods.
- Loading, empty, error, and missing-resource handling.
- Pre-close forecast/report pages show empty states instead of fabricated
  deliverables.

### Developer experience

- Reproducible setup, pinned runtime versions, Make targets, and an offline smoke
  pipeline.
- Backend acceptance tests and frontend trace/provenance tests.
- README with the demo sequence, metrics methodology, configuration, and limits.
- A repository environment blueprint was accepted to prepare future Devin sessions.

## Verified results

### Automated baseline: `d6e4249`

- 111 backend tests passed.
- Python Ruff and compilation passed.
- Frontend typecheck, ESLint, six frontend tests, and production build passed.
- Isolated January–March smoke, clean-checkout setup, and demo startup passed.

### Recorded browser rehearsal: `d6e4249`

The rehearsal verified January's cold close, all five teachings and sibling
propagation, learned behavior in January/February, March drift and correction,
rule versions/trust, decision provenance, audit trace, empty deliverable states,
and reloading a resolved exception.

| Stage | Human reviews | Automatic precedent hits | Accuracy on decided items |
| --- | ---: | ---: | ---: |
| January cold | 13 | 0 | 100% |
| January after teaching | 3 | — | — |
| February learned | 3 | 10 | 100% |
| March before CloudSpan correction | 5 | 8 | — |
| March after CloudSpan correction | 3 | — | — |

A dash means this table does not assert a measurement for that stage. Accuracy on
decided items does not mean every item is resolved. The offline benchmark measured
coverage rising from 93.69% in cold January to 98.54% in learned February.

The browser rehearsal found and prompted repairs to outgoing-wire sibling
propagation, the customer name in the demo instructions, and pre-close
forecast/report behavior. Those repairs are included in the tested revision.

## Latest review follow-ups

These backend changes were added after the recorded browser rehearsal:

1. Preserve the current API run during period reset so that it remains eligible
   for chronology checks. A completed February run must block an earlier January
   rerun.
2. Restore the period's financial baseline after an exception or cancellation
   during close; retain diagnostic events and supersede incomplete artifacts.
3. Use deterministic transfer-rule parsing in the optional OpenAI path so outgoing
   wire instructions retain their method and cash-direction constraints.
4. Include applicable global percentage rules in cash forecasting while avoiding
   applying transaction-method restrictions to invoices without that evidence.

Regression coverage was added for the real API chronology path, financial rollback
on failure/cancellation, transfer constraints, and global forecast rules.

Latest validation passed: **51 backend pipeline/API tests**, including the five
new regression cases; Ruff, Python compilation, and whitespace checks also passed.
This focused run is separate from the earlier 111-test full backend run.
The frontend was unchanged in these follow-ups. No new browser rehearsal was run
after them.

## Remaining validation and limitations

### Validation still worth doing

- A focused browser pass on the final backend revision.
- Post-close forecast/report rendering and the browser print flow.
- Backend restart persistence through the UI; automated API persistence coverage
  exists.
- Offline/error behavior and a visible `SUPERSEDES` graph edge in a presentation.
- Optional live OpenAI and Neo4j integrations.

### Known scope limits

- This is a single-process synthetic demo. It has no login, production
  authorization, real bank/ERP connectors, or multi-worker scheduler.
- The API currently allows wildcard CORS. Deployment access control and a
  restricted origin policy remain production hardening work.
- `CFO_BACKEND_URL` is a trusted server-side deployment setting. It must point to
  the intended CFO backend; it is not restricted by an application allowlist.
- Failure recovery uses baseline restoration; it is not a database-wide atomic
  transaction or a crash-proof transaction journal.
- The heuristic teaching parser supports documented financial patterns; arbitrary
  prose may require explicit rule parameters or the optional LLM path.
- Forecasting projects existing obligations and payroll, not future new business.
  January's measured cash-flow WAPE was 89.31%; reports expose that error rather
  than implying a highly accurate forecast.
- Frozen models emit `datetime.utcnow` deprecation warnings.

## Run the demo

Use Node `24.19.0`, npm `10.8.3`, Python `3.12.11`, and uv `0.8.22`.
Dependency installation needs internet access; the installed default demo runs
without hosted AI services.

```bash
make setup
make demo
```

`make demo` resets/reseeds the demo database, starts the backend on port 8000 and
the frontend on port 3001, and keeps them running until stopped. To preserve an
existing demo database, start the services separately with `make backend` and
`make frontend` instead.

### Presentation sequence

1. Run January cold and open the exception queue.
2. Teach the matching exceptions with these instructions:
   - `Stripe deducts a 3% processing fee`
   - `CloudSpan adds a 2% surcharge`
   - `Outgoing wire transfers add a $25 bank fee`
   - `Apex Manufacturing takes a 2% early payment discount`
   - `Accept up to 1.5% FX variance for Bosch`
3. Inspect propagation, rules, trust, and provenance.
4. Optionally rerun January with memory before advancing to February.
5. Run February and compare human reviews and precedent hits.
6. Run March, inspect the CloudSpan drift, and teach a 2.5% surcharge.
7. Inspect the superseding rule, trace, forecast, and report.

Run periods chronologically. Reset the demo before returning to January after a
later period has completed.

## Validation commands

```bash
make test
(cd backend && uv tool run --from ruff==0.13.1 ruff check app tests scripts)
(cd backend && uv run --frozen python -m compileall -q app)
(cd backend && CFO_LLM_ENABLED=false uv run --frozen python -m scripts.smoke_pipeline)
(cd frontend && npm run typecheck && npm run lint && npm test && npm run build)
git diff --check
```

## Next owner actions

1. Review the validation details above and PR #1.
2. Complete any remaining validation needed for the presentation.
3. Merge PR #1 when satisfied; it remains unmerged at this handoff.
