# PEAR v3.1 Roadmap — Ecosystem & Product Maturity

**Principle:** No core architecture rewrites. Ship on the frozen v3.0 public API
(`agents`, `connectors`, `plugins`, `workflows`, `service`).

**Status:** Planning + scaffolding in progress. Security hardening and closed beta
landed as parallel 3.1 tracks.

---

## Themes

1. **Usability** — polished clients, clearer defaults, less permission friction  
2. **Optimization** — planner quality, retrieval, latency, memory  
3. **Ecosystem** — connectors, plugins, sample workflows, tutorials  
4. **Compatibility** — additive APIs only; no removals in 3.1.x  

---

## Progress snapshot

| Item | State |
|------|--------|
| Connector scaffolds (Slack, Notion, GDrive, Jira) | **Done** (disabled until credentials) |
| Existing connectors (GitHub, Email, Calendar, n8n, Local Files) | **Done** |
| Tutorials 01–03 + sample workflow JSON | **Done** |
| Opt-in planner learned bias (`planner_use_learned_bias`) | **Done** |
| Security hardening (sessions, lockout, IDOR, uploads, CORS) | **Done** |
| Closed beta keys / activation / feedback | **Done** |
| RSI (v3.10) evidence-based proposals | **Done** (adjacent track) |
| Live OAuth for Slack/Notion/GDrive/Jira | **Next** |
| Thin REST client package | **Next** |
| Dashboard UX polish | **Next** |
| Retrieval TTL cache + lazy agent init | **Next** |
| Cookbook + 5-minute tutorial script | **Next** |

---

## Milestones

### v3.1.0 — Ecosystem foundations *(scaffolding complete)*

- [x] Optional connectors: Slack, Notion, Google Drive, Jira  
- [x] GitHub / Email / Calendar / n8n already present  
- [x] Sample workflows: finance monthly, contract summary  
- [x] Tutorials: first agent, connector, workflow  
- [x] Planner soft-bias from LearningEngine (opt-in)  
- [ ] Real OAuth / token flows for each new connector (replace dry-run)  
- [ ] Example plugins under `plugins/` with manifests (Weather/Notion/Slack demos)

### v3.1.1 — Performance

- [ ] Hybrid retrieval re-rank using usefulness feedback  
- [ ] In-process TTL cache for frequent knowledge chunks  
- [ ] Lazy-init agents not needed for the current route  
- [ ] Worker pool sizing from config profile  
- [ ] Publish offline baselines from `tests/test_perf_v300.py` into docs  

### v3.1.2 — Clients

- [ ] Web dashboard: login UX, goals/workers/beta panels  
- [ ] Official thin Python client (`clients/python/pear_client`) over REST  
- [ ] Design notes for desktop/mobile (consume stable `/v1`; beta already has activation)  
- [ ] CORS production origins documented (`docs/CORS.md`) — **done**

### v3.1.3 — Adoption

- [ ] Developer cookbook (common recipes)  
- [ ] “5-minute PEAR” script outline  
- [ ] Sample data pack pointer (contracts, statements already under evaluation/)  
- [ ] Beta feedback → prioritized fix backlog process  

---

## Planner quality plan

| Signal | Use in 3.1 |
|--------|------------|
| Evaluation suite scores | Nightly job → `LearningEngine.ingest_evaluation` |
| Route success / latency | `planner_agent_bias()` + opt-in config |
| Collab outcomes | Suggested default mode |
| Beta / user feedback | Retrieval term boost + issue triage |
| RSI proposals | Config-only tweaks after validation + human approve |

**Config:** `planner_use_learned_bias: false` by default.

---

## Connector priority

| Connector | Value | Status |
|-----------|-------|--------|
| Slack | Notifications | Scaffold |
| Notion | Knowledge sync | Scaffold |
| Google Drive | Doc ingest | Scaffold |
| Jira | Task bridge | Scaffold |
| GitHub | Repos/issues | Live scaffold |
| Email / Calendar | Messaging / scheduling | Present |
| n8n | Automation bridge | Optional |

All remain optional; PEAR runs with zero external accounts.

---

## Non-goals for 3.1

- Redesigning TaskGraph / Orchestrator  
- Breaking REST paths under `/v1`  
- Mandatory cloud dependencies  
- Full native mobile app rewrite (beta program covers closed mobile testing)

---

## Success metrics

- p95 chat latency stable or improved vs v3.0 offline baseline  
- ≥4 optional connectors documented and testable offline  
- Opt-in planner bias improves eval golden tasks by measurable delta  
- Zero breaking changes on `PUBLIC_APIS`  
- Security suite green (`tests/test_security_v31.py`)  

---

## Suggested near-term order

1. OAuth/token completion for top connectors (Slack → Notion)  
2. `pear_client` Python package + dashboard login polish  
3. Retrieval cache + lazy agents  
4. Cookbook from real beta feedback  
