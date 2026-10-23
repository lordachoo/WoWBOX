"""Runs addon/BoxBridge/BoxBridgeJunk.lua (with BoxBridge.lua, for /bb junk) in Lua 5.1 against stubbed bags."""
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")
from lupa import lua51  # noqa: E402

from test_addon_lua import STUBS  # noqa: E402

ADDON = Path(__file__).resolve().parents[1] / "addon" / "BoxBridge"

BAG_STUBS = r"""
now = 0
function GetTime() return now end
function select(n, ...) return (({ ... })[n]) end
-- BAGS[bag][slot] = { name, quality, price }; the link is built from the name
BAGS = { [0] = {}, [1] = {}, [2] = {}, [3] = {}, [4] = {}, [5] = {} }
cursor, deletes, sales, stuck = nil, {}, {}, {}
local function link(it) return "|cff9d9d9d|Hitem:1|h[" .. it[1] .. "]|h|r" end
function GetContainerNumSlots(bag) return BAGS[bag] and 16 or 0 end
function GetContainerItemLink(bag, slot) local it = BAGS[bag][slot]; return it and link(it) end
function GetContainerItemInfo(bag, slot)
  local it = BAGS[bag][slot]
  if it then return "tex", 1, nil, it[2] end
end
function GetItemInfo(l)
  for b = 0, 5 do for s = 1, 16 do
    local it = BAGS[b][s]
    if it and link(it) == l then return it[1], l, it[2], 1, 1, "", "", 1, "", "", it[3] end
  end end
end
function PickupContainerItem(bag, slot) cursor = { bag, slot } end
function CursorHasItem() return cursor ~= nil end
function GetCursorInfo()
  if cursor then return "item", 1, link(BAGS[cursor[1]][cursor[2]]) end
end
function ClearCursor() cursor = nil end
function DeleteCursorItem()
  local it = BAGS[cursor[1]][cursor[2]]
  if stuck[it[1]] then return end -- the game refuses: it stays on the cursor
  deletes[#deletes + 1] = it[1]
  BAGS[cursor[1]][cursor[2]] = nil
  cursor = nil
end
function UseContainerItem(bag, slot)
  sales[#sales + 1] = BAGS[bag][slot][1]
  BAGS[bag][slot] = nil
end
"""


@pytest.fixture
def g():
    lua = lua51.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUBS + BAG_STUBS)
    for f in ("BoxBridge.lua", "BoxBridgeJunk.lua"):
        lua.execute((ADDON / f).read_text(encoding="utf-8"))
    g = lua.globals()
    fire(g, "PLAYER_LOGIN")
    return g


def fire(g, event, *args):
    for f in g.eventFrames.values():
        f.scripts["OnEvent"](f, event, *args)


def run(g, seconds=5.0):
    f = g.BoxBridgeJunkFrame
    for _ in range(int(seconds / 0.1)):
        g.now += 0.1
        f.scripts["OnUpdate"](f, 0.1)


def bb(g, text):
    g.SlashCmdList["BOXBRIDGE"](text)


def put(g, bag, slot, name, quality, price=10):
    row = g.newobj("row")  # a Lua table: { name, quality, vendor price }
    row[1], row[2], row[3] = name, quality, price
    g.BAGS[bag][slot] = row


def lst(t):
    return [t[i] for i in range(1, len(t) + 1)]


def test_off_by_default_then_grays_and_the_list_go(g):
    put(g, 0, 1, "Broken Fang", 0)
    put(g, 0, 2, "Linen Cloth", 1)
    put(g, 1, 3, "Hearthstone", 1)
    put(g, 1, 4, "Cracked Bone", 0)
    fire(g, "BAG_UPDATE", 0)
    run(g)
    assert lst(g.deletes) == []  # off until you turn it on
    bb(g, "junk add |cffffffff|Hitem:2589|h[Linen Cloth]|h|r")
    bb(g, "junk on")
    run(g)
    assert sorted(lst(g.deletes)) == ["Broken Fang", "Cracked Bone", "Linen Cloth"]
    assert g.BAGS[1][3][1] == "Hearthstone" and g.cursor is None


def test_keep_list_wins_and_rare_items_are_never_deleted(g):
    put(g, 0, 1, "Lucky Gray", 0)
    put(g, 0, 2, "Epic Sword", 4)
    bb(g, "junk add Epic Sword")
    bb(g, "junk keep Lucky Gray")
    bb(g, "junk on")
    run(g)
    assert lst(g.deletes) == []
    bb(g, "junk unkeep lucky gray")  # any case
    run(g)
    assert lst(g.deletes) == ["Lucky Gray"]


def test_grays_off_leaves_grays_but_not_the_list(g):
    put(g, 0, 1, "Broken Fang", 0)
    put(g, 0, 2, "Linen Cloth", 1)
    bb(g, "junk grays off")
    bb(g, "junk add Linen Cloth")
    bb(g, "junk on")
    run(g)
    assert lst(g.deletes) == ["Linen Cloth"]


def test_a_stack_of_the_same_gray_in_many_slots_all_go(g):
    for slot in range(1, 7):
        put(g, 2, slot, "Broken Fang", 0)
    bb(g, "junk on")
    run(g)
    assert lst(g.deletes) == ["Broken Fang"] * 6


def test_an_item_the_game_wont_delete_is_put_back_and_left_alone(g):
    put(g, 0, 1, "Soulbound Gray", 0)
    put(g, 0, 2, "Broken Fang", 0)
    g.stuck["Soulbound Gray"] = True
    bb(g, "junk on")
    run(g, 10)
    assert lst(g.deletes) == ["Broken Fang"] and g.cursor is None
    assert g.BAGS[0][1][1] == "Soulbound Gray"


def test_waits_while_you_hold_an_item(g):
    put(g, 0, 1, "Broken Fang", 0)
    put(g, 3, 1, "My Sword", 2)
    g.cursor = g.newobj("c")
    g.cursor[1], g.cursor[2] = 3, 1
    bb(g, "junk on")
    run(g)
    assert lst(g.deletes) == []
    g.cursor = None
    fire(g, "BAG_UPDATE", 3)
    run(g)
    assert lst(g.deletes) == ["Broken Fang"]


def test_sell_keeps_grays_for_the_vendor(g):
    put(g, 0, 1, "Broken Fang", 0, price=12)
    put(g, 0, 2, "Worthless Gray", 0, price=0)
    put(g, 0, 3, "Linen Cloth", 1)
    bb(g, "junk add Linen Cloth")
    bb(g, "junk sell on")
    bb(g, "junk on")
    run(g)
    assert sorted(lst(g.deletes)) == ["Linen Cloth", "Worthless Gray"]  # no vendor price: nothing to sell it for
    fire(g, "MERCHANT_SHOW")
    run(g)
    assert lst(g.sales) == ["Broken Fang"]


def test_never_the_bank(g):
    put(g, 5, 1, "Broken Fang", 0)  # bag 5 is a bank bag
    bb(g, "junk on")
    run(g)
    assert lst(g.deletes) == []
