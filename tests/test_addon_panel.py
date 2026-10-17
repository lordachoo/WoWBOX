"""Runs addon/BoxBridge/BoxBridge.lua + BoxBridgePanel.lua in Lua 5.1 against a stubbed WoW API."""
from pathlib import Path

import numpy as np
import pytest

lupa = pytest.importorskip("lupa")
from lupa import lua51  # noqa: E402

from test_addon_lua import STUBS  # noqa: E402
from wowbox.bridge.decoder import NUM_BLOCKS, ChatEvent, decode_frame  # noqa: E402
from wowbox.panel import FLAGS, encode  # noqa: E402

ADDON = Path(__file__).resolve().parents[1] / "addon" / "BoxBridge"

PANEL_STUBS = r"""
now = 0
function GetTime() return now end
local make = CreateFrame
function CreateFrame(kind, name, parent) local f = make(kind, name); f.name = name; return f end
function Obj:GetName() return self.name end
function Obj:CreateFontString() return newobj("fs") end
function Obj:SetText(s) self.text = s end
function Obj:SetValue(v) self.value = v end
function Obj:GetPoint() return "TOPLEFT", nil, "TOPLEFT", 40, -50 end
function Obj:Click() self.scripts.OnClick(self) end
sent, bindings, warnings, sounds = {}, {}, {}, 0
function SendAddonMessage(prefix, msg, ch) sent[#sent + 1] = msg end
function SetOverrideBindingClick(owner, prio, key, button) bindings[key] = button end
function InCombatLockdown() return false end
RAID_CLASS_COLORS = setmetatable({}, { __index = function() return { r = 1, g = 1, b = 1 } end })
PowerBarColor = setmetatable({}, { __index = function() return { r = 0, g = 0, b = 1 } end })
function UnitIsDeadOrGhost() return nil end
function UnitInRange() return 1 end
function UnitBuff() return nil end
RaidWarningFrame, ChatTypeInfo = newobj("rw"), { RAID_WARNING = {} }
function RaidNotice_AddMessage(f, msg) warnings[#warnings + 1] = msg end
function PlaySound() sounds = sounds + 1 end
function strsplit(sep, s)
  local out = {}
  for part in string.gmatch(s .. sep, "(.-)" .. "%" .. sep) do out[#out + 1] = part end
  return unpack(out)
end
U.party1.name, U.party2.name = "Kunth", "Gnope"
"""


@pytest.fixture
def g():
    lua = lua51.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUBS + PANEL_STUBS)
    for f in ("BoxBridge.lua", "BoxBridgePanel.lua"):
        lua.execute((ADDON / f).read_text(encoding="utf-8"))
    g = lua.globals()
    fire(g, "PLAYER_LOGIN")
    return g


def fire(g, event, *args):
    for f in g.eventFrames.values():
        f.scripts["OnEvent"](f, event, *args)


def bridge_lines(g, seconds=3.0):
    """The chat/command lines the bridge strip shows over the next `seconds`."""
    lines = []
    frame = g.BoxBridgeFrame
    for _ in range(int(seconds / 0.1)):
        g.now += 0.1
        frame.scripts["OnUpdate"](frame, 1.0)
        img = np.zeros((8, 8 * NUM_BLOCKS + 30, 3), dtype=np.uint8)
        for i in range(NUM_BLOCKS):
            t = g.textures[i + 1]
            img[:, i * 8:(i + 1) * 8] = [round(t.color[k] * 255) for k in (1, 2, 3)]
        ev = decode_frame(img)
        if isinstance(ev, ChatEvent) and (not lines or lines[-1] != (ev.sender, ev.text)):
            lines.append((ev.sender, ev.text))
    return lines


def tick(g, seconds=0.2):
    panel = g.BoxBridgePanel
    for _ in range(int(seconds / 0.1)):
        g.now += 0.1
        panel.scripts["OnUpdate"](panel, 0.1)


def press(g, bits):
    g.BoxBridgePanelKey1.Click(g.BoxBridgePanelKey1)
    for i in range(len(FLAGS)):
        k = g.BoxBridgePanelKey3 if bits >> i & 1 else g.BoxBridgePanelKey2
        k.Click(k)


def row(g, i):
    """What row i (1 = you) shows: name, follow, activity."""
    r = g.BoxBridgePanel.rows[i]
    return r.name.text, r.follow.text, r.cast.label.text


def test_keys_are_bound_and_panel_asks_wowbox(g):
    assert dict(g.bindings.items()) == {"ALT-CTRL-SHIFT-F7": "BoxBridgePanelKey1",
                                        "ALT-CTRL-SHIFT-F8": "BoxBridgePanelKey2",
                                        "ALT-CTRL-SHIFT-F9": "BoxBridgePanelKey3"}
    assert not g.BoxBridgePanel.shown
    g.SlashCmdList["BOXBRIDGE"]("panel")
    assert g.BoxBridgePanel.shown and g.BoxBridgeDB.panel
    assert bridge_lines(g) == [("@wowbox", "panel on")]


def test_state_keys_show_in_the_title_and_are_answered(g):
    g.SlashCmdList["BOXBRIDGE"]("panel")
    bridge_lines(g)
    bits = encode({"broadcast": True, "automation": True, "dry_run": True, "follow": True})
    press(g, bits)
    assert bridge_lines(g) == [("@wowbox", f"panel {bits}")]
    tick(g)
    title = g.BoxBridgePanel.title.text
    assert "BROADCAST ON" in title and "AUTO (dry run)" in title and "FOLLOW" in title and "MIRROR" not in title
    g.BoxBridgePanelKey3.Click(g.BoxBridgePanelKey3)  # a stray bit without a start key: ignored
    assert bridge_lines(g) == []


def test_party_messages_follow_and_casts(g):
    g.SlashCmdList["BOXBRIDGE"]("panel")
    fire(g, "CHAT_MSG_ADDON", "BoxBridge", "F|Tankadin", "PARTY", "Kunth")
    fire(g, "CHAT_MSG_ADDON", "BoxBridge", "C|Healing Wave|Gnope", "PARTY", "Kunth")
    tick(g)
    name, follow, act = row(g, 2)
    assert "Kunth" in name and "Tankadin" in follow and act == "Healing Wave  > Gnope"
    fire(g, "CHAT_MSG_ADDON", "BoxBridge", "X|Healing Wave", "PARTY", "Kunth")
    tick(g)
    assert "(failed)" in row(g, 2)[2]
    tick(g, 3)
    assert row(g, 2)[2] == ""
    assert row(g, 3)[1] == ""  # Gnope never said: no BoxBridge 1.3 there


def test_follow_drop_warns_after_a_moment(g):
    g.SlashCmdList["BOXBRIDGE"]("panel")
    fire(g, "CHAT_MSG_ADDON", "BoxBridge", "F|Tankadin", "PARTY", "Kunth")
    fire(g, "CHAT_MSG_ADDON", "BoxBridge", "F|", "PARTY", "Kunth")
    tick(g, 1)
    assert list(g.warnings.values()) == []
    assert "not following" in row(g, 2)[1]
    tick(g, 1.5)
    assert list(g.warnings.values()) == ["Kunth stopped following"] and g.sounds == 1


def test_no_warning_when_it_comes_back_or_follow_mode_is_off(g):
    g.SlashCmdList["BOXBRIDGE"]("panel")
    for msg in ("F|Tankadin", "F|", "F|Tankadin"):  # WoWBox's follow mode put it back
        fire(g, "CHAT_MSG_ADDON", "BoxBridge", msg, "PARTY", "Kunth")
    tick(g, 3)
    press(g, encode({"broadcast": True}))  # follow mode OFF: you told them to stay
    for msg in ("F|Tankadin", "F|"):
        fire(g, "CHAT_MSG_ADDON", "BoxBridge", msg, "PARTY", "Kunth")
    tick(g, 3)
    g.SlashCmdList["BOXBRIDGE"]("panel alert")
    assert list(g.warnings.values()) == [] and not g.BoxBridgeDB.panelAlert


def test_own_follow_and_casts_are_told_to_the_party(g):
    fire(g, "AUTOFOLLOW_BEGIN", "Tinkerdawn")
    fire(g, "UNIT_SPELLCAST_SENT", "player", "Healing Wave", "", "Gnope")
    fire(g, "UNIT_SPELLCAST_SENT", "target", "Ignored", "", "")
    fire(g, "AUTOFOLLOW_END")
    reporter_tick = [f for f in g.eventFrames.values() if f.scripts["OnUpdate"]]
    assert list(g.sent.values()) == ["F|Tinkerdawn", "C|Healing Wave|Gnope", "F|"]
    for f in reporter_tick:  # after login: ask the party, say what we follow
        f.scripts["OnUpdate"](f, 5)
    assert list(g.sent.values())[-2:] == ["Q", "F|"]


# ---------------------------------------------------------------- watched spells, /bb rule, the panel's rules
SPELL_STUBS = r"""
BoxBridgeWords = { version = 5, channels = {}, spells = { "Avenger's Shield", "Holy Shield", "Consecration",
  "A", "B", "Exorcism" }, panel = { "Avenger's Shield on cooldown", "Buffs" } }
CD = { ["Avenger's Shield"] = { 0, 0 }, ["Holy Shield"] = { 100, 8 }, ["Consecration"] = { 0, 0 },
  A = { 99, 1.5 }, B = { 99, 1.5 }, Exorcism = { 0, 0 } }
NOMANA = { Consecration = true }
function GetSpellCooldown(name) local c = CD[name]; if c then return c[1], c[2], 1 end end
function IsUsableSpell(name) return CD[name] ~= nil and not NOMANA[name] end
RANGE = { Exorcism = 1 }  -- in range of the target; others: no range (nil) unless set
function IsSpellInRange(name, unit) return RANGE[name] end
"""


@pytest.fixture
def gs():
    lua = lua51.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUBS + PANEL_STUBS + SPELL_STUBS)
    for f in ("BoxBridge.lua", "BoxBridgePanel.lua"):
        lua.execute((ADDON / f).read_text(encoding="utf-8"))
    g = lua.globals()
    fire(g, "PLAYER_LOGIN")
    return g


def state_frame(g):
    frame = g.BoxBridgeFrame
    frame.scripts["OnUpdate"](frame, 1.0)
    img = np.zeros((8, 8 * NUM_BLOCKS + 30, 3), dtype=np.uint8)
    for i in range(NUM_BLOCKS):
        t = g.textures[i + 1]
        img[:, i * 8:(i + 1) * 8] = [round(t.color[k] * 255) for k in (1, 2, 3)]
    return decode_frame(img)


def test_watched_spells_ready_bits(gs):
    st = state_frame(gs)
    # Avenger's Shield ready; Holy Shield cooling down; Consecration no mana; A, B on the global cooldown
    assert st.ready_bits == 0b100001
    assert st.combat and st.chat_filter == 5  # the other bits in those bytes are untouched
    gs.RANGE["Exorcism"] = 0  # too close / too far: not ready, or a failing cast would fire forever
    assert state_frame(gs).ready_bits == 0b000001


def test_bb_rule_goes_to_wowbox(gs):
    gs.SlashCmdList["BOXBRIDGE"]("rule aven")
    gs.SlashCmdList["BOXBRIDGE"]("rule")  # no name: usage only
    assert bridge_lines(gs) == [("@wowbox", "rule aven")]


def test_panel_shows_its_rules_on_and_off(gs):
    gs.SlashCmdList["BOXBRIDGE"]("panel")
    tick(gs)
    assert "Avenger's Shield on cooldown off" in gs.BoxBridgePanel.ruleLine.text
    press(gs, encode({"broadcast": True, "rule1": True}))
    tick(gs)
    line = gs.BoxBridgePanel.ruleLine.text
    assert "Avenger's Shield on cooldown ON" in line and "Buffs off" in line


DOT_STUBS = r"""
BoxBridgeWords.dots = { "Corruption", "Curse of Agony", "Haunt", "Splurt", "Unknown Spell" }
CD.Corruption, CD["Curse of Agony"], CD.Haunt, CD.Splurt = { 0, 0 }, { 0, 0 }, { 50, 8 }, { 0, 0 }
DEBUFFS = { Corruption = { 10, "player" }, ["Curse of Agony"] = { 1, "player" }, Splurt = { 30, "party1" } }
function UnitDebuff(unit, name)  -- left: seconds left on the target; who cast it
  local d = DEBUFFS[name]
  if d then return name, nil, nil, 0, nil, 30, now + d[1], d[2] end
end
"""


@pytest.fixture
def gd():
    lua = lua51.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUBS + PANEL_STUBS + SPELL_STUBS + DOT_STUBS)
    for f in ("BoxBridge.lua", "BoxBridgePanel.lua"):
        lua.execute((ADDON / f).read_text(encoding="utf-8"))
    g = lua.globals()
    fire(g, "PLAYER_LOGIN")
    return g


def test_dots_needed(gd):
    st = state_frame(gd)
    # Corruption: mine, 10 s left -> no. Curse of Agony: mine but 1 s left -> recast. Haunt: on cooldown -> no.
    # Splurt: only someone else's on the target -> mine is missing. Unknown Spell: not castable -> no.
    assert st.dot_bits == 0b01010
    assert st.power_type == 0 and st.target_exists and not st.typing  # the bytes' own bits are untouched
