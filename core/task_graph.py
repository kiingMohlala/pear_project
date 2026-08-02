"""
Task graph (DAG) for multi-step plans.

Designed so parallel execution can be added later without redesign:
ready_tasks() already returns the full ready set; v0.22 runs one at a time.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class NodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass
class TaskNode:
    objective: str
    id: str = field(default_factory=lambda: f"node_{uuid.uuid4().hex[:10]}")
    assigned_agent: Optional[str] = None
    required_capabilities: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)  # node ids that must complete first
    parent: Optional[str] = None
    children: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class CycleError(ValueError):
    pass


class TaskGraph:
    """Directed acyclic graph of TaskNodes."""

    def __init__(self, plan_id: Optional[str] = None, summary: str = ""):
        self.plan_id = plan_id or f"plan_{uuid.uuid4().hex[:10]}"
        self.summary = summary
        self.nodes: Dict[str, TaskNode] = {}
        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self.metadata: Dict[str, Any] = {}

    # ── mutations ─────────────────────────────────────────────────

    def add_node(self, node: TaskNode) -> TaskNode:
        if node.id in self.nodes:
            raise ValueError(f"Node already exists: {node.id}")
        self.nodes[node.id] = node
        if node.parent and node.parent in self.nodes:
            parent = self.nodes[node.parent]
            if node.id not in parent.children:
                parent.children.append(node.id)
        return node

    def add_dependency(self, node_id: str, depends_on: str) -> None:
        """node_id depends on depends_on (depends_on must finish first)."""
        if node_id not in self.nodes or depends_on not in self.nodes:
            raise KeyError(f"Unknown node in dependency: {node_id} → {depends_on}")
        if node_id == depends_on:
            raise CycleError("Node cannot depend on itself")
        node = self.nodes[node_id]
        if depends_on not in node.dependencies:
            node.dependencies.append(depends_on)
        # Cycle check
        if self._would_cycle(node_id, depends_on):
            node.dependencies.remove(depends_on)
            raise CycleError(f"Adding {node_id} → {depends_on} would create a cycle")

    def _would_cycle(self, from_id: str, to_id: str) -> bool:
        """True if to_id can reach from_id following dependency edges."""
        # dependencies point to prerequisites; walk from to_id following deps
        seen: Set[str] = set()
        stack = [to_id]
        while stack:
            cur = stack.pop()
            if cur == from_id:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            n = self.nodes.get(cur)
            if n:
                stack.extend(n.dependencies)
        return False

    # ── queries ───────────────────────────────────────────────────

    def ready_tasks(self) -> List[TaskNode]:
        """Nodes whose dependencies are all completed and status is pending/ready."""
        ready = []
        for node in self.nodes.values():
            if node.status not in (NodeStatus.PENDING, NodeStatus.READY):
                continue
            deps_ok = all(
                self.nodes[d].status == NodeStatus.COMPLETED
                for d in node.dependencies
                if d in self.nodes
            )
            if deps_ok:
                node.status = NodeStatus.READY
                ready.append(node)
        return ready

    def completed(self) -> bool:
        if not self.nodes:
            return True
        return all(
            n.status in (NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.CANCELLED, NodeStatus.SKIPPED)
            for n in self.nodes.values()
        )

    def failed(self) -> bool:
        return any(n.status == NodeStatus.FAILED for n in self.nodes.values())

    def succeeded(self) -> bool:
        return self.completed() and not self.failed()

    def execution_order(self) -> List[TaskNode]:
        """Topological order (Kahn). Raises CycleError if cyclic."""
        in_degree = {nid: 0 for nid in self.nodes}
        for node in self.nodes.values():
            for dep in node.dependencies:
                if dep in in_degree:
                    # edge dep → node means dep must come first; in_degree of node increases
                    in_degree[node.id] = in_degree.get(node.id, 0) + 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        order: List[TaskNode] = []
        while queue:
            # deterministic: sort by id
            queue.sort()
            nid = queue.pop(0)
            order.append(self.nodes[nid])
            for other in self.nodes.values():
                if nid in other.dependencies:
                    in_degree[other.id] -= 1
                    if in_degree[other.id] == 0:
                        queue.append(other.id)

        if len(order) != len(self.nodes):
            raise CycleError("TaskGraph contains a cycle")
        return order

    def mark_running(self, node_id: str) -> None:
        node = self.nodes[node_id]
        node.status = NodeStatus.RUNNING
        node.started_at = time.time()

    def mark_completed(self, node_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        node = self.nodes[node_id]
        node.status = NodeStatus.COMPLETED
        node.completed_at = time.time()
        node.result = result

    def mark_failed(self, node_id: str, error: str) -> None:
        node = self.nodes[node_id]
        node.status = NodeStatus.FAILED
        node.completed_at = time.time()
        node.error = error

    def results(self) -> List[Dict[str, Any]]:
        """Results in topological order."""
        try:
            ordered = self.execution_order()
        except CycleError:
            ordered = list(self.nodes.values())
        out = []
        for n in ordered:
            out.append({
                "id": n.id,
                "objective": n.objective,
                "agent": n.assigned_agent,
                "status": n.status.value,
                "result": n.result,
                "error": n.error,
            })
        return out

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "summary": self.summary,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "completed": self.completed(),
            "failed": self.failed(),
        }

    def __repr__(self) -> str:
        return f"<TaskGraph {self.plan_id} nodes={len(self.nodes)} summary={self.summary!r}>"
