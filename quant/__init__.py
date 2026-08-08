"""
AI Quant Research Lab (concept)

Discovers, evaluates, and evolves trading strategies via historical
robustness evidence. Does NOT predict future prices.
"""

from .engine import QuantResearchLab
from .dsl import Strategy, parse_strategy, StrategySpec
from .backtest import BacktestResult
from .paper_engine import PaperTradingEngine
from .promotion import Stage, PromotionThresholds
from .long_horizon import LongHorizonValidator
from .execution_model import ExecutionModel

__all__ = [
    "QuantResearchLab",
    "Strategy",
    "StrategySpec",
    "parse_strategy",
    "BacktestResult",
    "PaperTradingEngine",
    "Stage",
    "PromotionThresholds",
    "LongHorizonValidator",
    "ExecutionModel",
]
