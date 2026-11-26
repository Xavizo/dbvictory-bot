"""Session retention, packet attribution, and opt-in reads of both histories."""
import asyncio
import struct
from types import SimpleNamespace

import pytest

from game_state import GameState, parse_server_packet, scan_packet
from packet_observer import apply_scan, detach_state, publish_server_feedback
from potions import PotionDiagnostics
from protocol import PacketReader


def frame(text, mode=25):
    data = text.encode("latin1")
    return b"\xb4" + bytes([mode]) + struct.pack("<H", len(data)) + data


def test_only_real_damage_and_item_feedback_leave_general_history(monkeypatch):
    import mcp_server
    live = GameState()
    live.session_id = 12
    scan = detach_state(live, 0)
    scan.received_at = 102
    hit = "You lose 17095 ki points blocking an attack by a garlic."
    hp = "You lose 15912 hitpoints due to an attack by a garlic."
    unknown = "You cannot use this object."
    assert scan_packet(b"".join([
        frame(unknown, 26), frame(hit), frame(hp), frame("Using one of 99 Senzu..."),
        frame(unknown, 26), frame("[Senzu] You are exhausted.",26), frame("Hello"),
    ]), scan.state)
    apply_scan(live, scan)
    publish_server_feedback(live, scan, PotionDiagnostics())
    assert live.last_incoming_hit == 102
    assert [r["text"] for r in live.messages] == [unknown,"Hello"]
    monkeypatch.setattr(mcp_server,"state", SimpleNamespace(game_state=live))
    page = asyncio.run(mcp_server.read_message_history("combat"))
    assert page["session_id"] == 12 and page["capacity"] == 100_000
    ki, health, stock, refusal, tagged = page["rows"]
    assert (ki["resource"],ki["amount"],ki["attacker"]) == ("ki",17095,"a garlic")
    assert (health["resource"],health["amount"]) == ("hp",15912)
    assert stock["kind"] == "stock_report" and stock["reported_stock"] == 99
    assert refusal["kind"] == tagged["kind"] == "item_refused"
    # The older callback classifies the same server text without a worker.
    legacy = GameState()
    parse_server_packet(0xb4, PacketReader(frame(hit)[1:]), legacy)
    assert legacy.message_history.combat[-1]["kind"] == "damage_received"


def test_independent_limits_and_stable_paging_after_new_messages():
    gs = GameState()
    h = gs.message_history
    h.record_server(25, "General", 1)
    text = "You lose 42 hitpoints due to an attack by a garlic."
    for i in range(100_002):
        h.record_server(25,text,i)
    first = h.page("combat",2)
    assert first["retained"] == 100_000 and first["oldest_id"] == 3
    assert [r["id"] for r in first["rows"]] == [100_001,100_002]
    for i in range(3):
        h.record_server(25,text,100_003+i)
    older = h.page("combat",2,first["next_before"])
    assert [r["id"] for r in older["rows"]] == [99_999,100_000]
    assert h.page("combat",2,3)["rows"] == []
    assert h.page("general")["rows"][0]["text"] == "General"
    for i in range(100_000):
        h.record_server(25,"General",i)
    assert h.page("general",1)["oldest_id"] == 2
    assert h.page("combat",1)["latest_id"] == 100_005
    assert not detach_state(gs,0).state.message_history.combat
    assert not GameState().message_history.combat
    for args in (("both",2),("general",501),("combat",1,0)):
        with pytest.raises(ValueError):
            h.page(*args)


def test_session_stock_observation_survives_manual_play_but_not_inventory_gaps(monkeypatch):
    h = GameState().message_history
    monkeypatch.setattr("time.monotonic",lambda:12)
    def report(at,n):
        h.observe_stock(25,f"Using one of {n} Senzu...",at,at)
    report(8,10)
    h.observe_stock(26,"[Senzu] You are exhausted.",8,8)
    report(9,9)
    report(10,8)
    h.invalidate_inventory()
    report(11,7)  # arrived before the invalidation, published afterwards
    report(12,6)
    report(13,5)
    assert [(r["kind"], r["remaining"]) for r in h.combat] == [
        ("senzu_stock_decreased",8), ("senzu_stock_decreased",5)]


def test_manual_and_auto_attempts_are_logged_with_diagnostics_off():
    gs = GameState()
    gs.hp,gs.max_hp,gs.mana,gs.max_mana,gs.stats_updated_at = 90,100,20,100,123
    diag = PotionDiagnostics()
    diag.record_use(8465,"use",gs)
    diag.record_use(10160,"self",gs,source="auto")
    diag.record_use(999,"use",gs)
    entries = gs.message_history.page("combat")["rows"]
    assert [(r["kind"],r["source"],r["item_id"]) for r in entries] == [
        ("item_attempt","manual",8465), ("item_attempt","auto",10160)]
    assert entries[0]["ki"] == 20 and entries[0]["stats_at"] == 123
    assert not gs.messages and not diag.snapshot()["rows"]
