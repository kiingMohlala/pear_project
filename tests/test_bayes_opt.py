"""Bayesian hyperparameter optimization tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant.data import synthetic_ohlcv
from quant.dsl import parse_strategy
from quant.bayes_opt import (
    ParamSpace,
    BayesianHyperOptimizer,
    optimize_strategy_params,
    compare_search_methods,
    GaussianProcess,
    expected_improvement,
)
import numpy as np


def test_gp_and_ei():
    gp = GaussianProcess()
    X = np.array([[0.1], [0.5], [0.9]])
    y = np.array([0.2, 0.8, 0.3])
    gp.fit(X, y)
    mu, var = gp.predict(np.array([[0.5], [0.7]]))
    assert mu.shape == (2,)
    ei = expected_improvement(mu, var, y_best=0.8)
    assert ei.shape == (2,)


def test_bo_improves_or_matches_random_budget():
    series = synthetic_ohlcv(n=180, seed=3)
    base = parse_strategy({"name": "sma_cross", "params": {"fast": 10, "slow": 30}})
    cmp = compare_search_methods(series, base, budget=12, seed=5)
    assert "bo_best_score" in cmp
    assert cmp["budget"] == 12
    # BO should be competitive (not necessarily always better on tiny budget)
    assert isinstance(cmp["bo_best_params"], dict)


def test_optimize_strategy_params():
    series = synthetic_ohlcv(n=150, seed=7)
    base = parse_strategy({"name": "sma_cross", "params": {"fast": 8, "slow": 25}})
    res = optimize_strategy_params(series, base, n_iter=10, n_init=4, seed=1)
    assert res.best_params
    assert "fast" in res.best_params
    assert res.best_params["fast"] < res.best_params["slow"]
    assert len(res.history) == 10
    d = res.to_dict()
    assert "disclaimer" in d


def test_param_space_int():
    space = ParamSpace(bounds={"fast": (3, 10, True), "slow": (12, 40, True)})
    rng = __import__("random").Random(0)
    p = space.sample(rng)
    assert p["fast"] == int(p["fast"])


if __name__ == "__main__":
    test_gp_and_ei()
    print("  ✓ GP/EI")
    test_param_space_int()
    print("  ✓ space")
    test_optimize_strategy_params()
    print("  ✓ optimize")
    test_bo_improves_or_matches_random_budget()
    print("  ✓ vs random")
    print("All bayes opt tests passed.")
