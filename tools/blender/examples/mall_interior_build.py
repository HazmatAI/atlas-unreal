"""Build a map INTERIOR and probe it: the ULTRA shopping mall on Interchange, photoreal mode.

An interior is a different problem from the outdoor vista ``tools/blender/example_scene.py``
stages, and these four scripts are the worked example of it. Geometry, terrain, lights, character
and the material rules are reused from example_scene VERBATIM, because none of them change indoors.
What changes is where a camera may legally stand, what is actually lighting the frame, and which
parts of the photoreal path are still physically true once there is a roof overhead. Each of those
is written down at the point it bites:

    mall_interior_build.py    this file: the staging, the floor question, diffuse_bounces
    mall_interior_cams.py     re-stage a built .blend: haze OFF indoors, and the floor clamp
    mall_interior_render.py   render the static cameras, plus the two lighting controls
    mall_interior_grade.py    LINEAR statistics, the only measurement that answers "is it lit"

Run the chain from the repository root::

    blender --background --python tools/blender/examples/mall_interior_build.py
    blender --background --python tools/blender/examples/mall_interior_cams.py
    blender --background --python tools/blender/examples/mall_interior_render.py
    python  tools/blender/examples/mall_interior_grade.py stats renders/mall/*.exr

Every path defaults under ``renders/`` beside the repository and every one is overridable from the
environment; nothing here needs editing to run.

WHERE THE MALL IS, AND WHY THE NAV GRID MUST NOT BE ASKED. This is the first trap and it is silent.
``gamedata.json`` is the authority for a walkable floor, not the mesh names and NOT the baked nav
grid:

    rooms[]           MALL_1stCentralSquare sits at pack (-3.1, 27.6, -66.7) and
                      Central2ndLVL_MainRoad at (-98.6, 37.1, -76.5), so the mall's walkable floors
                      are pack Y=27.1 (level 1) and Y=36.6 (level 2).
    patrol_ways[]     BossWay1, zone ZoneCenterBot: 8 waypoints all at Y=27.1, running x -68.7 to
                      32.6 and z -76.2 to -2.9. That is the boss's own walk across the central hall,
                      which is exactly the staging an interior legibility test wants.
    manifest.roots[]  SBG_Shopping_Mall_indoor's world AABB is X[-123, 85] Y[21, 46] Z[-184, 74].

The nav grid ALSO reports a walkable layer at Y=21.3 under the whole footprint. That layer is not a
floor of the building, it is the outdoor ground and parking deck the building stands on. Resolving a
camera or a character height from the grid alone therefore puts it 5.8 m below the level it was
staged on, outside the shell, under the slab: a frame of parked cars and daylight where a shopping
concourse was asked for. Stage from rooms[] and patrol_ways[], and then CLAMP the raycast (see
mall_interior_cams.py, which exists mostly because of this).

THE STAGING THAT WORKED. BossWay1 routed on the pack's own baked nav grid via nav_route.py, so the
path is one a bot could actually walk rather than a straight line through the retail units. Its
centroid is pack (-21.2, 27.1, -33.2) and MAP_RADIUS 110 about that point imports 16,407 instances
and 27.45 M triangles. The check that the region filter did not slice the building in half is the
probe's own sky fraction: every camera comes back with sky at 0.00% of its frame, i.e. the shell is
closed and no ray escapes. Cameras are STATIC 35 mm with depth of field off. A 50 mm sees almost
nothing of a room, DOF hides the geometry a legibility test is there to read, and cine_camera.py's
Viterbi solve is the slowest step in example_scene and buys nothing when the camera does not move.

env:
    EFT_REPO         repository root; only needed when this file is pasted into Blender's text
                     editor, where there is no __file__ to derive it from
    EFT_BLEND_OUT    where the .blend goes      (default renders/mall/mall_interior.blend)
    EFT_PROBE_JSON   where the probe table goes (default renders/mall/mall_interior_probe.json)
    EFT_MAP_RADIUS   metres of map around the route centroid (default 110)
"""
import json
import math
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

# import_eftpack.py runs a demo import at MODULE SCOPE when it is exec'd, so cap it at one instance
# and delete the collection it makes below. Without the cap it imports the whole pack twice.
os.environ["EFT_MAX_INSTANCES"] = "1"
os.environ.setdefault("EFT_BEVEL", "0.002")   # photoreal lever 1, the 2 mm radius the doc fits

OUT_DIR = os.path.join(REPO, "renders", "mall")
BLEND_OUT = os.environ.get("EFT_BLEND_OUT", os.path.join(OUT_DIR, "mall_interior.blend"))
PROBE_JSON = os.environ.get("EFT_PROBE_JSON", os.path.join(OUT_DIR, "mall_interior_probe.json"))
RADIUS = float(os.environ.get("EFT_MAP_RADIUS", "110"))

WAY_NAME, WAY_ZONE = "BossWay1", "ZoneCenterBot"
EYE = 1.65                                   # camera height above the RESOLVED floor, metres
LENS = 35.0                                  # a 50 mm sees almost nothing of a room
CAM_FRACS = (0.06, 0.34, 0.62, 0.90)         # where along the routed walk the cameras stand
SCAV_FRAC = 0.50

t0 = time.time()
path = os.path.join(REPO, "tools", "blender", "example_scene.py")
g = {"__name__": "example_scene", "__file__": path}
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), g)

# MAP_RADIUS is read by that module's own helpers out of g, so setting it here is enough.
# HAZE_ZMAX has to move with it: it is defined as MAP_RADIUS * 1.1, the atmosphere box's own half
# width, and the depth-haze clamp must stay the longest crossing that box can have.
g["MAP_RADIUS"] = RADIUS
g["HAZE_ZMAX"] = RADIUS * 1.1
PACK = g["PACK"]
cfg = g["MODES"]["photoreal"]

print("=" * 90, flush=True)
print("BUILD mall interior  photoreal  MAP_RADIUS=%.1f  way=%s/%s" % (RADIUS, WAY_ZONE, WAY_NAME),
      flush=True)
print("=" * 90, flush=True)

scene = bpy.context.scene
g["_clear"]()

# -- 0. the route ---------------------------------------------------------------------------
# example_scene._patrol picks the FIRST way in a zone. ZoneCenterBot's first is KILLA_PATROL_ALT,
# a ONE-point way, which trips its own len < 2 guard and aborts the build. Select by NAME as well
# as by zone: a zone is not a path.
gd = json.load(open(os.path.join(PACK, "gamedata.json"), encoding="utf-8"))
way = next(w for w in gd["patrol_ways"]
           if str(w.get("zone")) == WAY_ZONE and str(w.get("name")) == WAY_NAME)
raw = [tuple(p) for p in way["points"]]
nav = g["_load"]("nav_route.py")["NavGrid"](PACK)
routed = nav.route_through(raw)
pts = [mathutils.Vector((p[0], -p[2], p[1])) for p in routed]       # pack Y-up -> Blender Z-up
seg = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
total = sum(seg)
centre_bl = sum(pts, mathutils.Vector()) / len(pts)
centre_pack = (centre_bl.x, centre_bl.z, -centre_bl.y)
print("[mall] %d waypoint(s) -> %d routed node(s), %.1f m of walk" % (len(raw), len(pts), total),
      flush=True)
print("[mall] route centroid  pack (%.1f, %.1f, %.1f)  blender (%.1f, %.1f, %.1f)"
      % (centre_pack[0], centre_pack[1], centre_pack[2],
         centre_bl.x, centre_bl.y, centre_bl.z), flush=True)


def at(s):
    """Point at arc length `s` along the routed polyline."""
    acc = 0.0
    for i, L in enumerate(seg):
        if s <= acc + L:
            return pts[i].lerp(pts[i + 1], (s - acc) / max(L, 1e-9))
        acc += L
    return pts[-1]


# -- 1. map ---------------------------------------------------------------------------------
gm = g["_load"]("import_eftpack.py")
for c in list(bpy.data.collections):                    # its module-scope demo import
    if c.name.startswith("eftpack_"):
        for o in list(c.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(c)
CAVITY = g["CAVITY_DIR"] if (cfg["cavity"] and os.path.isdir(g["CAVITY_DIR"])) else None
mapres = gm["import_eftpack"](PACK, center=centre_pack, radius=RADIUS, with_textures=True,
                              collection_name="map", glass_mode=cfg["glass_mode"],
                              cavity_dir=CAVITY)
print("[mall] map imported in %.1f s" % (time.time() - t0), flush=True)

# -- 2. terrain splat -----------------------------------------------------------------------
# Still worth running for an interior build: the mall is a building standing ON the terrain, and
# the parking deck and every window view are terrain.
g["_load"]("terrain_splat.py")["apply_to_scene"](g["DATASET"])

# -- 3. practical lights --------------------------------------------------------------------
# THE WHOLE QUESTION THIS TEST ASKS. The pack ships no directional light, and the game's outdoor
# light is a baked SH volume a path tracer has no use for. Indoors the ONLY light is these, and
# the measurement that proves it is in mall_interior_render.py's EFT_NOLAMPS control.
lamps = g["_load"]("import_eftlights.py")["import_eftlights"](
    PACK, parent=mapres["empty"], center=centre_pack, radius=RADIUS)
print("[mall] PRACTICALS KEPT: %d" % len(lamps), flush=True)

# -- 4. character ---------------------------------------------------------------------------
coll = bpy.data.collections.new("actor")
scene.collection.children.link(coll)
man = json.load(open(os.path.join(g["CHARACTER"], "manifest.json"), encoding="utf-8"))
clip = next(c for c in man["clips"] if c["name"] == g["CLIP"])
scene.render.fps = int(round(clip["sampleRate"]))
res = g["_load"]("import_eftchar.py")["import_eftchar"](
    g["CHARACTER"], clip_name=g["CLIP"], loops=2, apply_root_motion=False, collection=coll)
arm = res[0] if isinstance(res, (tuple, list)) else res
g["_load"]("import_eftweap.py")["import_eftweap"](g["WEAPON"], armature=arm, bone="Weapon_root",
                                                  collection=coll)

# -- 5. world, sun, bounded atmosphere ------------------------------------------------------
# All of this is example_scene's step 6, unchanged. It matters indoors too: the sun and sky are
# what comes through the skylights and the shopfront glass, and they are the CONTROL the interior
# question is measured against.
w = scene.world or bpy.data.worlds.new("World")
scene.world = w
w.use_nodes = True
nt = w.node_tree
for nd in list(nt.nodes):
    nt.nodes.remove(nd)
out = nt.nodes.new("ShaderNodeOutputWorld")
out.location = (400, 0)
bg = nt.nodes.new("ShaderNodeBackground")
bg.location = (150, 0)
bg.inputs["Strength"].default_value = g["SKY_STRENGTH"]
if os.path.isfile(g["SKY_EQUIRECT"]):
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.location = (-200, 0)
    env.image = bpy.data.images.load(g["SKY_EQUIRECT"], check_existing=True)
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
else:
    bg.inputs["Color"].default_value = (0.36, 0.40, 0.47, 1.0)
    print("[mall] no sky equirect; run tools/blender/make_sky_equirect.py first", flush=True)
nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

sd = json.load(open(os.path.join(PACK, "volume.json"), encoding="utf-8")).get(
    "sun_dir", [0.449, 0.799, -0.400])
sun_dir = mathutils.Vector((sd[0], -sd[2], sd[1])).normalized()
ld = bpy.data.lights.new("eft_sun", 'SUN')
ld.energy = g["SUN_ENERGY"]
ld.angle = math.radians(0.526)
sun = bpy.data.objects.new("eft_sun", ld)
scene.collection.objects.link(sun)
sun.rotation_euler = (-sun_dir).to_track_quat('-Z', 'Y').to_euler()

import bmesh  # noqa: E402
me = bpy.data.meshes.new("eft_atmosphere")
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1.0)
bm.to_mesh(me)
bm.free()
atm = bpy.data.objects.new("eft_atmosphere", me)
scene.collection.objects.link(atm)
atm.location = centre_bl + mathutils.Vector((0, 0, g["ATM_LIFT"]))
atm.scale = mathutils.Vector((RADIUS * 2.2, RADIUS * 2.2, g["ATM_HEIGHT"]))
atm.display_type = 'WIRE'
am = bpy.data.materials.new("eft_atmosphere")
am.use_nodes = True
ant = am.node_tree
for nd in list(ant.nodes):
    ant.nodes.remove(nd)
g["_atmosphere_nodes"](ant, cfg["atmosphere"],
                       g["ATM_LIFT"] - g["ATM_HEIGHT"] * 0.5, g["ATM_LIFT"] + g["ATM_HEIGHT"] * 0.5)
me.materials.append(am)
atm.hide_render = True                       # photoreal: applied analytically from the Z pass
for vl in scene.view_layers:
    vl.use_pass_z = True

# -- 6. Cycles ------------------------------------------------------------------------------
scene.render.engine = 'CYCLES'
g["enable_cycles_gpu"]()
scene.cycles.device = 'GPU'
scene.cycles.samples = cfg["samples"]
scene.cycles.use_denoising = True
scene.cycles.volume_bounces = 1
scene.cycles.transparent_max_bounces = 256   # 8 DELETES pixels behind anything alpha-tested
# INTERIOR-ONLY DEPARTURE, and the only engine setting in this file that differs from
# example_scene. Indoors the interreflection IS the light: every practical here has a median Unity
# range of 7 m, so any surface more than one bounce from a lamp is lit only by what the room passes
# on. Cycles' shipped default of 4 diffuse bounces is a choice made for an outdoor scene where the
# sun does the work and the later bounces are a rounding error; 8 is the number this build uses.
scene.cycles.diffuse_bounces = max(scene.cycles.diffuse_bounces, 8)
scene.render.resolution_x, scene.render.resolution_y = 2560, 1440
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = 'Raw'
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_mode = 'RGB'
scene.render.image_settings.color_depth = '32'
scene.render.image_settings.exr_codec = 'ZIP'
g["_try"](scene, "compositing_node_group", None)
# Built WITH the depth haze, because that is what the photoreal path does outdoors and this build
# is the thing that proved it wrong indoors. mall_interior_cams.py rebuilds the group with
# haze=False and explains the measurement; see its docstring before copying this line.
g["_compositor"](scene, haze=True)

# -- 7. place the actor, then the cameras, from a raycast probe -------------------------------
dg = bpy.context.evaluated_depsgraph_get()
scene.frame_set(int((scene.frame_start + scene.frame_end) * 0.42))
dg = bpy.context.evaluated_depsgraph_get()


def floor_at(P, guess):
    z = g["_ground_height"](scene, dg, arm, P.x, P.y, guess)
    return P.z if z is None else z


S = at(SCAV_FRAC * total)
D = (at(min(SCAV_FRAC * total + 2.0, total)) - S)
D = D.normalized() if D.length > 1e-4 else mathutils.Vector((0, 1, 0))
arm.rotation_mode = 'XYZ'
arm.location = (S.x, S.y, floor_at(S, S.z))
arm.rotation_euler = (math.pi / 2, 0.0, math.atan2(D.x, -D.y))
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
print("[mall] actor at blender (%.1f, %.1f, %.1f)" % tuple(arm.location), flush=True)

NDIR = 24
KEYS = ("sky", "actor", "glass", "foliage", "terrain", "water", "solid")


def classify(obj, idx):
    """What a probe ray hit, as one of KEYS. Material ROLE first, object name only as a fallback."""
    if obj is None:
        return "sky"
    p = obj
    while p is not None:
        if p is arm:
            return "actor"
        p = p.parent
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
rows = []
cams = []
for k, fr in enumerate(CAM_FRACS):
    P = at(fr * total)
    z = floor_at(P, P.z) + EYE
    org = mathutils.Vector((P.x, P.y, z))

    # Which way is the room? 24 horizontal rays, keep the direction with the longest clear run,
    # biased toward the walk's own heading so the cameras do not all end up facing one wall.
    fwd = (at(min(fr * total + 4.0, total)) - P)
    fwd = mathutils.Vector((fwd.x, fwd.y, 0.0))
    fwd = fwd.normalized() if fwd.length > 1e-4 else mathutils.Vector((0, 1, 0))
    best, best_s, clears = None, -1e30, []
    for i in range(NDIR):
        a = 2.0 * math.pi * i / NDIR
        d = mathutils.Vector((math.cos(a), math.sin(a), 0.0))
        hit, loc, _n, _i, _o, _m = scene.ray_cast(dg, org, d, distance=120.0)
        run = (loc - org).length if hit else 120.0
        clears.append(run)
        s = min(run, 45.0) * (1.0 + 0.45 * d.dot(fwd))
        if s > best_s:
            best_s, best = s, d
    aim = best
    ceiling = scene.ray_cast(dg, org, mathutils.Vector((0, 0, 1)), distance=60.0)
    ceil_h = (ceiling[1] - org).length if ceiling[0] else None

    cd = bpy.data.cameras.new("mall_cam_%02d" % (k + 1))
    cd.lens = LENS
    cd.clip_start = 0.05
    cd.clip_end = 2000.0
    cd.dof.use_dof = False                    # a legibility test wants the geometry sharp
    cam = bpy.data.objects.new("mall_cam_%02d" % (k + 1), cd)
    scene.collection.objects.link(cam)
    cam.location = org
    # -Z down the aim, +Y up, then tilt 5 degrees down so the floor is in frame.
    q = mathutils.Vector((aim.x, aim.y, -0.09)).normalized().to_track_quat('-Z', 'Y')
    cam.rotation_euler = q.to_euler()
    cams.append(cam)
    bpy.context.view_layer.update()

    # WHAT THE FRAME WILL CONTAIN, before spending a render on it. 64x36 rays through the camera's
    # own view frame. `sky` is the load-bearing column indoors: anything above 0.00% means a ray
    # escaped, i.e. the region filter cut the shell or the camera is not inside the building.
    frv = [cam.matrix_world @ v for v in cam.data.view_frame(scene=scene)]
    tr, br, bl, tl = frv
    NX, NY = 64, 36
    counts, depths = {}, []
    for jj in range(NY):
        v = (jj + 0.5) / NY
        L = tl.lerp(bl, v)
        R = tr.lerp(br, v)
        for ii in range(NX):
            d = (L.lerp(R, (ii + 0.5) / NX) - org).normalized()
            hit, loc, _n, idx, obj, _m = scene.ray_cast(dg, org, d, distance=4000.0)
            kk = classify(obj if hit else None, idx)
            counts[kk] = counts.get(kk, 0) + 1
            if hit:
                depths.append((loc - org).length)
    depths.sort()
    tot = float(NX * NY)

    # How many practicals actually REACH this camera, and how near is the nearest. A lamp count is
    # not a light budget: a recessed bulb behind its own fixture housing contributes nothing, so
    # the line-of-sight count is the number that predicts whether the frame will read.
    near, los, dmin = 0, 0, 1e30
    for lp in lamp_pos:
        dv = lp - org
        dl = dv.length
        dmin = min(dmin, dl)
        if dl <= 20.0:
            near += 1
            hit, hloc, _n, _i, _o, _m = scene.ray_cast(dg, org, dv.normalized(),
                                                       distance=max(dl - 0.15, 0.05))
            if not hit:
                los += 1
    rec = dict(cam=cam.name, frac=fr, loc=[round(v, 2) for v in org],
               pack=[round(org.x, 2), round(org.z, 2), round(-org.y, 2)],
               aim=[round(aim.x, 3), round(aim.y, 3)],
               ceiling=(round(ceil_h, 2) if ceil_h else None),
               clear_max=round(max(clears), 1), clear_med=round(sorted(clears)[NDIR // 2], 1),
               med_depth=round(depths[len(depths) // 2], 1) if depths else 0.0,
               lamps_20m=near, lamps_los_20m=los, lamp_nearest=round(dmin, 2))
    for kk in KEYS:
        rec[kk] = round(100.0 * counts.get(kk, 0) / tot, 2)
    rows.append(rec)
    print("[probe] %s pack(%.1f %.1f %.1f) ceil %s  clear med %.1f max %.1f  depth med %.1f | "
          "sky %.1f%% solid %.1f%% glass %.1f%% actor %.1f%% terrain %.1f%% | lamps<20m %d "
          "(LOS %d) nearest %.1f m"
          % (cam.name, rec["pack"][0], rec["pack"][1], rec["pack"][2], rec["ceiling"],
             rec["clear_med"], rec["clear_max"], rec["med_depth"], rec["sky"], rec["solid"],
             rec["glass"], rec["actor"], rec["terrain"], near, los, dmin), flush=True)

scene.camera = cams[0]
os.makedirs(os.path.dirname(os.path.abspath(PROBE_JSON)) or ".", exist_ok=True)
json.dump(dict(radius=RADIUS, centre_pack=[round(v, 2) for v in centre_pack],
               route_len=round(total, 1), practicals=len(lamps),
               actor=[round(v, 2) for v in arm.location], frame=scene.frame_current,
               cameras=rows), open(PROBE_JSON, "w"), indent=1)

nobj = len(scene.objects)
os.makedirs(os.path.dirname(os.path.abspath(BLEND_OUT)) or ".", exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(BLEND_OUT), compress=False)
print("[mall] %d objects, %d lamps, saved %s (%.1f MB) in %.1f s"
      % (nobj, len(lamps), BLEND_OUT, os.path.getsize(BLEND_OUT) / 1e6, time.time() - t0),
      flush=True)
print("[mall] DONE", flush=True)
