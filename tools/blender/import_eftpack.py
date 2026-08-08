"""
Blender importer for the .eftpack map format.

Standalone: no imports from this repository. Run it inside a live Blender
session with

    exec(open(r"<repo>/tools/blender/import_eftpack.py").read())

which defines ``import_eftpack`` / ``main`` and then runs ``main()``.
``main()`` reads its settings from the environment (see ENV below) and is a
no-op printing one usage line when it cannot find a pack.

    import_eftpack(pack_dir, center=None, radius=None, max_instances=None,
                   with_textures=True, lod=0, include_inactive=False,
                   collection_name=None, verbose=True)

ENV (used by ``main()`` only)
    EFT_PACK            path to a *.eftpack directory
    EFT_CENTER          "x,y,z" in PACK space (same space as manifest.bounds)
    EFT_RADIUS          float, metres
    EFT_MAX_INSTANCES   int
    EFT_TEXTURES        "0" to build flat-tinted materials
    EFT_LOD             int, or "all"/"-" to keep every LOD level


WHAT THIS FILE ASSUMES ABOUT THE FORMAT
---------------------------------------
Nothing is hardcoded that the pack declares about itself. ``manifest.vertex``
and ``manifest.instance`` are turned into numpy structured dtypes at load time,
fields are resolved BY NAME, and the documented layout below is only the
fallback for a pack that omits the descriptor.

  meshes.bin   [whole vertex section][whole u32 index section], little-endian.
               vertex stride 36: position f32x3@0, normal f32x3@12,
               uv f32x2@24, color unorm8x4@32.
               mesh.vtxOffset is a BYTE offset, mesh.vtxCount a VERTEX count,
               mesh.idxOffset a BYTE offset. Indices are u32 and LOCAL /
               0-based inside that mesh's own vertex block.
               submeshes[].idxStart/idxCount are INDEX ELEMENTS, local to the
               mesh's index block.

  instances.bin  stride 80, little-endian.
               affine f32x12 @0 = ROW-MAJOR world 3x4 INCLUDING SHEAR, i.e.
                   rows [m0 m1 m2 | m3] [m4 m5 m6 | m7] [m8 m9 m10 | m11]
                   translation = (m3, m7, m11)
                   linear column i = (m[i], m[4+i], m[8+i])
               meshId u32@48, lodGroup i32@52, lodIndex i32@56,
               rootId u32@60, flags u32@64, par u32@68, par2 u32@72, lv u32@76.
               flags: 0x1 MIRROR, 0x2 TERRAIN, 0x4 BAKED_WORLD, 0x8 INACTIVE.


COORDINATES
-----------
The pack world is RIGHT-HANDED, Y-UP, metres. The Unity handedness conjugation
is ALREADY APPLIED in the data -- it is not re-applied here, ever. The only
change is Y-up -> Z-up:

    blender_xyz = (x, -z, y)

which is a +90 degree rotation about X, determinant +1. It is applied ONCE, as
the matrix of a single parent empty. No axis is ever mirrored and no matrix is
ever transposed "to make it look right".


SHEAR
-----
A Blender object stores location/rotation/scale, so it CANNOT hold a sheared
3x3: assigning a sheared matrix to obj.matrix_world / matrix_basis silently
re-orthogonalises it and misplaces the geometry (verified on Blender 5.1: a
0.4 shear term comes back as a rotation plus scale). Any instance whose 3x3
columns are not orthogonal -- normalise the columns, |dot| between any pair
> 0.02 -- is BAKED: its geometry is transformed to world space into its own
mesh datablock and the object carries an identity matrix. Rank-deficient 3x3s
are baked too, with a pseudo-inverse normal transform. Everything else shares
one mesh datablock per meshId and carries an object matrix.


UV / TEXTURES
-------------
uv V-flip and _MainTex_ST tiling are ALREADY BAKED into the vertex UVs;
materials.json.uvXform is REFERENCE ONLY and is never re-applied. Normal maps
are DirectX convention, so the green channel is inverted in the node graph
before the Normal Map node. Albedo/emissive load as sRGB, normal as Non-Color.
Sampler extension is REPEAT because baked tiling puts UVs well outside [0,1].


TWO BLENDER 4.2+ BEHAVIOURS THIS FILE WORKS AROUND
--------------------------------------------------
1. ``Material.blend_method`` and ``alpha_threshold`` are legacy aliases of
   ``surface_render_method`` under EEVEE Next. Assigning ``'OPAQUE'`` or
   ``'CLIP'`` is a SILENT no-op that leaves the material on ``HASHED``
   (measured on 5.1). The MASK alpha test therefore lives in the node graph as
   a GREATER_THAN node on the computed alpha, not in a material property.

2. ``Mesh.validate()`` deletes every face whose vertex set duplicates another
   face's. EFT builds double-sided cards -- glass shards, foliage, decal planes
   -- as two coincident triangles over one deduped vertex block, so validate()
   eats real geometry (measured on a 192-mesh slice: 522 real faces, 258 from a
   single 2204-triangle glass mesh). This importer never calls it, and instead
   filters only the triangles that have a repeated corner.
"""

import json
import os
import time

import bpy
import numpy as np
from mathutils import Matrix

__all__ = ["import_eftpack", "main"]

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

FLAG_MIRROR = 0x1
FLAG_TERRAIN = 0x2
FLAG_BAKED_WORLD = 0x4
FLAG_INACTIVE = 0x8

# Column-orthogonality tolerance. Matches the pipeline's own instmath.py:37.
SHEAR_TOL = 0.02

# Y-up -> Z-up. (x, y, z) -> (x, -z, y). Exact, det = +1, applied ONCE.
YUP_TO_ZUP = Matrix(((1.0, 0.0, 0.0, 0.0),
                     (0.0, 0.0, -1.0, 0.0),
                     (0.0, 1.0, 0.0, 0.0),
                     (0.0, 0.0, 0.0, 1.0)))

MAX_WARN = 12

# Fallback layout, used only when the manifest omits the descriptor.
_DEFAULT_VERTEX = {
    "stride": 36,
    "attrs": [
        {"name": "position", "fmt": "f32x3", "offset": 0},
        {"name": "normal", "fmt": "f32x3", "offset": 12},
        {"name": "uv", "fmt": "f32x2", "offset": 24},
        {"name": "color", "fmt": "unorm8x4", "offset": 32},
    ],
}
_DEFAULT_INSTANCE = {
    "stride": 80,
    "fields": [
        {"name": "affine", "fmt": "f32x12", "offset": 0},
        {"name": "meshId", "fmt": "u32", "offset": 48},
        {"name": "lodGroup", "fmt": "i32", "offset": 52},
        {"name": "lodIndex", "fmt": "i32", "offset": 56},
        {"name": "rootId", "fmt": "u32", "offset": 60},
        {"name": "flags", "fmt": "u32", "offset": 64},
        {"name": "par", "fmt": "u32", "offset": 68},
        {"name": "par2", "fmt": "u32", "offset": 72},
        {"name": "lv", "fmt": "u32", "offset": 76},
    ],
}

_FMT_BASE = {
    "f64": ("<f8", None), "f32": ("<f4", None), "f16": ("<f2", None),
    "u32": ("<u4", None), "i32": ("<i4", None),
    "u16": ("<u2", None), "i16": ("<i2", None),
    "u8": ("u1", None), "i8": ("i1", None),
    "unorm32": ("<u4", 4294967295.0), "snorm32": ("<i4", 2147483647.0),
    "unorm16": ("<u2", 65535.0), "snorm16": ("<i2", 32767.0),
    "unorm8": ("u1", 255.0), "snorm8": ("i1", 127.0),
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _parse_fmt(fmt):
    """'unorm8x4' -> ('u1', 4, 255.0). Returns None for an unknown base."""
    base, _, cnt = str(fmt).partition("x")
    try:
        n = int(cnt) if cnt else 1
    except ValueError:
        return None
    hit = _FMT_BASE.get(base)
    if hit is None or n < 1:
        return None
    return hit[0], n, hit[1]


def _layout_dtype(spec, default, what, log):
    """Turn a manifest {stride, attrs|fields} descriptor into a numpy dtype.

    Every field is validated against the stride before it is used, exactly the
    way the native loader does, so a malformed descriptor errors here instead
    of reading past the end of the last record.
    """
    if not isinstance(spec, dict):
        spec = default
    stride = int(spec.get("stride") or default["stride"])
    items = spec.get("attrs") or spec.get("fields") or default.get("attrs") or default.get("fields")
    names, formats, offsets, norms = [], [], [], {}
    for it in items:
        name = str(it.get("name", ""))
        parsed = _parse_fmt(it.get("fmt"))
        if not name or parsed is None:
            log.warn("%s: skipping field %r with unusable fmt %r" % (what, name, it.get("fmt")))
            continue
        base, n, norm = parsed
        off = int(it.get("offset", 0))
        size = np.dtype(base).itemsize * n
        if off < 0 or off + size > stride:
            raise ValueError("%s field %r [%d,%d) does not fit stride %d"
                             % (what, name, off, off + size, stride))
        if name in names:
            continue
        names.append(name)
        formats.append((base, n) if n > 1 else base)
        offsets.append(off)
        if norm is not None:
            norms[name] = norm
    dt = np.dtype({"names": names, "formats": formats,
                   "offsets": offsets, "itemsize": stride})
    return dt, stride, norms


def _posix(p):
    return str(p).replace("\\", "/")


def _is_abs(p):
    return os.path.isabs(p) or (len(p) > 1 and p[1] == ":")


class _Log(object):
    """Warning sink with a hard cap, so a 68k-instance import cannot spam."""

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.n = 0
        self._seen = set()

    def warn(self, msg, key=None):
        self.n += 1
        k = key if key is not None else msg
        if k in self._seen:
            return
        self._seen.add(k)
        if self.verbose and len(self._seen) <= MAX_WARN:
            print("[eftpack] WARN %s" % msg)
        elif self.verbose and len(self._seen) == MAX_WARN + 1:
            print("[eftpack] WARN ... further warnings suppressed")

    def info(self, msg):
        if self.verbose:
            print("[eftpack] %s" % msg)


# ---------------------------------------------------------------------------
# node-graph helpers
# ---------------------------------------------------------------------------

def _node(nt, idname, x, y):
    n = nt.nodes.new(idname)
    n.location = (x, y)
    return n


def _mix_multiply(nt, x, y):
    """(node, in_a, in_b, out) for a full-strength MULTIPLY colour mix."""
    try:
        n = _node(nt, "ShaderNodeMix", x, y)
        n.data_type = "RGBA"
        n.blend_type = "MULTIPLY"
        a = next(s for s in n.inputs if s.name == "A" and s.enabled)
        b = next(s for s in n.inputs if s.name == "B" and s.enabled)
        o = next(s for s in n.outputs if s.enabled)
        fac = next(s for s in n.inputs if s.name == "Factor" and s.enabled)
        fac.default_value = 1.0
        return n, a, b, o
    except Exception:
        n = _node(nt, "ShaderNodeMixRGB", x, y)
        n.blend_type = "MULTIPLY"
        n.inputs[0].default_value = 1.0
        return n, n.inputs[1], n.inputs[2], n.outputs[0]


def _math(nt, op, x, y):
    n = _node(nt, "ShaderNodeMath", x, y)
    n.operation = op
    return n


def _principled(nt):
    for n in nt.nodes:
        if n.bl_idname == "ShaderNodeBsdfPrincipled":
            return n
    return None


# ---------------------------------------------------------------------------
# importer
# ---------------------------------------------------------------------------

class _Importer(object):

    def __init__(self, pack_dir, center, radius, max_instances,
                 with_textures, lod, include_inactive, collection_name, verbose):
        self.pack = os.path.abspath(pack_dir)
        self.center = None if center is None else np.asarray(center, dtype=np.float64).reshape(3)
        self.radius = None if radius is None else float(radius)
        self.max_instances = None if max_instances is None else int(max_instances)
        self.with_textures = bool(with_textures)
        self.lod = lod
        self.include_inactive = bool(include_inactive)
        self.collection_name = collection_name
        self.log = _Log(verbose)

        self.fh = None
        self.mesh_cache = {}         # meshId -> bpy Mesh (shared)
        self.mat_cache = {}          # materialId -> bpy Material
        self.img_cache = {}          # (abspath.lower(), is_data) -> bpy Image or None
        self.path_cache = {}         # raw material path -> resolved abspath or None
        self.tag = ""

        self.n_objects = 0
        self.n_baked = 0
        self.n_mesh_fail = 0
        self.n_tex_missing = 0
        self.n_degenerate = 0
        self._centres = None

    # -- manifest ----------------------------------------------------------

    def _load_manifest(self):
        mp = os.path.join(self.pack, "manifest.json")
        if not os.path.isfile(mp):
            raise IOError("no manifest.json in %s" % self.pack)
        with open(mp, "r", encoding="utf-8") as f:
            m = json.load(f)
        ver = int(m.get("version", 0))
        if ver != 1:
            self.log.warn("manifest version %r is not 1; continuing anyway" % ver)
        self.man = m
        self.meshes = m.get("meshes") or []
        self.roots = m.get("roots") or [""]
        self.dataset_path = _posix(m.get("datasetPath") or "")
        self.map_id = m.get("map") or os.path.splitext(os.path.basename(self.pack))[0]

        self.vdtype, self.vstride, self.vnorm = _layout_dtype(
            m.get("vertex"), _DEFAULT_VERTEX, "vertex", self.log)
        self.idtype, self.istride, _ = _layout_dtype(
            m.get("instance"), _DEFAULT_INSTANCE, "instance", self.log)

        conv = m.get("conventions") or {}
        # Absent block means "already baked" -- same default as the native loader.
        self.conv_green_flip = bool(conv.get("normalMapGreenFlip", True))

        mats_p = os.path.join(self.pack, "materials.json")
        if os.path.isfile(mats_p):
            with open(mats_p, "r", encoding="utf-8") as f:
                self.mats = json.load(f)
        else:
            self.mats = []
            self.log.warn("no materials.json; every submesh gets a placeholder material")

    # -- instances ---------------------------------------------------------

    def _load_instances(self):
        ip = os.path.join(self.pack, "instances.bin")
        if not os.path.isfile(ip):
            raise IOError("no instances.bin in %s" % self.pack)
        nbytes = os.path.getsize(ip)
        if nbytes % self.istride:
            self.log.warn("instances.bin (%d B) is not a multiple of stride %d; truncating"
                          % (nbytes, self.istride))
        n = nbytes // self.istride
        declared = int(self.man.get("instanceCount", n) or n)
        if declared != n:
            self.log.warn("instanceCount %d != file-derived %d; using %d" % (declared, n, n))
        inst = np.fromfile(ip, dtype=self.idtype, count=n)

        aff = np.ascontiguousarray(inst["affine"], dtype=np.float64) if "affine" in inst.dtype.names \
            else np.zeros((n, 12))
        if aff.shape[1] != 12:
            raise ValueError("instance.affine is %d wide, expected 12" % aff.shape[1])

        def col(name, default, dt):
            if name in inst.dtype.names:
                return np.ascontiguousarray(inst[name], dtype=dt)
            return np.full(n, default, dtype=dt)

        self.inst_aff = aff
        self.inst_mesh = col("meshId", 0, np.int64)
        self.inst_lodi = col("lodIndex", -1, np.int64)
        self.inst_root = col("rootId", 0, np.int64)
        self.inst_flags = col("flags", 0, np.int64)
        self.inst_n = n

    def _select(self):
        n = self.inst_n
        keep = np.ones(n, dtype=bool)
        stats = {}

        finite = np.isfinite(self.inst_aff).all(axis=1)
        stats["nonfinite"] = int((~finite).sum())
        keep &= finite

        bad_mesh = (self.inst_mesh < 0) | (self.inst_mesh >= len(self.meshes))
        stats["badMeshId"] = int((bad_mesh & keep).sum())
        keep &= ~bad_mesh

        if not self.include_inactive:
            inactive = (self.inst_flags & FLAG_INACTIVE) != 0
            stats["inactive"] = int((inactive & keep).sum())
            keep &= ~inactive
        else:
            stats["inactive"] = 0

        if self.lod is not None:
            lodv = int(self.lod)
            # lodIndex < 0 means "not part of a LOD group": always keep it,
            # otherwise every non-LOD prop in the map disappears.
            lodok = (self.inst_lodi == lodv) | (self.inst_lodi < 0)
            stats["lod"] = int((~lodok & keep).sum())
            keep &= lodok
        else:
            stats["lod"] = 0

        if self.center is not None and self.radius is not None:
            # WHERE THE GEOMETRY ACTUALLY IS = M3 @ mesh_centre + T, not T alone. For an ordinary
            # instance the mesh is modelled about its own origin so this is ~T, but geometry that
            # was pre-baked into world space ships an IDENTITY affine, which puts T at the origin
            # no matter where the object is. Every projected decal is like that: 1353 of them on
            # this map, so a radius filter on T alone imported the checkpoint plates and not one
            # of their sprays. (They are not flagged BAKED_WORLD either, so testing the flag is
            # not enough - derive it from the geometry.)
            centres = self._mesh_centres()
            mids = np.clip(self.inst_mesh, 0, max(0, len(centres) - 1))
            c = centres[mids]
            m3 = self.inst_aff[:, [0, 1, 2, 4, 5, 6, 8, 9, 10]].reshape(-1, 3, 3)
            t = np.einsum("nij,nj->ni", m3, c) + self.inst_aff[:, [3, 7, 11]]
            d = t - self.center[None, :]
            inside = np.einsum("ij,ij->i", d, d) <= (self.radius * self.radius)
            stats["radius"] = int((~inside & keep).sum())
            keep &= inside
        else:
            stats["radius"] = 0

        sel = np.nonzero(keep)[0]
        stats["cap"] = 0
        if self.max_instances is not None and sel.size > self.max_instances:
            stats["cap"] = int(sel.size - self.max_instances)
            sel = sel[:self.max_instances]
        self.sel = sel
        self.stats = stats

    def _classify_shear(self):
        """True where the instance's 3x3 cannot survive Blender's loc/rot/scale."""
        a = self.inst_aff[self.sel]
        m3 = a.reshape(-1, 3, 4)[:, :, :3]          # rows of the world 3x3
        cols = np.transpose(m3, (0, 2, 1))          # cols[:, i] = linear column i
        ln = np.linalg.norm(cols, axis=2)
        lmax = ln.max(axis=1)
        lmin = ln.min(axis=1)
        degenerate = (lmax <= 1e-12) | (lmin <= lmax * 1e-6)
        u = cols / np.where(ln > 0.0, ln, 1.0)[:, :, None]
        d01 = np.abs(np.einsum("ij,ij->i", u[:, 0], u[:, 1]))
        d02 = np.abs(np.einsum("ij,ij->i", u[:, 0], u[:, 2]))
        d12 = np.abs(np.einsum("ij,ij->i", u[:, 1], u[:, 2]))
        skew = np.maximum(np.maximum(d01, d02), d12)
        self.bake_mask = (skew > SHEAR_TOL) | degenerate
        self.max_skew = float(skew.max()) if skew.size else 0.0

    # -- textures ----------------------------------------------------------

    def _resolve_tex(self, raw):
        if not raw:
            return None
        if raw in self.path_cache:
            return self.path_cache[raw]
        p = _posix(raw)
        base = os.path.basename(p)
        cands = []
        if _is_abs(p):
            cands.append(p)
        else:
            cands.append(os.path.join(self.pack, p))
            if self.dataset_path:
                cands.append(os.path.join(self.dataset_path, p))
        cands.append(os.path.join(self.pack, "tex", base))
        if self.dataset_path:
            cands.append(os.path.join(self.dataset_path, "tex", base))
        cands.append(os.path.join(self.pack, "terrain_layers", base))
        out = None
        for c in cands:
            c = os.path.normpath(c)
            if os.path.isfile(c):
                out = c
                break
        if out is None:
            self.n_tex_missing += 1
            self.log.warn("texture not found: %s" % base, key=("tex", base))
        self.path_cache[raw] = out
        return out

    def _image(self, raw, is_data):
        path = self._resolve_tex(raw)
        if path is None:
            return None
        key = (path.lower(), bool(is_data))
        if key in self.img_cache:
            return self.img_cache[key]
        try:
            # check_existing=False so the two colour spaces of one file never
            # fight over a single datablock; this dict is the real cache.
            img = bpy.data.images.load(path, check_existing=False)
        except Exception as e:
            self.n_tex_missing += 1
            self.log.warn("cannot load %s (%s)" % (os.path.basename(path), e),
                          key=("load", path.lower()))
            self.img_cache[key] = None
            return None
        try:
            img.colorspace_settings.name = "Non-Color" if is_data else "sRGB"
        except Exception:
            pass
        try:
            img.alpha_mode = "CHANNEL_PACKED" if is_data else "STRAIGHT"
        except Exception:
            pass
        self.img_cache[key] = img
        return img

    # -- materials ---------------------------------------------------------

    def _material(self, mid):
        if mid in self.mat_cache:
            return self.mat_cache[mid]
        rec = self.mats[mid] if 0 <= mid < len(self.mats) else None
        if rec is None:
            self.log.warn("materialId %d out of range (%d materials)" % (mid, len(self.mats)),
                          key=("matid", mid))
            rec = {}
        mat = self._build_material(mid, rec)
        self.mat_cache[mid] = mat
        return mat

    def _build_material(self, mid, rec):
        role = str(rec.get("role") or "opaque")
        name = "eft%s.%04d.%s" % (self.tag, mid, role)
        mat = bpy.data.materials.new(name)

        tint = rec.get("tint") or [1.0, 1.0, 1.0, 1.0]
        tint = [float(x) for x in (list(tint) + [1.0, 1.0, 1.0, 1.0])[:4]]
        alpha_mode = str(rec.get("alphaMode") or "OPAQUE").upper()
        cutoff = float(rec.get("alphaCutoff") or 0.0)
        double_sided = bool(rec.get("doubleSided", True))

        # Viewport-solid colour, so the map reads sensibly without shading.
        try:
            mat.diffuse_color = (tint[0], tint[1], tint[2], tint[3])
        except Exception:
            pass
        mat.use_backface_culling = not double_sided
        try:
            mat.use_backface_culling_shadow = not double_sided
        except Exception:
            pass

        # From 4.2 on, Material.blend_method and alpha_threshold are legacy
        # aliases of surface_render_method: setting them to OPAQUE or CLIP is a
        # silent no-op that leaves the material on HASHED (measured on 5.1).
        # surface_render_method is the real control, and the MASK alpha test has
        # to be built into the node graph -- see the GREATER_THAN node below.
        # Both are still set so this file also behaves on pre-4.2 Blender.
        if alpha_mode == "MASK":
            _set(mat, "blend_method", "CLIP")
            _set(mat, "alpha_threshold", max(0.0, min(1.0, cutoff)))
            _set(mat, "surface_render_method", "DITHERED")
        elif alpha_mode == "BLEND":
            _set(mat, "blend_method", "BLEND")
            _set(mat, "surface_render_method", "BLENDED")
        else:
            _set(mat, "blend_method", "OPAQUE")
            _set(mat, "surface_render_method", "DITHERED")

        if mat.node_tree is None:
            try:
                mat.use_nodes = True
            except Exception:
                pass
        nt = mat.node_tree
        if nt is None:
            return mat
        bsdf = _principled(nt)
        if bsdf is None:
            bsdf = _node(nt, "ShaderNodeBsdfPrincipled", 0, 0)
            out = None
            for n in nt.nodes:
                if n.bl_idname == "ShaderNodeOutputMaterial":
                    out = n
            if out is None:
                out = _node(nt, "ShaderNodeOutputMaterial", 300, 0)
            nt.links.new(bsdf.outputs[0], out.inputs["Surface"])

        _sock(bsdf, "Metallic", float(rec.get("metallic") or 0.0))
        _sock(bsdf, "Roughness", max(0.03, min(1.0, float(rec.get("roughness", 0.55) or 0.0))))
        _sock(bsdf, "Base Color", (tint[0], tint[1], tint[2], 1.0))
        if alpha_mode == "MASK":
            # Untextured cutout: the test is on tint.a alone and resolves here.
            _sock(bsdf, "Alpha", 1.0 if tint[3] > cutoff else 0.0)
        elif alpha_mode == "BLEND":
            _sock(bsdf, "Alpha", tint[3])

        if not self.with_textures:
            return mat

        # ---- albedo ------------------------------------------------------
        albedo = rec.get("albedo")
        if not albedo:
            vp = rec.get("vp")
            if isinstance(vp, dict):
                layers = vp.get("layers") or []
                if layers and isinstance(layers[0], dict):
                    # Approximation: the real splat is a 3-layer vertex-paint
                    # blend; layer 0 is the base and reads correctly on its own.
                    albedo = layers[0].get("albedo")
        img = self._image(albedo, False) if albedo else None
        alpha_out = None
        if img is not None:
            tex = _node(nt, "ShaderNodeTexImage", -820, 320)
            tex.image = img
            tex.label = "albedo"
            tex.interpolation = "Smart"
            tex.extension = "REPEAT"          # tiling is baked into the UVs
            col_out = tex.outputs["Color"]
            alpha_out = tex.outputs["Alpha"]
            if abs(tint[0] - 1.0) > 1e-4 or abs(tint[1] - 1.0) > 1e-4 or abs(tint[2] - 1.0) > 1e-4:
                mixn, ia, ib, oc = _mix_multiply(nt, -520, 320)
                nt.links.new(col_out, ia)
                ib.default_value = (tint[0], tint[1], tint[2], 1.0)
                col_out = oc
            nt.links.new(col_out, bsdf.inputs["Base Color"])

        # ---- alpha -------------------------------------------------------
        # The alpha test runs on the COMPUTED albedo alpha (tex.a * tint.a), so
        # an untextured cutout with tint.a below the cutoff still discards.
        # ---- water: coverage is in RED, not alpha --------------------------
        # The game's `Decal/Water Deferred Decal` samples the puddle mask from the RED channel;
        # these atlases ship alpha identically 1.0. Driving opacity from alpha therefore covers
        # the whole quad with a translucent sheet -- a 30 m glass plate laid over the road, which
        # is what "the road is transparent" looks like. Take coverage from red and shade it as
        # wet asphalt: dark, smooth, and opaque where the mask says there is water.
        if role == "water":
            if img is not None:
                sep = _node(nt, "ShaderNodeSeparateColor", -520, 120)
                nt.links.new(tex.outputs["Color"], sep.inputs["Color"])
                nt.links.new(sep.outputs["Red"], bsdf.inputs["Alpha"])
            else:
                _sock(bsdf, "Alpha", 0.0)          # untextured "water" is not a white slab
            _sock(bsdf, "Base Color", (0.02, 0.023, 0.026, 1.0))
            _sock(bsdf, "Roughness", 0.08)
            _sock(bsdf, "Metallic", 0.0)
            alpha_mode = "WATER_DONE"

        # ---- SoftCutout roads: coverage is VERTEX PAINT, not texture alpha ------------------
        # `Custom/Vert Paint SoftCutout Decal` is what EFT paves with: roads, parking, yard slabs.
        # Its coverage is COLOR_0.a shaped by the material's own params, and its texture ALPHA is
        # a SMOOTHNESS map. Driving opacity from that alpha eats the road surface in patches, which
        # is what the asphalt looked like. The viewer computes exactly this:
        #     coverage = clamp(color.a * astr - (acut - ahgt), 0, 1) * color.a
        sc_p = ((rec.get("vp") or {}).get("softCutout")
                if isinstance(rec.get("vp"), dict) else None)
        if sc_p and len(sc_p) >= 3 and role == "decal":
            astr, acut, ahgt = (float(sc_p[0]), float(sc_p[1]), float(sc_p[2]))
            vcol = _node(nt, "ShaderNodeVertexColor", -900, -40)
            vcol.layer_name = "Col"
            mul = _math(nt, "MULTIPLY", -700, -40); mul.inputs[1].default_value = astr
            nt.links.new(vcol.outputs["Alpha"], mul.inputs[0])
            sub = _math(nt, "SUBTRACT", -540, -40); sub.inputs[1].default_value = (acut - ahgt)
            nt.links.new(mul.outputs[0], sub.inputs[0])
            cl = _math(nt, "CLAMP" if False else "MINIMUM", -380, -40)
            cl.inputs[1].default_value = 1.0
            nt.links.new(sub.outputs[0], cl.inputs[0])
            mx = _math(nt, "MAXIMUM", -240, -40); mx.inputs[1].default_value = 0.0
            nt.links.new(cl.outputs[0], mx.inputs[0])
            fin = _math(nt, "MULTIPLY", -100, -40)
            nt.links.new(mx.outputs[0], fin.inputs[0])
            nt.links.new(vcol.outputs["Alpha"], fin.inputs[1])
            nt.links.new(fin.outputs[0], bsdf.inputs["Alpha"])
            alpha_mode = "SOFTCUT_DONE"

        if alpha_mode not in ("OPAQUE", "WATER_DONE", "SOFTCUT_DONE") and alpha_out is not None:
            a_out = alpha_out
            if abs(tint[3] - 1.0) > 1e-4:
                mn = _math(nt, "MULTIPLY", -520, 120)
                nt.links.new(a_out, mn.inputs[0])
                mn.inputs[1].default_value = tint[3]
                a_out = mn.outputs[0]
            if alpha_mode == "MASK":
                gt = _math(nt, "GREATER_THAN", -340, 120)
                nt.links.new(a_out, gt.inputs[0])
                gt.inputs[1].default_value = max(0.0, min(1.0, cutoff))
                gt.label = "alpha test (cutoff %.3f)" % cutoff
                a_out = gt.outputs[0]
            nt.links.new(a_out, bsdf.inputs["Alpha"])

        # ---- roughness from albedo alpha ---------------------------------
        if rec.get("roughnessFromAlbedoAlpha") and alpha_out is not None \
                and role in ("opaque", "glass"):
            sub = _math(nt, "SUBTRACT", -520, -60)
            sub.inputs[0].default_value = 1.0
            nt.links.new(alpha_out, sub.inputs[1])
            mx = _math(nt, "MAXIMUM", -340, -60)
            nt.links.new(sub.outputs[0], mx.inputs[0])
            mx.inputs[1].default_value = 0.06
            nt.links.new(mx.outputs[0], bsdf.inputs["Roughness"])

        # ---- normal ------------------------------------------------------
        nrm_path = rec.get("normal")
        nimg = self._image(nrm_path, True) if nrm_path else None
        if nimg is not None:
            ntex = _node(nt, "ShaderNodeTexImage", -1000, -320)
            ntex.image = nimg
            ntex.label = "normal (DirectX)"
            ntex.interpolation = "Smart"
            ntex.extension = "REPEAT"
            src = ntex.outputs["Color"]
            green_flip = bool(rec.get("normalGreenFlip", False)) or self.conv_green_flip
            if green_flip:
                sep = _node(nt, "ShaderNodeSeparateColor", -700, -320)
                sep.mode = "RGB"
                nt.links.new(src, sep.inputs["Color"])
                inv = _math(nt, "SUBTRACT", -520, -400)
                inv.inputs[0].default_value = 1.0
                nt.links.new(sep.outputs["Green"], inv.inputs[1])
                comb = _node(nt, "ShaderNodeCombineColor", -340, -320)
                comb.mode = "RGB"
                nt.links.new(sep.outputs["Red"], comb.inputs["Red"])
                nt.links.new(inv.outputs[0], comb.inputs["Green"])
                nt.links.new(sep.outputs["Blue"], comb.inputs["Blue"])
                src = comb.outputs["Color"]
            nmap = _node(nt, "ShaderNodeNormalMap", -180, -320)
            nmap.space = "TANGENT"
            _sock(nmap, "Strength", float(rec.get("normalScale", 1.0) or 1.0))
            nt.links.new(src, nmap.inputs["Color"])
            nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])

        # ---- emissive ----------------------------------------------------
        emis = rec.get("emissive")
        if isinstance(emis, dict):
            fac = emis.get("factor") or [1.0, 1.0, 1.0]
            fac = [float(x) for x in (list(fac) + [1.0, 1.0, 1.0])[:3]]
            hdr = float(emis.get("hdr", 1.0) or 0.0)
            etex = emis.get("texture")
            eimg = self._image(etex, False) if etex else None
            if eimg is not None:
                en = _node(nt, "ShaderNodeTexImage", -820, -60)
                en.image = eimg
                en.label = "emissive"
                en.extension = "REPEAT"
                eout = en.outputs["Color"]
                if any(abs(c - 1.0) > 1e-4 for c in fac):
                    mixn, ia, ib, oc = _mix_multiply(nt, -520, -60)
                    nt.links.new(eout, ia)
                    ib.default_value = (fac[0], fac[1], fac[2], 1.0)
                    eout = oc
                nt.links.new(eout, bsdf.inputs["Emission Color"])
                _sock(bsdf, "Emission Strength", hdr)
            else:
                _sock(bsdf, "Emission Color", (fac[0], fac[1], fac[2], 1.0))
                _sock(bsdf, "Emission Strength", hdr)

        return mat

    # -- mesh data ---------------------------------------------------------

    def _mesh_centres(self):
        """Per-mesh centre in the mesh's own space, (nmesh, 3) float32, computed once.

        Only meaningful for BAKED_WORLD meshes, whose "local" space IS world space. Reads the
        first and a strided sample of each vertex block rather than every vertex: this exists to
        place an instance within a radius, not to compute an exact centroid.
        """
        if getattr(self, "_centres", None) is not None:
            return self._centres
        out = np.zeros((len(self.meshes), 3), np.float32)
        f = self.fh
        for i, md in enumerate(self.meshes):
            nv = int(md.get("vtxCount", 0))
            if nv <= 0:
                continue
            step = max(1, nv // 64)                      # <=64 samples is plenty for a centre
            acc = np.zeros(3, np.float64)
            n = 0
            for v in range(0, nv, step):
                f.seek(int(md.get("vtxOffset", 0)) + v * self.vstride)
                b = f.read(12)
                if len(b) != 12:
                    break
                acc += np.frombuffer(b, np.float32, 3).astype(np.float64)
                n += 1
            if n:
                out[i] = acc / n
        self._centres = out
        return out

    def _read_mesh_arrays(self, mid):
        """(pos, nrm, uv, col_u8, idx) in MESH-LOCAL space, or None."""
        md = self.meshes[mid]
        nv = int(md.get("vtxCount", 0))
        ni = int(md.get("idxCount", 0))
        if nv <= 0 or ni < 3:
            return None
        vbytes = nv * self.vstride
        f = self.fh
        f.seek(int(md.get("vtxOffset", 0)))
        vb = f.read(vbytes)
        if len(vb) != vbytes:
            raise ValueError("short vertex read (%d/%d)" % (len(vb), vbytes))
        va = np.frombuffer(vb, dtype=self.vdtype, count=nv)

        f.seek(int(md.get("idxOffset", 0)))
        ib = f.read(ni * 4)
        if len(ib) != ni * 4:
            raise ValueError("short index read (%d/%d)" % (len(ib), ni * 4))
        idx = np.frombuffer(ib, dtype="<u4", count=ni).astype(np.int64)

        names = va.dtype.names
        if "position" in names:
            pos = np.ascontiguousarray(va["position"], dtype=np.float32).reshape(nv, -1)[:, :3]
        else:
            raise ValueError("vertex layout has no 'position'")
        if "normal" in names:
            nrm = np.ascontiguousarray(va["normal"], dtype=np.float32).reshape(nv, -1)[:, :3]
        else:
            nrm = np.zeros((nv, 3), np.float32)
            nrm[:, 2] = 1.0
        if "uv" in names:
            uv = np.ascontiguousarray(va["uv"], dtype=np.float32).reshape(nv, -1)[:, :2]
        else:
            uv = np.zeros((nv, 2), np.float32)
        if "color" in names:
            col = np.ascontiguousarray(va["color"]).reshape(nv, -1)
            if col.shape[1] < 4:
                col = np.concatenate(
                    [col, np.full((nv, 4 - col.shape[1]), 255, col.dtype)], axis=1)
            col = col[:, :4]
        else:
            col = np.full((nv, 4), 255, np.uint8)

        if idx.size % 3:
            idx = idx[:(idx.size // 3) * 3]
        if idx.size and idx.max() >= nv:
            self.log.warn("mesh %d (%s) has out-of-range indices; clamped"
                          % (mid, md.get("name")), key=("idx", mid))
            idx = np.clip(idx, 0, nv - 1)
        return pos, nrm, uv, col, idx

    def _face_materials(self, mid, ntri):
        """(material_index per triangle, [bpy Material per slot])."""
        md = self.meshes[mid]
        subs = md.get("submeshes") or []
        if not subs:
            subs = [{"materialId": 0, "idxStart": 0, "idxCount": ntri * 3}]
        fidx = np.zeros(ntri, dtype=np.int32)
        slots = []
        for si, s in enumerate(subs):
            start = int(s.get("idxStart", 0)) // 3
            cnt = int(s.get("idxCount", 0)) // 3
            a = max(0, min(ntri, start))
            b = max(a, min(ntri, start + cnt))
            if b > a:
                fidx[a:b] = si
            slots.append(self._material(int(s.get("materialId", 0))))
        return fidx, slots

    def _make_mesh(self, name, pos, nrm, uv, col, idx, fidx, slots, want_color):
        nv = pos.shape[0]

        # Drop triangles with a repeated corner. Blender's own mesh.validate()
        # would do this, but it ALSO deletes any face whose vertex set duplicates
        # another face's -- and EFT builds double-sided cards (glass shards,
        # foliage, decal planes) as two coincident triangles over one deduped
        # vertex block. Measured on a 192-mesh slice of interchange: validate()
        # removed 303 genuinely degenerate triangles and 522 real coincident
        # faces, 258 of them from one 2204-triangle glass mesh. So filter the
        # degenerate ones here, by hand, and never call validate().
        tri = np.ascontiguousarray(idx).reshape(-1, 3)
        good = ((tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2]))
        if not good.all():
            self.n_degenerate += int((~good).sum())
            tri = tri[good]
            fidx = np.ascontiguousarray(fidx)[good]
        idx = tri.ravel()
        ntri = tri.shape[0]
        if ntri == 0:
            return None

        me = bpy.data.meshes.new(name[:60])
        me.vertices.add(nv)
        me.vertices.foreach_set("co", np.ascontiguousarray(pos, np.float32).ravel())
        me.loops.add(ntri * 3)
        me.loops.foreach_set("vertex_index", np.ascontiguousarray(idx, np.int32))
        me.polygons.add(ntri)
        me.polygons.foreach_set("loop_start", np.arange(ntri, dtype=np.int32) * 3)
        me.update(calc_edges=True)

        corner = idx                              # loop -> source vertex
        uvl = me.uv_layers.new(name="UVMap", do_init=False)
        # V ORIGIN. The pack bakes its UVs for a TOP-LEFT image origin
        # (manifest.conventions uvOrigin = "top-left", uvVFlipBaked = true), which is what the
        # native renderer samples with. Blender samples from the BOTTOM-LEFT, so the baked V has
        # to be un-flipped exactly once on the way in. Without this every map texture is applied
        # upside down -- invisible on tiling asphalt and concrete, obvious the moment a texture is
        # a distinctive atlas: the vehicle bodies come out with their panels mirrored top-to-bottom.
        # The character importer does the same thing for the same reason.
        uv_b = np.ascontiguousarray(uv[corner], np.float32).copy()
        uv_b[:, 1] = 1.0 - uv_b[:, 1]
        uvl.data.foreach_set("uv", uv_b.ravel())

        if want_color:
            ca = me.color_attributes.new(name="Col", type="BYTE_COLOR", domain="CORNER")
            # BYTE_COLOR stores sRGB-encoded bytes; writing through color_srgb
            # round-trips the packed unorm8 weights byte-exactly.
            ca.data.foreach_set(
                "color_srgb",
                np.ascontiguousarray(col[corner], np.float32).ravel() * (1.0 / 255.0))

        for m in slots:
            me.materials.append(m)
        me.polygons.foreach_set("material_index", fidx)
        me.polygons.foreach_set("use_smooth", np.ones(ntri, dtype=np.int32))

        n2 = np.ascontiguousarray(nrm, np.float32)
        ln = np.linalg.norm(n2, axis=1)
        bad = ln < 1e-8
        if bad.any():
            n2 = n2.copy()
            n2[bad] = (0.0, 0.0, 1.0)
            ln = np.where(bad, 1.0, ln)
        n2 = n2 / ln[:, None]
        try:
            me.normals_split_custom_set_from_vertices(n2)
        except Exception as e:
            self.log.warn("custom normals rejected on %s (%s)" % (name, e), key="normals")
        me.update()
        return me

    def _shared_mesh(self, mid):
        me = self.mesh_cache.get(mid)
        if me is not None:
            return me
        md = self.meshes[mid]
        try:
            arrays = self._read_mesh_arrays(mid)
        except Exception as e:
            self.log.warn("mesh %d (%s) unreadable: %s" % (mid, md.get("name"), e),
                          key=("read", mid))
            self.mesh_cache[mid] = False
            self.n_mesh_fail += 1
            return False
        if arrays is None:
            self.mesh_cache[mid] = False
            return False
        pos, nrm, uv, col, idx = arrays
        ntri = idx.size // 3
        fidx, slots = self._face_materials(mid, ntri)
        want_col = self._wants_color(mid, col)
        name = "%s%s" % (_clean(md.get("name") or ("mesh%d" % mid)), self.tag)
        me = self._make_mesh(name, pos, nrm, uv, col, idx, fidx, slots, want_col)
        # False, not None: None means "not cached yet" and would rebuild forever.
        self.mesh_cache[mid] = me if me is not None else False
        return self.mesh_cache[mid]

    def _wants_color(self, mid, col):
        """COLOR_0 is only meaningful for vertex-paint splat materials; at
        4 bytes per loop over 52M loops it is not free, so skip it when the
        data is the constant white the assembler writes for everything else."""
        for s in (self.meshes[mid].get("submeshes") or []):
            r = self.mats[int(s.get("materialId", 0))] if 0 <= int(s.get("materialId", 0)) < len(self.mats) else None
            if isinstance(r, dict) and r.get("vp"):
                return True
        return bool((col != 255).any())

    # -- baking ------------------------------------------------------------

    def _baked_mesh(self, mid, aff, name):
        """Sheared / rank-deficient instance: geometry to world, identity object."""
        arrays = self._read_mesh_arrays(mid)
        if arrays is None:
            return None
        pos, nrm, uv, col, idx = arrays
        m3 = np.array([aff[0:3], aff[4:7], aff[8:11]], dtype=np.float64)
        t = np.array([aff[3], aff[7], aff[11]], dtype=np.float64)

        wpos = (pos.astype(np.float64) @ m3.T + t[None, :]).astype(np.float32)
        try:
            nmat = np.linalg.inv(m3).T
        except np.linalg.LinAlgError:
            nmat = np.linalg.pinv(m3).T
        if not np.isfinite(nmat).all():
            nmat = np.linalg.pinv(m3).T
        wnrm = (nrm.astype(np.float64) @ nmat.T).astype(np.float32)

        det = float(np.linalg.det(m3))
        if det < 0.0:
            # The linear part mirrors, so the triangle winding must flip to keep
            # the geometric normal agreeing with the transformed shading normal.
            idx = np.ascontiguousarray(idx.reshape(-1, 3)[:, ::-1]).ravel()

        ntri = idx.size // 3
        fidx, slots = self._face_materials(mid, ntri)
        want_col = self._wants_color(mid, col)
        return self._make_mesh(name, wpos, wnrm, uv, col, idx, fidx, slots, want_col)

    # -- run ---------------------------------------------------------------

    def run(self):
        t0 = time.time()
        self._load_manifest()
        self._load_instances()
        mesh_path = os.path.join(self.pack, "meshes.bin")
        if not os.path.isfile(mesh_path):
            raise IOError("no meshes.bin in %s" % self.pack)
        self.fh = open(mesh_path, "rb")
        try:
            return self._run_open(t0)
        finally:
            if self.fh is not None:
                self.fh.close()
                self.fh = None

    def _run_open(self, t0):
        # meshes.bin is already open: _select() needs it to place BAKED_WORLD
        # instances, whose affine is identity and whose position lives in the
        # geometry itself.
        self._select()
        self._classify_shear()

        log = self.log
        log.info("pack     %s" % self.pack)
        log.info("map      %s   dataset %s%s" % (
            self.map_id, self.man.get("dataset"),
            "   self-contained" if self.man.get("selfContained") else ""))
        log.info("layout   vertex stride %d, instance stride %d, meshes %d, materials %d, instances %d"
                 % (self.vstride, self.istride, len(self.meshes),
                    len(self.mats), self.inst_n))
        b = self.man.get("bounds")
        if b:
            log.info("bounds   pack-space (Y-up) min %s max %s"
                     % ([round(x, 1) for x in b[0:3]], [round(x, 1) for x in b[3:6]]))
        s = self.stats
        log.info("filter   lod=%s center=%s radius=%s max=%s  ->  %d instances"
                 % (self.lod,
                    None if self.center is None else [round(float(x), 1) for x in self.center],
                    self.radius, self.max_instances, self.sel.size))
        log.info("dropped  inactive %d, lod %d, radius %d, cap %d, bad-meshId %d, non-finite %d"
                 % (s["inactive"], s["lod"], s["radius"], s["cap"],
                    s["badMeshId"], s["nonfinite"]))
        log.info("shear    %d instance(s) exceed |dot| %.2f (max %.4f) -> baked to world geometry"
                 % (int(self.bake_mask.sum()), SHEAR_TOL, self.max_skew))

        # Unique suffix so repeated imports never collide on datablock names.
        self.tag = ".%s" % _short_tag()

        cname = self.collection_name or ("eftpack_%s%s" % (self.map_id, self.tag))
        coll = bpy.data.collections.new(cname[:60])
        bpy.context.scene.collection.children.link(coll)
        empty = bpy.data.objects.new("%s_root" % cname[:50], None)
        empty.empty_display_type = "PLAIN_AXES"
        empty.empty_display_size = 10.0
        # The ONE place the Y-up -> Z-up change lives.
        empty.matrix_world = YUP_TO_ZUP
        coll.objects.link(empty)

        self._build(coll, empty)

        # Parenting is a depsgraph relation: until it is evaluated, every child's
        # matrix_world still reads WITHOUT the empty's Y-up -> Z-up rotation. A
        # caller that measures, exports or raycasts right after the import would
        # silently get the un-rotated frame, so flush it here.
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass

        dt = time.time() - t0
        shared = sum(1 for v in self.mesh_cache.values() if v)
        log.info("-" * 62)
        log.info("objects %d   shared meshes %d   baked-shear %d   materials %d"
                 % (self.n_objects, shared, self.n_baked, len(self.mat_cache)))
        log.info("images  %d loaded, %d texture path(s) unresolved, %d mesh(es) failed"
                 % (sum(1 for v in self.img_cache.values() if v is not None),
                    self.n_tex_missing, self.n_mesh_fail))
        log.info("geom    %d degenerate triangle(s) dropped (repeated corner); coincident "
                 "double-sided faces kept" % self.n_degenerate)
        log.info("warnings %d (%d distinct)   elapsed %.1fs   collection %r"
                 % (log.n, len(log._seen), dt, coll.name))
        return {
            "collection": coll, "empty": empty,
            "objects": self.n_objects, "meshes": shared,
            "baked_shear": self.n_baked, "materials": len(self.mat_cache),
            "images": sum(1 for v in self.img_cache.values() if v is not None),
            "textures_missing": self.n_tex_missing,
            "meshes_failed": self.n_mesh_fail,
            "degenerate_tris": self.n_degenerate,
            "warnings": log.n, "elapsed": dt,
        }

    def _build(self, coll, empty):
        sel = self.sel
        bake = self.bake_mask
        log = self.log
        eye = Matrix.Identity(4)

        # Shared datablocks first, in meshId order == meshes.bin order, so the
        # 845 MB blob is walked front to back instead of seeked at random.
        need = np.unique(self.inst_mesh[sel][~bake]) if sel.size else np.empty(0, np.int64)
        total = int(need.size)
        step = max(1, total // 4)
        for k, mid in enumerate(need):
            if k and k % step == 0:
                log.info("  meshes %3d%%  (%d/%d)" % (100 * k // max(1, total), k, total))
            self._shared_mesh(int(mid))

        link = coll.objects.link
        names = self.roots
        aff = self.inst_aff
        for k in range(sel.size):
            i = int(sel[k])
            mid = int(self.inst_mesh[i])
            a = aff[i]
            base = _clean(self.meshes[mid].get("name") or ("mesh%d" % mid))[:40]
            oname = "%s.%05d" % (base, i)

            if bake[k]:
                try:
                    me = self._baked_mesh(mid, a, "%s.baked" % base)
                except Exception as e:
                    log.warn("bake failed for instance %d (mesh %d): %s" % (i, mid, e),
                             key=("bake", mid))
                    me = None
                if me is None:
                    self.n_mesh_fail += 1
                    continue
                mw = eye
                self.n_baked += 1
                oname = "%s.baked.%05d" % (base, i)
            else:
                me = self._shared_mesh(mid)
                if not me:
                    continue
                # ROW-major 3x4 -> 4x4. Never decomposed, never transposed.
                mw = Matrix(((float(a[0]), float(a[1]), float(a[2]), float(a[3])),
                             (float(a[4]), float(a[5]), float(a[6]), float(a[7])),
                             (float(a[8]), float(a[9]), float(a[10]), float(a[11])),
                             (0.0, 0.0, 0.0, 1.0)))

            obj = bpy.data.objects.new(oname, me)
            obj.parent = empty
            obj.matrix_parent_inverse = eye
            # matrix_basis, not matrix_world: the empty's Y-up -> Z-up rotation
            # must be the parent's, applied exactly once, not folded in here.
            obj.matrix_basis = mw

            # Blender rewrites a trailing numeric suffix when names collide, so
            # the index in the name is decoration; this property is the join key.
            obj["eft_instance"] = i
            obj["eft_mesh"] = mid
            flags = int(self.inst_flags[i])
            if flags:
                obj["eft_flags"] = flags
            root = int(self.inst_root[i])
            if 0 <= root < len(names) and names[root]:
                obj["eft_root"] = names[root]
            if flags & FLAG_INACTIVE:
                obj.hide_viewport = True
                obj.hide_render = True
            link(obj)
            self.n_objects += 1


def _set(obj, attr, value):
    try:
        setattr(obj, attr, value)
    except Exception:
        pass


def _sock(node, name, value):
    try:
        node.inputs[name].default_value = value
    except Exception:
        pass


def _clean(s):
    s = str(s)
    for suf in (".obj", ".OBJ"):
        if s.endswith(suf):
            s = s[:-len(suf)]
    return s


_TAG_SEQ = [0]


def _short_tag():
    _TAG_SEQ[0] += 1
    return "%03d" % (int(time.time()) % 1000 + _TAG_SEQ[0] - 1)


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def import_eftpack(pack_dir, center=None, radius=None, max_instances=None,
                   with_textures=True, lod=0, include_inactive=False,
                   collection_name=None, verbose=True):
    """Import a .eftpack map into the current Blender scene.

    pack_dir        path to the *.eftpack DIRECTORY (holding manifest.json).
    center, radius  when BOTH are given, only instances whose world translation
                    lies within `radius` metres of `center` are imported. This
                    is what makes a full-fidelity local import tractable.
                    `center` is in PACK space -- right-handed, Y-UP, the same
                    space as manifest.bounds. For a point you read off Blender's
                    header (bx, by, bz), pass (bx, bz, -by).
    max_instances   hard cap applied after every other filter, in file order.
    with_textures   False builds flat tint-only materials and loads no images.
    lod             LOD level to keep. Instances with lodIndex < 0 are not part
                    of any LOD group and are ALWAYS kept. Pass None for every
                    level (expect z-fighting between shells).
    include_inactive  keep Unity-disabled geometry (flag 0x8), hidden.
    collection_name   name for the created collection.

    Returns a summary dict; also prints a compact one.
    """
    imp = _Importer(pack_dir, center, radius, max_instances, with_textures,
                    lod, include_inactive, collection_name, verbose)
    return imp.run()


def _find_pack():
    env = os.environ.get("EFT_PACK")
    if env and os.path.isdir(env):
        return env
    seeds = []
    try:
        if bpy.data.filepath:
            seeds.append(os.path.dirname(bpy.data.filepath))
    except Exception:
        pass
    seeds.append(os.getcwd())
    seen = set()
    for seed in seeds:
        d = os.path.abspath(seed)
        for _ in range(6):
            if d in seen:
                break
            seen.add(d)
            pk = os.path.join(d, "packs")
            if os.path.isdir(pk):
                hits = sorted(x for x in os.listdir(pk) if x.endswith(".eftpack")
                              and os.path.isfile(os.path.join(pk, x, "manifest.json")))
                if hits:
                    return os.path.join(pk, hits[0])
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
    return None


def _env_float(name):
    v = os.environ.get(name)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def main():
    """Entry point used when this file is exec'd. Configured by EFT_* env vars."""
    pack = _find_pack()
    if not pack:
        print("[eftpack] no pack found. Set EFT_PACK=<path to *.eftpack>, or call "
              "import_eftpack(pack_dir, center=None, radius=None, max_instances=None, "
              "with_textures=True, lod=0).")
        return None

    center = None
    cs = os.environ.get("EFT_CENTER")
    if cs:
        try:
            parts = [float(x) for x in cs.replace(";", ",").split(",") if x.strip() != ""]
            if len(parts) == 3:
                center = parts
        except ValueError:
            print("[eftpack] EFT_CENTER=%r is not 'x,y,z'; ignoring" % cs)

    radius = _env_float("EFT_RADIUS")
    mi = os.environ.get("EFT_MAX_INSTANCES")
    max_instances = int(mi) if (mi or "").strip().lstrip("-").isdigit() else None
    with_textures = os.environ.get("EFT_TEXTURES", "1") not in ("0", "false", "False")
    lod_s = (os.environ.get("EFT_LOD") or "0").strip()
    lod = None if lod_s in ("all", "-", "none", "None", "*") else int(lod_s)

    return import_eftpack(pack, center=center, radius=radius,
                          max_instances=max_instances,
                          with_textures=with_textures, lod=lod)


main()
