"""Blender importer for the .eftweap weapon format, with bone attachment.

Standalone: no imports from this repository. Run inside a live Blender session with

    exec(open(r"<repo>/tools/blender/import_eftweap.py").read())

then call ``import_eftweap(weap_dir, armature=None, bone="Weapon_root")``.

FORMAT (all declared by the pack's own manifest; nothing here is hardcoded that it states)
    manifest.json   vertexCount, indexCount, vertex {stride, fields[]}, submeshes[
                    {material, idxStart, idxCount}], materials[] (names, indexed by
                    submesh.material), materialTextures {name: {_MainTex, _BumpMap,
                    _SpecMap, _Cube}}, materialProps, aim, conventions.
    mesh.bin        [vertexCount * stride bytes of vertices][indexCount * u32 indices],
                    little-endian. Default layout: pos f32x3@0, nrm f32x3@12, uv f32x2@24.

CONVENTIONS
    manifest.conventions is {"world": "viewer (X-flipped from Unity)", "windingFlipped": true},
    i.e. the same already-conjugated space the map pack uses. Nothing is re-flipped here.

    Y-up -> Z-up is NOT applied to the mesh: the weapon is parented to a bone of an armature
    that already carries that rotation, so applying it again would rotate the rifle twice. When
    imported without an armature the caller gets the raw pack-space mesh and can parent it to
    whatever it likes.

ATTACHMENT
    The runtime parents the weapon's parts to the character's ``Weapon_root`` bone with an
    IDENTITY transform (viewer/src/npc.rs::attach_weapons), so this does the same: a Blender
    bone parent with the child's inverse cleared. Any offset would be inventing a grip the game
    does not apply.
"""

import json
import os
import struct

import bpy
import numpy as np
from mathutils import Matrix

__all__ = ["import_eftweap"]

_FMT = {"f32": ("<f4", 4), "u32": ("<u4", 4), "i32": ("<i4", 4),
        "u16": ("<u2", 2), "u8": ("u1", 1)}


def _parse_fmt(fmt):
    base, _, cnt = str(fmt).partition("x")
    n = int(cnt) if cnt else 1
    if base not in _FMT:
        return None
    dt, sz = _FMT[base]
    return dt, n, sz * n


def _img(path, cache, colorspace="sRGB"):
    if not path or not os.path.isfile(path):
        return None
    key = (os.path.normcase(path), colorspace)
    if key in cache:
        return cache[key]
    im = bpy.data.images.load(path, check_existing=True)
    try:
        im.colorspace_settings.name = colorspace
    except Exception:
        pass
    cache[key] = im
    return im


def _material(name, texs, props, base_dir, cache):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    out.location = (400, 0)

    def p(slot):
        v = (texs or {}).get(slot)
        return os.path.join(base_dir, v.replace("/", os.sep)) if v else None

    alb = _img(p("_MainTex"), cache, "sRGB")
    if alb:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = alb; t.location = (-700, 200)
        nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])

    nrm = _img(p("_BumpMap"), cache, "Non-Color")
    if nrm:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = nrm; t.location = (-700, -200)
        # BSG normal maps are DirectX convention: green points down, so invert G.
        sep = nt.nodes.new("ShaderNodeSeparateColor"); sep.location = (-480, -200)
        inv = nt.nodes.new("ShaderNodeMath"); inv.operation = 'SUBTRACT'
        inv.inputs[0].default_value = 1.0; inv.location = (-330, -240)
        comb = nt.nodes.new("ShaderNodeCombineColor"); comb.location = (-190, -200)
        nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-40, -200)
        nt.links.new(t.outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Red"], comb.inputs["Red"])
        nt.links.new(sep.outputs["Green"], inv.inputs[1])
        nt.links.new(inv.outputs["Value"], comb.inputs["Green"])
        nt.links.new(sep.outputs["Blue"], comb.inputs["Blue"])
        nt.links.new(comb.outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])

    # _SpecMap is a GLOSS map (high = shiny), the inverse of Blender's roughness.
    spec = _img(p("_SpecMap"), cache, "Non-Color")
    if spec:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = spec; t.location = (-700, -600)
        inv = nt.nodes.new("ShaderNodeInvert"); inv.location = (-400, -600)
        nt.links.new(t.outputs["Color"], inv.inputs["Color"])
        nt.links.new(inv.outputs["Color"], bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = 0.45

    pr = (props or {}).get(name) or {}
    try:
        bsdf.inputs["Metallic"].default_value = float(pr.get("_Metallic", 0.0) or 0.0)
    except Exception:
        pass
    return mat


def import_eftweap(weap_dir, armature=None, bone="Weapon_root", collection=None,
                   name_prefix="", vflip=True, verbose=True):
    """Import a weapon pack, optionally parented to ``bone`` of ``armature``.

    Returns the created object.
    """
    weap_dir = os.path.abspath(weap_dir)
    man = json.load(open(os.path.join(weap_dir, "manifest.json"), encoding="utf-8"))
    name = man.get("name") or os.path.basename(weap_dir)

    vspec = man.get("vertex") or {}
    stride = int(vspec.get("stride") or 32)
    fields = {}
    for f in vspec.get("fields") or []:
        pf = _parse_fmt(f.get("fmt"))
        if pf:
            fields[str(f.get("name"))] = (int(f.get("offset", 0)), pf)

    nv = int(man["vertexCount"])
    ni = int(man["indexCount"])
    raw = np.fromfile(os.path.join(weap_dir, "mesh.bin"), np.uint8)
    need = nv * stride + ni * 4
    if raw.size < need:
        raise ValueError("mesh.bin is %d bytes, manifest needs %d" % (raw.size, need))
    vb = raw[: nv * stride].reshape(nv, stride)
    idx = raw[nv * stride: nv * stride + ni * 4].copy().view("<u4").astype(np.int32)

    def attr(key, default_off, fmt):
        off, (dt, n, _sz) = fields.get(key, (default_off, _parse_fmt(fmt)))
        sz = np.dtype(dt).itemsize * n
        return vb[:, off:off + sz].copy().view(dt).reshape(nv, n)

    pos = attr("pos", 0, "f32x3").astype(np.float32)
    uv = attr("uv", 24, "f32x2").astype(np.float32)

    tri = idx.reshape(-1, 3)
    good = ((tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2]))
    ndrop = int((~good).sum())

    me = bpy.data.meshes.new(name_prefix + name)
    me.vertices.add(nv)
    me.vertices.foreach_set("co", pos.ravel())
    keep = np.nonzero(good)[0]
    tri_k = tri[good]
    nt_ = tri_k.shape[0]
    me.loops.add(nt_ * 3)
    me.loops.foreach_set("vertex_index", np.ascontiguousarray(tri_k.ravel(), np.int32))
    me.polygons.add(nt_)
    me.polygons.foreach_set("loop_start", np.arange(nt_, dtype=np.int32) * 3)
    me.update(calc_edges=True)

    corner = tri_k.ravel()
    uvl = me.uv_layers.new(name="UVMap", do_init=False)
    uvb = uv[corner].copy()
    if vflip:
        # Same reason as the map and character importers: the packs bake V for a top-left
        # image origin and Blender samples from the bottom-left.
        uvb[:, 1] = 1.0 - uvb[:, 1]
    uvl.data.foreach_set("uv", np.ascontiguousarray(uvb, np.float32).ravel())

    # materials, one slot per distinct material, faces assigned by submesh index range
    mats = man.get("materials") or []
    texs = man.get("materialTextures") or {}
    props = man.get("materialProps") or {}
    cache = {}
    slot_of = {}
    for mi, mname in enumerate(mats):
        me.materials.append(_material(str(mname), texs.get(str(mname)), props, weap_dir, cache))
        slot_of[mi] = len(me.materials) - 1

    face_mat = np.zeros(tri.shape[0], np.int32)
    for sm in man.get("submeshes") or []:
        f0 = int(sm.get("idxStart", 0)) // 3
        f1 = f0 + int(sm.get("idxCount", 0)) // 3
        mi = int(sm.get("material", 0))
        if f1 > f0:
            face_mat[f0:f1] = slot_of.get(mi, 0)
    me.polygons.foreach_set("material_index", np.ascontiguousarray(face_mat[keep], np.int32))

    # WINDING: conventions.windingFlipped means the stored order is already reversed relative to
    # the source, exactly like the map pack. Normals are recomputed from the faces, so nothing
    # needs flipping here; shade smooth and let Blender derive them.
    for p_ in me.polygons:
        p_.use_smooth = True
    me.update()

    obj = bpy.data.objects.new(name_prefix + name, me)
    (collection or bpy.context.scene.collection).objects.link(obj)

    if armature is not None:
        if bone not in armature.data.bones:
            raise ValueError("armature %r has no bone %r" % (armature.name, bone))
        obj.parent = armature
        obj.parent_type = 'BONE'
        obj.parent_bone = bone
        obj.matrix_parent_inverse = Matrix.Identity(4)
        # The runtime attaches with an IDENTITY transform, i.e. the weapon sits ON the bone's own
        # matrix. Blender's bone parenting instead places a child at the bone's TAIL, so the two
        # differ by a translation along the bone. Rather than derive that offset, let Blender
        # solve it: assign the world matrix we want and it back-computes the local basis. The
        # result is expressed in bone space, so it stays correct for every frame of the animation.
        pb = armature.pose.bones.get(bone)
        if pb is not None:
            bpy.context.view_layer.update()
            # UNDO THE BONE-AXIS CORRECTION. Blender bones must point along their own local +Y, so
            # the character importer rotates every bone by q4 when it builds the rest pose
            # (rest = bind_world @ q4) and publishes q4 on the armature. The engine attaches the
            # weapon to the bone's OWN matrix, which is pose.matrix @ q4_inverse; using
            # pose.matrix directly rotates the rifle by that convention and it reads sideways.
            q4 = armature.get("eft_q4")
            corr = Matrix.Identity(4)
            if q4 is not None and len(q4) == 16:
                corr = Matrix([[q4[r * 4 + c] for c in range(4)] for r in range(4)]).inverted_safe()
            obj.matrix_world = armature.matrix_world @ pb.matrix @ corr

    if verbose:
        print("[eftweap] %s  %d verts, %d tris (%d degenerate dropped), %d submeshes, %d materials"
              % (name, nv, nt_, ndrop, len(man.get("submeshes") or []), len(mats)))
        print("[eftweap] textures %d  attached to %s"
              % (len(cache), ("%s/%s" % (armature.name, bone)) if armature else "nothing (free object)"))
        aim = man.get("aim") or {}
        if aim:
            print("[eftweap] aim fov %.2f  eyeRelief %.3f" % (aim.get("fov", 0.0), aim.get("eyeRelief", 0.0)))
    return obj
