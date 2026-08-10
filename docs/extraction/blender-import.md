## Contents

- [What this covers](#what-this-covers)
- [The one recurring bug](#the-one-recurring-bug)
- [Two modes, one builder](#two-modes-one-builder)
- [Channel semantics per material role](#channel-semantics-per-material-role)
- [Legacy glass: glassTRS is four lobes, not a Principled](#legacy-glass-glasstrs-is-four-lobes-not-a-principled)
- [Water is two materials wearing one role](#water-is-two-materials-wearing-one-role)
- [Parallax: one step, not thirty-two](#parallax-one-step-not-thirty-two)
- [Vert-paint: six divergences worth the node count](#vert-paint-six-divergences-worth-the-node-count)
- [Detail albedo: mean-neutralise it or the rock goes flat](#detail-albedo-mean-neutralise-it-or-the-rock-goes-flat)
- [Coplanar overlays: a 6 mm lift replaces the depth push](#coplanar-overlays-a-6-mm-lift-replaces-the-depth-push)
- [Import switches and the run summary](#import-switches-and-the-run-summary)
- [Coordinates, shear and instancing](#coordinates-shear-and-instancing)
- [The region filter tests an AABB, never a centre](#the-region-filter-tests-an-aabb-never-a-centre)
- [Characters, weapons and bone space](#characters-weapons-and-bone-space)
- [Grass](#grass)
- [Terrain](#terrain)
- [Walking a character on the ground](#walking-a-character-on-the-ground)
- [Rasteriser tricks that do not survive a path tracer](#rasteriser-tricks-that-do-not-survive-a-path-tracer)
- [Foliage transparency is a whole-path budget, not a quality knob](#foliage-transparency-is-a-whole-path-budget-not-a-quality-knob)
- [Blender behaviours a fresh implementation will trip on](#blender-behaviours-a-fresh-implementation-will-trip-on)
- [Walking a character somewhere real](#walking-a-character-somewhere-real)
- [Filming it](#filming-it)
- [Washed-out colour is an EXPOSURE symptom, not a material symptom](#washed-out-colour-is-an-exposure-symptom-not-a-material-symptom)
- [Lighting an external render: the two numbers the pack cannot give you](#lighting-an-external-render-the-two-numbers-the-pack-cannot-give-you)
- [Grading: shoot flat, grade after](#grading-shoot-flat-grade-after)
- [Verifying against the viewer](#verifying-against-the-viewer)
- [Practical lights](#practical-lights)
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
| `specMap` read as a gloss TEXTURE | bright saturated paint, washed to grey | 2,492 materials; 189 where it is the albedo file |
| `COLOR_0` read back through a Color Attribute node | an unpainted mesh | every weight and every SoftCutout edge: byte 128 arrives as 0.2159, not 0.5020 |
| water `tint.rgb` | nothing | 173 puddle materials, where the tint IS the wet-asphalt albedo and the texture is only a mask |
| parallax height GREEN | a flat wall | 139 relief materials: the channel is DEPTH (`1 - g`), so `g` pops the relief out instead of in |
| vert-paint layer-0 fallback taken untinted | a white layer-0 tint | 471 of 1,127 vp materials carry a non-white one |

The lesson is procedural: when a surface looks wrong, find the channel and compare it against the
shader before touching geometry or lighting. Three of the four above were first mistaken for
geometry or lighting problems.

## Two modes, one builder

`tools/blender/example_scene.py` serves the repo's two deliberately conflicting goals from one code
path. `MODE` at the top of that file selects which:

| `MODE` | The authority is | Specified in |
|---|---|---|
| `"game"` (the default) | the viewer. Fitted sun and sky, the pack's own cubemap, every material as `gpu_draw.wgsl` reads it, finished with the game's grade LUT | [game-parity.md](game-parity.md) |
| `"photoreal"` | a photograph. Departs from the viewer wherever a camera would, and labels every departure | [photorealism.md](photorealism.md) |

```
blender --python tools/blender/example_scene.py               # honours MODE
EFT_MODE=photoreal blender --python tools/blender/example_scene.py
```

`build()` takes the mode as an argument and falls back to `$EFT_MODE`, then to the `MODE` constant,
so `build("photoreal")` works from a live session too. An unknown name is a hard exit, not a
silent default.

**Nine things are IDENTICAL on both paths, and that is the point of one builder.** Nothing above
the fragment's final colour differs, so the diff between the two images is readable as a diff
between two dicts:

1. geometry, instancing, shear baking and the region filter;
2. UVs and the V-flip;
3. nav routing (`nav_route.py`), because a route through a wall is not a look decision;
4. the camera solve (`cine_camera.solve_follow_camera`), including its Viterbi cost weights;
5. the 6 mm coplanar decal lift;
6. every material channel semantic in this file;
7. the fitted `SUN_ENERGY = 6.90` / `SKY_STRENGTH = 2.35` pair;
8. the practical lights and the MicroSplat terrain splat;
9. `scene.cycles.transparent_max_bounces = 256`, which is correctness rather than quality
   ([Foliage transparency](#foliage-transparency-is-a-whole-path-budget-not-a-quality-knob)).

`import_eftgrass(wind=True)` is also on in BOTH modes and is deliberately not in the switch: it is
the viewer's own WavingGrass vertex stage driven by `grass_sidecar.json`'s own constants
(`strength 1.0, amount 0.157, speed 1.0`), which the Blender path used to discard entirely. Turning
it on CLOSES a parity gap rather than opening one.

**What the `MODES` dict actually changes.** Every entry is either derived from the pack or invented
and labelled:

| key | `"game"` | `"photoreal"` | wired through |
|---|---|---|---|
| `atmosphere` | `"uniform"`: one Volume Scatter density, 0.0016 | `"falloff"`: exponential in height, 4.0e-4 at the ground with a 40 m scale height | `_atmosphere_nodes` |
| `haze_post` | `False`, so the box is PATH TRACED | `True`: the box is still built and sized, then `hide_render`'d and applied analytically per pixel from the Z pass | `_depth_haze_nodes` |
| `glass_mode` | `"trs"`, the bounded legacy response term for term | `"physical"`, real Cycles transmission on panes the pack already ships as 1.6 to 2.3 mm slabs | `import_eftpack(glass_mode=)` |
| `cavity` | `False`. The game shader has no ambient-occlusion term at any scale | `True`: the normal maps' own Poisson-derived self-occlusion multiplied into Base Color | `import_eftpack(cavity_dir=)`, `bake_cavity.py` |
| `comp` | `False`. The viewer has neither glare nor chromatic aberration | `True`: Fog Glow at strength 0.015 and threshold 0, lateral CA 0.0012 | `_compositor` |
| `aperture_blades` | `0`, Blender's perfect circle | `9`; a 50 mm at f/2.8 is stopped down and no real iris is circular | `solve_follow_camera(blades=)` |
| `samples` | `SAMPLES` = 96 | `SAMPLES * 4.0` = 384 | `scene.cycles.samples` |

The sample multiplier is not a preference. Not tracing the box frees wall clock, and the untraced
frame's marginal cost is 0.2034 s/spp (172 spp 45.7 s, 345 spp 80.9 s), so 384 spp predicts 88.9 s
against the traced frame's 92.1 s and measured 87.8 s. Anything below 4x leaves measured time on
the table.

Two traps live in the parity column rather than the photoreal one:

- **`atmosphere="uniform"` is invented and stays anyway.** `gamedata.json`, `particles.json`,
  `volume.json`, `sky.json` and `manifest.json` were grepped for `fog|haze|atmos|scatter|mist|
  density` and returned zero hits, and `volume.json` is the SH irradiance bake (`"direct": false`),
  not a medium. But `SUN_ENERGY` and `SKY_STRENGTH` were least-squares fitted WITH that box in
  place, so removing it on the parity path without re-solving the pair drops the frame median 16%.
- **`haze_post=False` is part of the same fit.** The parity path traces the box because that is
  what the pair was fitted against, not because tracing is better.

**Both modes shoot FLAT linear EXR**, and the display chains part company afterwards, in
`eft_grade.py`. `build()` prints the exact command for the mode it just built:

```
game       python tools/blender/eft_grade.py frames/ out/ --auto
photoreal  python tools/blender/eft_grade.py frames/ out/ --look agx --auto --meter grey \
                  --no-vignette --lens 50 --grain 15000
```

`eft_grade.py` REFUSES `--lens`, `--grain` and `--meter grey` on `--look game`, because none of
them is what the viewer does and any of them makes the frame undiffable against it. The flags:

| flag | default | what it does |
|---|---|---|
| `--meter` | `highlight` | how `--auto` solves exposure. `highlight` drives p99 to 0.92, parking highlights under the LUT's clip. `grey` meters like a camera: an ISO centre-weighted mean (sigma 0.30 of image HEIGHT) driven onto the scene value the chosen look maps to 18% display grey |
| `--lens` | off | focal length in mm; enables the real `cos^4` falloff of the taking lens, applied in LINEAR before the curve. Cycles renders NO natural vignetting (measured: corner/centre = 1.00000 exactly against a uniform white world), so this is a correction and not a double count. It must never be stacked on the game's authored vignette, which is 4.68x deeper (-2.125 EV at the corner against `cos^4`'s -0.454 EV at 50 mm) and is a different claim about the same lens |
| `--sensor` | `36.0` | sensor WIDTH in mm, for `--lens` |
| `--grain` | off | full-well electron count for photon SHOT noise, `sigma(x) = sqrt(x / N_sat)` on exposed linear. 15000 is roughly ISO 400 on full frame. This is the one INVENTED number in the file, because the pack ships no sensor, so it is opt-in and named after what it is |

The four importer-side switches, with their exact signatures, so a caller can find them:

```
import_eftpack(pack_dir, center=None, radius=None, max_instances=None, with_textures=True,
               lod=0, include_inactive=False, collection_name=None, verbose=True,
               glass_mode="trs", cavity_dir=None)
import_eftgrass(pack_dir, center=None, radius=None, max_clumps=400000, points=None,
                wind=False, collection_name="grass", verbose=True)
solve_follow_camera(scene, arm, f0, f1, step=3, lens=50.0, fstop=2.8, blades=0, verbose=True)
```

Every one of them defaults to what the viewer does, so an existing caller keeps building the parity
image byte for byte. `cavity_dir` pointing at a missing or partial directory is not an error: the
importer skips what it cannot find, so a half-finished bake is a valid input. `blades` is free
(10.56 s at 0 against 10.72/10.45 s at 9 on the real scene, inside run-to-run noise).

### The cavity bake, and why it is derived rather than authored

`bake_cavity.py` produces the only input in the photoreal column that is not already in the pack, so
it is worth saying exactly where it comes from.

**Why it exists.** The game shader has no ambient-occlusion term of any kind at any scale, and the
pack ships no AO map: `gpu_draw.wgsl` perturbs the shading normal and stops there. In a rasteriser
that is a choice. In Cycles it is a hole, because Cycles derives occlusion from GEOMETRY, and a
normal map's relief has no geometry, so nothing shadows anything inside a brick course, a bolt
recess or a tread pattern. **1,016 of the built scene's 1,121 materials carry such a map.**

**Everything is derived.** A tangent-space normal map IS a slope field:

```
p = -nx/nz,  q = -ny/nz              # the slopes the Normal Map node already assumes
laplacian(h) = dp/dx + dq/dy         # solved by FFT, so h is in TEXEL units, mean-free
occ = mean over s in {4, 8, 16, 32} texels of clamp((boxblur_s(h) - h) / s, 0, 1)
cavity = 1 - occ
```

`occ` is local relief measured against progressively wider neighbourhoods: a texel sitting below its
surroundings is occluded by them, in proportion to how far below and how wide the pit. There is no
amplitude knob, no curve and no hand tuning, and the single assumption ("normal-map slopes are per
texel") is the one the Normal Map node makes anyway.

Three things that are easy to get wrong:

- **The green flip matters and is recorded.** Flipping green negates `q`, which mirrors the
  integrated height in `v`, so the crevices come out on the wrong side of every ridge. The pack's
  own `manifest.conversion.normalMapGreenFlip` (true on every shipped pack) is therefore the
  default, and it is written into `cavity.json` so the importer can refuse a mismatched bake.
- **Flat maps are gated out.** Maps whose cavity std is under 0.030 are skipped: painted panels,
  decal normals and poster maps are flat, and their cavity map is a uniform 1.0 that costs a texture
  fetch to do nothing. That drops **178 of 433** maps on interchange, and the gated build measured
  slightly MORE frame local contrast than the ungated one.
- **8-bit output is enough.** Measured bit-identical to a float32 buffer (-1.62% mean frame
  luminance either way), and float32 image buffers in Blender cost 4x the memory and a measured
  235% render penalty against the 8-bit path's 71% at the same low resolution.

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
renderer keeps in the OPAQUE pass and shades as a dark teal body. Both are covered in full under
[Water is two materials wearing one role](#water-is-two-materials-wearing-one-role); note there
that a puddle's SURFACE COLOUR is the record's `tint.rgb`, not the texture.

**Vert-Paint materials are a THREE-layer blend**, not one tiled texture, and 138 of them pave
Interchange. Weights are the heights mask times the mesh's `COLOR_0.rgb`, raised to the material's
blend exponent and normalised, with layer 0 as the base and as the fallback when the mask is empty
(an unpainted mesh or the SOLID variant) so a near-zero mask does not wash out to an even mix. Each
layer carries its own ST, and un-baking it from the base UV is V-FLIP AWARE: the assembler baked
`v' = 1 - (v*sy + oy)`, so the naive `(uv - offset) / scale` shifts a layer by up to half a tile.

Everything else: normal maps are DirectX convention, so invert green. UV tiling and the V flip are
already baked into the vertex UVs, so `materials.json.uvXform` is reference only and must not be
applied again.

**`specMap` is a PROVENANCE NOTE, not a texture to sample.** 2,492 Interchange materials carry one
and it is tempting to bind it as a per-texel gloss map. Do not. The assembler has already reduced
that map to the scalar `roughness` ("roughness from _SpecMap luma"), and the renderer reads only
the scalar, taking per-pixel roughness from the ALBEDO'S ALPHA and never from `specMap`:

```wgsl
var rough = clamp(m.roughness, 0.03, 1.0);                 // gpu_draw.wgsl
if (MAT_FLAG_RFA) { rough = clamp(1.0 - tex.a, 0.06, 1.0); }   // raw tex.a, not albedo.a
```

Binding it looks like an upgrade and is a regression, because for **189** of those materials
`specMap` IS THE ALBEDO FILE. `roughness = 1 - albedo` then makes bright paint glossy: a forklift's
red bodywork came out at roughness 0.24, mirrored the overcast sky and rendered neutral grey -
measured R/G **0.96** against the texture's own **1.43**, where the viewer gives **1.45**. Reverting
to the scalar restored it to 1.38 and cut whole-frame RMS against the viewer from 0.143 to 0.114.
Note also that 2,469 of the 2,492 ALSO set `roughnessFromAlbedoAlpha`, so a `specMap` binding does
not merely add detail - it suppresses the per-pixel path the renderer actually uses.

## Legacy glass: glassTRS is four lobes, not a Principled

EFT's car and storefront glass is the legacy Transparent/Reflective/Specular family. **1,057
materials across the six shipped packs** carry the capture (streets_nav 503, ground_zero 401,
interchange 153; factory_rework, icebreaker and woods have none), and it is ported as a node tree in
`tools/blender/import_eftpack.py::_Importer._glass_trs` (`:1030-1365`), gated at `:1958` and called
from `_build_material` at `:2159-2160`. Two pack-derived probes feed it, `_sky_probe` (`:801-948`)
and `_dom_light` (`:950-1028`), each evaluated once per import and cached. A textured pane with no
normal map ends at **45 nodes** (counted on interchange's `*.0487.glass`): the base albedo Image and
its tint Mix from the generic path, plus 1 Principled, 2 Emission, 1 Transparent, 2 Mix Shader,
2 Add Shader, 14 Math, 18 Vector Math, a Geometry, a Separate XYZ and the Output.

**Gate it on the role, verbatim.** `viewer/src/render/gpu_driven.rs:2013` is
`let glass_trs = mat.glass_trs && mat.role == "glass";`. A record carrying `glassTRS` with any
other role gets NO lane and NO flag and falls through to the ordinary opaque path (interchange ids
1613/2054/2248 are exactly that, and they carry no `reflectColor`/`specColor`/`shininess` at all).
Widening the test invents a response from Unity defaults for materials the renderer never gives
one.

**Why it cannot live on a Principled BSDF.** The shader composes
(`viewer/assets/shaders/gpu_draw.wgsl:1661-1664`):

```wgsl
out.rgb = (apply_fog(lit) * glass_a + spec_g + refl_g + em_rgb) * cov;
out.a   =  glass_a * cov;
```

which over a background B resolves to `glass_a*lit + spec + refl + em + (1-glass_a)*B`. **Only the
diffuse is scaled by coverage.** Everything on a Principled is scaled by its Alpha input, so at
interchange's median `tint.a` = 0.749 the reflection and the glint come back 25 to 50% short, which
is the flat dark square this block exists to prevent. The Cycles twin is that composition term for
term (`:1311-1354`): `AddShader( MixShader(Transparent, body, trs_a), env + blinn )`, then a second
`MixShader` against Transparent driven by the hole test. The Principled itself is neutralised to a
pure diffuse body (Metallic 0, Roughness 1, IOR 1, Specular IOR Level 0, Specular Tint white, Coat
and Sheen 0, `:1301-1309`) because the shader's own `select()`s throw its specular away: `:1651`
replaces `spec_rgb` with the Blinn lobe and `:1638` replaces `refl_rgb` with the TRS environment.
`lit` (`:1460`) is `albedo.rgb * (irradiance + sun_diffuse)` with no roughness dependence at all.

**Coverage carries `tint.a` TWICE.** `albedo.a = tex.a * tint.a` (`gpu_draw.wgsl:1237`) and then
`trs_a = clamp(albedo.a * max(trs_opac, 0.03), 0, 1) * m.tint.a` (`:1602`). Building coverage from
the raw `tex.a` renders interchange glass `1/0.749` = **1.34x too opaque**. The chain is four Math
nodes at `import_eftpack.py:1133-1149`.

Four constants that are easy to get wrong (`import_eftpack.py:1105-1130`), all mirroring
`gpu_driven.rs:2296-2333`:

| value | rule | population |
|---|---|---|
| `reflectColor` / `specColor` | Unity's legacy UI defaults are **grey 0.5**, not white, and both are quantized through a byte (`round(clamp(v,0,1)*255)/255`) | 0 of 1,057 lack either |
| `reflectCube` | folded into `reflectColor` so the lane IS the game's reflection radiance, and sets `MAT_FLAG_GLASS_CUBE` | 10 of 1,057 |
| `shininess` | ABSENT defaults to **0.078** (Blinn power 9.997). `or 0.0` gives power 1.28 | 4 of 1,057 authored none |
| `opacityScale` | quantized through `glass_refl`'s top byte, `round(clamp(v,0,8)/8*255) * 8/255`. 1.0 lands on 1.003921, and **0.0 lands on 0** and is then floored to 0.03 by the shader's own `max()`, which is what makes those panes near-holes | 21 of 1,057 ship 0.0; 33 ship anything other than 1.0 |

### Both additive lobes are BOUNDED VALUES, and neither is a BSDF

This is the load-bearing property of the whole port, and it is the one an obvious "improvement"
undoes. The family's reflection input is `texCUBE(_Cube)`: one lookup in an LDR IMAGE, so it is
bounded [0,1] and **`refl_g` can never exceed `_ReflectColor`, at any pixel, ever**. The viewer has
no cube for these materials (`RenderSettings` ships `customReflection=null` on every level and there
are zero ReflectionProbes), substitutes the baked SH volume, and then compresses it back into that
domain (`gpu_draw.wgsl:1632` and `:1637-1638`):

```wgsl
let sh_ldr = sh_env / (vec3<f32>(1.0) + sh_env);
let trs_env = select(sh_ldr * trs_refl, trs_refl, (m.flags & MAT_FLAG_GLASS_CUBE) != 0u) * fresnel_v;
```

The shader's own comment records what happens when the bound is violated: scene irradiance is HDR,
about 2.0 toward open sky, and it "washed panes to milk over their dark interiors" (bisected there:
diffuse only 0.11, plus reflection 0.76).

An earlier version of this importer built both additive terms as BSDFs, `Diffuse BSDF` with
`Color = reflectColor * fresnel` for the environment and a glossy lobe for the Blinn term, and let
Cycles trace the world into them. **A BSDF cannot be given that bound at any node.** A BSDF's input
is the radiance the integrator traces into it, that radiance does not exist until after the BSDF has
been evaluated, and nothing downstream of a shader socket is arithmetic, so `x/(1+x)` has nothing to
apply to. Measured on an interchange facade at head-on, the traced version came back **5.4x too
light** through the game's grade and **5.5x too light relative to the opaque wall beside it** (in
Atlas that pane is DARKER than the wall; it was rendering 2x brighter). 86% of the pane's radiance
entered through the "specular" node and 89% of that was world, not sun: a flat sky wash arriving
through a lobe the game only ever points at one light.

So neither lobe is traced now. Both are evaluated as VALUES and emitted, which is what makes the two
Reinhards expressible and the ceiling structural rather than a tuned multiplier:

| term | shader | Cycles twin | ceiling |
|---|---|---|---|
| environment | `_ReflectColor * fresnel_v * sh_env/(1+sh_env)` (`:1627-1638`) | Emission, colour = the same product built from an SH probe (`:1201-1253`) | `< _ReflectColor` componentwise. On interchange's dominant pane (`reflectColor` 0.1618, 87 of 153) that is **0.0065 head-on** and 0.1618 at pure grazing |
| Blinn | `sun_ldr * _SpecColor * pow(NdotH, shin*128) * NdotL * albedo.a` (`:1643-1652`) | Emission, colour = `_SpecColor * sun_ldr` constant, Strength = the analytic weight (`:1255-1299`) | peak `<= _SpecColor * sun_ldr`, and `sun_ldr = rad/(1+rad) < 1` |

**The Blinn exponent is used directly, and the GGX conversion is retired with the BSDF.**
`pow(NdotH, n)` is un-normalized: it peaks at `_SpecColor` and integrates to roughly
`SC*2*pi/(n+2)`, while a Cycles distribution is energy-normalized and integrates to about `SC`,
an order of magnitude hotter on the 89 shininess-1.0 panes interchange ships. Evaluated as a value
there is no normalization to fight. The `roughness = sqrt(2/(shin*128+2))` conversion
`gpu_driven.rs:2330-2333` performs is a dead lane (see below), and the `** 0.25` perceptual-roughness
correction a Glossy socket needed (Roughness squares to GGX alpha, so handing it alpha squares it
twice) went out with it. Anyone reintroducing a Cycles distribution here needs both back.

### The environment probe: SH evaluated along R, not a traced or sampled sky

`_sky_probe` (`:801-948`) returns four constants, `(E0, L1x, L1y, L1z)`, with the shader's own
reconstruction weights already folded in (`:938-940`), so the graph is 12 nodes: a Separate XYZ,
three scaled vector terms, three adds, the ambient-floor max, `1 + E`, the Reinhard divide, the
multiply by `reflectColor * fresnel` and the Emission (`:1201-1253`). It runs the shader's OWN band-1
reconstruction (`gpu_draw.wgsl:336-342`) along the mirror vector `R = reflect(-V, N)`:

```
e = 0.282095*c0 + 0.325735*(c1*n.y + c2*n.z + c3*n.x)      # Blender axes
sh_env = max(e, 0.03)                                      # ambient_floor, gpu_draw.wgsl:1359, :1499
refl    = reflectColor * fresnel_v * sh_env/(1 + sh_env)
```

with the coefficients projected off the world's equirect sky instead of read out of `volume.bin`,
because a node graph cannot carry a probe volume.

**Do not "simplify" this to an Environment Texture sampled along R**, which is the more literal
`texCUBE` twin. Measured, it is 3x too dark at grazing: an equirect's lower hemisphere is ground and
a pane seen edge-on mirrors the horizon, where the image is near black (linear luma 0.060 in the
horizon band) while the viewer's probe is an IRRADIANCE reconstruction, cosine-convolved and
therefore never dark, and reads 0.58 there. Reconstructing the way the shader does removes that
whole class of mismatch, and the result is still a value, so the bound survives.

The units are checked, not assumed. The projection reads the world's Background strength when there
is one, falls back to `SKY_STRENGTH_FALLBACK = 2.35` (`:136`, the same least-squares-solved number
as [the sun/sky fit](#lighting-an-external-render-the-two-numbers-the-pack-cannot-give-you)), and
scales by `gi_intensity` from `volume.json` (`sh.vol_min.w` in the shader, `:1499`; 1.0 on every
shipped pack). At 2.35 the projection lands on the volume it stands in for. Measured on interchange:

| quantity | projected sky | volume's measured p50 |
|---|---|---|
| DC only, direction-independent luma | 0.729 | 0.740 |
| most-lit direction (up) | 1.452 | 1.51 |

Within 4% with no fitted term. The importer logs both numbers next to the volume's on every run
(`:943-946`); a run whose DC luma is far off those is a mis-set world, not a material bug.

Three details that are easy to get wrong when re-implementing the projection (`:897-940`):

- Blender's `image.pixels` buffer is BOTTOM-UP, and `make_sky_equirect.py` writes row 0 = straight
  up, so the rows are reversed before projecting.
- For a byte sRGB image `pixels` hands back the STORED bytes, not the linear decode the shader graph
  sees (verified: mean luma 0.5103 out of Blender against 0.5103 sRGB-encoded / 0.3327 linear off
  disk). It is decoded with the exact piecewise transfer function.
- PROJECTION uses `0.488603` on the L1 band (the raw SH basis, `:925-927`); RECONSTRUCTION uses
  `0.325735` (the basis times the cosine-convolution ratio, `:940`), which is what
  `sh_irradiance_hw` applies at `gpu_draw.wgsl:336`. Using one weight for both misscales the whole
  directional band by 1.5x and tilts the up/down gradient the reflection is made of.

When the scene has no equirect at all the probe degrades to the flat world colour and warns
(`:931-937`): a uniform environment of radiance L has `c0 = L*4pi*0.282095` and the reconstruction
collapses to exactly L, so **the bound still holds and only the gradient is lost**. `_sky_probe`
also loads the pack's own `shared/sky/*_equirect.png` (preferring `NatureCubemap`) when the world is
not built yet, which is the NORMAL case: `example_scene.py` builds the world AFTER the map, so at
material-build time there is usually nothing to read.

**When `reflectCube` is set the probe is skipped entirely.** `MAT_FLAG_GLASS_CUBE` makes the viewer
use the material's own extracted `_Cube` mean as a CONSTANT, so the Cycles twin collapses those 12
reconstruction nodes to a single Emission carrying `reflectColor * fresnel` (`:1195-1200`). 10 of
1,057 take this branch; 0 of interchange's 153.

### The Blinn lobe's one light comes out of volume.bin, and it is not the sun

`_dom_light` (`:950-1028`) collapses `sh_dominant_light` (`gpu_draw.wgsl:467-514`), which the viewer
evaluates per pixel, to one pack-wide direction and radiance: the `|L1|`-weighted mean direction and
the componentwise MEDIAN radiance over the probes `volume_valid.bin` marks valid, then
`sun_ldr = rad/(1+rad)` (`:1647`). It is derived from the pack at import time, so it tracks a
re-bake, and the Y-up to Z-up mapping is the same `(x, y, z) -> (x, -z, y)` the geometry takes.

**`volume.json` records `"direct": false`.** The bake is sky visibility plus shadow-tested
practicals plus bounce, so the dominant direction is the SKY, not the sun. Measured on interchange
over 507,617 valid probes: `L = (-0.002, 1.000, -0.008)` in pack space, i.e. all but straight up
(logged as `(-0.002, 0.008, 1.000)` in Blender space), with 85% of valid probes inside 26 degrees of
up, radiance `(0.957, 0.958, 0.960)` giving `sun_ldr` `(0.489, 0.489, 0.490)`, and a median cosine
against `volume.json`'s own `sun_dir` of only 0.797. Aiming this lobe at `sun_dir` instead lights
facade panes the viewer leaves dark: a vertical pane's `NdotL` against an UP dominant is about zero,
which is exactly why Atlas's facade glass carries almost no Blinn term. With no readable
`volume.bin` the fallback is `sun_dir` and `sun_ldr = 0.5`, the Reinhard image of radiance 1.0 and
the middle of the domain the bound maps onto, and it warns.

The lobe is masked by `albedo.a`, which doubles as the gloss mask in the legacy convention
(`gpu_draw.wgsl:1649`), and `H = normalize(V + L)` is built from the Geometry node's Incoming
(`:1262-1268`).

### The rest of the tree

**Fresnel gates the environment ONLY.** Schlick `0.04 + 0.96*(1-NdotV)^5` on the normal-mapped N
(`gpu_draw.wgsl:1424` used at `:1637`), built by hand at `import_eftpack.py:1161-1178`, never on the
Blinn lobe. Principled's own IOR-1.5 fresnel is a different curve, ramps the sun-tinted term too,
and was being applied before the normal map even existed. Only **12 of the 1,057** carry a normal
map, so for the other 1,045 the fresnel gate and the mirror vector run on the geometric normal.

**The hole is a Transparent gate over the WHOLE tree, not an Alpha value.** `gpu_draw.wgsl:1603-1605`
discards below `trs_a < 0.03`: a genuine hole in the pane, not a dark smoothness spot. A hard
discard has no Cycles equivalent, but mixing the entire tree against a Transparent BSDF does
(`import_eftpack.py:1150-1155`, `:1351-1354`). Dropping the Alpha alone leaves the additive lobes
alive and the empty pane still mirrors the sky as a ghost.

**The roughness lane is DEAD for TRS glass**, so the RFA condition excludes it
(`import_eftpack.py:2013-2020`, `and not glass_trs`). `gpu_driven.rs:2330-2333` still computes a
roughness for those materials and the shader never reads it, because `:1638` and `:1651` select the
TRS environment and the Blinn lobe over `refl_rgb`/`spec_rgb`. 0 of the 1,057 carry
`roughnessFromAlbedoAlpha` anyway (the assembler force-clears it), but relinking Roughness there
would fight the graph built later. Note that the viewer's env blur is a fixed SH probe INDEPENDENT
of shininess, which is what the reconstruction above is, so there is nothing for a roughness to
drive even in principle.

**Emissive rides OUTSIDE the coverage mix.** `em_rgb` is added outside the alpha scale in the
shader, while a Principled's Emission sits inside the Mix and would be coverage-scaled. 0 of the
1,057 carry one; the separate lobe is built anyway (`:1326-1349`) and `_glass_trs` returns a flag so
the generic emissive block at `:2162-2187` does not add a second copy.

### What is still approximated, and is not to be "fixed"

- **`(1.0 - shadow_event)` on both additive terms** (`gpu_draw.wgsl:1638`, `:1649`), a cascaded
  shadow-map contact correction applied to specular. An Emission takes no shadow ray, so neither
  lobe is occluded here (the old BSDFs were, for free). Both are bounded and both fall to near zero
  on a pane whose normal faces away from the dominant light, so the exposure is a shaded pane still
  carrying its `<= reflectColor * fresnel` sky term. Re-attaching a BSDF to recover the occlusion
  costs the bound, and that is the bug this whole block exists to prevent.
- **The environment probe is POSITION-INDEPENDENT.** The viewer reads the SH volume AT the pane, and
  that volume holds a hard indoor/outdoor cliff right at the glass (measured around
  `Nikitskaya_2_Outdoor_Glass_04`: outdoor probes 1.2 to 2.1, indoor 0.01 to 0.04, a 40x step across
  one cell, `gpu_draw.wgsl:360-369`), which darkens interior glass. One set of pack-wide constants
  cannot. An interior pane therefore mirrors the outdoor sky, but only up to `reflectColor *
  fresnel`, 0.0065 head-on on interchange's dominant glass.
- **Azimuth of the two horizontal L1 terms.** The projection uses `make_sky_equirect.py`'s own
  `(u, v) -> direction` map, that file's contract for the images it writes. If Blender's sampler
  disagrees about where `u = 0.5` points, those two terms rotate. The sky is an up/down gradient
  (measured on interchange, the up coefficient `(0.662, 0.733, 0.810)` against `(-0.150, -0.148,
  -0.134)` and `(-0.134, -0.124, -0.102)` horizontally, roughly 5x), so the DC and the vertical
  term, which carry the reflection, are untouched either way.
- **`apply_fog` is applied to the DIFFUSE ONLY** (`gpu_draw.wgsl:1661`), leaving reflection,
  specular and emissive unfogged. This importer builds no fog; if one is ever added, a volume fogs
  all four uniformly. Do not "fix" the asymmetry by hand.
- Realtime point/spot spec is DROPPED for TRS glass in the viewer (`:1651` overwrites the
  accumulated `spec_rgb`, which already contains the realtime lights' GGX from `:1488`). It is
  dropped here too now that the lobe is analytic and sees only the dominant light. That moved
  TOWARD the viewer and away from Cycles, and it is deliberate.

## Water is two materials wearing one role

`role == "water"` covers two materials with nothing in common. Across the six shipped packs:
**173 TEXTURED puddle films** and **17 UNTEXTURED deep bodies** (streets_nav 105/7, ground_zero
43/6, interchange 10/1, woods 7/2, factory_rework 6/1, icebreaker 2/0). Textured water is a thin
film in the BLEND pass; untextured water is the sea, and the viewer keeps it OPAQUE.

### Puddles

Coverage is the probed mask chain already described above. Three scalars come from the shader and
one classification comes from the geometry.

- **Roughness floors at 0.10.** `gpu_draw.wgsl:1430` then `:1434`: `clamp(m.roughness, 0.03, 1.0)`
  floored at 0.10 for `MAT_FLAG_WATER`. All 190 water records in all six packs ship 0.05, so the
  renderer always draws exactly 0.10 (`import_eftpack.py:1526-1527`).
- **IOR 1.333, and the game's fresnel is deliberately not rebuilt.** IOR 1.333 gives F0 = 0.02,
  which is the same Schlick the deep branch hand-codes at `:1770`. The game's `fr = (1 -
  NdotV*0.354)^2` is 0.417 head-on, roughly 21x a real water surface, and a LayerWeight-driven mix
  is only correct for CAMERA rays: the puddle seen in a mirror or on a second bounce gets the wrong
  weight and the shader stops being reciprocal. `fr`, `refl` and `spec_rgb` are all stand-ins for
  an environment the forward pass cannot see, and Cycles traces the real sky. **Expect puddles to
  read darker head-on than the viewer and to brighten at grazing angles; that difference is the
  game's authored sheen, not a porting error.**
- **MATTE vs REFLECTIVE is a per-material classification the GEOMETRY decides**, so it does have to
  be ported. `Water Deferred Decal` serves both real puddles and stretched wet-ground / tire-trail
  floor decals, and the only discriminator is world metres per texture repeat: a puddle maps at a
  few m/repeat, a facility-floor decal at tens to hundreds. `_classify_water_matte`
  (`import_eftpack.py:1894-1937`) mirrors `gpu_driven.rs:1904-1958` exactly: per submesh, the local
  vertex-span **bbox diagonal** over the largest UV span, flagged above `WATER_MATTE_MPR = 40.0`.
  Matte kills the mirror and the sun glint (`gpu_draw.wgsl:1563,1566`); a real puddle keeps both.
- **It must be a PRE-PASS.** Materials are built lazily from `_face_materials` during mesh
  construction, so a water material shared by a normal quad and a 300 m strip would be built from
  whichever mesh is read first and permanently get the wrong class. Called from `_run_open` at
  `:2258`, after `_classify_shear()` and before `_build()`. The viewer runs the same scan as a
  prepass for the same reason.
- The reflective branch ports `refl_mix`'s interior gate, `clamp(2*max(coverage-0.5, 0), 0, 1)`,
  scaled onto Blender's neutral 0.5 on Specular IOR Level (`import_eftpack.py:1548-1562`), because a
  texel at coverage 0.4 gets ZERO reflection in the viewer while still being 40% opaque. The
  viewer's full gate is `clamp(ndv*ndv*2*max(coverage-0.5,0),0,1) * fr` (`gpu_draw.wgsl:1566`); the
  `ndv^2 * fr` view terms are dropped on purpose, for the same reason `fr` itself is.

**KNOWN DIVERGENCE, all 173 puddle materials: the puddle's Base Color is the ALBEDO TEXTURE, not
`tint.rgb`.** The shader's body term is `wet = m.tint.rgb * gi` (`gpu_draw.wgsl:1565`), a plain
diffuse whose albedo IS the authored tint; `tex.rgb` is never used for colour, only for the mask.
The importer links Base Color to the albedo image at `import_eftpack.py:1270`, before the water
branch runs, and the branch's `_sock(bsdf, "Base Color", tint)` at `:1522` is a **no-op on an
already-linked socket** (`_sock` at `:2448` writes `default_value` only). Every one of the 173 also
carries a non-white tint, so the tint multiply at `:1265-1269` fires too: what actually renders is
`tex.rgb * tint.rgb`. The puddles therefore carry the atlas's own colour variation and sit darker
than the viewer's flat grey (interchange's tint.rgb is already 0.1067 grey, 0.0539 on
`puddle_mask33`). Correcting it means REMOVING the link, not writing a default.

### Deep water

Untextured water is a deep BODY. Treating it like a puddle with no mask makes every one of them
fully transparent and you look straight through the sea to the world background.

- Alpha 1.0, Metallic 0, Roughness 0.10, IOR 1.333, **Transmission 0**: `deep` is already the
  extinction RESULT, so adding transmission double-counts the absorption
  (`import_eftpack.py:1565-1582`).
- Base Color is the fixed `(0.0275, 0.1418, 0.1323)` from `gpu_draw.wgsl:1781`, EFT's own
  FX/SimpleWater4 `_DepthColor` extracted from `Sandbox_Water4Advanced`. It is DERIVED, not
  authored, and it deliberately ignores the record's white tint.
- `wf = 0.02 + 0.98*pow(1-NwV, 5)` (`:1770`) IS Schlick for IOR 1.333, so Cycles computes it and
  traces the real sky instead of `sky_reflect`'s analytic gradient.

**The ripple is a WORLD-SPACE normal, not a normal map.** `_deep_water_normal`
(`import_eftpack.py:1027-1164`) exists because the viewer shades deep water against WORLD UP and
never the interpolated mesh normal: the sea quads' per-vertex normals crosshatch at the grid period
and produced the reported "shadow streaks" (`gpu_draw.wgsl:1737-1742`). Feeding the Principled a
world-space vector built on a constant Z = 1 reproduces that for free. The generic normal block is
skipped entirely for this case (`import_eftpack.py:1788`) so nothing rebinds the map as a
tangent-space normal.

- **Axis mapping.** `YUP_TO_ZUP` sends pack `(x, y, z)` to Blender `(x, -z, y)`, so `pack_x = B.x`,
  `pack_z = -B.y`, and pack up (+Y) is Blender +Z. The shader builds `Nw = normalize(Nup + (dx, 0,
  dy))`, so the Blender form is `normalize(dx, -dy, 1)`. Missing the sign on the second component
  scrolls the waves the wrong way across the surface.
- **Animation is not one of the impossible parts.** `sun.gfx.w` is app time in SECONDS; a Value
  node with a `frame / fps` SCRIPTED driver against `scene.render.fps` reproduces the scroll at the
  authored speeds exactly (`:1053-1069`). A single still just needs a frozen phase.
- **The procedural Water4 chop is written in RADIAN form.** The shader's `rsin(x) =
  sin(fract(x)*2pi)` with phases in CYCLES is an f32 fast-math workaround for a GPU, which Cycles
  does not need, so the drift constants are multiplied back by 2pi once: 0.0200 -> 0.1256637,
  0.0126 -> 0.0791681, -0.0160 -> -0.1005310, 0.0230 -> 0.1445133 (`:1092-1119`). Spatial frequency
  0.35, the four octave mixes `(1, 0.6)`, `(0.7, -1)`, `(3.1, 2.3)`, `(3.7, -2.9)`, detail octave
  at 0.5, amplitude 0.06.
- **When the record ships a real `WaterBasicNormals` map, two world-XZ scrolling layers REPLACE the
  chop** (`gpu_draw.wgsl:1717-1735`, ported at `import_eftpack.py:1120-1154`). Frequencies 0.15 and
  0.15*1.73 cycles/m, offsets `t*(0.050, 0.0315)` and `t*(-0.040, 0.0575)`, mix constant
  `0.5*0.85`. The shader reads `.xy * 2 - 1` **RAW**: it ignores both `normalGreenFlip` and
  `normalScale`, unlike the generic normal block, so neither is applied here. Mesh UVs are ignored
  too, the frame is world metres.
- **Two terms are not portable.** The distance falloff `(0.06/(1 + d*0.004)) * (1 -
  smoothstep(500, 1400, d))` (`:1680`) has no meaning in a path tracer: a material is evaluated for
  shadow, reflection and bounce rays, none of which has a camera distance, and wiring
  `ShaderNodeCameraData.Distance` would give the sea's reflection in a window the CAMERA's
  distance. The Nyquist octave gates `w1`/`w2` (`:1683-1690`) are derived from `wxz_footprint`, the
  screen-space derivative of world position, which a Cycles node graph does not have. Both stand at
  their d = 0 values (0.06, `w1 = w2 = 1`), which is the CORRECT value at close range; the pixel
  filter does the band-limiting the gates were faking.

### The puddle re-UV

`_read_mesh_arrays(mid, reuv=True)` (`import_eftpack.py:1939`, projection at `:1990-2044`) mirrors
`gpu_driven.rs:2710-2799`. EFT's real puddles are small ~5 m `decal_plane` quads whose [0,1] UVs map
the WHOLE soft blob stamp, so their edges feather. Some puddle materials are instead baked onto huge
ROAD strips (77 x 318 m) with the same texture mapped across the ENTIRE strip: every visible
fragment then samples a sub-3% UV window deep in the blob's OPAQUE CORE, where alpha is ~1 at any
mip, and the strip renders as a uniform hard-edged slab. The fix is to throw the baked UVs away and
planar-project.

- Textured water only, and `vert_mat` is **last-writer-wins** over the submesh index runs, exactly
  like the viewer's own array.
- Two gates: `m_per_tile >= 15.0` (`PUDDLE_STRETCH_MIN`) and the SECOND-widest local axis
  `>= 3.0 m` (`PUDDLE_MIN_WIDTH_M`). The second rejects a 1D tire-mark or water-trail streak
  authored to tile along one axis.
- Projection is onto the submesh's two widest LOCAL axes, centred, at `PUDDLE_TARGET_M = 6.0`
  m/tile. `REPEAT` extension on the image node is what makes the blob tile down the strip.
- Done on MESH-LOCAL positions because that is the single choke point for both `_shared_mesh` and
  `_baked_mesh` and it holds the exact space the viewer projects from. After the world bake the
  projection basis would be different.
- **Write PACK-space UVs and do NOT pre-negate.** `_make_mesh` owns the single V flip on the way
  into Blender, so the flipped result samples the identical texel the viewer samples.
- The MATTE flag is deliberately not a gate here (`gpu_driven.rs:2725-2726`): the stretch heuristic
  mis-tags these big road puddles as matte, and matte only kills reflection in the shader, it says
  nothing about edge softness.

## Parallax: one step, not thirty-two

**139 materials** across the six packs carry a height map (streets_nav 135, woods 4), authored scale
0.005 to 0.080. Ported in `tools/blender/import_eftpack.py::_Importer._parallax` (`:633-787`),
called from `_build_material` at `:1817`, after the normal block so both texture nodes exist.

**The gate mirrors `gpu_driven.rs:2278-2292`**: a non-empty `parallax.map`, scale clamped to
`[0.0, 0.5]` (the measured range never reaches it, carry it anyway), the `EFT_PARALLAX` kill switch,
and never on a vert-paint or terrain material because the vp splat owns its own UV
(`assemble_bevy.py:394` already omits it). The height map is DATA: load it Non-Color, matching
`ctrl_tex_linear.insert()` at `gpu_driven.rs:2288`.

**The march cannot be expressed, but its first iteration can be, exactly.** The viewer steps 8 to 32
layers along the tangent-space view ray until marched depth passes sampled depth, then interpolates
(`gpu_draw.wgsl:1019-1063`). A Cycles node graph has no loops and no conditional re-sampling.
Eliminating the step index gives the closed form

```
uv(d) = uv - (Vts.xy / vz) * (scale * fade) * d,     d in [0,1]
```

so `num` and `layer` cancel completely and porting them would be cargo cult. Taking `d = h(uv)` is
the Kaneko/Welsh approximation. It OVERSHOOTS where the height field is steep; on this corpus
(brick, roof tile, concrete slab, parquet, measured 57..189/255 on `City_Roof_tile_disp`) the error
is small, but a near-binary height field with deep mortar grooves gets a smear where steep parallax
gives a crisp occlusion edge. OSL is the only real march and it forces
`cycles.shading_system = True`, which is CPU/OptiX only, for 139 materials in two packs.

**DEPTH, NOT HEIGHT.** The shader reads `h = 1.0 - sample.g`: the texture's WHITE is the top of the
surface and the relief is recessed BELOW the polygon plane. Feeding `g` straight in inverts it. The
GREEN channel specifically, because these maps are near-grayscale but not bit-identical across
channels (`City_Roof_tile_disp` R/G/B means 119.73/119.51/119.73, `r == g` nowhere), so an RGB to BW
average is not the same number.

**The basis, in world space.** `Geometry > Normal` is the shading normal ALREADY back-face-flipped,
which is exactly the shader's `gN` and is not affected by the Normal Map node. `Geometry > Incoming`
is the unit vector toward where the point is viewed from, which is the shader's `V`. Use the
`Tangent` node in **UV_MAP** mode, never `Geometry > Tangent`: the latter is radial and has nothing
to do with the UV shell. `vz = max(V.N, 0.15)` is the grazing clamp, and
`puv = uv - (V.T, V.B, 0) * scale * h0 / vz`.

**The bitangent sign is a GUESS, and there is no sign to copy.** Cycles exposes no bitangent sign,
so `B = N x T` is a handedness guess for UV shells mirrored relative to the outward normal, and a
wrong sign makes the relief pop OUT instead of in. `EFT_PARALLAX_SIGN=-1` flips it. The viewer
normalizes `t_raw`/`b_raw` without dividing by the UV Jacobian determinant
(`gpu_draw.wgsl:1037-1041`), silently dropping `sign(det)` from BOTH axes, so its own offset
reverses on mirrored shells: exact sign parity with the viewer is not a well-defined target. Match
the physically recessed look instead.

**Rewire exactly two samples**, the same two the shader feeds with `puv`: base albedo (`:1127`) and
base NORMAL (`:1173`). Emissive stays on `o.uv` (`:1135`), detail maps own their own Mapping nodes,
and vp is mutually exclusive. `tex.a` rides `puv` for free, which is what puts the RFA per-pixel
roughness (`:1432`) and the cutout width (`:1142`) ON the relief instead of beside it: **137 of the
139 are RFA**.

**NO BUMP NODE, deliberately.** All 139 carry a real tangent-space normal map, and the viewer's
parallax perturbs NO normal, it only moves where that normal map is SAMPLED. A Bump node adds a
second copy of the same relief's shading, and it fails worst exactly where parallax is invisible:
head-on, `V.T` and `V.B` go to zero so the true offset is exactly ZERO while a Bump node's
perturbation is at its maximum. That is the `specMap` trap again, a field that looks like an upgrade
to bind and is a regression. Displacement is a different and more expensive feature (it moves
silhouettes, which parallax never does) and needs a UV-to-metres conversion the pack does not carry.

Three smaller divergences, all declared:

- The 25..50 m distance fade (`:1030-1033`) is NOT ported. It exists to stop per-quad
  screen-derivative shimmer under camera motion; an offline path trace has no temporal shimmer, and
  past 50 m the offset is sub-pixel and moves no silhouette. `fade = 1.0` everywhere, the same call
  this file already makes for detail maps.
- `textureSampleGrad`'s explicit gradients (`:1051-1060`) have no Cycles equivalent, so these two
  samples filter slightly worse than their neighbours. A little texture noise, nothing structural.
- The viewer's relief is defined against ONE view vector, the camera's. `Geometry > Incoming` is
  PER-RAY, so a diffuse bounce or the same wall seen in a mirror each get their own offset. More
  physical than the viewer, but it means a Cycles render can never be pixel-identical here.
  `Camera Data > View Vector` through a CAMERA-to-WORLD `VectorTransform` would buy parity at the
  cost of relief that is visibly wrong inside reflections.

## Vert-paint: six divergences worth the node count

The three-layer blend itself is described above. **1,127 materials across the six packs** build the
full splat graph (streets_nav 457, factory_rework 226, interchange 134, ground_zero 123,
icebreaker 101, woods 86); 1,125 of them ship a heights map. Six things in the shader are not what a
straight reading of the blend suggests, and each is invisible on the surfaces where the two readings
agree.

**1. `COLOR_0` must be RE-ENCODED on the way in.** The shader consumes `o.color.rgb` RAW: a vertex
attribute is never sRGB-decoded on the GPU, so it sees `byte/255`. Blender treats a `BYTE_COLOR`
attribute as sRGB-encoded, so the Color Attribute node hands the graph `srgb_to_linear(byte/255)`
instead. Measured on 5.1, byte 128 comes back as **0.2159** where the viewer sees **0.5020**.
`_srgb_encode()` (`import_eftpack.py:309-335`, used at `:1332-1334`) puts the byte back with the
exact IEC 61966-2-1 piecewise, 5 nodes per channel. **Exact, not the 2.2 shortcut**: the linear
segment below 0.0031308 is where an almost-unpainted vertex lives, and that is precisely the range
the empty-mask gate tests. Doing it in nodes rather than switching the attribute to `FLOAT_COLOR`
keeps storage at 4 B/corner.

**2. The empty-mask gate is on `hs`, NOT on the normaliser.** `gpu_draw.wgsl:1287` sums `hw` BEFORE
`max(., 1e-4)` and BEFORE `pow`; `:1291` tests THAT sum against 1e-5. Gating on the post-`pow` total
works only by accident: for `blend > 1` the floored term collapses (`1e-4^1.8` = 2.5e-8) so the test
still fires, but at `blend` clamped to 1.0 the floor survives as 3e-4 > 1e-5 and an unpainted face
washes out to the even three-way blend the fallback exists to prevent. **11 materials across the six
packs author `blend <= 1`** (interchange id 319 at 0.8 is exactly that case). The importer keeps a
separate raw sum for the gate at `:1336-1368`.

**3. The zero-coverage fallback INCLUDES the layer tint.** The shader's zero-coverage path is
`w = (1, 0, 0)`, so `spl = a0.rgb * tint0.rgb` (`:1290` feeding `:1299`). Falling back to the
untinted texture is a second divergence hiding behind the first (`import_eftpack.py:1378-1408`).

**4. The near-black resolve is a hard per-fragment test** (`gpu_draw.wgsl:1304-1305`):

```wgsl
if (dot(spl, vec3(0.299, 0.587, 0.114)) < 0.02) { spl = a0.rgb * v.tint0.rgb; }
```

A dark layer tint over a dark mask can resolve the splat to near-black, and the renderer catches it
and falls back to the tinted base layer at full strength. **471 of the 1,127** carry a non-white
layer-0 tint (95 of interchange's 134), which is what pushes a blend under the threshold in the
first place. The luma weights are **Rec.601 and they are not negotiable**: `ShaderNodeRGBToBW` uses
the working space's Rec.709 coefficients (0.2126/0.7152/0.0722), so a green-dominant blend would
cross 0.02 at a different colour than the shader. A `DOT_PRODUCT` against the literal constants is
the only faithful form (`import_eftpack.py:1425-1437`). Cycles supersamples through the
discontinuity where the raster viewer resolves it once per pixel, so the boundary dithers over about
a pixel; below 0.02 luma nothing is visible anyway. Do NOT soften it to a smoothstep, that changes
what the threshold means.

**5. The material tint is reapplied AFTER the splat.** `:1306` ends with
`albedo = vec4(spl, a) * m.tint`. Relinking Base Color straight from the splat bypasses the material
tint. A no-op on all six shipped packs (0 of 1,127 carry a non-white record tint) but wrong in
general (`import_eftpack.py:1442-1447`).

**6. The matte roughness override is an ASSIGNMENT, not a floor** (`gpu_draw.wgsl:1440-1441`):

```wgsl
if (vp_smooth >= 0.0) { rough = clamp(1.0 - 0.30 * vp_smooth, 0.72, 1.0); }
```

It DISCARDS the scalar for every `MAT_FLAG_VP` material, so all 134 flagged on interchange change,
not just the 5 sitting under 0.72. The 129 at scalar 0.9 now range over [0.72, 1.0] per texel: a
high-alpha texel gets GLOSSIER, a zero-alpha texel MATTER. The 5 at 0.5 (the mall's
`Concrete_floor_tiles`, ids 4278/4296/4304/4305/4312) are the ones that read semi-wet today. It is
LAST in the shader, so it beats BOTH the RFA per-texel roughness and the water floor; the importer
places it after the RFA block and before DETAIL ALBEDO (`import_eftpack.py:1706-1721`) so the
ordering holds even though interchange cannot test it (0 of the 134 are RFA, 0 are water). The
ceiling never binds, because `w` is normalised and the alphas are non-negative, so a single MAXIMUM
is the whole clamp.

`vp_smooth` is the weighted sum of the RAW layer texture ALPHAS (`:1309`). Nothing else multiplies
in: the layer tints are rgb-only, and `tint.w` is the blend EXPONENT, not an alpha
(`gpu_driven.rs:2097-2105` packs it into `tint0.w` and zero into `tint1/2.w`). At zero coverage
`w = (1, 0, 0)` exactly, so `vp_smooth` is `a0.a` there (`import_eftpack.py:1454-1470`). When the
splat graph cannot be built at all (no heights map or a layer image that failed to load, 2 of the
1,127) the viewer STILL applies the override, with `h = vec3(1.0)` where the heights index is
`NO_ALBEDO`, so the honest static stand-in is the 0.72 floor rather than the discarded scalar
(`:1716-1721`).

**Port the NUMBER, not the response.** The shader's `rough` drives a hand-written GGX with
`SPEC_STRENGTH`, Smith `k = (rough+1)^2/8` and F0 = 0.04, lit from the SH volume's dominant
direction. Cycles uses multiscatter GGX against the real scene, so the same 0.72 will not give the
same highlight. Leave `Specular IOR Level` at its 0.5 default, which IS F0 = 0.04, and do not
hand-tune the roughness to chase the raster highlight.

## Detail albedo: mean-neutralise it or the rock goes flat

**204 materials** across the six packs carry a detail albedo (ground_zero 91, woods 68,
interchange 23, streets_nav 22). They are the rock and cliff surfaces, which without it read flat
and low-frequency next to everything around them.

Unity Standard multiplies a tiling detail map over the base at x2. Copying that naively darkens or
recolours the whole surface by the detail map's own average, so the renderer divides that average
back out and the map contributes only LOCAL contrast, never a global shift
(`gpu_draw.wgsl` "#6 Detail albedo", `:1318-1337`):

```
neutral = clamp( detail_lin * 4.5948 / max(albedoMeanGain, 1e-3), 0.25, 4.0 )
base   *= mix( 1, neutral, clamp(albedoStrength * fade, 0, 1) )        # alpha untouched
```

`4.5948` is Unity's detail x2 expressed in linear space; `albedoMeanGain` is that same product's
per-channel mean, measured offline at assembly (`eft_pipeline/assemble_bevy.py:412-433`). Ported at
`import_eftpack.py:1734-1778`. Three details:

- `albedoUv` is the **RAW** Unity `_DetailAlbedoMap_ST` and must be re-based through
  `_detail_xform` against `uvXform`, because the base ST is already baked into the vertex UVs. Same
  V-flip-aware un-bake the vp layers use, and getting it wrong shifts the map by up to half a tile.
- It is inserted as a MULTIPLY on whatever currently drives Base Color (`:1772-1778`), so it
  composes with the plain, tinted and vert-paint paths alike instead of racing them.
- The shader's 40 m to 120 m distance fade (`gpu_driven.rs:2254-2261`) is not ported: an offline
  render has no reason to fade it, so the near-field value stands everywhere. Detail is mutually
  exclusive with the terrain splat (`gpu_driven.rs:2514` clears `MAT_FLAG_DETAIL | MAT_FLAG_RFA` on
  terrain materials).

## Coplanar overlays: a 6 mm lift replaces the depth push

The renderer separates coplanar overlays in CLIP space: `o.clip.z += 1.0e-3 * o.clip.w` for
`MAT_FLAG_DECAL | MAT_FLAG_WATER` (`viewer/assets/shaders/gpu_draw.wgsl:992`, `:1001`, under
`SURFACE_PUSH`). **A path tracer has no depth bias to borrow**, so the same separation has to be
geometric: `DECAL_LIFT = 0.006` m (`tools/blender/import_eftpack.py:128`), applied at `:2135-2161`.

Three rules make it work:

- **It happens on the MESH, not the object.** A road slab and the asphalt under it are exactly
  coplanar, and two coincident surfaces make the ray hit a coin toss: stippled speckle that changes
  with the camera. An object-level offset would also slide the overlay off the curbs and ramps it
  was authored against.
- **Along the VERTEX NORMAL**, which keeps the decal glued to whatever it was projected onto,
  including curved and ramped receivers.
- **Only the vertices used by decal/water faces move**, selected by material-slot name suffix,
  because a mesh can mix roles and lifting an opaque face would tear it from its neighbours.

This is separate from, and lands on top of, the 12 mm `SURFACE_OFFSET_M` the projector bake already
applied to StaticDeferredDecal geometry ([decals.md](decals.md) section 7). Roads, yard slabs and
water decals are ordinary submeshes with no bake-time offset at all, so 6 mm is their only
separation. The run summary reports how many vertices moved.

## Import switches and the run summary

`import_eftpack.py` reads three environment variables of its own, all A/B levers rather than tuning
knobs:

| variable | effect |
|---|---|
| `EFT_PARALLAX=0` | masks parallax for every material, matching the viewer's own kill switch at `gpu_driven.rs:2280` so an A/B is one env var. The importer additionally accepts `false`/`False`; the viewer only tests against `"0"` |
| `EFT_PARALLAX_SIGN=-1` | flips `B = N x T`, the bitangent handedness guess, for UV shells mirrored relative to the outward normal |
| `EFT_SKY_STRENGTH=<f>` | overrides the Background strength the glass reflection probe projects the sky at (`:878-882`), including the `2.35` fallback taken when the world is not built yet. Changes only the glassTRS environment term, so it is a clean A/B against a viewer frame |

The summary block at `_run_open` (`:2650-2666`) and the returned dict (`:2667-2681`) count the work
each of these features actually did: lifted vertices, puddle materials and how many were classified
matte, deep-water materials, re-UV'd submeshes, glassTRS trees, parallax offsets and vert-paint
splats. Those counters are the fastest way to catch a feature that silently did nothing, for example
a pack whose water never re-UV'd because the submesh gates never fired.

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
the object actually is. Every projected decal is like this (1,353 on Interchange). A spatial filter
on the affine translation drops all of them; derive the position from the mesh's own geometry
instead. They are not flagged `BAKED_WORLD` either, so the flag cannot be trusted to find them.

## The region filter tests an AABB, never a centre

The `radius` filter is what makes a full-fidelity local import tractable, and it has been wrong
twice in the same place for two different reasons. Both failures deleted geometry silently.

**It must test the mesh's world-space AABB against the disc.** A centre test asks "is this object's
middle near the camera", which is the wrong question for anything bigger than the radius. Terrain
ships as four roughly 700 x 700 m tiles: on the `ZoneBearCamp` staging, `Slice_2_2`'s world AABB
CONTAINS the route centroid (AABB distance **0.0 m**) while its true centroid is **163.3 m** away.
At `MAP_RADIUS = 170` a centre test therefore culled the tile the camera is standing on, and
`terrain_splat.py` then printed `rebuilt 0 terrain material(s)`: ground props floating over a void,
with no error anywhere. The test is

```
world_c = M3 @ local_c + T           # box centre
world_e = |M3| @ local_e             # half-extent; |M3| is the correct conservative bound
                                     # for a rotated or sheared box
q       = clip(center, world_c - world_e, world_c + world_e)     # nearest point of the box
inside  = |q - center|^2 <= radius^2
```

This is a strict SUPERSET of the centre test, because a mean of vertices always lies inside the
hull and therefore inside the box, so the box can only ever be nearer. Nothing that used to import
can stop importing, which is what makes it safe to swap in. Measured at `MAP_RADIUS 170` on
interchange: **3,683 to 3,766 instances (+2.3%)**, +766,415 triangles, **524,288 of which are the
terrain slice that was missing**.

**Not a bounding sphere.** `Slice_2_2`'s conservative sphere is **857.5 m** on a flat 700 m tile,
which admits all four terrain tiles at any radius and re-imports the whole map. The looseness is
the tile's own flatness: a sphere has to reach the corners of a plate that has almost no third
dimension.

**The pack ships no bounds, so derive them EXACTLY.** `manifest.meshes` carries only
`id/name/vtx*/idx*/submeshes`; there is no AABB and no sphere. The viewer derives its own the same
way (`bounding_spheres` in `viewer/src/eftpack.rs`, mean of positions plus a max radius, feeding the
cull compute pass), and the two implementations are independent. Do NOT sample: a strided read of
64 vertices per mesh ALIASES on a 513x513 terrain grid, and put `Slice_2_2`'s sampled centre 22.2 m
from its true centroid, which is on its own enough to push it outside `MAP_RADIUS 170` and delete
the ground. The exact answer is not slower, because one memmap pass over `meshes.bin` reading
position only pages in exactly the 12 bytes per vertex that min/max touches: **0.56 s for
interchange's 7,920 meshes**, against 0.55 s for the seek-per-sample loop it replaces, which issued
roughly 500k `seek`+`read` syscalls to read less data.

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
- **The clip-space push** for coplanar decals and water. There is no depth bias to copy, so it
  becomes 6 mm of geometry along the vertex normal:
  [Coplanar overlays](#coplanar-overlays-a-6-mm-lift-replaces-the-depth-push).
- **Reinhard tonemaps applied to incoming radiance** (`sh_ldr`, `sun_ldr` on TRS glass;
  `gpu_draw.wgsl:1632`, `:1647`). These DO survive, but only while the radiance is still a VALUE. A
  BSDF's input is what the integrator traces into it, which does not exist until after the BSDF has
  been evaluated, so `x/(1+x)` has nothing to apply to and the term loses its ceiling. Evaluate the
  environment and the one light analytically and each Reinhard is a single Divide node. That is the
  reason TRS glass emits values instead of tracing:
  [Both additive lobes are bounded values](#both-additive-lobes-are-bounded-values-and-neither-is-a-bsdf).
- **Shadow-map corrections applied to specular** (`(1 - shadow_event)` on the glass env and Blinn
  terms, `gpu_draw.wgsl:1638`, `:1649`). There is no path-traced twin once those lobes are emitted
  as values, because an Emission takes no shadow ray. Both are bounded and both fall to near zero on
  a pane facing away from the dominant light, so the exposure is small. Re-attaching a BSDF to
  recover the occlusion costs the bound and is a worse trade.
- **Anything driven by a screen-space derivative**: the deep-water Nyquist octave gates, parallax's
  `textureSampleGrad`, the detail and parallax distance fades. A node graph has no `ddx`/`ddy` and a
  shadow or bounce ray has no camera distance. Take the near-field constant and let supersampling
  do the band-limiting the gates were faking.

Each of these is expanded, with its population and its shipped substitute, in the feature sections
above.

## Foliage transparency is a whole-path budget, not a quality knob

`transparent_max_bounces` is the one Cycles setting in this pipeline where the default DELETES
pixels rather than degrading them, and it is set to **256** in both modes
(`example_scene.py` step 8, and `cine_camera.cinematic_render_settings`).

**It is a WHOLE-PATH counter, not a per-segment one.** The camera segment, every diffuse bounce
that follows it, and the shadow ray toward the sun all draw from the same budget. Every leaf, blade
and chain-link in this content is alpha-tested, so a ray that grazes the grass field or crosses a
bush spends the budget on transparency alone.

**And Cycles fails CLOSED.** A camera ray that runs out is TERMINATED and returns black; a shadow
ray that runs out is reported FULLY OCCLUDED. Neither warns, and neither shows up as noise, so the
result reads as a material or lighting bug in the foliage rather than as a render setting.

Measured on this pack's grass field:

| `transparent_max_bounces` | result |
|---|---|
| 8 (the Cycles default) | **14.78%** of the frame is EXACTLY 0.0 luma, and the mean is 12.2% low |
| 32 (what this document used to recommend) | still **0.48%** black |
| 256 | **bit-identical** to the 1024 maximum, at 2.0 to 2.3 s per frame |

256 is therefore the cheapest value that is indistinguishable from unlimited. A transparent bounce
does no shading, which is why raising the cap this far costs 0.3 s: this is the least expensive
fidelity in the whole file. Note that `cinematic_render_settings` currently has **no caller** in the
repo, so setting it there alone does not reach the built scene; `example_scene.py` configures its
own render settings in step 8, and that is the copy that ships.

## Blender behaviours a fresh implementation will trip on

Two of these are colour-management traps that predate 5.x; the rest are 5.1 API moves, and almost
every one of them fails SILENTLY.

`Material.blend_method` and `alpha_threshold` are legacy ALIASES of `surface_render_method` under
EEVEE Next (4.2+). Assigning `'OPAQUE'` or `'CLIP'` is a silent no-op that leaves the material on
`HASHED`, so a MASK alpha test has to live in the node graph as a GREATER_THAN on the computed
alpha rather than in a material property.

`COLOR_0` is packed `unorm8x4`, and Blender converts colour attributes on write. The attribute must
be CORNER-domain `BYTE_COLOR` written through `color_srgb` to round-trip the bytes; writing through
the linear `color` property changes the numeric values, which are blend WEIGHTS here, not a colour.
That silently moves Vert-Paint layer selection and SoftCutout edges.

The conversion happens on the READ side too, and it is a separate bug. Round-tripping the bytes into
the datablock does not mean the node graph sees them: a Color Attribute node on a `BYTE_COLOR`
attribute returns `srgb_to_linear(byte/255)`, so byte 128 arrives as 0.2159 where the viewer sees
0.5020 (measured on 5.1). Re-encode in nodes before the weights are computed
([vert-paint divergence 1](#vert-paint-six-divergences-worth-the-node-count)); switching the
attribute to `FLOAT_COLOR` also works and costs 4x the storage per corner.

### The 5.1 compositor and animation API moved, and most of it fails silently

| What changed | The trap |
|---|---|
| `scene.node_tree` is GONE, and so is `CompositorNodeComposite` (`hasattr` is False) | the pointer is `scene.compositing_node_group`, and it takes a `CompositorNodeTree` built with a Group Input and a Group Output |
| **The group's input socket is NOT the render result** | this is the expensive one. The beauty pass has to be pulled in by a `CompositorNodeRLayers` node INSIDE the group. Feed the chain from Group Input instead and the render does not merely come back uncomposited, it NEVER RUNS: measured at 480x270, `Group Input -> Group Output` returned in **0.03 s with every pixel exactly 0.0**, against **6.26 s** and a correct frame for `Render Layers -> Group Output`. Nothing warns; the mode just writes a black EXR |
| There is no `CompositorNodeMath` | `nodes.new("CompositorNodeMath")` raises "Node type undefined". The compositor takes the unified `ShaderNodeMath` now. `CompositorNodeSeparateColor` and `CompositorNodeCombineColor` are still `CompositorNode*`, so the two families are mixed in one tree. This one at least fails LOUDLY, at node creation |
| Glare and Lens Distortion parameters are INPUT SOCKETS, not RNA properties | `node.glare_type` does not exist; it is `node.inputs["Type"]`, a menu socket that takes TITLE-CASE strings (`'Bloom'`, `'Ghosts'`, `'Streaks'`, `'Fog Glow'`, `'Simple Star'`). `'FOG_GLOW'` raises |
| Render Layers grows its `Depth` output only AFTER `view_layer.use_pass_z` is set | enable the pass before building the node, or `outputs.get("Depth")` is `None` and any depth-driven effect silently drops out |
| `Action.fcurves` is gone | curves live in a slot channelbag from 4.4 on. Walk `action.layers[*].strips[*].channelbags[*].fcurves` with `Action.fcurves` as the legacy fallback (`cine_camera._fcurves`) |
| No `OPEN_EXR_MULTILAYER` file-format enum, and `CompositorNodeOutputFile` has no `.base_path` | a depth pass cannot be written alongside the beauty pass the old way. Either port the File Output node to the 5.1 API or emit depth as a separate 1-spp pass (about 1.5 s/frame, the measured fixed per-render overhead); the haze does not need an anti-aliased Z |

Two related notes that are not API breaks. `sky.sun_direction` is a no-op in 5.1: only
`sun_elevation` and `sun_rotation` drive the sun, and `HOSEK_WILKIE` and `PREETHAM` have no sun
disc at all and ignore `sun_rotation` entirely. And `scene.render.compositor_device` is deliberately
left on CPU here, because OptiX already owns both GPUs for the path trace and Fog Glow measured
0.26 s on the CPU against 0.50 to 0.74 s on the GPU.

## Walking a character somewhere real

Two separate problems, and getting one right does not fix the other.

**Vertical.** Resolve the ground the way the viewer does: the greatest walkable surface at (x, z)
that is no higher than `feet + STEP_UP`, counting only faces whose `normal.y/|n|` clears
`HORIZ_MIN`, cast DOWNWARD from that cap. A ray cast from the sky stops on bush canopies and
container roofs, which is how a character ends up standing in mid-air.

**Horizontal.** A `patrol_way` is a NETWORK, not an ordered path. Consecutive waypoints are not
guaranteed to be mutually visible, so a straight line between two of them walks the character
through whatever stands in between. Route each leg on the pack's baked nav grid instead - the same
grid the viewer navigates:

    nav.json         min_x, min_z, res, nx, nz, n_layers K, miss, climb, drop_max, vault, step_up
    nav.bin          f32[nx*nz*K], cell (iz*nx+ix) layer l at (iz*nx+ix)*K + l, heights ASCENDING,
                     `miss` (large negative) padding unused layers
    nav_door.bin     u8[nx*nz]   1 = door cell, forced passable
    nav_blk.bin      u8[nx*nz*K] 8-dir edge mask, bit d = edge to NB[d] blocked by a thin wall
    nav_wallcell.bin u8[nx*nz]   1 = a wall occupies this cell's body column

A* over (cell, layer) with `best_layer` picking the neighbour layer nearest the current height.
Four rules carry the weight, and each one silently produces a route through a wall if dropped:

- a DOWN step is bounded exactly like an UP step (`drop_max`, `run * tan(slope)`, capped by
  `vault`). A free-fall allowance is what lets a route leave the ground and walk over the top of a
  vehicle or container.
- a DIAGONAL needs BOTH shared orthogonal sides floored, walkable AND unblocked. A player capsule
  cannot squeeze a corner where either side is a wall.
- a door on EITHER side forces the seam passable, and that test must come BEFORE the block mask so
  a stray bit cannot seal a doorway. A forced UP step is still capped by `vault` - a door is not
  authorisation to hop a storey.
- `near_wall` (any blocked edge, dilated one cell) adds a small per-cell cost so routes stand off
  walls by roughly the agent radius. Soft: it must never close a corridor.

Take the heading from a point ~1.5 m AHEAD on the polyline, not from the current segment. A grid
route is 0.5 m steps locked to 8 directions, so a per-segment heading snaps the character between
45-degree facings every few frames.

## Filming it

A follow camera has to keep the subject unobstructed AND move smoothly, and those goals fight.
Choosing a position per frame and smoothing afterwards satisfies neither: the smoothing drags the
camera back through the wall the per-frame choice just escaped. Because a render is offline, solve
the WHOLE move first - score every candidate position at every moment, then take the lowest-cost
SEQUENCE, with camera travel between steps as part of the cost. That is a shortest path over time;
Viterbi solves it exactly, and smoothness falls out of the solution instead of being filtered on.

Three details decide whether it works:

- **Visibility is a fraction, not a boolean.** Test several body points (head, chest, both
  shoulders, hips, knees). One ray to the chest says nothing about a post across the torso or a
  chair across the legs, so a single-ray rig walks the subject behind clutter and reports success.
- **Candidates must cover the full circle.** A fixed side offset has nowhere to go when the route
  hugs a building - and a nav route hugs buildings by design. With the whole circle available the
  camera takes whichever side is open; the transition cost is what stops it flip-flopping.
- **Grass is a SOFT occluder.** Blades are thin and read as natural foreground, so they add cost
  rather than disqualifying a position. Treat them as solid and the camera flees anywhere with a
  tuft in front of it.

Raycast against ONE frame of geometry. Everything except the subject is static, and the subject is
on the ignore list, so re-evaluating the depsgraph per frame costs time and changes nothing.

Aim slightly AHEAD of the subject so he sits on the trailing side of frame with room to walk into,
and smooth the forward vector hard before using it: a walk cycle yaws the root every step, and a
camera that answers that judders. Key every frame with LINEAR interpolation - auto-Bezier overshoots
on a direction change, and an overshoot puts the lens inside a wall.

### What actually buys photorealism

Roughly in order of effect per unit of render time:

| Setting | Why |
|---|---|
| Motion blur, 180-degree shutter (`shutter = 0.5`) | what a real camera does; without it a walk cycle reads as stop-motion at any sample count |
| `transparent_max_bounces` 8 -> 256 | not a quality knob: at the default it DELETES pixels, because the counter is a whole-path budget and Cycles fails closed. 8 leaves 14.78% of the frame at exactly 0.0 luma, 32 leaves 0.48%, 256 is bit-identical to the 1024 maximum for +0.3 s. See [Foliage transparency](#foliage-transparency-is-a-whole-path-budget-not-a-quality-knob) |
| Sun angle 0.526 degrees | the sun's real angular diameter; sets shadow penumbra width, which the eye reads as "outdoors". The stock 1-2 degrees quietly softens every contact shadow |
| Adaptive threshold down (0.005) | concentrates samples on noisy regions instead of re-rendering clean sky, so a high sample cap costs far less than it looks |
| Depth of field on a focus object | focus follows the subject exactly, no keyed focus distance to drift |
| `filter_size` 1.5 -> 1.2 | the stock reconstruction filter is soft above 1080p |

## Washed-out colour is an EXPOSURE symptom, not a material symptom

This is the single most misleading failure in the whole pipeline, because it makes correct
materials look broken and sends you editing shaders.

The game's grade compresses toward a warm white above linear ~1.0, so **pushing a surface brighter
desaturates it**. Measured on the shipped cube, one saturated magenta:

| shaper index | linear value | saturation |
|---|---|---|
| 20 | 0.40 | 0.98 |
| 32 | 1.03 | 0.87 |
| 40 | 1.61 | 0.69 |
| 52 | 2.73 | 0.44 |

So in this display chain **exposure IS colour**. A blue fabric chair rendered 1.5x too bright came
out pale mauve (saturation 0.36 against the viewer's 0.52); at matched exposure the same pixels
measured [0.559, 0.265, 0.452] against the viewer's [0.575, 0.275, 0.446] - the material had been
right the whole time. The `specMap` regression above produced the SAME washed-out signature by a
different route: it made paint glossy, the white sky reflection raised the surface, and the LUT
did the rest. Two unrelated bugs, one visual symptom.

**How to tell them apart before touching anything.** Compare a rendered CHANNEL RATIO against the
source texture's own ratio (G/R or R/B), not brightness:

- ratio matches the texture but everything is pale and bright -> EXPOSURE or lighting;
- ratio differs from the texture -> a genuine material/channel bug.

The forklift failed this test (R/G 0.96 against the texture's 1.43) and was a real bug. The chair
passed it at correct exposure (G/R 0.47 against the texture's 0.53) and was not.

Note also that exposure agreement is VIEW-DEPENDENT between a path tracer and the viewer: the
viewer's ambient is a baked one-bounce SH volume while Cycles computes full GI, so a fit made in an
open yard drifts in an enclosed corner (that chair needed 0.70 where the yard wanted 1.35). Fit
per shot, and hold it fixed for the whole shot.

## Lighting an external render: the two numbers the pack cannot give you

The pack ships **no directional light**. Interchange's 1,659 lights are all Point and Spot; the
game's outdoor lighting lives in the baked SH volume (sky visibility plus bounce), which an offline
renderer has no equivalent for. So a sun and a sky strength have to come from somewhere, and
guessing them is how a render ends up looking nothing like the game while every material is
correct: too much sun and too little sky blows out lit ground, crushes shaded faces, and is
physically inconsistent with the overcast sky texture it is paired with.

Solve them instead. A path tracer is LINEAR in every light's power, so for a fixed camera

    render(sun=a, sky=b)  ==  a * render(sun=1, sky=0) + b * render(sun=0, sky=1)

exactly. Two basis renders therefore span the whole space, and the pair becomes a least-squares fit
against a viewer frame of the identical camera, compared AFTER both have gone through the same
grade. On Interchange this moved the sun:sky ratio from a guessed 3.75 to a fitted **2.94**
(`SUN_ENERGY = 6.90`, `SKY_STRENGTH = 2.35`) and cut RMS error against the viewer by 20%, with the
highlight percentile landing exactly on the reference. Re-solve whenever the sky or map changes.

Use a physically sized sun disc while you are there: **0.526 degrees**, its real angular diameter.
The stock 1-2 degrees quietly widens every contact shadow's penumbra.

## Grading: shoot flat, grade after

Render to **linear EXR**, never to a display-encoded PNG. OpenEXR stores scene-referred data and
ignores the view transform, so the look becomes a post step costing seconds instead of a re-render
costing hours - and it is the only way to apply the game's own grade at all, since a baked filmic
transform cannot be cleanly inverted.

The game's display chain is a 64-cube shipped in the pack. `tools/blender/eft_grade.py` implements
it; the authority is [terrain-and-colour-grade.md](terrain-and-colour-grade.md), which specifies the
shaper, the LUT layout and the vignette exactly. Three traps worth repeating here because they all
produce a plausible-looking wrong image:

- the shaper is `p = sqrt(clamp(lin/4, 0, 1))`, **not** sRGB and **not** log;
- the shipped LUT bytes are DISPLAY ENCODED and must have that encode inverted per texel;
- the vignette is authored in DISPLAY space, so applying it to linear darkens corners half enough.

**Exposure is RENDERER-RELATIVE.** The viewer ships **1.35** (`DEFAULT_GRADE_EXPOSURE`; the shader
comment saying 0.18 is the web viewer's stale value). A path tracer has its own radiance scale, so
either fit the lighting as above until 1.35 is correct, or solve exposure separately - but do it
ONCE for a shot and hold it, because per-frame auto-exposure flickers.

### AgX is now really AgX, and its cube needs tetrahedral interpolation

`eft_grade.py --look` takes three values. `game` is the pack's 64-cube. The other two skip the LUT
and substitute a display transform, for a straight A/B from the same EXRs:

- **`filmic`** is `srgb(x / (x + 0.155) * 1.019)`, the curve this file shipped for a year under the
  name "agx". It is not AgX and it is barely a filmic curve.
- **`agx`** is Blender 5.1's own **AgX Base sRGB**, read from the transform Blender ships and
  reimplemented in numpy.

The rename was not cosmetic. Measured on a neutral ramp, against four other candidates:

| transform | 0.18 CV | CV/stop at grey | 1.0 CV | 4.0 CV | 0.02 CV |
|---|---|---|---|---|---|
| `x/(x+0.155)*1.019` (the old "agx") | 195.3 | 28.0 | 241.3 | 252.9 | 95.8 |
| **AgX Base sRGB (Blender's)** | **117.6** | **38.8** | 196.6 | 233.0 | 31.9 |
| ACES 1.3 sRGB | 90.8 | 46.2 | 207.1 | 243.6 | 10.5 |
| ACES 2.0 sRGB | 89.0 | 35.3 | 180.2 | 229.5 | 9.8 |
| Khronos PBR Neutral | 104.9 | 46.8 | 238.8 | 253.1 | 8.3 |
| Filmic sRGB (Blender's) | 127.6 | 34.5 | 205.8 | 242.1 | 40.6 |

Two things are wrong with the old curve and only one was known. The **grey lift** (+2.40 EV, 195 CV
where every standard transform lands 112 to 120) was already documented and worked around downstream
in `auto_exposure(mode="grey")`. The other is that it has **no shoulder and no toe**: it is a single
hyperbola, so its slope at grey is the flattest of the six while its 1.0 and 4.0 both sit pinned
against white and its 0.02 sits at 96 CV. Re-exposing fixes where the midtones sit and cannot fix
how far apart they are. That is why the exposure-corrected photoreal frame still measured HIGHER
saturation than the game (0.2294 against 0.2130) and still read washed.

AgX is the only candidate that satisfies both requirements at once: 18% grey at 117.6 CV, inside the
112-120 band, AND +38.6% slope at grey over the old curve. Being Blender's default view transform it
is also what the EXRs were framed against in the viewport. ACES 2.0 has a beautiful shoulder and
less midtone contrast than AgX; ACES 1.3 and PBR Neutral have more slope but place grey at 89 to 105
CV, which buys the contrast by underexposing.

**The chain, three stages, all read from Blender's config rather than transcribed from the paper:**

1. scene-linear Rec.709 -> Linear FilmLight E-Gamut. The config routes this through the Linear
   CIE-XYZ I-E scene reference, so `AGX_EGAMUT` is the COMPOSITE of that round trip, read straight
   off the OCIO processor by pushing the identity basis through it. Its columns sum to
   1.0000/1.0000/0.9999, i.e. white is preserved, which is the check that it was composed the right
   way round.
2. `AllocationTransform` lg2 over `[-12.47393, +12.5260688117]`. Those are not arbitrary:
   `2^-12.47393 = 0.18 * 2^-10` and `2^12.52607 = 0.18 * 2^15`, so the shaper is exactly "-10 to +15
   stops around 18% grey", which is what the cube's own header says it expects. Hence the divide by
   25.0. Take the `log2` on `max(x, 2^-12.47393)`, not on `x`: a clean render has plenty of zeros in
   shadow and sky alpha, and `log2(0)` sprays `-inf` through the sampler and comes back NaN.
3. the shipped **57^3** `AgX_Base_sRGB.cube`, then Rec.1886 -> sRGB (a 2.4 power decode, then the
   sRGB OETF).

Reimplementing the SIGMOID instead of reading the cube was rejected: the cube carries the inset, the
rotation and the per-channel outset (its header records rotate `[3, -1, -2]`, inset
`[0.4, 0.22, 0.13]`, outset `[0.4, 0.22, 0.04]`), and any of those transcribed wrong is a hue shift,
not a visible error. `find_agx_cube()` locates the file (`$EFT_AGX_CUBE` wins, else the highest
sorting `luts/AgX_Base_sRGB.cube` under the Blender install roots).

**Verified, not assumed.** `python tools/blender/eft_grade.py --selfcheck-agx` builds Blender's own
OCIO processor for `Linear Rec.709 -> AgX Base sRGB` and diffs it against this implementation over
40k log-uniform samples spanning 20 stops: **max error 0.000021 (0.005 CV)**. Every way of getting
this wrong is quiet - a transposed `AGX_EGAMUT` still preserves white, a wrong log range still
produces a plausible picture - so the check exists to make them loud.

**Tetrahedral interpolation is REQUIRED here, and trilinear is required on the game path.** Both
samplers exist on purpose:

| LUT | sampler | why |
|---|---|---|
| AgX 57-cube | **tetrahedral** | AgX's cube is strongly non-planar off the neutral axis. Against Blender's own processor over the same 40k samples, trilinear peaks at **14.29 CV** of error where tetrahedral peaks at 0.005. The two agree on greys and diverge on saturated colour, which is the worst possible failure shape: it looks fine on a test ramp |
| the pack's 64-cube | **trilinear** | the authority is a GPU sampler. The game and the viewer both read this LUT through hardware trilinear filtering, so matching it is the whole point, and tetrahedral would be the better interpolator and the wrong one |

The tetrahedral lookup is Kasson's six-tetrahedra decomposition written as a SORT rather than six
branches: descending-sort the three in-cell fractions and the tetrahedron is always the same four
corners walked in the sorted axis order, with barycentric weights `1-f0, f0-f1, f1-f2, f2`. Four
gathers instead of eight plus six candidate results: **1.21 s against 3.66 s** for the `np.select`
form on a 2560x1440 frame, max difference between the two **0.0 exactly**.

**The grey anchor is PER LOOK**, and a stale entry is a whole-frame exposure error:

```
GREY_IN = {"agx": 0.17465, "filmic": 0.03202}
```

Both are solved by bisection against the shipped implementation rather than chosen, and re-solved
whenever the curve changes, which is why the table lives next to the curves instead of in a caller.
`auto_exposure(mode="grey")` drives the metered mean onto this number, so metering AgX with the
filmic anchor is a 2.4 EV error.

Measured on a real frame (`renders/photoreal_f0300.exr` at 2560x1440), each look metered against its
own anchor, both with `cos^4` 50 mm and ISO-400 shot noise:

| look | E | p1/p10/p50/p90/p99 CV | in-frame slope | 5x5 local contrast | <= 1 CV |
|---|---|---|---|---|---|
| `filmic` | 0.27066 | 2/15/68/147/183 | 21.87 CV/stop | 0.0712 | 0.21% |
| `agx` | 1.47629 | 1/7/58/151/185 | 24.45 CV/stop | 0.0781 | **3.67%** |
| `game` | 1.73604 | 6/13/32/184/227 | (LUT) | 0.1160 | 0.00% |

The in-frame slope is a fit of display CV against `log2` of exposed scene luma over the p20-p80 band,
so it is the picture's own midtone contrast rather than the curve's, and it is lower than the
neutral ramp's 38.8 because the fit band reaches into the toe. **+11.8% slope and +9.7% local
contrast is the whole win, and it is smaller than the ramp numbers promise.** The cost is the number
to watch: AgX has a true toe, so 3.67% of the frame lands at or under 1 CV where the old curve put
0.21% (1.98% of that is the curve, the rest is shot noise clamped at zero). The game path still has
1.5x this frame's local contrast, so the display transform was not the only thing that was flat.

### Installing the grade as a Blender View, and the allowlist that eats it

`eft_grade.py` finishes rendered EXRs, which is the right way to produce frames and tells you
nothing while you work: the viewport still shows AgX, so look-dev happens under a transform the game
does not use. `tools/blender/make_ocio_config.py` fixes that by installing the chain as a real
OpenColorIO **View**, "EFT Game Grade", next to AgX, Filmic and Standard, so the viewport, the render
window and any saved 8-bit image all agree with the viewer.

```
python tools/blender/make_ocio_config.py --out <dir>       # numpy only, runs OUTSIDE Blender
set OCIO=<dir>/config.ocio                                 # then start Blender
```

It copies Blender's bundled `colormanagement` directory (it never modifies the install and refuses
to write over it), writes one `.cube` into the copy's search path, and appends one ColorSpace plus
one View per display:

```
RangeTransform + ExponentTransform     p = sqrt(clamp(lin/4, 0, 1))
eft_grade.cube                         p -> graded LINEAR, the pack's 64-cube with its
                                       display encode inverted
```

**THE TRAP, and it costs an afternoon: `active_views:` is an ALLOWLIST.** It is not a display-order
hint. OCIO drops every view whose name is not in that list **silently, with no validation error**,
so a perfectly valid View plus ColorSpace pair is simply never offered and setting `view_transform`
fails with "enum not found". The symptom is indistinguishable from a malformed colorspace, which is
where the afternoon goes. Two corollaries: no `active_views:` key at all means "every view is
active", so only a PRESENT list needs editing; and the config must be self-checked by asking OCIO
whether the view is active, never by parsing the YAML back.

**The second silent one: the YAML keys are snake_case and the Python API is camelCase.**
`RangeTransform` takes `min_in_value` / `max_in_value` / `min_out_value` / `max_out_value` in the
config file, while the same parameters are `minInValue` and friends through `PyOpenColorIO`. Writing
the camelCase spelling into the YAML emits a warning on stderr and then yields a config that
**loads and validates**, and only throws when something finally asks for a processor. So `selfcheck`
builds a processor and compares numbers against `eft_grade.py` rather than trusting `validate()`.

Three more decisions in that file, each of which is the opposite of the obvious one:

- **The shaper is analytic, not a baked 1D LUT.** A 4096-entry `.spi1d` sounds like plenty and is
  not: `sqrt` is vertical at the origin, so linear interpolation across the first table interval is
  wrong by up to 0.0039 in shaper space, which is visible banding in the deep shadows - exactly
  where this grade puts its lift. The `RangeTransform` + forward `ExponentTransform` pair reproduces
  the shader to **7e-6** and needs no file.
- **The cube is written `interpolation: linear`**, not the tetrahedral OCIO would prefer, for the
  same reason the game path uses trilinear above: the authority is a hardware sampler.
- **The `.cube` is written RED fastest, then green, then blue**, which is exactly the order the
  loader's `[b][g][r]` array iterates in, so no transpose is needed. One would be silent.

Three limits are inherent and worth knowing before you trust the viewport: **no vignette** (it is a
spatial effect and a colour transform cannot express one, so the viewport is slightly brighter at
the corners than a finished frame); **exposure is separate** (set Color Management > Exposure to
`log2(1.35) = 0.433` to match `DEFAULT_GRADE_EXPOSURE`); and **sRGB display only** (the grade ends
in a hardcoded sRGB encode because that is what the game does, so on Display P3 or Rec.2100 it emits
sRGB-encoded values into a display expecting something else). Use `eft_grade.py` for final frames;
this is for working.

## Verifying against the viewer

The viewer is the reference implementation: it reads the pack's fields the way the pack means them.
Any external renderer can be checked against it directly, and this is what found both the `specMap`
regression and the false alarm above.

Drive it to the identical camera:

```
EFT_CLEAN=1                       # no HUD or panels in the frame
EFT_POSE="x,y,z,yaw,pitch"        # PACK space; yaw = atan2(-dx,-dz), pitch = asin(dy), degrees
EFT_GAME_FOV=<vertical degrees>   # the projection's VERTICAL fov
EFT_CAM=fly EFT_RENDER=gpu
EFT_HIDDEN=1 EFT_HIDDEN_ALLOW=1   # render with no window
EFT_SHOT=<out.png> EFT_SHOT_EXIT=1 EFT_SHOT_SETTLE=240
```

Then, on the other side: same vertical FOV, crop the wider frame to the viewer's aspect rather
than squashing it, and grade both through the same chain. Confirm the alignment by
cross-correlating edge maps before comparing anything - a coordinate error and a material error
look nothing alike once you know the frames register (this pipeline's conversion checks out at
dx=0, dy=0).

Two things that will otherwise waste an afternoon:

- `EFT_AUTO_EXPOSURE` defaults to **off**, so the viewer uses the constant `DEFAULT_GRADE_EXPOSURE`
  = 1.35. If you enable it, its eye adaptation makes exposure view-dependent and no fixed number on
  the other side can match.
- Compare on MATCHED PIXELS, not on eyeballed patches. Pick the reference's extreme pixels for the
  feature (its reddest, its most saturated) and read the same coordinates in the other render.

## Practical lights

A sun lights the outdoors and nothing else. Every ceiling panel, strip light and sign is a Unity
light object, and Interchange ships 1,659 of them. Skip them and interiors are black; a shop seen
through a window becomes a flat dark rectangle that is easy to misread as broken glass.

Read **every** sidecar in `manifest.sidecars.lightsAll`, not just the one named by `sidecars.lights`
(that is only the primary scene), and de-dup by filename. Drop a record the way the renderer does:
`on == false`, `intensity <= 0` or `range <= 0` contributes nothing. `direction` is already the
extracted forward vector, so the quaternion is never needed; a spot aims its local -Z there.

Energy needs converting, not copying. The renderer accumulates
`color * intensity * light_scale * (1-(d/r)^2)^2 / d^2` against N.L with no `1/pi`, while a path
tracer's lambert does divide by pi and a lamp of P watts delivers `P/(4.pi.d^2)`. Matching the
far-field term gives

    watts = 4 * pi^2 * light_scale * intensity          # light_scale 6.0 in this repo

which is why the wattages look large. A spot uses the same number: spot power is defined as the
equivalent point power, so narrowing the cone does not brighten it. `spotAngle` and
`innerSpotAngle` are both FULL angles in degrees, giving `blend = 1 - inner/outer`.

`range` has no path-traced equivalent. Unity's window culls the light hard at `range`; inverse
square never reaches zero. Set `cutoff_distance` for the rasterised preview and accept a slight
far-field lift in the path tracer.

Ignore `shadowType`. It is a Unity performance decision, not an artistic one: 1,626 of
Interchange's lights carry `None` because real-time shadow maps are expensive, and a path tracer
that honours it leaks every interior lamp through the walls into the street.

## Scripts

`tools/blender/` holds runnable importers; each is standalone and imports nothing from this repo
(`make_ocio_config.py` is the one exception: it imports `load_grade_lut` from `eft_grade.py`, which
sits beside it). This table is the whole of `tools/blender/*.py`; if a file is not here, the table
is stale. `tools/blender/README.md` is the operational companion to it: prerequisites, the order
the scripts run in, how to run either mode, and the external-process bridge.

| script | runs | what it does |
|---|---|---|
| `import_eftpack.py` | in Blender | map geometry, materials, terrain slice, shear baking, the AABB region filter, the coplanar 6 mm lift, and the ported shader families: glassTRS, puddle/deep water, parallax, the vert-paint splat, detail albedo. Derives two probes from the pack and the world for glassTRS: the sky's L1 SH (`_sky_probe`) and the SH volume's dominant light (`_dom_light`). Carries the two photoreal switches, `glass_mode=` and `cavity_dir=` |
| `import_eftchar.py` | in Blender | armature, skinning, clips as Actions; publishes `eft_q4` |
| `import_eftweap.py` | in Blender | weapon mesh and bone attachment |
| `import_eftgrass.py` | in Blender | the grass field from `grass.bin`. `wind=True` ports the pack's own WavingGrass stage as a Geometry Nodes modifier; it defaults OFF so existing callers keep building byte-identical geometry |
| `import_eftlights.py` | in Blender | the practical lights from every `lights_*.json` sidecar |
| `terrain_splat.py` | in Blender | rebuilds terrain materials as the real MicroSplat blend |
| `cine_camera.py` | in Blender | solves a follow camera over the whole shot (Viterbi over candidate positions), plus `cinematic_render_settings`. `blades=` sets the iris; note that `cinematic_render_settings` has no caller in the repo |
| `example_scene.py` | in Blender | end-to-end: map, terrain, grass, lights, character walking a patrol, camera, Cycles. Carries `MODE` and the `MODES` table ([Two modes, one builder](#two-modes-one-builder)) |
| `nav_route.py` | anywhere | routes between points on the pack's baked nav grid; numpy only, no `bpy`. A PORT of the viewer's router, not an approximation |
| `eft_grade.py` | anywhere | the game's display chain (exposure, shaper, 64-cube LUT, vignette) applied to linear EXR, plus the AgX and filmic A/B looks and the photoreal metering/vignette/grain stages. numpy only, no `bpy`. `--selfcheck-agx` diffs its AgX against Blender's own OCIO |
| `bake_cavity.py` | OUTSIDE Blender | Poisson-integrates every normal map the pack ships into a multi-scale cavity map, for `import_eftpack(cavity_dir=)`. Needs PIL, which Blender does not ship. **PHOTOREAL ONLY**: the game shader has no AO term at any scale, so never point a parity build at the output |
| `make_ocio_config.py` | OUTSIDE Blender | writes an OCIO config that installs the game grade as a selectable Blender View ([Installing the grade as a Blender View](#installing-the-grade-as-a-blender-view-and-the-allowlist-that-eats-it)) |
| `make_sky_equirect.py` | OUTSIDE Blender | cubemap faces to an equirectangular world texture |

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
| The character walks through a truck, container or wall | patrol waypoints joined by a straight line instead of routed on the nav grid |
| The character snaps between 45-degree facings while walking | heading taken from the current 0.5 m grid segment instead of a lookahead |
| A black wedge over most of the frame, subject nowhere in it | the camera is INSIDE geometry: a fixed offset with a "shove sideways when stuck" escape, which shoves it deeper whenever sideways is inward |
| The subject disappears behind posts and clutter the camera "checked" | occlusion tested with a single ray to the chest instead of several body points |
| A re-render is 10x slower than the build that made it | `scene.cycles.device` is saved in the .blend but the ENABLED DEVICES are an addon preference; `--factory-startup` resets them and Cycles falls back to CPU silently |
| Terrain soft and smeared close up | the baked albedo slice used instead of the MicroSplat splat |
| The whole frame renders black in Cycles | an unbounded world Volume Scatter |
| Puddles the wrong shape, or a sheet over the road | the mask channel hardcoded instead of probed |
| The sea invisible | untextured water treated as a puddle with no mask |
| Roads one flat tiled texture instead of a gravel/sand mix | the Vert-Paint splat collapsed to layer 0 |
| Bright paint (a red vehicle, a painted sign) renders grey or black | `specMap` bound as a per-texel gloss map; where it IS the albedo file this makes paint glossy and it mirrors the sky. Use the scalar `roughness` |
| Saturated things look pale and washed out, materials check out fine | OVEREXPOSURE - the grade desaturates above linear 1.0. Compare a channel RATIO against the texture before suspecting the material |
| Exposure matches in one shot and is wrong in the next | a baked one-bounce SH volume vs full path-traced GI; fit exposure per shot and hold it |
| Everything uniformly matte, no wear variation | the opposite error: `roughnessFromAlbedoAlpha` ignored, so per-pixel roughness never comes from `tex.a` |
| MicroSplat weights mirrored against the terrain | the control-map V flipped a second time |
| Interiors black; a lit room through a window reads as a flat dark rectangle | the practical lights never imported, or only the primary `lights` sidecar read instead of `lightsAll` |
| Interior lamps lighting the street through the walls | `shadowType` honoured; it is a Unity performance flag, not an artistic one |
| Glass panes render LIGHT, washed out or milky over a dark interior, and the brighter the world the worse it gets | the environment term built as a BSDF (`Diffuse BSDF`, Color = `reflectColor * fresnel`) and left for Cycles to trace. The family's input is `texCUBE(_Cube)`, an LDR image, so `refl < _ReflectColor` ALWAYS; unbounded traced HDR radiance ignores that ceiling and the pane came back 5.4x too light. Evaluate the probe as a VALUE, Reinhard it (`gpu_draw.wgsl:1632`) and emit it. See [Both additive lobes are bounded values](#both-additive-lobes-are-bounded-values-and-neither-is-a-bsdf) |
| A pane carries a broad grey sheen and reads brighter than the opaque wall beside it | the Blinn lobe handed to a Cycles glossy distribution: energy-normalised where `pow(NdotH, n)` is not, and lit by the whole world where the game points it at ONE light. 86% of the pane's radiance arrived that way, 89% of it world rather than sun |
| Glass reflects a flat neutral tint with no sky gradient anywhere in the map | `_sky_probe` found no equirect and fell back to the flat world colour (it warns, `import_eftpack.py:931-937`). The bound still holds; only the gradient is lost. Build the world first or keep `shared/sky/*_equirect.png` in the pack |
| Storefront and car glass reads as a flat dark slab; a clear pane never mirrors the sky | the TRS terms hung on a Principled, whose Alpha scales reflection and glint along with the diffuse. Only the DIFFUSE is coverage-scaled |
| Glass roughly 1.3x too opaque across a whole pack | TRS coverage built from the raw `tex.a`. `tint.a` enters TWICE, once in `albedo.a` and again in `trs_a` |
| Bullet-holed or shattered panes still mirror the sky as ghost glass | the `trs_a < 0.03` discard implemented as an Alpha value instead of a Transparent gate over the whole tree |
| A handful of panes per pack wear a much broader, duller glint than the viewer's | absent `shininess` defaulted to 0.0 instead of Unity's 0.078, so the Blinn exponent is 1.28 instead of 9.997 (4 of 1,057) |
| Whole rows of glass panes render as near-holes | correct: 21 TRS materials ship `opacityScale` 0, which quantizes to 0 and hits the shader's own 0.03 floor |
| Relief pops OUT of the wall instead of into it | the height map's `g` used directly instead of `1 - g`, or the bitangent handedness guessed the wrong way (`EFT_PARALLAX_SIGN=-1`) |
| Relief doubled, and strongest head-on where parallax should vanish | a Bump node added alongside the parallax UV offset; the viewer's parallax perturbs no normal |
| Wear and grime sit BESIDE the relief instead of on it | `puv` rewired onto the albedo only; `tex.a` must ride it too, and 137 of the 139 parallax materials are RFA |
| A road strip is a uniform hard-edged water slab | the baked UVs kept on a stretched water submesh instead of the 6 m/tile planar re-projection |
| Wet-ground and tire-mark decals mirror the sky like real puddles | `MAT_FLAG_WATER_MATTE` never classified, or classified lazily per mesh instead of in a pre-pass |
| Puddles carry the atlas's own colour and read darker than the viewer | known divergence: Base Color stays LINKED to the albedo texture, so the shader's `wet = tint.rgb * gi` never takes effect. See [Water](#water-is-two-materials-wearing-one-role) |
| The sea is a mirror-flat plate, or streaked with wide dark bands | the deep-water world-space normal not built (it is derived from world up, never the mesh normal), or its wave map bound as an ordinary tangent-space normal |
| Sea waves frozen in an animation | the water time Value node has no `frame / fps` driver, so the phase is stuck at t = 0 |
| An unpainted vert-paint face washes out to an even three-way mix | the empty-mask gate tested against the post-`pow` normaliser instead of the raw `hs` sum; only bites the 11 materials with `blend <= 1` |
| Vert-paint slabs go near-black in patches | the near-black resolve missing, or built with Rec.709 luma (`RGB to BW`) instead of the literal Rec.601 weights |
| Vert-paint weights subtly wrong everywhere, worst on lightly painted faces | `COLOR_0` read through a Color Attribute node without re-encoding to sRGB bytes |
| Every vert-paint road uniformly matte, or uniformly at its authored gloss | `1 - 0.30*vp_smooth` treated as a floor on the scalar instead of an assignment that REPLACES it |
| Rock and cliff surfaces read flat and low-frequency | detail albedo skipped, or applied without dividing `albedoMeanGain` back out, which shifts the whole surface instead of adding contrast |
| Roads, decals and puddles stipple, and the speckle pattern changes with the camera | coplanar overlays not lifted; the renderer's clip-space push has no path-traced equivalent |
| Black patches inside bushes and hard black wedges through the grass field, and the frame reads globally dark on top of that | `transparent_max_bounces` exhausted. It is a WHOLE-PATH budget and Cycles fails closed, so camera rays terminate black and shadow rays report fully occluded. At the default 8, 14.78% of the frame is exactly 0.0 luma. Set 256 ([Foliage transparency](#foliage-transparency-is-a-whole-path-budget-not-a-quality-knob)) |
| The ground is missing under the shot, props float over a void, and `terrain_splat` prints `rebuilt 0 terrain material(s)` | the region filter tested a mesh CENTRE against the disc. Terrain ships as ~700 m tiles whose AABB contains the camera while the centroid is 163 m away. Test the world-space AABB, and derive it exactly rather than sampling ([The region filter](#the-region-filter-tests-an-aabb-never-a-centre)) |
| Terrain missing in a slightly different way after switching to a bounding sphere | a sphere is far too loose on a flat tile (857.5 m on a 700 m plate) and admits every terrain tile at any radius, so the "fix" re-imports the whole map |
| `MODE = "photoreal"` writes a completely black EXR and the render returns in a fraction of a second | the compositor group is fed from Group Input. The group's input socket is NOT the render result; a `CompositorNodeRLayers` has to sit INSIDE the group. Nothing warns |
| A depth-driven compositor effect silently does nothing | `view_layer.use_pass_z` was set after the Render Layers node was created, so it never grew a `Depth` output |
| `nodes.new("CompositorNodeMath")` raises "Node type undefined" | 5.1 unified it; the compositor takes `ShaderNodeMath`, while Separate/Combine Color are still `CompositorNode*` |
| The grade View never appears in Blender's colour-management menu, and setting `view_transform` fails with "enum not found" | `active_views:` is an ALLOWLIST and OCIO drops unlisted views silently, with no validation error. Indistinguishable from a malformed colorspace ([Installing the grade as a Blender View](#installing-the-grade-as-a-blender-view-and-the-allowlist-that-eats-it)) |
| An OCIO config loads and passes `validate()`, then throws the moment something asks for a processor | a transform key written in the Python API's camelCase (`minInValue`) where the YAML wants snake_case (`min_in_value`). It is a warning on stderr and nothing more until far too late |
| Banding in the deep shadows through the OCIO view only | the `sqrt` shaper baked as a 1D LUT. `sqrt` is vertical at the origin, so the first table interval is wrong by up to 0.0039 even at 4096 entries. Use the analytic Range + Exponent pair |
| `--look agx` matches on greys and is visibly off on saturated colour | the AgX cube sampled trilinearly. It is strongly non-planar off the neutral axis; trilinear peaks at 14.29 CV of error where tetrahedral peaks at 0.005. The game's own 64-cube is the opposite case and must stay trilinear |
| The whole frame is about 2.4 EV out after switching `--look` | `auto_exposure(mode="grey")` metered against the other look's anchor. `GREY_IN` is per look: agx 0.17465, filmic 0.03202 |
