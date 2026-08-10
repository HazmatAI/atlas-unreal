"""Shoot a patrol as a SEQUENCE in game-accurate mode, and finish it with the game's own grade.

The other two examples render stills. This one renders a shot: the scav walks a real patrol way,
the solved follow camera moves with him, and every frame goes out flat so the game's 64-cube grade
can be applied afterwards in seconds rather than re-rendered in hours. It is the recipe behind the
power station frame on the repository's front page.

Run it from the repository root::

    blender --background --python tools/blender/examples/sequence_game_render.py
    python  tools/blender/eft_grade.py renders/powerstation renders/powerstation \
        --lut packs/shared/grade_lut.bin --look game --exposure 1.35

The grade is the stock eft_grade.py CLI with its defaults, deliberately: `--look game` with
exposure 1.35 and the authored vignette IS the viewer's finish, and there is nothing example
specific to add on top of it. 1.35 is `DEFAULT_GRADE_EXPOSURE` in the viewer's own render module,
and it is the one number in the chain that is renderer-relative, so it only means what it means
because SUN_ENERGY and SKY_STRENGTH were fitted against a viewer frame at that exposure.

THE STAGING IS NOT THE ONE example_scene.py SHIPS. `example_scene` currently stages ZoneBearCamp
3..5, a 24.6 m walk chosen to show a lighting change; this file overrides it back to the previous
staging, ZonePowerStation 0..6 at MAP_RADIUS 150 and GRASS_RADIUS 26, which is what the published
sequence was shot on and what every measurement in docs/extraction/game-parity.md was taken on.
The overrides are set on the loaded module's globals rather than by editing the file, so both
stagings stay runnable. ZonePowerStation's first six waypoints route to 181.1 m of walk over 318
nav nodes, of which SHOT_SECONDS films the first 12.0 s, i.e. frames 1..360 at 30 fps.

WHY MODE="game" AND NOT "photoreal". The whole point of a sequence in this mode is that it is
diffable against the viewer frame for frame: uniform traced haze exactly as the sun and sky pair
was fitted, the bounded legacy glassTRS response term for term, no cavity because the game shader
has no ambient-occlusion term at any scale, and no compositor because the viewer has neither glare
nor chromatic aberration. Every one of those is a photoreal-only departure and each would make the
frame undiffable.

RENDER SETTINGS COME FROM cine_camera.cinematic_render_settings, which is the function that already
knows them and which nothing else in the repository calls: 2560x1440, 256 samples with adaptive
sampling at threshold 0.005, motion blur at a 180 degree shutter, persistent data, and
transparent_max_bounces 256 for the alpha-tested foliage. Motion blur is what stops a walk cycle
reading as stop motion, and it is the single biggest realism-per-second setting in the list.

DISK, AND WHY THE EXRs ARE HALF FLOAT AND LOSSY HERE. A still is written 32-bit ZIP; a 360 frame
sequence at 2560x1440 is not. Half float with the DWAA codec is roughly a tenth the size and the
grade that follows quantises to 8 bits anyway. Set EFT_EXR_FULL=1 to get the lossless 32-bit ZIP
the still examples use.

COST. Measured on this staging on two RTX 5090s under OptiX: the build is dominated by the nav
route and the camera solve, and the render ran about 32 s a frame, i.e. roughly 3.2 hours for the
full 360. The default below therefore renders four spread frames (40, 140, 240, 340) rather than
the whole shot; set EFT_FRAMES=all when you actually want the sequence. Frame 140 is the published
one.

WHAT WILL NOT COME BACK BIT IDENTICAL, and it is worth knowing before diffing against the published
frame. Three things in this repository moved after that sequence was shot, all of them forward:
  * the grass field. It was a disc about the route centroid with no wind; it is now a capsule along
    the routed polyline with the pack's own WavingGrass stage applied, so the blades both sit
    differently and move.
  * the region filter. It tested each instance's sampled mesh CENTRE; it now tests the mesh's world
    AABB, which is a strict superset, so a few hundred more instances import at the same radius.
  * transparent_max_bounces. `cinematic_render_settings` set 32 at the time, which leaves 0.48% of
    a foliage frame at exactly 0.0 luma; it sets 256 now, which is converged.
The staging, the camera solve, the resolution, the sample count and the grade are unchanged, so the
frame is the same frame. The pixels are not the same pixels.

env:
    EFT_REPO        repository root; only needed when there is no __file__ to derive it from
    EFT_BLEND       the .blend to build or reuse (default renders/powerstation/patrol_game.blend).
                    If it already exists it is OPENED instead of rebuilt, so a re-render costs
                    nothing but the render.
    EFT_OUTDIR      where the .exr frames go (default renders/powerstation)
    EFT_PATROL_ZONE gamedata.json patrol_ways zone   (default ZonePowerStation)
    EFT_PATROL_SPAN "first,last" waypoint slice      (default 0,6)
    EFT_MAP_RADIUS  metres of map around the route   (default 150)
    EFT_GRASS_RADIUS                                 (default 26)
    EFT_FRAMES      "all", or a comma list of frame numbers (default "40,140,240,340")
    EFT_SPP         samples override (default 256, from cinematic_render_settings)
    EFT_RES         WxH, default 2560x1440
    EFT_EXR_FULL    "1" writes 32-bit ZIP EXRs instead of half-float DWAA
"""
import os
import time

import bpy

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))
os.environ.setdefault("EFT_REPO", REPO)

# import_eftpack.py runs a demo import at MODULE SCOPE when it is exec'd; example_scene.build()
# deletes the collection it makes, but the cap is what stops the pack being imported twice.
os.environ["EFT_MAX_INSTANCES"] = "1"

OUT_DIR = os.path.join(REPO, "renders", "powerstation")
BLEND = os.environ.get("EFT_BLEND", os.path.join(OUT_DIR, "patrol_game.blend"))
OUTDIR = os.environ.get("EFT_OUTDIR", OUT_DIR)
ZONE = os.environ.get("EFT_PATROL_ZONE", "ZonePowerStation")
SPAN = tuple(int(v) for v in os.environ.get("EFT_PATROL_SPAN", "0,6").split(","))
RADIUS = float(os.environ.get("EFT_MAP_RADIUS", "150"))
GRASS_RADIUS = float(os.environ.get("EFT_GRASS_RADIUS", "26"))
WANT = os.environ.get("EFT_FRAMES", "40,140,240,340")
RES = tuple(int(v) for v in os.environ.get("EFT_RES", "2560x1440").split("x"))
SPP = int(os.environ.get("EFT_SPP", "256"))

t0 = time.time()
path = os.path.join(REPO, "tools", "blender", "example_scene.py")
g = {"__name__": "example_scene", "__file__": path}
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), g)

if os.path.isfile(BLEND):
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(BLEND))
    scene = bpy.context.scene
    print("[seq] reusing %s: %d objects, frames %d..%d"
          % (os.path.basename(BLEND), len(scene.objects), scene.frame_start, scene.frame_end),
          flush=True)
    # scene.cycles.device is saved in the .blend but the list of ENABLED devices is an addon
    # preference, so a --factory-startup run silently renders on the CPU at a tenth of the speed.
    g["enable_cycles_gpu"]()
    scene.cycles.device = 'GPU'
else:
    # The staging overrides. Set on the loaded module's globals, so no file is edited and the
    # shipped ZoneBearCamp staging stays exactly as it is for anyone running example_scene itself.
    g["PATROL_ZONE"] = ZONE
    g["PATROL_SPAN"] = SPAN
    g["MAP_RADIUS"] = RADIUS
    g["GRASS_RADIUS"] = GRASS_RADIUS
    g["HAZE_ZMAX"] = RADIUS * 1.1        # the atmosphere box's own half width; see example_scene
    print("=" * 90, flush=True)
    print("BUILD sequence game  patrol=%s %s  MAP_RADIUS=%.1f  GRASS_RADIUS=%.1f"
          % (ZONE, SPAN, RADIUS, GRASS_RADIUS), flush=True)
    print("=" * 90, flush=True)
    g["build"]("game")
    scene = bpy.context.scene
    print("[seq] build took %.1f s  %d objects" % (time.time() - t0, len(scene.objects)),
          flush=True)

# The settings that buy realism per unit of render time, from the one function that carries them.
g["_load"]("cine_camera.py")["cinematic_render_settings"](scene, samples=SPP, res=RES)

# FLAT linear EXR. The look is a post step, which is the whole reason the sequence is worth
# keeping: any grade change re-runs in seconds instead of re-rendering for hours.
scene.view_settings.view_transform = 'Raw'
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_mode = 'RGB'
if os.environ.get("EFT_EXR_FULL") == "1":
    scene.render.image_settings.color_depth = '32'
    scene.render.image_settings.exr_codec = 'ZIP'
else:
    scene.render.image_settings.color_depth = '16'
    scene.render.image_settings.exr_codec = 'DWAA'
scene.render.use_overwrite = True
os.makedirs(OUTDIR, exist_ok=True)

print("[seq] %dx%d  %d spp  frames %d..%d  motion blur %s  transparent bounces %d"
      % (scene.render.resolution_x, scene.render.resolution_y, scene.cycles.samples,
         scene.frame_start, scene.frame_end, scene.render.use_motion_blur,
         scene.cycles.transparent_max_bounces), flush=True)

if not os.path.isfile(BLEND):
    os.makedirs(os.path.dirname(os.path.abspath(BLEND)) or ".", exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(BLEND), compress=False)
    print("[seq] saved %s (%.1f MB)" % (BLEND, os.path.getsize(BLEND) / 1e6), flush=True)

if WANT.strip().lower() == "all":
    # Blender's own animation loop, which is what names the files f0001.exr .. f0360.exr.
    scene.render.filepath = os.path.join(OUTDIR, "f")
    n = scene.frame_end - scene.frame_start + 1
    print("[seq] rendering %d frame(s) -> %s" % (n, OUTDIR), flush=True)
    t = time.time()
    bpy.ops.render.render(animation=True)
    dt = time.time() - t
    print("[seq] rendered %d frame(s) in %.1f s (%.1f s/frame)" % (n, dt, dt / max(n, 1)),
          flush=True)
else:
    for fs in [s for s in WANT.split(",") if s.strip()]:
        f = int(fs)
        scene.frame_set(f)
        cam = scene.camera
        m = cam.matrix_world
        out = os.path.join(OUTDIR, "f%04d" % f)
        print("[seq] frame %d  cam %.2f %.2f %.2f  lens %.1f -> %s"
              % (f, m.translation.x, m.translation.y, m.translation.z, cam.data.lens, out),
              flush=True)
        scene.render.filepath = out
        t = time.time()
        bpy.ops.render.render(write_still=True)
        print("[seq] frame %d done in %.1f s" % (f, time.time() - t), flush=True)

print("[seq] grade with: python tools/blender/eft_grade.py %s %s --lut packs/shared/grade_lut.bin "
      "--look game --exposure 1.35"
      % (os.path.relpath(OUTDIR, REPO).replace(os.sep, "/"),
         os.path.relpath(OUTDIR, REPO).replace(os.sep, "/")), flush=True)
print("[seq] DONE", flush=True)
