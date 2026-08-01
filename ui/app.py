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
from core.tools import read_document, summarize_text
from core.llm import create_llm
from agents import PersonalAgent, DesktopAgent, FinanceAgent, LegalAgent


def build_pear() -> Orchestrator:
    memory = Memory(session_id="cli", persist_dir=ROOT / "data")
    llm = create_llm()
    orch = Orchestrator(memory=memory, llm=llm)

    orch.register(PersonalAgent(llm=llm), default=True)
    orch.register(DesktopAgent())
    orch.register(FinanceAgent())
    orch.register(LegalAgent())

    return orch


def handle_file_upload(orch: Orchestrator, path_str: str) -> None:
    path = Path(path_str).expanduser()
    if not path.exists():
        print(f"  ✗ File not found: {path}")
        return
    try:
        text = read_document(path)
        summary = summarize_text(text)
        orch.memory.knowledge.add_document(
            name=path.name,
            text=text,
            source_path=str(path),
        )
        orch.memory._save()
        print(f"\n  📄 {path.name} loaded ({len(text)} chars) → knowledge store")
        print("  ── Summary ──")
        print(summary)
        print()
    except Exception as e:
        print(f"  ✗ Failed to read document: {e}")


def print_banner():
    print(
        """
╔══════════════════════════════════════╗
║              P E A R                 ║
║     Personal Agent Runtime           ║
║              v0.1                    ║
╚══════════════════════════════════════╝
  Type a message, or:
    /file <path>   – upload & summarize PDF/DOCX
    /notes         – list saved notes
    /agents        – agents + capabilities + tools
    /tools         – tool registry
    /llm           – show active LLM provider / model
    /tasks         – recent tasks
    /events        – recent event stream
    /planner       – planner memory summary
    /clear         – clear working memory
    /quit          – exit
"""
    )


def main() -> None:
    orch = build_pear()
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

        result = orch.route(user)
        reply = result.get("reply") or result.get("error") or str(result)
        agent = result.get("agent", "?")
        task_id = result.get("task_id", "")
        print(f"\npear ({agent}) [{task_id}] › {reply}\n")


if __name__ == "__main__":
    main()
