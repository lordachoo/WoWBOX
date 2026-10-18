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
--   /bb rule <name>         turn a WoWBox automation rule on/off (the start of its name is enough)
-- The party panel (BoxBridgePanel.lua):
--   /bb panel               show/hide it on this character: who follows whom, who casts what on whom, HP/mana
--   /bb panel alert         sound + warning when someone stops following (on/off)
--   /bb panel reset         put it back in its default place
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

-- watched spells (Words.lua `spells`, what WoWBox's `ready:` conditions ask about): bit i set while spell i+1
-- could be cast now: known, off its cooldown and the global cooldown, enough mana/rage, and (for a spell cast at
-- your target) the target in range: a shot from too close fails without going on cooldown, so it'd stay "ready"
local MAX_SPELLS, MAX_DOTS = 7, 14
local DOT_REFRESH = 2 -- a DoT of yours with less than this left counts as gone: cast it again

local function castable(name)
  local start, duration, enabled = GetSpellCooldown(name)
  local ok = start and enabled == 1 and (start == 0 or duration == 0) and IsUsableSpell(name)
  return ok and IsSpellInRange(name, "target") ~= 0 -- nil: not a spell for the target
end

local function spellsReady()
  local f = BoxBridgeWords
  if type(f) ~= "table" or type(f.spells) ~= "table" then return 0 end
  local v = 0
  for i, name in ipairs(f.spells) do
    if i > MAX_SPELLS then break end
    if castable(name) then v = v + 2 ^ (i - 1) end
  end
  return v
end

-- is my `name` on the target, with more than DOT_REFRESH seconds left?
local function myDot(name)
  local _, _, _, _, _, _, expires, caster = UnitDebuff("target", name, nil, "PLAYER")
  if not expires then return false end
  if caster and caster ~= "player" then return false end
  return expires == 0 or expires - GetTime() > DOT_REFRESH
end

-- watched DoTs (Words.lua `dots`, what WoWBox's `dot:` conditions ask about): bit i set while DoT i+1 should go on
-- the target now: castable (as above) and not on the target from you, or about to run out
local function dotsNeeded()
  local f = BoxBridgeWords
  if type(f) ~= "table" or type(f.dots) ~= "table" or not UnitExists("target") then return 0 end
  local v = 0
  for i, name in ipairs(f.dots) do
    if i > MAX_DOTS then break end
    if castable(name) and not myDot(name) then v = v + 2 ^ (i - 1) end
  end
  return v
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

-- which unit the target is, as a 24-bit number (0: no target): the same mob gives the same number in every
-- client, so WoWBox can tell whether two characters have the same target (`same_target:`)
local NAME_BYTES = 20
local function targetId()
  local guid = UnitExists("target") and (UnitGUID and UnitGUID("target") or UnitName("target")) or nil
  if not guid then return 0 end
  local h = 0
  for i = 1, #guid do h = (h * 31 + string.byte(guid, i)) % 16777216 end
  return h == 0 and 1 or h
end

-- how far the target really is (CheckInteractDistance: the game measures it, whatever spell ranges or auras say):
-- 1 within about 10 yards, 2 within about 28, 3 farther or no target
local function targetRange()
  if not UnitExists("target") or not CheckInteractDistance then return 3 end
  if CheckInteractDistance("target", 3) then return 1 end
  return CheckInteractDistance("target", 4) and 2 or 3
end
local RANGE_WORDS = { "within about 10 yards", "within about 28 yards", "farther than 28 yards" }

-- "can't hit": the game just refused a swing or cast at this target because of facing or distance
-- ("You are facing the wrong way!", "You are too far away!", "Target needs to be in front of you.", "Out of range.").
-- Closer than ~10 yards is the one thing CheckInteractDistance can't tell, so this is how a rule learns
-- a character in "range" still can't hit. Lasts CANT_HIT_SECONDS, and only while the target stays the same.
local CANT_HIT_SECONDS = 3
local cantHitUntil, cantHitGuid = 0, nil
local function targetGuid() return UnitGUID and UnitGUID("target") end
-- matched loosely (lower case, anywhere in the text): the client and the server word these differently
-- ("Out of range" / "Out of range.", custom servers' own wording)
local CANT_HIT_WORDS = { "out of range", "too far away", "facing the wrong way", "front of you", "infront" }
local lastError, lastErrorAt = nil, 0
local function isCantHit(msg)
  if type(msg) ~= "string" then return false end
  for _, k in ipairs({ ERR_BADATTACKFACING, ERR_BADATTACKPOS, SPELL_FAILED_UNIT_NOT_INFRONT,
      SPELL_FAILED_OUT_OF_RANGE, ERR_SPELL_OUT_OF_RANGE, ERR_OUT_OF_RANGE }) do
    if msg == k then return true end
  end
  local m = string.lower(msg)
  for _, w in ipairs(CANT_HIT_WORDS) do
    if string.find(m, w, 1, true) then return true end
  end
  return false
end
local function cantHit()
  return cantHitUntil > 0 and UnitExists("target") and GetTime() < cantHitUntil and targetGuid() == cantHitGuid
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
  local ready, dots = spellsReady(), dotsNeeded()
  local d = {
    MAGIC_A, MAGIC_B, counter,
    pct("player"), powerPct("player"), (UnitPowerType("player") or 0) % 8 + math.floor(dots / 512) * 8,
    tExists and pct("target") or 0,
    bits(tExists, tExists and UnitCanAttack("player", "target"), tExists and UnitIsDead("target"),
      tExists and UnitIsPlayer("target"), tExists and UnitAffectingCombat("target")) + (dots % 8) * 32,
    bits(UnitAffectingCombat("player"), IsResting(), inParty, inRaid) + (ready % 16) * 16,
    inputFlags() + (math.floor(dots / 8) % 64) * 4,
    bits(casting, channeling) + filterVersion() * 4,
    bits(IsMounted(), UnitIsDead("player"), UnitIsGhost("player"), UnitIsAFK("player"), following)
      + math.floor(ready / 16) * 32,
    partyHp[1], partyHp[2], partyHp[3], partyHp[4],
    level, partyBits + (dropped % 16) * 16,
  }
  addText(d, UnitName("player"), NAME_BYTES)
  local id = targetId()
  d[#d + 1] = math.floor(id / 65536)
  d[#d + 1] = math.floor(id / 256) % 256
  d[#d + 1] = id % 256
  d[#d + 1] = (CLASS_IDS[classToken or ""] or 0) + targetRange() * 16 + (cantHit() and 64 or 0)
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
events:RegisterEvent("UI_ERROR_MESSAGE")
events:SetScript("OnEvent", function(_, event, msg, sender)
  if event == "PLAYER_ENTERING_WORLD" or string.find(event, "^ZONE_CHANGED") then
    mapToCurrentZone()
    if event ~= "PLAYER_ENTERING_WORLD" then return end
  end
  if event == "AUTOFOLLOW_BEGIN" or event == "AUTOFOLLOW_END" then
    following = event == "AUTOFOLLOW_BEGIN"
    return
  end
  if event == "UI_ERROR_MESSAGE" then
    lastError, lastErrorAt = msg, GetTime()
    if isCantHit(msg) then cantHitUntil, cantHitGuid = GetTime() + CANT_HIT_SECONDS, targetGuid() end
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
local function bbCommand(msg)
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
      if type(BoxBridgeWords.spells) == "table" then
        local r = spellsReady()
        for i, name in ipairs(BoxBridgeWords.spells) do
          local raw = " (not a spell of yours)"
          if GetSpellCooldown(name) then -- only ask the game about spells this character has
            local okR, range = pcall(IsSpellInRange, name, "target")
            local okU, usable = pcall(IsUsableSpell, name)
            raw = " | in range: " .. tostring(okR and range or "error") .. ", usable: " .. tostring(okU and usable or "error")
          end
          p("  watched spell: " .. name .. (math.floor(r / 2 ^ (i - 1)) % 2 == 1 and " (ready)" or " (not ready)") .. raw)
        end
      end
      if type(BoxBridgeWords.dots) == "table" then
        for _, name in ipairs(BoxBridgeWords.dots) do
          local why = not UnitExists("target") and "no target" or myDot(name) and "on your target"
            or not castable(name) and "not castable now (cooldown, mana, range or unknown)" or "NEEDED"
          p("  watched DoT: " .. name .. " (" .. why .. ")")
        end
      end
      p("target id: " .. (UnitExists("target") and (targetId() .. " (characters on the same mob show the same id)")
        or "no target") .. " | guid: " .. tostring(UnitGUID and UnitGUID("target")))
      if UnitExists("target") then p("target distance: " .. RANGE_WORDS[targetRange()]) end
      if lastError then
        p(string.format("last game error: \"%s\" %.0fs ago (%s)", tostring(lastError), GetTime() - lastErrorAt,
          isCantHit(lastError) and "counts as can't hit" or "not a can't-hit error"))
      end
      p("can't hit now: " .. (cantHit() and "yes" or "no"))
    end
    p(dropped .. " line(s) dropped since login because the queue was full")
    return
  elseif cmd == "rule" then
    local text = "rule " .. (arg or "")
    if (arg or "") == "" then
      p("usage: /bb rule <name>   turns that WoWBox rule on or off; the start of its name is enough")
    elseif #text > MAX_COMMAND_BYTES then
      p("rule name too long (max " .. (MAX_COMMAND_BYTES - 5) .. " bytes): the start of it is enough")
    elseif not db.enabled then
      p("BoxBridge is off (/bb to turn it on): WoWBox can't see the command")
    else
      queueChat(COMMAND, "@wowbox", text)
      p("-> WoWBox: " .. text .. " (on/off)")
    end
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
  elseif cmd == "panel" then
    if BoxBridgePanel_Command then BoxBridgePanel_Command(string.lower(arg or "")) end
    return
  elseif cmd == "status" then
    p(string.format("enabled=%s size=%d height=%s (using %d) hz=%d chat=%s chat filter=%s dropped=%d",
      tostring(db.enabled), db.size, tostring(db.height), renderHeight(), db.hz, tostring(db.chat),
      filterVersion() == 0 and "none" or ("version " .. filterVersion()), dropped))
    return
  else
    p("usage: /bb [all|others <command> | to <who> <command> | stack | follow | stay | mirror | rule <name> | panel | "
      .. "size <px> | height <px> | hz <n> | chat | words | test <Name> <text> | status]")
    return
  end
  apply()
end
-- an error in a command is shown, not swallowed (the game hides Lua errors and leaves the line in the chat box)
SlashCmdList["BOXBRIDGE"] = function(msg)
  local ok, err = pcall(bbCommand, msg)
  if not ok then p("error in /bb " .. tostring(msg) .. ": " .. tostring(err)) end
end

-- for BoxBridgePanel.lua: tell WoWBox something (a /bb command it handles itself). False while the bridge is off.
BoxBridgeAPI = {
  wowbox = function(text)
    if not db or not db.enabled then return false end
    return queueChat(COMMAND, "@wowbox", text)
  end,
}
