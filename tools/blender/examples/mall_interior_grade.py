"""Measure and grade the mall EXRs. The measurement is the point; the grade is the by-product.

Step four of the interior example (see mall_interior_build.py for the chain). Runs OUTSIDE Blender,
like eft_grade.py itself, and needs numpy, PIL and the OpenEXR bindings.

    python tools/blender/examples/mall_interior_grade.py stats renders/mall/mall_test_*.exr
    python tools/blender/examples/mall_interior_grade.py auto  renders/mall/mall_test_*.exr
    python tools/blender/examples/mall_interior_grade.py final renders/mall/mall_test_*.exr

    stats   per-frame LINEAR statistics only, nothing written
    auto    grade each frame at its OWN grey-metered exposure, to <name>.png
    final   grade every frame at the one exposure in EFT_E, to <name>.png

WHY `stats` EXISTS, AND WHY IT IS THE DEFAULT THING TO RUN. With AgX and grey metering, an unlit
room still grades to a plausible looking picture: auto exposure finds the median wherever the
median happens to be and puts it at middle grey, so a frame 175x too dark comes back with a
sensible histogram, sensible contrast and content in it. Measured on the central square, the
practicals give a median linear 0.15416 and sun-and-sky-only gives 0.00088, and the two graded PNGs
are both perfectly presentable images. THE GRADED PNG CANNOT TELL YOU WHETHER THE ROOM IS LIT. Only
p1 and p50 of the scene-referred linear can, which is why the render stage writes flat EXR and why
this file prints those percentiles before it prints anything about a look.

The second thing the linear statistics settle is the haze. `haze_floor` below evaluates the
analytic in-scatter the photoreal compositor adds on top of the beauty pass, at whatever depth you
ask for, in the same linear luminance units as the frame percentiles printed beside it. That is how
"the veil is more than the surface it covers" stops being an opinion: at 60 m the veil alone is
0.0199 against a room median of 0.014. See mall_interior_cams.py for what was done about it.

env:
    EFT_OUTDIR   where the graded PNGs go (default renders/mall)
    EFT_E        the fixed exposure used by `final`
    EFT_LENS     focal length for the optical vignette, default 35 to match the example's cameras
"""
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))
sys.path.insert(0, os.path.join(REPO, "tools", "blender"))
import eft_grade as G          # noqa: E402

LENS = float(os.environ.get("EFT_LENS", "35"))
SENSOR, GRAIN = 36.0, 15000.0

# The analytic depth haze the photoreal compositor adds on top of the beauty pass:
#     out = in * exp(-sigma * z) + L * (1 - exp(-sigma * z))
# COPIED from example_scene.HAZE_SIGMA / HAZE_INSCATTER rather than imported, because that module
# does `import bpy` at the top and this one runs under a plain interpreter. If the volume's
# density or scale height is refitted there, refit these too; they are properties of the volume,
# not of any staging.
HAZE_SIGMA = (2.549e-4, 2.525e-4, 2.623e-4)
HAZE_L = (1.2491, 1.3298, 1.3951)

USAGE = "usage: mall_interior_grade.py {stats|auto|final} <exr> [exr...]"


def haze_floor(z):
    """Linear luminance the haze ALONE contributes at depth z metres, with nothing behind it."""
    v = [HAZE_L[i] * (1.0 - np.exp(-HAZE_SIGMA[i] * z)) for i in range(3)]
    return v[0] * 0.2126 + v[1] * 0.7152 + v[2] * 0.0722


def lin_stats(x):
    """Scene-referred percentiles. p50 is the number that says whether the room is lit."""
    lum = x[..., 0] * 0.2126 + x[..., 1] * 0.7152 + x[..., 2] * 0.0722
    p = np.percentile(lum, [0.1, 1.0, 10.0, 50.0, 90.0, 99.0, 99.9])
    return dict(p01=float(p[0]), p1=float(p[1]), p10=float(p[2]), p50=float(p[3]),
                p90=float(p[4]), p99=float(p[5]), p999=float(p[6]),
                mean=float(lum.mean()), zero=float(100.0 * (lum <= 0.0).mean()),
                rng=float(p[6] / max(p[3], 1e-9)))


def disp_stats(rgb):
    """Display-referred code values, for reporting only. These CANNOT answer the lit question."""
    cv = rgb * 255.0
    lum = cv[..., 0] * 0.2126 + cv[..., 1] * 0.7152 + cv[..., 2] * 0.0722
    p = np.percentile(lum, [1, 10, 50, 90, 99])
    mx, mn = rgb.max(-1), rgb.min(-1)
    return dict(p1=p[0], p10=p[1], p50=p[2], p90=p[3], p99=p[4],
                lo=float(100.0 * (lum <= 1.0).mean()), hi=float(100.0 * (lum >= 254.0).mean()),
                sat=float(np.mean((mx - mn) / np.maximum(mx, 1e-6))))


def main(argv):
    if len(argv) < 2 or argv[0] not in ("stats", "auto", "final"):
        raise SystemExit(USAGE)
    mode = argv[0]
    files = argv[1:]
    cube = G.load_agx_cube(G.find_agx_cube())
    outdir = os.environ.get("EFT_OUTDIR", os.path.join(REPO, "renders", "mall"))
    fixedE = float(os.environ.get("EFT_E", "0") or 0)
    if mode != "stats":
        os.makedirs(outdir, exist_ok=True)
    print("[grade] haze-only linear luminance: 10 m %.5f  25 m %.5f  60 m %.5f  121 m %.5f"
          % (haze_floor(10), haze_floor(25), haze_floor(60), haze_floor(121)), flush=True)
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        lin = G._read_exr(f)
        s = lin_stats(lin)
        E = G.auto_exposure(lin, mode="grey", look="agx")
        print("[stat] %-16s p0.1 %.5f p1 %.5f p10 %.5f p50 %.5f p90 %.5f p99 %.5f p99.9 %.5f "
              "mean %.5f  exact-0 %.2f%%  p99.9/p50 %6.1f  Egrey %8.3f"
              % (name, s["p01"], s["p1"], s["p10"], s["p50"], s["p90"], s["p99"], s["p999"],
                 s["mean"], s["zero"], s["rng"], E), flush=True)
        if mode == "stats":
            continue
        use = E if mode == "auto" else fixedE
        out = G.apply_grade(lin, cube, use, vignette=False, look="agx",
                            optical_vignette=(LENS, SENSOR), grain=GRAIN, grain_seed=0)
        d = disp_stats(out)
        G._write_png(os.path.join(outdir, name + ".png"), out)
        print("      graded E %.4f  CV p1/p10/p50/p90/p99 %5.1f %5.1f %5.1f %5.1f %5.1f  "
              "<=1CV %5.2f%%  >=254CV %5.3f%%  sat %.4f"
              % (use, d["p1"], d["p10"], d["p50"], d["p90"], d["p99"], d["lo"], d["hi"],
                 d["sat"]), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
