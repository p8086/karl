local VERSION = "2.6"
local UEHelpers = require("UEHelpers")

local RETRY_DELAY = 2000
local MAX_TRIES = 6
local STEP_DELAY = 20
local STEP_BUDGET = 0.015
local MAX_WAITS = 1500
local EARLY_RETRY = 25

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
local viewport = nil
local schedule

local function current_world()
    local okWorld, world = pcall(function()
        if not viewport or not viewport:IsValid() then
            viewport = FindFirstOf("GameViewportClient")
        end
        if not viewport or not viewport:IsValid() then
            return nil
        end
        local w = viewport.World
        if w and w:IsValid() then
            return w
        end
        return nil
    end)
    if okWorld then
        return world
    end
    return nil
end

local function key_of(world)
    local kind = map_kind(world)
    if not kind then
        return nil, nil
    end
    local okState, state = pcall(function()
        local level = world.PersistentLevel
        if level ~= nil and level:IsValid() then
            return tostring(level:GetAddress())
        end
        return nil
    end)
    if not okState or not state then
        return nil, nil
    end
    return kind .. "|" .. tostring(world:GetAddress()) .. "|" .. state, kind
end

local function attempt(key, st, budget, given)
    if worlds[key] ~= st or st.done then
        return true
    end
    local world = given or current_world()
    if not world or key_of(world) ~= key then
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
                    local ok_actor = false
                    if cls then
                        local okSpawn, actor = pcall(function()
                            return world:SpawnActor(cls, {}, {})
                        end)
                        ok_actor = okSpawn and actor ~= nil and actor:IsValid()
                    end
                    if ok_actor then
                        st.spawned[path] = true
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
        if #missing > 0 then
            return "later"
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

local function run(key, st, budget, given)
    if busy then
        return budget and "later" or false
    end
    busy = true
    local okRun, result = pcall(attempt, key, st, budget, given)
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
    local key, kind = key_of(world)
    if not key then
        return nil
    end
    if worlds[key] then
        return key, worlds[key]
    end
    local st = { kind = kind, spawned = {}, failed = {}, tries = 0,
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
    if not run(key, st, nil, world) then
        schedule(key, st, STEP_DELAY)
    end
end

local early_running = false

local function early()
    if early_running then
        return
    end
    early_running = true
    local waits = 0
    local mine = nil
    local function step()
        waits = waits + 1
        local world = current_world()
        if not world or not key_of(world) or not ready(world) then
            return waits < MAX_WAITS and "wait" or nil
        end
        local key, st = state_for(world, "anticipo")
        if not st then
            return waits < MAX_WAITS and "wait" or nil
        end
        if mine == nil and st.tries == 0 and not st.done then
            mine = st
        end
        if st ~= mine then
            return waits < MAX_WAITS and "wait" or nil
        end
        if st.done or st.tries > 0 then
            return
        end
        if st.pause and st.pause > 0 then
            st.pause = st.pause - 1
            return waits < MAX_WAITS and "wait" or nil
        end
        local res = run(key, st, STEP_BUDGET, world)
        if res == "later" then
            st.pause = EARLY_RETRY
            return waits < MAX_WAITS and "wait" or nil
        end
        if not res then
            return "wait"
        end
    end
    local function again()
        ExecuteWithDelay(STEP_DELAY, function()
            ExecuteInGameThread(function()
                local okRun, res = pcall(step)
                if okRun and res == "wait" then
                    again()
                    return
                end
                early_running = false
                if not okRun then
                    log("errore nell'avvio anticipato: " .. tostring(res))
                end
            end)
        end)
    end
    again()
end

local okNotify, notifyErr = pcall(NotifyOnNewObject, "/Script/Engine.World", function(world)
    early()
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
