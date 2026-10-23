-- BoxBridge junk: deletes items you don't want from your bags as they arrive, without asking. /bb junk
-- Each one deleted or sold gets a line in chat (`messages`, /bb junk messages off for none).
--
-- What goes: every gray item (`grays`, on by default once junk is on) and the items on your delete list
-- (/bb junk add). Never: Rare or better, anything on your keep list (/bb junk keep), anything in the bank.
-- With `sell` on, grays wait for the next vendor instead and are sold when you open it.
-- Settings are per character (BoxBridgeJunkDB); /bb all bb junk ... sets every client at once.

local STEP = 0.25 -- seconds between two deletes (or sales): the game needs a moment for each
local MAX_TRIES = 2 -- an item still in its slot after this many tries is left alone (the game won't let it go)
local NEVER_QUALITY = 3 -- Rare and better are never deleted, even when listed (a typo guard)
local BAGS = { 0, 1, 2, 3, 4 } -- backpack and bags: never the bank or the keyring

local db
local dirty, merchant, acc = false, false, 0
local deleted, sold, tries, warned = 0, 0, {}, {} -- tries: bag * 100 + slot -> { link, n } of the item there

local function p(msg) DEFAULT_CHAT_FRAME:AddMessage("|cff66ccff[BoxBridge]|r junk: " .. msg) end

local function onOff(v) return v and "ON" or "off" end

-- "deleted [Broken Fang] x3" (unless messages are off)
local function told(what, link, count)
  if db.messages then p(what .. " " .. link .. ((count or 1) > 1 and (" x" .. count) or "")) end
end

-- "Linen Cloth" from an item link or a typed name, lower case (the lists' keys)
local function key(text) return string.lower(string.match(text, "%[(.-)%]") or text) end

-- every item named in `arg`: each link in it, or else the whole text as a name
local function itemsIn(arg)
  local out = {}
  for name in string.gmatch(arg, "|h%[(.-)%]|h") do out[#out + 1] = name end
  if #out == 0 and arg ~= "" then out[1] = arg end
  return out
end

local function sorted(set)
  local out = {}
  for _, name in pairs(set) do out[#out + 1] = name end
  table.sort(out)
  return #out > 0 and table.concat(out, ", ") or "(empty)"
end

-- what to do with the item in bag, slot: "delete", "sell" or nil
local function verdict(bag, slot)
  local link = GetContainerItemLink(bag, slot)
  local t = tries[bag * 100 + slot]
  if t and t.link ~= link then tries[bag * 100 + slot], t = nil, nil end -- gone (or something else there now)
  if not link then return nil end
  local name, _, quality, _, _, _, _, _, _, _, price = GetItemInfo(link)
  name = name or string.match(link, "%[(.-)%]")
  if not quality or quality < 0 then quality = select(4, GetContainerItemInfo(bag, slot)) end
  if not name or not quality or quality < 0 then return nil end -- not known yet: next time
  local k = string.lower(name)
  if db.keep[k] or quality >= NEVER_QUALITY then return nil end
  if t and t.n >= MAX_TRIES then -- still here after that many tries: the game won't let it go
    if not warned[link] then
      warned[link] = true
      p("couldn't get rid of " .. link .. " (the game didn't let it go); leaving it")
    end
    return nil
  end
  if quality == 0 and db.grays then
    if db.sell and (price or 0) > 0 then return merchant and "sell" or nil end
    return "delete"
  end
  if db.list[k] then return "delete" end
  return nil
end

local function nextItem()
  for _, bag in ipairs(BAGS) do
    for slot = 1, GetContainerNumSlots(bag) or 0 do
      local _, _, locked = GetContainerItemInfo(bag, slot)
      local what = not locked and verdict(bag, slot)
      if what then return bag, slot, what end
    end
  end
end

-- one delete or sale. False when there's nothing (more) to do now
local function step()
  if CursorHasItem() or (GetCursorInfo and GetCursorInfo()) then return true end -- you're holding something: wait
  local bag, slot, what = nextItem()
  if not bag then return false end
  local link = GetContainerItemLink(bag, slot)
  local _, count = GetContainerItemInfo(bag, slot)
  local t = tries[bag * 100 + slot] or { link = link, n = 0 }
  tries[bag * 100 + slot], t.n = t, t.n + 1
  if what == "sell" then
    UseContainerItem(bag, slot)
    sold = sold + 1
    told("sold", link, count)
    return true
  end
  PickupContainerItem(bag, slot)
  local kind, _, onCursor = GetCursorInfo()
  if kind ~= "item" or (onCursor and onCursor ~= link) then -- not what we picked: put it back
    ClearCursor()
    return true
  end
  DeleteCursorItem()
  if CursorHasItem() then -- the game refused: put it back rather than leave it in your hand
    ClearCursor()
  else
    deleted = deleted + 1
    told("deleted", link, count)
  end
  return true
end

local frame = CreateFrame("Frame", "BoxBridgeJunkFrame")
frame:SetScript("OnUpdate", function(_, elapsed)
  if not dirty or not db or not db.enabled then return end
  acc = acc + elapsed
  if acc < STEP then return end
  acc = 0
  dirty = step()
end)
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("BAG_UPDATE")
frame:RegisterEvent("MERCHANT_SHOW")
frame:RegisterEvent("MERCHANT_CLOSED")
frame:SetScript("OnEvent", function(_, event)
  if event == "PLAYER_LOGIN" then
    BoxBridgeJunkDB = BoxBridgeJunkDB or {}
    db = BoxBridgeJunkDB
    if db.enabled == nil then db.enabled = false end
    if db.grays == nil then db.grays = true end
    if db.sell == nil then db.sell = false end
    if db.messages == nil then db.messages = true end
    db.list, db.keep = db.list or {}, db.keep or {}
  elseif event == "MERCHANT_SHOW" then
    merchant = true
  elseif event == "MERCHANT_CLOSED" then
    merchant = false
  end
  dirty = true
end)

local function setFlag(field, word, label)
  if word == "on" or word == "off" then
    db[field] = word == "on"
    dirty = true
  end
  p(label .. " " .. onOff(db[field]))
end

-- /bb junk [on|off | grays on|off | sell on|off | messages on|off | add|remove|keep|unkeep <item> | list]
function BoxBridgeJunk_Command(arg)
  if not db then return end
  local cmd, rest = string.match(arg or "", "^%s*(%S*)%s*(.-)%s*$")
  cmd = string.lower(cmd or "")
  if cmd == "on" or cmd == "off" then
    setFlag("enabled", cmd, "auto-delete")
  elseif cmd == "grays" or cmd == "greys" then
    setFlag("grays", string.lower(rest), "grays")
  elseif cmd == "sell" then
    setFlag("sell", string.lower(rest), "sell grays at vendors (instead of deleting them)")
  elseif cmd == "messages" then
    setFlag("messages", string.lower(rest), "a chat line for each item deleted or sold")
  elseif cmd == "add" or cmd == "remove" or cmd == "keep" or cmd == "unkeep" then
    local items = itemsIn(rest)
    if #items == 0 then
      p("usage: /bb junk " .. cmd .. " <item name or shift-click the item>")
      return
    end
    local list = (cmd == "add" or cmd == "remove") and db.list or db.keep
    for _, name in ipairs(items) do
      local k = key(name)
      if cmd == "add" or cmd == "keep" then
        list[k] = name
        if cmd == "add" then db.keep[k] = nil else db.list[k] = nil end
      else
        list[k] = nil
      end
      tries = {}
      p((cmd == "add" and "deleting " or cmd == "keep" and "keeping " or "no longer listed: ") .. name)
    end
    dirty = true
  elseif cmd == "list" or cmd == "" then
    p("auto-delete " .. onOff(db.enabled) .. " | grays " .. onOff(db.grays) .. " | sell grays at vendors "
      .. onOff(db.sell) .. " | messages " .. onOff(db.messages) .. " | " .. deleted .. " deleted, " .. sold .. " sold since login")
    p("delete list: " .. sorted(db.list))
    p("keep list: " .. sorted(db.keep))
  else
    p("usage: /bb junk [on | off | grays on|off | sell on|off | messages on|off | add <item> | remove <item> | "
      .. "keep <item> | unkeep <item> | list]")
  end
end
