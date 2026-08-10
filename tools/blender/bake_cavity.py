"""Bake a cavity (self-occlusion) map from every normal map a pack ships.

    python tools/blender/bake_cavity.py --pack packs/interchange.eftpack --out out/cavity

Runs OUTSIDE Blender: it needs PIL, which a Blender install does not ship. The output is what
import_eftpack(cavity_dir=...) reads, and it is a PHOTOREALISM-ONLY input - see the warning at the
bottom of this docstring.

WHY THIS EXISTS. The game shader has no ambient-occlusion term of any kind, at any scale, and the
pack ships no AO map: gpu_draw.wgsl perturbs the shading normal and stops there. In a rasteriser
that is a choice. In Cycles it is a hole, because Cycles derives occlusion from GEOMETRY and the
normal map's relief has no geometry - so nothing shadows anything inside a brick course, a bolt
recess or a tread pattern, and 1016 of the built scene's 1121 materials carry such a map.

WHAT IS DERIVED. Everything. A tangent-space normal map IS a slope field:

    p = -nx/nz,  q = -ny/nz          (the slopes the Normal Map node already assumes)
    laplacian(h) = dp/dx + dq/dy     solved by FFT, so h comes out in TEXEL units, mean-free
    occ = mean over s in {4, 8, 16, 32} texels of clamp((boxblur_s(h) - h) / s, 0, 1)
    cavity = 1 - occ

occ is local relief measured against progressively wider neighbourhoods: a texel that sits below
its surroundings is occluded by them, in proportion to how far below and how wide the pit. There
is no amplitude knob, no curve and no hand tuning; the one assumption is "normal-map slopes are
per texel", which is the assumption the Normal Map node makes anyway.

THE GREEN FLIP MATTERS AND IS RECORDED. Flipping green negates q, which mirrors the integrated
height in v - the crevices come out on the wrong side of every ridge. The pack's own conversion
flag (manifest conversion.normalMapGreenFlip, true on every shipped pack) is therefore the default
here and is written into cavity.json so the importer can refuse a mismatched bake.

THE GATE. Maps whose cavity std is under 0.030 are skipped: painted panels, decal normals and
poster maps are flat, and their cavity map is a uniform 1.0 that costs a texture fetch to do
nothing. That drops 178 of 433 maps on interchange, and the gated build measured slightly MORE
frame local contrast than the ungated one.

8-BIT IS ENOUGH. Measured bit-identical to a float32 buffer (-1.62% mean frame luminance either
way), and float32 image buffers in Blender cost 4x the memory and a measured 235% render penalty
against the 8-bit path's 71% at the same low resolution.

PARITY: none of this is in the game. Never point a game-parity build at the output.
"""

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

SCALES = (4, 8, 16, 32)       # texels; the multi-scale relief neighbourhoods
GATE_STD = 0.030              # skip maps flatter than this - see the docstring


def _height_from_normal(rgb, green_flip):
    """Poisson-integrate a tangent-space normal map to a mean-free height field, in texels."""
    n = rgb.astype(np.float32) * 2.0 - 1.0
    if green_flip:
        n[..., 1] = -n[..., 1]
    ln = np.sqrt(np.maximum((n * n).sum(-1), 1e-12))
    n /= ln[..., None]
    nz = np.where(np.abs(n[..., 2]) < 1e-3, 1e-3, n[..., 2])
    p = -n[..., 0] / nz
    q = -n[..., 1] / nz

    h, w = p.shape
    # divergence of the slope field, by the same centred differences the solver inverts
    div = (np.roll(p, -1, 1) - np.roll(p, 1, 1)) * 0.5 + \
          (np.roll(q, -1, 0) - np.roll(q, 1, 0)) * 0.5
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    # eigenvalue of the periodic 5-point laplacian; DC is left at 0, which is what makes h
    # mean-free (and is the only sane choice: an absolute height has no meaning here)
    lam = (2.0 * np.cos(2.0 * np.pi * fy) - 2.0) + (2.0 * np.cos(2.0 * np.pi * fx) - 2.0)
    lam[0, 0] = 1.0
    H = np.fft.fft2(div) / lam
    H[0, 0] = 0.0
    return np.real(np.fft.ifft2(H)).astype(np.float32)


def _boxblur(a, r):
    """Periodic box blur of radius r, via a summed-area pass in each axis. O(n) in the radius."""
    k = 2 * r + 1
    c = np.cumsum(np.concatenate([a, a[:, :k]], 1), 1, dtype=np.float32)
    c = np.concatenate([c[:, :1] * 0, c], 1)
    out = (c[:, k:k + a.shape[1]] - c[:, :a.shape[1]]) / k
    out = np.roll(out, r, 1)
    c = np.cumsum(np.concatenate([out, out[:k]], 0), 0, dtype=np.float32)
    c = np.concatenate([c[:1] * 0, c], 0)
    out = (c[k:k + a.shape[0]] - c[:a.shape[0]]) / k
    return np.roll(out, r, 0)


def cavity_from_normal(rgb, green_flip=True, scales=SCALES):
    """(h, w, 3) float normal map in [0,1] -> (h, w) float32 cavity in [0,1]."""
    hgt = _height_from_normal(rgb, green_flip)
    # A neighbourhood wider than the map has no meaning, and the periodic cumsum below cannot
    # build one. 32x32 normal maps do ship (2 on interchange), so drop the scales that do not fit.
    lim = max(1, min(hgt.shape[0], hgt.shape[1]) // 2 - 1)
    use = [s for s in scales if s <= lim] or [lim]
    occ = np.zeros_like(hgt)
    for s in use:
        occ += np.clip((_boxblur(hgt, s) - hgt) / float(s), 0.0, 1.0)
    return (1.0 - occ / len(use)).astype(np.float32)


def _normal_maps(pack_dir):
    """Every distinct normal-map path in the pack's materials.json, resolved to a real file."""
    mp = os.path.join(pack_dir, "materials.json")
    doc = json.load(open(mp, encoding="utf-8"))
    recs = doc.get("materials") if isinstance(doc, dict) else doc
    seen = {}
    for r in recs or []:
        p = r.get("normal")
        if not p:
            continue
        base = os.path.splitext(os.path.basename(str(p).replace("\\", "/")))[0]
        if base in seen:
            continue
        f = os.path.join(pack_dir, str(p).replace("\\", "/"))
        if os.path.isfile(f):
            seen[base] = f
    return seen


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pack", required=True, help="path to a *.eftpack directory")
    ap.add_argument("--out", required=True, help="directory to write <normal>.cav.png into")
    ap.add_argument("--gate", type=float, default=GATE_STD,
                    help="skip maps whose cavity std is below this (0 bakes everything)")
    ap.add_argument("--no-green-flip", action="store_true",
                    help="bake without the pack's normalMapGreenFlip convention")
    ap.add_argument("--limit", type=int, default=0, help="stop after N maps (for a smoke test)")
    a = ap.parse_args(argv)

    green_flip = not a.no_green_flip
    maps = _normal_maps(a.pack)
    os.makedirs(a.out, exist_ok=True)
    print("[cavity] %d unique normal map(s) in %s" % (len(maps), a.pack))

    n_done = n_gated = n_fail = 0
    for i, (base, path) in enumerate(sorted(maps.items())):
        if a.limit and i >= a.limit:
            break
        dst = os.path.join(a.out, base + ".cav.png")
        if os.path.isfile(dst):
            n_done += 1
            continue
        try:
            rgb = np.asarray(Image.open(path).convert("RGB"), np.float32) / 255.0
            cav = cavity_from_normal(rgb, green_flip, SCALES)
        except Exception as e:
            print("[cavity] %s: %s" % (base, e))
            n_fail += 1
            continue
        if float(cav.std()) < a.gate:
            n_gated += 1
            continue
        Image.fromarray((np.clip(cav, 0, 1) * 255.0 + 0.5).astype(np.uint8), "L").save(dst)
        n_done += 1
        if n_done % 50 == 0:
            print("[cavity] %d baked, %d gated" % (n_done, n_gated), flush=True)

    json.dump({"greenFlip": bool(green_flip), "scales": list(SCALES), "gate": a.gate,
               "pack": os.path.basename(os.path.abspath(a.pack))},
              open(os.path.join(a.out, "cavity.json"), "w", encoding="utf-8"), indent=1)
    print("[cavity] %d written, %d gated flat (std < %.3f), %d failed -> %s"
          % (n_done, n_gated, a.gate, n_fail, a.out))


if __name__ == "__main__":
    main(sys.argv[1:])
