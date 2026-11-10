# Hardware status (accepted v135 / kernel #95)

States describe what was verified on the test handset. Short qualification
runs are not the same as long daily-use reliability.

| Subsystem | State | Notes |
|---|---|---|
| Boot | OK | stock LK, header-v2 boot.img, 0x180000 shim, earlycon `mt6833uart` |
| Display / backlight | OK | MT6833 RDMA + C-PHY DSI, JDI HX83112F panel, DCS backlight; display domain and all panel rails switch off; 60 Hz default, 90 Hz selectable. LK display state is taken over at boot. Base DT keeps a simpledrm fallback. |
| Touch | OK | Himax HX83112F zero-flash SPI, reloads firmware after panel power cycles |
| GPU | OK | Mali-G57 MC2, Panfrost, 390–955 MHz at 750–781.25 mV, SRAM 850 mV. These are conservative per-device voltages, not the EEM-calibrated Android ones. 955 MHz is the MFGPLL limit on this chip. A separate experimental PanVK build completed Geekbench 6 Vulkan with score 989 and no reported workload validation failures; it is not Vulkan-conformant or included in the published images. See the [development report](VALIDATION-20261017.md). |
| CPU / CCI | OK | mediatek-cpufreq; little 2.0 GHz, big 2.203 GHz, CCI 1.131 GHz (stock maxima); scheduler capacities set; PSCI CPU-off and cluster-off idle |
| RAM | OK | 4 GiB physical, 3.44 GiB usable after reclaiming the size-only Android ZMC pool; 4266 MT/s; DVFSRC disabled (earlier hangs) |
| Thermal | OK | LVTS, zones little/big/gpu/soc, passive 85/95 °C, critical 117 °C; controller context restored after sleep |
| eMMC / microSD | OK | MSDC0 / MSDC1 (SDR104) |
| USB | device + host tested | MUSB + T-PHY, MT6360 TCPC and 5 V boost. NCM, read-only USB-C exFAT storage in both orientations, and return to charging/NCM verified on v142/#100. Device role re-attached after sleep (#121) and the link to a computer kept through sleep (#122). OTG across suspend not tested. |
| Audio | OK | MT8192 AFE driver on MT6833, MT6359 codec, `mt6833-mt6359` machine driver; speaker (SIA8109 via aw8738), earpiece, headphones, jack detection (accdet), built-in microphone (DCC). MT6833 memifs have no 32-bit mode. |
| Battery | OK | own MT6359 fuel gauge (current + charge counter); SoC from OCV at probe, not kept across reboots. MT6360 charger. |
| Wi-Fi | OK | vendor gen4m (`wlan_6833_axi.ko`, soc2 1x1, AXI) ported to 7.2 |
| Bluetooth | OK | own `btmtk_stp.ko` (H4 over WMT/STP), A2DP and HID tested; factory BD_ADDR restored after WMT power cycles |
| IMU | working, intermittent error observed | ICM-40607 on SPI2 (inv_icm42600 + new ID); auto-rotate tested on earlier baseline. Raw acceleration reads passed on v142, but one SPI timeout/FIFO error was logged; full orientation retest pending. |
| Magnetometer | OK | MMC5603 on I2C1 |
| Light / proximity | response tested | MN78911: cover/uncover proximity transitions and light-driven automatic backlight verified on v142. Lux scale remains an estimate; call-time screen blanking not tested. |
| Torch / vibration | OK | MT6360 FLED1 (`white:flash`), VIBR LDO at 3.3 V |
| Keys / RTC | OK | power, volume keys, MT6359 RTC |
| Sleep | OK | s2idle with CPU/cluster off, 26 MHz off >99 % of the sleep. Installed systems suspend by themselves (Plasma Mobile/PowerDevil, GNOME) and with the Power key, and wake with the Power key; display, Wi-Fi and USB networking come back (USB re-attach since #121, link kept to a computer since #122, while a computer is attached its traffic wakes the phone). Measured sleep current about 11.3 mA (build #89, USB unplugged, series of 120 s RTC-timed sleeps, phone fuel gauge charge counter); stock Android about 7.3–7.9 mA. `deep` (SYSTEM_SUSPEND) reboots; keep `mem_sleep` on `s2idle`. The recovery blanks the screen (Power key, timeout) without suspending. |
| NFC | reader/writer works | Samsung S3NRN82 on I2C3 (`-nfc-experimental` DTB, the default). Reading and writing ISO-DEP Type 4 tags verified; a PolygoCard transit card was read with its data. No card emulation, so no contactless payments or banking apps. Not yet integrated with sleep: NFC is powered down during suspend, so holding a card to the phone does not wake it. |
| Modem / SIM | FS1446 / HS2 revalidated; SIM1 unavailable | v135 only owns and parks the MD1 power domain. The experimental CCCI harness revalidated the full FS1–1446 prefix and HS2 on 2026-11-03, but the later READY stage ended with DigRF ASSERT107. RF power/clock checks, PC Monitor capture and a post-fault DHL test have not established the cause. A 39-test stock RF comparison controller is ready offline; its Android capture is pending. See the [current SIM1 report](MODEM-VALIDATION-20261103.md) and [original HS2 milestone](MODEM-VALIDATION-20261024.md). Network registration, calls and data remain unavailable; the harness is not bundled in the published image. |
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
