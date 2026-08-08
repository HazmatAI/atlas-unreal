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
  * the grass field, distance-limited the way the viewer culls it
  * the scav, its rifle, and a walk along one of the game's own patrol_ways
  * a camera that follows him, the game's sky as the world, and a bounded atmosphere
  * Cycles on the GPU
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

PATROL_ZONE = "ZonePowerStation"   # a gamedata.json patrol_ways zone
PATROL_SPAN = (0, 6)               # waypoint slice; the ways are NETWORKS, not ordered paths
CLIP = "walk_aim_slow_0"           # a LOOPING clip with forward root motion
MAP_RADIUS = 150.0                 # metres of map to build around the route
GRASS_RADIUS = 26.0                # the viewer culls grass by screen size; a static build needs this
SAMPLES = 96

HORIZ_MIN = 0.5                    # walk_ground.rs: a face is GROUND when its normal.y/|n| > 0.5
STEP_UP = 0.5                      # walk_ground.rs: how far the feet may rise to select a surface


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
    """The game's own waypoints, pack Y-up -> Blender Z-up."""
    gd = json.load(open(os.path.join(pack, "gamedata.json"), encoding="utf-8"))
    way = next(w for w in gd["patrol_ways"] if str(w.get("zone")) == zone)
    pts = [mathutils.Vector((p[0], -p[2], p[1])) for p in way["points"][span[0]:span[1]]]
    if len(pts) < 2:
        raise SystemExit("patrol %r has fewer than 2 usable points" % zone)
    return pts


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


def build():
    scene = bpy.context.scene
    _clear()

    pts = _patrol(PACK, PATROL_ZONE, PATROL_SPAN)
    centre_bl = sum(pts, mathutils.Vector()) / len(pts)
    centre_pack = (centre_bl.x, centre_bl.z, -centre_bl.y)          # back to pack Y-up

    # 1. map -------------------------------------------------------------------------------
    gm = _load("import_eftpack.py")
    for c in list(bpy.data.collections):                            # its demo import
        if c.name.startswith("eftpack_"):
            for o in list(c.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            bpy.data.collections.remove(c)
    gm["import_eftpack"](PACK, center=centre_pack, radius=MAP_RADIUS,
                         with_textures=True, collection_name="map")

    # 2. terrain: the real splat, not the 5.9 texel/m baked slice --------------------------
    _load("terrain_splat.py")["apply_to_scene"](DATASET)

    # 3. grass, distance-limited ------------------------------------------------------------
    grass = _load("import_eftgrass.py")["import_eftgrass"](
        PACK, center=centre_pack, radius=GRASS_RADIUS, max_clumps=120000)
    for o in grass:
        o.visible_shadow = False        # the viewer keeps grass out of the shadow pass

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
    for fr in range(f0, f1 + 1, 3):
        s = (fr - f0) / fps * speed
        acc = 0.0; P = pts[-1]; D = (pts[-1] - pts[-2]).normalized()
        for i, L in enumerate(seg):
            if s <= acc + L:
                P = pts[i].lerp(pts[i + 1], (s - acc) / L)
                D = (pts[i + 1] - pts[i]).normalized()
                break
            acc += L
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
    bg.inputs["Strength"].default_value = 1.6
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
    ld = bpy.data.lights.new("eft_sun", 'SUN'); ld.energy = 6.0; ld.angle = math.radians(0.9)
    sun = bpy.data.objects.new("eft_sun", ld); scene.collection.objects.link(sun)
    sun.rotation_euler = (-sun_dir).to_track_quat('-Z', 'Y').to_euler()

    # A Cycles WORLD volume is unbounded and renders the frame black. Bound it.
    import bmesh
    me = bpy.data.meshes.new("eft_atmosphere")
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(me); bm.free()
    atm = bpy.data.objects.new("eft_atmosphere", me); scene.collection.objects.link(atm)
    atm.location = centre_bl + mathutils.Vector((0, 0, 30.0))
    atm.scale = mathutils.Vector((MAP_RADIUS * 2.2, MAP_RADIUS * 2.2, 80.0))
    atm.display_type = 'WIRE'
    am = bpy.data.materials.new("eft_atmosphere"); am.use_nodes = True
    ant = am.node_tree
    for n in list(ant.nodes):
        ant.nodes.remove(n)
    ao = ant.nodes.new("ShaderNodeOutputMaterial")
    vs = ant.nodes.new("ShaderNodeVolumeScatter")
    vs.inputs["Density"].default_value = 0.0016
    vs.inputs["Anisotropy"].default_value = 0.6
    ant.links.new(vs.outputs["Volume"], ao.inputs["Volume"])
    me.materials.append(am)

    # 7. following camera --------------------------------------------------------------------
    tk = bpy.data.objects.new("track", None); scene.collection.objects.link(tk)
    tk.parent = arm; tk.parent_type = 'BONE'; tk.parent_bone = 'Base HumanSpine3'
    tk.matrix_parent_inverse = mathutils.Matrix.Identity(4)
    cd = bpy.data.cameras.new("shot"); cd.lens = 45.0; cd.clip_start = 0.05; cd.clip_end = 4000.0
    cam = bpy.data.objects.new("shot", cd); scene.collection.objects.link(cam)
    con = cam.constraints.new('TRACK_TO'); con.target = tk
    con.track_axis = 'TRACK_NEGATIVE_Z'; con.up_axis = 'UP_Y'
    dg = bpy.context.evaluated_depsgraph_get()
    for fr in range(f0, f1 + 1, 15):
        scene.frame_set(fr); bpy.context.view_layer.update()
        p = arm.matrix_world.translation.copy()
        fwd = (arm.matrix_world.to_3x3() @ mathutils.Vector((0, 0, 1))).normalized()
        side = mathutils.Vector((-fwd.y, fwd.x, 0)).normalized()
        pos = p + side * 5.0 - fwd * 2.4 + mathutils.Vector((0, 0, 1.75))
        for _ in range(6):                                  # step out of anything solid
            d = (p + mathutils.Vector((0, 0, 1.1))) - pos
            hit, *_ = scene.ray_cast(dg, pos, d.normalized(), distance=max(0.2, d.length - 0.4))
            if not hit:
                break
            pos = pos + side * 1.5 + mathutils.Vector((0, 0, 0.4))
        cam.location = pos
        cam.keyframe_insert("location", frame=fr)
    scene.camera = cam

    # 8. Cycles -------------------------------------------------------------------------------
    scene.render.engine = 'CYCLES'
    prefs = bpy.context.preferences.addons.get("cycles")
    if prefs:
        cp = prefs.preferences
        for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI"):
            try:
                cp.compute_device_type = backend
                cp.get_devices()
                if any(d.type == backend for d in cp.devices):
                    break
            except Exception:
                continue
        for d in cp.devices:
            d.use = (d.type != 'CPU')
    scene.cycles.device = 'GPU'
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = True
    scene.cycles.volume_bounces = 1
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    scene.frame_set((f0 + f1) // 2)

    print("[example] built: %d objects, frames %d..%d (%.1f s of walk)"
          % (len(scene.objects), f0, f1, dur))
    print("[example] render a still with bpy.ops.render.render(write_still=True)")
    return arm, cam


if __name__ == "__main__":
    build()
