# Demon's Souls: Omnidirectional Roll (RPCS3 patch)

In vanilla Demon's Souls, a roll while **locked on** only goes in 4 directions: forward, back, left or right.
Push the stick diagonally and the game snaps it to the nearest of the four.

This patch makes locked-on rolls go **where your stick points, at any analog angle**, like the
Demon's Souls remake and later Souls games.

* Locked on, front half of the stick (forward, sides, forward diagonals): you roll in exactly that direction.
* Locked on, back half (back, back diagonals): you get the game's back-roll animation, aimed exactly along the stick.
  You keep roughly facing the enemy.
* Unlocked rolls go exactly where you're moving. This also **fixes a vanilla bug** where unlocked *diagonal* rolls
  (mostly on keyboard) randomly go to the mirrored diagonal: forward-right becomes forward-left, and so on
  (RPCS3 issue #11262).
* The backstep (roll with the stick neutral) and rolls out of a sprint are **unchanged**.
* Stamina cost, i-frames, roll speed and timing are **the game's own**. The patch only chooses which roll animation
  plays and which way you face when it starts.

It's an RPCS3 game patch, a small text file. No game files are modified, and it can be turned on or off in
RPCS3's patch manager.

## Supported versions

| Region | Serial | Version | RPCS3 PPU hash | Patch file | Status |
|---|---|---|---|---|---|
| Europe | BLES00932 | 01.00 | `PPU-5446a2645880eefa75f7e374abd6b7818511e2ef` | `patches/DeS_OmniRoll_EU_BLES00932_patch.yml` | tested in game |
| North America | BLUS30443 | 01.00 | `PPU-83681f6110d33442329073b72b8dc88a2f677172` | `patches/DeS_OmniRoll_US_BLUS30443_patch.yml` | new in this release, please report how it works |

Japan (BCJS30022), Asia (BCAS20071) and the trade demo aren't supported. A patch only appears in RPCS3 for the
exact executable it was made for, so installing the wrong file does nothing.

To check your version: in RPCS3, right-click the game. The serial is shown in the game list, and
*Manage Game Patches* shows the serial and version (`01.00`).

| | |
|---|---|
| Emulator | [RPCS3](https://rpcs3.net) (tested on 0.0.42-20001) |
| Input | Tested with an Xbox 360 controller and with mouse & keyboard (locked and unlocked). |

## Install

Use the file for **your** region (see the table above).

### Option A: drag & drop into the Patch Manager (recommended)
RPCS3's Patch Manager has no direct **Import button**. Importing works by dragging the file onto the window.

1. Download your region's `…_patch.yml` from `patches/`. **Keep the file name as it is**: RPCS3 only accepts
   files whose name ends in `patch.yml`.
2. In RPCS3: right-click **Demon's Souls** → **Manage Game Patches**.
3. Drag the `…_patch.yml` file from Windows Explorer and drop it onto the patch list.
4. RPCS3 asks *"What do you want to do with the patch file?"*. Click **Import**. You should see
   "Imported 1/1 patches". (**Validate** only checks the file without installing it.)
5. In the list, under Demon's Souls → your serial, tick **Omnidirectional Roll**, then **Save**.
6. Fully **close and restart** the game. Patches are applied when the game boots.

This adds the patch to `patches/imported_patch.yml` and keeps any other imported patches you already have.

### Option B: manual copy
Only if you do **not** already have a file called `imported_patch.yml` in RPCS3's `patches` folder:
1. Copy your region's `…_patch.yml` into `<RPCS3 folder>/patches/` and rename the copy to `imported_patch.yml`.
2. Tick **Omnidirectional Roll** in *Manage Game Patches*, **Save**, and restart the game.

If you already have an `imported_patch.yml`, use Option A instead, so your other patches aren't replaced.

### Check that it works
Lock onto an enemy, hold the stick diagonally (for example forward-left) and roll. You should roll diagonally.

Optional (Windows, Python 3): `python tools/omniroll_status.py --watch` detects the region, confirms the patch is in
memory and prints a line for every redirected roll. It only reads memory.

## Uninstall
Untick **Omnidirectional Roll** in *Manage Game Patches* and restart the game. To remove it completely, delete its
`BLES00932_OmniRoll` / `BLUS30443_OmniRoll` anchor and its `"Omnidirectional Roll"` entry from
`patches/imported_patch.yml`, or delete that file if this was the only patch in it.

## Compatibility
* Works alongside the usual RPCS3 patches for this game (Unlock FPS, Skip Intro, Disable Motion Blur, Aspect Ratio):
  the addresses don't overlap, which the build script checks against RPCS3's patch database for each region.
  On EU it was tested with Unlock FPS, Skip Intro and Disable Motion Blur enabled.
* Doesn't overlap the **FreeCam** patch's addresses in either region, but using both together hasn't been tested.
* Texture/file mods (e.g. `_MODS` folders) aren't affected, since this patch only changes game code in memory.
* Tested offline only. Online (private servers) is untested.

## Troubleshooting
* **Dropping the file does nothing**: the file name must end in `patch.yml` (e.g. `DeS_OmniRoll_EU_BLES00932_patch.yml`).
  Downloads renamed to something like `…_patch (1).yml` are refused, so rename them back. Drop it onto the patch
  list inside *Manage Game Patches*, not onto the main RPCS3 window.
* **The patch isn't listed in Manage Game Patches**: wrong file for your region/version, or the import failed.
  Check RPCS3's log (`RPCS3.log`) for "patch" errors.
* **Ticked, but rolls are still 4-way**: restart the game completely (stop emulation, boot again).
  Patches are only applied at boot. `tools/omniroll_status.py` tells you whether the code is in memory.
* **US version crashes or misbehaves**: please open an issue with your RPCS3 log. Until then, untick the patch.
* **Rolls go the mirrored way / feel wrong**: please open an issue with your region, controller type and a short clip.

## How it works
Short version: when a locked-on roll starts, the patch reads your stick and turns your character toward the
stick direction, measured from the direction to your lock-on target. It then makes the game play a forward roll
(front half) or a back roll (back half), and holds that facing for 0.3 s so lock-on can't pull it back.

The full write-up covers how the game's roll code works, every address for both regions, how it was
reverse-engineered, how to rebuild the patch and how to port it: **[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)**.

## Building from source
The patch files are generated by `tools/build_omniroll.py` (Python 3 + `pip install capstone`):
```
python tools/build_omniroll.py --region EU --elf <decrypted EU EBOOT.elf> --patch-db <RPCS3>/patches/patch.yml
python tools/build_omniroll.py --region US --elf <decrypted US EBOOT.elf> --patch-db <RPCS3>/patches/patch.yml
```
See [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md#building-and-verifying).

## Credits
* **hamzafallahi**: research, testing, release, reverse engineering, patch code, US version and documentation, with every EU address verified against the running game.
* The **RPCS3** team, **Ghidra** (NSA), **Capstone** and a lot of assistance from Claude.
* the RPCS3 patch authors: their patches showed how code caves are done for this game.

## License
MIT, see [LICENSE](LICENSE). This repository contains no game code or game files. The patches only contain new
instructions written for them and the addresses where they go.
