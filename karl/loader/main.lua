local VERSION = "2.1"
local UEHelpers = require("UEHelpers")

local FIRST_DELAY = 1500
local RETRY_DELAY = 2000
local MAX_TRIES = 6

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

local worlds = {}
local schedule

local function attempt(key, st)
    if worlds[key] ~= st then
        return
    end
    local world = st.world
    if not world or not world:IsValid() then
        worlds[key] = nil
        return
    end
    st.tries = st.tries + 1
    local who = role(world)
    local started, skipped, missing = 0, 0, {}
    for _, mod in ipairs(MODS) do
        local allowed = who ~= "client" or mod.client ~= false
        for _, path in ipairs(mod[st.kind] or {}) do
            if not allowed then
                skipped = skipped + 1
            elseif not st.spawned[path] then
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
                    log("avviata " .. tostring(mod.name) .. " (" .. path .. ")")
                else
                    table.insert(missing, tostring(mod.name) .. " (" .. path .. ")")
                end
            end
        end
    end
    log(string.format("%s, %s, tentativo %d: avviate %d, in attesa %d, saltate %d (non Verified nella lobby di un altro)",
        st.kind, who, st.tries, started, #missing, skipped))
    if #missing == 0 then
        return
    end
    if st.tries < MAX_TRIES then
        schedule(key, st, RETRY_DELAY)
        return
    end
    for _, m in ipairs(missing) do
        log("non riesco ad avviare " .. m)
    end
end

schedule = function(key, st, delay)
    ExecuteWithDelay(delay, function()
        ExecuteInGameThread(function()
            local okRun, err = pcall(attempt, key, st)
            if not okRun then
                log("errore: " .. tostring(err))
            end
        end)
    end)
end

local function start(world, fresh)
    if not world or not world:IsValid() then
        return
    end
    local kind = map_kind(world)
    if not kind then
        return
    end
    local key = kind .. "|" .. tostring(world:GetAddress())
    local old = worlds[key]
    if old and not fresh and (old.mark == nil or old.mark:IsValid()) then
        return
    end
    local st = { world = world, kind = kind, spawned = {}, tries = 0 }
    worlds[key] = st
    schedule(key, st, FIRST_DELAY)
end

RegisterLoadMapPostHook(function(engine, world)
    start(world:get(), true)
end)

RegisterHook("/Script/Engine.PlayerController:ClientRestart", function(ctx)
    local controller = ctx:get()
    if controller and controller:IsValid() then
        start(controller:GetWorld(), false)
    end
end)
