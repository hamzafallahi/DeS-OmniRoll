"""Builds the Omnidirectional Roll RPCS3 patch for Demon's Souls (EU BLES00932 / US BLUS30443, v01.00).

Addresses below are the EU ones; the US build is the same code at different addresses (see REGIONS).
Mechanism (verified live on EU, see docs/HOW_IT_WORKS.md):
  PadManipulator roll decision (0x30f528) raises one of four direction flags when a roll starts:
    +0x93 forward (@0x3100d4)  +0x94 back (@0x31016c)  +0x95 left (@0x30f9d8)  +0x96 right (@0x31009c)
  each site = li r0,1 / stb r0,0x158(r14) / stb r0,<flag>(r14) / b 0x30f858.
  r14 = PadManipulator, r15 = character ctrl (+0x139 = free-move flag, 0 while locked on), r30 = pool,
  sp+0x78 = pad device (PadMan::GetPadDeviceForIdx). ctrl+4 = PlayerIns (+0x110 = lock target point),
  [ctrl+0x10] = (rot x, yaw, z, w) followed by position.
Patch:
  * each site's 'li r0,1' -> 'b stub'. When locked on and the left stick is outside the game's roll deadzone:
      ref = atan2(px-tx, pz-tz)  (yaw that faces the lock target),  a = atan2(stick x, stick y)
      |a| <= 90 deg: yaw := ref + a,          raise FORWARD (+0x93)
      |a| >  90 deg: yaw := ref + a -/+ 180,  raise BACK    (+0x94)  (never face > 90 deg away from the target)
    Otherwise (unlocked, or stick in the deadzone) the original flag is raised: vanilla.
  * 'bl 0x30f528' in PadManipulator::Update (0x311838) -> 'bl hold': re-applies that yaw for HOLD_SECONDS
    (lock-on turns the character back toward the target during the first ~0.1 s of a roll).

Usage:
  python build_omniroll.py [--region EU|US] [--elf EBOOT.elf] [--patch-db patch.yml] [--out file.yml]
    --region    EU (BLES00932, default) or US (BLUS30443)
    --elf       that region's decrypted EBOOT (rpcs3.exe --decrypt EBOOT.BIN). Enables the ELF checks: original
                instructions at every hook site, the cave lies in unreachable code, the telemetry block is an
                unreferenced zero block. Without it those checks are skipped (a warning is printed).
    --patch-db  RPCS3's patches/patch.yml. Enables the "no overlap with that region's other patches" check.
    --out       output file (default: ../patches/DeS_OmniRoll_<EU_BLES00932|US_BLUS30443>.yml)
Requires: Python 3.8+, capstone (pip install capstone) for the commented listing.
"""
import struct, re, os, sys
import capstone



def load_elf(path):
    """Minimal PPC64 BE ELF reader: returns a u32(va) function over PT_LOAD segments."""
    data = open(path, 'rb').read()
    assert data[:4] == b'\x7fELF', 'not a decrypted ELF (use rpcs3.exe --decrypt EBOOT.BIN)'
    phoff, = struct.unpack_from('>Q', data, 0x20)
    phentsize, phnum = struct.unpack_from('>HH', data, 0x36)
    segs = []
    for i in range(phnum):
        t, fl, off, va, pa, fsz, msz, al = struct.unpack_from('>IIQQQQQQ', data, phoff + i * phentsize)
        if t == 1 and fsz:
            segs.append((va, off, fsz))

    def u32(va):
        for sva, off, fsz in segs:
            if sva <= va and va + 4 <= sva + fsz:
                return struct.unpack_from('>I', data, off + va - sva)[0]
        raise ValueError(hex(va))
    u32.data, u32.segs = data, segs
    return u32


def check_unreachable(u32, lo, hi, func, func_end):
    """[lo, hi) must be unreachable: no b/bl/bc from outside [func, func_end) into [func, func_end), and no 32-bit
    word anywhere pointing into it except the function's own descriptor, which itself must be unreferenced."""
    data, segs = u32.data, u32.segs
    text_va, text_off, text_sz = segs[0]
    n = text_sz // 4
    words = struct.unpack_from('>%dI' % n, data, text_off)
    for i, w in enumerate(words):
        va = text_va + 4 * i
        if func <= va < func_end:
            continue
        op = w >> 26
        if op == 18 and not (w & 2):
            off = w & 0x03fffffc
            tgt = va + (off - 0x4000000 if off & 0x2000000 else off)
        elif op == 16 and not (w & 2):
            off = w & 0xfffc
            tgt = va + (off - 0x10000 if off & 0x8000 else off)
        else:
            continue
        assert not (func <= tgt < func_end), 'branch from 0x%x into the cave function' % va
    allw = struct.unpack_from('>%dI' % (len(data) // 4), data, 0)
    ptrs = [i * 4 for i, w in enumerate(allw) if func <= w < func_end]
    file_to_va = lambda o: next(sva + o - off for sva, off, fsz in segs if off <= o < off + fsz)
    for o in ptrs:
        opd = file_to_va(o)
        assert allw[o // 4] == func, 'pointer into the middle of the cave function at 0x%x' % opd
        assert opd not in allw, 'the cave function descriptor 0x%x is referenced' % opd
    assert lo >= func and hi <= func_end


def check_zero_block(u32, lo, hi):
    for va in range(lo, hi, 4):
        assert u32(va) == 0, 'telemetry block not zero at 0x%x' % va
    allw = struct.unpack_from('>%dI' % (len(u32.data) // 4), u32.data, 0)
    assert not any(lo - 0x40 <= w < hi for w in allw), 'a pointer references the telemetry block' 


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH_VERSION = '1.4'
HOLD_SECONDS = 0.30        # lock-on turns the char back toward the target during the first ~0.1 s of a roll
YAW_SIGN = +1              # yaw := ref + YAW_SIGN*atan2(x, y); verified in game: -1 was mirrored (v1.1)
TELEM_SIZE = 0x38

# Per-executable addresses. EU: found and verified in game. US: same code shifted (decision/hook region -0xdd8,
# pad code -0xe88, matching the US FreeCam patch); the --elf checks validate a US EBOOT.
REGIONS = {
    'EU': dict(serial='BLES00932', title_id='EU', ppu='PPU-5446a2645880eefa75f7e374abd6b7818511e2ef',
               func=0x220320, func_end=0x222538,   # unreachable function holding the cave (FreeCam uses its head)
               cave=0x220500,
               telem=0x019eeb00, telem_run=(0x19eea54, 0x19eebf8),
               sites={0x93: 0x3100d4, 0x94: 0x31016c, 0x95: 0x30f9d8, 0x96: 0x31009c},
               join=0x30f858, decision=0x30f528, hold_site=0x311838,
               get_stick=0x1c3e88, atan2f=0x9513a0),
    'US': dict(serial='BLUS30443', title_id='US', ppu='PPU-83681f6110d33442329073b72b8dc88a2f677172',
               func=0x21f498, func_end=0x2216b0,   # same function as EU 0x220320 (US FreeCam uses its head)
               cave=0x21f678,                      # func+0x1e0, same relative spot as EU; past US FreeCam (..0x21f627)
               telem=0x019eeb00, telem_run=(0x19eeab8, 0x19eeb38),
               sites={0x93: 0x30f2fc, 0x94: 0x30f394, 0x95: 0x30ec00, 0x96: 0x30f2c4},
               join=0x30ea80, decision=0x30e750, hold_site=0x310a60,
               get_stick=0x1c3000, atan2f=0x94f928),
}
REGION = (sys.argv[sys.argv.index('--region') + 1] if '--region' in sys.argv else 'EU').upper()
R = REGIONS[REGION]
SERIAL, PPU_HASH = R['serial'], R['ppu']
CAVE, CAVE_LIMIT, TELEM = R['cave'], R['func_end'], R['telem']
SITES, JOIN, ROLL_DECISION, HOLD_SITE = R['sites'], R['join'], R['decision'], R['hold_site']
GET_STICK, ATAN2F = R['get_stick'], R['atan2f']
SITE_ORIG = lambda flag: [0x38000001, 0x980e0158, 0x980e0000 | flag]


# ---------------- tiny PPC encoder ----------------
def D(op, rt, ra, d): return (op << 26) | (rt << 21) | (ra << 16) | (d & 0xffff)
def DS(op, rs, ra, ds, xo): assert ds % 4 == 0; return (op << 26) | (rs << 21) | (ra << 16) | (ds & 0xfffc) | xo
def addi(rt, ra, v):
    assert ra != 0, 'addi with rA=0 means li: use li()'
    return D(14, rt, ra, v)  # NB: ra=0 means literal 0 -> only li() may use it
li = lambda rt, v: D(14, rt, 0, v)
lis = lambda rt, v: D(15, rt, 0, v)
lwz = lambda rt, d, ra: D(32, rt, ra, d)
stw = lambda rs, d, ra: D(36, rs, ra, d)
lbz = lambda rt, d, ra: D(34, rt, ra, d)
stb = lambda rs, d, ra: D(38, rs, ra, d)
lfs = lambda ft, d, ra: D(48, ft, ra, d)
stfs = lambda fs, d, ra: D(52, fs, ra, d)
lfd = lambda ft, d, ra: D(50, ft, ra, d)
stfd = lambda fs, d, ra: D(54, fs, ra, d)
std = lambda rs, d, ra: DS(62, rs, ra, d, 0)
stdu = lambda rs, d, ra: DS(62, rs, ra, d, 1)
ld = lambda rt, d, ra: DS(58, rt, ra, d, 0)
cmpwi = lambda crf, ra, v: (11 << 26) | (crf << 23) | (ra << 16) | (v & 0xffff)
fcmpu = lambda crf, fa, fb: (63 << 26) | (crf << 23) | (fa << 16) | (fb << 11)
fmuls = lambda d, a, c: (59 << 26) | (d << 21) | (a << 16) | (c << 6) | (25 << 1)
fmadds = lambda d, a, c, b: (59 << 26) | (d << 21) | (a << 16) | (b << 11) | (c << 6) | (29 << 1)
fadds = lambda d, a, b: (59 << 26) | (d << 21) | (a << 16) | (b << 11) | (21 << 1)
fsubs = lambda d, a, b: (59 << 26) | (d << 21) | (a << 16) | (b << 11) | (20 << 1)
fneg = lambda d, b: (63 << 26) | (d << 21) | (b << 11) | (40 << 1)
fabs = lambda d, b: (63 << 26) | (d << 21) | (b << 11) | (264 << 1)
frsp = lambda d, b: (63 << 26) | (d << 21) | (b << 11) | (12 << 1)
stbx = lambda rs, ra, rb: (31 << 26) | (rs << 21) | (ra << 16) | (rb << 11) | (215 << 1)
MFLR_R0, MTLR_R0, BLR = 0x7c0802a6, 0x7c0803a6, 0x4e800020
CR7_LT, CR7_GT, CR7_EQ = 28, 29, 30
BO_TRUE, BO_FALSE = 12, 4


class Asm:
    def __init__(self, org):
        self.org, self.words, self.labels, self.fix, self.notes = org, [], {}, [], {}

    @property
    def pc(self): return self.org + 4 * len(self.words)

    def label(self, n): self.labels[n] = self.pc

    def emit(self, w, note=''):
        if note: self.notes[self.pc] = note
        self.words.append(w & 0xffffffff)

    def b(self, target, link=False, note=''):
        self.fix.append((len(self.words), 'b', target, link)); self.emit(0, note)

    def bc(self, bo, bi, target, note=''):
        self.fix.append((len(self.words), 'bc', target, (bo, bi))); self.emit(0, note)

    def resolve(self):
        for i, kind, t, extra in self.fix:
            pc = self.org + 4 * i
            tgt = self.labels[t] if isinstance(t, str) else t
            off = tgt - pc
            if kind == 'b':
                assert -0x2000000 <= off < 0x2000000
                self.words[i] = (18 << 26) | (off & 0x03fffffc) | (1 if extra else 0)
            else:
                assert -0x8000 <= off < 0x8000
                bo, bi = extra
                self.words[i] = (16 << 26) | (bo << 21) | (bi << 16) | (off & 0xfffc)


def hi_lo(addr):
    lo = addr & 0xffff
    lo = lo - 0x10000 if lo & 0x8000 else lo
    return (addr - lo) >> 16, lo


CONSTS = [(3.14159265, 'pi'), (6.28318531, '2pi'), (-3.14159265, '-pi'), (HOLD_SECONDS, 'hold seconds'),
          (0.0001, 'min target dist^2'), (0.0, 'zero'), (1.57079633, 'pi/2'), (0.25, 'min move^2 (|v| > 0.5)')]
C_PI, C_2PI, C_NPI, C_HOLD, C_MIND2, C_ZERO, C_HALFPI, C_MINMOVE2 = (4 * i for i in range(8))


def build():
    a = Asm(CAVE)
    th, tl = hi_lo(TELEM)
    const_fix = []

    def TEL():  # r12 = telemetry. Reload after every call: r12 is volatile and memcpy/import stubs clobber it.
        a.emit(lis(12, th)); a.emit(addi(12, 12, tl))

    def CON():  # r10 = constants (address patched after layout)
        const_fix.append(len(a.words)); a.emit(0); a.emit(0)

    # ---- entry stubs: one per original direction flag ----
    for flag in (0x93, 0x94, 0x95, 0x96):
        a.label('stub%x' % flag)
        a.emit(li(11, flag), 'r11 = original direction flag 0x%x' % flag)
        a.b('common')

    # ---- redirect (runs when the game raises a roll direction flag = roll start) ----
    a.label('common')
    a.emit(stdu(1, -0x80, 1), 'frame: linkage 0x00-0x2f, locals 0x60-0x7f')
    a.emit(MFLR_R0); a.emit(std(0, 0x90, 1), 'save LR')
    a.emit(std(11, 0x70, 1), 'save original flag')
    TEL()
    a.emit(lwz(10, 4, 12)); a.emit(addi(10, 10, 1)); a.emit(stw(10, 4, 12), 'telem+04: directional rolls seen')
    a.emit(lbz(0, 0x139, 15), 'ctrl+0x139 free-move flag: 0 = locked on')
    a.emit(cmpwi(7, 0, 0))
    a.bc(BO_FALSE, CR7_EQ, 'unlocked', 'bne: unlocked -> unlocked fix')
    a.emit(addi(3, 1, 0x60), 'out = sp+0x60')
    a.emit(lwz(4, 0x80 + 0x78, 1), 'pad device (0x30f528 keeps it at its sp+0x78)')
    a.emit(li(5, 0x11)); a.emit(li(6, 0x10))
    a.b(GET_STICK, link=True, note='bl read left stick -> out (x, y)')
    a.emit(lfs(1, 0x60, 1), 'f1 = x (+ = right)')
    a.emit(lfs(2, 0x64, 1), 'f2 = y (+ = up)')
    a.emit(lwz(9, -0x7e50, 30), 'game roll deadzone (same global the vanilla compare uses)')
    a.emit(lfs(13, 0, 9))
    a.emit(fmuls(3, 1, 1)); a.emit(fmadds(3, 2, 2, 3), 'f3 = x*x + y*y')
    a.emit(fmuls(4, 13, 13), 'f4 = dz*dz')
    a.emit(fcmpu(7, 3, 4))
    a.bc(BO_FALSE, CR7_GT, 'vanilla', 'ble: stick inside deadzone -> vanilla')
    TEL()
    a.emit(stfs(1, 8, 12), 'telem+08: x'); a.emit(stfs(2, 0xc, 12), 'telem+0c: y')
    a.b(ATAN2F, link=True, note='bl atan2f(x, y): stick angle, 0 = up, + = right')
    a.emit(stfs(1, 0x68, 1), 'save stick angle')
    a.emit(lwz(9, 0x10, 15), 'ctrl+0x10 -> (rot x, yaw, z, w), position at +0x10')
    a.emit(lfs(0, 4, 9), 'current yaw = fallback reference')
    a.emit(stfs(0, 0x6c, 1))
    TEL()
    a.emit(stfs(0, 0x10, 12), 'telem+10: yaw before')
    a.emit(lwz(8, 4, 15), 'ctrl+4 -> PlayerIns')
    a.emit(lfs(1, 0x10, 9)); a.emit(lfs(3, 0x110, 8))
    a.emit(fsubs(1, 1, 3), 'f1 = px - tx  (PlayerIns+0x110 = lock target point)')
    a.emit(lfs(2, 0x18, 9)); a.emit(lfs(4, 0x118, 8))
    a.emit(fsubs(2, 2, 4), 'f2 = pz - tz')
    a.emit(fmuls(5, 1, 1)); a.emit(fmadds(5, 2, 2, 5), 'f5 = dist^2')
    CON()
    a.emit(lfs(6, C_MIND2, 10))
    a.emit(fcmpu(7, 5, 6))
    a.bc(BO_FALSE, CR7_GT, 'haveref', 'ble: degenerate target -> keep current yaw')
    a.b(ATAN2F, link=True, note='bl atan2f(px-tx, pz-tz) = yaw facing the target')
    a.emit(stfs(1, 0x6c, 1))
    a.label('haveref')
    a.emit(lfs(0, 0x6c, 1), 'f0 = reference yaw')
    a.emit(lfs(1, 0x68, 1), 'f1 = stick angle')
    # back half (|angle| > 90 deg): play the game's BACK roll and face (angle - 180), so the character never
    # turns more than 90 deg away from the target (a forward roll facing away gets cancelled by lock-on).
    CON()
    a.emit(lfs(9, C_HALFPI, 10)); a.emit(lfs(5, C_PI, 10)); a.emit(lfs(2, C_ZERO, 10))
    a.emit(li(11, 0x93), 'front half: forward roll')
    a.emit(fabs(8, 1))
    a.emit(fcmpu(7, 8, 9))
    a.bc(BO_FALSE, CR7_GT, 'half_done', 'ble: |angle| <= 90 deg')
    a.emit(li(11, 0x94), 'back half: back roll')
    a.emit(fcmpu(7, 1, 2))
    a.bc(BO_FALSE, CR7_GT, 'neg_angle', 'ble: angle <= 0')
    a.emit(fsubs(1, 1, 5), 'angle -= pi')
    a.b('half_done')
    a.label('neg_angle')
    a.emit(fadds(1, 1, 5), 'angle += pi')
    a.label('half_done')
    a.emit(std(11, 0x78, 1), 'save flag to raise')
    TEL()
    a.emit(stfs(0, 0x1c, 12), 'telem+1c: reference yaw')
    a.emit((fsubs if YAW_SIGN < 0 else fadds)(0, 0, 1), 'f0 = ref %s angle' % ('-' if YAW_SIGN < 0 else '+'))
    CON()
    a.emit(lfs(5, C_PI, 10)); a.emit(lfs(6, C_2PI, 10)); a.emit(lfs(7, C_NPI, 10))
    a.emit(fcmpu(7, 0, 5))
    a.bc(BO_FALSE, CR7_GT, 'w1', 'ble: no wrap down')
    a.emit(fsubs(0, 0, 6))
    a.label('w1')
    a.emit(fcmpu(7, 0, 7))
    a.bc(BO_FALSE, CR7_LT, 'w2', 'bge: no wrap up')
    a.emit(fadds(0, 0, 6))
    a.label('w2')
    a.emit(lwz(9, 0x10, 15))
    a.emit(stfs(0, 4, 9), 'SET YAW (the field SetRotation 0x2e9838 writes)')
    a.emit(lfs(5, C_HOLD, 10))
    TEL()
    a.emit(stfs(0, 0x14, 12), 'telem+14: new yaw')
    a.emit(stfs(0, 0x24, 12), 'telem+24: hold yaw')
    a.emit(stfs(5, 0x20, 12), 'telem+20: hold seconds left')
    a.emit(lwz(10, 0, 12)); a.emit(addi(10, 10, 1)); a.emit(stw(10, 0, 12), 'telem+00: redirected rolls')
    a.emit(ld(11, 0x70, 1)); a.emit(stw(11, 0x18, 12), 'telem+18: original flag')
    a.emit(ld(11, 0x78, 1), 'raise FORWARD (front half) or BACK (back half) instead')
    a.emit(stw(11, 0x2c, 12), 'telem+2c: raised flag')
    a.b('raise')

    # ---- unlocked: vanilla snaps the stick to an axis (exact keyboard diagonals tie) and can raise a SIDE flag or
    # turn the character toward a snapped axis at roll start -> mirrored diagonals. Face the real move direction
    # (PadManipulator+0x60, the world move vector written this frame before the roll decision), roll forward, hold.
    # For a controller this re-applies the facing the character already has.
    a.label('unlocked')
    a.emit(lfs(1, 0x60, 14), 'f1 = move.x (PadManipulator+0x60, world)')
    a.emit(lfs(2, 0x68, 14), 'f2 = move.z')
    a.emit(fmuls(3, 1, 1)); a.emit(fmadds(3, 2, 2, 3), 'f3 = |move|^2')
    CON()
    a.emit(lfs(4, C_MINMOVE2, 10))
    a.emit(fcmpu(7, 3, 4))
    a.bc(BO_FALSE, CR7_GT, 'vanilla', 'ble: no movement input (backstep, released flick) -> vanilla')
    a.emit(fneg(1, 1)); a.emit(fneg(2, 2))
    a.b(ATAN2F, link=True, note='bl atan2f(-x, -z) = yaw facing the move direction')
    a.emit(lwz(9, 0x10, 15))
    a.emit(stfs(1, 4, 9), 'SET YAW')
    CON()
    a.emit(lfs(5, C_HOLD, 10))
    TEL()
    a.emit(stfs(1, 0x24, 12), 'telem+24: hold yaw')
    a.emit(stfs(5, 0x20, 12), 'telem+20: hold seconds left (beats the roll-start snap)')
    a.emit(lwz(10, 0x30, 12)); a.emit(addi(10, 10, 1)); a.emit(stw(10, 0x30, 12), 'telem+30: unlocked rolls normalised')
    a.emit(ld(11, 0x70, 1)); a.emit(stw(11, 0x34, 12), 'telem+34: original flag (unlocked)')
    a.emit(li(11, 0x93), 'raise FORWARD')
    a.b('raise')

    a.label('vanilla')
    a.emit(ld(11, 0x70, 1), 'original flag')
    a.label('raise')
    a.emit(li(0, 1))
    a.emit(stb(0, 0x158, 14), 'PadManipulator+0x158: roll requested')
    a.emit(stbx(0, 14, 11), 'PadManipulator+flag')
    a.emit(ld(0, 0x90, 1)); a.emit(MTLR_R0)
    a.emit(addi(1, 1, 0x80))
    a.b(JOIN, note='b 0x30f858 (vanilla join)')

    # ---- hold: replaces 'bl 0x30f528' in PadManipulator::Update; f1 = dt, r3 = this, r5 = ctrl ----
    a.label('hold')
    a.emit(stdu(1, -0x80, 1)); a.emit(MFLR_R0); a.emit(std(0, 0x90, 1))
    a.emit(stfd(1, 0x60, 1), 'save dt')
    a.emit(std(5, 0x68, 1), 'save ctrl')
    a.b(ROLL_DECISION, link=True, note='bl 0x30f528 (original call, args untouched)')
    TEL()
    a.emit(lfs(0, 0x20, 12), 'hold seconds left')
    CON()
    a.emit(lfs(2, C_ZERO, 10))
    a.emit(fcmpu(7, 0, 2))
    a.bc(BO_FALSE, CR7_GT, 'hdone', 'ble: no hold active')
    a.emit(lfd(1, 0x60, 1)); a.emit(frsp(1, 1)); a.emit(fsubs(0, 0, 1), 'left -= dt')
    a.emit(stfs(0, 0x20, 12))
    a.emit(ld(5, 0x68, 1)); a.emit(lwz(9, 0x10, 5))
    a.emit(lfs(3, 0x24, 12)); a.emit(stfs(3, 4, 9), 're-apply roll yaw')
    a.emit(lwz(10, 0x28, 12)); a.emit(addi(10, 10, 1)); a.emit(stw(10, 0x28, 12), 'telem+28: hold frames')
    a.label('hdone')
    a.emit(ld(0, 0x90, 1)); a.emit(MTLR_R0); a.emit(addi(1, 1, 0x80))
    a.emit(BLR)

    # ---- constants ----
    a.label('consts')
    for f, n in CONSTS:
        a.emit(struct.unpack('>I', struct.pack('>f', f))[0], '.float %g (%s)' % (f, n))
    ch, cl = hi_lo(a.labels['consts'])
    for i in const_fix:
        a.words[i] = lis(10, ch); a.words[i + 1] = addi(10, 10, cl)
        a.notes[CAVE + 4 * i] = 'r10 = constants'
    a.resolve()
    return a


def rel_b(src, dst, link=False):
    off = dst - src
    assert -0x2000000 <= off < 0x2000000
    return (18 << 26) | (off & 0x03fffffc) | (1 if link else 0)


def main():
    a = build()
    end = a.pc
    assert end <= CAVE_LIMIT, hex(end)
    # 1) original bytes at every hook site (needs the user's own decrypted EBOOT)
    elf_path = arg('--elf')
    if elf_path:
        elf_u32 = load_elf(elf_path)
    else:
        print('WARNING: --elf not given, original-bytes check skipped')
    for flag, site in (SITES.items() if elf_path else []):
        orig = [elf_u32(site + 4 * k) for k in range(4)]
        assert orig[:3] == SITE_ORIG(flag) and (orig[3] >> 26) == 18, ('site mismatch', hex(site))
        off = orig[3] & 0x03fffffc
        assert site + 12 + (off - 0x4000000 if off & 0x2000000 else off) == JOIN
    if elf_path:
        assert elf_u32(HOLD_SITE) == rel_b(HOLD_SITE, ROLL_DECISION, link=True), 'hold site is not bl <roll decision>'
        check_unreachable(elf_u32, CAVE, end, R['func'], R['func_end'])
        check_zero_block(elf_u32, TELEM, TELEM + TELEM_SIZE)
        print('ELF checks passed (hook sites, unreachable cave, zero telemetry block)')
    # 3) no overlap with any other patch for this executable
    db = arg('--patch-db')
    used = patch_ranges(db) if db else []
    if not db:
        print('WARNING: --patch-db not given, overlap check against other patches skipped')
    mine = [(CAVE, end), (HOLD_SITE, HOLD_SITE + 4), (TELEM, TELEM + TELEM_SIZE)] + [(s, s + 4) for s in SITES.values()]
    for lo, hi in mine:
        for name, (ulo, uhi) in used:
            assert hi <= ulo or lo >= uhi, ('overlap', name, hex(lo), hex(ulo))
    assert R['telem_run'][0] <= TELEM and TELEM + TELEM_SIZE <= R['telem_run'][1]
    # 4) listing via capstone
    cs = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_64 | capstone.CS_MODE_BIG_ENDIAN)
    consts_at = a.labels['consts']
    hooks = [(site, rel_b(site, a.labels['stub%x' % flag]), 'b 0x%x   was: li r0,1 (raise flag 0x%x)' % (a.labels['stub%x' % flag], flag))
             for flag, site in sorted(SITES.items(), key=lambda kv: kv[1])]
    hooks.append((HOLD_SITE, rel_b(HOLD_SITE, a.labels['hold'], link=True),
                  'bl 0x%x  was: bl 0x%x (roll decision)' % (a.labels['hold'], ROLL_DECISION)))
    anchor_name = '%s_OmniRoll' % SERIAL
    out = ['  %s: &%s' % (anchor_name, anchor_name),
           '  # Omnidirectional locked-on roll v%s for %s %s (hooks the roll-direction flag writes of the roll decision 0x%x)'
           % (PATCH_VERSION, R['title_id'], SERIAL, ROLL_DECISION),
           '  # Cave in unreachable code: function 0x%x, cave 0x%x-0x%x. Telemetry @ 0x%08X:' % (R['func'], CAVE, end, TELEM),
           '  #   +00 redirected  +04 directional rolls  +08/+0c stick x/y  +10 yaw before  +14 new yaw',
           '  #   +18 original flag  +1c yaw to target  +20 hold s left  +24 hold yaw  +28 hold frames  +2c raised flag',
           '  #   +30 unlocked rolls normalised  +34 original flag (unlocked)']
    for site, w, txt in hooks:
        out.append('    - [ be32, 0x%08x, 0x%08x ] # %s' % (site, w, txt))
    for i, w in enumerate(a.words):
        pc = CAVE + 4 * i
        if pc >= consts_at:
            txt = '.float'
        else:
            ins = list(cs.disasm(struct.pack('>I', w), pc))
            txt = (ins[0].mnemonic + ' ' + ins[0].op_str) if ins else '.long'
        note = a.notes.get(pc, '')
        out.append('    - [ be32, 0x%08x, 0x%08x ] # %-28s %s' % (pc, w, txt, ('// ' + note) if note else ''))
    anchor = '\n'.join(out) + '\n'
    doc = ('Version: 1.2\n\nAnchors:\n' + anchor + '\n' + PPU_HASH + ':\n'
           '  "Omnidirectional Roll":\n'
           '    Games:\n'
           '      "Demon\'s Souls":\n'
           '        %s: [ 01.00 ]\n'
           '    Author: "hamzafallahi"\n'
           '    Notes: "While locked on, rolls follow the left stick at any analog angle instead of the vanilla 4 directions. '
           'Unlocked rolls, backstep, stamina, i-frames and timing are vanilla. https://github.com/hamzafallahi/DeS-OmniRoll"\n'
           '    Patch Version: %s\n'
           '    Patch:\n'
           '      - [ load, *%s ]\n') % (SERIAL, PATCH_VERSION, anchor_name)
    out_path = arg('--out', os.path.join(HERE, '..', 'patches', 'DeS_OmniRoll_%s_%s.yml' % (R['title_id'], SERIAL)))
    open(out_path, 'w', newline='\n').write(doc)
    print('%s %s: cave 0x%x-0x%x (%d words); self-checks passed' % (REGION, SERIAL, CAVE, end, len(a.words)))
    print('wrote', os.path.abspath(out_path))


def patch_ranges(path):
    """(name, (lo, hi)) for every address patched by this executable's anchors and PPU section of patch.yml."""
    txt = open(path, encoding='utf-8').read()
    res, cur = [], None
    for line in txt.splitlines():
        m = re.match(r'  (\w+): &\w+', line)
        if m:
            cur = m.group(1) if m.group(1).startswith(SERIAL) else None
        if line.startswith('PPU-'):
            cur = PPU_HASH[:10] if line.startswith(PPU_HASH) else None
        if cur:
            m = re.search(r'\[\s*(be32|be16|byte|bef32|be64|bef64)\s*,\s*(0x[0-9a-fA-F]+)', line)
            if m:
                n = {'be32': 4, 'be16': 2, 'byte': 1, 'bef32': 4, 'be64': 8, 'bef64': 8}[m.group(1)]
                ad = int(m.group(2), 16)
                res.append((cur, (ad, ad + n)))
    res.append(('FpsUnlock-data', (0x01852608, 0x01852620)))  # EU and US FPS patch data slot (lis/addi, not a patch line)
    return res


if __name__ == '__main__':
    main()
