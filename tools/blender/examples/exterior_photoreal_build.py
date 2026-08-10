"""Build the outdoor photoreal shot and PROBE every candidate frame before spending a render.

This is the exterior counterpart of the interior example (``mall_interior_*.py``), and it is the
recipe behind the wooded ZoneBearCamp frame on the repository's front page. Three scripts run as a
chain:

    exterior_photoreal_build.py   this file: the staging, the terrain cull, the frame probe
    exterior_photoreal_render.py  render the chosen frames flat, and the two settings that decide
                                  whether the frame has content in it at all
    exterior_photoreal_grade.py   LINEAR statistics first, then the AgX finish

Run it from the repository root::

    blender --background --python tools/blender/examples/exterior_photoreal_build.py
    blender --background --python tools/blender/examples/exterior_photoreal_render.py
    python  tools/blender/examples/exterior_photoreal_grade.py stats renders/exterior/*.exr

Every path defaults under ``renders/`` beside the repository and every one is overridable from the
environment; nothing here needs editing to run.

WHAT IS DIFFERENT FROM ``tools/blender/example_scene.py``. Almost nothing, and that is the point.
The staging, the route, the character, the lights, the sun and the two MODES tables are all
``example_scene`` verbatim: this file calls ``build("photoreal")`` and does not reimplement a line
of it. What it adds is the two things a still frame needs that a shot does not.

FIRST, THE REGION RADIUS, AND THE TERRAIN CULL THAT USED TO HIDE IN IT. The shipped MAP_RADIUS is
170 m; this file defaults to 190. The reason is historical and worth writing down because the
symptom was silent. Terrain ships as four ~700 m tiles. The region filter used to test each
instance's mesh CENTRE, sampled from <=64 strided vertices, and on a 513x513 terrain grid that
stride aliases: Slice_2_2's sampled centre came back 185.5 m from this route's centroid even though
the route is INSIDE that tile. At 170 the tile was culled, ``terrain_splat`` then printed
"rebuilt 0 terrain material(s)", and the frame rendered as ground props floating over a void with
no error anywhere. 190 cleared 185.5 by 4.5 m, at 4,309 instances against 3,683.

``import_eftpack._select`` now tests the mesh's world AABB instead, and this route's distance to
Slice_2_2's box is 0.0 m because the route is inside it, so the shipped 170 keeps the terrain too
(3,683 -> 3,766 instances, +766,415 triangles, 524,288 of them the terrain slice). 190 is kept as
the default here only because it is what the published frame was built with, and the extra 20 m of
map is visible in the background. Set EFT_MAP_RADIUS=170 for the lighter, equally correct build.

HAZE_ZMAX moves with the radius and is not optional. It is defined as MAP_RADIUS * 1.1, the
atmosphere box's own half width, and the depth-haze node clamps Z to it so the sky (which comes
back at 1e10) does not saturate the exponential and paint itself flat in-scatter.

SECOND, THE FRAME PROBE, WHICH IS WHY THIS FILE EXISTS AT ALL. A solved follow camera is a
continuous move through a forest, and a forest is full of places a camera should not stop. Probing
every twelfth frame with an 80x45 raycast grid before rendering any of them threw out, on this
route: frames 44, 68, 140 and 236, which come back 100% grass or 100% foliage with `near` at 100%
and `boxed` 3 to 6, i.e. the solver's boom has dropped to its 2.34 m minimum and parked the lens
inside a bush; and frames 56, 80, 104 and 128, which are 55-65% terrain at 1.5-1.8 m median depth
with the actor out of frame, i.e. the camera skimming the ground. None of those is detectable from
the camera keys, and each one costs a 30 to 66 second render to discover by eye.

The probe columns, per candidate frame:
    sky/actor/grass/foliage/terrain/solid/glass/water   % of rays whose FIRST hit is that class,
                                                        by material role, name only as a fallback
    near                                                % of rays terminating under 0.75 m
    boxed                                               of 6 axis rays cast 0.5 m from the camera
                                                        origin, how many hit. Nonzero means the
                                                        camera is inside something
    med/mean depth                                      metres

Note that the ground is real terrain in every frame of this shot but the classifier mostly reports
it as `grass`, because the grass field is the first thing a downward ray meets. Directly visible
terrain runs 0.1 to 2.4%.

THE FRAMES THE PUBLISHED SET USED, chosen from the survivors so that no two cameras are within 3 m
of each other and each has a different dominant class: 20, 200, 212, 248, 320, 356 of the 360-frame
solved shot. Frame 20 is the front-page one - the start of the walk, deepest into the forest, grass
62%, foliage 27%, solid 6.7%, actor 4.1%, mean depth 15.4 m.

env:
    EFT_REPO         repository root; only needed when this file is pasted into Blender's text
                     editor, where there is no __file__ to derive it from
    EFT_BLEND_OUT    where the .blend goes      (default renders/exterior/exterior_photoreal.blend)
    EFT_PROBE_JSON   where the probe table goes (default renders/exterior/exterior_probe.json)
    EFT_MAP_RADIUS   metres of map around the route centroid (default 190)
    EFT_PROBE_STEP   probe every Nth frame (default 12); 0 skips the probe entirely
"""
import json
import os
import time

import bpy
import mathutils

# The repository root, three levels up from tools/blender/examples/. Pasting this file into
# Blender's text editor leaves no __file__ to derive it from, so fall back to EFT_REPO exactly as
# example_scene.py does.
_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))
os.environ.setdefault("EFT_REPO", REPO)

# import_eftpack.py runs a demo import at MODULE SCOPE when it is exec'd, so cap it at one
# instance; example_scene.build() deletes the collection it makes. Without the cap the pack is
# imported twice.
os.environ["EFT_MAX_INSTANCES"] = "1"
os.environ.setdefault("EFT_BEVEL", "0.002")   # photoreal lever 1, the 2 mm radius the doc fits

OUT_DIR = os.path.join(REPO, "renders", "exterior")
BLEND_OUT = os.environ.get("EFT_BLEND_OUT", os.path.join(OUT_DIR, "exterior_photoreal.blend"))
PROBE_JSON = os.environ.get("EFT_PROBE_JSON", os.path.join(OUT_DIR, "exterior_probe.json"))
RADIUS = float(os.environ.get("EFT_MAP_RADIUS", "190"))
PROBE_STEP = int(os.environ.get("EFT_PROBE_STEP", "12"))

NX, NY = 80, 45                    # probe ray grid
NEAR_M = 0.75                      # a hit closer than this is the lens inside something
BOX_M = 0.5                        # the 6-axis "am I inside geometry" probe distance

t0 = time.time()
path = os.path.join(REPO, "tools", "blender", "example_scene.py")
g = {"__name__": "example_scene", "__file__": path}
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), g)

# MAP_RADIUS is read by that module's own helpers out of g, so setting it here is enough.
# HAZE_ZMAX has to move with it; see the module docstring.
g["MAP_RADIUS"] = RADIUS
g["HAZE_ZMAX"] = RADIUS * 1.1

print("=" * 90, flush=True)
print("BUILD exterior photoreal  MAP_RADIUS=%.1f  patrol=%s %s  EFT_BEVEL=%s"
      % (RADIUS, g["PATROL_ZONE"], g["PATROL_SPAN"], os.environ["EFT_BEVEL"]), flush=True)
print("=" * 90, flush=True)

arm, cam = g["build"]("photoreal")
scene = bpy.context.scene
print("[ext] build took %.1f s" % (time.time() - t0), flush=True)

# The shot resolution, and FLAT linear EXR: the look is applied afterwards by
# exterior_photoreal_grade.py, and keeping the render scene-referred is what makes the linear
# statistics measurable at all. 16:9 like the 1280x720 build() sets, so framing is unchanged.
scene.render.resolution_x, scene.render.resolution_y = 2560, 1440
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = 'Raw'
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_mode = 'RGB'
scene.render.image_settings.color_depth = '32'
scene.render.image_settings.exr_codec = 'ZIP'

print("[ext] frames %d..%d  cam=%s  samples=%d  comp=%s"
      % (scene.frame_start, scene.frame_end, cam.name, scene.cycles.samples,
         getattr(scene, "compositing_node_group", None)), flush=True)
print("[ext] atmosphere hide_render=%s  use_pass_z=%s"
      % (bpy.data.objects["eft_atmosphere"].hide_render,
         [vl.use_pass_z for vl in scene.view_layers]), flush=True)
terr = [o.name for o in scene.objects
        if "terrain" in o.name.lower() or o.name.lower().startswith("slice")]
print("[ext] terrain objects in the scene: %d  %s" % (len(terr), terr[:6]), flush=True)
print("[ext] HAZE_ZMAX %.1f  MAP_RADIUS %.1f" % (g["HAZE_ZMAX"], g["MAP_RADIUS"]), flush=True)

KEYS = ("sky", "actor", "grass", "glass", "foliage", "terrain", "water", "solid")


def classify(obj, idx, armature):
    """What a probe ray hit, as one of KEYS. Material ROLE first, object name only as a fallback."""
    if obj is None:
        return "sky"
    p = obj
    while p is not None:
        if p is armature:
            return "actor"
        p = p.parent
    n = obj.name
    if n.startswith("grass_kind"):
        return "grass"
    mat = None
    try:
        mat = obj.data.materials[obj.data.polygons[idx].material_index]
    except Exception:
        pass
    mn = mat.name if mat else ""
    if mn.endswith(".glass"):
        return "glass"
    if mn.endswith(".cutout"):
        return "foliage"
    if "terrain" in mn.lower() or "terrain" in n.lower() or n.lower().startswith("slice"):
        return "terrain"
    if mn.endswith(".water"):
        return "water"
    return "solid"


rows = []
if PROBE_STEP > 0:
    f0, f1 = scene.frame_start, scene.frame_end
    cands = list(range(max(f0, 8), f1 + 1, PROBE_STEP))
    print("[ext] probing %d candidate frame(s) at %dx%d rays" % (len(cands), NX, NY), flush=True)
    for f in cands:
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        fr = [cam.matrix_world @ v for v in cam.data.view_frame(scene=scene)]
        tr, br, bl, tl = fr                       # view_frame: top-right, bottom-right, bl, tl
        org = cam.matrix_world.translation.copy()
        counts, depths, near = {}, [], 0
        for j in range(NY):
            v = (j + 0.5) / NY
            L = tl.lerp(bl, v)
            R = tr.lerp(br, v)
            for i in range(NX):
                d = (L.lerp(R, (i + 0.5) / NX) - org).normalized()
                hit, loc, _n, idx, obj, _m = scene.ray_cast(dg, org, d, distance=4000.0)
                k = classify(obj if hit else None, idx, arm)
                counts[k] = counts.get(k, 0) + 1
                if hit:
                    z = (loc - org).length
                    depths.append(z)
                    if z < NEAR_M:
                        near += 1

        # Is the camera INSIDE something? Six axis rays, and how many hit within BOX_M. This is the
        # test that catches the boom collapsing into a bush, which the class percentages alone
        # cannot: a lens buried in foliage reads as a legitimate 100% foliage frame.
        boxed = 0
        for d in (mathutils.Vector(t) for t in ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                                                (0, -1, 0), (0, 0, 1), (0, 0, -1))):
            hit, _l, _n, _i, _o, _m = scene.ray_cast(dg, org, d, distance=BOX_M)
            boxed += 1 if hit else 0

        depths.sort()
        tot = float(NX * NY)
        rec = dict(frame=f, cam=[round(c, 3) for c in org],
                   mean_depth=(sum(depths) / max(len(depths), 1)),
                   med_depth=(depths[len(depths) // 2] if depths else 0.0),
                   near_pct=100.0 * near / tot, boxed=boxed)
        for k in KEYS:
            rec[k] = 100.0 * counts.get(k, 0) / tot
        rows.append(rec)
        print("[probe] f%-4d sky%6.2f actor%6.2f grass%6.2f foliage%6.2f terrain%6.2f solid%6.2f "
              "glass%5.2f water%5.2f | near%5.2f boxed%d  med%6.1f mean%6.1f  cam %.1f %.1f %.1f"
              % (f, rec["sky"], rec["actor"], rec["grass"], rec["foliage"], rec["terrain"],
                 rec["solid"], rec["glass"], rec["water"], rec["near_pct"], rec["boxed"],
                 rec["med_depth"], rec["mean_depth"], org.x, org.y, org.z), flush=True)

os.makedirs(os.path.dirname(os.path.abspath(PROBE_JSON)) or ".", exist_ok=True)
json.dump(dict(radius=RADIUS, samples=scene.cycles.samples,
               frames=[scene.frame_start, scene.frame_end], probe=rows),
          open(PROBE_JSON, "w"), indent=1)

os.makedirs(os.path.dirname(os.path.abspath(BLEND_OUT)) or ".", exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(BLEND_OUT), compress=False)
print("[ext] %d objects, saved %s (%.1f MB) in %.1f s"
      % (len(scene.objects), BLEND_OUT, os.path.getsize(BLEND_OUT) / 1e6, time.time() - t0),
      flush=True)
print("[ext] DONE", flush=True)
