# Phone details

`sysinfo.py` prints what this phone is, as JSON, for Settings → About phone and the model in the home page.
Everything comes from the running phone: the kernel command line of the bootloader, the device tree,
`/proc` and `/sys`. Nothing describes one unit, so the same recovery reports another A22x correctly,
and a value the phone does not expose is left out.

```
{"ok":true,"name":"Galaxy A22 5G","model":"SM-A226B","codename":"a22x","version":"0.6.31",
 "summary":[["Processor","2.2","GHz"],...],
 "sections":[{"title":"Device","rows":[["Model","SM-A226B",""],...]},...]}
```

A row is label, value and unit. The interface translates the label and the unit.
The serial number is shown on the phone only. The interface keeps it out of its logs.
IMEI is not here: it needs the modem, which recovery does not start.
`--root DIR` reads another tree laid out like `/`, for tests.
