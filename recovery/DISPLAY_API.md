# Recovery display service: refresh, brightness and blank/wake

`display.py serve` owns the existing persistent MediaTek DRM descriptor and the
native A22x panel's backlight attributes. Clients use the private root-only UNIX
socket; they never open DRM or acquire the storage-operation lease. Backlight
control therefore remains usable during backup, MTP or an administrator terminal.
The UI owns KEY_POWER routing; this backend does not open input devices.

## Commands and response contract

```text
python3 /opt/a22x-recovery/display.py status
python3 /opt/a22x-recovery/display.py set --hz 60
python3 /opt/a22x-recovery/display.py set --hz 90
python3 /opt/a22x-recovery/display.py brightness --percent 40
python3 /opt/a22x-recovery/display.py blank
python3 /opt/a22x-recovery/display.py wake
python3 /opt/a22x-recovery/display.py toggle
```

The existing `status`/`set --hz` API and fields remain valid. Each successful
response contains `ok`, `operation`, `boot_id`, the previous refresh fields
(`supported`, `available_hz`, `current_hz`, `actual_hz`, `service_running`,
`reason`) and these additional fields:

| Field | Meaning |
| --- | --- |
| `brightness_supported` | The exact native panel's attributes were admitted and can currently be read. |
| `blank_supported` | The admitted backlight also has a live crash guard. |
| `brightness_percent` | Rounded observed raw value as 0–100 percent; 0 is reserved for blanking. |
| `brightness_raw` | Same value as `brightness_actual_raw`. |
| `brightness_requested_raw` | Fresh read of sysfs `brightness`. |
| `brightness_actual_raw` | Fresh read of sysfs `actual_brightness`. |
| `brightness_max` | 255 for the qualified eight-bit HX83112F driver. |
| `blanked` | Whether observed raw brightness is zero; null if unavailable. |
| `last_nonzero_percent` | The nonzero brightness to restore on wake. |
| `backlight_reason` | Null normally; a read/guard failure reason otherwise. |
| `backlight_readback` | `kernel-cached` when available. |

Unavailable backlight fields are null and both capability booleans are false.
Refresh capability and brightness capability are independent: an unavailable
refresh-mode controller does not automatically disable an admitted backlight.

`brightness` accepts only integers 1 through 100. No implicit clamping, strings,
booleans or zero are accepted by the IPC backend. Conversion is
`max(1, (255 * percent + 50) // 100)`. Setting brightness writes a positive value
and wakes the screen. The successful response and subsequent status report
observed values, not merely the submitted percentage.

`blank` writes raw zero after the restore value has been acknowledged by the
crash guard and saved in RAM. `wake` restores the last verified positive value;
when already lit it preserves current positive brightness. Both operations are
idempotent. `toggle` makes one decision from a fresh read and is **not replayed**
after a timeout or uncertain response. The UI should issue status only to resolve
that uncertainty. Successful mutations include `changed` and complete refresh
and backlight status. Failed writes include `observed` state and whether a visible
restore was confirmed in `error.details`.

Blanking changes only backlight brightness. It does not suspend the system,
power-cycle the panel, change DRM mode, tear down the framebuffer, or alter
storage mounts. The screen-off state therefore does not imply system suspend or
the same power consumption as Android sleep.

## Verified kernel interface and admission

The k107 native DTS declares `jdi,hx83112f-a22x`; its legacy
`mediatek,dsi-vm-backlight` node is disabled. The compiled configuration enables
`CONFIG_DRM_PANEL_HIMAX_HX83112F` and `CONFIG_BACKLIGHT_CLASS_DEVICE`.
`drivers/gpu/drm/panel/panel-himax-hx83112f.c` registers the raw backlight before
the DRM panel/DSI attachment, with maximum 255 and initial value 200. Its driver
name is `panel-jdi-hx83112f`. The update callback sends eight-bit DCS command
0x51 when the native panel is enabled.

That driver has no hardware `get_brightness` callback. Consequently Linux's
`actual_brightness` is the cached kernel value. Both attributes are read after a
write, and disagreement fails the operation, but this is not a light-sensor or
panel-register measurement. Physical blanking/brightness acceptance is a separate
root-operated test.

Discovery is bounded to 16 class entries and requires exactly one matching native
panel. Its canonical class target must reside under `/sys/devices` on the same
identified sysfs mount. The compatible string, driver, raw type and exact maximum
are checked. The four attribute FDs are root-owned, regular, single-link and
protected from non-root writes; symlink attribute replacement is rejected.
Canonical path, mount, driver and attribute inode identities are rechecked before
every operation. Integer reads are bounded. `bl_power` must already be zero;
this service does not change panel power state. There is no caller-supplied path,
raw I²C/register fallback, alternate device selector or storage mount operation.

All existing dedicated recovery, RAM-root, little-endian ABI, exact boot receipt,
root peer credentials, private socket, singleton and PID/start-time checks remain.
IPC retains its 1024-byte request, 8192-byte response and two-second deadlines.
Unknown fields, duplicate JSON keys and multiple requests on one connection are
rejected. Schema remains `a22x.display.request.v1` with additive operations.

## RAM state and upgrade handoff

The sole brightness/refresh preference file is:

```text
/run/a22x-recovery/display-backlight.json
```

It is a root-owned, single-link regular file, mode 0600, inside the private RAM
runtime. No userdata, SD or other persistent storage write is performed. The
previous f12 source is archived under
`recovery-transfer/display-v05-backlight/original/`.

The final upgrade controller may create this exact seed **after the old display
service has exited and before PID1 restarts the new service**:

```json
{
  "schema": "a22x.display.backlight.v1",
  "boot_id": "CURRENT_VERIFIED_BOOT_ID",
  "device": "EXACT_CANONICAL_SYSFS_BACKLIGHT_DIRECTORY",
  "maximum": 255,
  "last_nonzero_raw": 200,
  "preferred_hz": 90,
  "guard_pid": null,
  "guard_start": null
}
```

The placeholder identity strings and example brightness must be replaced with
the actual read-only capture. The same boot ID, canonical panel identity and
maximum are mandatory. `last_nonzero_raw` must be 1–255; `preferred_hz` is 60, 90
or null. A null guard pair is explicitly admitted for this seed. Current positive
brightness takes precedence over the seed's restore value; zero is restored from
the seed. The service then installs its own guard and atomically replaces the
record with actual guard PID/start metadata.

The saved refresh preference is applied through the existing verified DRM mode
change **before socket readiness and the startup receipt**. A successful later
`set --hz` updates the same RAM preference. Normal shutdown restores visible
brightness and retains preferences. It never writes a zero restore preference.
New recovery boots ignore old-boot preferences; missing valid restore data uses
the safe positive 50% fallback if startup finds zero brightness.

## Crash recovery

A private child uses a pidfd to observe its exact service parent and a bounded
socketpair for restore-value acknowledgements. It has no public listener or
command execution path. Before accepting work it closes **every inherited FD**
except the private channel, parent pidfd and the four pinned backlight attributes:
no DRM, display singleton, public socket, storage lease, stdin/stdout/stderr or
unrelated file remains open.

The guard acknowledges a valid last-positive value before the service may write
zero. Parent death, channel closure and private-protocol errors all cause a
restore attempt if the backlight is zero. Graceful shutdown restores visibility
too. A new service waits up to two seconds for an exactly pinned previous guard
to finish before taking over. If the guard dies while the service lives, the
service wakes the backlight on its next loop and disables further blanking.
Brightness changes and wake remain available. No recovery scheme can promise a
successful physical restore if the kernel backlight operation itself fails; such
a failure is reported and never represented as verified visibility.

## Validation scope

Owned regular-file fixtures exercise real zero/positive writes, last-positive RAM
state, repeated blank/wake, percent bounds, malformed requests, readback failure,
path replacement, the upgrade seed and preservation of the DRM mode. The tests
assert that no storage lock is requested and no files outside the owned fixture
are created. Real Arch fork/pidfd tests exercise service SIGKILL, descriptor
closure, independent storage-lock release, graceful closure and malformed private
messages. These tests never open a phone, USB endpoint or physical partition.

Root must still verify the actual class path and driver, perform a bounded
brightness→blank→wake cycle with a finally-wake cleanup, confirm 60/90 Hz and the
framebuffer stay unchanged, then check short Power and the UI slider. Physical
acceptance is required before reporting this feature complete to the user.
