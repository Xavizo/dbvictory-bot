import json

from dbv.brain.state.session_journal import SessionJournal


def make(tmp_path, **kw):
    clock = iter(range(1000, 100000)).__next__
    return SessionJournal(directory=tmp_path, clock=clock, **kw)


def test_off_by_default_records_nothing(tmp_path):
    journal = make(tmp_path)
    journal.client_packet(0x96, 12)
    journal.server_frame(0x6B, 40)
    journal.chat({"kind": "private", "sender": "Someone", "text": "hello"})
    assert journal.status()["counts"] == {"packets": 0, "events": 0, "chat": 0, "messages": 0}
    assert not list(tmp_path.iterdir())


def test_metadata_only_and_chat_text_redacted(tmp_path):
    journal = make(tmp_path)
    journal.start(session=3)
    journal.client_packet(0x96, 12)
    journal.server_frame(0x6B, 40, complete=False, lag_ms=2.5)
    journal.chat({"kind": "private", "sender": "Someone", "text": "secret words"})
    journal.chat({"kind": "personal_loot", "sender": "", "text": "Loot of a rat: 2 gold"})
    journal.server_events([(1.0, "cancel_walk", {"direction": 2, "pos": [1, 2, 7], "raw": b"\x01"})])
    packets = list(journal.rings["packets"])
    assert packets[0]["dir"] == "C>S" and packets[0]["size"] == 12 and packets[0]["session"] == 3
    assert packets[1]["dir"] == "S>C" and packets[1]["complete"] is False
    private, loot = journal.rings["chat"]
    assert "text" not in private and private["text_length"] == len("secret words")
    assert loot["text"] == "Loot of a rat: 2 gold"
    event = journal.rings["events"][-1]
    assert event["details"]["raw"] is None
    journal.stop()
    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("journal-*.jsonl"))
    assert "secret words" not in written
    assert all(json.loads(line) for line in written.splitlines())


def test_include_chat_keeps_text(tmp_path):
    journal = make(tmp_path)
    journal.start(session=1, include_chat=True, write_file=False)
    journal.chat({"kind": "default", "sender": "A", "text": "hi"})
    assert journal.rings["chat"][-1]["text"] == "hi"


def test_session_change_ends_opt_in(tmp_path):
    journal = make(tmp_path)
    journal.start(session=None, write_file=False)
    journal.session_changed(5)
    assert journal.enabled and journal.session == 5
    journal.session_changed(6)
    assert not journal.enabled
    assert journal.rings["events"][-1]["reason"] == "session_changed"


def test_rolling_file_rotates_and_keeps_limit(tmp_path):
    journal = make(tmp_path)
    journal.start(session=1)
    journal.file.limit, journal.file.count = 400, 2
    for _ in range(60):
        journal.client_packet(0x64, 3)
    journal.stop()
    files = sorted(tmp_path.glob("journal-*.jsonl"))
    assert len(files) == 2
    assert all(f.stat().st_size <= 400 for f in files)


def test_dump_writes_rings_and_extra(tmp_path):
    journal = make(tmp_path)
    journal.start(session=2, write_file=False)
    journal.client_packet(0x65, 1)
    path = journal.dump({"activity": {"hp": 10, "blob": b"x"}})
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["packets"][0]["name"]
    assert body["activity"] == {"hp": 10, "blob": None}
    assert body["journal"]["enabled"] is True
