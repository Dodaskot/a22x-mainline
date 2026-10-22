/* SPDX-License-Identifier: GPL-2.0-only */
/* CPU-only diagnostic. Host SRAM alias/ROM entry remain unverified. */
.section .vectors,"ax",@progbits
.global _start
_start:
    j diagnostic_start
.rept 15
    j diagnostic_trap
.endr
.section .text.start,"ax",@progbits
.global diagnostic_start
diagnostic_start:
    setgie.d
    dsb
    sethi $r0, 0x40000
    mtsr $r0, $IVB
    isb
    sethi $sp, hi20(__stack_top)
    ori $sp, $sp, lo12(__stack_top)
    jal diagnostic_main
.global diagnostic_done
diagnostic_done:
    j diagnostic_done
.global diagnostic_trap
diagnostic_trap:
    setgie.d
    dsb
    j diagnostic_trap
