"""Measure the exterior EXRs, then finish them the way the photoreal path intends.

Step three of the exterior example (see exterior_photoreal_build.py for the chain). Runs OUTSIDE
Blender, like eft_grade.py itself, and needs numpy and the OpenEXR bindings.

    python tools/blender/examples/exterior_photoreal_grade.py stats renders/exterior/*.exr
    python tools/blender/examples/exterior_photoreal_grade.py sweep renders/exterior/*.exr
    python tools/blender/examples/exterior_photoreal_grade.py auto  renders/exterior/*.exr
    python tools/blender/examples/exterior_photoreal_grade.py final renders/exterior/*.exr

    stats   per-frame LINEAR statistics and the grey-metered exposure, nothing written
    sweep   grade at E/2, E and 2E into sweep_<name>_<tag>.png and print display statistics
    auto    grade each frame at its OWN grey-metered exposure, to <name>.png
    final   grade every frame at the one exposure in EFT_E, to <name>.png

WHY `stats` IS THE DEFAULT THING TO RUN. The photoreal finish is AgX with a centre-weighted grey
meter, and a grey meter drives whatever median it finds onto middle grey. A frame with a dead black
slab across its lower half, or an unlit one, therefore still grades to a plausible looking picture
with a sensible histogram and content in it. THE GRADED PNG CANNOT TELL YOU WHETHER THE RENDER IS
RIGHT. Only the scene-referred percentiles can, and the specific number to watch on this shot is
p1: the transparent-bounce failure described in exterior_photoreal_render.py leaves a band at
0.00129 linear, which is zero plus the analytic haze in-scatter at 3.3 m, so it never shows up as
an exact zero.

THE CHAIN THIS APPLIES, and every stage of it is a documented departure from the viewer, which is
what "photoreal" means here (see docs/extraction/photorealism.md):

    exposure   auto_exposure(mode="grey", look="agx"), an ISO centre-weighted mean driven onto the
               scene value AgX maps to 18% display grey. NOT the highlight rule, which exists to
               park highlights under the game LUT's clip and says nothing about where the midtones
               land.
    look       Blender's own AgX Base sRGB, read from the cube Blender ships. eft_grade validates
               its implementation against Blender's OCIO to 0.005 CV (--selfcheck-agx).
    vignette   the lens's real cos^4 falloff for a 50 mm on a 36 mm sensor, applied in LINEAR.
               Cycles renders no natural vignetting at all (corner/centre = 1.00000 exactly against
               a uniform white world), so this is a correction, not a double count. It must never
               ride on the game's authored vignette, which is 4.68x deeper and is a different claim
               about the same lens; eft_grade refuses the combination.
    grain      photon SHOT noise, sigma = sqrt(x / N_sat) on exposed linear, N_sat = 15000, roughly
               ISO 400 on full frame. This is the one INVENTED number in the chain - the pack ships
               no sensor - so it is named after what it is rather than called "film grain".

WHAT THE PUBLISHED FRAME USED. docs/img/interchange-bearcamp-photoreal.jpg is slot 01 (patrol frame
20) at E = 2.37876, which is that frame's OWN grey-metered exposure, with grain seed 1. So

    EFT_E=2.37876 EFT_SEED=1 python tools/blender/examples/exterior_photoreal_grade.py final \
        renders/exterior/exterior_photoreal_01.exr

reproduces it, and so does `auto` on that frame up to the noise realisation. That exposure was
RECOVERED by solving it back out of the published PNG rather than read from a log: graded at it,
the result matches the published pixels to 0.25 CV mean absolute, which is PNG rounding. The whole
six-frame set was finished at that one exposure, held from the first frame, because a per-frame
auto flickers across a shot.

env:
    EFT_OUTDIR   where the graded PNGs go (default renders/exterior)
    EFT_E        the fixed exposure used by `final`, and the sweep's centre if set
    EFT_SEED     grain seed, default 0. Changes the noise realisation and nothing else.
    EFT_LENS     focal length for the optical vignette, default 50 to match the solved camera
"""
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))
sys.path.insert(0, os.path.join(REPO, "tools", "blender"))
import eft_grade as G          # noqa: E402

LENS = float(os.environ.get("EFT_LENS", "50"))
SENSOR, GRAIN = 36.0, 15000.0

USAGE = "usage: exterior_photoreal_grade.py {stats|sweep|auto|final} <exr> [exr...]"


def lin_stats(x):
    """Scene-referred percentiles. p99.9/p50 is the brightness-INDEPENDENT range metric."""
    lum = x[..., 0] * 0.2126 + x[..., 1] * 0.7152 + x[..., 2] * 0.0722
    p = np.percentile(lum, [1.0, 50.0, 99.9])
    return dict(p1=float(p[0]), p50=float(p[1]), p999=float(p[2]),
                mean=float(lum.mean()), rng=float(p[2] / max(p[1], 1e-9)),
                zero=float(100.0 * (lum <= 0.0).mean()))


def disp_stats(rgb):
    """Display-referred code values, for reporting only. These CANNOT answer "is it right"."""
    cv = rgb * 255.0
    lum = cv[..., 0] * 0.2126 + cv[..., 1] * 0.7152 + cv[..., 2] * 0.0722
    p = np.percentile(lum, [1, 10, 50, 90, 99])
    mx, mn = rgb.max(-1), rgb.min(-1)
    return dict(p1=p[0], p10=p[1], p50=p[2], p90=p[3], p99=p[4],
                lo=float(100.0 * (lum <= 1.0).mean()), hi=float(100.0 * (lum >= 254.0).mean()),
                sat=float(np.mean((mx - mn) / np.maximum(mx, 1e-6))))


def main(argv):
    if len(argv) < 2 or argv[0] not in ("stats", "sweep", "auto", "final"):
        raise SystemExit(USAGE)
    mode = argv[0]
    files = argv[1:]
    cube = G.load_agx_cube(G.find_agx_cube())
    outdir = os.environ.get("EFT_OUTDIR", os.path.join(REPO, "renders", "exterior"))
    fixedE = float(os.environ.get("EFT_E", "0") or 0)
    seed = int(os.environ.get("EFT_SEED", "0"))
    if mode != "stats":
        os.makedirs(outdir, exist_ok=True)
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        lin = G._read_exr(f)
        s = lin_stats(lin)
        E = G.auto_exposure(lin, mode="grey", look="agx")
        print("[stat] %-26s p1 %.5f  p50 %.5f  p99.9 %.5f  mean %.5f  exact-0 %.2f%%  "
              "p99.9/p50 %6.2f  Egrey %.5f"
              % (name, s["p1"], s["p50"], s["p999"], s["mean"], s["zero"], s["rng"], E), flush=True)
        if mode == "stats":
            continue
        if mode == "sweep":
            for tag, mul in (("m1EV", 0.5), ("0EV", 1.0), ("p1EV", 2.0)):
                use = (fixedE or E) * mul
                out = G.apply_grade(lin, cube, use, vignette=False, look="agx",
                                    optical_vignette=(LENS, SENSOR), grain=GRAIN, grain_seed=seed)
                d = disp_stats(out)
                G._write_png(os.path.join(outdir, "sweep_%s_%s.png" % (name, tag)), out)
                print("      %-5s E %8.5f  CV p1/p10/p50/p90/p99 %5.1f %5.1f %5.1f %5.1f %5.1f "
                      " <=1CV %5.2f%%  >=254CV %5.3f%%  sat %.4f"
                      % (tag, use, d["p1"], d["p10"], d["p50"], d["p90"], d["p99"],
                         d["lo"], d["hi"], d["sat"]), flush=True)
            continue
        use = E if mode == "auto" else fixedE
        if mode == "final" and use <= 0.0:
            raise SystemExit("`final` needs EFT_E; run `stats` first, or use `auto`")
        out = G.apply_grade(lin, cube, use, vignette=False, look="agx",
                            optical_vignette=(LENS, SENSOR), grain=GRAIN, grain_seed=seed)
        d = disp_stats(out)
        G._write_png(os.path.join(outdir, name + ".png"), out)
        print("      graded E %.5f seed %d  CV p1/p10/p50/p90/p99 %5.1f %5.1f %5.1f %5.1f %5.1f "
              " <=1CV %5.2f%%  >=254CV %5.3f%%  sat %.4f"
              % (use, seed, d["p1"], d["p10"], d["p50"], d["p90"], d["p99"],
                 d["lo"], d["hi"], d["sat"]), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
