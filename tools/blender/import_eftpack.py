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

# Coplanar overlays (decal/water) are lifted this far along their vertex normal, the
# geometric stand-in for the renderer's clip-space push. Large enough to break the tie
# with the surface underneath, small enough that nothing floats at gameplay distances.
DECAL_LIFT = 0.006

# Background strength assumed for the glass reflection probe when the scene has NO world yet.
# _sky_probe() reads the real one whenever a world exists, and that is the normal case: this
# only covers an import into an empty file. example_scene.py builds the world AFTER the map, so
# that path always lands here, and 2.35 is exactly its SKY_STRENGTH -- least-squares-solved
# against an Atlas frame through the game's own grade LUT, not eyeballed. EFT_SKY_STRENGTH
# overrides it for an A/B.
SKY_STRENGTH_FALLBACK = 2.35

def _detail_xform(det_st, base_st):
    """Un-bake a secondary ST from the BAKED base UV, V-FLIP AWARE.

    Mirrors `detail_xform` in gpu_draw.wgsl. The assembler baked layer 0's ST into the vertex UVs
    and then flipped V (`v' = 1 - (v*sy + oy)`), so the naive `(uv - offset) / scale` is wrong and
    shifts a detail map or vert-paint layer by up to half a tile. Used by both.
    """

    bsx = base_st[0] if abs(base_st[0]) > 1e-6 else 1.0
    bsy = base_st[1] if abs(base_st[1]) > 1e-6 else 1.0
    rx, ry = det_st[0] / bsx, det_st[1] / bsy
    return (rx, ry, det_st[2] - base_st[2] * rx, 1.0 - det_st[3] - ry * (1.0 - base_st[3]))



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


def _srgb_encode(nt, sock, x, y):
    """linear -> sRGB, the exact IEC 61966-2-1 piecewise, returning a scalar socket.

    Blender hands the graph the LINEAR decode of a BYTE_COLOR attribute; the renderer reads the
    stored byte. This puts the byte back. Exact, not the 2.2 shortcut: the linear segment below
    0.0031308 is where an almost-unpainted vertex lives, and that is precisely the range the
    empty-mask gate tests.
    """
    lo = _math(nt, "MULTIPLY", x, y - 80)
    lo.inputs[1].default_value = 12.92
    nt.links.new(sock, lo.inputs[0])
    pw = _math(nt, "POWER", x, y)
    pw.inputs[1].default_value = 1.0 / 2.4
    nt.links.new(sock, pw.inputs[0])
    hi = _math(nt, "MULTIPLY_ADD", x + 140, y)
    hi.inputs[1].default_value = 1.055
    hi.inputs[2].default_value = -0.055
    nt.links.new(pw.outputs[0], hi.inputs[0])
    lt = _math(nt, "LESS_THAN", x + 140, y - 160)
    lt.inputs[1].default_value = 0.0031308
    nt.links.new(sock, lt.inputs[0])
    mix = _node(nt, "ShaderNodeMix", x + 300, y)
    mix.data_type = 'FLOAT'
    nt.links.new(lt.outputs[0], mix.inputs[0])
    nt.links.new(hi.outputs[0], mix.inputs[2])
    nt.links.new(lo.outputs[0], mix.inputs[3])
    return mix.outputs[0]


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
                 with_textures, lod, include_inactive, collection_name, verbose,
                 glass_mode="trs", cavity_dir=None):
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
        self.n_lifted = 0
        self._bounds = None

        # EFT_PARALLAX=0 masks MAT_FLAG_PARALLAX for every material in the viewer
        # (gpu_driven.rs:2280). Same kill switch here so an A/B is one env var.
        self.parallax_on = os.environ.get("EFT_PARALLAX", "1") not in ("0", "false", "False")
        # Cycles exposes no bitangent sign, so B = N x T is a handedness GUESS for mirrored UV
        # shells. EFT_PARALLAX_SIGN=-1 flips it. (The viewer has the same ambiguity and drops
        # sign(det) when it normalizes t_raw/b_raw, so there is no sign to copy exactly.)
        self.parallax_sign = -1.0 if str(
            os.environ.get("EFT_PARALLAX_SIGN", "1")).strip().startswith("-") else 1.0

        # EFT_BEVEL=<radius in metres>, 0 = off, which is the DEFAULT: at 0 no node is built and
        # a parity render is the pre-switch render to within the renderer's OWN run-to-run float
        # noise (measured on a 967-object build: pre vs post mean |d| 2.0e-8, max 2.6e-3, against
        # 8.6e-8 / 2.8e-3 for the SAME code rendered twice -- OptiX accumulation order is not
        # reproducible on this box, so "bit for bit" is not a test that can pass here). Lever 1 of
        # docs/extraction/photoreal-lowergamefidelity.md -- a Cycles Bevel node rounding the
        # SHADING normal on opaque materials only, because a rasteriser ships perfect 90 degree
        # corners and 19.5% of this corpus' manifold edge pairs (measured over 889,005 edges on
        # 120 meshes) sit in the 80-100 degree band with nothing to catch a highlight.
        # 2 mm is the doc's radius and it is bounded from both sides: it must stay well under
        # DECAL_LIFT = 6 mm or the ground's bevel query starts hitting the decal plane floating
        # above it, and under the p5 edge length of 4.5 mm or the node stops being an edge
        # treatment and becomes a general normal blur on the densest meshes.
        try:
            self.bevel_radius = max(0.0, float(os.environ.get("EFT_BEVEL", "0") or 0.0))
        except ValueError:
            self.bevel_radius = 0.0
        # COST, MEASURED, not estimated. 967 objects / 468 materials / 323 bevelled, 2560x1440 at
        # 512 spp on OptiX, best of six renders per config, twice: 22.82 and 23.02 s off against
        # 24.18 and 24.48 s on, i.e. +5.9% and +6.3%. The low end of the 5-15% the doc guessed.
        # Samples is the first knob to turn if that ever comes in high: the noise the node makes
        # is confined to a 2 mm band, which is exactly what a denoiser eats.
        try:
            self.bevel_samples = max(1, int(os.environ.get("EFT_BEVEL_SAMPLES", "4") or 4))
        except ValueError:
            self.bevel_samples = 4
        if self.bevel_radius >= DECAL_LIFT:
            self.log.warn("bevel radius %.4f m is not under DECAL_LIFT %.4f m; every decal and "
                          "puddle plane is now inside the receiver's bevel query and both sides "
                          "get their normals smeared along the seam"
                          % (self.bevel_radius, DECAL_LIFT), key=("bevlift",))
        self.n_bevel = 0

        # material ids carrying MAT_FLAG_WATER_MATTE (gpu_driven.rs:1904-1958), filled by
        # _classify_water_matte() before any material is built.
        self.water_matte = set()
        # lazily derived from the pack / the scene by _sky_probe() and _dom_light(), both of
        # which the glassTRS graph needs and neither of which is per-material.
        self._sky = None
        self._dom = None
        self.n_water_reuv = 0
        self.n_glass_trs = 0
        self.n_parallax = 0
        self.n_vp_graph = 0
        self.n_water_puddle = 0
        self.n_water_matte_mat = 0
        self.n_water_deep = 0

        # PHOTOREALISM SWITCHES. Both default to the viewer's behaviour, both are documented in
        # docs/extraction/photorealism.md, and neither may be on in a parity build.
        if glass_mode not in ("trs", "physical"):
            raise ValueError("glass_mode must be 'trs' or 'physical', got %r" % glass_mode)
        self.glass_mode = glass_mode
        self.cavity_dir = os.path.abspath(cavity_dir) if cavity_dir else None
        self.n_glass_phys = 0
        self.n_cavity = 0
        self._cav_cache = {}         # normal-map basename -> bpy Image or None
        self._cav_meta = None

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
        # needed to find each LOD group's FINEST shipped shell (see the lod filter below)
        self.inst_lodg = col("lodGroup", -1, np.int64)
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
            # A group whose FINEST shipped shell is not the requested level would otherwise
            # vanish outright: some groups ship only LOD1+ (the assembler's dedup can drop a
            # redundant LOD0), so an equality test deletes the object instead of drawing it at
            # the closest shell it has. Take the group's minimum available level instead, which
            # is what the viewer does when it clamps a forced shell.
            lodok = (self.inst_lodi == lodv) | (self.inst_lodi < 0)
            grp = self.inst_lodg
            real = grp >= 0
            if real.any():
                gmax = int(grp[real].max())
                finest = np.full(gmax + 2, 1 << 30, np.int64)
                np.minimum.at(finest, grp[real], self.inst_lodi[real])
                nearest = (self.inst_lodi == finest[np.clip(grp, 0, gmax)]) & real
                rescued = int((nearest & ~lodok & keep).sum())
                if rescued:
                    # `log` (no self) was a NameError waiting for the first pack that ships a LOD
                    # group with no LOD0 shell -- it would abort the import, not log it.
                    self.log.info("lod      %d instance(s) kept at their group's finest shell "
                                  "(no LOD%d shipped)" % (rescued, lodv))
                lodok |= nearest
            stats["lod"] = int((~lodok & keep).sum())
            keep &= lodok
        else:
            stats["lod"] = 0

        if self.center is not None and self.radius is not None:
            # WHERE THE GEOMETRY ACTUALLY IS = M3 @ mesh_AABB + T, not T alone and not a single
            # point. For an ordinary instance the mesh is modelled about its own origin so its
            # translation is ~where it is, but geometry that was pre-baked into world space ships
            # an IDENTITY affine, which puts T at the origin no matter where the object stands.
            # Every projected decal is like that: 1353 of them on this map, so a radius filter on T
            # alone imported the checkpoint plates and not one of their sprays. (They are not
            # flagged BAKED_WORLD either, so testing the flag is not enough - derive it from the
            # geometry.)
            #
            # AND IT MUST BE THE EXTENT, NOT A CENTRE. A centre test asks "is this object's middle
            # near the camera", which is the wrong question for anything larger than the radius.
            # Terrain ships as four ~700 m tiles: Slice_2_2's world AABB CONTAINS the ZoneBearCamp
            # route centroid - AABB distance 0.0 m - while its true centroid is 163.3 m away and
            # the old strided 64-vertex sample reported 185.5 m (the stride ALIASES on a 513x513
            # terrain grid). MAP_RADIUS 170 therefore culled the tile the camera is standing on,
            # and terrain_splat then printed "rebuilt 0 terrain material(s)": ground props floating
            # over a void, with no error anywhere.
            #
            # So: transform the mesh's own local AABB and test the DISC against the world-space box.
            #     world_c = M3 @ local_c + T          box centre
            #     world_e = |M3| @ local_e            half-extent; |M3| is the correct conservative
            #                                         bound for a rotated/sheared box
            #     q = clip(center, world_c +/- world_e)      nearest point of the box to the camera
            #     inside = |q - center|^2 <= radius^2
            # This is a strict SUPERSET of the centre test (a mean of vertices always lies inside
            # the hull, hence inside the box, so the box can only ever be nearer), which is what
            # makes it safe to swap in: nothing that used to import can stop importing. Measured at
            # MAP_RADIUS 170 on interchange: 3,683 -> 3,766 instances (+2.3%), +766,415 triangles,
            # 524,288 of which ARE the terrain slice that was missing.
            #
            # NOT A BOUNDING SPHERE: Slice_2_2's conservative sphere is 857.5 m on a flat 700 m
            # tile, which admits all four terrain tiles at any radius and re-imports the whole map.
            bnd = self._mesh_bounds()
            mids = np.clip(self.inst_mesh, 0, max(0, len(bnd) - 1))
            lo = bnd[mids, 0].astype(np.float64)
            hi = bnd[mids, 1].astype(np.float64)
            local_c = (hi + lo) * 0.5
            local_e = (hi - lo) * 0.5
            m3 = self.inst_aff[:, [0, 1, 2, 4, 5, 6, 8, 9, 10]].reshape(-1, 3, 3)
            world_c = np.einsum("nij,nj->ni", m3, local_c) + self.inst_aff[:, [3, 7, 11]]
            world_e = np.einsum("nij,nj->ni", np.abs(m3), local_e)
            q = np.clip(self.center[None, :], world_c - world_e, world_c + world_e)
            d = q - self.center[None, :]
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

    def _parallax(self, nt, rec, role, base_tex, nrm_tex):
        """Single-step (offset-limited) parallax UV, rewired onto the base albedo + base normal.

        THE VIEWER MARCHES (gpu_draw.wgsl:1019-1063): 8..32 layers stepping along the tangent-space
        view ray until the marched depth passes the sampled depth, then interpolates between the
        last two layers. A Cycles node graph has no loops and no conditional re-sampling, so the
        search cannot be expressed. What CAN be expressed exactly is the shader's OWN FIRST
        ITERATION -- same constants, nothing invented. Eliminating the step index from the march
        gives it in closed form,

            uv(d) = uv - (Vts.xy / vz) * (scale * fade) * d,    d in [0,1]

        so `num` and `layer` cancel completely and porting them would be cargo cult. Taking
        d = h(uv) is the Kaneko/Welsh approximation. It OVERSHOOTS where the height field is
        steep; on this corpus (brick, roof tile, concrete slab, parquet -- measured 57..189/255 on
        City_Roof_tile_disp) the error is small, but a near-binary height field with deep mortar
        grooves gets a smear where steep parallax gives a crisp occlusion edge. No way to close
        that gap in a Principled graph; OSL is the only real march, and it forces
        cycles.shading_system = True (CPU/OptiX only) for 139 materials in two packs.

        DEPTH, NOT HEIGHT. The shader reads `h = 1.0 - sample.g` -- the texture's WHITE is the top
        of the surface and the relief is recessed BELOW the polygon plane. Feeding `g` straight in
        inverts it. The green channel specifically: these maps are near-grayscale but not
        bit-identical across channels (City_Roof_tile_disp R/G/B means 119.73/119.51/119.73, r==g
        nowhere), so an RGB->BW average is not the same number.

        NO BUMP NODE, DELIBERATELY. All 139 affected materials carry a real tangent-space normal
        map, and the viewer's parallax perturbs NO normal -- it only moves where that normal map
        is SAMPLED (:1173). A Bump node would add a second copy of the same relief's shading, and
        it fails worst exactly where parallax is invisible: head-on, V.T and V.B go to zero so the
        true offset is exactly ZERO while a Bump node's perturbation is at its maximum. That is
        the specMap trap again -- a field that looks like an upgrade to bind and is a regression.
        Displacement is a different and more expensive feature (it moves silhouettes, which
        parallax never does) and needs a UV-to-metres conversion the pack does not carry.

        NOT PORTED: the 25..50 m distance fade (:1030-1033). It exists to stop per-quad
        screen-derivative shimmer under camera motion; an offline path trace has no temporal
        shimmer, and past 50 m the offset is sub-pixel and moves no silhouette. This file already
        made the same call for detail maps. fade = 1.0 everywhere.
        NOT PORTED: textureSampleGrad's explicit gradients (:1051-1060). Cycles has no
        explicit-gradient lookup, so these two samples filter slightly worse than their
        neighbours. Costs a little texture noise, nothing structural.
        DIVERGENCE, BY CHOICE: the viewer's relief is defined against ONE view vector, the
        camera's. Geometry > Incoming is PER-RAY, so a diffuse bounce or the same wall seen in a
        mirror each get their own offset. That is more physical than the viewer but it means a
        Cycles render can never be pixel-identical. Camera Data > View Vector (through a
        VectorTransform from CAMERA to WORLD) would buy parity at the cost of relief that is
        visibly wrong inside reflections.
        """
        par = rec.get("parallax") if isinstance(rec.get("parallax"), dict) else None
        pmap = (par or {}).get("map")
        # gpu_driven.rs:2289 clamps the authored scale to [0.0, 0.5]. Measured range across the
        # 139 affected materials is 0.005..0.08, so the clamp never binds; carry it anyway.
        pscale = min(max(float((par or {}).get("scale", 0.02) or 0.0), 0.0), 0.5)
        if not (par and pmap and self.parallax_on and pscale > 0.0):
            return
        # The assembler never emits parallax on a vert-paint material (assemble_bevy.py:394 --
        # the vp splat owns its own UV), so terrain never carries it either.
        if isinstance(rec.get("vp"), dict) or role == "terrain":
            return
        if base_tex is None and nrm_tex is None:
            return
        # is_data -> Non-Color + CHANNEL_PACKED, matching ctrl_tex_linear.insert() at
        # gpu_driven.rs:2288: a height map is linear DATA, never sRGB.
        himg = self._image(pmap, True)
        if himg is None:
            return

        uvn = _node(nt, "ShaderNodeUVMap", -2200, 1180)
        uvn.uv_map = "UVMap"
        geo = _node(nt, "ShaderNodeNewGeometry", -2200, 1560)
        # Geometry > Normal is the shading normal ALREADY back-face-flipped, which is exactly the
        # shader's gN (:1088); it is not affected by the Normal Map node. Geometry > Incoming is
        # the unit vector toward where the point is viewed from == the shader's
        # Vw = (view.world_position - wp)/dist for camera rays. Both world space, matching the
        # world-space T/B/N the shader dots against. The UV_MAP tangent, not Geometry > Tangent:
        # the latter is radial and has nothing to do with the UV shell.
        tan = _node(nt, "ShaderNodeTangent", -2200, 1360)
        tan.direction_type = "UV_MAP"
        tan.uv_map = "UVMap"

        ph = _node(nt, "ShaderNodeTexImage", -2020, 1240)
        ph.image = himg
        ph.label = "parallax height"
        ph.extension = "REPEAT"
        ph.interpolation = "Smart"
        nt.links.new(uvn.outputs["UV"], ph.inputs["Vector"])
        psep = _node(nt, "ShaderNodeSeparateColor", -1840, 1240)
        psep.mode = "RGB"
        nt.links.new(ph.outputs["Color"], psep.inputs["Color"])
        pdep = _math(nt, "SUBTRACT", -1680, 1240)          # h0 = 1 - g
        pdep.inputs[0].default_value = 1.0
        nt.links.new(psep.outputs["Green"], pdep.inputs[1])

        # B = N x T. Cycles exposes no bitangent sign, so this is a handedness guess for UV
        # shells mirrored relative to the outward normal; a wrong sign makes the relief pop OUT
        # instead of in. EFT_PARALLAX_SIGN=-1 flips it. The viewer normalizes t_raw/b_raw without
        # dividing by the UV Jacobian determinant (:1037-1041), silently dropping sign(det) from
        # BOTH axes, so its own offset reverses on mirrored shells -- exact sign parity with the
        # viewer is not a well-defined target. Match the physically-correct recessed look.
        cross = _node(nt, "ShaderNodeVectorMath", -2020, 1460)
        cross.operation = "CROSS_PRODUCT"
        nt.links.new(geo.outputs["Normal"], cross.inputs[0])
        nt.links.new(tan.outputs["Tangent"], cross.inputs[1])
        bsgn = _node(nt, "ShaderNodeVectorMath", -1880, 1460)
        bsgn.operation = "SCALE"
        nt.links.new(cross.outputs["Vector"], bsgn.inputs[0])
        bsgn.inputs["Scale"].default_value = self.parallax_sign

        def _dot(a, b, y):
            n = _node(nt, "ShaderNodeVectorMath", -1680, y)
            n.operation = "DOT_PRODUCT"
            nt.links.new(a, n.inputs[0]); nt.links.new(b, n.inputs[1])
            return n.outputs["Value"]

        d_t = _dot(geo.outputs["Incoming"], tan.outputs["Tangent"], 1560)
        d_b = _dot(geo.outputs["Incoming"], bsgn.outputs["Vector"], 1460)
        d_n = _dot(geo.outputs["Incoming"], geo.outputs["Normal"], 1360)

        pvz = _math(nt, "MAXIMUM", -1520, 1360)            # vz = max(V.N, 0.15), the grazing clamp
        pvz.inputs[1].default_value = 0.15
        nt.links.new(d_n, pvz.inputs[0])
        pks = _math(nt, "MULTIPLY", -1520, 1240)
        pks.inputs[1].default_value = pscale
        nt.links.new(pdep.outputs[0], pks.inputs[0])
        pk = _math(nt, "DIVIDE", -1360, 1240)              # k = scale * h0 / vz
        nt.links.new(pks.outputs[0], pk.inputs[0])
        nt.links.new(pvz.outputs[0], pk.inputs[1])

        pxy = _node(nt, "ShaderNodeCombineXYZ", -1360, 1460)
        pxy.inputs["Z"].default_value = 0.0
        nt.links.new(d_t, pxy.inputs["X"])
        nt.links.new(d_b, pxy.inputs["Y"])
        poff = _node(nt, "ShaderNodeVectorMath", -1200, 1460)
        poff.operation = "SCALE"
        nt.links.new(pxy.outputs["Vector"], poff.inputs[0])
        nt.links.new(pk.outputs[0], poff.inputs["Scale"])
        ppuv = _node(nt, "ShaderNodeVectorMath", -1050, 1300)
        ppuv.operation = "SUBTRACT"
        ppuv.label = "puv (single-step parallax)"
        nt.links.new(uvn.outputs["UV"], ppuv.inputs[0])
        nt.links.new(poff.outputs["Vector"], ppuv.inputs[1])

        # Exactly the two samples the shader feeds with puv -- base albedo (:1127) and base normal
        # (:1173). Emissive stays on o.uv (:1135); detail albedo/normal own their own Mapping
        # nodes; vp is mutually exclusive. tex.a rides puv for free, which is what puts the RFA
        # per-pixel roughness (:1432) and the cutout width (:1142) ON the relief instead of beside
        # it -- 137 of the 139 affected materials are RFA.
        if base_tex is not None:
            nt.links.new(ppuv.outputs["Vector"], base_tex.inputs["Vector"])
        if nrm_tex is not None:
            nt.links.new(ppuv.outputs["Vector"], nrm_tex.inputs["Vector"])
        self.n_parallax += 1
        self.log.warn("parallax: single-step approximation of the viewer's 32-step march "
                      "(no loops in a Cycles graph)", key=("parallax",))

    def _sky_probe(self):
        """(E0, L1x, L1y, L1z) -- the L1 SH of the scene's sky, prescaled for the shader's own
        reconstruction, in BLENDER space. Each is an (r, g, b) tuple.

        The legacy family's reflection input is texCUBE(_Cube): ONE lookup along the mirror
        vector, bounded [0,1] because it IS an image. The viewer has no cube for these materials
        (RenderSettings ships customReflection=null on every level) and substitutes the baked SH
        volume (:1499), then Reinhard-compresses it back into the LDR domain the lookup lived in
        (:1627-1632). Cycles has no probe volume either, so this reproduces the SUBSTITUTE rather
        than the original: the same band-1 reconstruction the shader runs (:336-342),

            e = 0.282095*c0 + 0.325735*(c1*n.y + c2*n.z + c3*n.x),   max(e, ambient_floor)

        with the coefficients projected off the world's sky instead of read out of volume.bin.

        WHY NOT SAMPLE THE SKY IMAGE DIRECTLY ALONG R, which is the more literal texCUBE twin:
        measured, it is 3x too dark at grazing. An equirect's lower hemisphere is ground, and a
        pane seen edge-on mirrors the horizon, where the image is near black (linear luma 0.060
        in the horizon band) while the viewer's probe -- an IRRADIANCE reconstruction, cosine-
        convolved and therefore never dark -- reads 0.58 there. Reconstructing the same way the
        shader does removes that whole class of mismatch, and it is still a VALUE, so the bound
        below survives.

        THE UNITS ARE NOT ASSUMED, THEY ARE CHECKED. The one thing here that is not pack-derived
        is the world: an equirect sky at some Background strength. At example_scene.py's solved
        2.35 this projection lands on the baked volume it stands in for, on interchange:
            DC-only (n-independent) luma   0.739   vs the volume's measured p50 0.740
            most-lit direction (up)        1.452   vs the volume's measured p50 1.51
        i.e. within 4% without a single fitted term. Reading the strength off the world node keeps
        the two in step if it is ever re-solved.

        Azimuth caveat: the projection uses make_sky_equirect.py's own (u, v) -> direction map,
        which is that file's contract for images it wrote. If Blender's sampler disagrees about
        where u = 0.5 points, the two horizontal L1 terms rotate; the sky is an up/down gradient
        (c2 is 5x c1 and c3 here) so the DC and the vertical term, which carry the reflection,
        are untouched either way.
        """
        if self._sky is not None:
            return self._sky
        img = None
        col = (1.0, 1.0, 1.0)
        strength = None
        try:
            w = bpy.context.scene.world
        except Exception:
            w = None
        nt = getattr(w, "node_tree", None) if (w is not None and getattr(w, "use_nodes", False)) else None
        bg = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBackground"), None) if nt else None
        if bg is not None:
            w_strength = float(bg.inputs["Strength"].default_value)
            ci = bg.inputs["Color"]
            if ci.is_linked:
                src = ci.links[0].from_node
                if src.bl_idname == "ShaderNodeTexEnvironment" and src.image is not None:
                    img = src.image
                    strength = w_strength          # the scene's own sky wins outright
            else:
                col = tuple(float(x) for x in ci.default_value[:3])
                strength = w_strength              # only used if there is no equirect to load
        if img is None:
            # No sky in the world. This is the NORMAL case, not the exotic one: Blender's factory
            # world is a flat 0.05 grey, and example_scene.py builds the real one AFTER the map,
            # so at material-build time there is usually nothing to read. Load the same equirect
            # it will load, from the pack's own shared sky directory. A world that IS a flat
            # colour still loses to the pack's sky here -- reflecting the map's own environment is
            # closer to the game than reflecting Blender's default grey, and the log says so.
            sky_dir = os.path.join(os.path.dirname(self.pack), "shared", "sky")
            cands = []
            if os.path.isdir(sky_dir):
                cands = sorted(f for f in os.listdir(sky_dir) if f.endswith("_equirect.png"))
                cands.sort(key=lambda f: (not f.startswith("NatureCubemap"), f))
            if cands:
                try:
                    img = bpy.data.images.load(os.path.join(sky_dir, cands[0]), check_existing=True)
                    strength = SKY_STRENGTH_FALLBACK
                except Exception as e:
                    self.log.warn("sky equirect %s did not load (%s)" % (cands[0], e))
        env = os.environ.get("EFT_SKY_STRENGTH")
        if env not in (None, ""):
            try:
                strength = float(env)
            except ValueError:
                pass
        if strength is None:
            strength = SKY_STRENGTH_FALLBACK
        # gi_intensity, the shader's sh.vol_min.w scale on sh_env (:1499). Sidecar key, 1.0 when
        # absent, which is every shipped pack today (gpu_driven.rs:1023).
        gi = 1.0
        vj = os.path.join(self.pack, "volume.json")
        if os.path.isfile(vj):
            try:
                with open(vj, "r", encoding="utf-8") as f:
                    gi = float(json.load(f).get("gi_intensity", 1.0) or 1.0)
            except Exception:
                pass

        c = np.zeros((4, 3), np.float64)
        if img is not None:
            try:
                w_px, h_px = int(img.size[0]), int(img.size[1])
                nch = int(img.channels)
                buf = np.empty(w_px * h_px * nch, np.float32)
                img.pixels.foreach_get(buf)
                # Blender's buffer is BOTTOM-UP; make_sky_equirect.py wrote row 0 = straight up.
                a = buf.reshape(h_px, w_px, nch)[::-1, :, :3].astype(np.float64)
                if str(img.colorspace_settings.name) == "sRGB":
                    # For a byte sRGB image `pixels` hands back the STORED bytes, not the linear
                    # decode the shader graph sees (verified: mean luma 0.5103 out of Blender vs
                    # 0.5103 sRGB-encoded / 0.3327 linear off disk). Decode, exact piecewise.
                    a = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
                a *= (strength * gi)
                # make_sky_equirect.py's parameterization, inverted: theta = (u-0.5)*2pi azimuth,
                # phi = (0.5-v)*pi elevation, direction (-sin t cos p, cos t cos p, sin p) with
                # +Z up -- Blender space, the space R arrives in.
                u = (np.arange(w_px) + 0.5) / w_px
                v = (np.arange(h_px) + 0.5) / h_px
                th = (u - 0.5) * (2.0 * np.pi)
                ph = (0.5 - v) * np.pi
                ct = np.cos(ph)[:, None]
                dx = (-np.sin(th))[None, :] * ct
                dy = (np.cos(th))[None, :] * ct
                dz = np.repeat(np.sin(ph)[:, None], w_px, 1)
                dw = ct * ((np.pi / h_px) * (2.0 * np.pi / w_px))   # solid angle per texel
                c[0] = (a * dw[..., None]).sum((0, 1)) * 0.282095   # Y00
                c[1] = (a * (dw * dy)[..., None]).sum((0, 1)) * 0.488603  # Y1-1, blender y
                c[2] = (a * (dw * dz)[..., None]).sum((0, 1)) * 0.488603  # Y10,  blender z (up)
                c[3] = (a * (dw * dx)[..., None]).sum((0, 1)) * 0.488603  # Y11,  blender x
            except Exception as e:
                self.log.warn("glassTRS: sky %s would not project to SH (%s)" % (img.name, e))
                img = None
        if img is None:
            # A uniform environment of radiance L: c0 = L*4pi*0.282095, and the reconstruction
            # collapses to exactly L. So the flat-world fallback is the flat world, no fudge.
            c[0] = np.asarray(col, np.float64) * (strength * gi) * (4.0 * np.pi * 0.282095)
            self.log.warn("glassTRS: no equirect sky to project; the reflection probe is the flat "
                          "world colour %s. The BOUND still holds, only the gradient is lost."
                          % (col,), key=("skyprobe",))
        # Fold the shader's own reconstruction weights in so the graph is 4 constants and a dot.
        E0 = tuple(float(x) for x in c[0] * 0.282095)
        L1 = [tuple(float(x) for x in c[i] * 0.325735) for i in (3, 1, 2)]   # x, y, z order
        lw = (0.2126, 0.7152, 0.0722)
        dot = lambda t: sum(t[i] * lw[i] for i in range(3))
        self.log.info("glassTRS: reflection probe = %s at strength %.3f x gi %.3f -> DC luma "
                      "%.3f, up luma %.3f (volume p50 0.74 / 1.51)"
                      % (img.name if img is not None else "flat world colour", strength, gi,
                         dot(E0), dot(tuple(E0[i] + L1[2][i] for i in range(3)))))
        self._sky = (E0, L1[0], L1[1], L1[2])
        return self._sky

    def _dom_light(self):
        """(direction in BLENDER space, Reinhard-bounded radiance rgb) of the pack's dominant light.

        gpu_draw.wgsl:467-514 builds this per pixel out of the SH volume: the L1 band's
        luminance-weighted direction is the dominant direction L, the SH radiance reconstructed
        toward L is the light's colour, and it is scaled by directionality = |L1|/(sqrt(3)*L0) so
        an isotropic probe stops contributing a phantom highlight. :1647 then bounds it,
        sun_ldr = radiance / (1 + radiance).

        A node graph cannot carry a probe volume, so the SAME reconstruction is collapsed to ONE
        pack-wide constant here: the |L1|-weighted mean direction and the componentwise MEDIAN
        radiance over the probes volume_valid.bin marks valid. Derived from the pack at import
        time, so it tracks a re-bake; nothing is authored.

        THE DOMINANT LIGHT IS NOT THE SUN. volume.json records "direct": false -- the bake is
        sky-visibility plus shadow-tested practicals plus bounce -- so the dominant direction is
        the SKY. Measured on interchange over 507,617 valid probes: L = (-0.002, 1.000, -0.008)
        in pack space, i.e. all but straight up, with 85% of valid probes inside 26 deg of up;
        radiance (0.957, 0.958, 0.960) -> sun_ldr (0.489, 0.489, 0.490); median cosine against
        volume.json's own sun_dir only 0.797. Aiming this lobe at sun_dir instead would light
        facade panes the viewer leaves dark, because a vertical pane's NdotL against an UP
        dominant is ~0 and that is exactly why Atlas's facade glass carries almost no Blinn term.
        """
        if self._dom is not None:
            return self._dom
        sd = [0.449, 0.799, -0.400]
        rad = None
        vj = os.path.join(self.pack, "volume.json")
        if os.path.isfile(vj):
            try:
                with open(vj, "r", encoding="utf-8") as f:
                    sd = [float(x) for x in (json.load(f).get("sun_dir") or sd)]
            except Exception:
                pass
        vb = os.path.join(self.pack, "volume.bin")
        if os.path.isfile(vb):
            try:
                a = np.fromfile(vb, dtype="<f2").astype(np.float32).reshape(-1, 4, 3)
                vv = os.path.join(self.pack, "volume_valid.bin")
                if os.path.isfile(vv):
                    ok = np.fromfile(vv, dtype=np.uint8)[:a.shape[0]] == 255
                else:
                    ok = np.ones(a.shape[0], bool)
                lw = np.array([0.2126, 0.7152, 0.0722], np.float32)
                lm = a @ lw                                     # (N, 4) per-coeff luminance
                # x from Y11 (coeff3), y from Y1-1 (coeff1), z from Y10 (coeff2). :481-484.
                dom = np.stack([lm[:, 3], lm[:, 1], lm[:, 2]], 1)
                mag = np.linalg.norm(dom, axis=1)
                ok = ok & (mag >= 1e-4)
                if ok.any():
                    Ln = dom[ok] / mag[ok][:, None]
                    dirn = np.clip(mag[ok] / (1.73205 * np.maximum(lm[ok, 0], 1e-4)), 0.0, 1.0)
                    c = a[ok]
                    r = 0.282095 * c[:, 0, :] + 0.488603 * (
                        c[:, 1, :] * Ln[:, 1:2] + c[:, 2, :] * Ln[:, 2:3] + c[:, 3, :] * Ln[:, 0:1])
                    r = np.maximum(r, 0.0) * dirn[:, None]
                    wsum = mag[ok].sum()
                    if wsum > 1e-6:
                        m = (Ln * mag[ok][:, None]).sum(0) / wsum
                        n = float(np.linalg.norm(m))
                        if n > 1e-6:
                            sd = [float(x) for x in (m / n)]
                    rad = np.median(r, 0)
            except Exception as e:
                self.log.warn("volume.bin unreadable (%s); the Blinn lobe falls back to "
                              "sun_dir and a mid-domain sun_ldr" % e, key=("domlight",))
        if rad is None:
            # No volume: 0.5 is the Reinhard image of radiance 1.0, the middle of the domain the
            # bound maps onto. Never above 1 -- the contract's ceiling is what matters.
            ldr = (0.5, 0.5, 0.5)
        else:
            ldr = tuple(float(x / (1.0 + x)) for x in rad)
        n = (sd[0] ** 2 + sd[1] ** 2 + sd[2] ** 2) ** 0.5 or 1.0
        sd = [sd[0] / n, sd[1] / n, sd[2] / n]
        # pack Y-up -> Blender Z-up, the same (x, y, z) -> (x, -z, y) the geometry takes.
        self._dom = ((sd[0], -sd[2], sd[1]), ldr)
        self.log.info("glassTRS: dominant light L=(%.3f, %.3f, %.3f) blender, sun_ldr=(%.3f, "
                      "%.3f, %.3f)" % (self._dom[0] + self._dom[1]))
        return self._dom

    def _glass_trs(self, nt, bsdf, rec, tint, base_tex, nmap_node):
        """The legacy Transparent/Reflective/Specular glass family as a Cycles shader tree.

        WHAT THE VIEWER ACTUALLY COMPOSES (gpu_draw.wgsl:1661-1663):

            out.rgb = apply_fog(lit) * trs_a + spec_g + refl_g + em_rgb
            out.a   = trs_a

        which over a background B resolves to `trs_a*lit + spec + refl + em + (1-trs_a)*B`. ONLY
        THE DIFFUSE IS SCALED BY COVERAGE. That is why this cannot live on a Principled BSDF:
        everything on a Principled is scaled by its Alpha input, so at interchange's median
        tint.a = 0.749 the reflection would come back 25-50% short -- the flat DARK SQUARE this
        block exists to prevent. AddShader(MixShader(Transparent, body, a), refl + spec) is that
        composition term for term.

        The Principled is reduced to a pure diffuse body because the shader's own select()s throw
        its specular away: :1651 replaces spec_rgb (which already contains the realtime lights'
        GGX, folded in at :1488) with the Blinn lobe, and :1638 replaces refl_rgb with the TRS
        environment. `lit` (:1460) is albedo.rgb * (irradiance + sun_diffuse) with no roughness
        dependence at all.

        COVERAGE IS TINT.A TWICE. albedo.a = tex.a * tint.a (:1237), and :1602 then computes
        `trs_a = clamp(albedo.a * max(trs_opac, 0.03), 0, 1) * m.tint.a`. That is the authority;
        port it verbatim. The importer used to build coverage from the RAW tex.a, rendering
        interchange glass 1/0.749 = 1.34x too opaque.

        BOTH ADDITIVE LOBES ARE BOUNDED, AND NEITHER IS A BSDF. This is the correction to an
        earlier version of this block, which built them as BSDFs and let Cycles trace the world
        into them. Measured on an interchange facade at head-on: the pane came back 5.4x too light
        in graded display and 5.5x too light RELATIVE TO THE OPAQUE WALL BESIDE IT (in Atlas the
        pane is DARKER than that wall; it was rendering 2x brighter). 86% of that radiance entered
        through the "specular" node and 89% of that was world, not sun -- a flat sky wash arriving
        through a lobe the game only ever points at one light.
        * `sh_ldr = sh_env / (1 + sh_env)` (:1632) is a Reinhard on the environment RADIANCE
          before it enters the lobe, and it is the family's whole contract: texCUBE(_Cube) is an
          IMAGE, bounded [0,1], so `refl_g < _ReflectColor` componentwise, always. A `Diffuse BSDF
          with Color = RC*fresnel` CANNOT be given that bound: a BSDF's input is the radiance the
          integrator traces into it, that radiance only exists after the BSDF has been evaluated,
          and no node can post-process a shader's result -- `x/(1+x)` has nothing to apply to. So
          the environment is not traced here at all: it is FETCHED, one Environment Texture along
          R out of the same sky the world is built from (_sky_probe), and compressed as a value.
          The ceiling is then structural, not a tuned multiplier.
        * `sun_ldr = dom.radiance / (1 + dom.radiance)` (:1647), same class, on the dominant
          light. Reproduced the same way and for the same reason: the Blinn lobe is evaluated
          ANALYTICALLY (Emission driven by pow(NdotH, n) * NdotL), so its one light is the pack's
          own dominant light and its radiance is a number the graph can compress. Both the
          direction and the radiance come out of volume.bin at import time (_dom_light), so
          nothing about the scene's lights or world is assumed -- which is what an earlier note
          here forbade, correctly, for a constant that would have been guessed instead.
        Expressing Blinn-Phong analytically also settles the ENERGY question a Cycles distribution
        could not: pow(NdotH, n) is un-normalized and peaks at <= _SpecColor, integrating to about
        SC*2*pi/(n+2), while a Cycles BSDF is energy-normalized and integrates to ~SC -- an order
        of magnitude hotter on the shininess-1.0 panes. Evaluated as a value there is no
        normalization to fight, and the ** 0.25 perceptual-roughness conversion that a Glossy
        socket needed (Roughness squares to GGX alpha, so handing it alpha squares it twice) is
        retired WITH the BSDF. Anyone reintroducing a Cycles distribution here needs it back.

        WHAT STILL DIVERGES, and is not faked:
        * `(1.0 - shadow_event)` on both the env and the Blinn term (:1638, :1649) -- a cascaded
          shadow-map contact correction applied to specular. An Emission takes no shadow ray, so
          neither additive lobe is occluded now (the BSDFs were, for free). Both are bounded and
          both are near zero on a pane whose normal faces away from the dominant light, so the
          exposure is a shaded pane that still carries its <= RC*fresnel sky term. Do not "fix"
          this by re-attaching a BSDF; that is the bug this block exists to prevent.
        * the reflection probe is now POSITION-INDEPENDENT. The viewer reads the SH volume at the
          pane, which holds a ~40x indoor/outdoor cliff (:360-369) and darkens interior glass; one
          equirect fetch cannot. An interior pane therefore mirrors the outdoor sky -- but only up
          to RC*fresnel, which on interchange's dominant glass is 0.1618 * 0.04 = 0.0065 head-on.
        * realtime point/spot spec is DROPPED for TRS glass in the viewer (:1651 overwrites the
          accumulated spec_rgb). It is dropped here too now, since the lobe is analytic and sees
          only the dominant light. This moved TOWARD the viewer and away from Cycles.
        * apply_fog is applied to the DIFFUSE ONLY here (:1661) -- reflection, specular and
          emissive are deliberately unfogged. This importer builds no fog; if one is ever added,
          a volume would fog all four uniformly. Do not "fix" the asymmetry.
        """
        q8 = lambda v: round(max(0.0, min(1.0, float(v))) * 255.0) / 255.0

        # gpu_driven.rs:2296-2320. Unity's legacy UI defaults where the material authored none:
        # GREY 0.5 reflection/specular (not white), gloss 0.078.
        rc = list(rec.get("reflectColor") or [0.5, 0.5, 0.5, 0.5])
        cube = rec.get("reflectCube")
        if cube:
            # the extracted _Cube mean folded in, so the lane IS the game's reflection radiance
            rc = [float(rc[i]) * float(cube[i]) for i in range(3)]
        RC = [q8(rc[0]), q8(rc[1]), q8(rc[2])]
        sc = list(rec.get("specColor") or [0.5, 0.5, 0.5])
        SC = [q8(sc[0]), q8(sc[1]), q8(sc[2])]
        s = rec.get("shininess")
        # `or 0.0` is WRONG here: an ABSENT key defaults to 0.078 (power 9.997, rough 0.408), not
        # to 0.0 (power 1.28, rough 0.781). 4 of streets_nav's 503 gated materials hit it.
        shin = min(1.0, max(0.01, 0.078 if s is None else float(s)))
        o = rec.get("opacityScale")
        # quantized through glass_refl's top byte exactly as the lane does, so 1.0 lands on
        # 1.003921... and 0.0 (21 materials on streets_nav) lands on 0 and is floored to 0.03
        # by the shader's own max() -- which is what makes those panes near-holes.
        opac = round(min(8.0, max(0.0, 1.0 if o is None else float(o))) / 8.0 * 255.0) * (8.0 / 255.0)
        oeff = max(opac, 0.03)
        # The Blinn EXPONENT, used directly: pow(NdotH, _Shininess*128), gpu_draw.wgsl:1646. There
        # is no roughness conversion any more because there is no Cycles distribution any more --
        # see the docstring for why the retired `** 0.25` is not to be brought back on its own.
        n_blinn = max(shin, 0.01) * 128.0
        ta = float(tint[3])

        # -- coverage: trs_a = min(albedo.a * oeff, 1) * tint.a ------------------------------
        A1 = None
        if base_tex is not None:
            n = _math(nt, "MULTIPLY", -900, 300)
            nt.links.new(base_tex.outputs["Alpha"], n.inputs[0])
            n.inputs[1].default_value = ta                     # albedo.a = tex.a * tint.a
            A1 = n.outputs[0]
        A2 = _math(nt, "MULTIPLY", -760, 300)
        if A1 is not None:
            nt.links.new(A1, A2.inputs[0])
        else:
            A2.inputs[0].default_value = ta                    # untextured: albedo IS m.tint
        A2.inputs[1].default_value = oeff
        A3 = _math(nt, "MINIMUM", -620, 300); A3.inputs[1].default_value = 1.0
        nt.links.new(A2.outputs[0], A3.inputs[0])
        A4 = _math(nt, "MULTIPLY", -480, 300); A4.inputs[1].default_value = ta
        nt.links.new(A3.outputs[0], A4.inputs[0])
        # :1603-1605 discards below 0.03 -- "a genuine hole in the pane, not a dark smoothness
        # spot". A hard discard has no Cycles equivalent, but gating the WHOLE tree against a
        # Transparent BSDF does: an Alpha value alone would leave the additive lobes alive and
        # the empty pane would still mirror the sky as a ghost.
        HOLE = _math(nt, "GREATER_THAN", -480, 180); HOLE.inputs[1].default_value = 0.03
        nt.links.new(A4.outputs[0], HOLE.inputs[0])

        # -- shading basis --------------------------------------------------------------------
        geo = _node(nt, "ShaderNodeNewGeometry", -1300, -600)
        NRM = nmap_node.outputs["Normal"] if nmap_node is not None else geo.outputs["Normal"]

        # Schlick view fresnel on the NORMAL-MAPPED N, exactly 0.04 + 0.96*(1-NdotV)^5
        # (:1422, :1424). It gates ONLY the environment term -- never the Blinn lobe. Principled's
        # own IOR-1.5 fresnel has a different curve, ramps the sun-tinted term too, and was being
        # applied before the normal map even existed.
        F1 = _node(nt, "ShaderNodeVectorMath", -1120, -600)
        F1.operation = "DOT_PRODUCT"
        nt.links.new(geo.outputs["Incoming"], F1.inputs[0])
        nt.links.new(NRM, F1.inputs[1])
        F2 = _math(nt, "MAXIMUM", -960, -600); F2.inputs[1].default_value = 1e-3
        nt.links.new(F1.outputs["Value"], F2.inputs[0])
        F3 = _math(nt, "SUBTRACT", -820, -600); F3.inputs[0].default_value = 1.0
        nt.links.new(F2.outputs[0], F3.inputs[1])
        F4 = _math(nt, "POWER", -680, -600); F4.inputs[1].default_value = 5.0
        nt.links.new(F3.outputs[0], F4.inputs[0])
        F5 = _math(nt, "MULTIPLY_ADD", -540, -600)
        F5.inputs[1].default_value = 0.96
        F5.inputs[2].default_value = 0.04
        nt.links.new(F4.outputs[0], F5.inputs[0])

        # R = reflect(-V, N)
        V1 = _node(nt, "ShaderNodeVectorMath", -1120, -780)
        V1.operation = "SCALE"
        nt.links.new(geo.outputs["Incoming"], V1.inputs[0])
        V1.inputs["Scale"].default_value = -1.0
        RV = _node(nt, "ShaderNodeVectorMath", -960, -780)
        RV.operation = "REFLECT"
        nt.links.new(V1.outputs["Vector"], RV.inputs[0])
        nt.links.new(NRM, RV.inputs[1])

        EC = _node(nt, "ShaderNodeVectorMath", -380, -600)
        EC.operation = "SCALE"
        EC.inputs[0].default_value = (RC[0], RC[1], RC[2])
        nt.links.new(F5.outputs[0], EC.inputs["Scale"])

        if cube:
            # MAT_FLAG_GLASS_CUBE: the viewer skips the probe entirely and uses the material's own
            # extracted _Cube mean as a CONSTANT, so the Cycles twin is an emissive constant too.
            ENV = _node(nt, "ShaderNodeEmission", -200, -600)
            nt.links.new(EC.outputs["Vector"], ENV.inputs["Color"])
            ENV.inputs["Strength"].default_value = 1.0
        else:
            # THE BOUNDED ENVIRONMENT TERM, gpu_draw.wgsl:1627-1638:
            #     refl_g = _ReflectColor * fresnel_v * (E / (1 + E)),  E = max(sh_env, 0.03)
            # E is EVALUATED, not traced: the shader's own band-1 reconstruction (:336-342) along
            # the mirror vector R, off the sky's SH (see _sky_probe). Because it is a value and not
            # a BSDF's incoming radiance, the Reinhard is expressible, and the term then inherits
            # the game's ceiling exactly: E/(1+E) < 1 componentwise => refl < _ReflectColor, for
            # every pixel, always. A Diffuse BSDF cannot be given this bound at any node -- its
            # input is whatever the integrator traces into it, which does not exist until after
            # the BSDF has been evaluated, and nothing downstream of a shader socket is
            # arithmetic. Tracing it is what put unbounded HDR sky behind _ReflectColor.
            # NOT a sharp mirror either: the viewer's env blur is a fixed SH probe, INDEPENDENT of
            # shininess, which is what this reconstruction is, and why the dead roughness lane
            # still has nothing to drive.
            E0, L1x, L1y, L1z = self._sky_probe()
            RS = _node(nt, "ShaderNodeSeparateXYZ", -900, -600)
            nt.links.new(RV.outputs["Vector"], RS.inputs[0])
            terms = []
            for i, (cf, comp) in enumerate(((L1x, "X"), (L1y, "Y"), (L1z, "Z"))):
                t = _node(nt, "ShaderNodeVectorMath", -760, -540 - 90 * i)
                t.operation = "SCALE"                      # 0.325735 * c_k * R.component
                t.inputs[0].default_value = (cf[0], cf[1], cf[2])
                nt.links.new(RS.outputs[comp], t.inputs["Scale"])
                terms.append(t)
            S1 = _node(nt, "ShaderNodeVectorMath", -600, -560); S1.operation = "ADD"
            nt.links.new(terms[0].outputs["Vector"], S1.inputs[0])
            nt.links.new(terms[1].outputs["Vector"], S1.inputs[1])
            S2 = _node(nt, "ShaderNodeVectorMath", -600, -700); S2.operation = "ADD"
            nt.links.new(S1.outputs["Vector"], S2.inputs[0])
            nt.links.new(terms[2].outputs["Vector"], S2.inputs[1])
            S3 = _node(nt, "ShaderNodeVectorMath", -460, -700); S3.operation = "ADD"
            nt.links.new(S2.outputs["Vector"], S3.inputs[0])
            S3.inputs[1].default_value = (E0[0], E0[1], E0[2])      # + 0.282095 * c0
            EF = _node(nt, "ShaderNodeVectorMath", -320, -700); EF.operation = "MAXIMUM"
            EF.inputs[1].default_value = (0.03, 0.03, 0.03)         # ambient_floor (:1359, :1499)
            EF.label = "sh_env"
            nt.links.new(S3.outputs["Vector"], EF.inputs[0])
            E1 = _node(nt, "ShaderNodeVectorMath", -180, -800)      # 1 + E
            E1.operation = "ADD"
            nt.links.new(EF.outputs["Vector"], E1.inputs[0])
            E1.inputs[1].default_value = (1.0, 1.0, 1.0)
            LDR = _node(nt, "ShaderNodeVectorMath", -40, -800)      # sh_ldr = E / (1 + E)
            LDR.operation = "DIVIDE"
            LDR.label = "Reinhard: back into the LDR domain texCUBE lived in"
            nt.links.new(EF.outputs["Vector"], LDR.inputs[0])
            nt.links.new(E1.outputs["Vector"], LDR.inputs[1])
            ENVC = _node(nt, "ShaderNodeVectorMath", -200, -600)    # x _ReflectColor * fresnel
            ENVC.operation = "MULTIPLY"
            nt.links.new(EC.outputs["Vector"], ENVC.inputs[0])
            nt.links.new(LDR.outputs["Vector"], ENVC.inputs[1])
            ENV = _node(nt, "ShaderNodeEmission", -40, -600)
            nt.links.new(ENVC.outputs["Vector"], ENV.inputs["Color"])
            ENV.inputs["Strength"].default_value = 1.0

        # -- Blinn lobe, ANALYTIC: sun_ldr * SC * pow(NdotH, shin*128) * NdotL * albedo.a -----
        # gpu_draw.wgsl:1643-1652, term for term, with H = normalize(V + L). Evaluated as a value
        # and emitted, NOT handed to a glossy BSDF: the game points this lobe at ONE light, a BSDF
        # points it at the whole world, and on a shininess-0.078 pane the implied roughness is 0.64
        # -- a near-hemispherical grey mirror. That is how a "specular highlight" became 86% of the
        # pane's radiance, 89% of it sky. L and sun_ldr come from the pack (see _dom_light).
        L, sun_ldr = self._dom_light()
        H1 = _node(nt, "ShaderNodeVectorMath", -900, -960)          # V + L
        H1.operation = "ADD"
        nt.links.new(geo.outputs["Incoming"], H1.inputs[0])
        H1.inputs[1].default_value = (L[0], L[1], L[2])
        H2 = _node(nt, "ShaderNodeVectorMath", -760, -960)
        H2.operation = "NORMALIZE"
        nt.links.new(H1.outputs["Vector"], H2.inputs[0])
        NH = _node(nt, "ShaderNodeVectorMath", -620, -960)          # NdotH
        NH.operation = "DOT_PRODUCT"
        nt.links.new(NRM, NH.inputs[0])
        nt.links.new(H2.outputs["Vector"], NH.inputs[1])
        NH0 = _math(nt, "MAXIMUM", -480, -960); NH0.inputs[1].default_value = 0.0
        nt.links.new(NH.outputs["Value"], NH0.inputs[0])
        BL = _math(nt, "POWER", -340, -960)                         # pow(NdotH, _Shininess*128)
        BL.inputs[1].default_value = n_blinn
        nt.links.new(NH0.outputs[0], BL.inputs[0])
        NL = _node(nt, "ShaderNodeVectorMath", -620, -1120)         # NdotL
        NL.operation = "DOT_PRODUCT"
        nt.links.new(NRM, NL.inputs[0])
        NL.inputs[1].default_value = (L[0], L[1], L[2])
        NL0 = _math(nt, "MAXIMUM", -480, -1120); NL0.inputs[1].default_value = 0.0
        nt.links.new(NL.outputs["Value"], NL0.inputs[0])
        SPW = _math(nt, "MULTIPLY", -200, -1000)
        nt.links.new(BL.outputs[0], SPW.inputs[0])
        nt.links.new(NL0.outputs[0], SPW.inputs[1])
        SPA = _math(nt, "MULTIPLY", -60, -1000)   # albedo.a doubles as the gloss mask (:1649)
        nt.links.new(SPW.outputs[0], SPA.inputs[0])
        if A1 is not None:
            nt.links.new(A1, SPA.inputs[1])
        else:
            SPA.inputs[1].default_value = ta
        GLS = _node(nt, "ShaderNodeEmission", 80, -1000)
        # Colour carries _SpecColor already multiplied by the Reinhard-bounded dominant radiance,
        # so the peak of the lobe is sun_ldr * SC -- under _SpecColor, which is the same LDR
        # contract the reflection term obeys.
        GLS.inputs["Color"].default_value = (SC[0] * sun_ldr[0], SC[1] * sun_ldr[1],
                                             SC[2] * sun_ldr[2], 1.0)
        nt.links.new(SPA.outputs[0], GLS.inputs["Strength"])

        # -- the Principled reduced to the diffuse body --------------------------------------
        _sock(bsdf, "Alpha", 1.0)                 # coverage lives in the Mix Shader now
        _sock(bsdf, "Metallic", 0.0)
        _sock(bsdf, "Roughness", 1.0)             # `lit` has no roughness dependence
        _sock(bsdf, "IOR", 1.0)
        _sock(bsdf, "Specular IOR Level", 0.0)
        _sock(bsdf, "Specular Tint", (1.0, 1.0, 1.0, 1.0))   # undo the reflectColor mis-binding
        _sock(bsdf, "Coat Weight", 0.0)
        _sock(bsdf, "Sheen Weight", 0.0)

        # -- composition ---------------------------------------------------------------------
        # MixShader inputs have DUPLICATE names ('Factor','Shader','Shader'), so index them:
        # [0] Factor, [1] the shader at Factor 0, [2] the shader at Factor 1.
        TRN = _node(nt, "ShaderNodeBsdfTransparent", 120, 60)
        MIXB = _node(nt, "ShaderNodeMixShader", 280, 120)
        nt.links.new(A4.outputs[0], MIXB.inputs[0])
        nt.links.new(TRN.outputs["BSDF"], MIXB.inputs[1])
        nt.links.new(bsdf.outputs["BSDF"], MIXB.inputs[2])
        ADD1 = _node(nt, "ShaderNodeAddShader", 280, -60)
        nt.links.new(ENV.outputs[0], ADD1.inputs[0])
        nt.links.new(GLS.outputs[0], ADD1.inputs[1])
        ADD2 = _node(nt, "ShaderNodeAddShader", 440, 60)
        nt.links.new(MIXB.outputs["Shader"], ADD2.inputs[0])
        nt.links.new(ADD1.outputs["Shader"], ADD2.inputs[1])

        # em_rgb is added OUTSIDE the alpha scale in the shader, but the Principled's Emission
        # sits inside MIXB and would be coverage-scaled. 0 of 153 interchange / 401 ground_zero /
        # 503 streets_nav TRS materials carry one; build the separate lobe if that ever changes.
        emis = rec.get("emissive") if isinstance(rec.get("emissive"), dict) else None
        em_done = False
        if emis is not None:
            fac = [float(x) for x in (list(emis.get("factor") or [1, 1, 1]) + [1, 1, 1])[:3]]
            eimg = self._image(emis.get("texture"), False) if emis.get("texture") else None
            EM = _node(nt, "ShaderNodeEmission", 280, -220)
            EM.inputs["Strength"].default_value = float(emis.get("hdr", 1.0) or 0.0)
            if eimg is not None:
                et = _node(nt, "ShaderNodeTexImage", -80, -220)
                et.image = eimg; et.extension = "REPEAT"; et.label = "emissive"
                mn, ia, ib, oc = _mix_multiply(nt, 120, -220)
                nt.links.new(et.outputs["Color"], ia)
                ib.default_value = (fac[0], fac[1], fac[2], 1.0)
                nt.links.new(oc, EM.inputs["Color"])
            else:
                EM.inputs["Color"].default_value = (fac[0], fac[1], fac[2], 1.0)
            AD3 = _node(nt, "ShaderNodeAddShader", 600, -60)
            nt.links.new(ADD2.outputs["Shader"], AD3.inputs[0])
            nt.links.new(EM.outputs[0], AD3.inputs[1])
            ADD2 = AD3
            em_done = True

        HMIX = _node(nt, "ShaderNodeMixShader", 760, 60)
        nt.links.new(HOLE.outputs[0], HMIX.inputs[0])
        nt.links.new(TRN.outputs["BSDF"], HMIX.inputs[1])
        nt.links.new(ADD2.outputs["Shader"], HMIX.inputs[2])

        out = None
        for n in nt.nodes:
            if n.bl_idname == "ShaderNodeOutputMaterial":
                out = n
        if out is None:
            out = _node(nt, "ShaderNodeOutputMaterial", 940, 60)
        # relinking an occupied input replaces the old link
        nt.links.new(HMIX.outputs["Shader"], out.inputs["Surface"])
        self.n_glass_trs += 1
        return em_done

    def _glass_physical(self, nt, bsdf, rec, tint, base_tex):
        """Real transmission in place of the legacy glassTRS response. PHOTOREAL ONLY.

        WHY THIS IS ALLOWED TO EXIST. The panes are already physical slabs. Measured on the built
        scene: 0 of 138 glass objects are zero-thickness, the thinnest bbox axis runs 1.59 mm at
        the minimum and 2.26 mm at p10, and the test pane is 4 triangles - 2 front, 2 back - at
        0.004 x 2.629 x 2.577 m. So a transmissive BSDF on the shipped geometry refracts through a
        real parallel plate, and nothing has to be invented to give it thickness. (Do NOT add a
        Solidify: a 6 mm shell inside a slab that already has two surfaces turns every window into
        a fisheye lens. Measured, rendered, discarded.)

        WHAT IT COSTS IN PARITY, stated plainly, because it is a clean break. `_glass_trs` above
        reproduces four things the viewer does and this does not: the Reinhard-bounded environment
        term (`refl < _ReflectColor` at every pixel, by construction), the analytic Blinn lobe
        aimed at the pack's one dominant light, the additive composition where only the diffuse is
        scaled by coverage, and the discard-below-0.03 hole. Expect the regression that block's
        docstring names in reverse: in the game the pane reads DARKER than the wall beside it, and
        a transmissive pane reflecting a traced sky reads brighter at grazing angles.

        WHAT IS DERIVED AND WHAT IS NOT:
          roughness  DERIVED. rec["roughness"], already on the BSDF. It is 0.05 on every one of
                     the 1248 glass records across the six packs. Do NOT reconstruct it from
                     `shininess` instead: shininess*128 is a Blinn EXPONENT, and reading it as a
                     GGX alpha gives 0.408 on the dominant 0.0781 class, which renders the facade
                     frosted.
          tint       DERIVED. Hue only - the level is carried by the transmittance below.
          T0         DERIVED, and this is the good part. The game's own coverage line is
                         trs_a = min(albedo.a * tint.a * max(opacityScale, 0.03), 1) * tint.a
                     so 1 - trs_a is exactly what the game let through. Across interchange's 153
                     glass records that has a median of 0.460 and 145 of 153 land inside the real
                     architectural-glass range 0.20-0.90. The alpha the game shipped IS a usable
                     physical transmittance.
          IOR 1.52   INVENTED. Standard soda-lime glass; the pack ships no IOR anywhere.
          0.92       the normal-incidence Fresnel throughput of the pane's two shipped interfaces
                     at that IOR, so Base Color only has to supply the ABSORPTION. The sqrt is
                     because Principled applies the tint once per interface.

        The albedo RGB is deliberately NOT multiplied in. The dominant glass texture (86 of the
        153) has a linear RGB mean of 0.0065: it is a near-black body/dirt layer whose ALPHA is
        the payload. Feeding it to a transmissive Base Color turns every pane into an absorber.
        """
        ta = float(tint[3])
        o = rec.get("opacityScale")
        # the same top-byte quantization the glass_refl lane applies, so the two modes agree on
        # coverage to the bit before one of them turns it into transmittance
        opac = round(min(8.0, max(0.0, 1.0 if o is None else float(o))) / 8.0 * 255.0) * (8.0 / 255.0)
        oeff = max(opac, 0.03)
        amean = 1.0
        if base_tex is not None and base_tex.image is not None:
            am = _alpha_mean(base_tex.image)
            if am is not None:
                amean = am
        trs_a = min(amean * ta * oeff, 1.0) * ta
        t0 = max(0.0, min(1.0, 1.0 - trs_a))

        mx = max(tint[0], tint[1], tint[2], 1e-4)
        k = (max(t0, 1e-4) / 0.92) ** 0.5
        col = tuple(min(1.0, k * (c / mx)) for c in tint[:3])

        # UNLINK FIRST. The albedo block above wired the texture straight into Base Color, and a
        # linked socket ignores its default_value entirely, so setting the colour without cutting
        # the link leaves the near-black dirt layer driving a transmissive body: exactly the
        # absorber the docstring warns about, and silent, because the value looks right in the UI.
        for name in ("Base Color", "Alpha"):
            for lk in list(bsdf.inputs[name].links):
                nt.links.remove(lk)
        _sock(bsdf, "Base Color", (col[0], col[1], col[2], 1.0))
        _sock(bsdf, "Transmission Weight", 1.0)
        _sock(bsdf, "IOR", 1.52)               # INVENTED: soda-lime; the pack ships no IOR
        _sock(bsdf, "Metallic", 0.0)
        _sock(bsdf, "Alpha", 1.0)              # coverage now lives in the transmission, not here
        self.n_glass_phys += 1
        return False                            # emissive is untouched; the caller still runs it

    def _cavity(self, nt, bsdf, rec, nrm_tex):
        """Multiply the normal map's own self-occlusion into Base Color. PHOTOREAL ONLY.

        THE GAP THIS FILLS. The game shader has no ambient-occlusion term of any kind and the pack
        ships no AO map, so the normal map perturbs shading but casts no occlusion at any scale.
        In Cycles that absence is real rather than double-counted: Cycles computes occlusion from
        geometry, and the normal-map relief has no geometry, so nothing shadows anything inside a
        brick course or a bolt recess.

        THE MAP IS DERIVED, NOT AUTHORED. bake_cavity.py Poisson-integrates the shipped tangent
        space normal to a height field - the normal map IS a slope field, p = -nx/nz, q = -ny/nz -
        and takes a multi-scale local-relief occlusion off it. The only assumption is "normal-map
        slopes are per texel", which is the same assumption the Normal Map node beside this one
        already makes. No amplitude knob and no curve.

        Sampled through the NORMAL texture's own Vector socket when it has one, so the cavity gets
        the identical uvXform and detail transform without a second Mapping node.

        Do NOT also drive Roughness from this ("dirt collects in crevices"). That mapping is
        invented and there is no measurement behind it.
        """
        img = self._cavity_image(rec.get("normal"))
        if img is None:
            return
        base_in = bsdf.inputs["Base Color"]
        if not base_in.links:
            return
        src = base_in.links[0].from_socket
        ct = _node(nt, "ShaderNodeTexImage", -1000, -1180)
        ct.image = img
        ct.label = "cavity (baked from the normal map)"
        ct.interpolation = "Smart"
        ct.extension = "REPEAT"
        if nrm_tex is not None and nrm_tex.inputs["Vector"].links:
            nt.links.new(nrm_tex.inputs["Vector"].links[0].from_socket, ct.inputs["Vector"])
        mul = _node(nt, "ShaderNodeMix", -700, -1100)
        mul.data_type = 'RGBA'; mul.blend_type = 'MULTIPLY'
        mul.inputs["Factor"].default_value = 1.0
        nt.links.new(src, mul.inputs[6])
        nt.links.new(ct.outputs["Color"], mul.inputs[7])
        nt.links.new(mul.outputs[2], base_in)
        self.n_cavity += 1

    def _cavity_image(self, nrm_path):
        """Look a baked cavity map up by the normal map's basename. Missing is not an error."""
        if not self.cavity_dir or not nrm_path:
            return None
        if self._cav_meta is None:
            self._cav_meta = {}
            mp = os.path.join(self.cavity_dir, "cavity.json")
            if os.path.isfile(mp):
                try:
                    self._cav_meta = json.load(open(mp, encoding="utf-8"))
                except Exception:
                    self._cav_meta = {}
            gf = self._cav_meta.get("greenFlip")
            if gf is not None and bool(gf) != bool(self.conv_green_flip):
                # A green flip mirrors the integrated height in v, so a cavity baked under the
                # other convention lands its crevices on the wrong side of every ridge.
                self.log.warn("cavity maps were baked with greenFlip=%s but this pack converts "
                              "with %s; re-run bake_cavity.py or the occlusion is v-mirrored"
                              % (gf, self.conv_green_flip), key=("cavgf",))
        base = os.path.splitext(os.path.basename(str(nrm_path).replace("\\", "/")))[0]
        if base in self._cav_cache:
            return self._cav_cache[base]
        p = os.path.join(self.cavity_dir, base + ".cav.png")
        img = None
        if os.path.isfile(p):
            try:
                img = bpy.data.images.load(p, check_existing=True)
                img.colorspace_settings.name = "Non-Color"   # it is an occlusion factor, not colour
            except Exception as e:
                self.log.warn("cavity %s unreadable (%s)" % (p, e), key=("cavload",))
                img = None
        self._cav_cache[base] = img
        return img

    def _deep_water_normal(self, nt, bsdf, rec):
        """The deep-water ripple, gpu_draw.wgsl:1683-1742, as a world-space Normal.

        The viewer shades deep water against WORLD UP, not the interpolated mesh normal: the sea
        quads' per-vertex normals crosshatch at the grid period and produced reported "shadow
        streaks" (:1737-1742). Feeding the Principled a world-space vector built on a constant
        Z = 1 reproduces that for free -- the pack's custom split normals are replaced outright.

        AXIS MAPPING. YUP_TO_ZUP sends pack (x, y, z) -> Blender (x, -z, y), so pack_x = B.x,
        pack_z = -B.y and pack up (+Y) is Blender +Z. Hence N_blender = normalize(dx, -dy, 1).

        THREE TERMS OF THE SHADER ARE DELIBERATELY NOT HERE.
        * ripple_amp's distance falloff, `(0.06/(1+d*0.004)) * (1-smoothstep(500,1400,d))`
          (:1680). A path-traced material is evaluated for shadow, reflection and bounce rays,
          none of which has a meaningful "camera distance"; wiring ShaderNodeCameraData.Distance
          would make the sea's reflection in a window carry the CAMERA's distance. The falloff
          only exists to hide sine aliasing at km range, which supersampling solves properly, so
          the constant 0.06 (its value at d = 0) stands everywhere.
        * the Nyquist octave gates w1/w2 (:1683-1690), derived from wxz_footprint, the
          screen-space derivative of world position. Cycles has no per-pixel ddx/ddy in the node
          graph. w1 = w2 = 1.0 is not a compromise: it is the correct value at close range, and
          the pixel filter does the band-limiting the gates were faking.
        """
        # wt = sun.gfx.w, app time in SECONDS. Animation is NOT one of the impossible parts:
        # the drift rides a wall clock and Blender has a frame clock, so frame/fps reproduces the
        # scroll at the authored speeds exactly. A single still just needs a frozen phase.
        tnode = _node(nt, "ShaderNodeValue", -2000, -1200)
        tnode.label = "water time (s)"
        tnode.outputs[0].default_value = 0.0
        try:
            d = tnode.outputs[0].driver_add("default_value").driver
            d.type = 'SCRIPTED'
            v = d.variables.new()
            v.name = "fps"
            v.type = 'SINGLE_PROP'
            v.targets[0].id_type = 'SCENE'
            v.targets[0].id = bpy.context.scene
            v.targets[0].data_path = "render.fps"
            d.expression = "frame / fps"
        except Exception as e:
            self.log.warn("water: time driver rejected (%s); waves are frozen at t=0" % e,
                          key="watertime")
        t = tnode.outputs[0]

        def _mulc(sock, k, x, y):
            n = _math(nt, "MULTIPLY", x, y)
            n.inputs[1].default_value = k
            nt.links.new(sock, n.inputs[0])
            return n.outputs[0]

        def _add(a, b, x, y):
            n = _math(nt, "ADD", x, y)
            nt.links.new(a, n.inputs[0]); nt.links.new(b, n.inputs[1])
            return n.outputs[0]

        geo = _node(nt, "ShaderNodeNewGeometry", -2000, -900)
        sep = _node(nt, "ShaderNodeSeparateXYZ", -1840, -900)
        nt.links.new(geo.outputs["Position"], sep.inputs["Vector"])
        px = sep.outputs["X"]
        pz = _mulc(sep.outputs["Y"], -1.0, -1700, -960)          # pack_z = -B.y

        nrm_path = rec.get("normal")
        nimg = self._image(nrm_path, True) if nrm_path else None

        if nimg is None:
            # Water4 procedural chop, in the RADIAN form. The shader's rsin(x) = sin(fract(x)*2pi)
            # with phases in CYCLES is an f32 fast-math workaround for a GPU; Cycles has no such
            # problem, so the drift constants are multiplied back by 2pi once here.
            sx = _mulc(px, 0.35, -1560, -840)
            sy = _mulc(pz, 0.35, -1560, -960)
            q1x = _add(sx, _mulc(t, 0.1256637, -1420, -1100), -1280, -840)   # 2pi * 0.0200
            q1y = _add(sy, _mulc(t, 0.0791681, -1420, -1220), -1280, -960)   # 2pi * 0.0126
            q2x = _add(sx, _mulc(t, -0.1005310, -1420, -1340), -1280, -1080)  # 2pi * -0.0160
            q2y = _add(sy, _mulc(t, 0.1445133, -1420, -1460), -1280, -1200)  # 2pi * 0.0230

            def _sin(sock, x, y):
                n = _math(nt, "SINE", x, y)
                nt.links.new(sock, n.inputs[0])
                return n.outputs[0]

            def _sub(a, b, x, y):
                n = _math(nt, "SUBTRACT", x, y)
                nt.links.new(a, n.inputs[0]); nt.links.new(b, n.inputs[1])
                return n.outputs[0]

            b1x = _sin(_add(q1x, _mulc(q1y, 0.6, -1140, -820), -1000, -840), -860, -840)
            b2x = _sin(_add(_mulc(q2x, 3.1, -1140, -1060), _mulc(q2y, 2.3, -1140, -1180),
                            -1000, -1080), -860, -1080)
            b1y = _sin(_sub(_mulc(q1x, 0.7, -1140, -1300), q1y, -1000, -1320), -860, -1320)
            b2y = _sin(_sub(_mulc(q2y, 3.7, -1140, -1540), _mulc(q2x, 2.9, -1140, -1660),
                            -1000, -1560), -860, -1560)
            dx = _mulc(_add(b1x, _mulc(b2x, 0.5, -720, -1080), -580, -900), 0.06, -440, -900)
            dy = _mulc(_add(b1y, _mulc(b2y, 0.5, -720, -1560), -580, -1380), 0.06, -440, -1380)
        else:
            # The game's WaterBasicNormals: two world-XZ scrolling layers that REPLACE the
            # procedural chop (:1717-1735). The shader reads `.xy * 2 - 1` RAW -- it ignores both
            # normalGreenFlip and normalScale, unlike the generic normal block below, so neither
            # is applied here. Mesh UVs are ignored too; the frame is world metres.
            wuv = _node(nt, "ShaderNodeCombineXYZ", -1560, -900)
            nt.links.new(px, wuv.inputs["X"]); nt.links.new(pz, wuv.inputs["Y"])
            outs = []
            for k, (sc, ox, oy, yy) in enumerate(((0.15, 0.050, 0.0315, -900),
                                                  (0.15 * 1.73, -0.040, 0.0575, -1400))):
                loc = _node(nt, "ShaderNodeCombineXYZ", -1400, yy - 200)
                nt.links.new(_mulc(t, ox, -1560, yy - 200), loc.inputs["X"])
                nt.links.new(_mulc(t, oy, -1560, yy - 320), loc.inputs["Y"])
                mp = _node(nt, "ShaderNodeMapping", -1240, yy)
                mp.inputs["Scale"].default_value = (sc, sc, 1.0)
                nt.links.new(wuv.outputs["Vector"], mp.inputs["Vector"])
                nt.links.new(loc.outputs["Vector"], mp.inputs["Location"])
                tx = _node(nt, "ShaderNodeTexImage", -1060, yy)
                tx.image = nimg; tx.extension = "REPEAT"; tx.interpolation = "Smart"
                tx.label = "water waves %d" % k
                nt.links.new(mp.outputs["Vector"], tx.inputs["Vector"])
                sc2 = _node(nt, "ShaderNodeSeparateColor", -880, yy)
                sc2.mode = "RGB"
                nt.links.new(tx.outputs["Color"], sc2.inputs["Color"])
                dec = []
                for ci, cn in enumerate(("Red", "Green")):
                    n = _math(nt, "MULTIPLY_ADD", -720, yy - ci * 120)
                    n.inputs[1].default_value = 2.0
                    n.inputs[2].default_value = -1.0
                    nt.links.new(sc2.outputs[cn], n.inputs[0])
                    dec.append(n.outputs[0])
                outs.append(dec)
            k = 0.5 * 0.85                       # (a1+a2)*0.5*0.85, ripple_amp/0.06 = 1, w1 = 1
            dx = _mulc(_add(outs[0][0], outs[1][0], -560, -900), k, -420, -900)
            dy = _mulc(_add(outs[0][1], outs[1][1], -560, -1400), k, -420, -1400)

        ndy = _mulc(dy, -1.0, -280, -1380)
        comb = _node(nt, "ShaderNodeCombineXYZ", -140, -1100)
        nt.links.new(dx, comb.inputs["X"])
        nt.links.new(ndy, comb.inputs["Y"])
        comb.inputs["Z"].default_value = 1.0
        nz = _node(nt, "ShaderNodeVectorMath", 20, -1100)
        nz.operation = 'NORMALIZE'
        nt.links.new(comb.outputs["Vector"], nz.inputs[0])
        nt.links.new(nz.outputs["Vector"], bsdf.inputs["Normal"])

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
        vp_rec = rec.get("vp") if isinstance(rec.get("vp"), dict) else None
        vp_layers = (vp_rec.get("layers") or []) if vp_rec else []
        # the PACK's layer schema is {albedo, normal, uv, tint} (assemble_bevy._vp record),
        # not the dataset's {tex, nrm, uv, col}
        vp_ok = (len(vp_layers) == 3
                 and all(isinstance(l, dict) and l.get("albedo") for l in vp_layers))
        if not albedo and vp_layers and isinstance(vp_layers[0], dict):
            # layer 0 is the base; it is also the fallback when the full splat cannot be built
            albedo = vp_layers[0].get("albedo")
        img = self._image(albedo, False) if albedo else None
        alpha_out = None
        base_tex = None          # the albedo Image Texture node; parallax rewires its Vector
        # vert-paint sockets hoisted out of the nested block: the near-black resolve, the
        # zero-coverage select and the matte roughness override all need them downstream.
        vp_tinted0 = None        # socket: a0.rgb * tint0.rgb  (the shader's fallback splat)
        vp_gate = None           # socket: 0/1, "hs > 1e-5" (gpu_draw.wgsl:1291)
        vp_smooth = None         # socket: w.x*a0.a + w.y*a1.a + w.z*a2.a (gpu_draw.wgsl:1309)
        vp_w = []                # the three NORMALISED weights
        if img is not None:
            tex = _node(nt, "ShaderNodeTexImage", -820, 320)
            tex.image = img
            tex.label = "albedo"
            base_tex = tex
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

            # ---- VERT-PAINT 3-LAYER SPLAT -------------------------------------------------
            # Roads, parking, yards and painted metal are a three-layer blend, not one tiled
            # texture. Weights are the HEIGHTS mask times the mesh's COLOR_0.rgb, raised to the
            # material's blend exponent and normalised; layer 0 is the base and is also the
            # fallback when the mask is empty (an unpainted mesh or the "Solid" variant), which
            # is what stops a near-zero mask washing out to an even three-way mix.
            #
            # Each layer has its own ST, and un-baking it from the BASE uv is V-FLIP AWARE: the
            # assembler baked v' = 1 - (v*sy + oy), so the naive (uv - zw)/xy is wrong and shifts
            # a layer by up to half a tile. This mirrors the renderer's detail_xform exactly.
            if vp_ok:
                _xf = _detail_xform          # one definition, shared

                base_st = [float(x) for x in (rec.get("uvXform") or [1, 1, 0, 0])]
                uvn = _node(nt, "ShaderNodeUVMap", -1500, 700)
                heights = self._image(vp_rec.get("heights"), True) if vp_rec.get("heights") else None
                blend_p = max(float(vp_rec.get("blend", 1.0) or 1.0), 1.0)

                def _sample(path, st, y):
                    """A layer image sampled in its OWN frame, relative to the baked base UV."""
                    im = self._image(path, False)
                    if im is None:
                        return None
                    r = _xf([float(v) for v in st], base_st)
                    m = _node(nt, "ShaderNodeMapping", -1180, y)
                    m.inputs["Scale"].default_value = (r[0], r[1], 1.0)
                    m.inputs["Location"].default_value = (r[2], r[3], 0.0)
                    nt.links.new(uvn.outputs["UV"], m.inputs["Vector"])
                    t = _node(nt, "ShaderNodeTexImage", -980, y)
                    t.image = im; t.extension = "REPEAT"; t.interpolation = "Smart"
                    nt.links.new(m.outputs["Vector"], t.inputs["Vector"])
                    return t

                lay = [_sample(l.get("albedo"), l.get("uv") or [1, 1, 0, 0], 700 - i * 280)
                       for i, l in enumerate(vp_layers)]
                if heights is not None and all(l is not None for l in lay):
                    hr = _xf([1.0, 1.0, 0.0, 0.0], base_st)
                    hm = _node(nt, "ShaderNodeMapping", -1180, -140)
                    hm.inputs["Scale"].default_value = (hr[0], hr[1], 1.0)
                    hm.inputs["Location"].default_value = (hr[2], hr[3], 0.0)
                    nt.links.new(uvn.outputs["UV"], hm.inputs["Vector"])
                    ht = _node(nt, "ShaderNodeTexImage", -980, -140)
                    ht.image = heights; ht.extension = "REPEAT"
                    nt.links.new(hm.outputs["Vector"], ht.inputs["Vector"])

                    hsep = _node(nt, "ShaderNodeSeparateColor", -800, -140)
                    nt.links.new(ht.outputs["Color"], hsep.inputs["Color"])
                    vcs = _node(nt, "ShaderNodeVertexColor", -800, -320)
                    vcs.layer_name = "Col"
                    vsep = _node(nt, "ShaderNodeSeparateColor", -640, -320)
                    nt.links.new(vcs.outputs["Color"], vsep.inputs["Color"])

                    # COLOR_0 COLOUR SPACE. The shader consumes `o.color.rgb` RAW: a vertex
                    # attribute is never sRGB-decoded on the GPU, so it sees byte/255. Blender
                    # treats a BYTE_COLOR attribute as sRGB-encoded, so the Color Attribute node
                    # hands the graph srgb_to_linear(byte/255) instead -- measured on 5.1, byte
                    # 128 comes back as 0.2159 where the viewer sees 0.5020. Re-encode here so
                    # the weights (and vp_smooth below) are computed on the same numbers the
                    # renderer uses. Doing it in nodes rather than switching the attribute to
                    # FLOAT_COLOR keeps storage at 4 B/corner, which is why _wants_color exists.
                    chan = ("Red", "Green", "Blue")
                    vsrgb = [_srgb_encode(nt, vsep.outputs[chan[i]], -500, -300 - i * 200)
                             for i in range(3)]

                    wsock, tot, raw = [], None, []
                    for i in range(3):
                        mul = _math(nt, "MULTIPLY", -460, -140 - i * 120)
                        nt.links.new(hsep.outputs[chan[i]], mul.inputs[0])
                        nt.links.new(vsrgb[i], mul.inputs[1])
                        raw.append(mul.outputs[0])            # hw.i, PRE-max, PRE-pow
                        mx = _math(nt, "MAXIMUM", -320, -140 - i * 120)
                        nt.links.new(mul.outputs[0], mx.inputs[0])
                        mx.inputs[1].default_value = 1e-4
                        pw = _math(nt, "POWER", -180, -140 - i * 120)
                        nt.links.new(mx.outputs[0], pw.inputs[0])
                        pw.inputs[1].default_value = blend_p
                        wsock.append(pw.outputs[0])
                        if tot is None:
                            tot = pw.outputs[0]
                        else:
                            ad = _math(nt, "ADD", -40, -140 - i * 120)
                            nt.links.new(tot, ad.inputs[0]); nt.links.new(pw.outputs[0], ad.inputs[1])
                            tot = ad.outputs[0]
                    safe = _math(nt, "MAXIMUM", 100, -420); safe.inputs[1].default_value = 1e-4
                    nt.links.new(tot, safe.inputs[0])

                    # THE GATE IS ON hs, NOT ON THE NORMALISER. gpu_draw.wgsl:1287 sums hw
                    # BEFORE max(.,1e-4) and BEFORE pow; :1291 tests THAT sum against 1e-5.
                    # Gating on the post-pow total works only by accident: for blend > 1 the
                    # floored term collapses (1e-4^1.8 = 2.5e-8) so the test still fires, but
                    # at blend clamped to 1.0 the floor survives as 3e-4 > 1e-5 and an
                    # unpainted face washes to the even three-way blend the fallback exists to
                    # prevent (interchange id 319, blend 0.8 -> 1.0, is exactly that case).
                    hs1 = _math(nt, "ADD", 100, -560)
                    nt.links.new(raw[0], hs1.inputs[0]); nt.links.new(raw[1], hs1.inputs[1])
                    hs2 = _math(nt, "ADD", 240, -560)
                    nt.links.new(hs1.outputs[0], hs2.inputs[0]); nt.links.new(raw[2], hs2.inputs[1])

                    acc = None
                    for i in range(3):
                        tintl = [float(x) for x in (vp_layers[i].get("tint") or [1, 1, 1])]
                        tn = _node(nt, "ShaderNodeMix", 260, 700 - i * 200)
                        tn.data_type = 'RGBA'; tn.blend_type = 'MULTIPLY'
                        tn.inputs["Factor"].default_value = 1.0
                        nt.links.new(lay[i].outputs["Color"], tn.inputs[6])
                        tn.inputs[7].default_value = (tintl[0], tintl[1], tintl[2], 1.0)
                        if i == 0:
                            vp_tinted0 = tn.outputs[2]        # a0.rgb * tint0.rgb
                        nrm_w = _math(nt, "DIVIDE", 260, 620 - i * 200)
                        nt.links.new(wsock[i], nrm_w.inputs[0])
                        nt.links.new(safe.outputs[0], nrm_w.inputs[1])
                        vp_w.append(nrm_w.outputs[0])
                        sc = _node(nt, "ShaderNodeVectorMath", 420, 700 - i * 200)
                        sc.operation = 'SCALE'
                        nt.links.new(tn.outputs[2], sc.inputs[0])
                        nt.links.new(nrm_w.outputs[0], sc.inputs["Scale"])
                        if acc is None:
                            acc = sc.outputs["Vector"]
                        else:
                            ad = _node(nt, "ShaderNodeVectorMath", 580, 700 - i * 200)
                            ad.operation = 'ADD'
                            nt.links.new(acc, ad.inputs[0]); nt.links.new(sc.outputs["Vector"], ad.inputs[1])
                            acc = ad.outputs["Vector"]

                    # empty mask -> base layer, matching the renderer rather than washing out.
                    # The shader's zero-coverage path is w = (1,0,0), i.e. spl = a0.rgb *
                    # tint0.rgb (gpu_draw.wgsl:1290 feeding :1299) -- the LAYER TINT is included,
                    # so falling back to the untinted texture was a second divergence.
                    gate = _math(nt, "GREATER_THAN", 700, -420)
                    nt.links.new(hs2.outputs[0], gate.inputs[0])
                    gate.inputs[1].default_value = 1e-5
                    vp_gate = gate.outputs[0]
                    pick = _node(nt, "ShaderNodeMix", 840, 400)
                    pick.data_type = 'RGBA'
                    nt.links.new(gate.outputs[0], pick.inputs["Factor"])
                    nt.links.new(vp_tinted0, pick.inputs[6])
                    nt.links.new(acc, pick.inputs[7])

                    # ---- NEAR-BLACK RESOLVE (gpu_draw.wgsl:1304-1305) ------------------------
                    # A dark layer tint over a dark mask can resolve the splat to near-black, and
                    # the renderer catches that with a hard per-fragment test and falls back to
                    # the tinted base layer at full strength. 95 of interchange's 134 flagged
                    # materials carry a non-white layer-0 tint, which is what pushes a blend under
                    # the threshold in the first place.
                    #
                    # The luma weights are Rec.601 and they are NOT negotiable: ShaderNodeRGBToBW
                    # uses the working space's Rec.709 coefficients (0.2126/0.7152/0.0722), so a
                    # green-dominant blend would cross 0.02 at a different colour than the shader.
                    # DOT_PRODUCT against the literal constants is the only faithful form.
                    # Cycles supersamples through the discontinuity where the raster viewer
                    # resolves it once per pixel, so the boundary dithers over about a pixel --
                    # below 0.02 luma nothing is visible anyway. Do NOT soften it to a smoothstep:
                    # that would change what the threshold means.
                    nbl = _node(nt, "ShaderNodeVectorMath", 1000, 240)
                    nbl.operation = 'DOT_PRODUCT'
                    nt.links.new(pick.outputs[2], nbl.inputs[0])
                    nbl.inputs[1].default_value = (0.299, 0.587, 0.114)
                    nbt = _math(nt, "LESS_THAN", 1160, 240)
                    nt.links.new(nbl.outputs["Value"], nbt.inputs[0])
                    nbt.inputs[1].default_value = 0.02
                    nbm = _node(nt, "ShaderNodeMix", 1320, 400)
                    nbm.data_type = 'RGBA'
                    nt.links.new(nbt.outputs[0], nbm.inputs["Factor"])
                    nt.links.new(pick.outputs[2], nbm.inputs[6])
                    nt.links.new(vp_tinted0, nbm.inputs[7])
                    vp_col = nbm.outputs[2]

                    # gpu_draw.wgsl:1306 ends with `albedo = vec4(spl, a) * m.tint`. Relinking
                    # Base Color straight from the splat bypassed the material tint built above.
                    # No-op on interchange (0 of the 134 carry one) but wrong in general.
                    if any(abs(tint[k] - 1.0) > 1e-4 for k in range(3)):
                        _mn, _ia, _ib, _oc = _mix_multiply(nt, 1480, 400)
                        nt.links.new(vp_col, _ia)
                        _ib.default_value = (tint[0], tint[1], tint[2], 1.0)
                        vp_col = _oc
                    nt.links.new(vp_col, bsdf.inputs["Base Color"])

                    # ---- vp_smooth (gpu_draw.wgsl:1309) --------------------------------------
                    # The weighted sum of the RAW layer texture alphas. The layer tints are
                    # rgb-only -- tint.w is the blend EXPONENT (gpu_driven.rs:2097-2105 packs it
                    # into tint0.w and zero into tint1/2.w), not an alpha -- so nothing multiplies
                    # in here. Drives the matte roughness override below.
                    sm = []
                    for i in range(3):
                        n_ = _math(nt, "MULTIPLY", 1000, -640 - i * 120)
                        nt.links.new(vp_w[i], n_.inputs[0])
                        nt.links.new(lay[i].outputs["Alpha"], n_.inputs[1])
                        sm.append(n_.outputs[0])
                    s1 = _math(nt, "ADD", 1160, -700)
                    nt.links.new(sm[0], s1.inputs[0]); nt.links.new(sm[1], s1.inputs[1])
                    s2 = _math(nt, "ADD", 1300, -700)
                    nt.links.new(s1.outputs[0], s2.inputs[0]); nt.links.new(sm[2], s2.inputs[1])
                    # zero coverage is w = (1,0,0) exactly, so vp_smooth is a0.a there
                    ssel = _node(nt, "ShaderNodeMix", 1440, -700)
                    ssel.data_type = 'FLOAT'
                    nt.links.new(vp_gate, ssel.inputs[0])
                    nt.links.new(lay[0].outputs["Alpha"], ssel.inputs[2])
                    nt.links.new(s2.outputs[0], ssel.inputs[3])
                    vp_smooth = ssel.outputs[0]
                    self.n_vp_graph += 1

        # ---- alpha -------------------------------------------------------
        # The alpha test runs on the COMPUTED albedo alpha (tex.a * tint.a), so
        # an untextured cutout with tint.a below the cutoff still discards.
        # ---- water ---------------------------------------------------------
        # WHERE THE PUDDLE MASK LIVES IS PER-TEXTURE, not a fixed channel. The renderer probes the
        # albedo's alpha and only falls back to luma when that alpha is (near) CONSTANT, which is
        # the City_puddle_atlas case that ships alpha identically 1.0:
        #     mask = tex.a,  or luma(tex.rgb) when (alpha_hi - alpha_lo) < 13/255
        # Hardcoding either channel is wrong. Interchange's four water textures all have a REAL
        # varying alpha (ranges 0..174, 0..255, 0..251, 0..240), so reading red there drew every
        # puddle at the wrong shape and a fraction of its intended coverage.
        #
        # Coverage is then shaped the way the game's own fragment does:
        #     coverage = clamp(mask * 1.52, 0, 1) * smoothstep(0.015, 0.10, that) * tint.a
        # The 1.52 is the authored _FadeStrength; the smoothstep suppresses the near-zero tail so
        # the decal quad's own boundary does not become visible.
        if role == "water":
            if img is not None:
                mask_out = None
                if _alpha_is_constant(img):
                    lum = _node(nt, "ShaderNodeRGBToBW", -520, 120)
                    nt.links.new(tex.outputs["Color"], lum.inputs["Color"])
                    mask_out = lum.outputs["Val"]
                else:
                    mask_out = tex.outputs["Alpha"]
                gain = _math(nt, "MULTIPLY", -360, 120); gain.inputs[1].default_value = 1.52
                nt.links.new(mask_out, gain.inputs[0])
                cl = _math(nt, "MINIMUM", -220, 120); cl.inputs[1].default_value = 1.0
                nt.links.new(gain.outputs[0], cl.inputs[0])
                ss = _node(nt, "ShaderNodeMapRange", -80, 120)
                ss.interpolation_type = 'SMOOTHSTEP'
                ss.inputs["From Min"].default_value = 0.015
                ss.inputs["From Max"].default_value = 0.10
                nt.links.new(cl.outputs[0], ss.inputs["Value"])
                tail = _math(nt, "MULTIPLY", 60, 120)
                nt.links.new(cl.outputs[0], tail.inputs[0])
                nt.links.new(ss.outputs["Result"], tail.inputs[1])
                fin = _math(nt, "MULTIPLY", 200, 120)
                fin.inputs[1].default_value = float(tint[3])   # authored _Color.a, not a constant
                nt.links.new(tail.outputs[0], fin.inputs[0])
                nt.links.new(fin.outputs[0], bsdf.inputs["Alpha"])
                cov_out = tail.outputs[0]        # `coverage`, BEFORE the tint.a multiply

                # Wet asphalt keeps the AUTHORED tint; the records carry materially different
                # colours and alphas and overwriting them flattened every puddle to one look.
                # The body term is `wet = m.tint.rgb * gi` (gpu_draw.wgsl:1565) -- a plain
                # diffuse whose albedo IS tint.rgb, exactly like the generic `lit` path. There is
                # no 0.25 anywhere in the shader; interchange's tint.rgb is already 0.1067 grey
                # (0.0539 on puddle_mask33) and a quarter of that is black.
                _sock(bsdf, "Base Color", (tint[0], tint[1], tint[2], 1.0))
                # gpu_draw.wgsl:1430 then :1434 -- clamp(m.roughness, 0.03, 1.0) floored at 0.10
                # for MAT_FLAG_WATER. All 190 water records in all six packs ship 0.05, so the
                # renderer always draws 0.10; the old 0.08 was not a value the viewer can produce.
                _sock(bsdf, "Roughness", max(0.10, max(0.03, min(1.0, float(
                    rec.get("roughness", 0.55) or 0.0)))))
                # IOR 1.333 is water, F0 = 0.02 -- the SAME Schlick the deep branch hand-codes at
                # :1770. NOT a hand-built copy of the game's `fr = (1 - NdotV*0.354)^2`: that is
                # 0.417 head-on, ~21x a real water surface, and a LayerWeight-driven mix is only
                # correct for CAMERA rays (the puddle seen in a mirror or on a second bounce gets
                # the wrong weight, and the shader stops being reciprocal). Cycles traces the real
                # sky, so `fr`, `refl` and `spec_rgb` -- all three stand-ins for an environment
                # the forward pass cannot see -- have nothing left to stand in for. Expect the
                # puddles to read DARKER head-on than the viewer and brighten at grazing: that
                # difference is the game's authored sheen, not a porting error.
                _sock(bsdf, "IOR", 1.333)

                # MATTE vs REFLECTIVE is the part that DOES have to be ported, because it is a
                # per-material classification the geometry decides, not a shading trick: a
                # stretched floor decal (tens-to-hundreds of metres per texture repeat) is wet
                # ground or tire marks and the shader kills its mirror AND its sun glint
                # (gpu_draw.wgsl:1563,1566), while a real puddle keeps both.
                if mid in self.water_matte:
                    _sock(bsdf, "Specular IOR Level", 0.0)
                    self.n_water_matte_mat += 1
                else:
                    # refl_mix's interior gate, clamp(2*max(coverage-0.5,0),0,1), scaled onto
                    # Blender's neutral 0.5 (0.5 == "F0 straight from the IOR", 0.0 == no lobe).
                    # A texel at coverage 0.4 gets ZERO reflection in the viewer while still being
                    # 40% opaque; leaving the socket at its default would not reproduce that.
                    sub = _math(nt, "SUBTRACT", 200, 260); sub.inputs[1].default_value = 0.5
                    nt.links.new(cov_out, sub.inputs[0])
                    mxg = _math(nt, "MAXIMUM", 340, 260); mxg.inputs[1].default_value = 0.0
                    nt.links.new(sub.outputs[0], mxg.inputs[0])
                    g = _math(nt, "MULTIPLY", 480, 260); g.inputs[1].default_value = 2.0
                    nt.links.new(mxg.outputs[0], g.inputs[0])
                    cg = _math(nt, "MINIMUM", 620, 260); cg.inputs[1].default_value = 1.0
                    nt.links.new(g.outputs[0], cg.inputs[0])
                    half = _math(nt, "MULTIPLY", 760, 260); half.inputs[1].default_value = 0.5
                    nt.links.new(cg.outputs[0], half.inputs[0])
                    nt.links.new(half.outputs[0], bsdf.inputs["Specular IOR Level"])
                self.n_water_puddle += 1
            else:
                # UNTEXTURED water is the sea, lakes and treatment basins: a deep BODY of water,
                # which the viewer keeps in the OPAQUE pass and shades as dark teal. Treating it
                # like a puddle with no mask made every one of them fully transparent, so you
                # looked straight through the sea to the world background.
                _sock(bsdf, "Alpha", 1.0)
                # `deep`, gpu_draw.wgsl:1781 -- EFT's own FX/SimpleWater4 _DepthColor, extracted
                # from Sandbox_Water4Advanced. Derived, not authored, and it deliberately IGNORES
                # the record's white tint.
                _sock(bsdf, "Base Color", (0.0275, 0.1418, 0.1323, 1.0))
                _sock(bsdf, "Roughness", 0.10)            # = max(clamp(0.05, 0.03, 1), 0.10)
                # THE MISSING PIECE. `wf = 0.02 + 0.98*pow(1-NwV, 5)` (:1770) IS Schlick for
                # IOR 1.333; Cycles computes it and traces the real sky instead of sky_reflect's
                # analytic gradient. Transmission stays OFF: `deep` is already the extinction
                # RESULT, so adding it would double-count the absorption.
                _sock(bsdf, "IOR", 1.333)
                _sock(bsdf, "Transmission Weight", 0.0)
                self._deep_water_normal(nt, bsdf, rec)
                self.n_water_deep += 1
            _sock(bsdf, "Metallic", 0.0)
            alpha_mode = "WATER_DONE"

        # ---- SoftCutout roads: coverage is VERTEX PAINT, not texture alpha ------------------
        # `Custom/Vert Paint SoftCutout Decal` is what EFT paves with: roads, parking, yard slabs.
        # Its coverage is COLOR_0.a shaped by the material's own params, and its texture ALPHA is
        # a SMOOTHNESS map. Driving opacity from that alpha eats the road surface in patches, which
        # is what the asphalt looked like. The viewer computes exactly this:
        #     coverage = clamp(color.a * astr - (acut - ahgt), 0, 1) * color.a
        # THE VERT-PAINT FAMILY NEVER ALPHA-TESTS. Its texture alpha is a SMOOTHNESS map, and the
        # assembler's Otsu coverage classifier mis-tags some of these as cutout. The viewer clears
        # the cutout bit for any vp material that is not a decal (gpu_driven.rs); without the same
        # guard every texel whose SMOOTHNESS falls under the cutoff is punched out and the ground
        # slabs come in full of rectangular holes.
        if isinstance(rec.get("vp"), dict) and role != "decal" and alpha_mode != "OPAQUE":
            alpha_mode = "OPAQUE"
            _set(mat, "blend_method", "OPAQUE")
            _set(mat, "surface_render_method", "DITHERED")

        # RFA GLASS: same channel, same trap. When roughnessFromAlbedoAlpha is set the alpha is a
        # smoothness map and coverage comes from the TINT alpha alone, not from the texture.
        if role == "glass" and rec.get("roughnessFromAlbedoAlpha") and alpha_out is not None:
            _sock(bsdf, "Alpha", tint[3])
            alpha_mode = "RFA_GLASS_DONE"

        # ---- glassTRS: the authored legacy glass response ---------------------------------
        # The gate is the shader's, verbatim: gpu_driven.rs:2013 is
        #     let glass_trs = mat.glass_trs && mat.role == "glass";
        # A record with glassTRS but role != "glass" gets NO lane and NO flag and falls through
        # to the ordinary opaque path (interchange ids 1613/2054/2248 are exactly that, and they
        # carry no reflectColor/specColor/shininess at all). Do not widen this test.
        #
        # The GRAPH is built after the normal block, because the environment lobe and the Schlick
        # gate both need the SHADING normal and the Normal Map node does not exist yet here. This
        # only reserves the alpha_mode marker so line "alpha_mode not in (...)" below stays out.
        #
        # glass_mode="physical" takes the SAME gate, so a record the viewer would not have given
        # the TRS lane to does not get transmission either. Only what happens inside changes.
        glass_gate = (role == "glass" and bool(rec.get("glassTRS")))
        glass_trs = glass_gate and self.glass_mode == "trs"
        glass_phys = glass_gate and self.glass_mode == "physical"
        if glass_gate:
            alpha_mode = "GLASS_TRS_DONE"
        if glass_phys:
            # coverage moves into the transmission, so the pane is a solid surface again and the
            # BLEND the record asked for would only buy sorting cost
            _set(mat, "blend_method", "OPAQUE")
            _set(mat, "surface_render_method", "DITHERED")

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

        if alpha_mode not in ("OPAQUE", "WATER_DONE", "SOFTCUT_DONE", "RFA_GLASS_DONE",
                              "GLASS_TRS_DONE") and alpha_out is not None:
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

        # ---- roughness ----------------------------------------------------
        # DO NOT bind `specMap` as a per-texel gloss map. The field is a PROVENANCE note, not a
        # texture to sample: the assembler already reduced that map to the scalar `roughness`
        # ("roughness from _SpecMap luma"), and the renderer only ever reads the scalar --
        #     rough = clamp(m.roughness, 0.03, 1.0);
        #     if (RFA) { rough = clamp(1.0 - tex.a, 0.06, 1.0); }      // gpu_draw.wgsl
        # -- taking per-pixel roughness from the ALBEDO'S ALPHA, never from specMap.
        #
        # Binding it looks like an upgrade and is a regression. For 189 of the 2,492 materials that
        # carry one, `specMap` IS THE ALBEDO FILE, so `roughness = 1 - albedo` makes bright paint
        # glossy: the forklift's red body came out at roughness 0.24, mirrored the overcast sky and
        # rendered neutral grey (measured R/G 0.96 against the texture's own 1.42, where the viewer
        # gives 1.45). Same channel, two meanings -- the trap this pipeline keeps setting.
        #
        # `and not glass_trs`: for TRS glass the shader NEVER READS the roughness lane at all.
        # gpu_draw.wgsl:1638 and :1651 select the TRS environment and the Blinn lobe over
        # refl_rgb / spec_rgb, discarding the GGX lobe, the analytic env lobe and the realtime
        # lights' spec with them. gpu_driven.rs:2330-2333 still computes a roughness for those
        # materials; it is a dead lane. No shipped pack has a TRS material with RFA set, but
        # relinking Roughness here would fight the graph built after the normal block.
        # `not glass_gate` covers BOTH glass modes: under glass_mode="physical" that same alpha is
        # being read as transmittance, and one channel cannot be smoothness and coverage at once.
        # (No shipped pack has a TRS material with RFA set, so this is a guard, not a behaviour.)
        if rec.get("roughnessFromAlbedoAlpha") and alpha_out is not None \
                and role in ("opaque", "glass") and not glass_gate:
            sub = _math(nt, "SUBTRACT", -520, -60)
            sub.inputs[0].default_value = 1.0
            nt.links.new(alpha_out, sub.inputs[1])
            mx = _math(nt, "MAXIMUM", -340, -60)
            nt.links.new(sub.outputs[0], mx.inputs[0])
            mx.inputs[1].default_value = 0.06
            nt.links.new(mx.outputs[0], bsdf.inputs["Roughness"])

        # ---- VERT-PAINT MATTE ROUGHNESS (gpu_draw.wgsl:1440-1441) ---------------------------
        #     if (vp_smooth >= 0.0) { rough = clamp(1.0 - 0.30 * vp_smooth, 0.72, 1.0); }
        # This is an ASSIGNMENT, not a floor: it DISCARDS the scalar for every MAT_FLAG_VP
        # material, so all 134 flagged on interchange change, not just the 5 sitting under 0.72.
        # The 129 at scalar 0.9 now range over [0.72, 1.0] per texel -- a high-alpha texel gets
        # GLOSSIER, a zero-alpha texel MATTER. The 5 at 0.5 (the mall's Concrete_floor_tiles,
        # ids 4278/4296/4304/4305/4312) are the ones that read semi-wet today.
        #
        # It is LAST in the shader, so it beats BOTH the RFA per-texel roughness above and the
        # water floor. On interchange that precedence is untestable (0 of the 134 are RFA, 0 are
        # water) -- implement the ordering anyway rather than relying on one pack.
        #
        # Port the NUMBER, not the response: the shader's rough drives a hand-written GGX with
        # SPEC_STRENGTH, Smith k = (rough+1)^2/8 and F0 = 0.04 lit from the SH volume's dominant
        # direction. Cycles uses multiscatter GGX against the real scene, so the same 0.72 will
        # not give the same highlight. Leave "Specular IOR Level" at its 0.5 default -- that IS
        # F0 = 0.04 -- and do not hand-tune the roughness to chase the raster highlight.
        if vp_smooth is not None:
            rg = _math(nt, "MULTIPLY", 1600, -700); rg.inputs[1].default_value = 0.30
            nt.links.new(vp_smooth, rg.inputs[0])
            rs = _math(nt, "SUBTRACT", 1740, -700); rs.inputs[0].default_value = 1.0
            nt.links.new(rg.outputs[0], rs.inputs[1])
            # 1 - 0.30*vp_smooth can never exceed 1.0 (w is normalised and the alphas are >= 0),
            # so the clamp's ceiling never binds and MAXIMUM alone is the whole clamp.
            rc = _math(nt, "MAXIMUM", 1880, -700); rc.inputs[1].default_value = 0.72
            nt.links.new(rs.outputs[0], rc.inputs[0])
            nt.links.new(rc.outputs[0], bsdf.inputs["Roughness"])
        elif vp_ok:
            # MAT_FLAG_VP in the viewer, but the splat graph could not be built here (no heights
            # map, or a layer image failed to load). The viewer still applies the override -- with
            # h = vec3(1.0) when the heights index is NO_ALBEDO -- so the honest static stand-in
            # is the floor rather than the discarded scalar.
            _sock(bsdf, "Roughness", max(0.72, min(1.0, float(rec.get("roughness", 0.55) or 0.0))))

        # ---- DETAIL ALBEDO, mean-neutralised -------------------------------------------------
        # Unity Standard multiplies a tiling detail map over the base at x2. Copying that naively
        # darkens or recolours the whole surface by the detail map's own average, so the renderer
        # divides that average back out: the map contributes only LOCAL contrast, never a global
        # shift. `albedoMeanGain` is that average, measured offline as mean(linear(sample) * 4.5948)
        # (gpu_draw.wgsl "#6 Detail albedo"). Getting this wrong is not subtle - 23 Interchange
        # materials carry one, and they are the rock and cliff surfaces, which without it read as
        # flat and low-frequency next to everything around them.
        #
        # Inserted as a multiply on whatever currently drives Base Color, so it composes with the
        # plain, tinted and vert-paint paths alike instead of racing them.
        det = rec.get("detail") if isinstance(rec.get("detail"), dict) else None
        dimg = self._image(det.get("albedo"), False) if det and det.get("albedo") else None
        if dimg is not None and bsdf.inputs["Base Color"].is_linked:
            base_st = [float(x) for x in (rec.get("uvXform") or [1, 1, 0, 0])]
            r = _detail_xform([float(x) for x in (det.get("albedoUv") or [1, 1, 0, 0])], base_st)
            duv = _node(nt, "ShaderNodeUVMap", -1500, -900)
            dm = _node(nt, "ShaderNodeMapping", -1320, -900)
            dm.inputs["Scale"].default_value = (r[0], r[1], 1.0)
            dm.inputs["Location"].default_value = (r[2], r[3], 0.0)
            nt.links.new(duv.outputs["UV"], dm.inputs["Vector"])
            dtex = _node(nt, "ShaderNodeTexImage", -1120, -900)
            dtex.image = dimg; dtex.extension = "REPEAT"; dtex.interpolation = "Smart"
            dtex.label = "detail albedo"
            nt.links.new(dm.outputs["Vector"], dtex.inputs["Vector"])

            gain = _node(nt, "ShaderNodeVectorMath", -930, -900)
            gain.operation = 'SCALE'; gain.inputs["Scale"].default_value = 4.5948   # Unity x2
            nt.links.new(dtex.outputs["Color"], gain.inputs[0])
            mg = [max(1e-3, float(v)) for v in (det.get("albedoMeanGain") or [1.0, 1.0, 1.0])]
            neu = _node(nt, "ShaderNodeVectorMath", -770, -900)
            neu.operation = 'DIVIDE'
            nt.links.new(gain.outputs["Vector"], neu.inputs[0])
            neu.inputs[1].default_value = (mg[0], mg[1], mg[2])
            lo = _node(nt, "ShaderNodeVectorMath", -610, -900)
            lo.operation = 'MAXIMUM'; lo.inputs[1].default_value = (0.25, 0.25, 0.25)
            nt.links.new(neu.outputs["Vector"], lo.inputs[0])
            hi = _node(nt, "ShaderNodeVectorMath", -450, -900)
            hi.operation = 'MINIMUM'; hi.inputs[1].default_value = (4.0, 4.0, 4.0)
            nt.links.new(lo.outputs["Vector"], hi.inputs[0])

            # weight = albedoStrength (the shader also fades it with distance; an offline render
            # has no reason to, so the near-field value stands everywhere)
            w = max(0.0, min(1.0, float(det.get("albedoStrength", 1.0) or 0.0)))
            wm = _node(nt, "ShaderNodeMix", -290, -900)
            wm.data_type = 'RGBA'; wm.inputs["Factor"].default_value = w
            wm.inputs[6].default_value = (1.0, 1.0, 1.0, 1.0)
            nt.links.new(hi.outputs["Vector"], wm.inputs[7])

            src = bsdf.inputs["Base Color"].links[0].from_socket
            mul = _node(nt, "ShaderNodeMix", -120, -760)
            mul.data_type = 'RGBA'; mul.blend_type = 'MULTIPLY'
            mul.inputs["Factor"].default_value = 1.0
            nt.links.new(src, mul.inputs[6])
            nt.links.new(wm.outputs[2], mul.inputs[7])
            nt.links.new(mul.outputs[2], bsdf.inputs["Base Color"])

        # ---- normal ------------------------------------------------------
        # DEEP water is skipped: gpu_draw.wgsl:1737-1742 builds its normal from WORLD UP plus the
        # ripple and never touches the mesh normal, and _deep_water_normal already consumed the
        # map (raw .xy, no green flip, no normalScale) as the authored wave layers.
        nmap_node = None
        nrm_tex = None
        nrm_path = rec.get("normal")
        nimg = self._image(nrm_path, True) if nrm_path else None
        if nimg is not None and not (role == "water" and img is None):
            ntex = _node(nt, "ShaderNodeTexImage", -1000, -320)
            ntex.image = nimg
            ntex.label = "normal (DirectX)"
            ntex.interpolation = "Smart"
            ntex.extension = "REPEAT"
            nrm_tex = ntex
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
            nmap_node = nmap

        # ---- bevel (photoreal only, EFT_BEVEL) -----------------------------
        # AFTER the Normal Map node, never instead of it. The Bevel node's Normal input is the
        # base it bevels FROM: left unconnected it falls back to the shading normal and the map
        # this block just built -- green-flipped, scaled by normalScale -- never reaches the BSDF
        # on any of the 874 normal-mapped materials in this pack. That is the specMap shape of
        # bug, a channel that is computed, looks connected and is dead, so the link below is the
        # whole point of the feature, and it is testable: at radius 0.0001 the frame must come back
        # as bevel-off. Measured, 2560x1440, 512 spp, against the two-seed sampling noise as the
        # unit: link present 0.12x the noise floor, link CUT 2.9x it and the whole frame 0.24%
        # darker, because 319 of these 323 materials lose their normal map. Same shape as specMap
        # -- silent, global, and it reads as "smoother", not as "broken".
        # Only `opaque`. decal is a coplanar overlay lifted 6 mm off a receiver it is meant to be
        # flush with, so a painted marking with no thickness would get an edge highlight and the
        # receiver sits inside three radii of the query anyway; cutout is a flat card whose real
        # edges are the CARD's corners, which is precisely what must stay invisible, and it is
        # built as coincident double-sided triangles (see the module docstring) so the query
        # averages a normal with its own negation; glass has the same coincident construction and
        # its edge is genuinely sharp because it is a cut edge, plus _glass_trs composes four
        # lobes against the normal it is handed; water either floats 6 mm off the ground or, when
        # deep, builds its normal from world up and never reads the mesh normal at all.
        # No bevel node without a normal map either -- the fallback base is Geometry > Normal,
        # which is the pack's own custom split normals, so authored smoothing survives.
        if self.bevel_radius > 0.0 and role == "opaque":
            bev = _node(nt, "ShaderNodeBevel", -20, -560)
            bev.samples = self.bevel_samples
            bev.inputs["Radius"].default_value = self.bevel_radius
            bev.label = "bevel %.1f mm (EFT_BEVEL)" % (self.bevel_radius * 1000.0)
            if nmap_node is not None:
                nt.links.new(nmap_node.outputs["Normal"], bev.inputs["Normal"])
            nt.links.new(bev.outputs["Normal"], bsdf.inputs["Normal"])
            self.n_bevel += 1

        self._parallax(nt, rec, role, base_tex, nrm_tex)
        # After the normal block, and only when there is a Base Color chain to multiply into.
        # Transmissive glass is skipped: darkening a transmissive Base Color attenuates what
        # passes THROUGH the pane, which is not what an occlusion term means.
        if self.cavity_dir and not glass_phys and role in ("opaque", "cutout", "decal"):
            self._cavity(nt, bsdf, rec, nrm_tex)
        em_done = False
        if glass_trs:
            em_done = self._glass_trs(nt, bsdf, rec, tint, base_tex, nmap_node)
        elif glass_phys:
            em_done = self._glass_physical(nt, bsdf, rec, tint, base_tex)

        # ---- emissive ----------------------------------------------------
        emis = rec.get("emissive")
        if isinstance(emis, dict) and not em_done:
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

    def _mesh_bounds(self):
        """Per-mesh EXACT local-space AABB, (nmesh, 2, 3) float32 -- [i, 0] = min, [i, 1] = max.

        THE PACK SHIPS NO BOUNDS (manifest.meshes carries only id/name/vtx*/idx*/submeshes), so
        they have to be derived, and they are derived EXACTLY rather than sampled. The thing this
        replaces read a strided sample of at most 64 vertices per mesh, which is fine for "roughly
        where is this" and wrong for anything a filter depends on: on a 513x513 terrain grid the
        stride ALIASES, and the sampled centre of Slice_2_2 came out 22.2 m from its true centroid
        -- enough to push it outside MAP_RADIUS 170 and delete the ground.

        ONE MEMMAP PASS over meshes.bin, reading position only. The OS pages in exactly the 12
        bytes per vertex the min/max touches and nothing else, so the exact answer is not slower
        than the approximation was: 0.56 s for interchange's 7,920 meshes, against 0.55 s for the
        seek-per-sample loop it replaces (which issued ~500k seek+read syscalls to read less data).

        Meaningful in the mesh's own space; for BAKED_WORLD meshes that space IS world space, which
        is exactly why the caller transforms by the instance affine either way.
        """
        if getattr(self, "_bounds", None) is not None:
            return self._bounds
        out = np.zeros((len(self.meshes), 2, 3), np.float32)
        path = os.path.join(self.pack, "meshes.bin")
        try:
            nrec = os.path.getsize(path) // self.vstride
            verts = np.memmap(path, dtype=self.vdtype, mode="r", shape=(int(nrec),))
            pos = verts["position"]
        except Exception as e:
            self.log.warn("cannot memmap meshes.bin for mesh bounds (%s); every instance will be "
                          "tested at its origin, so large meshes may be culled wrongly" % e)
            self._bounds = out
            return out
        t0 = time.time()
        for i, md in enumerate(self.meshes):
            nv = int(md.get("vtxCount", 0))
            off = int(md.get("vtxOffset", 0))
            if nv <= 0 or off < 0 or off % self.vstride:
                continue
            s = off // self.vstride
            blk = pos[s:s + nv]
            if blk.shape[0] == 0:
                continue
            out[i, 0] = blk.min(axis=0)
            out[i, 1] = blk.max(axis=0)
        self.log.info("bounds   exact local AABB for %d mesh(es) in %.2f s (one memmap pass)"
                      % (len(self.meshes), time.time() - t0))
        self._bounds = out
        return out

    def _water_tex_ids(self):
        """Material ids of TEXTURED water: the puddle class (gpu_driven.rs:2172-2193).

        Textured water is a thin puddle FILM and goes to the blend pass; untextured water is a
        deep BODY and stays opaque. Both the matte classification and the re-UV are puddle-only.
        """
        out = getattr(self, "_water_tex", None)
        if out is None:
            out = {int(m.get("id", i)) for i, m in enumerate(self.mats)
                   if isinstance(m, dict) and m.get("role") == "water" and m.get("albedo")}
            self._water_tex = out
        return out

    def _classify_water_matte(self):
        """MAT_FLAG_WATER_MATTE, mirroring the viewer's pre-pass at gpu_driven.rs:1904-1958.

        `Water Deferred Decal` serves both real puddles and stretched wet-ground / tire-trail
        floor decals, and the ONLY discriminator is world metres per texture repeat: a puddle maps
        its texture at a few m/repeat, a facility-floor decal at tens to hundreds. Measured from
        the geometry alone -- submesh local vertex-span diagonal over UV span -- so it is
        map-agnostic and needs no texture or mesh names.

        WHY A PRE-PASS AND NOT LAZY. Materials are built lazily from _face_materials during mesh
        construction, so a water material shared by a normal quad and a 300 m strip would be built
        from whichever mesh is read first and permanently get the wrong class. The viewer runs the
        same scan as a prepass for the same reason.
        """
        water_tex = self._water_tex_ids()
        if not water_tex:
            return
        for mid, md in enumerate(self.meshes):
            subs = md.get("submeshes") or []
            if not any(int(s.get("materialId", 0)) in water_tex for s in subs):
                continue
            try:
                # reuv=False: the classification reads the BAKED uvs, exactly like the viewer,
                # whose re-UV happens later in the upload loop.
                arrays = self._read_mesh_arrays(mid, reuv=False)
            except Exception:
                continue
            if arrays is None:
                continue
            pos, _nrm, uv, _col, idx = arrays
            for s in subs:
                m = int(s.get("materialId", 0))
                if m not in water_tex:
                    continue
                s0 = int(s.get("idxStart", 0))
                s1 = min(s0 + int(s.get("idxCount", 0)), idx.size)
                if s1 <= s0:
                    continue
                v = np.unique(idx[s0:s1])
                p, u = pos[v], uv[v]
                span = float(np.linalg.norm(p.max(0) - p.min(0)))      # 3D bbox DIAGONAL
                uv_rep = max(float((u.max(0) - u.min(0)).max()), 1e-3)
                if span / uv_rep > 40.0:                               # WATER_MATTE_MPR
                    self.water_matte.add(m)

    def _read_mesh_arrays(self, mid, reuv=True):
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

        # ---- PUDDLE RE-UV (gpu_driven.rs:2710-2799) ----------------------------------------
        # EFT's real puddles are small ~5 m decal_plane quads whose [0,1] UVs map the WHOLE soft
        # blob stamp, so their edges feather. But some puddle materials are baked onto huge ROAD
        # strips (77 x 318 m) with the same texture mapped across the ENTIRE strip: every visible
        # fragment then samples a <3% UV window deep in the blob's OPAQUE CORE (alpha ~1 there at
        # ANY mip) and the strip renders as a uniform HARD-EDGED slab. The renderer throws those
        # baked UVs away and planar-projects the LOCAL position onto the submesh's two widest
        # local axes at a fixed puddle-sized scale, so the blob tiles down the strip.
        #
        # Done here, on MESH-LOCAL positions, because this is the single choke point for both
        # _shared_mesh and _baked_mesh and it holds the exact space the viewer projects from --
        # after the world bake the projection basis would be different.
        #
        # PACK-space UVs are written: _make_mesh does the single V-flip on the way into Blender,
        # so the flipped result samples the identical texel the viewer samples. Do NOT pre-negate.
        # The image node's REPEAT extension (already the default here) is what makes it tile.
        water_tex = self._water_tex_ids() if reuv else set()
        subs = md.get("submeshes") or []
        if water_tex and any(int(s.get("materialId", 0)) in water_tex for s in subs):
            uv = uv.copy()                       # np.frombuffer views are read-only
            # last-writer-wins over the submesh index runs, exactly like the viewer's vert_mat
            vert_mat = np.full(nv, int(subs[0].get("materialId", 0)), np.int64)
            for s in subs:
                a = int(s.get("idxStart", 0))
                b = min(a + int(s.get("idxCount", 0)), idx.size)
                if b > a:
                    vert_mat[idx[a:b]] = int(s.get("materialId", 0))
            for s in subs:
                m = int(s.get("materialId", 0))
                if m not in water_tex:
                    continue
                a = int(s.get("idxStart", 0))
                b = min(a + int(s.get("idxCount", 0)), idx.size)
                if b <= a:
                    continue
                v = np.unique(idx[a:b])
                p, u = pos[v], uv[v]
                psz = p.max(0) - p.min(0)
                uv_span = max(float((u.max(0) - u.min(0)).max()), 1e-3)
                m_per_tile = float(np.linalg.norm(psz)) / uv_span
                ax = np.argsort(psz)[::-1]
                a_u, a_v = int(ax[0]), int(ax[1])
                # PUDDLE_STRETCH_MIN 15.0 / PUDDLE_MIN_WIDTH_M 3.0. The second test rejects a 1D
                # tire-mark / water-trail streak authored to tile along one axis. The MATTE flag
                # is deliberately NOT a gate here (the viewer says so at :2725-2726): matte only
                # kills reflection in the shader, it says nothing about edge softness.
                if m_per_tile < 15.0 or float(psz[a_v]) < 3.0:
                    continue
                cu = 0.5 * (float(p[:, a_u].min()) + float(p[:, a_u].max()))
                cv = 0.5 * (float(p[:, a_v].min()) + float(p[:, a_v].max()))
                sel = v[vert_mat[v] == m]
                if sel.size:
                    uv[sel, 0] = (pos[sel, a_u] - cu) / 6.0      # PUDDLE_TARGET_M
                    uv[sel, 1] = (pos[sel, a_v] - cv) / 6.0
                    self.n_water_reuv += 1
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

        # ---- DECAL / WATER LIFT ------------------------------------------------------------
        # The renderer separates coplanar overlays in CLIP space: `o.clip.z += 1e-3 * o.clip.w`
        # for MAT_FLAG_DECAL | MAT_FLAG_WATER (gpu_draw.wgsl, SURFACE_PUSH). A path tracer has no
        # depth bias to borrow, so the same separation has to be geometric, and it must happen on
        # the MESH rather than the object: a road slab and the asphalt under it are exactly
        # coplanar, and two coincident surfaces make the ray hit a coin toss - stippled speckle
        # that changes with the camera. Lifting along the vertex normal keeps the decal glued to
        # whatever it was authored against, including curbs and ramps.
        #
        # Only the vertices used by decal/water faces move, because a mesh can mix roles and
        # lifting an opaque face would detach it from its neighbours.
        lift = [i for i, sm in enumerate(slots)
                if sm is not None and sm.name.endswith((".decal", ".water"))]
        if lift and len(me.polygons):
            fi = np.empty(len(me.polygons), np.int32)
            me.polygons.foreach_get("material_index", fi)
            sel = np.isin(fi, np.asarray(lift, np.int32))
            if sel.any():
                lv = np.empty(len(me.loops), np.int32)
                me.loops.foreach_get("vertex_index", lv)
                vidx = np.unique(lv.reshape(-1, 3)[sel].ravel())
                co = np.empty(len(me.vertices) * 3, np.float32)
                me.vertices.foreach_get("co", co)
                co = co.reshape(-1, 3)
                co[vidx] += n2[vidx] * DECAL_LIFT
                me.vertices.foreach_set("co", co.ravel())
                self.n_lifted += int(vidx.size)

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
        # Must precede _build(): materials are built lazily during mesh construction, so the
        # matte/reflective class has to be known before the first water material is created.
        self._classify_water_matte()

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
        log.info("overlay %d vertex/vertices lifted %.0f mm along their normal (decal/water, the "
                 "geometric stand-in for the renderer's clip-space push)"
                 % (self.n_lifted, DECAL_LIFT * 1000.0))
        log.info("water   %d puddle material(s) (%d matte), %d deep, %d submesh(es) re-UV'd at "
                 "6 m/tile" % (self.n_water_puddle, self.n_water_matte_mat,
                               self.n_water_deep, self.n_water_reuv))
        log.info("shaders %d glassTRS tree(s), %d parallax offset(s), %d vert-paint splat(s)"
                 % (self.n_glass_trs, self.n_parallax, self.n_vp_graph))
        if self.n_glass_phys or self.n_cavity:
            log.info("photoreal %d physical glass material(s) (glass_mode=%s), %d cavity map(s) "
                     "into base colour -- NOT a parity build"
                     % (self.n_glass_phys, self.glass_mode, self.n_cavity))
        if self.bevel_radius > 0.0:
            # Counted for the same reason every other feature here is: a counter is the fastest
            # way to catch a lever that silently did nothing. A zero here with EFT_BEVEL set means
            # the role gate rejected the whole build, not that the bevel is subtle.
            log.info("bevel   %d opaque material(s) at %.1f mm, %d sample(s) -- shading only, no "
                     "geometry; decal/cutout/glass/water excluded -- NOT a parity build"
                     % (self.n_bevel, self.bevel_radius * 1000.0, self.bevel_samples))
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
            "glass_trs": self.n_glass_trs, "glass_physical": self.n_glass_phys,
            "cavity": self.n_cavity, "parallax": self.n_parallax,
            "bevel": self.n_bevel, "bevel_radius": self.bevel_radius,
            "vp_splat": self.n_vp_graph,
            "water_puddle": self.n_water_puddle,
            "water_matte": self.n_water_matte_mat,
            "water_deep": self.n_water_deep, "water_reuv": self.n_water_reuv,
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


def _alpha_is_constant(img):
    """True when an image's ALPHA is (near) constant, so the mask is in the luma instead.

    Mirrors the renderer's probe: sample on a big stride and call it constant when the range is
    under 13/255. Blender keeps pixels as a flat RGBA float list; sampling every 101st texel is
    the same cadence and keeps this cheap on a 2K atlas. Undecodable -> False, i.e. assume the
    alpha mask, which is the renderer's fallback too.
    """
    try:
        n = img.size[0] * img.size[1]
        if n <= 0:
            return False
        px = img.pixels[:]
        lo, hi = 1.0, 0.0
        for i in range(3, len(px), 4 * 101):
            a = px[i]
            if a < lo:
                lo = a
            if a > hi:
                hi = a
        return (hi - lo) < (13.0 / 255.0)
    except Exception:
        return False


def _alpha_mean(img):
    """Mean ALPHA of an image, or None. Same stride as _alpha_is_constant, for the same reason.

    Used by physical glass, where the game's coverage lane becomes the pane's transmittance and a
    scalar is the right shape for it: a Principled's transmission is not per-texel, and the 153
    interchange glass records carry no normal map and no RFA, so there is nothing per-texel to
    preserve. The stride is 101 texels so a 2K atlas stays cheap.
    """
    try:
        n = img.size[0] * img.size[1]
        if n <= 0:
            return None
        px = img.pixels[:]
        s, c = 0.0, 0
        for i in range(3, len(px), 4 * 101):
            s += px[i]
            c += 1
        return (s / c) if c else None
    except Exception:
        return None


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
                   collection_name=None, verbose=True,
                   glass_mode="trs", cavity_dir=None):
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

    TWO PHOTOREALISM SWITCHES. Both default to what the viewer does, and a game-parity build must
    leave both alone. See docs/extraction/photorealism.md.

    glass_mode      "trs" reproduces the legacy Transparent/Reflective/Specular response term for
                    term (_glass_trs). "physical" replaces it with real Cycles transmission on the
                    same panes, deriving the transmittance from the game's own coverage lane
                    (_glass_physical). Measured slightly CHEAPER than "trs" - the TRS graph is
                    ~40 nodes and an environment fetch per material, and one BSDF replaces it.
    cavity_dir      directory of cavity maps baked by tools/blender/bake_cavity.py. When given,
                    each opaque/cutout/decal material with a normal map also multiplies that
                    map's own self-occlusion into Base Color. Missing maps are silently skipped,
                    so a partial bake is a valid input.

    Returns a summary dict; also prints a compact one.
    """
    imp = _Importer(pack_dir, center, radius, max_instances, with_textures,
                    lod, include_inactive, collection_name, verbose,
                    glass_mode=glass_mode, cavity_dir=cavity_dir)
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
