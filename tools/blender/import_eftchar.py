"""Blender importer for the ``.eftchar`` animated-character container.

Standalone: only ``bpy``, ``mathutils``, ``numpy``, ``json``, ``os``, ``struct``. Designed to be
run inside a live Blender session with ``exec(open(path).read())``.

    arm, meshes, action = import_eftchar(r"...\\out\\characters\\scav", "walk_aim_0", loops=4)

--------------------------------------------------------------------------------------------------
THE MATH, because every one of these is a silent-corruption trap
--------------------------------------------------------------------------------------------------

Frames.  The pack is RIGHT-HANDED, Y-UP, metres.  The Unity handedness conjugation (G3 =
diag(-1,1,1)) is ALREADY BAKED into every datum -- positions, quaternions, tangents, winding.  It is
never re-applied here.  The only change Blender needs is Y-up -> Z-up::

    blender_xyz = (x, -z, y)          # Matrix.Rotation(+90 deg, 4, 'X'), det = +1

applied ONCE, as the armature object's matrix_world.  Meshes are object-children of the armature
with an identity local matrix, so they inherit it exactly once.  No axis is ever mirrored.

Quaternions are stored XYZW.  ``mathutils.Quaternion`` is WXYZ.  Everything here converts
explicitly; the raw arrays are never handed to mathutils.

Matrices are COLUMN-VECTOR (p' = M p), composed ``parent @ local``, exactly as the pack's own
skeleton builder does.  ``inverseBindposes`` rows are 16 floats ROW-MAJOR, i.e. reshape(4,4) with
the translation in column 3.

Rest pose = the BIND pose, not the skeleton's local-TRS pose.  These are NOT the same on this
data: measured on out/characters/scav, ``Wrest[b] @ inverseBindpose[b]`` is a constant renderer
matrix ``T_r`` for 36 of 58 bound bones but deviates by up to ~8 degrees at the elbows and by 2.7 cm
through the hand chain.  The pack skins with ``sum_i w_i * WorldPose(j_i) @ InverseBindpose(j_i) @ v``
while Blender skins with ``sum_i w_i * Pose(j_i) @ RestMatrix(j_i)^-1 @ v``.  Reproducing the pack
therefore requires the armature's REST matrices to be the bind matrices::

    Wbind[b] = T_r @ inv(inverseBindpose[b])          # bound bones
    Wbind[b] = Wbind[parent] @ Lrest[b]               # bones no mesh binds (21 of 79 here)

and the mesh vertices to be carried into the same frame by ``v' = T_r @ v``.  ``T_r`` cancels
algebraically out of the deformation (``Wpose @ IBP @ T_r^-1 @ T_r @ v``), so it only chooses the
frame the REST pose is drawn in; it is recovered as the medoid of ``Wrest[b] @ IBP[b]``.
Pre-skinning the vertices into the skeleton rest pose instead -- the obvious shortcut -- is
linear-blend-skinning applied twice and is wrong by that 2.7 cm wherever a vertex spans two bones
with different bind deltas.

Bone roll/length must not perturb the skinning, so the rest orientation is decoupled from the joint
frame by a per-bone rigid ``Q``: rest matrix ``R_b = Wbind[b] @ Q``.  For any Q, the exact pose
channel value is::

    matrix_basis(b) = Q^-1 @ (Lbind[b]^-1 @ Lclip[b]) @ Q          Lbind[b] = Wbind[par]^-1 @ Wbind[b]

(verified against Blender 5.1: pose.matrix(b) == pose.matrix(par) @ (matrix_local(par)^-1 @
matrix_local(b)) @ matrix_basis(b), to 3e-7).  This is purely local -- no chain walk per frame --
and it is why writing world-space or raw-local values onto pose bones mangles the rig.  ``Q`` is
DERIVED, never authored: the rig's dominant bone axis is measured from the bind pose (this rig is
+X-down-the-bone, 44 of 48 parents) and ``Q`` is the signed axis permutation carrying it to
Blender's +Y.  Being a signed permutation, it cannot manufacture shear out of a scale track.

Bones with no animation track are NOT left at identity: their bind local differs from their rest
local, so they get the constant basis ``Q^-1 @ Lbind^-1 @ Lrest @ Q`` written to the pose channel.

UVs.  The pack has ``uvVFlipBaked: true`` -- V is already flipped for a TOP-LEFT-origin sampler
(wgpu).  Blender samples BOTTOM-LEFT, like Unity.  So V is flipped BACK here: ``v = 1 - v_pack``.

Winding is already reversed in the pack for the right-handed frame, and every transform applied
here has det > 0, so indices are used verbatim.

--------------------------------------------------------------------------------------------------
Known limitations
--------------------------------------------------------------------------------------------------
* Every mesh at the selected LOD is imported, matching viewer/src/character/rig.rs.  A pack can ship
  alternate variants of one garment (scav: ``Top_wild_bomber_base/_AR/_CR``) that co-exist in space;
  pass ``mesh_filter`` to pick one.
* Root motion is stripped from the bone tracks by the extractor.  ``apply_root_motion=True``
  keyframes the armature object from the clip's ``rootMotion`` channel; by default, like the
  viewer, the character animates in place.
* No blend shapes, no IK solving, no controller state machine or blend trees -- one clip at a time.
* ``_SpecMap`` is a GLOSS map (high = shiny); it is inverted into Roughness rather than bound raw.
"""

import json
import os
import struct  # noqa: F401  (part of the sanctioned import set; numpy does the actual decoding)

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

__all__ = ["import_eftchar", "main"]

# (x, y, z) -> (x, -z, y).  A rotation of +90 degrees about X.  det = +1.  Applied exactly once.
Y_UP_TO_Z_UP = Matrix(((1.0, 0.0, 0.0, 0.0),
                       (0.0, 0.0, -1.0, 0.0),
                       (0.0, 1.0, 0.0, 0.0),
                       (0.0, 0.0, 0.0, 1.0)))

_EPS_W = 1e-6          # weight below which an influence is dropped
_MIN_BONE_LEN = 0.005  # metres
_DEF_BONE_LEN = 0.02   # metres, for leaves with no usable parent length
_AXIS_ALIGNED = 0.90   # |dot| above which a bone counts as "aimed along" a local axis


# ----------------------------------------------------------------------------------------------
# small numeric helpers (numpy, column-vector convention throughout)
# ----------------------------------------------------------------------------------------------

def _mat3_from_quat_xyzw(q):
    """XYZW quaternion -> 3x3 rotation.  NOT WXYZ; getting this backwards is the classic bug."""
    x, y, z, w = (float(v) for v in q)
    n = (x * x + y * y + z * z + w * w) ** 0.5
    if n < 1e-12:
        return np.eye(3)
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array((
        (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)),
    ), dtype=np.float64)


def _compose(pos, quat_xyzw, scale):
    """Local TRS -> 4x4.  Scale multiplies the COLUMNS (skeleton.py:82)."""
    m = np.eye(4)
    m[:3, :3] = _mat3_from_quat_xyzw(quat_xyzw) * np.asarray(scale, dtype=np.float64)[None, :]
    m[:3, 3] = np.asarray(pos, dtype=np.float64)
    return m


def _to_bl(m):
    return Matrix([[float(v) for v in row] for row in m])


def _is_sheared(m3, tol=0.02):
    c = np.asarray(m3, dtype=np.float64)
    n = np.linalg.norm(c, axis=0)
    if np.any(n < 1e-9):
        return True
    u = c / n
    return max(abs(float(u[:, 0] @ u[:, 1])),
               abs(float(u[:, 0] @ u[:, 2])),
               abs(float(u[:, 1] @ u[:, 2]))) > tol


# ----------------------------------------------------------------------------------------------
# manifest / blob reading
# ----------------------------------------------------------------------------------------------

class _Pack(object):
    """A parsed .eftchar directory.  Every stride and offset comes from the manifest."""

    def __init__(self, char_dir):
        self.dir = os.path.abspath(char_dir)
        mpath = os.path.join(self.dir, "manifest.json")
        if not os.path.isfile(mpath):
            raise IOError("no manifest.json in %s" % self.dir)
        with open(mpath, "r", encoding="utf-8") as fh:
            self.m = json.load(fh)

        if int(self.m.get("version", 0)) != 1:
            raise ValueError("unsupported .eftchar version %r" % self.m.get("version"))
        conv = self.m.get("conventions") or {}
        if conv.get("quatOrder", "xyzw") != "xyzw":
            raise ValueError("unexpected quatOrder %r" % conv.get("quatOrder"))
        if conv.get("upAxis", "y") != "y":
            raise ValueError("unexpected upAxis %r" % conv.get("upAxis"))
        if not conv.get("windingFlipped", True):
            raise ValueError("pack predates the winding flip; indices cannot be used verbatim")
        self.uv_v_flipped = bool(conv.get("uvVFlipBaked", True))

        # vertex layout, read from the manifest -- nothing is hardcoded.
        vl = self.m["vertexLayout"]
        self.stride = int(vl["stride"])
        self.attrs = {a["name"]: (int(a["offset"]), a["format"]) for a in vl["attributes"]}
        for need, fmt in (("position", "f32x3"), ("normal", "f32x3"), ("uv0", "f32x2"),
                          ("jointIndex", "u16x4"), ("jointWeight", "f32x4")):
            if need not in self.attrs:
                raise ValueError("vertexLayout is missing %r" % need)
            if self.attrs[need][1] != fmt:
                raise ValueError("attribute %s is %s, expected %s"
                                 % (need, self.attrs[need][1], fmt))
        if self.m.get("indexFormat", "u32") != "u32":
            raise ValueError("unsupported indexFormat %r" % self.m.get("indexFormat"))

        blobs = self.m.get("blobs") or {}
        self.skin = self._read_blob(blobs.get("skin"), "skin.bin")
        self.anim = self._read_blob(blobs.get("anim"), "anim.bin")

        sk = self.m["skeleton"]
        self.n_bones = int(sk["boneCount"])
        for key in ("names", "paths", "parents", "localPos", "localRot", "localScale"):
            if len(sk[key]) != self.n_bones:
                raise ValueError("skeleton.%s has %d entries, boneCount is %d"
                                 % (key, len(sk[key]), self.n_bones))
        self.bone_names = list(sk["names"])
        self.parents = [int(p) for p in sk["parents"]]
        for i, p in enumerate(self.parents):
            if p >= i:
                raise ValueError("skeleton is not topologically ordered at bone %d" % i)
        self.local_pos = np.asarray(sk["localPos"], dtype=np.float64)
        self.local_rot = np.asarray(sk["localRot"], dtype=np.float64)
        self.local_scale = np.asarray(sk["localScale"], dtype=np.float64)

        self.clips_by_name = {}
        for c in self.m.get("clips") or []:
            self.clips_by_name.setdefault(c["name"], c)

    def _read_blob(self, spec, default_name):
        name = (spec or {}).get("file", default_name)
        path = os.path.join(self.dir, name)
        if not os.path.isfile(path):
            return b""
        with open(path, "rb") as fh:
            data = fh.read()
        want = (spec or {}).get("totalByteLength")
        if want is not None and len(data) != int(want):
            raise ValueError("%s is %d bytes, manifest declares %d" % (name, len(data), int(want)))
        return data

    # -- skeleton -------------------------------------------------------------------------------

    def rest_locals(self):
        return [_compose(self.local_pos[i], self.local_rot[i], self.local_scale[i])
                for i in range(self.n_bones)]

    def rest_worlds(self, locals_):
        w = [None] * self.n_bones
        for i in range(self.n_bones):
            p = self.parents[i]
            w[i] = locals_[i] if p < 0 else w[p] @ locals_[i]
        return w

    def merged_inverse_bindposes(self):
        """One IBP per bound bone.  All meshes agree to ~7e-7 on shared bones (verified on scav).

        Attachments are excluded: they pin every vertex to bone 0 with full weight and carry a
        separate bone + local TRS, so their tables describe a different binding.
        """
        out, worst = {}, 0.0
        for md in self.m.get("meshes", []):
            rows = md.get("inverseBindposes")
            if not rows:
                continue
            if len(rows) != self.n_bones:
                raise ValueError("%s has a %d-row bindpose table, boneCount is %d"
                                 % (md.get("name"), len(rows), self.n_bones))
            tab = np.asarray(rows, dtype=np.float64).reshape(self.n_bones, 4, 4)
            for b in md.get("boundBones", []):
                b = int(b)
                if b < 0 or b >= self.n_bones:
                    raise ValueError("%s binds bone %d, out of range" % (md.get("name"), b))
                if b in out:
                    worst = max(worst, float(np.abs(out[b] - tab[b]).max()))
                else:
                    out[b] = tab[b]
        return out, worst


def _renderer_matrix(rest_worlds, ibps):
    """T_r: the medoid of ``Wrest[b] @ IBP[b]``.

    Cancels out of the deformation, so a wrong pick only tilts the frame the rest pose is drawn in.
    The medoid is used rather than a mean because the deviating bones are real bind-vs-rest deltas,
    not noise, and averaging them would leave the whole rig slightly rotated.
    """
    bones = sorted(ibps)
    if not bones:
        return np.eye(4), 0, 0
    cands = [rest_worlds[b] @ ibps[b] for b in bones]
    support = [sum(1 for e in cands if float(np.abs(e - d).max()) < 1e-3) for d in cands]
    k = int(np.argmax(support))
    return cands[k], support[k], len(bones)


def _bind_worlds(pack, rest_locals, rest_worlds, ibps, t_r):
    """Bone world matrices in the BIND pose, rig space.  Unbound bones inherit through the chain."""
    wb = [None] * pack.n_bones
    for i in range(pack.n_bones):
        if i in ibps:
            wb[i] = t_r @ np.linalg.inv(ibps[i])
        else:
            p = pack.parents[i]
            wb[i] = rest_worlds[i].copy() if p < 0 else wb[p] @ rest_locals[i]
    return wb


def _bind_locals(pack, bind_worlds):
    out = []
    for i in range(pack.n_bones):
        p = pack.parents[i]
        out.append(bind_worlds[i].copy() if p < 0 else np.linalg.inv(bind_worlds[p]) @ bind_worlds[i])
    return out


# ----------------------------------------------------------------------------------------------
# bone orientation: derived from the rig, never authored
# ----------------------------------------------------------------------------------------------

_PERMS = {
    (0, +1): ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),   # +X -> +Y
    (0, -1): ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),   # -X -> +Y
    (1, +1): ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),    # identity
    (1, -1): ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
    (2, +1): ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),   # +Z -> +Y
    (2, -1): ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
}


def _children(parents):
    ch = {}
    for i, p in enumerate(parents):
        if p >= 0:
            ch.setdefault(p, []).append(i)
    return ch


def _derive_bone_axis(pack, bind_worlds, children):
    """Which LOCAL axis of a joint frame runs down the bone?  Measured, not assumed."""
    votes, total = {}, 0
    for b, kids in children.items():
        pb = bind_worlds[b][:3, 3]
        for c in kids:
            d = bind_worlds[c][:3, 3] - pb
            ln = float(np.linalg.norm(d))
            if ln < 1e-4:
                continue
            d = d / ln
            dots = [float(bind_worlds[b][:3, a] @ d) for a in range(3)]
            a = int(np.argmax(np.abs(dots)))
            total += 1
            if abs(dots[a]) >= _AXIS_ALIGNED:
                key = (a, 1 if dots[a] > 0 else -1)
                votes[key] = votes.get(key, 0) + 1
            break
    if not votes or total == 0:
        return None, 0, total
    key = max(votes, key=lambda k: votes[k])
    if votes[key] * 2 < total:          # no clear majority -> leave the joint frames alone
        return None, votes[key], total
    return key, votes[key], total


def _q_matrix(axis_key):
    q = np.eye(4)
    if axis_key is None:
        return q
    p = np.asarray(_PERMS[axis_key], dtype=np.float64)
    if abs(float(np.linalg.det(p)) - 1.0) > 1e-9:
        raise ValueError("bone-axis permutation is not a rotation")
    q[:3, :3] = p
    return q


def _bone_lengths(pack, bind_worlds, children):
    """Head-to-child distance where there is a child, a shrunk parent length otherwise."""
    lens = [0.0] * pack.n_bones
    for b in range(pack.n_bones):
        best = 0.0
        for c in children.get(b, ()):
            d = float(np.linalg.norm(bind_worlds[c][:3, 3] - bind_worlds[b][:3, 3]))
            if d > best:
                best = d
        lens[b] = best
    for b in range(pack.n_bones):
        if lens[b] < _MIN_BONE_LEN:
            p = pack.parents[b]
            lens[b] = max(_MIN_BONE_LEN,
                          min(_DEF_BONE_LEN, lens[p] * 0.5) if p >= 0 and lens[p] > 0
                          else _DEF_BONE_LEN)
    return lens


# ----------------------------------------------------------------------------------------------
# armature
# ----------------------------------------------------------------------------------------------

def _ensure_object_mode():
    obj = bpy.context.view_layer.objects.active
    if obj is not None and obj.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass


def _build_armature(pack, rest_mats, lengths, collection, name):
    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    collection.objects.link(arm_obj)

    _ensure_object_mode()
    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        ebs = []
        for i in range(pack.n_bones):
            eb = arm_data.edit_bones.new(pack.bone_names[i])
            eb.head = (0.0, 0.0, 0.0)
            eb.tail = (0.0, max(lengths[i], _MIN_BONE_LEN), 0.0)
            # The matrix setter derives head/tail-direction/roll and PRESERVES length, so the
            # joint frame lands exactly and the length choice cannot perturb it.
            eb.matrix = _to_bl(rest_mats[i])
            eb.use_deform = True
            ebs.append(eb)
        for i in range(pack.n_bones):
            p = pack.parents[i]
            if p >= 0:
                ebs[i].parent = ebs[p]
                ebs[i].use_connect = False
        bone_names = [eb.name for eb in ebs]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        if prev_active is not None:
            try:
                bpy.context.view_layer.objects.active = prev_active
            except Exception:
                pass

    arm_obj.matrix_world = Y_UP_TO_Z_UP        # the ONE Y-up -> Z-up application
    for pb in arm_obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return arm_obj, bone_names


# ----------------------------------------------------------------------------------------------
# materials
# ----------------------------------------------------------------------------------------------

_ALPHA_ROLE = {"OPAQUE": "OPAQUE", "MASK": "MASK", "CUTOUT": "MASK", "CUTOFF": "MASK",
               "BLEND": "BLEND", "FADE": "BLEND", "TRANSPARENT": "BLEND"}
_ALPHA_MODE = {0: "OPAQUE", 1: "MASK", 2: "BLEND", 3: "BLEND"}


def _alpha_mode(spec):
    role = spec.get("role")
    if isinstance(role, str) and role.upper() in _ALPHA_ROLE:
        return _ALPHA_ROLE[role.upper()]
    floats = spec.get("floats") or {}
    return _ALPHA_MODE.get(int(round(float(floats.get("_Mode", 0.0)))), "OPAQUE")


def _load_image(base_dir, rel, srgb):
    if not rel:
        return None
    path = rel if os.path.isabs(rel) else os.path.join(base_dir, rel.replace("/", os.sep))
    if not os.path.isfile(path):
        return None
    img = bpy.data.images.load(path, check_existing=True)
    try:
        img.colorspace_settings.name = "sRGB" if srgb else "Non-Color"
    except Exception:
        pass
    if not srgb:
        img.alpha_mode = "CHANNEL_PACKED"
    return img


def _build_material(pack, spec, prefix, use_gloss):
    mat = bpy.data.materials.new(prefix + str(spec.get("name", "material")))
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if bsdf is None:
        return mat
    mat.use_backface_culling = True          # the character path draws single-sided

    texs = spec.get("textures") or {}
    floats = spec.get("floats") or {}
    colors = spec.get("colors") or {}
    mode = _alpha_mode(spec)

    col = colors.get("_Color")
    if isinstance(col, (list, tuple)) and len(col) >= 3:
        bsdf.inputs["Base Color"].default_value = (float(col[0]), float(col[1]), float(col[2]), 1.0)
    bsdf.inputs["Metallic"].default_value = float(floats.get("_Metallic", 0.0))
    bsdf.inputs["Roughness"].default_value = 0.6

    y = 300
    albedo = _load_image(pack.dir, texs.get("_MainTex"), srgb=True)
    if albedo is not None:
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = albedo
        n.label = "_MainTex"
        n.location = (-620, y)
        y -= 320
        nt.links.new(n.outputs["Color"], bsdf.inputs["Base Color"])
        if mode == "MASK":
            cut = nt.nodes.new("ShaderNodeMath")
            cut.operation = "GREATER_THAN"
            cut.inputs[1].default_value = float(floats.get("_Cutoff", 0.5))
            cut.location = (-320, y + 200)
            nt.links.new(n.outputs["Alpha"], cut.inputs[0])
            nt.links.new(cut.outputs["Value"], bsdf.inputs["Alpha"])
        elif mode == "BLEND":
            nt.links.new(n.outputs["Alpha"], bsdf.inputs["Alpha"])

    nrm = _load_image(pack.dir, texs.get("_BumpMap"), srgb=False)
    if nrm is not None:
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = nrm
        n.label = "_BumpMap"
        n.location = (-620, y)
        y -= 320
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.location = (-320, y + 260)
        nm.inputs["Strength"].default_value = float(floats.get("_BumpScale", 1.0))
        nt.links.new(n.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])

    # _SpecMap is a GLOSS map (high = shiny) -- the inverse of roughness.  Binding it raw crushes
    # the shading; it needs the 1 - x pass the extractor does not do.
    gloss = _load_image(pack.dir, texs.get("_SpecMap"), srgb=False) if use_gloss else None
    if gloss is not None:
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = gloss
        n.label = "_SpecMap (gloss)"
        n.location = (-620, y)
        inv = nt.nodes.new("ShaderNodeInvert")
        inv.location = (-320, y)
        nt.links.new(n.outputs["Color"], inv.inputs["Color"])
        nt.links.new(inv.outputs["Color"], bsdf.inputs["Roughness"])

    if mode == "BLEND":
        for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
            if hasattr(mat, attr):
                try:
                    setattr(mat, attr, val)
                except Exception:
                    pass
    return mat


# ----------------------------------------------------------------------------------------------
# meshes
# ----------------------------------------------------------------------------------------------

def _slice_vertices(pack, md):
    v = int(md["vertexCount"])
    off = int(md["vertexByteOffset"])
    if int(md["vertexByteLength"]) != v * pack.stride:
        raise ValueError("%s: vertexByteLength != vertexCount * stride" % md["name"])
    if off + v * pack.stride > len(pack.skin):
        raise ValueError("%s: vertex block runs past skin.bin" % md["name"])
    blk = np.frombuffer(pack.skin, dtype=np.uint8, count=v * pack.stride, offset=off)
    blk = blk.reshape(v, pack.stride)

    def f32(name, n):
        o = pack.attrs[name][0]
        return blk[:, o:o + 4 * n].copy().view("<f4").reshape(v, n).astype(np.float64)

    ji_o = pack.attrs["jointIndex"][0]
    ji = blk[:, ji_o:ji_o + 8].copy().view("<u2").reshape(v, 4)
    return f32("position", 3), f32("normal", 3), f32("uv0", 2), ji, f32("jointWeight", 4)


def _slice_indices(pack, md):
    n = int(md["indexCount"])
    off = int(md["indexByteOffset"])
    if int(md["indexByteLength"]) != n * 4:
        raise ValueError("%s: indexByteLength != indexCount * 4" % md["name"])
    if off + n * 4 > len(pack.skin):
        raise ValueError("%s: index block runs past skin.bin" % md["name"])
    if n % 3:
        raise ValueError("%s: indexCount %d is not a multiple of 3" % (md["name"], n))
    return np.frombuffer(pack.skin, dtype="<u4", count=n, offset=off).astype(np.int64)


def _build_mesh(pack, md, t_r, mats, bone_names, arm_obj, collection, prefix):
    pos, nrm, uv, ji, jw = _slice_vertices(pack, md)
    idx = _slice_indices(pack, md)
    if idx.size and int(idx.max()) >= pos.shape[0]:
        raise ValueError("%s: index %d exceeds vertexCount" % (md["name"], int(idx.max())))

    # Carry the geometry into rig space.  T_r is rigid for every pack seen, but a sheared one would
    # be baked here just the same: this is a per-vertex bake, not an object matrix, so nothing is
    # silently dropped.
    r3 = t_r[:3, :3]
    pos_w = pos @ r3.T + t_r[:3, 3][None, :]
    n_inv_t = np.linalg.inv(r3).T
    nrm_w = nrm @ n_inv_t.T
    ln = np.linalg.norm(nrm_w, axis=1, keepdims=True)
    nrm_w = np.divide(nrm_w, np.where(ln < 1e-12, 1.0, ln))

    tris = idx.reshape(-1, 3)
    me = bpy.data.meshes.new(prefix + md["name"])
    me.from_pydata(pos_w.tolist(), [], tris.tolist())
    me.update()
    me.validate(verbose=False, clean_customdata=False)

    # UVs: undo the pack's baked V-flip -- it targets a top-left sampler, Blender is bottom-left.
    uv_l = uv.copy()
    if pack.uv_v_flipped:
        uv_l[:, 1] = 1.0 - uv_l[:, 1]
    lay = me.uv_layers.new(name="UVMap")
    loop_v = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("vertex_index", loop_v)
    lay.data.foreach_set("uv", uv_l[loop_v].astype(np.float32).ravel())

    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    try:
        me.normals_split_custom_set_from_vertices([tuple(n) for n in nrm_w])
    except Exception:
        pass

    # submesh -> material slot
    used, slot_of = [], {}
    for sub in md.get("submeshes", []):
        mi = int(sub["material"])
        if mi not in slot_of:
            slot_of[mi] = len(used)
            used.append(mi)
            me.materials.append(mats.get(mi))
    if len(used) > 1:
        mat_idx = np.zeros(tris.shape[0], dtype=np.int32)
        for sub in md.get("submeshes", []):
            s, c = int(sub["indexStart"]) // 3, int(sub["indexCount"]) // 3
            mat_idx[s:s + c] = slot_of[int(sub["material"])]
        me.polygons.foreach_set("material_index", mat_idx)
    me.update()

    obj = bpy.data.objects.new(prefix + md["name"], me)
    collection.objects.link(obj)
    obj.parent = arm_obj
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.matrix_basis = Matrix.Identity(4)     # mesh space == armature space, so the modifier is exact
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj
    mod.use_vertex_groups = True

    # weights: drop zeros, renormalise, accumulate (a vertex may list one bone twice)
    n_w = 0
    per_bone = {}
    for vi, (bs, ws) in enumerate(zip(ji.tolist(), jw.tolist())):
        tot = 0.0
        for w in ws:
            if w > _EPS_W:
                tot += w
        if tot <= 0.0:
            per_bone.setdefault(int(bs[0]), []).append((vi, 1.0))
            n_w += 1
            continue
        inv = 1.0 / tot
        for b, w in zip(bs, ws):
            if w > _EPS_W:
                per_bone.setdefault(int(b), []).append((vi, w * inv))
                n_w += 1
    for b in sorted(per_bone):
        if b >= pack.n_bones:
            raise ValueError("%s: vertex references bone %d, boneCount is %d"
                             % (md["name"], b, pack.n_bones))
        vg = obj.vertex_groups.new(name=bone_names[b])
        for vi, w in per_bone[b]:
            vg.add((vi,), w, "ADD")
    return obj, pos.shape[0], tris.shape[0], n_w


# ----------------------------------------------------------------------------------------------
# animation
# ----------------------------------------------------------------------------------------------

def _channel(pack, spec, frames, comps):
    off, blen = int(spec["byteOffset"]), int(spec["byteLength"])
    if blen % 4:
        raise ValueError("channel byteLength %d is not divisible by 4" % blen)
    if int(spec["components"]) != comps:
        raise ValueError("channel has %d components, expected %d" % (spec["components"], comps))
    if blen != frames * comps * 4:
        raise ValueError("channel is %d bytes, expected %d" % (blen, frames * comps * 4))
    if off + blen > len(pack.anim):
        raise ValueError("channel range runs past anim.bin")
    return np.frombuffer(pack.anim, dtype="<f4", count=frames * comps,
                         offset=off).reshape(frames, comps).astype(np.float64)


def _clip_locals(pack, clip):
    """Per bone, per frame: the clip's LOCAL matrix.  Absent channels fall back to the bind pose."""
    f = int(clip["frameCount"])
    if f < 1:
        raise ValueError("clip %r has frameCount %d" % (clip.get("name"), f))
    pos = np.repeat(pack.local_pos[:, None, :], f, axis=1)
    rot = np.repeat(pack.local_rot[:, None, :], f, axis=1)
    scl = np.repeat(pack.local_scale[:, None, :], f, axis=1)
    tracked = set()
    for t in clip.get("tracks", []):
        b = int(t["bone"])
        if b < 0 or b >= pack.n_bones:
            raise ValueError("clip %r drives bone %d, boneCount is %d"
                             % (clip.get("name"), b, pack.n_bones))
        tracked.add(b)
        if t.get("position"):
            pos[b] = _channel(pack, t["position"], f, 3)
        if t.get("rotation"):
            rot[b] = _channel(pack, t["rotation"], f, 4)
        if t.get("scale"):
            scl[b] = _channel(pack, t["scale"], f, 3)
    mats = np.empty((pack.n_bones, f, 4, 4))
    for b in range(pack.n_bones):
        for k in range(f):
            mats[b, k] = _compose(pos[b, k], rot[b, k], scl[b, k])
    return mats, tracked, f


def _fcurve(action, arm_obj, path, index, group):
    return action.fcurve_ensure_for_datablock(arm_obj, path, index=index, group_name=group)


def _fill(fc, frames, values):
    n = len(frames)
    fc.keyframe_points.add(n)
    co = np.empty(n * 2, dtype=np.float32)
    co[0::2] = frames
    co[1::2] = values
    fc.keyframe_points.foreach_set("co", co)
    fc.keyframe_points.foreach_set("interpolation", [1] * n)   # LINEAR, matching the pack's sampler
    fc.update()


def _build_action(pack, clip, arm_obj, bone_names, bind_locals, q4, loops, action_name,
                  apply_root_motion):
    mats, tracked, f = _clip_locals(pack, clip)
    q_inv = np.linalg.inv(q4)

    # matrix_basis(b) = Q^-1 @ (Lbind[b]^-1 @ Lclip[b]) @ Q  -- purely local, exact for any Q.
    loc = np.empty((pack.n_bones, f, 3))
    quat = np.empty((pack.n_bones, f, 4))          # WXYZ, Blender order
    scale = np.empty((pack.n_bones, f, 3))
    sheared = 0
    for b in range(pack.n_bones):
        lb_inv = np.linalg.inv(bind_locals[b])
        prev = None
        for k in range(f):
            basis = q_inv @ (lb_inv @ mats[b, k]) @ q4
            if _is_sheared(basis[:3, :3]):
                sheared += 1
            t, r, s = _to_bl(basis).decompose()
            if prev is not None and (r.w * prev[0] + r.x * prev[1]
                                     + r.y * prev[2] + r.z * prev[3]) < 0.0:
                r = Quaternion((-r.w, -r.x, -r.y, -r.z))   # keep the fcurve on the short arc
            prev = (r.w, r.x, r.y, r.z)
            loc[b, k] = (t.x, t.y, t.z)
            quat[b, k] = (r.w, r.x, r.y, r.z)
            scale[b, k] = (s.x, s.y, s.z)

    # frame numbering: clip frame 0 -> scene frame 1; each extra cycle re-plays frames 1..f-1,
    # because frameCount = round(duration * rate) + 1 makes the last frame coincide with the first.
    loops = max(1, int(loops))
    src, cycle = [0], [0]
    for c in range(loops):
        for k in range(1, f):
            src.append(k)
            cycle.append(c)
    frames = [1 + i for i in range(len(src))]
    src = np.asarray(src, dtype=np.int64)
    cycle = np.asarray(cycle, dtype=np.float64)

    action = bpy.data.actions.new(action_name)
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    arm_obj.animation_data.action = action

    n_fc = n_key = 0
    for b in range(pack.n_bones):
        name = bone_names[b]
        pb = arm_obj.pose.bones.get(name)
        if pb is None:
            continue
        pb.rotation_mode = "QUATERNION"
        for path, arr, ident in (("location", loc[b], (0.0, 0.0, 0.0)),
                                 ("rotation_quaternion", quat[b], (1.0, 0.0, 0.0, 0.0)),
                                 ("scale", scale[b], (1.0, 1.0, 1.0))):
            seq = arr[src]
            varies = bool(np.abs(seq - seq[0]).max() > 1e-7) if len(seq) > 1 else False
            const = tuple(float(v) for v in seq[0])
            setattr(pb, path, const)                        # so the pose is right with no action too
            if not varies and max(abs(c - i) for c, i in zip(const, ident)) < 1e-7:
                continue                                    # identity and static: nothing to store
            # A constant, non-identity channel still belongs IN the action: a bone whose bind local
            # differs from its clip local holds a fixed offset, and leaving that only on the pose
            # bone would leak into whatever action is assigned next.  Two keys pin it.
            keys = frames if varies else [frames[0], frames[-1]]
            dp = 'pose.bones["%s"].%s' % (name.replace('"', '\\"'), path)
            for ci in range(seq.shape[1]):
                fc = _fcurve(action, arm_obj, dp, ci, name)
                vals = seq[:, ci] if varies else np.full(len(keys), seq[0, ci])
                _fill(fc, keys, vals.astype(np.float32))
                n_fc += 1
                n_key += len(keys)

    rm_applied = False
    rm = clip.get("rootMotion")
    if apply_root_motion and rm:
        raw = _channel(pack, rm, f, 3)                       # rig space, relative to frame 0
        per_cycle = (raw[f - 1] - raw[0]) if f > 1 else np.zeros(3)
        disp = raw[src] + cycle[:, None] * per_cycle[None, :]
        world = np.asarray([(Y_UP_TO_Z_UP @ Vector(tuple(d)))[:] for d in disp])
        for ci in range(3):
            fc = _fcurve(action, arm_obj, "location", ci, "Object Transforms")
            _fill(fc, frames, world[:, ci].astype(np.float32))
            n_fc += 1
            n_key += len(frames)
        rm_applied = True

    return action, frames[-1], n_fc, n_key, sheared, len(tracked), rm_applied


# ----------------------------------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------------------------------

_CLIP_PREFERENCE = ("idle_aim", "Idle_Aim", "idle", "Idle", "idle_aim_low")


def _pick_clip(pack, clip_name):
    clips = pack.m.get("clips") or []
    if not clips:
        return None
    if clip_name is not None:
        c = pack.clips_by_name.get(clip_name)
        if c is None:
            low = {n.lower(): n for n in pack.clips_by_name}
            hit = low.get(str(clip_name).lower())
            if hit:
                return pack.clips_by_name[hit]
            near = [n for n in pack.clips_by_name if str(clip_name).lower() in n.lower()][:8]
            raise KeyError("clip %r not in this pack (%d clips). Close: %s"
                           % (clip_name, len(clips), near or "none"))
        return c
    for want in _CLIP_PREFERENCE:
        if want in pack.clips_by_name:
            return pack.clips_by_name[want]
    loops_ = [c for c in clips if c.get("loop")]
    return max(loops_ or clips, key=lambda c: float(c.get("duration", 0.0)))


def import_eftchar(char_dir, clip_name=None, loops=1, name_prefix="",
                   lod=None, include_first_person=False, mesh_filter=None,
                   bone_axis="auto", use_gloss_map=True, apply_root_motion=False,
                   set_scene_range=True, collection=None):
    """Import a ``.eftchar`` pack.  Returns ``(armature_object, mesh_objects, action_or_None)``.

    char_dir              directory holding manifest.json / skin.bin / anim.bin / textures/
    clip_name             clip to import; None picks an idle-ish looping clip
    loops                 how many cycles of the clip to lay down in the action
    name_prefix           prepended to object / mesh / material / action names (never to bones)
    lod                   LOD to import; None uses manifest.defaultLod
    include_first_person  also import meshes tagged view == "first" (FPV hands)
    mesh_filter           callable(mesh_name) -> bool, applied after the LOD/view filters
    bone_axis             'auto' derives the rig's bone axis and points bones along +Y;
                          'joint'/None keeps the raw joint frames as the bone rest orientation
    use_gloss_map         wire _SpecMap through an Invert into Roughness
    apply_root_motion     keyframe the armature object from the clip's rootMotion channel
    set_scene_range       set scene fps / frame_start / frame_end from the clip
    collection            target collection; defaults to the scene's active collection
    """
    pack = _Pack(char_dir)
    coll = collection or bpy.context.collection or bpy.context.scene.collection

    rest_locals = pack.rest_locals()
    rest_worlds = pack.rest_worlds(rest_locals)
    ibps, ibp_disagree = pack.merged_inverse_bindposes()
    t_r, support, n_bound = _renderer_matrix(rest_worlds, ibps)
    bind_worlds = _bind_worlds(pack, rest_locals, rest_worlds, ibps, t_r)
    bind_locals = _bind_locals(pack, bind_worlds)

    kids = _children(pack.parents)
    axis_key, votes, n_votes = (None, 0, 0)
    if bone_axis == "auto":
        axis_key, votes, n_votes = _derive_bone_axis(pack, bind_worlds, kids)
    q4 = _q_matrix(axis_key)
    rest_mats = [bind_worlds[i] @ q4 for i in range(pack.n_bones)]
    lengths = _bone_lengths(pack, bind_worlds, kids)

    ident = name_prefix + str(pack.m.get("id", "character"))
    arm_obj, bone_names = _build_armature(pack, rest_mats, lengths, coll, ident)
    # PUBLISH the axis correction. Blender bones point along their own local +Y, so this importer
    # rotates every bone by q4 to make that happen (rest = bind_world @ q4). Anything that wants to
    # sit where the ENGINE puts it -- a weapon attached to Weapon_root with an identity transform --
    # must undo it: engine_pose = pose_bone.matrix @ q4_inverse. Without that the rifle is rotated
    # by the bone-axis convention and comes out sideways in the character's hands.
    arm_obj["eft_bone_axis"] = str(axis_key)
    arm_obj["eft_q4"] = [c for row in q4 for c in row]

    mats = {}
    for i, spec in enumerate(pack.m.get("materials") or []):
        mats[i] = _build_material(pack, spec, name_prefix, use_gloss_map)

    want_lod = int(pack.m.get("defaultLod", 0)) if lod is None else int(lod)
    mesh_objs, n_v, n_t, n_w, skipped = [], 0, 0, 0, 0
    for md in pack.m.get("meshes") or []:
        if int(md.get("lod", 0)) != want_lod:
            skipped += 1
            continue
        if str(md.get("view", "third")) == "first" and not include_first_person:
            skipped += 1
            continue
        if mesh_filter is not None and not mesh_filter(md["name"]):
            skipped += 1
            continue
        obj, v, t, w = _build_mesh(pack, md, t_r, mats, bone_names, arm_obj, coll, name_prefix)
        mesh_objs.append(obj)
        n_v += v
        n_t += t
        n_w += w

    clip = _pick_clip(pack, clip_name)
    action = None
    last = 1
    n_fc = n_key = sheared = n_tracked = 0
    rm_applied = False
    if clip is not None:
        action, last, n_fc, n_key, sheared, n_tracked, rm_applied = _build_action(
            pack, clip, arm_obj, bone_names, bind_locals, q4, loops,
            "%s%s|%s" % (name_prefix, pack.m.get("id", "char"), clip["name"]),
            apply_root_motion)
        if set_scene_range:
            sc = bpy.context.scene
            sc.render.fps = max(1, int(round(float(clip.get("sampleRate", 30.0)))))
            sc.render.fps_base = 1.0
            sc.frame_start = 1
            sc.frame_end = max(1, last)
            sc.frame_set(1)

    axis_txt = ("%s%s (%d/%d bones)" % ("+-"[axis_key[1] < 0], "XYZ"[axis_key[0]], votes, n_votes)
                if axis_key else "joint frames kept")
    print("[eftchar] %s  '%s'  v%s" % (pack.m.get("id"), pack.m.get("displayName"),
                                       pack.m.get("version")))
    print("[eftchar] skeleton   %d bones, %d bound by geometry, bindpose agreement %.1e"
          % (pack.n_bones, n_bound, ibp_disagree))
    print("[eftchar] rest pose  BIND pose; T_r medoid support %d/%d; bone axis %s"
          % (support, n_bound, axis_txt))
    print("[eftchar] geometry   %d mesh(es) at lod %d (%d skipped), %d verts, %d tris, %d weights"
          % (len(mesh_objs), want_lod, skipped, n_v, n_t, n_w))
    print("[eftchar] materials  %d, textures %d, UV V un-flipped for Blender: %s"
          % (len(mats), len(pack.m.get("textures") or []), pack.uv_v_flipped))
    n_att = len(pack.m.get("attachments") or [])
    if n_att:
        print("[eftchar] NOTE       %d attachment(s) NOT imported (bone-pinned rigid geometry)"
              % n_att)
    if clip is not None:
        print("[eftchar] clip       '%s'  %d frames @ %.1f Hz  %.3f s  loop=%s  tracks=%d"
              % (clip["name"], int(clip["frameCount"]), float(clip.get("sampleRate", 0.0)),
                 float(clip.get("duration", 0.0)), bool(clip.get("loop")), n_tracked))
        print("[eftchar] action     %s  x%d cycles -> frames 1..%d, %d fcurves, %d keys%s"
              % (action.name, max(1, int(loops)), last, n_fc, n_key,
                 ", root motion applied" if rm_applied else ""))
        if sheared:
            print("[eftchar] WARNING    %d pose bases carried shear; decompose dropped it" % sheared)
    else:
        print("[eftchar] clip       none imported (pack has %d)" % len(pack.m.get("clips") or []))
    print("[eftchar] armature   %s at %s (Y-up -> Z-up applied once, det=%+.0f)"
          % (arm_obj.name, tuple(round(v, 3) for v in arm_obj.matrix_world.translation),
             arm_obj.matrix_world.to_3x3().determinant()))
    return arm_obj, mesh_objs, action


def main():
    """Env-driven entry point, for ``exec(open(path).read())`` where there is no ``__file__``.

    EFTCHAR_DIR (required)  EFTCHAR_CLIP  EFTCHAR_LOOPS  EFTCHAR_PREFIX  EFTCHAR_LOD
    """
    char_dir = os.environ.get("EFTCHAR_DIR", "")
    if not char_dir or not os.path.isdir(char_dir):
        print("[eftchar] set EFTCHAR_DIR to a .eftchar directory, or call import_eftchar(dir, clip)"
              + ("" if not char_dir else "  (not a directory: %s)" % char_dir))
        return None
    try:
        loops = int(os.environ.get("EFTCHAR_LOOPS", "1"))
    except ValueError:
        loops = 1
    lod = os.environ.get("EFTCHAR_LOD")
    return import_eftchar(char_dir,
                          clip_name=os.environ.get("EFTCHAR_CLIP") or None,
                          loops=loops,
                          name_prefix=os.environ.get("EFTCHAR_PREFIX", ""),
                          lod=int(lod) if lod not in (None, "") else None)


if os.environ.get("EFTCHAR_NO_AUTORUN") != "1":
    main()
