-- WoWBox Phase 0 spike helper (3.3.5a, Lua 5.1). Prints only to your own chat frame.
--   /wbs info      environment dump (S3/S9)
--   /wbs pattern   toggle the colour test pattern in the top-left corner (S7/S9)
--   /wbs keys      toggle the key logger: prints every key the client receives (S1/S2). ESC turns it off.
--   /wbs binds     toggle override bindings on 1, SHIFT-1, CTRL-1, ALT-1, F13-F19 that print when fired (S1/S2)
--   /wbs chat      toggle the chat edit box watcher (S9)

local function p(...)
  local t = {}
  for i = 1, select("#", ...) do t[#t + 1] = tostring((select(i, ...))) end
  DEFAULT_CHAT_FRAME:AddMessage("|cff33ff99[WBS]|r " .. table.concat(t, " "))
end

local function b(v) return v and "1" or "0" end

local function mods()
  return "shift=" .. b(IsShiftKeyDown()) .. " ctrl=" .. b(IsControlKeyDown()) .. " alt=" .. b(IsAltKeyDown())
    .. string.format(" t=%.3f", GetTime())
end

local function resolution()
  local res = GetCVar("gxResolution") or ""
  local w, h = string.match(res, "(%d+)x(%d+)")
  return tonumber(w), tonumber(h), res
end

---------------------------------------------------------------------------
-- info
---------------------------------------------------------------------------
local function cmdInfo()
  local version, build, date, toc = GetBuildInfo()
  p("build:", version, build, date, toc)
  local cvars = { "gxWindow", "gxMaximize", "gxResolution", "gxRefresh", "gxVSync", "maxFPS", "maxFPSBk",
    "useUiScale", "uiScale", "chatStyle", "gamma" }
  for _, cv in ipairs(cvars) do
    local ok, v = pcall(GetCVar, cv)
    p("cvar", cv, "=", ok and tostring(v) or "<error>")
  end
  local _, h = resolution()
  p("UIParent effective scale:", UIParent:GetEffectiveScale(), " GetScreenWidth/Height:", GetScreenWidth(), GetScreenHeight())
  if h then p("pattern frame scale for 1 unit = 1 px:", (768 / h) / UIParent:GetEffectiveScale()) end

  local f = CreateFrame("Frame")
  local tex = f:CreateTexture()
  local ok, err = pcall(tex.SetTexture, tex, 1, 0, 0, 1)
  p("Texture:SetTexture(1,0,0,1) ok=" .. b(ok), err and ("err=" .. tostring(err)) or "", "GetTexture=" .. tostring(tex:GetTexture()))
  p("Texture:SetColorTexture exists:", b(tex.SetColorTexture ~= nil))

  local globals = { "ChatFrameEditBox", "ChatFrame1EditBox", "ChatEdit_GetActiveWindow", "ChatEdit_GetLastActiveWindow",
    "GetCurrentKeyBoardFocus", "SetOverrideBindingClick", "NUM_CHAT_WINDOWS" }
  for _, n in ipairs(globals) do
    p("global", n, "exists=" .. b(_G[n] ~= nil), "type=" .. type(_G[n]))
  end
end

---------------------------------------------------------------------------
-- colour test pattern: 2 rows x 8 blocks of BLOCK px.
-- Row A uses Texture:SetTexture(r,g,b,a); row B uses WHITE8X8 + SetVertexColor.
-- Blocks 0-6 are fixed colours; block 7 is a frame counter (R = counter, G = 90, B = 165).
-- spikes/s7_capture.py has the same table; keep them in sync.
---------------------------------------------------------------------------
local BLOCK = 8
local COLORS = {
  { 255, 0, 0 }, { 0, 255, 0 }, { 0, 0, 255 }, { 255, 255, 255 },
  { 128, 64, 32 }, { 17, 34, 51 }, { 200, 150, 100 },
}
local pattern, rowA, rowB, counter, overrideH = nil, {}, {}, 0, nil
local rowAOk = true

local function setBlock(i, r, g, bl)
  if rowAOk then
    rowAOk = pcall(rowA[i].SetTexture, rowA[i], r / 255, g / 255, bl / 255, 1)
    if not rowAOk then p("row A: Texture:SetTexture(r,g,b,a) raised an error; row A disabled") end
  end
  rowB[i]:SetVertexColor(r / 255, g / 255, bl / 255, 1)
end

local function applyScale()
  local _, h = resolution()
  h = overrideH or h
  if not h then p("cannot parse gxResolution; use /wbs pattern <physical height>") return end
  pattern:SetScale((768 / h) / UIParent:GetEffectiveScale())
  p(string.format("pattern scaled for %d px tall render (eff scale %.5f)", h, pattern:GetEffectiveScale()))
end

local function buildPattern()
  pattern = CreateFrame("Frame", "WowboxSpikePattern", UIParent)
  pattern:SetFrameStrata("TOOLTIP")
  pattern:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 0, 0)
  pattern:SetWidth(BLOCK * 8)
  pattern:SetHeight(BLOCK * 2)
  for i = 0, 7 do
    local a = pattern:CreateTexture(nil, "OVERLAY")
    a:SetWidth(BLOCK) a:SetHeight(BLOCK)
    a:SetPoint("TOPLEFT", pattern, "TOPLEFT", i * BLOCK, 0)
    local bt = pattern:CreateTexture(nil, "OVERLAY")
    bt:SetTexture("Interface\\Buttons\\WHITE8X8")
    bt:SetWidth(BLOCK) bt:SetHeight(BLOCK)
    bt:SetPoint("TOPLEFT", pattern, "TOPLEFT", i * BLOCK, -BLOCK)
    rowA[i], rowB[i] = a, bt
  end
  for i, c in ipairs(COLORS) do setBlock(i - 1, c[1], c[2], c[3]) end
  local acc = 0
  pattern:SetScript("OnUpdate", function(_, elapsed)
    acc = acc + elapsed
    if acc < 0.05 then return end -- ~20 Hz
    acc = 0
    counter = (counter + 1) % 256
    setBlock(7, counter, 90, 165)
  end)
  pattern:RegisterEvent("UI_SCALE_CHANGED")
  pattern:RegisterEvent("DISPLAY_SIZE_CHANGED")
  pattern:SetScript("OnEvent", applyScale)
  pattern:Hide()
end

local function cmdPattern(arg)
  if not pattern then buildPattern() end
  local h = tonumber(arg)
  if h then overrideH = h end
  if pattern:IsShown() and not h then
    pattern:Hide()
    p("pattern OFF")
  else
    applyScale()
    pattern:Show()
    p("pattern ON (top-left corner, " .. (BLOCK * 8) .. "x" .. (BLOCK * 2) .. " px)")
  end
end

---------------------------------------------------------------------------
-- key logger: a keyboard-enabled frame swallows every key and prints it.
---------------------------------------------------------------------------
local keyFrame

local function cmdKeys()
  if not keyFrame then
    keyFrame = CreateFrame("Frame", "WowboxSpikeKeys", UIParent)
    keyFrame:SetAllPoints(UIParent)
    keyFrame:EnableKeyboard(true)
    keyFrame:SetScript("OnKeyDown", function(self, key)
      p("KEY DOWN", key, mods())
      if key == "ESCAPE" then self:Hide() p("key logger OFF") end
    end)
    keyFrame:SetScript("OnKeyUp", function(_, key) p("KEY UP  ", key, mods()) end)
    keyFrame:Hide()
  end
  if keyFrame:IsShown() then
    keyFrame:Hide()
    p("key logger OFF")
  else
    keyFrame:Show()
    p("key logger ON: all keys are captured and printed. Press ESC to stop.")
  end
end

---------------------------------------------------------------------------
-- override bindings: checks which *binding* the client resolves (incl. modifiers).
---------------------------------------------------------------------------
local bindOwner = CreateFrame("Frame", "WowboxSpikeBindOwner")
local bindsOn = false
local BIND_KEYS = { "1", "SHIFT-1", "CTRL-1", "ALT-1", "F13", "F14", "F15", "F16", "F17", "F18", "F19" }

local function bindButton(key)
  local name = "WowboxSpikeBind_" .. (string.gsub(key, "%-", "_"))
  local btn = _G[name]
  if not btn then
    btn = CreateFrame("Button", name, UIParent)
    btn:RegisterForClicks("AnyDown")
    btn:SetScript("OnClick", function() p("BIND fired:", key, mods()) end)
  end
  return name
end

local function cmdBinds()
  if InCombatLockdown() then p("can't change bindings in combat") return end
  if bindsOn then
    ClearOverrideBindings(bindOwner)
    bindsOn = false
    p("override bindings OFF (your normal bindings are back)")
  else
    for _, key in ipairs(BIND_KEYS) do SetOverrideBindingClick(bindOwner, true, key, bindButton(key)) end
    bindsOn = true
    p("override bindings ON for: " .. table.concat(BIND_KEYS, ", "))
  end
end

---------------------------------------------------------------------------
-- chat edit box watcher
---------------------------------------------------------------------------
local chatWatch, lastChat

local function chatState()
  local parts = {}
  local function add(name)
    local f = _G[name]
    if f and (f:IsShown() or (f.HasFocus and f:HasFocus())) then
      parts[#parts + 1] = string.format("%s(shown=%s,focus=%s)", name, b(f:IsShown()), b(f.HasFocus and f:HasFocus()))
    end
  end
  add("ChatFrameEditBox")
  for i = 1, (NUM_CHAT_WINDOWS or 10) do add("ChatFrame" .. i .. "EditBox") end
  local act = ChatEdit_GetActiveWindow and ChatEdit_GetActiveWindow()
  parts[#parts + 1] = "active=" .. (act and (act:GetName() or "?") or "nil")
  local kf = GetCurrentKeyBoardFocus and GetCurrentKeyBoardFocus()
  parts[#parts + 1] = "kbfocus=" .. (kf and (kf:GetName() or "?") or "nil")
  return table.concat(parts, " ")
end

local function cmdChat()
  if not chatWatch then
    chatWatch = CreateFrame("Frame")
    local acc = 0
    chatWatch:SetScript("OnUpdate", function(_, elapsed)
      acc = acc + elapsed
      if acc < 0.1 then return end
      acc = 0
      local s = chatState()
      if s ~= lastChat then lastChat = s p("CHAT", string.format("t=%.3f", GetTime()), s) end
    end)
    chatWatch:Hide()
  end
  if chatWatch:IsShown() then
    chatWatch:Hide()
    p("chat watcher OFF")
  else
    p("chatStyle =", tostring(GetCVar("chatStyle")),
      " ChatFrameEditBox exists=" .. b(_G.ChatFrameEditBox ~= nil),
      " ChatFrame1EditBox exists=" .. b(_G.ChatFrame1EditBox ~= nil))
    lastChat = nil
    chatWatch:Show()
    p("chat watcher ON: open/close chat with Enter, /, Esc, R (reply) and watch the CHAT lines.")
  end
end

---------------------------------------------------------------------------
SLASH_WOWBOXSPIKE1 = "/wbs"
SlashCmdList["WOWBOXSPIKE"] = function(msg)
  local cmd, arg = string.match(msg or "", "^%s*(%S*)%s*(.-)%s*$")
  cmd = string.lower(cmd or "")
  if cmd == "info" then cmdInfo()
  elseif cmd == "pattern" then cmdPattern(arg)
  elseif cmd == "keys" then cmdKeys()
  elseif cmd == "binds" then cmdBinds()
  elseif cmd == "chat" then cmdChat()
  else
    p("commands: /wbs info | pattern [physicalHeight] | keys | binds | chat")
  end
end

local loader = CreateFrame("Frame")
loader:RegisterEvent("PLAYER_LOGIN")
loader:SetScript("OnEvent", function() p("loaded. /wbs for commands.") end)
