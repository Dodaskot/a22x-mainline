#!/usr/bin/env python3
"""Print theme colors from OKLCH, a space where equal steps look equal to the eye."""
import math


def channel(value):
    value = min(1.0, max(0.0, value))
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def oklch(light, chroma, hue):
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    l_, m_, s_ = light + 0.3963377774 * a + 0.2158037573 * b, light - 0.1055613458 * a - 0.0638541728 * b, light - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    return ''.join('%02x' % round(channel(value) * 255) for value in rgb)


def luminance(color):
    values = [int(color[n:n + 2], 16) / 255 for n in (0, 2, 4)]
    r, g, b = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one, two):
    high, low = sorted((luminance(one), luminance(two)), reverse=True)
    return (high + 0.05) / (low + 0.05)


if __name__ == '__main__':
    tones = {'daylight': {
        'bg': (.972, .012, 300), 'panel': (.995, .004, 300), 'panel2': (.925, .04, 280), 'line': (.88, .03, 285), 'edge': (1, 0, 0), 'text': (.235, .035, 285),
        'muted': (.47, .035, 285), 'accent': (.5, .235, 276), 'container': (.925, .05, 276), 'on_accent': (1, 0, 0), 'icon_fill': (.92, .05, 276),
        'green': (.5, .13, 160), 'red': (.53, .2, 25), 'blue': (.5, .17, 255), 'purple': (.5, .2, 310), 'hero_top': (.56, .235, 285), 'hero_bottom': (.47, .24, 268),
        'tile_blue': (.92, .055, 255), 'ink_blue': (.5, .18, 258), 'tile_amber': (.93, .07, 80), 'ink_amber': (.56, .13, 65), 'tile_green': (.92, .065, 165),
        'ink_green': (.5, .12, 165), 'tile_violet': (.92, .06, 305), 'ink_violet': (.5, .22, 300), 'tile_teal': (.92, .055, 205), 'ink_teal': (.5, .09, 210),
        'tile_rose': (.92, .055, 15), 'ink_rose': (.54, .2, 18)},
        'aurora': {
        'bg': (.17, .035, 265), 'panel': (.28, .04, 265), 'panel2': (.36, .05, 255), 'line': (.42, .04, 260), 'edge': (.42, .05, 245), 'text': (.97, .01, 250),
        'muted': (.78, .03, 250), 'accent': (.84, .13, 185), 'container': (.4, .08, 195), 'on_accent': (.2, .05, 195), 'icon_fill': (.38, .07, 200),
        'green': (.84, .13, 165), 'red': (.76, .14, 20), 'blue': (.8, .1, 250), 'purple': (.8, .12, 305), 'hero_top': (.55, .12, 190), 'hero_bottom': (.42, .17, 275),
        'tile_blue': (.38, .08, 255), 'ink_blue': (.85, .09, 255), 'tile_amber': (.4, .07, 80), 'ink_amber': (.87, .11, 85), 'tile_green': (.38, .07, 165),
        'ink_green': (.86, .12, 165), 'tile_violet': (.38, .09, 305), 'ink_violet': (.84, .11, 305), 'tile_teal': (.38, .06, 205), 'ink_teal': (.86, .1, 200),
        'tile_rose': (.4, .09, 15), 'ink_rose': (.84, .11, 15)}}
    for theme, colors in tones.items():
        print(theme)
        values = {name: oklch(*tone) for name, tone in colors.items()}
        print('  ' + ' '.join(f'{name}={value}' for name, value in values.items()))
        print('  contrast text/panel %.1f muted/panel %.1f accent/panel %.1f on_accent/accent %.1f text/bg %.1f muted/bg %.1f' % (
            contrast(values['text'], values['panel']), contrast(values['muted'], values['panel']), contrast(values['accent'], values['panel']),
            contrast(values['on_accent'], values['accent']), contrast(values['text'], values['bg']), contrast(values['muted'], values['bg'])))
