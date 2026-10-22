#!/usr/bin/env python3
import struct, sys, os, argparse
p = argparse.ArgumentParser()
p.add_argument('--kernel'); p.add_argument('--ramdisk'); p.add_argument('--second'); p.add_argument('--dtb')
p.add_argument('--header_version', type=int, default=2)
p.add_argument('--base', type=lambda x:int(x,0), default=0)
p.add_argument('--kernel_offset', type=lambda x:int(x,0), default=0x8000)
p.add_argument('--ramdisk_offset', type=lambda x:int(x,0), default=0x1000000)
p.add_argument('--second_offset', type=lambda x:int(x,0), default=0xf00000)
p.add_argument('--tags_offset', type=lambda x:int(x,0), default=0x100)
p.add_argument('--dtb_offset', type=lambda x:int(x,0), default=0x1f00000)
p.add_argument('--pagesize', type=int, default=4096)
p.add_argument('--os_version', default=''); p.add_argument('--os_patch_level', default='')
p.add_argument('--cmdline', default=''); p.add_argument('-o', required=True)
args = p.parse_args()
def load(x): 
    if not x: return b''
    return open(x,'rb').read()
k,r,s,dt=load(args.kernel),load(args.ramdisk),load(args.second),load(args.dtb)
def osver(v):
    if not v: return 0
    parts=v.split('.')
    a=int(parts[0]) if len(parts)>0 else 0
    b=int(parts[1]) if len(parts)>1 else 0
    c=int(parts[2]) if len(parts)>2 else 0
    return (a<<14)|(b<<7)|c
def ospl(v):
    if not v: return 0
    v=v.split('-')[-1]
    parts=v.split('.')
    y=int(parts[0]) if len(parts)>0 else 0
    m=int(parts[1]) if len(parts)>1 else 0
    d=int(parts[2]) if len(parts)>2 else 0
    return ((y-2000)<<4)|m
hdr = bytearray(4096)
hdr[0:8]=b'ANDROID!'
struct.pack_into('<10I', hdr, 8, len(k), args.base+args.kernel_offset, len(r), args.base+args.ramdisk_offset, len(s), args.base+args.second_offset, args.base+args.tags_offset, args.pagesize, args.header_version, osver(args.os_version))
cmd=args.cmdline.encode()[:1536]
hdr[64:64+len(cmd)]=cmd
try:
    hdr[108:112]=struct.pack('<I',ospl(args.os_patch_level))
except Exception:
    pass
# name
# header size for v2
if args.header_version>=1: struct.pack_into('<I', hdr, 1648-16, 0) # recovery dtbo size
if args.header_version>=2:
    struct.pack_into('<I', hdr, 1648, len(dt))
    struct.pack_into('<Q', hdr, 1652, args.base+args.dtb_offset)
out=bytearray()
def pad(b,ps): 
    if len(b)%ps==0: return b
    return b + bytes(ps-len(b)%ps)
off=0
# write header page(s)
out += bytes(hdr[:args.pagesize])
# kernel
out += pad(k,args.pagesize)
out += pad(r,args.pagesize)
out += pad(s,args.pagesize)
if args.header_version>=2:
    out += pad(dt,args.pagesize)
open(args.o,'wb').write(out)
sys.stderr.write(f'{args.o}: {len(out)} bytes\n')
