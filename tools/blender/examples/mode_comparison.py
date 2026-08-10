"""Render ONE frame of ONE mode, from a camera that is forced to be the same one in both.

This is half of the two-mode comparison on the repository's front page: one camera, one frame, the
game-accurate build on the left and the photoreal build on the right, each finished the way its own
mode intends. Run it twice, then compose:

    EFT_MODE=game       blender --background --python tools/blender/examples/mode_comparison.py
    EFT_MODE=photoreal  blender --background --python tools/blender/examples/mode_comparison.py
    python tools/blender/examples/mode_comparison_compose.py \
        renders/comparison/game_f0300.exr renders/comparison/photoreal_f0300.exr renders/comparison

ONE MODE PER PROCESS, DELIBERATELY. Each build is around 3,700 objects, 8.7 M triangles and 300 MB
of .blend, and `example_scene._clear()` unlinks objects without purging the mesh, material and
image datablocks behind them. Building both in one session is the shape that runs out of memory on
the second one. Two processes also mean either mode can be re-rendered on its own.

THE CAMERA HAS TO BE FORCED, AND THIS IS THE WHOLE REASON THE FILE EXISTS. `cine_camera`'s solve is
a Viterbi pass whose per-state cost is measured by RAYCASTING THE SCENE: visibility of six body
points from each candidate camera position, ground clearance, and how much grass is in the way.
That makes the solved move a function of the geometry, and the two modes do not import identical
geometry - the photoreal build carries a different glass shader, cavity maps, a different atmosphere
and a bevel radius. It also makes the move a function of anything in the importer that changes what
is in the scene, so two builds separated by a repository change can solve differently even in the
same mode. A comparison whose halves have drifted apart by half a metre is worthless: the eye reads
the parallax, not the shading.

So the FIRST run writes the solved camera at the target frame to a small JSON sidecar, and every
later run READS it and overwrites the camera's own animation with it. Concretely: clear the camera
object's animation data (the solve keys location and rotation_quaternion on every frame), assign the
recorded world matrix, and pin the DOF focus empty to its recorded position too, because focus is
keyed to the actor and a defocus difference reads as a framing difference. Delete the sidecar to
re-solve from scratch.

WHAT IS DELIBERATELY *NOT* FORCED: `aperture_blades`, 0 in game mode and 9 in photoreal. It is one
of the things the two modes disagree about and one of the things the comparison is there to show -
a stopped-down 50 mm has a polygonal iris and no real lens produces the perfect circle Blender
defaults to. Lens (50 mm) and f-stop (f/2.8) are the same in both modes anyway.

THE SAMPLE COUNTS ARE NOT EQUAL EITHER, and that is also on purpose. `MODES` gives game mode
SAMPLES and photoreal 4x SAMPLES, because photoreal does not path-trace its atmosphere box and
spends the freed time on samples instead of on wall clock. Equal samples would be comparing the
wrong thing: what each mode does with a comparable time budget is the honest comparison.

THE PUBLISHED FRAME. docs/img/game-vs-photoreal.jpg is frame 300 of the 360-frame ZoneBearCamp 3..5
shot at MAP_RADIUS 170, i.e. everything at `example_scene`'s shipped defaults, at
camera (-476.73, -97.02, 28.14), 2560x1440. It was shot at 96 spp for game and 172 for photoreal;
`MODES["photoreal"]["samples"]` has since gone to 4x SAMPLES = 384, so a rebuild renders the right
half cleaner than the published one. Everything else about the staging is unchanged.

env:
    EFT_REPO      repository root; only needed when there is no __file__ to derive it from
    EFT_MODE      "game" or "photoreal" (default game)
    EFT_FRAME     which frame of the solved shot (default 300)
    EFT_OUTDIR    where the .exr and the camera sidecar go (default renders/comparison)
    EFT_CAMJSON   the camera sidecar (default <outdir>/comparison_camera.json)
    EFT_BLEND_OUT optional; save the built .blend here as well
    EFT_RES       WxH, default 2560x1440
    EFT_SPP       samples override; by default each mode uses its own MODES entry
    EFT_TMB       transparent_max_bounces, default 256
"""
import json
import os
import time

import bpy
import mathutils

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))
os.environ.setdefault("EFT_REPO", REPO)

# import_eftpack.py runs a demo import at MODULE SCOPE when it is exec'd; cap it at one instance.
os.environ["EFT_MAX_INSTANCES"] = "1"

MODE = (os.environ.get("EFT_MODE") or "game").lower()
FRAME = int(os.environ.get("EFT_FRAME", "300"))
OUTDIR = os.environ.get("EFT_OUTDIR", os.path.join(REPO, "renders", "comparison"))
CAMJSON = os.environ.get("EFT_CAMJSON", os.path.join(OUTDIR, "comparison_camera.json"))
RES = [int(v) for v in os.environ.get("EFT_RES", "2560x1440").split("x")]

t0 = time.time()
path = os.path.join(REPO, "tools", "blender", "example_scene.py")
g = {"__name__": "example_scene", "__file__": path}
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), g)

if MODE == "photoreal":
    # Photoreal lever 1, the 2 mm radius the doc fits. Read by import_eftpack at material build.
    os.environ.setdefault("EFT_BEVEL", "0.002")

print("=" * 90, flush=True)
print("COMPARISON  mode=%s  frame=%d  patrol=%s %s  MAP_RADIUS=%.1f"
      % (MODE, FRAME, g["PATROL_ZONE"], g["PATROL_SPAN"], g["MAP_RADIUS"]), flush=True)
print("=" * 90, flush=True)

_arm, cam = g["build"](MODE)
scene = bpy.context.scene
print("[cmp] build took %.1f s  %d objects" % (time.time() - t0, len(bpy.context.scene.objects)),
      flush=True)

if FRAME < scene.frame_start or FRAME > scene.frame_end:
    raise SystemExit("frame %d is outside the solved shot %d..%d"
                     % (FRAME, scene.frame_start, scene.frame_end))
scene.frame_set(FRAME)
bpy.context.view_layer.update()

focus = bpy.data.objects.get("focus")

if os.path.isfile(CAMJSON):
    rec = json.load(open(CAMJSON, encoding="utf-8"))
    if int(rec.get("frame", FRAME)) != FRAME:
        raise SystemExit("%s was written for frame %s, not %d; delete it or set EFT_FRAME"
                         % (CAMJSON, rec.get("frame"), FRAME))
    # Overwrite the solve. The camera carries location + rotation_quaternion keys on EVERY frame,
    # so assigning matrix_world without clearing the action would be undone at the next depsgraph
    # evaluation and the render would silently use the solved pose instead of the recorded one.
    cam.animation_data_clear()
    cam.matrix_world = mathutils.Matrix(rec["matrix_world"])
    cam.data.lens = float(rec["lens"])
    cam.data.sensor_width = float(rec["sensor_width"])
    if focus is not None and rec.get("focus") is not None:
        focus.animation_data_clear()
        focus.location = mathutils.Vector(rec["focus"])
    bpy.context.view_layer.update()
    print("[cmp] camera FORCED from %s" % CAMJSON, flush=True)
else:
    os.makedirs(os.path.dirname(os.path.abspath(CAMJSON)) or ".", exist_ok=True)
    json.dump(dict(frame=FRAME, mode=MODE,
                   matrix_world=[list(r) for r in cam.matrix_world],
                   lens=cam.data.lens, sensor_width=cam.data.sensor_width,
                   fstop=cam.data.dof.aperture_fstop,
                   focus=(list(focus.matrix_world.translation) if focus is not None else None)),
              open(CAMJSON, "w"), indent=1)
    print("[cmp] camera SOLVED and recorded to %s" % CAMJSON, flush=True)

m = cam.matrix_world
print("[cmp] cam loc %.6f %.6f %.6f  rot %.6f %.6f %.6f  lens %.2f f/%.2f blades %d"
      % (m.translation.x, m.translation.y, m.translation.z,
         m.to_euler().x, m.to_euler().y, m.to_euler().z,
         cam.data.lens, cam.data.dof.aperture_fstop, cam.data.dof.aperture_blades), flush=True)

# scene.cycles.device is saved with the scene but the list of ENABLED devices is an addon
# preference, so a --factory-startup run silently renders on the CPU at a tenth of the speed.
g["enable_cycles_gpu"]()
scene.cycles.device = 'GPU'
if os.environ.get("EFT_SPP"):
    scene.cycles.samples = int(os.environ["EFT_SPP"])
scene.cycles.transparent_max_bounces = int(os.environ.get("EFT_TMB", "256"))

ng = getattr(scene, "compositing_node_group", None)
ids = sorted(n.bl_idname for n in ng.nodes) if ng else []
if MODE == "photoreal" and (not ng or "CompositorNodeRLayers" not in ids):
    # The group's input socket is NOT the render result: the beauty pass has to be pulled in by a
    # CompositorNodeRLayers INSIDE the group, or every pixel comes back exactly 0.0 with no error.
    # On this path that group also carries the analytic depth haze, so losing it drops the
    # atmosphere as well as the lens.
    raise SystemExit("photoreal mode has no usable compositor group; refusing to render")
print("[cmp] samples %d  comp %s  atmosphere nodes %d  transparent bounces %d"
      % (scene.cycles.samples, ng,
         len(bpy.data.materials["eft_atmosphere"].node_tree.nodes),
         scene.cycles.transparent_max_bounces), flush=True)

# FLAT linear EXR, always. Each half is finished differently and the two chains cannot share a
# baked-in display transform; mode_comparison_compose.py applies them.
scene.render.resolution_x, scene.render.resolution_y = RES
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = 'Raw'
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_mode = 'RGB'
scene.render.image_settings.color_depth = '32'
scene.render.image_settings.exr_codec = 'ZIP'

blend_out = os.environ.get("EFT_BLEND_OUT")
if blend_out:
    os.makedirs(os.path.dirname(os.path.abspath(blend_out)) or ".", exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(blend_out), compress=False)
    print("[cmp] saved %s (%.1f MB)" % (blend_out, os.path.getsize(blend_out) / 1e6), flush=True)

os.makedirs(OUTDIR, exist_ok=True)
out = os.path.join(OUTDIR, "%s_f%04d" % (MODE, FRAME))
scene.render.filepath = out
print("[cmp] rendering -> %s.exr" % out, flush=True)
t = time.time()
bpy.ops.render.render(write_still=True)
print("[cmp] %s.exr in %.1f s (total %.1f s)" % (out, time.time() - t, time.time() - t0),
      flush=True)
print("[cmp] DONE", flush=True)
