# a22x changes to the vendor connectivity sources

Base: Samsung SM-A226B GPL release A226BXXSBDYA1 (kernel 4.14). Oldest first.

- kbuild glue
- btif: builds on 7.2
- wmt_drv + btif build on 7.2 (compat layer, mainline adapter, EMI MPU via SMC)
- wmt: CONN power domain via runtime PM, EMI from KO
- tools: python wmt_loader/wmt_launcher replacements; WMT powers on (LPBK)
- wlan gen4m (soc2_1x1, axi) + wmt_chrdev_wifi build on 7.2: cfg80211 adapters, plat_priv for mainline
- wlan: EMI firmware sections, eth_hw_addr_set
- tools: systemd units for boot-time bring-up
- bt: HCI driver over WMT/STP (btmtk_stp)
- bt: answer Read Sync Train Params locally
- bt: fix fake cmd build
- conn-start: load btmtk_stp
- bt: raw dump param
- wlan: quieter default log level
- conn-start: idempotent
- wmt: demote per-second connlog/STP-PSM/EIRQ messages to pr_debug
- connlog: drop ratelimit wrapper around the debug cache-full message
- wmt: retain MT6833 firmware power while runtime reference is held
- wmt: use explicit firmware power state for suspend retention
- wlan: arm AXI wake IRQ only for active WoWLAN suspend
- wlan: preserve existing AXI HIF member offsets
- wlan: add bounded opt-in AXI firmware ownership handoff for suspend
- wlan: request PM ownership work without a wakeup-source event
- Bluetooth: btmtk_stp: configure MT6631 controller sleep on each power-on
- Bluetooth: btmtk_stp: restore factory address after WMT power cycles
- wifi: axi: drain active scan before opt-in suspend firmware own
- a22x: correct MT6359P connectivity LP fields and hardware voter ABI
- a22x: implement opt-in MT6359P CONN hardware low-power voters
- a22x: couple software CONN LP entry to hardware voter policy
- wifi: axi: keep the HIF IRQ as a PM wake source during the suspend_fw_own handshake (`suspend_hif_wake`, default on)
- Bluetooth: btmtk_stp: leave the skb to hci_send_frame() on send errors (double free); fail the faked Read Sync Train Params on allocation failure (after v135, not in conn95)
- conn-start: fail on any failed step; report a partial bring-up instead of exiting 0 (after v135)
