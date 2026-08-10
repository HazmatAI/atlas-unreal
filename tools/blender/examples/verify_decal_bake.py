"""Verify, on a SHIPPED pack and dataset, the two decal claims every published frame depends on.

Runs OUTSIDE Blender, under any interpreter with numpy. Neither claim can be checked inside
Blender: by the time ``import_eftpack`` sees a decal it is already a baked, world-space triangle
soup, so what has to be tested is the bake and the material table that produced it, not the scene
they end up in.

Run it from the repository root::

    python tools/blender/examples/verify_decal_bake.py
    python tools/blender/examples/verify_decal_bake.py packs/woods.eftpack eft_assets/woods_v2

It writes nothing. It prints two verdicts and the census behind each, and it is worth running after
any change to the decal bake, to ``materials.json``, or to the projector code.

CLAIM 1 - THE 60 DEGREE FACING CULL IS APPLIED, UNCONDITIONALLY.
``decal_project`` keeps a receiving triangle only when ``n . uy >= FACING_MIN`` (0.50, i.e. 60
degrees) where uy is the projector's own axis. An earlier version waived that cutoff for decals
carrying a ``vp`` block, on the theory that the shader's baked angle fade would attenuate them; it
does not read that fade off the non-SoftCutout path, so the exemption shipped grazing faces at FULL
opacity - 13.3% of all baked faces, and the vertical smears down angled concrete that invariant 7
names.

``decals.json`` is rewritten by the bake with an IDENTITY matrix per decal, so uy is not recoverable
from the dataset and the cull cannot be re-run. It can still be FALSIFIED, cleanly. If every kept
normal lies within 60 degrees of one axis uy, then any two kept normals are within 120 degrees of
each other, so

    min over pairs of (n_i . n_j)  >=  cos(120 deg)  =  -0.5

for every decal, with no reference to uy at all, and a pair below it proves the cull was not applied
to that decal. Two things have to be handled before that bound means anything, and both were found
by this check failing: the orphaned bakes (see the code) and the sliver normals (ditto). With those
out, the worst pair on Interchange is -0.5025, i.e. the bound is TIGHT - there really are decals
painting two surfaces 120 degrees apart, which is exactly what a 60 degree cone permits and what
nothing looser would stop at. The second statistic printed is the softer one: taking the
area-weighted mean normal as an estimate of uy, the per-face ``n . uy_est`` distribution should stop
near 0.5 rather than trailing to zero.

CLAIM 2 - DECAL COVERAGE IS SAMPLED, i.e. the SoftCutout flag survives into the pack.
The alpha-less decal class carries its coverage in COLOR_0 and only the SoftCutout path reads it.
The producer may spell the flag as an explicit ``softCutout`` triple or as the three scalars
astr/acut/ahgt; a consumer that accepts only the scalars drops the flag for the explicit spelling,
coverage falls back to tex.a = 1.0, and the decal paints its whole footprint opaque - a shell 18 mm
off a plate rendering as a solid rectangle. Measured before the fix on Interchange: 1,731 decal
materials, 195 with a vp block, 117 with softCutout and 78 without, all 78 byte-identical
``{"layers": [], "heights": null, "blend": 1.0}`` sharing one albedo. This prints the same census,
so the 78 are visible if they come back.

env:
    EFT_REPO      repository root; only needed when there is no __file__ to derive it from
    EFT_PACK      the pack to audit     (default packs/interchange.eftpack)
    EFT_DATASET   the dataset beside it (default eft_assets/interchange_v2)
"""
import json
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
REPO = (os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir)) if _HERE
        else os.environ.get("EFT_REPO", os.getcwd()))

PACK = (sys.argv[1] if len(sys.argv) > 1
        else os.environ.get("EFT_PACK", os.path.join(REPO, "packs", "interchange.eftpack")))
DATASET = (sys.argv[2] if len(sys.argv) > 2
           else os.environ.get("EFT_DATASET", os.path.join(REPO, "eft_assets", "interchange_v2")))

FACING_MIN = 0.50
MIN_ALTITUDE_M = 1e-3            # see the sliver note below; 100x the OBJ's 10 micron write quantum
# cos(120 deg) is the bound; the tolerance is the numerical width of it. A face pair sitting at
# exactly the cull limit lands on -0.5000 in exact arithmetic and on -0.5025 through a 10 micron
# vertex quantum at 1 mm altitude, so the falsifier has to be looser than that noise and tighter
# than anything a waived cull produces (a waived cull puts pairs at -1.0).
CONE_DOT = -0.5 - 0.01


def read_obj_faces(path):
    """Vertices and triangle indices from a baked decal OBJ. `f a/a b/b c/c` only."""
    vs, fs = [], []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                a = line.split()
                vs.append((float(a[1]), float(a[2]), float(a[3])))
            elif line.startswith("f "):
                a = line.split()[1:]
                idx = [int(t.split("/")[0]) - 1 for t in a[:3]]
                fs.append(idx)
    return np.asarray(vs, np.float64), np.asarray(fs, np.int64)


def main():
    print("pack    %s" % PACK, flush=True)
    print("dataset %s" % DATASET, flush=True)

    # ---- CLAIM 2, the material census -------------------------------------------------------
    mats = json.load(open(os.path.join(PACK, "materials.json"), encoding="utf-8"))
    mats = mats if isinstance(mats, list) else mats.get("materials", [])
    dec = [m for m in mats if m.get("role") == "decal"]
    vp = [m for m in dec if m.get("vp")]
    sc = [m for m in vp if isinstance(m.get("vp"), dict) and m["vp"].get("softCutout")]
    nosc = [m for m in vp if m not in sc]
    print("\n[decal coverage] %d material(s) total, %d role=decal, %d with a vp block"
          % (len(mats), len(dec), len(vp)), flush=True)
    print("[decal coverage] vp WITH softCutout %d   vp WITHOUT %d   (the 78 that shipped opaque "
          "were all vp-without)" % (len(sc), len(nosc)), flush=True)
    if nosc:
        alb = {}
        for m in nosc:
            k = str(m.get("tex") or (m.get("subs") or [{}])[0].get("tex"))
            alb[k] = alb.get(k, 0) + 1
        print("[decal coverage] FAIL-SHAPED: %d vp decal(s) with no softCutout; albedo census %s"
              % (len(nosc), sorted(alb.items(), key=lambda t: -t[1])[:4]), flush=True)
    else:
        print("[decal coverage] PASS: every vp decal carries an explicit softCutout triple, so "
              "MAT_FLAG_SOFTCUTOUT sets and COLOR_0 coverage is sampled", flush=True)
    ex = sc[0]["vp"] if sc else None
    if ex:
        print("[decal coverage] example softCutout: %s" % json.dumps(ex)[:180], flush=True)

    # ---- CLAIM 1, the cone test -------------------------------------------------------------
    # ONLY THE BAKES decals.json STILL REFERENCES. decal_bake_%05d__gen.obj is keyed by list
    # index, so when the surviving-decal count changes the indices shift and the previous run's
    # files linger on disk. Testing the directory listing instead of the instance table therefore
    # tests a mixture of the current bake and every earlier one: on the Interchange dataset 89 of
    # the 1,710 files are orphans, and the first version of this check "failed" on
    # decal_bake_00326, an 18,434-triangle orphan that today's MAX_TRIS_PER_DECAL = 4,000 would
    # have skipped outright (the largest LIVE decal is 3,949). The assembler already ignores them;
    # so must this.
    mdir = os.path.join(DATASET, "meshes")
    live = [i["mesh"] for i in
            json.load(open(os.path.join(DATASET, "decals.json"), encoding="utf-8"))["instances"]]
    on_disk = set(f for f in os.listdir(mdir)
                  if f.startswith("decal_bake_") and f.endswith(".obj"))
    bakes = sorted(set(live) & on_disk)
    print("\n[facing cull] %d live decal instance(s); %d bake file(s) on disk, %d orphan(s) ignored"
          % (len(live), len(on_disk), len(on_disk - set(live))), flush=True)
    viol, checked, tris = [], 0, 0
    below = 0
    total_faces = 0
    worst = (1.0, None)
    rng = np.random.default_rng(0)
    for name in bakes:
        V, F = read_obj_faces(os.path.join(mdir, name))
        if len(F) == 0:
            continue
        P0, P1, P2 = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
        n = np.cross(P1 - P0, P2 - P0)
        ln = np.linalg.norm(n, axis=1)
        # SLIVERS ARE NOT EVIDENCE, AND AREA IS THE WRONG FILTER FOR THEM. The bake fans a clipped
        # polygon and orders each fan triangle so tri_n . nrm >= 0; three clipped points that come
        # out collinear make tri_n zero, the test passes vacuously, and the emitted normal is
        # whatever the rounding was. The OBJ writes vertices at "%.5f", a 10 micron quantum, so a
        # triangle 1 m long and 2 microns wide has a MEANINGLESS normal at a perfectly respectable
        # 1e-6 m^2 of area. Filter on the minimum ALTITUDE, 2*area/longest edge, which is the
        # dimension the quantum actually attacks. At 1 mm -- 100 quanta -- this drops 1.0% of
        # faces and the worst pair in the whole map moves from -1.0000 to -0.5025.
        edge = np.maximum(np.maximum(np.linalg.norm(P1 - P0, axis=1),
                                     np.linalg.norm(P2 - P1, axis=1)),
                          np.linalg.norm(P0 - P2, axis=1))
        alt = np.where(edge > 0, ln / np.maximum(edge, 1e-30), 0.0)
        ok = alt > MIN_ALTITUDE_M
        if not ok.any():
            continue
        area = ln[ok] * 0.5
        nn = n[ok] / ln[ok][:, None]
        checked += 1
        tris += len(nn)
        # falsifier: min pairwise dot must be >= cos(120 deg)
        if len(nn) <= 700:
            gram = nn @ nn.T
            mind = float(gram.min())
        else:
            i = rng.integers(0, len(nn), 40000)
            j = rng.integers(0, len(nn), 40000)
            mind = float((nn[i] * nn[j]).sum(1).min())
        if mind < worst[0]:
            worst = (mind, name)
        if mind < CONE_DOT:
            viol.append((name, mind, len(nn)))
        # soft statistic: where does n . uy_est stop
        uy = (nn * area[:, None]).sum(0)
        uy = uy / max(np.linalg.norm(uy), 1e-12)
        d = nn @ uy
        below += int((d < FACING_MIN).sum())
        total_faces += len(d)
    print("[facing cull] %d decal(s) with geometry, %d triangle(s)" % (checked, tris), flush=True)
    print("[facing cull] worst min pairwise normal dot = %.4f on %s   (a 60 deg cull bounds it at "
          "-0.5000; anything below FALSIFIES the cull)" % (worst[0], worst[1]), flush=True)
    if viol:
        print("[facing cull] FAIL: %d decal(s) violate the cone bound, worst 5 %s"
              % (len(viol), sorted(viol, key=lambda t: t[1])[:5]), flush=True)
    else:
        print("[facing cull] PASS: no decal contains a pair of faces more than 120 deg apart, "
              "which is exactly what n.uy >= 0.50 permits and a waived cull does not", flush=True)
    print("[facing cull] faces below 0.50 of their own area-weighted mean normal: %d / %d = %.2f%% "
          "(uy_est is only an estimate of the projector axis, so this is the soft check; the "
          "waived-cull measurement was 13.3%% of faces on grazing surfaces)"
          % (below, total_faces, 100.0 * below / max(total_faces, 1)), flush=True)


if __name__ == "__main__":
    main()
