"""Alert sounds of the panel window (dbv/mcp_server/panel_alerts.py). Ported from
the Electron tests of alert-sounds.js and notification-audio.js."""
import pytest

from dbv.mcp_server import panel_alerts
from dbv.mcp_server.panel_alerts import AlertSounds, NotificationAudio, SoundConsumer, visible_players

CONFIG = {"private": {"enabled": True, "sound": True, "scope": "always"},
          "default": {"enabled": True, "sound": True, "scope": "always"},
          "player": {"enabled": True, "sound": True, "scope": "cavebot"}}


def snapshot(events=(), **extra):
    events = list(events)
    return {"session": "one", "latest_id": events[-1]["id"] if events else 0, "events": events,
            "config": CONFIG, "cavebot_active": True, **extra}


def event(ident, **extra):
    return {"id": ident, "kind": "private", "at": 100, "sound": True, **extra}


class Clock:
    def __init__(self, ms=100000):
        self.ms = ms

    def __call__(self):
        return self.ms


def test_player_repeats_while_visible_stops_on_departure_and_ignores_history_acknowledgement():
    now, sounds = Clock(), []
    c = SoundConsumer(sounds.append, now)
    c.consume(snapshot(), [12]); assert sounds == ["player"]
    now.ms += 1499; c.consume(snapshot(), [12]); assert len(sounds) == 1
    now.ms += 1; c.consume(snapshot(unread=0), [12]); assert len(sounds) == 2
    now.ms += 1500; c.consume(snapshot(), [12]); assert len(sounds) == 3
    now.ms += 1500; c.consume(snapshot(), []); assert len(sounds) == 3
    c.consume(snapshot(), [13]); assert len(sounds) == 4
    now.ms += 1500; c.consume(snapshot(cavebot_active=False), [13]); assert len(sounds) == 4
    c.consume(snapshot(config={**CONFIG, "player": {**CONFIG["player"], "sound": False}}), [13])
    assert len(sounds) == 4


def test_each_new_chat_message_has_one_distinct_sound_including_coalesced_messages():
    now, sounds = Clock(), []
    c = SoundConsumer(sounds.append, now)
    c.consume(snapshot([event(1)])); assert sounds == []
    s = snapshot([event(1), event(2), event(3, sound=False, kind="default")])
    c.consume(s); now.ms += 750; c.consume(s); now.ms += 5000; c.consume(s)
    assert sounds == ["message", "message"]
    c.consume(snapshot([event(1)], session="new")); assert len(sounds) == 2


def test_busy_audio_retries_and_current_preferences_cancel_queued_messages_and_stale_chat_is_skipped():
    now, state = Clock(), {"busy": True, "sounds": 0}

    def play(kind):
        if state["busy"]:
            return False
        state["sounds"] += 1
        return True
    c = SoundConsumer(play, now)
    c.consume(snapshot()); c.consume(snapshot([event(1)])); assert state["sounds"] == 0
    state["busy"] = False; now.ms += 750; c.consume(snapshot([event(1)])); assert state["sounds"] == 1
    c.consume(snapshot([event(2), event(3)]))
    now.ms += 750
    c.consume(snapshot([event(3)], config={**CONFIG, "private": {**CONFIG["private"], "sound": False}}))
    assert state["sounds"] == 1
    c.consume(snapshot([event(4, at=60)])); assert state["sounds"] == 1


@pytest.mark.parametrize("poll_while_paused", [False, True])
def test_global_pause_silences_chat_and_player_and_discards_queued_sounds(poll_while_paused):
    now, sounds = Clock(), []
    c = SoundConsumer(sounds.append, now)
    c.consume(snapshot())
    c.consume(snapshot([event(1), event(2)]))
    assert sounds == ["message"]
    if poll_while_paused:
        now.ms += 5000
        c.consume(snapshot([event(1), event(2)], paused=True, silenced_through=2), [12])
        assert sounds == ["message"]
    now.ms += 750; c.consume(snapshot([event(1), event(2)], paused=False, silenced_through=2))
    assert sounds == ["message"]
    c.consume(snapshot([event(3, at=now.ms / 1000)], paused=False, silenced_through=2))
    now.ms += 5000; c.consume(snapshot([event(3)], paused=False, silenced_through=2), [12])
    assert sounds == ["message", "message", "player"]


def test_only_a_fresh_aligned_map_with_typed_players_permits_the_repeat_sound():
    passive = {"status": "ready", "stale": False, "captured_at": 100, "center": [10, 10, 7],
               "cells": [{"z": 7, "alert_players": [{"id": 0x10000002}]}, {"z": 6, "alert_players": [{"id": 0x10000003}]}]}
    data = {"connected": True, "player": {"position": [10, 10, 7]}, "passive_map": passive}
    assert visible_players(data, 100000) == [0x10000002]
    for changed in ({"stale": True}, {"captured_at": 90}, {"center": [11, 10, 7]}):
        assert visible_players({**data, "passive_map": {**passive, **changed}}, 100000) == []
    assert visible_players({**data, "connected": False}, 100000) == []


def test_vitals_loss_sounds_on_first_poll_repeats_while_active_and_stops_on_recovery_or_mute():
    now, sounds = Clock(), []
    c = SoundConsumer(sounds.append, now)
    alarm = snapshot([event(4, kind="vitals")], vitals_alarm_id=4)
    c.consume(alarm)
    now.ms += 1500; c.consume(alarm)
    now.ms += 500; c.consume(alarm)
    now.ms += 3000; c.consume({**alarm, "paused": True})
    c.consume({**alarm, "vitals_alarm_id": None})
    assert sounds == ["vitals", "vitals"]
    c.consume({**alarm, "vitals_alarm_id": 6})
    assert sounds == ["vitals"] * 3


def test_ki_alarm_repeats_while_active_respecting_mute_scope_and_expiry():
    now, sounds = Clock(), []
    c = SoundConsumer(sounds.append, now)
    row = {"enabled": True, "sound": True, "scope": "always"}
    state = snapshot([event(1, kind="ki_loss")], ki_alarm_id=1, config={**CONFIG, "ki_loss": row})
    c.consume(state)
    for i in range(1, 6):
        now.ms += 2000
        c.consume({**state, "ki_alarm_id": None} if i == 5 else state)
    assert sounds == ["vitals"] * 5
    c.consume({**state, "paused": True})
    c.consume({**state, "silenced_through": 1})
    c.consume({**state, "config": {**CONFIG, "ki_loss": {**row, "sound": False}}})
    c.consume({**state, "cavebot_active": False, "config": {**CONFIG, "ki_loss": {**row, "scope": "cavebot"}}})
    assert len(sounds) == 5
    c.consume({**state, "ki_alarm_id": 2, "latest_id": 2})
    assert len(sounds) == 6


def poller(response, now, heard, **kwargs):
    sounds = AlertSounds("test", heard.append, fetch=lambda token: response(), now=now, **kwargs)
    sounds.poll_once()  # the first poll runs at start
    return sounds


def test_panel_mute_on_an_older_engine_silences_the_tray_and_resumes_on_new_chat():
    now, heard, state = Clock(), [], {"alerts": snapshot()}
    p = poller(lambda: {"alerts": state["alerts"]}, now, heard)

    def tick():
        now.ms += 750
        p.poll_once()
    assert p.panel_pause()["paused"] is True
    state["alerts"] = snapshot([event(1)]); tick()
    assert p.panel_pause()["through"] == 1
    assert p.test_sound() is False
    p.panel_pause(False); tick()
    state["alerts"] = snapshot([event(1), event(2, at=now.ms / 1000)]); tick()
    assert heard == ["message"]
    state["alerts"] = snapshot(session="two"); tick()
    assert p.panel_pause()["paused"] is True
    with pytest.raises(TypeError):
        p.panel_pause("on")
    p.stop()


def test_a_restarted_panel_uses_the_engine_pause_and_suppresses_sound_until_resumed():
    now, heard = Clock(), []
    state = {"alerts": snapshot([event(1)], paused=True, silenced_through=1)}
    passive = {"status": "ready", "center": [1, 1, 7], "cells": [{"z": 7, "alert_players": [{"id": 0x10000020}]}]}

    def response():
        return {"connected": True, "player": {"position": [1, 1, 7]}, "alerts": state["alerts"],
                "passive_map": {**passive, "captured_at": now.ms / 1000}}
    sounds = AlertSounds("test", heard.append, fetch=lambda token: response(), now=now)
    assert sounds.test_sound() is False
    sounds.poll_once()
    now.ms += 1500; sounds.poll_once()
    assert heard == []
    state["alerts"] = {**state["alerts"], "paused": False}; sounds.poll_once()
    assert heard == ["player"]
    now.ms += 1500
    state["alerts"] = snapshot([event(2, at=now.ms / 1000)], paused=False, silenced_through=1); sounds.poll_once()
    assert heard == ["player", "message"]
    state["alerts"] = snapshot(session="new", paused=True); now.ms += 1500; sounds.poll_once()
    assert heard == ["player", "message"]
    assert sounds.test_sound() is False


def test_polling_stops_on_quit_and_failed_requests_stay_silent():
    calls, heard = [], []

    def fetch(token):
        calls.append(token)
        raise OSError("refused")
    sounds = AlertSounds("synthetic", heard.append, fetch=fetch)
    sounds.poll_once()
    assert calls == ["synthetic"] and heard == []
    sounds.stop(); sounds.poll_once()
    assert calls == ["synthetic"]


def test_the_state_request_is_authenticated_without_redirects_or_proxies(monkeypatch):
    seen = {}

    class Opener:
        def open(self, request, timeout):
            seen.update(url=request.full_url, headers=dict(request.header_items()), timeout=timeout)
            raise OSError("offline")

    def build_opener(*handlers):
        seen["handlers"] = handlers
        return Opener()
    monkeypatch.setattr(panel_alerts.urllib.request, "build_opener", build_opener)
    with pytest.raises(OSError):
        panel_alerts.fetch_state("synthetic")
    assert seen["url"] == "http://127.0.0.1:8089/api/state"
    assert seen["headers"]["Authorization"] == "Bearer synthetic"
    assert seen["headers"]["Origin"] == "http://127.0.0.1:4747"
    proxy = next(h for h in seen["handlers"] if isinstance(h, panel_alerts.urllib.request.ProxyHandler))
    assert proxy.proxies == {}
    redirect = next(h for h in seen["handlers"] if isinstance(h, type))
    assert redirect().redirect_request(None, None, 302, "", {}, "http://elsewhere/") is None


def test_adaptive_hunt_readiness_notifies_once_per_recording_and_respects_mute():
    rec = {"active": True, "mode": "adaptive", "name": "first", "hunt_progress": {"stage": 3}}
    state, count = {"paused": False}, []
    sounds = poller(lambda: {"alerts": snapshot(paused=state["paused"]), "cavebot": {"recording": rec}},
                    Clock(), [], on_hunt_ready=lambda: count.append(1))
    assert count == []
    rec["hunt_progress"]["stage"] = 4; sounds.poll_once(); sounds.poll_once(); assert len(count) == 1
    rec["hunt_progress"]["stage"] = 3; sounds.poll_once(); rec["hunt_progress"]["stage"] = 4; sounds.poll_once()
    assert len(count) == 1
    rec["active"] = False; sounds.poll_once(); rec["active"] = True; rec["name"] = "second"
    state["paused"] = True; sounds.poll_once()
    state["paused"] = False; sounds.poll_once(); assert len(count) == 1
    rec["name"] = "third"; sounds.poll_once(); assert len(count) == 2


def test_global_automation_pause_interrupts_signals_mutes_tests_and_resumes_on_new_chat():
    now, heard, interruptions, balloons = Clock(), [], [], []
    state = {"alerts": snapshot(paused=False), "active": False, "stage": 3}

    def response():
        return {"automation_paused": state["active"], "alerts": state["alerts"],
                "cavebot": {"recording": {"active": True, "mode": "adaptive", "name": "hunt",
                                          "hunt_progress": {"stage": state["stage"]}}}}
    p = poller(response, now, heard, on_mute=lambda: interruptions.append(1), on_hunt_ready=lambda: balloons.append(1))

    def tick():
        now.ms += 750
        p.poll_once()
    state["alerts"] = snapshot([event(1), event(2)], paused=False)
    tick()
    assert heard == ["message"]
    state["active"], state["stage"] = True, 4
    state["alerts"] = snapshot([event(1), event(2), event(3)], paused=True, automation_paused=True, silenced_through=3)
    tick(); tick()
    assert p.panel_pause(False)["paused"] is True
    assert p.test_sound() is False
    assert len(interruptions) == 1
    assert balloons == []
    state["active"] = False
    state["alerts"] = {**state["alerts"], "paused": False, "automation_paused": False}
    tick(); assert heard == ["message"]
    state["alerts"] = snapshot([event(4, at=now.ms / 1000)], paused=False, silenced_through=3)
    tick()
    assert heard == ["message", "message"]


class FakeProcess:
    def __init__(self, log, ident):
        self.log, self.ident = log, ident

    def kill(self):
        self.log.append(self.ident)


def test_interrupting_audio_does_not_beep_on_cancellation_or_clear_the_next_sound():
    callbacks, killed, fallback = [], [], []

    def run(args, on_exit):
        callbacks.append(on_exit)
        return FakeProcess(killed, len(callbacks))
    audio = NotificationAudio(lambda: fallback.append(True), run=run, platform="win32", helper="x.py", python="pythonw.exe")
    audio.play("vitals"); audio.interrupt(); audio.interrupt()
    assert audio.play("message") is True
    callbacks[0](1)  # the killed helper exits with an error
    assert audio.play("player") is False
    assert killed == [1] and fallback == []
    callbacks[1](0)
    assert audio.play("player") is True
    audio.stop()


def test_every_kind_uses_the_same_hidden_helper_and_busy_or_stopped_signals_stay_silent(tmp_path):
    calls, done, killed = [], [], []
    helper = tmp_path / "test's app" / "windows_alert.py"

    def run(args, on_exit):
        calls.append(args)
        done.append(on_exit)
        return FakeProcess(killed, 1)
    audio = NotificationAudio(lambda: None, run=run, platform="win32", helper=helper,
                              python=r"C:\DBV\venv\Scripts\pythonw.exe")
    assert audio.play("message") is True
    assert audio.play("player") is False
    assert calls[0] == [r"C:\DBV\venv\Scripts\pythonw.exe", "-I", "-B", str(helper), "message"]
    done[0](0); audio.play("untrusted command")
    assert calls[1][-1] == "player"
    audio.stop()
    assert killed == [1]
    assert audio.play("message") is False


def test_other_systems_fall_back_to_the_bell():
    rung = []
    audio = NotificationAudio(lambda: rung.append(True), run=lambda *a: pytest.fail("no helper"), platform="linux")
    assert audio.play("message") is True and rung == [True]
