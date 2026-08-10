"""Re-stage a built mall .blend: curated cameras, the actor in frame, and NO depth haze.

Step two of the interior example (see mall_interior_build.py for the chain). It exists because the
first pass got two things wrong, both of them things the outdoor path gets RIGHT, and both worth
more than the scripts that found them.

1. THE DEPTH HAZE IS FOG INDOORS. example_scene's photoreal path replaces a traced height-falloff
   volume with an analytic per-pixel exponential fitted to that same volume,

       out_c = in_c * exp(-sigma_c * z) + L_c * (1 - exp(-sigma_c * z))

   at sigma about 2.55e-4 per metre and in-scatter L about 1.3. That is aerial perspective fitted
   for a 150 m outdoor vista and it is correct there. Inside a building it adds a flat veil of
   L * (1 - exp(-sigma * z)) over a room whose own linear median is 0.014: the veil alone is 0.0033
   at 10 m, 0.0083 at 25 m and 0.0199 at 60 m. On the 40 m concourse shot the haze contributed MORE
   than the surface it was covering, and the frame came back looking smoke filled. So the
   compositor is rebuilt here with haze=False.

   Glare and chromatic aberration STAY ON. They are lens behaviour and a lens does not care what is
   being photographed; the haze is scene behaviour and this scene does not have any.

   The atmosphere box stays hide_render'd. example_scene warns that hidden-and-unapplied is the one
   combination worse than either end of the choice, and that warning is about OUTDOOR frames, where
   the haze is real light being removed from the fitted sun and sky pair. Indoors there is no vista
   to attenuate, so there is nothing to remove.

   BUT hide_render IS A RENDER FLAG, AND THIS FILE IS ALL RAYCASTS. The box is a real mesh cube
   MAP_RADIUS * 2.2 across; hide_render stops Cycles tracing it and leaves it in the view layer, in
   the evaluated depsgraph, and in scene.ray_cast. Every probe in this repository written before
   this was noticed - the one at the bottom of this file included - was casting into the inside of
   a closed 528 x 528 x 80 m room, which means its `sky` column was 0.00% BY CONSTRUCTION and the
   comment that read it as "the shell is closed and the camera is inside it" was reading a
   tautology. It was caught outdoors, where 144 swept poses all came back sky 0.00% with p90 depth
   290-350 m (the box wall, not any surface in the map) and the same pose reads sky 73.96% with the
   box hide_viewport'd. The conclusion is TRUE indoors, which is exactly why it survived. This file
   now sets hide_viewport for the whole staging and clears it before the save, so the number is a
   measurement again, and `atmosphere` is a probe class of its own so a regression says so.

2. A CAMERA WAS UNDER THE MALL. gamedata rooms[] and patrol_ways[] put the mall's walkable floors
   at pack Y=27.1 (level 1) and Y=36.6 (level 2). The nav grid ALSO reports a walkable layer at
   Y=21.3 across the whole footprint, and that is not a floor of the building: it is the outdoor
   ground and parking deck the building stands on. example_scene._ground_height implements the
   viewer's own rule, the GREATEST walkable surface at or below the feet plus a step allowance, cast
   downward. That rule is right outdoors and it is right indoors too, right up until level 1 has a
   hole in it, at which point the cast falls 5.8 m through the slab and the camera is standing on
   the parking deck looking at daylight.

   The fix is a clamp, not a different cast: a resolved floor more than 1.2 m below the level the
   shot was STAGED on is rejected and the staged level is used instead. Stage from rooms[], let the
   raycast refine, never let it relocate.

env:
    EFT_REPO        repository root; only needed when there is no __file__ to derive it from
    EFT_BLEND       the .blend to re-stage  (default renders/mall/mall_interior.blend)
    EFT_BLEND_OUT   where the result goes   (default renders/mall/mall_interior_staged.blend)
    EFT_PROBE_JSON  probe table, updated in place
                    (default renders/mall/mall_interior_probe.json)
"""
import json
import math
import os
import time

import bpy
import mathutils

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))
os.environ.setdefault("EFT_REPO", REPO)
os.environ["EFT_MAX_INSTANCES"] = "1"        # the module-scope demo import in import_eftpack

OUT_DIR = os.path.join(REPO, "renders", "mall")
BLEND = os.environ.get("EFT_BLEND", os.path.join(OUT_DIR, "mall_interior.blend"))
BLEND_OUT = os.environ.get("EFT_BLEND_OUT", os.path.join(OUT_DIR, "mall_interior_staged.blend"))
PROBE_JSON = os.environ.get("EFT_PROBE_JSON", os.path.join(OUT_DIR, "mall_interior_probe.json"))
LENS = 35.0
EYE = 1.65
FLOOR_L1 = 27.1                    # pack Y of the mall's level-1 floor, from gamedata rooms[]
FLOOR_DROP_MAX = 1.2               # metres a raycast may refine DOWNWARD before it is disbelieved

t0 = time.time()
bpy.ops.wm.open_mainfile(filepath=os.path.abspath(BLEND))
scene = bpy.context.scene
print("[cams] opened %s in %.1f s, %d objects" % (BLEND, time.time() - t0, len(scene.objects)),
      flush=True)

path = os.path.join(REPO, "tools", "blender", "example_scene.py")
g = {"__name__": "example_scene_helper", "__file__": path}
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), g)

# 1. compositor without the haze -------------------------------------------------------------
old = getattr(scene, "compositing_node_group", None)
g["_try"](scene, "compositing_node_group", None)
if old is not None:
    try:
        bpy.data.node_groups.remove(old)
    except Exception:
        pass
ng = g["_compositor"](scene, haze=False)
print("[cams] compositor rebuilt haze=OFF: %d node(s) %s"
      % (len(ng.nodes), sorted({n.bl_idname for n in ng.nodes})), flush=True)
# THE ATMOSPHERE BOX COMES OUT OF THE DEPSGRAPH FOR THE WHOLE OF THIS FILE, and goes back before
# the save. hide_render is a RENDER flag and this file is all raycasts: see the docstring.
atm = bpy.data.objects["eft_atmosphere"]
atm.hide_viewport = True
bpy.context.view_layer.update()
print("[cams] atmosphere hide_render=%s (a RENDER flag), hide_viewport=%s for the raycasts"
      % (atm.hide_render, atm.hide_viewport), flush=True)

# The importer names the armature after the character directory, so derive it rather than typing
# the name twice; fall back to the one armature in the scene if that ever stops being true.
_actor_name = os.path.basename(os.path.normpath(g["CHARACTER"]))
arm = bpy.data.objects.get(_actor_name) or next(o for o in scene.objects if o.type == 'ARMATURE')
lamps = [o for o in scene.objects if o.type == 'LIGHT' and o.data.type != 'SUN']
print("[cams] actor %r, %d practical lamp(s) in the scene" % (arm.name, len(lamps)), flush=True)

for o in [o for o in scene.objects if o.name.startswith("mall_cam_")]:
    bpy.data.objects.remove(o, do_unlink=True)

dg = bpy.context.evaluated_depsgraph_get()


def pack_to_bl(p):
    return mathutils.Vector((p[0], -p[2], p[1]))


def floor_at(x, y, level):
    """The floor under (x, y), refusing to fall through a hole into the deck below.

    `level` is the pack Y this shot was staged on, out of gamedata rooms[]. The raycast is allowed
    to refine it upward without limit (a step, a plinth, a shop threshold) but only FLOOR_DROP_MAX
    downward. Anything further down is the outdoor ground under the building, not a floor of it.
    """
    z = g["_ground_height"](scene, dg, arm, x, y, level)
    if z is None or z < level - FLOOR_DROP_MAX:
        return level
    return z


def clear_run(org, d, dist=120.0):
    hit, loc, _n, _i, _o, _m = scene.ray_cast(dg, org, d, distance=dist)
    return (loc - org).length if hit else dist


def open_dir(org, bias=None, bias_w=0.45):
    """Horizontal direction with the longest clear run, optionally biased toward `bias`."""
    best, best_s = None, -1e30
    for i in range(36):
        a = 2.0 * math.pi * i / 36
        d = mathutils.Vector((math.cos(a), math.sin(a), 0.0))
        s = min(clear_run(org, d), 45.0)
        if bias is not None:
            s *= (1.0 + bias_w * d.dot(bias))
        if s > best_s:
            best_s, best = s, d
    return best


# 2. stage the actor on the central square, facing down its most open axis --------------------
SQUARE = (-3.1, FLOOR_L1, -66.7)          # rooms[] MALL_1stCentralSquare, snapped to the level
sq = pack_to_bl(SQUARE)
sq.z = floor_at(sq.x, sq.y, FLOOR_L1)
eye_sq = mathutils.Vector((sq.x, sq.y, sq.z + EYE))
axis = open_dir(eye_sq)
# The room's most-open axis puts a square column hard against the left edge of a 35 mm frame taken
# from the square's exact centre: measured at 20.0% of the preview at or below 1 CV, one black slab
# and no information in it. Slide 2.6 m along the camera-right of that axis to clear the column;
# the square is 12 m wide, so this does not leave the room.
right = mathutils.Vector((axis.y, -axis.x, 0.0))
eye_sq = eye_sq + right * 2.6
eye_sq.z = floor_at(eye_sq.x, eye_sq.y, FLOOR_L1) + EYE
print("[cams] central square blender (%.1f %.1f %.1f) most-open axis (%.2f %.2f) run %.1f m"
      % (sq.x, sq.y, sq.z, axis.x, axis.y, clear_run(eye_sq, axis)), flush=True)

# put the actor 9 m along that axis, walking back toward the camera position
ACTOR = sq + axis * 9.0
ACTOR.z = floor_at(ACTOR.x, ACTOR.y, FLOOR_L1)
arm.rotation_mode = 'XYZ'
arm.location = ACTOR
face = -axis
# (pi/2, 0, yaw): the X term is the Y-up -> Z-up stand-up the importer applied and it must be
# COMPOSED with the yaw, not replaced by it, or the character lies on its back.
arm.rotation_euler = (math.pi / 2, 0.0, math.atan2(face.x, -face.y))
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
print("[cams] actor at blender (%.2f %.2f %.2f) pack (%.1f %.1f %.1f)"
      % (ACTOR.x, ACTOR.y, ACTOR.z, ACTOR.x, ACTOR.z, -ACTOR.y), flush=True)

# 3. the four cameras ------------------------------------------------------------------------
# 01 and 02 are the two spots mall_interior_build's own probe staged (route fractions 0.06 and
# 0.34) and the preview proved read well, so they are pinned as literal positions rather than
# re-derived. 03 is the central square with the actor 9 m out; 04 is a three-quarter close on him
# from his left. All four are STATIC: a legibility test does not need a follow-camera solve.
CHEST = ACTOR + mathutils.Vector((0, 0, 1.35))
ang = math.atan2(axis.y, axis.x) + math.radians(148.0)
off = mathutils.Vector((math.cos(ang), math.sin(ang), 0.0))
c4 = ACTOR + off * 5.5
c4.z = floor_at(c4.x, c4.y, FLOOR_L1) + EYE

SPECS = [
    ("mall_cam_01", mathutils.Vector((-69.03, 50.35, 28.76)), None, None),
    ("mall_cam_02", mathutils.Vector((-41.90, 45.99, 28.74)), None, None),
    ("mall_cam_03", eye_sq, None, axis),
    ("mall_cam_04", c4, CHEST, None),
]

KEYS = ("sky", "actor", "glass", "foliage", "terrain", "water", "solid", "atmosphere")


def classify(obj, idx):
    """What a probe ray hit, as one of KEYS. Material ROLE first, object name only as a fallback."""
    if obj is None:
        return "sky"
    p = obj
    while p is not None:
        if p is arm:
            return "actor"
        p = p.parent
    if obj.name == "eft_atmosphere":
        # Only reachable if the box is back in the depsgraph, in which case every other column in
        # the row is wrong too. It is a class of its own so that it says so.
        return "atmosphere"
    mat = None
    try:
        mat = obj.data.materials[obj.data.polygons[idx].material_index]
    except Exception:
        pass
    mn = mat.name if mat else ""
    n = obj.name
    if mn.endswith(".glass"):
        return "glass"
    if mn.endswith(".cutout"):
        return "foliage"
    if "terrain" in mn.lower() or "terrain" in n.lower() or n.lower().startswith("slice"):
        return "terrain"
    if mn.endswith(".water"):
        return "water"
    return "solid"


lamp_pos = [o.matrix_world.translation.copy() for o in lamps]
rows, cams = [], []
for nm, org, look, want in SPECS:
    if look is not None:
        aim = (look - org)
        tilt = aim.normalized()
    else:
        a = want if want is not None else open_dir(org)
        tilt = mathutils.Vector((a.x, a.y, -0.09)).normalized()
    cd = bpy.data.cameras.new(nm)
    cd.lens = LENS
    cd.clip_start = 0.05
    cd.clip_end = 2000.0
    cd.dof.use_dof = False
    cam = bpy.data.objects.new(nm, cd)
    scene.collection.objects.link(cam)
    cam.location = org
    cam.rotation_euler = tilt.to_track_quat('-Z', 'Y').to_euler()
    cams.append(cam)
    bpy.context.view_layer.update()

    tr, br, bl, tl = [cam.matrix_world @ v for v in cam.data.view_frame(scene=scene)]
    NX, NY = 64, 36
    counts, depths = {}, []
    for jj in range(NY):
        v = (jj + 0.5) / NY
        L, R = tl.lerp(bl, v), tr.lerp(br, v)
        for ii in range(NX):
            d = (L.lerp(R, (ii + 0.5) / NX) - org).normalized()
            hit, loc, _n, idx, obj, _m = scene.ray_cast(dg, org, d, distance=4000.0)
            k = classify(obj if hit else None, idx)
            counts[k] = counts.get(k, 0) + 1
            if hit:
                depths.append((loc - org).length)
    depths.sort()
    tot = float(NX * NY)
    near = los = 0
    dmin = 1e30
    for lp in lamp_pos:
        dv = lp - org
        dl = dv.length
        dmin = min(dmin, dl)
        if dl <= 20.0:
            near += 1
            if not scene.ray_cast(dg, org, dv.normalized(), distance=max(dl - 0.15, 0.05))[0]:
                los += 1
    rec = dict(cam=nm, loc=[round(v, 2) for v in org],
               pack=[round(org.x, 2), round(org.z, 2), round(-org.y, 2)],
               med_depth=round(depths[len(depths) // 2], 1) if depths else 0.0,
               p90_depth=round(depths[int(len(depths) * 0.9)], 1) if depths else 0.0,
               lamps_20m=near, lamps_los_20m=los, lamp_nearest=round(dmin, 2))
    for k in KEYS:
        rec[k] = round(100.0 * counts.get(k, 0) / tot, 2)
    rows.append(rec)
    # sky 0.00% on every row is the check that the shell is closed and the camera is inside it,
    # and it is only that check because the atmosphere box was taken out of the depsgraph above.
    # With the box in, this column reads 0.00% for any scene at all. `atmosphere` above 0.00% here
    # means the box came back and the row means nothing.
    print("[probe] %s pack(%.1f %.1f %.1f) depth med %.1f p90 %.1f | sky %.1f%% solid %.1f%% "
          "glass %.1f%% actor %.1f%% terrain %.1f%% | lamps<20m %d (LOS %d) nearest %.1f m"
          % (nm, rec["pack"][0], rec["pack"][1], rec["pack"][2], rec["med_depth"],
             rec["p90_depth"], rec["sky"], rec["solid"], rec["glass"], rec["actor"],
             rec["terrain"], near, los, dmin), flush=True)

# Put the box back as example_scene left it, hide_render'd and visible to the view layer, BEFORE
# the save. Every script that opens this .blend afterwards inherits whatever state it is saved in.
atm.hide_viewport = False
bpy.context.view_layer.update()

scene.camera = cams[0]
os.makedirs(os.path.dirname(os.path.abspath(PROBE_JSON)) or ".", exist_ok=True)
old = json.load(open(PROBE_JSON, encoding="utf-8")) if os.path.exists(PROBE_JSON) else {}
old["cameras"] = rows
old["haze"] = False
old["actor"] = [round(v, 2) for v in ACTOR]
json.dump(old, open(PROBE_JSON, "w"), indent=1)
os.makedirs(os.path.dirname(os.path.abspath(BLEND_OUT)) or ".", exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(BLEND_OUT), compress=False)
print("[cams] saved %s in %.1f s" % (BLEND_OUT, time.time() - t0), flush=True)
print("[cams] DONE", flush=True)
