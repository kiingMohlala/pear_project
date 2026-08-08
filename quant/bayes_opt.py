"""
Bayesian optimization for strategy hyperparameters (Quant Lab).

Fits a Gaussian-process surrogate to backtest objective scores and
proposes the next parameter set via Expected Improvement.

This optimizes *historical* objective estimates — it does not predict
future prices. Use inside research only; freeze winners before paper trials.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

from .dsl import Strategy, parse_strategy
from .backtest import run_backtest, BacktestResult
from .data import Series
from .optimize import scalarize


@dataclass
class ParamSpace:
    """Continuous search space: name → (low, high, is_int)."""
    bounds: Dict[str, Tuple[float, float, bool]] = field(default_factory=dict)

    def names(self) -> List[str]:
        return list(self.bounds.keys())

    def sample(self, rng: random.Random) -> Dict[str, float]:
        out = {}
        for k, (lo, hi, is_int) in self.bounds.items():
            v = rng.uniform(lo, hi)
            out[k] = float(int(round(v))) if is_int else float(v)
        return out

    def to_unit(self, params: Dict[str, float]) -> np.ndarray:
        xs = []
        for k, (lo, hi, _) in self.bounds.items():
            v = float(params.get(k, lo))
            xs.append((v - lo) / (hi - lo + 1e-12))
        return np.array(xs, dtype=float)

    def from_unit(self, x: np.ndarray) -> Dict[str, float]:
        out = {}
        for i, (k, (lo, hi, is_int)) in enumerate(self.bounds.items()):
            v = lo + float(x[i]) * (hi - lo)
            v = min(hi, max(lo, v))
            out[k] = float(int(round(v))) if is_int else float(v)
        return out

    def dim(self) -> int:
        return len(self.bounds)


def default_sma_space() -> ParamSpace:
    return ParamSpace(bounds={
        "fast": (3, 30, True),
        "slow": (10, 80, True),
    })


class GaussianProcess:
    """RBF-kernel GP with simple noise — pure numpy/scipy."""

    def __init__(self, length_scale: float = 0.3, signal_var: float = 1.0, noise: float = 1e-4):
        self.length_scale = length_scale
        self.signal_var = signal_var
        self.noise = noise
        self.X: Optional[np.ndarray] = None
        self.y: Optional[np.ndarray] = None
        self.K_inv: Optional[np.ndarray] = None
        self.y_mean: float = 0.0
        self.y_std: float = 1.0

    def _kernel(self, A: np.ndarray, B: np.ndarray) -> np.ndarray:
        # RBF
        A2 = np.sum(A ** 2, axis=1)[:, None]
        B2 = np.sum(B ** 2, axis=1)[None, :]
        d2 = A2 + B2 - 2 * A @ B.T
        return self.signal_var * np.exp(-0.5 * d2 / (self.length_scale ** 2))

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self.X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        self.y_mean = float(np.mean(y)) if len(y) else 0.0
        self.y_std = float(np.std(y)) if len(y) > 1 else 1.0
        if self.y_std < 1e-9:
            self.y_std = 1.0
        self.y = (y - self.y_mean) / self.y_std
        K = self._kernel(self.X, self.X)
        K = K + self.noise * np.eye(len(self.X))
        try:
            self.K_inv = np.linalg.inv(K)
        except np.linalg.LinAlgError:
            self.K_inv = np.linalg.pinv(K)

    def predict(self, Xstar: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if self.X is None or self.K_inv is None or self.y is None:
            mu = np.zeros(len(Xstar))
            var = np.ones(len(Xstar))
            return mu, var
        Ks = self._kernel(Xstar, self.X)
        Kss = self._kernel(Xstar, Xstar)
        mu = Ks @ self.K_inv @ self.y
        var = np.diag(Kss - Ks @ self.K_inv @ Ks.T)
        var = np.maximum(var, 1e-9)
        # unnormalize mean
        mu = mu * self.y_std + self.y_mean
        var = var * (self.y_std ** 2)
        return mu, var


def expected_improvement(mu: np.ndarray, var: np.ndarray, y_best: float, xi: float = 0.01) -> np.ndarray:
    sigma = np.sqrt(var)
    improvement = mu - y_best - xi
    Z = np.where(sigma > 1e-12, improvement / sigma, 0.0)
    ei = improvement * norm.cdf(Z) + sigma * norm.pdf(Z)
    ei = np.where(sigma > 1e-12, ei, 0.0)
    return ei


@dataclass
class BOObservation:
    params: Dict[str, float]
    score: float
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BOResult:
    best_params: Dict[str, float]
    best_score: float
    history: List[BOObservation]
    n_iter: int
    method: str = "gp_ei"

    def to_dict(self) -> dict:
        return {
            "best_params": self.best_params,
            "best_score": self.best_score,
            "n_iter": self.n_iter,
            "method": self.method,
            "history": [h.to_dict() for h in self.history],
            "disclaimer": (
                "Bayesian optimization searched historical objective scores only. "
                "It does not predict future prices. Freeze params before paper validation."
            ),
        }


class BayesianHyperOptimizer:
    """
    Sequential BO over a ParamSpace.

    objective(params) -> float  (higher is better)
    """

    def __init__(
        self,
        space: ParamSpace,
        objective: Callable[[Dict[str, float]], float],
        *,
        n_init: int = 5,
        seed: int = 0,
        xi: float = 0.01,
    ):
        self.space = space
        self.objective = objective
        self.n_init = n_init
        self.rng = random.Random(seed)
        self.xi = xi
        self.history: List[BOObservation] = []
        self.gp = GaussianProcess()

    def _eval(self, params: Dict[str, float], metrics: Optional[dict] = None) -> float:
        # enforce fast < slow when both present
        if "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
            params = dict(params)
            params["slow"] = params["fast"] + 2
        score = float(self.objective(params))
        self.history.append(BOObservation(params=dict(params), score=score, metrics=metrics or {}))
        return score

    def _fit_gp(self) -> None:
        X = np.array([self.space.to_unit(h.params) for h in self.history])
        y = np.array([h.score for h in self.history])
        self.gp.fit(X, y)

    def _propose(self) -> Dict[str, float]:
        self._fit_gp()
        y_best = max(h.score for h in self.history)
        dim = self.space.dim()

        def neg_ei(x: np.ndarray) -> float:
            x = np.clip(x, 0.0, 1.0)
            mu, var = self.gp.predict(x.reshape(1, -1))
            ei = expected_improvement(mu, var, y_best, xi=self.xi)
            return -float(ei[0])

        best_x = None
        best_val = float("inf")
        # multi-start L-BFGS-B on unit cube
        for _ in range(max(8, 4 * dim)):
            x0 = np.array([self.rng.random() for _ in range(dim)])
            res = minimize(neg_ei, x0, method="L-BFGS-B", bounds=[(0.0, 1.0)] * dim)
            if res.success and res.fun < best_val:
                best_val = res.fun
                best_x = res.x
        if best_x is None:
            return self.space.sample(self.rng)
        return self.space.from_unit(np.clip(best_x, 0.0, 1.0))

    def run(self, n_iter: int = 20) -> BOResult:
        # initial random design
        while len(self.history) < self.n_init:
            params = self.space.sample(self.rng)
            self._eval(params)

        for _ in range(max(0, n_iter - self.n_init)):
            params = self._propose()
            self._eval(params)

        best = max(self.history, key=lambda h: h.score)
        return BOResult(
            best_params=best.params,
            best_score=best.score,
            history=list(self.history),
            n_iter=len(self.history),
        )


def optimize_strategy_params(
    series: Series,
    base_strategy: Strategy,
    space: Optional[ParamSpace] = None,
    *,
    n_iter: int = 25,
    n_init: int = 6,
    seed: int = 0,
) -> BOResult:
    """
    BO over strategy params maximizing multi-objective scalarized backtest score.
    """
    space = space or default_sma_space()

    def objective(params: Dict[str, float]) -> float:
        strat = base_strategy.clone(**params)
        result = run_backtest(series, strat)
        return scalarize(result)

    opt = BayesianHyperOptimizer(space, objective, n_init=n_init, seed=seed)
    return opt.run(n_iter=n_iter)


def compare_search_methods(
    series: Series,
    base: Strategy,
    space: Optional[ParamSpace] = None,
    *,
    budget: int = 20,
    seed: int = 0,
) -> Dict[str, Any]:
    """Compare random search vs Bayesian optimization under equal evaluation budget."""
    space = space or default_sma_space()
    rng = random.Random(seed)

    def eval_params(params: Dict[str, float]) -> float:
        return scalarize(run_backtest(series, base.clone(**params)))

    # random
    rand_hist = []
    best_r = -1e9
    best_rp = {}
    for _ in range(budget):
        p = space.sample(rng)
        if "fast" in p and "slow" in p and p["fast"] >= p["slow"]:
            p["slow"] = p["fast"] + 2
        s = eval_params(p)
        rand_hist.append(s)
        if s > best_r:
            best_r, best_rp = s, p

    # BO
    bo = optimize_strategy_params(series, base, space, n_iter=budget, n_init=min(5, budget // 2), seed=seed + 1)

    return {
        "budget": budget,
        "random_best_score": best_r,
        "random_best_params": best_rp,
        "bo_best_score": bo.best_score,
        "bo_best_params": bo.best_params,
        "random_curve": rand_hist,
        "bo_curve": [h.score for h in bo.history],
        "bo_improved": bo.best_score >= best_r - 1e-9,
        "disclaimer": "Comparison on historical scores only — not live predictive power.",
    }
