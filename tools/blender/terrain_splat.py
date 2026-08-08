"""MicroSplat terrain material for the Blender importer, run inside Blender.

The pack ships a BAKED albedo slice per terrain tile (4096 px over a 700 m tile = 5.9 texels/m).
That is fine at distance and visibly soft up close. The real material is a MicroSplat splat: N
layer textures, each tiling at its own repeat count, blended by weights packed into control maps
four-per-texture (layer.ctrl selects the map, layer.chan selects R/G/B/A). The grass layer tiles
every 1.76 m, so at 1024 px it carries ~582 texels/m: about a hundred times the baked slice.

    weight_i = ctrl[layer.ctrl][layer.chan]        sampled with the tile's own 0..1 UV
    albedo   = sum(weight_i * layer_i(uv * rep_i)) / sum(weight_i)
"""
import json
import os
import re

import bpy

CHAN = {0: "Red", 1: "Green", 2: "Blue"}          # 3 is the image node's Alpha output


def _img(path, srgb=True):
    im = bpy.data.images.load(path, check_existing=True)
    try:
        im.colorspace_settings.name = "sRGB" if srgb else "Non-Color"
    except Exception:
        pass
    return im


def build_terrain_splat(mat, tile, layers_dir, uv_vflipped=True, max_layers=12):
    """Replace `mat`'s node tree with the tile's MicroSplat blend. Returns the layer count."""
    ctrl_maps = tile.get("ctrl_maps") or []
    layers = [L for L in (tile.get("layers") or []) if int(L.get("idx", 99)) < max_layers]
    if not ctrl_maps or not layers:
        return 0

    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (1400, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled"); bsdf.location = (1100, 0)
    bsdf.inputs["Roughness"].default_value = 0.92
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    uvn = nt.nodes.new("ShaderNodeTexCoord"); uvn.location = (-1600, 0)
    uv_src = uvn.outputs["UV"]

    # The importer un-flips V for Blender's bottom-left origin; the control maps are authored in
    # the pack's top-left space, so flip V back for THEM only. The layer textures tile, so their
    # V origin does not matter.
    if uv_vflipped:
        flip = nt.nodes.new("ShaderNodeVectorMath"); flip.operation = 'MULTIPLY_ADD'
        flip.location = (-1420, 120)
        flip.inputs[1].default_value = (1.0, -1.0, 1.0)
        flip.inputs[2].default_value = (0.0, 1.0, 0.0)
        nt.links.new(uv_src, flip.inputs[0])
        ctrl_uv = flip.outputs["Vector"]
    else:
        ctrl_uv = uv_src

    # control maps: sampled once each, split to 4 weight channels
    weights = {}
    for j, fn in enumerate(ctrl_maps):
        p = os.path.join(layers_dir, fn)
        if not os.path.isfile(p):
            continue
        t = nt.nodes.new("ShaderNodeTexImage"); t.location = (-1200, 400 - j * 300)
        t.image = _img(p, srgb=False)          # weights are DATA, never colour-managed
        t.extension = 'EXTEND'
        t.interpolation = 'Cubic'
        nt.links.new(ctrl_uv, t.inputs["Vector"])
        sep = nt.nodes.new("ShaderNodeSeparateColor"); sep.location = (-1000, 400 - j * 300)
        nt.links.new(t.outputs["Color"], sep.inputs["Color"])
        for c in range(3):
            weights[(j, c)] = sep.outputs[CHAN[c]]
        weights[(j, 3)] = t.outputs["Alpha"]

    col_sum = None
    w_sum = None
    used = 0
    for k, L in enumerate(sorted(layers, key=lambda x: int(x.get("idx", 0)))):
        wsock = weights.get((int(L.get("ctrl", 0)), int(L.get("chan", 0))))
        name = str(L.get("name", ""))
        p = os.path.join(layers_dir, "layer_%s.png" % name)
        if wsock is None or not os.path.isfile(p):
            continue
        rep = float(L.get("rep", 1.0)) or 1.0
        y = 300 - k * 220

        scale = nt.nodes.new("ShaderNodeVectorMath"); scale.operation = 'SCALE'
        scale.location = (-700, y); scale.inputs["Scale"].default_value = rep
        nt.links.new(uv_src, scale.inputs[0])
        t = nt.nodes.new("ShaderNodeTexImage"); t.location = (-500, y)
        t.image = _img(p, srgb=True); t.extension = 'REPEAT'
        nt.links.new(scale.outputs["Vector"], t.inputs["Vector"])

        prod = nt.nodes.new("ShaderNodeVectorMath"); prod.operation = 'SCALE'
        prod.location = (-250, y)
        nt.links.new(t.outputs["Color"], prod.inputs[0])
        nt.links.new(wsock, prod.inputs["Scale"])

        if col_sum is None:
            col_sum, w_sum = prod.outputs["Vector"], wsock
        else:
            add = nt.nodes.new("ShaderNodeVectorMath"); add.operation = 'ADD'
            add.location = (0, y)
            nt.links.new(col_sum, add.inputs[0]); nt.links.new(prod.outputs["Vector"], add.inputs[1])
            col_sum = add.outputs["Vector"]
            wa = nt.nodes.new("ShaderNodeMath"); wa.operation = 'ADD'; wa.location = (0, y - 110)
            nt.links.new(w_sum, wa.inputs[0]); nt.links.new(wsock, wa.inputs[1])
            w_sum = wa.outputs["Value"]
        used += 1

    if col_sum is None:
        return 0
    # normalise: the control channels do not necessarily sum to 1
    safe = nt.nodes.new("ShaderNodeMath"); safe.operation = 'MAXIMUM'; safe.location = (350, -150)
    safe.inputs[1].default_value = 1e-3
    nt.links.new(w_sum, safe.inputs[0])
    inv = nt.nodes.new("ShaderNodeMath"); inv.operation = 'DIVIDE'; inv.location = (520, -150)
    inv.inputs[0].default_value = 1.0
    nt.links.new(safe.outputs["Value"], inv.inputs[1])
    norm = nt.nodes.new("ShaderNodeVectorMath"); norm.operation = 'SCALE'; norm.location = (750, 0)
    nt.links.new(col_sum, norm.inputs[0]); nt.links.new(inv.outputs["Value"], norm.inputs["Scale"])
    nt.links.new(norm.outputs["Vector"], bsdf.inputs["Base Color"])
    return used


def apply_to_scene(dataset_dir, verbose=True):
    """Find every terrain material in the scene and rebuild it as a splat."""
    layers_dir = os.path.join(dataset_dir, "terrain_layers")
    man_p = os.path.join(layers_dir, "manifest.json")
    if not os.path.isfile(man_p):
        print("[terrain] no terrain_layers/manifest.json at %s" % layers_dir)
        return
    man = json.load(open(man_p, encoding="utf-8"))
    tiles = man.get("tiles") or {}
    done = 0
    for o in bpy.context.scene.objects:
        if o.type != 'MESH':
            continue
        for ms in o.material_slots:
            m = ms.material
            if not m or not m.use_nodes:
                continue
            img = None
            for n in m.node_tree.nodes:
                if n.type == 'TEX_IMAGE' and n.image:
                    img = n.image.name
                    break
            if not img:
                continue
            hit = re.search(r"(Slice_\d+_\d+)_albedo", img)
            if not hit or hit.group(1) not in tiles:
                continue
            n = build_terrain_splat(m, tiles[hit.group(1)], layers_dir)
            if n:
                done += 1
                if verbose:
                    print("[terrain] %-28s -> MicroSplat, %d layers (%s)" % (m.name[:28], n, hit.group(1)))
    print("[terrain] rebuilt %d terrain material(s) from control maps + tiled layers" % done)
