#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Draw the picture of the Beaver theme: dusk over a river, a beaver gnawing a twig on a log.

The page is 720 x 1600 in interface units. The text of the interface sits on the top
two thirds, so they stay plain; the beaver sits in the strip the home page leaves free.
"""
import math
from pathlib import Path
import random
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 3
W, H = 720 * S, 1600 * S
SCALE = .82
rng = random.Random(22)


def rgb(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def mix(one, two, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(one, two))


def gradient(stops):
    column = Image.new('RGB', (1, H))
    for y in range(H):
        t = y / (H - 1)
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= t <= t1:
                column.putpixel((0, y), mix(rgb(c0), rgb(c1), (t - t0) / (t1 - t0)))
                break
    return column.resize((W, H))


def layer():
    return Image.new('RGBA', (W, H), (0, 0, 0, 0))


def ellipse(draw, cx, cy, rx, ry, fill, angle=0):
    if not angle:
        draw.ellipse([(cx - rx) * S, (cy - ry) * S, (cx + rx) * S, (cy + ry) * S], fill=fill)
        return
    a = math.radians(angle)
    points = [((cx + rx * math.cos(t) * math.cos(a) - ry * math.sin(t) * math.sin(a)) * S,
               (cy + rx * math.cos(t) * math.sin(a) + ry * math.sin(t) * math.cos(a)) * S)
              for t in (i * math.tau / 96 for i in range(96))]
    draw.polygon(points, fill=fill)


def polygon(draw, points, fill):
    draw.polygon([(x * S, y * S) for x, y in points], fill=fill)


def pine(draw, x, base, height, color):
    width = height * .24
    for level in range(5):
        top = base - height + level * height * .17
        bottom = top + height * .34
        half = width * (.32 + level * .17)
        polygon(draw, [(x, top), (x + half, bottom), (x - half, bottom)], color)
    draw.rectangle([(x - width * .07) * S, (base - height * .2) * S, (x + width * .07) * S, base * S], fill=color)


def ridge(base, amplitude, seed):
    r = random.Random(seed)
    phases = [(r.uniform(.004, .012), r.uniform(0, 6), r.uniform(.4, 1)) for _ in range(3)]
    return lambda x: base - amplitude * sum(w * math.sin(f * x + p) for f, p, w in phases) / 2


def landscape(page):
    glow = layer()
    d = ImageDraw.Draw(glow)
    ellipse(d, 250, 1262, 430, 150, rgb('#e98a2d') + (120,))
    ellipse(d, 250, 1262, 230, 66, rgb('#ffc56b') + (150,))
    page.paste(glow.filter(ImageFilter.GaussianBlur(46 * S)), (0, 0), glow.filter(ImageFilter.GaussianBlur(46 * S)))
    for base, amplitude, color, seed in ((1216, 34, '#4a2c17', 5), (1244, 26, '#37210f', 9)):
        hills = layer()
        d = ImageDraw.Draw(hills)
        height = ridge(base, amplitude, seed)
        polygon(d, [(0, 1320)] + [(x, height(x)) for x in range(0, 730, 10)] + [(720, 1320)], rgb(color))
        page.paste(hills, (0, 0), hills)
    forest = layer()
    d = ImageDraw.Draw(forest)
    height = ridge(1262, 12, 3)
    for x in range(-10, 740, 17):
        pine(d, x + rng.uniform(-5, 5), height(x) + 14, rng.uniform(62, 124), rgb('#22130a'))
    polygon(d, [(0, 1330)] + [(x, height(x) + 6) for x in range(0, 730, 10)] + [(720, 1330)], rgb('#22130a'))
    page.paste(forest, (0, 0), forest)


def river(page):
    water = Image.new('RGB', (W, H))
    d = ImageDraw.Draw(water)
    top, bottom = 1296, 1600
    for y in range(top * S, H):
        t = (y / S - top) / (bottom - top)
        d.line([(0, y), (W, y)], fill=mix(rgb('#6a4a2a'), rgb('#12282b'), min(1, t * 2.6) ** .7))
    mask = Image.new('L', (W, H), 0)
    ImageDraw.Draw(mask).rectangle([0, top * S, W, H], fill=255)
    page.paste(water, (0, 0), mask)
    shine = layer()
    d = ImageDraw.Draw(shine)
    for _ in range(90):
        y = rng.uniform(top + 4, bottom - 6)
        t = (y - top) / (bottom - top)
        x = rng.gauss(250, 90 + 210 * t)
        length = rng.uniform(16, 74) * (1 - t * .5)
        ellipse(d, x, y, length, 1.3 + t * 1.5, rgb('#f6b65c') + (int(120 * (1 - t) ** 1.5) + 14,))
    page.paste(shine.filter(ImageFilter.GaussianBlur(.6 * S)), (0, 0), shine.filter(ImageFilter.GaussianBlur(.6 * S)))


def log(draw):
    bark, dark, light = rgb('#5b3a1f'), rgb('#3f2613'), rgb('#7a5230')
    ellipse(draw, 366, 1554, 16, 34, dark)
    draw.rounded_rectangle([366 * S, 1520 * S, 744 * S, 1588 * S], radius=8 * S, fill=bark)
    for y, length in ((1532, 240), (1546, 330), (1560, 280), (1574, 300)):
        start = 380 + rng.uniform(0, 40)
        draw.line([(start * S, y * S), ((start + length) * S, (y + rng.uniform(-2, 2)) * S)], fill=dark, width=2 * S)
    ellipse(draw, 366, 1554, 30, 35, rgb('#c79a63'))
    for radius, color in ((24, '#b78650'), (16, '#c79a63'), (9, '#b78650'), (3, '#8a5a33')):
        ellipse(draw, 366, 1554, radius * .86, radius, rgb(color))
    ellipse(draw, 400, 1524, 26, 5, light)


def capsule(draw, x0, y0, x1, y1, radius, fill):
    """A thick line with round ends: arms and twigs."""
    draw.line([(x0 * S, y0 * S), (x1 * S, y1 * S)], fill=fill, width=int(radius * 2 * S))
    ellipse(draw, x0, y0, radius, radius, fill)
    ellipse(draw, x1, y1, radius, radius, fill)


def mirrored(center, offset):
    return center - offset, center + offset


def beaver(page):
    """Seen from the front and drawn from one side and its mirror image, so it cannot lean."""
    fur, shade, light, belly = rgb('#9a673c'), rgb('#7a4d2a'), rgb('#ad7a4a'), rgb('#dcb78a')
    dark, ink, tooth, twig = rgb('#5a3820'), rgb('#21140c'), rgb('#fdf4de'), rgb('#7c5a34')
    cx = 530
    art = layer()
    d = ImageDraw.Draw(art)
    # The flat tail lies on the log behind the body, with the crossed scales a beaver is known for.
    tail, tail_mask = layer(), layer()
    t = ImageDraw.Draw(tail)
    ellipse(t, cx + 122, 1516, 74, 30, rgb('#44301f'), -8)
    for k in range(-16, 18):
        x = cx + 122 + k * 13
        t.line([((x - 34) * S, 1470 * S), ((x + 34) * S, 1560 * S)], fill=rgb('#5e4531'), width=2 * S)
        t.line([((x + 34) * S, 1470 * S), ((x - 34) * S, 1560 * S)], fill=rgb('#5e4531'), width=2 * S)
    ellipse(ImageDraw.Draw(tail_mask), cx + 122, 1516, 74, 30, (255, 255, 255, 255), -8)
    art.paste(tail, (0, 0), tail_mask.getchannel('A'))
    # A body like a pear, with the shade on its lower edge and a pale belly.
    ellipse(d, cx, 1478, 104, 92, shade)
    ellipse(d, cx, 1470, 100, 90, fur)
    ellipse(d, cx, 1484, 66, 70, belly)
    # Ears, then the head above them.
    for x in mirrored(cx, 66):
        ellipse(d, x, 1296, 25, 25, shade)
        ellipse(d, x, 1298, 13, 13, dark)
    ellipse(d, cx, 1364, 96, 82, shade)
    ellipse(d, cx, 1360, 94, 80, fur)
    ellipse(d, cx, 1336, 62, 44, light)
    # The muzzle, two round cheeks that meet under the nose.
    for x in mirrored(cx, 27):
        ellipse(d, x, 1396, 37, 31, belly)
    ellipse(d, cx, 1380, 30, 22, belly)
    for x in mirrored(cx, 37):
        ellipse(d, x, 1342, 12.5, 13.5, ink)
        ellipse(d, x + 4, 1337, 4, 4, (255, 255, 255))
        ellipse(d, x - 3, 1348, 1.8, 1.8, (255, 255, 255))
    ellipse(d, cx, 1373, 17, 12, ink)
    ellipse(d, cx - 5, 1369, 4.5, 2.4, rgb('#65503f'))
    d.line([(cx * S, 1384 * S), (cx * S, 1399 * S)], fill=ink, width=2 * S)
    d.arc([(cx - 27) * S, 1380 * S, cx * S, 1404 * S], 25, 165, fill=ink, width=2 * S)
    d.arc([cx * S, 1380 * S, (cx + 27) * S, 1404 * S], 15, 155, fill=ink, width=2 * S)
    for x in mirrored(cx, 8.5):
        d.rounded_rectangle([(x - 7.5) * S, 1403 * S, (x + 7.5) * S, 1428 * S], radius=3 * S, fill=tooth)
    d.line([(cx * S, 1404 * S), (cx * S, 1427 * S)], fill=rgb('#d9caa8'), width=S)
    for dy, rise in ((-7, -8), (3, 0), (13, 8)):
        for side in (-1, 1):
            d.line([((cx + side * 56) * S, (1396 + dy) * S), ((cx + side * 104) * S, (1396 + dy + rise) * S)], fill=rgb('#efe0c2'), width=S)
    # A twig held across the chest in both paws, with one leaf.
    capsule(d, cx - 124, 1486, cx + 124, 1486, 6.5, twig)
    for x in mirrored(cx, 124):
        ellipse(d, x, 1486, 5, 5.5, rgb('#d0a878'))
    d.line([((cx - 118) * S, 1484 * S), ((cx - 136) * S, 1460 * S)], fill=twig, width=4 * S)
    ellipse(d, cx - 146, 1450, 16, 9, rgb('#6f9a55'), -48)
    ellipse(d, cx - 122, 1452, 13, 7, rgb('#8ab56a'), -112)
    for side in (-1, 1):
        capsule(d, cx + side * 82, 1444, cx + side * 46, 1484, 16, fur)
        ellipse(d, cx + side * 44, 1487, 20, 15, shade)
        for k in (-11, 0, 11):
            ellipse(d, cx + side * 44 + k, 1478, 5, 5.5, fur)
    # Wide webbed feet hang over the log.
    for side in (-1, 1):
        x = cx + side * 56
        ellipse(d, x, 1556, 46, 16, shade)
        for k in (-30, -10, 10, 30):
            ellipse(d, x + k, 1564, 10, 8, dark)
    # A soft shadow joins the beaver to the log. The beaver is drawn large and set down
    # small, feet on the log, so its head stays below the last card of the home page.
    shadow = layer()
    ellipse(ImageDraw.Draw(shadow), cx, 1554, 124, 11, (0, 0, 0, 130))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6 * S))
    page.paste(shadow, (0, 0), shadow)
    small = art.resize((round(W * SCALE), round(H * SCALE)), Image.LANCZOS)
    page.paste(small, (round(cx * S * (1 - SCALE)), round(1562 * S * (1 - SCALE))), small)


def main():
    page = gradient(((0, '#120c08'), (.45, '#1a120c'), (.66, '#2a1a10'), (.8, '#5a3216'), (1, '#5a3216')))
    landscape(page)
    river(page)
    wood = layer()
    log(ImageDraw.Draw(wood))
    page.paste(wood, (0, 0), wood)
    beaver(page)
    page.resize((540, 1204), Image.LANCZOS).save(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name('beaver.png'))


if __name__ == '__main__':
    main()
