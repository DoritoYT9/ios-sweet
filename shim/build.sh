#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
	CC=aarch64-linux-gnu-gcc
	OBJCOPY=aarch64-linux-gnu-objcopy
	READELF=aarch64-linux-gnu-readelf
	NM=aarch64-linux-gnu-nm
	LD=""
else
	CC="clang -target aarch64-none-elf"
	if [ -x /Users/doritoyt/local/llvm19/bin/ld.lld ]; then LD=/Users/doritoyt/local/llvm19/bin/ld.lld
	elif command -v ld.lld >/dev/null 2>&1; then LD=$(command -v ld.lld)
	else LD=""; fi
	OBJCOPY=/Users/doritoyt/local/llvm19/bin/llvm-objcopy
	READELF=/Users/doritoyt/local/llvm19/bin/llvm-readelf
	NM=/Users/doritoyt/local/llvm19/bin/llvm-nm
fi
CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra"
mkdir -p out
for f in start.S shim.c console.c mmu.c fdt.c loader.c ufs.c; do
	if [[ "$CC" == *clang* ]]; then
		clang -target aarch64-none-elf $CFLAGS -c $f -o out/${f%.*}.o
	else
		$CC $CFLAGS -c $f -o out/${f%.*}.o
	fi
done
if [[ "$CC" == *clang* ]] && [ -n "${LD:-}" ]; then
	clang -target aarch64-none-elf $CFLAGS --ld-path=$LD -static -T shim.ld -Wl,--build-id=none -Wl,-N \
		out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o -o out/shim.elf
else
	$CC $CFLAGS -static -T shim.ld -Wl,--build-id=none -Wl,-N \
		out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o -o out/shim.elf
fi
$OBJCOPY -O binary out/shim.elf out/Image
size=$($NM out/shim.elf 2>/dev/null | awk '$3=="_end"{print ("0x"$1)+0}')
[ -n "${size:-}" ] && truncate -s "$size" out/Image || true
if [ -n "${1:-}" ]; then
	cp "$1" out/ramdisk.bin
else
	(cd out && rm -rf rd && mkdir rd && cd rd && (find . -print0 2>/dev/null | cpio -o -H newc --null 2>/dev/null || find . | cpio -o -H newc 2>/dev/null) | gzip -9 > ../ramdisk.bin) || printf '' | gzip -9 > ../ramdisk.bin
fi
if command -v mkbootimg >/dev/null 2>&1; then
	mkbootimg --header_version 2 --kernel out/Image --ramdisk out/ramdisk.bin \
		--dtb ../dtb/boot_unpacked/dtb --base 0x80000000 --kernel_offset 0x8000 --ramdisk_offset 0x1000000 \
		--tags_offset 0x100 --dtb_offset 0x1f00000 --pagesize 4096 \
		--os_version 17.0.0 --os_patch_level 2035-06 \
		--cmdline "console=null" -o out/shim-boot.img
else
	python3 /Users/doritoyt/ios-sweet/tools/mkbootimg_v2.py --kernel out/Image --ramdisk out/ramdisk.bin \
		--dtb ../dtb/boot_unpacked/dtb --header_version 2 --pagesize 4096 \
		--base 0x80000000 --kernel_offset 0x8000 --ramdisk_offset 0x1000000 --tags_offset 0x100 --dtb_offset 0x1f00000 \
		--os_version 17.0.0 --os_patch_level 2035-06 --cmdline "console=null" -o out/shim-boot.img
fi
ls -lh out/Image out/shim-boot.img 2>&1 | tail -2
