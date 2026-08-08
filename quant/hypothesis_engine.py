"""
Evidence-driven hypothesis generation.

Rejects ungrounded ideas. Spawns new candidates only; never mutates frozen ones.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .hypothesis import (
    Hypothesis,
    HypothesisStatus,
    MutationType,
    FalsificationCriteria,
    new_hypothesis_id,
)
from .research_memory import ResearchMemory
from .experiment import ExperimentRecord, Disposition
from .dsl import Strategy, parse_strategy
from .research_lab import ResearchLab
from .data import Series
from .trial import fingerprint_strategy


class HypothesisEngine:
    def __init__(
        self,
        memory: Optional[ResearchMemory] = None,
        persist_path: Optional[Path] = None,
        min_evidence_experiments: int = 2,
    ):
        self.memory = memory or ResearchMemory()
        self.persist_path = Path(persist_path) if persist_path else Path.home() / ".pear" / "quant_hypotheses.json"
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        self.min_evidence = min_evidence_experiments
        self.hypotheses: Dict[str, Hypothesis] = {}
        self._load()

    def _load(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            for d in data.get("hypotheses") or []:
                h = Hypothesis.from_dict(d)
                self.hypotheses[h.id] = h
        except Exception:
            pass

    def _save(self) -> None:
        payload = {"hypotheses": [h.to_dict() for h in self.hypotheses.values()]}
        self.persist_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def generate_from_memory(
        self,
        family: Optional[str] = None,
        limit: int = 5,
    ) -> List[Hypothesis]:
        """
        Scan ResearchMemory for failure patterns + successful conditions → grounded hypotheses.
        """
        exps = self.memory.all()
        if family:
            exps = [e for e in exps if e.strategy_family == family]
        if len(exps) < self.min_evidence:
            return []

        failures = [e for e in exps if e.disposition in (Disposition.FAILED, Disposition.RETIRED)]
        successes = [e for e in exps if e.disposition in (Disposition.SURVIVED, Disposition.PROMOTED, Disposition.CONTINUED)]

        generated: List[Hypothesis] = []

        # Pattern 1: range/low-vol failures + successful vol-aware specs → volatility filter
        range_fail = [
            e for e in failures
            if any("range" in str(r).lower() or "vol" in str(r).lower() or "oos" in str(r).lower() for r in e.failure_reasons)
            or (e.regimes and float(e.regimes.get("range", 0)) > 0.3)
        ]
        if len(range_fail) >= 1 and len(exps) >= self.min_evidence:
            base = range_fail[0] if range_fail else failures[0]
            h = self._propose_volatility_filter(base, exps, successes)
            if h:
                generated.append(h)

        # Pattern 2: parameter instability → tighten params toward successful medians
        if successes and failures:
            h = self._propose_parameter_shift(failures[0], successes, exps)
            if h:
                generated.append(h)

        # Pattern 3: exit modification if many weak profit factors on survivors' relatives
        weak_pf = [e for e in failures if float(e.backtest.get("profit_factor") or 0) < 1.0]
        if weak_pf and successes:
            h = self._propose_exit_tweak(weak_pf[0], successes, exps)
            if h:
                generated.append(h)

        # Pattern 4: combine components from two successful families (if available)
        if len(successes) >= 2:
            h = self._propose_combine(successes[0], successes[1], exps)
            if h:
                generated.append(h)

        out = []
        for h in generated[:limit]:
            if not h.parent_experiments:
                h.status = HypothesisStatus.REJECTED_EVIDENCE
                h.explanation = self._reject_msg("no parent experiments linked")
            elif len(h.parent_experiments) < self.min_evidence and len(exps) < self.min_evidence:
                h.status = HypothesisStatus.REJECTED_EVIDENCE
            else:
                h.explanation = h.human_readable()
                h.seal()
            self.hypotheses[h.id] = h
            out.append(h)
        self._save()
        return out

    def _reject_msg(self, why: str) -> str:
        return f"REJECTED: insufficient evidence ({why}). Ungrounded ideas are not allowed."

    def _base_lineage(self, parents: List[ExperimentRecord]) -> List[Dict[str, str]]:
        return [{"type": "experiment", "id": e.id} for e in parents]

    def _propose_volatility_filter(
        self,
        base: ExperimentRecord,
        all_exps: List[ExperimentRecord],
        successes: List[ExperimentRecord],
    ) -> Optional[Hypothesis]:
        parents = list({e.id: e for e in all_exps}.values())[:12]
        parent_ids = [e.id for e in parents]
        markets = sorted({e.market for e in parents})
        tfs = sorted({e.timeframe for e in parents})
        spec = copy.deepcopy(base.strategy_spec)
        # proposed change: tag volatility filter in params + indicators
        params = dict(spec.get("params") or {})
        params["vol_gate"] = 0.01  # minimum vol to allow entries
        spec["params"] = params
        inds = dict(spec.get("indicators") or {})
        inds["vol_roc"] = {"type": "roc", "period": 10}
        spec["indicators"] = inds
        spec["name"] = (spec.get("name") or "strat") + "_volgate"
        # entry becomes below/above still same — filter encoded in params for research to interpret;
        # for DSL compatibility we keep entry but document the gate
        hid = new_hypothesis_id()
        fc = FalsificationCriteria()
        h = Hypothesis(
            id=hid,
            parent_experiments=parent_ids,
            parent_strategies=[base.strategy_fingerprint] + [e.strategy_fingerprint for e in successes[:3]],
            reason=(
                f"{len([e for e in parents if e.disposition in (Disposition.FAILED, Disposition.RETIRED)])} related candidates "
                "show weakness associated with ranging/low-vol conditions; "
                f"{len(successes)} successful records exist in memory for comparison."
            ),
            observed_failure="Performance deteriorates in low-volatility or ranging regimes.",
            proposed_change="Add volatility-regime gating (vol_gate param + roc indicator) before entries.",
            expected_effect="Reduce range-regime drawdown without materially reducing trend participation.",
            falsification_criteria=fc,
            market_scope=markets,
            timeframe_scope=tfs,
            regime_scope=["range", "low_vol"],
            mutation_type=MutationType.VOLATILITY_FILTER.value,
            strategy_family=base.strategy_family,
            base_strategy_spec=dict(base.strategy_spec),
            proposed_strategy_spec=spec,
            evidence_summary={
                "n_experiments": len(parents),
                "n_markets": len(markets),
                "n_timeframes": len(tfs),
                "n_successes": len(successes),
                "n_failures": len([e for e in parents if e.disposition in (Disposition.FAILED, Disposition.RETIRED)]),
            },
            lineage=self._base_lineage(parents) + [{"type": "hypothesis", "id": hid}],
        )
        return h

    def _propose_parameter_shift(
        self,
        base: ExperimentRecord,
        successes: List[ExperimentRecord],
        all_exps: List[ExperimentRecord],
    ) -> Optional[Hypothesis]:
        # median successful fast/slow if present
        fasts = [float(e.parameters["fast"]) for e in successes if "fast" in e.parameters]
        slows = [float(e.parameters["slow"]) for e in successes if "slow" in e.parameters]
        if not fasts or not slows:
            return None
        fasts.sort()
        slows.sort()
        med_f = fasts[len(fasts) // 2]
        med_s = slows[len(slows) // 2]
        if med_f >= med_s:
            med_s = med_f + 5
        spec = copy.deepcopy(base.strategy_spec)
        params = dict(spec.get("params") or {})
        params["fast"] = med_f
        params["slow"] = med_s
        spec["params"] = params
        spec["name"] = (spec.get("name") or "strat") + f"_p{int(med_f)}_{int(med_s)}"
        parents = all_exps[:10]
        hid = new_hypothesis_id()
        return Hypothesis(
            id=hid,
            parent_experiments=[e.id for e in parents],
            parent_strategies=[base.strategy_fingerprint] + [e.strategy_fingerprint for e in successes[:3]],
            reason=f"Successful siblings cluster near fast={med_f}, slow={med_s}; base candidate differs.",
            observed_failure="Parameter region of failures diverges from successful cluster.",
            proposed_change=f"Shift parameters toward successful median (fast={med_f}, slow={med_s}).",
            expected_effect="Improve OOS stability by moving into historically robust param neighborhood.",
            falsification_criteria=FalsificationCriteria(),
            market_scope=sorted({e.market for e in parents}),
            timeframe_scope=sorted({e.timeframe for e in parents}),
            mutation_type=MutationType.PARAMETER.value,
            strategy_family=base.strategy_family,
            base_strategy_spec=dict(base.strategy_spec),
            proposed_strategy_spec=spec,
            evidence_summary={
                "n_experiments": len(parents),
                "n_markets": len({e.market for e in parents}),
                "n_timeframes": len({e.timeframe for e in parents}),
                "median_fast": med_f,
                "median_slow": med_s,
            },
            lineage=self._base_lineage(parents) + [{"type": "hypothesis", "id": hid}],
        )

    def _propose_exit_tweak(
        self,
        base: ExperimentRecord,
        successes: List[ExperimentRecord],
        all_exps: List[ExperimentRecord],
    ) -> Optional[Hypothesis]:
        spec = copy.deepcopy(base.strategy_spec)
        # adopt exit from a successful sibling if different
        donor = successes[0]
        donor_exit = (donor.strategy_spec or {}).get("exit")
        if not donor_exit:
            return None
        spec["exit"] = copy.deepcopy(donor_exit)
        spec["name"] = (spec.get("name") or "strat") + "_exitfix"
        parents = all_exps[:10]
        hid = new_hypothesis_id()
        return Hypothesis(
            id=hid,
            parent_experiments=[e.id for e in parents],
            parent_strategies=[base.strategy_fingerprint, donor.strategy_fingerprint],
            reason="Weak profit factor on failing candidates; successful peers use different exit rules.",
            observed_failure="Exits appear to leave adverse regimes too slowly (low profit factor).",
            proposed_change="Replace exit condition with structure from successful sibling experiment.",
            expected_effect="Improve profit factor and reduce hold-time in adverse moves.",
            falsification_criteria=FalsificationCriteria(),
            market_scope=sorted({e.market for e in parents}),
            timeframe_scope=sorted({e.timeframe for e in parents}),
            mutation_type=MutationType.EXIT.value,
            strategy_family=base.strategy_family,
            base_strategy_spec=dict(base.strategy_spec),
            proposed_strategy_spec=spec,
            evidence_summary={
                "n_experiments": len(parents),
                "n_markets": len({e.market for e in parents}),
                "n_timeframes": len({e.timeframe for e in parents}),
                "donor_experiment": donor.id,
            },
            lineage=self._base_lineage(parents) + [{"type": "hypothesis", "id": hid}],
        )

    def _propose_combine(
        self,
        a: ExperimentRecord,
        b: ExperimentRecord,
        all_exps: List[ExperimentRecord],
    ) -> Optional[Hypothesis]:
        spec = copy.deepcopy(a.strategy_spec)
        # combine params: fast from a, slow from b if sensible
        pa = dict(a.parameters or {})
        pb = dict(b.parameters or {})
        if "fast" in pa and "slow" in pb:
            fast = float(pa["fast"])
            slow = float(pb["slow"])
            if fast >= slow:
                slow = fast + 5
            spec["params"] = {"fast": fast, "slow": slow}
            spec["name"] = f"combine_{a.strategy_name}_{b.strategy_name}"
        else:
            return None
        parents = all_exps[:10]
        hid = new_hypothesis_id()
        return Hypothesis(
            id=hid,
            parent_experiments=[e.id for e in parents],
            parent_strategies=[a.strategy_fingerprint, b.strategy_fingerprint],
            reason="Two surviving components show complementary strengths in memory.",
            observed_failure="Single-component candidates leave residual failure modes.",
            proposed_change="Recombine parameters from two successful experiments.",
            expected_effect="Retain strengths of both parents under falsification gates.",
            falsification_criteria=FalsificationCriteria(),
            market_scope=sorted({e.market for e in parents}),
            timeframe_scope=sorted({e.timeframe for e in parents}),
            mutation_type=MutationType.COMBINE.value,
            strategy_family=a.strategy_family,
            base_strategy_spec=dict(a.strategy_spec),
            proposed_strategy_spec=spec,
            evidence_summary={
                "n_experiments": len(parents),
                "n_markets": len({e.market for e in parents}),
                "n_timeframes": len({e.timeframe for e in parents}),
                "parents": [a.id, b.id],
            },
            lineage=self._base_lineage(parents) + [{"type": "hypothesis", "id": hid}],
        )

    def reject_ungrounded(self, idea: str) -> Hypothesis:
        """Explicit rejection path for ideas without evidence."""
        h = Hypothesis(
            id=new_hypothesis_id(),
            parent_experiments=[],
            parent_strategies=[],
            reason=idea,
            observed_failure="n/a",
            proposed_change=idea,
            expected_effect="n/a",
            falsification_criteria=FalsificationCriteria(),
            status=HypothesisStatus.REJECTED_EVIDENCE,
            explanation=self._reject_msg(f"ungrounded proposal: {idea[:120]}"),
        )
        self.hypotheses[h.id] = h
        self._save()
        return h

    def spawn_candidate(self, hypothesis_id: str) -> Strategy:
        """Turn a sealed, evidence-backed hypothesis into a new research Strategy (not a mutation of a frozen one)."""
        h = self.hypotheses[hypothesis_id]
        if h.status == HypothesisStatus.REJECTED_EVIDENCE:
            raise RuntimeError("cannot spawn from rejected hypothesis")
        if not h.parent_experiments:
            raise RuntimeError("cannot spawn without parent evidence")
        if not h.sealed:
            h.seal()
        strat = parse_strategy(h.proposed_strategy_spec)
        h.status = HypothesisStatus.CANDIDATE_SPAWNED
        h.child_candidate_id = fingerprint_strategy(strat.spec.to_dict())
        h.lineage.append({"type": "candidate", "id": h.child_candidate_id})
        self._save()
        return strat

    def evaluate_candidate_through_pipeline(
        self,
        hypothesis_id: str,
        series: Series,
        research: Optional[ResearchLab] = None,
    ) -> ExperimentRecord:
        """
        Mandatory full research entry — no shortcut past ResearchLab gates.
        """
        research = research or ResearchLab(memory_path=None)
        # use shared memory if possible
        if research.memory is not self.memory and self.memory.all():
            research.memory = self.memory
        strat = self.spawn_candidate(hypothesis_id)
        h = self.hypotheses[hypothesis_id]
        h.status = HypothesisStatus.TESTING
        exp = research.run_experiment(strat, series, source=f"hypothesis:{hypothesis_id}")
        h.child_experiment_ids.append(exp.id)
        h.lineage.append({"type": "experiment", "id": exp.id})

        # falsification vs criteria using OOS vs backtest
        fc = h.falsification_criteria
        bt_s = float(exp.backtest.get("sharpe") or 0)
        oos_s = float(exp.oos.get("sharpe") or bt_s)
        if bt_s != 0:
            deg_pct = abs((bt_s - oos_s) / abs(bt_s)) * 100
        else:
            deg_pct = 0.0
        dd_bt = float(exp.backtest.get("max_drawdown") or 0)
        dd_oos = float(exp.oos.get("max_drawdown") or dd_bt)
        dd_inc_pct = max(0.0, (dd_oos - dd_bt) * 100)
        trades = int(exp.backtest.get("trades") or 0)

        falsified = False
        reasons = []
        if deg_pct > fc.max_oos_sharpe_degradation_pct:
            falsified = True
            reasons.append(f"OOS Sharpe degradation {deg_pct:.1f}% > {fc.max_oos_sharpe_degradation_pct}%")
        if dd_inc_pct > fc.max_drawdown_increase_pct:
            falsified = True
            reasons.append(f"DD increase {dd_inc_pct:.1f}% > {fc.max_drawdown_increase_pct}%")
        if trades < fc.min_trades:
            falsified = True
            reasons.append(f"trades {trades} < {fc.min_trades}")
        if exp.disposition in (Disposition.FAILED, Disposition.RETIRED):
            falsified = True
            reasons.append("research disposition failed/retired")

        if falsified:
            h.status = HypothesisStatus.FALSIFIED
            h.lineage.append({"type": "falsified", "id": ";".join(reasons)})
        else:
            h.status = HypothesisStatus.SURVIVED
            h.lineage.append({"type": "survived", "id": exp.id})
        self._save()
        # ensure experiment notes lineage
        return exp

    def lineage_report(self, hypothesis_id: str) -> str:
        h = self.hypotheses[hypothesis_id]
        lines = [f"# Lineage for {h.id}", ""]
        for step in h.lineage:
            lines.append(f"- {step.get('type')}: {step.get('id')}")
        lines.append("")
        lines.append(h.human_readable())
        return "\n".join(lines)
