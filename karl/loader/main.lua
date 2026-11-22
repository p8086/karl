local VERSION = "2.4"
local UEHelpers = require("UEHelpers")

local RETRY_DELAY = 2000
local MAX_TRIES = 6
local STEP_DELAY = 20
local STEP_BUDGET = 0.015
local MAX_WAITS = 3000

local function log(msg)
    print("[Karl] " .. tostring(msg) .. "\n")
end

local ok, MODS = pcall(require, "karl_mods")
if not ok or type(MODS) ~= "table" then
    log("karl_mods.lua mancante o non valido: nessuna mod da avviare")
    MODS = {}
end
log("KarlLoader " .. VERSION .. ": " .. #MODS .. " mod con blueprint")

local function map_kind(world)
    local name = world:GetFullName()
    if name:find("/Maps/SpaceRig/", 1, true) then
        return "spacerig"
    end
    if name:find("LVL_Procedural", 1, true) then
        return "cave"
    end
    return nil
end

local Helpers = nil

local function helpers()
    if Helpers and Helpers:IsValid() then
        return Helpers
    end
    Helpers = StaticFindObject("/Script/AssetRegistry.Default__AssetRegistryHelpers")
    return Helpers
end

local function find_class(path)
    local cls = StaticFindObject(path)
    if cls and cls:IsValid() then
        return cls
    end
    local h = helpers()
    if not h or not h:IsValid() then
        return nil
    end
    local package, name = path:match("^(.-)%.(.+)$")
    local okGet, result = pcall(function()
        return h:GetAsset({
            ["ObjectPath"] = UEHelpers.FindOrAddFName(path),
            ["PackageName"] = UEHelpers.FindOrAddFName(package),
            ["AssetName"] = UEHelpers.FindOrAddFName(name),
        })
    end)
    if okGet and result and result:IsValid() then
        return result
    end
    return nil
end

local function ms(t0)
    return math.floor((os.clock() - t0) * 1000 + 0.5)
end

local function role(world)
    local okRole, server = pcall(function()
        local ksl = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        return ksl:IsServer(world)
    end)
    if not okRole then
        return "?"
    end
    return server and "host" or "client"
end

local function ready(world)
    local okState, valid = pcall(function()
        local gs = world.GameState
        if gs == nil or not gs:IsValid() then
            return false
        end
        local gps = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        local pc = gps:GetPlayerController(world, 0)
        return pc ~= nil and pc:IsValid()
    end)
    return okState and valid
end

local worlds = {}
local busy = false
local schedule

local function attempt(key, st, budget)
    if worlds[key] ~= st or st.done then
        return true
    end
    local world = st.world
    if not world or not world:IsValid() then
        worlds[key] = nil
        return true
    end
    local t0 = os.clock()
    local who = role(world)
    local started, skipped, missing, partial = 0, 0, {}, false
    for _, mod in ipairs(MODS) do
        local allowed = who ~= "client" or mod.client ~= false
        for _, path in ipairs(mod[st.kind] or {}) do
            if not allowed then
                skipped = skipped + 1
            elseif not st.spawned[path] and not st.failed[path] then
                if budget and started > 0 and os.clock() - t0 > budget then
                    partial = true
                else
                    local tm = os.clock()
                    local cls = find_class(path)
                    local actor = nil
                    if cls then
                        local okSpawn, result = pcall(function()
                            return world:SpawnActor(cls, {}, {})
                        end)
                        if okSpawn then
                            actor = result
                        end
                    end
                    if actor and actor:IsValid() then
                        st.spawned[path] = true
                        st.mark = st.mark or actor
                        started = started + 1
                        log("avviata " .. tostring(mod.name) .. " (" .. path .. ") in " .. ms(tm) .. " ms")
                    elseif budget then
                        st.failed[path] = true
                    else
                        table.insert(missing, tostring(mod.name) .. " (" .. path .. ")")
                    end
                end
            end
        end
    end
    st.started = st.started + started
    st.spent = st.spent + ms(t0)
    if partial then
        return false
    end
    if budget then
        st.failed = {}
        for _, mod in ipairs(MODS) do
            if who ~= "client" or mod.client ~= false then
                for _, path in ipairs(mod[st.kind] or {}) do
                    if not st.spawned[path] then
                        table.insert(missing, tostring(mod.name) .. " (" .. path .. ")")
                    end
                end
            end
        end
    end
    st.tries = st.tries + 1
    log(string.format("%s, %s, %s, tentativo %d: avviate %d, in attesa %d, saltate %d (non Verified nella lobby di un altro), %d ms",
        st.kind, who, st.trigger, st.tries, st.started, #missing, skipped, st.spent))
    st.started, st.spent = 0, 0
    if #missing == 0 then
        st.done = true
        return true
    end
    if st.tries < MAX_TRIES then
        schedule(key, st, RETRY_DELAY)
        return true
    end
    st.done = true
    for _, m in ipairs(missing) do
        log("non riesco ad avviare " .. m)
    end
    return true
end

local function run(key, st, budget)
    if busy then
        return false
    end
    busy = true
    local okRun, result = pcall(attempt, key, st, budget)
    busy = false
    if not okRun then
        log("errore: " .. tostring(result))
        return true
    end
    return result
end

schedule = function(key, st, delay)
    ExecuteWithDelay(delay, function()
        ExecuteInGameThread(function()
            if not run(key, st) then
                schedule(key, st, STEP_DELAY)
            end
        end)
    end)
end

local function state_for(world, trigger)
    local kind = map_kind(world)
    if not kind then
        return nil
    end
    local key = kind .. "|" .. tostring(world:GetAddress())
    local old = worlds[key]
    if old and not (old.mark and not old.mark:IsValid()) then
        return key, old
    end
    local st = { world = world, kind = kind, spawned = {}, failed = {}, tries = 0,
                 started = 0, spent = 0, trigger = trigger }
    worlds[key] = st
    return key, st
end

local function start(world, trigger)
    if not world or not world:IsValid() then
        return
    end
    local key, st = state_for(world, trigger)
    if not st or st.done or st.tries > 0 then
        return
    end
    if st.trigger ~= trigger then
        st.trigger = st.trigger .. "+" .. trigger
    end
    if not run(key, st) then
        schedule(key, st, STEP_DELAY)
    end
end

local function early(world)
    local key, st
    local waits = 0
    local function step()
        if not world:IsValid() then
            return
        end
        if not st then
            if not map_kind(world) then
                return
            end
            if not ready(world) then
                waits = waits + 1
                return waits < MAX_WAITS and "wait" or nil
            end
            key, st = state_for(world, "anticipo")
            if not st then
                return
            end
        end
        if st.done or worlds[key] ~= st then
            return
        end
        if not run(key, st, STEP_BUDGET) then
            return "wait"
        end
    end
    local function again()
        ExecuteWithDelay(STEP_DELAY, function()
            ExecuteInGameThread(function()
                local okRun, res = pcall(step)
                if not okRun then
                    log("errore nell'avvio anticipato: " .. tostring(res))
                elseif res == "wait" then
                    again()
                end
            end)
        end)
    end
    again()
end

local okNotify, notifyErr = pcall(NotifyOnNewObject, "/Script/Engine.World", function(world)
    early(world)
end)
if not okNotify then
    log("avvio anticipato non disponibile: " .. tostring(notifyErr))
end

RegisterLoadMapPostHook(function(engine, world)
    start(world:get(), "mappa")
end)

RegisterHook("/Script/Engine.PlayerController:ClientRestart", function(ctx)
    local controller = ctx:get()
    if controller and controller:IsValid() then
        start(controller:GetWorld(), "ClientRestart")
    end
end)
