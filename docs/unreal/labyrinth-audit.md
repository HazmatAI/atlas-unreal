# Audit du pack Atlas `labyrinth.eftpack`

Date de l'audit : 2026-09-19
Atlas : `0.1.0-3dce665-win64-full`
Pack : `D:\EXTRAtor tarkov to UE\atlas-0.1.0-3dce665-win64-full\packs\labyrinth.eftpack`

## Verdict

Le pack est valide, auto-contenu et suffisant pour construire le premier import complet de Labyrinthe dans UE 5.8. Son journal se termine par `[BUILD OK] pack ready`. Aucun fichier source du jeu n'est nécessaire pendant l'import.

Labyrinthe est un bon POC pour la géométrie, les matériaux complexes, les décals, les colliders, les lumières et les données de gameplay. Ce n'est pas un test de qualification final : il n'exerce ni terrain Atlas, ni grass, ni shear/mirror réel, ni detail/parallax material.

## Inventaire vérifié

| Élément | Valeur |
|---|---:|
| Taille du pack | environ 1,39 Go |
| Fichiers | 756 |
| Meshes | 2 124 |
| Vertices | 1 883 999 |
| Indices | 5 640 441 |
| Triangles | 1 880 147 |
| Submeshes/sections | 2 637 |
| Instances | 21 734 |
| Matériaux | 1 523 |
| Textures distinctes référencées | 738 |
| Références de texture manquantes | 0 |
| Groupes LOD | 17 017 |
| Racines de scène | 18 |
| Colliders | 24 661 |
| Meshes de collision | 1 787 |
| Lumières | 624 |

Le pack annonce `selfContained: true`, une empreinte source `a2fa9ad39c0dc867` et les bornes Atlas suivantes, en mètres, Y-up :

```text
min = (-53.1435, -10.3959, -38.5540)
max = ( 53.4658,  14.7887,  75.8978)
```

## Géométrie et instances

Layout vertex, stride 36 octets :

| Champ | Format | Offset |
|---|---|---:|
| position | `f32x3` | 0 |
| normal | `f32x3` | 12 |
| uv | `f32x2` | 24 |
| color | `unorm8x4` | 32 |

Layout instance, stride 80 octets :

| Champ | Format | Offset |
|---|---|---:|
| affine | `f32x12`, row-major 3×4 | 0 |
| meshId | `u32` | 48 |
| lodGroup | `i32` | 52 |
| lodIndex | `i32` | 56 |
| rootId | `u32` | 60 |
| flags | `u32` | 64 |
| par | `u32` | 68 |
| par2 | `u32` | 72 |
| lv | `u32` | 76 |

Répartition LOD :

- 545 instances hors LODGroup ;
- 18 554 instances au LOD0 ;
- 2 635 instances aux LOD supérieurs ;
- 1 instance `BAKED_WORLD` ;
- 0 instance inactive.

Audit des matrices avec le même seuil que l'importeur Blender Atlas (`max |dot(col_i,col_j)| > 0.02`) :

- shear ou rang dégénéré : 0 ;
- déterminant négatif : 0 ;
- flags mirror : 0 ;
- valeurs non finies : 0 ;
- plus grand produit scalaire normalisé entre colonnes : `0.000536`.

Conclusion : Labyrinthe ne validera pas la branche critique shear/mirror. Des tests synthétiques sont obligatoires dès le lecteur, puis une seconde map réelle devra la valider.

## Matériaux

| Rôle | Nombre |
|---|---:|
| opaque | 1 030 |
| cutout | 103 |
| decal | 383 |
| glass | 3 |
| water | 4 |

| Fonction | Nombre de matériaux |
|---|---:|
| albedo | 1 510 |
| normal | 1 362 |
| emissive | 26 |
| roughness depuis alpha albedo | 862 |
| vertex paint 3 couches | 188 |
| legacy glassTRS | 6 |
| detail | 0 |
| parallax | 0 |
| double-sided | 1 523 |

Points contractuels :

- UV V-flip et `_MainTex_ST` sont déjà intégrés dans les vertices ; `uvXform` ne doit pas être réappliqué.
- Les normal maps sont déjà en convention DirectX. UE doit les importer en `Normal Map`, linéaire, sans second green flip.
- Le RGB de `tint` est déjà linéaire.
- `specMap` est de la provenance : Atlas a déjà réduit son effet dans le scalaire roughness. Il ne faut pas la brancher comme gloss texture.
- L'alpha de l'albedo doit être conservé : coverage, transparence ou smoothness selon le rôle.
- Les décals projetés Atlas ont déjà été transformés en géométrie ordinaire dans le pack.

## Collisions

| Type Atlas | Nombre |
|---|---:|
| box | 578 |
| sphere | 5 |
| capsule | 0 |
| mesh | 24 078 |

Flags :

- triggers : 332 ;
- `NAV_IGNORE` : 5 ;
- colliders associés à un renderer visible : 303 ;
- mirror : 0 ;
- couches Unity distinctes : 8.

La collision physique n'est pas un sous-ensemble de la géométrie visible. L'importeur ne doit donc pas générer la collision uniquement depuis les Static Meshes de rendu.

## Lumière et données annexes

- `lights_551.json` : 464 Point + 158 Spot ;
- `lights_552.json` : 2 Point ;
- volume SH : `34 × 6 × 36`, 4 coefficients L1 × RGB, float16 ;
- bake SH indirect, `direct: false`, une bounce ;
- navigation Atlas : grille `224 × 239 × 8` ;
- gameplay : 2 exfils, 26 portes, 15 spawn points, 51 containers, 12 switches, 94 rooms et 138 room portals, entre autres.

Les unités d'intensité des lumières Atlas ne correspondent pas directement aux lumens UE. Le mapping sera marqué expérimental et calibré visuellement. Le volume SH Atlas n'est pas un Volumetric Lightmap UE importable tel quel ; Lumen est la voie par défaut, avec support SH personnalisé éventuel dans un jalon ultérieur.

## Lacunes du pack v1 pour la cible maximale

Ces éléments sont absents du pack, pas seulement de l'importeur :

1. Tangentes : UE devra d'abord les recalculer en MikkTSpace. Une extension Atlas pourra ensuite exporter le tangent `f32x4` original.
2. UV secondaires/lightmap UV : seul UV0 est présent. Pour Lumen ce n'est pas bloquant ; un bake statique demandera un UV1 généré ou extrait.
3. Hiérarchie GameObject complète : le pack conserve root, niveau, LODGroup et deux ids d'ancêtres repliés, mais pas le nom/id stable de chaque instance ni tout le chemin parent.
4. Certains canaux Unity ne sont jamais capturés : AO, metallic-gloss maps, sampler state exact et ordre fin de render queue.
5. Les textures/materials terrain, grass, detail et parallax ne sont pas représentatifs sur Labyrinthe.

Le lecteur UE doit donc être manifest-driven et tolérer de futurs attributs supplémentaires sans casser les packs v1.

## Qualification minimale après Labyrinthe

La solution ne pourra être déclarée générale qu'après :

- tests synthétiques de shear, miroir, rang dégénéré et winding ;
- import d'une map extérieure possédant terrain layers et grass ;
- import d'une map contenant des matériaux detail/parallax ;
- reimport idempotent après reconstruction du pack ;
- comparaison visuelle Atlas/UE sur des repères asymétriques et du texte lisible.
