-- BoxBridge party panel (WotLK 3.3.5a): what your team is doing, in the game, on the character you play.
--   /bb panel          show/hide it on this character (remembered per character)
--   /bb panel alert    sound + raid warning when someone stops following (on/off)
--   /bb panel reset    put it back in its default place (drag it to move it)
-- Rules marked "show on the in-game panel" in WoWBox get a line saying whether they're on (/bb rule <name>).
-- Each BoxBridge tells the party, in hidden addon messages, who it follows and what it casts on whom.
-- Health, mana, casting, drinking and range come straight from the game.
-- WoWBox's own state (broadcast, automation, follow mode...) arrives as three spare key presses WoWBox sends
-- to this window only (ALT-CTRL-SHIFT-F7 = start, F8 = 0, F9 = 1; see src/wowbox/panel.py), and the panel
-- answers through the bridge with what it got.

local PREFIX = "BoxBridge"
-- bit 0 first, as panel.py; rule1-8: the rules in Words.lua's `panel` list are on
local FLAGS = { "broadcast", "automation", "dry_run", "paused", "follow", "mirror",
  "rule1", "rule2", "rule3", "rule4", "rule5", "rule6", "rule7", "rule8" }
local KEYS = { "ALT-CTRL-SHIFT-F7", "ALT-CTRL-SHIFT-F8", "ALT-CTRL-SHIFT-F9" } -- start, 0, 1
local ALERT_DELAY = 2 -- WoWBox's follow mode re-follows a broken follow: only warn if it's still broken then
local SHOW_CAST = 2.5 -- an instant (or failed) cast stays on its row this long
local ROW_H, NAME_W, FOLLOW_W, HP_W, MANA_W, ACT_W = 16, 82, 92, 64, 44, 170
local WIDTH = NAME_W + FOLLOW_W + HP_W + MANA_W + ACT_W + 16

local db
local members = {} -- name -> { following = name | false | nil (no BoxBridge 1.3 there), cast = {...}, ... }
local state -- WoWBox's flags (FLAGS -> true/false), nil until it sends them
local bitsIn -- the flags being received, nil between messages
local lastQuery = 0

local function p(msg) DEFAULT_CHAT_FRAME:AddMessage("|cff66ccff[BoxBridge]|r " .. msg) end

local function short(name) return name and string.match(name, "^([^%-]+)") or name end

local function info(name)
  name = short(name)
  members[name] = members[name] or {}
  return members[name]
end

local function channel()
  if GetNumRaidMembers() > 0 then return "RAID" end
  if GetNumPartyMembers() > 0 then return "PARTY" end
end

local function tell(msg)
  local ch = channel()
  if ch then SendAddonMessage(PREFIX, msg, ch) end
end

-- ------------------------------------------------------------------ what this character does
local following = false

local function tellFollow() tell("F|" .. (following or "")) end

local reporter = CreateFrame("Frame")
for _, e in ipairs({ "AUTOFOLLOW_BEGIN", "AUTOFOLLOW_END", "UNIT_SPELLCAST_SENT", "UNIT_SPELLCAST_FAILED",
  "UNIT_SPELLCAST_INTERRUPTED", "PARTY_MEMBERS_CHANGED", "CHAT_MSG_ADDON", "PLAYER_LOGIN", "PLAYER_REGEN_ENABLED" }) do
  reporter:RegisterEvent(e)
end

-- ------------------------------------------------------------------ the panel
local panel = CreateFrame("Frame", "BoxBridgePanel", UIParent)
panel:SetWidth(WIDTH)
panel:SetFrameStrata("MEDIUM")
panel:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
  edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", tile = true, tileSize = 16, edgeSize = 12,
  insets = { left = 3, right = 3, top = 3, bottom = 3 } })
panel:SetBackdropColor(0, 0, 0, 0.7)
panel:SetBackdropBorderColor(0.4, 0.6, 0.8, 0.9)
panel:SetMovable(true)
panel:SetClampedToScreen(true)
panel:EnableMouse(true)
panel:RegisterForDrag("LeftButton")
panel:SetScript("OnDragStart", panel.StartMoving)
panel:SetScript("OnDragStop", function(self)
  self:StopMovingOrSizing()
  local point, _, relPoint, x, y = self:GetPoint()
  db.panelPos = { point, relPoint, x, y }
end)
panel:Hide()

local title = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
title:SetPoint("TOPLEFT", 8, -6)
title:SetPoint("TOPRIGHT", -8, -6)
title:SetJustifyH("LEFT")
local ruleLine = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
ruleLine:SetPoint("TOPLEFT", 8, -20)
ruleLine:SetPoint("TOPRIGHT", -8, -20)
ruleLine:SetJustifyH("LEFT")

local function text(parent, width, justify)
  local t = parent:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  t:SetWidth(width)
  t:SetHeight(ROW_H)
  t:SetJustifyH(justify or "LEFT")
  return t
end

local function bar(parent, width)
  local b = CreateFrame("StatusBar", nil, parent)
  b:SetStatusBarTexture("Interface\\TargetingFrame\\UI-StatusBar")
  b:SetMinMaxValues(0, 1)
  b:SetWidth(width - 4)
  b:SetHeight(ROW_H - 4)
  local bg = b:CreateTexture(nil, "BACKGROUND")
  bg:SetAllPoints()
  bg:SetTexture(0.15, 0.15, 0.15, 0.8)
  b.label = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.label:SetPoint("CENTER")
  return b
end

local rows = {}
for i = 1, 5 do
  local r = CreateFrame("Frame", nil, panel)
  r:SetWidth(WIDTH - 16)
  r:SetHeight(ROW_H)
  r.flash = r:CreateTexture(nil, "BACKGROUND")
  r.flash:SetAllPoints()
  r.flash:SetTexture(1, 0, 0, 0)
  r.name = text(r, NAME_W)
  r.name:SetPoint("LEFT")
  r.follow = text(r, FOLLOW_W)
  r.follow:SetPoint("LEFT", r.name, "RIGHT")
  r.hp = bar(r, HP_W)
  r.hp:SetPoint("LEFT", r.follow, "RIGHT", 2, 0)
  r.mana = bar(r, MANA_W)
  r.mana:SetPoint("LEFT", r.hp, "RIGHT", 4, 0)
  r.cast = bar(r, ACT_W)
  r.cast:SetPoint("LEFT", r.mana, "RIGHT", 4, 0)
  r.cast.label:ClearAllPoints()
  r.cast.label:SetPoint("LEFT", 3, 0)
  r.cast.label:SetPoint("RIGHT", -3, 0)
  r.cast.label:SetJustifyH("LEFT")
  r:Hide()
  rows[i] = r
end

panel.title, panel.ruleLine, panel.rows = title, ruleLine, rows -- for the tests

local function place()
  panel:ClearAllPoints()
  local pos = db.panelPos
  if pos then panel:SetPoint(pos[1], UIParent, pos[2], pos[3], pos[4])
  else panel:SetPoint("LEFT", UIParent, "LEFT", 20, 120) end
end

local function titleText()
  if not state then return "|cff888888WoWBox: no state yet|r" end
  local parts = {}
  local function add(on, s) if on then parts[#parts + 1] = s end end
  add(true, state.broadcast and "|cff40ff40BROADCAST ON|r" or "|cffff4040BROADCAST OFF|r")
  add(state.automation, state.dry_run and "|cffffd000AUTO (dry run)|r" or "|cff40c0ffAUTO|r")
  add(state.paused, "|cffff8000rules PAUSED|r")
  add(state.follow, "|cff40c0ffFOLLOW|r")
  add(state.mirror, "|cffc080ffMIRROR|r")
  return "WoWBox: " .. table.concat(parts, "  ")
end

-- the panel's rules (Words.lua `panel`) and whether WoWBox says they're on
local function rulesText()
  local names = type(BoxBridgeWords) == "table" and BoxBridgeWords.panel
  if type(names) ~= "table" or #names == 0 then return nil end
  local parts = {}
  for i, name in ipairs(names) do
    local on = state and state["rule" .. i]
    parts[#parts + 1] = (on and "|cff40ff40" or "|cff808080") .. name .. (on and " ON" or " off") .. "|r"
  end
  return table.concat(parts, "   ")
end

local function hasBuff(unit, ...)
  for i = 1, select("#", ...) do
    if UnitBuff(unit, (select(i, ...))) then return true end
  end
  return false
end

local function activity(unit, m, now)
  -- casting now: the bar fills; the target comes from that character's BoxBridge
  local spell, _, _, _, startMs, endMs = UnitCastingInfo(unit)
  local channeled = false
  if not spell then
    spell, _, _, _, startMs, endMs = UnitChannelInfo(unit)
    channeled = spell ~= nil
  end
  local c = m.cast
  if spell then
    local target = c and c.spell == spell and now - c.at < 15 and c.target
    local frac = (now * 1000 - startMs) / math.max(1, endMs - startMs)
    if channeled then frac = 1 - frac end
    return spell .. (target and target ~= "" and ("  > " .. target) or ""), frac, 1, 0.7, 0
  end
  if c and now - c.at < SHOW_CAST then
    local target = c.target ~= "" and ("  > " .. c.target) or ""
    if c.failed then return c.spell .. target .. "  (failed)", 1, 0.6, 0.1, 0.1 end
    return c.spell .. target, 1, 0.3, 0.3, 0.3
  end
  if hasBuff(unit, "Drink", "Food", "Refreshment") then return "drinking", 1, 0.2, 0.3, 0.6 end
  return "", 0, 0, 0, 0
end

local function updateRow(r, unit, now)
  local name = UnitName(unit)
  local m = info(name)
  local _, class = UnitClass(unit)
  local color = class and RAID_CLASS_COLORS[class] or { r = 1, g = 1, b = 1 }
  local dead = UnitIsDeadOrGhost(unit)
  local far = unit ~= "player" and not UnitInRange(unit)
  r.name:SetText(name or "?")
  r.name:SetTextColor(color.r, color.g, color.b)
  r:SetAlpha(far and 0.5 or 1)
  if unit == "player" then r.follow:SetText(following and ("|cff40ff40-> " .. short(following) .. "|r") or "")
  elseif m.following then r.follow:SetText("|cff40ff40-> " .. short(m.following) .. "|r")
  elseif m.following == false then r.follow:SetText("|cffff8000not following|r")
  else r.follow:SetText("") end -- no BoxBridge 1.3 there
  local hpMax, hp = UnitHealthMax(unit) or 0, UnitHealth(unit) or 0
  local hpFrac = hpMax > 0 and hp / hpMax or 0
  r.hp:SetValue(dead and 0 or hpFrac)
  r.hp:SetStatusBarColor(1 - hpFrac, hpFrac, 0)
  r.hp.label:SetText(dead and "|cffff4040DEAD|r" or (math.floor(hpFrac * 100 + 0.5) .. "%" .. (far and " far" or "")))
  local pMax, pw = UnitPowerMax(unit) or 0, UnitPower(unit) or 0
  r.mana:SetValue(pMax > 0 and pw / pMax or 0)
  local pc = PowerBarColor[UnitPowerType(unit)] or PowerBarColor["MANA"]
  r.mana:SetStatusBarColor(pc.r, pc.g, pc.b)
  r.mana.label:SetText(pMax > 0 and (math.floor(pw / pMax * 100 + 0.5) .. "%") or "")
  local act, frac, cr, cg, cb = activity(unit, m, now)
  r.cast:SetValue(frac)
  r.cast:SetStatusBarColor(cr, cg, cb)
  r.cast.label:SetText(act)
  -- the follow warning flashes the row
  local left = m.alertUntil and m.alertUntil - now or 0
  r.flash:SetTexture(1, 0, 0, left > 0 and 0.35 * (math.floor(left * 3) % 2) or 0)
  if UnitAffectingCombat(unit) and not dead then r.name:SetText("|cffff4040*|r" .. (name or "?")) end
end

local acc = 0
panel:SetScript("OnUpdate", function(_, elapsed)
  acc = acc + elapsed
  if acc < 0.1 then return end
  acc = 0
  local now = GetTime()
  title:SetText(titleText())
  local rt = rulesText()
  ruleLine:SetText(rt or "")
  local top = rt and 36 or 22
  local units = { "player" }
  for i = 1, GetNumPartyMembers() do units[#units + 1] = "party" .. i end
  for i, r in ipairs(rows) do
    r:ClearAllPoints()
    r:SetPoint("TOPLEFT", 8, -top - (i - 1) * (ROW_H + 1))
    if units[i] and UnitExists(units[i]) then
      updateRow(r, units[i], now)
      r:Show()
    else
      r:Hide()
    end
  end
  panel:SetHeight(top + 6 + #units * (ROW_H + 1))
  -- follow warnings that are due (see ALERT_DELAY)
  for name, m in pairs(members) do
    if m.checkAt and now >= m.checkAt then
      m.checkAt = nil
      local wanted = not state or state.follow -- WoWBox's follow mode off: you told them to stay
      if m.following == false and wanted and db.panelAlert and not UnitIsDeadOrGhost(name) then
        m.alertUntil = now + 4
        RaidNotice_AddMessage(RaidWarningFrame, name .. " stopped following", ChatTypeInfo["RAID_WARNING"])
        PlaySound("RaidWarning")
      end
    end
  end
end)

-- ------------------------------------------------------------------ WoWBox's state, as key presses
local function received()
  local value, flags = 0, {}
  for i, f in ipairs(FLAGS) do
    flags[f] = bitsIn[i] == 1
    if flags[f] then value = value + 2 ^ (i - 1) end
  end
  state, bitsIn = flags, nil
  BoxBridgeAPI.wowbox("panel " .. value) -- what it got: WoWBox sends it again if that's not the state
end

local keyOwner = CreateFrame("Frame")
local keyButtons = {}
for i = 1, 3 do
  local b = CreateFrame("Button", "BoxBridgePanelKey" .. i, UIParent)
  b:SetScript("OnClick", function()
    if i == 1 then bitsIn = {}
    elseif bitsIn then
      bitsIn[#bitsIn + 1] = i - 2
      if #bitsIn == #FLAGS then received() end
    end
  end)
  keyButtons[i] = b
end

local bound = false
local function bindKeys()
  if bound or InCombatLockdown() then return end -- bindings can't change in combat: again when it ends
  for i, key in ipairs(KEYS) do SetOverrideBindingClick(keyOwner, true, key, keyButtons[i]:GetName()) end
  bound = true
end

-- ------------------------------------------------------------------ events
local loginWait
reporter:SetScript("OnEvent", function(_, event, a1, a2, a3, a4)
  if event == "PLAYER_LOGIN" then
    BoxBridgeDB = BoxBridgeDB or {}
    db = BoxBridgeDB
    if db.panelAlert == nil then db.panelAlert = true end
    place()
    bindKeys()
    if db.panel then panel:Show() end
    loginWait = 3 -- then tell WoWBox (the bridge needs a moment) and ask the party what it's doing
  elseif event == "PLAYER_REGEN_ENABLED" then
    bindKeys()
  elseif event == "AUTOFOLLOW_BEGIN" or event == "AUTOFOLLOW_END" then
    following = event == "AUTOFOLLOW_BEGIN" and (a1 or "?") or false
    tellFollow()
  elseif event == "UNIT_SPELLCAST_SENT" and a1 == "player" then
    tell("C|" .. (a2 or "") .. "|" .. (a4 or ""))
  elseif (event == "UNIT_SPELLCAST_FAILED" or event == "UNIT_SPELLCAST_INTERRUPTED") and a1 == "player" then
    tell("X|" .. (a2 or ""))
  elseif event == "PARTY_MEMBERS_CHANGED" then
    tellFollow()
  elseif event == "CHAT_MSG_ADDON" and a1 == PREFIX then
    local kind, x, y = strsplit("|", a2 or "")
    local m = info(a4)
    if kind == "F" then
      local was = m.following
      m.following = x ~= "" and x or false
      if was and not m.following and short(a4) ~= UnitName("player") then m.checkAt = GetTime() + ALERT_DELAY end
      if m.following then m.checkAt, m.alertUntil = nil, nil end
    elseif kind == "C" then
      m.cast = { spell = x or "", target = y or "", at = GetTime() }
    elseif kind == "X" then
      if m.cast and m.cast.spell == x then m.cast.failed, m.cast.at = true, GetTime()
      else m.cast = { spell = x or "", target = "", at = GetTime(), failed = true } end
    elseif kind == "Q" and GetTime() - lastQuery > 1 then
      lastQuery = GetTime()
      tellFollow()
    end
  end
end)

reporter:SetScript("OnUpdate", function(_, elapsed)
  if not loginWait then return end
  loginWait = loginWait - elapsed
  if loginWait > 0 then return end
  loginWait = nil
  tell("Q")
  tellFollow()
  if db.panel then BoxBridgeAPI.wowbox("panel on") end
end)

function BoxBridgePanel_Command(arg)
  if not db then return end
  if arg == "alert" then
    db.panelAlert = not db.panelAlert
    p("follow warning " .. (db.panelAlert and "ON" or "OFF"))
  elseif arg == "reset" then
    db.panelPos = nil
    place()
    p("panel back in its default place")
  else
    db.panel = not db.panel
    if db.panel then
      panel:Show()
      tell("Q")
      p("party panel ON (drag to move; /bb panel alert, /bb panel reset)")
    else
      panel:Hide()
      p("party panel OFF")
    end
    if not BoxBridgeAPI.wowbox(db.panel and "panel on" or "panel off") then
      p("BoxBridge is off, so WoWBox's state can't be asked for (/bb turns it on)")
    end
  end
end
