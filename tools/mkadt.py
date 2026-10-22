#!/usr/bin/env python3
"""Build the minimal Apple Device Tree that the SWEET xnu needs to boot.

Only what the early boot path actually reads (see NOTES.md); every MMIO
address comes from the sweet live FDT. Usage:  mkadt.py OUT.adt [--dump] [--qemu] [--highmem] [--target sweet|miatoll|curtana]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

# Kernel-managed RAM: above every Qualcomm XPU carve-out (see NOTES.md)
PHYS_BASE = 0xb0000000
MEM_SIZE = 0x4d000000
# --highmem (phone only): the whole upper DDR bank.
HIGH_PHYS_BASE = 0x100000000
HIGH_MEM_SIZE = 0xfcc00000
# /arm-io children are relative to this; it must not be 0 (serial_init bails)
SOC_BASE = 0x01000000

TARGETS = {
    'sweet': {
        'gicd': 0x17a00000, 'gicr': 0x17a60000,
        'fb': 0x9c000000, 'fb_size': 0x1700000,
        'ecid': 0x5357454554000001,
        'fb_w': 1080, 'fb_h': 2400, 'fb_stride': 1080 * 4,
        'ufs': 0x01d84000, 'ufs_size': 0x3000,
        'timebase': 19200000, 'vtimer': 19,
        'compatible': ['D22AP', 'iPhone10,3', 'xiaomi,sweet', 'qcom,sm7150', 'AppleARM'],
        'model': 'iPhone10,3', 'target_type': 'D22', 'target_sub_type': 'D22AP',
        'io_compat': ['sweet-io', 'arm-io'],
        'disp_compat': 'sweet,display',
        'ufs_compat': 'qcom,ufshc',
        'cpu_compat': ['qcom,kryo470', 'ARM,v8'],
        # sweet DRAM (live FDT /memory): 0x80000000+2G and 0x10000000+0xfcc00000.
        # Every fixed carve-out is below 0xb0000000 (ramdump_fb 0xaf000000+16M),
        # so the kernel goes above them.
        'phys_base': 0xb0000000, 'mem_size': 0x4d000000,
    },
    'miatoll': {
        'gicd': 0x17a00000, 'gicr': 0x17a60000,
        'fb': 0x9c000000, 'fb_size': 0x1700000,
        'ecid': 0x4D4941544F4C4C01,
        'fb_w': 1080, 'fb_h': 2400, 'fb_stride': 1080 * 4,
        'ufs': 0x01d84000, 'ufs_size': 0x3000,
        'timebase': 19200000, 'vtimer': 19,
        'compatible': ['D22AP', 'iPhone10,3', 'xiaomi,curtana', 'qcom,atoll', 'qcom,sm7125', 'AppleARM'],
        'model': 'iPhone10,3', 'target_type': 'D22', 'target_sub_type': 'D22AP',
        'io_compat': ['curtana-io', 'arm-io'],
        'disp_compat': 'qcom,display',
        'ufs_compat': 'qcom,ufshc',
        'cpu_compat': ['qcom,kryo465', 'ARM,v8'],
        # curtana DRAM (live FDT /memory): 0x80000000+0x3cc00000, 0xc0000000+0x40000000,
        # 0x100000000+0x100000000. Every fixed carve-out (hyp 0x80000000, xbl_aop,
        # smem, removed_region 0x80b00000..0x84400000, modem, cdsp, qseecom,
        # cont_splash 0x9c000000 ...) sits in the low bank, and the low bank
        # ends at 0xbcc00000 (0xbcc00000..0xc0000000 is a DRAM hole).
        # The kernel image also reaches 0x1238000 BELOW phys_base (the prelink
        # segments are mapped below virt_base), so phys_base has to sit far
        # enough into the second bank for those to stay in RAM:
        #   image = phys_base-0x1238000 .. phys_base+0x1ee4000
        'phys_base': 0xc4000000, 'mem_size': 0x3c000000,
    },
}
TARGETS['curtana'] = TARGETS['miatoll']

QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE = 0x08000000, 0x080a0000, 62500000
QEMU_VTIMER_INTID = 27
BOOT_MANIFEST_HASH = hashlib.sha384(b'sweet boot manifest').digest()

D22AP_ADT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../research/ipsw-x/d22ap.adt')

def d22ap_product(path=D22AP_ADT):
    if not os.path.exists(path):
        print(f'warning: {path} missing, no /product node', file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit(f'{path}: no /product node')

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode('utf-8') + b'\0'

def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n

def build(seed=None, qemu=False, highmem=False, target='sweet'):
    t = TARGETS.get(target, TARGETS['sweet'])
    PHYS_BASE = t['phys_base']
    MEM_SIZE = t['mem_size']
    HIGH_PHYS_BASE = 0x100000000
    HIGH_MEM_SIZE = 0xfcc00000
    SOC_BASE = 0x01000000
    phys_base, mem_size = (HIGH_PHYS_BASE, HIGH_MEM_SIZE) if highmem else (PHYS_BASE, MEM_SIZE)
    if qemu:
        gicd,gicr,timebase,vtimer = QEMU_GICD,QEMU_GICR,QEMU_TIMEBASE,QEMU_VTIMER_INTID
    else:
        gicd,gicr,timebase,vtimer = t['gicd'],t['gicr'],t['timebase'],t['vtimer']
    ecid = t['ecid']
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr('sweet-shim')),
        ('system-firmware-version', cstr('sweet-shim')),
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        ('crypto-hash-method', cstr('sha2-384')),
        ('display-scale', u32(3)),
        ('unique-chip-id', struct.pack('<Q', ecid)),
        ('chip-id', u32(0x8015)),
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [node('memory-map', [ph()]),
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    cpu_nodes = []
    cpu_compat = t['cpu_compat']
    for core in range(1 if qemu else 8):
        cpu_nodes.append(node(f'cpu{core}', [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', b''.join(cstr(c) for c in cpu_compat)),
            ('reg', u32(core << 8)),
            ('cpu-id', u32(core)),
            ('cluster-id', u32(0)),
            ('cluster-type', u32(0)),
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(core)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(timebase)),
            ('clock-frequency', u32(2304000000 if core >= 6 else 1804800000)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(1866000000)),
            ('peripheral-frequency', u32(timebase)),
            ('fixed-frequency', u32(timebase)),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)), ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(vtimer)),
        ('reg', u64(gicd - SOC_BASE, 0x10000, gicr - SOC_BASE, 0x100000)),
    ])
    io_compat = t['io_compat']
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr(io_compat[0])),
        ('compatible', b''.join(cstr(c) for c in io_compat)),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], [gic,
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr(t['disp_compat'])),
            ('canvas-width', u32(1125)),
            ('canvas-height', u32(2436)),
        ])])

    fbcon = node('sweet-fbcon', [
        ph(),
        ('reg', u64(t['fb'], t['fb_size'])),
        ('width', u32(t['fb_w'])),
        ('height', u32(t['fb_h'])),
        ('stride', u32(t['fb_stride'])),
    ])

    ufs = node('sweet-ufs', [
        ph(),
        ('compatible', cstr(t['ufs_compat'])),
        ('reg', u64(t['ufs'], t['ufs_size'])),
    ])

    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])
    product_props = d22ap_product()
    product = [node('product', [ph()] + product_props)] if product_props else []

    return node('device-tree', [
        ph(),
        ('compatible', b''.join(cstr(c) for c in t['compatible'])),
        ('model', cstr(t['model'])),
        ('target-type', cstr(t['target_type'])),
        ('target-sub-type', cstr(t['target_sub_type'])),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons] + product + ([] if qemu else [ufs]))

if __name__ == '__main__':
    target = 'sweet'
    for i,a in enumerate(sys.argv):
        if a.startswith('--target='):
            target = a.split('=',1)[1]
        elif a == '--target' and i+1 < len(sys.argv):
            target = sys.argv[i+1]
    root = build(qemu='--qemu' in sys.argv, highmem='--highmem' in sys.argv, target=target)
    blob = adt.serialize(root)
    open(sys.argv[1],'wb').write(blob)
    if '--dump' in sys.argv:
        adt.dump(adt.parse(blob)[0])
    print(f'{sys.argv[1]}: {len(blob)} bytes', file=sys.stderr)
