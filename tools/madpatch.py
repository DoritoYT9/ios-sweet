#!/usr/bin/env python3
"""Make mobileactivationd's startup failures non-fatal (curtana bring-up).

    sudo madpatch.py /Volumes/iOSRoot/usr/libexec/mobileactivationd

Why: on curtana the daemon dies every few seconds with
`CRASH mobileactivationd sig 5 code 0x600000001` = EXC_BREAKPOINT from
`__os_crash("Migration failed.")`.  It runs a data migration that needs the SEP
keychain (`Migrating DCRT/UCRT from the keychain`), we have no SEP, so it
kills itself and launchd respawns it forever - right while SpringBoard is
trying to initialise.

Seeding the data ark (`DataMigrationCompleted`) did not stop it, so we patch
the crash helpers instead: each one is a small function that logs a CFString
and ends with

    bl   __os_crash
    bl   <helper>
    brk  #1

Replace those three instructions with the function's own epilogue
(`ldp x29,x30,[sp,#0x10] / add sp,sp,#0x20 / ret`, the frame the prologue of
every one of these helpers builds) so the caller gets control back and the
daemon keeps running.  Only the startup/migration helpers are patched: the
XPC/queue/lockdown ones stay fatal (if those fail the daemon is useless).

Sites are found by pattern, not by hardcoded offset: standard 0x20-byte frame
prologue + `bl __os_crash` (0x1001bfc10) + `bl` + `brk #1`.  Idempotent.
"""
import struct
import sys

BASE = 0x100000000
OSCRASH = 0x1001bfc10
PROLOGUE = b'\xf4\x4f\xbe\xa9' + b'\xfd\x7b\x01\xa9' + b'\xfd\x43\x00\x91'
EPILOGUE = b'\xfd\x7b\x41\xa9' + b'\xff\x83\x00\x91' + b'\xc0\x03\x5f\xd6'
BRK = 0xd4200020
# "Failed to create data ark.", "Migration failed." x2, "Failed to perform
# boot initialization tasks."
PATCH_AT = {0x1001bf230, 0x1001bf35c, 0x1001bf3c0, 0x1001bf6a0}


def bl_target(v, insn):
    imm = insn & 0x03ffffff
    if imm & 0x02000000:
        imm -= 0x04000000
    return v + (imm << 2)


def find(d):
    out = {}
    for off in range(0, len(d) - 16, 4):
        if d[off:off + 12] != PROLOGUE:
            continue
        v = BASE + off
        for o2 in range(off + 12, min(off + 0x120, len(d) - 8), 4):
            insn = struct.unpack_from('<I', d, o2)[0]
            if (insn & 0xfc000000) != 0x94000000 or bl_target(BASE + o2, insn) != OSCRASH:
                continue
            nxt = struct.unpack_from('<I', d, o2 + 4)[0]
            after = struct.unpack_from('<I', d, o2 + 8)[0]
            if (nxt & 0xfc000000) == 0x94000000 and after == BRK:
                out[v] = o2
            break
    return out


def patch_stub(d):
    """Turn the binary's own `__os_crash` trampoline into a plain `ret`.

    Some `bl __os_crash` sites are inside the control-flow-flattened code and
    cannot be rewritten into an epilogue, so instead neuter the stub every one
    of them jumps through: `adrp/ldr/br` -> `ret; nop; nop`."""
    sites = [off for off in range(0, len(d) - 12, 4)
             if (struct.unpack_from('<I', d, off)[0] & 0xfc000000) == 0x94000000
             and bl_target(BASE + off, struct.unpack_from('<I', d, off)[0]) == OSCRASH]
    if not sites:
        return 'no __os_crash call sites'
    o = OSCRASH - BASE
    insn = [struct.unpack_from('<I', d, o + 4 * i)[0] for i in range(3)]
    if d[o:o + 12] == b'\xc0\x03\x5f\xd6' + b'\x1f\x20\x03\xd5' * 2:
        return 'stub already returns'
    if (insn[0] & 0x9f000000) != 0x90000000 or (insn[1] & 0xffc00000) != 0xf9400000 \
            or (insn[2] & 0xfffffc1f) != 0xd61f0000:
        return f'0x{OSCRASH:x} is not the expected adrp/ldr/br stub: {d[o:o+12].hex()}'
    d[o:o + 12] = b'\xc0\x03\x5f\xd6' + b'\x1f\x20\x03\xd5' * 2
    return f'stub 0x{OSCRASH:x} -> ret ({len(sites)} call sites neutered)'


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) != 1:
        sys.exit(__doc__)
    stub = '--stub' in sys.argv or '--stub-only' in sys.argv
    helpers = '--stub-only' not in sys.argv
    path = args[0]
    d = bytearray(open(path, 'rb').read())
    sites = find(d) if helpers else {}
    print(f'{path}: {len(sites)} crash helpers')
    done = 0
    for fn, at in sorted(sites.items()):
        cur = bytes(d[at:at + 12])
        if cur == EPILOGUE:
            print(f'  {fn:#x} already patched')
            continue
        if fn not in PATCH_AT:
            print(f'  {fn:#x} left fatal ({cur.hex()})')
            continue
        assert struct.unpack_from('<I', d, at)[0] & 0xfc000000 == 0x94000000
        d[at:at + 12] = EPILOGUE
        print(f'  {fn:#x} -> returns instead of crashing')
        done += 1
    if done:
        open(path, 'r+b').write(d)
        print(f'{done} helper(s) patched')
    else:
        print('nothing to do')
    if stub:
        d = bytearray(open(path, 'rb').read())
        print('stub:', patch_stub(d))
        open(path, 'r+b').write(d)


if __name__ == '__main__':
    main()
