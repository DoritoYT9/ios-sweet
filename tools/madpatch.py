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


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    d = bytearray(open(path, 'rb').read())
    sites = find(d)
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


if __name__ == '__main__':
    main()
