<p align="center">
  <img src="docs/karl01.png" alt="Karl" width="200">
</p>

**Karl** is a mod manager for **Deep Rock Galactic** on Windows and Linux. Find mods on mod.io, paste their links into Karl and it installs them: no mod.io subscriptions, no game mod menu, no extra tools.

## Download

Get the latest version from the **Releases** page. No Python needed.

- **Windows:** download `Karl.exe` and run it. If SmartScreen says "Windows protected your PC", click **More info → Run anyway** (the exe is not signed).
- **Linux:** download `karl-linux-x86_64.tar.gz`, extract it and run `./install.sh` from the `karl` folder. Karl appears in your applications menu.

## Getting started

1. **Sign in.** Click **Sign in with mod.io**, create a *personal access token* on mod.io (Permissions: **User actions**, Scope: **Read** and **Write**) and paste it into Karl. The token stays on your PC.
2. **Find mods.** In **Browse** search and filter mods like on the website, then press **🔗 Copy link** on the ones you want.
3. **Add them.** In **My mods** the links land in the *Mod links* box: press **Add**. You can also paste links, or drop `.pak`/`.zip` files on the window.
4. **Install.** Turn mods on or off with their switch, then press **💾 Install and save**.
5. **Play.** Press **▶ Play**. Install and update mods with the game closed.

![The My mods tab](docs/screenshots/my-mods.png)

## Features

- **Folders:** keep several mod lists ("Normal", "With friends"…) and switch between them; only the active folder goes in the game.
- **Load order:** drag mods or type their position in the **#** column. The higher mod wins when two mods change the same file; conflicts are marked with ⇄.
- **Updates:** **🔄 Check for updates** finds new versions, **Install and save** downloads them. **Previous version** and **Choose version…** (right-click) go back to an older file, which then stays locked 🔒.
- **Time machine and history:** every save takes a snapshot of the folder; restore any of them, or read what changed and when.
- **Play without mods:** starts DRG vanilla and puts your mods back when you close it.
- **Problem mods:** crash detection after each game and **Find problem mod…**, which halves your mods until it finds the culprit.
- **Sharing:** **Export** copies a code with the folder's mods for a friend to **Import**; **Export list** saves the mod links as a text file, one per line; **Export configuration** moves everything to another PC.
- **Your own mods:** **Add .pak file…** adds a mod that isn't on mod.io; with **Developer mode** Karl reimports it every time you rebuild it.
- **And more:** favorites, notes and tags, sort by category, required mods check, removed mods warning, safe downloads (MD5), several game installations, 17 languages, interface size, notification area icon, automatic Karl updates.

## Blueprint mods and multiplayer

Mods like Mod Hub, HUDs and DRGLib need their startup blueprints spawned in the Space Rig and on missions. Karl does it inside the game: at every **Install and save** its tool `karl-merge` builds one small extra pak (`FSD-WindowsNoEditor_karl_integration_9999_P.pak`) that starts those blueprints. No DLL or script framework is installed. **Settings → Rebuild mod integration** builds it again from scratch.

Since Karl installs mods outside DRG's mod system, the game doesn't know which mods you use:

- your lobby is **not marked as modded**, and *Sandbox* mods don't turn off progression;
- your mods also start in other players' lobbies: keep gameplay mods (enemies, damage, missions) for your own games or friends with the same mods.

If Karl finds files of another mod loader (`x3daudio1_7.dll`, `dwmapi.dll`, `mods_P.pak`) it offers to move them to a backup folder, deleting nothing. Karl only touches files starting with `karl_`.

## Troubleshooting

- **Error 401:** mod.io rejects the token. Use **Settings → mod.io token → Verify**; if needed create a new token with **User actions** and **All my mods**, Read and Write.
- **Something doesn't work:** **Settings → Diagnostics…** checks the game folder, the mods, conflicts, the integration and mod.io. Attach its report to an issue.
- **Logs:** Karl's log is `~/.cache/karl/karl.log` (Windows: `C:\Users\<you>\.cache\karl\karl.log`), the game's is `FSD\Saved\Logs\FSD.log`.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| Ctrl+F | Search |
| F5 | Check for updates / reload Browse |
| Ctrl+S | Install and save |
| Ctrl+L | Hide or show the Mod links box |
| Ctrl+Enter | Add the links in the box |
| Space | Turn the selected mods on or off |
| Delete | Remove the selected mods |
| Alt+Up / Alt+Down | Move the selected mod in the load order |

## From source

You need Python 3 and, to build the integration tool, [Rust](https://rustup.rs).

```bash
pip install -r requirements.txt
cargo build --release --manifest-path tools/merge/Cargo.toml
python main.py
```

On Linux install Tkinter first (`python3-tk` on Ubuntu/Debian, `python3-tkinter` on Fedora, `tk` on Arch). To build the executable run `build_exe.bat` on Windows (creates `dist\Karl.exe`) or `bash build_linux.sh` on Linux. Releases are built by GitHub Actions when a `v*` tag matching `VERSION` in `karl/__init__.py` is pushed.

Karl's data is in `~/.config/karl` and `~/.cache/karl`.

## Reporting issues

Open an issue with your system, Karl's version, what happened, and the **Diagnostics** report or Karl's log.

## Credits

- [mint](https://github.com/trumank/mint) by trumank and jieyouxu (MIT license): Karl's integration tool reuses its integration code and assets, see `tools/merge/LICENSE-mint`.
- [mod.io](https://mod.io), for the API behind Browse and the downloads.
- [Pillow](https://python-pillow.org), [tkinterdnd2](https://github.com/Eliav2/tkinterdnd2) and [pystray](https://github.com/moses-palmer/pystray).
- All the DRG modders: Karl only downloads their mods, they remain their authors' work.

## Disclaimer

Karl is an unofficial fan project, not affiliated with Ghost Ship Games, Coffee Stain or mod.io. Deep Rock Galactic is a trademark of its respective owners.

## Logo

The Karl logos were manually designed by Zoppone.
