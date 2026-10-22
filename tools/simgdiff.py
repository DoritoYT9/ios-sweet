#!/usr/bin/env python3
"""Write a sparse image with only the blocks where NEW.img differs from OLD.simg.

  simgdiff.py OLD.simg NEW.img OUT.simg

OLD is a sparse image (raw/fill/dont-care chunks, as produced by raw2simg.py) of
what the partition currently holds; NEW is the edited raw copy.  Blocks that are
equal become DONT_CARE, differing blocks become RAW chunks read from NEW, so
`fastboot flash PART OUT.simg` rewrites a handful of blocks instead of 16 GiB.

This exists because a curtana userdata flash costs ~5.5 min while the boot image
is 0.5 s, and bring-up needs one experiment per boot.

Caveat: the diff is against the image that was flashed, not against the live
partition, so blocks the running system has since rewritten (logs, prefs) are
left untouched - which is what we want - and only our own patched file blocks
get rewritten, i.e. file data + inode metadata that HFS+ journal replay will not
revert (data is not journaled).
"""
import os
import struct
import sys

MAGIC = 0xed26ff3a
RAW, FILL, DONT_CARE = 0xcac1, 0xcac2, 0xcac3


def protected_ranges(path):
    """Byte ranges at the start of the image that must never be rewritten.

    A diff flash used to write the host's copy of the HFS+ journal and volume
    header over the phone's, after which the phone could not mount the volume
    any more ("mount() failed").  The journal describes transactions relative to
    the state of the *host* mount, so it must be left alone; the volume header
    carries the mounted/dirty flags and lastMountedVersion, same story.
    """
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        f.seek(1024)
        hdr = f.read(512)
    sig, ver, attr, lastmnt, jib = struct.unpack_from('>HHIII', hdr, 0)
    blk = struct.unpack_from('>I', hdr, 40)[0]
    total = struct.unpack_from('>I', hdr, 44)[0]
    out = [(0, 2048)]                       # primary volume header (+ boot blocks)
    note = f'volume header sig 0x{sig:04x} ver 0x{ver:04x} journalInfoBlock {jib} blockSize {blk}'
    if total and blk and total * blk <= size:
        out.append((total * blk - 2048, total * blk))   # alternate volume header
    # The JournalInfoBlock's block number is documented as an allocation block,
    # but some tools store a 512-byte block: accept whichever parses sanely.
    best = None
    for unit in (blk, 512):
        if not jib or not unit:
            continue
        at = jib * unit
        if at + 52 > size:
            continue
        with open(path, 'rb') as f:
            f.seek(at)
            j = f.read(52)
        flags = struct.unpack_from('>I', j, 0)[0]
        off, jsize = struct.unpack_from('>QQ', j, 36)
        if not (off and jsize and off + jsize <= size and 1 << 20 <= jsize <= 1 << 29):
            continue
        score = 1 + (flags & 1)          # kJIJournalInFSMask
        if best is None or score > best[0]:
            best = (score, at, off, jsize, unit)
    if best:
        _, at, off, jsize, unit = best
        out.append((at, at + max(blk, 4096)))            # the JournalInfoBlock
        out.append((off, off + jsize))                   # the journal itself
        note += f'; journal {off:#x}+{jsize:#x} (unit {unit})'
    else:
        note += '; no sane JournalInfoBlock, protecting only the headers'
    return out, note


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    old, new, out = sys.argv[1:4]
    if not os.path.exists(new):
        sys.exit(f'{new}: no such file')
    skip = '--no-skip-journal' not in sys.argv
    guard, note = protected_ranges(new) if skip else ([], 'journal NOT protected')
    print(f'guard: {note}')
    blk_size = 4096
    guard_set = set()
    for s, e in guard:
        guard_set.update(range(s // blk_size, (e + blk_size - 1) // blk_size))

    ops = []            # (kind, lba, nblocks, bytes-or-None)
    lba = 0
    with open(old, 'rb') as f, open(new, 'rb') as fn:
        magic, major, minor, fhs, chs, blk, total_blks, total_chunks, _ = \
            struct.unpack('<IHHHHIIII', f.read(28))
        if magic != MAGIC:
            sys.exit(f'{old}: not a sparse image')
        if os.path.getsize(new) != total_blks * blk:
            sys.exit(f'{new}: size {os.path.getsize(new)} != {total_blks * blk}')
        for _ in range(total_chunks):
            t, _, n, sz = struct.unpack('<HHII', f.read(12))
            body = f.read(sz - 12)
            fn.seek(lba * blk)
            cur = fn.read(n * blk)
            if t == RAW:
                base = body
            elif t == FILL:
                base = body[:4] * (n * blk // 4)
            else:
                base = None                     # unknown -> must write
            if base is not None and base == cur:
                ops.append((DONT_CARE, lba, n, None))
                lba += n
                continue
            # split the chunk into runs of equal/differing blocks; blocks inside
            # a guarded range are never rewritten, whatever differs there
            i = 0
            while i < n:
                def same_at(k):
                    if lba + k in guard_set:
                        return True
                    if base is None:
                        return False
                    return base[k * blk:(k + 1) * blk] == cur[k * blk:(k + 1) * blk]
                same = same_at(i)
                j = i + 1
                while j < n and same_at(j) == same:
                    j += 1
                if same:
                    ops.append((DONT_CARE, lba + i, j - i, None))
                else:
                    ops.append((RAW, lba + i, j - i, cur[i * blk:j * blk]))
                i = j
            lba += n

    # coalesce neighbouring DONT_CARE runs so the chunk count stays sane
    merged = []
    for op in ops:
        if merged and op[0] == DONT_CARE and merged[-1][0] == DONT_CARE \
                and merged[-1][1] + merged[-1][2] == op[1]:
            p = merged[-1]
            merged[-1] = (DONT_CARE, p[1], p[2] + op[2], None)
        else:
            merged.append(op)

    raw_blocks = sum(o[2] for o in merged if o[0] == RAW)
    with open(out, 'wb') as fo:
        fo.write(struct.pack('<IHHHHIIII', MAGIC, 1, 0, 28, 12, blk,
                             total_blks, len(merged), 0))
        for kind, at, n, data in merged:
            if kind == DONT_CARE:
                fo.write(struct.pack('<HHII', DONT_CARE, 0, n, 12))
            else:
                fo.write(struct.pack('<HHII', RAW, 0, n, 12 + len(data)))
                fo.write(data)
    print(f'{out}: {len(merged)} chunks, {raw_blocks} of {total_blks} blocks rewritten '
          f'({os.path.getsize(out)} bytes)')


if __name__ == '__main__':
    main()
