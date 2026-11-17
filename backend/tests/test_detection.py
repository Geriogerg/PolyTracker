"""End-to-end detection on the synthetic dataset: the scores must separate
informed coordination from coordination alone, skill alone, and noise."""
import statistics

from sqlalchemy import select

from polytracker.db import session_scope
from polytracker.demo.seed import addr
from polytracker.models import Address, Cluster, PriceJump, WalletScore

GROUPS = {"insider": 9, "trio": 3, "follower": 5, "farmer": 12, "sharp": 2, "crowd": 300}
LABELS = {addr(f"{g}-{i}"): g for g, n in GROUPS.items() for i in range(n)}


def _latest(s, model, run_id):
    return s.scalars(select(model).where(model.run_id == run_id)).all()


def test_scores_separate_actor_groups(demo_db):
    run_id = demo_db["run_id"]
    with session_scope() as s:
        scores = {w.wallet: w.score for w in _latest(s, WalletScore, run_id)}
    by = {}
    for w, sc in scores.items():
        by.setdefault(LABELS.get(w, "?"), []).append(sc)
    assert min(by["insider"]) >= 60, by["insider"]
    assert statistics.median(by["crowd"]) < 5
    assert max(by["crowd"]) < 40
    assert max(by["sharp"]) < 15, "a skilled favourite-buyer must not look like an insider"
    assert max(by["follower"]) < 15, "buying after the news is not suspicious"
    assert max(by["farmer"]) < 30
    assert min(by["insider"]) > max(by["trio"] + by["farmer"] + by["follower"] + by["sharp"]) - 1


def test_clusters_recover_groups(demo_db):
    run_id = demo_db["run_id"]
    with session_scope() as s:
        clusters = sorted(_latest(s, Cluster, run_id), key=lambda c: -c.score)
    groups = [{LABELS.get(m) for m in c.members} for c in clusters]
    # every planted group comes back as its own pure cluster
    assert {"insider"} in groups and {"trio"} in groups and {"farmer"} in groups and {"follower"} in groups
    top = clusters[0]
    assert {LABELS[m] for m in top.members} == {"insider"} and top.size == 9
    assert top.score >= 70
    trio = next(c for c in clusters if {LABELS.get(m) for m in c.members} == {"trio"})
    farm = next(c for c in clusters if {LABELS.get(m) for m in c.members} == {"farmer"})
    assert 25 <= trio.score < top.score, "behaviour-only cluster should be flagged, below the money-linked ring"
    assert farm.score < trio.score, "a sybil farm is coordinated but not informed"
    # every component carries a human-readable reason
    assert all(c["detail"] for c in top.components)


def test_exchange_hubs_are_not_links(demo_db):
    with session_scope() as s:
        hubs = set(s.scalars(select(Address.address).where(Address.kind == "hub")).all())
        crowd_in_clusters = [m for c in _latest(s, Cluster, demo_db["run_id"]) for m in c.members if LABELS.get(m) == "crowd"]
    assert all(addr(f"exchange-hot-{i}") in hubs for i in range(4))
    assert len(crowd_in_clusters) <= 6, "retail wallets funded by the same exchange must not be clustered"


def test_news_jumps_detected(demo_db):
    with session_scope() as s:
        n = s.scalar(select(PriceJump.id).limit(1))
        jumps = s.scalars(select(PriceJump)).all()
    assert n is not None
    big = [j for j in jumps if abs(j.p_after - j.p_before) >= 0.5]
    assert len(big) >= 5


def test_cluster_keys_are_stable_across_runs(demo_db):
    from polytracker.analysis.pipeline import run_analysis

    with session_scope() as s:
        before = {c.key for c in _latest(s, Cluster, demo_db["run_id"])}
    r2 = run_analysis()
    with session_scope() as s:
        after = {c.key for c in _latest(s, Cluster, r2["run_id"])}
    assert before == after
