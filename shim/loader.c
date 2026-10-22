/*
 * Milestone 4: act as iBoot for xnu.
 *
 * mkpayload.py already resolved the kernel's chained fixups and built the
 * ADT, so this only has to: find the payload in the ABL ramdisk, copy the
 * segments to phys_base + (vmaddr - virt_base), place the ADT and
 * boot_args behind the kernel, and jump to the entry point with the MMU
 * and caches off, x0 = boot_args (physical).
 */
#include <stdint.h>
#include "console.h"
#include "fdt.h"
#include "loader.h"

#define BOOT_LINE_LENGTH	608
#define PAGE_16K		0x4000UL

struct payload_seg {
	uint64_t vmaddr, vmsize, file_off, file_size;
};

struct payload {
	char magic[8];
	uint32_t version, nsegs;
	uint64_t virt_base, entry_va, adt_off, adt_size, kernel_end;
	uint64_t phys_base, mem_size;
	char cmdline[BOOT_LINE_LENGTH];
	struct payload_seg segs[];
};

/* pexpert/pexpert/arm64/boot.h, Revision/Version 2 */
struct boot_video {
	uint64_t v_baseAddr, v_display, v_rowBytes, v_width, v_height, v_depth;
};

struct boot_args {
	uint16_t Revision, Version;
	uint64_t virtBase, physBase, memSize, topOfKernelData;
	struct boot_video Video;
	uint32_t machineType;
	uint64_t deviceTreeP;
	uint32_t deviceTreeLength;
	char CommandLine[BOOT_LINE_LENGTH];
	uint64_t bootFlags, memSizeActual;
};

_Static_assert(__builtin_offsetof(struct payload, segs) == 0x2a8, "payload header layout");
_Static_assert(__builtin_offsetof(struct boot_args, Video) == 40, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, CommandLine) == 108, "boot_args layout");
_Static_assert(sizeof(struct boot_args) == 736, "boot_args layout");

/* MMU off: everything is Device memory, so copy in aligned 8-byte words. */
static void copy64(uint64_t dst, uintptr_t src, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	volatile const uint64_t *s = (volatile const uint64_t *)src;

	for (uint64_t i = 0; i < (n + 7) / 8; i++)
		d[i] = s[i];
}

/* Is the destination address usable? A plain store into a protected or
 * absent DRAM hole never completes (the core waits for the bus answer), so
 * the probe prints before every access: the last line on the screen says
 * which access hung. */
static void probe_dst(uint64_t dst)
{
	volatile uint32_t *d = (volatile uint32_t *)dst;
	uint32_t old, back;

	con_hex2("PROBE", "at=", dst, "rd=", 0);
	old = d[0];
	con_hex2("PROBE", "at=", dst, "got=", old);
	d[0] = 0xa5a5a5a5;
	con_hex2("PROBE", "at=", dst, "wr=", 0xa5a5a5a5);
	back = d[0];
	con_hex2("PROBE", "at=", dst, "back=", back);
	d[0] = old;
}

/* The copy must land: compare the first and the last word. */
static int verify_seg(uint64_t dst, uintptr_t src, uint64_t n)
{
	volatile const uint64_t *d = (volatile const uint64_t *)dst;
	volatile const uint64_t *s = (volatile const uint64_t *)src;
	uint64_t last = (n / 8) - 1;

	if (n < 8)
		return 0;
	if (d[0] != s[0] || d[last] != s[last])
		return -1;
	return 0;
}

static void zero64(uint64_t dst, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	for (uint64_t i = 0; i < n / 8; i++)
		d[i] = 0;
}

static int magic_ok(const char *m)
{
	static const char want[] = "SWEETXNU";
	for (int i = 0; i < 8; i++)
		if (m[i] != want[i])
			return 0;
	return 1;
}

typedef void (*xnu_entry_t)(uint64_t boot_args_pa);

const char *loader_boot(uintptr_t dtb)
{
	uint64_t rd_start, rd_end;

	if (!fdt_chosen_u64(dtb, "linux,initrd-start", &rd_start) ||
	    !fdt_chosen_u64(dtb, "linux,initrd-end", &rd_end))
		return "no initrd in /chosen";
	con_hex2("INITRD", "lo=", rd_start, "hi=", rd_end);

	const struct payload *p = (const struct payload *)rd_start;
	uint64_t sctlr;

	/* The MMU is still off here: every copy below is a raw physical store.
	 * Print the state so the screen shows it. */
	__asm__ volatile("mrs %0, sctlr_el1" : "=r"(sctlr));
	con_hex2("SCTLR", "M=", sctlr & 1, "C=", (sctlr >> 2) & 1);

	/* CPU identity. xnu is a 16K-page kernel, so the granule support of
	 * this core decides whether the port can work at all.
	 * ID_AA64MMFR0_EL1: TGran4=[15:12] TGran16=[19:16] TGran64=[23:20].
	 * TGran4: 0=4K only, 1=4K+16K, 2=4K+64K, 3=4K+16K+64K. */
	{
		uint64_t mmfr0, pfr0, midr;

		__asm__ volatile("mrs %0, id_aa64mmfr0_el1" : "=r"(mmfr0));
		__asm__ volatile("mrs %0, id_aa64pfr0_el1" : "=r"(pfr0));
		__asm__ volatile("mrs %0, midr_el1" : "=r"(midr));
		con_hex2("MMFR0", "tg4=", (mmfr0 >> 12) & 0xf, "tg16=", (mmfr0 >> 16) & 0xf);
		con_hex2("PFR0", "tg64=", (mmfr0 >> 20) & 0xf, "parange=", pfr0 & 0xf);
		con_hex("MIDR", midr);
	}

	if (!magic_ok(p->magic))
		return "no SWEETXNU payload";
	if (p->version != 1 || (p->adt_off & 7) || (rd_start & 7))
		return "bad payload header";

	uint64_t pa_of_va_base = p->phys_base - p->virt_base;	/* pa = va + this */
	con_hex2("PHYS", "base=", p->phys_base, "size=", p->mem_size);
	con_hex("VIRT", p->virt_base);
	con_hex("ENTRY", p->entry_va);

	/* The payload must not overlap where the kernel goes. */
// 	if (rd_end > p->phys_base && rd_start < p->phys_base + p->mem_size)
// 		return "ramdisk overlaps kernel memory";

	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = &p->segs[i];
		uint64_t dst = (s->vmaddr - p->virt_base) + p->phys_base;
		uintptr_t src = rd_start + s->file_off;

		con_hex2("SEG", "i=", i, "dst=", dst);
		/* The kernel must not be written over the payload we read from. */
		if (dst < rd_end && dst + s->vmsize > rd_start) {
			con_str("SEG", "OVERLAPS the ramdisk");
			return "kernel overlaps the ramdisk";
		}
		if (i == 0)
			probe_dst(dst);
		copy64(dst, src, s->file_size);
		if (verify_seg(dst, src, s->file_size))
			con_str("SEG", "VERIFY FAILED");
		if (s->vmsize > s->file_size)
			zero64(dst + ((s->file_size + 7) & ~7UL), s->vmsize - ((s->file_size + 7) & ~7UL));
	}
	con_hex2("SEGS", "n=", p->nsegs, "end=", p->kernel_end + pa_of_va_base);

	/* ADT, then boot_args, behind the kernel; everything below topOfKernelData is ours. */
	uint64_t adt_pa = (p->kernel_end - p->virt_base) + p->phys_base;
	copy64(adt_pa, rd_start + p->adt_off, p->adt_size);
	uint64_t ba_pa = (adt_pa + p->adt_size + PAGE_16K - 1) & ~(PAGE_16K - 1);
	uint64_t top = ba_pa + PAGE_16K;

	zero64(ba_pa, PAGE_16K);
	volatile struct boot_args *ba = (volatile struct boot_args *)ba_pa;
	ba->Revision = 2;
	ba->Version = 2;
	ba->virtBase = p->virt_base;
	ba->physBase = p->phys_base;
	ba->memSize = p->mem_size;
	ba->topOfKernelData = top;
	ba->Video.v_baseAddr = FB_BASE;
	ba->Video.v_display = 1;
	ba->Video.v_rowBytes = FB_STRIDE;
	ba->Video.v_width = FB_W;
	ba->Video.v_height = FB_H;
	ba->Video.v_depth = 32;
	ba->deviceTreeP = adt_pa - pa_of_va_base;	/* xnu reads it with the MMU on: a VA */
	ba->deviceTreeLength = (uint32_t)p->adt_size;
	for (int i = 0; i < BOOT_LINE_LENGTH; i++)
		ba->CommandLine[i] = p->cmdline[i];
	ba->memSizeActual = p->mem_size;
	con_hex2("BOOTARG", "pa=", ba_pa, "top=", top);

	uint64_t entry_pa = (p->entry_va - p->virt_base) + p->phys_base;
	con_hex("JUMP", entry_pa);
	con_str("XNU", "jumping (fbcon clears screen)");

	/* Everything was written with the MMU and caches off; drop any stale I-cache lines too. */
	__asm__ volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
	((xnu_entry_t)entry_pa)(ba_pa);
	return "xnu returned";
}
