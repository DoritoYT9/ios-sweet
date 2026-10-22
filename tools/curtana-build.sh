#!/bin/sh
# Build the curtana (SM7125) boot image: ADT -> payload -> shim -> boot img.
#
#   tools/curtana-build.sh KERNEL [OUT-IMG]
#
# KERNEL  = the SWEET kernel Mach-O (xnu/out-NNNN/kernel.development.sweet).
#           The stock Apple kernelcache does NOT work here (arm64e, no PAC,
#           no Qualcomm support).
# OUT-IMG = boot image to write (default shim/out/shim-boot-curtana.img).
#
# Extra boot args can be added with EXTRA="sweetusb=2" (USB live console).
#
# Flash with:  fastboot flash boot <OUT-IMG> && fastboot reboot
# (this phone's ABL does not implement "fastboot boot").
set -e

cd "$(dirname "$0")/.."

KERNEL="$1"
[ -n "$KERNEL" ] || { echo "usage: $0 KERNEL [OUT-IMG]" >&2; exit 1; }
[ -f "$KERNEL" ] || { echo "$KERNEL: not found" >&2; exit 1; }

OUT="${2:-shim/out/shim-boot-curtana.img}"
EXTRA="${EXTRA:-}"

# The ADT carries phys_base/mem_size (0xc4000000/0x3c000000 for curtana) and
# the MMIO map; mkpayload.py reads them back from it.
CPUS="${CPUS:-1}"

python3 tools/mkadt.py shim/out/curtana.adt --target=curtana

python3 tools/mkpayload.py "$KERNEL" shim/out/curtana.adt shim/out/payload-curtana.bin \
	--cmdline "debug=0xc serial=3 cpus=$CPUS keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1 $EXTRA"

(cd shim && ./build.sh ../shim/out/payload-curtana.bin)
cp shim/out/shim-boot.img "$OUT"
ls -l "$OUT"
