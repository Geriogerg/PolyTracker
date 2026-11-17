"""API contract tests against the demo dataset."""
from fastapi.testclient import TestClient

from polytracker.api.main import app


def test_endpoints(demo_db):
    c = TestClient(app)
    ov = c.get("/api/overview").json()
    assert ov["run"]["status"] == "done" and ov["top_clusters"] and ov["top_wallets"]
    assert len(ov["score_histogram"]) == 10

    clusters = c.get("/api/clusters").json()
    top = clusters[0]
    d = c.get(f"/api/clusters/{top['key']}").json()
    assert d["cluster"]["key"] == top["key"] and d["members"] and d["edges"] and d["trades"]
    assert {"kind"} <= set(d["edges"][0]["evidence"][0])

    g = c.get("/api/graph?min_score=50").json()
    assert g["nodes"] and all(n["cluster"] for n in g["nodes"])

    wallets = c.get("/api/wallets?limit=5").json()
    w = c.get(f"/api/wallets/{wallets[0]['wallet']}").json()
    assert w["score"]["components"] and w["positions"] and w["links"]
    assert c.get("/api/wallets/0x" + "0" * 40).status_code == 404

    markets = c.get("/api/markets").json()
    with_jump = next(m for m in markets if m["n_jumps"])
    md = c.get(f"/api/markets/{with_jump['condition_id']}").json()
    assert md["series"] and md["jumps"][0]["buyers"]

    assert isinstance(c.get("/api/feed?limit=5").json(), list)
    assert c.get("/api/status").json()["counts"]["trades"] > 0


def test_settings_roundtrip(demo_db):
    c = TestClient(app)
    s = c.get("/api/settings").json()
    scope = {**s["scope"], "tags": ["geopolitics"], "min_volume": 1000}
    r = c.put("/api/settings", json={"scope": scope}).json()
    assert r["scope"]["tags"] == ["geopolitics"] and "scope" in r["overridden"]
    assert c.put("/api/settings", json={"alerts": {"cluster_score": 500}}).status_code == 422
    r = c.delete("/api/settings").json()
    assert r["overridden"] == []


def test_alerts_flow(demo_db):
    from polytracker.config import effective_config
    from polytracker.workers.alerts import evaluate_run

    created = evaluate_run(demo_db["run_id"], effective_config())
    assert any(a["kind"] == "cluster" for a in created)
    # same run again: deduplicated by cooldown
    assert evaluate_run(demo_db["run_id"], effective_config()) == []
    c = TestClient(app)
    alerts = c.get("/api/alerts").json()
    assert alerts and c.post(f"/api/alerts/{alerts[0]['id']}/ack").json()["ok"]
