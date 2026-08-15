# PEAR API Surface — Parity Matrix (PEAR 3.1 Gate 9)

## Canonical decision

**`service/app.py`'s stdlib `_dispatch()` is the canonical PEAR HTTP API.**
`create_app()` (FastAPI) is a **secondary, compatibility surface** — it
covers the routes needed for basic chat/goals/dashboard use, not the full
feature set, and is not expected to grow to full parity as part of PEAR 3.1.

Rationale: the stdlib dispatcher already carries the real ~40-route
feature set (jobs, workflows, memories, traces, plugins, connectors, the
beta program, admin). Making FastAPI canonical now would turn this gate
into a framework migration, not a security-hardening pass. A future
FastAPI migration — if wanted — should be its own controlled PEAR 3.2/4.0
task, with the stdlib test suite acting as the behavioral oracle it has
to match.

`main()` prefers FastAPI+uvicorn when both are importable, stdlib
otherwise. That preference is about which *server* runs, not which is
canonical — it predates this gate and wasn't changed by it, since doing so
would be a deployment-behavior change out of scope for a docs/parity pass.
Worth a deliberate decision in Gate 9's follow-up, not made unilaterally
here.

## Overlapping routes (exist on both surfaces)

| Route | Method | Stdlib | FastAPI | Parity after Gate 9 |
|---|---|---|---|---|
| `/health` | GET | ✅ | ✅ | Identical |
| `/ready` | GET | ✅ | ✅ | Identical |
| `/metrics` | GET | ✅ (+`uptime_s`) | ✅ | Stdlib includes `uptime_s`; FastAPI doesn't. Cosmetic, not fixed — low value, high churn to touch a monitoring-only field. |
| `/`, `/dashboard` | GET | ✅ | ✅ | Identical (same static file) |
| `/auth/login` | POST | ✅ | ✅ | **Fixed.** Both now call shared `PearService.do_login()` — rate limiting and audit logging were previously FastAPI-only-missing. |
| `/auth/logout` | POST | ✅ | ✅ | **Fixed.** Both now call shared `PearService.do_logout()` — audit logging was previously FastAPI-only-missing. |
| `/v1/chat` | POST | ✅ | ✅ | **Fixed.** Both now call shared `PearService.do_chat()` — `learning.observe_route()` was previously FastAPI-only-missing, meaning `/v1/recommendations` would silently starve under a FastAPI-only deployment. |
| `/v1/chat/stream` | POST | ✅ (simulated) | ✅ (real SSE) | **Partially fixed, partially intentional.** Stdlib was calling `orch.route()` **twice** per request (dead leftover code) — fixed to call once via `do_chat()`. Response *shape* still differs: FastAPI streams real Server-Sent-Events via `StreamingResponse`; stdlib collects the same chunks and returns them as a JSON list in one response, since `http.server` has no native streaming-response story the way an ASGI app does. This is a genuine capability difference, not a bug — documented here rather than "fixed" by building chunked-transfer-encoding into the stdlib handler, which would be a bigger change than this gate's scope. |
| `/v1/goals` | GET | ✅ | ✅ | Identical — both call `orch.goals.list_goals()` directly, same shape |
| `/v1/agents` | GET | ✅ | ✅ | Identical field set |
| `/v1/recommendations` | GET | ✅ | ✅ | Identical — both call `orch.learning.analyze()` + `list_recommendations()` |

**Authentication behavior**, all overlapping authenticated routes: both
surfaces resolve the user from `Authorization: Bearer <token>` via
`AuthManager.resolve_token()` (stdlib via `user_from_headers()`, FastAPI
via the `user_dep()` dependency) — same underlying call, same semantics.

**Authorization/ownership behavior**: both surfaces resolve
`service.sessions.get(user.username).orchestrator` — the exact same
per-user session/ownership boundary from Gates 4 and 6. No divergence
found here; this was already shared by construction since both call the
same `SessionManager`.

## Stdlib-only routes (not ported to FastAPI — compatibility surface, not required)

Per the "do not blindly duplicate" principle: these are **not** being
added to FastAPI as part of Gate 9. Listed here so the gap is explicit
and documented rather than silently discovered later.

- `/v1/me`
- `/v1/upload`
- `/v1/goals` POST, `/v1/goals/<id>` GET
- `/v1/jobs`, `/v1/workflows`, `/v1/memories`, `/v1/traces`, `/v1/plugins`, `/v1/connectors`
- `/v1/evaluate`
- `/v1/quant/paper`, `/v1/quant/paper/dashboard`
- `/v1/beta/*` (activate, status, consent, feedback, telemetry) and `/beta`, `/beta/feedback`
- `/admin/*` (beta keys/revoke/extend/feedback, sessions, users) and `/admin/beta`

If any of these need to work under a FastAPI deployment, that's a
deliberate scope decision for whoever picks up the FastAPI-migration
follow-up, not something to backfill quietly here.

## Findings from building this matrix

1. **Dead/duplicate route handlers.** A second, pre-Gate-2-vulnerable copy
   of `/v1/beta/activate` and `/v1/beta/status` sat later in `_dispatch()`
   — unreachable today only because the earlier (Gate-2-fixed) block
   always matches first. A real landmine: any future reordering of these
   blocks would have silently reintroduced the exact account-override
   vulnerability Gate 2 fixed, with no warning, since the dead code still
   looked valid. Removed. Locked in with
   `test_gate9_no_duplicate_dead_route_handlers`, which reads the actual
   source rather than testing behavior — a behavioral test alone can't
   catch dead code that never executes.
2. **Shared business logic extracted**, not just patched on the FastAPI
   side: `PearService.do_login()`, `do_logout()`, `do_chat()`. The point
   isn't just closing today's three gaps — it's that a *third*
   independent implementation can't quietly reappear later, since both
   surfaces now call one place.
3. Neither FastAPI nor uvicorn were installed in the environment this
   gate was verified in — meaning the FastAPI surface had never actually
   been exercised end-to-end anywhere in this hardening pass before this
   gate. Installed both and tested against a real `TestClient`
   (`fastapi.testclient`) rather than trusting the code by inspection.

## Recommendation for after PEAR 3.1

Freeze 3.1 once Gate 9 lands and re-run the full architecture audit from
scratch (the same audit prompt that started this hardening pass) against
the frozen state. The original audit's findings — cross-user tracer leak,
dead `authorize_resource()`, shared credential store, implicit ownership,
worker identity drop, unbounded sessions, silent persistence corruption,
no concurrency testing, divergent HTTP surfaces — should now all resolve
to 0 unresolved P0/P1s, per the task card's own required result. That
re-audit is what actually confirms it, not this document.
