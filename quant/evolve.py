"""Evolutionary strategy generator — mutation, crossover, recombination."""

from __future__ import annotations

import random
from typing import List, Optional

from .dsl import Strategy, StrategySpec, parse_strategy


DEFAULT_GENE_KEYS = ("fast", "slow")


def seed_population(templates: Optional[List[Strategy]] = None, size: int = 20, seed: int = 7) -> List[Strategy]:
    rng = random.Random(seed)
    if not templates:
        templates = [
            parse_strategy({
                "name": "sma_cross",
                "params": {"fast": 10, "slow": 30},
                "entry": {"type": "cross_above", "a": "sma_fast", "b": "sma_slow"},
                "exit": {"type": "cross_below", "a": "sma_fast", "b": "sma_slow"},
            }),
            parse_strategy({
                "name": "ema_cross",
                "params": {"fast": 8, "slow": 21},
                "entry": {"type": "cross_above", "a": "sma_fast", "b": "sma_slow"},
                "exit": {"type": "cross_below", "a": "sma_fast", "b": "sma_slow"},
                "indicators": {
                    "sma_fast": {"type": "ema", "period": "fast"},
                    "sma_slow": {"type": "ema", "period": "slow"},
                },
            }),
        ]
    pop: List[Strategy] = []
    while len(pop) < size:
        base = templates[len(pop) % len(templates)]
        fast = rng.randint(3, 25)
        slow = rng.randint(fast + 2, 80)
        pop.append(base.clone(fast=fast, slow=slow))
    return pop


def mutate(strategy: Strategy, rng: random.Random, scale: float = 0.3) -> Strategy:
    params = dict(strategy.spec.params)
    for k, v in list(params.items()):
        if rng.random() < 0.7:
            delta = 1 + scale * (rng.random() * 2 - 1)
            params[k] = max(2, int(v * delta))
    if "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
        params["slow"] = params["fast"] + rng.randint(2, 15)
    return strategy.clone(**params)


def crossover(a: Strategy, b: Strategy, rng: random.Random) -> Strategy:
    params = {}
    keys = set(a.spec.params) | set(b.spec.params)
    for k in keys:
        params[k] = a.spec.params.get(k, b.spec.params.get(k, 10)) if rng.random() < 0.5 else b.spec.params.get(k, a.spec.params.get(k, 10))
    child = a.clone(**params)
    # sometimes inherit indicators from b
    if rng.random() < 0.3:
        child.spec.indicators = dict(b.spec.indicators)
    return child


def evolve_generation(
    population: List[Strategy],
    fitness: List[float],
    *,
    elite: int = 4,
    seed: int = 0,
) -> List[Strategy]:
    rng = random.Random(seed)
    ranked = sorted(zip(fitness, population), key=lambda x: -x[0])
    next_pop = [s for _, s in ranked[:elite]]
    while len(next_pop) < len(population):
        # tournament
        def pick():
            c = rng.sample(ranked[: max(elite * 3, 6)], k=min(3, len(ranked)))
            return max(c, key=lambda x: x[0])[1]
        if rng.random() < 0.5:
            child = crossover(pick(), pick(), rng)
        else:
            child = mutate(pick(), rng)
        next_pop.append(child)
    return next_pop
