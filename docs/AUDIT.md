# PEAR Audit Log

Running punch-list from the module-by-module pass. `docs/roadmap.md` says what
was *intended*; this says what's actually been verified by running the code,
not just reading it. Status is only set to Fixed once it's been executed and
checked, not once code has been written.

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

## 🔴 `ui/dashboard.py` — AttributeError on `mem.messages` / `mem.notes`

Fixed in an earlier session, then reverted when v2.30 merged over the older
base. `Memory` only exposes `mem.working.messages` / `mem.knowledge.notes`;
`dashboard.py` reads `mem.messages` / `mem.notes` directly.

```
AttributeError: 'Memory' object has no attribute 'messages'
```

Needs the same two-line fix as before. Not yet re-applied to this tree.

---

## 🔴 `ui/app.py` file upload bypasses agents/registry entirely

Also reverted. `handle_file_upload()` imports `read_document`/`summarize_text`
from `core.tools` and calls them directly — no agent, no permission check, no
audit trail. Contradicts the "agents create actions, not raw tool calls"
principle from the Action-layer work. That whole layer (`core/action.py`) is
absent from this tree — needs a decision: rebuild it against the current
`core/executor.py`/`core/task_graph.py`, or confirm those already cover the
same ground and the Action layer is redundant.

---

## 🟡 6 failing tests — not yet root-caused

```
test_eval_v120.py::test_isolation_temp_state              TypeError
test_media_v100.py::test_media_manager_indexes_knowledge   TypeError
test_plugins_v110.py::test_discover_builtin_plugins        AssertionError
test_plugins_v110.py::test_load_weather_registers_tool_and_command
test_plugins_v110.py::test_disable_enable                  KeyError: unknown plugin 'notion'
test_plugins_v110.py::test_notion_registers_connector_when_enabled
```

The plugin ones look related — `PluginManager.discover()` isn't finding the
`notion` plugin at all, which would explain all four. Not dug into yet.

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

---

## Known design footgun to watch for elsewhere

`ToolRegistry.call(name, *args, **kwargs)` uses `name` as its own parameter —
collides with any tool whose own signature also takes a `name` argument
(bit `open_application(name: str)`). Worth grepping for other tools with a
`name` param before wiring anything else through `registry.call()` by keyword.
