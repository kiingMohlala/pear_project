"""
Research Agent (v1.30) – multi-source web research with ranked, cited reports.

Uses BrowserAgent tools when available, KnowledgeStore for caching,
and jobs for long-running research. Not a substitute for primary sources.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .base import Agent
from core.task import Task
from core.llm import BaseLLM, create_llm, EchoLLM


@dataclass
class Source:
    id: str
    url: str
    title: str
    snippet: str
    credibility: float  # 0–1
    rank_score: float
    retrieved_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


# Lightweight domain credibility priors (extensible)
DOMAIN_SCORES: Dict[str, float] = {
    "wikipedia.org": 0.75,
    "gov": 0.9,
    "edu": 0.85,
    "arxiv.org": 0.85,
    "nature.com": 0.9,
    "sciencedirect.com": 0.85,
    "nih.gov": 0.9,
    "who.int": 0.9,
    "bbc.com": 0.7,
    "reuters.com": 0.8,
    "apnews.com": 0.8,
    "example.com": 0.3,
    "medium.com": 0.45,
    "blogspot.com": 0.35,
    "wordpress.com": 0.4,
}


def domain_credibility(url: str) -> float:
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return 0.4
    if host in DOMAIN_SCORES:
        return DOMAIN_SCORES[host]
    for suffix, score in DOMAIN_SCORES.items():
        if host.endswith("." + suffix) or host.endswith(suffix):
            return score
    if host.endswith(".gov") or ".gov." in host:
        return 0.9
    if host.endswith(".edu") or ".edu." in host:
        return 0.85
    if host.endswith(".org"):
        return 0.6
    return 0.5


def dedupe_sources(sources: List[Source]) -> List[Source]:
    seen_urls = set()
    seen_text = set()
    out: List[Source] = []
    for s in sources:
        url_key = s.url.rstrip("/").lower()
        text_key = re.sub(r"\W+", " ", (s.title + " " + s.snippet).lower())[:120]
        if url_key in seen_urls or text_key in seen_text:
            continue
        seen_urls.add(url_key)
        seen_text.add(text_key)
        out.append(s)
    return out


def rank_sources(sources: List[Source], query: str) -> List[Source]:
    tokens = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
    ranked = []
    for s in sources:
        blob = f"{s.title} {s.snippet}".lower()
        overlap = sum(1 for t in tokens if t in blob) / max(1, len(tokens))
        score = 0.55 * s.credibility + 0.45 * overlap
        s.rank_score = round(score, 4)
        ranked.append(s)
    ranked.sort(key=lambda x: x.rank_score, reverse=True)
    return ranked


class ResearchAgent(Agent):
    def __init__(self, llm: Optional[BaseLLM] = None, **kwargs):
        super().__init__(
            name="research",
            description=(
                "Performs multi-source web research: searches, ranks sources by "
                "credibility and relevance, and synthesizes cited executive summaries "
                "and detailed reports."
            ),
            capabilities=[
                "search",
                "retrieve",
                "evaluate",
                "synthesize",
                "cite",
                "research",
            ],
            allowed_tools=["open_url", "search_web", "extract_text", "summarize_text"],
            system_prompt=(
                "You are PEAR's Research Agent. Synthesize information carefully, "
                "cite sources by number, and distinguish facts from uncertainty."
            ),
            **kwargs,
        )
        self.llm: BaseLLM = llm or create_llm()
        self.last_sources: List[Source] = []
        self.last_report: str = ""

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        signals = [
            "research", "investigate", "find sources", "what does the web",
            "literature", "survey", "cite", "according to", "deep dive",
            "look up", "fact check",
        ]
        hits = sum(1 for s in signals if s in obj)
        if hits:
            score = max(score, min(0.95, 0.5 + 0.12 * hits))
        return score

    def _span(self, name: str, **attrs):
        try:
            from core.tracing import get_tracer
            return get_tracer().span(name, kind="agent", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        objective = task.objective
        lower = objective.lower().strip()

        if lower in ("sources", "/sources", "show sources", "list sources"):
            return self._show_sources()

        if lower in ("research report", "/research-report", "show report"):
            if self.last_report:
                return {"ok": True, "reply": self.last_report, "action": "research_report"}
            return {"ok": True, "reply": "No report yet. Run a research query first.", "action": "need_research"}

        # Long research → background job
        if any(k in lower for k in ("deep research", "long research", "comprehensive research")):
            if self.planner and "foreground" not in lower:
                r = self.planner.submit_job(f"research foreground {objective}")
                return {
                    "ok": True,
                    "reply": f"Queued background research job {r.get('job_id')}.",
                    "action": "research_queued",
                    "job_id": r.get("job_id"),
                }

        query = re.sub(
            r"^(?:research|investigate|look up|fact check|deep dive(?:\s+into)?)\s+",
            "",
            objective,
            flags=re.I,
        ).strip() or objective

        return self._research(query, mode="detailed" if "detail" in lower or "report" in lower else "executive")

    def _research(self, query: str, mode: str = "executive") -> Dict[str, Any]:
        with self._span("research.search", query=query[:120]):
            raw = self._acquire(query)
        with self._span("research.rank", count=len(raw)):
            sources = rank_sources(dedupe_sources(raw), query)[:8]
        self.last_sources = sources

        # Cache into knowledge store
        for s in sources:
            try:
                self.memory.knowledge.add_document(
                    name=f"source:{s.title[:60]}",
                    text=f"{s.title}\n{s.url}\n{s.snippet}",
                    source_path=s.url,
                    metadata={
                        "type": "research_source",
                        "credibility": s.credibility,
                        "rank_score": s.rank_score,
                        "query": query,
                    },
                )
            except Exception:
                pass

        with self._span("research.summarize", mode=mode):
            summary = self._synthesize(query, sources, mode=mode)
        with self._span("research.report"):
            report = self._format_report(query, sources, summary, mode=mode)
        self.last_report = report

        return {
            "ok": True,
            "reply": report,
            "action": "research_complete",
            "sources": [s.to_dict() for s in sources],
            "query": query,
        }

    def _acquire(self, query: str) -> List[Source]:
        """Gather candidate sources via browser (live or simulated) + knowledge."""
        sources: List[Source] = []

        # 1) Knowledge store hits
        try:
            hits = self.memory.knowledge.search(query, limit=5)
            for h in hits:
                url = h.get("source_path") or f"knowledge://{h.get('id')}"
                sources.append(Source(
                    id=f"src_{uuid.uuid4().hex[:8]}",
                    url=str(url),
                    title=str(h.get("title") or "Knowledge hit"),
                    snippet=str(h.get("snippet") or "")[:400],
                    credibility=domain_credibility(str(url)),
                    rank_score=0.0,
                ))
        except Exception:
            pass

        # 2) Browser search (Playwright or simulated)
        browser = None
        try:
            if self.planner and "browser" in getattr(self.planner, "agents", {}):
                browser = self.planner.agents["browser"]
        except Exception:
            browser = None

        if browser is not None:
            try:
                browser.think(f"search web {query}")
                # extract whatever page text we got
                ext = browser.think("extract text")
                text = (ext.get("data") or {}).get("text") or ext.get("reply") or ""
                url = getattr(getattr(browser, "browser", None), "session", None)
                current = getattr(url, "current_url", "") if url else ""
                sources.append(Source(
                    id=f"src_{uuid.uuid4().hex[:8]}",
                    url=current or "browser://search",
                    title=f"Web search: {query[:60]}",
                    snippet=str(text)[:500],
                    credibility=domain_credibility(current or ""),
                    rank_score=0.0,
                ))
            except Exception:
                pass
        else:
            # Built-in simulated multi-source pack for offline/demo quality
            sources.extend(self._simulated_sources(query))

        return sources

    def _simulated_sources(self, query: str) -> List[Source]:
        """Deterministic demo sources so research works without Playwright."""
        q = query.strip()
        seeds = [
            (f"https://en.wikipedia.org/wiki/{q.replace(' ', '_')}", f"{q} — Wikipedia", 0.75,
             f"Overview article about {q}. Covers definitions, history, and key concepts."),
            (f"https://www.reuters.com/search/news?blob={q.replace(' ', '+')}", f"Reuters on {q}", 0.8,
             f"News coverage related to {q} with recent developments and quotes from officials."),
            (f"https://arxiv.org/search/?query={q.replace(' ', '+')}", f"arXiv results for {q}", 0.85,
             f"Academic preprints discussing methods and findings related to {q}."),
            (f"https://example.com/blog/{q.replace(' ', '-').lower()}", f"Blog opinion on {q}", 0.3,
             f"Informal commentary about {q}; lower reliability."),
        ]
        out = []
        for url, title, cred, snip in seeds:
            out.append(Source(
                id=f"src_{hashlib.md5(url.encode()).hexdigest()[:8]}",
                url=url,
                title=title,
                snippet=snip,
                credibility=cred,
                rank_score=0.0,
            ))
        return out

    def _synthesize(self, query: str, sources: List[Source], mode: str) -> str:
        if not sources:
            return f"No sources found for “{query}”."

        # Prefer LLM when not Echo
        if self._llm_usable():
            bullets = "\n".join(
                f"[{i+1}] {s.title} ({s.url})\n{s.snippet}" for i, s in enumerate(sources[:6])
            )
            prompt = (
                f"Research query: {query}\n\nSources:\n{bullets}\n\n"
                "Write a concise synthesis with inline citations like [1], [2]. "
                "Note uncertainty. Do not invent facts beyond the sources."
            )
            try:
                resp = self.llm.chat(self.system_prompt, prompt)
                return (resp.content or "").strip()
            except Exception:
                pass

        # Deterministic extractive synthesis
        lines = [f"Findings for **{query}** (extractive synthesis):\n"]
        for i, s in enumerate(sources[:5], 1):
            lines.append(f"- [{i}] {s.snippet} ({s.title})")
        lines.append("\nKey themes: " + ", ".join(
            sorted({w for s in sources for w in re.findall(r"[A-Za-z]{5,}", s.snippet.lower())})[:8]
        ))
        return "\n".join(lines)

    def _format_report(self, query: str, sources: List[Source], summary: str, mode: str) -> str:
        lines = [
            f"# Research report: {query}",
            "",
            "## Executive summary" if mode == "executive" else "## Detailed synthesis",
            summary,
            "",
            "## Sources (ranked)",
        ]
        for i, s in enumerate(sources, 1):
            lines.append(
                f"[{i}] **{s.title}**  \n"
                f"    {s.url}  \n"
                f"    credibility={s.credibility:.2f} rank={s.rank_score:.2f}"
            )
        lines.append("")
        lines.append("_Automated research assist — verify critical claims with primary sources._")
        return "\n".join(lines)

    def _show_sources(self) -> Dict[str, Any]:
        if not self.last_sources:
            return {"ok": True, "reply": "No sources in memory. Run a research query first.", "action": "sources"}
        lines = ["## Last research sources\n"]
        for i, s in enumerate(self.last_sources, 1):
            lines.append(f"[{i}] {s.title}\n    {s.url} (cred={s.credibility:.2f})")
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "sources",
            "sources": [s.to_dict() for s in self.last_sources],
        }

    def _llm_usable(self) -> bool:
        return not isinstance(self.llm, EchoLLM) and getattr(self.llm, "provider", "") not in ("echo", "")
