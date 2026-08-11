# PEAR Audit Log

## PEAR 3.1 — Ownership, Isolation & Concurrency Hardening

Status of the 9-gate task card. Each gate is only marked done once it has
a real test that (a) fails against the pre-fix code via `git stash` and
(b) passes against the fix, run stable across repeated full-suite runs —
not just "code written."

- 🟢 **Gate 1 — Tracer isolation.** Fixed in `b93c91c`. Root cause was
  deeper than the original audit's `/v1/traces` finding: 28 call sites
  across agents/connectors/workers/goals all read a bare process-global,
  and `Orchestrator.__init__` mutated it on every construction. Replaced
  with a `contextvars.ContextVar` (zero changes needed to those 28 sites),
  activated at `route()`, `JobManager._execute()`, and
  `WorkerManager._run_local/_run_remote`. Verified with a real
  `ThreadingHTTPServer`, two real users, 50 concurrent `/v1/chat` requests.
- 🟢 **Gate 2 — Resource ownership / IDOR protection.** Fixed in `e1ab238`.
  Audited every ID-accepting route: `/v1/goals/<gid>` is the *only* by-ID
  resource route that exists anywhere in the stdlib dispatcher — jobs,
  workflows, memories, traces, plugins, connectors have no individual
  lookup route at all today, so most of the task card's example resource
  types have no reachable IDOR surface yet (noted, not invented). Found a
  real bug unrelated to numeric/path IDs but the same class: `/v1/beta/activate`
  and `/v1/beta/status` both let an authenticated caller's request body
  override their own identity (`account = data.get("account") or
  user.username`, wrong priority), and `/v1/beta/status` required no
  credential at all — a pure account-status oracle. Fixed both; server-
  derived identity always wins now. Added an explicit `authorize_resource()`
  check on `/v1/goals/<gid>` using `Goal.user_id` (Gate 4) as defense-in-
  depth, even though the route is already structurally safe via per-user
  Orchestrator scoping — denial returns 404, not 403, so it can't be used
  to enumerate other users' IDs.
  **New tracked gap:** admin's `authorize_resource(allow_admin=True)`
  bypass is correct at the check level but currently unreachable via any
  HTTP route — admin is scoped to admin's own Orchestrator same as anyone
  else, so admin gets 404 on another user's goal too. Confirmed this
  predates Gate 2 (tested directly against `SessionManager`). Needs an
  actual cross-session resource-lookup path for admins — a real design
  decision, not queued to a specific gate yet.
- 🟡 Gate 3 — Credential isolation. Not started.
- 🟢 **Gate 4 — Explicit ownership propagation.** Fixed in `f108bf5`.
  `Orchestrator` now carries `self.user_id`, set once at construction by
  `SessionManager`. `Job`, `Goal`, and `WorkflowRun` all gained a `user_id`
  field, auto-stamped from the owning orchestrator. `DispatchRecord`'s
  existing-but-unused `session_user` field now actually gets populated.
  Deliberately skipped "Connector execution" — no durable record exists
  for it at all (nothing to stamp, adding one would be a feature, not a
  fix). Found but not fixed: `WorkerManager` computes a `persist_dir` but
  has zero save/load calls anywhere — dispatches don't survive restart at
  all, independent of ownership. Flagged below for Gate 7.
- 🟢 **Gate 5 — Worker identity propagation.** Fixed in `fd51818`.
  `_run_remote_inner` was sending only `{"message": objective}` plus the
  worker's own stored bearer token — the originating user's identity
  never reached the remote side or any audit trail. Added
  `origin_user_id`/`dispatch_id` to the outbound payload, explicitly as
  informational metadata, not a credential — commented directly in the
  code that the bearer token remains the only thing that actually
  authenticates the request, since conflating those two is exactly what
  Gate 2 found and fixed in the beta routes. Two of the gate's other
  required properties (retry preserves ownership; a spoofed identity in
  a remote response can't override local ownership) were already true by
  construction — locked in with tests, not "fixed" since there was
  nothing to fix. "Ownership survives restart" doesn't apply here yet —
  same WorkerManager persistence gap already logged under Gate 4/7.
- 🟡 Gate 6 — Session lifecycle (eviction). Not started.
- 🟡 Gate 7 — Persistence/recovery audit. Not started as its own pass, but
  now has two concrete items queued: WorkerManager's missing persistence
  (above), and the broad `except: pass` patterns on auth/session file loads
  noted in the original architecture audit.
- 🟡 Gate 8 — Concurrency testing (30 concurrent users). Not started —
  intentionally sequenced last among the gates it depends on.
- 🟡 Gate 9 — API surface reconciliation (stdlib vs FastAPI). Not started.

See `tests/test_security_v310.py` for the live test suite as gates land.

---

## Pre-3.1 findings (module-by-module pass, before the v3.0/quant merge)

`docs/roadmap.md` says what was *intended*; this says what's actually been
verified by running the code, not just reading it. Status is only set to
Fixed once it's been executed and checked, not once code has been written.

Legend: 🔴 Broken · 🟡 Open / not yet diagnosed · 🟢 Fixed & verified

---

## 🔴 `scripts/expand_core.py` silently destroys newer memory.py

`core/_memory.py.z64` decompresses to an old, simpler `memory.py` — no
embeddings, no vector store, no memory-intelligence integration. The
committed `core/memory.py` in the repo has all of that, but the compressed
payload was never regenerated to match after those features were added.

**Anyone who runs the documented Quick Start (`python scripts/expand_core.py`)
on a fresh checkout right now silently overwrites the real memory.py with the
stale one.** No error, no warning — it just succeeds and wipes newer work.

Fix options: (a) regenerate `_memory.py.z64` from the current `memory.py` so
the payload matches, or (b) make `expand_core.py` a no-op / prompt-before-
overwrite when the destination file already exists and differs. Not fixed yet.

---

## 🟡 Action layer (`core/action.py`) still doesn't exist here

The file-upload fix above uses `use_tool()` directly (the only tool-access
path that actually exists in this tree) rather than the Action-layer
(`ReadDocumentAction`/`ActionExecutor`/retries/audit trail) built in an
earlier session — that layer never made it into v2.30 at all. Needs a
decision: rebuild it against the current `core/executor.py`/
`core/task_graph.py`, or confirm those already cover the same ground
(retries, structured execution tracking) and the Action layer is redundant.

---

## 🟡 `open_application` is permission-denied by default — needs a decision, not a silent fix

Same root cause as the permission bug below, but I didn't touch this one:
`open_application` requires permission key `"open_app"`, which isn't in
`Permissions`' default grant set, and its `policies` entry is `"confirm"` —
that looks deliberate (launching arbitrary desktop apps probably *should*
need confirmation, unlike reading/summarizing a file). Right now there's no
actual confirm-flow wired up though, so in practice it's just permanently
blocked with no path to grant it. Needs a real decision: build the confirm
flow, or grant it by default like read/summarize were supposed to be.

---

## 🟡 6 failing tests — 4 remain, root cause found for the other 2

```
test_plugins_v110.py::test_discover_builtin_plugins        AssertionError
test_plugins_v110.py::test_load_weather_registers_tool_and_command
test_plugins_v110.py::test_disable_enable                  KeyError: unknown plugin 'notion'
test_plugins_v110.py::test_notion_registers_connector_when_enabled
```

The plugin ones look related — `PluginManager.discover()` isn't finding the
`notion` plugin at all, which would explain all four. Not dug into yet.

(`test_eval_v120::test_isolation_temp_state` and
`test_media_v100::test_media_manager_indexes_knowledge` turned out to be the
same permission-key bug as below — fixed as a side effect, not separately.)

---

## 🟢 Fixed & verified

- **`core/_memory.py.z64` payload corruption** — decompression used to die on
  the checksum trailer and leave 5 stray `0` lines from a bad source encode.
  Payload now decompresses clean. (Distinct from the stale-content issue
  above — this was about the bytes being corrupt, not the content being old.)
- **`chat_stream` didn't exist anywhere** — every chat message threw
  `AttributeError`, silently caught, replaced with a generic error reply.
  `orchestrator.py` also force-set `streamed: True` regardless of outcome.
  Both fixed in `8aa32f1`: real `chat_stream`/`generate_stream` on all four
  LLM providers, and the flag now reflects what actually happened.
- **`ui/dashboard.py` crash** — `mem.messages`/`mem.notes` don't exist on the
  `Memory` facade. Fixed in `ba3e67a`.
- **`ui/app.py` file upload bypassed agents/registry** — fixed in `ba3e67a`
  via `PersonalAgent.handle_file_upload()`, routed through `use_tool()`.
- **`read_document`/`summarize_text` permission-key mismatch** — found while
  testing the file-upload fix, not going in blind: both tools required
  permission keys (`read_file`, `summarize`) that don't exist anywhere in
  `Permissions`' default grant set, which grants `read_document`/
  `summarize_text` by tool name — matching the convention every other tool
  in the registry already follows. Every call to either tool was silently
  permission-denied by default, regardless of caller. Fixed in `ba3e67a`;
  also fixed two of the "not yet diagnosed" test failures as a side effect.

---

## Known design footgun to watch for elsewhere

`ToolRegistry.call(name, *args, **kwargs)` uses `name` as its own parameter —
collides with any tool whose own signature also takes a `name` argument
(bit `open_application(name: str)`). Worth grepping for other tools with a
`name` param before wiring anything else through `registry.call()` by keyword.
