# How the Omnidirectional Roll patch works

This document explains how Demon's Souls decides the direction of a locked-on roll, what the patch changes, how
every address was found and verified, and how to rebuild or port the patch. It's written for modders and curious
players. Some familiarity with assembly helps for the later sections.

All addresses are **PS3 virtual addresses** for **Demon's Souls EU, BLES00932 v01.00**
(RPCS3 PPU hash `PPU-5446a2645880eefa75f7e374abd6b7818511e2ef`). On this executable they're identical to the
addresses in the decrypted ELF and to the addresses used in RPCS3's `patch.yml`.

Contents
1. [The problem](#1-the-problem)
2. [Background: RPCS3 game patches](#2-background-rpcs3-game-patches)
3. [How the game decides a roll's direction](#3-how-the-game-decides-a-rolls-direction)
4. [What the patch does](#4-what-the-patch-does)
5. [Where the patch lives in memory](#5-where-the-patch-lives-in-memory)
6. [The patch code, step by step](#6-the-patch-code-step-by-step)
7. [How it was reverse-engineered](#7-how-it-was-reverse-engineered)
8. [Development history](#8-development-history)
9. [Building and verifying](#building-and-verifying)
10. [Porting to other versions](#porting-to-other-versions)
11. [Limitations and ideas](#11-limitations-and-ideas)
12. [Reference tables](#12-reference-tables)

---

## 1. The problem

When you're **not** locked on, Demon's Souls rolls wherever you push the stick: the character turns to the stick
direction and plays a forward roll. When you **are** locked on, the character keeps facing the enemy, and the game
picks one of four dedicated animations: forward, back, left or right. The stick is snapped to the nearest axis,
so a diagonal input becomes a straight side or forward roll.

The patch makes locked-on rolls follow the stick at any analog angle while keeping everything else
(stamina, i-frames, timing, lock-on itself) as the game does it.

## 2. Background: RPCS3 game patches

RPCS3 can patch a PS3 game's code in memory when the game boots. Patches are YAML files in `<RPCS3>/patches/`.
`patch.yml` is the community database, which RPCS3's updater overwrites. `imported_patch.yml` holds patches you
import yourself. A patch entry is keyed by the game executable's **PPU hash**, so it only appears for the exact
executable it was written for.

Each line writes a value at an address once the executable is loaded:
```yaml
- [ be32, 0x003100d4, 0x4bf1042c ]   # write the 32-bit big-endian word 0x4bf1042c at 0x003100d4
```
The PS3's main CPU (the Cell **PPU**) is a 64-bit **PowerPC**, big-endian, with fixed 4-byte instructions. So a
patch is a list of instruction words. Bigger changes use a **code cave**: a few original instructions are replaced
with branches into a block of unused memory, which holds the new code.

The game's `EBOOT.BIN` on disc is an encrypted SELF. RPCS3 can decrypt it (`rpcs3.exe --decrypt EBOOT.BIN`,
or *Utilities → Decrypt PS3 Binaries*) into an `EBOOT.elf` for analysis. Never distribute that file.

## 3. How the game decides a roll's direction

### 3.1 The objects involved

| Object | How to find it | Relevant fields |
|---|---|---|
| **PadManipulator**: turns pad input into action requests for the player | vtable `0x0185C050` (exactly one instance in game) | `+0x93/+0x94/+0x95/+0x96` roll direction flags, `+0x158` "roll requested", `+0x8E` backstep, `+0x168` sprint, `+0x1B0` roll-button hold timer |
| **Character controller** ("ctrl") | vtable `0x0185BAE0`; passed to the PadManipulator update as an argument | `+0x04` → PlayerIns, `+0x10` → transform block, `+0x139` free-movement flag |
| **Transform block** | `[ctrl+0x10]` | `+0x00` rotation `(x, yaw, z, w)` in radians, `+0x10` position `(x, y, z)` |
| **PlayerIns** | `[ctrl+0x04]` | `+0x20` → ctrl, `+0x100` locked-on flag, `+0x110` lock-on target point `(x, y, z)` |

`ctrl+0x139` is **1 when you move freely (unlocked)** and **0 while locked on**. It's set by
`SetLockOn` (`0x274C70`), which writes `PlayerIns+0x100` and calls the setter `0x2E84C0`.

The PadManipulator's per-frame update is the virtual method at `0x3111D0` (vtable slot 7). Around `0x311838` it
calls the **roll decision** function `0x30F528` with `f1 = frame time (dt)`, `r3 = PadManipulator`, `r5 = ctrl`.

### 3.2 The roll decision (`0x30F528`)

Demon's Souls rolls when you **release** the roll button. Holding it longer than 0.4 s makes you sprint instead.
Every frame, `0x30F528` does the following:

1. Reads the left stick (`0x1B77E8` = x, `0x1B7820` = y, both thin wrappers around `0x1C3E88`) and stores it in
   a 6-frame ring buffer, keeping only the larger axis of each sample.
2. Takes the strongest recent value on each axis. That lets a quick flick still count after the stick has
   returned to centre.
3. **If unlocked** (`ctrl+0x139 != 0`), rotates that stick vector into the character's own frame.
   **If locked**, uses it as is, because the camera already sits behind a character who faces the target.
4. **Snaps it to one axis**: `if |x| < |y| then x = 0 else y = 0`. This is where the 4-way limit comes from.
5. On button release, compares against the roll deadzone (a global read through pool slot `-0x7E50(r30)`)
   and raises exactly one flag:

| Stick (after the snap) | Flag | Written at |
|---|---|---|
| x > deadzone | `+0x96` right | `0x31009C` |
| x < −deadzone | `+0x95` left | `0x30F9D8` |
| y < −deadzone | `+0x94` back | `0x31016C` |
| y > deadzone (also a quick-tap path) | `+0x93` forward | `0x3100D4` |
| neutral | `+0x8E` backstep | `0x31015C` |

Each of the four direction writes is the same 4-instruction sequence:
```
li   r0, 1
stb  r0, 0x158(r14)     # roll requested
stb  r0, 0x9X(r14)      # direction flag
b    0x30F858           # common exit
```
(`r14` = PadManipulator, `r15` = ctrl, `r30` = this function's constant pool.)

### 3.3 What the flags lead to (observed live)

* **Unlocked:** the character already faces the stick direction, because free movement turns it instantly, so the
  flag is almost always `+0x93` and a forward roll plays in that direction.
* **Locked on:** the flag selects one of four roll animations while the character keeps facing the target.
* The flag is raised **in the same frame the roll animation starts**. A roll buffered during an attack raised its
  flag only when the roll actually began. That makes the flag-write sites a safe place to change the roll.
* During roughly the first **0.1 s** of a roll, lock-on keeps turning the character toward the target. After that,
  the roll animation freezes facing until it ends.
* If a locked-on character is turned **more than 90° away** from the target and starts a forward roll, the roll is
  cancelled on the first press.

## 4. What the patch does

The patch changes two things.

**A. At the moment a roll starts (the four flag-write sites):**
```
if locked on (ctrl+0x139 == 0) and the stick is outside the roll deadzone:
    a   = atan2(stick.x, stick.y)                    # stick angle: 0 = up, +90 = right
    ref = atan2(player.x - target.x, player.z - target.z)  # the yaw that faces the lock-on target
    if |a| <= 90 deg:  yaw = ref + a;        play the FORWARD roll (+0x93)
    else:              yaw = ref + a - 180;  play the BACK roll    (+0x94)   (a +/- 180, wrapped)
    set the character's yaw; start a 0.3 s "hold" of that yaw
else:
    raise the original flag (vanilla behaviour)
```
**B. Every frame (wrapping the call to the roll decision in the PadManipulator update):**
```
call the original roll decision
if hold time > 0:  hold time -= dt;  character yaw = held yaw
```

Why each choice:
* **Measured from the target, not the current facing.** Rolling left twice in a row would otherwise add 90°
  each time (the first version did that), because lock-on hasn't turned you back toward the enemy yet.
  "Stick up = toward the enemy" is also what the vanilla locked-on rolls mean.
* **Back half uses the back roll.** It keeps the character within 90° of the target, which avoids the
  cancelled-roll problem from 3.3. It also looks like the game's own locked-on movement.
* **The 0.3 s hold.** Without it, lock-on turns the character back toward the enemy in the first 0.1 s and the
  roll ends up only slightly diagonal. 0.3 s covers that window, and the roll animation keeps the facing after it.
  The hold ends long before the roll finishes, so lock-on turns you back to the enemy normally afterwards.
  The timer uses the real frame time, so it behaves the same at 30 or 60 FPS.
* **Raw stick, not the game's snapped value.** The patch re-reads the stick with the game's own function
  `0x1C3E88`, because the values left in registers have already been snapped.
* **Same deadzone as the game.** A neutral or nearly neutral stick falls through to vanilla, so the backstep is unchanged.
* **The game's own helpers.** `atan2f` (`0x9513A0`, also used by the game's `NoAnimeTurnCharactor` script command)
  and the same rotation field that `SetRotation` (`0x2E9838`) writes.

Stamina, i-frames, roll speed and roll distance all come from the animation the game plays, so they're vanilla.

## 5. Where the patch lives in memory

| What | Address | Why it's safe |
|---|---|---|
| Hook ×4 | `0x30F9D8`, `0x31009C`, `0x3100D4`, `0x31016C` | first instruction (`li r0,1`) of each direction write becomes `b stub` |
| Hook (hold) | `0x311838` | `bl 0x30F528` becomes `bl hold`, which calls `0x30F528` itself |
| Code cave | `0x220500`–`0x22076B` (155 words) | inside the function at `0x220320`–`0x222537`. Nothing calls that function: no `bl`/`b` references and no references to its function descriptor (OPD `0x192B2B8`). The community FreeCam patch overwrites its entry point, which is field evidence it never runs. This cave starts after FreeCam's range (`0x220320`–`0x2204AF`). |
| Telemetry | `0x19EEB00`–`0x19EEB2F` | a zero-filled block in the writable data segment (`0x19EEA54`–`0x19EEBF7`) with no code or pointer references. Verified to stay zero during play. |

The build script also checks that none of these addresses overlap any BLES00932 patch in RPCS3's `patch.yml`
(Unlock FPS, Skip Intro, Aspect Ratio, Motion Blur, FreeCam), including Unlock FPS's data slot at `0x1852608`.

### Telemetry block
Purely informational. The patch writes it so you can confirm it's working; nothing reads it back except the hold timer.

| Offset | Type | Meaning |
|---|---|---|
| +0x00 | u32 | locked-on rolls redirected |
| +0x04 | u32 | directional rolls seen (locked or not) |
| +0x08 / +0x0C | f32 | last stick x / y |
| +0x10 | f32 | yaw before |
| +0x14 | f32 | yaw set |
| +0x18 | u32 | flag the game wanted (0x93–0x96) |
| +0x1C | f32 | yaw facing the target |
| +0x20 | f32 | hold time left (s) |
| +0x24 | f32 | held yaw |
| +0x28 | u32 | frames the hold was applied |
| +0x2C | u32 | flag raised by the patch |

`tools/omniroll_status.py --watch` reads this block.

## 6. The patch code, step by step

The full commented listing is in `patches/DeS_OmniRoll.yml` (generated by `tools/build_omniroll.py`).
Structure:

```
0x220500  stub93: li r11,0x93 ; b common       (one stub per direction flag, r11 = what the game wanted)
0x220508  stub94 ... 0x220518 stub96
0x220520  common:
            stdu r1,-0x80(r1) ; save LR and r11              own stack frame
            telemetry +04 ++
            lbz r0,0x139(r15) ; bne -> vanilla               unlocked: vanilla
            bl 0x1C3E88(out=sp+0x60, pad=[caller sp+0x78], 0x11, 0x10)    raw left stick
            x*x+y*y <= deadzone^2 -> vanilla                 same deadzone global as the game
            bl atan2f(x, y)                -> a
            bl atan2f(px-tx, pz-tz)        -> ref           (skipped if target distance^2 < 0.0001)
            |a| > pi/2 ? (flag=0x94, a -= / += pi) : flag=0x93
            yaw = wrap(ref + a) -> [ctrl+0x10]+4 ; hold yaw, hold time = 0.3
            telemetry ; r11 = flag
          vanilla: r11 = original flag
          raise: stb 1 -> +0x158(r14) ; stbx 1 -> r14+r11 ; restore ; b 0x30F858
0x2206DC  hold:  (replaces bl 0x30F528 in PadManipulator::Update)
            save dt (f1) and ctrl (r5) ; bl 0x30F528 with the original arguments
            if hold time > 0: hold time -= dt ; [ctrl+0x10]+4 = held yaw
            blr
0x220750  constants: pi, 2pi, -pi, 0.3, 0.0001, 0.0, pi/2
```

ABI details that matter on PS3 (all handled):
* Only volatile registers (`r0`, `r3`–`r12`, `f0`–`f13`, `cr7`) are modified. `r14`/`r15`/`r30`/`f30`/`f31`, which
  the game still needs after the hook, are untouched.
* The cave makes its own 0x80-byte stack frame, so the callee's TOC save slot at `sp+0x28` is valid.
* `r12` is reloaded after every call. The game's `memcpy` (reached through `0x1C3E88`) uses `r12` as scratch.
* `addi rX, r0, imm` reads r0 as the literal 0 on PowerPC, so counters are incremented through `r10`.

## 7. How it was reverse-engineered

What was done, in order, so it can be repeated for other games or versions.

1. **Decrypt.** `rpcs3.exe --decrypt <copy of EBOOT.BIN>` → `EBOOT.elf`. Checked that patch addresses equal ELF
   addresses: `0x1451F4` holds `bl 0x1C5968`, the call FreeCam hijacks, and `0x1C5968` is a function start. That
   confirmed FreeCam's label `PadMan::GetPadDeviceForIdx`.
2. **Disassembler setup.** Ghidra 12 with language **`PowerPC:BE:64:64-32addr`**. The `A2` variant lacks the
   AltiVec/VMX instructions the Cell uses heavily, which truncates decompilation. Then:
   * `r2` (TOC) is `0x19B5270`, from the entry point's function descriptor.
   * This executable uses GCC's *minimal TOC*: functions load a per-file constant pool into `r30`
     (`lwz r30,K(r2)`) and read constants and global pointers from it (`lwz rX,off(r30)`). Ghidra doesn't resolve
     that, so a small Python indexer (capstone) resolves `r2`- and `r30`-relative loads to find "who uses this
     string or global".
   * Auto-analysis of the whole 25 MB executable wasn't needed and was unstable. Functions were decompiled on
     demand with a headless script.
3. **Finding the class.** The string `"PadManipulator"` appears in a class-registration table. Its neighbouring
   pool entries led to the vtable `0x185C050`. Slot 7 (`0x3111D0`) is the per-frame update; it calls `0x30F528`,
   whose decompilation shows the four `stb` writes and the axis snap.
4. **Live memory.** A Python reader opens `rpcs3.exe` with `ReadProcessMemory` and finds the emulated PS3 memory
   base by searching for the ELF's first code bytes, which sit at PS3 address `0x10000` (the base was `0x300000000`).
   Scanning memory for the vtable value found the single PadManipulator instance.
   Note: an RPCS3 running as administrator can't be read by a normal process.
5. **Voice-cued scans.** To find fields without guessing, a script spoke cues ("lock on", "unlock",
   "roll left", …) through text-to-speech while the player followed them on the controller, and snapshotted
   memory after each cue. That found:
   * the lock-related byte `ctrl+0x139` (toggles with lock-on; confirmed by the `SetLockOn` code);
   * which flag each locked-on roll raises (`+0x95` left, `+0x96` right, `+0x94` back);
   * that the flags coincide with the roll animation starting (an animation counter at `ctrl+0x274` changes in
     the same frame).
6. **One test write.** Setting `ctrl+0x139 = 1` while locked on changed nothing about the rolls. So that byte
   only selects how the stick is interpreted, not which animation plays.
7. **Facing.** The script bindings `NoAnimeTurnCharactor` → `0x44B980` → `0x42C180` show how the game turns a
   character: `atan2f` (`0x9513A0`) of a direction, then `0x2E85E0` / `SetRotation` (`0x2E9838`), which store into
   `[ctrl+0x10]`. Reading `[ctrl+0x10]+4` live gave the yaw in radians, matching the world matrix at `ctrl+0x50`.
8. **Lock target.** Diffing PlayerIns between unlocked and locked showed `+0x100` (locked flag) and `+0x110` (a
   point that follows the enemy). Checked live: while locked, the character's yaw equals
   `atan2(player − target)` within a few degrees.
9. **Code cave and telemetry.** Searched for code nothing references (no `b`/`bl`, no function-descriptor
   references) and settled on the function FreeCam already overwrites. Unreferenced `.data` zero blocks were
   checked live for staying zero.
10. **Iterate in game** with telemetry and a high-rate recording of the yaw after each roll (section 8).

## 8. Development history

| Version | Change | Result in game |
|---|---|---|
| 1.0 | yaw = current yaw + stick angle, forward roll | Patch active (103/115 rolls redirected), but rolls drifted back toward the enemy and chained rolls went round "in a square" |
| 1.1 | reference = direction to the target; 0.3 s yaw hold; fixed `r12` reload | Rolls held their direction, but left/right were mirrored |
| 1.2 | yaw sign flipped | Front half perfect. Back and back-diagonals cancelled on the first press. |
| 1.3 | back half uses the back roll and faces `angle − 180°` | All directions work on the first press |

The recording behind 1.1 showed the yaw being set correctly at roll start (+90° for a left roll), then pulled
back by about 60° within 0.1 s, then frozen for the rest of the roll. The hold was sized from that.

## Building and verifying

Requirements: Python 3.8+, `pip install capstone`.

```
python tools/build_omniroll.py --elf EBOOT.elf --patch-db "<RPCS3>/patches/patch.yml" --out patches/DeS_OmniRoll.yml
```
Self-checks performed:
* `--elf`: the original instructions at all five hook sites are exactly what the patch expects
  (`li r0,1 / stb 0x158 / stb <flag> / b 0x30F858` and `bl 0x30F528`).
* `--patch-db`: no overlap with any address of the other BLES00932 patches.
* Branch ranges and the cave size limit.
* A capstone disassembly of every word, written as the comments in the YAML.

Tunables at the top of the script: `HOLD_SECONDS` (0.30) and `YAW_SIGN`.

In game: `tools/omniroll_status.py` checks that the cave is in memory and the hold hook is installed. With
`--watch` it prints the stick angle, the roll chosen and the resulting facing for every redirected roll.

## Porting to other versions

The US (BLUS30443), JP and Asia executables are different builds, so every address must be found again. The game
logic is very likely the same. Suggested steps:

1. Decrypt the target `EBOOT.BIN` and get its PPU hash (RPCS3 log or *Manage Game Patches*).
2. Find the four flag writes: search the code for `38000001 980E0158 980E0093` (and `…0094`, `…0095`,
   `…0096`), each followed by a `b` to a common address. Register numbers may differ between builds, so check
   that the surrounding function matches section 3.2.
3. Find the update's call to that function (`bl`) and the PadManipulator vtable (FreeCam's US entry gives
   `PadMan::GetPadDeviceForIdx` as a starting point).
4. Re-find the helpers with the same byte patterns: `0x1C3E88` (stick reader), `0x9513A0` (atan2f wrapper,
   `stdu r1,-0x80 / mflr / std r0,0x90 / bl <import stub> / ld r2,0x28(r1)`), and check the pool slot for the
   deadzone.
5. Verify the struct offsets live: `ctrl+0x139`, `[ctrl+0x10]+4` (yaw), `ctrl+4` → PlayerIns `+0x110`. They're
   probably identical across regions, but check.
6. Pick a cave and a zero `.data` block. The US FreeCam patch uses a function at `0x21F498`; its tail is a
   likely candidate, but check it the same way as section 5 (no references to the function or its descriptor).
7. Change the constants at the top of `build_omniroll.py`, build, and test in this order: boot → title → load
   save → unlocked rolls → locked rolls in all directions.

## 11. Limitations and ideas

* Only BLES00932 v01.00 for now.
* Keyboard: RPCS3 maps keys to the analog stick, so 8 directions are expected, but this hasn't been tested.
* Heavy-load ("fat") rolls are expected to work the same way (same flags) but haven't been tested specifically.
* Rolls out of a sprint (roll button held > 0.4 s) don't go through the four flags and are unchanged.
* The reference direction is the direction to the lock-on target, not the camera. While locked on these almost
  always agree. With a very close or very tall enemy they can differ slightly.
* Idea: a configurable hold time via RPCS3's "Configurable Values" patch feature.

## 12. Reference tables

**Functions**

| Address | What |
|---|---|
| `0x30F528` | PadManipulator roll decision |
| `0x3111D0` | PadManipulator::Update (vtable `0x185C050` slot 7); calls the decision at `0x311838` |
| `0x30F858` | common exit of the decision after a flag is raised |
| `0x1C5968` | PadMan::GetPadDeviceForIdx (name from the FreeCam patch) |
| `0x1C3E88` | read stick pair `(out, device, axisX=0x11, axisY=0x10)` |
| `0x1B77E8` / `0x1B7820` | stick x / y wrappers |
| `0x9513A0` | `atan2f(f1, f2)` |
| `0x274C70` | SetLockOn(PlayerIns, on) |
| `0x2E84C0` | ctrl free-movement flag setter (`+0x139`) |
| `0x2E9838` | SetRotation(ctrl, rotation vector) |
| `0x2E85E0` | request position + rotation (`ctrl+0xC0`, `+0xD0`, `+0xB8`) |
| `0x44B980` / `0x42C180` | script command `NoAnimeTurnCharactor` and its implementation |

**Data**

| Address / field | What |
|---|---|
| `0x185C050` | PadManipulator vtable (base ChrManipulator `0x185BC60`) |
| `0x185BAE0` | character controller vtable |
| `0x19B5270` | TOC (`r2`) |
| PadManipulator `+0x93/94/95/96` | roll flags forward / back / left / right |
| PadManipulator `+0x158` / `+0x8E` / `+0x168` | roll requested / backstep / sprint |
| ctrl `+0x139` | 1 = free movement, 0 = locked on |
| ctrl `+0x04` | → PlayerIns |
| `[ctrl+0x10] +0x04` | yaw (radians). Facing a world direction d: `yaw = atan2(-d.x, -d.z)` |
| `[ctrl+0x10] +0x10` | position |
| PlayerIns `+0x100` / `+0x110` | locked-on flag / lock target point |
