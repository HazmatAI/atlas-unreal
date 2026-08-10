# Lowering game fidelity to raise photographic fidelity

[game-parity.md](game-parity.md) makes an external renderer produce the GAME'S image.
[photorealism.md](photorealism.md) abandons the game's grade and light transport for a photographic
one, but still renders **the game's data as shipped**: every texel, every normal, every polygon is
the one the pack carries. Both files treat the pack as ground truth and argue only about what is
done downstream of it.

This file is the third mode, and it is the one that stops doing that. It **edits the assets** to add
information the game never had, because the game never needed it: a rasteriser that cannot compute
occlusion has it painted in, a rasteriser with no way to round an edge ships perfect 90 degree
corners, and a rasteriser that cannot tessellate ships a road as two triangles. Each of those is a
correct engineering decision for a real-time renderer and a defect in a path trace. Removing them
produces a better photograph and a worse match to the game, and there is no version of this mode
that is also parity. It is the most photorealistic tier and the least faithful, in that order and
for that reason.

**It is a superset of `photoreal`, not an alternative to it.** Everything in
[photorealism.md](photorealism.md) still applies underneath: physical sky, filmic view transform,
real transmissive glass, camera and comp. The three levers below are added on top of that, and none
of them pays off on an 8-bit clipped sky.

**Marking convention.** Every count and every measurement below was taken this session against the
shipped packs and is reproducible from the pack files; those tagged **[session]** are one-off timings
on this machine. Render-time figures are **estimates and are marked as such**: none of the three
levers has been rendered yet, and an estimate that is presented as a measurement is how the sun/sky
fit got a 16% and a 20% in two different files that still do not reconcile
([photorealism §1](photorealism.md#1-the-sky-is-8-bit-ldr)).

## Contents

- [Where this sits, and the one line it crosses](#where-this-sits-and-the-one-line-it-crosses)
- [The order, and why it is this order](#the-order-and-why-it-is-this-order)
- [1. Bevel every hard edge](#1-bevel-every-hard-edge)
- [2. De-light the albedos](#2-de-light-the-albedos)
- [3. Displacement on hero surfaces](#3-displacement-on-hero-surfaces)
- [Composing with the MODE switch](#composing-with-the-mode-switch)
- [What this mode does not change](#what-this-mode-does-not-change)
- [Recommended implementation order](#recommended-implementation-order)
- [How to tell if it worked](#how-to-tell-if-it-worked)

---

## Where this sits, and the one line it crosses

The line is **provenance**. Every number in the other two files is either read from the pack or
derived from the pack by a stated rule. Two of the three levers here introduce numbers that are
neither: a bevel radius and an albedo floor are physical priors imported from outside the data.
That is allowed in this mode and nowhere else, and every such number is labelled INVENTED below so
that a future reader can tell in one pass which claims are about EFT and which are about concrete.

The scene these numbers are sized against is the one `tools/blender/example_scene.py` builds:
`packs/interchange.eftpack` (7,920 meshes, 68,418 instances, 5,765 materials, bounds
1415 x 115 x 1756 m), filtered to `MAP_RADIUS = 150` m around the `ZonePowerStation` patrol
(`example_scene.py:45-49`). Measured on that selection:

| | R = 150 m | R = 250 m |
|---|---|---|
| objects | **4,465** | **9,577** |
| shared mesh datablocks | 1,017 | 1,846 |
| sharing factor | 4.4x | 5.2x |
| unique triangles | 2.59 M | 5.03 M |
| triangles after instancing | 7.91 M | 19.30 M |
| distinct materials | 1,070 | 1,660 |
| of which opaque / decal / cutout / glass / water | 900 / 93 / 46 / 28 / 3 | 1,314 / 249 / 58 / 35 / 4 |

The "~10,000 objects" figure this pipeline quotes is the R = 250 build. Across the six shipped packs
there are **43,470 materials**: 33,668 opaque, 6,320 decal, 2,044 cutout, 1,248 glass, 190 water.

---

## The order, and why it is this order

Ranked by payoff per unit of RISK, not per unit of work. All three are cheap to write; they differ
enormously in what they can break.

| # | Lever | Build cost | Render cost | What it can break |
|---|---|---|---|---|
| 1 | Bevel every hard edge | hours | est. 5-15% | nothing in the data; one constant, reversible by setting it to 0 |
| 2 | De-light the albedos | days | zero at render time, 6.7 GB of disk | the alpha plane, which is per-texel ROUGHNESS on 3,608 of interchange's 5,765 materials |
| 3 | Displacement on hero surfaces | days | unbounded if unrestricted | the build itself: adaptive subdivision de-instances shared meshes, and this scene shares them 4.4x |

**Why bevel first even though de-lighting is the bigger correction.** Lever 2 is the only one of the
three that is a genuine *correction* rather than an addition, so on argument it should lead. It does
not, because it is also the only one that can silently corrupt 63% of the pack's materials through a
channel that nothing in the image points at. Lever 1 touches 33,668 materials, changes every opaque
surface in every shot, needs no new data, no cache and no memory, and cannot damage anything: if it
is wrong, it is visibly wrong and it is one constant away from off. Ship the safe change first,
build the measurement habit on it, then spend that habit on the dangerous one.

**Why displacement last.** Its selection depends on the solved camera, which does not exist until
step 7 of `example_scene.py:294-304`. It is also the only lever whose cost can go superlinear with
no warning.

---

## 1. Bevel every hard edge

### The physical justification

There are no perfectly sharp edges. A machined steel edge breaks at 0.2 to 0.5 mm, a cast concrete
arris chips to 2 to 5 mm within a season, a painted steel panel carries 0.5 to 1.5 mm of paint
build-up at the fold. Every one of them catches a specular highlight that runs the length of the
edge, and that highlight is most of what tells the eye an object is a real manufactured thing rather
than a rendering of one. A game mesh has none of it, because the triangles that would carry it are
not worth spending.

**How much of this geometry is affected, measured.** Over 120 sampled meshes, 889,005 edges and
398,495 manifold edge pairs from `packs/interchange.eftpack`:

| dihedral | share |
|---|---|
| 0 to 5 deg | 45.7% |
| 5 to 20 deg | 12.2% |
| 20 to 45 deg | 12.4% |
| 45 to 60 deg | 4.3% |
| 60 to 80 deg | 3.9% |
| **80 to 100 deg** | **19.5%** |
| 100 to 180 deg | 1.8% |

That 19.5% spike in the 80-100 band is the right angle, and it is the single largest non-flat class
in the corpus. 36.4% of edge pairs exceed 30 degrees. This is not a subtle correction applied to a
few props; it is a fifth of the map's edges.

### The concrete implementation

Cycles' Bevel node rounds the SHADING normal at render time by ray-sampling the surrounding surface
inside a world-space radius. It adds no geometry, no memory and no build time. It has inputs
`Radius` and `Normal`, a `Samples` property (default 4) and a single `Normal` output; the output is
the shading normal everywhere except near an edge.

The importer currently ends the normal chain by wiring the Normal Map node straight into the
Principled (`tools/blender/import_eftpack.py:2150-2155`):

```
ntex(DirectX) -> [green flip] -> ShaderNodeNormalMap(TANGENT, Strength=normalScale)
                                          -> bsdf.inputs["Normal"]                    # :2154
```

The bevel goes **after** the normal map, not before and not instead:

```
... ShaderNodeNormalMap -> ShaderNodeBevel.Normal
    ShaderNodeBevel(Radius=0.002, Samples=4).Normal -> bsdf.inputs["Normal"]
```

**Why that order and not the other one.** The Bevel node's `Normal` input is the base it bevels
*from*. Leave it unconnected and the node falls back to the surface shading normal, which throws
away the normal map entirely on every one of interchange's 874 normal-mapped materials: the map
would still be loaded, still be green-flipped, still be scaled by `normalScale`, and never reach the
BSDF. That is the `specMap` shape of bug again - a channel that is computed, looks connected, and is
dead ([game-parity §4](game-parity.md#4-channel-rules-that-have-actually-bitten)). Connect it and the
bevel is applied on top of the mapped normal, which is what is wanted.

For materials with no normal map (interchange ships 1,208 albedos against 874 normals, so a
meaningful minority have none) the Bevel node is inserted with its `Normal` input unconnected, which
is correct: the base is then the shading normal, exactly as before.

**Where in the code.** Two lines at `import_eftpack.py:2154`, plus a bevel insertion for the
no-normal-map case, plus a role gate. The switch follows the file's existing pattern - an
environment variable read once in `_Importer.__init__` beside `parallax_on` (`:388-393`):
`EFT_BEVEL=<radius in metres>`, `0` disables. That keeps every lever a one-variable A/B, which is how
`EFT_PARALLAX` already works (`blender-import.md` "Import switches and the run summary"), and keeps
the importer standalone.

### The radius, and why 2 mm

The radius is **INVENTED** - the pack carries no edge-radius field and nothing in it implies one -
but it is not free. Three separate constraints bound it, and they converge:

- **It must be smaller than `DECAL_LIFT`.** The importer lifts decal and water vertices 6 mm along
  their normal to replace the renderer's clip-space push (`DECAL_LIFT = 0.006`,
  `import_eftpack.py:128`, applied at `:2490-2499`). The bevel filter cannot tell an edge from two
  separate surfaces that happen to pass within its radius of each other. At radius 6 mm the ground's
  bevel query starts hitting the decal plane floating above it and vice versa, and both surfaces get
  their normals smeared along the seam.
- **It must be smaller than the fine end of the edge-length distribution.** Measured over 889,005
  edges: p1 = **1.9 mm**, p5 = **4.5 mm**, p25 = 15 mm, p50 = 40 mm, p90 = 709 mm. A radius above the
  p5 length turns the bevel into a general normal blur on the densest meshes instead of an edge
  treatment.
- **It should sit in the physical range** for the materials that dominate the frame, which here is
  concrete, painted steel and asphalt.

**2 mm** satisfies all three: a third of the decal lift, under half the p5 edge length, and inside
the real arris range for cast concrete. About 1% of edges are shorter than the radius; on those the
node degenerates to a smooth-shading filter, which is harmless but is the first thing to check if a
dense prop reads waxy.

### Interaction with the custom split normals

The importer sets every face smooth (`import_eftpack.py:2460`) and then writes the pack's per-vertex
normals as custom split normals (`me.normals_split_custom_set_from_vertices(n2)`, `:2471`). EFT, like
every Unity exporter, represents a hard edge by DUPLICATING the vertices and giving each copy a
different normal, so the pack's authored smoothing groups are already in that array.

This matters twice, in opposite directions:

- **The base normal is the split normal, which is what you want.** Geometry > Normal in Cycles is the
  shading normal, already back-face flipped, and the importer's own comment says so at
  `import_eftpack.py:716`. So the Bevel node's fallback base is the game's authored smoothing, not the
  face normal. A low-poly cylinder that the pack shades smooth stays smooth.
- **The bevel sampling is over the GEOMETRY, which is not what the split normals say.** At a
  duplicated-vertex hard edge the geometry really does turn 90 degrees, so the node finds the edge
  and rounds it - correct. But at a *smoothed* faceted surface, the geometry also turns, and the node
  will round facet boundaries the pack deliberately shaded flat across. At 2 mm on a cylinder whose
  facets are 40 mm wide this is invisible; on a 1.9 mm facet it is a 100% effect. Same failure as the
  first bullet, same detector below.

### Which roles to exclude, and why each

The gate is `role`, which the importer already has in hand at `_build_material`
(`import_eftpack.py:1507`). Excluded across the six packs: 9,802 of 43,470 materials, 22.5%.

| role | count (6 packs) | why excluded |
|---|---|---|
| `decal` | 6,320 | They are coplanar overlays lifted 6 mm off a receiver they are meant to be flush with. A bevel on a decal quad rounds the boundary of a *painted marking*, which has no thickness and therefore no edge to catch light. Worse, the receiver is 6 mm away, inside three radii, so the query is contaminated. |
| `cutout` | 2,044 | The silhouette of a leaf is defined by the alpha test, not by geometry. The geometry is a flat card whose real edges are its rectangle corners, so a bevel highlights the CARD, which is precisely the thing that must not be visible. Foliage is also built as double-sided coincident triangles over one deduped vertex block (`import_eftpack.py:2416-2424`), so the front and back faces are at distance ZERO and the query averages a normal with its own negation. |
| `glass` | 1,248 | Same coincident-card construction, measured: one 2204-triangle glass mesh contributed 258 of the 522 coincident faces on a 192-mesh slice (`import_eftpack.py:2416-2424`). Beyond that, a glass pane's edge is genuinely sharp because it is a cut edge, and the glassTRS tree at `:1030-1366` composes four lobes against a normal it took as a parameter (`nmap_node`, `:1030`); inserting a bevel would change what all four see. |
| `water` | 190 | Puddles are a film lifted 6 mm off the ground; deep water builds its normal from world up plus the ripple and never touches the mesh normal at all (`import_eftpack.py:2122-2124`, `_deep_water_normal` at `:1367`). There is no edge and no normal to bevel. |

`terrain` is not a material role (roles are opaque / decal / glass / cutout / water); terrain is an
instance flag, and in the example build it is rebuilt by `terrain_splat.py` anyway. It gets no bevel
because it has no hard edges.

That leaves the **33,668 opaque materials** (900 of the 1,070 in the R = 150 build) carrying the node.

### Cost

**Build time: zero.** One node and one link per opaque material. On the R = 150 build that is 900
extra node pairs, well under a second against the import's existing elapsed.

**Render time: estimated 5 to 15%, NOT MEASURED.** The node is a ray-traced query - 4 samples per
shading evaluation by default - so its cost scales with the number of shading events on bevelled
materials, not with resolution or with sample count directly. On a shot dominated by opaque
architecture, most shading events are on bevelled materials. The honest statement is that this
number has to be measured on one shot before it is quoted anywhere, and that `Samples = 4` is the
first knob to turn if it comes in high: the noise it produces is spatially tiny (a 2 mm band) and the
denoiser already configured at `cine_camera.py:334-341` handles it well.

### Failure modes and how to detect them

1. **False bevels where two surfaces pass close.** The generic form of the decal case. Detector: add
   a temporary AOV of `acos(dot(bevel_N, geometry_N))` and threshold at 30 degrees. Every pixel above
   threshold should lie in a band no wider than 2 mm of world space along an actual edge. A blob in
   the middle of a flat wall is two surfaces within a radius of each other.
2. **The normal map silently dropped.** If the Bevel `Normal` input is left unconnected on a
   normal-mapped material, the image gets *smoother*, not sharper. Detector: render one
   normal-map-heavy material with `EFT_BEVEL=0` and with the bevel at radius 0.0001 and confirm the
   two are effectively identical. If radius-near-zero differs from bevel-off, the base normal is
   being discarded.
3. **Waxy dense props.** The 1% of edges under 1.9 mm. Detector: the same AOV, looking for whole
   surfaces above threshold rather than bands.
4. **The node is Cycles-only.** An EEVEE viewport preview silently ignores it, so a viewport A/B
   lies. Only compare F12 renders.

### How to verify it improved rather than merely changed

Difference two linear EXRs of the identical camera, bevel on and off, and ask **where the energy
went**. Three tests, in order:

1. **Localisation.** The absolute difference must be concentrated in thin bands along edges. Compute
   the fraction of changed pixels (>1% linear) and their spatial distribution. Bands: correct.
   Diffuse haze over faces: the radius is too big or the base normal was dropped.
2. **Sign and direction.** The bevel adds a highlight, so on edges facing the key light the
   difference is positive and on edges facing away it is near zero. A difference that is uniformly
   negative means the node is being used as a normal blur.
3. **The grazing test.** The whole physical claim is about a highlight that runs the length of an
   edge, and that highlight is strongest at grazing incidence to a bright source. Shoot one frame
   with the sun near the edge's grazing plane. If the bevel does not clearly win there, it will not
   win anywhere, and 5 to 15% of render time is buying nothing.

Negative control: `EFT_BEVEL=0` must reproduce the pre-change render bit for bit.

---

## 2. De-light the albedos

### The physical justification, stated precisely

An albedo map is supposed to be a **material property**: the fraction of incident light a surface
reflects, independent of how it is lit. A game's albedo maps are not that. A rasteriser cannot
compute ambient occlusion or contact shadowing per frame, so the artist paints it in, and the
texture becomes reflectance multiplied by a low-frequency lighting term.

A path tracer then computes that occlusion **from the geometry**, correctly, and multiplies it in
again. The surface is darkened twice. This is why de-lighting is a genuine **correction** and not a
stylistic addition: the pipeline is applying one physical effect twice, and removing one copy makes
the render *more* physically consistent, not less.

**The error is amplified by the bounce series, which is the part that gets under-estimated.** A
diffuse surface in a closed environment returns light as the geometric series `rho / (1 - rho)`:

| rho | rho / (1 - rho) |
|---|---|
| 0.155 (measured median of interchange's albedos) | 0.1834 |
| 0.18 | 0.2195 |
| 0.20 (physical floor for concrete) | 0.2500 |
| 0.25 (typical concrete) | 0.3333 |
| 0.35 (concrete ceiling) | 0.5385 |

The measured median surface returns **45% less** interreflected light than a plausible concrete
(0.1834 / 0.3333 = 0.55). In an open sunlit yard that mostly does not matter, because direct sun
dominates. In an interior, a stairwell or a shaded corner, interreflection *is* the light, and this
is exactly where the render reads flat and dirty. That is the mechanism, and it predicts *where* the
fix should show up, which is what makes it testable.

### What the pack actually ships, counted

| pack | texture files | bytes | distinct albedos referenced | albedo megapixels | albedo bytes (share of tex) |
|---|---|---|---|---|---|
| interchange | 2,707 | 2.65 GB | 1,208 | 1,017 MP | 1.67 GB (63%) |
| ground_zero | 3,906 | 3.98 GB | 1,610 | 1,567 MP | 2.28 GB (57%) |
| woods | 2,879 | 4.05 GB | 1,260 | 1,400 MP | 2.40 GB (59%) |
| icebreaker | 527 | 0.73 GB | 197 | 239 MP | 0.36 GB (49%) |
| **total on disk** | **10,019** | **11.41 GB** | **4,275** | **4,223 MP** | **6.71 GB** |

`factory_rework` and `streets_nav` ship no `tex/` directory in this checkout, so their 2,683 and
19,715 materials are not counted above. All files are PNG; the modal size is 1024x1024 (71 of a
160-file sample), then 512x512 (42), 2048x2048 (7).

The de-lighting is **per texture, not per material**: interchange's 5,765 materials reference 1,208
distinct albedos, 874 normals and 107 other maps (detail, vert-paint layers, heights, emissive),
2,154 in union. That 4.8:1 sharing is what makes an offline cache worth building.

### Measured evidence that it is really baked in

Mean linear luminance over **all 1,208** interchange albedo maps (sRGB decoded, Rec.709 luma):

| mean linear albedo | count | share |
|---|---|---|
| 0.00 to 0.05 | 176 | **14.6%** |
| 0.05 to 0.08 | 141 | 11.7% |
| 0.08 to 0.12 | 175 | 14.5% |
| 0.12 to 0.20 | 224 | 18.5% |
| 0.20 to 0.35 | 266 | 22.0% |
| 0.35 to 0.60 | 147 | 12.2% |
| 0.60 to 1.00 | 54 | 4.5% |

Median **0.1553**, p10 0.0371, p90 0.4754. **176 maps (14.6%) sit below linear 0.05**, which is
darker than charcoal and darker than any surface that appears on this map.

By material class, matched on filename:

| class | n | measured median | physical range | verdict |
|---|---|---|---|---|
| concrete | 28 | **0.1818** | 0.20 to 0.35 | **below the floor** |
| asphalt | 4 | 0.0767 | 0.08 to 0.12 | at or just under the bottom |
| grass / foliage | 7 | 0.1089 | 0.05 to 0.15 | in band |
| brick | 9 | 0.2048 | 0.20 to 0.40 | in band |
| metal / steel / rust | 55 | 0.0830 | wide, 0.05 to 0.60 | not diagnosable by class |

Individual concretes, mean linear: `Factory_Concrete_Slab_01_D` **0.1515**,
`Concrete_clean_02_d` **0.1703**, `Concrete_Smooth_d` **0.1815** - all under the floor.
`Custom_Concrete_rough_D` 0.2565 and `Concrete_smooth_diffuse` 0.2617 - in band. **The darkening is
per texture, not per pack**, which is itself the strongest evidence that it is authored occlusion
rather than a global colour-space error: a colour-space bug would move all 28 by the same factor.

Low-frequency content, measured as p98/p2 of a Gaussian-blurred (sigma = max(W,H)/16) linear
luminance:

| texture | LF ratio |
|---|---|
| `asphalt_crushed` | 1.22 |
| `Factory_Concrete_Slab_01_D` | 1.34 |
| `Concrete_clean_02_d` | 1.35 |
| `T_Asphalt_A` | 1.58 |
| `AM_Rock_04_D` | 3.33 |
| `AM_Rock_01_D` | 4.16 |
| `AM_Rock_02_D` | 4.89 |
| `AM_Rock_03_D` | 6.29 |
| `AM_Rock_06_D` | **6.95** |

Tiling grounds carry 1.2 to 1.6x of low-frequency variation. The rock maps carry 3.3 to **7.0x**.
Nothing about rock reflectance varies sevenfold at the metre scale; that is form shading, painted in.
The rocks are also the surfaces the 205 detail-map materials sit on, which is the one place the
pipeline **already** does exactly this correction in the other direction (see below).

### Where the correction must NOT be applied, measured

The low-frequency field is only lighting if the texture TILES. On a unique-UV atlas, the
low-frequency field carries **material identity** - this region is a red panel, that region is a
tyre - and dividing it out greys the asset.

Measured on a random 160-map sample, comparing the wrap-around seam discontinuity against the mean
interior gradient: **70 maps (44%) are seam-continuous** and therefore tiling; **90 (56%) are not**.
The distribution is strongly bimodal (p10 = 0.21, median 4.12, p90 = 14.99), which means a simple
threshold at ~3x separates them cleanly. That test is the gate, and it is derived entirely from the
texture with no naming heuristics, in the same spirit as `_classify_water_matte`'s geometry-only
puddle discriminator (`import_eftpack.py:2234-2277`).

### THE TRAP: the alpha plane is roughness

**3,608 of interchange's 5,765 materials (63%) set `roughnessFromAlbedoAlpha`.** The renderer's rule,
quoted in the importer at `import_eftpack.py:2003-2004` and ported at `:2019-2027`:

```
rough = clamp(m.roughness, 0.03, 1.0);
if (RFA) { rough = clamp(1.0 - tex.a, 0.06, 1.0); }
```

A further **112 MASK materials** read the same alpha as the cutout coverage (`:1568`, `:1983-1997`),
and the vert-paint smoothness term sums the three layer alphas (`vp_smooth`, `:2049-2058`).

So the albedo PNG's alpha channel is, across most of this pack, a **per-texel roughness map and a
silhouette**. A de-lighting pass that decodes RGBA, operates, and re-encodes will at minimum requantise
it and at worst drop it - and the symptom is not "the textures look wrong", it is "everything is
subtly the wrong gloss", which is the single hardest class of bug in this codebase to attribute
([game-parity §3](game-parity.md#3-exposure-is-colour-and-it-is-the-first-thing-to-rule-out)).
**Copy the alpha plane through byte for byte. Assert it, do not warn about it.**

### The concrete implementation: offline cache, not node graph

**It must be offline.** Three reasons, and the second is decisive:

1. The decision of whether to correct at all is per texture and depends on measurements OF the
   texture (seam ratio, mean, LF ratio). A node graph has no way to ask a question about an image.
2. **Cycles has no image-space blur node.** The low-frequency estimate cannot be computed in a
   shader at all. There is no node-graph version of this lever, only a worse approximation of it.
3. The cache is computed once and reused by 5,765 materials across every frame of every shot.

**Where it hooks in: one candidate path in `_resolve_tex`.** `import_eftpack.py:573-601` is the single
choke point through which every texture path in the file passes, and it already tries a list of
candidate locations in order. Prepending a cache directory when `EFT_DELIGHT=<dir>` is set is a
three-line change and the rest of the importer is untouched. `_image` (`:603-628`) keeps setting
`sRGB` / `STRAIGHT` for albedo exactly as now (`:621`, `:625`).

**The pass, step by step:**

1. Decode to linear. **[session]** 0.025 s per megapixel.
2. **Classify.** Seam ratio > ~3 means unique-UV atlas: skip the spatial divide entirely (it may
   still qualify for the class-floor gain in step 6). Seam ratio <= 3 means tiling: proceed.
3. **Estimate the low-frequency field** with a **wrapping** Gaussian at sigma = max(W,H)/16. Wrapping
   is not optional: a clamped blur invents a dark rim on a tiling map, and the divide then brightens
   the border into a visible tile seam that was not there before.
4. **Divide and restore the mean**, per channel: `out = lin / max(LF, eps) * mean(LF)`.
5. **Clamp the CORRECTION, not the result**, to [0.5, 2.0] - one stop either way. This is deliberately
   the same shape as the correction the pipeline already performs on detail albedos, where the
   detail map's own mean is divided out so it contributes only local contrast
   (`albedoMeanGain`, `import_eftpack.py:2092-2102`, mirroring `gpu_draw.wgsl:1318-1337`, documented
   at `blender-import.md` "Detail albedo"). That path clamps its ratio to [0.25, 4.0]; this one edits
   the BASE rather than a multiplier on top of it, so it clamps tighter.
6. **Range check against the class floor.** If the corrected mean is still below its class floor,
   apply a single **global scalar gain** and record it separately. A global gain is a much weaker
   claim than a spatial divide - it says "this texture is too dark", not "this texture has occlusion
   painted at these locations" - and conflating the two in one number is how a summary stops being
   evidence.
7. **Write 16-bit PNG**, alpha copied byte-exact. A de-lit map is a brightened map, and 8-bit
   quantisation in the shadows bands visibly once a filmic curve stretches the toe.
8. **Write a sidecar** recording source size, mtime and hash plus every number produced, and refuse
   to run the pass on its own output. Double-correction is silent and looks like over-exposure.

### Data provenance

| element | DERIVED or INVENTED |
|---|---|
| the low-frequency field | DERIVED, measured off the texture |
| the tiling / atlas classification | DERIVED, measured off the texture |
| sigma = max(W,H)/16 | INVENTED (a scale choice; defensible because road tiles map at 8.51 m median, so sigma corresponds to ~0.5 m of world, well above aggregate scale and well below marking scale) |
| the [0.5, 2.0] correction clamp | INVENTED |
| the class albedo floors (0.08-0.12 / 0.20-0.35 / 0.05-0.15) | INVENTED, imported physical priors |
| the class assignment itself | WEAK - matched on filename, which is the least defensible step in the whole lever |

### Cost, measured

**[session]** End-to-end on this machine, single-threaded, `Factory_Concrete_Slab_01_D` at 2048x2048:
decode 0.103 s, to-linear 0.150 s, blur (r=128) 0.055 s, divide 0.210 s, encode 0.354 s, **total
0.872 s**. `Concrete_clean_02_d` at 1024x1024: total 0.221 s. Both land at **0.21 s per megapixel**,
so the pass is linear in pixels and encode-dominated.

- interchange: 1,017 MP -> **214 s** single-threaded, about **30 s** on 8 processes.
- all four packs with textures: 4,223 MP -> **~15 min** single-threaded, **~2 min** on 8 processes.

**Render time cost: zero.** The graph is identical; only the file behind the Image Texture node
changes.

**Disk cost, which is the real cost:** interchange's albedos are 1.67 GB at 8 bit. At 16 bit expect
**3.0 to 3.5 GB** (estimate, PNG compresses a smoother image better than the naive 2x). All four
packs: **~13 GB**. That is the number to put in front of whoever owns the disk before writing a line.

### Where this argument could be wrong

Four ways, and they are not equally likely:

1. **Some baked darkening is authored intent, not occlusion.** A prop meant to read as sooty, a wall
   meant to read as water-stained. De-lighting turns it into a clean prop wearing a soot-shaped normal
   map. The seam gate does not catch this, because a sooty tiling wall tiles. This is the failure this
   lever cannot fully defend against, and the mitigation is the [0.5, 2.0] clamp plus human review of
   the largest corrections, not a better algorithm.
2. **The physical ranges are for CLEAN materials.** A genuinely filthy loading-dock floor legitimately
   sits at 0.10, not 0.20. Applying a class floor there manufactures a clean floor. The floor step
   should therefore be reported separately (step 6) and be independently switchable.
3. **For the GAME, the shipped albedo is correct by definition.** EFT applies its own baked SH ambient
   on top, and the artist tuned the map against that. Nothing here is a bug in the pack. That is
   precisely why this lever lives in this file and not in `game-parity.md`, and why running it under
   `MODE = 'game'` would be a defect.
4. **56% of the maps are atlases** where the LF field is material identity. The gate handles this, but
   the gate is a threshold on a bimodal distribution and bimodal is not binary; some maps sit near 3.

### Failure modes and detection

| failure | detector |
|---|---|
| painted markings flattened (a 2 m arrow on an 8.5 m tile is a quarter of the tile and sits inside sigma) | compare p95/p5 of the corrected map against the original; a drop over ~20% on a map with known markings means sigma is too small |
| atlas greyed out | contact-sheet the 90 non-tiling sample maps before and after; the gate should have skipped every one |
| **alpha destroyed -> roughness wrong on 3,608 materials** | byte-compare source and cache alpha planes. Hard assert. |
| double correction | sidecar hash check, refuse to process an output |
| rocks over-corrected | the AM_Rock family carries LF 3.3 to 7.0, so it receives the most correction; it is ALSO the family whose detail albedo is already mean-neutralised at `:2092-2102`. Confirm the two do not stack: the detail neutralisation divides the DETAIL map's mean, this divides the BASE's field, and they are independent - but verify rather than assume |
| tile seam appears where none existed | caused by a non-wrapping blur, step 3 |

### How to verify it improved rather than merely changed

1. **The histogram, before and after.** The 14.6% of maps below linear 0.05 must fall. Print the full
   seven-bin table above for the cache and diff it. If nothing moved, the gate rejected everything.
2. **Per class.** Concrete's median must move from 0.1818 into 0.20-0.35. Asphalt at 0.0767 must stay
   in 0.08-0.12; **if the pass pushes asphalt above 0.12 it is over-correcting**, and asphalt is the
   canary precisely because it starts in band.
3. **The bounce test, which is the one that distinguishes a de-light from a brightness knob.** Render
   two matched shots: an enclosed interior corner and an open sunlit yard. The interior must brighten
   substantially (order of the `rho/(1-rho)` ratio, roughly 1.3 to 1.5x on the affected surfaces); the
   yard must barely change, because direct sun dominates there and direct sun is linear in rho.
   **If both brighten equally, the pass applied a global gain and step 6 is doing all the work.**
4. **Invertibility.** Multiply the de-lit map by its recovered LF field and confirm the original
   returns to within quantisation. If it does not, the pass destroyed data it did not intend to.
5. **Re-fit exposure afterwards, per shot.** Changing average scene reflectance changes exposure.
   Comparing a de-lit render against a pre-de-light render at a fixed exposure will show the
   correction as "everything got brighter", which is not the claim being tested
   ([game-parity §3](game-parity.md#3-exposure-is-colour-and-it-is-the-first-thing-to-rule-out)).

---

## 3. Displacement on hero surfaces

### The physical justification

A normal map perturbs the shading normal and moves nothing. Three things follow, all of which are
visible and none of which more samples will fix:

- **The silhouette stays flat.** A kerb, a road edge against sky, the lip of a slab - all razor
  straight.
- **Nothing self-shadows.** At a grazing angle a 5 mm stone throws a shadow many times its own height.
  Measured at the camera heights the solver actually uses (`RISES = [1.35, 1.80, 2.25]`,
  `cine_camera.py:45`), eye at 1.8 m:

  | ground distance | grazing angle | shadow cast by a 5 mm bump |
  |---|---|---|
  | 3 m | 31.0 deg | 8 mm |
  | 5 m | 19.8 deg | 14 mm |
  | 8 m | 12.7 deg | 22 mm |
  | 12 m | 8.5 deg | 33 mm |

  A normal map produces none of that shadow, which is why game ground reads as a photograph of a
  texture rather than a photograph of a road.
- **Nothing occludes anything.** Parallax mapping addresses the third point only, and the importer's
  port is a **single-step approximation of the viewer's 32-step march** because a Cycles graph has no
  loops (`import_eftpack.py:645-683`). Its own docstring is explicit that displacement is a different
  feature: *"Displacement is a different and more expensive feature (it moves silhouettes, which
  parallax never does) and needs a UV-to-metres conversion the pack does not carry"*
  (`:677-678`). This section is the answer to that sentence.

### Which surfaces qualify, counted

Measured on the R = 150 m build:

| candidate | in the build | pack-wide (interchange) | pack-wide (6 packs) |
|---|---|---|---|
| vert-paint road / parking / yard slabs | 16 meshes, **22 instances**, **16,291 m2**, 3,874 triangles | 216 materials | **1,237 materials** |
| detail-map rock and cliff | 8 meshes, **22 instances** | 23 materials | **205 materials** |
| terrain (`FLAG_TERRAIN`) | **0** | 4 instances map-wide | - |

**16,291 m2 over 3,874 triangles is 4.21 m2 per triangle**, roughly a 2 m edge. The roads in this
scene are, geometrically, flat quads. That is the whole case for this lever in one number.

Terrain is worth calling out: `FLAG_TERRAIN` appears on **4 instances in the entire interchange
pack**, and none within 150 m of the power station. In this scene terrain is rebuilt from the
dataset by `terrain_splat.py`, whose 700 m tiles carry **albedo layers only** - no normals, no
heights (`eft_assets/interchange_v2/terrain_layers/manifest.json`). Terrain therefore has the
weakest possible height provenance and should be the last thing displaced, not the first.

### Where the height data comes from, and how derived it is

The pack ships **139 parallax height maps across two packs** (streets_nav 135, woods 4) and **zero**
in interchange, ground_zero, icebreaker and factory_rework. On the map the example scene builds
there is no shipped height field at all. Three sources, most-derived first:

- **(a) Integrate the layer normal maps. DERIVED.** A tangent-space normal map IS a gradient field;
  Poisson or frequency-domain integration recovers a height field up to an additive constant and a
  scale. The interchange vert-paint layers carry **410 normal maps**, and 874 distinct normals exist
  pack-wide. Only the scale is invented. This is the right source.
- **(b) The vert-paint `heights` mask. SEMANTICALLY WRONG if used directly.** 138 of interchange's 216
  vp materials carry one, drawn from just **10 distinct files** (`road_mask2` 41 uses,
  `asphalt_crushed_mask02` 35, `asphalt_crushed_mask` 17, `mask` 5, ...). It is a per-layer BLEND
  WEIGHT consumed at `import_eftpack.py:1633-1660`: it says *"the pebble layer wins here"*, not
  *"the surface is 4 mm higher here"*. Displacing by it puts a step at every layer boundary. It is
  legitimate only as a MASK controlling where (a) is applied and how strongly.
- **(c) Procedural noise. INVENTED.** Cheapest, and defensible only if it is labelled as invented in
  the code and in the shot notes.

**The UV-to-metres conversion the parallax docstring says the pack does not carry is DERIVABLE from
the geometry, and the importer already derives it.** `_classify_water_matte` computes submesh 3D
bounding-box diagonal over UV span to separate puddles from stretched floor decals
(`import_eftpack.py:2273-2277`, `WATER_MATTE_MPR = 40`). Applying the identical measurement to the
299 vert-paint submeshes in interchange:

| metres per UV repeat | p10 | median | p90 | max |
|---|---|---|---|---|
| vert-paint slabs | 4.18 | **8.51** | 16.09 | 23.0 |

A 1024 px albedo over 8.51 m is **120 texels/m, 8.3 mm per texel**. So the finest relief the source
data can even express on a road is about 8 mm, and physical asphalt aggregate relief is 2 to 8 mm.
**A displacement Scale of about 5 mm with Midlevel 0.5 is the entire defensible budget.** Anything
larger is sculpture, and it will be obvious as sculpture at the first grazing shot.

### Cost, and the one that actually kills it

**Dicing is the affordable cost.** Cycles adaptive subdivision dices to a screen-space micropolygon
target: Render dicing rate defaults to 1 px, viewport 8 px, Max Subdivisions 12, Offscreen Scale 4
(Blender manual, Render Properties > Subdivision). At the `cine_camera.py:299` default of 2560x1440,
a ground plane covering a third of the frame is roughly 1.2 M micropolygons. That is fine.

**De-instancing is the cost that kills it.** Adaptive subdivision makes multi-user object data
single-user. This build shares mesh datablocks **4.4x** (4,465 objects over 1,017 meshes) and 5.2x at
R = 250. Enabling adaptive subdivision on a MATERIAL or a ROLE would therefore hit every instance of
every road slab in the build and multiply their memory by the sharing factor.

**Therefore: this lever is applied to a hand-selected set of OBJECTS, never to a material, never to a
role, never inside `_build_material`.** That is the structural difference between lever 3 and levers
1 and 2, and it is why lever 3 lives in `example_scene.py` rather than in the importer.

### How to restrict it to surfaces actually near the camera

Not with Offscreen Scale. It coarsens off-screen geometry, does nothing about on-screen geometry
200 m away, and does not prevent de-instancing at all.

Use the camera that has already been solved. `cine_camera.py` places the lens 3 to 6 m from the
subject with an ideal of 5.0 m (`DISTANCES`, `IDEAL_DIST`, `:44`, `:48`), 1.35 to 2.25 m up (`:45`),
on a 50 mm lens at f/2.8 with DOF bound to a focus object (`:142`, `:260-266`). Those numbers bound
the useful displacement region tightly:

- **DOF.** 50 mm, f/2.8, focused at 5.0 m, CoC 0.029 mm: hyperfocal **30.8 m**, sharp band **4.31 m
  to 5.96 m**, 1.65 m deep.
- **Feature size against blur.** A 5 mm relief on a 36 mm sensor subtends 1.78 px at 5 m and 0.89 px
  at 10 m (2560 wide). The defocus circle at 10 m is 3.2 px at 1280 wide and 6.4 px at 2560. Ratio of
  feature to blur: 0.46 at 8 m, **0.28 at 10 m**, 0.14 at 15 m, 0.09 at 20 m.

Past roughly 10 m the relief is a quarter of the blur circle and cannot be seen at any sample count.
So: **select objects whose bounding box comes within ~12 m of the solved camera path at any shot
frame, intersect with the vert-paint / detail material set, and set
`obj.cycles.use_adaptive_subdivision` on those objects alone.** On the measured build that is a
handful of the 22 vert-paint instances, not 4,465 objects.

### The concrete implementation

A MODE-3 block in `example_scene.py` placed **after** the camera solve at `:294-304` (it needs `cam`)
and after `scene.frame_end` is set at `:304`. Per selected object:

1. A Subdivision modifier with `subdivision_type = 'SIMPLE'`. **Not Catmull-Clark**: the slabs are
   flat quads, and Catmull-Clark rounds the slab's own boundary, which pulls the road away from the
   kerb it was authored flush against.
2. `obj.cycles.use_adaptive_subdivision = True`.
3. `material.displacement_method = 'BOTH'` - true displacement for what the dicer can resolve, bump
   for the sub-micropolygon remainder.
4. A `ShaderNodeDisplacement` (Height from the integrated field, Midlevel 0.5, Scale 0.005) into the
   Material Output's Displacement socket.

Note on subdivision depth: at 4.21 m2 per triangle, SIMPLE subdivision needs about **9 levels** to
reach 4 mm micropolygons (2 m / 2^9 = 3.9 mm), inside the default Max Subdivisions of 12 but not
comfortably. In practice the adaptive dicer stops earlier on the pixel rate for anything not right
under the lens, which is the intended behaviour.

### Failure modes and detection

1. **Cracks at the slab boundary.** A displaced slab and the un-displaced asphalt beside it separate
   by up to the Scale. Detector: a depth AOV, looking for a discontinuity exactly on a material
   boundary. Mitigation: displace both sides of every boundary or neither.
2. **The 6 mm decal lift is now the same order as the displacement, and this is the most likely
   visible bug in the lever.** `DECAL_LIFT = 0.006` m (`import_eftpack.py:128`) versus a 5 mm
   displacement: where the road displaces UP the decal sits 1 mm proud and starts to intersect;
   where it displaces DOWN the decal sits 11 mm proud, floats, and casts its own shadow. Detector:
   any road marking that reads as a sticker. Mitigation: exclude any slab carrying a decal receiver,
   or displace the decal with the same height field - only possible where the decal shares the
   receiver's UV, which it usually does not. **Excluding is the honest option.**
3. **Memory blow-up from de-instancing.** Detector: report peak memory before and after. A build of
   4,465 objects over 1,017 meshes should gain no more mesh copies than the number of objects
   actually selected.
4. **Silhouette noise from a badly integrated normal map.** Integration is defined only up to a
   constant per connected region, so a discontinuous normal map integrates to a step. Detector: look
   at the integrated height as an image before using it, every time.
5. **The parallax port and the displacement double up.** 137 of the 139 parallax materials are RFA
   and none is on interchange, so on this map they cannot collide - but on streets_nav they can.
   Gate: a material that got displacement gets `EFT_PARALLAX=0` treatment for itself.

### How to verify it improved rather than merely changed

1. **Shoot the grazing case, because that is the entire claim.** Put the lens at 1.35 m, the lowest
   `RISES` value, looking along the road. Render with and without and difference the linear EXRs.
   **The difference must land on the ground's SILHOUETTE against what is behind it, and in contact
   shadows.** If it is spread evenly over the surface, the Displacement node is reproducing what the
   normal map already did and the lever bought nothing but render time.
2. **Count moved silhouette pixels.** Extract the ground/sky and ground/object boundary in both
   renders and measure the displacement in pixels. **Zero means the Scale is too small or
   `displacement_method` is still `'BUMP'`** - which is the default, and is the single most likely
   reason for a null result.
3. **Print render time and peak memory, before and after, on the same shot.** This is the only one of
   the three levers whose cost can go superlinear without warning, so the measurement is part of the
   feature, not part of the review.

---

## Composing with the MODE switch

`photoreal-lowergamefidelity` is the **third value** of the mode switch in
`tools/blender/example_scene.py`, and it is a superset of `photoreal`:

```
MODE = 'game' | 'photoreal' | 'photoreal-lowergamefidelity'
```

Everything shared today stays shared, because none of it is a look decision: the pack read, the raw
3x3 affine and the shear bake (`SHEAR_TOL = 0.02`, `import_eftpack.py:123`), the Y-up to Z-up
rotation applied exactly once on one empty (`:2633`), the UVs and the V-flip, the `BYTE_COLOR`
sRGB round-trip, the nav routing (`_patrol`, `example_scene.py:88-106`), the ground resolve
(`_ground_height`, `:109-127`), the character and weapon import, and the solved follow camera
(`:294-304`).

**Where the switch is read.**

| lever | file | mechanism | why there |
|---|---|---|---|
| 1 bevel | `import_eftpack.py` | `EFT_BEVEL=<metres>` read once in `_Importer.__init__` beside `parallax_on` (`:388-393`); consumed at `:2150-2155` | per material, and the importer imports nothing from this repo - an env var keeps it standalone and keeps the A/B to one variable |
| 2 de-light | `import_eftpack.py` | `EFT_DELIGHT=<cache dir>` prepended as a candidate in `_resolve_tex` (`:573-601`) | per texture; the cache is built by a separate offline script that Blender never has to load |
| 3 displacement | `example_scene.py` | a `MODE`-gated block after the camera solve (`:304`) | per OBJECT, and the selection depends on the solved camera, which does not exist inside the importer |

`example_scene.py` sets the two env vars from `MODE` before calling `_load("import_eftpack.py")` at
`:172`, so the mode remains one constant at the top of one file, which is what that file is for
(`example_scene.py:12-13`).

**Two counters in the run summary.** The block at `_run_open` (`:2650-2666`) and the returned dict
(`:2667-2681`) should gain `bevel` (materials that received a Bevel node) and `delit` (textures
resolved from the cache). Every other feature in this importer is counted there, and the reason is
stated in `blender-import.md`: those counters are the fastest way to catch a feature that silently
did nothing. A de-light cache pointed at the wrong directory fails exactly that way - it produces a
correct render of the uncorrected textures.

**The one thing this mode must NOT inherit: the sun/sky fit.** `SUN_ENERGY = 6.90` and
`SKY_STRENGTH = 2.35` (`example_scene.py:68-69`) are a least-squares fit against a viewer frame
through the game's grade at exposure 1.35 (`:56-67`). Lever 2 changes the scene's average
reflectance, which changes the fit. Under `photoreal` the fit is already abandoned for a physical sky
([photorealism §1](photorealism.md#1-the-sky-is-8-bit-ldr)); under this mode it **must** be, because
keeping it would re-darken the image by roughly the amount lever 2 just removed and the two changes
would cancel invisibly.

---

## What this mode does not change

Everything on this list is on the parity side and does not move, exactly as in
[photorealism, Keep these regardless](photorealism.md#keep-these-regardless). This mode edits
appearance, never structure.

| unchanged | why it is not negotiable here |
|---|---|
| placement: the raw 3x3, never TRS-decomposed; shear baked to world geometry above `SHEAR_TOL` | a de-lit texture on a misplaced object is a worse render, not a different one |
| UVs, the V-flip, `uvTilingBaked` | lever 3 READS the UV-to-metres scale; it never rewrites a UV |
| the alpha semantics: RFA roughness, MASK cutoff, `softCutout` | lever 2 is explicitly forbidden from touching the alpha plane |
| `DECAL_LIFT = 0.006` m | levers 1 and 3 are both BOUNDED BY it. Neither may raise it to make itself easier |
| nav routing and the camera solve | geometry and timing, not look, and lever 3 depends on the camera solve being trustworthy |
| the DirectX green flip and `normalScale` | lever 1 sits downstream of the Normal Map node and must not replace it |
| the `BYTE_COLOR` sRGB re-encode for vertex colours | drives the vert-paint weights lever 3 selects on |
| the single-step parallax port | left exactly as it is; lever 3 replaces it per material or leaves it alone, never half |
| the terrain splat weights and control-map sampling | lever 2 may de-light a terrain LAYER texture; the weights are untouched |
| the material role classification | all three levers gate ON the roles; none reclassifies |

---

## Recommended implementation order

**1. Bevel (hours).** One node, one link, one constant, one role gate, one env var, one counter.
No new data, no cache, no memory risk, and `EFT_BEVEL=0` reproduces today's render bit for bit. It
changes every opaque surface in every shot, which makes it the cheapest way to find out whether the
measurement harness for this whole mode actually works. Build the harness here, on the lever that
cannot lose data.

**2. De-light (days).** The work is not the divide, it is the classifier and the validation. Do it in
this order: the alpha byte-compare assert first, then the seam classifier, then the histogram
harness, then the divide. Compute cost is 30 s per pack on 8 cores, so iteration is cheap; the
expensive part is deciding, per texture class, whether the correction is right. Budget the disk
(~3.3 GB for interchange, ~13 GB for four packs) before starting.

**3. Displacement (days, and the only unbounded one).** Last, because the selection depends on a
solved camera, because it is the only lever that can make a build unrenderable, and because its most
likely visible bug is an interaction with the 6 mm decal lift that only shows up on the ground
surfaces lever 2 has just corrected. Do it on one shot, with render time and peak memory printed,
before it goes near a second one.

---

## How to tell if it worked

The tests specific to each lever are in each section. Four apply to the mode as a whole.

**1. Keep the registration harness even though parity is gone.** Drive the viewer to the identical
pose and settle it (`EFT_POSE`, `EFT_CLEAN`, `EFT_SHOT_SETTLE=240`, the two harness gates that look
like crashes -
[game-parity §2](game-parity.md#2-matching-a-shot-then-matching-pixels)). Confirm `dx=0 dy=0` on
cross-correlated edge maps before reading a pixel. **Geometry parity is not what this mode trades
away**, so the harness keeps working, and it is the only thing that lets a difference image mean
anything.

**2. Difference images, not side-by-sides, for all three levers.** Each lever makes a *localisation*
claim: bevel changes edges, de-lighting changes shaded regions more than lit ones, displacement
changes silhouettes. Every one of those is falsifiable on a difference of two linear EXRs and none of
them is settleable by looking at two pictures. If a lever's difference is spread evenly over the
frame, it became a brightness or a blur knob and should be reverted.

**3. Re-fit exposure per shot, after lever 2, and hold it.** Exposure agreement is view-dependent even
before any of this (an interior needed 0.70 where the yard wanted 1.35 -
[game-parity §3](game-parity.md#3-exposure-is-colour-and-it-is-the-first-thing-to-rule-out)), and
lever 2 changes average reflectance. Comparing at a stale exposure makes a successful de-light look
like an over-exposure and an unsuccessful one look fine.

**4. Keep a parity render of the same shot, and look at it.** This mode's output should be visibly
NOT the game. If a `photoreal-lowergamefidelity` frame is hard to tell from a `game` frame, one of
three things is true: a lever silently did nothing (check the two new counters), the physical sky
from [photorealism §1](photorealism.md#1-the-sky-is-8-bit-ldr) was not enabled underneath, or the
grade LUT is still in the chain. All three have happened to features in this pipeline before, and all
three are counter-detectable rather than eye-detectable.
