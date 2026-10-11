#!/usr/bin/env python3
"""Generate a mainline drivers/clk/mediatek/clk-mt6833.c (topckgen,
infracfg_ao, pericfg) from the Samsung SM-A226B downstream clk-mt6833.c.

usage: gen-clk-mt6833.py downstream/clk-mt6833.c out/clk-mt6833.c
"""
import re
import sys

src = open(sys.argv[1]).read()


def strip_comments(s):
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


def resolve_ccf_ifs(s):
    """Keep the #else branch of '#if MT_CCF_MUX_DISABLE' (what Samsung ships)."""
    out, keep = [], [True]
    for line in s.splitlines():
        t = line.strip()
        if t.startswith("#if MT_CCF_MUX_DISABLE"):
            keep.append(False)
            continue
        if t.startswith("#else") and len(keep) > 1:
            keep[-1] = True
            continue
        if t.startswith("#endif") and len(keep) > 1 and "MT_CCF" in t:
            keep.pop()
            continue
        if all(keep):
            out.append(line)
    return "\n".join(out)


def block(start_re):
    m = re.search(start_re, src)
    assert m, start_re
    end = src.index("\n};", m.start()) + 3
    return src[m.start():end]


def split_args(s):
    args, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return args


def calls(body, name):
    """Yield argument lists of NAME(...) invocations in body."""
    for m in re.finditer(r"\b%s\(" % name, body):
        i, depth = m.end(), 1
        while depth:
            depth += {"(": 1, ")": -1}.get(body[i], 0)
            i += 1
        yield split_args(body[m.end():i - 1])


# --- parents, fixed clocks, factors -----------------------------------------
parents = re.findall(r"static const char \* const \w+_parents\[\] = \{.*?\n\};",
                     src, flags=re.S)
fixed = strip_comments(block(r"static const struct mtk_fixed_clk top_fixed_clks\[\]"))
divs = strip_comments(block(r"static const struct mtk_fixed_factor top_divs\[\]"))


def lock_shared_plls(m):
    """Only dedicated PLLs may be retuned through their dividers: a rate
    request on any mux must never reprogram mainpll/univpll/... (shared by
    the whole SoC). mfgpll (GPU DVFS) and the audio PLLs keep propagating."""
    cid, name, ws, parent, ratio = m.groups()
    if parent in ("mfgpll", "apll1", "apll2"):
        return m.group(0)
    return f'FACTOR_FLAGS({cid}, "{name}",{ws}"{parent}", {ratio}, 0)'


divs = re.sub(r'FACTOR\((\w+), "(\w+)",(\s*)"(\w+)", (\d+, \d+)\)',
              lock_shared_plls, divs)

# --- muxes --------------------------------------------------------------------
mux_body = strip_comments(resolve_ccf_ifs(block(r"static const struct mtk_mux top_muxes\[\]")))
mux_lines = []
for a in calls(mux_body, "MUX_CLR_SET_UPD"):
    (cid, name, par, ofs, set_ofs, clr_ofs, shift, width, pdn, upd_ofs, upd) = a
    if pdn in ("INV_BIT", "-1"):
        mux_lines.append(
            f"\tMUX_CLR_SET_UPD({cid}, {name}, {par}, {ofs}, {set_ofs}, {clr_ofs},\n"
            f"\t\t\t{shift}, {width}, {upd_ofs}, {upd}),")
    else:
        mux_lines.append(
            f"\tMUX_GATE_CLR_SET_UPD({cid}, {name}, {par}, {ofs}, {set_ofs}, {clr_ofs},\n"
            f"\t\t\t{shift}, {width}, {pdn}, {upd_ofs}, {upd}),")

# --- composites ---------------------------------------------------------------
comp_body = strip_comments(resolve_ccf_ifs(block(r"static const struct mtk_composite top_composites\[\]")))
comp_lines = []
for m in re.finditer(r"\b(MUX|DIV_GATE)\(", comp_body):
    i, depth = m.end(), 1
    while depth:
        depth += {"(": 1, ")": -1}.get(comp_body[i], 0)
        i += 1
    a = split_args(comp_body[m.end():i - 1])
    comp_lines.append(f"\t{m.group(1)}({', '.join(a)}),")

# --- gates --------------------------------------------------------------------
regs = re.findall(r"static const struct mtk_gate_regs (?:ifrao\d|peri)_cg_regs = \{.*?\n\};",
                  src, flags=re.S)
gate_macros = []
for m in re.finditer(r"#define (GATE_(?:IFRAO\d|PERI))\(_id, _name, _parent, _shift\) \{(.*?)\n\t\}",
                     src, flags=re.S):
    body = m.group(2)
    r = re.search(r"\.regs = &(\w+)", body).group(1)
    ops = re.search(r"\.ops = &(\w+)", body).group(1)
    flags = re.search(r"\.flags = ([^,\n]+)", body)
    if flags:
        gate_macros.append(
            f"#define {m.group(1)}(_id, _name, _parent, _shift)\t\\\n"
            f"\tGATE_MTK_FLAGS(_id, _name, _parent, &{r}, _shift,\t\\\n"
            f"\t\t       &{ops}, {flags.group(1).strip()})\n")
    else:
        gate_macros.append(
            f"#define {m.group(1)}(_id, _name, _parent, _shift)\t\\\n"
            f"\tGATE_MTK(_id, _name, _parent, &{r}, _shift, &{ops})\n")

ifrao = strip_comments(block(r"static const struct mtk_gate ifrao_clks\[\]"))
peri = strip_comments(block(r"static const struct mtk_gate peri_clks\[\]"))
if "GATE_PERI(" not in peri:
    peri = None


def tidy(s):
    return re.sub(r"\n\s*\n", "\n", s)


out = []
out.append("""// SPDX-License-Identifier: GPL-2.0-only
/*
 * MediaTek MT6833 (Dimensity 700) topckgen, infracfg_ao and pericfg clocks.
 *
 * Generated by tools/gen-clk-mt6833.py from the Samsung SM-A226B (MT6833)
 * kernel source tables, using the mainline MediaTek clock helpers.
 */

#include <dt-bindings/clock/mt6833-clk.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>

#include "clk-gate.h"
#include "clk-mtk.h"
#include "clk-mux.h"

static DEFINE_SPINLOCK(mt6833_clk_lock);

/* No update register / gate bit (mainline skips negative shifts). */
#define INV_OFS	-1
#define INV_BIT	-1
""")
# register offset defines used by the tables
defines = re.findall(r"^#define (CLK_CFG_\w+|CLK_AUDDIV_\d+|TOP_MUX_\w+_SHIFT)\s+(\S+)", src, flags=re.M)
for k, v in defines:
    out.append(f"#define {k}\t{v}")
if "CLK_AUDDIV_2" not in dict(defines):
    out.append("#define CLK_AUDDIV_2\t0x328")
out.append("")
out.append(tidy(fixed))
out.append("")
out.append(tidy(divs))
out.append("")
out.extend(parents)
out.append("")
out.append("static const struct mtk_mux top_mtk_muxes[] = {")
out.extend(mux_lines)
out.append("};\n")
out.append("static const struct mtk_composite top_composites[] = {")
out.extend(comp_lines)
out.append("};\n")
out.extend(r + "\n" for r in regs)
out.extend(gate_macros)
out.append("")
out.append(tidy(ifrao))
out.append("")
if peri:
    out.append(tidy(peri))
    out.append("")

out.append("""/*
 * The GPU mux must not feed the GPU from mfgpll while that PLL is being
 * reprogrammed (GPU DVFS): park it on mfg_ref_sel around the rate change.
 */
static int clk_mt6833_reg_mfg_mux_notifier(struct device *dev, struct clk *clk)
{
	struct mtk_mux_nb *mfg_mux_nb;
	int i;

	mfg_mux_nb = devm_kzalloc(dev, sizeof(*mfg_mux_nb), GFP_KERNEL);
	if (!mfg_mux_nb)
		return -ENOMEM;

	for (i = 0; i < ARRAY_SIZE(top_mtk_muxes); i++)
		if (top_mtk_muxes[i].id == CLK_TOP_MFG_PLL_SEL)
			break;
	if (i == ARRAY_SIZE(top_mtk_muxes))
		return -EINVAL;

	mfg_mux_nb->ops = top_mtk_muxes[i].ops;
	mfg_mux_nb->bypass_index = 0; /* mfg_ref_sel */

	return devm_mtk_clk_mux_notifier_register(dev, clk, mfg_mux_nb);
}
""")
out.append("""static const struct mtk_clk_desc topck_desc = {
	.fixed_clks = top_fixed_clks,
	.num_fixed_clks = ARRAY_SIZE(top_fixed_clks),
	.factor_clks = top_divs,
	.num_factor_clks = ARRAY_SIZE(top_divs),
	.mux_clks = top_mtk_muxes,
	.num_mux_clks = ARRAY_SIZE(top_mtk_muxes),
	.composite_clks = top_composites,
	.num_composite_clks = ARRAY_SIZE(top_composites),
	.clk_lock = &mt6833_clk_lock,
	.clk_notifier_func = clk_mt6833_reg_mfg_mux_notifier,
	.mfg_clk_idx = CLK_TOP_MFG_PLL_SEL,
};

static const struct mtk_clk_desc ifrao_desc = {
	.clks = ifrao_clks,
	.num_clks = ARRAY_SIZE(ifrao_clks),
};
""")
if peri:
    out.append("""static const struct mtk_clk_desc peri_desc = {
	.clks = peri_clks,
	.num_clks = ARRAY_SIZE(peri_clks),
};
""")
out.append("""static const struct of_device_id of_match_clk_mt6833[] = {
	{ .compatible = "mediatek,mt6833-topckgen", .data = &topck_desc },
	{ .compatible = "mediatek,mt6833-infracfg_ao", .data = &ifrao_desc },""")
if peri:
    out.append("""	{ .compatible = "mediatek,mt6833-pericfg", .data = &peri_desc },""")
out.append("""	{ /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, of_match_clk_mt6833);

static struct platform_driver clk_mt6833_drv = {
	.probe = mtk_clk_simple_probe,
	.remove = mtk_clk_simple_remove,
	.driver = {
		.name = "clk-mt6833",
		.of_match_table = of_match_clk_mt6833,
	},
};
module_platform_driver(clk_mt6833_drv);

MODULE_DESCRIPTION("MediaTek MT6833 clocks driver");
MODULE_LICENSE("GPL");
""")
open(sys.argv[2], "w").write("\n".join(out))
print(f"parents={len(parents)} muxes={len(mux_lines)} composites={len(comp_lines)} "
      f"gate_macros={len(gate_macros)} ifrao_gates={ifrao.count('GATE_IFRAO')} "
      f"peri={'yes' if peri else 'empty'}")
