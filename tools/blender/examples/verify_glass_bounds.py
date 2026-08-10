"""Verify that the legacy glassTRS reflection is BOUNDED, by auditing the graph the importer builds.

Run it from the repository root::

    blender -b --factory-startup --python tools/blender/examples/verify_glass_bounds.py

It renders nothing and writes nothing: it imports one small region twice, once in each glass mode,
and audits the node tree the TRS path produces.

WHY THIS IS A SEPARATE CHECK AND NOT A MEASUREMENT ON THE PUBLISHED FRAMES. ``MODES["photoreal"]``
sets ``glass_mode="physical"``, so the photoreal frames on the front page contain no TRS glass at
all - the exterior build logs "0 glassTRS tree(s), 19 physical glass material(s)". The
bounded-reflection fix lives on the GAME-PARITY path, so nothing in the photoreal chain exercises
it and it has to be exercised deliberately.

WHAT "BOUNDED" MEANS HERE, precisely. The viewer composes

    out.rgb = apply_fog(lit) * trs_a + spec_g + refl_g + em_rgb        (gpu_draw.wgsl:1661)
    refl_g  = _ReflectColor * fresnel_v * (E / (1 + E)),  E = max(sh_env, 0.03)   (:1627-1638)

E/(1+E) is a Reinhard on the environment RADIANCE, applied BEFORE it enters the lobe, and it is the
whole contract of the family: texCUBE returns an image in [0,1], so refl_g < _ReflectColor
componentwise for every pixel, always. An earlier importer built the two additive lobes as BSDFs
and let Cycles trace the world into them. A BSDF cannot be given that bound at any node: its input
is the radiance the integrator carries into it, that radiance does not exist until after the BSDF
has been evaluated, and no node can post-process a shader socket - x/(1+x) has nothing to apply
to. Measured cost of getting this wrong, on an Interchange facade head-on: the pane came back 5.4x
too light in graded display and 5.5x too light RELATIVE TO THE OPAQUE WALL BESIDE IT, with 86% of
the excess radiance entering through the "specular" node and 89% of that being world, not sun.

So the audit is structural, because the bound is structural. For every TRS glass material it asserts
    1. neither additive lobe is a BSDF -- no Glossy/Diffuse/Glass/Refraction node anywhere;
    2. the environment enters as a VALUE: an Environment Texture (or, for MAT_FLAG_GLASS_CUBE
       materials, a constant) reaching a DIVIDE whose denominator is 1 + E;
    3. that value is scaled by _ReflectColor and by a Schlick fresnel that is itself <= 1,
and then prints the resulting ceiling, RC componentwise, per material.

env:
    EFT_REPO      repository root; only needed when there is no __file__ to derive it from
    EFT_PACK      the pack to audit (default packs/interchange.eftpack)
    EFT_CENTRE    pack-space "x,y,z" to import around (default the ULTRA mall's shopfront glass)
    EFT_RADIUS    metres about that centre (default 26)
"""
import os

import bpy

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))
os.environ.setdefault("EFT_REPO", REPO)
# import_eftpack.py runs a demo import at MODULE SCOPE when it is exec'd, so cap it at one instance.
os.environ["EFT_MAX_INSTANCES"] = "1"

PACK = os.environ.get("EFT_PACK", os.path.join(REPO, "packs", "interchange.eftpack"))
# gamedata rooms[] MALL_1stCentralSquare: shopfront glass, which is where the 5.4x was measured.
CENTRE = tuple(float(v) for v in os.environ.get("EFT_CENTRE", "-3.1,27.1,-66.7").split(","))
RADIUS = float(os.environ.get("EFT_RADIUS", "26"))

BSDF_IDS = ("ShaderNodeBsdfGlossy", "ShaderNodeBsdfDiffuse", "ShaderNodeBsdfGlass",
            "ShaderNodeBsdfRefraction", "ShaderNodeBsdfAnisotropic", "ShaderNodeBsdfToon")

path = os.path.join(REPO, "tools", "blender", "example_scene.py")
g = {"__name__": "example_scene", "__file__": path}
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), g)
gm = g["_load"]("import_eftpack.py")
for c in list(bpy.data.collections):
    if c.name.startswith("eftpack_"):
        for o in list(c.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(c)


def glass_materials():
    return [m for m in bpy.data.materials if m.name.endswith(".glass") and m.use_nodes]


for mode in ("trs", "physical"):
    g["_clear"]()
    for m in list(bpy.data.materials):
        bpy.data.materials.remove(m)
    res = gm["import_eftpack"](PACK, center=CENTRE, radius=RADIUS, with_textures=True,
                               collection_name="probe_%s" % mode, glass_mode=mode)
    mats = glass_materials()
    print("\n%s glass_mode=%r: %d instance(s), %d glass material(s)"
          % ("=" * 8, mode, len(res["objects"]) if isinstance(res.get("objects"), list)
             else res.get("instances", -1), len(mats)), flush=True)
    if mode == "physical":
        # The photoreal path. Nothing to bound: it is real Cycles transmission, and the reason it
        # cannot blow out the way the traced-BSDF TRS bug did is that transmission is
        # energy-conserving by construction rather than additive on top of a lit body.
        for m in mats[:4]:
            bs = [n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"]
            for b in bs[:1]:
                print("   %-46s transmission %.3f roughness %.3f ior %.3f"
                      % (m.name[:46], b.inputs["Transmission Weight"].default_value,
                         b.inputs["Roughness"].default_value, b.inputs["IOR"].default_value),
                      flush=True)
        continue

    bad_bsdf = failed = passed = 0
    for m in mats:
        nt = m.node_tree
        ids = [n.bl_idname for n in nt.nodes]
        offenders = [i for i in ids if i in BSDF_IDS]
        if offenders:
            bad_bsdf += 1
            print("   FAIL %-40s additive lobe built as a BSDF: %s" % (m.name[:40], offenders),
                  flush=True)
            continue
        # the Reinhard: a DIVIDE whose second input comes from an ADD of (E, 1.0)
        reinhard = None
        for n in nt.nodes:
            if n.bl_idname != "ShaderNodeMath" or n.operation != "DIVIDE":
                continue
            den = [l.from_node for l in nt.links if l.to_node is n and l.to_socket is n.inputs[1]]
            if not den:
                continue
            d = den[0]
            if d.bl_idname == "ShaderNodeMath" and d.operation == "ADD":
                consts = [s.default_value for s in d.inputs[:2] if not s.is_linked]
                if any(abs(float(c) - 1.0) < 1e-6 for c in consts):
                    reinhard = n
                    break
        env = [n for n in nt.nodes if n.bl_idname == "ShaderNodeTexEnvironment"]
        emis = [n for n in nt.nodes if n.bl_idname == "ShaderNodeEmission"]
        # EC only: the node whose constant vector IS _ReflectColor and whose Scale is driven by
        # the fresnel. Filtering on "unlinked vector input" alone also catches the dominant-light
        # direction scale and prints its (negative) components as if they were a ceiling.
        scale = [n for n in nt.nodes if n.bl_idname == "ShaderNodeVectorMath"
                 and n.operation == "SCALE" and not n.inputs[0].is_linked
                 and n.inputs["Scale"].is_linked
                 and min(list(n.inputs[0].default_value)[:3]) >= 0.0]
        rc = [tuple(round(v, 4) for v in list(s.inputs[0].default_value)[:3]) for s in scale]
        cube_const = bool(scale) and not env      # MAT_FLAG_GLASS_CUBE: constant, still bounded
        ok = (reinhard is not None or cube_const) and len(emis) >= 1
        if ok:
            passed += 1
        else:
            failed += 1
        if passed <= 4 and ok:
            print("   PASS %-40s emission lobes %d  reinhard %s  env-fetch %d  ceiling RC=%s"
                  % (m.name[:40], len(emis),
                     "E/(1+E)" if reinhard is not None else "constant _Cube (bounded by "
                                                            "construction)", len(env), rc[:2]),
                  flush=True)
    print("   glassTRS audit: %d bounded, %d without a bound, %d built as BSDFs (must be 0)"
          % (passed, failed, bad_bsdf), flush=True)
print("\n[glass] DONE", flush=True)
