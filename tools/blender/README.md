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
