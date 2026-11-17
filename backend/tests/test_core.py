"""Unit tests: statistics, jump detection, normalisation."""
import itertools
import math

import numpy as np
import pytest

from polytracker.analysis.jumps import detect_jumps
from polytracker.analysis.stats import interval_coverage, points_from_p, poisson_binomial_sf
from polytracker.clients.normalize import normalize_market, normalize_trade, normalize_transfer


# ----------------------------------------------------------------- stats
def brute_sf(probs, k):
    total = 0.0
    for outcome in itertools.product([0, 1], repeat=len(probs)):
        if sum(outcome) >= k:
            total += math.prod(p if o else 1 - p for p, o in zip(probs, outcome))
    return total


@pytest.mark.parametrize("probs,k", [([0.1, 0.2, 0.9], 2), ([0.5] * 6, 4), ([0.12, 0.15, 0.3, 0.66, 0.08], 5), ([0.9] * 5, 0)])
def test_poisson_binomial_matches_brute_force(probs, k):
    assert poisson_binomial_sf(probs, k) == pytest.approx(brute_sf(probs, k), rel=1e-9, abs=1e-12)


def test_poisson_binomial_edges():
    assert poisson_binomial_sf([0.5, 0.5], 3) == 0.0
    assert poisson_binomial_sf([], 0) == 1.0


def test_points_scale():
    assert points_from_p(0.5, 35) == 0
    assert points_from_p(0.1, 35) == 0
    assert points_from_p(1e-6, 35) == 35
    assert 0 < points_from_p(1e-3, 35) < 35


def test_longshot_record_is_surprising_but_favourites_are_not():
    longshots = poisson_binomial_sf([0.12] * 5, 5)
    favourites = poisson_binomial_sf([0.9] * 10, 10)
    assert longshots < 1e-4
    assert favourites > 0.3


def test_interval_coverage_merges_overlaps():
    assert interval_coverage([(0, 10), (5, 15), (30, 40)], (0, 100)) == pytest.approx(0.25)
    assert interval_coverage([(-10, 5)], (0, 10)) == pytest.approx(0.5)


# ----------------------------------------------------------------- jumps
def _series(levels, step=600):
    ts = np.arange(len(levels)) * step + 1_700_000_000
    return ts, np.array(levels, dtype=float)


def test_detects_step_move_with_full_extent():
    ts, p = _series([0.12] * 30 + [0.4, 0.75, 0.93, 0.96] + [0.96] * 20)
    js = detect_jumps("m", ts, p, threshold=0.2, window_s=7200)
    assert len(js) == 1
    j = js[0]
    assert j.direction == 1
    assert j.p_before == pytest.approx(0.12)
    assert j.p_after >= 0.93
    assert j.start_ts == ts[29]


def test_ignores_single_stray_print():
    ts, p = _series([0.3] * 20 + [0.8] + [0.3] * 20)
    assert detect_jumps("m", ts, p, threshold=0.2, window_s=7200) == []


def test_detects_down_moves():
    ts, p = _series([0.35] * 20 + [0.2, 0.09, 0.07] + [0.07] * 10)
    js = detect_jumps("m", ts, p, threshold=0.2, window_s=7200)
    assert len(js) == 1 and js[0].direction == -1


def test_slow_drift_is_not_a_jump():
    ts, p = _series(list(np.linspace(0.2, 0.8, 200)))
    assert detect_jumps("m", ts, p, threshold=0.2, window_s=7200) == []


# ------------------------------------------------------------- normalise
def test_normalize_v2_trade():
    t = normalize_trade({
        "proxy_wallet": "0xABC" + "0" * 37, "side": "BUY", "token_id": "123", "condition_id": "0xDEAD",
        "size": 100.0, "price": 0.25, "timestamp": 1782752879, "outcome": "Yes", "outcome_index": 0,
        "transaction_hash": "0xF00", "name": "Mean-Record",
    })
    assert t["wallet"] == ("0xabc" + "0" * 37)
    assert t["condition_id"] == "0xdead"
    assert t["usd"] == pytest.approx(25.0)
    assert t["_name"] == "Mean-Record"


def test_normalize_v1_and_rtds_ms_timestamp():
    t = normalize_trade({"proxyWallet": "0x" + "a" * 40, "conditionId": "0x01", "asset": "9", "side": "SELL",
                         "size": "10", "price": "0.5", "timestamp": 1782752879000, "outcomeIndex": 1, "usdcSize": 5.1})
    assert t["ts"] == 1782752879 and t["side"] == "SELL" and t["outcome_index"] == 1 and t["usd"] == 5.1


def test_normalize_trade_rejects_junk():
    assert normalize_trade({"proxy_wallet": "0x1"}) is None
    assert normalize_trade({"proxy_wallet": "0x1", "condition_id": "0x2", "price": 0, "size": 1, "timestamp": 1}) is None


def test_outcome_sentinel_999_is_mapped():
    t = normalize_trade({"proxy_wallet": "0x" + "a" * 40, "condition_id": "0x1", "price": 0.5, "size": 1,
                         "timestamp": 1, "outcome_index": 999})
    assert t["outcome_index"] == 0


def test_normalize_market_parses_json_strings_and_resolution():
    m = normalize_market({
        "id": 5, "question": "Q?", "conditionId": "0xAB", "slug": "q", "outcomes": '["Yes","No"]',
        "outcomePrices": '["0","1"]', "clobTokenIds": '["1","2"]', "closed": True, "volumeNum": 1000,
        "closedTime": "2026-03-01 10:00:00+00", "endDate": "2026-02-28T00:00:00Z",
    }, {"slug": "ev", "title": "Event", "tags": [{"slug": "geopolitics"}]})
    assert m["resolved"] and m["winning_index"] == 1
    assert m["token_ids"] == ["1", "2"] and m["tags"] == ["geopolitics"]
    assert m["closed_ts"] and m["end_ts"]


def test_open_market_not_resolved():
    m = normalize_market({"conditionId": "0x1", "outcomePrices": '["0.4","0.6"]', "closed": False})
    assert not m["resolved"] and m["winning_index"] is None and m["last_price"] == 0.4


def test_normalize_transfer_decimals():
    t = normalize_transfer({"hash": "0xA", "from": "0xB", "to": "0xC", "value": "1500000", "tokenDecimal": "6",
                            "contractAddress": "0xD", "timeStamp": "1700000000"})
    assert t["amount"] == 1.5 and t["from_addr"] == "0xb"
