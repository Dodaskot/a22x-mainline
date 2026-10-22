# A22X connectivity configuration

These plain-text board settings reproduce the values read from the currently
working A22X installation on 2026-10-22. They can be installed under
`/usr/lib/firmware/` as `WMT_SOC.cfg` and `wifi.cfg`, or included in an initramfs
there. Users no longer need to extract these two text files from Android.

These files are board settings, not replacement MCU, Bluetooth or Wi-Fi
firmware. The binary firmware requirements remain unchanged. The files contain
no device addresses, NVRAM payloads or per-device factory calibration. Their
formatting is supplied under GPL-2.0-only; adjacent `.license` files carry the
SPDX identifiers without adding comment lines the WMT parser would warn about.

## Host-driver support

All nine keys in `WMT_SOC.cfg` are explicitly registered in `wmtcfg_fields` in
[`wmt_conf.c`](../common/common_main/core/wmt_conf.c). Its character/short parsers
accept decimal values. The settings select antenna/coexistence, clock, GPS-LNA
and Bluetooth TSSI options already used on this board.

`StatsLlsEn` and `ScanNoApRecover` are explicitly read by
[`wlan_lib.c`](../wlan/core/gen4m/common/wlan_lib.c). The latter remains `0` for
this port: the existing configuration disables false scan recovery/SER when
BandP is zero. It is not a general recommendation for other devices.

`EdccaTh*` and `WmmParam*` are preserved firmware-consumed options. Their literal
names are not implemented in the GPL host driver. The host's `wlanCfgParse`
accepts configuration entries and `wlanFeatureToFw` forwards their names and
values through `CMD_ID_GET_SET_CUSTOMER_CFG`. The firmware's interpretation
remains closed; publishing these settings does not establish an open
implementation of EDCCA or WMM.

This change documents and packages current settings. It does not claim a new
hardware validation run or suitability for other panel/board variants.
