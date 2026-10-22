#!/usr/bin/env python3
"""Convert a raw image into an Android sparse image (fastboot/ABL).

  raw2simg.py RAW OUT.simg [--blk 4096] [--chunk 4194304]

The ABL refuses a raw download bigger than its max-download-size, and the
fastboot client reads a whole raw image into memory, so the root volume is
sent as a sparse image: runs of zero blocks become DONT_CARE chunks (the
flash is skipped) and the data becomes RAW chunks.

Layout (sparse.h):
  header: magic 0xED26FF3A, major 1, minor 0, file_hdr_sz 28, chunk_hdr_sz 12,
          blk_sz, total_blks, total_chunks, image_checksum (0)
  chunk:  type, reserved, chunk_sz (blocks), total_sz (bytes, header included)
          RAW 0xCAC1 (data), DONT_CARE 0xCAC3 (nothing)
"""
import os
import struct
import sys

SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW = 0xCAC1
CHUNK_FILL = 0xCAC2
CHUNK_DONT_CARE = 0xCAC3
FILE_HDR_SZ = 28
CHUNK_HDR_SZ = 12


def main():
    argv = sys.argv[1:]
    positional = []
    opts = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--blk':
            opts += [a, argv[i + 1]]; i += 2
        elif a == '--chunk':
            opts += [a, argv[i + 1]]; i += 2
        elif a.startswith('--'):
            sys.exit(f'unknown option {a}')
        else:
            positional.append(a); i += 1
    blk = 4096
    chunk_bytes = 4 * 1024 * 1024
    for k, opt in enumerate(opts):
        if opt == '--blk':
            blk = int(opts[k + 1])
        elif opt == '--chunk':
            chunk_bytes = int(opts[k + 1])
    if len(positional) != 2:
        sys.exit(f'usage: {sys.argv[0]} RAW OUT.simg [--blk N] [--chunk N]')
    src, dst = positional
    if blk % 4:
        sys.exit('blk size must be a multiple of 4')
    if chunk_bytes % blk:
        sys.exit('chunk size must be a multiple of the block size')

    total_blks = os.path.getsize(src) // blk
    raw_blocks = chunk_bytes // blk
    out = open(dst, 'wb')
    out.write(struct.pack('<IHHHHIIII', SPARSE_MAGIC, 1, 0, FILE_HDR_SZ, CHUNK_HDR_SZ,
                          blk, total_blks, 0, 0))

    chunks = 0
    written_blks = 0
    with open(src, 'rb') as f:
        while written_blks < total_blks:
            # A run of zero blocks becomes DONT_CARE.
            blk_data = f.read(blk)
            if not blk_data:
                break
            if blk_data == b'\0' * blk:
                run = 1
                while written_blks + run < total_blks:
                    pos = f.tell()
                    nxt = f.read(blk)
                    if nxt != b'\0' * blk:
                        f.seek(pos)
                        break
                    run += 1
                # FILL (not DONT_CARE): the flash has to write the zeros, the
                # partition still holds Android's ext4 data and a sparse flash
                # that skips the blocks would leave it inside the HFS+ volume.
                out.write(struct.pack('<HHII', CHUNK_FILL, 0, run, CHUNK_HDR_SZ + 4))
                out.write(b'\0\0\0\0')
                chunks += 1
                written_blks += run
                continue
            # Otherwise collect raw blocks until a zero block or the chunk limit.
            data = bytearray(blk_data)
            while len(data) < chunk_bytes and written_blks + len(data) // blk < total_blks:
                pos = f.tell()
                nxt = f.read(blk)
                if not nxt or nxt == b'\0' * blk:
                    f.seek(pos)
                    break
                data += nxt
            n = len(data) // blk
            out.write(struct.pack('<HHII', CHUNK_RAW, 0, n, CHUNK_HDR_SZ + len(data)))
            out.write(data)
            chunks += 1
            written_blks += n

    # Patch the header with the real chunk count.
    out.seek(0)
    out.write(struct.pack('<IHHHHIIII', SPARSE_MAGIC, 1, 0, FILE_HDR_SZ, CHUNK_HDR_SZ,
                          blk, total_blks, chunks, 0))
    out.close()
    print(f'{dst}: {total_blks} blocks of {blk}, {chunks} chunks, '
          f'{os.path.getsize(dst)} bytes')


if __name__ == '__main__':
    main()
