# PEAR Roadmap

## v0.1
- Chat (session memory)
- Notes
- File reading (PDF / DOCX summarization)
- Desktop control (open apps, folders, search files)
- Generic Agent base + Task model
- Tool Registry, Events, Planner memory

## v0.2 (current)
- LLM abstraction (`BaseLLM`, Ollama, OpenAI, Anthropic)
- Ollama integration (default local provider)
- PersonalAgent uses LLM for chat
- KnowledgeStore retrieval → grounded answers

## v0.21
- Better document retrieval (chunking / embeddings)
- Improved memory search

## v0.22
- LLM-based planner (replace score heuristics)
- Multi-step task decomposition via parent/child Tasks

## v0.23
- Streaming responses
- Conversation improvements

## v0.3
- Real Legal Agent (document review)

## v0.4
- Finance analysis

## v0.5
- Multi-agent orchestration polish

---

## Design Principles
- Generic `Agent` base class with description + capabilities
- Central Tool Registry (agents request tools, do not own them)
- Tasks with parent/child support
- Events for observability
- Agents never call each other – only via Planner
- LLM provider is swappable; agents depend on `BaseLLM` only
