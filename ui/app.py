"""
PEAR CLI entry point – interactive chat loop with file upload support.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import create_llm
from agents import PersonalAgent, DesktopAgent, FinanceAgent, LegalAgent, BrowserAgent, ResearchAgent, ComputerUseAgent, EmailAgent, CalendarAgent, ReviewerAgent, CriticAgent


def build_pear() -> Orchestrator:
    memory = Memory(session_id="cli", persist_dir=ROOT / "data")
    llm = create_llm()
    orch = Orchestrator(memory=memory, llm=llm)

    orch.register(PersonalAgent(llm=llm), default=True)
    orch.register(DesktopAgent())
    orch.register(FinanceAgent(llm=llm))  # llm optional until v0.4
    orch.register(LegalAgent(llm=llm))
    orch.register(BrowserAgent())
    orch.register(ResearchAgent(llm=llm))
    orch.register(ComputerUseAgent())
    orch.register(EmailAgent(llm=llm))
    orch.register(CalendarAgent(llm=llm))
    orch.register(ReviewerAgent(llm=llm))
    orch.register(CriticAgent(llm=llm))

    return orch


def handle_file_upload(orch: Orchestrator, path_str: str) -> None:
    path = Path(path_str).expanduser()
    if not path.exists():
        print(f"  ✗ File not found: {path}")
        return
    personal = orch.agents.get("personal")
    if personal is None:
        print("  ✗ No personal agent registered.")
        return

    result = personal.handle_file_upload(str(path))
    if not result.get("ok"):
        print(f"  ✗ Failed to read document: {result.get('error')}")
        return

    print(f"\n  📄 {result['name']} loaded ({result['chars']} chars) → knowledge store")
    print("  ── Summary ──")
    print(result["reply"])
    print()


def print_banner():
    print(
        """
╔══════════════════════════════════════╗
║              P E A R                 ║
║     Personal Agent Runtime           ║
║              v3.10                   ║
╚══════════════════════════════════════╝
  Type a message, or:
    /file <path>   – upload & summarize PDF/DOCX
    import statement <csv> – finance import
    import contract <path> – legal import
    /notes         – list saved notes
    /agents        – agents + capabilities + tools
    /tools         – tool registry
    /llm           – show active LLM provider / model
    /plan          – current execution plan
    /graph         – current task graph
    /plan-history  – previous execution plans
    /jobs          – list background jobs
    /queue         – jobs waiting to run
    /schedule      – schedule a job (see help)
    /cancel-job    – cancel a job by id
    /resume-job    – resume a paused job
    /retry-job     – retry a failed/cancelled job
    /traces        – recent execution traces
    /trace <id>    – inspect a trace
    /metrics       – aggregate latency & success metrics
    /workflows     – list workflow definitions
    /run-workflow  – run a workflow
    /workflow-status – status of a run
    /cancel-workflow – cancel a run
    /approve-workflow – approve paused run
    /desktop       – desktop help
    /files <path>  – list directory in workspace
    /workspace     – show sandbox roots
    /permissions   – show permission policies
    /browser       – browser help
    /open <url>    – open a URL
    /downloads     – list browser downloads
    /history       – browser history
    /browser-permissions – browser permission groups
    /connectors    – list connectors
    /connect <name> – connect a connector
    /disconnect <name>
    /auth-status   – connector auth status
    /credentials   – credential store summary
    /listen /transcribe <audio> – speech to text
    /ocr <image>   – OCR image
    /describe-image <path> – describe image
    /vision <path> – OCR + describe
    /media         – media subsystem status
    /plugins       – list plugins
    /plugin-info <name>
    /plugin-enable <name>
    /plugin-disable <name>
    /plugin-remove <name>
    /evaluate [suite...] – run benchmarks
    /benchmarks     – list suites
    /benchmark-history
    /compare-builds <id_a> <id_b>
    /quality-report
    /research <query> – multi-source research
    /sources         – last research sources
    /research-report – last research report
    /computer       – computer-use help
    /capture-ui     – screenshot + element map
    /click <target> – click UI element
    /type <text>    – keyboard input
    /inbox          – prioritized inbox
    /email-search <q>
    /summarize-thread <id|subject>
    /draft-email <intent>
    /calendar       – calendar help
    /agenda         – upcoming agenda
    /schedule <event>
    /free-time [tomorrow]
    /reminders
    /voice          – voice status
    /listen <audio> – transcribe + route + speak
    /mute /unmute
    /voice-settings [wake=...]
    /memories       – top important memories
    /memory-stats
    /memory-cleanup
    /memory-search <query>
    /collaborate <agents> :: <objective>
    /review <objective>
    /consensus <agents> :: <objective>
    /tasks         – recent tasks
    /events        – recent event stream
    /planner       – planner memory summary
    /clear         – clear working memory
    /quit          – exit
"""
    )


def main() -> None:
    orch = build_pear()
    orch.jobs.start()
    print_banner()

    while True:
        try:
            user = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user:
            continue

        if user.lower() in ("/quit", "/exit", "quit", "exit"):
            print("Bye.")
            break

        if user.lower().startswith("/file "):
            handle_file_upload(orch, user[6:].strip())
            continue

        if user.lower() in ("/notes", "list notes"):
            result = orch.route("list notes")
            print(f"\npear › {result.get('reply', result)}\n")
            continue

        if user.lower() == "/agents":
            for a in orch.agent_catalog():
                print(f"  • {a['name']}: {a['description']}")
                print(f"    caps:  {', '.join(a['capabilities'])}")
                print(f"    tools: {', '.join(a['allowed_tools']) or '(none)'}")
            continue

        if user.lower() == "/tools":
            for t in orch.registry.list_tools():
                print(f"  • {t['name']}: {t['description']}")
                print(f"    tags: {', '.join(t['tags'])}  perm: {t['requires_permission']}")
            continue

        if user.lower() == "/llm":
            llm = orch.llm
            avail = llm.is_available()
            print(f"  provider : {llm.provider}")
            print(f"  model    : {llm.model}")
            print(f"  available: {avail}")
            if hasattr(llm, "list_models"):
                models = llm.list_models()
                if models:
                    print(f"  local    : {', '.join(models[:12])}")
            continue

        if user.lower() in ("/plan", "/plan-history", "/graph"):
            cmd = user.lower()
            if cmd == "/plan":
                snap = orch.plan_snapshot()
                if not snap:
                    print("  (no plan yet)")
                else:
                    print(f"  plan_id : {snap.get('plan_id')}")
                    print(f"  summary : {snap.get('summary')}")
                    print(f"  single  : {snap.get('single_step')}")
                    print(f"  reason  : {snap.get('reasoning')}")
                    for i, t in enumerate(snap.get("tasks") or []):
                        deps = t.get("depends_on") or []
                        print(f"  [{i}] {t.get('objective')[:70]}")
                        print(f"       agent={t.get('preferred_agent')} caps={t.get('required_capabilities')} deps={deps}")
            elif cmd == "/graph":
                g = orch.graph_snapshot()
                if not g:
                    print("  (no graph yet)")
                else:
                    print(f"  plan_id : {g.get('plan_id')}")
                    print(f"  summary : {g.get('summary')}")
                    print(f"  done    : {g.get('completed')}  failed={g.get('failed')}")
                    for nid, n in (g.get("nodes") or {}).items():
                        print(f"  • {nid} [{n.get('status')}] {n.get('objective')[:60]}")
                        print(f"      agent={n.get('assigned_agent')} deps={n.get('dependencies')}")
            else:  # plan-history
                plans = orch.planner_memory.recent_plans(10)
                if not plans:
                    print("  (no plan history)")
                for p in plans:
                    flag = "ok" if p.get("success") else "FAIL"
                    print(f"  [{flag}] {p.get('plan_id')} tasks={p.get('task_count')} {p.get('duration_s'):.2f}s")
                    print(f"       {p.get('summary') or p.get('objective')}")
            continue


        if user.lower() in ("/jobs", "/queue"):
            jobs = orch.jobs.queue() if user.lower() == "/queue" else orch.jobs.list_jobs(limit=20)
            if not jobs:
                print("  (no jobs)")
            for j in jobs:
                when = ""
                if j.scheduled_at:
                    import time as _t
                    when = f" sched={_t.strftime('%H:%M:%S', _t.localtime(j.scheduled_at))}"
                print(f"  [{j.status.value}] {j.id} p={j.priority.value} {j.progress:.0%}{when}")
                print(f"       {j.objective[:70]}")
            continue

        if user.lower().startswith("/schedule"):
            # /schedule [in Ns|daily HH] <objective>
            parts = user.split(maxsplit=2)
            if len(parts) < 3:
                print("  usage: /schedule in 60s <objective>")
                print("         /schedule daily 9 <objective>")
                continue
            mode, rest = parts[1], parts[2]
            if mode == "in" and rest:
                # in 60s buy milk  OR  in 60s <obj>
                bits = rest.split(maxsplit=1)
                if len(bits) < 2:
                    print("  usage: /schedule in 60s <objective>")
                    continue
                dur, objective = bits[0], bits[1]
                secs = float(dur.rstrip("sS"))
                r = orch.submit_job(objective, interval_s=None, scheduled_at=__import__("time").time() + secs)
                # one-shot via scheduled_at
                print(f"  {r.get('reply')}")
            elif mode == "daily":
                bits = rest.split(maxsplit=1)
                hour = int(bits[0])
                objective = bits[1] if len(bits) > 1 else ""
                r = orch.submit_job(objective, daily_hour=hour)
                print(f"  {r.get('reply')}")
            elif mode == "every":
                bits = rest.split(maxsplit=1)
                secs = float(bits[0].rstrip("sS"))
                objective = bits[1] if len(bits) > 1 else ""
                r = orch.submit_job(objective, interval_s=secs)
                print(f"  {r.get('reply')}")
            else:
                # treat rest of line after /schedule as objective one-shot now
                r = orch.submit_job(user.split(" ", 1)[1] if " " in user else user)
                print(f"  {r.get('reply')}")
            continue

        if user.lower().startswith("/cancel-job"):
            parts = user.split(maxsplit=1)
            if len(parts) < 2:
                print("  usage: /cancel-job <job_id>")
                continue
            try:
                j = orch.jobs.cancel(parts[1].strip())
                print(f"  cancelled {j.id}")
            except Exception as e:
                print(f"  ✗ {e}")
            continue


        if user.lower().startswith("/retry-job"):
            parts = user.split(maxsplit=1)
            if len(parts) < 2:
                print("  usage: /retry-job <job_id>")
                continue
            try:
                j = orch.jobs.retry(parts[1].strip())
                print(f"  retrying {j.id} (status={j.status.value})")
            except Exception as e:
                print(f"  ✗ {e}")
            continue

        if user.lower().startswith("/resume-job"):
            parts = user.split(maxsplit=1)
            if len(parts) < 2:
                print("  usage: /resume-job <job_id>")
                continue
            try:
                j = orch.jobs.resume(parts[1].strip())
                print(f"  resumed {j.id}")
            except Exception as e:
                print(f"  ✗ {e}")
            continue


        if user.lower() == "/traces":
            for t in orch.tracer.list_traces(15):
                print(f"  [{t.get('status')}] {t.get('id')} {t.get('duration_ms')}ms  {t.get('name')} spans={len(t.get('spans') or [])}")
            continue

        if user.lower().startswith("/trace"):
            parts = user.split(maxsplit=1)
            if len(parts) < 2:
                print("  usage: /trace <trace_id>")
                continue
            tr = orch.tracer.get_trace(parts[1].strip())
            if not tr:
                print("  (trace not found)")
            else:
                print(f"  {tr.get('id')} status={tr.get('status')} duration={tr.get('duration_ms')}ms")
                for s in tr.get("spans") or []:
                    print(f"    • [{s.get('kind')}] {s.get('name')} {s.get('duration_ms')}ms status={s.get('status')}")
                    if s.get("error"):
                        print(f"      error: {s.get('error')}")
            continue

        if user.lower() == "/metrics":
            m = orch.tracer.summary_metrics()
            print(f"  requests={m['requests']} success_rate={m['success_rate']} retries={m['retries']}")
            lat = m.get("latency_ms") or {}
            for k, v in lat.items():
                print(f"  {k}: {v} ms")
            continue


        if user.lower() == "/workflows":
            for w in orch.workflows.list_workflows():
                print(f"  {w['name']} ({w['steps']} steps) — {w['description'][:60]}")
            continue

        if user.lower().startswith("/run-workflow"):
            parts = user.split(maxsplit=2)
            if len(parts) < 2:
                print("  usage: /run-workflow <name> [key=value ...]")
                continue
            name = parts[1]
            ctx = {}
            if len(parts) > 2:
                for token in parts[2].split():
                    if "=" in token:
                        k, _, v = token.partition("=")
                        ctx[k] = v
            try:
                run = orch.workflows.start(name, context=ctx)
                print(f"  run {run.id} status={run.status.value}")
                if run.status.value == "waiting_approval":
                    print(f"  approval needed: {run.approval_message}")
                    print(f"  /approve-workflow {run.id}")
            except Exception as e:
                print(f"  ✗ {e}")
            continue

        if user.lower().startswith("/workflow-status"):
            parts = user.split(maxsplit=1)
            if len(parts) < 2:
                for r in orch.workflows.list_runs(10):
                    print(f"  [{r['status']}] {r['id']} {r['workflow_name']}")
                continue
            try:
                st = orch.workflows.status(parts[1].strip())
                print(f"  {st['id']} {st['workflow_name']} status={st['status']} step={st['current_index']}/{len(st['steps'])}")
                if st.get("approval_message"):
                    print(f"  approval: {st['approval_message']}")
            except Exception as e:
                print(f"  ✗ {e}")
            continue

        if user.lower().startswith("/cancel-workflow"):
            parts = user.split(maxsplit=1)
            if len(parts) < 2:
                print("  usage: /cancel-workflow <run_id>")
                continue
            try:
                r = orch.workflows.cancel(parts[1].strip())
                print(f"  cancelled {r.id}")
            except Exception as e:
                print(f"  ✗ {e}")
            continue

        if user.lower().startswith("/approve-workflow"):
            parts = user.split(maxsplit=1)
            if len(parts) < 2:
                print("  usage: /approve-workflow <run_id>")
                continue
            try:
                r = orch.workflows.resume(parts[1].strip(), approve=True)
                print(f"  run {r.id} status={r.status.value}")
            except Exception as e:
                print(f"  ✗ {e}")
            continue


        if user.lower() in ("/desktop", "/files") or user.lower().startswith("/files "):
            if user.lower() == "/desktop":
                r = orch.route("desktop help")
            else:
                path_arg = user.split(maxsplit=1)[1] if " " in user else "."
                r = orch.route(f"list dir {path_arg}")
            print(r.get("reply") or r)
            continue

        if user.lower() == "/workspace" or user.lower().startswith("/workspace "):
            r = orch.route(user[1:] if user.startswith("/") else user)
            print(r.get("reply") or r)
            continue

        if user.lower() == "/permissions":
            # show desktop agent policies if present
            desk = orch.agents.get("desktop")
            if desk:
                summary = desk.permissions.summary()
                print("  allowed:", ", ".join(summary["allowed"][:20]), "...")
                for a, p in sorted((summary.get("policies") or {}).items()):
                    print(f"  {a}: {p}")
            else:
                print("  (no desktop agent)")
            continue


        if user.lower() == "/browser":
            print(orch.route("browser help").get("reply"))
            continue
        if user.lower().startswith("/open "):
            url = user.split(maxsplit=1)[1]
            print(orch.route(f"open url {url}").get("reply"))
            continue
        if user.lower() == "/downloads":
            print(orch.route("downloads").get("reply"))
            continue
        if user.lower() == "/history":
            print(orch.route("browser history").get("reply"))
            continue
        if user.lower() == "/browser-permissions":
            brow = orch.agents.get("browser")
            if brow:
                for g, acts in __import__("core.browser", fromlist=["BROWSER_PERM_GROUPS"]).BROWSER_PERM_GROUPS.items():
                    print(f"  {g}: {', '.join(sorted(acts))}")
            else:
                print("  (no browser agent)")
            continue


        if user.lower() == "/connectors":
            for c in orch.connectors.list():
                print(f"  [{c['status']}] {c['name']} ({c['provider']}) caps={len(c.get('capabilities') or [])}")
            continue
        if user.lower().startswith("/connect "):
            name = user.split(maxsplit=1)[1].strip()
            # optional key=value creds
            parts = name.split()
            cname = parts[0]
            creds = {}
            for token in parts[1:]:
                if "=" in token:
                    k, _, v = token.partition("=")
                    creds[k] = v
            r = orch.connectors.connect(cname, creds or None)
            print(f"  {r.message or r.error or r}")
            continue
        if user.lower().startswith("/disconnect "):
            name = user.split(maxsplit=1)[1].strip()
            r = orch.connectors.disconnect(name)
            print(f"  {r.message or r.error}")
            continue
        if user.lower() == "/auth-status":
            st = orch.connectors.auth_status()
            for c in st["connectors"]:
                print(f"  {c['name']}: {c['status']}")
            continue
        if user.lower() == "/credentials":
            print(orch.connectors.credentials.status())
            continue


        if user.lower() in ("/media",):
            print(orch.media.status())
            continue
        if user.lower().startswith("/listen ") or user.lower().startswith("/transcribe "):
            path_arg = user.split(maxsplit=1)[1]
            r = orch.media.transcribe(path_arg)
            if r.get("ok"):
                print((r.get("transcript") or {}).get("text") or r)
            else:
                print(r.get("error") or r)
            continue
        if user.lower().startswith("/ocr "):
            r = orch.media.ocr(user.split(maxsplit=1)[1])
            print(((r.get("vision") or {}).get("text") or r.get("error") or r))
            continue
        if user.lower().startswith("/describe-image "):
            r = orch.media.describe_image(user.split(maxsplit=1)[1])
            print(((r.get("vision") or {}).get("description") or r.get("error") or r))
            continue
        if user.lower().startswith("/vision "):
            p = user.split(maxsplit=1)[1]
            o = orch.media.ocr(p)
            d = orch.media.describe_image(p)
            print("OCR:", ((o.get("vision") or {}).get("text") or o.get("error") or "")[:500])
            print("Describe:", ((d.get("vision") or {}).get("description") or "")[:500])
            continue


        if user.lower() == "/plugins":
            for p in orch.plugins.list_plugins():
                flags = []
                if p.get("enabled"): flags.append("on")
                if p.get("loaded"): flags.append("loaded")
                if p.get("error"): flags.append(f"err={p['error'][:40]}")
                print(f"  {p['name']} v{p.get('version','?')} [{', '.join(flags) or 'off'}]")
            continue
        if user.lower().startswith("/plugin-info "):
            name = user.split(maxsplit=1)[1].strip()
            try:
                info = orch.plugins.info(name)
                for k, v in info.items():
                    print(f"  {k}: {v}")
            except Exception as e:
                print(f"  ✗ {e}")
            continue
        if user.lower().startswith("/plugin-enable "):
            print(" ", orch.plugins.enable(user.split(maxsplit=1)[1].strip()))
            continue
        if user.lower().startswith("/plugin-disable "):
            print(" ", orch.plugins.disable(user.split(maxsplit=1)[1].strip()))
            continue
        if user.lower().startswith("/plugin-remove "):
            print(" ", orch.plugins.uninstall(user.split(maxsplit=1)[1].strip()))
            continue
        # plugin custom commands: /weather Johannesburg
        if user.startswith("/") and hasattr(orch, "plugin_commands"):
            cmd = user[1:].split(maxsplit=1)
            cname = cmd[0].lower()
            arg = cmd[1] if len(cmd) > 1 else ""
            if cname in orch.plugin_commands:
                try:
                    print(orch.plugin_commands[cname](arg))
                except Exception as e:
                    print(f"  ✗ {e}")
                continue


        if user.lower() == "/benchmarks":
            from evaluation.engine import EvaluationEngine
            eng = EvaluationEngine()
            for s in eng.list_suites():
                print(f"  • {s}")
            continue
        if user.lower().startswith("/evaluate"):
            from evaluation.engine import EvaluationEngine
            eng = EvaluationEngine()
            parts = user.split()[1:]
            suites = parts or None
            print("  Running evaluation...")
            report = eng.run(suites=suites, save_history=True, compare_baseline=True)
            print(eng.quality_report(report))
            print(f"  id={report.id}")
            continue
        if user.lower() == "/benchmark-history":
            from evaluation.engine import EvaluationEngine
            for row in EvaluationEngine().history(15):
                m = row.get("metrics") or {}
                print(f"  {row.get('id')} success={m.get('success_rate')} score={m.get('avg_score')}")
            continue
        if user.lower().startswith("/compare-builds "):
            from evaluation.engine import EvaluationEngine
            parts = user.split()
            if len(parts) < 3:
                print("  usage: /compare-builds <id_a> <id_b>")
                continue
            print(EvaluationEngine().compare_builds(parts[1], parts[2]))
            continue
        if user.lower() == "/quality-report":
            from evaluation.engine import EvaluationEngine
            print(EvaluationEngine().quality_report())
            continue


        if user.lower().startswith("/research ") or user.lower() == "/research":
            q = user.split(maxsplit=1)[1] if " " in user else ""
            if not q:
                print("  usage: /research <query>")
                continue
            print(orch.route(f"research {q}").get("reply"))
            continue
        if user.lower() == "/sources":
            print(orch.route("sources").get("reply"))
            continue
        if user.lower() == "/research-report":
            print(orch.route("research report").get("reply"))
            continue


        if user.lower() in ("/computer",):
            print(orch.route("computer help").get("reply"))
            continue
        if user.lower() in ("/capture-ui", "/capture ui"):
            print(orch.route("capture ui").get("reply"))
            continue
        if user.lower().startswith("/click "):
            print(orch.route("click " + user.split(maxsplit=1)[1]).get("reply"))
            continue
        if user.lower().startswith("/type "):
            print(orch.route("type " + user.split(maxsplit=1)[1]).get("reply"))
            continue


        if user.lower() in ("/inbox",):
            print(orch.route("inbox").get("reply"))
            continue
        if user.lower().startswith("/email-search "):
            print(orch.route("email search " + user.split(maxsplit=1)[1]).get("reply"))
            continue
        if user.lower().startswith("/summarize-thread "):
            print(orch.route("summarize thread " + user.split(maxsplit=1)[1]).get("reply"))
            continue
        if user.lower().startswith("/draft-email"):
            intent = user.split(maxsplit=1)[1] if " " in user else "reply"
            print(orch.route("draft email " + intent).get("reply"))
            continue


        if user.lower() in ("/calendar",):
            print(orch.route("calendar help").get("reply"))
            continue
        if user.lower() in ("/agenda",):
            print(orch.route("agenda").get("reply"))
            continue
        if user.lower().startswith("/schedule "):
            print(orch.route("schedule " + user.split(maxsplit=1)[1]).get("reply"))
            continue
        if user.lower().startswith("/free-time"):
            arg = user.split(maxsplit=1)[1] if " " in user else "today"
            print(orch.route("free time " + arg).get("reply"))
            continue
        if user.lower() in ("/reminders",):
            print(orch.route("reminders").get("reply"))
            continue


        if user.lower() in ("/voice",):
            print(orch.voice.status())
            continue
        if user.lower() in ("/mute",):
            orch.voice.mute()
            print("  muted")
            continue
        if user.lower() in ("/unmute",):
            orch.voice.unmute()
            print("  unmuted")
            continue
        if user.lower().startswith("/voice-settings"):
            arg = user.split(maxsplit=1)[1] if " " in user else ""
            if arg.startswith("wake="):
                orch.voice.settings.wake_word = arg.split("=", 1)[1].strip().strip('"')
            print(orch.voice.settings.to_dict())
            continue
        if user.lower().startswith("/listen "):
            path_arg = user.split(maxsplit=1)[1]
            r = orch.voice.process_audio(path_arg, require_wake=False)
            if r.get("ok"):
                print("  heard:", r.get("transcript"))
                print("  reply:", (r.get("reply") or "")[:500])
            else:
                print("  ✗", r.get("error") or r)
            continue


        if user.lower() in ("/memories",):
            orch.memory.sync_intelligence()
            for it in orch.memory.intelligence.list_items(limit=15):
                print(f"  [{it['importance']:.2f}] ({it['kind']}) {it['text'][:80].replace(chr(10),' ')}")
            continue
        if user.lower() in ("/memory-stats",):
            print(orch.memory.memory_stats())
            continue
        if user.lower() in ("/memory-cleanup",):
            print(orch.memory.memory_cleanup())
            continue
        if user.lower().startswith("/memory-search "):
            q = user.split(maxsplit=1)[1]
            for h in orch.memory.memory_search(q):
                print(f"  [{h['score']:.2f}] {h['text'][:100].replace(chr(10),' ')}")
            continue


        if user.lower().startswith("/collaborate "):
            body = user.split(maxsplit=1)[1]
            if "::" in body:
                agents_part, obj = body.split("::", 1)
                agents = [a.strip() for a in agents_part.split(",") if a.strip()]
            else:
                agents = ["personal", "research"]
                obj = body
            result = orch.collaboration.run(obj.strip(), agents)
            print(result.final_reply)
            print(f"  [mode={result.mode} agreement={result.agreement} rounds={result.rounds}]")
            continue
        if user.lower().startswith("/review "):
            obj = user.split(maxsplit=1)[1]
            result = orch.collaboration.run(obj, ["personal"], mode="reviewer", reviewer="reviewer")
            print(result.final_reply)
            if result.reviews:
                print(f"  [score={result.reviews[-1].score} rounds={result.rounds}]")
            continue
        if user.lower().startswith("/consensus "):
            body = user.split(maxsplit=1)[1]
            if "::" in body:
                agents_part, obj = body.split("::", 1)
                agents = [a.strip() for a in agents_part.split(",") if a.strip()]
            else:
                agents = ["personal", "research"]
                obj = body
            result = orch.collaboration.run(obj.strip(), agents, mode="consensus")
            print(result.final_reply)
            print(f"  [agreement={result.agreement}]")
            continue


        if user.lower() == "/goals" or user.lower().startswith("/goals "):
            st = user.split(maxsplit=1)[1] if " " in user else None
            for g in orch.goals.list_goals(status=st):
                print(f"  [{g.status.value}] {g.id} {int(g.progress*100)}% — {g.title[:50]}")
            continue
        if user.lower().startswith("/goal-create "):
            obj = user.split(maxsplit=1)[1]
            g = orch.goals.create(obj)
            print(f"  created {g.id} status={g.status.value} progress={g.progress}")
            print(orch.goals.status_report(g.id)[:1200])
            continue
        if user.lower().startswith("/goal-status "):
            print(orch.goals.status_report(user.split(maxsplit=1)[1].strip()))
            continue
        if user.lower().startswith("/goal "):
            print(orch.goals.status_report(user.split(maxsplit=1)[1].strip()))
            continue
        if user.lower().startswith("/pause-goal "):
            g = orch.goals.pause(user.split(maxsplit=1)[1].strip())
            print(f"  paused {g.id}")
            continue
        if user.lower().startswith("/resume-goal "):
            g = orch.goals.resume(user.split(maxsplit=1)[1].strip())
            print(f"  resumed {g.id} status={g.status.value}")
            continue
        if user.lower().startswith("/cancel-goal "):
            g = orch.goals.cancel(user.split(maxsplit=1)[1].strip())
            print(f"  cancelled {g.id}")
            continue


        if user.lower() in ("/learning",):
            print(orch.learning.status())
            continue
        if user.lower() in ("/learning-report",):
            # refresh analysis then report
            orch.learning.analyze()
            print(orch.learning.report())
            continue
        if user.lower() == "/recommendations" or user.lower().startswith("/recommendations "):
            cat = user.split(maxsplit=1)[1] if " " in user else None
            for r in orch.learning.list_recommendations(category=cat):
                flag = "applied" if r.get("applied") else "open"
                print(f"  [{r['category']}] ({r['confidence']:.2f}) {r['title']} ({flag})")
                print(f"      {r['detail'][:120]}")
            continue
        if user.lower() in ("/optimization-history",):
            for h in orch.learning.optimization_history(20):
                print(f"  {h}")
            continue


        if user.lower() in ("/workers",):
            for w in orch.workers.list_workers():
                print(f"  [{w['status']}] {w['id']} {w['name']} load={w['load']:.2f} caps={w['capabilities']}")
            print("  metrics:", orch.workers.metrics_snapshot())
            continue
        if user.lower().startswith("/worker-status "):
            print(orch.workers.worker_status(user.split(maxsplit=1)[1].strip()))
            continue
        if user.lower().startswith("/drain-worker "):
            print(orch.workers.drain(user.split(maxsplit=1)[1].strip()).to_dict())
            continue
        if user.lower().startswith("/enable-worker "):
            print(orch.workers.enable(user.split(maxsplit=1)[1].strip()).to_dict())
            continue
        if user.lower().startswith("/disable-worker "):
            print(orch.workers.disable(user.split(maxsplit=1)[1].strip()).to_dict())
            continue


        if user.lower() == "/config":
            from core.config import get_config
            print(get_config().as_dict())
            continue
        if user.lower() == "/diagnostics":
            from core.ops import diagnostics
            print(diagnostics(orch))
            continue
        if user.lower() == "/backup" or user.lower().startswith("/backup "):
            label = user.split(maxsplit=1)[1] if " " in user else "manual"
            print(orch.backups.create(label=label))
            continue


        if user.lower() in ("/n8n",):
            try:
                r = orch.connectors.execute("n8n", "status")
                print(r.to_dict() if hasattr(r, "to_dict") else r)
            except Exception as e:
                print("  n8n:", e)
            continue
        if user.lower() in ("/n8n-workflows",):
            try:
                r = orch.connectors.execute("n8n", "list_workflows")
                print(r.to_dict() if hasattr(r, "to_dict") else r)
            except Exception as e:
                print("  n8n:", e)
            continue
        if user.lower().startswith("/run-n8n "):
            wid = user.split(maxsplit=1)[1].strip()
            try:
                r = orch.connectors.execute("n8n", "execute_workflow", workflow_id=wid)
                print(r.to_dict() if hasattr(r, "to_dict") else r)
            except Exception as e:
                print("  n8n:", e)
            continue
        if user.lower().startswith("/n8n-status "):
            eid = user.split(maxsplit=1)[1].strip()
            try:
                r = orch.connectors.execute("n8n", "get_execution_status", execution_id=eid)
                print(r.to_dict() if hasattr(r, "to_dict") else r)
            except Exception as e:
                print("  n8n:", e)
            continue


        if user.lower() in ("/self-improve",):
            print(orch.self_improve.run_cycle())
            continue
        if user.lower() in ("/improvement-report",):
            print(orch.self_improve.report())
            continue
        if user.lower() in ("/improvement-history",):
            for h in orch.self_improve.list_history(20):
                print(" ", h)
            continue
        if user.lower().startswith("/rollback-improvement "):
            pid = user.split(maxsplit=1)[1].strip()
            print(orch.self_improve.rollback(pid).to_dict())
            continue
        if user.lower().startswith("/approve-improvement "):
            pid = user.split(maxsplit=1)[1].strip()
            p = orch.self_improve.approve(pid)
            print(p.to_dict())
            # does not auto-deploy — user must confirm separately via deploy if desired
            continue


        if user.lower() in ("/beta-keys",):
            from core.beta import BetaManager
            from core.config import get_config
            bm = BetaManager(persist_dir=__import__("pathlib").Path(str(get_config().get("data_dir"))) / "beta")
            print(bm.stats())
            for k in bm.list_keys()[:30]:
                print(f"  {k['code']}  {k['status']}  {k.get('bound_account') or ''}")
            continue
        if user.lower().startswith("/beta-create "):
            from core.beta import BetaManager
            from core.config import get_config
            n = int(user.split(maxsplit=1)[1])
            bm = BetaManager(persist_dir=__import__("pathlib").Path(str(get_config().get("data_dir"))) / "beta")
            keys = bm.create_keys(n, label_prefix="cli-")
            for k in keys:
                print(k.code)
            continue
        if user.lower().startswith("/beta-revoke "):
            from core.beta import BetaManager
            from core.config import get_config
            kid = user.split(maxsplit=1)[1].strip()
            bm = BetaManager(persist_dir=__import__("pathlib").Path(str(get_config().get("data_dir"))) / "beta")
            print(bm.revoke(kid).to_dict())
            continue


        if user.lower() in ("/quant-shadow", "/shadow-status"):
            print("  Shadow engine: use quant.ShadowEngine in code/API.")
            print("  Hard constraint: no real orders, no broker trading credentials.")
            print("  Commands: /shadow-status /shadow-report <id>")
            continue
        if user.lower().startswith("/shadow-report "):
            print("  Load trial via ShadowEngine.report(id) in Python.")
            continue


        if user.lower() in ("/quant", "/quant-status"):
            try:
                from core.connectors import build_default_connectors
                reg = build_default_connectors()
                r = reg.execute("quant", "quant_dashboard")
                if r.ok and isinstance(r.data, dict) and r.data.get("text"):
                    print(r.data["text"])
                else:
                    print(" ", r.to_dict() if hasattr(r, "to_dict") else r)
            except Exception as e:
                print("  quant error:", e)
            continue
        if user.lower().startswith("/quant-candidate "):
            try:
                eid = user.split(None, 1)[1].strip()
                from core.connectors import build_default_connectors
                reg = build_default_connectors()
                r = reg.execute("quant", "quant_candidate", experiment_id=eid)
                print((r.data or {}).get("text") or r.to_dict())
            except Exception as e:
                print("  quant error:", e)
            continue
        if user.lower().startswith("/quant-hypothesis "):
            try:
                hid = user.split(None, 1)[1].strip()
                from core.connectors import build_default_connectors
                reg = build_default_connectors()
                r = reg.execute("quant", "quant_lineage", hypothesis_id=hid)
                print((r.data or {}).get("text") or r.to_dict())
            except Exception as e:
                print("  quant error:", e)
            continue
        if user.lower() == "/quant-candidates":
            try:
                from core.connectors import build_default_connectors
                reg = build_default_connectors()
                reg.execute("quant", "quant_status")  # ensure connect
                r = reg.execute("quant", "quant_candidates")
                print(" ", r.to_dict() if hasattr(r, "to_dict") else r)
            except Exception as e:
                print("  quant error:", e)
            continue
        if user.lower() == "/quant-hypotheses":
            try:
                from core.connectors import build_default_connectors
                reg = build_default_connectors()
                r = reg.execute("quant", "quant_hypotheses")
                print(" ", r.to_dict() if hasattr(r, "to_dict") else r)
            except Exception as e:
                print("  quant error:", e)
            continue
        if user.lower() == "/quant-review":
            try:
                from core.connectors import build_default_connectors
                reg = build_default_connectors()
                r = reg.execute("quant", "quant_review")
                print(" ", r.to_dict() if hasattr(r, "to_dict") else r)
            except Exception as e:
                print("  quant error:", e)
            continue

        if user.lower() == "/tasks":
            for t in orch.recent_tasks():
                print(f"  [{t['status']}] {t['id']} → {t.get('assigned_agent')} | {t['objective'][:60]}")
            continue

        if user.lower() == "/events":
            for e in orch.recent_events(15):
                print(f"  {e['type']:18} src={e['source']:10} {e['payload']}")
            continue

        if user.lower() == "/planner":
            print(orch.planner_memory.summary())
            for d in orch.planner_memory.recent(5):
                print(f"  → {d['chosen_agent']:10} ok={d['success']} | {d['objective'][:50]}")
            continue

        if user.lower() == "/clear":
            orch.memory.clear_session()
            print("  Working memory cleared.")
            continue

        chunks: list = []

        def _on_token(tok: str) -> None:
            chunks.append(tok)
            print(tok, end="", flush=True)

        agent_hint = ""
        print("  pear › ", end="", flush=True)
        result = orch.route(user, on_token=_on_token)
        reply = result.get("reply") or result.get("error") or str(result)
        agent = result.get("agent", "?")
        task_id = result.get("task_id", "")
        if chunks:
            print()  # finish streamed line
            print(f"  ({agent}) [{task_id}]\n")
        else:
            print()
            print(f"pear ({agent}) [{task_id}] › {reply}\n")


if __name__ == "__main__":
    main()
