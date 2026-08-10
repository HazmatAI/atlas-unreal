"""Render the chosen frames of the exterior photoreal .blend to linear EXR at 2560x1440.

Step two of the exterior example (see exterior_photoreal_build.py for the chain). Rendering a
still out of a solved shot is three lines of bpy; the reason this is a file rather than a snippet
is that two of the settings it carries are the difference between a photograph and a frame with a
black slab across it, and neither one warns.

TRANSPARENT BOUNCES, AND WHY THIS SETS THEM AGAIN. `transparent_max_bounces` is a WHOLE-PATH
counter, not a per-segment one: the camera ray, every diffuse bounce that follows it, and the
shadow ray toward the sun all draw from the same budget. Every grass blade and every leaf in this
shot is alpha-tested, so a camera ray that grazes the meadow spends the whole budget on
transparency, and Cycles fails CLOSED - a camera ray that runs out is TERMINATED and returns black;
a shadow ray that runs out is reported FULLY OCCLUDED.

`cine_camera.cinematic_render_settings` knows this and sets 256, and NOTHING IN THE REPOSITORY
CALLS THAT FUNCTION, so a scene built by `example_scene.build()` alone runs whatever the .blend
carries. Measured on frame 20 of this shot at 1280x720: at the Cycles default of 8, 13.22% of the
frame is dead and the frame mean is 40% low (0.05911 against 0.09873); 32 still leaves rms 0.0237
and 22.2% of pixels more than 10% off the converged image; 256 is converged (rms 1e-5, mean
identical to the 1024 maximum) at the same cost. The price is 8.4 s -> 20.6 s at 720p and 25 s ->
30 to 66 s per frame at 2560x1440.

AND IT HIDES FROM A NAIVE CHECK. The EXR contains no pure zeros, because the depth-haze node adds
L * (1 - T) on top of the dead pixel: the black band measures 0.00129 linear, which is exactly zero
plus the in-scatter at 3.3 m. So "no black pixels in the file" proves nothing. Set the value, and
PRINT what it was, because a .blend built before the fix carries the old number and looks plausible
until it is measured.

THE COMPOSITOR GROUP MUST SURVIVE THE SAVE, and if it does not the render is not merely
uncomposited - it never runs. The group's INPUT SOCKET IS NOT THE RENDER RESULT: the beauty pass
has to be pulled in by a CompositorNodeRLayers node INSIDE the group. Feed the chain from the Group
Input and every pixel comes back exactly 0.0, in 0.03 s, with no error. This script refuses to
render rather than write a black EXR. On the photoreal path that group also carries the analytic
depth haze, so losing it silently drops the atmosphere as well.

FLAT LINEAR EXR ALWAYS. With AgX and grey metering an unlit or half-black frame still grades to a
plausible looking picture, because auto exposure puts whatever median it finds at middle grey. The
graded PNG cannot tell you whether the render is right; only the scene-referred percentiles can,
which is what exterior_photoreal_grade.py's `stats` mode prints first.

Run it from the repository root::

    blender --background --python tools/blender/examples/exterior_photoreal_render.py

env:
    EFT_REPO      repository root; only needed when there is no __file__ to derive it from
    EFT_BLEND     the .blend to render     (default renders/exterior/exterior_photoreal.blend)
    EFT_OUTDIR    where the .exr files go  (default renders/exterior)
    EFT_PREFIX    filename prefix          (default "exterior_photoreal_")
    EFT_FRAMES    comma list of "frame:slot"; slot is the output suffix.
                  Default "20:01,200:02,212:03,248:04,320:05,356:06", the six frames the probe
                  in exterior_photoreal_build.py selected out of the 360-frame shot.
    EFT_RES       WxH, default 2560x1440
    EFT_SPP       samples override; the photoreal mode's own value is 4x SAMPLES, i.e. 384
    EFT_TMB       transparent_max_bounces, default 256. See above before lowering it.
"""
import os
import time

import bpy

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))
os.environ.setdefault("EFT_REPO", REPO)

OUT_DIR = os.path.join(REPO, "renders", "exterior")
BLEND = os.environ.get("EFT_BLEND", os.path.join(OUT_DIR, "exterior_photoreal.blend"))
OUTDIR = os.environ.get("EFT_OUTDIR", OUT_DIR)
PREFIX = os.environ.get("EFT_PREFIX", "exterior_photoreal_")
JOBS = [p.split(":") for p in
        os.environ.get("EFT_FRAMES", "20:01,200:02,212:03,248:04,320:05,356:06").split(",")
        if p.strip()]

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
print("[render] comp group %s nodes=%d %s"
      % (ng, len(ng.nodes) if ng else 0, sorted(set(ids))), flush=True)
if not ng or "CompositorNodeRLayers" not in ids:
    raise SystemExit("compositor group did not survive the save; refusing to render a black frame")
print("[render] atmosphere hide_render=%s  use_pass_z=%s  samples=%d"
      % (bpy.data.objects["eft_atmosphere"].hide_render,
         [vl.use_pass_z for vl in scene.view_layers], scene.cycles.samples), flush=True)

if os.environ.get("EFT_SPP"):
    scene.cycles.samples = int(os.environ["EFT_SPP"])
tmb = int(os.environ.get("EFT_TMB", "256"))
print("[render] transparent_max_bounces %d -> %d" % (scene.cycles.transparent_max_bounces, tmb),
      flush=True)
scene.cycles.transparent_max_bounces = tmb

RES = [int(v) for v in os.environ.get("EFT_RES", "2560x1440").split("x")]
scene.render.resolution_x, scene.render.resolution_y = RES
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = 'Raw'
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_mode = 'RGB'
scene.render.image_settings.color_depth = '32'
scene.render.image_settings.exr_codec = 'ZIP'
os.makedirs(OUTDIR, exist_ok=True)

for fs, slot in JOBS:
    f = int(fs)
    scene.frame_set(f)
    cam = scene.camera
    m = cam.matrix_world
    out = os.path.join(OUTDIR, PREFIX + slot)
    print("[render] slot %s frame %d  cam %.2f %.2f %.2f  lens %.1f f/%.2f -> %s"
          % (slot, f, m.translation.x, m.translation.y, m.translation.z,
             cam.data.lens, cam.data.dof.aperture_fstop, out), flush=True)
    scene.render.filepath = out
    t = time.time()
    bpy.ops.render.render(write_still=True)
    print("[render] slot %s done in %.1f s" % (slot, time.time() - t), flush=True)
print("[render] ALL DONE", flush=True)
