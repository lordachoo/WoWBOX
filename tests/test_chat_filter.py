"""The chat filter: Words.lua written from the chat rules, read by the real addon, and the Controller's side."""
from pathlib import Path

import numpy as np
import pytest

from wowbox.bridge import words
from wowbox.bridge.decoder import ChatEvent, ClientState, NUM_BLOCKS, decode_frame, encode, encode_bytes
from wowbox.config import AutomationRule

ADDON = Path(__file__).resolve().parents[1] / "addon" / "BoxBridge" / "BoxBridge.lua"


def rule(name, channel, contains=None, enabled=True):
    chat = {"channel": channel}
    if contains is not None:
        chat["contains"] = contains
    return AutomationRule.model_validate({"name": name, "enabled": enabled, "chat": chat,
                                          "do": {"type": "/cast Heal"}})


RULES = [rule("heal", ["whisper", "party"], ["Heal"]), rule("light", "party", ["light heal"]),
         rule("raid-any", "raid"), rule("off", "guild", ["x"], enabled=False),
         rule("buff", "any", ["paladin buff"])]


def test_build_collects_channels_and_words():
    f = words.build(RULES)
    assert f.channels["whisper"] == ("heal", "paladin buff")
    assert f.channels["party"] == ("heal", "light heal", "paladin buff")
    assert f.channels["raid"] == words.ALL  # a rule without words takes every line
    assert f.channels["guild"] == ("paladin buff",)  # the disabled rule adds nothing
    assert f.passes("party", "can I get a HEAL") and not f.passes("party", "hello")
    assert f.passes("raid", "anything at all") and f.passes("say", "Paladin Buff pls")
    assert not words.build([]).passes("whisper", "heal")  # no chat rules: nothing to pass on


def test_sync_only_bumps_the_version_when_the_words_change(tmp_path):
    f = words.build(RULES)
    assert words.sync(tmp_path, f) == (1, True)
    assert words.sync(tmp_path, f) == (1, False)
    assert words.sync(tmp_path, words.build(RULES[:1])) == (2, True)
    (tmp_path / words.FILE_NAME).write_text("-- placeholder\n")  # a fresh copy of the addon
    assert words.sync(tmp_path, f) == (1, True)
    (tmp_path / words.FILE_NAME).write_text(words.lua(f, words.MAX_VERSION).replace("heal", "hael"))
    assert words.sync(tmp_path, f) == (1, True)  # wraps 63 -> 1


def test_lua_strings_are_escaped(tmp_path):
    f = words.build([rule("q", "whisper", ['say "hi"\\'])])
    assert 'say \\"hi\\"\\\\' in words.lua(f, 1)


def test_decoder_reads_filter_version_and_dropped_counter():
    data = encode_bytes({"cast_flags": 2, "chat_filter": 41, "party_bits": 0b0101, "chat_dropped": 19}, 7)
    st = decode_frame(encode({}, 7, 8, data))
    assert st.channeling and not st.casting and st.chat_filter == 41
    assert st.party_exists == (True, False, True, False) and st.chat_dropped == 3


# ---------------------------------------------------------------------- the real addon
lupa = pytest.importorskip("lupa")


def _load(words_lua: str | None):
    from test_addon_lua import STUBS
    lua = lupa.lua51.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUBS + "\nnow = 0\nfunction GetTime() return now end\n")
    if words_lua:
        lua.execute(words_lua)
    lua.execute(ADDON.read_text(encoding="utf-8"))
    g = lua.globals()
    for f in g.eventFrames.values():
        f.scripts["OnEvent"](f, "PLAYER_LOGIN")
    return g


def _draw(g, block_px=8):
    frame = g.BoxBridgeFrame
    frame.scripts["OnUpdate"](frame, 1.0)
    img = np.zeros((block_px, block_px * NUM_BLOCKS + 30, 3), dtype=np.uint8)
    for i, t in enumerate(g.textures.values()):
        img[:, i * block_px:(i + 1) * block_px] = [round(t.color[k] * 255) for k in (1, 2, 3)]
    return decode_frame(img)


def _fire(g, event, *args):
    for f in g.eventFrames.values():
        f.scripts["OnEvent"](f, event, *args)


def _lines(g, until=20.0):
    """Every chat line the addon shows, in order, stepping its clock."""
    out, t = [], 0.0
    while t < until:
        g.now = t
        x = _draw(g)
        if isinstance(x, ChatEvent) and (not out or out[-1].seq != x.seq):
            out.append(x)
        t += 0.1
    return out


def test_addon_without_words_passes_everything_and_reports_version_0():
    g = _load(None)
    assert _draw(g).chat_filter == 0
    _fire(g, "CHAT_MSG_RAID", "pull in 5", "Raidlead")
    assert [e.text for e in _lines(g)] == ["pull in 5"]


def test_addon_filters_by_channel_and_word():
    g = _load(words.lua(words.build(RULES[:2]), 9))
    st = _draw(g)
    assert isinstance(st, ClientState) and st.chat_filter == 9
    _fire(g, "CHAT_MSG_RAID", "heal me", "Raider")  # raid isn't listened to
    _fire(g, "CHAT_MSG_WHISPER", "lol", "Gnope")  # no word
    _fire(g, "CHAT_MSG_WHISPER", "|cffff0000HEAL|r pls", "Gnope")
    _fire(g, "CHAT_MSG_PARTY", "need a light heal", "Kunth")
    _fire(g, "CHAT_MSG_RAID", "spam", "Raider")
    assert [(e.channel, e.text) for e in _lines(g)] == [("whisper", "HEAL pls"), ("party", "need a light heal")]


def test_addon_sends_a_late_match_from_the_matched_word():
    g = _load(words.lua(words.build(RULES), 1))
    text = "hey guys sorry to bother but could I get paladin buff thanks"
    _fire(g, "CHAT_MSG_PARTY", text, "Gnope")
    (ev,) = _lines(g)
    assert ev.text.startswith("paladin buff") and ev.truncated and ev.length == len(text)


def test_addon_whispers_jump_the_queue_and_raid_spam_is_dropped_first():
    g = _load(None)  # no filter: everything queues, so the queue fills
    _fire(g, "CHAT_MSG_RAID", "spam 0", "Raider")
    _draw(g)  # on screen now: it stays first
    for i in range(1, 12):
        _fire(g, "CHAT_MSG_RAID", f"spam {i}", "Raider")
    _fire(g, "CHAT_MSG_WHISPER", "heal", "Gnope")
    lines = _lines(g, until=12)
    assert lines[1].channel == "whisper"  # right after the line that was already on screen
    assert len(lines) == 10 and lines[0].text == "spam 0"
    assert _draw(g).chat_dropped == 3  # 13 lines, room for 10


def test_addon_test_command_says_when_the_filter_drops_it():
    g = _load(words.lua(words.build(RULES[:1]), 2))
    printed = []
    g.DEFAULT_CHAT_FRAME.AddMessage = lambda _self, msg: printed.append(msg)
    g.SlashCmdList["BOXBRIDGE"]("test Gnope hello")
    assert "no chat rule listens for it" in printed[-1]
    g.SlashCmdList["BOXBRIDGE"]("words")
    assert any("whisper: heal" in m for m in printed)


# ---------------------------------------------------------------------- the Controller
@pytest.fixture
def controller(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from wowbox.app import Controller
    from wowbox.config import load_config
    from wowbox.platform.fake import FakeCapture, FakeInput, FakeWindows
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "team.yaml").write_text((root / "config" / "team.example.yaml").read_text(encoding="utf-8"),
                                        encoding="utf-8")
    game = tmp_path / "game" / "BoxBridge"
    game.mkdir(parents=True)
    cfg = load_config(tmp_path / "team.yaml")
    cfg.bridge.addon_dir = str(game)
    windows = FakeWindows()
    c = Controller(tmp_path / "team.yaml", cfg, windows, FakeInput(), lambda: FakeCapture())
    for slot in (1, 2):
        windows.add(0x100 * slot, pid=slot)
        cl = c.team.clients[slot]
        cl.hwnd, cl.pid, cl.alive = 0x100 * slot, slot, True
    return c, game


def test_controller_writes_words_and_flags_clients_that_need_a_reload(controller):
    c, game = controller
    c.cfg.automation = RULES
    c.sync_chat_filter()
    assert (game / "Words.lua").exists() and c.words_version == 1
    c.team.clients[1].state = ClientState(name="A", chat_filter=1)
    c.team.clients[2].state = ClientState(name="B", chat_filter=0)
    assert c.chat_filter_stale() == [2]
    c.cfg.bridge.chat_filter_reload = True
    c.team.clients[2].state = ClientState(name="B", chat_filter=0, combat=True)
    c._check_chat_filter(100.0)
    assert not c.router.waiting() and not c._reload_asked  # in combat: no /reload
    c.team.clients[2].state = ClientState(name="B", chat_filter=0)
    c._check_chat_filter(200.0)
    assert list(c._reload_asked) == [2]  # out of combat: /reload typed in that client only
    c.cfg.bridge.chat_filter = False
    c.sync_chat_filter()
    assert c.chat_filter_stale() == []


def test_controller_counts_dropped_lines(controller):
    c, _ = controller
    c._count_dropped(1, 14)  # first sight: just the baseline
    c._count_dropped(1, 2)  # wrapped: 4 more
    assert c.dropped == {1: 4}
    assert any("dropped 4" in a.text for a in c.activity.items())


def test_controller_without_the_addon_folder_says_so(controller, tmp_path):
    c, _ = controller
    c.cfg.bridge.addon_dir = str(tmp_path / "nope")
    c.sync_chat_filter()
    assert c.words_version is None and "isn't installed" in c.words_problem
