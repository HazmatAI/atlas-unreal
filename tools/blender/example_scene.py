"""End-to-end example: a map region, terrain, grass, and a character walking a real patrol.

Run inside a live Blender session (5.x):

    exec(open(r"<repo>/tools/blender/example_scene.py").read())

or from the command line:

    blender --python tools/blender/example_scene.py

Everything it does is documented in docs/extraction/blender-import.md. The point of this file is
to show the ORDER and the handful of decisions that are easy to get wrong, not to be configurable:
edit the constants at the top and re-run.

What it builds:
  * the map around a patrol, at LOD0, with textures
  * terrain rebuilt as the real MicroSplat splat rather than the soft baked slice
  * the grass field, distance-limited the way the viewer culls it, waving on the pack's own wind
  * the scav, its rifle, and a walk along one of the game's own patrol_ways
  * a camera that follows him, the game's sky as the world, and a bounded atmosphere
  * Cycles on the GPU

TWO MODES, ONE BUILDER. `MODE` selects which of the repo's two documented and deliberately
conflicting goals this run is serving:

    MODE = "game"       docs/extraction/game-parity.md. The viewer is the authority. Fitted sun
                        and sky, the pack's own cubemap, every material as the viewer reads it,
                        finished with the game's grade LUT through tools/blender/eft_grade.py.
    MODE = "photoreal"  docs/extraction/photorealism.md. Departs from the viewer wherever a
                        photograph would, and says so at every departure.

Geometry, UVs, nav routing, the camera solve and the decal lift are IDENTICAL in both - there is
nothing photographic about where a wall is. Everything the modes disagree about is collected in
the two tables below so the diff between the images is readable as a diff between two dicts.

WHAT PHOTOREAL MODE DOES *NOT* DO, because it was measured and did not pay:
  * a physical Nishita sky. It delivers 131.4 W/m^2 against the pack's total 10.42, i.e. 12.6x,
    and 59.6% of the frame then clips against the parity path's 0.611%. The measured "12x dynamic
    range" of the earlier photoreal test frame is mostly BRIGHTNESS: normalised by each frame's
    own median it is 12.1 against 7.3, only 1.66x more range, with 60.4% of the frame blown.
  * rebuilding the 8-bit pack sky as an HDR environment with a real solar disc. Energy-matched to
    0.01%, it moved the render by a uniform -3.4 to -4.3% at every percentile, which is exactly
    the disc's solid-angle quantization and not a lighting change. The sun LAMP already supplies
    the specular energy and Cycles shows sun lamps in glossy reflections. It also cost +37% seed
    to seed noise, because a 0.526 degree disc in an importance map samples worse than a lamp.
  * foliage translucency. Cycles already shades a backfacing double-sided leaf with the flipped
    normal, so foliage-pixel mean luminance moved +0.5% and the A/B crop is indistinguishable.
  * more grass. grass.bin's 3,261,251 clumps are 96.6% of Unity's own authored detail grids, so
    multiplying density is invention, not restoration.
  * lens distortion. Blender's Distortion socket is clamped to barrel only, and the ~1% a named
    lens shows is lens-specific, i.e. invented, and costs a full-frame resample.
"""

import json
import math
import os
import sys

import bpy
import mathutils

# ---------------------------------------------------------------------------------------------
# EDIT THESE
# ---------------------------------------------------------------------------------------------
# The repo root, derived from this file's own location. When the script is pasted into Blender's
# text editor rather than exec'd from disk there is no __file__, so set EFT_REPO instead.
REPO = (os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if "__file__" in globals() else os.environ.get("EFT_REPO", os.getcwd()))
PACK = os.path.join(REPO, "packs", "interchange.eftpack")
DATASET = os.path.join(REPO, "eft_assets", "interchange_v2")
CHARACTER = os.path.join(REPO, "out", "characters", "scav")
WEAPON = os.path.join(REPO, "out", "weapons", "weapon_colt_m4a1_556x45")
SKY_EQUIRECT = os.path.join(REPO, "packs", "shared", "sky", "NatureCubemap_equirect.png")
# Cavity maps baked from the pack's normal maps by tools/blender/bake_cavity.py. Photoreal only,
# and an absent or partial directory is not an error - the importer skips what it cannot find.
CAVITY_DIR = os.path.join(REPO, "out", "cavity")

MODE = "game"                      # "game" or "photoreal"; see the module docstring

# THE STAGING. ZoneBearCamp 3..5 is a 24.6 m walk out of the forest past two truck cabs and the
# camp building. It was chosen against the four things that decide whether any of the lighting
# work below is visible at all, all measured on the pack:
#   varied ground   5 terrain layers under the filmed 19.2 m (Gravel_Road_B 29%, Soil_Grass 22%,
#                   Grassy_Ground 20%, Forest_Ground 15%, Grass 9%) and 1.96 m of climb, so the
#                   camera changes height instead of dollying across a plane
#   occlusion       44 LOD groups over 4 m within 14 m of the walk, one of them 39.5 m across, so
#                   the solver's rear-left camera has trunks and a wall to pass behind
#   scale           471 instances within 22 m: Ural-280 and Kamaz-4310 cabs, sandbags, pallets
#   light           the filmed heading is 42 deg off the sun azimuth and cine_camera's PREF_AZ is
#                   +118 deg, so the camera-to-subject bearing puts the backlight factor at +0.94
# It is also a LIGHTER build than the old staging: MAP_RADIUS 170 imports 3,766 instances against
# the previous staging's 150 m / 4,484.
# (The line that used to sit here claimed 170 "clears terrain tile Slice_2_2's 159.0 m centre by
# 11 m at 3,528 instances". Neither number is reproducible and the reasoning behind them was the
# bug: the tile's true centroid is 163.3 m away, the sampled centre the importer actually used
# reported 185.5 m, and MAP_RADIUS 170 clears NEITHER - it culled the tile the camera stands on
# until _select started testing the mesh's world AABB, whose distance to this route is 0.0 m
# because the route is INSIDE the tile. A radius is not a clearance over an object's middle.)
# The previous staging, kept because every measurement in game-parity.md was taken on it:
#     PATROL_ZONE, PATROL_SPAN, MAP_RADIUS, GRASS_RADIUS = "ZonePowerStation", (0, 6), 150.0, 26.0
PATROL_ZONE = "ZoneBearCamp"       # a gamedata.json patrol_ways zone
PATROL_SPAN = (3, 5)               # waypoint slice; the ways are NETWORKS, not ordered paths
CLIP = "walk_aim_slow_0"           # a LOOPING clip with forward root motion
SHOT_SECONDS = 12.0                # how much of the patrol to film (the walk itself stays whole)
MAP_RADIUS = 170.0                 # metres of map to build around the route
GRASS_RADIUS = 40.0                # the viewer culls grass by screen size; a static build needs this
SAMPLES = 96

HORIZ_MIN = 0.5                    # walk_ground.rs: a face is GROUND when its normal.y/|n| > 0.5
STEP_UP = 0.5                      # walk_ground.rs: how far the feet may rise to select a surface

# Sun and sky are the two numbers this scene cannot read out of the pack: it ships NO directional
# light (the game's outdoor lighting lives in the baked SH volume), so there is nothing to copy.
# They were therefore SOLVED against the viewer rather than eyeballed. Cycles is linear in every
# light's power, so for a fixed camera
#     render(sun=a, sky=b) == a * render(sun=1, sky=0) + b * render(sun=0, sky=1)
# exactly. Two basis renders span the whole space, and the pair below is the least-squares fit of
# that combination - pushed through the game's own grade LUT at the viewer's exposure 1.35 - to an
# Atlas frame of the identical camera. Fitting on lit surfaces cut RMS error 16% versus the values
# originally guessed here (6.0 / 1.6), and moved the sun:sky ratio from 3.75 to 2.94: the guess had
# too much direct sun and too little sky, which is what made sunlit ground blow out while shaded
# faces went muddy under an overcast sky it was inconsistent with.
# Re-solve with tools/blender/eft_grade.py + a basis pair whenever the sky or map changes.
SUN_ENERGY = 6.90
SKY_STRENGTH = 2.35

# ---------------------------------------------------------------------------------------------
# THE ONLY THINGS THE TWO MODES DISAGREE ABOUT
# ---------------------------------------------------------------------------------------------
# Every entry is either derived from the pack, or invented and labelled. Read the two dicts side
# by side: that IS the difference between the two images.
MODES = {
    "game": dict(
        # The traced haze box. Nothing in the pack supports it - gamedata/particles/volume/sky/
        # manifest were grepped for fog|haze|atmos|scatter|mist|density and returned zero hits, and
        # volume.json is the SH irradiance bake ("direct": false), not a medium. But SUN_ENERGY and
        # SKY_STRENGTH were least-squares fitted against an Atlas frame WITH this box in place, so
        # on the parity path it stays exactly as fitted. Removing it here without re-solving that
        # pair would drop the median 16%.
        atmosphere="uniform",
        haze_post=False,           # and TRACED, because that is what the pair was fitted against
        glass_mode="trs",          # the bounded legacy reflection, term for term
        cavity=False,              # the game shader has no ambient-occlusion term at all
        comp=False,                # no glare, no chromatic aberration: the viewer has neither
        aperture_blades=0,
        samples=SAMPLES,
    ),
    "photoreal": dict(
        # HEIGHT FALLOFF instead of a uniform slab, and 2.5x thinner at the ground. A constant
        # density veils near and far equally, which is fog; aerial perspective needs the extinction
        # to build with distance, and a uniform box only approximates that when it is much larger
        # than the scene. Measured on the old staging, disabling the box entirely took the frame's
        # p99.9/median range from 7.3 to 9.1 (+25%) and the render from 129.9 s to 73.2 s (-44%):
        # this is the single biggest lever on the whole photoreal path, and it BUYS time.
        # The numbers are invented in the same sense the 0.0016 they replace was - the pack ships
        # no atmosphere - but 4e-4 with a 40 m scale height is the aerosol profile a real 150 m
        # vista shows, where a flat 0.0016 is ~130x sea-level Rayleigh applied uniformly.
        atmosphere="falloff",
        # ...and then do NOT path-trace it. Thinning the box was only half the win: measured on
        # this staging at 1280x720, tracing the thinned box still costs HALF the frame (see
        # HAZE_SIGMA below for the numbers), and every one of those seconds is spent marching
        # single scatter through the whole scene BVH to produce something a depth-driven
        # exponential reproduces to 1.5x the path tracer's own noise. The box is still built, sized
        # and shaped by HAZE_DENSITY/HAZE_SCALE_H, and still visible in the viewport; it is
        # hide_render'd and applied analytically from the Z pass instead. See _depth_haze_nodes.
        haze_post=True,
        glass_mode="physical",     # real transmission on panes the pack already ships as slabs
        cavity=True,               # the normal maps' own self-occlusion, Poisson-derived
        comp=True,                 # veiling glare + lateral CA, calibrated below
        aperture_blades=9,         # a 50 mm at f/2.8 is stopped down; no iris is a perfect circle
        # Spend what that frees on samples rather than on wall clock, which is the whole point of
        # not tracing the box. Noise falls as 1/sqrt(spp) and that law was confirmed to 1% here.
        # The multiplier is not a preference: at 172 spp the frame costs 91.2 s traced and 45.7 s
        # untraced, and the untraced frame's marginal cost is 0.2034 s/spp (172 spp 45.7 s, 345
        # spp 80.9 s), so 4.0x SAMPLES = 384 spp predicts ~88.9 s and measured 87.8 s against the
        # traced frame's 92.1 s. Anything less than this leaves measured time on the table.
        samples=int(SAMPLES * 4.0),
    ),
}

# Ground extinction and scale height for the photoreal atmosphere. See MODES above.
HAZE_DENSITY = 4.0e-4
HAZE_SCALE_H = 40.0
ATM_LIFT = 30.0        # box centre above the route, metres
ATM_HEIGHT = 80.0      # box height, metres; so it spans route-10 m .. route+70 m

# THE ANALYTIC STAND-IN for that box on the photoreal path, evaluated per pixel from the Z pass:
#     out_c = in_c * exp(-sigma_c * z) + L_c * (1 - exp(-sigma_c * z))
# with z in metres, clamped to the box's own half-width because that is the longest crossing a
# camera near its centre can have. sigma and L are NOT authored numbers. They are a least-squares
# fit of that model to THIS SCENE'S OWN traced falloff volume: two renders of the same frame and
# seed, one with the box in the beauty pass and one without, plus the Z pass, then a scan over
# sigma with L solved in closed form at each step. So the replacement is a model of the exact
# thing it replaces, not a second invention on top of the first.
# They are also properties of the VOLUME rather than of this staging, which is the only reason it
# is safe to write them down as constants: refitting on a second build (MAP_RADIUS 260, 7050
# instances instead of 3706, terrain present, clamp 286 m instead of 187 m) moved sigma by 6 to 8%
# and L by under 2%. Refit if HAZE_DENSITY or HAZE_SCALE_H move, not if the shot does.
# Fit residual and what it buys are in _depth_haze_nodes' docstring.
HAZE_SIGMA = (2.549e-4, 2.525e-4, 2.623e-4)     # per-channel extinction, 1/m
HAZE_INSCATTER = (1.2491, 1.3298, 1.3951)       # in-scatter colour, linear, scene-referred
HAZE_ZMAX = MAP_RADIUS * 1.1                    # the box is MAP_RADIUS * 2.2 across; see step 6

# COMPOSITOR, photoreal only, and every number here is calibrated rather than chosen.
# Veiling glare is a whole-image PSF convolution at THRESHOLD 0, because that is what a lens does:
# it scatters a fixed fraction of all light into wide tails, it does not run a threshold. At
# strength 0.015 it adds 1.43% of total frame energy, inside the 0.5-2% a real prime scatters.
# Lateral CA: the calibration is shift_px = 275 * Dispersion at r = 1216 px on a 2560-wide frame,
# linear to about 0.002. A good 50 mm shows 1-1.5 px at 6000 px wide, i.e. ~0.5 px here, so 0.0012.
# (A first guess of 0.004 produced +110/-91 CV swings - four times too strong.)
GLARE_STRENGTH = 0.015
CA_DISPERSION = 0.0012
# NOT included: a threshold Bloom node. It would have to be re-derived from the exposure every
# time exposure moves, which bakes a grading decision into the linear EXR and stops that file
# being scene-referred. Exposure is solved later, in eft_grade.py, where it belongs.


def _load(script):
    p = os.path.join(REPO, "tools", "blender", script)
    g = {"__name__": os.path.splitext(script)[0], "__file__": p}
    exec(compile(open(p, encoding="utf-8").read(), p, "exec"), g)
    return g


def _clear():
    for c in list(bpy.data.collections):
        for o in list(c.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(c)
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)


def _patrol(pack, zone, span):
    """The game's waypoints, ROUTED on the baked nav grid, pack Y-up -> Blender Z-up.

    The waypoints alone are not a path. A patrol_way is a NETWORK: consecutive entries are not
    guaranteed to be mutually visible, and a straight line between two of them walks the character
    through whatever stands in between (on this route, an 11.7 m tanker). Routing each leg on the
    same grid the viewer navigates gives a path a bot could actually walk: around obstacles, up
    stairs, through doors, and never off a ledge it could not drop.
    """
    gd = json.load(open(os.path.join(pack, "gamedata.json"), encoding="utf-8"))
    way = next(w for w in gd["patrol_ways"] if str(w.get("zone")) == zone)
    raw = [tuple(p) for p in way["points"][span[0]:span[1]]]
    if len(raw) < 2:
        raise SystemExit("patrol %r has fewer than 2 usable points" % zone)
    nav = _load("nav_route.py")["NavGrid"](pack)
    routed = nav.route_through(raw)
    if len(routed) < 2:
        raise SystemExit("patrol %r would not route on the nav grid" % zone)
    return [mathutils.Vector((p[0], -p[2], p[1])) for p in routed]


def _ground_height(scene, dg, arm, x, y, feet_z):
    """Atlas's rule: the GREATEST walkable surface at (x, y) that is <= feet_z + STEP_UP.

    Cast DOWN from the cap, never from the sky: a sky-down ray stops on bush canopies and
    container roofs, which is how a character ends up standing in mid-air.
    """
    org = mathutils.Vector((x, y, feet_z + STEP_UP))
    d = mathutils.Vector((0, 0, -1))
    for _ in range(16):
        hit, loc, nrm, _i, obj, _m = scene.ray_cast(dg, org, d, distance=60.0)
        if not hit:
            return None
        foliage = any(ms.material and ms.material.name.endswith(".cutout")
                      for ms in obj.material_slots)
        if (nrm.z > HORIZ_MIN and not obj.name.startswith("grass_kind")
                and obj.parent is not arm and not foliage):
            return loc.z
        org = loc + d * 0.02
    return None


def enable_cycles_gpu():
    """Pick a GPU backend and enable its devices.

    This lives in the ADDON PREFERENCES, not in the .blend. `scene.cycles.device = 'GPU'` is saved
    with the file, but the list of enabled devices is not, so a `--factory-startup` run that opens
    an existing scene silently renders on the CPU at a tenth of the speed. Any script that renders
    without calling build() has to call this itself.
    """
    prefs = bpy.context.preferences.addons.get("cycles")
    if not prefs:
        print("[cycles] no cycles addon; leaving the device alone")
        return None
    cp = prefs.preferences
    chosen = None
    for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            cp.compute_device_type = backend
            cp.get_devices()
            if any(d.type == backend for d in cp.devices):
                chosen = backend
                break
        except Exception:
            continue
    on = []
    for d in cp.devices:
        d.use = (d.type != 'CPU')
        if d.use:
            on.append(d.name)
    print("[cycles] backend %s, %d device(s) enabled: %s"
          % (chosen or "NONE (CPU!)", len(on), ", ".join(on) or "none"))
    return chosen


def _atmosphere_nodes(ant, mode, h_lo, h_hi):
    """The bounded haze volume, in whichever of the two shapes the mode asks for.

    "uniform"  the fitted parity slab: one constant density, exactly what SUN_ENERGY and
               SKY_STRENGTH were solved against.
    "falloff"  exponential in height, exp(-(z - ground) / H). Real aerosol is stratified, and a
               constant slab veils a wall 5 m away as hard as a treeline at 150 m, which is what
               made the parity frame milky (p99.9/median 7.3 against 9.1 with no volume at all).
    """
    ao = ant.nodes.new("ShaderNodeOutputMaterial"); ao.location = (300, 0)
    vs = ant.nodes.new("ShaderNodeVolumeScatter"); vs.location = (100, 0)
    vs.inputs["Anisotropy"].default_value = 0.6
    if mode != "falloff":
        vs.inputs["Density"].default_value = 0.0016
    else:
        # HEIGHT ABOVE THE ROUTE, in metres. Object coordinates run [-0.5, 0.5] over a cube built
        # at size 1.0 whatever the object's scale is, so Map Range converts them back to the metres
        # the caller placed the box at, and MAXIMUM floors the profile at the ground: below it
        # exp(-h/H) climbs above 1 and would put MORE haze under the terrain than over it.
        tc = ant.nodes.new("ShaderNodeTexCoord"); tc.location = (-900, 0)
        sep = ant.nodes.new("ShaderNodeSeparateXYZ"); sep.location = (-720, 0)
        ant.links.new(tc.outputs["Object"], sep.inputs["Vector"])
        mr = ant.nodes.new("ShaderNodeMapRange"); mr.location = (-540, 0)
        mr.inputs["From Min"].default_value = -0.5
        mr.inputs["From Max"].default_value = 0.5
        mr.inputs["To Min"].default_value = float(h_lo)
        mr.inputs["To Max"].default_value = float(h_hi)
        ant.links.new(sep.outputs["Z"], mr.inputs["Value"])
        fl = ant.nodes.new("ShaderNodeMath"); fl.location = (-380, 0)
        fl.operation = 'MAXIMUM'; fl.inputs[1].default_value = 0.0
        ant.links.new(mr.outputs["Result"], fl.inputs[0])
        neg = ant.nodes.new("ShaderNodeMath"); neg.location = (-260, 0)
        neg.operation = 'MULTIPLY'; neg.inputs[1].default_value = -1.0 / HAZE_SCALE_H
        ant.links.new(fl.outputs[0], neg.inputs[0])
        ex = ant.nodes.new("ShaderNodeMath"); ex.location = (-100, 0)
        ex.operation = 'MULTIPLY_ADD'
        ex.inputs[1].default_value = HAZE_DENSITY
        ex.inputs[2].default_value = 0.0
        exp = ant.nodes.new("ShaderNodeMath"); exp.location = (-180, -180)
        exp.operation = 'EXPONENT'
        ant.links.new(neg.outputs[0], exp.inputs[0])
        ant.links.new(exp.outputs[0], ex.inputs[0])
        ant.links.new(ex.outputs[0], vs.inputs["Density"])
    ant.links.new(vs.outputs["Volume"], ao.inputs["Volume"])


def _depth_haze_nodes(ng, rl, src):
    """The traced atmosphere as six multiply-adds on the Z pass. Returns the new image socket.

        out_c = in_c * exp(-sigma_c * z) + L_c * (1 - exp(-sigma_c * z))

    written as (in_c - L_c) * T_c + L_c so each channel is one MULTIPLY_ADD. It goes BEFORE the
    glare, because the haze is in the scene and the glare is in the lens.

    WHY THIS EXISTS. Path-tracing the box was measured at 45.5 s of a 91.2 s frame (49.9%) on this
    staging at 1280x720, and volume_bounces=0 was already known to save only 5.5%: the cost is the
    single-scatter march through the scene BVH, not the extra bounce. Against the traced
    box on the same frame and seed, deleting it outright is an rms error of 0.01047 and drops the
    frame mean 7.0%; this model brings the error to 0.00515 and the mean to within 2.2%, against a
    two-seed path-trace noise floor of 0.00338. So what is left is 1.5x noise, and it is not a fit
    artifact: scored against a held-out second seed the same constants give 0.00508. The error is
    concentrated exactly where there is the least of it, by depth shell (model vs delete):
    0-25 m, 84% of the frame, 0.00269 vs 0.00307; 25-50 m 0.00638 vs 0.01114; 50-100 m 0.00741 vs
    0.01720; past 100 m, 3% of the frame, 0.02225 vs 0.05216.

    END TO END, the two builds A/B'd at 1280x720 with the full compositor on both, two seeds each,
    interleaved in one session on an idle GPU: traced box at 172 spp is 92.1 s, rel-sigma 0.0634,
    bright quartile 0.0376; this at 384 spp is 87.8 s, rel-sigma 0.0564, bright quartile 0.0339.
    So 4.7% LESS wall clock for 1.12x less noise overall and 1.11x in the highlights, with the
    frame mean moved +2.2% and rms 0.00532 against the image it replaces. The gain is smaller than
    1/sqrt(spp) predicts because OIDN is already doing most of that work; what the extra samples
    buy is what the denoiser was inventing.

    WHAT IT CANNOT DO, since the fit cannot invent what it never saw: the model is isotropic, so
    the box's anisotropy 0.6 forward-scatter halo around the sun and any shafts through the canopy
    are gone. On this shot that lives in the 3% of pixels past 100 m. A vista shot, or the sun in
    frame, wants the traced box back (set haze_post False) rather than this.

    5.1 TRAP, and it fails loudly at node creation rather than silently at render, which is a
    mercy: there is no CompositorNodeMath in this build. `nodes.new("CompositorNodeMath")` raises
    "Node type undefined". The compositor takes the unified ShaderNodeMath now, while Separate and
    Combine Color are still CompositorNode*. The other half of the trap is silent: Render Layers
    grows its "Depth" output only once view_layer.use_pass_z is True, so the pass has to be
    enabled before this runs or `outputs.get("Depth")` is None.
    """
    zs = rl.outputs.get("Depth") or rl.outputs.get("Z")
    if zs is None:
        print("[example] Render Layers has no Depth output; skipping the depth haze")
        return src

    # Sky and any ray that leaves the box come back at 1e10, so clamp to the longest crossing the
    # box actually has. Without this the exponential saturates and the sky is painted flat L.
    zc = ng.nodes.new("ShaderNodeMath"); zc.location = (-300, -320)
    zc.operation = 'MINIMUM'; zc.inputs[1].default_value = float(HAZE_ZMAX)
    ng.links.new(zs, zc.inputs[0])

    # Per channel and explicit rather than one vector op: the fit is per channel, and a Color fed
    # into a Vector socket would go through an implicit conversion that drops alpha.
    sep = ng.nodes.new("CompositorNodeSeparateColor"); sep.location = (-300, 180)
    ng.links.new(src, sep.inputs["Image"])
    comb = ng.nodes.new("CompositorNodeCombineColor"); comb.location = (30, 180)
    ng.links.new(sep.outputs["Alpha"], comb.inputs["Alpha"])
    for i, ch in enumerate(("Red", "Green", "Blue")):
        mz = ng.nodes.new("ShaderNodeMath"); mz.location = (-190, -400 - i * 130)
        mz.operation = 'MULTIPLY'; mz.inputs[1].default_value = -float(HAZE_SIGMA[i])
        ng.links.new(zc.outputs[0], mz.inputs[0])
        tr = ng.nodes.new("ShaderNodeMath"); tr.location = (-60, -400 - i * 130)
        tr.operation = 'EXPONENT'                       # T_c = exp(-sigma_c * z)
        ng.links.new(mz.outputs[0], tr.inputs[0])
        df = ng.nodes.new("ShaderNodeMath"); df.location = (-190, 120 - i * 130)
        df.operation = 'SUBTRACT'; df.inputs[1].default_value = float(HAZE_INSCATTER[i])
        ng.links.new(sep.outputs[ch], df.inputs[0])
        ma = ng.nodes.new("ShaderNodeMath"); ma.location = (-60, 120 - i * 130)
        ma.operation = 'MULTIPLY_ADD'; ma.inputs[2].default_value = float(HAZE_INSCATTER[i])
        ng.links.new(df.outputs[0], ma.inputs[0])
        ng.links.new(tr.outputs[0], ma.inputs[1])
        ng.links.new(ma.outputs[0], comb.inputs[ch])
    return comb.outputs["Image"]


def _compositor(scene, haze=False):
    """Veiling glare and lateral CA, photoreal only. Returns the node group, or None.

    THE 5.1 API MOVED, three times, and every trap is silent. `scene.node_tree` is gone and so is
    CompositorNodeComposite (hasattr is False): the pointer is now `scene.compositing_node_group`
    and it takes a CompositorNodeTree with a Group Input and a Group Output. Second, Glare's and
    Lens Distortion's parameters are INPUT SOCKETS now, not RNA properties - `node.glare_type`
    does not exist, it is `node.inputs["Type"]` on a menu socket that takes TITLE-CASE strings
    ('Bloom', 'Ghosts', 'Streaks', 'Fog Glow', 'Simple Star'). 'FOG_GLOW' raises.

    THIRD, AND IT COSTS A WHOLE FRAME: the group's INPUT SOCKET IS NOT THE RENDER RESULT. The
    beauty pass has to be pulled in by a CompositorNodeRLayers node INSIDE the group. Feed the
    chain from the Group Input instead and the render is not merely uncomposited - it never runs:
    measured at 480x270, `Group Input -> Group Output` returned in 0.03 s with every pixel exactly
    0.0, against 6.26 s and a correct frame for `Render Layers -> Group Output` (identical to the
    no-compositor render to 6 decimal places, mean 0.084657). With the full chain the frame comes
    back at mean 0.08588, i.e. the Fog Glow adds 1.4% of total energy, which is the number the
    veiling-glare calibration predicts. Nothing warns; MODE='photoreal' just writes a black EXR.

    compositor_device stays on CPU deliberately: OptiX already owns both GPUs for the path trace,
    and Fog Glow measured 0.26 s on the CPU against 0.50-0.74 s on the GPU. The whole chain is
    +0.24 s on a 34.8 s frame, i.e. +0.7%.
    """
    if not hasattr(scene, "compositing_node_group"):
        print("[example] no scene.compositing_node_group on this build; skipping comp")
        return None
    ng = bpy.data.node_groups.new("eft_photoreal_comp", "CompositorNodeTree")
    ng.interface.new_socket("Image", in_out='INPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    ng.nodes.new("NodeGroupInput").location = (-620, -220)   # unused; see the docstring
    go = ng.nodes.new("NodeGroupOutput"); go.location = (400, 0)
    rl = ng.nodes.new("CompositorNodeRLayers"); rl.location = (-400, 0)
    rl.scene = scene                                 # the beauty pass; the group input is NOT it

    src = rl.outputs["Image"]
    if haze:
        src = _depth_haze_nodes(ng, rl, src)      # the atmosphere, before the lens sees it

    gl = ng.nodes.new("CompositorNodeGlare"); gl.location = (-150, 0)
    gl.inputs["Type"].default_value = "Fog Glow"
    gl.inputs["Quality"].default_value = "High"
    gl.inputs["Threshold"].default_value = 0.0       # a lens has no threshold; see the docstring
    gl.inputs["Strength"].default_value = GLARE_STRENGTH
    gl.inputs["Size"].default_value = 1.0
    ng.links.new(src, gl.inputs["Image"])

    ca = ng.nodes.new("CompositorNodeLensdist"); ca.location = (120, 0)
    ca.inputs["Type"].default_value = "Radial"
    ca.inputs["Distortion"].default_value = 0.0      # barrel only in 5.1, and it is invented
    ca.inputs["Dispersion"].default_value = CA_DISPERSION
    ng.links.new(gl.outputs["Image"], ca.inputs["Image"])
    ng.links.new(ca.outputs["Image"], go.inputs[0])

    scene.compositing_node_group = ng
    _try(scene.render, "compositor_device", 'CPU')
    return ng


def _try(obj, attr, value):
    try:
        setattr(obj, attr, value)
    except Exception:
        pass


def build(mode=None):
    mode = (mode or MODE).lower()
    if mode not in MODES:
        raise SystemExit("MODE must be one of %s, got %r" % (sorted(MODES), mode))
    cfg = MODES[mode]
    print("[example] MODE = %s  %s" % (mode, cfg))

    scene = bpy.context.scene
    _clear()

    pts = _patrol(PACK, PATROL_ZONE, PATROL_SPAN)
    centre_bl = sum(pts, mathutils.Vector()) / len(pts)
    centre_pack = (centre_bl.x, centre_bl.z, -centre_bl.y)          # back to pack Y-up
    route_pack = [(p.x, p.z, -p.y) for p in pts]                    # the routed walk, pack Y-up

    # 1. map -------------------------------------------------------------------------------
    gm = _load("import_eftpack.py")
    for c in list(bpy.data.collections):                            # its demo import
        if c.name.startswith("eftpack_"):
            for o in list(c.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            bpy.data.collections.remove(c)
    mapres = gm["import_eftpack"](PACK, center=centre_pack, radius=MAP_RADIUS,
                                  with_textures=True, collection_name="map",
                                  glass_mode=cfg["glass_mode"],
                                  cavity_dir=(CAVITY_DIR if cfg["cavity"]
                                              and os.path.isdir(CAVITY_DIR) else None))

    # 2. terrain: the real splat, not the 5.9 texel/m baked slice --------------------------
    _load("terrain_splat.py")["apply_to_scene"](DATASET)

    # 3. grass, distance-limited along the ROUTE ---------------------------------------------
    # A capsule about the routed polyline, not a disc about its centroid: on a 24.6 m walk the
    # actor spends most of the shot outside a centroid disc, so a disc puts the grass where the
    # camera is not. The viewer culls grass by projected screen size, so any static radius is an
    # approximation already and following the route is strictly the closer one.
    # `wind` is not a departure - it is the game's own WavingGrass stage with the sidecar's own
    # constants (strength 1.0, amount 0.157, speed 1.0), which this path used to discard. Without
    # it the field is frozen for all 360 frames while the scav walks through it, and the sway
    # moves half the vertices by a mean of 0.15 m on a 0.9 m blade.
    grass = _load("import_eftgrass.py")["import_eftgrass"](
        PACK, points=route_pack, radius=GRASS_RADIUS, max_clumps=120000, wind=True)
    for o in grass:
        o.visible_shadow = False        # the viewer keeps grass out of the shadow pass

    # 3b. practical lights ------------------------------------------------------------------
    # The sun lights the outdoors and nothing else. Without these every interior is black, and
    # a lit room seen through a window collapses to a flat dark rectangle that looks like a
    # broken glass material. Parented to the map root, so positions stay in pack space.
    _load("import_eftlights.py")["import_eftlights"](
        PACK, parent=mapres["empty"], center=centre_pack, radius=MAP_RADIUS)

    # 4. character + weapon -----------------------------------------------------------------
    coll = bpy.data.collections.new("actor"); scene.collection.children.link(coll)
    man = json.load(open(os.path.join(CHARACTER, "manifest.json"), encoding="utf-8"))
    clip = next(c for c in man["clips"] if c["name"] == CLIP)
    speed = abs(clip["averageSpeed"][2])
    fps = int(round(clip["sampleRate"])); scene.render.fps = fps
    seg = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
    dur = sum(seg) / speed
    res = _load("import_eftchar.py")["import_eftchar"](
        CHARACTER, clip_name=CLIP, loops=int(math.ceil(dur / clip["duration"])) + 1,
        apply_root_motion=False, collection=coll)
    arm = res[0] if isinstance(res, (tuple, list)) else res
    _load("import_eftweap.py")["import_eftweap"](WEAPON, armature=arm, bone="Weapon_root",
                                                 collection=coll)

    # 5. walk the patrol, resolving the ground the way the viewer does ----------------------
    f0, f1 = 1, 1 + int(round(dur * fps))
    dg = bpy.context.evaluated_depsgraph_get()
    arm.rotation_mode = 'XYZ'
    prev_z = pts[0].z
    def _at(s):
        """Point at arc length `s` along the routed polyline."""
        acc = 0.0
        for i, L in enumerate(seg):
            if s <= acc + L:
                return pts[i].lerp(pts[i + 1], (s - acc) / L)
            acc += L
        return pts[-1]

    for fr in range(f0, f1 + 1, 3):
        s = (fr - f0) / fps * speed
        P = _at(s)
        # Heading from a LOOKAHEAD, not from the current segment. A grid route is a chain of 0.5 m
        # steps locked to 8 directions, so a per-segment heading makes the character snap between
        # 45-degree facings every few frames. Aiming at a point ~1.5 m ahead averages the staircase
        # out without moving him off the route.
        D = _at(s + 1.5) - P
        if D.length < 1e-4:
            D = pts[-1] - pts[-2]
        D = D.normalized()
        z = _ground_height(scene, dg, arm, P.x, P.y, prev_z)
        z = P.z if z is None else z
        prev_z = z
        arm.location = (P.x, P.y, z)
        # (pi/2, 0, yaw): the X term is the Y-up -> Z-up stand-up the importer applied, and it
        # must be COMPOSED with the yaw, not replaced by it, or the character lies on its back.
        arm.rotation_euler = (math.pi / 2, 0.0, math.atan2(D.x, -D.y))
        arm.keyframe_insert("location", frame=fr)
        arm.keyframe_insert("rotation_euler", frame=fr)
    scene.frame_start, scene.frame_end = f0, f1

    # 6. world, sun, bounded atmosphere ------------------------------------------------------
    w = scene.world or bpy.data.worlds.new("World"); scene.world = w
    w.use_nodes = True; nt = w.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputWorld"); out.location = (400, 0)
    bg = nt.nodes.new("ShaderNodeBackground"); bg.location = (150, 0)
    bg.inputs["Strength"].default_value = SKY_STRENGTH
    if os.path.isfile(SKY_EQUIRECT):
        env = nt.nodes.new("ShaderNodeTexEnvironment"); env.location = (-200, 0)
        env.image = bpy.data.images.load(SKY_EQUIRECT, check_existing=True)
        nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    else:
        bg.inputs["Color"].default_value = (0.36, 0.40, 0.47, 1.0)
        print("[example] no sky equirect; run tools/blender/make_sky_equirect.py first")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    volj = os.path.join(PACK, "volume.json")
    sd = [0.449, 0.799, -0.400]
    if os.path.isfile(volj):
        sd = json.load(open(volj, encoding="utf-8")).get("sun_dir", sd)
    sun_dir = mathutils.Vector((sd[0], -sd[2], sd[1])).normalized()
    ld = bpy.data.lights.new("eft_sun", 'SUN'); ld.energy = SUN_ENERGY
    ld.angle = math.radians(0.526)              # the sun's real angular diameter
    sun = bpy.data.objects.new("eft_sun", ld); scene.collection.objects.link(sun)
    sun.rotation_euler = (-sun_dir).to_track_quat('-Z', 'Y').to_euler()

    # A Cycles WORLD volume is unbounded and renders the frame black. Bound it.
    import bmesh
    me = bpy.data.meshes.new("eft_atmosphere")
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(me); bm.free()
    atm = bpy.data.objects.new("eft_atmosphere", me); scene.collection.objects.link(atm)
    atm.location = centre_bl + mathutils.Vector((0, 0, ATM_LIFT))
    atm.scale = mathutils.Vector((MAP_RADIUS * 2.2, MAP_RADIUS * 2.2, ATM_HEIGHT))
    atm.display_type = 'WIRE'
    am = bpy.data.materials.new("eft_atmosphere"); am.use_nodes = True
    ant = am.node_tree
    for n in list(ant.nodes):
        ant.nodes.remove(n)
    _atmosphere_nodes(ant, cfg["atmosphere"],
                      ATM_LIFT - ATM_HEIGHT * 0.5, ATM_LIFT + ATM_HEIGHT * 0.5)
    me.materials.append(am)

    # PHOTOREAL: built, sized and shaded exactly as above, and then kept out of the beauty pass.
    # It stays in the .blend and in the viewport, so the shape it describes is still readable and
    # still the thing HAZE_SIGMA was fitted to; it is just not worth 49.9% of a frame to trace.
    # use_pass_z must be set HERE, not in step 8: Render Layers only grows the Depth output once
    # the pass is on, and _compositor builds that node.
    # The `and cfg["comp"]` is not belt and braces: the haze lives in the compositor group, so
    # hiding the box with the compositor off would delete the atmosphere outright and silently,
    # at a measured -7.0% on the frame mean. Hidden and unapplied is the one combination that is
    # worse than either end of the choice.
    if cfg.get("haze_post") and cfg["comp"]:
        atm.hide_render = True
        for vl in scene.view_layers:
            vl.use_pass_z = True

    # 7. following camera --------------------------------------------------------------------
    # Solved as one continuous move (see cine_camera.py): visibility of the whole body, both sides
    # of the subject, and smoothness are minimised together rather than patched frame by frame.
    # Solve the camera only over the frames actually being shot. The walk keys stay full length,
    # so the shot can be moved or extended by re-solving, but solving all of it is the single
    # slowest thing in this script and it cannot use the GPU: it is one Python thread doing
    # frame_set + ray_cast + keyframe_insert. On a 113 s patrol that is ~1.1 M visibility rays and
    # 10 k key inserts to produce a 12 s shot - about 20 minutes of work, 90% of it discarded.
    shot_end = min(f1, f0 + int(round(SHOT_SECONDS * fps)) - 1)
    cam = _load("cine_camera.py")["solve_follow_camera"](
        scene, arm, f0, shot_end, blades=cfg["aperture_blades"])
    scene.frame_end = shot_end          # keep the scene self-consistent with the solved camera

    # 8. Cycles, and the optics that live outside the shader ----------------------------------
    scene.render.engine = 'CYCLES'
    enable_cycles_gpu()
    scene.cycles.device = 'GPU'
    scene.cycles.samples = cfg["samples"]
    scene.cycles.use_denoising = True
    scene.cycles.volume_bounces = 1
    # TRANSPARENT BOUNCES, and this is not a quality knob - at the default it DELETES pixels.
    # `transparent_bounce` is a WHOLE-PATH counter, not a per-segment one: the camera segment, every
    # diffuse bounce that follows it and the shadow ray toward the sun all draw from the same
    # budget. Every leaf, blade and chain-link in this scene is alpha-tested, so a ray that grazes
    # the grass field or crosses a bush spends the budget on transparency alone - and Cycles fails
    # CLOSED. A camera ray that runs out is TERMINATED and returns black; a shadow ray that runs out
    # is reported FULLY OCCLUDED. Neither warns.
    # MEASURED on this scene's grass at the Cycles default of 8: 14.78% of the frame is EXACTLY
    # 0.0 luma and the mean is 12.2% low. 32 still leaves 0.48% black. 256 is BIT-IDENTICAL to the
    # 1024 maximum, so it is the cheapest value that is indistinguishable from unlimited, and it
    # costs 2.0 -> 2.3 s per frame. A transparent bounce does no shading; this is the least
    # expensive fidelity in the whole file.
    scene.cycles.transparent_max_bounces = 256
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    # Both modes shoot FLAT linear EXR; the look is applied afterwards by eft_grade.py, which is
    # also where the two modes' display chains part company (game LUT vs filmic + centre-weighted
    # metering + cos^4 + shot noise). Keeping the render scene-referred is what lets one set of
    # frames answer both questions.
    _try(scene, "compositing_node_group", None)
    if cfg["comp"]:
        _compositor(scene, haze=cfg.get("haze_post", False))   # step 6 hid the box for this
    scene.frame_set((f0 + f1) // 2)

    print("[example] MODE %s built: %d objects, frames %d..%d (%.1f s of walk), %d samples"
          % (mode, len(scene.objects), f0, f1, dur, cfg["samples"]))
    if mode == "game":
        print("[example] grade with: python tools/blender/eft_grade.py frames/ out/ --auto")
    else:
        print("[example] grade with: python tools/blender/eft_grade.py frames/ out/ --look agx "
              "--auto --meter grey --no-vignette --lens 50 --grain 15000")
    return arm, cam


if __name__ == "__main__":
    build(os.environ.get("EFT_MODE") or MODE)
