"""
PEAR 3.2 Task 018 — Quant free-text disclosure.

Investigation findings, established before any code change:

1. `name`/`family` (quant_research, quant_review) and `symbol`/`market`
   (quant_research) were free text with zero validation anywhere in the
   pipeline, including inside quant.dsl.parse_strategy (which also
   accepts `name` with no constraint). Confirmed via full trace from
   core/connectors/quant_connector.py's `_research`/`_review` handlers
   down through quant/dsl.py and quant/experiment.py.

2. The research corpus (research_memory.json, hypotheses.json,
   review_board.json) is genuinely shared across every user by design
   -- ExperimentRecord/Hypothesis have no user-identity field at all.
   This is NOT the bug; sharing is intentional. quant_candidates/
   quant_status/quant_market_summary return `market`/`family` verbatim
   to any caller with no filtering, consistent with that design.

3. CORRECTED a materially wrong assumption carried from Re-Audit 3:
   QuantConnector's actions are NOT reachable via chat message routing
   through any agent, on either HTTP surface. Confirmed by exhaustive
   grep: zero agent files reference "quant" in any form; `self.
   connectors` is assigned once in Orchestrator.__init__ and never
   referenced again; ConnectorCapability metadata is defined but never
   consumed by anything that would expose it to an LLM's tool-calling
   interface. The only way to reach these actions today is a direct
   Python call to the connector's own `.execute()` method (exactly how
   the existing test suite, and this file, exercise it) -- not through
   the live multi-user service on either surface. Fixed anyway, per
   this project's established "close it before it becomes reachable"
   practice (the same reasoning already applied to Voice pre-Gate-12
   and BackupManager).

4. Design decision, documented here and in core/connectors/
   quant_connector.py's class docstring: `name`/`family`/`symbol`/
   `market` must look like short identifiers (quant.dsl.
   validate_shared_identifier) -- this rejects the worst cases (NUL
   bytes, multi-word free-text sentences, excessive length) but
   CANNOT, and does not claim to, distinguish a genuine market symbol
   from a short identifier-shaped label a caller chooses to submit
   instead. Verified directly: the exact 27-character reproduction
   string from Re-Audit 3 is now rejected (exceeds the 24-char cap),
   but a shorter identifier-shaped string of the same kind still
   passes -- that residual gap is closed by disclosure (the connector's
   docstring now states explicitly that these fields are shared and
   readable by every user), not by further validation, because no
   validation can make that distinction without abandoning free-text
   market/strategy names entirely. No per-user ownership was added --
   that would contradict the corpus's established shared-by-design
   purpose -- so there is no "authorized owner" vs "unauthorized
   access" distinction to test here: every quant-capable caller has
   equal, intended read access to the whole corpus. No historical
   migration: the connector was never reachable via the live service.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.connectors import build_default_connectors
from quant.dsl import validate_shared_identifier


def _quant(td: Path):
    import os
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(td)
    try:
        reg = build_default_connectors()
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
    return reg.get("quant")


# ── Validator unit coverage ──────────────────────────────────────────

def test_validator_rejects_the_original_reaudit3_repro_string():
    with pytest.raises(ValueError, match="exceeds"):
        validate_shared_identifier("ALICE_PRIVATE_PORTFOLIO_XYZ", "symbol")


def test_validator_rejects_long_sentences():
    with pytest.raises(ValueError):
        validate_shared_identifier("here is a whole sentence of private notes", "symbol")


def test_validator_rejects_nul_bytes():
    with pytest.raises(ValueError):
        validate_shared_identifier("evil\x00payload", "symbol")


def test_validator_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        validate_shared_identifier("", "symbol")


def test_validator_accepts_realistic_market_and_strategy_names():
    for v in ["BTCUSDT", "EURUSD", "AAPL", "sma_cross", "ema_cross_v2", "momentum_v1"]:
        assert validate_shared_identifier(v, "symbol") == v


def test_validator_documented_residual_gap_short_label_still_passes():
    """Not a bug to fix here -- a documented, verified limitation. A
    short identifier-shaped label is indistinguishable from a real
    symbol by construction; this test exists so the limitation stays
    visible and intentional rather than silently assumed away."""
    assert validate_shared_identifier("ALICE_X1", "symbol") == "ALICE_X1"


# ── Connector-level behavior ──────────────────────────────────────────

def test_quant_research_rejects_oversized_or_malformed_symbol_and_name():
    with tempfile.TemporaryDirectory() as td:
        q = _quant(Path(td))
        r = q.execute("quant_research", symbol="ALICE_PRIVATE_PORTFOLIO_XYZ", name="sma_cross")
        assert r.ok is False
        assert "exceeds" in r.error

        r2 = q.execute("quant_research", symbol="BTCUSDT", name="not a valid name at all here")
        assert r2.ok is False


def test_quant_research_accepts_legitimate_requests_unchanged():
    with tempfile.TemporaryDirectory() as td:
        q = _quant(Path(td))
        r = q.execute("quant_research", symbol="BTCUSDT", name="sma_cross", n_bars=120, seed=1)
        assert r.ok is True
        assert r.data["market"] == "BTCUSDT"


def test_quant_review_rejects_malformed_name():
    with tempfile.TemporaryDirectory() as td:
        q = _quant(Path(td))
        r = q.execute("quant_review", name="this is not an identifier shaped string")
        assert r.ok is False


def test_shared_corpus_is_readable_across_sessions_by_design():
    """Confirms the DECIDED behavior explicitly, not an oversight: a
    second, independently-constructed connector instance pointed at the
    same shared corpus can read what the first one wrote. This is the
    intended shared-research design (Task 018's documented decision),
    not something to silently assume -- if a future change makes the
    corpus per-user, this test should start failing and prompt an
    explicit decision, not pass by accident either way."""
    with tempfile.TemporaryDirectory() as td:
        import os
        os.environ["HOME"] = str(td)
        reg_a = build_default_connectors()
        q_a = reg_a.get("quant")
        r = q_a.execute("quant_research", symbol="BTCUSDT", name="sma_cross")
        assert r.ok is True

        reg_b = build_default_connectors()
        q_b = reg_b.get("quant")
        cand = q_b.execute("quant_candidates", limit=10)
        assert cand.ok is True
        assert any(c["market"] == "BTCUSDT" for c in cand.data["candidates"])


# ── Both HTTP surfaces: confirmed symmetric non-exposure ────────────

def test_quant_not_reachable_via_fastapi_or_stdlib_chat_surface():
    """Honest coverage of the 'test both surfaces' requirement given
    what's actually true: no agent routes any chat message to the
    quant connector on either surface (confirmed this task by grep:
    zero references to 'quant' in any agents/*.py file, zero
    consumers of ConnectorCapability, self.connectors never read again
    after Orchestrator.__init__ assigns it). This test exercises a
    real chat round-trip on both surfaces and confirms neither one's
    response or state shows any quant involvement -- not just an
    absence of a dedicated quant HTTP route (already true, checked
    directly in service/app.py), but that ordinary chat traffic
    genuinely never reaches it on either surface."""
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        from service.app import PearService
        from service.auth import Role
        svc = PearService(data_root=Path(td) / "svc")
        svc.auth.create_user("t018_stdlib_user", "pw", Role.USER)
        ok, tok_resp = svc.do_login("t018_stdlib_user", "pw")
        assert ok
        token = tok_resp["token"]
        status, body = svc.handle_route(
            "POST", "/v1/chat", {"Authorization": f"Bearer {token}"},
            b'{"message":"run quant_research on BTCUSDT"}',
        )
        assert status == 200
        orch = svc.sessions.get("t018_stdlib_user").orchestrator
        # The connector exists on the orchestrator (registered at
        # construction) but connect() is never called and _lab stays
        # None -- even stronger confirmation than "zero experiments"
        # would be that nothing in ordinary chat traffic ever reaches
        # it on this surface.
        q = orch.connectors.get("quant")
        assert q._lab is None

    try:
        import fastapi  # noqa
        import uvicorn  # noqa
    except Exception:
        pytest.skip("fastapi/uvicorn not installed")

    with _tf.TemporaryDirectory() as td2:
        from service.app import create_app
        from fastapi.testclient import TestClient
        from service.auth import Role
        app = create_app(data_root=Path(td2) / "svc2")
        service = app.state.service
        service.auth.create_user("t018_fastapi_user", "pw", Role.USER)
        client = TestClient(app)
        tok = client.post("/auth/login", json={"username": "t018_fastapi_user", "password": "pw"}).json()["token"]
        r = client.post("/v1/chat", json={"message": "run quant_research on BTCUSDT"},
                         headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        orch = service.sessions.get("t018_fastapi_user").orchestrator
        q = orch.connectors.get("quant")
        assert q._lab is None


# ── Concurrency: Gate 12/Task 016 persistence protections intact ────

def test_concurrent_quant_research_still_safe_with_validation_added():
    """Re-runs the Gate 12 concurrency shape (many independently-
    constructed connectors sharing one corpus) with the new validation
    layer in the call path, confirming it didn't reintroduce the
    lost-write race Gate 12 fixed."""
    N = 30
    with tempfile.TemporaryDirectory() as td:
        import os
        os.environ["HOME"] = str(td)
        errors = []

        def worker(i):
            try:
                reg = build_default_connectors()
                q = reg.get("quant")
                r = q.execute("quant_research", symbol="BTCUSDT", name="sma_cross", seed=i)
                if not r.ok:
                    errors.append((i, r.error))
            except Exception as e:
                errors.append((i, repr(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not [t for t in threads if t.is_alive()], "possible deadlock"
        assert not errors, f"unexpected errors: {errors}"

        reg_check = build_default_connectors()
        q_check = reg_check.get("quant")
        cand = q_check.execute("quant_candidates", limit=100)
        assert cand.ok is True
        assert len(cand.data["candidates"]) == N
