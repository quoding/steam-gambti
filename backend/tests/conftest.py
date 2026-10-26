import os

os.environ["STEAM_WEB_API_KEY"] = ""  # 테스트는 루트 .env의 실제 키를 쓰지 않는다

import socket

import pytest

from gembti import catalog as catalog_mod
from gembti import config


def fixture_games(n=40):
    """테스트 전용 가상 게임. 서비스 카탈로그와 무관."""
    pools = [(1695, 3834, 1664), (1662, 1702, 1685), (9, 1741, 1775), (32322, 1666, 1716), (1742, 6426, 6129),
             (599, 12472, 1643), (7332, 1643, 1662), (3959, 1716, 32322)]
    games = []
    for i in range(n):
        a, b, c = pools[i % len(pools)]
        games.append({
            "appid": 900000 + i, "name": f"Fixture Game {i}", "short_description": "fixture" if i % 5 else "",
            "short_description_lang": "other", "header_image": None if i % 7 == 0 else f"https://example.invalid/{i}.jpg",
            "capsule_image": None, "store_url": f"https://store.steampowered.com/app/{900000 + i}/",
            "windows": "true", "mac": "true" if i % 3 == 0 else "false", "linux": "false",
            "korean": "true" if i % 2 == 0 else "false", "english": "true",
            "review_count": 1000 + i * 37, "percent_positive": 80 + i % 19,
            "series_id": f"series:{i}", "developer_key": f"dev:{i % 9}", "bucket": "G01", "size": "small",
            "fetched_at": "2026-10-24T00:00:00+0900",
            "tags": [{"tagid": a, "weight": 900, "rank": 1}, {"tagid": b, "weight": 600, "rank": 2},
                     {"tagid": c, "weight": 300, "rank": 3}],
        })
    return games


@pytest.fixture
def var_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    monkeypatch.setattr(config, "SERVICE_DB", tmp_path / "service.db")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(config, "ACTIVE_SNAPSHOT_FILE", tmp_path / "active_snapshot.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 1000)
    catalog_mod._cache.update(key=None, catalog=None)
    return tmp_path


def make_snapshot(tmp_path, games, sid="test-snap-1"):
    from gembti.policy import get_policy
    p = get_policy()
    ko = {t.tagid: t.name_ko for t in p.tags}
    en = {t.tagid: t.name_en for t in p.tags}
    path = tmp_path / f"{sid}.db"
    catalog_mod.write_snapshot_db(path, sid, games, ko, en, {"raw_run_id": "fixture"})
    return path


@pytest.fixture
def snapshot(var_dir):
    games = fixture_games()
    path = make_snapshot(var_dir, games)
    catalog_mod.promote(path, expected=len(games))
    return games


@pytest.fixture
def no_network(monkeypatch):
    def deny(*a, **k):
        raise OSError("network disabled in test")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
