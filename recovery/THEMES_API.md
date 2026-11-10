# Themes from a file

A theme is one `.a22xtheme` file. It can come from any volume the file manager sees. Choose it in
Settings → Theme → Add a theme from a file, in Settings → Terminal colors → Add colors from a file, or
tap it in Files and press Use this theme.

The file is JSON. `themes.py` checks it and compiles it into `a22x-recovery/themes/<code>.theme` on the
volume that keeps the settings, with a copy in RAM. The interface reads only the compiled form.

```json
{
  "format": 1,
  "code": "my-theme",
  "name": "My theme",
  "note": "Shown under the name",
  "dark": true,
  "layout": "tiles",
  "glass": {"surface_alpha": 176, "edge_alpha": 84, "shadow_strength": 84},
  "colors": {"bg": "#1b120c", "panel": "#2d1f15", "text": "#f8f0e5", "muted": "#d2bea8", "accent": "#f2a53c", "on_accent": "#2a1603"},
  "terminal": {"background": "#17100b", "foreground": "#eee2d2", "palette": ["16 colors, black to bright white"]},
  "image": {"width": 540, "height": 1204, "data": "base64 of zlib of RGB rows"}
}
```

| Field | Meaning |
|---|---|
| `format` | Always 1. |
| `code` | 3 to 23 of `a-z`, `0-9`, `-`. Without it the file name is used. Names of built-in themes are refused. |
| `name`, `note` | Plain text, under 48 and 80 bytes. |
| `dark` | Picks the default status colors. Without it the background decides. |
| `layout` | `tiles` for the large home page, `list` for the plain one. |
| `glass` | How much of the picture shows through the cards. `surface_alpha` is at least 96. |
| `colors` | `bg`, `panel`, `text`, `muted`, `accent` and `on_accent` are required. Any other name from `COLORS` in `themes.py`, `tile_fill`, `tile_ink` (six colors each), `terminal_bg` and `terminal_fg` may be added; the rest is derived. |
| `terminal` | Colors for the terminal. A file with only this section adds terminal colors and no theme. |
| `image` | The background, at most 540 x 1204. It is stretched to the panel. |

A theme that nobody could read would stay after every reboot, so contrast is checked and such a file
is refused with `THEME_UNREADABLE`. Eight themes can be imported.

Every theme from a file has a bin on its card, in the theme list and in the terminal list: the first
tap asks, the second removes. A theme in use gives way to the first built-in one. A theme that comes
with the image is only hidden, by a `<code>.hidden` note beside the imported themes; Return removed
themes brings it back, and so does importing a file with the same code.

To put a picture into a theme, on a computer with Pillow:

```
tools/pack_theme.py theme.json --image picture.png --output my.a22xtheme
```

`themes/beaver.json` is a complete example. `themes/draw_beaver.py` draws its picture. The compiled
Beaver theme comes with the image, in `/opt/a22x-recovery/themes`.
