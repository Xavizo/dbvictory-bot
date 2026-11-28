"""English and Polish: the launcher's switch, its words, and the panel following the choice."""
import asyncio
import json
import re

import pytest

from dbv import language as lang
from launcher import desktop_app, texts
from tests.test_desktop_app import FakeWindow

TEXT_DIR = texts.TEXT_DIR


@pytest.fixture(autouse=True)
def polish_afterwards():
    yield
    texts.use("pl")


def test_polish_stays_the_default_and_a_choice_survives_in_the_state_folder(tmp_path):
    assert lang.read_language(tmp_path) == "pl"
    (tmp_path / ".dbvictory").mkdir()
    (tmp_path / ".dbvictory" / "language.json").write_text("{broken", encoding="utf-8")
    assert lang.read_language(tmp_path) == "pl"
    lang.write_language("en", tmp_path)
    assert lang.read_language(tmp_path) == "en"
    with pytest.raises(ValueError):
        lang.write_language("de", tmp_path)


def test_an_installation_not_yet_moved_keeps_its_choice_in_codex(tmp_path):
    (tmp_path / ".codex").mkdir()
    lang.write_language("en", tmp_path)
    assert (tmp_path / ".codex" / "language.json").is_file()
    assert lang.read_language(tmp_path) == "en"


def test_the_panel_watch_sees_a_switch_without_rereading_every_push(tmp_path):
    watch = lang.LanguageWatch(tmp_path)
    assert watch() == "pl"
    lang.write_language("en", tmp_path)
    assert watch() == "en"
    lang.write_language("pl", tmp_path)
    assert watch() == "pl"


def test_both_languages_name_the_same_texts_and_placeholders():
    pl = json.loads((TEXT_DIR / "pl.json").read_text(encoding="utf-8"))
    en = json.loads((TEXT_DIR / "en.json").read_text(encoding="utf-8"))
    assert set(pl) == set(en)
    for key in pl:
        assert sorted(re.findall(r"\{\w+\}", pl[key])) == sorted(re.findall(r"\{\w+\}", en[key])), key


def test_download_progress_reads_in_the_chosen_language():
    report = {"stage": "download", "done": 2_500_000, "total": 10_000_000, "started": 100.0, "kind": "patch"}
    assert desktop_app.describe_progress(report, now=101.0)["label"] == "Pobieranie łatki: 2,5 z 10,0 MB"
    texts.use("en")
    shown = desktop_app.describe_progress(report, now=101.0)
    assert shown["label"] == "Downloading patch: 2.5 of 10.0 MB"
    assert shown["rate"].startswith("2.5 MB/s · about ")


def test_switching_saves_the_choice_and_says_the_current_message_again(tmp_path):
    async def scenario():
        window = FakeWindow()
        launcher = desktop_app.Launcher(tmp_path, None, window, settings=tmp_path / "client.json",
                                        loop=asyncio.get_running_loop())
        launcher.set("closed", desktop_app.text("state.sessionClosed"))
        assert window.views[-1]["text"] == "Sesja została zamknięta."
        words = launcher.set_language("en")
        assert words["button.connect"] == "Connect to the game"
        assert window.views[-1]["text"] == "The session was closed."
        assert window.views[-1]["language"] == "en"
        assert lang.read_language(tmp_path) == "en"
        again = desktop_app.Launcher(tmp_path, None, FakeWindow(), settings=tmp_path / "client.json",
                                     loop=asyncio.get_running_loop())
        assert again.view["language"] == "en" and again.view["text"] == "Checking the local release queue…"
        with pytest.raises(ValueError):
            launcher.set_language("de")
    asyncio.run(scenario())


def test_a_read_only_launcher_switches_words_but_writes_nothing(tmp_path):
    async def scenario():
        launcher = desktop_app.Launcher(tmp_path, None, FakeWindow(), read_only=True,
                                        settings=tmp_path / "client.json", loop=asyncio.get_running_loop())
        launcher.set_language("en")
        assert texts.language() == "en" and lang.read_language(tmp_path) == "pl"
    asyncio.run(scenario())


def test_the_panel_state_carries_the_language(tmp_path, monkeypatch):
    from dbv.mcp_server import dashboard_api
    monkeypatch.setattr(dashboard_api, "_language", lang.LanguageWatch(tmp_path))
    assert json.loads(dashboard_api._build_state_json())["language"] == "pl"
    lang.write_language("en", tmp_path)
    assert json.loads(dashboard_api._build_state_json())["language"] == "en"
