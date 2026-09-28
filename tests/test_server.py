import pytest
from fastapi.testclient import TestClient

from baselinetrading.broker import RiskBypass
from baselinetrading.server import Runtime, create_app
from tests.helpers import Clock, engine

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def running(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, clock=Clock("11:00"))
    clock.set("11:00", 20)
    eng.tick()
    app = create_app(lambda: Runtime(engine=eng), token=TOKEN, start_engine=False)
    with TestClient(app) as client:
        yield client, eng, gw, broker, clock


def test_everything_but_health_needs_the_token(running):
    client, *_ = running
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/orders", json={"symbol": "SPY", "qty": 1, "stop_price": 495}).status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_without_a_configured_token_nothing_works(tmp_path):
    eng, *_ = engine(tmp_path)
    app = create_app(lambda: Runtime(engine=eng), token="short", start_engine=False)
    with TestClient(app) as client:
        assert client.get("/api/status", headers={"Authorization": "Bearer short"}).status_code == 503


def test_a_manual_order_goes_through_the_risk_manager_and_is_tagged_manual(running):
    client, eng, gw, broker, _ = running
    response = client.post("/api/orders", headers=AUTH, json={"symbol": "SPY", "qty": 10, "stop_price": 495})
    assert response.status_code == 200, response.json()
    assert gw.open_trade.source == "manual"
    assert {e["kind"] for e in gw.journal.recent(20) if e["source"] == "manual"} >= {"order_request", "risk_approved", "fill"}


def test_a_manual_order_without_a_stop_is_vetoed_with_the_reason(running):
    client, eng, gw, broker, _ = running
    response = client.post("/api/orders", headers=AUTH, json={"symbol": "SPY", "qty": 10})
    assert response.status_code == 422
    assert any("needs a stop" in r for r in response.json()["reasons"])
    assert broker.mutations == []


def test_the_client_cannot_choose_its_source_or_its_price(running):
    client, eng, gw, broker, _ = running
    client.post("/api/orders", headers=AUTH,
                json={"symbol": "SPY", "qty": 10, "stop_price": 495, "source": "strategy", "price": 1.0})
    assert gw.open_trade.source == "manual"
    assert gw.journal.recent(1, {"order_request"})[0]["reference_price"] == 500.01  # server's live price


def test_kill_needs_confirmation_then_flattens(running):
    client, eng, gw, broker, _ = running
    client.post("/api/orders", headers=AUTH, json={"symbol": "SPY", "qty": 10, "stop_price": 495})
    assert client.post("/api/kill", headers=AUTH, json={"confirm": "yes"}).status_code == 400
    assert broker.positions()
    assert client.post("/api/kill", headers=AUTH, json={"confirm": "FLATTEN"}).status_code == 200
    assert broker.positions() == [] and gw.kill_switch
    status = client.get("/api/status", headers=AUTH).json()
    assert any("kill switch" in r for r in status["entry"]["reasons"])


def test_startup_problems_are_shown_and_orders_refused(tmp_path):
    app = create_app(lambda: Runtime(problems=["environment variable APCA_API_KEY_ID is not set"]),
                     token=TOKEN, start_engine=False)
    with TestClient(app) as client:
        status = client.get("/api/status", headers=AUTH).json()
        assert status["armed"] is False and "APCA_API_KEY_ID" in status["disarmed_reasons"][0]
        response = client.post("/api/orders", headers=AUTH, json={"symbol": "SPY", "qty": 1, "stop_price": 495})
        assert response.status_code == 503


def test_websocket_rejects_a_bad_token_and_streams_status(running):
    client, *_ = running
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=wrong") as socket:
            socket.receive_json()
    with client.websocket_connect(f"/ws?token={TOKEN}") as socket:
        assert socket.receive_json()["mode"] == "PAPER"


def test_no_route_reaches_the_broker_without_the_risk_manager(running, monkeypatch):
    """Call every POST route; the broker spy fails the test if a mutation lacks RiskManager.execute on the stack."""
    client, eng, gw, broker, _ = running
    import sys

    from baselinetrading.risk import RiskManager

    seen = []
    for name in ("submit_market", "submit_stop_sell", "cancel_order", "cancel_all", "close_position"):
        original = getattr(broker, name)

        def spy(*args, _original=original, _name=name, **kwargs):
            frame, found = sys._getframe(), False
            while frame is not None:
                found |= frame.f_code is RiskManager.execute.__code__
                frame = frame.f_back
            seen.append((_name, found))
            return _original(*args, **kwargs)

        monkeypatch.setattr(broker, name, spy)

    payloads = {
        "/api/orders": {"symbol": "SPY", "qty": 10, "stop_price": 495},
        "/api/positions/close": {},
        "/api/kill": {"confirm": "FLATTEN"},
        "/api/kill/reset": {"confirm": "RESET"},
    }
    post_routes = {r.path for r in client.app.routes if "POST" in getattr(r, "methods", set())}
    assert post_routes == set(payloads), "a new POST route needs a case here"
    for path, body in payloads.items():
        client.post(path, headers=AUTH, json=body)
        client.post("/api/orders", headers=AUTH, json=payloads["/api/orders"])
    assert seen, "expected the routes to reach the broker"
    assert all(found for _, found in seen), seen
