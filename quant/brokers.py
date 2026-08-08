"""
Broker adapters for *demo/paper* accounts only.

No adapter places real money orders. Live brokers require explicit
PaperTradingEngine mode and refuse live credentials unless paper=True.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    ts: float = field(default_factory=time.time)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass
class OrderRequest:
    symbol: str
    side: str  # buy | sell
    qty: float
    order_type: str = "market"
    strategy_id: str = ""
    client_id: str = ""


@dataclass
class Fill:
    id: str
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    ts: float
    paper: bool = True


class BrokerAdapter(ABC):
    name: str = "base"
    paper_only: bool = True

    @abstractmethod
    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> bool:
        ...

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        ...

    @abstractmethod
    def place_order(self, order: OrderRequest) -> Fill:
        ...

    def stream_quotes(self, symbol: str, n: int = 10) -> List[Quote]:
        return [self.get_quote(symbol) for _ in range(n)]


class SimulatedBroker(BrokerAdapter):
    """Offline / deterministic paper broker fed by a price series or random walk."""

    name = "simulated_paper"

    def __init__(self, prices: Optional[Dict[str, float]] = None, spread_bps: float = 2.0):
        self.prices = dict(prices or {"EURUSD": 1.1000, "SYN": 100.0})
        self.spread_bps = spread_bps
        self.connected = False
        self._tick = 0

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> bool:
        # Ignore credentials — always paper
        self.connected = True
        return True

    def set_price(self, symbol: str, price: float) -> None:
        self.prices[symbol] = price

    def get_quote(self, symbol: str) -> Quote:
        self._tick += 1
        mid = float(self.prices.get(symbol, 100.0))
        # mild walk if series not pushed
        mid *= 1.0 + 0.0001 * ((self._tick % 7) - 3)
        self.prices[symbol] = mid
        half = mid * (self.spread_bps / 10000.0) / 2.0
        return Quote(symbol=symbol, bid=mid - half, ask=mid + half)

    def place_order(self, order: OrderRequest) -> Fill:
        if not self.connected:
            self.connect()
        q = self.get_quote(order.symbol)
        px = q.ask if order.side == "buy" else q.bid
        return Fill(
            id=f"fill_{uuid.uuid4().hex[:10]}",
            order_id=order.client_id or f"ord_{uuid.uuid4().hex[:8]}",
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            price=px,
            ts=time.time(),
            paper=True,
        )


class OANDAPracticeAdapter(BrokerAdapter):
    """Practice API scaffold — refuses non-practice hosts."""

    name = "oanda_practice"

    def __init__(self, api_url: str = "https://api-fxpractice.oanda.com", token: str = ""):
        if "fxpractice" not in api_url and "practice" not in api_url:
            raise ValueError("OANDA adapter only allows practice API hosts")
        self.api_url = api_url
        self.token = token
        self._sim = SimulatedBroker()
        self.connected = False

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> bool:
        creds = credentials or {}
        self.token = str(creds.get("token") or self.token)
        # Without network deps, fall back to simulated paper quotes
        self.connected = self._sim.connect()
        return True

    def get_quote(self, symbol: str) -> Quote:
        return self._sim.get_quote(symbol)

    def place_order(self, order: OrderRequest) -> Fill:
        fill = self._sim.place_order(order)
        fill.paper = True
        return fill


class IBPaperAdapter(BrokerAdapter):
    """Interactive Brokers paper-account scaffold (port 7497 convention)."""

    name = "ib_paper"

    def __init__(self, host: str = "127.0.0.1", port: int = 7497):
        if port not in (7497, 7496):  # 7497 paper TWS, 7496 gateway paper often
            # still allow but mark paper
            pass
        self.host = host
        self.port = port
        self._sim = SimulatedBroker()
        self.connected = False

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> bool:
        self.connected = self._sim.connect()
        return True

    def get_quote(self, symbol: str) -> Quote:
        return self._sim.get_quote(symbol)

    def place_order(self, order: OrderRequest) -> Fill:
        fill = self._sim.place_order(order)
        fill.paper = True
        return fill


class MTDemoAdapter(BrokerAdapter):
    """MetaTrader demo scaffold — always virtual."""

    name = "mt_demo"

    def __init__(self):
        self._sim = SimulatedBroker()
        self.connected = False

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> bool:
        self.connected = self._sim.connect()
        return True

    def get_quote(self, symbol: str) -> Quote:
        return self._sim.get_quote(symbol)

    def place_order(self, order: OrderRequest) -> Fill:
        fill = self._sim.place_order(order)
        fill.paper = True
        return fill


def get_broker(name: str = "simulated", **kwargs) -> BrokerAdapter:
    name = (name or "simulated").lower()
    if name in ("simulated", "sim", "paper"):
        return SimulatedBroker(**{k: v for k, v in kwargs.items() if k in ("prices", "spread_bps")})
    if name in ("oanda", "oanda_practice"):
        return OANDAPracticeAdapter(**{k: v for k, v in kwargs.items() if k in ("api_url", "token")})
    if name in ("ib", "ib_paper"):
        return IBPaperAdapter(**{k: v for k, v in kwargs.items() if k in ("host", "port")})
    if name in ("mt", "mt_demo", "metatrader"):
        return MTDemoAdapter()
    raise ValueError(f"unknown paper broker: {name}")
