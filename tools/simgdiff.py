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


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    old, new, out = sys.argv[1:4]
    if not os.path.exists(new):
        sys.exit(f'{new}: no such file')

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
            if base is None:
                ops.append((RAW, lba, n, cur))
                lba += n
                continue
            # split the chunk into runs of equal/differing blocks
            i = 0
            while i < n:
                same = base[i * blk:(i + 1) * blk] == cur[i * blk:(i + 1) * blk]
                j = i + 1
                while j < n and (base[j * blk:(j + 1) * blk] == cur[j * blk:(j + 1) * blk]) == same:
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
