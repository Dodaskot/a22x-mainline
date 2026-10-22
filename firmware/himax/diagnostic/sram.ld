/* CPU address model, not hardware alias validation. */
OUTPUT_ARCH(nds32)
ENTRY(_start)
MEMORY { SRAM (rwx) : ORIGIN = 0x40000000, LENGTH = 64K }
SECTIONS {
    .vectors : { KEEP(*(.vectors)) } > SRAM
    .text 0x40000100 : { *(.text.start) *(.text*) *(.rodata*) } > SRAM
    .data : { *(.data*) *(.sdata*) } > SRAM
    .bss (NOLOAD) : { *(.bss*) *(.sbss*) *(COMMON) } > SRAM
    __code_end = .;
    .mailbox 0x4000f000 (NOLOAD) : { KEEP(*(.mailbox)) } > SRAM
    __stack_top = 0x40010000;
    ASSERT(SIZEOF(.vectors) == 64, "expected sixteen four-byte vectors")
    ASSERT(__code_end < 0x4000f000, "code overlaps mailbox")
    ASSERT(SIZEOF(.mailbox) == 28, "mailbox layout changed")
    /DISCARD/ : { *(.comment) *(.note*) }
}
