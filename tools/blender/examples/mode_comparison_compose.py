"""Finish each half of the two-mode comparison the way its own mode intends, then compose them.

Second half of the comparison (see mode_comparison.py for the render side, and run that twice
first). Runs OUTSIDE Blender, needs numpy and the OpenEXR bindings; PIL is optional and only draws
the caption bands.

    python tools/blender/examples/mode_comparison_compose.py \
        renders/comparison/game_f0300.exr renders/comparison/photoreal_f0300.exr renders/comparison

It writes three files into the output directory: `game_graded.png`, `photoreal_graded.png` and
`game_vs_photoreal.png`, the last being the two side by side with a small gap.

THE TWO CHAINS, AND WHY THEY MUST NOT BE THE SAME CHAIN. Both halves come out of Cycles as the same
kind of file - flat, linear, scene-referred - and everything below is display-referred. Grading
them identically would be the wrong comparison: the question is not "what do these two renders look
like through one look", it is "what does each mode look like finished the way it is meant to be
finished".

    game       exposure 1.35, then the pack's own 64-cube grade LUT over the sqrt(lin/4) shaper,
               then the authored PRISM vignette. Exactly what the viewer does. 1.35 is
               DEFAULT_GRADE_EXPOSURE in the viewer's render module, and it is renderer-relative:
               it means what it means only because SUN_ENERGY and SKY_STRENGTH were least-squares
               fitted against a viewer frame at that exposure.
    photoreal  a centre-weighted grey meter, a filmic display transform, the taking lens's own
               cos^4 falloff, and photon shot noise. NO authored vignette: the game's is 4.68x
               deeper than cos^4 for this 50 mm and the two are contradictory claims about the same
               lens, so eft_grade refuses the combination outright.

WHICH CURVE THE PUBLISHED IMAGE USED, AND WHY THE DEFAULT HERE IS `filmic`. docs/img/game-vs-
photoreal.jpg was made when `eft_grade`'s look named "agx" was the curve x/(x+0.155)*1.019. That
curve is not AgX: it has no toe and no shoulder, its slope at grey is the flattest of the six
transforms eft_grade measures, and it places 18% grey at 195 CV where every standard transform puts
it at 112-120. It has since been renamed `filmic` and `agx` is now Blender's own AgX Base sRGB, read
from the cube Blender ships and validated against Blender's OCIO to 0.005 CV.

So `--look filmic` reproduces the published right half and `--look agx` is the better picture. The
caption on the published image reads E=0.28909, and that number cannot be re-metered today either:
the grey anchor for that curve was 0.0342 then and was re-solved to 0.03202 later, so the same
meter on the same frame now returns 0.27066, which is 0.095 EV darker. Set EFT_E=0.28909 to
reproduce the published pixels exactly (verified: 0.25 CV mean absolute against the published PNG,
which is PNG rounding), or leave it unset for the current, better-solved meter.

env:
    EFT_LOOK   photoreal display transform: "filmic" (default, what the published image used)
               or "agx" (Blender's real AgX, the better picture)
    EFT_E      absolute exposure for the photoreal half; overrides the meter
    EFT_EV     EV offset applied to the metered exposure (default 0.0); ignored when EFT_E is set
    EFT_LUT    the pack's grade LUT (default packs/shared/grade_lut.bin)
    EFT_SEED   grain seed for the photoreal half (default 7, what the published image used)
    EFT_FONT   a TrueType font file for the captions; without PIL, or without a usable font, the
               halves are composed unlabelled
"""
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))
sys.path.insert(0, os.path.join(REPO, "tools", "blender"))
import eft_grade as G          # noqa: E402

GAME_EXPOSURE = 1.35           # the viewer's DEFAULT_GRADE_EXPOSURE
LENS_MM, SENSOR_MM = 50.0, 36.0
GRAIN_NSAT = 15000.0           # roughly ISO 400 full frame; the one invented number in the chain
GAP_PX = 8
BACKDROP = 16.0 / 255.0

USAGE = "usage: mode_comparison_compose.py <game.exr> <photoreal.exr> <outdir>"


def lum(x):
    return x[..., 0] * 0.2126 + x[..., 1] * 0.7152 + x[..., 2] * 0.0722


def lin_report(tag, lin):
    L = lum(lin)
    p = np.percentile(L, [1, 50, 99, 99.9])
    print("[%s] linear luminance  p1 %.5f  p50 %.5f  p99 %.5f  p99.9 %.5f  max %.4f  mean %.5f  "
          "p99.9/p50 %.2f  frac>1.0 %.3f%%"
          % (tag, p[0], p[1], p[2], p[3], L.max(), L.mean(), p[3] / max(p[1], 1e-9),
             100.0 * (L > 1.0).mean()), flush=True)


def disp_report(tag, rgb):
    cv = lum(rgb) * 255.0
    p = np.percentile(cv, [1, 10, 50, 90, 99])
    print("[%s] display CV  p1 %.0f  p10 %.0f  p50 %.0f  p90 %.0f  p99 %.0f  >250CV %.3f%%  "
          "<4CV %.3f%%"
          % (tag, p[0], p[1], p[2], p[3], p[4], 100.0 * (cv > 250).mean(),
             100.0 * (cv < 4).mean()), flush=True)


def label(rgb, title, subs):
    """Dark band across the top with a title and subtitle lines. No-op without PIL or a font."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[compose] PIL not installed; composing unlabelled", flush=True)
        return rgb
    img = Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8))
    f1 = f2 = None
    for name in ([os.environ["EFT_FONT"]] if os.environ.get("EFT_FONT") else
                 ["DejaVuSans-Bold.ttf", "arialbd.ttf", "LiberationSans-Bold.ttf"]):
        try:
            f1 = ImageFont.truetype(name, 56)
            break
        except Exception:
            continue
    for name in ["DejaVuSans.ttf", "arial.ttf", "LiberationSans-Regular.ttf"]:
        try:
            f2 = ImageFont.truetype(name, 27)
            break
        except Exception:
            continue
    if f1 is None or f2 is None:
        f1 = f2 = ImageFont.load_default()
    d = ImageDraw.Draw(img, "RGBA")
    band = 78 + 34 * len(subs)
    d.rectangle([0, 0, img.width, band], fill=(0, 0, 0, 170))
    d.text((34, 12), title, font=f1, fill=(255, 255, 255, 255))
    for i, s in enumerate(subs):
        d.text((36, 76 + 33 * i), s, font=f2, fill=(206, 206, 206, 255))
    return np.asarray(img, np.float32) / 255.0


def main(argv):
    if len(argv) < 3:
        raise SystemExit(USAGE)
    game_exr, photo_exr, outdir = argv[0], argv[1], argv[2]
    look = (os.environ.get("EFT_LOOK") or "filmic").lower()
    if look not in ("filmic", "agx"):
        raise SystemExit("EFT_LOOK must be 'filmic' or 'agx', got %r" % look)
    lut = os.environ.get("EFT_LUT", os.path.join(REPO, "packs", "shared", "grade_lut.bin"))
    seed = int(os.environ.get("EFT_SEED", "7"))
    ev_off = float(os.environ.get("EFT_EV", "0") or 0)
    fixedE = float(os.environ.get("EFT_E", "0") or 0)
    os.makedirs(outdir, exist_ok=True)

    gl = G._read_exr(game_exr)
    pl = G._read_exr(photo_exr)
    if gl.shape != pl.shape:
        raise SystemExit("the two halves are %s and %s; render both at one resolution"
                         % (gl.shape, pl.shape))
    lin_report("game ", gl)
    lin_report("photo", pl)

    # ---- game: the viewer's own finish ----------------------------------------------------
    gcube = G.load_grade_lut(lut)
    gimg = G.apply_grade(gl, gcube, GAME_EXPOSURE, True, "game")
    disp_report("game ", gimg)
    print("[game ] exposure %.4f (the viewer's DEFAULT_GRADE_EXPOSURE), authored vignette ON; "
          "the highlight rule would have chosen %.4f" % (GAME_EXPOSURE, G.auto_exposure(gl)),
          flush=True)

    # ---- photoreal: metered like a camera, not like a clip detector -------------------------
    pcube = G.load_agx_cube(G.find_agx_cube()) if look == "agx" else None
    e_grey = G.auto_exposure(pl, mode="grey", look=look)
    e = fixedE if fixedE > 0 else e_grey * (2.0 ** ev_off)
    print("[photo] look=%s  grey meter E=%.5f  chosen E=%.5f (%+.3f EV off the meter)"
          % (look, e_grey, e, np.log2(e / max(e_grey, 1e-9))), flush=True)
    pimg = G.apply_grade(pl, pcube, e, False, look,
                         optical_vignette=(LENS_MM, SENSOR_MM), grain=GRAIN_NSAT, grain_seed=seed)
    disp_report("photo", pimg)

    G._write_png(os.path.join(outdir, "game_graded.png"), gimg)
    G._write_png(os.path.join(outdir, "photoreal_graded.png"), pimg)

    # ---- compose ----------------------------------------------------------------------------
    h, w = gimg.shape[:2]
    shot = "%s, identical camera" % os.path.splitext(os.path.basename(photo_exr))[0]
    dev = np.log2(e / max(e_grey, 1e-9))
    a = label(gimg, "GAME", [
        shot,
        "the viewer's own finish: 64-cube grade LUT over the sqrt(lin/4) shaper,",
        "exposure %.2f, authored PRISM vignette   |   uniform traced haze, glassTRS"
        % GAME_EXPOSURE])
    b = label(pimg, "PHOTOREAL", [
        shot,
        "%s curve, centre-weighted grey meter E=%.5f (%+.2f EV off the meter)," % (look, e, dev),
        "cos^4 %.0f mm optical vignette, ISO-400 shot noise, NO authored vignette" % LENS_MM,
        "|   height-falloff haze from the Z pass, transmissive glass, cavity, glare+CA"])

    out = np.full((h, w * 2 + GAP_PX, 3), BACKDROP, np.float32)
    out[:, :w] = a
    out[:, w + GAP_PX:] = b
    dst = os.path.join(outdir, "game_vs_photoreal.png")
    G._write_png(dst, out)
    print("[compose] wrote %s  %dx%d  %.1f MB"
          % (dst, out.shape[1], out.shape[0], os.path.getsize(dst) / 1e6), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
