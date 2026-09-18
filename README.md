# NR Map Seed Toolbox

An all-in-one **map editor + map-seed locker** for *ELDEN RING NIGHTREIGN* (`regulation.bin`).

## What it does

1. **Reads any `regulation.bin`** with its own unpacker (AES-256-CBC + DCX/ZSTD + BND4) — no third-party tools needed.
2. **Map editor** — opens a local web page where you can view and edit map layouts, small bases/spots and map flags, then export the changes as CSV.
3. **Writes edits back** into the same `regulation.bin`, repacking it with the *exact* format the game expects (the included self-check proves the repack is byte-identical to the original file).
4. **Seed locker + mod launcher** — locks the map seed you choose and launches the whole mod through me3, so the very first night map is always the one you picked, with your edits active.

## How to build

Requirements: Windows x64, Python 3.11.

```bat
pip install pyinstaller zstandard pycryptodome
python -m PyInstaller --noconfirm --clean NRSeedToolbox.spec
```

The result is `dist\NRSeedToolbox.exe`.

The spec file also pulls `vcruntime140.dll` and `vcruntime140_1.dll` from `%SystemRoot%\System32` so the built executable is self-contained.

## Self-check (proof the bin repacking is correct)

`engine\自检_重封包.py` re-packs a `regulation.bin` **without changing any value** and compares the result with the original file byte-for-byte:

```bat
dist\NRSeedToolbox.exe --run-script 自检_重封包.py
```

Expected output: `[OK] 与原文件【逐字节相同】` (byte-identical ⇒ the game can read what we write).

## Third-party components (NOT part of this repository)

The packaged release also contains the following **third-party** components. They are not source code of this project and are not included in this repository:

| Component | Purpose | Upstream | License |
|---|---|---|---|
| **me3** (Mod Engine 3) | mod loader used to launch the game with a mod | `github.com/garyttierney/me3` | MIT / Apache-2.0 |
| **NightreignRandomizerHelper.dll** | community seed-patching DLL | distributed by its original author (not redistributed here) | see its original distribution |
| Param definitions under `engine/assets/Defs` | table layouts used by the editor | Smithbox community paramdefs | as published by Smithbox |

No third-party binaries are versioned in this repository — only our own Python / HTML / JS source.

## Safety

* **Fully offline.** No network access, no telemetry, no data collection of any kind.
* Only touches the `regulation.bin` you select, plus a copy of your save file (`*.tst`) used while testing (your main save is never modified).
* Requires administrator rights only because the seed locker writes to the game's memory at startup.

## License

(Add the license you want to publish under.)
