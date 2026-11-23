use std::collections::{HashMap, HashSet};
use std::error::Error;
use std::fs;
use std::io::{BufReader, BufWriter, Cursor, Read, Seek, Write};
use std::path::{Path, PathBuf};

use repak::PakWriter;
use serde::Deserialize;
use uasset_utils::asset_registry::{AssetRegistry, Readable as _, Writable as _};
use uasset_utils::paths::{PakPath, PakPathBuf, PakPathComponentTrait};
use uasset_utils::splice::{
    AssetVersion, TrackedStatement, extract_tracked_statements, inject_tracked_statements, walk,
};
use unreal_asset::AssetBuilder;
use unreal_asset::engine_version::EngineVersion;
use unreal_asset::{
    Asset,
    exports::ExportBaseTrait,
    flags::EObjectFlags,
    kismet::{
        EExprToken, ExByteConst, ExCallMath, ExLet, ExLetBool, ExLetObj, ExLocalOutVariable,
        ExEndOfScript, ExLocalVariable, ExNothing, ExReturn, ExRotationConst, ExSelf, ExSoftObjectConst, ExStringConst, ExTrue,
        ExVectorConst, FieldPath, KismetPropertyPointer,
    },
    kismet::{ExFalse, KismetExpression},
    types::PackageIndex,
    types::vector::Vector,
};

type Res<T> = Result<T, Box<dyn Error>>;

trait ReadSeek: Read + Seek {}
impl<T: Read + Seek> ReadSeek for T {}

static INTEGRATION_DIR: include_dir::Dir<'_> =
    include_dir::include_dir!("$CARGO_MANIFEST_DIR/assets/integration");

const PCB_PATH: &str = "FSD/Content/Game/BP_PlayerControllerBase";
const PATCH_PATHS: [&str; 6] = [
    "FSD/Content/Game/BP_GameInstance",
    "FSD/Content/Game/SpaceRig/BP_PlayerController_SpaceRig",
    "FSD/Content/Game/StartMenu/Bp_StartMenu_PlayerController",
    "FSD/Content/UI/Menu_DeepDives/ITM_DeepDives_Join",
    "FSD/Content/UI/Menu_ServerList/_MENU_ServerList",
    "FSD/Content/UI/Menu_ServerList/WND_JoiningModded",
];
const SERVER_LIST_ENTRY_PATH: &str = "FSD/Content/UI/Menu_ServerList/ITM_ServerList_Entry";
const AR_PATH: &str = "FSD/AssetRegistry.bin";
const MINT_BPL_PATH: &str = "FSD/Content/_mint/BPL_MINT";
const SPAWN_MODS_PATH: &str = "FSD/Content/_AssemblyStorm/ModIntegration/MI_SpawnMods";

#[derive(Deserialize)]
struct Job {
    game_pak: PathBuf,
    out: PathBuf,
    mods: Vec<JobMod>,
    #[serde(default)]
    meta: serde_json::Value,
}

#[derive(Deserialize)]
struct JobMod {
    name: String,
    paks: Vec<PathBuf>,
}

#[derive(Debug, Default)]
struct RawAsset {
    uasset: Option<Vec<u8>>,
    uexp: Option<Vec<u8>>,
}

impl RawAsset {
    fn parse(&self, path: &str) -> Res<Asset<Cursor<&Vec<u8>>>> {
        let (Some(uasset), Some(uexp)) = (self.uasset.as_ref(), self.uexp.as_ref()) else {
            return Err(format!("asset mancante nel gioco: {path}").into());
        };
        Ok(AssetBuilder::new(Cursor::new(uasset), EngineVersion::VER_UE4_27)
            .bulk(Cursor::new(uexp))
            .build()?)
    }
}

struct State {
    asset_registry: AssetRegistry,
    deferred: HashMap<&'static str, RawAsset>,
    from_mods: HashSet<String>,
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() == 2 && args[1] == "--version" {
        println!("karl-merge {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if args.len() != 2 {
        eprintln!("uso: karl-merge <job.json>");
        std::process::exit(2);
    }
    if let Err(e) = run(Path::new(&args[1])) {
        println!("ERROR\t{e}");
        std::process::exit(1);
    }
}

fn run(job_path: &Path) -> Res<()> {
    let job: Job = serde_json::from_slice(&fs::read(job_path)?)?;

    let mut fsd_pak_reader = BufReader::new(fs::File::open(&job.game_pak)?);
    let fsd_pak = repak::PakBuilder::new().reader(&mut fsd_pak_reader)?;

    let asset_registry =
        AssetRegistry::read(&mut Cursor::new(fsd_pak.get(AR_PATH, &mut fsd_pak_reader)?))
            .map_err(|e| format!("AssetRegistry del gioco: {e}"))?;

    let mut deferred: HashMap<&'static str, RawAsset> = [PCB_PATH, SERVER_LIST_ENTRY_PATH]
        .iter()
        .chain(PATCH_PATHS.iter())
        .map(|p| (*p, RawAsset::default()))
        .collect();

    for (path, asset) in &mut deferred {
        asset.uasset = game_file(&fsd_pak, &mut fsd_pak_reader, &format!("{path}.uasset"))?;
        asset.uexp = game_file(&fsd_pak, &mut fsd_pak_reader, &format!("{path}.uexp"))?;
    }

    let mut state = State {
        asset_registry,
        deferred,
        from_mods: HashSet::new(),
    };

    let mut merged = 0;
    for m in &job.mods {
        let mut ok = true;
        for pak in &m.paks {
            if let Err(e) = add_mod_pak(&mut state, pak) {
                println!("SKIP\t{}\t{}", m.name, e);
                ok = false;
            }
        }
        if ok {
            merged += 1;
        }
    }

    let State {
        asset_registry,
        deferred,
        ..
    } = state;

    let mut bundle = ModBundleWriter::new(
        BufWriter::new(fs::File::create(&job.out)?),
        &fsd_pak.files(),
    )?;

    {
        let mut pcb = deferred[PCB_PATH].parse(PCB_PATH)?;
        hook_pcb(&mut pcb);
        bundle.write_asset(pcb, PCB_PATH)?;
    }
    for path in PATCH_PATHS {
        let mut asset = deferred[path].parse(path)?;
        patch(&mut asset)?;
        bundle.write_asset(asset, path)?;
    }
    {
        let mut asset = deferred[SERVER_LIST_ENTRY_PATH].parse(SERVER_LIST_ENTRY_PATH)?;
        patch_server_list_entry(&mut asset)?;
        bundle.write_asset(asset, SERVER_LIST_ENTRY_PATH)?;
    }

    let mut int_files = HashMap::new();
    collect_dir_files(&INTEGRATION_DIR, &mut int_files);
    let bpl_uasset = int_files.remove(&format!("{MINT_BPL_PATH}.uasset"));
    let bpl_uexp = int_files.remove(&format!("{MINT_BPL_PATH}.uexp"));
    let spawn_files = int_files
        .remove(&format!("{SPAWN_MODS_PATH}.uasset"))
        .zip(int_files.remove(&format!("{SPAWN_MODS_PATH}.uexp")));
    for (path, data) in &int_files {
        bundle.write_file(data, path)?;
    }
    if let Some((uasset, uexp)) = spawn_files {
        let mut asset = AssetBuilder::new(Cursor::new(uasset), EngineVersion::VER_UE4_27)
            .bulk(Cursor::new(uexp))
            .build()?;
        keep_session(&mut asset)?;
        bundle.write_asset(asset, SPAWN_MODS_PATH)?;
    }
    if let (Some(uasset), Some(uexp)) = (bpl_uasset, bpl_uexp) {
        let mut asset = AssetBuilder::new(Cursor::new(uasset), EngineVersion::VER_UE4_27)
            .bulk(Cursor::new(uexp))
            .build()?;
        let meta = if job.meta.is_null() {
            serde_json::json!({ "version": "0.2.10", "mods": [], "config": {} })
        } else {
            job.meta.clone()
        };
        patch_mint_library(&mut asset, &serde_json::to_string(&meta)?)?;
        bundle.write_asset(asset, MINT_BPL_PATH)?;
    }

    let mut buf = vec![];
    asset_registry
        .write(&mut buf)
        .map_err(|e| format!("AssetRegistry: {e}"))?;
    bundle.write_file(&buf, AR_PATH)?;
    bundle.finish()?;

    println!("OK\t{merged}");
    Ok(())
}

fn game_file<R: Read + Seek>(pak: &repak::PakReader, reader: &mut R, path: &str) -> Res<Option<Vec<u8>>> {
    match pak.get(path, reader) {
        Ok(data) => Ok(Some(data)),
        Err(repak::Error::MissingEntry(_)) => Ok(None),
        Err(e) => Err(e.into()),
    }
}

fn add_mod_pak(state: &mut State, path: &Path) -> Res<()> {
    let raw = fs::File::open(path)?;
    let mut buf = get_pak_from_data(Box::new(BufReader::new(raw)))?;
    let pak = repak::PakBuilder::new().reader(&mut buf)?;
    let mount = PakPath::new(pak.mount_point());

    let mut pak_files = HashMap::new();
    for p in pak.files() {
        let j = mount.join(&p);
        let normalized = j
            .strip_prefix("../../../")
            .map_err(|_| format!("percorso inatteso nel pak: {j}"))?
            .to_path_buf();
        pak_files.insert(normalized, p);
    }

    for (normalized, pak_path) in &pak_files {
        if !matches!(normalized.extension(), Some("uasset" | "umap"))
            || !pak_files.contains_key(&normalized.with_extension("uexp"))
        {
            continue;
        }
        let uasset = pak.get(pak_path, &mut buf)?;
        let uexp = pak.get(PakPath::new(pak_path).with_extension("uexp").as_str(), &mut buf)?;
        let asset = AssetBuilder::new(Cursor::new(uasset), EngineVersion::VER_UE4_27)
            .bulk(Cursor::new(uexp))
            .skip_data(true)
            .build();
        let Ok(asset) = asset else {
            continue;
        };
        if let Err(e) = state
            .asset_registry
            .populate(normalized.with_extension("").as_str(), &asset)
        {
            println!("WARN\t{normalized}\t{e}");
        }
    }

    for (normalized, pak_path) in pak_files {
        let Some((base, ext)) = normalized.as_str().rsplit_once('.') else {
            continue;
        };
        if ext != "uasset" && ext != "uexp" {
            continue;
        }
        let Some(key) = state
            .deferred
            .keys()
            .find(|k| k.eq_ignore_ascii_case(base))
            .copied()
        else {
            continue;
        };
        if !state.from_mods.insert(format!("{key}.{ext}")) {
            continue;
        }
        let data = pak.get(&pak_path, &mut buf)?;
        let Some(raw) = state.deferred.get_mut(key) else {
            continue;
        };
        if ext == "uasset" {
            raw.uasset = Some(data);
        } else {
            raw.uexp = Some(data);
        }
    }
    Ok(())
}

fn function_export<R: Read + Seek>(asset: &Asset<R>, name: &str) -> Option<usize> {
    asset
        .asset_data
        .exports
        .iter()
        .position(|e| {
            matches!(e, unreal_asset::exports::Export::FunctionExport(func)
                if func.get_base_export().object_name.get_content(|n| n == name))
        })
}

fn out_param<R: Read + Seek>(asset: &mut Asset<R>, owner: PackageIndex, name: &str) -> (KismetPropertyPointer, KismetExpression) {
    let pointer = KismetPropertyPointer {
        old: None,
        new: Some(FieldPath {
            path: vec![asset.add_fname(name)],
            resolved_owner: owner,
        }),
    };
    let variable = ExLocalOutVariable {
        token: EExprToken::ExLocalOutVariable,
        variable: pointer.clone(),
    }
    .into();
    (pointer, variable)
}

fn insert_code<R: Read + Seek>(asset: &mut Asset<R>, index: usize, code: Vec<KismetExpression>) -> Res<()> {
    let Some(unreal_asset::exports::Export::FunctionExport(func)) =
        asset.asset_data.exports.get_mut(index)
    else {
        return Err("funzione non trovata".into());
    };
    let Some(inst) = func.struct_export.script_bytecode.as_mut() else {
        return Err("funzione senza bytecode".into());
    };
    let at = inst
        .iter()
        .position(|ex| matches!(ex, KismetExpression::ExReturn(_)))
        .unwrap_or(0);
    for (i, ex) in code.into_iter().enumerate() {
        inst.insert(at + i, ex);
    }
    Ok(())
}

fn keep_session<R: Read + Seek>(asset: &mut Asset<R>) -> Res<()> {
    let Some(index) = function_export(asset, "CheckKeepSession") else {
        return Err("funzione CheckKeepSession non trovata".into());
    };
    let owner = PackageIndex::from_export(index as i32).unwrap();
    let (_, keep) = out_param(asset, owner, "ShouldKeepSession");
    if let Some(unreal_asset::exports::Export::FunctionExport(func)) =
        asset.asset_data.exports.get_mut(index)
    {
        func.struct_export.script_bytecode = Some(vec![
            ExLetBool {
                token: EExprToken::ExLetBool,
                variable_expression: Box::new(keep),
                assignment_expression: Box::new(ExTrue { token: EExprToken::ExTrue }.into()),
            }
            .into(),
            ExReturn {
                token: EExprToken::ExReturn,
                return_expression: Box::new(ExNothing { token: EExprToken::ExNothing }.into()),
            }
            .into(),
            ExEndOfScript { token: EExprToken::ExEndOfScript }.into(),
        ]);
    }
    Ok(())
}

fn patch_mint_library<R: Read + Seek>(asset: &mut Asset<R>, meta_json: &str) -> Res<()> {
    if let Some(index) = function_export(asset, "Get Mod JSON") {
        let owner = PackageIndex::from_export(index as i32).unwrap();
        let (pointer, variable) = out_param(asset, owner, "JSON");
        let code = vec![
            ExLet {
                token: EExprToken::ExLet,
                value: pointer,
                variable: Box::new(variable),
                expression: Box::new(
                    ExStringConst {
                        token: EExprToken::ExStringConst,
                        value: meta_json.to_string(),
                    }
                    .into(),
                ),
            }
            .into(),
        ];
        insert_code(asset, index, code)?;
    } else {
        println!("WARN\tBPL_MINT\tGet Mod JSON");
    }
    if let Some(index) = function_export(asset, "GetUpdateAvailable") {
        let owner = PackageIndex::from_export(index as i32).unwrap();
        let (_, done) = out_param(asset, owner, "DoneChecking");
        let (_, available) = out_param(asset, owner, "Available");
        let code = vec![
            ExLetBool {
                token: EExprToken::ExLetBool,
                variable_expression: Box::new(done),
                assignment_expression: Box::new(ExTrue { token: EExprToken::ExTrue }.into()),
            }
            .into(),
            ExLetBool {
                token: EExprToken::ExLetBool,
                variable_expression: Box::new(available),
                assignment_expression: Box::new(ExFalse { token: EExprToken::ExFalse }.into()),
            }
            .into(),
        ];
        insert_code(asset, index, code)?;
    } else {
        println!("WARN\tBPL_MINT\tGetUpdateAvailable");
    }
    Ok(())
}

fn collect_dir_files(dir: &'static include_dir::Dir, collect: &mut HashMap<String, &[u8]>) {
    for entry in dir.entries() {
        match entry {
            include_dir::DirEntry::Dir(dir) => collect_dir_files(dir, collect),
            include_dir::DirEntry::File(file) => {
                collect.insert(
                    file.path().to_string_lossy().replace('\\', "/"),
                    file.contents(),
                );
            }
        }
    }
}

fn get_pak_from_data(mut data: Box<dyn ReadSeek>) -> Res<Box<dyn ReadSeek>> {
    if let Ok(mut archive) = zip::ZipArchive::new(&mut data) {
        for i in 0..archive.len() {
            let mut file = archive.by_index(i)?;
            if file.is_file()
                && file
                    .enclosed_name()
                    .is_some_and(|p| p.extension() == Some(std::ffi::OsStr::new("pak")))
            {
                let mut buf = vec![];
                file.read_to_end(&mut buf)?;
                return Ok(Box::new(Cursor::new(buf)));
            }
        }
        return Err("lo zip non contiene un pak".into());
    }
    data.rewind()?;
    Ok(data)
}

struct ModBundleWriter<W: Write + Seek> {
    pak_writer: PakWriter<W>,
    directories: HashMap<String, Dir>,
}

#[derive(Debug, Default)]
struct Dir {
    name: String,
    children: HashMap<String, Dir>,
}

impl<W: Write + Seek> ModBundleWriter<W> {
    fn new(writer: W, fsd_paths: &[String]) -> Res<Self> {
        let mut directories: HashMap<String, Dir> = HashMap::new();
        for f in fsd_paths {
            let mut dir = &mut directories;
            for c in PakPath::new(f).components() {
                dir = &mut dir
                    .entry(c.as_str().to_ascii_lowercase())
                    .or_insert(Dir {
                        name: c.to_string(),
                        children: Default::default(),
                    })
                    .children;
            }
        }
        Ok(Self {
            pak_writer: repak::PakBuilder::new()
                .compression([repak::Compression::Zlib])
                .writer(writer, repak::Version::V11, "../../../".to_string(), None),
            directories,
        })
    }

    fn normalize_path(&self, path_str: &str) -> PakPathBuf {
        let mut dir = Some(&self.directories);
        let path = PakPath::new(path_str);
        let mut normalized_path = PakPathBuf::new();
        for c in path.components() {
            if let Some(entry) = dir.and_then(|d| d.get(&c.as_str().to_ascii_lowercase())) {
                normalized_path.push(&entry.name);
                dir = Some(&entry.children);
            } else {
                normalized_path.push(c);
            }
        }
        normalized_path
    }

    fn write_file(&mut self, data: &[u8], path: &str) -> Res<()> {
        self.pak_writer
            .write_file(self.normalize_path(path).as_str(), data)?;
        Ok(())
    }

    fn write_asset<C: Read + Seek>(&mut self, asset: Asset<C>, path: &str) -> Res<()> {
        let mut data_out = (Cursor::new(vec![]), Cursor::new(vec![]));
        asset.write_data(&mut data_out.0, Some(&mut data_out.1))?;
        data_out.0.rewind()?;
        data_out.1.rewind()?;
        self.write_file(&data_out.0.into_inner(), &format!("{path}.uasset"))?;
        self.write_file(&data_out.1.into_inner(), &format!("{path}.uexp"))?;
        Ok(())
    }

    fn finish(self) -> Res<()> {
        self.pak_writer.write_index()?;
        Ok(())
    }
}

type ImportChain<'a> = Vec<Import<'a>>;

struct Import<'a> {
    class_package: &'a str,
    class_name: &'a str,
    object_name: &'a str,
}
impl<'a> Import<'a> {
    fn new(class_package: &'a str, class_name: &'a str, object_name: &'a str) -> Import<'a> {
        Import {
            class_package,
            class_name,
            object_name,
        }
    }
}

fn get_import<R: Read + Seek>(asset: &mut Asset<R>, import: ImportChain) -> PackageIndex {
    let mut pi = PackageIndex::new(0);
    for i in import {
        let ai = &asset
            .imports
            .iter()
            .enumerate()
            .find(|(_, ai)| {
                ai.class_package.get_content(|n| n == i.class_package)
                    && ai.class_name.get_content(|n| n == i.class_name)
                    && ai.object_name.get_content(|n| n == i.object_name)
                    && ai.outer_index == pi
            })
            .map(|(index, _)| PackageIndex::from_import(index as i32).unwrap());
        pi = ai.unwrap_or_else(|| {
            let new_import = unreal_asset::Import::new(
                asset.add_fname(i.class_package),
                asset.add_fname(i.class_name),
                pi,
                asset.add_fname(i.object_name),
                false,
            );
            asset.add_import(new_import)
        });
    }
    pi
}

fn hook_pcb<R: Read + Seek>(asset: &mut Asset<R>) {
    let transform = get_import(
        asset,
        vec![
            Import::new("/Script/CoreUObject", "Package", "/Script/CoreUObject"),
            Import::new("/Script/CoreUObject", "ScriptStruct", "Transform"),
        ],
    );
    let actor = get_import(
        asset,
        vec![
            Import::new("/Script/CoreUObject", "Package", "/Script/Engine"),
            Import::new("/Script/CoreUObject", "Class", "Actor"),
        ],
    );
    let load_class = get_import(
        asset,
        vec![
            Import::new("/Script/CoreUObject", "Package", "/Script/Engine"),
            Import::new("/Script/CoreUObject", "Class", "KismetSystemLibrary"),
            Import::new("/Script/CoreUObject", "Function", "LoadClassAsset_Blocking"),
        ],
    );
    let make_transform = get_import(
        asset,
        vec![
            Import::new("/Script/CoreUObject", "Package", "/Script/Engine"),
            Import::new("/Script/CoreUObject", "Class", "KismetMathLibrary"),
            Import::new("/Script/CoreUObject", "Function", "MakeTransform"),
        ],
    );
    let begin_spawning = get_import(
        asset,
        vec![
            Import::new("/Script/CoreUObject", "Package", "/Script/Engine"),
            Import::new("/Script/CoreUObject", "Class", "GameplayStatics"),
            Import::new(
                "/Script/CoreUObject",
                "Function",
                "BeginDeferredActorSpawnFromClass",
            ),
        ],
    );
    let finish_spawning = get_import(
        asset,
        vec![
            Import::new("/Script/CoreUObject", "Package", "/Script/Engine"),
            Import::new("/Script/CoreUObject", "Class", "GameplayStatics"),
            Import::new("/Script/CoreUObject", "Function", "FinishSpawningActor"),
        ],
    );
    let ex_transform = ExCallMath {
        token: EExprToken::ExCallMath,
        stack_node: make_transform,
        parameters: vec![
            ExVectorConst {
                token: EExprToken::ExVectorConst,
                value: unreal_asset::types::vector::Vector::new(
                    0f64.into(),
                    0f64.into(),
                    0f64.into(),
                ),
            }
            .into(),
            ExRotationConst {
                token: EExprToken::ExVectorConst,
                rotator: Vector::new(0f64.into(), 0f64.into(), 0f64.into()),
            }
            .into(),
            ExVectorConst {
                token: EExprToken::ExVectorConst,
                value: unreal_asset::types::vector::Vector::new(
                    1f64.into(),
                    1f64.into(),
                    1f64.into(),
                ),
            }
            .into(),
        ],
    };
    let prop_class_name = asset.add_fname("spawn_class");
    let prop_class = unreal_asset::fproperty::FObjectProperty {
        generic_property: unreal_asset::fproperty::FGenericProperty {
            name: prop_class_name.clone(),
            flags: EObjectFlags::RF_PUBLIC,
            array_dim: unreal_asset::enums::EArrayDim::TArray,
            element_size: 8,
            property_flags: unreal_asset::flags::EPropertyFlags::CPF_NONE,
            rep_index: 0,
            rep_notify_func: asset.add_fname("None"),
            blueprint_replication_condition: unreal_asset::enums::ELifetimeCondition::CondNone,
            serialized_type: Some(asset.add_fname("ClassProperty")),
        },
        property_class: actor,
    };
    let prop_transform_name = asset.add_fname("transform");
    let prop_transform = unreal_asset::fproperty::FStructProperty {
        generic_property: unreal_asset::fproperty::FGenericProperty {
            name: prop_transform_name.clone(),
            flags: EObjectFlags::RF_PUBLIC,
            array_dim: unreal_asset::enums::EArrayDim::TArray,
            element_size: 48,
            property_flags: unreal_asset::flags::EPropertyFlags::CPF_NONE,
            rep_index: 0,
            rep_notify_func: asset.add_fname("None"),
            blueprint_replication_condition: unreal_asset::enums::ELifetimeCondition::CondNone,
            serialized_type: Some(asset.add_fname("StructProperty")),
        },
        struct_value: transform,
    };
    let prop_begin_spawn_name = asset.add_fname("begin_spawn");
    let prop_begin_spawn = unreal_asset::fproperty::FObjectProperty {
        generic_property: unreal_asset::fproperty::FGenericProperty {
            name: prop_begin_spawn_name.clone(),
            flags: EObjectFlags::RF_PUBLIC,
            array_dim: unreal_asset::enums::EArrayDim::TArray,
            element_size: 8,
            property_flags: unreal_asset::flags::EPropertyFlags::CPF_NONE,
            rep_index: 0,
            rep_notify_func: asset.add_fname("None"),
            blueprint_replication_condition: unreal_asset::enums::ELifetimeCondition::CondNone,
            serialized_type: Some(asset.add_fname("ObjectProperty")),
        },
        property_class: actor,
    };

    let (fi, func) = asset
        .asset_data
        .exports
        .iter_mut()
        .enumerate()
        .find_map(|(i, e)| {
            if let unreal_asset::exports::Export::FunctionExport(func) = e
                && func
                    .get_base_export()
                    .object_name
                    .get_content(|n| n == "ReceiveBeginPlay")
            {
                return Some((PackageIndex::from_export(i as i32).unwrap(), func));
            }
            None
        })
        .unwrap();

    func.struct_export.loaded_properties.push(prop_class.into());
    func.struct_export
        .loaded_properties
        .push(prop_transform.into());
    func.struct_export
        .loaded_properties
        .push(prop_begin_spawn.into());
    let mut new_code: Vec<KismetExpression> = Vec::new();
    new_code.push(
        ExLetObj {
            token: EExprToken::ExLetObj,
            variable_expression: Box::new(
                ExLocalVariable {
                    token: EExprToken::ExLocalVariable,
                    variable: KismetPropertyPointer {
                        old: None,
                        new: Some(FieldPath {
                            path: vec![prop_class_name.clone()],
                            resolved_owner: fi,
                        }),
                    },
                }
                .into(),
            ),
            assignment_expression: Box::new(
                ExCallMath {
                    token: EExprToken::ExCallMath,
                    stack_node: load_class,
                    parameters: vec![
                        ExSoftObjectConst {
                            token: EExprToken::ExSoftObjectConst,
                            value: Box::new(
                                ExStringConst {
                                    token: EExprToken::ExStringConst,
                                    value: "/Game/_AssemblyStorm/ModIntegration/MI_SpawnMods.MI_SpawnMods_C".to_string()
                                }.into()
                            )
                        }
                        .into()
                    ]
                }
                .into(),
            ),
        }
        .into(),
    );
    new_code.push(
        ExLet {
            token: EExprToken::ExLet,
            value: KismetPropertyPointer {
                old: None,
                new: Some(FieldPath {
                    path: vec![prop_transform_name.clone()],
                    resolved_owner: fi,
                }),
            },
            variable: Box::new(
                ExLocalVariable {
                    token: EExprToken::ExLocalVariable,
                    variable: KismetPropertyPointer {
                        old: None,
                        new: Some(FieldPath {
                            path: vec![prop_transform_name.clone()],
                            resolved_owner: fi,
                        }),
                    },
                }
                .into(),
            ),
            expression: Box::new(ex_transform.into()),
        }
        .into(),
    );

    new_code.push(
        ExLetObj {
            token: EExprToken::ExLetObj,
            variable_expression: Box::new(
                ExLocalVariable {
                    token: EExprToken::ExLocalVariable,
                    variable: KismetPropertyPointer {
                        old: None,
                        new: Some(FieldPath {
                            path: vec![prop_begin_spawn_name.clone()],
                            resolved_owner: fi,
                        }),
                    },
                }
                .into(),
            ),
            assignment_expression: Box::new(
                ExCallMath {
                    token: EExprToken::ExCallMath,
                    stack_node: begin_spawning,
                    parameters: vec![
                        ExSelf {
                            token: EExprToken::ExSelf,
                        }
                        .into(),
                        ExLocalVariable {
                            token: EExprToken::ExLocalVariable,
                            variable: KismetPropertyPointer {
                                old: None,
                                new: Some(FieldPath {
                                    path: vec![prop_class_name],
                                    resolved_owner: fi,
                                }),
                            },
                        }
                        .into(),
                        ExLocalVariable {
                            token: EExprToken::ExLocalVariable,
                            variable: KismetPropertyPointer {
                                old: None,
                                new: Some(FieldPath {
                                    path: vec![prop_transform_name.clone()],
                                    resolved_owner: fi,
                                }),
                            },
                        }
                        .into(),
                        ExByteConst {
                            token: EExprToken::ExByteConst,
                            value: 1,
                        }
                        .into(),
                        ExSelf {
                            token: EExprToken::ExSelf,
                        }
                        .into(),
                    ],
                }
                .into(),
            ),
        }
        .into(),
    );

    new_code.push(
        ExCallMath {
            token: EExprToken::ExCallMath,
            stack_node: finish_spawning,
            parameters: vec![
                ExLocalVariable {
                    token: EExprToken::ExLocalVariable,
                    variable: KismetPropertyPointer {
                        old: None,
                        new: Some(FieldPath {
                            path: vec![prop_begin_spawn_name],
                            resolved_owner: fi,
                        }),
                    },
                }
                .into(),
                ExLocalVariable {
                    token: EExprToken::ExLocalVariable,
                    variable: KismetPropertyPointer {
                        old: None,
                        new: Some(FieldPath {
                            path: vec![prop_transform_name],
                            resolved_owner: fi,
                        }),
                    },
                }
                .into(),
            ],
        }
        .into(),
    );

    let ver = AssetVersion::new_from(asset);
    let mut statements = extract_tracked_statements(asset, ver, &None);
    if let Some(list) = statements.get_mut(&fi) {
        let at = list
            .iter()
            .position(|s| matches!(s.ex, KismetExpression::ExReturn(_)))
            .unwrap_or(list.len());
        for (i, ex) in new_code.into_iter().enumerate() {
            list.insert(
                at + i,
                TrackedStatement {
                    origin: (None, fi),
                    points_to: None,
                    original_offset: None,
                    ex,
                },
            );
        }
    }
    inject_tracked_statements(asset, ver, statements);
}

fn patch<C: Seek + Read>(asset: &mut Asset<C>) -> Res<()> {
    let ver = AssetVersion::new_from(asset);
    let mut statements = extract_tracked_statements(asset, ver, &None);

    let find_function = |name| {
        asset
            .imports
            .iter()
            .enumerate()
            .find(|(_, i)| {
                i.class_package.get_content(|s| s == "/Script/CoreUObject")
                    && i.class_name.get_content(|s| s == "Function")
                    && i.object_name.get_content(|s| s == name)
            })
            .map(|(pi, _)| PackageIndex::from_import(pi as i32).unwrap())
    };

    fn patch_ismodded(
        is_modded: Option<PackageIndex>,
        is_modded_sandbox: Option<PackageIndex>,
        mut statement: TrackedStatement,
    ) -> Option<TrackedStatement> {
        walk(&mut statement.ex, &|ex| {
            if let KismetExpression::ExCallMath(f) = ex
                && (Some(f.stack_node) == is_modded || Some(f.stack_node) == is_modded_sandbox)
            {
                *ex = ExFalse::default().into()
            }
        });
        Some(statement)
    }

    let is_modded = find_function("FSDIsModdedServer");
    let is_modded_sandbox = find_function("FSDIsModdedSandboxServer");

    for (_pi, statements) in statements.iter_mut() {
        *statements = std::mem::take(statements)
            .into_iter()
            .filter_map(|s| patch_ismodded(is_modded, is_modded_sandbox, s))
            .collect();
    }
    inject_tracked_statements(asset, ver, statements);
    Ok(())
}


fn patch_server_list_entry<C: Seek + Read>(asset: &mut Asset<C>) -> Res<()> {
    let get_mods_installed = asset
        .imports
        .iter()
        .enumerate()
        .find(|(_, i)| {
            i.class_package.get_content(|s| s == "/Script/CoreUObject")
                && i.class_name.get_content(|s| s == "Function")
                && i.object_name.get_content(|s| s == "FSDGetModsInstalled")
        })
        .map(|(pi, _)| PackageIndex::from_import(pi as i32).unwrap());

    let fsd_target_platform = asset
        .imports
        .iter()
        .enumerate()
        .find(|(_, i)| {
            i.class_package.get_content(|s| s == "/Script/CoreUObject")
                && i.class_name.get_content(|s| s == "Function")
                && i.object_name.get_content(|s| s == "FSDTargetPlatform")
        })
        .map(|(pi, _)| PackageIndex::from_import(pi as i32).unwrap());

    let ver = AssetVersion::new_from(asset);
    let mut statements = extract_tracked_statements(asset, ver, &None);

    for (pi, statements) in statements.iter_mut() {
        let name = &asset
            .asset_data
            .get_export(*pi)
            .unwrap()
            .get_base_export()
            .object_name;

        let swap_platform = name.get_content(|c| ["GetMissionToolTip", "SetSession"].contains(&c));

        for statement in statements {
            walk(&mut statement.ex, &|ex| {
                if let KismetExpression::ExCallMath(cm) = ex {
                    if Some(cm.stack_node) == get_mods_installed && cm.parameters.len() == 2 {
                        cm.parameters[1] = ExFalse {
                            token: EExprToken::ExFalse,
                        }
                        .into();
                    }
                    if swap_platform && Some(cm.stack_node) == fsd_target_platform {
                        *ex = ExByteConst {
                            token: EExprToken::ExByteConst,
                            value: 0,
                        }
                        .into();
                    }
                }
            });
        }
    }
    inject_tracked_statements(asset, ver, statements);

    {
        let itm_tab_modding = get_import(
            asset,
            vec![
                Import::new(
                    "/Script/CoreUObject",
                    "Package",
                    "/Game/UI/Menu_ServerList/TOOLTIP_ServerEntry_Mods",
                ),
                Import::new(
                    "/Script/UMG",
                    "WidgetBlueprintGeneratedClass",
                    "TOOLTIP_ServerEntry_Mods_C",
                ),
            ],
        );
        let itm_tab_modding_cdo = get_import(
            asset,
            vec![
                Import::new(
                    "/Script/CoreUObject",
                    "Package",
                    "/Game/UI/Menu_ServerList/TOOLTIP_ServerEntry_Mods",
                ),
                Import::new(
                    "/Game/UI/Menu_ServerList/TOOLTIP_ServerEntry_Mods",
                    "TOOLTIP_ServerEntry_Mods_C",
                    "Default__TOOLTIP_ServerEntry_Mods_C",
                ),
            ],
        );
        let new_package = asset.add_fname(
            "/Game/_AssemblyStorm/ModIntegration/RebuiltAssets/TOOLTIP_ServerEntry_Mods",
        );
        asset.imports[(-itm_tab_modding_cdo.index - 1) as usize].class_package =
            new_package.clone();
        let package_index = {
            let obj = &mut asset.imports[(-itm_tab_modding.index - 1) as usize];
            obj.outer_index
        };
        asset.imports[(-package_index.index - 1) as usize].object_name = new_package;
    }

    Ok(())
}
