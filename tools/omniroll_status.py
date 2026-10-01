"""Read-only status check for the Demon's Souls Omnidirectional Roll patch (Windows, RPCS3).

Run while the game is running (in game, after loading a character):
    python omniroll_status.py          one-shot report
    python omniroll_status.py --watch  print a line for every redirected roll

It only READS RPCS3's memory (ReadProcessMemory). It never writes anything.
If RPCS3 runs as administrator, run this from an administrator prompt too.
"""
import ctypes, ctypes.wintypes as wt, math, struct, subprocess, sys, time

CAVE, CAVE_SIG = 0x00220500, bytes.fromhex('396000934800001c')   # first two patch words
HOOK, TEL = 0x00311838, 0x019EEB00

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]


class MBI(ctypes.Structure):
    _fields_ = [('BaseAddress', ctypes.c_ulonglong), ('AllocationBase', ctypes.c_ulonglong),
                ('AllocationProtect', wt.DWORD), ('PartitionId', wt.WORD), ('RegionSize', ctypes.c_ulonglong),
                ('State', wt.DWORD), ('Protect', wt.DWORD), ('Type', wt.DWORD)]


k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t


def rpcs3_pid():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq rpcs3.exe', '/FO', 'CSV', '/NH'],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if 'rpcs3' in line.lower():
            return int(line.split('","')[1])
    sys.exit('RPCS3 is not running.')


H = k32.OpenProcess(0x410, False, rpcs3_pid())
if not H:
    sys.exit('Cannot open RPCS3 (error %d). If RPCS3 runs as administrator, run this as administrator too.'
             % ctypes.get_last_error())


def hread(addr, n):
    buf, got = ctypes.create_string_buffer(n), ctypes.c_size_t()
    ok = k32.ReadProcessMemory(H, ctypes.c_void_p(addr), buf, n, ctypes.byref(got))
    return buf.raw[:got.value] if ok else None


def find_base():
    """Host address of PS3 address 0: the region holding the game's code starts at PS3 address 0x10000."""
    m, a = MBI(), 0
    while a < 0x7FFF_FFFF_FFFF and k32.VirtualQueryEx(H, ctypes.c_void_p(a), ctypes.byref(m), ctypes.sizeof(m)):
        if m.State == 0x1000 and m.RegionSize >= 0x1000000:
            base = m.BaseAddress - 0x10000
            if hread(base + CAVE, 8) == CAVE_SIG:
                return base
        a = m.BaseAddress + m.RegionSize
    return None


base = find_base()
if base is None:
    sys.exit('Patch NOT active: the Omnidirectional Roll code is not in memory.\n'
             'Check that the patch is ticked in Manage Game Patches, that the game is BLES00932 v1.00,\n'
             'and that you fully restarted the game after enabling it.')
hook_ok = hread(base + HOOK, 4) != bytes.fromhex('4bffdcf1')   # original: bl 0x30f528
print('Patch ACTIVE (code found, hold hook %s).' % ('installed' if hook_ok else 'MISSING'))


def tel():
    b = hread(base + TEL, 0x30)
    return struct.unpack('>IIffffIfffII', b)


def deg(r): return (math.degrees(r) + 180) % 360 - 180


red, seen, x, y, yb, yn, fl, ref, hl, hy, hf, raised = tel()
print('Locked-on rolls redirected: %d  (rolls seen by the patch: %d)' % (red, seen))
if '--watch' not in sys.argv:
    sys.exit(0)
print('Watching... lock on and roll (Ctrl+C to stop)')
last = red
NAMES = {0x93: 'forward', 0x94: 'back', 0x95: 'left', 0x96: 'right'}
while True:
    t = tel()
    if t[0] != last:
        red, seen, x, y, yb, yn, fl, ref, hl, hy, hf, raised = t
        print('roll #%d: stick %+4.0f deg -> %s roll, facing %+4.0f deg from the target (vanilla would have been %s)'
              % (red, math.degrees(math.atan2(x, y)), NAMES.get(raised, hex(raised)), deg(yn - ref),
                 NAMES.get(fl, hex(fl))))
        last = red
    time.sleep(0.01)
