import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("POLYTRACKER_CONFIG", str(ROOT.parent / "config.yaml"))
os.environ["DEMO_MODE"] = "false"
os.environ.setdefault("ETHERSCAN_API_KEY", "test-key")


@pytest.fixture()
def db(tmp_path):
    """Fresh SQLite database per test (Postgres is used in production)."""
    from polytracker import db as dbmod

    url = f"sqlite:///{tmp_path / 'test.db'}"
    os.environ["DATABASE_URL"] = url
    dbmod.init_db(url)
    yield dbmod
    dbmod.get_engine().dispose()


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    """Demo dataset + one analysis run, shared by read-only tests."""
    from polytracker import db as dbmod
    from polytracker.analysis.pipeline import run_analysis
    from polytracker.demo.seed import seed_demo

    url = f"sqlite:///{tmp_path_factory.mktemp('demo') / 'demo.db'}"
    os.environ["DATABASE_URL"] = url
    dbmod.init_db(url)
    seed_demo()
    summary = run_analysis()
    yield summary
    dbmod.get_engine().dispose()
