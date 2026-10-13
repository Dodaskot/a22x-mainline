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
| USB | device + host tested | MUSB + T-PHY, MT6360 TCPC and 5 V boost. NCM, read-only USB-C exFAT storage in both orientations, and return to charging/NCM verified on v142/#100. OTG across suspend not tested. |
| Audio | OK | MT8192 AFE driver on MT6833, MT6359 codec, `mt6833-mt6359` machine driver; speaker (SIA8109 via aw8738), earpiece, headphones, jack detection (accdet), built-in microphone (DCC). MT6833 memifs have no 32-bit mode. |
| Battery | OK | own MT6359 fuel gauge (current + charge counter); SoC from OCV at probe, not kept across reboots. MT6360 charger. |
| Wi-Fi | OK | vendor gen4m (`wlan_6833_axi.ko`, soc2 1x1, AXI) ported to 7.2 |
| Bluetooth | OK | own `btmtk_stp.ko` (H4 over WMT/STP), A2DP and HID tested; factory BD_ADDR restored after WMT power cycles |
| IMU | working, intermittent error observed | ICM-40607 on SPI2 (inv_icm42600 + new ID); auto-rotate tested on earlier baseline. Raw acceleration reads passed on v142, but one SPI timeout/FIFO error was logged; full orientation retest pending. |
| Magnetometer | OK | MMC5603 on I2C1 |
| Light / proximity | response tested | MN78911: cover/uncover proximity transitions and light-driven automatic backlight verified on v142. Lux scale remains an estimate; call-time screen blanking not tested. |
| Torch / vibration | OK | MT6360 FLED1 (`white:flash`), VIBR LDO at 3.3 V |
| Keys / RTC | OK | power, volume keys, MT6359 RTC |
| Sleep | works in tests, not automatic yet | s2idle with CPU/cluster off, 26 MHz off >99 % of the sleep, display/radio recovery verified in instrumented cycles. Measured sleep current about 11.3 mA (build #89, USB unplugged, series of 120 s RTC-timed sleeps, phone fuel gauge charge counter); stock Android about 7.3–7.9 mA. The development setup still keeps a sleep inhibitor and test gates: automatic suspend with safe quiesce/recovery of all devices is not enabled yet. `deep` (SYSTEM_SUSPEND) reboots. |
| NFC | reader/writer works | Samsung S3NRN82 on I2C3 (`-nfc-experimental` DTB, the default). Reading and writing ISO-DEP Type 4 tags verified; a PolygoCard transit card was read with its data. No card emulation, so no contactless payments or banking apps. Not yet integrated with sleep: NFC is powered down during suspend, so holding a card to the phone does not wake it. |
| Modem / SIM | in progress | v135 only owns and parks the MD1 power domain. Experimental CCCI work outside this baseline powers and releases the modem: the first handshake (HS1) passes, and the modem's early RPC and NVRAM file requests are answered one by one (28 RPC + 130 file requests verified on 2026-10-13). It does not reach the second handshake (HS2, boot complete) yet; SIM, network, calls and data are not working. |
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
