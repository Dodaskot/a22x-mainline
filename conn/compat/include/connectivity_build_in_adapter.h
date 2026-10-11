/* SPDX-License-Identifier: GPL-2.0 */
/*
 * a22x mainline replacement for the vendor connectivity_build_in_adapter:
 * the vendor kernel exported MTK-internal PMIC/clock-buffer/SPM helpers
 * through it; here they are implemented on mainline APIs (a22x_adapter.c).
 */
#ifndef CONNECTIVITY_BUILD_IN_ADAPTER_H
#define CONNECTIVITY_BUILD_IN_ADAPTER_H

#include <linux/kernel.h>
#include <linux/sched.h>
#include <linux/sched/debug.h>
#include <linux/mm.h>
#include <linux/dma-mapping.h>
#include <linux/sched/clock.h>

/* clock buffer (MT6359 DCXO) */
enum clk_buf_id {
	CLK_BUF_BB_MD = 0,
	CLK_BUF_CONN,
	CLK_BUF_NFC,
	CLK_BUF_RF,
	CLK_BUF_UFS,
	CLK_BUF_AUDIO,
	CLK_BUF_CHG,
	CLK_BUF_EXT,
	CLK_BUF_INVALID,
};
#define CONNADP_HAS_CLOCK_BUF_CTRL
#define KERNEL_CLK_BUF_CHIP_NOT_SUPPORT -7788
#define KERNEL_clk_buf_ctrl connectivity_export_clk_buf_ctrl
#define KERNEL_clk_buf_show_status_info connectivity_export_clk_buf_show_status_info
#define KERNEL_clk_buf_get_xo_en_sta connectivity_export_clk_buf_get_xo_en_sta
void connectivity_export_clk_buf_ctrl(enum clk_buf_id id, bool onoff);
void connectivity_export_clk_buf_show_status_info(void);
int connectivity_export_clk_buf_get_xo_en_sta(int id);

/* PMIC fields touched by the connectivity code (vendor enum PMU_FLAGS_LIST) */
enum a22x_pmic_field {
	PMIC_RG_LDO_VCN13_LP,
	PMIC_RG_LDO_VCN18_LP,
	PMIC_RG_LDO_VCN33_1_LP,
	PMIC_RG_LDO_VCN33_2_LP,
	PMIC_RG_BUCK_VS2_VOSEL,
	PMIC_RG_BUCK_VS2_VOSEL_SLEEP,
	PMIC_RG_BUCK_VS2_VOTER_EN_SET,
	PMIC_RG_BUCK_VS2_VOTER_EN_CLR,
	PMIC_RG_VCN13_VOCAL,
	A22X_PMIC_FIELD_NR,
};

/* low-power control users / modes (vendor mtk_pmic_api_buck.h) */
enum BUCK_LDO_EN_USER {
	SRCLKEN0, SRCLKEN1, SRCLKEN2, SRCLKEN3, SRCLKEN4,
	SRCLKEN5, SRCLKEN6, SRCLKEN7, SRCLKEN8, SRCLKEN9, SRCLKEN10, SRCLKEN11,
	SRCLKEN12, SRCLKEN13, SRCLKEN14, SW, SPM = SW,
};
#define HW_OFF	0
#define HW_LP	1
#define HW_ON	0
#define SW_OFF	0
#define SW_EN	1
#define SW_LP	3
#define PMIC_CTRL_ENABLE 1

#define KERNEL_pmic_config_interface connectivity_export_pmic_config_interface
#define KERNEL_pmic_read_interface connectivity_export_pmic_read_interface
#define KERNEL_pmic_set_register_value connectivity_export_pmic_set_register_value
#define KERNEL_pmic_get_register_value connectivity_export_pmic_get_register_value
#define KERNEL_upmu_set_reg_value connectivity_export_upmu_set_reg_value
#define KERNEL_pmic_ldo_vcn13_lp connectivity_export_pmic_ldo_vcn13_lp
#define KERNEL_pmic_ldo_vcn18_lp connectivity_export_pmic_ldo_vcn18_lp
#define KERNEL_pmic_ldo_vfe28_lp connectivity_export_pmic_ldo_vfe28_lp
#define KERNEL_pmic_ldo_vcn33_1_lp connectivity_export_pmic_ldo_vcn33_1_lp
#define KERNEL_pmic_ldo_vcn33_2_lp connectivity_export_pmic_ldo_vcn33_2_lp
void connectivity_export_pmic_config_interface(unsigned int reg, unsigned int val,
					       unsigned int mask, unsigned int shift);
void connectivity_export_pmic_read_interface(unsigned int reg, unsigned int *val,
					     unsigned int mask, unsigned int shift);
void connectivity_export_pmic_set_register_value(int field, unsigned int val);
unsigned short connectivity_export_pmic_get_register_value(int field);
void connectivity_export_upmu_set_reg_value(unsigned int reg, unsigned int val);
int connectivity_export_pmic_ldo_vcn13_lp(int user, int op_mode, unsigned char op_en, unsigned char op_cfg);
int connectivity_export_pmic_ldo_vcn18_lp(int user, int op_mode, unsigned char op_en, unsigned char op_cfg);
void connectivity_export_pmic_ldo_vfe28_lp(unsigned int user, int op_mode, unsigned char op_en, unsigned char op_cfg);
int connectivity_export_pmic_ldo_vcn33_1_lp(int user, int op_mode, unsigned char op_en, unsigned char op_cfg);
int connectivity_export_pmic_ldo_vcn33_2_lp(int user, int op_mode, unsigned char op_en, unsigned char op_cfg);

/* SDIO combo chips: unused on this SoC */
#define KERNEL_mmc_io_rw_direct connectivity_export_mmc_io_rw_direct
struct mmc_card;
int connectivity_export_mmc_io_rw_direct(struct mmc_card *card, int write, unsigned int fn,
					 unsigned int addr, u8 in, u8 *out);

#define KERNEL_mt_ppm_sysboost_freq
#define KERNEL_mt_ppm_sysboost_core
#define KERNEL_mt_ppm_sysboost_set_core_limit
#define KERNEL_mt_ppm_sysboost_set_freq_limit
#define KERNEL_spm_resource_req

#define KERNEL_show_stack connectivity_export_show_stack
#define KERNEL_tracing_record_cmdline connectivity_export_tracing_record_cmdline
#define KERNEL_dump_thread_state connectivity_export_dump_thread_state
void connectivity_export_show_stack(struct task_struct *tsk, unsigned long *sp);
void connectivity_export_dump_thread_state(const char *name);
void connectivity_export_tracing_record_cmdline(struct task_struct *tsk);
void connectivity_export_conap_scp_init(unsigned int chip_info, phys_addr_t emi_phy_addr);
void connectivity_export_conap_scp_deinit(void);

#define KERNEL_event_trace_printk(ip, fmt, args...) do { } while (0)

#define KERNEL_gpio_dump_regs_range connectivity_export_dump_gpio_info
void connectivity_export_dump_gpio_info(int start, int end);
int connectivity_export_gpio_get_tristate_input(unsigned int pin);

extern phys_addr_t gConEmiPhyBase;
extern unsigned long long gConEmiSize;
extern phys_addr_t gGpsRsvMemPhyBase;
extern unsigned long long gGpsRsvMemSize;

#endif /* CONNECTIVITY_BUILD_IN_ADAPTER_H */
