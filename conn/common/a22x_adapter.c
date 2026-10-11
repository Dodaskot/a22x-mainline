// SPDX-License-Identifier: GPL-2.0
/*
 * Mainline implementation of the vendor connectivity_build_in_adapter for
 * SM-A226B (MT6833 + MT6359P). PMIC fields are written through the PMIC
 * wrapper regmap. Hardware low-power voters remain explicit bring-up opt-in.
 */
#include <linux/of.h>
#include <linux/mfd/mt6359p/registers.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/sched/debug.h>
#include "connectivity_build_in_adapter.h"

static bool pmic_hw_lp;
module_param(pmic_hw_lp, bool, 0444);
MODULE_PARM_DESC(pmic_hw_lp, "Opt-in MT6359P hardware CONN LDO voters at power-on");

/* gConEmiPhyBase/gConEmiSize live in mtk_wcn_consys_hw.c */
phys_addr_t gGpsRsvMemPhyBase;
unsigned long long gGpsRsvMemSize;

static const struct {
	u16 reg;
	u16 mask;
	u8 shift;
} a22x_pmic_fields[A22X_PMIC_FIELD_NR] = {
	[PMIC_RG_LDO_VCN13_LP]		= { MT6359P_LDO_VCN13_CON0, 0x1, 1 },
	[PMIC_RG_LDO_VCN18_LP]		= { MT6359P_LDO_VCN18_CON0, 0x1, 1 },
	[PMIC_RG_LDO_VCN33_1_LP]	= { MT6359P_LDO_VCN33_1_CON0, 0x1, 1 },
	[PMIC_RG_LDO_VCN33_2_LP]	= { MT6359P_LDO_VCN33_2_CON0, 0x1, 1 },
	[PMIC_RG_BUCK_VS2_VOSEL]	= { 0x18b4, 0x7f, 0 },
	[PMIC_RG_BUCK_VS2_VOSEL_SLEEP]	= { 0x188e, 0x7f, 0 },
	[PMIC_RG_BUCK_VS2_VOTER_EN_SET]	= { 0x18ac, 0xfff, 0 },
	[PMIC_RG_BUCK_VS2_VOTER_EN_CLR]	= { 0x18ae, 0xfff, 0 },
	[PMIC_RG_VCN13_VOCAL]		= { 0x202e, 0xf, 0 },
};

static struct regmap *a22x_pmic_regmap(void)
{
	static struct regmap *map;
	struct device_node *np;
	struct platform_device *pdev;

	if (map)
		return map;
	np = of_find_compatible_node(NULL, NULL, "mediatek,mt6359");
	if (!np)
		return NULL;
	pdev = of_find_device_by_node(np);
	of_node_put(np);
	if (!pdev)
		return NULL;
	/* the MFD core uses the PMIC wrapper's regmap */
	map = dev_get_regmap(pdev->dev.parent, NULL);
	put_device(&pdev->dev);
	if (!map)
		pr_err("a22x-conn: no MT6359 regmap\n");
	return map;
}

void connectivity_export_pmic_config_interface(unsigned int reg, unsigned int val,
					       unsigned int mask, unsigned int shift)
{
	struct regmap *map = a22x_pmic_regmap();

	if (map)
		regmap_update_bits(map, reg, mask << shift, (val & mask) << shift);
}

void connectivity_export_pmic_read_interface(unsigned int reg, unsigned int *val,
					     unsigned int mask, unsigned int shift)
{
	struct regmap *map = a22x_pmic_regmap();
	unsigned int v = 0;

	if (map)
		regmap_read(map, reg, &v);
	*val = (v >> shift) & mask;
}

void connectivity_export_pmic_set_register_value(int field, unsigned int val)
{
	if (field < 0 || field >= A22X_PMIC_FIELD_NR)
		return;
	/* RC idle sets SW_LP and relies on hardware voters to force normal mode
	 * for RF activity. Without those voters, software LP breaks calibration.
	 * Clearing LP is always allowed; enabling it belongs to the same opt-in
	 * policy as the hardware votes, never to an address-only correction.
	 */
	if (!pmic_hw_lp && field <= PMIC_RG_LDO_VCN33_2_LP && (val & 1)) {
		pr_info_once("a22x-conn: software LP held off until hardware voters are enabled\n");
		return;
	}
	pr_debug("a22x-conn: pmic field %d = 0x%x\n", field, val);
	connectivity_export_pmic_config_interface(a22x_pmic_fields[field].reg, val,
						  a22x_pmic_fields[field].mask,
						  a22x_pmic_fields[field].shift);
}

unsigned short connectivity_export_pmic_get_register_value(int field)
{
	unsigned int v = 0;

	if (field < 0 || field >= A22X_PMIC_FIELD_NR)
		return 0;
	connectivity_export_pmic_read_interface(a22x_pmic_fields[field].reg, &v,
						a22x_pmic_fields[field].mask,
						a22x_pmic_fields[field].shift);
	return v;
}

void connectivity_export_upmu_set_reg_value(unsigned int reg, unsigned int val)
{
	struct regmap *map = a22x_pmic_regmap();

	if (map)
		regmap_write(map, reg, val);
}

static DEFINE_MUTEX(a22x_pmic_lp_lock);

/* MT6359P CONN LDOs: OP_EN = CON0 + 6, OP_CFG = OP_EN + 6.
 * Samsung pmic_lp_api.c assigns OP_MODE bits 10..12 only to SRCLKEN0..2.
 * RC users 4..7 use multi-user votes without changing those mode bits.
 */
static int a22x_pmic_ldo_hw_lp(unsigned int con0, int user, int op_mode,
			     unsigned char op_en, unsigned char op_cfg)
{
	struct regmap *map;
	unsigned int en_reg = con0 + 6, cfg_reg = con0 + 12;
	unsigned int bit, mode_mask = 0, mode_value = 0;
	unsigned int old_mode, old_en, old_cfg, value, id;
	int ret, restore_ret;

	if (!pmic_hw_lp)
		return 0;
	/* This adapter owns hardware voters, not software regulator enables. */
	if (user < SRCLKEN0 || user > SRCLKEN14 ||
	    op_mode < 0 || op_mode > 1 || op_en > 1 || op_cfg > HW_LP)
		return -EINVAL;
	map = a22x_pmic_regmap();
	if (!map)
		return -ENODEV;
	bit = BIT(user);
	if (user <= SRCLKEN2) {
		mode_mask = BIT(10 + user);
		mode_value = op_mode ? mode_mask : 0;
	}
	mutex_lock(&a22x_pmic_lp_lock);
	ret = regmap_read(map, MT6359P_HWCID, &id);
	if (ret)
		goto out;
	if (id != MT6359P_CHIP_VER) {
		ret = -ENODEV;
		goto out;
	}
	ret = regmap_read(map, con0, &old_mode);
	if (ret)
		goto out;
	ret = regmap_read(map, en_reg, &old_en);
	if (ret)
		goto out;
	ret = regmap_read(map, cfg_reg, &old_cfg);
	if (ret)
		goto out;

	if (mode_mask) {
		ret = regmap_update_bits(map, con0, mode_mask, mode_value);
		if (ret)
			goto restore;
	}
	/* Establish the selected operation before enabling a new voter. */
	ret = regmap_update_bits(map, cfg_reg, bit, op_cfg ? bit : 0);
	if (ret)
		goto restore;
	ret = regmap_update_bits(map, en_reg, bit, op_en ? bit : 0);
	if (ret)
		goto restore;
	ret = regmap_read(map, en_reg, &value);
	if (!ret && (value & bit) != (op_en ? bit : 0))
		ret = -EIO;
	if (ret)
		goto restore;
	ret = regmap_read(map, cfg_reg, &value);
	if (!ret && (value & bit) != (op_cfg ? bit : 0))
		ret = -EIO;
	if (ret)
		goto restore;
	if (mode_mask) {
		ret = regmap_read(map, con0, &value);
		if (!ret && (value & mode_mask) != mode_value)
			ret = -EIO;
		if (ret)
			goto restore;
	}
	pr_info("a22x-conn: PMIC LP %#x voter%d en=%u cfg=%u verified\n",
		con0, user, op_en, op_cfg);
	goto out;

restore:
	/* Restore only owned fields; never overwrite regulator enable/voltage. */
	restore_ret = regmap_update_bits(map, en_reg, bit, old_en & bit);
	if (regmap_update_bits(map, cfg_reg, bit, old_cfg & bit))
		restore_ret = -EIO;
	if (mode_mask && regmap_update_bits(map, con0, mode_mask,
					  old_mode & mode_mask))
		restore_ret = -EIO;
	pr_err("a22x-conn: PMIC LP %#x voter%d failed %d, restore=%d\n",
	       con0, user, ret, restore_ret);
out:
	mutex_unlock(&a22x_pmic_lp_lock);
	return ret;
}

int connectivity_export_pmic_ldo_vcn13_lp(int user, int op_mode,
				       unsigned char op_en, unsigned char op_cfg)
{
	return a22x_pmic_ldo_hw_lp(MT6359P_LDO_VCN13_CON0, user, op_mode,
				 op_en, op_cfg);
}

int connectivity_export_pmic_ldo_vcn18_lp(int user, int op_mode,
				       unsigned char op_en, unsigned char op_cfg)
{
	return a22x_pmic_ldo_hw_lp(MT6359P_LDO_VCN18_CON0, user, op_mode,
				 op_en, op_cfg);
}

void connectivity_export_pmic_ldo_vfe28_lp(unsigned int user, int op_mode,
					unsigned char op_en, unsigned char op_cfg)
{
	int ret = a22x_pmic_ldo_hw_lp(MT6359P_LDO_VFE28_CON0, user, op_mode,
				    op_en, op_cfg);

	if (ret)
		pr_err("a22x-conn: VFE28 hardware voter failed %d\n", ret);
}

int connectivity_export_pmic_ldo_vcn33_1_lp(int user, int op_mode,
					 unsigned char op_en, unsigned char op_cfg)
{
	return a22x_pmic_ldo_hw_lp(MT6359P_LDO_VCN33_1_CON0, user, op_mode,
				 op_en, op_cfg);
}

int connectivity_export_pmic_ldo_vcn33_2_lp(int user, int op_mode,
					 unsigned char op_en, unsigned char op_cfg)
{
	return a22x_pmic_ldo_hw_lp(MT6359P_LDO_VCN33_2_CON0, user, op_mode,
				 op_en, op_cfg);
}

void connectivity_export_clk_buf_ctrl(enum clk_buf_id id, bool onoff)
{
	pr_info("a22x-conn: clk_buf_ctrl(%d, %d) ignored\n", id, onoff);
}

void connectivity_export_clk_buf_show_status_info(void) { }
int connectivity_export_clk_buf_get_xo_en_sta(int id) { return 1; }

int connectivity_export_mmc_io_rw_direct(struct mmc_card *card, int write, unsigned int fn,
					 unsigned int addr, u8 in, u8 *out)
{
	return -ENODEV;
}

void connectivity_export_show_stack(struct task_struct *tsk, unsigned long *sp)
{
	sched_show_task(tsk ? tsk : current);
}

void connectivity_export_dump_thread_state(const char *name) { }
void connectivity_export_tracing_record_cmdline(struct task_struct *tsk) { }
void connectivity_export_conap_scp_init(unsigned int chip_info, phys_addr_t emi_phy_addr) { }
void connectivity_export_conap_scp_deinit(void) { }
void connectivity_export_dump_gpio_info(int start, int end) { }
int connectivity_export_gpio_get_tristate_input(unsigned int pin) { return -1; }

/*
 * EMI MPU: regions are programmed by TF-A (MTK_SIP_EMIMPU_CONTROL).
 * MT6833: 16 domains, addresses in 64 KiB units (vendor DT emimpu@10226000).
 */
#include <linux/arm-smccc.h>
#include <linux/slab.h>
#include <memory/mediatek/emi.h>

#define A22X_SIP_EMIMPU_CONTROL	0xC200050B
#define A22X_EMIMPU_SET		0
#define A22X_EMIMPU_CLEAR	1
#define A22X_EMIMPU_DOMAINS	16
#define A22X_EMIMPU_ALIGN	16
#define A22X_EMIMPU_REGIONS	32

int mtk_emimpu_init_region(struct emimpu_region_t *rg, unsigned int rg_num)
{
	if (!rg || rg_num >= A22X_EMIMPU_REGIONS)
		return -EINVAL;
	rg->start = 0;
	rg->end = 0;
	rg->rg_num = rg_num;
	rg->lock = false;
	rg->apc = kcalloc(A22X_EMIMPU_DOMAINS, sizeof(*rg->apc), GFP_KERNEL);
	if (!rg->apc)
		return -ENOMEM;
	for (int i = 0; i < A22X_EMIMPU_DOMAINS; i++)
		rg->apc[i] = MTK_EMIMPU_FORBIDDEN;
	return 0;
}
EXPORT_SYMBOL(mtk_emimpu_init_region);

int mtk_emimpu_set_addr(struct emimpu_region_t *rg, unsigned long long start,
			unsigned long long end)
{
	rg->start = start;
	rg->end = end;
	return 0;
}
EXPORT_SYMBOL(mtk_emimpu_set_addr);

int mtk_emimpu_set_apc(struct emimpu_region_t *rg, unsigned int d_num, unsigned int apc)
{
	if (d_num >= A22X_EMIMPU_DOMAINS || apc > MTK_EMIMPU_SEC_R_NSEC_RW)
		return -EINVAL;
	rg->apc[d_num] = apc;
	return 0;
}
EXPORT_SYMBOL(mtk_emimpu_set_apc);

int mtk_emimpu_lock_region(struct emimpu_region_t *rg, bool lock)
{
	rg->lock = lock;
	return 0;
}
EXPORT_SYMBOL(mtk_emimpu_lock_region);

int mtk_emimpu_set_protection(struct emimpu_region_t *rg)
{
	struct arm_smccc_res res;
	unsigned int start, end, group_apc;
	int i, j;

	if (!rg->apc)
		return -EINVAL;
	start = (unsigned int)(rg->start >> A22X_EMIMPU_ALIGN) | (rg->rg_num << 24);
	for (i = A22X_EMIMPU_DOMAINS / 8 - 1; i >= 0; i--) {
		end = (unsigned int)(rg->end >> A22X_EMIMPU_ALIGN) | (i << 24);
		for (group_apc = 0, j = 0; j < 8; j++)
			group_apc |= (rg->apc[i * 8 + j] & 0x7) << (3 * j);
		if (i == 0 && rg->lock)
			group_apc |= 0x80000000;
		arm_smccc_smc(A22X_SIP_EMIMPU_CONTROL, A22X_EMIMPU_SET,
			      start, end, group_apc, 0, 0, 0, &res);
		pr_info("a22x-conn: emimpu region %u [%llx-%llx] group %d apc 0x%08x -> %ld\n",
			rg->rg_num, rg->start, rg->end, i, group_apc, (long)res.a0);
	}
	return 0;
}
EXPORT_SYMBOL(mtk_emimpu_set_protection);

int mtk_emimpu_clear_protection(struct emimpu_region_t *rg)
{
	struct arm_smccc_res res;

	arm_smccc_smc(A22X_SIP_EMIMPU_CONTROL, A22X_EMIMPU_CLEAR, rg->rg_num,
		      0, 0, 0, 0, 0, &res);
	return 0;
}
EXPORT_SYMBOL(mtk_emimpu_clear_protection);

int mtk_emimpu_free_region(struct emimpu_region_t *rg)
{
	kfree(rg->apc);
	rg->apc = NULL;
	return 0;
}
EXPORT_SYMBOL(mtk_emimpu_free_region);

/* the UART STP transport is only used by external combo chips */
int mtk_wcn_stp_uart_drv_init(void) { return 0; }
int mtk_wcn_stp_uart_drv_exit(void) { return 0; }

EXPORT_SYMBOL(connectivity_export_clk_buf_ctrl);
EXPORT_SYMBOL(connectivity_export_clk_buf_get_xo_en_sta);
EXPORT_SYMBOL(connectivity_export_clk_buf_show_status_info);
EXPORT_SYMBOL(connectivity_export_conap_scp_deinit);
EXPORT_SYMBOL(connectivity_export_conap_scp_init);
EXPORT_SYMBOL(connectivity_export_dump_gpio_info);
EXPORT_SYMBOL(connectivity_export_dump_thread_state);
EXPORT_SYMBOL(connectivity_export_gpio_get_tristate_input);
EXPORT_SYMBOL(connectivity_export_mmc_io_rw_direct);
EXPORT_SYMBOL(connectivity_export_pmic_config_interface);
EXPORT_SYMBOL(connectivity_export_pmic_get_register_value);
EXPORT_SYMBOL(connectivity_export_pmic_ldo_vcn13_lp);
EXPORT_SYMBOL(connectivity_export_pmic_ldo_vcn18_lp);
EXPORT_SYMBOL(connectivity_export_pmic_ldo_vcn33_1_lp);
EXPORT_SYMBOL(connectivity_export_pmic_ldo_vcn33_2_lp);
EXPORT_SYMBOL(connectivity_export_pmic_ldo_vfe28_lp);
EXPORT_SYMBOL(connectivity_export_pmic_read_interface);
EXPORT_SYMBOL(connectivity_export_pmic_set_register_value);
EXPORT_SYMBOL(connectivity_export_show_stack);
EXPORT_SYMBOL(connectivity_export_tracing_record_cmdline);
EXPORT_SYMBOL(connectivity_export_upmu_set_reg_value);
EXPORT_SYMBOL(mtk_wcn_stp_uart_drv_exit);
EXPORT_SYMBOL(mtk_wcn_stp_uart_drv_init);
EXPORT_SYMBOL(gGpsRsvMemPhyBase);
EXPORT_SYMBOL(gGpsRsvMemSize);
