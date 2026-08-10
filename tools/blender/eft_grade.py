"""Apply the game's own display chain to linear renders.

Standalone (numpy + imageio/OpenEXR via Blender or OpenCV). No bpy. Typical use:

    python tools/blender/eft_grade.py frames/ out/ --lut packs/shared/grade_lut.bin

WHY. Blender's AgX is a neutral filmic tonemap; the game is not neutral. Its look comes from a
64-cube colour grade shipped in the pack, applied over a specific exposure and finished with a
vignette. Render FLAT (linear EXR) and run this, and the result matches the viewer instead of
merely being a nice picture of the same geometry.

THE CHAIN, ported from viewer/assets/shaders/grade.wgsl and render/grade.rs. Order matters and
every step has a trap in it:

  1. EXPOSURE. A plain linear scale (the scene carries no tonemap), and RENDERER-RELATIVE: the
     native viewer ships 1.35 (`DEFAULT_GRADE_EXPOSURE` in render/mod.rs - the shader's "default
     0.18" comment is the WEB viewer's value and is stale). Cycles has its own radiance scale set
     by the sun and world strengths, so this is the one number that must be re-tuned by eye rather
     than copied; everything downstream of it is exact.

  2. SHAPER: `p = sqrt(clamp(lin / 4, 0, 1))`. This is the LUT's domain map and it is NOT sRGB and
     NOT log. It exists so a 64-cube can cover HDR up to 4.0. Feed the LUT sRGB-encoded values, as
     every "apply a .cube" tutorial assumes, and you sample the wrong slice everywhere - the image
     will look plausible and be wrong.

  3. LUT, 64x64x64, sampled trilinearly. The shipped file is a 512x512 RGBA8 raw dump of the WEB
     viewer's 2D-tiled atlas: 64 blue slices in an 8x8 grid, each 64x64. Its bytes are DISPLAY
     ENCODED, and the shader works in linear, so every texel's sRGB encode is INVERTED at load.
     Skip that inversion and the grade comes out crushed and oversaturated.

  4. VIGNETTE, from the PRISM post stack: radius on (x/1.15, y/0.95) in centred [-1,1] coords,
     smoothstep(0.55, 1.25), strength 0.488. It is authored in DISPLAY space, so it must be applied
     to the display-encoded signal, not to the linear one.

  5. sRGB encode for output.

`--look agx` and `--look filmic` skip the LUT and use a display transform instead, for a straight
A/B against the game grade from the same EXRs.

THE DISPLAY TRANSFORM, and why "agx" now really is AgX. `--look filmic` is the curve this file
shipped for a year under the name "agx": `x/(x+0.155)*1.019`. It is not AgX and it is barely a
filmic curve. Measured on a neutral ramp through it and sRGB:

    transform                   0.18 CV   CV/stop at grey   1.0 CV   4.0 CV   0.02 CV
    x/(x+0.155)*1.019             195.3        28.0          241.3    252.9     95.8
    AgX Base sRGB (Blender's)     117.6        38.8          196.6    233.0     31.9
    ACES 1.3 sRGB                  90.8        46.2          207.1    243.6     10.5
    ACES 2.0 sRGB                  89.0        35.3          180.2    229.5      9.8
    Khronos PBR Neutral           104.9        46.8          238.8    253.1      8.3
    Filmic sRGB (Blender's)       127.6        34.5          205.8    242.1     40.6

Two things are wrong with the old curve and only one of them was known. The grey lift (+2.40 EV,
195 CV where every standard transform lands 112-120) was already documented and was worked around
downstream in auto_exposure(mode="grey"). The other is that it has NO shoulder and NO toe: it is a
single hyperbola, so its slope at grey is 28.0 CV/stop, the flattest of the six, while its 1.0 and
4.0 both sit pinned against white and its 0.02 sits at 96 CV. Re-exposing it fixes where the
midtones sit and cannot fix how far apart they are. That is why the exposure-corrected photoreal
frame still measured HIGHER saturation than the game (0.2294 vs 0.2130) and still read washed: the
grade's teal/amber split-tone survives, the contrast that would separate it does not.

So look="agx" is now Blender's own AgX Base sRGB, read from the transform Blender itself ships and
applied here in numpy (see _agx_display). It is the only candidate above that satisfies both
requirements at once - 18% grey at 117.6 CV, inside the 112-120 band, AND +38.6% slope at grey over
the old curve - and being Blender's default view transform it is what the EXRs were framed against
in the viewport. ACES 2.0 has a beautiful shoulder and less midtone contrast than AgX; ACES 1.3 and
PBR Neutral have more slope but place grey at 89-105 CV, which buys the contrast by underexposing.

MEASURED ON THE FRAME, renders/photoreal_f0300.exr at 2560x1440, both looks metered by
auto_exposure(mode="grey") against their own anchor, both with cos^4 50 mm and ISO-400 shot noise
(renders/photoreal_filmic_vs_agx.png is the split):

    look     E         p1/p10/p50/p90/p99 CV   in-frame slope   5x5 local contrast   <=1 CV
    filmic   0.27066    2/15/68/147/183        21.87 CV/stop        0.0712            0.21%
    agx      1.47629    1/ 7/58/151/185        24.45 CV/stop        0.0781            3.67%
    game     1.73604    6/13/32/184/227        (LUT)                0.1160            0.00%

The in-frame slope is a fit of display CV against log2 of exposed scene luma over the p20-p80 band,
so it is the picture's own midtone contrast rather than the curve's; it is lower than the 38.8 the
neutral ramp gives because the fit band reaches into the toe. +11.8% there and +9.7% local contrast
is the whole win, and it is smaller than the ramp numbers promise. The cost is real and is the
number to watch: AgX has a true toe, so 3.67% of the frame lands at or under 1 CV where the old
curve put 0.21% (1.98% of it is the curve, the rest is shot noise clamped at zero). The game path
still has 1.5x this frame's local contrast, so the display transform was not the only thing flat.

THE PHOTOREAL TAIL. A display transform alone is not a photograph. Stacking the old curve's grey
lift on the HIGHLIGHT-anchored auto_exposure() below (p99 -> 0.92, which exists to park highlights
under the LUT's clip) put a measured photoreal frame's display histogram at p10/p50/p90 =
101/167/220 CV against the game path's 24/81/192. That is the milky look, and it was exposure
placement on top of a flat curve.

So three optional stages live here, all OFF by default and all invalid on look="game" (see
docs/extraction/photorealism.md):

  * auto_exposure(mode="grey") meters like a camera - an ISO centre-weighted mean driven onto the
    scene value the chosen curve maps to 18% display grey - instead of like a clip detector.
  * optical_vignette=(lens_mm, sensor_mm) applies the real cos^4 falloff of the taking lens, in
    LINEAR, before the curve. Cycles renders NO natural vignetting (measured: corner/centre =
    1.00000 exactly against a uniform white world), so this is a correction, not a double-count.
    It must never be combined with the game's authored vignette above, which is 4.68x deeper
    (-2.125 EV at the corner against cos^4's -0.454 EV for the 50 mm this shot uses) and is a
    different claim about the same lens.
  * grain=N_sat adds photon SHOT NOISE, sigma(x) = sqrt(x / N_sat) on exposed linear. N_sat is the
    sensor's full-well count; 15000 is roughly ISO 400 on full frame. This is the one INVENTED
    number in the file - the pack ships no sensor - so it is opt-in and named after what it is.
"""

import argparse
import os
import struct
import sys
import zlib

import numpy as np

__all__ = ["load_grade_lut", "load_agx_cube", "find_agx_cube", "apply_grade", "auto_exposure"]

EXPOSURE = 1.35            # the native viewer's default; re-tune for Cycles' radiance scale
VIG_DIV = (1.15, 0.95)
VIG_EDGE = (0.55, 1.25)
VIG_STRENGTH = 0.488
LUT_N = 64
LUT_TILES = 8

# AgX Base sRGB, transcribed from Blender 5.1's config.ocio (the "AgX Base Rec.1886" ViewTransform,
# config.ocio:332). Three stages, in this order:
#   1. scene-linear Rec.709 -> Linear FilmLight E-Gamut. The config routes this through the
#      Linear CIE-XYZ I-E scene reference, so AGX_EGAMUT below is the COMPOSITE of that round trip
#      and the E-Gamut matrix at config.ocio:604, read straight off the OCIO processor by pushing
#      the identity basis through it. Its columns sum to 1.0000/1.0000/0.9999, i.e. white is
#      preserved, which is the check that it was composed the right way round.
#   2. AllocationTransform lg2 over [-12.47393, 12.5260688117]. Those are not arbitrary:
#      2^-12.47393 = 0.18 * 2^-10 and 2^12.52607 = 0.18 * 2^15, so the shaper is exactly
#      "-10 to +15 stops around 18% grey", which is what AgX_Base_sRGB.cube's own header says it
#      expects. Hence the /25.0.
#   3. the 57^3 AgX_Base_sRGB.cube, then Rec.1886 -> sRGB (a 2.4 power decode, then the sRGB OETF).
# Reimplementing the SIGMOID instead of reading the cube was rejected: the cube carries the inset,
# the rotation and the per-channel outset (its header: rotate [3, -1, -2], inset [0.4, 0.22, 0.13],
# outset [0.4, 0.22, 0.04]) and any of those transcribed wrong is a hue shift, not a visible error.
# _agx_display is validated against Blender's own OCIO processor by selfcheck_agx() below, which
# --selfcheck-agx runs: measured max error 0.000021 (0.005 CV) over 40k log-uniform samples.
AGX_EGAMUT = np.array([[0.5593711, 0.076220706, 0.06552671],
                       [0.30478334, 0.7879718, 0.16454676],
                       [0.13584556, 0.13580747, 0.7699265]], np.float32)
AGX_LOG_MIN = -12.47393        # log2(0.18) - 10
AGX_LOG_RANGE = 25.0           # 25 stops, -10 to +15 around grey
AGX_CUBE_NAME = "AgX_Base_sRGB.cube"

# The scene-linear value each look puts on 18% display grey (0.4547 encoded, 116 CV). Solved
# against the shipped implementation by bisection, not chosen, and re-solved whenever the curve
# changes - which is why the table sits next to the curves instead of in a caller. auto_exposure's
# "grey" meter drives the metered mean onto this number, so a stale entry here is a whole-frame
# exposure error.
GREY_IN = {"agx": 0.17465, "filmic": 0.03202}
GREY_IN_FILMIC = GREY_IN["filmic"]      # kept as a name; the old 0.0342 was a coarser solve
GREY_WEIGHT_SIGMA = 0.30   # ISO centre-weighted metering pattern, in units of image HEIGHT


def _srgb_to_linear(x):
    x = np.asarray(x, np.float32)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _linear_to_srgb(x):
    x = np.clip(np.asarray(x, np.float32), 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92,
                    1.055 * np.power(x, 1.0 / 2.4) - 0.055).astype(np.float32)


def load_grade_lut(path):
    """512x512 RGBA8 tiled atlas -> a LINEAR (64, 64, 64, 3) cube indexed [b, g, r].

    The atlas is 8x8 tiles of 64x64; tile t holds blue slice t, laid out row-major.
    """
    raw = np.fromfile(path, np.uint8)
    if raw.size != 512 * 512 * 4:
        raise IOError("%s is %d bytes, expected %d" % (path, raw.size, 512 * 512 * 4))
    atlas = raw.reshape(512, 512, 4)[:, :, :3]
    cube = np.empty((LUT_N, LUT_N, LUT_N, 3), np.float32)
    for b in range(LUT_N):
        ty, tx = divmod(b, LUT_TILES)
        tile = atlas[ty * LUT_N:(ty + 1) * LUT_N, tx * LUT_N:(tx + 1) * LUT_N, :]
        cube[b] = tile.astype(np.float32) / 255.0          # tile is [g, r] -> cube[b, g, r]
    return _srgb_to_linear(cube)                            # the shipped bytes are display encoded


def find_agx_cube():
    """Locate Blender's shipped AgX_Base_sRGB.cube, or None.

    $EFT_AGX_CUBE wins if set. Otherwise walk the same roots make_ocio_config._find_blender_cm
    walks and take the highest-sorting hit, so a machine with several Blenders installed picks the
    newest rather than whichever os.walk reached first. Depth-capped because Blender's install tree
    is deep and the addons directory underneath it is enormous.
    """
    env = os.environ.get("EFT_AGX_CUBE")
    if env:
        return env
    roots = [r"C:\Program Files\Blender Foundation", "/usr/share/blender", "/Applications"]
    hits = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if AGX_CUBE_NAME in filenames and os.path.basename(dirpath) == "luts":
                hits.append(os.path.join(dirpath, AGX_CUBE_NAME))
            if dirpath.count(os.sep) - root.count(os.sep) > 5:
                dirnames[:] = []
    return sorted(hits)[-1] if hits else None


def load_agx_cube(path):
    """Parse an Iridas .cube into an (N, N, N, 3) float32 array indexed [b, g, r].

    The .cube format runs RED fastest, then green, then blue, so a flat reshape to (N, N, N, 3)
    already lands on [b][g][r] - the same convention load_grade_lut builds and _sample_trilinear
    expects, so the two LUT paths share one sampler. DOMAIN_MIN/MAX are asserted to be 0..1 rather
    than honoured: AgX's domain map is the lg2 shaper in _agx_display, and a cube that wanted a
    different domain would be silently mis-sampled here.
    """
    lo, hi, n, vals = [0.0] * 3, [1.0] * 3, None, []
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key = line.split()[0].upper()
            if key == "LUT_3D_SIZE":
                n = int(line.split()[1])
            elif key == "DOMAIN_MIN":
                lo = [float(v) for v in line.split()[1:4]]
            elif key == "DOMAIN_MAX":
                hi = [float(v) for v in line.split()[1:4]]
            elif key in ("TITLE", "LUT_1D_SIZE", "LUT_3D_INPUT_RANGE"):
                continue
            else:
                vals.append([float(v) for v in line.split()[:3]])
    if n is None:
        raise IOError("%s: no LUT_3D_SIZE" % path)
    if lo != [0.0] * 3 or hi != [1.0] * 3:
        raise IOError("%s: domain %s..%s, expected 0..1" % (path, lo, hi))
    if len(vals) != n ** 3:
        raise IOError("%s: %d entries, expected %d" % (path, len(vals), n ** 3))
    return np.asarray(vals, np.float32).reshape(n, n, n, 3)


def _agx_display(lin, cube):
    """Scene-linear Rec.709 -> sRGB display, through Blender's AgX Base. See the AGX_* block.

    The log2 is taken on max(x, 2^-12.47393) rather than on x: the shaper's own floor is -10 stops
    below grey, everything under it maps to the cube's black corner anyway, and feeding log2 a zero
    (which a clean render has plenty of, in shadow and in sky alpha) would otherwise spray -inf
    through the sampler and come back NaN.
    """
    x = np.asarray(lin, np.float32) @ AGX_EGAMUT
    x = np.maximum(x, np.float32(2.0) ** AGX_LOG_MIN)
    t = (np.log2(x) - AGX_LOG_MIN) / AGX_LOG_RANGE
    out = _sample_tetrahedral(cube, t)
    # the cube emits Rec.1886, a pure 2.4 power; decode it and re-encode for sRGB
    return _linear_to_srgb(np.clip(out, 0.0, 1.0) ** 2.4)


def _sample_trilinear(cube, rgb):
    """rgb in [0,1] shaper space, shape (..., 3) -> graded linear, matching HW trilinear."""
    n = cube.shape[0]
    c = np.clip(rgb, 0.0, 1.0) * (n - 1)
    i0 = np.floor(c).astype(np.int32)
    i1 = np.minimum(i0 + 1, n - 1)
    f = (c - i0).astype(np.float32)
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]

    def at(bi, gi, ri):
        return cube[bi, gi, ri]

    c00 = at(b0, g0, r0) * (1 - fr) + at(b0, g0, r1) * fr
    c01 = at(b0, g1, r0) * (1 - fr) + at(b0, g1, r1) * fr
    c10 = at(b1, g0, r0) * (1 - fr) + at(b1, g0, r1) * fr
    c11 = at(b1, g1, r0) * (1 - fr) + at(b1, g1, r1) * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb


def _sample_tetrahedral(cube, rgb, chunk=1 << 19):
    """Tetrahedral 3D-LUT lookup, the interpolation Blender's config asks AgX for.

    NOT interchangeable with _sample_trilinear, which is why both exist. Trilinear splits the cell
    into 8 corners and AgX's cube is strongly non-planar off the neutral axis, so the two agree on
    greys and diverge on saturated colour: measured against Blender's own OCIO processor over 40k
    log-uniform samples spanning 20 stops, trilinear peaks at 14.29 CV of error while this peaks at
    0.005 CV. The game path keeps trilinear because the VIEWER samples its LUT with hardware
    trilinear, so matching it there is the whole point.

    Six tetrahedra, selected by the ordering of the in-cell fractions (Kasson's decomposition, the
    same one OCIO's Lut3DOpCPU uses). Written as a SORT rather than as six branches: descending-sort
    the three fractions and the tetrahedron is always the same four corners walked in the sorted
    axis order - black, then the largest axis, then the two largest, then white - with barycentric
    weights 1-f0, f0-f1, f1-f2, f2. That is four gathers instead of eight plus six candidate
    results: min-of-3 on a 2560x1440 frame, 3.66 s for the six-branch np.select form against 1.21 s
    for this, max difference between the two 0.0 exactly. The branch form is also where the one real
    bug lived (a swapped corner in one of six, invisible on greys, 27.6 CV out on saturated colour);
    there is only one code path here to get wrong.

    Chunked over pixels because this holds about thirteen (N, 3) temporaries live at once, which is
    ~80 MB at this chunk size and ~550 MB if handed a whole 2560x1440 frame.
    """
    n = cube.shape[0]
    src = np.asarray(rgb, np.float32)
    a = np.ascontiguousarray(src).reshape(-1, 3)
    out = np.empty_like(a)
    eye = np.eye(3, dtype=np.int32)
    for s in range(0, a.shape[0], chunk):
        c = np.clip(a[s:s + chunk], 0.0, 1.0) * (n - 1)
        i0 = np.floor(c).astype(np.int32)
        np.minimum(i0, n - 2, out=i0)                        # keep i0+1 in range at the top edge
        f = c - i0
        order = np.argsort(-f, axis=1, kind="stable")        # descending; ties share a face anyway
        fs = np.take_along_axis(f, order, 1)
        d1 = eye[order[:, 0]]                                # step onto the largest axis
        d2 = d1 + eye[order[:, 1]]                           # then onto the second largest

        def at(d):
            j = i0 + d
            return cube[j[:, 2], j[:, 1], j[:, 0]]

        out[s:s + chunk] = ((1.0 - fs[:, 0:1]) * at(0)
                            + (fs[:, 0:1] - fs[:, 1:2]) * at(d1)
                            + (fs[:, 1:2] - fs[:, 2:3]) * at(d2)
                            + fs[:, 2:3] * at(1))
    return out.reshape(src.shape)


def _vignette(h, w):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    u = (x + 0.5) / w * 2.0 - 1.0
    v = (y + 0.5) / h * 2.0 - 1.0
    d = np.sqrt((u / VIG_DIV[0]) ** 2 + (v / VIG_DIV[1]) ** 2)
    t = np.clip((d - VIG_EDGE[0]) / (VIG_EDGE[1] - VIG_EDGE[0]), 0.0, 1.0)
    s = t * t * (3.0 - 2.0 * t)
    return (1.0 - VIG_STRENGTH * s).astype(np.float32)[..., None]


def _cos4(h, w, lens_mm, sensor_mm):
    """The lens's own illumination falloff, (h, w, 1) linear multiplier.

    cos^4(theta) with theta the field angle at the pixel: f / sqrt(f^2 + r^2) raised to the fourth,
    r measured on the sensor. The sensor's HEIGHT follows the render's aspect, which is how the
    camera is actually configured (Blender fits the 36 mm to the long edge). For 50 mm on
    36 x 20.25 mm that is 0.7300 at the corner, 0.7838 at the x edge and 0.8991 over the frame.
    """
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    sx = sensor_mm
    sy = sensor_mm * (float(h) / float(w))
    rx = ((x + 0.5) / w - 0.5) * sx
    ry = ((y + 0.5) / h - 0.5) * sy
    r2 = rx * rx + ry * ry
    f = float(lens_mm)
    c = f / np.sqrt(f * f + r2)
    return (c ** 4).astype(np.float32)[..., None]


def _shot_noise(x, n_sat, seed=0):
    """Photon shot noise on EXPOSED linear: sigma = sqrt(x / n_sat).

    Signal-dependent by construction, so it lands hardest in the shadows - which is what separates
    it from the flat gaussian "film grain" slider that reads as dirt on the lens.
    """
    rng = np.random.default_rng(seed)
    s = np.sqrt(np.maximum(x, 0.0) / float(n_sat))
    return np.maximum(x + s * rng.standard_normal(x.shape).astype(np.float32), 0.0)


def apply_grade(lin, cube=None, exposure=EXPOSURE, vignette=True, look="game",
                optical_vignette=None, grain=None, grain_seed=0):
    """Linear scene-referred RGB float -> display-encoded [0,1] RGB.

    cube              the LUT the look needs: the pack's 64-cube for look="game", Blender's AgX
                      57-cube (load_agx_cube) for look="agx". look="filmic" is analytic and takes
                      None. Passing the wrong one is caught here rather than sampled silently.
    optical_vignette  (lens_mm, sensor_mm) to apply the lens's cos^4 falloff in LINEAR. Rejected
                      outright on look="game", and equally rejected on any look while `vignette`
                      is on: the game's authored vignette is 4.68x deeper and the two are mutually
                      exclusive claims about the same lens. Both halves of that rule are enforced
                      here - game gets the authored one, the filmic path gets the optical one.
    grain             full-well electron count for photon shot noise, or None. Applied to exposed
                      linear, after the falloff, before the curve - the order light actually
                      arrives in.
    """
    x = np.asarray(lin, np.float32) * float(exposure)
    if optical_vignette is not None:
        if look == "game" or vignette:
            raise ValueError("the optical cos^4 vignette cannot ride on the game's authored one; "
                             "see the module docstring")
        x = x * _cos4(x.shape[0], x.shape[1], optical_vignette[0], optical_vignette[1])
    if grain:
        x = _shot_noise(x, grain, grain_seed)
    if look == "game":
        if cube is None or cube.shape[0] != LUT_N:
            raise ValueError("the game look needs the pack's 64-cube (load_grade_lut)")
        shaped = np.sqrt(np.clip(x / 4.0, 0.0, 1.0))        # the shaper - see module docstring
        out = _sample_trilinear(cube, shaped)
        out = _linear_to_srgb(out)
    elif look == "agx":
        if cube is None or cube.shape[0] == LUT_N:
            raise ValueError("look='agx' needs Blender's AgX cube (load_agx_cube), not the pack's")
        out = _agx_display(x, cube)
    elif look == "filmic":                                   # the pre-AgX curve, kept for the A/B
        out = _linear_to_srgb(x / (x + 0.155) * 1.019)
    else:
        raise ValueError("look must be 'game', 'agx' or 'filmic', got %r" % look)
    if vignette:
        out = out * _vignette(out.shape[0], out.shape[1])    # authored in DISPLAY space
    return np.clip(out, 0.0, 1.0)


def selfcheck_agx(cube, config=None):
    """Prove _agx_display IS Blender's AgX, by asking Blender's own OCIO to do it and diffing.

    Cheap insurance on a transform that is four stages of transcription, every one of which fails
    QUIETLY. A transposed AGX_EGAMUT still preserves white. A wrong log range still produces a
    plausible S-curve. A mis-branched tetrahedron matched the neutral ramp to 1 CV and was 27.6 CV
    out on saturated colour, which is exactly the class of error a "looks right" eyeball misses.
    So the samples below are log-uniform over 20 stops and per-channel independent, not a grey ramp.

    Needs `pip install opencolorio`; skipped with a message if it is not installed.
    """
    try:
        import PyOpenColorIO as ocio
    except ImportError:
        print("[agx] selfcheck SKIPPED (pip install opencolorio to enable)")
        return None
    if config is None:
        p = find_agx_cube()
        config = os.path.join(os.path.dirname(os.path.dirname(p or "")), "config.ocio")
    proc = ocio.Config.CreateFromFile(config).getProcessor(
        "Linear Rec.709", "AgX Base sRGB").getDefaultCPUProcessor()
    rng = np.random.default_rng(7)
    lin = (0.18 * 2.0 ** rng.uniform(-12, 8, size=(40000, 3))).astype(np.float32)
    lin[:200] = 0.0                                          # renders are full of exact zeros
    got = lin.copy()
    proc.applyRGB(got)
    err = float(np.abs(_agx_display(lin, cube) - got).max())
    if err > 0.5 / 255.0:
        raise SystemExit("agx selfcheck: max error %.5f (%.2f CV) vs Blender's own transform"
                         % (err, err * 255.0))
    print("[agx] selfcheck  max error vs %s = %.6f (%.3f CV)" % (config, err, err * 255.0))
    return err


def auto_exposure(lin, percentile=99.0, target=0.92, mode="highlight",
                  grey_in=None, look="agx"):
    """Solve the one number that is not copyable from the viewer.

    mode="highlight" (the default, and the ONLY correct rule for look="game")
        Exposure that lands the image's highlights just under the LUT's clip point. The cube
        saturates at shaper 0.5, i.e. LINEAR 1.0 after exposure: everything brighter maps to the
        same texel. So exposure is not a taste control there, it decides what blows out. Atlas's
        1.35 is calibrated to ITS radiance scale; Cycles' scale comes from whatever sun and world
        strengths the scene was built with, so the two are not comparable and copying the number
        across would either crush the image or clip most of it. Solve for it instead: put the Nth
        percentile of luminance at `target`, just below the clip.

    mode="grey" (the photoreal path only)
        Meter the way a camera does. A clip detector anchored to p99 says nothing about where the
        midtones land. Weight luminance with the ISO centre-weighted pattern, a gaussian of sigma
        0.30 x image height, and drive that weighted mean onto `grey_in` - the scene value the
        chosen curve maps to 18% display grey, taken from the GREY_IN table for `look` unless the
        caller overrides it. `look` is a REQUIRED part of this solve, not decoration: grey_in is
        0.17465 on AgX and 0.03202 on the old filmic curve, so metering AgX with the filmic anchor
        underexposes by 2.45 EV.

        Do NOT use it on look="game": the LUT hard-clips at exposed-linear 1.0 and its shaper is
        sqrt(lin/4), so dropping 2 EV there shoves the whole image into the bottom quarter of the
        cube's domain and throws away LUT resolution for no gain.
    """
    lum = lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722
    if mode == "grey":
        # resolved HERE, not at the top: look="game" has no grey anchor by design and the highlight
        # rule below never wants one, so hoisting this turned `--look game --auto` into a crash
        if grey_in is None:
            if look not in GREY_IN:
                raise ValueError("no 18%% grey anchor for look=%r; pass grey_in" % look)
            grey_in = GREY_IN[look]
        h, w = lum.shape[:2]
        y, x = np.mgrid[0:h, 0:w].astype(np.float32)
        s = GREY_WEIGHT_SIGMA * float(h)
        wt = np.exp(-((((y + 0.5) - h * 0.5) / s) ** 2 + (((x + 0.5) - w * 0.5) / s) ** 2))
        m = float((lum * wt).sum() / max(wt.sum(), 1e-6))
        return float(grey_in / max(m, 1e-6))
    if mode != "highlight":
        raise ValueError("auto_exposure mode must be 'highlight' or 'grey', got %r" % mode)
    p = float(np.percentile(lum, percentile))
    return float(target / max(p, 1e-6))


def _read_exr(path):
    """Read a linear EXR as float32 RGB.

    Uses the official OpenEXR bindings (`pip install OpenEXR`). OpenCV is deliberately NOT used:
    the opencv-python 5.x wheels ship with OpenEXR compiled out entirely - `haveImageReader` is
    False and even `imwrite` has no EXR writer - so it fails on every file rather than only on the
    lossy DWA codecs, which is easy to misread as a bad render.
    """
    import OpenEXR

    with OpenEXR.File(path) as f:
        px = f.channels()
        # Blender names the channel group by what it wrote: "RGB" for RGB output, "RGBA" when
        # alpha is on, and separate "R"/"G"/"B" for some layouts. Take whichever is present and
        # keep the first three components.
        for key in ("RGB", "RGBA"):
            if key in px:
                return np.asarray(px[key].pixels, np.float32)[:, :, :3]
        if all(c in px for c in ("R", "G", "B")):
            return np.stack([np.asarray(px[c].pixels, np.float32) for c in ("R", "G", "B")], -1)
        raise IOError("%s: no RGB channels, found %s" % (path, sorted(px.keys())))


def _write_png(path, rgb01):
    """Minimal 8-bit PNG writer, so this depends on nothing but numpy and zlib."""
    a = (np.clip(rgb01, 0, 1) * 255.0 + 0.5).astype(np.uint8)
    h, w, _ = a.shape
    raw = b"".join(b"\x00" + a[y].tobytes() for y in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    open(path, "wb").write(png)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # optional only so --selfcheck-agx can run without inventing a src/dst; still required for a
    # real grade, and the check for that is four lines down
    ap.add_argument("src", nargs="?", help="directory of linear .exr frames (or a single file)")
    ap.add_argument("dst", nargs="?", help="output directory for graded .png")
    ap.add_argument("--lut", default="packs/shared/grade_lut.bin")
    ap.add_argument("--agx-cube", default=None,
                    help="Blender's AgX_Base_sRGB.cube (auto-detected; $EFT_AGX_CUBE overrides)")
    ap.add_argument("--look", default="game", choices=("game", "agx", "filmic"))
    ap.add_argument("--selfcheck-agx", action="store_true",
                    help="diff the AgX implementation against Blender's own OCIO and exit")
    ap.add_argument("--exposure", type=float, default=EXPOSURE)
    ap.add_argument("--auto", action="store_true",
                    help="solve exposure from the FIRST frame and hold it for the whole shot "
                         "(per-frame auto would flicker)")
    ap.add_argument("--no-vignette", action="store_true")
    ap.add_argument("--meter", default="highlight", choices=("highlight", "grey"),
                    help="how --auto solves exposure; 'grey' is the photoreal rule and is "
                         "refused on --look game")
    ap.add_argument("--lens", type=float, default=None,
                    help="focal length in mm; enables the cos^4 optical vignette (photoreal only)")
    ap.add_argument("--sensor", type=float, default=36.0, help="sensor WIDTH in mm for --lens")
    ap.add_argument("--grain", type=float, default=None,
                    help="full-well electrons for photon shot noise, e.g. 15000 (~ISO 400)")
    a = ap.parse_args(argv)

    if a.look == "game" and (a.lens or a.grain or a.meter == "grey"):
        raise SystemExit("--lens/--grain/--meter grey are photoreal-only; they are not what the "
                         "viewer does and would make the frame undiffable against it")

    if a.look == "game" and not a.selfcheck_agx:
        cube = load_grade_lut(a.lut)
    elif a.look == "agx" or a.selfcheck_agx:
        p = a.agx_cube or find_agx_cube()
        if not p or not os.path.isfile(p):
            raise SystemExit("could not find %s; pass --agx-cube or set $EFT_AGX_CUBE. It ships "
                             "with Blender under 5.x/datafiles/colormanagement/luts/"
                             % AGX_CUBE_NAME)
        cube = load_agx_cube(p)
        print("[grade] agx cube %s (%d^3)" % (p, cube.shape[0]))
    else:
        cube = None
    if a.selfcheck_agx:
        selfcheck_agx(cube)
        return
    if not a.src or not a.dst:
        raise SystemExit("src and dst are required unless --selfcheck-agx is given")
    files = ([a.src] if os.path.isfile(a.src)
             else [os.path.join(a.src, f) for f in sorted(os.listdir(a.src))
                   if f.lower().endswith(".exr")])
    if not files:
        raise SystemExit("no .exr frames in %s" % a.src)
    os.makedirs(a.dst, exist_ok=True)

    optvig = (a.lens, a.sensor) if a.lens else None
    exposure = a.exposure
    if a.auto:
        exposure = auto_exposure(_read_exr(files[0]), mode=a.meter, look=a.look)
        print("[grade] auto exposure %.5f (meter=%s, from %s)"
              % (exposure, a.meter, os.path.basename(files[0])))
    for i, f in enumerate(files):
        out = apply_grade(_read_exr(f), cube, exposure, not a.no_vignette, a.look,
                          optical_vignette=optvig, grain=a.grain, grain_seed=i)
        _write_png(os.path.join(a.dst, os.path.splitext(os.path.basename(f))[0] + ".png"), out)
        if i % 30 == 0 or i == len(files) - 1:
            print("[grade] %d/%d" % (i + 1, len(files)), flush=True)
    print("[grade] %d frame(s) -> %s (look=%s, exposure=%.5f%s%s)"
          % (len(files), a.dst, a.look, exposure,
             ", cos^4 %.0fmm" % a.lens if optvig else "",
             ", grain N_sat=%.0f" % a.grain if a.grain else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
