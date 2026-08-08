## Contents

- [What this covers](#what-this-covers)
- [The one recurring bug](#the-one-recurring-bug)
- [Channel semantics per material role](#channel-semantics-per-material-role)
- [Coordinates, shear and instancing](#coordinates-shear-and-instancing)
- [Characters, weapons and bone space](#characters-weapons-and-bone-space)
- [Grass](#grass)
- [Terrain](#terrain)
- [Walking a character on the ground](#walking-a-character-on-the-ground)
- [Rasteriser tricks that do not survive a path tracer](#rasteriser-tricks-that-do-not-survive-a-path-tracer)
- [Scripts](#scripts)
- [Failure signatures](#failure-signatures)

---

## What this covers

Rebuilding a shipped pack inside Blender: map geometry, materials, terrain, grass, characters,
weapons and animation. The formats themselves are documented in the sibling references; this file
is only about the places where a faithful reader still gets the wrong picture, because a channel
means something other than its name.

The renderer in `viewer/` is the reference implementation. Where this document and the shader
disagree, the shader is right.

## The one recurring bug

Every visual defect found while writing these importers was the same shape: **a channel that means
one thing in the pack and another to the importer**. None were crashes, none were caught by a
validator, and each was invisible on the surfaces where the two interpretations happen to agree.

| Channel | Looks fine on | Wrong on |
|---|---|---|
| UV V origin | tiling asphalt, concrete, terrain | any distinctive atlas: a vehicle body, a number plate |
| water coverage | nothing | every road under a puddle decal |
| SoftCutout coverage | nothing | every road, parking bay and yard slab |
| grass card V | nothing | every blade, tips buried and cut ends waving |
| water mask channel | a constant-alpha atlas | every puddle whose alpha is real |
| vert-paint layer count | a single-layer surface | every road, yard and painted slab |
| `specMap` | matte surfaces | anything painted, worn or wet |

The lesson is procedural: when a surface looks wrong, find the channel and compare it against the
shader before touching geometry or lighting. Three of the four above were first mistaken for
geometry or lighting problems.

## Channel semantics per material role

`materials.json` gives every material a `role`. The role decides what the ALBEDO ALPHA means, and
it is not opacity in three of the six cases.

| role | alphaMode | what the albedo's alpha actually is | where coverage comes from |
|---|---|---|---|
| `opaque` | OPAQUE | smoothness, when `roughnessFromAlbedoAlpha` | nothing; fully opaque |
| `cutout` | MASK | real coverage | alpha, tested against `alphaCutoff` |
| `glass` | BLEND | real coverage | alpha |
| `decal` | BLEND | real coverage, **unless** the material carries `vp.softCutout` | alpha, or vertex paint (below) |
| `water` | BLEND | usually a REAL mask; constant on some atlases | the albedo's alpha, or its luma when that alpha is constant |
| terrain | OPAQUE | unused | the MicroSplat control maps |

Two special cases carry the surface of the whole map between them:

**SoftCutout decals** (`vp.softCutout = [alphaStrength, cutoff, alphaHeight]`) are the roads,
parking and yard slabs. On Interchange 133 materials carry those params but only 117 are
`role == "decal"`; the other 16 are opaque Vert-Paint SOLIDS and must have NO alpha gate at all.
Gate on the role, not on the presence of the params, or those sixteen ground and courtyard
materials come in full of holes or vanish. Their coverage is per-vertex `COLOR_0.a`, and
their texture alpha is a SMOOTHNESS map:

```
coverage = clamp(COLOR_0.a * alphaStrength - (cutoff - alphaHeight), 0, 1) * COLOR_0.a
```

`alphaStrength` is written only when the material authors it. ABSENT and EXPLICIT ZERO are
different render paths, and conflating them produces the invisible-parking-lot / hard-dirt-road
pair.

**Water decals** do NOT have a fixed mask channel. The renderer probes the albedo's alpha and
only falls back to luma when that alpha is (near) CONSTANT:

```
mask = tex.a,  or luma(tex.rgb) when (alpha_hi - alpha_lo) < 13/255      # stride-101 sample
coverage = clamp(mask * 1.52, 0, 1) * smoothstep(0.015, 0.10, that) * tint.a
```

The 1.52 is the authored fade strength and the smoothstep suppresses the near-zero tail, which is
what stops the decal quad's own boundary becoming visible. `City_puddle_atlas` is the constant-alpha
case that motivates the luma path, but it is the exception: all four of Interchange's water
textures carry a real varying alpha (ranges 0..174, 0..255, 0..251, 0..240), so hardcoding EITHER
channel is wrong. Hardcoding red draws every one of those puddles at the wrong shape and a fraction
of its coverage; hardcoding alpha lays a translucent sheet over the road wherever the atlas really
is constant. Keep the authored tint too: the records carry materially different colours and alphas,
including 0, 0.297, 0.5804 and 0.747.

Untextured `water` is not a puddle at all. It is the sea, lakes and treatment basins, which the
renderer keeps in the OPAQUE pass and shades as a dark teal body.

**Vert-Paint materials are a THREE-layer blend**, not one tiled texture, and 138 of them pave
Interchange. Weights are the heights mask times the mesh's `COLOR_0.rgb`, raised to the material's
blend exponent and normalised, with layer 0 as the base and as the fallback when the mask is empty
(an unpainted mesh or the SOLID variant) so a near-zero mask does not wash out to an even mix. Each
layer carries its own ST, and un-baking it from the base UV is V-FLIP AWARE: the assembler baked
`v' = 1 - (v*sy + oy)`, so the naive `(uv - offset) / scale` shifts a layer by up to half a tile.

Everything else: normal maps are DirectX convention, so invert green. `_SpecMap` is a GLOSS map,
the inverse of roughness, and it is PER-TEXEL where the material's scalar roughness is one number
for the whole surface; 2,492 Interchange materials ship one, so ignoring it makes every painted,
worn and wet surface uniformly rough. UV tiling and the V flip are already baked into the vertex UVs, so
`materials.json.uvXform` is reference only and must not be applied again.

## Coordinates, shear and instancing

The pack world is right-handed, Y-up, metres, and the Unity handedness conjugation is ALREADY
APPLIED. Do not re-apply it. The only change a Z-up tool needs is:

```
blender_xyz = (x, -z, y)          # +90 deg about X, determinant +1, applied ONCE
```

**Blender objects cannot hold shear.** They store location/rotation/scale, so assigning a sheared
3x3 silently re-orthogonalises it and misplaces the geometry. About 4% of instances carry
legitimate shear. Detect it (normalise the columns, `|dot|` between any pair > 0.02) and bake those
instances to world geometry with an identity transform. That is the same sanctioned exception the
pipeline's own assembler uses.

**Pre-baked geometry ships an identity affine**, so its translation is the origin no matter where
the object actually is. Every projected decal is like this. A spatial filter on the affine
translation drops all of them; derive the position from the transformed mesh centre instead. They
are not flagged `BAKED_WORLD` either, so the flag cannot be trusted to find them.

## Characters, weapons and bone space

Blender bones must point along their own local +Y, so an importer rotates every bone by an
axis-correction `q4` when building the rest pose:

```
rest_blender = bind_world_pack @ q4
```

Anything that attaches where the ENGINE attaches must undo it. The runtime parents a weapon to
`Weapon_root` with an IDENTITY transform, so the correct world matrix is:

```
weapon_world = armature.matrix_world @ pose_bone.matrix @ q4_inverse
```

Using `pose_bone.matrix` alone rotates the weapon by the bone-axis convention and it reads
sideways in the character's hands. `import_eftchar.py` publishes `q4` on the armature as
`eft_q4` so consumers do not have to rediscover it.

Two more: Blender's `Quaternion()` is WXYZ and the packs are XYZW. Blender bone-parents a child to
the bone's TAIL, not its matrix, so let Blender solve the local basis from the world matrix you
want rather than computing an offset.

## Grass

`grass.bin` is a headerless stride-24 array; the layout is in the pack-format reference. The card
the viewer builds is a **three-plane star** (quads at 0, 60 and 120 degrees), half-width 0.42 m,
height 0.9 m, and it is alpha-tested rather than camera-facing.

Its UVs are authored `v = 1` at the BASE and `v = 0` at the tip, for the same top-left origin as
every other texture in the pack. A Z-up, bottom-left-origin tool must flip them or every blade
grows upside down.

Interchange ships 3.26 million clumps. The viewer culls them by screen size; a static build has to
select a radius instead, or looking horizontally stacks tens of metres of cards into a wall.

## Terrain

The pack ships a baked albedo slice per tile: 4096 px over a 700 m tile, which is 5.9 texels per
metre and visibly soft close up. It also ships everything needed for the real MicroSplat splat,
which is what the viewer draws:

```
weight_i = ctrl[layer.ctrl][layer.chan]        sampled with the tile's 0..1 UV
albedo   = sum(weight_i * layer_i(uv * rep_i)) / sum(weight_i)
```

Weights are packed four to a control map (`layer.ctrl` picks the map, `layer.chan` picks R/G/B/A)
and each layer tiles at its own `rep`. Grass repeats every 1.76 m, so at 1024 px it carries about
582 texels per metre: roughly a hundred times the baked slice. Normalise by the weight sum, because
the control channels do not have to add to one.

The control maps are DATA. Load them Non-Color, or colour management warps the blend.

## Walking a character on the ground

Do not raycast down from the sky to find the ground. The first thing such a ray meets is a bush
canopy or a container roof, and the character ends up standing metres in the air.

`viewer/src/walk_ground.rs` defines the rule the viewer uses:

> the GREATEST surface Y at (x, z) that is `<= feet_y + step_up`, counting only faces whose
> `n.y/|n| > 0.5`

with `STEP_UP = 0.5 m`. In practice: cast DOWN from `feet_y + STEP_UP`, not from above the map, and
accept only near-horizontal faces. Carry the previous sample's height forward as `feet_y` so the
walk has continuity, which is what makes multi-storey and kerbs come out right.

Patrol waypoints in `gamedata.json` already sit on the navigable surface, so they are a good
starting height and a good fallback when no surface resolves.

## Rasteriser tricks that do not survive a path tracer

Some of what the viewer does is a shading trick, not data, and copying it into Cycles makes things
worse rather than better.

- **Grass up-normals.** The viewer bakes a straight-up normal into every grass card so the blades
  take the same light as the ground. A path tracer treats a shading normal that faces away from the
  viewer as backfacing and returns black. Use the true geometric normals and get the soft look from
  a diffuse/translucent mix instead, which is what thin foliage actually does with light.
- **Grass shadows.** The viewer keeps grass out of the shadow pass entirely. Left casting in a path
  tracer, a dense field self-shadows into a dark mat.
- **World volumes.** A Cycles world Volume Scatter is unbounded: camera rays integrate it to
  infinity and the frame renders black. Atmospherics must be a bounded box containing the shot.

## Two Blender behaviours a fresh implementation will trip on

`Material.blend_method` and `alpha_threshold` are legacy ALIASES of `surface_render_method` under
EEVEE Next (4.2+). Assigning `'OPAQUE'` or `'CLIP'` is a silent no-op that leaves the material on
`HASHED`, so a MASK alpha test has to live in the node graph as a GREATER_THAN on the computed
alpha rather than in a material property.

`COLOR_0` is packed `unorm8x4`, and Blender converts colour attributes on write. The attribute must
be CORNER-domain `BYTE_COLOR` written through `color_srgb` to round-trip the bytes; writing through
the linear `color` property changes the numeric values, which are blend WEIGHTS here, not a colour.
That silently moves Vert-Paint layer selection and SoftCutout edges.

## Scripts

`tools/blender/` holds runnable importers; each is standalone and imports nothing from this repo.

| script | what it does |
|---|---|
| `import_eftpack.py` | map geometry, materials, terrain slice, shear baking, region filter |
| `import_eftchar.py` | armature, skinning, clips as Actions; publishes `eft_q4` |
| `import_eftweap.py` | weapon mesh and bone attachment |
| `import_eftgrass.py` | the grass field from `grass.bin` |
| `terrain_splat.py` | rebuilds terrain materials as the real MicroSplat blend |
| `make_sky_equirect.py` | cubemap faces to an equirectangular world texture (runs OUTSIDE Blender) |
| `example_scene.py` | end-to-end: map, terrain, grass, character walking a patrol, camera, Cycles |

## Failure signatures

| Symptom | Cause |
|---|---|
| Every texture upside down, obvious only on a distinctive atlas | UV V not un-flipped for a bottom-left origin |
| The road looks like glass, a large translucent sheet over it | water coverage read from alpha instead of RED |
| Asphalt eaten away in patches | SoftCutout coverage read from texture alpha, which is smoothness |
| Grass blades upside down, tips in the soil | grass card V copied verbatim from the viewer |
| Grass renders as a black mat | up-normals copied into a path tracer, or grass left casting shadows |
| Geometry misplaced and skewed, roughly 4% of instances | a sheared matrix assigned to an object that cannot hold shear |
| A whole class of objects at the world origin | spatial filter applied to the affine translation of pre-baked geometry |
| The rifle is sideways in the character's hands | the bone-axis correction was not undone |
| The character stands in mid-air on bushes | ground found by raycasting down from the sky |
| Terrain soft and smeared close up | the baked albedo slice used instead of the MicroSplat splat |
| The whole frame renders black in Cycles | an unbounded world Volume Scatter |
| Puddles the wrong shape, or a sheet over the road | the mask channel hardcoded instead of probed |
| The sea invisible | untextured water treated as a puddle with no mask |
| Roads one flat tiled texture instead of a gravel/sand mix | the Vert-Paint splat collapsed to layer 0 |
| Painted and wet surfaces uniformly rough | `specMap` never bound; only the scalar roughness read |
| MicroSplat weights mirrored against the terrain | the control-map V flipped a second time |
