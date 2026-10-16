-- BoxBridge (WotLK 3.3.5a): writes game state as a row of solid colour blocks at the top-left corner of the screen.
-- Each block carries 2 bytes in red/green; blue alternates 224/32 so the app can find every block edge
-- however the game stretches its picture. Format: src/wowbox/bridge/decoder.py (keep both in sync).
--   /boxbridge              toggle on/off
--   /boxbridge size <px>    block size (default 8; bigger is more robust)
--   /boxbridge height <px>  override the render height used for pixel snapping (default: from gxResolution)
--   /boxbridge hz <n>       update rate (default 15)
--   /boxbridge status       print current settings
--   /boxbridge chat         toggle passing chat lines (whisper/party/raid/guild/say/yell) to WoWBox
--   /boxbridge words        show the chat filter WoWBox wrote (Words.lua): only lines your rules could use pass
--   /boxbridge test <Name> <text>   pretend <Name> whispered <text> (to test WoWBox chat rules)
-- Commands for your whole team, typed by WoWBox into each client's chat ("/" is optional):
--   /bb all <command>       every client, this one included     e.g. /bb all reload
--   /bb cmd <command>       same as all
--   /bb others <command>    every client except this one         e.g. /bb others follow Tinkerdawn
--   /bb to <who> <command>  a character name, class:X, role:X, leader or a slot number
-- WoWBox actions:
--   /bb stack               everyone walks onto your spot and facing
--   /bb follow | stay       follow mode on (everyone follows the leader) / off
--   /bb mirror              movement mirroring on/off
--                                                                e.g. /bb to Gnope assist Tinkerdawn

local NUM_BYTES = 56
local NUM_BLOCKS = NUM_BYTES / 2
local MAGIC_A, MAGIC_B, MAGIC_CHAT = 0xB0, 0x0E, 0x0C
local CLASS_IDS = { WARRIOR = 1, PALADIN = 2, HUNTER = 3, ROGUE = 4, PRIEST = 5, DEATHKNIGHT = 6, SHAMAN = 7,
  MAGE = 8, WARLOCK = 9, DRUID = 11 }
local MARK_EVEN, MARK_ODD = 224, 32

local defaults = { enabled = true, size = 8, height = nil, hz = 15, chat = true, chathold = 0.5 }
-- chat event -> channel byte (see CHAT_CHANNELS in decoder.py)
local CHANNELS = {
  CHAT_MSG_WHISPER = 1,
  CHAT_MSG_PARTY = 2, CHAT_MSG_PARTY_LEADER = 2,
  CHAT_MSG_RAID = 3, CHAT_MSG_RAID_LEADER = 3, CHAT_MSG_RAID_WARNING = 3,
  CHAT_MSG_GUILD = 4, CHAT_MSG_OFFICER = 5,
  CHAT_MSG_SAY = 6, CHAT_MSG_YELL = 7,
}
local COMMAND = 8 -- a /bb all|others|to command: sender field = who, text = the command
local CHANNEL_NAMES = { "whisper", "party", "raid", "guild", "officer", "say", "yell" } -- by channel byte
local PRIORITY = { [1] = 3, [2] = 3, [COMMAND] = 4 } -- whispers, party and /bb go first; the rest are 1
local MAX_COMMAND_BYTES = 27
local MAX_TEXT_BYTES = 27
local MAX_QUEUE = 10
local chatQueue = {}
local chatSeq = 0
local dropped = 0 -- lines thrown away because the queue was full (WoWBox sees it mod 16)
local following = false -- AUTOFOLLOW_BEGIN .. AUTOFOLLOW_END
local db
local frame = CreateFrame("Frame", "BoxBridgeFrame", UIParent)
local blocks = {}
local counter = 0
local acc = 0

local function p(msg) DEFAULT_CHAT_FRAME:AddMessage("|cff66ccff[BoxBridge]|r " .. msg) end

local function byte(frac)
  if not frac or frac ~= frac then return 0 end -- nil / NaN
  if frac < 0 then frac = 0 elseif frac > 1 then frac = 1 end
  return math.floor(frac * 255 + 0.5)
end

local function pct(unit)
  local max = UnitHealthMax(unit)
  if not max or max == 0 then return 0 end
  return byte(UnitHealth(unit) / max)
end

local function powerPct(unit)
  local cur, max
  if UnitPower then cur, max = UnitPower(unit), UnitPowerMax(unit) else cur, max = UnitMana(unit), UnitManaMax(unit) end
  if not max or max == 0 then return 0 end
  return byte(cur / max)
end

local function bits(...)
  local v = 0
  for i = 1, select("#", ...) do
    if select(i, ...) then v = v + 2 ^ (i - 1) end
  end
  return v
end

local function chatOpen()
  local eb = ChatEdit_GetActiveWindow and ChatEdit_GetActiveWindow()
  if eb and eb:IsShown() and (not eb.HasFocus or eb:HasFocus()) then return true end
  for _, name in ipairs({ "ChatFrameEditBox", "ChatFrame1EditBox" }) do
    local f = _G[name]
    if f and f.HasFocus and f:HasFocus() then return true end
  end
  return false
end

local function inputFlags()
  return bits(GetCurrentKeyBoardFocus and GetCurrentKeyBoardFocus() ~= nil, chatOpen())
end

-- append text as exactly n bytes, zero padded; a cut never splits a UTF-8 character
local function addText(out, text, n)
  text = text or ""
  local len = math.min(#text, n)
  while len > 0 and len < #text do
    local b = string.byte(text, len + 1)
    if b < 128 or b >= 192 then break end -- next byte starts a character: safe to cut here
    len = len - 1
  end
  for i = 1, n do out[#out + 1] = i <= len and string.byte(text, i) or 0 end
end

-- chat text without colour codes and links ("|Hitem:...|h[Sword]|h" -> "[Sword]")
local function plain(text)
  text = string.gsub(text or "", "|c%x%x%x%x%x%x%x%x", "")
  text = string.gsub(text, "|r", "")
  text = string.gsub(text, "|H.-|h(.-)|h", "%1")
  text = string.gsub(text, "|T.-|t", "")
  return text
end

-- the version of Words.lua loaded (0-63; 0 = none: every line passes)
local function filterVersion()
  local f = BoxBridgeWords
  if type(f) ~= "table" or type(f.channels) ~= "table" then return 0 end
  return (tonumber(f.version) or 0) % 64
end

-- does a chat rule want this line? Returns whether it passes and, when one of the filter's words matched,
-- where it starts, how long it is and its number in the channel's list
local function wanted(channel, text)
  if filterVersion() == 0 then return true end
  local words = BoxBridgeWords.channels[CHANNEL_NAMES[channel] or ""]
  if words == "all" then return true end
  if type(words) ~= "table" then return false end
  local lower = string.lower(text)
  local at, len, index
  for n, w in ipairs(words) do
    local i = string.find(lower, w, 1, true)
    if i and (not at or i < at or (i == at and #w > len)) then at, len, index = i, #w, n end
  end
  if not at then return false end
  return true, at, len, index
end

-- a matched word is sent as 2 bytes (KEYWORD, its number in the list) followed by the text after it, so what
-- follows it (a name, an item) gets all of the 27 bytes WoWBox sees; WoWBox puts the word back
local KEYWORD = 1

local function priority(ev) return PRIORITY[ev.channel] or 1 end

-- queue a line for WoWBox. Returns false (and why) when it isn't passed on
local function queueChat(channel, sender, text)
  text = plain(text)
  local length = #text
  if channel ~= COMMAND then
    local ok, at, len, index = wanted(channel, text)
    if not ok then return false, "no chat rule listens for it" end
    if index and index < 256 then
      text = string.sub(text, at + len)
      length = #text -- what's after the word: WoWBox tells from it whether the line was cut
      text = string.char(KEYWORD, index) .. text
    end
  end
  chatSeq = chatSeq % 255 + 1
  local ev = { seq = chatSeq, channel = channel, sender = sender or "", text = text, length = length }
  local first = (chatQueue[1] and chatQueue[1].untilTime) and 2 or 1 -- never pull the line on screen
  if #chatQueue >= MAX_QUEUE then
    local victim
    for i = first, #chatQueue do -- the oldest of the least important
      if not victim or priority(chatQueue[i]) < priority(chatQueue[victim]) then victim = i end
    end
    dropped = dropped + 1
    if not victim or priority(ev) < priority(chatQueue[victim]) then return false, "queue full" end
    table.remove(chatQueue, victim)
  end
  local pos = #chatQueue + 1 -- behind everything at least as important
  while pos > first and priority(chatQueue[pos - 1]) < priority(ev) do pos = pos - 1 end
  table.insert(chatQueue, pos, ev)
  return true
end

local function chatFrame(ev)
  local d = { MAGIC_A, MAGIC_CHAT, counter, ev.seq, ev.channel, math.min(ev.length or #ev.text, 255) }
  addText(d, ev.sender, 20)
  addText(d, ev.text, MAX_TEXT_BYTES)
  d[#d + 1] = inputFlags() -- this frame stands in for state frames while it's held
  return d
end

local function u24(out, n)
  n = math.floor(n + 0.5) % 16777216
  out[#out + 1] = math.floor(n / 65536)
  out[#out + 1] = math.floor(n / 256) % 256
  out[#out + 1] = n % 256
end

local function u16(out, n)
  n = math.floor(n + 0.5) % 65536
  out[#out + 1] = math.floor(n / 256)
  out[#out + 1] = n % 256
end

-- map position, facing, map and frame rate, for stacking: 11 bytes (see decoder.py)
local function addPosition(out)
  local x, y = GetPlayerMapPosition("player")
  local mapShown = WorldMapFrame and WorldMapFrame:IsShown() -- you may be looking at another zone's map
  local valid = x and y and not (x == 0 and y == 0) and not mapShown
  if not valid then x, y = 0, 0 end
  local facing = GetPlayerFacing and GetPlayerFacing() or 0
  local mapId = GetCurrentMapAreaID and GetCurrentMapAreaID() or 0
  local level = GetCurrentMapDungeonLevel and GetCurrentMapDungeonLevel() or 0
  u24(out, math.max(0, math.min(1, x)) * 16777215)
  u24(out, math.max(0, math.min(1, y)) * 16777215)
  u16(out, facing / (2 * math.pi) * 65536)
  u16(out, mapId)
  out[#out + 1] = (valid and 1 or 0) + (level % 128) * 2
  out[#out + 1] = math.min(255, math.floor((GetFramerate and GetFramerate() or 0) + 0.5))
end

-- GetPlayerMapPosition is relative to the map the world map shows: keep that on the zone you're in
local function mapToCurrentZone()
  if SetMapToCurrentZone and not (WorldMapFrame and WorldMapFrame:IsShown()) then SetMapToCurrentZone() end
end

local function snapshot()
  local tExists = UnitExists("target") and true or false
  local inRaid = GetNumRaidMembers() > 0
  local inParty = GetNumPartyMembers() > 0
  local casting = UnitCastingInfo("player") ~= nil
  local channeling = UnitChannelInfo("player") ~= nil
  local partyHp, partyBits = {}, 0
  for i = 1, 4 do
    local u = "party" .. i
    if UnitExists(u) then
      partyHp[i] = pct(u)
      partyBits = partyBits + 2 ^ (i - 1)
    else
      partyHp[i] = 0
    end
  end
  local level = UnitLevel("player") or 0
  if level < 0 then level = 0 elseif level > 255 then level = 255 end
  local _, classToken = UnitClass("player")
  local d = {
    MAGIC_A, MAGIC_B, counter,
    pct("player"), powerPct("player"), UnitPowerType("player") or 0,
    tExists and pct("target") or 0,
    bits(tExists, tExists and UnitCanAttack("player", "target"), tExists and UnitIsDead("target"),
      tExists and UnitIsPlayer("target"), tExists and UnitAffectingCombat("target")),
    bits(UnitAffectingCombat("player"), IsResting(), inParty, inRaid),
    inputFlags(),
    bits(casting, channeling) + filterVersion() * 4,
    bits(IsMounted(), UnitIsDead("player"), UnitIsGhost("player"), UnitIsAFK("player"), following),
    partyHp[1], partyHp[2], partyHp[3], partyHp[4],
    level, partyBits + (dropped % 16) * 16,
  }
  addText(d, UnitName("player"), 23)
  d[#d + 1] = CLASS_IDS[classToken or ""] or 0
  addPosition(d)
  return d
end

-- the frame to show now: the oldest queued chat line while it is held, otherwise the state
local function nextFrame()
  local ev = chatQueue[1]
  if ev then
    local now = GetTime()
    ev.untilTime = ev.untilTime or now + db.chathold
    if now < ev.untilTime then return chatFrame(ev) end
    table.remove(chatQueue, 1) -- done: show one state frame before the next line
  end
  return snapshot()
end

local function render()
  local d = nextFrame()
  local sum, xor = 0, 0
  for i = 1, NUM_BYTES - 2 do
    sum = sum + d[i]
    xor = bit.bxor(xor, d[i])
  end
  d[NUM_BYTES - 1] = sum % 256
  d[NUM_BYTES] = xor
  for i = 1, NUM_BLOCKS do
    local mark = (i % 2 == 1) and MARK_EVEN or MARK_ODD -- block 1 is "even" (index 0 in the app)
    blocks[i]:SetVertexColor(d[2 * i - 1] / 255, d[2 * i] / 255, mark / 255, 1)
  end
end

local function renderHeight()
  if db.height then return db.height end
  local h = string.match(GetCVar("gxResolution") or "", "%d+x(%d+)")
  return tonumber(h) or 768
end

local function layout()
  -- scale so that 1 unit in this frame = 1 physical pixel
  frame:SetScale((768 / renderHeight()) / UIParent:GetEffectiveScale())
  frame:ClearAllPoints()
  frame:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 0, 0)
  frame:SetWidth(db.size * NUM_BLOCKS)
  frame:SetHeight(db.size)
  for i = 1, NUM_BLOCKS do
    local t = blocks[i]
    t:ClearAllPoints()
    t:SetPoint("TOPLEFT", frame, "TOPLEFT", (i - 1) * db.size, 0)
    t:SetWidth(db.size)
    t:SetHeight(db.size)
  end
end

local function apply()
  layout()
  if db.enabled then frame:Show() else frame:Hide() end
end

frame:SetFrameStrata("TOOLTIP")
frame:SetFrameLevel(127)
for i = 1, NUM_BLOCKS do
  local t = frame:CreateTexture(nil, "OVERLAY")
  t:SetTexture("Interface\\Buttons\\WHITE8X8")
  blocks[i] = t
end
frame:Hide()

frame:SetScript("OnUpdate", function(_, elapsed)
  acc = acc + elapsed
  if acc < 1 / db.hz then return end
  acc = 0
  counter = (counter + 1) % 256
  render()
end)

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("UI_SCALE_CHANGED")
events:RegisterEvent("DISPLAY_SIZE_CHANGED")
for event in pairs(CHANNELS) do events:RegisterEvent(event) end
for _, e in ipairs({ "PLAYER_ENTERING_WORLD", "ZONE_CHANGED", "ZONE_CHANGED_INDOORS", "ZONE_CHANGED_NEW_AREA" }) do
  events:RegisterEvent(e)
end
if WorldMapFrame then WorldMapFrame:HookScript("OnHide", mapToCurrentZone) end
events:RegisterEvent("AUTOFOLLOW_BEGIN")
events:RegisterEvent("AUTOFOLLOW_END")
events:SetScript("OnEvent", function(_, event, msg, sender)
  if event == "PLAYER_ENTERING_WORLD" or string.find(event, "^ZONE_CHANGED") then
    mapToCurrentZone()
    if event ~= "PLAYER_ENTERING_WORLD" then return end
  end
  if event == "AUTOFOLLOW_BEGIN" or event == "AUTOFOLLOW_END" then
    following = event == "AUTOFOLLOW_BEGIN"
    return
  end
  if CHANNELS[event] then
    if db and db.enabled and db.chat then queueChat(CHANNELS[event], sender, msg) end
    return
  end
  if event == "PLAYER_LOGIN" then
    BoxBridgeDB = BoxBridgeDB or {}
    db = BoxBridgeDB
    for k, v in pairs(defaults) do if db[k] == nil then db[k] = v end end
  end
  if db then apply() end
end)

SLASH_BOXBRIDGE1 = "/boxbridge"
SLASH_BOXBRIDGE2 = "/bb"
SlashCmdList["BOXBRIDGE"] = function(msg)
  if not db then return end
  local cmd, arg = string.match(msg or "", "^%s*(%S*)%s*(.-)%s*$")
  cmd = string.lower(cmd or "")
  local n = tonumber(arg)
  if cmd == "" then
    db.enabled = not db.enabled
    p(db.enabled and "ON" or "OFF")
  elseif cmd == "size" and n and n >= 2 and n <= 32 then
    db.size = math.floor(n)
    p("block size " .. db.size .. " px (set bridge.block_size: " .. db.size .. " in team.yaml)")
  elseif cmd == "height" then
    db.height = n
    p("render height " .. (n and tostring(n) or "auto (gxResolution)"))
  elseif cmd == "hz" and n and n >= 1 and n <= 60 then
    db.hz = n
    p("update rate " .. n .. " Hz")
  elseif cmd == "chat" then
    db.chat = not db.chat
    p("chat to WoWBox " .. (db.chat and "ON" or "OFF"))
    return
  elseif cmd == "test" then
    local who, text = string.match(arg or "", "^(%S+)%s*(.-)$")
    if not who then
      p("usage: /boxbridge test <Name> <text>")
      return
    end
    local ok, why = queueChat(CHANNELS.CHAT_MSG_WHISPER, who, text)
    p("test whisper from " .. who .. ": " .. text .. (ok and "" or " (not passed to WoWBox: " .. why .. ")"))
    return
  elseif cmd == "words" then
    local v = filterVersion()
    if v == 0 then
      p("no chat filter (Words.lua not written by WoWBox yet, or this BoxBridge is older): every line passes")
    else
      p("chat filter version " .. v .. " (WoWBox rewrites Words.lua when your chat rules change; /reload to load it)")
      for _, ch in ipairs(CHANNEL_NAMES) do
        local w = BoxBridgeWords.channels[ch]
        if w == "all" then p("  " .. ch .. ": every line")
        elseif type(w) == "table" then p("  " .. ch .. ": " .. table.concat(w, ", ")) end
      end
    end
    p(dropped .. " line(s) dropped since login because the queue was full")
    return
  elseif cmd == "stack" or cmd == "follow" or cmd == "stay" or cmd == "mirror" then
    if not db.enabled then
      p("BoxBridge is off (/bb to turn it on): WoWBox can't see the command")
      return
    end
    queueChat(COMMAND, "@wowbox", cmd)
    p("-> WoWBox: " .. cmd)
    return
  elseif cmd == "all" or cmd == "cmd" or cmd == "others" or cmd == "to" then
    local who, text = cmd == "others" and "others" or "all", arg or ""
    if cmd == "to" then who, text = string.match(text, "^(%S+)%s*(.-)$") end
    if not who or not text or text == "" then
      p("usage: /bb all <command> | /bb others <command> | /bb to <who> <command>   e.g. /bb all reload")
      return
    end
    if string.sub(text, 1, 1) ~= "/" then text = "/" .. text end
    if #text > MAX_COMMAND_BYTES then
      p("command too long (" .. #text .. " bytes, max " .. MAX_COMMAND_BYTES .. "): use a WoWBox macro instead")
      return
    end
    if not db.enabled then
      p("BoxBridge is off (/bb to turn it on): WoWBox can't see the command")
      return
    end
    queueChat(COMMAND, who, text)
    p("-> " .. who .. ": " .. text)
    return
  elseif cmd == "status" then
    p(string.format("enabled=%s size=%d height=%s (using %d) hz=%d chat=%s chat filter=%s dropped=%d",
      tostring(db.enabled), db.size, tostring(db.height), renderHeight(), db.hz, tostring(db.chat),
      filterVersion() == 0 and "none" or ("version " .. filterVersion()), dropped))
    return
  else
    p("usage: /bb [all|others <command> | to <who> <command> | stack | follow | stay | mirror | size <px> | "
      .. "height <px> | hz <n> | chat | words | test <Name> <text> | status]")
    return
  end
  apply()
end
