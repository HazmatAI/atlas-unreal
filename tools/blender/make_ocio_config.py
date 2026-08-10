"""Build an OCIO config that adds the game's grade to Blender as a real View Transform.

Runs OUTSIDE Blender (numpy only):

    python tools/blender/make_ocio_config.py --out <dir>
    set OCIO=<dir>/config.ocio       # then start Blender

WHY. `tools/blender/eft_grade.py` applies the game's display chain to rendered EXRs, which is the
right way to finish frames but tells you nothing while you work: the viewport still shows AgX, so
look-dev happens under a transform the game does not use. Blender's colour management is
OpenColorIO, so the chain can be installed as a selectable View - "EFT Game Grade" next to AgX,
Filmic and Standard - and then the VIEWPORT, the render window and any saved 8-bit image all agree
with the viewer.

WHAT IT DOES. Copies Blender's bundled colormanagement directory (never modifies the install),
writes one LUT into its search path, and appends one ColorSpace plus one View per display.

    RangeTransform + ExponentTransform     p = sqrt(clamp(lin/4, 0, 1))
    eft_grade.cube                         p -> graded LINEAR, the pack's 64-cube with its
                                           display encode inverted

The shaper is analytic rather than a baked .spi1d. A 4096-entry 1D LUT sounds like plenty and is
not: sqrt is vertical at the origin, so linear interpolation across the first table interval is
wrong by up to 0.0039 in shaper space, which is visible banding in the deep shadows - exactly where
this grade puts its lift. The Range/Exponent pair reproduces the shader to 7e-6 and needs no file.
`RangeTransform` supplies the /4 and the clamp; forward `ExponentTransform` is pow(in, value),
verified against the shader rather than assumed.

The cube is sampled with `interpolation: linear` (trilinear), not the tetrahedral OCIO would
otherwise prefer, because the authority here is a GPU sampler: the game and the viewer both read
this LUT through hardware trilinear filtering. Tetrahedral is the better interpolator in general
and it is the wrong one here - it disagrees with the reference exactly where the cube is least
smooth.

The view is added to `active_views:` as well as to each display. That list is an ALLOWLIST: a view
missing from it is discarded without any error, which looks precisely like a malformed colorspace.

THREE LIMITS, all inherent:

  * NO VIGNETTE. It is a spatial effect and a colour transform cannot express one. The viewport
    will therefore be slightly brighter at the corners than a finished frame. Use eft_grade.py for
    final frames; this is for working.
  * EXPOSURE IS SEPARATE. The viewer's `DEFAULT_GRADE_EXPOSURE` is 1.35, which is not baked in so
    it stays adjustable. Set Color Management > Exposure to log2(1.35) = 0.433 to match.
  * sRGB DISPLAY ONLY. The grade ends in a hardcoded sRGB encode because that is what the game
    does. The view is offered on all six displays so it is always reachable, but on Display P3 or
    Rec.2100 it emits sRGB-encoded values into a display expecting a different encoding and the
    result is wrong. Keep Display Device on sRGB. (Blender's own Filmic Log and Raw views are the
    same shape and have the same caveat.)
"""

import argparse
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eft_grade import load_grade_lut                      # noqa: E402

CS_NAME = "EFT Game Grade"
SHAPER_MAX = 4.0


def _write_cube(path, cube):
    """64^3 .cube. Order is RED fastest, then green, then blue - which is exactly the order the
    loader's [b][g][r] array iterates in, so no transpose is needed (and one would be silent)."""
    n = cube.shape[0]
    with open(path, "w", encoding="utf-8") as f:
        f.write('TITLE "EFT game grade"\n')
        f.write("LUT_3D_SIZE %d\n" % n)
        f.write("DOMAIN_MIN 0.0 0.0 0.0\n")
        f.write("DOMAIN_MAX 1.0 1.0 1.0\n")
        for b in range(n):
            for g in range(n):
                for r in range(n):
                    c = cube[b, g, r]
                    f.write("%.8f %.8f %.8f\n" % (c[0], c[1], c[2]))


COLORSPACE_BLOCK = """
  - !<ColorSpace>
    name: {name}
    family: EFT
    equalitygroup:
    bitdepth: 32f
    description: |
      Escape from Tarkov display chain: sqrt(lin/4) shaper, the pack's 64-cube grade LUT, then the
      display encode. No vignette (spatial, not a colour transform). Set Exposure 0.433 to match
      the viewer's DEFAULT_GRADE_EXPOSURE of 1.35.
    isdata: false
    from_scene_reference: !<GroupTransform>
      children:
        - !<ColorSpaceTransform> {{src: {ref}, dst: {lin}}}
        - !<RangeTransform> {{min_in_value: 0, max_in_value: {smax}, min_out_value: 0, max_out_value: 1}}
        - !<ExponentTransform> {{value: [0.5, 0.5, 0.5, 1]}}
        - !<FileTransform> {{src: eft_grade.cube, interpolation: linear}}
        - !<ColorSpaceTransform> {{src: {lin}, dst: {srgb}}}
"""


def build(src_dir, out_dir, lut_bin, ref="Linear CIE-XYZ E", lin="Linear Rec.709", srgb="sRGB"):
    if os.path.abspath(src_dir) == os.path.abspath(out_dir):
        raise SystemExit("refusing to write over Blender's own colormanagement directory")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(src_dir, out_dir)

    luts = os.path.join(out_dir, "luts")
    os.makedirs(luts, exist_ok=True)
    _write_cube(os.path.join(luts, "eft_grade.cube"), load_grade_lut(lut_bin))

    cfg_path = os.path.join(out_dir, "config.ocio")
    cfg = open(cfg_path, encoding="utf-8").read()
    if CS_NAME in cfg:
        raise SystemExit("config already contains %r" % CS_NAME)

    # one View per display, inserted after that display's first entry so it sits near the top
    out, added = [], 0
    for line in cfg.splitlines(True):
        out.append(line)
        if line.lstrip().startswith("- !<View> {name: Standard,") and line.startswith("    "):
            indent = line[:len(line) - len(line.lstrip())]
            out.append("%s- !<View> {name: %s, colorspace: %s}\n" % (indent, CS_NAME, CS_NAME))
            added += 1
    cfg = "".join(out)
    if not added:
        raise SystemExit("found no 'Standard' views to anchor to; config layout changed")

    # THE ONE THAT BITES. `active_views:` is an ALLOWLIST, not a display order hint: OCIO drops
    # every view whose name is not in it, silently and without a validation error. Blender's config
    # ships one, so a perfectly valid View + ColorSpace pair is simply never offered and setting
    # view_transform fails with "enum not found". Nothing else needs to change - the colorspace was
    # always fine.
    out, listed = [], False
    for line in cfg.splitlines(True):
        if line.startswith("active_views:") and CS_NAME not in line:
            head, _, rest = line.partition("[")
            line = "%s[%s, %s" % (head, CS_NAME, rest)
            listed = True
        out.append(line)
    cfg = "".join(out)
    # No active_views: at all is fine and means "every view is active" - only a PRESENT list needs
    # editing. Say which happened, because "it silently didn't appear" is the failure we just fixed.
    print("[ocio] active_views %s" % ("+= %r" % CS_NAME if listed else "absent (all views active)"))

    # the ColorSpace itself, before the looks: section (end of the colorspaces list)
    anchor = "\nlooks:"
    if anchor not in cfg:
        raise SystemExit("no looks: section to insert before; config layout changed")
    block = COLORSPACE_BLOCK.format(name=CS_NAME, ref=ref, lin=lin, srgb=srgb, smax=SHAPER_MAX)
    cfg = cfg.replace(anchor, "\n" + block + anchor, 1)
    open(cfg_path, "w", encoding="utf-8").write(cfg)
    return cfg_path, added


def selfcheck(cfg_path, lut_bin, display="sRGB"):
    """Load the config the way OCIO will and compare it against eft_grade.py.

    Worth the twenty lines. Every failure this file has had was SILENT: a view missing from
    `active_views` is dropped without a word, and a mistyped transform key ("minInValue" is the
    Python spelling, "min_in_value" the YAML one) is a warning on stderr that still yields a config
    which loads and validates and only throws when something finally asks for a processor. Neither
    shows up until Blender refuses the enum. So: build the processor, and check the numbers.

    Needs `pip install opencolorio`; skipped with a message if it is not installed.
    """
    try:
        import PyOpenColorIO as ocio
    except ImportError:
        print("[ocio] selfcheck SKIPPED (pip install opencolorio to enable)")
        return None

    cfg = ocio.Config.CreateFromFile(cfg_path)
    cfg.validate()
    if CS_NAME not in list(cfg.getViews(display)):
        raise SystemExit("selfcheck: %r is not an active view on %r - active_views filtered it out"
                         % (CS_NAME, display))

    proc = cfg.getProcessor(ocio.DisplayViewTransform(
        src="Linear Rec.709", display=display, view=CS_NAME)).getDefaultCPUProcessor()

    rng = np.random.default_rng(0)
    lin = (rng.random((60000, 3), np.float32) * 6.0).astype(np.float32)
    got = lin.copy()
    proc.applyRGB(got)
    from eft_grade import apply_grade
    ref = apply_grade(lin, load_grade_lut(lut_bin), exposure=1.0, vignette=False)

    err = float(np.abs(got.astype(np.float64) - ref.astype(np.float64)).max())
    if err > 1.0 / 255.0:
        raise SystemExit("selfcheck: max error %.5f vs eft_grade.py exceeds one 8-bit step" % err)
    print("[ocio] selfcheck  max error vs eft_grade.py %.6f (%.3f of an 8-bit step)"
          % (err, err * 255.0))
    return err


def _find_blender_cm():
    roots = [r"C:\Program Files\Blender Foundation", "/usr/share/blender", "/Applications"]
    hits = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if "config.ocio" in filenames and dirpath.endswith("colormanagement"):
                hits.append(dirpath)
            if dirpath.count(os.sep) - root.count(os.sep) > 4:
                dirnames[:] = []
    return sorted(hits)[-1] if hits else None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", default=None, help="Blender's colormanagement dir (auto-detected)")
    ap.add_argument("--out", required=True, help="where to write the patched config")
    ap.add_argument("--lut", default="packs/shared/grade_lut.bin")
    a = ap.parse_args(argv)
    src = a.src or _find_blender_cm()
    if not src:
        raise SystemExit("could not find Blender's colormanagement dir; pass --src")
    cfg, n = build(src, a.out, a.lut)
    print("[ocio] source     %s" % src)
    print("[ocio] wrote      %s" % cfg)
    print("[ocio] added view %r to %d display(s)" % (CS_NAME, n))
    selfcheck(cfg, a.lut)
    print()
    print("Use it:  set OCIO=%s" % cfg)
    print("Then in Blender: Render Properties > Color Management > View Transform = %r," % CS_NAME)
    print("and Exposure = 0.433 to match the viewer's 1.35.")


if __name__ == "__main__":
    main(sys.argv[1:])
