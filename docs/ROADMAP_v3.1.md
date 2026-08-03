# PEAR v3.1 Roadmap — Ecosystem & Product Maturity

**Principle:** No core architecture rewrites. Ship on the frozen v3.0 public API (`agents`, `connectors`, `plugins`, `workflows`, `service`).

## Themes

1. **Usability** — polished clients, clearer defaults, fewer permission friction points  
2. **Optimization** — planner quality, retrieval, latency, memory  
3. **Ecosystem** — connectors, plugins, sample workflows, tutorials  
4. **Compatibility** — additive APIs only; deprecations announced, not removed in 3.1.x  

---

## Milestones

### v3.1.0 — Ecosystem Foundations (target)
- Expand connector pack (optional, disabled until configured):
  - Slack, Notion, Google Drive, Jira (plus existing GitHub / Email / Calendar / n8n)
- Plugin examples published under `plugins/` with manifests
- Sample workflows: finance monthly review, contract → summary, research brief
- Tutorial docs: first agent, first connector, first workflow
- Planner soft-bias from LearningEngine (still advisory unless explicitly enabled)

### v3.1.1 — Performance
- Retrieval: hybrid ranker tuning from usefulness feedback
- Cache frequent knowledge chunks in-process (TTL)
- Session orchestrator lazy-init for unused agents
- Worker pool sizing from config profile
- Publish perf baselines from `tests/test_perf_v300.py` into docs

### v3.1.2 — Clients
- Web dashboard polish (auth UX, goal/worker panels)
- Official thin Python client (`pear_client`) over REST
- Design notes for desktop/mobile (not full native apps required in 3.1)

### v3.1.3 — Adoption
- Expanded developer guide + cookbook
- Video/script outline for “5-minute PEAR”
- Public sample data pack (contracts, statements) for evaluation

---

## Planner quality plan

| Signal | Use in 3.1 |
|--------|------------|
| Evaluation suite scores | Nightly job → learning ingest |
| Route success / latency | `LearningEngine.planner_agent_bias()` |
| Collab mode outcomes | Suggested default mode |
| User feedback on answers | Retrieval term boost |

**Config flag:** `planner_use_learned_bias: false` by default (opt-in).

---

## Connector priority

| Connector | Value | Complexity |
|-----------|-------|------------|
| Slack | Notifications, commands | Medium |
| Notion | Knowledge sync | Medium |
| Google Drive | Doc ingest | Medium |
| Jira | Task bridge | Medium |
| GitHub | Already present | — |

All remain optional; PEAR runs with zero external accounts.

---

## Non-goals for 3.1

- Redesigning TaskGraph / Orchestrator internals  
- Breaking REST paths under `/v1`  
- Mandatory cloud dependencies  
- Full native mobile apps  

---

## Success metrics

- p95 chat latency stable or improved vs v3.0 offline baseline  
- ≥4 new optional connectors or plugins documented  
- Planner bias opt-in improves eval suite by measurable delta on golden tasks  
- Zero breaking changes on `PUBLIC_APIS`  
