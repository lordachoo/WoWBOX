"""Runs the real addon/BoxBridge/BoxBridge.lua in Lua 5.1 against a stubbed WoW API and decodes what it draws."""
from pathlib import Path

import numpy as np
import pytest

lupa = pytest.importorskip("lupa")
from lupa import lua51  # noqa: E402

from wowbox.bridge.decoder import NUM_BLOCKS, decode, decode_frame  # noqa: E402

ADDON = Path(__file__).resolve().parents[1] / "addon" / "BoxBridge" / "BoxBridge.lua"

STUBS = r"""
local function noop() end
Obj = {}
Obj.__index = function(t, k) return rawget(Obj, k) or noop end
function newobj(kind) return setmetatable({ scripts = {}, kind = kind, shown = true }, Obj) end
textures, eventFrames = {}, {}
function Obj:SetScript(name, fn) self.scripts[name] = fn end
function Obj:CreateTexture() local t = newobj("tex"); t.color = { 1, 1, 1, 1 }; textures[#textures + 1] = t; return t end
function Obj:SetVertexColor(r, g, b, a) self.color = { r, g, b, a } end
function Obj:GetEffectiveScale() return 1 end
function Obj:RegisterEvent(e)
  if not rawget(self, "registered") then self.registered = true; eventFrames[#eventFrames + 1] = self end
end
function Obj:Show() self.shown = true end
function Obj:Hide() self.shown = false end
function Obj:IsShown() return self.shown end
function CreateFrame(kind, name) local f = newobj(kind); if name then _G[name] = f end; return f end
UIParent, DEFAULT_CHAT_FRAME, SlashCmdList = newobj("UIParent"), newobj("chat"), {}
bit = { bxor = function(a, b)
  local r, p = 0, 1
  a, b = math.floor(a), math.floor(b)
  while a > 0 or b > 0 do
    local x, y = a % 2, b % 2
    if x ~= y then r = r + p end
    a, b, p = (a - x) / 2, (b - y) / 2, p * 2
  end
  return r
end }
function GetCVar(k) if k == "gxResolution" then return "1920x1080" end end
U = {
  player = { hp = 50, max = 100, pw = 30, pmax = 60, ptype = 0, name = "Tankadin", class = "PALADIN", level = 80,
             combat = 1 },
  target = { hp = 25, max = 100, hostile = 1 },
  party1 = { hp = 100, max = 100 }, party2 = { hp = 10, max = 100 },
}
function UnitExists(u) return U[u] and 1 or nil end
function UnitHealth(u) return U[u].hp end
function UnitHealthMax(u) return U[u].max end
function UnitPower(u) return U[u].pw end
function UnitPowerMax(u) return U[u].pmax end
function UnitPowerType(u) return U[u].ptype, "MANA" end
function UnitName(u) return U[u].name end
function UnitClass(u) return "Paladin", U[u].class end
function UnitLevel(u) return U[u].level end
function UnitCanAttack(a, b) return U[b].hostile end
function UnitAffectingCombat(u) return U[u].combat end
function GetNumPartyMembers() return 2 end
function GetNumRaidMembers() return 0 end
function GetCurrentKeyBoardFocus() return nil end
function UnitCastingInfo(u) return nil end
function UnitChannelInfo(u) return nil end
function UnitIsDead(u) return nil end
function UnitIsGhost(u) return nil end
function UnitIsAFK(u) return nil end
function UnitIsPlayer(u) return u == "player" and 1 or nil end
function IsResting() return nil end
function IsMounted() return nil end
POS = { x = 0.25, y = 0.75, facing = 1.5, map = 301, level = 0 }
mapResets = 0
function GetPlayerMapPosition(u) return POS.x, POS.y end
function GetPlayerFacing() return POS.facing end
function GetCurrentMapAreaID() return POS.map end
function GetCurrentMapDungeonLevel() return POS.level end
function SetMapToCurrentZone() mapResets = mapResets + 1 end
function GetFramerate() return 59.7 end
"""


def run_addon(block_px: int = 8) -> np.ndarray:
    lua = lua51.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUBS)
    lua.execute(ADDON.read_text(encoding="utf-8"))
    g = lua.globals()
    for f in g.eventFrames.values():
        f.scripts["OnEvent"](f, "PLAYER_LOGIN")
    frame = g.BoxBridgeFrame
    frame.scripts["OnUpdate"](frame, 1.0)
    colors = [t.color for t in g.textures.values()]
    assert len(colors) == NUM_BLOCKS
    img = np.zeros((block_px, block_px * NUM_BLOCKS + 30, 3), dtype=np.uint8)
    img[:, NUM_BLOCKS * block_px:] = (90, 90, 60)  # game background after the strip
    for i, c in enumerate(colors):
        img[:, i * block_px:(i + 1) * block_px] = [round(c[k] * 255) for k in (1, 2, 3)]
    return img


def test_addon_output_decodes():
    st = decode(run_addon())
    assert st.name == "Tankadin" and st.class_token == "PALADIN"
    assert st.health_pct == pytest.approx(50, abs=0.5)
    assert st.power_pct == pytest.approx(50, abs=0.5)
    assert st.target_exists and st.target_hostile
    assert st.target_health_pct == pytest.approx(25, abs=0.5)
    assert st.combat and st.in_party and not st.typing
    assert st.level == 80
    assert st.party_exists == (True, True, False, False)
    assert st.party_health_pct[1] == pytest.approx(10, abs=0.5)
    assert st.position_ok and st.map_id == 301
    assert st.map_x == pytest.approx(0.25, abs=1e-4) and st.map_y == pytest.approx(0.75, abs=1e-4)
    assert st.facing == pytest.approx(1.5, abs=1e-3)
    assert st.fps == 60


def test_addon_position_invalid_in_instances_without_coordinates():
    lua, g = _load()
    g.POS.x, g.POS.y = 0, 0
    st = _draw(g)
    assert not st.position_ok and st.class_token == "PALADIN"
    _fire(g, "ZONE_CHANGED_NEW_AREA")
    assert g.mapResets >= 1


def _load():
    lua = lua51.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUBS + "\nnow = 0\nfunction GetTime() return now end\n")
    lua.execute(ADDON.read_text(encoding="utf-8"))
    g = lua.globals()
    for f in g.eventFrames.values():
        f.scripts["OnEvent"](f, "PLAYER_LOGIN")
    return lua, g


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


def test_addon_chat_frames():
    from wowbox.bridge.decoder import ChatEvent, ClientState
    lua, g = _load()
    _fire(g, "CHAT_MSG_WHISPER", "|cffff0000heal|r me, got |Hitem:123|h[Sword]|h", "Tinkerdawn")
    _fire(g, "CHAT_MSG_PARTY_LEADER", "a" + "€" * 15, "Gnope")  # 3-byte characters
    ev = _draw(g)
    assert isinstance(ev, ChatEvent)
    assert (ev.channel, ev.sender, ev.text) == ("whisper", "Tinkerdawn", "heal me, got [Sword]")
    g.now = 0.3
    assert _draw(g).seq == ev.seq  # still held
    g.now = 0.6
    assert isinstance(_draw(g), ClientState)  # one state frame between lines
    ev2 = _draw(g)
    assert ev2.channel == "party" and ev2.sender == "Gnope" and ev2.seq == ev.seq + 1
    assert ev2.text == "a" + "€" * 8 and ev2.truncated  # 27 bytes cut back to a character boundary (25)
    g.now = 2
    assert isinstance(_draw(g), ClientState)
    assert isinstance(_draw(g), ClientState)


def test_addon_chat_toggle_and_test_command():
    from wowbox.bridge.decoder import ChatEvent
    lua, g = _load()
    g.SlashCmdList["BOXBRIDGE"]("test Tinkerdawn heal pls")
    ev = _draw(g)
    assert isinstance(ev, ChatEvent) and (ev.sender, ev.text) == ("Tinkerdawn", "heal pls")
    g.now = 5
    _draw(g)
    g.SlashCmdList["BOXBRIDGE"]("chat")  # off
    _fire(g, "CHAT_MSG_WHISPER", "heal", "Tinkerdawn")
    assert not isinstance(_draw(g), ChatEvent)


def test_addon_follow_flag_and_typing_in_chat_frames():
    from wowbox.bridge.decoder import ClientState
    lua, g = _load()
    assert not _draw(g).following
    _fire(g, "AUTOFOLLOW_BEGIN", "Tinkerdawn")
    st = _draw(g)
    assert isinstance(st, ClientState) and st.following
    _fire(g, "AUTOFOLLOW_END")
    assert not _draw(g).following
    lua.execute("function GetCurrentKeyBoardFocus() return {} end")
    g.SlashCmdList["BOXBRIDGE"]("test Tinkerdawn heal")
    ev = _draw(g)
    assert ev.sender == "Tinkerdawn" and ev.typing


def test_addon_team_commands():
    from wowbox.bridge.decoder import ChatEvent
    lua, g = _load()
    bb = g.SlashCmdList["BOXBRIDGE"]
    bb("all reload")
    bb("others /follow Tinkerdawn")
    bb("to Gnope assist Tinkerdawn")
    bb("to Gnope")  # no command: usage, nothing queued
    bb("all cast Some Very Long Spell Name Rank 9")  # over 27 bytes: refused
    got = []
    for t in range(3):
        g.now = t * 1.0
        ev = _draw(g)
        assert isinstance(ev, ChatEvent)
        got.append((ev.channel, ev.sender, ev.text))
        g.now = t * 1.0 + 0.6
        _draw(g)  # the state frame between lines
    assert got == [("command", "all", "/reload"), ("command", "others", "/follow Tinkerdawn"),
                   ("command", "Gnope", "/assist Tinkerdawn")]
    g.now = 10
    assert not isinstance(_draw(g), ChatEvent)


def test_addon_wowbox_actions():
    from wowbox.bridge.decoder import ChatEvent
    lua, g = _load()
    g.SlashCmdList["BOXBRIDGE"]("stack")
    ev = _draw(g)
    assert isinstance(ev, ChatEvent) and (ev.channel, ev.sender, ev.text) == ("command", "@wowbox", "stack")


def test_addon_target_id():
    lua, g = _load()
    lua.execute('GUIDS = { target = "0xF13000064E0012A4" }\nfunction UnitGUID(u) return GUIDS[u] end')
    first = _draw(g).target_id
    assert first and _draw(g).target_id == first and _draw(g).name == "Tankadin"
    g.GUIDS.target = "0xF13000064E0012A5"  # another mob of the same kind
    assert _draw(g).target_id not in (0, first)
    g.U.target = None
    assert _draw(g).target_id == 0


def test_addon_target_range():
    lua, g = _load()
    lua.execute('DIST = 5\nfunction CheckInteractDistance(u, i) return DIST <= (i == 3 and 10 or 28) and 1 or nil end')
    assert _draw(g).target_range == 10 and _draw(g).class_token == "PALADIN"
    g.DIST = 20
    assert _draw(g).target_range == 28
    g.DIST = 60
    assert _draw(g).target_range == 100
    g.U.target = None
    assert _draw(g).target_range == 100


def test_addon_cant_hit():
    lua, g = _load()
    lua.execute('ERR_BADATTACKFACING = "You are facing the wrong way!"\n'
                'GUIDS = { target = "0xF13000064E0012A4" }\nfunction UnitGUID(u) return GUIDS[u] end')
    assert not _draw(g).cant_hit
    _fire(g, "UI_ERROR_MESSAGE", "Not enough mana")  # other errors don't count
    assert not _draw(g).cant_hit
    _fire(g, "UI_ERROR_MESSAGE", "You are facing the wrong way!")
    st = _draw(g)
    assert st.cant_hit and st.class_token == "PALADIN"
    g.now = 2.9
    assert _draw(g).cant_hit
    g.now = 3.1  # it wears off
    assert not _draw(g).cant_hit
    _fire(g, "UI_ERROR_MESSAGE", "You are facing the wrong way!")
    assert _draw(g).cant_hit
    g.GUIDS.target = "0xF13000064E0012A5"  # a new target starts clean
    assert not _draw(g).cant_hit
    g.now = 10
    _fire(g, "UI_ERROR_MESSAGE", "Out of range.")  # no global for it: matched by its words
    assert _draw(g).cant_hit
