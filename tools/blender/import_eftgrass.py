"""Blender importer for the pack's procedural grass field (grass.bin).

Standalone. Run inside Blender, then call ``import_eftgrass(pack_dir, center=..., radius=...)``.

FORMAT (docs/extraction/build-pipeline-and-pack-format.md, "grass.bin")
    Headerless array, stride 24, ``grass_sidecar.format == 2``:
        @0  f32x3  x, y, z   PACK (viewer) space, exact terrain height
        @12 f32    rotY      radians, [0, 2pi)
        @16 f32    scale     [0.75, 1.35]
        @20 u32    kind      index into grass_sidecar.kinds[], stored as the u32 bit pattern
    Legacy format absent/1 -> stride 20, no kind lane, one implicit kind.

    Kind slots are POSITIONAL: an unresolvable texture keeps its slot rather than shifting the
    indices in grass.bin.

CARD GEOMETRY matches what the viewer builds (render/gpu_driven.rs): a THREE-plane star, quads at
0, 60 and 120 degrees, half-width 0.42 m and height 0.9 m, normals straight up, alpha-tested. Not
a camera-facing billboard, so it reads correctly from any angle and needs no per-frame work.

SCALE: Interchange ships 3.26 million clumps. Building all of them as Blender geometry is not
useful for a shot, so `radius` selects a disc around a point (or a CAPSULE around a polyline, see
`points`) and `max_clumps` caps the rest.

WIND. The sidecar ships Unity's own WavingGrass parameters and this importer used to throw them
away, leaving the field frozen for the whole shot while a character walks through it. `wind=True`
ports the viewer's vertex stage (gpu_draw.wgsl, "WavingGrass (#4)") verbatim as a Geometry Nodes
modifier, so Blender evaluates it per frame with no Python rebuild. See `_wind_nodes`.
"""

import json
import os

import bpy
import numpy as np

__all__ = ["import_eftgrass"]

HALF_WIDTH = 0.42          # gpu_driven.rs: (hw, gh) = (0.42, 0.9)
HEIGHT = 0.90
PLANES = 3                 # quads at q * pi/3


def _resolve(pack_dir, raw):
    """Sidecar albedo -> a real file. Absolute paths from another machine are not trusted."""
    base = os.path.basename(str(raw).replace("\\", "/"))
    for c in (os.path.join(pack_dir, str(raw)), os.path.join(pack_dir, base),
              os.path.join(pack_dir, "terrain_layers", base)):
        if os.path.isfile(c):
            return c
    return None


def _material(name, tex_path, tint):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (700, 0)
    # Blades are thin: light passes THROUGH them as much as it bounces off. Diffuse alone under a
    # low sun leaves every card that faces away from the light black, which is the slab look.
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse"); diff.location = (250, 120)
    tran = nt.nodes.new("ShaderNodeBsdfTranslucent"); tran.location = (250, -60)
    mixs = nt.nodes.new("ShaderNodeMixShader"); mixs.location = (450, 40)
    mixs.inputs["Fac"].default_value = 0.40
    nt.links.new(diff.outputs["BSDF"], mixs.inputs[1])
    nt.links.new(tran.outputs["BSDF"], mixs.inputs[2])
    trans = nt.nodes.new("ShaderNodeBsdfTransparent"); trans.location = (450, -220)
    cut = nt.nodes.new("ShaderNodeMixShader"); cut.location = (600, 0)
    nt.links.new(trans.outputs["BSDF"], cut.inputs[1])
    nt.links.new(mixs.outputs["Shader"], cut.inputs[2])
    nt.links.new(cut.outputs["Shader"], out.inputs["Surface"])
    bsdf = diff        # colour goes to both lobes below
    img = bpy.data.images.load(tex_path, check_existing=True)
    try:
        img.colorspace_settings.name = "sRGB"
    except Exception:
        pass
    t = nt.nodes.new("ShaderNodeTexImage"); t.image = img; t.location = (-350, 100)
    t.interpolation = "Smart"
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = 'RGBA'; mix.blend_type = 'MULTIPLY'
    mix.location = (-60, 100); mix.inputs["Factor"].default_value = 1.0
    nt.links.new(t.outputs["Color"], mix.inputs[6])
    mix.inputs[7].default_value = (tint[0], tint[1], tint[2], 1.0)
    nt.links.new(mix.outputs[2], diff.inputs["Color"])
    nt.links.new(mix.outputs[2], tran.inputs["Color"])
    # alpha-tested, like the viewer: a GREATER_THAN keeps Cycles from soft-blending the card edge
    gt = nt.nodes.new("ShaderNodeMath"); gt.operation = 'GREATER_THAN'
    gt.location = (-60, -180); gt.inputs[1].default_value = 0.35
    nt.links.new(t.outputs["Alpha"], gt.inputs[0])
    nt.links.new(gt.outputs["Value"], cut.inputs["Fac"])
    return mat


def _dist2_to_polyline(pos, pts):
    """Squared distance from each clump to a polyline, both in PACK space. (n,) float32.

    A disc about a route's CENTROID is the wrong shape for a walking shot: on the 24.6 m patrol
    this file is normally called with, the actor is outside a 26 m disc for the first ~210 of 360
    frames, so grass is dense where nobody looks and absent where he walks. A capsule about the
    routed polyline is the same cost and follows him.
    """
    p = np.asarray(pts, np.float32).reshape(-1, 3)
    best = np.full(pos.shape[0], np.inf, np.float32)
    for i in range(p.shape[0] - 1):
        a, b = p[i], p[i + 1]
        ab = b - a
        L2 = float(ab @ ab)
        d = pos - a[None, :]
        t = np.clip((d @ ab) / L2, 0.0, 1.0) if L2 > 1e-12 else np.zeros(pos.shape[0], np.float32)
        q = d - t[:, None] * ab[None, :]
        np.minimum(best, np.einsum("ij,ij->i", q, q), out=best)
    return best


def _wind_nodes(name, strength, amount, speed):
    """The viewer's WavingGrass vertex stage as a Geometry Nodes group.

    gpu_draw.wgsl, @vertex "WavingGrass (#4)":

        ph   = time*speed + t.x*0.35 + t.z*0.27
        sway = (sin(ph) + 0.5*sin(2.13*ph + 1.7),  cos(0.87*ph + 0.4) + 0.5*sin(1.61*ph))
        world += vec3(sway.x, 0, sway.y) * amount*strength*h

    with `t` the CLUMP's translation and `h` the blade-local height, so the base stays planted and
    only the tips move. Both of those are per-vertex constants, so they are baked once into the
    "eft_wind" attribute (x = the constant part of the phase, y = h) and the modifier only has to
    evaluate the trigonometry against Scene Time.

    The pack-space offset (sway.x, 0, sway.y) becomes (sway.x, -sway.y, 0) in Blender, the same
    (x, y, z) -> (x, -z, y) the vertices themselves took.

    PHASE: the viewer's clock is `time.elapsed_secs_wrapped() % 3600` and Blender's is
    frame/fps from the scene start, so this reproduces the MOTION but not the game's absolute
    phase. For a rendered shot that is not a difference anyone can measure.
    """
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    n = ng.nodes
    gi = n.new("NodeGroupInput"); gi.location = (-900, 0)
    go = n.new("NodeGroupOutput"); go.location = (700, 0)

    at = n.new("GeometryNodeInputNamedAttribute"); at.location = (-900, -240)
    at.data_type = 'FLOAT_VECTOR'
    at.inputs["Name"].default_value = "eft_wind"
    sep = n.new("ShaderNodeSeparateXYZ"); sep.location = (-700, -240)
    ng.links.new(at.outputs["Attribute"], sep.inputs["Vector"])

    tm = n.new("GeometryNodeInputSceneTime"); tm.location = (-900, -420)

    def math(op, x, y, a=None, b=None):
        m = n.new("ShaderNodeMath"); m.operation = op; m.location = (x, y)
        for i, v in ((0, a), (1, b)):
            if v is None:
                continue
            if hasattr(v, "default_value") or hasattr(v, "is_linked"):
                ng.links.new(v, m.inputs[i])
            else:
                m.inputs[i].default_value = float(v)
        return m.outputs[0]

    # ph = seconds*speed + ph_const
    ph = math('ADD', -500, -380,
              math('MULTIPLY', -680, -420, tm.outputs["Seconds"], speed), sep.outputs["X"])
    # sway.x = sin(ph) + 0.5*sin(2.13*ph + 1.7)
    swx = math('ADD', -80, -140,
               math('SINE', -320, -100, ph),
               math('MULTIPLY', -240, -220,
                    math('SINE', -400, -220,
                         math('ADD', -560, -220, math('MULTIPLY', -700, -220, ph, 2.13), 1.7)),
                    0.5))
    # sway.y = cos(0.87*ph + 0.4) + 0.5*sin(1.61*ph)
    swz = math('ADD', -80, -560,
               math('COSINE', -320, -500,
                    math('ADD', -480, -500, math('MULTIPLY', -640, -500, ph, 0.87), 0.4)),
               math('MULTIPLY', -240, -640,
                    math('SINE', -400, -640, math('MULTIPLY', -560, -640, ph, 1.61)), 0.5))
    # amp = amount * strength * h
    amp = math('MULTIPLY', 60, -380, sep.outputs["Y"], float(amount) * float(strength))

    comb = n.new("ShaderNodeCombineXYZ"); comb.location = (400, -300)
    ng.links.new(math('MULTIPLY', 240, -160, swx, amp), comb.inputs["X"])
    ng.links.new(math('MULTIPLY', 240, -560, math('MULTIPLY', 240, -700, swz, -1.0), amp),
                 comb.inputs["Y"])

    sp = n.new("GeometryNodeSetPosition"); sp.location = (550, 0)
    ng.links.new(gi.outputs[0], sp.inputs["Geometry"])
    ng.links.new(comb.outputs["Vector"], sp.inputs["Offset"])
    ng.links.new(sp.outputs["Geometry"], go.inputs[0])
    return ng


def import_eftgrass(pack_dir, center=None, radius=None, max_clumps=400000,
                    points=None, wind=False, collection_name="grass", verbose=True):
    """Build the grass field as one merged mesh per kind. Returns the objects created.

    points   optional polyline in PACK space. When given it replaces `center` in the distance
             test: clumps are kept within `radius` of the POLYLINE rather than of a point.
    wind     port the pack's own WavingGrass parameters as a Geometry Nodes modifier. Defaults
             off so every existing caller keeps building byte-identical geometry.
    """
    pack_dir = os.path.abspath(pack_dir)
    side = json.load(open(os.path.join(pack_dir, "grass_sidecar.json"), encoding="utf-8"))
    fmt = int(side.get("format", 1) or 1)
    stride = 24 if fmt >= 2 else 20
    raw = np.fromfile(os.path.join(pack_dir, "grass.bin"), np.uint8)
    n = raw.size // stride
    raw = raw[: n * stride].reshape(n, stride)
    pos = raw[:, 0:12].copy().view("<f4").reshape(n, 3)
    rot = raw[:, 12:16].copy().view("<f4").ravel()
    scl = raw[:, 16:20].copy().view("<f4").ravel()
    kind = (raw[:, 20:24].copy().view("<u4").ravel() if stride == 24
            else np.zeros(n, np.uint32))

    keep = np.ones(n, bool)
    if points is not None and radius is not None:
        p = np.asarray(points, np.float32).reshape(-1, 3)
        r = float(radius)
        # bbox prefilter first: the exact test is O(clumps x segments) and the file holds 3.26 M
        lo, hi = p.min(0) - r, p.max(0) + r
        near = np.all((pos >= lo[None, :]) & (pos <= hi[None, :]), 1)
        keep &= near
        sub = np.nonzero(near)[0]
        if sub.size:
            ok = _dist2_to_polyline(pos[sub], p) <= r * r
            keep[sub[~ok]] = False
    elif center is not None and radius is not None:
        c = np.asarray(center, np.float32)
        d = pos - c[None, :]
        keep &= np.einsum("ij,ij->i", d, d) <= float(radius) ** 2
    idx = np.nonzero(keep)[0]
    if idx.size > max_clumps:                       # keep a spatially even subset, not a prefix
        idx = idx[np.linspace(0, idx.size - 1, max_clumps).astype(np.int64)]
    if verbose:
        print("[eftgrass] %d clumps in file (format %d, stride %d) -> %d selected"
              % (n, fmt, stride, idx.size))

    kinds = side.get("kinds") or []
    w = side.get("wind") or {}
    W_S, W_A, W_SP = (float(w.get("strength", 0.0) or 0.0), float(w.get("amount", 0.0) or 0.0),
                      float(w.get("speed", 0.0) or 0.0))
    windy = bool(wind) and W_S > 0.0 and W_A > 0.0
    wind_ng = _wind_nodes("eft_grass_wind", W_S, W_A, W_SP) if windy else None
    if wind and not windy:
        print("[eftgrass] wind requested but the sidecar ships none (%r); field stays static" % w)

    coll = bpy.data.collections.get(collection_name) or bpy.data.collections.new(collection_name)
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)

    # unit card: PLANES quads, built once and transformed per clump with numpy
    ang = (np.arange(PLANES, dtype=np.float32) * (np.pi / PLANES))
    dx, dz = np.cos(ang) * HALF_WIDTH, np.sin(ang) * HALF_WIDTH
    card = np.empty((PLANES * 4, 3), np.float32)
    for q in range(PLANES):
        card[q * 4 + 0] = (-dx[q], 0.0, -dz[q])
        card[q * 4 + 1] = (dx[q], 0.0, dz[q])
        card[q * 4 + 2] = (dx[q], HEIGHT, dz[q])
        card[q * 4 + 3] = (-dx[q], HEIGHT, -dz[q])
    # V FLIPPED for Blender. The viewer builds this card with v=1 at the BASE and v=0 at the tip,
    # because the pack's textures are authored for a top-left image origin. Blender samples from
    # the bottom-left, so copying those UVs verbatim renders every blade upside down: the soft
    # tips end up in the soil and the cut bases wave in the air.
    cuv = np.tile(np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32), (PLANES, 1))
    cf = np.vstack([np.array([[0, 1, 2], [0, 2, 3]], np.int32) + q * 4 for q in range(PLANES)])

    objs = []
    for ki in range(max(1, len(kinds))):
        sel = idx[kind[idx] == ki] if len(kinds) > 1 else idx
        if sel.size == 0:
            continue
        entry = kinds[ki] if ki < len(kinds) else {}
        tex = _resolve(pack_dir, entry.get("albedo", side.get("albedo", "")))
        if not tex:
            print("[eftgrass] kind %d: texture %r unresolved, slot kept, clumps skipped"
                  % (ki, entry.get("albedo")))
            continue
        m = sel.size
        s = scl[sel].astype(np.float32)[:, None, None]
        a = rot[sel].astype(np.float32)
        ca, sa = np.cos(a)[:, None], np.sin(a)[:, None]
        v = card[None, :, :] * s                              # (m, V, 3) scaled
        x, y, z = v[..., 0], v[..., 1], v[..., 2]
        # rotate about Y (pack space is Y-up), then translate, then Y-up -> Z-up
        rx = x * ca - z * sa
        rz = x * sa + z * ca
        px = rx + pos[sel, 0][:, None]
        py = y + pos[sel, 1][:, None]
        pz = rz + pos[sel, 2][:, None]
        verts = np.stack([px, -pz, py], -1).reshape(-1, 3)     # (x, -z, y)

        nv = card.shape[0]
        faces = (cf[None, :, :] + (np.arange(m, dtype=np.int32) * nv)[:, None, None]).reshape(-1, 3)
        uvs = np.tile(cuv, (m, 1))

        me = bpy.data.meshes.new("grass_kind_%d" % ki)
        me.vertices.add(verts.shape[0])
        me.vertices.foreach_set("co", verts.ravel())
        me.loops.add(faces.size)
        me.loops.foreach_set("vertex_index", faces.ravel())
        me.polygons.add(faces.shape[0])
        me.polygons.foreach_set("loop_start", np.arange(faces.shape[0], dtype=np.int32) * 3)
        me.update(calc_edges=True)
        uvl = me.uv_layers.new(name="UVMap", do_init=False)
        uvl.data.foreach_set("uv", uvs[faces.ravel() % nv].astype(np.float32).ravel())
        # NORMALS: the viewer bakes an UP normal into every card (oct_bits(Vec3::Y)) so grass takes
        # the same light as the ground it stands on. That trick is rasteriser-only. A path tracer
        # treats a shading normal pointing away from the viewer as backfacing and returns BLACK, so
        # copying it here turned the field into a black mat. Keep the true geometric normals and get
        # the same soft look from translucency in the material instead, which is what foliage
        # actually does with light.
        me.polygons.foreach_set("use_smooth", np.zeros(faces.shape[0], np.int8))
        me.update()
        me.materials.append(_material("grass_%d" % ki, tex,
                                      entry.get("tint") or side.get("tint") or [1, 1, 1]))
        o = bpy.data.objects.new("grass_kind_%d" % ki, me)
        if windy:
            # (phase constant, blade-local height) per vertex, the two per-vertex constants of the
            # viewer's sway. `y` is the card height ALREADY scaled by the clump's scale lane, and
            # it is clamped at 0 so the base cannot be pushed below the terrain it was placed on.
            phc = (pos[sel, 0] * 0.35 + pos[sel, 2] * 0.27).astype(np.float32)[:, None]
            wv = np.zeros((m, nv, 3), np.float32)
            wv[..., 0] = phc
            wv[..., 1] = np.maximum(y, 0.0)
            a = me.attributes.new("eft_wind", 'FLOAT_VECTOR', 'POINT')
            a.data.foreach_set("vector", wv.reshape(-1, 3).ravel())
            md = o.modifiers.new("eft_wind", 'NODES')
            md.node_group = wind_ng
        coll.objects.link(o)
        objs.append(o)
        if verbose:
            print("[eftgrass]   kind %2d  %7d clumps  %-30s" % (ki, m, os.path.basename(tex)))

    print("[eftgrass] %d kind mesh(es), %d clumps, %d planes each (%.2f x %.2f m cards)%s"
          % (len(objs), idx.size, PLANES, HALF_WIDTH * 2, HEIGHT,
             ", wind strength %.3g amount %.3g speed %.3g" % (W_S, W_A, W_SP) if windy else ""))
    return objs
