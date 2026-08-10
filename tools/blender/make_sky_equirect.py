"""Turn a shipped sky cubemap into an equirectangular HDR-ish PNG for Blender's world.

Blender's Environment Texture node samples equirectangular or mirror-ball images; it has no
6-face cubemap input. The pack ships the game's own cubemaps as six faces in WGPU order
(+X, -X, +Y, -Y, +Z, -Z, i.e. face0..face5), so they have to be resampled once.

    python tools/blender/make_sky_equirect.py [--name NatureCubemap] [--width 2048] [--out PATH]

Runs outside Blender on purpose: it needs PIL, which a Blender install does not ship.
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SKY_DIR = os.path.join(REPO, "packs", "shared", "sky")


def load_faces(sky_dir, entry):
    """(6, S, S, 3) float32 in face order +X, -X, +Y, -Y, +Z, -Z."""
    imgs = []
    for fn in entry["faces"]:
        p = os.path.join(sky_dir, fn)
        if not os.path.isfile(p):
            raise SystemExit("missing face: %s" % p)
        imgs.append(np.asarray(Image.open(p).convert("RGB"), np.float32) / 255.0)
    s = imgs[0].shape[0]
    if any(i.shape[0] != s or i.shape[1] != s for i in imgs):
        raise SystemExit("cube faces are not square and equal-sized")
    return np.stack(imgs, 0), s


def equirect(faces, size, width):
    """Resample the cube into an equirectangular image, Blender world orientation.

    Blender is Z-up and the pack's cubemap is authored in the pack's Y-up world, so the
    direction is converted per pixel: d_pack = (bx, bz, -by) is the inverse of the importer's
    (x, y, z) -> (x, -z, y).
    """
    h = width // 2
    u = (np.arange(width, dtype=np.float32) + 0.5) / width
    v = (np.arange(h, dtype=np.float32) + 0.5) / h
    theta = (u - 0.5) * (2.0 * np.pi)               # azimuth
    phi = (0.5 - v) * np.pi                         # elevation, +pi/2 at the top row
    ct = np.cos(phi)[:, None]
    # AZIMUTH, and this is the one line in the file that is easy to get backwards. Blender's
    # Environment Texture node maps a direction to `u = 0.5 - atan2(d.y, d.x) / 2pi` (the
    # equirectangular case in kernel/geom/../projection). Our `u = 0.5 + theta / 2pi`, so the
    # direction this texel must hold has atan2(by, bx) = -theta, i.e.
    #     bx = cos(theta) * ct,  by = -sin(theta) * ct.
    # The previous pair, (-sin, +cos), is atan2 = theta + pi/2: a 90 degree rotation AND a flip of
    # handedness, so the sky was loaded MIRRORED and every cloud mass - and every reflection of one
    # off the pack's 587 near-mirror materials - sat in the wrong world direction. Verified by
    # rendering a synthetic 5-texel dot: it lands where the formula above predicts to 0.300 deg,
    # and 47.596 deg away from where the old pair predicted.
    bx = (np.cos(theta)[None, :]) * ct
    by = (-np.sin(theta)[None, :]) * ct
    bz = np.repeat(np.sin(phi)[:, None], width, 1)

    # Blender -> pack (Y-up)
    dx, dy, dz = bx, bz, -by

    ax, ay, az = np.abs(dx), np.abs(dy), np.abs(dz)
    face = np.where(
        (ax >= ay) & (ax >= az), np.where(dx > 0, 0, 1),
        np.where((ay >= az), np.where(dy > 0, 2, 3), np.where(dz > 0, 4, 5)),
    ).astype(np.int32)

    # Per-face (u, v) in [-1, 1], standard cube mapping.
    ma = np.maximum(np.maximum(ax, ay), az)
    ma = np.where(ma == 0, 1e-9, ma)
    sc = np.select(
        [face == 0, face == 1, face == 2, face == 3, face == 4, face == 5],
        [-dz, dz, dx, dx, dx, -dx],
    )
    tc = np.select(
        [face == 0, face == 1, face == 2, face == 3, face == 4, face == 5],
        [-dy, -dy, dz, -dz, -dy, -dy],
    )
    fu = np.clip((sc / ma + 1.0) * 0.5, 0.0, 1.0)
    fv = np.clip((tc / ma + 1.0) * 0.5, 0.0, 1.0)
    px = np.clip((fu * size).astype(np.int32), 0, size - 1)
    py = np.clip((fv * size).astype(np.int32), 0, size - 1)
    return faces[face, py, px]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="NatureCubemap")
    ap.add_argument("--width", type=int, default=2048)
    ap.add_argument("--sky-dir", default=SKY_DIR)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    meta_p = os.path.join(a.sky_dir, "sky.json")
    meta = json.load(open(meta_p, encoding="utf-8"))
    cubes = meta.get("cubemaps") or {}
    if a.name not in cubes:
        sky = [k for k, v in cubes.items() if v.get("is_sky")]
        raise SystemExit("no cubemap %r. is_sky candidates: %s" % (a.name, sorted(sky)))
    entry = cubes[a.name]

    faces, size = load_faces(a.sky_dir, entry)
    img = equirect(faces, size, a.width)
    out = a.out or os.path.join(a.sky_dir, "%s_equirect.png" % a.name)
    Image.fromarray(np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8)).save(out)
    print("%s: %d faces at %dpx -> %dx%d equirect" % (a.name, len(entry["faces"]), size,
                                                      a.width, a.width // 2))
    print("  zenith %s  horizon %s  mean %s"
          % (entry.get("zenith"), entry.get("horizon"), entry.get("mean")))
    print("  wrote %s" % out)


if __name__ == "__main__":
    main()
