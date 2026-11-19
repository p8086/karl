local VERSION = "2.0"
local UEHelpers = require("UEHelpers")

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
        log("AssetRegistryHelpers non disponibile")
        return nil
    end
    local package, name = path:match("^(.-)%.(.+)$")
    local ok, result = pcall(function()
        return h:GetAsset({
            ["ObjectPath"] = UEHelpers.FindOrAddFName(path),
            ["PackageName"] = UEHelpers.FindOrAddFName(package),
            ["AssetName"] = UEHelpers.FindOrAddFName(name),
        })
    end)
    if not ok then
        log("errore caricando " .. path .. ": " .. tostring(result))
        return nil
    end
    if result and result:IsValid() then
        return result
    end
    return nil
end

local done = {}

local function spawn_mods(world)
    local kind = map_kind(world)
    if not kind then
        return
    end
    local key = string.format("%s|%s", kind, tostring(world:GetAddress()))
    local mark = done[key]
    if mark and mark:IsValid() then
        return
    end
    local count = 0
    for _, mod in ipairs(MODS) do
        for _, path in ipairs(mod[kind] or {}) do
            local cls = find_class(path)
            if cls then
                local okSpawn, actor = pcall(function()
                    return world:SpawnActor(cls, {}, {})
                end)
                if okSpawn and actor and actor:IsValid() then
                    count = count + 1
                    done[key] = done[key] or actor
                else
                    log("non riesco a creare " .. path)
                end
            else
                log("blueprint non trovato: " .. path .. " (" .. tostring(mod.name) .. ")")
            end
        end
    end
    log(string.format("%s: avviate %d mod", kind, count))
end

local function on_player_ready(controller)
    if not controller or not controller:IsValid() then
        return
    end
    local world = controller:GetWorld()
    if world and world:IsValid() then
        spawn_mods(world)
    end
end

RegisterLoadMapPostHook(function(engine, world)
    local w = world:get()
    if w and w:IsValid() then
        spawn_mods(w)
    end
end)

RegisterHook("/Script/Engine.PlayerController:ClientRestart", function(ctx)
    on_player_ready(ctx:get())
end)
