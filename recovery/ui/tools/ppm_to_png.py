#!/usr/bin/env python3
"""Convert exact software-rendered test frames for visual inspection only."""
from pathlib import Path
from PIL import Image
import sys
for p in Path(sys.argv[1]).glob('*.ppm'):
    Image.open(p).save(p.with_suffix('.png'))
    print(p.with_suffix('.png'))
