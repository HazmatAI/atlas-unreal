# `tools/blender` - rebuilding a pack in Blender

A set of standalone scripts that load an `.eftpack` map (plus its character, weapon, grass, light
and nav sidecars) into Blender and render it with Cycles. They exist to answer two different
questions, and the collection is organised around the fact that those questions disagree:

- **Does another renderer read the pack the way the viewer does?** A path tracer is an independent
  implementation of the same material rules, so a divergence between the two is a bug in one of
  them. Several of the channel bugs recorded in `../../docs/extraction/` were found this way.
- **What does this content look like as a photograph?** The game's display chain has no highlights
  and desaturates as it gains light. Departing from it is a deliberate act, and every departure is
  labelled as one.

Nothing here imports from the rest of the repository. Each script is a single file that reads a
pack and does one thing, so it can be pasted into Blender's text editor, `exec`'d from a session,
or run from a plain interpreter, whichever suits. The only exception is `make_ocio_config.py`,
which imports `load_grade_lut` from `eft_grade.py` beside it.

## Prerequisites

- **Blender 5.1.** Everything here is verified against it. 4.2+ is likely to work for import, but
  the compositor and animation APIs moved in 5.1 and most of the moved calls fail *silently*
  (see [blender-import.md](../../docs/extraction/blender-import.md#the-51-compositor-and-animation-api-moved-and-most-of-it-fails-silently)).
  numpy ships with Blender; nothing else is needed inside it.
- **A built pack**, `packs/<map>.eftpack`, from `python tools/build_map.py <map>`. Packs are
  game-derived and are never committed; build your own.
- **The dataset** (`scene.json` + terrain control maps) if you want the real MicroSplat terrain
  rather than the baked slice. `terrain_splat.py` reads it directly.
- **A character and a weapon**, if you want an actor:
  `python extraction/characters/build_character.py --character scav` and
  `python extraction/characters/build_weapon.py --item <template id>`, which write into
  `out/characters/` and `out/weapons/`.
- **Outside Blender**, for the four scripts that do not use `bpy`: `numpy`, `PIL` (Blender ships
  neither PIL nor an EXR reader) and, for grading EXRs, the official `OpenEXR` bindings
  (`pip install OpenEXR`; opencv is deliberately not used, its 5.x wheels have OpenEXR compiled
  out).

## Pipeline order

The order matters, and `example_scene.py` is it, end to end and runnable. Each step below is a link
in that file.

1. **Route first.** `nav_route.py` turns a `gamedata.json` patrol way into a walkable path. Do this
   before importing anything, because the route decides where the map region is centred. Patrol
   waypoints are a network, not an ordered path: a straight line between two of them walks through
   whatever stands in between.
2. **Map.** `import_eftpack.py` imports geometry, materials and the terrain slice within a radius of
   that route. The region filter tests each instance's world AABB, never its centre.
3. **Terrain.** `terrain_splat.py` replaces the baked terrain slice with the real MicroSplat blend.
   Run it after the map, it rebuilds materials the importer created.
4. **Grass.** `import_eftgrass.py`, distance-limited along the route and with `wind=True` for the
   pack's own WavingGrass stage. Keep it out of the shadow pass, as the viewer does.
5. **Lights.** `import_eftlights.py`. The sun lights the outdoors and nothing else; without the
   practicals (Interchange ships 1,659) every interior is black, and a lit shop seen through a
   window reads as a broken glass material rather than as an unlit room.
6. **Character and weapon.** `import_eftchar.py` then `import_eftweap.py`. The weapon attaches to
   the bone's own matrix, so the importer's bone-axis correction has to be undone; it publishes the
   correction rather than leaving each caller to rediscover it.
7. **Walk it.** Sample the routed polyline, take the heading from a lookahead rather than the
   current 0.5 m grid segment, and resolve the ground the way the viewer does (greatest walkable
   surface at or below the feet plus a step allowance, cast down from that cap and never from the
   sky).
8. **Camera.** `cine_camera.py` solves the whole move at once rather than placing the camera frame
   by frame and smoothing afterwards.
9. **World and sun.** `make_sky_equirect.py` converts the pack's cubemap once; the sun direction
   comes from `volume.json`, but its *strength* and the sky's do not exist in the pack and must be
   fitted (below).
10. **Render flat, grade after.** Cycles to linear EXR, then `eft_grade.py`. The render stays
    scene-referred, so one set of frames can answer both questions.

## The scripts

| script | runs | what it does |
|---|---|---|
| `example_scene.py` | in Blender | the whole pipeline above, end to end. Carries `MODE` and the `MODES` table |
| `import_eftpack.py` | in Blender | map geometry, materials, shear baking, the AABB region filter, the 6 mm coplanar lift, and the ported shader families: glassTRS, puddle and deep water, parallax, the vert-paint splat, detail albedo. Carries the two photoreal switches, `glass_mode=` and `cavity_dir=` |
| `import_eftchar.py` | in Blender | armature, skinning and clips as Actions; publishes the bone-axis correction as `eft_q4` |
| `import_eftweap.py` | in Blender | weapon mesh and bone attachment |
| `import_eftgrass.py` | in Blender | the grass field from `grass.bin`; `wind=True` ports the pack's own WavingGrass stage as a Geometry Nodes modifier |
| `import_eftlights.py` | in Blender | the practical lights from every `lights_*.json` sidecar, with Unity intensity converted to watts |
| `terrain_splat.py` | in Blender | rebuilds terrain materials as the real MicroSplat blend |
| `cine_camera.py` | in Blender | solves a follow camera over the whole shot (Viterbi over candidate positions), plus `cinematic_render_settings` |
| `nav_route.py` | anywhere | routes between points on the pack's baked nav grid. numpy only, no `bpy`. A port of the viewer's router, not an approximation |
| `eft_grade.py` | anywhere | the game's display chain (exposure, shaper, 64-cube LUT, vignette) applied to linear EXR, plus the AgX and filmic looks and the photoreal metering, optical vignette and grain stages. `--selfcheck-agx` diffs its AgX against Blender's own OCIO |
| `bake_cavity.py` | outside Blender | Poisson-integrates every normal map the pack ships into a multi-scale cavity map for `import_eftpack(cavity_dir=)`. Photoreal only: the game shader has no AO term at any scale |
| `make_ocio_config.py` | outside Blender | writes an OCIO config that installs the game grade as a selectable Blender View, so look-dev happens under the game's look instead of AgX |
| `make_sky_equirect.py` | outside Blender | the shipped cubemap faces to an equirectangular world texture |

`../../docs/extraction/blender-import.md` carries the same table with the node graphs, the derived
probes and the traps behind each entry.

## The two modes

`MODE` at the top of `example_scene.py` selects which of the two goals a run is serving. Geometry,
UVs, nav routing, the camera solve and the decal lift are identical in both, because there is
nothing photographic about where a wall is. Everything the modes disagree about is collected in one
`MODES` dict, so the diff between the two images is readable as the diff between two dicts.

| | `"game"` | `"photoreal"` |
|---|---|---|
| authority | the viewer, frame for frame | a photograph |
| glass | the bounded legacy `glassTRS` response, term for term | real transmission |
| cavity | none, the game shader has no AO term | the normal maps' own self-occlusion |
| atmosphere | a uniform slab, traced, exactly as the sun and sky pair was fitted | height falloff, applied analytically from the Z pass |
| comp | none, the viewer has none | veiling glare and lateral CA, both calibrated |
| display | the game's 64-cube grade | AgX with centre-weighted metering, optical vignette and grain |

Run one:

```
blender --python tools/blender/example_scene.py           # honours MODE, or EFT_MODE in the env
```

or, in a live session, `exec(open("tools/blender/example_scene.py").read())` and then
`build("photoreal")`. It builds and configures the scene; render it, then grade the frames:

```
python tools/blender/eft_grade.py frames/ out/ --auto
python tools/blender/eft_grade.py frames/ out/ --look agx --auto --meter grey --no-vignette --lens 50 --grain 15000
```

Two things about running it that cost real time to learn. `scene.cycles.device` is saved in the
`.blend` but the *enabled devices* are an addon preference, so `--factory-startup` resets them and
Cycles falls back to CPU without saying so. And the camera solve is the slowest step in the file:
it is one Python thread doing `frame_set` plus raycasts plus keyframe inserts, so solve only the
frames actually being shot.

## `examples/` - the worked shots

`example_scene.py` stages one outdoor walk and renders one kind of frame. [`examples/`](examples/)
carries the shots that are actually published, each as a chain of scripts that is readable in
order, plus two standalone checks that answer a question about a pack rather than making a picture
out of it. Every path in them defaults under `renders/`, every one is overridable from the
environment, and none of them edits `example_scene.py`: they load it and set its globals, so both
stagings stay runnable.

| chain | runs | what it does |
|---|---|---|
| `examples/mall_interior_build.py` | in Blender | routes the boss patrol, builds the map region around it, and probes four static 35 mm cameras before spending a render on them |
| `examples/mall_interior_cams.py` | in Blender | re-stages a built `.blend`: curated cameras, the actor in frame, and the compositor rebuilt without the depth haze |
| `examples/mall_interior_render.py` | in Blender | renders the static cameras to linear EXR, and carries the two lighting controls |
| `examples/mall_interior_grade.py` | outside Blender | linear statistics first, the AgX grade second |
| `examples/exterior_photoreal_build.py` | in Blender | the outdoor photoreal build, plus an 80x45 raycast probe of every candidate frame that rejects the ones where the solved boom has parked the lens inside a bush |
| `examples/exterior_photoreal_render.py` | in Blender | renders the chosen frames flat, and sets the transparent-bounce budget that decides whether the lower half of a foliage frame exists |
| `examples/exterior_photoreal_grade.py` | outside Blender | linear statistics, an exposure sweep, and the AgX photoreal finish |
| `examples/sequence_game_render.py` | in Blender | a whole patrol as a SEQUENCE in game mode, on the previous ZonePowerStation staging, with `cinematic_render_settings` and motion blur. Grade with the stock `eft_grade.py` |
| `examples/mode_comparison.py` | in Blender | one frame of one mode, from a camera FORCED to be the same one in both. Run it twice |
| `examples/mode_comparison_compose.py` | outside Blender | finishes each half the way its own mode intends and composes them side by side |
| `examples/verify_decal_bake.py` | outside Blender | audits a pack and its dataset for the two decal claims every frame here depends on: that the 60 degree facing cull was applied (falsified by a pair of kept normals more than 120 degrees apart, which needs no knowledge of the projector axis) and that the SoftCutout flag survived into `materials.json`. numpy only |
| `examples/verify_glass_bounds.py` | in Blender | imports one small region in each glass mode and asserts STRUCTURALLY that the legacy `glassTRS` reflection is bounded: no BSDF in either additive lobe, the environment entering as a value through a `E/(1+E)` Reinhard, and a ceiling of `_ReflectColor`. Nothing on the photoreal path exercises this, which is why it is a separate check |

### Shooting an interior

The mall chain is the worked interior, because an interior is a different problem from a vista.
Geometry, terrain, lights, character and every material rule come from `example_scene.py`
unchanged, because none of them care whether there is a roof. Three things do, and they are the
reason this is committed rather than described:

- **The walkable floors come from `gamedata.rooms[]` and `patrol_ways`, not from the nav grid.**
  The mall's floors are pack Y=27.1 (level 1) and Y=36.6 (level 2). The nav grid *also* reports a
  walkable layer at Y=21.3 under the whole footprint, and that layer is the outdoor ground the
  building stands on. Resolving a floor from it drops the camera through the slab onto the parking
  deck, silently, and the frame is daylight and parked cars. Stage from `rooms[]`, let the raycast
  refine upward, and clamp how far it may fall.
- **The practicals are the interior's entire light budget.** Measured on the central square, 541
  imported practicals give a median linear 0.15416; sun and sky alone, with the practicals hidden,
  give 0.00088, 175x darker. The trap is that with AgX and grey metering an unlit room still grades
  to a plausible looking picture, because auto exposure puts whatever median it finds at middle
  grey. The graded PNG cannot tell you whether the room is lit; only the linear statistics can.
- **Depth haze must be off indoors.** The analytic in-scatter fitted for a 150 m outdoor vista
  contributes 0.0199 linear at 60 m, which is more than the surface it covers in a room whose
  median is 0.014, and the frame reads smoke filled. Glare and chromatic aberration stay on: they
  are lens behaviour and do not care what is being photographed. `diffuse_bounces` goes 4 to 8,
  because indoors the interreflection *is* the light.

Every path defaults under `renders/` and every one is overridable from the environment, so the
chain runs from a clean checkout with a built pack and nothing edited:

```
blender --background --python tools/blender/examples/mall_interior_build.py
blender --background --python tools/blender/examples/mall_interior_cams.py
blender --background --python tools/blender/examples/mall_interior_render.py
python  tools/blender/examples/mall_interior_grade.py stats renders/mall/mall_test_01.exr
```

### Shooting an exterior still

The exterior chain builds the shipped ZoneBearCamp staging in photoreal mode and then does the one
thing a still out of a solved shot needs and a sequence does not: it **probes every candidate frame
before rendering any of them**. A solved follow camera is a continuous move through a forest, and a
forest is full of places a camera should not stop. An 80x45 raycast grid per candidate, classified
by material role, plus a six-axis 0.5 m probe from the camera origin, rejected frames 44, 68, 140
and 236 on this route (100% grass or foliage, `boxed` 3 to 6, i.e. the boom collapsed to its 2.34 m
minimum and the lens is inside a bush) and frames 56, 80, 104 and 128 (55-65% terrain at 1.5-1.8 m
median depth, the camera skimming the ground). None of those is visible in the camera keys and each
costs a 30 to 66 second render to find by eye.

The second thing it carries is `transparent_max_bounces`. It is a WHOLE-PATH counter and Cycles
fails closed: at the default of 8, 13.22% of frame 20 is exactly 0.0 luma and the frame mean is 40%
low. It never reads as pure black in the EXR, because the analytic depth haze adds in-scatter on top
of the dead pixel, so the file contains no zeros and the defect measures 0.00129 linear.
`cine_camera.cinematic_render_settings` knows this and sets 256; nothing calls that function, so
the render script sets it and prints what it was.

### Every "sky 0.00%" probe in this repository was reading the inside of a box

`hide_render` is a RENDER flag. `example_scene` builds `eft_atmosphere` as a real mesh cube
`MAP_RADIUS * 2.2` across and `ATM_HEIGHT` tall, and the photoreal path sets `hide_render` on it so
Cycles does not trace it and the haze is applied analytically from the Z pass instead. That flag
does not remove the object from the view layer, from the evaluated depsgraph, or from
`scene.ray_cast`.

So every raycast probe written before this was noticed - the frame probe in
`exterior_photoreal_build.py`, the camera probes in both mall scripts - was casting into the inside
of a closed 528 x 528 x 80 m room. Three consequences, in order of how badly they mislead:

- **`sky` could not be anything but 0.00%**, for any scene, closed or open or empty. Any conclusion
  drawn from it ("the shell is closed", "the camera is inside the building", "this pose sees no
  sky") was a tautology, not a measurement.
- **Long rays terminated on the box wall**, so `p90` and `mean` depth were capped at the box's own
  half width rather than at whatever the map does.
- **Those hits were counted as `solid`**, because no classifier had a name for the box, so the
  built-geometry fraction was inflated by exactly the rays that left the map.

It was caught on an outdoor ZoneRoad staging, where all 144 swept poses came back sky 0.00% with
p90 depth 290-350 m - which is the box wall, and not any surface on Interchange - so the ranking
built on top of them was ranking nothing. The same pose reads **sky 73.96%** once the box is
`hide_viewport`'d, which is the flag that takes an object out of the depsgraph.

The three probe scripts now set `hide_viewport` for the probe and clear it again before the save,
so the `.blend` is written in the state `example_scene` left it in. `exterior_photoreal_build.py`
prints the before/after at its first candidate frame, so the size of the correction is measured on
every run rather than taken on trust. And `atmosphere` is a probe class of its own in all three, so
if the box ever comes back it appears in a column instead of hiding inside `solid`.

Two things this does NOT invalidate. The frames the exterior probe rejected are still rejected: a
lens buried in a bush reads 100% grass or foliage with `near` at 100% either way, and a 1.5 m
median depth is not a 200 m box. And the mall's shell really is closed - that conclusion happens to
be true, which is exactly why a vacuous check survived so long behind it. Neither is an argument
for having measured it the way it was measured.

### Shooting a sequence

`sequence_game_render.py` is the same pipeline aimed at a shot rather than a still, in game mode,
on the previous ZonePowerStation staging (`example_scene` still records it in a comment). It builds
once, saves the `.blend`, and reuses it on later runs so a re-render costs only the render. Render
settings come from `cine_camera.cinematic_render_settings` rather than being restated: 256 samples,
adaptive threshold 0.005, 180 degree motion blur, persistent data. Frames go out as half-float DWAA
because a lossless 32-bit 360-frame sequence is ten times the disk for a grade that quantises to 8
bits anyway. The default renders four spread frames; `EFT_FRAMES=all` renders the shot, about 3.2
hours on two RTX 5090s under OptiX.

### Both modes at one camera

`mode_comparison.py` renders one frame of one mode per process, deliberately: each build is ~3,700
objects and 300 MB, and `_clear()` unlinks objects without purging the datablocks behind them, so
building both in one session runs out of memory on the second.

**The camera has to be forced, and that is the whole reason the script exists.** `cine_camera`'s
solve is a Viterbi pass whose per-state cost is measured by RAYCASTING THE SCENE - body-point
visibility, ground clearance, how much grass is in the way - so the solved move is a function of the
geometry. The two modes do not import identical geometry, and neither do two builds separated by a
change to the importer. A comparison whose halves have drifted by half a metre is worthless: the eye
reads the parallax, not the shading. So the first run writes the solved camera at the target frame
to a JSON sidecar and every later run clears the camera's animation and assigns that matrix,
pinning the DOF focus empty with it. What is deliberately *not* forced is `aperture_blades` (0
against 9) and the sample count (`MODES` gives photoreal 4x, because it does not path-trace its
atmosphere and spends the freed time on samples): both are things the comparison exists to show.

## Reproducing the images in the top-level README

Every image on the front page is one of these chains. The table is the mapping; the commands under
it are literal. All five are Interchange, `packs/interchange.eftpack`, 2560x1440, rendered flat to
linear EXR with the display transform applied afterwards by `eft_grade.py`, and every photoreal one
runs with `transparent_max_bounces` 256.

| README image | chain | mode | patrol zone / span | MAP\_RADIUS | frame | samples | grade |
|---|---|---|---|---|---|---|---|
| `docs/img/interchange-bearcamp-checkpoint-photoreal.jpg` | `exterior_photoreal_*` | photoreal | `ZoneBearCamp` (3, 5) | 190 | 356 of 360, 50 mm f/2.8 | 640 | AgX, E 0.76320, cos^4 50 mm |
| `docs/img/interchange-mall-interior-photoreal.jpg` | `mall_interior_*` | photoreal | `ZoneCenterBot` / way `BossWay1` | 110 | static `mall_cam_03`, 35 mm | 384 | AgX, E 0.93650, cos^4 35 mm |
| `docs/img/interchange-bearcamp-photoreal.jpg` | `exterior_photoreal_*` | photoreal | `ZoneBearCamp` (3, 5) | 190 | 14 of 360, 50 mm f/2.8 | 640 | AgX, E 1.68920, cos^4 50 mm |
| `docs/img/interchange-powerstation-game.jpg` | `sequence_game_render.py` | game | `ZonePowerStation` (0, 6) | 150 | 140 of 360, 50 mm f/2.8 | 256 | game LUT, E 1.35, authored vignette |
| `docs/img/game-vs-photoreal.jpg` | `mode_comparison*` | both | `ZoneBearCamp` (3, 5) | 170 | 300 of 360, 50 mm f/2.8 | 96 game, 172 photoreal | left game LUT E 1.35; right filmic E 0.28909, cos^4 50 mm |

**The mall central square**, photoreal. `GRASS_RADIUS` is not used (interior), `diffuse_bounces` 8,
depth haze OFF, glare and CA ON, `transparent_max_bounces` 256, DOF off.

```
blender --background --python tools/blender/examples/mall_interior_build.py
blender --background --python tools/blender/examples/mall_interior_cams.py
EFT_CAMS=mall_cam_03 blender --background --python tools/blender/examples/mall_interior_render.py
python tools/blender/examples/mall_interior_grade.py auto renders/mall/mall_test_03.exr
```

`auto` grades each frame at its own grey-metered exposure, which for this frame is 0.93650; the
optical vignette is the 35 mm the cameras use, grain `N_sat` 15000 at seed 0.

**The checkpoint and the wooded scav camp**, photoreal, two frames of ONE solved 360-frame walk and
therefore one build. `GRASS_RADIUS` 40 with the pack's own wind, `HAZE_ZMAX` 209 (= `MAP_RADIUS` x
1.1), bevel 2 mm, height-falloff haze applied analytically from the Z pass, glare and CA on,
`transparent_max_bounces` 256, 640 samples.

```
EFT_PROBE_STEP=6 blender --background --python tools/blender/examples/exterior_photoreal_build.py
EFT_FRAMES=356:06,14:07 EFT_SPP=640 \
    blender --background --python tools/blender/examples/exterior_photoreal_render.py
EFT_E=0.76320 python tools/blender/examples/exterior_photoreal_grade.py final \
    renders/exterior/exterior_photoreal_06.exr
EFT_E=1.68920 python tools/blender/examples/exterior_photoreal_grade.py final \
    renders/exterior/exterior_photoreal_07.exr
```

`EFT_PROBE_STEP=6` rather than the default 12 is what puts frame 14 in front of the probe at all;
frame 356 is in the default stride. `EFT_LENS` stays at its default of 50, which is the solved
camera's own focal length and therefore the right cos^4 optical vignette: grading either frame
through the 35 mm falloff moves it 4.53 CV, 30x the grain, and is the second thing to check if a
rebuild will not land.

Neither exposure is the meter's first answer, and that is deliberate. Both are the frame's own
centre-weighted grey meter pulled DOWN by a swept offset - 1.68920 is Egrey 2.38885 at -0.50 EV,
0.76320 is Egrey 0.90763 at -0.25 EV - because in both frames the subject is off centre and darker
than what surrounds him, so the meter lifts until the sky or the van's roof goes chalky. Run
`sweep` and choose on the shadow and highlight populations. Both exposures were recovered by
solving them back out of the published PNGs: regraded, the two frames match to 0.162 and 0.151 CV
mean absolute over 8x8 blocks, against 0.159 and 0.148 CV between two grain SEEDS of the same
grade, so the residual is the noise realisation and nothing else, and 0.01 EV either way doubles
it.

The older front-page pick from this same walk was frame 20 (slot 01) at E 2.37876, seed 1. It is
still a good frame and still the render script's slot 01; the two published now simply read better,
one at each end of the walk.

`MAP_RADIUS` 190 rather than the shipped 170 is the one value here that is historical rather than
chosen. The region filter used to test each instance's sampled mesh centre, and a 64-vertex stride
aliases on a 513x513 terrain grid, so terrain tile `Slice_2_2` reported its centre 185.5 m from this
route and was culled at 170: `terrain_splat` then printed "rebuilt 0 terrain material(s)" and the
frame rendered ground props over a void, with no error anywhere. `_select` now tests the mesh's
world AABB, whose distance to this route is 0.0 m because the route is inside the tile, so 170 keeps
the terrain too. 190 is kept as the default because it is what the published frame was built with
and the extra 20 m of map is visible behind the actor.

**The power station yard**, game-accurate, finished with the game's own grade. `GRASS_RADIUS` 26,
uniform TRACED haze exactly as `SUN_ENERGY` 6.90 / `SKY_STRENGTH` 2.35 were fitted, `glassTRS`, no
cavity, no compositor, 180 degree motion blur. The routed walk is 181.1 m over 318 nav nodes and
`SHOT_SECONDS` films the first 12.0 s, i.e. frames 1..360 at 30 fps.

```
EFT_FRAMES=140 blender --background --python tools/blender/examples/sequence_game_render.py
python tools/blender/eft_grade.py renders/powerstation renders/powerstation \
    --lut packs/shared/grade_lut.bin --look game --exposure 1.35
```

The grade is the stock CLI at its defaults; 1.35 is the viewer's `DEFAULT_GRADE_EXPOSURE` and the
authored vignette is on. `--auto` would solve the highlight rule instead and land on 1.31156 for
this frame, which is not what was published.

**The two-mode comparison**, one camera, frame 300, on `example_scene`'s shipped defaults
throughout. Left half game, right half photoreal, composed 5128x1440 with an 8 px gap. The camera is
at Blender (-476.73, -97.02, 28.14).

```
EFT_MODE=game       blender --background --python tools/blender/examples/mode_comparison.py
EFT_MODE=photoreal  blender --background --python tools/blender/examples/mode_comparison.py
EFT_E=0.28909 python tools/blender/examples/mode_comparison_compose.py \
    renders/comparison/game_f0300.exr renders/comparison/photoreal_f0300.exr renders/comparison
```

The right half's `EFT_E` is there because the published caption's 0.28909 cannot be re-metered
today. That half was graded when `eft_grade`'s look named `agx` was the curve `x/(x+0.155)*1.019`;
that curve is now named `filmic` (and `agx` is Blender's real AgX Base sRGB), and its 18% grey
anchor was re-solved from 0.0342 to 0.03202 afterwards. The same meter on the same frame therefore
returns 0.27066 now, 0.095 EV darker. `--look agx` is the better picture and a different one.

### What these commands do NOT give you

Read this before diffing a rebuild against a published image.

- **The packs are not in the repository and never will be**: they are game-derived. Build your own
  with `python tools/build_map.py interchange`, plus the dataset for the real MicroSplat terrain and
  a character and weapon for the actor. Nothing here can run without them, and a pack rebuilt from a
  different game patch is a different pack.
- **The camera solve raycasts the scene, so a rebuilt scene solves a different move.** This is the
  big one. `solve_follow_camera` scores every candidate camera position by casting visibility rays
  at the subject, so anything that changes what is in the scene - a different pack, a different
  `MAP_RADIUS`, a change to the region filter or to the grass - changes the solved path, and the
  frame numbers in the table then point at different pictures. Only `mode_comparison.py` is immune,
  because it forces the transforms from a sidecar; that is exactly why it does.
- **Three things moved after the power station sequence was shot**, all of them forward, and all of
  them change its pixels: the grass field went from a disc about the route centroid with no wind to
  a capsule along the routed polyline with the pack's own WavingGrass stage; the region filter went
  from a mesh-centre test to a world-AABB test, which is a strict superset (3,683 to 3,766 instances
  at radius 170); and `cinematic_render_settings` went from `transparent_max_bounces` 32, which
  leaves 0.48% of a foliage frame at exactly 0.0 luma, to 256, which is converged. The staging, the
  camera solve, the resolution, the samples and the grade are unchanged. The frame is the same
  frame; the pixels are not the same pixels.
- **`MODES["photoreal"]["samples"]` has since gone from 172 to 4x `SAMPLES` = 384**, so a rebuilt
  right half of the comparison is cleaner than the published one.
- **The exposures in the table were recovered, not read from a log.** They were solved back out of
  the published PNGs and verified against them: the mall and scav-camp frames come back bit
  identical at the values above, and the comparison's two halves within 1 CV at worst. The one place
  the recovery is a reconstruction rather than a recovery is the *chain* behind the power station
  frame: the shot itself was driven by a script that lived outside the repository and no longer
  exists, so `sequence_game_render.py` is the smallest faithful equivalent rather than that file
  recovered. Its staging, samples, resolution and grade are all confirmed against the surviving
  render logs and against the published frame, which regrades from the surviving EXR to 0.249 CV
  mean absolute, i.e. PNG rounding.

## Sun and sky are fitted, not guessed

The pack ships no directional light at all: every one of its lights is Point or Spot, and the
game's outdoor lighting lives in a baked SH volume that a path tracer has no use for. Only the sun
*direction* is in the pack (`volume.json.sun_dir`). Guessing the two strengths produces the classic
mismatch - blown-out sunlit ground against crushed, muddy shade under an overcast sky it is
physically inconsistent with - and it is invisible until compared against the viewer.

Solve them instead. Cycles is linear in each light's power, so for a fixed camera
`render(sun=a, sky=b)` is exactly `a * render(sun=1, sky=0) + b * render(sun=0, sky=1)`. Two basis
renders span the space; fit `(a, b)` by least squares against a viewer frame of the identical
camera, comparing after both have been through the same grade, because the grade is strongly
non-linear. The constants in `example_scene.py` are that fit for one staging on Interchange, and
they must be re-solved whenever the sky, the map or the staging changes.

## Driving Blender from an external process

Most of this work is done by executing code inside a running Blender rather than by launching it
per change: the map takes minutes to import and a look-dev iteration takes seconds. That needs a
small addon that listens on a socket and runs what it receives on Blender's main thread (`bpy` is
not thread-safe, so a socket thread must hand work to a `bpy.app.timers` callback rather than touch
the scene itself).

**This repository does not ship one, deliberately.** The addon in use during this work carries no
license header and no upstream URL, so its provenance cannot be established well enough to vendor
it. The interface is small enough to describe instead, and any implementation of it will do:

- listen on `127.0.0.1`, TCP;
- read until a terminator, then either parse the payload as a JSON command
  `{"type": ..., "params": {...}}` and reply `{"status": "success", "result": ...}`, or fall back to
  treating it as raw Python;
- queue the work and execute it from a `bpy.app.timers` callback, capturing stdout and stderr;
- reply on the same connection and close it.

A known open-source implementation of exactly this shape is
[`blender-mcp`](https://github.com/ahujasid/blender-mcp) (MIT), whose addon listens on TCP 9876 and
dispatches `execute_code`, `get_scene_info`, `get_object_info` and `get_viewport_screenshot`.
Install it the normal way: Edit > Preferences > Add-ons > Install, pick the file, enable it. It
starts with Blender and needs no configuration.

Nothing in `tools/blender/` depends on the bridge. Every script also runs under
`blender --python`, and that is the mode to use for an unattended render.

## Where the reasoning lives

| question | document |
|---|---|
| Which channel means what per material role, the ported node graphs, the import switches, and the tricks that do not survive a path tracer | [blender-import.md](../../docs/extraction/blender-import.md) |
| Making an external renderer produce the GAME'S image, and how to prove it matches | [game-parity.md](../../docs/extraction/game-parity.md) |
| What to give up for a photograph, ranked by payoff, and what each costs in parity | [photorealism.md](../../docs/extraction/photorealism.md) |
| The tier that edits the assets themselves, and is therefore no longer parity at all | [photoreal-lowergamefidelity.md](../../docs/extraction/photoreal-lowergamefidelity.md) |
| Placement, shear, the handedness conjugation, LODs and the structural culls | [geometry-and-placement.md](../../docs/extraction/geometry-and-placement.md) |
| Material fields, texture conventions, the V-flip, glass and parallax | [textures-and-materials.md](../../docs/extraction/textures-and-materials.md) |
| Projected decals, the facing cull and the coplanar contract | [decals.md](../../docs/extraction/decals.md) |
| The lights, what EFT does not ship, and reproducing its lighting offline | [lighting-and-sh-bake.md](../../docs/extraction/lighting-and-sh-bake.md) |
| The nav grid, colliders and the GameObject-name semantic layer | [colliders-interactables-and-semantics.md](../../docs/extraction/colliders-interactables-and-semantics.md) |
| Everything else | [README.md](../../docs/extraction/README.md) |
