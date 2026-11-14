# Settings helper

`settings.py` keeps preferences and the optional password in the `misc` partition, and
the saved Wi-Fi networks beside them (since 2026-11-14, `misc_store.py`). Each is one record
kept twice, in its own 64 KiB of the last 128 KiB of `misc` (settings at 384 KiB, Wi-Fi at
448 KiB): each copy has a generation and a SHA-256, a read takes the newest whole copy, and a
write replaces both, one after the other, so a save that stops halfway leaves a whole copy of
the state before or after it, and one damaged copy leaves the other, which is the same. No
filesystem is involved: no mount, no journal, no older copy on another volume. Android uses
only the first 64 KiB of `misc` (the bootloader message, the system space), the LK only its
bootloader message, MediaTek's sysenv the `para` partition; bytes in the record's place that
are not this recovery's are never written over. `/run/a22x-recovery/settings.json` avoids
reading `misc` for every question; a save `misc` cannot take now stays in RAM (exit 3) and
the interface tries again after 10 s, then each time twice as late, 5 min at most.

Releases up to 1.0.1 kept the settings in `a22x-recovery/settings.json` on `cache` (or on
`userdata`), with a password mark beside them. While `misc` holds no record yet, that file is
read as those releases read it and copied to `misc`, which decides from then on; the file and
its password marks are then removed, so erasing `misc` gives the defaults, never the older file. While that older file cannot be read (a userdata it
recognizes that will not mount), the recovery stays locked (exit 2), as those releases did:
a password may be there. The themes stay on `cache`, with the backups.

`misc` that cannot be read is "cannot be read now" (exit 2), never "no settings"; one copy
that cannot be read leaves the other. Both copies damaged, or bytes that are no copy of this
recovery's where its record was, are damaged settings (exit 4), never "no settings": the phone
stays locked until they are reset together with userdata (userdata first, then both copies and
the older file on cache). The boot splash reads its theme from `misc` too. Erasing `misc` itself in Download Mode gives the defaults: that takes
Download Mode, where any recovery can be flashed, which no password here covers.

```
settings.py status
settings.py set [--language uk] [--theme CODE] [--terminal CODE] [--storage sd|usb]
                [--vibration 0-3] [--timeout 0-5] [--utc-offset MINUTES]
                [--refresh 0|60|90] [--brightness 0-100]
                [--animation zoom|blur|slide|fade|dissolve|off]
                [--lock 0-2] [--access 0|1] [--navigation 0-2] [--torch 0-255]
                [--android-log ID]
settings.py verify --secret-file FILE
settings.py set-password --secret-file FILE [--old-secret-file FILE]
settings.py clear-password [--secret-file FILE]
```

Exit codes: 0 done or yes, 1 no, 2 error, 3 kept in RAM until reboot.

`navigation` is 0 for gestures, 1 for buttons and 2 for neither.
`torch` is the level of the flashlight chosen last, in the steps of the phone's LED; 0 means none was
chosen, and the interface then starts in the middle. A level the LED does not have is ignored.

`android_log` is the id of the failed start of Android the owner has read about (16 hex digits from
`android_log.py read`, or empty): the page that says why Android did not start opens once for each
failed start.

`status` also says `password` and `password_digits`. The second one picks the page a password is asked
on: the digit pad, or the full keyboard when the password has anything else in it.
A disk that cannot be read is an error, not an empty disk: a save after such a read would lose the
password. While another operation holds the lock, the first read waits up to three seconds.
The first read also copies imported themes to RAM; see `THEMES_API.md`.

`refresh` and `brightness` use 0 for "leave the panel as it starts". The UI applies saved
values after startup, behind whatever is on screen, and never opens a page for it.
`storage` remembers the choice only: a USB stick opens for writing from a tap on its card,
because any stick may be the one inserted now.

With a password set, `set-password` and `clear-password` need the current one and return 1
without it. `lock` is 0 never, 1 when recovery starts, 2 also when the screen turns off.
`access` 1 makes `access_gate.py` ask on the phone before SSH, SFTP or the UART console opens.

The password stores a salted PBKDF2 hash. It guards the screen, SSH, SFTP and the UART
shell. It does not guard Download mode, a removed SD card, or the partition that holds the
file: formatting `cache` removes it.
