"""Persistent store for paper-trading signals, orders, fills, and metrics."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  id TEXT PRIMARY KEY,
  strategy_id TEXT,
  symbol TEXT,
  side TEXT,
  strength REAL,
  regime TEXT,
  ts REAL,
  meta TEXT
);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  strategy_id TEXT,
  symbol TEXT,
  side TEXT,
  qty REAL,
  status TEXT,
  ts REAL
);
CREATE TABLE IF NOT EXISTS fills (
  id TEXT PRIMARY KEY,
  order_id TEXT,
  strategy_id TEXT,
  symbol TEXT,
  side TEXT,
  qty REAL,
  price REAL,
  ts REAL,
  paper INTEGER
);
CREATE TABLE IF NOT EXISTS equity (
  strategy_id TEXT,
  ts REAL,
  equity REAL,
  drawdown REAL,
  PRIMARY KEY (strategy_id, ts)
);
CREATE TABLE IF NOT EXISTS strategy_state (
  strategy_id TEXT PRIMARY KEY,
  name TEXT,
  stage TEXT,
  symbol TEXT,
  params TEXT,
  created_at REAL,
  updated_at REAL,
  metrics TEXT,
  regime_stats TEXT
);
CREATE TABLE IF NOT EXISTS quotes (
  symbol TEXT,
  ts REAL,
  bid REAL,
  ask REAL,
  PRIMARY KEY (symbol, ts)
);
"""


class PaperStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else Path.home() / ".pear" / "quant_paper.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def execute(self, sql: str, args: tuple = ()) -> None:
        self._conn.execute(sql, args)
        self._conn.commit()

    def query(self, sql: str, args: tuple = ()) -> List[tuple]:
        cur = self._conn.execute(sql, args)
        return list(cur.fetchall())

    def log_signal(self, sid: str, strategy_id: str, symbol: str, side: str, strength: float, regime: str, meta: dict) -> None:
        self.execute(
            "INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?)",
            (sid, strategy_id, symbol, side, strength, regime, time.time(), json.dumps(meta)),
        )

    def log_order(self, oid: str, strategy_id: str, symbol: str, side: str, qty: float, status: str) -> None:
        self.execute(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?)",
            (oid, strategy_id, symbol, side, qty, status, time.time()),
        )

    def log_fill(self, fid: str, order_id: str, strategy_id: str, symbol: str, side: str, qty: float, price: float) -> None:
        self.execute(
            "INSERT OR REPLACE INTO fills VALUES (?,?,?,?,?,?,?,?,1)",
            (fid, order_id, strategy_id, symbol, side, qty, price, time.time()),
        )

    def log_equity(self, strategy_id: str, equity: float, drawdown: float) -> None:
        self.execute(
            "INSERT OR REPLACE INTO equity VALUES (?,?,?,?)",
            (strategy_id, time.time(), equity, drawdown),
        )

    def log_quote(self, symbol: str, bid: float, ask: float, ts: Optional[float] = None) -> None:
        self.execute(
            "INSERT OR REPLACE INTO quotes VALUES (?,?,?,?)",
            (symbol, ts or time.time(), bid, ask),
        )

    def upsert_strategy(self, strategy_id: str, name: str, stage: str, symbol: str, params: dict, metrics: dict, regime_stats: dict) -> None:
        now = time.time()
        rows = self.query("SELECT created_at FROM strategy_state WHERE strategy_id=?", (strategy_id,))
        created = rows[0][0] if rows else now
        self.execute(
            "INSERT OR REPLACE INTO strategy_state VALUES (?,?,?,?,?,?,?,?,?)",
            (strategy_id, name, stage, symbol, json.dumps(params), created, now, json.dumps(metrics), json.dumps(regime_stats)),
        )

    def list_strategies(self, stage: Optional[str] = None) -> List[Dict[str, Any]]:
        if stage:
            rows = self.query("SELECT * FROM strategy_state WHERE stage=? ORDER BY updated_at DESC", (stage,))
        else:
            rows = self.query("SELECT * FROM strategy_state ORDER BY updated_at DESC")
        out = []
        for r in rows:
            out.append({
                "strategy_id": r[0],
                "name": r[1],
                "stage": r[2],
                "symbol": r[3],
                "params": json.loads(r[4] or "{}"),
                "created_at": r[5],
                "updated_at": r[6],
                "metrics": json.loads(r[7] or "{}"),
                "regime_stats": json.loads(r[8] or "{}"),
            })
        return out

    def fills_for(self, strategy_id: str) -> List[Dict[str, Any]]:
        rows = self.query("SELECT id,order_id,symbol,side,qty,price,ts FROM fills WHERE strategy_id=? ORDER BY ts", (strategy_id,))
        return [
            {"id": r[0], "order_id": r[1], "symbol": r[2], "side": r[3], "qty": r[4], "price": r[5], "ts": r[6]}
            for r in rows
        ]

    def equity_curve(self, strategy_id: str) -> List[Dict[str, float]]:
        rows = self.query("SELECT ts, equity, drawdown FROM equity WHERE strategy_id=? ORDER BY ts", (strategy_id,))
        return [{"ts": r[0], "equity": r[1], "drawdown": r[2]} for r in rows]
