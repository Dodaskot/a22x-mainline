# Hardware status (accepted v135 / kernel #95)

States describe what was verified on the test handset. Short qualification
runs are not the same as long daily-use reliability.

| Subsystem | State | Notes |
|---|---|---|
| Boot | OK | stock LK, header-v2 boot.img, 0x180000 shim, earlycon `mt6833uart` |
| Display / backlight | OK | MT6833 RDMA + C-PHY DSI, JDI HX83112F panel, DCS backlight; display domain and all panel rails switch off; 60 Hz default, 90 Hz selectable. LK display state is taken over at boot. Base DT keeps a simpledrm fallback. |
| Touch | OK | Himax HX83112F zero-flash SPI, reloads firmware after panel power cycles |
| GPU | OK | Mali-G57 MC2, Panfrost, 390–955 MHz at 750–781.25 mV, SRAM 850 mV. These are conservative per-device voltages, not the EEM-calibrated Android ones. 955 MHz is the MFGPLL limit on this chip. |
| CPU / CCI | OK | mediatek-cpufreq; little 2.0 GHz, big 2.203 GHz, CCI 1.131 GHz (stock maxima); scheduler capacities set; PSCI CPU-off and cluster-off idle |
| RAM | OK | 4 GiB physical, 3.44 GiB usable after reclaiming the size-only Android ZMC pool; 4266 MT/s; DVFSRC disabled (earlier hangs) |
| Thermal | OK | LVTS, zones little/big/gpu/soc, passive 85/95 °C, critical 117 °C; controller context restored after sleep |
| eMMC / microSD | OK | MSDC0 / MSDC1 (SDR104) |
| USB | peripheral OK | MUSB + T-PHY, NCM networking, state restored across sleep. Host/OTG not done. |
| Audio | OK | MT8192 AFE driver on MT6833, MT6359 codec, `mt6833-mt6359` machine driver; speaker (SIA8109 via aw8738), earpiece, headphones, jack detection (accdet), built-in microphone (DCC). MT6833 memifs have no 32-bit mode. |
| Battery | OK | own MT6359 fuel gauge (current + charge counter); SoC from OCV at probe, not kept across reboots. MT6360 charger. |
| Wi-Fi | OK | vendor gen4m (`wlan_6833_axi.ko`, soc2 1x1, AXI) ported to 7.2 |
| Bluetooth | OK | own `btmtk_stp.ko` (H4 over WMT/STP), A2DP and HID tested; factory BD_ADDR restored after WMT power cycles |
| IMU | OK | ICM-40607 on SPI2 (inv_icm42600 + new ID), auto-rotate |
| Magnetometer | OK | MMC5603 on I2C1 |
| Light / proximity | basic | own MN78911 driver, power-on configuration, lux scale is an estimate |
| Torch / vibration | OK | MT6360 FLED1 (`white:flash`), VIBR LDO at 3.3 V |
| Keys / RTC | OK | power, volume keys, MT6359 RTC |
| Sleep | guarded | s2idle with CPU/cluster off, 26 MHz off >99 % of the sleep, display/radio recovery verified in instrumented cycles. Not yet an automatic everyday power policy. `deep` (SYSTEM_SUSPEND) reboots. |
| NFC | experimental | ISO-DEP Type 4 read/write verified with a test tag; no card emulation or payments |
| Modem / SIM | not working | MD1 power domain is owned and parked; CCCI bring-up in progress outside this baseline |
| Camera | not working | |
| GPS, fingerprint | deferred | |

## Notes for porters

- LK patches the kernel DTB with the stock `dtbo` and looks up stock nodes;
  the mainline DT therefore keeps the stock tree (disabled) in
  `mt6833-samsung-a22x-lk-compat.dtsi`, and new hardware gets separate
  `*_ml` nodes.
- Weak Wi-Fi/Bluetooth signal on the test unit came from mis-seated
  antennas after disassembly, not from the driver.
- KWin 6.7.5 leaked GPU buffers on this device until OOM; Phosh does not.
