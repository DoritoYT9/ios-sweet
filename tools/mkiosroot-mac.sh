#!/bin/bash
# Build the SWEET iOS root on macOS (port of tools/mkiosroot.sh, which needs
# apfs-fuse + hfsprogs and only runs on Linux).
#
#   sudo tools/mkiosroot-mac.sh OS.dmg SYSTEMOS.dmg APPOS.dmg OUT.img [SIZE]
#
#   OS.dmg        the iOS root filesystem  (087-86698-044.dmg)
#   SYSTEMOS.dmg  the SystemOS cryptex     (087-86593-047.dmg)
#   APPOS.dmg     the AppOS cryptex        (087-86715-047.dmg)
#   OUT.img       raw HFS+ volume to create (16G default)
#
# The IPSW images are APFS with LZBITMAP-compressed files; macOS mounts them
# read-only with hdiutil and decompresses on the fly. The HFS+ copy is
# uncompressed, so the iOS 16.7.16 root needs ~12 GB of the 16 GB volume.
#
# macOS cannot report the ownership of a case-sensitive APFS volume (it is
# mounted "noowners": Mac OS X does not support owners on case-sensitive
# volumes), so everything lands as root:wheel and the few paths that need
# another owner are fixed up explicitly (mobile 501, _logd 272).
#
# The result is a raw HFS+ volume (no wrapper) so that it can be flashed as
# the first bytes of a partition; XNU mounts it with rd=ufsroot0.
set -euo pipefail

[[ $# -ge 4 ]] || { sed -n '2,17p' "$0"; exit 2; }
os=$(realpath "$1"); sysos=$(realpath "$2"); appos=$(realpath "$3"); out=$4; size=${5:-16G}
[[ ! -e $out ]] || { echo "$out already exists" >&2; exit 1; }

work=$(mktemp -d "${TMPDIR:-/tmp}/mkiosroot-mac.XXXXXX")
attached=()      # dmg mount points to detach
imgdev=""        # device of the HFS+ image being built
imgmnt=""        # mount point of the HFS+ image

cleanup() {
	[ -n "$imgmnt" ] && umount "$imgmnt" 2>/dev/null || true
	for d in "${attached[@]:-}"; do
		[ -n "$d" ] && hdiutil detach "$d" 2>/dev/null || true
	done
	[ -n "$imgdev" ] && hdiutil detach "$imgdev" 2>/dev/null || true
	rm -rf "$work"
}
trap cleanup EXIT

# Mount an APFS dmg read-only and echo its mount point.
attach_img() {
	local point
	point=$(hdiutil attach -readonly -nobrowse "$1" | tail -1 | awk '{print $NF}')
	attached+=("$point")
	echo "$point"
}

# A file with the given owner and mode (install(1) refuses /dev/null on macOS).
own_file() {
	local path=$1 mode=$2 uid=$3 gid=$4
	: > "$path"
	chown "$uid:$gid" "$path"
	chmod "$mode" "$path"
}

os_m=$(attach_img "$os")
sysos_m=$(attach_img "$sysos")
appos_m=$(attach_img "$appos")

# The HFS+ volume: raw file, case-sensitive, journaled, volume at offset 0.
truncate -s "$size" "$out"
imgdev=$(hdiutil attach -nomount -imagekey diskimage-class=CRawDiskImage "$out" | tail -1 | awk '{print $NF}')
newfs_hfs -s -J -v iOSRoot "$imgdev"
mkdir "$work/root"
mount -t hfs "$imgdev" "$work/root"
imgmnt="$work/root"

copy() {
	echo "copying $1 -> ${2#"$work/root"}"
	mkdir -p "$2"
	rsync -aH --no-owner --no-group "$1/" "$2/"
}
copy "$os_m" "$work/root"
copy "$sysos_m" "$work/root/private/preboot/Cryptexes/OS"
copy "$appos_m" "$work/root/private/preboot/Cryptexes/App"

# Shared cache where dyld looks at ignition level 1: hard links, no extra space.
dsc=private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld
mkdir -p "$work/root/System/Library/Caches/com.apple.dyld"
for f in "$work/root/$dsc"/*; do
	ln "$f" "$work/root/System/Library/Caches/com.apple.dyld/${f##*/}"
done

# dyld does not search the cryptexes at ignition level 1: hard-link every
# cryptex file that has no counterpart in the OS root (libobjc-trampolines,
# libffi-trampolines, libstdc++, ...) to its root path.
for c in OS App; do
	(cd "$work/root/private/preboot/Cryptexes/$c" && find . ! -type d ! -path './System/Library/Caches/com.apple.dyld/*') |
	while IFS= read -r f; do
		f=${f#./}
		if ! test -e "$work/root/$f" && ! test -L "$work/root/$f"; then
			mkdir -p "$work/root/$(dirname "$f")"
			src="$work/root/private/preboot/Cryptexes/$c/$f"
			# HFS+ cannot hard-link symlinks: copy those.
			if test -L "$src"; then cp -P "$src" "$work/root/$f"; else ln "$src" "$work/root/$f"; fi
			echo "linked $c:/$f"
		fi
	done
done

# logd's log store, normally made at restore: it checks owner _logd (272)
# and mode, plus a .migrated marker, else asks a helper and crashes
# ("BUG IN LIBTRACE: Failed to migrate directory").
db="$work/root/private/var/db"
install -d -o 272 -g 272 -m 0751 "$db/diagnostics"
install -d -o 272 -g 272 -m 0755 "$db/uuidtext" "$db/diagnostics/timesync"
for d in diagnostics uuidtext diagnostics/timesync; do
	own_file "$db/$d/.migrated" 0644 272 272
done

# System keybag for the built-in AppleKeyStore (no passcode).
install -d -m 0700 -o 0 -g 0 "$work/root/private/var/keybags"
printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' \
	'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
	'<plist version="1.0"><dict><key>KeyBagKeys</key><data>U1dFRVQ=</data></dict></plist>' \
	> "$work/systembag.kb"
install -m 0600 -o 0 -g 0 "$work/systembag.kb" "$work/root/private/var/keybags/systembag.kb"

# The data volume is not part of the rootfs: the mobile user's home belongs to
# uid 501 (mobile), everything else stays root:wheel.
if [ -d "$work/root/private/var/mobile" ]; then
	chown -R 501:501 "$work/root/private/var/mobile"
fi

# SWEET bring-up changes (launchd, containers, migration, audio, SpringBoard):
# these are what made the same image reach the home screen on sweet.
tweaks="$(dirname "$0")/sweet-root-tweaks.py"
[ -f "$tweaks" ] && python3 "$tweaks" "$work/root"

df -h "$work/root" | tail -1
umount "$work/root"
imgmnt=""
hdiutil detach "$imgdev" >/dev/null
imgdev=""

# Verify the volume (read-only check).
fsck_hfs -fn "$out" || true
echo "built $out"
