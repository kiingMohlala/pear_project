"""
PEAR Quant Connector (v0.9) — research-only bridge to the independent Quant Lab.

Hard boundary:
  PEAR may call research / reports / hypotheses / shadow *status*
  PEAR must NEVER place real orders, allocate capital, or mutate frozen candidates.
  No broker trading credentials are accepted or stored.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus


# Explicit denylist — connector refuses these action names
FORBIDDEN_ACTIONS = frozenset({
    "order", "place_order", "buy", "sell", "trade", "live_trade",
    "allocate", "capital", "broker", "withdraw", "deposit",
    "modify_frozen", "unfreeze", "set_live",
})


class QuantConnector(Connector):
    name = "quant"
    description = (
        "Independent Quant Research Lab — research, hypotheses, reviews, reports. "
        "No real orders or capital allocation."
    )
    provider = "pear_quant_lab"
    capabilities = [
        ConnectorCapability("quant_research", "Run research experiment on a strategy", "quant_research"),
        ConnectorCapability("quant_status", "Lab / memory / hypothesis status", "quant_read"),
        ConnectorCapability("quant_candidates", "List recent candidates from memory", "quant_read"),
        ConnectorCapability("quant_hypotheses", "List or generate evidence-based hypotheses", "quant_research"),
        ConnectorCapability("quant_review", "Independent review + research decision", "quant_research"),
        ConnectorCapability("quant_report", "Human-readable research report", "quant_read"),
        ConnectorCapability("quant_market_summary", "Market summary from research memory", "quant_read"),
        ConnectorCapability("quant_failure_patterns", "Aggregated failure patterns", "quant_read"),
        ConnectorCapability("quant_lineage", "Hypothesis/candidate/experiment lineage", "quant_read"),
        ConnectorCapability("quant_shadow_status", "Shadow engine status (zero orders)", "quant_read"),
        ConnectorCapability("quant_dashboard", "Operator dashboard snapshot", "quant_read"),
        ConnectorCapability("quant_candidate", "Candidate lifecycle view", "quant_read"),
        ConnectorCapability("quant_decision", "Decision explanation", "quant_read"),
    ]

    def __init__(self, data_dir: Optional[Path] = None):
        super().__init__()
        self.data_dir = Path(data_dir) if data_dir else Path.home() / ".pear" / "quant_connector"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lab = None
        self._hyp_engine = None
        self._board = None
        self._trace_hooks: List = []

    def _span(self, name: str, **meta):
        """Optional tracing integration — inherits PEAR trace when tracer present."""
        try:
            from core.tracing import get_tracer
            tr = get_tracer()
            if tr is not None:
                return tr.span(f"quant.{name}", **meta)
        except Exception:
            pass
        return None

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        # Refuse broker/trading credentials entirely
        if credentials:
            bad = [k for k in credentials if any(x in k.lower() for x in ("broker", "order", "api_secret", "trading"))]
            if bad:
                return ConnectorResult(
                    ok=False,
                    error=f"Quant connector rejects trading credentials: {bad}",
                )
        try:
            from quant.research_lab import ResearchLab
            from quant.hypothesis_engine import HypothesisEngine
            from quant.research_review import ResearchReviewBoard

            mem = self.data_dir / "research_memory.json"
            self._lab = ResearchLab(memory_path=mem)
            self._hyp_engine = HypothesisEngine(
                memory=self._lab.memory,
                persist_path=self.data_dir / "hypotheses.json",
            )
            self._board = ResearchReviewBoard(
                memory=self._lab.memory,
                persist_path=self.data_dir / "review_board.json",
            )
            self.status = ConnectorStatus.CONNECTED
            self.connected_at = time.time()
            self.metadata["zero_real_orders"] = True
            self.metadata["allows_capital_allocation"] = False
            return ConnectorResult(ok=True, message="Quant research lab connected (research-only)")
        except Exception as e:
            self.status = ConnectorStatus.ERROR
            self.last_error = str(e)
            return ConnectorResult(ok=False, error=str(e))

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        # No auth secrets for research lab path
        if credentials and any("broker" in k.lower() or "trading" in k.lower() for k in credentials):
            return ConnectorResult(ok=False, error="trading credentials not permitted")
        return ConnectorResult(ok=True, message="Quant connector requires no trading auth")

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action in FORBIDDEN_ACTIONS or any(a in action.lower() for a in ("place_order", "live_trade", "allocate")):
            return ConnectorResult(
                ok=False,
                error=f"forbidden: '{action}' is not allowed through Quant connector (zero real orders)",
            )
        err = self.require_connected()
        if err:
            # auto-connect for research convenience
            conn = self.connect()
            if not conn.ok:
                return conn

        span = self._span(action.replace("quant_", ""), action=action)
        try:
            result = self._dispatch(action, **params)
            if span and hasattr(span, "end"):
                try:
                    span.end(ok=result.ok)
                except Exception:
                    pass
            return result
        except Exception as e:
            self.last_error = str(e)
            return ConnectorResult(ok=False, error=str(e))

    def _dispatch(self, action: str, **params: Any) -> ConnectorResult:
        handlers = {
            "quant_research": self._research,
            "quant_status": self._status,
            "quant_candidates": self._candidates,
            "quant_hypotheses": self._hypotheses,
            "quant_review": self._review,
            "quant_report": self._report,
            "quant_market_summary": self._market_summary,
            "quant_failure_patterns": self._failure_patterns,
            "quant_lineage": self._lineage,
            "quant_shadow_status": self._shadow_status,
            "quant_dashboard": self._dashboard,
            "quant_candidate": self._candidate,
            "quant_decision": self._decision_expl,
        }
        fn = handlers.get(action)
        if not fn:
            return ConnectorResult(
                ok=False,
                error=f"unknown quant action '{action}'. Allowed: {sorted(handlers)}",
            )
        return fn(**params)

    def _research(self, **params) -> ConnectorResult:
        """
        Synchronous research experiment.
        params: family/name, fast, slow, symbol, n_bars, seed
        For long runs, PEAR should wrap this in a Job.
        """
        from quant.dsl import parse_strategy
        from quant.data import synthetic_ohlcv

        name = str(params.get("name") or params.get("family") or "sma_cross")
        fast = float(params.get("fast") or 5)
        slow = float(params.get("slow") or 20)
        symbol = str(params.get("symbol") or params.get("market") or "SYN")
        n = int(params.get("n_bars") or 200)
        seed = int(params.get("seed") or 1)
        strat = parse_strategy({"name": name, "params": {"fast": fast, "slow": slow}})
        series = synthetic_ohlcv(n=n, seed=seed, symbol=symbol)
        # optional CSV path
        if params.get("csv_path"):
            from quant.data import load_csv
            series = load_csv(Path(params["csv_path"]), symbol=symbol)

        exp = self._lab.run_experiment(strat, series, source="pear_connector")
        return ConnectorResult(
            ok=True,
            data={
                "experiment_id": exp.id,
                "disposition": exp.disposition.value,
                "fingerprint": exp.strategy_fingerprint,
                "backtest": exp.backtest,
                "oos": exp.oos,
                "market": exp.market,
                "failure_reasons": exp.failure_reasons,
            },
            message=f"research complete: {exp.disposition.value}",
        )

    def _status(self, **params) -> ConnectorResult:
        hist = self._lab.research_history(limit=10)
        return ConnectorResult(
            ok=True,
            data={
                "connected": True,
                "zero_real_orders": True,
                "allows_capital_allocation": False,
                "experiments": len(self._lab.memory.all()),
                "recent": hist,
                "hypotheses": len(self._hyp_engine.hypotheses),
            },
        )

    def _candidates(self, **params) -> ConnectorResult:
        limit = int(params.get("limit") or 20)
        rows = []
        for e in sorted(self._lab.memory.all(), key=lambda x: -x.created_at)[:limit]:
            rows.append({
                "experiment_id": e.id,
                "fingerprint": e.strategy_fingerprint,
                "family": e.strategy_family,
                "market": e.market,
                "timeframe": e.timeframe,
                "disposition": e.disposition.value,
                "sharpe": (e.paper or e.backtest).get("sharpe"),
            })
        return ConnectorResult(ok=True, data={"candidates": rows})

    def _hypotheses(self, **params) -> ConnectorResult:
        generate = bool(params.get("generate"))
        family = params.get("family")
        if generate:
            hyps = self._hyp_engine.generate_from_memory(family=family, limit=int(params.get("limit") or 5))
        else:
            hyps = list(self._hyp_engine.hypotheses.values())
            if family:
                hyps = [h for h in hyps if h.strategy_family == family]
        return ConnectorResult(
            ok=True,
            data={
                "hypotheses": [
                    {
                        "id": h.id,
                        "status": h.status.value if hasattr(h.status, "value") else h.status,
                        "reason": h.reason,
                        "proposed_change": h.proposed_change,
                        "parents": h.parent_experiments,
                        "explanation": h.human_readable()[:2000],
                    }
                    for h in hyps[:20]
                ]
            },
        )

    def _review(self, **params) -> ConnectorResult:
        from quant.dsl import parse_strategy
        from quant.data import synthetic_ohlcv

        name = str(params.get("name") or "sma_cross")
        fast = float(params.get("fast") or 5)
        slow = float(params.get("slow") or 20)
        strat = parse_strategy({"name": name, "params": {"fast": fast, "slow": slow}})
        research = synthetic_ohlcv(n=int(params.get("n_research") or 80), seed=int(params.get("seed") or 1))
        independent = synthetic_ohlcv(n=int(params.get("n_independent") or 80), seed=int(params.get("seed") or 1) + 99)
        for i, b in enumerate(independent.bars):
            b.ts = 100_000 + i
        pkg = self._board.full_review_package(
            strat,
            independent,
            research,
            hypothesis_id=str(params.get("hypothesis_id") or ""),
            evidence_count=int(params.get("evidence_count") or 3),
            markets_tested=int(params.get("markets_tested") or 1),
            timeframes_tested=int(params.get("timeframes_tested") or 1),
        )
        return ConnectorResult(ok=True, data=pkg, message=pkg["decision"]["decision"])

    def _report(self, **params) -> ConnectorResult:
        exp_id = params.get("experiment_id") or params.get("id")
        if not exp_id:
            return ConnectorResult(ok=False, error="experiment_id required")
        try:
            text = self._lab.report(str(exp_id))
            return ConnectorResult(ok=True, data={"report": text})
        except Exception as e:
            return ConnectorResult(ok=False, error=str(e))

    def _market_summary(self, **params) -> ConnectorResult:
        market = str(params.get("market") or params.get("symbol") or "SYN")
        return ConnectorResult(ok=True, data=self._lab.market_summary(market))

    def _failure_patterns(self, **params) -> ConnectorResult:
        return ConnectorResult(ok=True, data={"patterns": self._lab.failure_patterns()})

    def _lineage(self, **params) -> ConnectorResult:
        hid = params.get("hypothesis_id")
        if not hid:
            return ConnectorResult(ok=False, error="hypothesis_id required")
        if hid not in self._hyp_engine.hypotheses:
            return ConnectorResult(ok=False, error=f"unknown hypothesis {hid}")
        data = self._board.lineage_query(hypothesis_id=str(hid), hypothesis_engine=self._hyp_engine)
        data["text"] = self._hyp_engine.lineage_report(str(hid))
        return ConnectorResult(ok=True, data=data)

    def _shadow_status(self, **params) -> ConnectorResult:
        # Read-only: no ability to place orders
        return ConnectorResult(
            ok=True,
            data={
                "allows_real_orders": False,
                "broker": None,
                "mode": "shadow_research_only",
                "note": "Use quant package ShadowEngine for trials; connector does not expose order APIs",
            },
        )

    def _ux(self):
        from quant.operator_ux import QuantOperatorUX
        return QuantOperatorUX(
            memory=self._lab.memory,
            hyp_engine=self._hyp_engine,
            board=self._board,
            data_dir=self.data_dir,
        )

    def _dashboard(self, **params) -> ConnectorResult:
        ux = self._ux()
        data = ux.dashboard()
        data["text"] = ux.dashboard_text()
        return ConnectorResult(ok=True, data=data)

    def _candidate(self, **params) -> ConnectorResult:
        eid = params.get("experiment_id") or params.get("id")
        if not eid:
            return ConnectorResult(ok=False, error="experiment_id required")
        ux = self._ux()
        try:
            data = ux.candidate_view(str(eid))
            data["text"] = ux.candidate_view_text(str(eid))
            return ConnectorResult(ok=True, data=data)
        except Exception as e:
            return ConnectorResult(ok=False, error=str(e))

    def _decision_expl(self, **params) -> ConnectorResult:
        cid = params.get("candidate_id") or params.get("id")
        if not cid:
            return ConnectorResult(ok=False, error="candidate_id required")
        ux = self._ux()
        text = ux.decision_explanation(str(cid))
        return ConnectorResult(ok=True, data={"explanation": text})
