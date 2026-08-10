"""Blender importer for the pack's practical lights (lights_*.json).

Standalone. Run inside Blender, then call ``import_eftlights(pack_dir, parent=..., center=...,
radius=...)``.

WHY THIS EXISTS. A map's sun lights the outdoors and nothing else. Every ceiling panel, strip
light, lamp and sign in Interchange is a Unity light object, and the pack ships 1,659 of them. Skip
them and every interior goes BLACK: seen through a window from outside, a lit shop reads as a flat
dark rectangle, which is easy to misdiagnose as a broken glass material because the pane is the
thing you are looking at. It is not the pane. It is the unlit room behind it.

FORMAT (docs/extraction/lighting-and-sh-bake.md). One JSON array per Unity scene, listed in
``manifest.sidecars.lightsAll``; ``sidecars.lights`` names only the primary one. Load them ALL and
de-dup by filename, exactly as the viewer does, or a multi-scene map loses whole buildings.

    name, type ("Point" | "Spot" | "Directional"), position[3] and direction[3] in PACK space,
    rotation[4] (xyzw), color[4] LINEAR, intensity, range, spotAngle, innerSpotAngle (both FULL
    angles, degrees), shadowType (Unity LightShadows: 0 None, 1 Hard, 2 Soft), on.

`direction` is already the extracted forward vector, so the quaternion never has to be touched.

ENERGY. The viewer accumulates ``color * intensity * EFT_LIGHT_SCALE * (1-(d/r)^2)^2 / d^2`` and
multiplies by N.L with no 1/pi (gpu_draw.wgsl). Blender's lambert does divide by pi, and a lamp of
P watts delivers P/(4.pi.d^2). Matching the far-field term gives

    watts = 4 * pi^2 * LIGHT_SCALE * intensity

which is why the numbers look large. A spot uses the same formula: Blender defines spot power as
the equivalent point power, so narrowing the cone does not brighten it.

RANGE has no Cycles equivalent. Unity's window culls the light hard at `range`; Blender's falloff
is pure inverse-square and never reaches zero. `cutoff_distance` is set for EEVEE, which honours
it, and Cycles simply lets the far tail run - visible only as a slight lift far from a lamp.

SHADOWS default to ON for every light regardless of `shadowType`. That flag is a Unity performance
decision, not an artistic one: 1,626 of Interchange's lights have it at None because real-time
shadow maps are expensive, and a path tracer that honours it leaks every interior lamp straight
through the walls into the street. Pass ``honour_shadow_flag=True`` to reproduce the game's own
shadowing instead.
"""

import json
import math
import os

import bpy
from mathutils import Matrix, Vector

__all__ = ["import_eftlights"]

LIGHT_SCALE = 6.0                       # gpu_driven.rs DEFAULT_LIGHT_SCALE
WATTS_PER_INTENSITY = 4.0 * math.pi * math.pi * LIGHT_SCALE
POINT_RADIUS = 0.05                     # m; punctual in Unity, a touch of softness here


def _sidecar_names(pack_dir):
    """Every light sidecar the assembler emitted, de-duped, primary first."""
    try:
        mf = json.load(open(os.path.join(pack_dir, "manifest.json"), encoding="utf-8"))
    except Exception:
        mf = {}
    side = mf.get("sidecars") or {}
    names, seen = [], set()
    for n in [side.get("lights")] + list(side.get("lightsAll") or []):
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    if not names:                        # older pack with no manifest entry
        names = sorted(f for f in os.listdir(pack_dir)
                       if f.startswith("lights_") and f.endswith(".json"))
    return [n for n in names if os.path.exists(os.path.join(pack_dir, n))]


def import_eftlights(pack_dir, parent=None, center=None, radius=None,
                     collection_name="eft_lights", honour_shadow_flag=False,
                     light_scale=LIGHT_SCALE, verbose=True):
    """Create one Blender lamp per pack light. Returns the objects created.

    `parent` should be the map root empty from import_eftpack: positions stay in PACK space and
    the empty's YUP_TO_ZUP carries them into Blender's frame, the same contract the grass and
    character importers use. With no parent the conversion is applied per light instead.
    """
    pack_dir = os.path.abspath(pack_dir)
    names = _sidecar_names(pack_dir)
    if not names:
        raise IOError("no lights_*.json in %s" % pack_dir)

    recs = []
    for n in names:
        recs.extend(json.load(open(os.path.join(pack_dir, n), encoding="utf-8")))

    coll = bpy.data.collections.get(collection_name) or bpy.data.collections.new(collection_name)
    if coll.name not in {c.name for c in bpy.context.scene.collection.children}:
        bpy.context.scene.collection.children.link(coll)

    watts = 4.0 * math.pi * math.pi * float(light_scale)
    r2 = float(radius) ** 2 if radius is not None else None
    made, n_off, n_far, n_dir = [], 0, 0, 0

    for rec in recs:
        # The viewer's own reduce rule: an off light, or one with no intensity or no reach,
        # contributes nothing and is dropped rather than imported as a dead object.
        if not rec.get("on", True) or float(rec.get("intensity") or 0.0) <= 0.0 \
                or float(rec.get("range") or 0.0) <= 0.0:
            n_off += 1
            continue

        p = [float(v) for v in rec["position"]]
        if r2 is not None and center is not None:
            d = [p[0] - center[0], p[1] - center[1], p[2] - center[2]]
            if d[0] * d[0] + d[1] * d[1] + d[2] * d[2] > r2:
                n_far += 1
                continue

        kind = str(rec.get("type") or "Point")
        if kind == "Directional":
            # The sun is authored separately (and a second one double-lights the scene).
            n_dir += 1
            continue

        ld = bpy.data.lights.new(str(rec.get("name") or "light")[:58],
                                 "SPOT" if kind == "Spot" else "POINT")
        col = [float(c) for c in (rec.get("color") or [1, 1, 1, 1])]
        ld.color = (col[0], col[1], col[2])
        ld.energy = watts * float(rec["intensity"])
        ld.shadow_soft_size = POINT_RADIUS
        ld.use_shadow = (int(rec.get("shadowType") or 0) != 0) if honour_shadow_flag else True
        try:                                     # EEVEE honours this; Cycles ignores it
            ld.use_custom_distance = True
            ld.cutoff_distance = float(rec["range"])
        except Exception:
            pass

        if kind == "Spot":
            outer = max(float(rec.get("spotAngle") or 60.0), 1.0)
            inner = max(min(float(rec.get("innerSpotAngle") or 0.0), outer), 0.0)
            ld.spot_size = math.radians(min(outer, 180.0))
            ld.spot_blend = max(0.0, min(1.0, 1.0 - inner / outer))

        obj = bpy.data.objects.new(ld.name, ld)
        coll.objects.link(obj)
        obj.location = p                          # PACK space; the parent empty rotates it
        if kind == "Spot":
            # Blender emits down local -Z, so aim that at the extracted forward vector.
            d = rec.get("direction") or [0.0, -1.0, 0.0]
            fwd = Vector((float(d[0]), float(d[1]), float(d[2])))
            if fwd.length > 1e-6:
                obj.rotation_euler = fwd.normalized().to_track_quat("-Z", "Y").to_euler()
        if parent is not None:
            obj.parent = parent
        made.append(obj)

    if parent is None:                            # no root empty: carry the frame per light
        yup_to_zup = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
        for o in made:
            o.matrix_world = yup_to_zup @ o.matrix_world

    try:
        bpy.context.view_layer.update()
    except Exception:
        pass

    if verbose:
        print("[eftlights] %d record(s) in %d sidecar(s) -> %d lamp(s)"
              % (len(recs), len(names), len(made)))
        print("[eftlights] dropped %d off/zero, %d outside radius, %d directional"
              % (n_off, n_far, n_dir))
        print("[eftlights] shadows %s"
              % ("from shadowType" if honour_shadow_flag else "forced ON (see module docstring)"))
    return made
