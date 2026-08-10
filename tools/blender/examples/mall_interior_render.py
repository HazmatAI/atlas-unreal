"""Render the mall .blend's static cameras to linear EXR, and carry the two lighting controls.

Step three of the interior example (see mall_interior_build.py for the chain). Rendering an
interior is not different from rendering a vista; what is different is that you cannot tell by
LOOKING whether you got it right, so this script also carries the two controls that answer the
question numerically. Both are off by default and both are one environment variable.

THE PRACTICALS ARE THE INTERIOR'S ENTIRE LIGHT BUDGET, and the control proves it. Measured on the
central square shot, in scene-referred linear:

    541 imported practicals                 median linear 0.15416
    sun and sky only (EFT_NOLAMPS=1)        median linear 0.00088, i.e. 175x darker

The pack ships no directional light and the game's outdoor lighting lives in a baked SH volume that
a path tracer has no use for, so a renderer that skips import_eftlights gets that second number and
nothing else. Outdoors the same omission is a rounding error; indoors it is the whole image.

AND THE TRAP THAT MAKES IT INVISIBLE: with AgX and grey metering, an unlit room still grades to a
plausible looking picture. Auto exposure finds the median wherever the median happens to be and
puts it at middle grey, so a frame 175x too dark comes back with a sensible histogram, sensible
contrast, and content in it. The graded PNG cannot tell you whether the room is lit. Only the
LINEAR statistics can, which is what mall_interior_grade.py's `stats` mode is for, and it is why
every frame here is written scene-referred rather than display-referred.

env:
    EFT_REPO      repository root; only needed when there is no __file__ to derive it from
    EFT_BLEND     the .blend to render (default renders/mall/mall_interior_staged.blend)
    EFT_OUTDIR    where the .exr files go (default renders/mall)
    EFT_PREFIX    filename prefix (default "mall_test_")
    EFT_CAMS      comma list of camera names; default every mall_cam_*
    EFT_RES       WxH, default 2560x1440
    EFT_SPP       samples override
    EFT_NOCOMP    "1" drops the compositor group, giving the raw beauty pass
    EFT_NOLAMPS   "1" hides every practical, keeping sun and sky: the interior control
    EFT_NOSHADOW  "1" turns off practical shadows: what honouring Unity's flag would cost
"""
import os
import time

import bpy

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))
os.environ.setdefault("EFT_REPO", REPO)

OUT_DIR = os.path.join(REPO, "renders", "mall")
BLEND = os.environ.get("EFT_BLEND", os.path.join(OUT_DIR, "mall_interior_staged.blend"))
OUTDIR = os.environ.get("EFT_OUTDIR", OUT_DIR)
PREFIX = os.environ.get("EFT_PREFIX", "mall_test_")

bpy.ops.wm.open_mainfile(filepath=os.path.abspath(BLEND))
scene = bpy.context.scene

path = os.path.join(REPO, "tools", "blender", "example_scene.py")
g = {"__name__": "example_scene_helper", "__file__": path}
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), g)
# scene.cycles.device is saved in the .blend but the list of ENABLED devices is an addon
# preference, so a --factory-startup run silently renders on the CPU at a tenth of the speed.
g["enable_cycles_gpu"]()
scene.cycles.device = 'GPU'

ng = getattr(scene, "compositing_node_group", None)
ids = sorted(n.bl_idname for n in ng.nodes) if ng else []
print("[render] comp group %s nodes=%d" % (ng, len(ng.nodes) if ng else 0), flush=True)
if os.environ.get("EFT_NOCOMP") == "1":
    # The depth haze, the veiling glare and the CA all live in that group, so dropping it gives the
    # raw scene-referred beauty pass. That is the only way to tell a LIT interior from a frame that
    # is mostly in-scatter: at the fitted in-scatter of about 1.3 and sigma 2.5e-4 per metre, 25 m
    # of interior depth alone contributes about 0.008 linear, and a dark room's median is the same
    # order of magnitude. See mall_interior_cams.py for why the haze is off in the shipped chain.
    scene.compositing_node_group = None
    print("[render] COMPOSITOR DROPPED (raw beauty pass)", flush=True)
elif not ng or "CompositorNodeRLayers" not in ids:
    # The group's input socket is NOT the render result: the beauty pass has to be pulled in by a
    # CompositorNodeRLayers INSIDE the group, or every pixel comes back exactly 0.0 with no error.
    raise SystemExit("compositor group did not survive the save; refusing to render a black frame")

if os.environ.get("EFT_NOLAMPS") == "1":
    # THE CONTROL FOR THE WHOLE INTERIOR QUESTION. Hide every practical, keep the sun and sky.
    # Whatever survives is what an interior gets from the outdoors alone, which is exactly what the
    # pack would give a renderer that skipped import_eftlights. Measured on the central square:
    # median linear 0.00088 against 0.15416 with the practicals in, 175x darker. Grade both and
    # they look similar; that is the trap, not a result.
    k = 0
    for o in scene.objects:
        if o.type == 'LIGHT' and o.data.type != 'SUN':
            o.hide_render = True
            k += 1
    print("[render] CONTROL: %d practical(s) hidden, sun+sky only" % k, flush=True)

if os.environ.get("EFT_NOSHADOW") == "1":
    # import_eftlights forces use_shadow ON for every practical, deliberately: honouring Unity's
    # flag leaks interior lamps through the walls into the street. But all 543 lights the sidecars
    # place in this region ship shadowType None, so the game itself draws every one of them
    # unshadowed, and a recessed bulb inside its own fixture housing contributes nothing at all
    # once Cycles shadows it. This control measures what that decision costs the interior.
    k = 0
    for o in scene.objects:
        if o.type == 'LIGHT' and o.data.type != 'SUN':
            o.data.use_shadow = False
            k += 1
    print("[render] CONTROL: use_shadow OFF on %d practical(s)" % k, flush=True)

if os.environ.get("EFT_SPP"):
    scene.cycles.samples = int(os.environ["EFT_SPP"])
scene.cycles.transparent_max_bounces = 256
RES = [int(v) for v in os.environ.get("EFT_RES", "2560x1440").split("x")]
scene.render.resolution_x, scene.render.resolution_y = RES
scene.render.resolution_percentage = 100
# FLAT linear EXR, always. The look is applied afterwards by eft_grade.py, and keeping the render
# scene-referred is the whole reason the statistics above are measurable at all.
scene.view_settings.view_transform = 'Raw'
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_mode = 'RGB'
scene.render.image_settings.color_depth = '32'
scene.render.image_settings.exr_codec = 'ZIP'
os.makedirs(OUTDIR, exist_ok=True)

want = os.environ.get("EFT_CAMS")
names = [s.strip() for s in want.split(",")] if want else \
    sorted(o.name for o in scene.objects if o.name.startswith("mall_cam_"))
print("[render] %d camera(s) %s  %dx%d  %d spp  diffuse_bounces %d"
      % (len(names), names, RES[0], RES[1], scene.cycles.samples, scene.cycles.diffuse_bounces),
      flush=True)

for nm in names:
    cam = bpy.data.objects[nm]
    scene.camera = cam
    m = cam.matrix_world
    slot = nm.replace("mall_cam_", "")
    out = os.path.join(OUTDIR, PREFIX + slot)
    print("[render] %s  blender %.2f %.2f %.2f  lens %.1f -> %s"
          % (nm, m.translation.x, m.translation.y, m.translation.z, cam.data.lens, out), flush=True)
    scene.render.filepath = out
    t = time.time()
    bpy.ops.render.render(write_still=True)
    print("[render] %s done in %.1f s" % (nm, time.time() - t), flush=True)
print("[render] ALL DONE", flush=True)
