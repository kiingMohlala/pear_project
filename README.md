# PEAR 3.0.0-rc1

Personal AI platform: multi-agent planning, memory, goals, workers, and multi-user API.

## Quick start

```bash
pip install -r requirements.txt
python -m ui.app
# or
python -m service.app
```

Docs: `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md`, `docs/USER_GUIDE.md`, `docs/RELEASE_CHECKLIST.md`


# PEAR – Personal Agent Runtime

Lightweight multi-agent personal assistant scaffold.

## Capabilities (v0.2)

1. **Chat** – LLM-backed conversation (Ollama by default)
2. **Notes** – `note: buy milk` / `list notes`
3. **File Reading + Retrieval** – upload PDF/DOCX, ask questions about them
4. **Desktop Tasks** – open apps, open folders, search files
5. **Swappable LLM** – Ollama / OpenAI / Anthropic via `core/llm.py`

## Architecture

```
Agent (base)
 ├── PersonalAgent
 ├── DesktopAgent
 ├── LegalAgent      (stub → v0.3)
 └── FinanceAgent    (stub → v0.4)

Orchestrator → routes by capability / keywords
Memory       → session history + notes
Tools        → shared file & OS helpers
Permissions  → simple action gates
```

## Quick start

```bash
cd PEAR
pip install -r requirements.txt
python -m ui.app
```

### Example commands

```
you › hello
you › note: call the lawyer tomorrow
you › list notes
you › /file ~/Documents/contract.pdf
you › open app calculator
you › open folder ~/Downloads
you › search files *.pdf in ~/Documents
```

## Roadmap

See `docs/roadmap.md`.


## LLM setup

```bash
# Default: Ollama (local)
ollama serve
ollama pull llama3.2

# Or cloud:
export PEAR_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...
export PEAR_LLM_MODEL=gpt-4o-mini
```

Env vars: `PEAR_LLM_PROVIDER`, `PEAR_LLM_MODEL`, `OLLAMA_HOST`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.
