# Tutorial: Your first agent

1. Subclass `agents.base.Agent`
2. Set `name`, `description`, `capabilities`
3. Implement `_process(self, task)`
4. Register with the orchestrator
5. Prefer tools via Tool Registry — do not call other agents

```python
from agents.base import Agent
from core.task import Task

class HelloAgent(Agent):
    def __init__(self):
        super().__init__(
            name="hello",
            description="Says hello",
            capabilities=["greeting"],
        )
    def _process(self, task: Task, **kwargs):
        return {"ok": True, "reply": f"Hello! You said: {task.objective}"}
```
