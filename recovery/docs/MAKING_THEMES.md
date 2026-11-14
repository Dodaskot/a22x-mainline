# Making your own theme

Mainline Recovery can use themes and terminal colors from a file. A theme is a small text file
ending in `.a22xtheme`. You can write one in any text editor; no tools are needed unless you want a
background picture.

Ukrainian: [MAKING_THEMES_UK.md](MAKING_THEMES_UK.md) · Every field in detail: [THEMES_API.md](../THEMES_API.md)

## 1. A theme with your colors

Save this as `deep-sea.a22xtheme`:

```json
{
  "format": 1,
  "code": "deep-sea",
  "name": "Deep Sea",
  "note": "Dark blue with a coral accent",
  "dark": true,
  "layout": "tiles",
  "colors": {
    "bg": "#0b1622",
    "panel": "#13263a",
    "text": "#eaf3fb",
    "muted": "#9fb6c9",
    "accent": "#ff7f6a",
    "on_accent": "#2a0d07"
  }
}
```

Six colors are required:

| Color | Where it is used |
|---|---|
| `bg` | The page background |
| `panel` | Cards and buttons |
| `text` | Normal text |
| `muted` | Smaller notes under the text |
| `accent` | Main buttons, the chosen option, switches |
| `on_accent` | Text on the accent color |

Everything else (borders, the large Install card, the colors of the tiles) is worked out from these.
You can still set any of them yourself; [THEMES_API.md](../THEMES_API.md) lists the names.

Other fields:

- `code`: a short name, 3 to 23 characters of `a-z`, `0-9` and `-`. It must not be the name of a
  built-in theme (`graphite`, `daylight`, `aurora`…).
- `name` and `note`: what the theme list shows.
- `dark`: `true` for a dark theme, `false` for a light one.
- `layout`: `tiles` for the large home page with tiles, `list` for a plain list.

## 2. Terminal colors

A file with only a `terminal` section adds terminal colors and no theme. The palette has 16 colors:
black, red, green, yellow, blue, magenta, cyan, white, then the same eight in their bright form.

```json
{
  "format": 1,
  "code": "my-terminal",
  "name": "My terminal",
  "terminal": {
    "background": "#101418",
    "foreground": "#d8dee9",
    "palette": ["#101418", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
                "#5c6370", "#ff7b86", "#b5e08f", "#ffd68a", "#82c4ff", "#dd9cf0", "#7fd3dd", "#ffffff"]
  }
}
```

A theme may also carry a `terminal` section; then both come from one file.

## 3. Put it on the phone

1. Copy the file to a microSD card, a USB stick, or the phone (for example with USB files from the
   recovery).
2. In the recovery, open **Settings → Theme → Add a theme from a file** (or **Settings → Terminal
   colors → Add colors from a file**) and choose the file. You can also tap the file in **Files** and
   press **Use this theme**.

The theme stays after a restart. Up to eight themes can be added. To remove one, tap the bin on its
card twice.

## 4. Readability check

The recovery checks the contrast of text against the background before it accepts a theme. A theme
that would be hard to read is refused with **THEME_UNREADABLE**, so you can never end up in front of
a screen you cannot read. If that happens, make `text` and `muted` lighter on a dark theme (or darker
on a light one) and try again.

## 5. A background picture

For a picture behind the cards you need a computer with Python and Pillow:

```
tools/pack_theme.py deep-sea.json --image picture.png --output deep-sea.a22xtheme
```

`deep-sea.json` is the same text as above. The picture is scaled to 540 × 1204. The optional `glass`
field sets how much of it shows through the cards:

```json
"glass": {"surface_alpha": 176, "edge_alpha": 84, "shadow_strength": 84}
```

`themes/beaver.json` is a complete example with a picture, and `themes/draw_beaver.py` draws it.
