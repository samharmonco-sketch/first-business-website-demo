import time

import tradingbot.dashboard.web as web
from tradingbot.logging_setup import BotLogs


def make_logs(tmp_path):
    return BotLogs(tmp_path / "logs", "decisions.jsonl", "orders.jsonl", "account.jsonl")


def log_one_decision(logs):
    logs.log_decision(cycle_id="c1", strategy_name="s", ticker="T", action="hold",
                       size_usd=0.0, confidence=0.0, edge=0.0, reasoning="r", inputs={}, traded=False)


def test_heartbeat_no_cycles_logged_yet_is_red(tmp_path):
    logs = make_logs(tmp_path)
    text, color = web._heartbeat(logs, poll_interval_seconds=60)
    assert "no cycles" in text
    assert color == "#cf222e"


def test_heartbeat_fresh_cycle_is_green(tmp_path):
    logs = make_logs(tmp_path)
    log_one_decision(logs)
    text, color = web._heartbeat(logs, poll_interval_seconds=60)
    assert "ago" in text
    assert color == "#1a7f37"


def test_heartbeat_running_late_is_amber(tmp_path, monkeypatch):
    logs = make_logs(tmp_path)
    log_one_decision(logs)
    real_now = time.time()
    monkeypatch.setattr(web.time, "time", lambda: real_now + 60 * 3)  # 3x the poll interval

    text, color = web._heartbeat(logs, poll_interval_seconds=60)

    assert color == "#9a6700"


def test_heartbeat_long_silence_is_red(tmp_path, monkeypatch):
    logs = make_logs(tmp_path)
    log_one_decision(logs)
    real_now = time.time()
    monkeypatch.setattr(web.time, "time", lambda: real_now + 60 * 10)  # 10x the poll interval -- laptop asleep, etc.

    text, color = web._heartbeat(logs, poll_interval_seconds=60)

    assert color == "#cf222e"
