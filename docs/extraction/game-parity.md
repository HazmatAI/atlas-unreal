## Contents

- [What this covers](#what-this-covers)
- [1. The method: the viewer is the authority](#1-the-method-the-viewer-is-the-authority)
- [2. Matching a shot, then matching pixels](#2-matching-a-shot-then-matching-pixels)
- [3. Exposure is colour, and it is the first thing to rule out](#3-exposure-is-colour-and-it-is-the-first-thing-to-rule-out)
- [4. Channel rules that have actually bitten](#4-channel-rules-that-have-actually-bitten)
- [5. Legacy glass: two bounds and a retired conversion](#5-legacy-glass-two-bounds-and-a-retired-conversion)
- [6. The coplanar push has no path-traced equivalent](#6-the-coplanar-push-has-no-path-traced-equivalent)
- [7. The grade chain, exactly](#7-the-grade-chain-exactly)
- [8. Deriving sun and sky, because the pack has neither](#8-deriving-sun-and-sky-because-the-pack-has-neither)
- [9. Where parity works against photorealism](#9-where-parity-works-against-photorealism)
- [10. Failure signatures](#10-failure-signatures)

**Marking convention.** Every material COUNT below has been re-measured against the six shipped packs and every `file:line` re-checked against the file. Figures tagged **[session]** are one-off measurements recorded in a source comment or a sibling document: they describe a specific render on a specific shot, are not reproducible from the code or the pack, and should be treated as provenance rather than as data. Where a source comment and a sibling document disagree on a number, both are printed and neither is reconciled.

---

## What this covers

Making an external renderer (path tracer, offline comp, a second real-time engine) produce the image the GAME produces, using `viewer/` as the reference implementation. It is about the places where a faithful reading of the pack still gives the wrong picture, and about how to prove which of the two you are looking at.

The Blender-specific script inventory, node graphs and import switches are in [blender-import.md](blender-import.md); this file references it rather than repeating it. The pack formats themselves are in the sibling references. The three files overlap deliberately at the channel rules, because that is where every defect has been.

**The other half of the pair is [photorealism.md](photorealism.md)**: what each of these rules costs when the goal stops being the game's image and becomes a photograph, ranked by payoff. This file says what the game does and how to prove you match it; that one says what to give up, in what order, and what it buys. §9 below is the hand-off, not a summary.

---

## 1. The method: the viewer is the authority

**Read the consumer, not the prose.** `viewer/assets/shaders/gpu_draw.wgsl` and `viewer/src/render/*.rs` decide what every field in `materials.json` means. The extraction documents describe intent; they have been wrong more than once (the `** 0.25` conversion in §5 is currently documented in a source docstring as retired, and this file corrects a session note that still carried it). When a document and the shader disagree, the shader is right, and the document is the thing to fix.

Three concrete consequences.

- **A field's presence is not a licence to bind it.** `specMap` is parsed into the pack struct (`viewer/src/eftpack.rs:450-451`) and is never sampled by any shader. It exists as PROVENANCE for a scalar that was already computed offline. §4.
- **A lane can be computed and dead.** `viewer/src/render/gpu_driven.rs:2330-2334` derives a GGX roughness from the legacy Blinn `_Shininess` for glassTRS materials. The shader's TRS branch never reads `m.roughness`: it uses `m.glass_shin` directly (`gpu_draw.wgsl:1646`). Porting the roughness lane for TRS glass ports something the reference does not use.
- **The gate matters as much as the value.** `glass_trs = mat.glass_trs && mat.role == "glass"` (`gpu_driven.rs:2013`). A record carrying `glassTRS` with a different role gets no flag, no response lanes, and falls through to the ordinary path.

The counted scope for the material rules below is the six shipped packs (interchange, streets_nav, ground_zero, factory_rework, icebreaker, woods) unless a single pack is named.

---

## 2. Matching a shot, then matching pixels

Nothing in §3 to §8 can be settled by looking at two renders side by side. Every comparison is made from an identical camera, through an identical grade, on named pixels.

**Drive the viewer to the exact pose.**

```
EFT_CLEAN=1                       # no HUD, no panels (ui.rs:285-286, pick.rs:95-96)
EFT_POSE="x,y,z,yaw_deg,pitch_deg"    # PACK space, degrees (main.rs:122-136, :1746-1753)
EFT_GAME_FOV=<vertical degrees>   # clamped to 20..120, default 60 (main.rs:194-198)
EFT_CAM=fly EFT_RENDER=gpu        # (main.rs:181, :877)
EFT_HIDDEN=1 EFT_HIDDEN_ALLOW=1   # render with no window
EFT_SHOT=<out.png> EFT_SHOT_EXIT=1 EFT_SHOT_SETTLE=240
```

`EFT_POSE` is five comma-separated finite floats or it is ignored entirely (`main.rs:128-130`); there is no partial parse and no warning. The angle convention is the one the POS HUD's copy button emits, so a copied pose round-trips:

```
yaw   = atan2(-fwd.x, -fwd.z)     # ui.rs:2024-2028, main.rs:794
pitch = asin(fwd.y)
```

Do not derive the angles from `frame_for_pack`'s own target math (`main.rs:1734`, `yaw = atan2(dir.x, -dir.z)`): that path is the initial map framing and its own comment records that it only aims correctly when `dir.x == 0`.

**Two harness gates that look like crashes.** `EFT_HIDDEN=1` is refused unless a finite `EFT_SHOT`/`EFT_BENCH` job was supplied, because a leaked hidden mode is an invisible process that never exits; `EFT_HIDDEN_ALLOW=1` is the explicit opt-out for a harness that guarantees cleanup (`main.rs:982-994`). `EFT_SHOT_SETTLE` defaults to **30** frames (`main.rs:2426`); 240 is the value this pipeline uses, because streaming, TAA convergence and the SH volume upload are not done at 30.

**Register before comparing.** Cross-correlate edge maps of the two frames and confirm the offset before reading a single pixel. A coordinate error and a material error look nothing alike once the frames are known to register, and identical otherwise. This pipeline's conversion lands at dx=0, dy=0.

**Crop, never squash.** Match the vertical FOV and crop the wider frame to the viewer's aspect.

**Compare MATCHED PIXELS.** Pick the reference's extreme pixels for the feature under test (its reddest, its most saturated, the pane centre) and read the same coordinates in the other render. An eyeballed patch average hides exactly the channel shift §3 exists to detect.

**Fix exposure first.** `EFT_AUTO_EXPOSURE` defaults to OFF (`main.rs:1238` only arms it when the variable is present), so an unmodified viewer grades at the constant `DEFAULT_GRADE_EXPOSURE = 1.35` (`viewer/src/render/mod.rs:34`, consumed at `render/grade.rs:116`). Turn it on and eye adaptation makes exposure view-dependent, at which point no fixed number on the other side can match.

---

## 3. Exposure is colour, and it is the first thing to rule out

The most misleading failure in the pipeline, because it makes correct materials look broken and sends you editing shaders.

The shipped grade compresses toward a warm white above linear 1.0, so **pushing a surface brighter desaturates it**. Measured on the shipped cube, for one saturated magenta ([blender-import.md](blender-import.md#washed-out-colour-is-an-exposure-symptom-not-a-material-symptom)) **[session]** - the probe colour is not recorded, so the saturation column is not reproducible; the LINEAR column and the clip are (re-evaluating `packs/shared/grade_lut.bin` at those four indices confirms `4*(i/63)^2` and confirms that every index at or above 32 returns the same texel):

| shaper index | linear value | saturation |
|---|---|---|
| 20 | 0.40 | 0.98 |
| 32 | 1.03 | 0.87 |
| 40 | 1.61 | 0.69 |
| 52 | 2.73 | 0.44 |

A photographic image does the opposite: real film and real sensors desaturate far less on the way up and roll off instead of clipping. So in this display chain exposure IS colour, and "washed out" is not evidence of anything on its own.

**The discriminator.** Compare a rendered CHANNEL RATIO against the source texture's own ratio (G/R or R/B), never brightness.

- Ratio matches the texture, image is pale and bright: EXPOSURE or lighting.
- Ratio differs from the texture: a genuine material or channel bug.

Both outcomes have a worked example. A red forklift measured **R/G 0.96** against its texture's own ratio, where the viewer gives **1.45**, and was a real bug (§4). A blue fabric chair rendered 1.5x too bright came out pale mauve at saturation 0.36 against the viewer's 0.52, passed the ratio test (G/R 0.47 against the texture's 0.53), and was correct all along; at matched exposure the same pixels measured `[0.559, 0.265, 0.452]` against the viewer's `[0.575, 0.275, 0.446]` ([blender-import.md:989-991](blender-import.md#washed-out-colour-is-an-exposure-symptom-not-a-material-symptom); the chair figures exist only there, not in any source file) **[session]**. The forklift's texture ratio is printed as **1.42** at `import_eftpack.py:2010` and as **1.43** at `docs/extraction/textures-and-materials.md:342` and `blender-import.md:143`; nothing reconciles the two, so do not treat either as more than "about 1.4".

**Exposure agreement is view-dependent.** The viewer's ambient is a baked one-bounce SH volume; a path tracer computes full GI. A fit made in an open yard drifts in an enclosed corner (the chair above needed 0.70 where the yard wanted 1.35). Fit per shot and hold it fixed for the whole shot.

---

## 4. Channel rules that have actually bitten

Each of these is a real defect, each was invisible on the surfaces where the two readings happen to agree, and none produced an error.

### `specMap` is a provenance note, never a texture

The assembler has already reduced `_SpecMap`/`_SpecTex`/`_GlossMap` to the scalar `roughness` (`eft_pipeline/assemble_bevy.py:357`, "roughness from _SpecMap luma"; the scalar itself comes from `_pbr` at `:176-182` plus authored `_Glossiness` overrides). The renderer reads that scalar, and nothing else, except for the per-pixel path below.

Scope, re-measured: **2,492** Interchange materials carry a `specMap`. For **189** of them the `specMap` path IS THE ALBEDO FILE, so `roughness = 1 - albedo` makes bright paint glossy. A further **2,469** of the 2,492 also set `roughnessFromAlbedoAlpha`, so binding the map additionally SUPPRESSES the per-pixel path the renderer actually uses (`textures-and-materials.md:340-345`). The field is read into the pack struct at `eftpack.rs:450-451` and appears nowhere in `viewer/src/render/`.

Binding it rendered a red forklift neutral grey at R/G 0.96 against the viewer's 1.45. Reverting to the scalar restored it to **1.38** and cut whole-frame RMS against the viewer from 0.143 to 0.114 (`blender-import.md:140-145`) **[session]**.

### Per-pixel roughness comes from RAW `tex.a`, not from `albedo.a`

```wgsl
var rough = clamp(m.roughness, 0.03, 1.0);                      // gpu_draw.wgsl:1430
if ((m.flags & MAT_FLAG_RFA) != 0u && has_albedo) {
    rough = clamp(1.0 - tex.a, 0.06, 1.0);                      // :1432
}
if ((m.flags & MAT_FLAG_WATER) != 0u) { rough = max(rough, 0.10); }   // :1435
```

`tex.a` is the texture sample before the `_Color` tint is applied; `albedo.a` is `tex.a * tint.a`. The shader's own comment at `:1427-1429` says so explicitly: using `albedo.a` biases roughness by the material tint's alpha. The comment at `:1426` calls RFA "82% of the pack"; that is no single pack's number. Re-measured: interchange **3,608 / 5,765 (63%)**, woods 5,222 / 6,021 (87%), streets_nav 13,681 / 19,715 (69%), ground_zero 5,377 / 7,539 (71%), factory_rework 1,920 / 2,683 (72%), icebreaker 1,313 / 1,747 (75%). Water's 0.10 floor exists so its sun glint is an overcast smudge rather than a pinprick.

### Vert-paint: a near-black resolve and a matte override

Two behaviours sit outside the splat blend itself and both change the surface visibly.

```wgsl
if (dot(spl, vec3<f32>(0.299, 0.587, 0.114)) < 0.02) {   // gpu_draw.wgsl:1304-1306
    spl = a0.rgb * v.tint0.rgb;                          // fall back to TINTED layer 0
}
...
if (vp_smooth >= 0.0) {                                  // :1440-1442
    rough = clamp(1.0 - 0.30 * vp_smooth, 0.72, 1.0);
}
```

The fallback is to the layer-0 sample times **`v.tint0.rgb`**, not to an untinted sample: **471 of the 1,127** materials that build the full splat graph carry a non-white layer-0 tint (`blender-import.md:60`, `:630`). Mind the denominator - that 1,127 is the RUNTIME population, the vp materials whose heights map and all three layer images actually resolved. The pack ships **1,237** records carrying a `vp` block at all (interchange 216, streets_nav 458, factory_rework 226, ground_zero 125, woods 111, icebreaker 101, re-measured), and 474 of those have a non-white layer-0 tint. Quote whichever you mean, but a port that walks `materials.json` is counting the 1,237. `vp_smooth` is the weighted sum of the RAW layer texture alphas (`:1309`) and is initialised to `-1.0` (`:1268`), which is what gates the override to vp materials only. The 0.72 floor is a matte clamp: raw `1 - smoothness` read near-mirror and made road slabs pop off the terrain around them.

Port the NUMBER, not the response. The shader's `rough` drives a hand-written GGX with `SPEC_STRENGTH = 1.5` (`gpu_draw.wgsl:133`, `:1480`), Smith `k = (rough+1)^2/8` and F0 = 0.04; a multiscatter GGX against a real traced scene will not give the same highlight from the same 0.72.

### `BYTE_COLOR` must round-trip through the sRGB encode

The renderer reads `COLOR_0` as raw bytes over 255. Blender's `BYTE_COLOR` attribute is treated as sRGB-encoded and the Color Attribute node hands the graph its LINEAR decode, so a stored byte **128** arrives as **0.2159** where the viewer sees **0.5020** (`import_eftpack.py:1666-1668`). The fix is to write through the `color_srgb` accessor (`:2450-2454`) and re-encode on read. Nothing errors; every blend weight and every SoftCutout edge silently shifts.

This is the general form of the trap: any attribute a DCC tool colour-manages must be checked for a decode you did not ask for.

### Detail albedo is MEAN-NEUTRALISED

Unity Standard multiplies a tiling detail map over the base at x2. Copying that naively recolours the whole surface by the detail map's own average, so the renderer divides that average back out and the map contributes only LOCAL contrast (`gpu_draw.wgsl:1319-1332`):

```
neutral = clamp( detail_lin * 4.5948 / max(albedoMeanGain, 1e-3), 0.25, 4.0 )
base   *= mix( 1, neutral, clamp(albedoStrength * fade, 0, 1) )      # alpha untouched
```

`DETAIL_UNITY_GAIN = 4.5948` (`gpu_draw.wgsl:176`) is Unity's detail x2 expressed in linear space; `albedoMeanGain` is that same product's per-channel mean, measured offline at assembly and shipped per material (`viewer/src/eftpack.rs:391-393`, packed at `gpu_driven.rs:2262-2266`).

Scope, re-measured: **204** materials across the six packs carry a detail albedo - ground_zero 91, woods 68, interchange 23, streets_nav 22, factory_rework and icebreaker none (`blender-import.md:676-678` agrees). They are the rock and cliff surfaces: all 23 of interchange's have an `AM_Rock_*` or `Arid_rock_*` base albedo and all 23 share one detail map. A session note recording "23 materials" is the interchange figure alone. `albedoStrength` spans 0.4 to 1.0 in the shipped data.

Two details an external renderer needs: `albedoUv` is the RAW Unity `_DetailAlbedoMap_ST` and must be re-based against `uvXform` because the base ST is already baked into the vertex UVs; and the 40 m to 120 m distance fade (`gpu_driven.rs:2254-2261`) exists for raster LOD, so an offline render has no reason to port it.

---

## 5. Legacy glass: two bounds and a retired conversion

EFT's car and storefront glass is the legacy Transparent/Reflective/Specular family. Scope, re-measured: **1,057** materials satisfy the consumer gate `role == "glass" && glassTRS` (streets_nav 503, ground_zero 401, interchange 153; none in factory_rework, icebreaker or woods) (`textures-and-materials.md:534` agrees). The raw `glassTRS` field appears on **1,217** records; the 160 that carry it with some other role are gated out and get no response lanes at all, so 1,217 is never the number to port against.

### The composition, term for term

```wgsl
// gpu_draw.wgsl:1661-1664
out.rgb = (apply_fog(lit) * trs_a + spec_g + refl_g + em_rgb) * cov
out.a   = trs_a * cov
```

`cov` is 1.0 for this family and only bites on LEGACY coverage-mask glass: `cov = smoothstep(0.02, 0.30, albedo.a)` when `MAT_FLAG_GLASS_MASK` is set (`:1656`), which kills the ADDITIVE terms as well so an empty shard-atlas region cannot go on mirroring the sky as a ghost pane. The flag's own gate opens with `!glass_trs` (`gpu_driven.rs:2023`), so on the 1,057 above `cov == 1.0` and the two forms are identical.

**Only the diffuse is scaled by coverage.** Everything on a Principled BSDF is scaled by its Alpha input, so building this on one returns the reflection 25-50% short at interchange's median `tint.a = 0.749` and the pane reads as a flat dark square (`import_eftpack.py:1030-1050`).

**Coverage uses `tint.a` twice**, and that is not a bug to fix:

```wgsl
trs_opac = ((glass_refl >> 24) & 255) * 8/255           // :1601, the opacity PRE-SCALE
trs_a    = clamp(albedo.a * max(trs_opac, 0.03), 0, 1) * m.tint.a    // :1602
if (is_trs && trs_a < 0.03) { discard; }                // :1603-1605, a real hole, not a dark spot
```

`albedo.a` is already `tex.a * tint.a`, so `tint.a` enters again at the end. Building coverage from the raw `tex.a` renders interchange glass `1/0.749 = 1.34x` too opaque.

### Bound one: the reflection can never exceed `_ReflectColor`

```wgsl
sh_ldr  = sh_env / (1 + sh_env);                                          // :1632
trs_env = select(sh_ldr * trs_refl, trs_refl, GLASS_CUBE) * fresnel_v;    // :1636
refl_g  = trs_env * (1.0 - shadow_event);                                 // :1638
```

The family's reflection input in the game is `texCUBE(_Cube)`, an LDR IMAGE bounded to [0,1], so the whole term is structurally under `_ReflectColor`. The scene irradiance available here is HDR (about 2.0 toward open sky) and overflows that contract, so it is Reinhard-compressed back into the LDR domain the shader was authored against. `E` itself comes from an INDIRECT-ONLY bake: `volume.json` is marked `"direct": false` and the volume carries sky plus bounce only, no sun (`viewer/src/sh_bake.rs:551`, `:936`, `:986`; `tools/build_map.py:499` asserts it). There is no sun in the reflection at all.

The `MAT_FLAG_GLASS_CUBE` branch skips the probe entirely: when the material authored its own `_Cube`, the extracted mean is folded into `_ReflectColor` at pack time (`gpu_driven.rs:2301-2305`) so the lane IS the reflection radiance. 10 of the 1,057 carry a `reflectCube` and take that branch (re-measured).

What removing the bound costs: tracing the environment as an unbounded HDR world put the pane at **5.4x** too light in graded display and **5.5x** too light relative to the opaque wall beside it, where in the reference the pane is DARKER than that wall. **86%** of that radiance entered through the specular node and **89%** of that was world, not sun (`import_eftpack.py:1058-1061`) **[session]**. A term bisection in the shader records diffuse-only 0.11 against +reflection 0.76 (`gpu_draw.wgsl:1630`) **[session]**.

The structural reason a BSDF cannot carry this bound is worth stating once: a BSDF's input is the radiance the integrator traces into it, that radiance does not exist until after the BSDF has been evaluated, and no node post-processes a shader's result. `x/(1+x)` has nothing to apply to. So the environment must be FETCHED as a value, not traced.

### Bound two: the Blinn lobe is analytic, and so is its light

```wgsl
Hh      = normalize(V + dom.dir);
blinn   = pow(max(dot(N, Hh), 0.0), max(m.glass_shin, 0.01) * 128.0);   // :1646
sun_ldr = dom.radiance / (1 + dom.radiance);                            // :1647
spec_trs = sun_ldr * trs_spec * blinn * max(dot(N, dom.dir), 0) * albedo.a * (1 - shadow_event);
```

`pow(NdotH, n)` is un-normalized and peaks at `_SpecColor`, integrating to about `SC * 2*pi/(n+2)`. An energy-normalized Cycles distribution integrates to about `SC`, an order of magnitude hotter on the shininess-1.0 panes. The game points this lobe at ONE light; a BSDF points it at the whole world.

**Correction to a widely repeated note.** The `** 0.25` perceptual-roughness conversion (a Cycles Roughness socket is perceptual and squares to GGX alpha, so handing it alpha squares it twice) is **RETIRED** in the current reference port, together with the Glossy BSDF that needed it. `import_eftpack.py:1130` uses the Blinn exponent directly, `n_blinn = max(shin, 0.01) * 128.0`, and `:1078-1085` records why. The rule survives only as a conditional: **anyone reintroducing a Cycles distribution here needs the `** 0.25` back**, and `** 0.5` is wrong in that case because it squares twice.

The GGX roughness lane at `gpu_driven.rs:2330-2334`, `sqrt(2/(shin*128 + 2))`, is DEAD for TRS materials for the same reason.

### The authored defaults are grey, and absence is not zero

All three scope figures were re-measured across the six packs.

| field | rule | scope |
|---|---|---|
| `reflectColor` / `specColor` | Unity's legacy UI defaults are **grey 0.5**, not white (`gpu_driven.rs:2297`, `:2307`), and both quantize through a byte, `round(clamp(v,0,1)*255)/255` (`:2308`) | 0 of 1,057 lack either |
| `shininess` | ABSENT defaults to **0.078** (Blinn power 9.997), not 0.0 (power 1.28). `or 0.0` is the bug (`gpu_driven.rs:2317`, `import_eftpack.py:1117-1120`) | 4 of 1,057 authored none, all on streets_nav |
| `opacityScale` | quantized through `glass_refl`'s top byte, `round(clamp(v,0,8)/8*255) * 8/255` (`gpu_driven.rs:2313`). 1.0 lands on 1.003921; **0.0 lands on 0** and is floored to 0.03 by the shader's `max()`, which is what makes those panes near-holes (`gpu_draw.wgsl:1601`) | 21 of 1,057 ship 0.0, all on streets_nav; only 38 author the field at all |

### What still diverges, deliberately

`(1.0 - shadow_event)` multiplies both additive lobes (`:1638`, `:1649`). It is a cascaded shadow-map contact correction applied to specular; an offline renderer has no equivalent and an emission takes no shadow ray. Both terms are bounded and both are near zero on a pane facing away from the dominant light, so the exposure is a shaded pane still carrying its `<= RC*fresnel` sky term. Do not re-attach a BSDF to "fix" it.

`apply_fog` is applied to the DIFFUSE ONLY (`:1661`); reflection, specular and emissive are unfogged. If an external renderer adds a fog volume, it will fog all four uniformly. That asymmetry is the reference's, not a mistake.

The Blender node graph that implements all of this is in [blender-import.md](blender-import.md#legacy-glass-glasstrs-is-four-lobes-not-a-principled).

---

## 6. The coplanar push has no path-traced equivalent

The renderer separates coplanar overlays in CLIP space:

```wgsl
o.clip.z = o.clip.z + 1.0e-3 * o.clip.w;      // gpu_draw.wgsl:992 (DECAL_NDC_PUSH), :1001 (SURFACE_PUSH)
```

Under `SURFACE_PUSH` it is decided per MATERIAL: `MAT_FLAG_DECAL | MAT_FLAG_WATER` get it, true transparency (glass) stays unbiased (`:997-1002`). After the perspective divide this is exactly `+eps` on `z_ndc` and therefore exponent-INDEPENDENT, which a constant depth offset on `Depth32Float` is not. It is applied only on colour passes that do NOT write depth, so it moves the depth used for the test and never a written depth or the screen xy; the depth prepass deliberately does not get it (`:983-991`).

The real failure it fixes is not decal-over-road but decal-over-decal: two stacked SoftCutout roads whose colour passes each failed `GreaterEqual` against the OTHER's prepass write.

**A path tracer has no depth bias to borrow.** The same separation has to be geometric: `DECAL_LIFT = 0.006` m (`tools/blender/import_eftpack.py:128`, applied at `:2499`), about 6 mm. Three rules make it work, and all three come from the failure modes of the alternatives:

- **On the MESH, not the object.** Two exactly coincident surfaces make the ray hit a coin toss, which is stippled speckle that changes with the camera. An object-level offset also slides the overlay off the curbs and ramps it was authored against.
- **Along the VERTEX NORMAL**, which keeps the overlay glued to curved and ramped receivers.
- **Only vertices used by decal/water faces move.** A mesh can mix roles, and lifting an opaque face tears it from its neighbours.

This is separate from, and lands on top of, the 12 mm `SURFACE_OFFSET_M` the projector bake already applied to StaticDeferredDecal geometry ([decals.md](decals.md)). Roads, yard slabs and water decals are ordinary submeshes with no bake-time offset, so 6 mm is their only separation.

---

## 7. The grade chain, exactly

`tools/blender/eft_grade.py` is the verified standalone reference (numpy only, no bpy); the runtime authority is `viewer/assets/shaders/grade.wgsl` plus `viewer/src/render/grade.rs`. The format itself is specified in [terrain-and-colour-grade.md](terrain-and-colour-grade.md).

Order matters and every step has a trap in it.

**1. Exposure.** A plain linear scale; the scene carries no tonemap. `DEFAULT_GRADE_EXPOSURE = 1.35` (`viewer/src/render/mod.rs:34`). The shader comment naming 0.18 is the WEB viewer's value and is stale (`eft_grade.py:17-20`). This number is RENDERER-RELATIVE: a path tracer has its own radiance scale set by its sun and world strengths, so either fit the lighting (§8) until 1.35 is correct, or solve exposure separately, once per shot.

**2. Shaper.** `p = sqrt(clamp(lin / 4, 0, 1))` (`grade.wgsl:73`, `eft_grade.py:126`). **NOT sRGB and NOT log.** It exists so a 64-cube can cover HDR up to 4.0. Feed the LUT sRGB-encoded values, as every "apply a .cube" tutorial assumes, and you sample the wrong slice everywhere; the image looks plausible and is wrong.

**3. LUT.** 64x64x64, trilinear. The shipped file is a 512x512 RGBA8 raw dump of the web viewer's 2D-tiled atlas: 64 blue slices in an 8x8 grid, each 64x64, texel `(r,g,b)` at `atlas[row = (b/8)*64 + g][col = (b%8)*64 + r]`. **Its bytes are DISPLAY ENCODED** and the shader works in linear, so every texel's sRGB encode is INVERTED at load (`render/grade.rs:87-111`, an explicit `srgb_to_linear` per channel, packed to f16). Skip the inversion and the grade comes out crushed and oversaturated.

**4. Vignette.** PRISM post stack: `e = (uv - 0.5) * 2 / (1.15, 0.95)`, `vig = 1 - smoothstep(0.55, 1.25, |e|) * 0.488` (`grade.wgsl:181-183`, constants at `render/grade.rs:486-488`). **Authored in DISPLAY space.** The shader's `g` is linear because the swapchain encodes afterwards, so it raises `vig` to the 2.4 exponent to make the post-encode attenuation equal `encode(g) * vig` (`grade.wgsl:185-189`). Applied naively in linear, corners darken about half enough.

**5. sRGB encode** for output.

### The clip point is the thing that decides what blows out

The cube saturates at shaper 0.5, which is LINEAR 1.0 **after** exposure (`eft_grade.py:136-148`) - confirmed by evaluating `packs/shared/grade_lut.bin`: index 32 and every index above it return the same texel. Below the exposure, at the viewer's 1.35, that is every channel at or above about **0.74** linear collapsing to one flat highlight plateau, recorded as roughly `(244, 227, 191)` (`gpu_draw.wgsl:1777-1778`) **[session]**. This is not a cosmetic ceiling: it is why the shader caps water radiance hue-preservingly at 0.18 head-on to 0.28 grazing before the grade ever sees it (`:1785`), because a colour that arrives above the plateau is unrecoverable.

For an external renderer, this is the practical rule: **shoot flat to linear EXR, grade after.** OpenEXR is scene-referred and ignores the view transform, so the look is a post step costing seconds rather than a re-render costing hours, and it is the only way to apply the game's own grade at all, since a baked filmic transform cannot be cleanly inverted. `eft_grade.py --look` skips the LUT and substitutes a display transform, for a straight A/B from the same EXRs. **`--look agx` now means Blender 5.1's real AgX Base sRGB**, reimplemented in numpy and verified against Blender's own OCIO processor to a max error of 0.000021 (0.005 CV) by `--selfcheck-agx`. The curve this file previously described under that name, `srgb(x / (x + 0.155) * 1.019)`, survives as **`--look filmic`**; it lifts 18% grey to 195 CV where every standard transform lands 112 to 120, and has neither a toe nor a shoulder. The six-transform comparison that chose AgX, and the reason its cube must be sampled TETRAHEDRALLY while the game's 64-cube must stay trilinear, are in [blender-import.md](blender-import.md#agx-is-now-really-agx-and-its-cube-needs-tetrahedral-interpolation).

### OCIO: `active_views:` is an allowlist

If the grade is installed as an OCIO view rather than applied in comp, one line decides whether it exists. `active_views:` is an ALLOWLIST, not a display-order hint: OCIO drops every view whose name is not in it, **silently and with no validation error** (`tools/blender/make_ocio_config.py:131-138`). The symptom is indistinguishable from a malformed colorspace, and it is what makes this worth a paragraph.

Two corollaries the same file records: no `active_views:` key at all means "every view is active", so only a PRESENT list needs editing (`:144-146`); and the config must be self-checked by asking OCIO whether the view is active, not by parsing the YAML (`:176-179`), because a mistyped transform key fails the same silent way.

---

## 8. Deriving sun and sky, because the pack has neither

**The pack ships no sun and no sky light.** There are zero `Light` components of `m_Type == 1` on the mainline maps and `RenderSettings.m_Sun` is null; the sun is driven by a day/night SCRIPT at runtime, not by a scene object, and any Directional that does appear is deliberately discarded rather than coerced into the point loop (`viewer/src/eftpack.rs:675-685`, counted and warned in aggregate at `:1293`; [lighting-and-sh-bake.md](lighting-and-sh-bake.md)). The game's outdoor lighting lives entirely in the baked SH irradiance volume, which is sky visibility plus bounce and is marked `"direct": false`.

An offline renderer has no equivalent, so a sun energy and a sky strength have to come from somewhere. Guessing them is how a render ends up looking nothing like the game while every material is correct: too much sun and too little sky blows out lit ground, crushes shaded faces, and is physically inconsistent with the overcast sky texture it is paired with.

**Solve them instead.** A path tracer is LINEAR in every light's power, so for a fixed camera

```
render(sun=a, sky=b)  ==  a * render(sun=1, sky=0) + b * render(sun=0, sky=1)      exactly
```

Two basis renders therefore span the whole space, and the pair becomes a least-squares fit of that combination against a viewer frame of the identical camera, compared **after both have gone through the same grade at the same exposure**. Grading before comparing is not optional: the fit is being made against a non-linear, clipping display chain, so a linear-domain fit optimises radiance the display throws away.

Result on Interchange (`tools/blender/example_scene.py:57-69`):

| | value |
|---|---|
| `SUN_ENERGY` | **6.90** |
| `SKY_STRENGTH` | **2.35** |
| sun:sky ratio, guessed (6.0 / 1.6) | 3.75 |
| sun:sky ratio, fitted | **2.94** |
| RMS error versus the guess | **-16%** (`example_scene.py:63`); `blender-import.md:1026` prints **-20%** for the same fit, unreconciled |

The guess had too much direct sun and too little sky, which is exactly what made sunlit ground blow out while shaded faces went muddy under an overcast sky they were inconsistent with. `SKY_STRENGTH = 2.35` is also the importer's `SKY_STRENGTH_FALLBACK` for the glassTRS reflection probe (`import_eftpack.py:130-136`), and `EFT_SKY_STRENGTH=<f>` overrides it for a clean single-variable A/B (`import_eftpack.py:878-882`).

Re-solve whenever the sky or the map changes. Use a physically sized sun disc while you are there: **0.526 degrees**, the sun's real angular diameter (`tools/blender/cine_camera.py:349`). The stock 1 to 2 degrees quietly widens every contact shadow's penumbra.

---

## 9. Where parity works against photorealism

Everything above makes an image match the GAME. Several of those rules are the reason it will not look like a photograph, and someone will eventually want to "fix" a faithful port. **[photorealism.md](photorealism.md) is where that decision is made**: it ranks the six departures by perceptual payoff per unit of work, states the dependency order between them, and prices each one in parity. This section exists only to name which rule above turns into which entry there, so a reader arriving from §1-§8 does not have to guess.

**The decision is a switch, not a fork.** `tools/blender/example_scene.py` carries `MODE = "game"` and `MODE = "photoreal"` over one builder; the parity path is the default and reproduces everything in §1-§8. Nothing above the fragment's final colour differs between them. The table of what does differ, and which of the six departures were measured and rejected, is in [photorealism: the MODE switch](photorealism.md#the-mode-switch-and-the-three-items-this-file-over-ranked). Two things in that file are parity GAINS rather than departures and are therefore on in both modes: the grass wind (the viewer's own WavingGrass stage, previously discarded by the Blender path) and the `make_sky_equirect.py` azimuth fix, which had been loading the pack sky mirrored.

| Rule here | Becomes | Where it is priced |
|---|---|---|
| The grade clips at linear 1.0 (§7) and desaturates as it brightens (§3) | drop the LUT for AgX or a filmic curve; `eft_grade.py --look agx` is the A/B switch | [photorealism §2](photorealism.md#2-the-grade-clips-and-desaturates) |
| The sky is 8-bit LDR, which is exactly why the sun had to be FITTED rather than read (§8) | a physical sky (Nishita) driven by `volume.json` `sun_dir`, plus a real sun lamp at 0.526 deg | [photorealism §1](photorealism.md#1-the-sky-is-8-bit-ldr) - the highest-value single change, and items 2 and 5 depend on it |
| Legacy TRS glass is a bounded LDR reflection hack with no refraction (§5) | a real dielectric BSDF at IOR 1.52 with thickness and absorption | [photorealism §3](photorealism.md#3-glass-reflects-but-never-refracts) |
| Roughness is a per-material scalar, per-texel only through `roughnessFromAlbedoAlpha`, and only 204 materials carry a detail map (§4) | roughness break-up, cavity/AO, curvature edge wear | [photorealism §4](photorealism.md#4-roughness-is-one-scalar-per-material) |
| Foliage is a three-plane star with a straight-up normal on every vertex (`gpu_driven.rs:3204-3256`, `up_oct = oct_bits(Vec3::Y)` at `:3249`) - the trick that makes an alpha card read as lit grass in a raster G-buffer and as a black mat in a path tracer | keep the card, change only the shading: per-clump thickness, per-instance jitter, a leaning normal | [photorealism §6](photorealism.md#6-foliage-is-a-three-plane-star-with-up-normals) |
| Lighting units are fitted to a game renderer's scale, not to radiometry (§8, and the point/spot conversion in [blender-import.md](blender-import.md#practical-lights)) | nothing to fix - but it is why exposure stops being a fitted constant once the sky is physical | [photorealism §1](photorealism.md#1-the-sky-is-8-bit-ldr) |
| Nothing above covers comp | bloom/glare, CA, optical vignetting, grain - cheapest wins in the list, and the optical vignette REPLACES the grade's own rather than stacking on it | [photorealism §5](photorealism.md#5-camera-and-comp-are-the-cheap-win) |

### Keep these regardless of which direction you go

Nothing in the photoreal list touches any of the following, and all of them are correctness, not style. The same list, with the pointer for each, is in [photorealism.md](photorealism.md#keep-these-regardless).

- geometry and instancing, including the ban on TRS-decomposing an instance matrix ([geometry-and-placement.md](geometry-and-placement.md));
- UVs, the V-flip, and the baked base ST;
- textures and their sRGB-versus-data classification;
- nav routing (`tools/blender/nav_route.py` is a PORT of the viewer's router, not an approximation: a divergence produces a route through a wall, silently);
- the camera solver (`cine_camera.py:142`, `solve_follow_camera`);
- the decal lift (§6);
- every material channel semantic in §4 and §5.

---

## 10. Failure signatures

| Symptom | Cause | Where |
|---|---|---|
| Bright saturated paint renders neutral grey; channel ratio DIFFERS from the texture | `specMap` bound as a per-texel gloss map | §4, `assemble_bevy.py:357` |
| Everything pale and bright; channel ratio MATCHES the texture | exposure or lighting, not the material | §3 |
| Whole frame pale, correct in an open yard, wrong in a corner | one global exposure fitted for a different shot; SH ambient versus full GI | §3 |
| Glass panes read as white foil / milk over a dark interior | reflection traced as unbounded HDR instead of Reinhard-bounded below `_ReflectColor` | §5, `gpu_draw.wgsl:1632` |
| Glass reads as a flat dark square | coverage applied to the whole shader (a Principled Alpha) instead of the diffuse only | §5, `gpu_draw.wgsl:1661` |
| Glass 1.34x too opaque | coverage built from raw `tex.a`; `tint.a` must enter twice | §5, `gpu_draw.wgsl:1602` |
| A few panes per pack wear a much broader, duller glint | absent `shininess` defaulted to 0.0 (power 1.28) instead of Unity's 0.078 (power 9.997) | §5, 4 of 1,057 |
| Panes 8x too tight and far too bright at the peak, using a Cycles Glossy socket | perceptual Roughness squares to GGX alpha; needs `** 0.25`, not `** 0.5` | §5, `import_eftpack.py:1078-1085` |
| Whole panes vanish as holes | `opacityScale` 0.0 quantizes to 0 and is floored to 0.03 | §5, 21 of 1,057 |
| Stippled speckle on roads that changes with the camera | coplanar overlay and receiver, no separation | §6 |
| Decal visible over the road but not over another decal | prepass-versus-colour depth ordering; the push must clear other decals' prepass writes too | §6, `gpu_draw.wgsl:983-991` |
| Rock and cliff surfaces read flat and low-frequency | detail albedo not applied, or applied without dividing out `albedoMeanGain` | §4 |
| Rock surfaces globally darkened or tinted | detail applied at Unity's x2 without the mean-neutralise | §4 |
| Road and yard blends subtly wrong everywhere; no error | `BYTE_COLOR` read through a colour-managed node: byte 128 arrives as 0.2159 | §4 |
| Painted slabs read wet and glossy against matte terrain | vert-paint matte roughness override not applied | §4, `gpu_draw.wgsl:1441` |
| A vp surface resolves to an untinted layer-0 patch | near-black fallback took `a0.rgb` without `v.tint0.rgb` | §4, 471 of 1,127 |
| Roughness biased by material tint | per-pixel roughness taken from `albedo.a` instead of raw `tex.a` | §4, `gpu_draw.wgsl:1432` |
| Grade looks crushed and oversaturated | LUT bytes used as-is; the shipped encode must be INVERTED per texel | §7, `render/grade.rs:91-111` |
| Grade plausible but wrong everywhere | shaper fed sRGB or log instead of `sqrt(clamp(lin/4,0,1))` | §7, `grade.wgsl:73` |
| Vignette corners only half dark enough | vignette applied in linear; it is authored in DISPLAY space | §7, `grade.wgsl:185-189` |
| Bright surfaces collapse to one flat cream tone | above the LUT clip (linear 1.0 post-exposure, about 0.74 at 1.35) | §7 |
| Grade view absent from the DCC menu, looks like a malformed colorspace | `active_views:` is an allowlist and drops unlisted views silently | §7, `make_ocio_config.py:131-138` |
| Sunlit ground blows out while shaded faces go muddy | sun:sky guessed rather than fitted; too much direct, too little sky | §8 |
| Contact shadows softer than the reference | sun angular diameter left at the stock 1 to 2 degrees instead of 0.526 | §8, §9 |
| Grass renders as a black mat | flat up-normals ported into a path tracer | §9 |
| Bush interiors black | `transparent_max_bounces` exhausted inside alpha-tested foliage | [photorealism §5](photorealism.md#5-camera-and-comp-are-the-cheap-win) |
| A character walks through a tanker or a wall | patrol waypoints treated as an ordered path; they are a NETWORK and must be routed on the nav grid | §9, `nav_route.py` |
| A ported "improvement" changed the image and no test failed | a field was bound that the reference never samples, or a lane ported that the reference computes and does not read | §1 |
