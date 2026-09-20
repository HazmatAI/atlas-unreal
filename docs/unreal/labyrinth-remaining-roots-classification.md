# Labyrinthe — classification des 16 racines restantes

Date d’audit : 2026-09-20. Source : `labyrinth.eftpack`, empreinte `a2fa9ad39c0dc867`. Root1 et Root3 sont exclus de la classification, déjà importés/validés. Ce document classe uniquement les 16 RootId restants : `0, 2, 4–17`.

## Résultat et ordre recommandé

La géométrie active LOD0 totale compte **19 099 placements**. Root1 + Root3 en couvrent **11 953 (62,6 %)**; les 16 racines restantes représentent **7 146 placements actifs (37,4 %)**, 7 698 records bruts, 552 LOD supérieurs et zéro record marqué inactif.

Chemin critique actuel — passe de géométrie structurelle :

1. **Root7 — A / haute confiance**, petit secteur de 629 placements et 93 meshes.
2. **Root6 — A**, puis **Root5 — A**, **Root4 — A**, par petits lots avec validation des bounds et contacts.

**Hors périmètre actuel / différé : Root8–Root16 incluses**, tout en restant inventoriées. Elles regroupent eau, portes/interactifs, DesignStuff/props, alarme, pièges, grille, valve et guntrap; elles pourront être reprises ultérieurement via un pipeline interactif/secondaire. **Root2** (lumières) et **Root17** (décals) restent également différées vers leurs pipelines dédiés. **Root0** demeure ambigu et à investiguer avant toute décision.

Les noms de meshes et racines servent d’heuristiques. Une catégorie A signifie « vraisemblablement structurel », pas vérité visuelle. Les quatre A regroupent 5 430 placements actifs (28,4 % du total map); après eux Root1/Root3 + A couvriraient 17 383 / 19 099 placements actifs (91,0 %), avant les couches secondaires.

## Tableau de couverture, matériaux et collisions

`bruts / LOD0 actif / LOD supérieur actif / inactifs`. Triangles : estimation depuis `idxCount/3`, indiquée **meshes uniques / placements pondérés**. Les compteurs Unreal validés peuvent différer (dégénérés éliminés); ces estimations n’impliquent aucun build. Slots supportés suivent uniquement l’heuristique du master opaque actuel et ne valent pas validation visuelle. Colliders: `candidats blockers / lignes de la racine; flags trigger; lignes candidates convexes non supportées`.

| RootId | Nom exact du manifest | Records bruts / LOD0 / LOD sup. / inactifs | Meshes IDs / noms | Triangles source approx. uniques / placés | Matériaux uniques supportés / total; slots supportés / instanciés | Blockers / colliders; triggers; convex | Classe / confiance |
|---:|---|---:|---:|---:|---:|---:|---|
| 0 | `(sans nom)` | 1 / 1 / 0 / 0 | 1 IDs / 1 noms | 3 690 / 3 690 | 1/1; 1/1 (100.0 %) | 0/0; 0; convex 0 | **E** · low |
| 2 | `SBG_Labyrinth_Light` | 802 / 767 / 35 / 0 | 54 IDs / 44 noms | 61 790 / 450 782 | 17/50; 229/881 (26.0 %) | 117/509; 4; convex 0 | **D** · high |
| 4 | `SBG_Labyrinth_Area_05` | 2 570 / 2 413 / 157 / 0 | 204 IDs / 191 noms | 221 254 / 909 495 | 147/243; 2 309/2 630 (87.8 %) | 544/1131; 0; convex 7 | **A** · high |
| 5 | `SBG_Labyrinth_Area_04` | 1 540 / 1 445 / 95 / 0 | 192 IDs / 182 noms | 143 767 / 369 191 | 137/199; 759/1 507 (50.4 %) | 729/1508; 0; convex 0 | **A** · high |
| 6 | `SBG_Labyrinth_Area_01` | 1 074 / 943 / 131 / 0 | 160 IDs / 155 noms | 121 143 / 401 977 | 113/164; 796/1 002 (79.4 %) | 748/1541; 0; convex 0 | **A** · high |
| 7 | `SBG_Labyrinth_Area_02` | 688 / 629 / 59 / 0 | 93 IDs / 91 noms | 93 379 / 245 759 | 77/114; 363/721 (50.3 %) | 599/1189; 0; convex 0 | **A** · high |
| 8 | `INTERACTIVE_Water` | 4 / 4 / 0 / 0 | 4 IDs / 4 noms | 51 / 51 | 0/3; 0/4 (0.0 %) | 1/4; 2; convex 0 | **B** · medium |
| 9 | `SBG_Labyrinth_DesignStuff` | 142 / 74 / 68 / 0 | 21 IDs / 20 noms | 192 831 / 541 187 | 58/60; 181/191 (94.8 %) | 115/203; 4; convex 28 | **B** · low |
| 10 | `INTERACTIVE_Door_02` | 4 / 3 / 1 / 0 | 3 IDs / 3 noms | 958 / 958 | 3/3; 3/3 (100.0 %) | 3/7; 1; convex 0 | **B** · high |
| 11 | `INTERACTIVE_Door_01` | 8 / 7 / 1 / 0 | 7 IDs / 7 noms | 3 310 / 3 310 | 1/7; 1/7 (14.3 %) | 3/7; 1; convex 0 | **B** · high |
| 12 | `INTERACTIVE_Boss_Alarm` | 10 / 5 / 5 / 0 | 1 IDs / 1 noms | 1 264 / 6 320 | 1/1; 5/5 (100.0 %) | 10/19; 4; convex 0 | **B** · medium |
| 13 | `INTERACTIVE_Spikes_traps` | 588 / 588 / 0 / 0 | 6 IDs / 6 noms | 258 / 29 256 | 1/5; 312/588 (53.1 %) | 0/60; 12; convex 0 | **B** · high |
| 14 | `INTERACTIVE_Lifting_grate` | 5 / 5 / 0 / 0 | 5 IDs / 5 noms | 576 / 576 | 4/5; 4/5 (80.0 %) | 5/9; 2; convex 0 | **B** · high |
| 15 | `INTERACTIVE_Valve` | 1 / 1 / 0 / 0 | 1 IDs / 1 noms | 1 336 / 1 336 | 1/1; 1/1 (100.0 %) | 0/3; 1; convex 0 | **B** · high |
| 16 | `INTERACTIVE_Guntrap` | 6 / 6 / 0 / 0 | 3 IDs / 3 noms | 774 / 2 358 | 4/4; 10/10 (100.0 %) | 2/14; 4; convex 0 | **B** · high |
| 17 | `DECALS_PROJECTED` | 255 / 255 / 0 / 0 | 255 IDs / 255 noms | 161 666 / 161 666 | 0/234; 0/255 (0.0 %) | 0/0; 0; convex 0 | **C** · high |

Les colliders sont associés à la racine par **égalité exacte** entre `colliders.json.root` et le nom unique dans `manifest.roots[RootId]`; leur niveau concorde avec les niveaux géométriques des racines. Association pack/sidecar contrôlée sur les **24 661** lignes par index et champs après conversion affine : 0 ligne divergente, erreur numérique maximale (3,81×10⁻⁶), 0 mismatch Root/Level pour les racines manifestées. Les 298 lignes sans nom manifesté restent séparées (dont `SpatialAudioSystem`: 285); elles ne sont pas arbitrairement attribuées à une racine.

La politique blocker actuelle compte les lignes non-trigger des couches `LowPolyCollider`, `DoorLowPolyCollider`, `TransparentCollider`, `Interactive`, `LevelBorder`, `Terrain`, `Default`; elle exclut HighPoly, la couche Triggers et les flags trigger. Les 35 lignes convexes Root4 (7) + Root9 (28) sont candidates selon la source mais non prises en charge par le chemin actuel complex-as-simple; elles exigent une politique/fallback avant génération.

## Bounds monde et proximité Root1 / Root3

Bounds conservatrices des meshes LOD0 actifs transformés, en mètres Atlas ((min → max)). La proximité est la distance minimum des AABB d’instances non nommées decal. **0** signifie chevauchement/contact de bounds, pas jonction praticable.

| RootId | Bounds min → max (m, axes Atlas) | Distance AABB non-decal Root1 | Distance AABB non-decal Root3 |
|---:|---|---:|---:|
| 0 | 26,884, 0,026, 42,420 → 26,884, 0,026, 42,420 | 0,000 m | 51,118 m |
| 2 | -49,949, -4,470, -35,818 → 51,336, 6,680, 71,668 | 0,000 m | 0,000 m |
| 4 | -53,143, -6,115, 3,105 → -13,576, 12,652, 63,350 | 0,000 m | 43,351 m |
| 5 | -32,215, -1,497, 48,449 → -1,374, 4,070, 69,906 | 0,000 m | 67,916 m |
| 6 | -5,298, -5,140, 37,951 → 46,851, 7,269, 75,898 | 0,000 m | 49,316 m |
| 7 | -10,541, -0,690, -38,554 → 14,971, 9,583, -27,591 | 0,000 m | 15,375 m |
| 8 | 35,957, -0,046, 48,520 → 40,993, 0,291, 59,045 | 3,492 m | 55,022 m |
| 9 | -37,651, -0,550, -36,370 → 51,109, 5,103, 67,739 | 0,000 m | 0,000 m |
| 10 | 48,412, 0,470, -11,790 → 49,443, 2,577, -11,576 | 18,210 m | 0,000 m |
| 11 | -41,937, -0,748, 18,643 → -39,918, 2,186, 20,662 | 15,735 m | 73,831 m |
| 12 | -9,167, 1,335, 1,352 → 13,607, 1,810, 55,928 | 0,000 m | 23,286 m |
| 13 | -16,291, 0,013, 0,657 → 31,405, 0,254, 48,187 | 0,000 m | 21,204 m |
| 14 | -46,485, -0,016, 11,019 → -27,799, 1,879, 40,101 | 2,970 m | 76,477 m |
| 15 | -2,848, 1,963, -31,719 → -2,504, 2,307, -31,691 | 7,175 m | 29,932 m |
| 16 | -25,556, 1,236, 59,362 → -8,265, 2,906, 62,757 | 3,061 m | 79,854 m |
| 17 | -50,966, -3,576, -36,574 → 51,022, 9,583, 75,158 | N/A | N/A |

Contact utile à inspecter si Root7 est importée : Root7 instance 20204 `Bunker_Door_B_01_R_220-110_LOD0` / Root1 instance 6230 `Pipe_support_wall_01_A_LOD0`, overlap AABB d’environ **0,009860 × 0,068143 × 0,013625 m**. Root7 reste à **15,374519 m** de Root3 sur la paire non-décal la plus proche; son AABB ne prouve donc pas une jonction Root3. Root6, Root4 et Root5 ont aussi des contacts AABB avec Root1, à contrôler avant assemblage. Les bounds de Root2, Root9 et Root17 se superposent largement parce que lumières/props/décals sont des couches sémantiques; leur distance n’est pas un critère de raccord architectural.

## Distribution des noms de meshes et indices de contenu

Le JSON adjacent contient la fréquence complète de **chaque nom distinct** parmi les instances actives LOD0, ses IDs de mesh, les bins de fréquence, les niveaux, bounds, couches et paires spatiales. Ci-dessous : bins = nombre de noms apparaissant respectivement 1, 2–5, 6–20 et >20 fois; exemples = les 3 noms ayant le plus d’instances (donc pas un échantillon uniforme).

| RootId | Noms distincts | Bins fréquence (1 / 2–5 / 6–20 / >20) | Exemples les plus fréquents |
|---:|---:|---:|---|
| 0 | 1 | 1 / 0 / 0 / 0 | `baked_world` ×1 |
| 2 | 44 | 12 / 11 / 8 / 13 | `Plafond_lamp_02_source_LOD0` ×86<br>`Plane_LOD0` ×82<br>`Plafond_lamp_02_source_glass_LOD0` ×74 |
| 4 | 191 | 75 / 80 / 29 / 7 | `Brick_broken_05_LOD0` ×593<br>`Brick_broken_04_LOD0` ×533<br>`Brick_broken_03_LOD0` ×296 |
| 5 | 182 | 89 / 50 / 31 / 12 | `Bunker_tunnel_wall_01_decal_A_01_LOD0` ×384<br>`Pipe_flange_01_A_LOD0` ×92<br>`Metal_sling_loop_01_LOD0` ×60 |
| 6 | 155 | 75 / 54 / 15 / 11 | `Pipe_flange_01_A_LOD0` ×130<br>`Pipe_support_wall_01_A_LOD0` ×114<br>`Pipe_support_wall_01_C_LOD0` ×60 |
| 7 | 91 | 38 / 41 / 6 / 6 | `Arc_beam_metal_01_LOD0` ×206<br>`Pipe_flange_01_A_LOD0` ×74<br>`Sand_decal_06_C_LOD0` ×38 |
| 8 | 4 | 4 / 0 / 0 / 0 | `Labyrinth_water_02_LOD0` ×1<br>`Labyrinth_water_02_dirt_LOD0` ×1<br>`Labyrinth_water_02_moss_LOD0` ×1 |
| 9 | 20 | 1 / 16 / 3 / 0 | `Corpse_Civilian_03_LOD0` ×7<br>`Wooden_box_Cup_LOD0` ×6<br>`Wooden_box_LOD0` ×6 |
| 10 | 3 | 3 / 0 / 0 / 0 | `Outside_Door_Metal_17_R_210-100_Door_LOD0` ×1<br>`Outside_Door_Metal_17_R_210-100_LOD0` ×1<br>`model_LOD0` ×1 |
| 11 | 7 | 7 / 0 / 0 / 0 | `Collector_door_metal_round_Decal_A_LOD0` ×1<br>`Collector_door_metal_round_Decal_B_LOD0` ×1<br>`Collector_door_metal_round_Door_Decal_A_LOD0` ×1 |
| 12 | 1 | 0 / 1 / 0 / 0 | `Old_Electrical_Cabinet_B_LOD0` ×5 |
| 13 | 6 | 0 / 0 / 0 / 6 | `Spikes_trap_pike_01_LOD0` ×312<br>`Spikes_trap_wire_01_LOD0` ×96<br>`Spikes_trap_wire_02_LOD0` ×84 |
| 14 | 5 | 5 / 0 / 0 / 0 | `Quest_platform_metal_LOD0` ×1<br>`Quest_platform_metal_cap_LOD0` ×1<br>`Quest_platform_metal_cap_grate_LOD0` ×1 |
| 15 | 1 | 1 / 0 / 0 / 0 | `Item_spec_labyrinth_valve_LOD0` ×1 |
| 16 | 3 | 2 / 1 / 0 / 0 | `Shotgun_trap_LOD0` ×4<br>`reserve_electric_switcher_BASE_LOD0` ×1<br>`reserve_electric_switcher_lever_LOD0` ×1 |
| 17 | 255 | 255 / 0 / 0 / 0 | `decal_bake_00000` ×1<br>`decal_bake_00001` ×1<br>`decal_bake_00002` ×1 |

Signaux par racine (indices textuels, pas identification visuelle) :

- Root0 : seul `baked_world` (1 record) à bounds dégénérées; ambigu/donnée technique probable.
- Root2 : 415 placements dont le nom contient light/lamp; lampes, bougies, Security_Camera; racine au niveau 551. Les sidecars comptent 622 lumières au niveau 551 (464 Point, 158 Spot) et 2 Point au niveau 552, mais aucun champ RootId ne prouve leur affectation par racine.
- Root4 : area géométrique, briques cassées/piliers; le nom `decal` apparaît sur 80 placements, à vérifier comme géométrie/décal.
- Root5 : conduites/câbles et surfaces de tunnel; 587 placements dont le nom contient `decal`, sans présumer qu’il s’agit de decals projetés.
- Root6 : canalisations/supports et murs centraux; mentions de portes/accessoires.
- Root7 : poutres, pipes, ventilation et plancher grillagé.
- Root8 : quatre meshes eau/boue/mousse/trigger; collisions 4 lignes mais seulement 1 candidat blocker simple.
- Root9 : 22 occurrences nommées corpse et 50 container/box; 68 records LOD supérieurs sur 142 bruts, contenu/accessoires probablement mélangés.
- Root10/11 : éléments nommés door; Root11 inclut variantes de porte collector et decals.
- Root12 : seul mesh `Old_Electrical_Cabinet_B`; le nom Boss_Alarm n’établit pas le comportement de l’objet.
- Root13 : 492 placements aux noms spikes/trap, 48 large mud decal et 48 water puddle; 60 colliders exclusivement HighPoly/Triggers selon politique, aucun blocker retenu.
- Root14 : plateformes de quête et switcher/levier.
- Root15 : un objet valve; ses trois boxes ne produisent aucun blocker selon la politique actuelle.
- Root16 : shotgun trap et switch/levier; 14 colliders dont 2 blockers/4 triggers.
- Root17 : 255 noms `decal_bake_*`, un par mesh/placement, aucun collider. Le sidecar decals contient 256 entrées, soit un écart +1 à rapprocher.

Les données secondaires confirment le caractère multi-couche : `decals.json` couvre les niveaux 545–550; lumières niveau 551/552; géométrie principale Area_01…Area_06 se répartit entre les niveaux 545–550; racines interactives niveau 552. Les métadonnées de volume (grille 34×6×36), navigation (224×239, jusqu’à 8 couches/cellule) et gamedata sont globales ou niveau/map-level, **sans RootId fiable**; elles ne doivent pas être attribuées aux 16 secteurs sur le seul nom. Ces couches se superposent intentionnellement aux zones géométriques et doivent suivre des pipelines dédiés.

## Méthode et limites

- Sources : `labyrinth.eftpack`, `eft_assets/labyrinth/colliders.json`, sidecars de lumière/décals, manifeste. Empreinte pack `a2fa9ad39c0dc867`.
- LOD0 actif : `(flags & 0x8)==0 && lodIndex<=0`; LOD supérieur actif : `flags` actif et `lodIndex>0`; inactif : `flags & 0x8`.
- Bounds : scan des vertices de chaque mesh dans `meshes.bin`, AABB locale puis transformation des huit coins par l’affine d’instance; union en espace monde Atlas. Méthode cohérente avec `unreal/Scripts/AuditRootAdjacency.py`; les distances euclidiennes résistent à la permutation d’axes UE.
- Les AABB enveloppent les meshes et peuvent surestimer contact; aucune conclusion sur vue, gameplay, navigation ou ouverture d’une porte n’est faite sans inspection.
- Triangles source estimés par `idxCount/3); le rendu UE peut exclure les triangles dégénérés.
- Compatibilité matériaux = filtre documentaire du master opaque existant, pas mesure esthétique.
- Les colliders suivent le root-name source; aucun lien 1:1 collider→acteur/instance visuelle n’est inféré.
- `volume.json`, `nav.json`, `gamedata.json`, les sidecars lumière/décal sont des données globales/per-level; leur origine RootId n’est pas prouvée.
- Aucun import, build Unreal, commandlet ni mutation live n’a été exécuté pour cette classification.

## Validations manuelles connues

L’utilisateur a rapporté **PASS** pour « Root1 Player Collision » et pour l’alignement de la porte/jonction Root1↔Root3. Cette note retranscrit le résultat utilisateur; cet audit n’a pas répété les essais. La validation MCP post-sync de Root3 (1 944 acteurs géométriques, 4 acteurs/1 702 composants collision et traces Pawn/WorldStatic) reste distincte et en attente.


