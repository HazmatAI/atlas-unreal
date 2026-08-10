# Departing from parity: a photoreal render of this content

Parity and photorealism are not the same goal and past a certain point they are opposed. Everything
in this pipeline up to the fragment's final colour is shared: geometry, instancing, UVs, textures,
decal separation, nav, the camera solver, the meaning of every material channel. Nothing there
conflicts. The conflict lives in two places only, the display chain and the light transport, and it
is concentrated hardest in the colour grade.

The grade is not a neutral tonemap that happens to look stylised. Its 64-cube saturates at shaper
0.5, i.e. **linear 1.0 after exposure**, so everything brighter maps to the same texel
(`tools/blender/eft_grade.py:139`) - verified by evaluating `packs/shared/grade_lut.bin` directly:
a ramp's output stops changing at index 32 and every index above it returns the same texel. And the
cube compresses toward a warm white on the way up, so brightening a surface **desaturates** it. The
measured saturation-versus-index table is in
[game-parity §3](game-parity.md#3-exposure-is-colour-and-it-is-the-first-thing-to-rule-out); it is
not repeated here.

A photographic image does the opposite in both respects: it carries a shoulder over several stops
above diffuse white, and it holds hue into the highlight. Matching the game means accepting a
display chain that has no highlights and loses colour as it gains light. That is the trade, stated
plainly, and everything below is the consequence.

**This file is the second half of a pair.** [game-parity.md](game-parity.md) is how you make an
external renderer match the GAME: the matched-shot method, the channel semantics, the grade chain,
the sun/sky fit, and the failure signature of each. This file is what you give up, item by item,
when you stop matching the game and start matching a photograph. Where a rule is stated there it is
linked here rather than restated, and the two files' numbers are drawn from the same measurements.

The renderer is the authority for what the game does. `viewer/assets/shaders/gpu_draw.wgsl` and
`viewer/src/render/*.rs` are the consumers; prose in `docs/extraction` has been wrong more than
once. Every count below was re-measured against the six shipped packs at the time of writing; every
`file:line` was re-checked against the file. Figures that are session measurements with no
reproducible source are marked **[session]**.

## Contents

- [The order, and why it is this order](#the-order-and-why-it-is-this-order)
- [1. The sky is 8 bit LDR](#1-the-sky-is-8-bit-ldr)
- [2. The grade clips and desaturates](#2-the-grade-clips-and-desaturates)
- [3. Glass reflects but never refracts](#3-glass-reflects-but-never-refracts)
- [4. Roughness is one scalar per material](#4-roughness-is-one-scalar-per-material)
- [5. Camera and comp are the cheap win](#5-camera-and-comp-are-the-cheap-win)
- [6. Foliage is a three plane star with up normals](#6-foliage-is-a-three-plane-star-with-up-normals)
- [Keep these regardless](#keep-these-regardless)
- [How to tell if it worked](#how-to-tell-if-it-worked)

The parity side of every rule referenced here is in [game-parity.md](game-parity.md).

---

## The order, and why it is this order

Ranked by perceptual payoff per unit of work.

| # | Change | Work | Why it sits here |
|---|---|---|---|
| 1 | Physical sun and sky, drop the 8-bit environment | hours | The pack's whole sky spans 3.01:1 and clips at linear 1.0. It bounds every soft shadow, every highlight and every glancing reflection at once, and it is the reason the sun's strength had to be **fitted** rather than derived |
| 2 | Drop the grade LUT for AgX or a filmic curve | minutes | Highest payoff per minute of any item, but it is downstream of 1 |
| 3 | Real transmissive glass | hours | 1,057 materials across the shipped packs reach the glass branch; the current port has no refraction at all |
| 4 | Roughness break-up, cavity, edge wear | days | Only 23 interchange materials carry any micro-variation; this is what reads as "real" up close |
| 5 | Bloom, CA, optical vignette, grain | minutes | Cheap and disproportionately convincing, but bloom needs item 1 |
| 6 | Foliage thickness and translucency | days | Highest cost, and it only matters in shots where the field is the subject |

**Why the sky and not the tonemap first**, given the tonemap is a one-flag change. A filmic curve's
entire value is what it does *above* linear 1.0. The shipped environment's maximum linear luminance
measures exactly **1.0000** against a mean of **0.3327**, a ratio of **3.01**, and its brightest byte
is 255, i.e. clipped. Swap the view transform while that is your only outdoor light source and you
get a flatter picture with a shoulder that has nothing to sit on. Fix the sky, and the same one-line
change has four or five orders of magnitude to work with. Items 2 and 5 both depend on item 1 to pay
off; item 1 pays off on its own.

---

## 1. The sky is 8 bit LDR

**What the pack ships.** `packs/shared/sky/NatureCubemap_equirect.png` is 2048x1024, PNG **bit depth
8**, colour type 2 (RGB). The width is misleading: `tools/blender/make_sky_equirect.py` resamples it
from six **128x128** 8-bit cube faces, so the real angular detail is 128 px per face, roughly 0.7
degrees per texel. `sky.json` records the source's zenith at `[0.646, 0.716, 0.807]` and its horizon
at `[0.032, 0.041, 0.067]`. Measured on the equirect itself: max linear luminance 1.0000, mean
0.3327, **max/mean 3.01**.

**What the pack does not ship: a sun.** Interchange's `lights_64.json` and `lights_520.json` hold
**722 Point and 937 Spot lights and zero Directional**. Outdoor lighting lives entirely in the baked
SH volume, and `volume.json` records `"direct": false`, so the bake carries no sun either. The only
sun datum in the whole pack is `volume.json` `sun_dir = [0.449, 0.799, -0.400]`: a direction, no
intensity, no colour, no angular size.

**Which is why the sun had to be solved, not read.** `tools/blender/example_scene.py` fits it: a path
tracer is linear in every light's power, so for a fixed camera
`render(sun=a, sky=b) == a*render(1,0) + b*render(0,1)` exactly. Two basis renders span the space and
the pair is least-squares fitted against a viewer frame of the identical camera, compared **after
both have gone through the same grade**. Result `SUN_ENERGY = 6.90` (`example_scene.py:68`),
`SKY_STRENGTH = 2.35` (`:69`), which moved the sun:sky ratio from a guessed 3.75 to **2.94** and cut
RMS error against the viewer by **16%** (`example_scene.py:63`; `blender-import.md:1026` prints 20%
for the same fit and nothing reconciles the two - see
[game-parity §8](game-parity.md#8-deriving-sun-and-sky-because-the-pack-has-neither)). That fit is
good engineering and it is still a fit: two free parameters standing in for a measurement the data
does not contain.

**Why it limits realism.** A real HDRI carries the solar disc at roughly 1e4 to 1e5 times the sky
mean. This one carries 3.01:1, clipped. Everything downstream inherits that ceiling: soft-shadow
contrast is bounded by the sky's own range, no specular highlight has a source brighter than three
times the sky mean, glancing reflections mirror a 128 px source and read as flat paint, there is no
energy to bloom from, and a filmic shoulder has nothing above 1.0 to compress.

**What to do instead.** Replace the environment image with a physical sky model (Nishita) plus a real
sun lamp, both driven by `volume.json` `sun_dir` so the direction still comes from the game. Set the
sun's angular diameter to **0.526 degrees**, its true value; the stock 1 to 2 degrees quietly widens
every contact shadow's penumbra. Once the sky carries physical range, exposure stops being a fitted
constant and becomes a camera decision. Keep the shipped equirect as a colour reference for the
horizon/zenith balance, nothing more.

**What it costs in parity.** The fit dies with the image. `SUN_ENERGY = 6.90` and
`SKY_STRENGTH = 2.35` are calibrated to that specific LDR file at the viewer's exposure 1.35; a
physical sky invalidates both, and every A/B against a viewer frame has to be re-solved or abandoned.
Two viewer constants also stop meaning anything: the analytic sky reflection gain
(`SKY_REFL_GAIN = 1.45`) and the distance fog (`FOG_DENSITY = 0.00075`,
`FOG_COLOR = (0.44, 0.49, 0.58)`) are both tuned to this sky's colour and brightness. A physical sky
wants real volumetric aerial perspective, not a per-pixel exponential haze fitted to a horizon
colour.

---

## 2. The grade clips and desaturates

**What the viewer does.** Exposure, shaper, 64-cube LUT, vignette, sRGB encode - five steps, each
with a trap, specified step by step with citations in
[game-parity §7](game-parity.md#7-the-grade-chain-exactly) and in the format reference
[terrain-and-colour-grade.md](terrain-and-colour-grade.md). Only two of those steps decide anything
in this document:

- **Exposure** is a plain linear scale, `DEFAULT_GRADE_EXPOSURE = 1.35` (`render/mod.rs:34`), and
  `EFT_AUTO_EXPOSURE` defaults **off**, so 1.35 is what a reference frame is graded at.
- **The LUT** clips. Its cube saturates at shaper 0.5, i.e. linear 1.0 after exposure.

**Why it limits realism.** Exposure is therefore not a taste control in this chain, it decides what
blows out, and nothing above linear 1.0 survives as distinguishable detail. The cube also desaturates
as it brightens. The combination is the exact inverse of photographic response, and it is why correct
materials routinely look broken here - the worked example, a blue chair that was right the whole
time, is in
[game-parity §3](game-parity.md#3-exposure-is-colour-and-it-is-the-first-thing-to-rule-out).

**What to do instead.** `eft_grade.py` already ships the A/B off the same linear EXRs, and the
"real view transform" half of this recommendation is now done rather than pending. `--look agx` is
**Blender 5.1's own AgX Base sRGB**, reimplemented in numpy and verified against Blender's OCIO
processor to a max error of 0.000021 (0.005 CV) via `--selfcheck-agx`. The placeholder curve this
paragraph used to name, `x/(x+0.155)*1.019`, survives as `--look filmic` for the historical A/B; it
was chosen against five standard transforms and lost on both counts that matter, placing 18% grey at
195 CV (against the 112-120 every standard transform gives) and running the flattest slope at grey
of the six. Two consequences worth carrying: the AgX cube must be sampled **tetrahedrally**
(trilinear peaks at 14.29 CV of error on saturated colour), and the grey anchor is per look, so
`--meter grey` against the wrong one is a 2.4 EV whole-frame error. Full comparison in
[blender-import.md](blender-import.md#agx-is-now-really-agx-and-its-cube-needs-tetrahedral-interpolation).

For look-dev rather than finished frames, install the grade as a Blender View:
`tools/blender/make_ocio_config.py` builds the config. Its one trap: **`active_views:` is an
ALLOWLIST**. A view missing from that list is discarded silently with no validation error, which
presents exactly like a malformed colorspace. The script's selfcheck exists for that failure, and
for a second silent one: the YAML spells `RangeTransform` keys snake_case while the Python API uses
camelCase, and the camelCase spelling loads AND validates before throwing later.

**What it costs in parity.** Total, and it is the loudest single change in this document. Every viewer
comparison in the pipeline is made through this LUT, so dropping it means pixel A/B against a viewer
frame stops being meaningful at the colour level (it stays meaningful for registration and geometry;
see [How to tell if it worked](#how-to-tell-if-it-worked)). The vignette goes with it, and that
vignette is part of the *game's* look rather than the camera's, so its replacement is an optical
falloff, not the same curve re-applied. Do not stack both.

---

## 3. Glass reflects but never refracts

**What the viewer does.** The `MAT_FLAG_GLASS_TRS` branch (`gpu_draw.wgsl:1597-1664`) is a faithful
port of Unity's legacy Transparent/Reflective/Specular family. Three terms, spelled out term for
term in [game-parity §5](game-parity.md#5-legacy-glass-two-bounds-and-a-retired-conversion):

- **Reflection**, bounded by construction:
  `trs_env = sh_ldr * _ReflectColor * fresnel_v`, then `* (1 - shadow_event)` (`:1632`, `:1636`,
  `:1638`), where `sh_ldr = sh_env / (1 + sh_env)` is the baked SH sampled along the mirror vector
  and Reinhard-compressed into the LDR domain the original shader was authored against. The product
  can never exceed `_ReflectColor`. The SH it reads comes from an **indirect-only** bake
  (`volume.json` `"direct": false`), so there is no sun in the reflection at all.
- **Specular**, analytic Blinn-Phong (`:1646-1649`):
  `sun_ldr * _SpecColor * pow(NdotH, _Shininess*128) * NdotL * albedo.a * (1 - shadow_event)`,
  against the SH volume's own dominant light, Reinhard-bounded the same way. Note `albedo.a`, which
  is `tex.a * tint.a`, not the raw `tex.a`. `_Shininess` defaults to 0.078 where a material authored
  none (`gpu_driven.rs:2317`).
- **Transmission**, premultiplied alpha on the diffuse only. Coverage is
  `clamp(albedo.a * max(_OpacityScale, 0.03), 0, 1) * _Color.a` (`:1601-1602`), so **`_Color.a`
  enters twice** - once inside `albedo.a` and once at the end. Building it from the raw `tex.a`
  renders interchange glass `1/0.749 = 1.34x` too opaque. Discarded below 0.03 (`:1603-1605`)
  because a bullet hole at `tex.a ~ 0` is a real hole.

There is no refraction, no thickness, no absorption by path length and no dispersion anywhere in that
branch.

**Counts, measured on the shipped packs.** The flag is gated on
`glass_trs && role == "glass"` (`gpu_driven.rs:2013`), so the count that matters is the gated one:
**153** on interchange, **401** on ground_zero, **503** on streets_nav, and **0** on woods,
factory_rework and icebreaker - **1,057** total. (The raw `glassTRS` field appears on 1,217 records;
the 160 that carry it with some other role get no flag and no response lanes.)

**Two corrections against the code**, both worth stating because both have been written down wrong:

- The `** 0.25` perceptual-roughness conversion is **retired** in `tools/blender/import_eftpack.py`.
  It was needed when the lobe was a Cycles Glossy BSDF, whose Roughness socket is perceptual and
  squares to alpha, so handing it alpha squared it twice. The lobe is now evaluated analytically as a
  value (`pow(NdotH, n)` driving an Emission), which also settles the energy question the BSDF could
  not: the un-normalized Blinn lobe peaks at `<= _SpecColor` and integrates to about
  `SC*2*pi/(n+2)`, while an energy-normalized BSDF integrates to `~SC`, an order of magnitude hotter
  on the shininess-1.0 panes. Reintroduce a Cycles distribution and you need the `** 0.25` back with
  it; the importer's docstring says so at `:1085`, and the whole argument is at `:1079-1085`.
- `viewer/src/render/gpu_driven.rs:2331` still computes `roughness = sqrt(2/(shin*128+2))` for TRS
  materials. The shader's TRS branch selects `trs_env` and `spec_trs` over `refl_rgb` and `spec_rgb`
  (`gpu_draw.wgsl:1638`, `:1651`), so **that lane is dead** for TRS. Do not tune it expecting a
  result.

**Why it limits realism.** Real architectural glass transmits at IOR ~1.52, refracts twice through a
finite thickness, tints by path length, and reflects on a true Fresnel curve with no ceiling. The
`_ReflectColor` bound is exactly correct for the game and exactly wrong for a photograph: it is what
stops a crumpled pane mirroring the whole sky as white foil, and it is also why no pane in this
content ever produces a bright specular event.

**What to do instead.** A real dielectric BSDF: IOR 1.52, modelled thickness or a thin-film
approximation, tint expressed as absorption rather than as a reflection multiplier, and no ceiling on
the reflection. Keep `_Color.a * _OpacityScale` as coverage and keep the sub-0.03 discard, since
those encode real holes in the geometry.

**What it costs in parity.** Measured, and it is large. Tracing the reflection HDR and unbounded put
an interchange facade pane at **5.4x** too light in graded display and **5.5x** too light relative to
the opaque wall beside it, where in the viewer the pane is DARKER than that wall; **86%** of that
radiance entered through the specular node and **89%** of that was world, not sun
(`tools/blender/import_eftpack.py:1058-1061`). A photoreal pane will read brighter than the viewer's
on purpose. When it does, do not "fix" it back to the bound.

---

## 4. Roughness is one scalar per material

**What the viewer does.** `MaterialGpu.roughness` is a single `f32` (`gpu_draw.wgsl:92`), clamped to
`[0.03, 1.0]`, defaulting to 0.55 (`gpu_driven.rs:2333`). The only per-texel path is
`MAT_FLAG_RFA`: `rough = clamp(1.0 - tex.a, 0.06, 1.0)` (`gpu_draw.wgsl:1431-1433`), read from the
**raw albedo texture alpha**, not from the tinted albedo (`tint.a` would bias it). Measured coverage:
**3,608 of interchange's 5,765 materials (63%)**, 5,222 of woods' 6,021 (87%), 13,681 of streets_nav's
19,715 (69%). The shader comment's "82% of the pack" (`:1426`) is no single pack's number.

Two other per-texel overrides exist and both are **compressors, not detail**: vert-paint materials get
`rough = clamp(1 - 0.30*vp_smooth, 0.72, 1.0)` (216 vp records on interchange, 1,237 across the six
packs; see the denominator note in
[game-parity §4](game-parity.md#4-channel-rules-that-have-actually-bitten)), and water floors at 0.10.

**`specMap` is never sampled.** It is parsed at `viewer/src/eftpack.rs:450-451` and referenced nowhere
in `viewer/src/render/`. The assembler already reduced that map to the scalar ("roughness from
_SpecMap luma", `eft_pipeline/assemble_bevy.py:357`), so the field is a **provenance note**. Binding
it looks like an upgrade and is a regression: for **189 of the 2,492** interchange materials that
carry one, `specMap` **is the albedo file** (both counts re-measured), so `roughness = 1 - albedo`
makes bright paint glossy. The red forklift came out at roughness 0.24, mirrored the overcast sky and
rendered neutral grey, measuring R/G **0.96** against the texture's own ratio, where the viewer gives
1.45; after reverting, 1.38. **[session]** (That texture ratio is printed as 1.42 in one place and
1.43 in two others and nothing reconciles them - see
[game-parity §3](game-parity.md#3-exposure-is-colour-and-it-is-the-first-thing-to-rule-out).)

**Detail maps are the pack's only real micro-variation and there are almost none.** 23 materials on
interchange, **every one a rock or cliff** (all 23 base albedos are `AM_Rock_*` or `Arid_rock_*`, all
23 share the one detail map `Grange_detail_D`); 91 on ground_zero, 68 on woods, 22 on streets_nav, 0
on factory_rework and icebreaker - **204** across the six packs. They are mean-neutralised:
`detail * 4.5948 / albedoMeanGain`, clamped `0.25..4`, weighted by `albedoStrength` (0.4 to 1.0 in the
shipped data; 0.699 to 1.0 on interchange, a flat 0.7 on streets_nav).

**Why it limits realism.** One roughness value per material makes every surface of that material read
as a single manufactured piece. What sells a still photograph at close range is variation at the scale
of *use*: dust settling flat and wiping off on edges, cavity darkening in every crease, wear
brightening every corner, a handle polished where hands go.

**What to do instead**, in payoff order. (i) A triplanar noise break-up on roughness, keyed off the
existing scalar so no material has to be re-authored and the mean stays where the pack put it.
(ii) Cavity and AO, baked or screen-space, folded into both the diffuse and the specular occlusion.
(iii) Curvature-driven edge wear on metals and painted props. Extend detail-map coverage past the 23
rock materials with the same mean-neutralise math, since that is the pattern the pack already
establishes and it is already implemented.

**What it costs in parity.** The least of the six. None of this changes what a channel means, so
nothing in the extraction breaks. It does end pixel-level colour comparison against the viewer, which
has none of it. The one hard rule that survives: **do not reach for `specMap` as the per-texel
source**. Same channel, two meanings, and it is the trap this pipeline keeps setting.

---

## 5. Camera and comp are the cheap win

**What is already in.** `tools/blender/cine_camera.py` sets `motion_blur_shutter = 0.5` (`:321`), a
**180-degree shutter**, which is the single strongest cue that a frame came out of a camera rather
than a renderer. It also enables DOF with `aperture_fstop = 2.8` on a 50 mm lens by default
(`:260-266`, focus on an OBJECT rather than a keyed distance), and `solve_follow_camera` (`:142`)
picks the shot. One more that is easy to miss and matters here specifically: raise
`transparent_max_bounces` from 8 to **256**. It is a whole-path budget shared by the camera segment,
every diffuse bounce and the shadow ray, and Cycles fails CLOSED when it runs out, so this is not a
quality setting: at the default it deletes pixels. Measured on the grass field, 8 leaves 14.78% of
the frame at exactly 0.0 luma and the mean 12.2% low; 32 still leaves 0.48% black; 256 is
bit-identical to the 1024 maximum for +0.3 s on a 2.0 s frame. Both modes set it. Full numbers in
[blender-import.md](blender-import.md#foliage-transparency-is-a-whole-path-budget-not-a-quality-knob).

**What is not in, and should be.** Bloom/glare, chromatic aberration, optical lens vignetting, grain.
All four are comp operations on the linear EXR, seconds each, and disproportionately convincing
because they are the artefacts a viewer's eye has been trained on by every photograph they have seen.

Order by cost: **grain** first (free, and it covers a sampler noise floor that is too clean to be
photographic), then **glare**, then a mild **lateral CA**, then **optical vignetting**. Note the
dependency: bloom needs values above linear 1.0 to bloom from, which is item 1 again.

**What it costs in parity.** Near zero for grain and CA. One interaction to watch: the game's vignette
lives *inside* the grade at strength 0.488 in display space, so dropping the LUT (item 2) drops that
vignette with it. An optical vignette is its replacement, not an addition on top.

---

## 6. Foliage is a three plane star with up normals

**What the viewer does.** `gpu_driven.rs:3204-3256` and `tools/blender/import_eftgrass.py:32-34` build
the identical card: **three quads at 0, 60 and 120 degrees**, half-width **0.42 m**, height
**0.90 m**, normals straight **up** (`up_oct = oct_bits(Vec3::Y)`, `gpu_driven.rs:3249`),
alpha-tested. Not a camera-facing billboard, so it reads correctly from any angle and costs nothing
per frame. Interchange ships **3,261,251 clumps** over **12** grass textures
(`grass_sidecar.json`: `count`, `kinds`).

**Why it limits realism.** Up-normals are a rasteriser trick with a real purpose: they make a grass
field shade like the ground it grows out of, which is what stops the field strobing as a light moves.
They are also why nothing in the field ever catches a rim, ever backlights, and ever varies blade to
blade. Real vegetation is thin, translucent, and varies in both.

**What to do instead.** Keep the card geometry, which is cheap and is what the density grid was built
for, and change only the shading. The first step is **already shipped**: `import_eftgrass._material`
mixes a Diffuse and a Translucent BSDF at a flat `Fac = 0.40` behind the alpha test, which is what
stops every card facing away from the sun rendering as a black slab. What is still missing is the
*variation*: per-clump thickness (that 0.40 is one constant for every blade in the map),
per-instance hue and value jitter, and a normal that leans toward the card's own face rather than
straight up. At hero distance swap cards for modelled clumps; at field distance the card still wins
on cost.

**What it costs in parity.** Nothing in the data. Card geometry, density grid and the 12 texture slots
are untouched; only the BSDF differs. The slot rule survives either way: **kind slots are positional**,
so an unresolvable texture keeps its slot rather than shifting every index in `grass.bin`.

---

## The MODE switch, and the three items this file over-ranked

`tools/blender/example_scene.py` builds either image from the same assets. `MODE = "game"` is the
parity path this file's sibling describes; `MODE = "photoreal"` is this file's path. There is one
builder and no forked copy, because nothing above the fragment's final colour differs: geometry,
UVs, nav routing, the camera solve and the 6 mm decal lift are shared code on both paths. Every
difference is collected in the `MODES` dict at the top of that file, so the diff between the two
images is readable as a diff between two dicts.

| | `game` | `photoreal` | Wired through |
|---|---|---|---|
| Atmosphere | uniform slab, density 0.0016 | exponential height falloff, 4e-4 at ground, 40 m scale height | `example_scene._atmosphere_nodes` |
| ...and how it is evaluated | PATH TRACED, because that is what the sun/sky pair was fitted against | the box is built, sized, then `hide_render`'d and applied analytically per pixel from the Z pass | `example_scene._depth_haze_nodes` |
| Glass | `_glass_trs`, the bounded legacy response | Principled transmission, IOR 1.52, transmittance from the game's own coverage lane | `import_eftpack(glass_mode=)` |
| Normal-map self-occlusion | none, as in the shader | cavity map multiplied into Base Color | `import_eftpack(cavity_dir=)`, `bake_cavity.py` |
| Optics | none | 9 aperture blades, Fog Glow veiling glare at threshold 0, lateral CA 0.0012 | `cine_camera(blades=)`, `example_scene._compositor` |
| Display chain | grade LUT, highlight-anchored exposure, authored vignette | **AgX Base sRGB**, centre-weighted metering, cos^4 vignette, photon shot noise | `eft_grade.py --look agx --meter grey --lens --grain` |
| Samples | 96 (`SAMPLES`) | **384** (`SAMPLES * 4.0`) | the untraced atmosphere pays for them: 384 spp untraced measured 87.8 s against the traced frame's 92.1 s |

**Not in the switch, and grass is why.** `import_eftgrass(wind=True)` is on in BOTH modes. It is not
a departure: it is the viewer's own WavingGrass vertex stage (`gpu_draw.wgsl`, `@vertex` "#4") with
`grass_sidecar.json`'s own constants (`strength 1.0, amount 0.157, speed 1.0`), which the Blender
path used to discard entirely. Turning it on closes a parity gap rather than opening one. Same for
selecting grass along the routed polyline instead of a disc about its centroid, and for the
`make_sky_equirect.py` azimuth fix below.

### Three corrections to the ranking table at the top of this file

All three are measured, and all three contradict what this file ranked. They are stated here rather
than edited into the table because the reasoning above them is still right and only the payoff was
wrong.

**Item 1 (physical sun and sky) is not first, and on this content it may not be worth doing at all.**
Two independent tests. A physical Nishita sky delivers **131.4 W/m^2** against the pack's total
**10.42** (sky 4.905 at strength 2.35 plus lamp 6.90 x 0.799 = 5.513, a 47:53 split), i.e. 12.6x the
game's light, and through the game grade **59.6%** of the frame then clips against the parity path's
0.611%. Rebuilding the shipped cubemap as a true HDR environment instead, with a real 46101-radiance
solar disc injected at `sun_dir` and irradiance matched to 0.01%, moved the render by a **uniform
-3.4% to -4.3% at every percentile** - which is exactly the disc's solid-angle quantization, not a
lighting change. Only 0.0045% of pixels moved by more than 0.10 absolute. The reason is that the sun
LAMP already supplies the specular energy and Cycles shows sun lamps in glossy reflections, so
replacing it with a real disc changes almost nothing while costing **+37%** seed-to-seed noise. Two
caveats worth keeping: the shipped sky's 8-bit clip destroys **structure, not energy** (2.63% of
texels have a channel at 255, only 0.56% are pure white, and the clipped plateau sits at radiance
2.35 against the ~1.76 a diffuse cloud lit by the fitted sun would show), so the cost lands on the
587 near-mirror materials rather than on overall light; and the null result was measured on a
courtyard frame where only 0.611% of pixels exceed linear 1.0, which is the weakest possible test
for a sky-reflection change.

**The "12x dynamic range" claim was mostly brightness.** The photoreal test frame's p99.9 of 16.50
against the game path's 1.36 is 12.1x, but its median is also 7.4x higher. Normalised by each
frame's own median it is 12.1 against 7.3, i.e. **1.66x more range**, with 60.4% of the frame above
linear 1.0. Normalised by each frame's own p99 the photoreal frame has *no* more highlight headroom
than the game path (max/p99 2.34 against 2.43); what it actually gained is about 2x the
shadow-to-highlight span.

**The real first item is the atmosphere, and it is not on the list at all.** `example_scene.py`'s
`eft_atmosphere` box is invented - `gamedata.json`, `particles.json`, `volume.json`, `sky.json` and
`manifest.json` were grepped for `fog|haze|atmos|scatter|mist|density` and returned zero hits, and
`volume.json` is the SH irradiance bake (`"direct": false`), not a medium. A uniform slab veils a
wall 5 m away as hard as a treeline at 150 m, which is fog, not aerial perspective. Disabling it
entirely took the frame's p99.9/median range from **7.3 to 9.1 (+25%)** and the render from
**129.9 s to 73.2 s (-44%)**; on a physical-sky variant the same change took range from 13.1 to 25.5
(+95%) and time -64%. It is the only item on this page that improves the image and **buys** time.
`MODE = "photoreal"` therefore keeps a bounded volume but makes it exponential in height and 4x
thinner at the ground. Note that `SUN_ENERGY` and `SKY_STRENGTH` were fitted **with the flat slab in
place**, so the parity path keeps it exactly as fitted; removing it there without re-solving the pair
drops the frame median 16%.

**Item 6 (foliage translucency) is a measured dud.** A 0.35 translucent lobe on all 16 in-scene
foliage materials, alpha cutout preserved, cost nothing (-0.5%, inside noise) and did nothing:
foliage-pixel mean linear luminance +0.5%, canopy contrast 0.637 to 0.630, and the A/B crop is
indistinguishable. Cycles already shades a backfacing double-sided leaf with the flipped normal, so
the black-slab problem that justifies `import_eftgrass.py`'s 0.40 translucent mix does not exist for
trees - that mix is justified there only because the viewer's baked UP normals had to be dropped,
and the reasoning does not transfer. Grass density is a dud for the opposite reason: `grass.bin`'s
3,261,251 clumps are **96.6%** of Unity's own authored detail grids, so multiplying it is invention,
not restoration.

### Two bugs found on the way, both fixed, both parity gains

**`make_sky_equirect.py` loaded the sky mirrored.** Its `equirect()` built `bx = -sin(theta)*ct`,
`by = cos(theta)*ct`, which is `atan2(by, bx) = theta + pi/2`: a 90 degree rotation and a flip of
handedness. Blender's Environment Texture node samples `u = 0.5 - atan2(d.y, d.x) / 2pi`, so the
correct pair is `bx = cos(theta)*ct`, `by = -sin(theta)*ct`. Verified by rendering a synthetic
5-texel dot, which lands on the corrected prediction to 0.300 degrees and 47.596 degrees from the old
one. Fixed, and the shipped equirect regenerated. The visible change on *this* sky is modest (its one
clipped cloud mass happens to sit near the mirror's fixed axis, so its azimuth shifts about 17
degrees) but the handedness was objectively wrong and would matter more on any sky with off-axis
features.

**`sun_rotation = atan2(y, x)` is the wrong convention for Blender's Sky Texture.** The correct one
is `atan2(x, y)`, measured to within 0.101 degrees across five test directions spanning elevation 10
to 65 and azimuth 0 to 300, against 4.03 to 61 degrees of error for `atan2(y, x)`. Also
`sky.sun_direction` is a no-op in 5.1; only `sun_elevation` and `sun_rotation` drive the sun. Neither
applies to the shipped code - `example_scene.py` builds a sun LAMP, whose direction was verified
correct to 0.02 degrees - but any future physical-sky experiment will hit it, and `HOSEK_WILKIE` and
`PREETHAM` have no sun disc in 5.1 and ignore `sun_rotation` entirely (irradiance 0.22 and 1.50
W/m^2, i.e. dusk). Use `MULTIPLE_SCATTERING` or `SINGLE_SCATTERING` or nothing.

---

## Keep these regardless

None of these are look decisions. Breaking one produces output that is plausible and wrong in a way
that is hard to attribute, which is the failure mode this whole pipeline is built against. Every one
of them is specified elsewhere; this list exists so nobody has to guess which side of the parity /
photorealism line a rule falls on. **All of them are on the parity side, and none of them move.**

| Rule | Specified in |
|---|---|
| Geometry and instancing: apply the raw 3x3, never TRS-decompose (shear, mirror and non-uniform scale are all real here) | [geometry-and-placement.md](geometry-and-placement.md), README "THE RULES" |
| UVs, the V-flip, and `manifest.conventions.uvTilingBaked = true` - `uv_xform` is reference only, and applying it double-tiles | [textures-and-materials.md](textures-and-materials.md) |
| Every channel semantic: `specMap` is provenance, `tex.a` is smoothness on the RFA majority and coverage on the TRS family, `BYTE_COLOR` must round-trip through `color_srgb` (byte 128 reads back as linear 0.2159, not 0.5020) | [game-parity §4](game-parity.md#4-channel-rules-that-have-actually-bitten) |
| The vert-paint near-black resolve, `luma(spl) < 0.02` falls back to the TINTED layer 0 | [game-parity §4](game-parity.md#4-channel-rules-that-have-actually-bitten), `gpu_draw.wgsl:1304-1306` |
| The 6 mm decal lift replacing the clip-space push - a path tracer has no depth bias to borrow, and z-fighting is not a style | [game-parity §6](game-parity.md#6-the-coplanar-push-has-no-path-traced-equivalent), [blender-import.md](blender-import.md#coplanar-overlays-a-6-mm-lift-replaces-the-depth-push) |
| Nav routing and the camera solver: geometry and timing, not look, and both are where a shot comes from | [blender-import.md](blender-import.md#walking-a-character-somewhere-real), `tools/blender/nav_route.py`, `cine_camera.py:142` |
| Practical-light energy conversion (watts need converting, not copying) | [blender-import.md](blender-import.md#practical-lights) |

---

## How to tell if it worked

Measurement, not vibes. Five tests, in the order you should run them.

**1. Register before you compare anything.** Drive the viewer to the identical pose and settle it -
the full environment block, the pose convention, the two harness gates that look like crashes and the
`EFT_SHOT_SETTLE` reason are in
[game-parity §2](game-parity.md#2-matching-a-shot-then-matching-pixels). Cross-correlate edge maps and
confirm `dx=0 dy=0` before comparing a single pixel, then compare **matched pixels only**. That
harness keeps working after the look diverges, because geometry parity is not what you are trading
away - which is exactly why the photoreal branch keeps using it.

**2. The channel-ratio test, which survives the tonemap swap.** Compare a rendered **channel ratio**
(G/R or R/B) against the source texture's own ratio, never brightness. Ratio matches but the image is
pale and bright, it is exposure or lighting. Ratio differs from the texture, it is a genuine material
or channel bug. This is the one diagnostic that does not care which view transform you are using,
which is exactly why it is the one to carry across; the two worked examples that establish both
outcomes are in
[game-parity §3](game-parity.md#3-exposure-is-colour-and-it-is-the-first-thing-to-rule-out).

**3. Dynamic range, measured on the linear EXR, never on the PNG.** Baseline: the shipped environment
measures max linear luminance 1.0000 over mean 0.3327, ratio **3.01**, clipped. After a physical sky
the solar disc should measure four to five orders of magnitude over the sky mean. If the histogram
still dies at 1.0, item 1 did not land and item 2 is cosmetic.

**4. Count the pixels above linear 1.0.** Under the game grade every one of them is the same texel,
because the cube saturates at shaper 0.5. Under AgX they must span visibly distinguishable values. If
the above-1.0 fraction is under about 1% you have changed the curve without changing the light.

**5. Fit exposure per shot and hold it.** Exposure agreement between a path tracer and the viewer is
**view dependent**: the viewer's ambient is a baked one-bounce SH volume while a path tracer computes
full GI, so a fit made in an open yard drifts in an enclosed corner (one interior needed 0.70 where the
yard wanted 1.35). Solve it once per shot, then freeze it for the whole shot.

One negative test worth keeping in the suite: re-introduce the `specMap` binding and the unbounded
glass reflection deliberately, once, on a shot you know, and confirm both still show up in the numbers
(forklift R/G, glass-to-wall ratio). If the photoreal chain hides those two, it will hide the next one
as well.
