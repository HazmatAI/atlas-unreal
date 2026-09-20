# Labyrinthe : audit des racines et secteur pilote

Audit des records complets ; bounds des huit coins des AABB locales, transformes en metres Atlas (Y-up). Les ids des materiaux utilises figurent dans le JSON adjacent. Couverture = slots instancies compatibles avec le master opaque courant, pas qualite visuelle.

| Root | Nom | Instances | Meshes | Niveaux | Materiaux supportes/total | Slots supportes | Bounds min / max (m) |
|---|---|---:|---:|---|---:|---:|---|
| 0 | (sans racine) | 1 | 1 | [0] | 1/1 | 100.0% | 26.88, 0.03, 42.42 / 26.88, 0.03, 42.42 |
| 1 | SBG_Labyrinth_Area_06 | 11877 | 610 | [550] | 341/597 | 69.8% | -25.48, -4.73, -33.84 / 44.68, 7.40, 65.46 |
| 2 | SBG_Labyrinth_Light | 802 | 58 | [551] | 21/54 | 27.3% | -49.95, -4.47, -35.82 / 51.34, 6.68, 71.67 |
| 3 | SBG_Labyrinth_Area_03 | 2159 | 350 | [547] | 280/351 | 77.1% | 25.91, -10.40, -22.87 / 53.47, 14.79, -6.30 |
| 4 | SBG_Labyrinth_Area_05 | 2570 | 253 | [549] | 191/291 | 87.7% | -53.14, -6.11, 3.11 / -13.58, 12.65, 63.35 |
| 5 | SBG_Labyrinth_Area_04 | 1540 | 235 | [548] | 171/237 | 50.7% | -32.22, -1.50, 48.45 / -1.37, 4.07, 69.91 |
| 6 | SBG_Labyrinth_Area_01 | 1074 | 184 | [545] | 132/187 | 76.5% | -5.30, -5.14, 37.95 / 46.85, 7.27, 75.90 |
| 7 | SBG_Labyrinth_Area_02 | 688 | 108 | [546] | 90/129 | 49.0% | -10.54, -0.69, -38.55 / 14.97, 9.58, -27.59 |
| 8 | INTERACTIVE_Water | 4 | 4 | [552] | 0/3 | 0.0% | 35.96, -0.05, 48.52 / 40.99, 0.29, 59.05 |
| 9 | SBG_Labyrinth_DesignStuff | 142 | 41 | [552] | 112/114 | 97.2% | -37.65, -0.55, -36.37 / 51.11, 5.10, 67.74 |
| 10 | INTERACTIVE_Door_02 | 4 | 4 | [552] | 4/4 | 100.0% | 48.41, 0.47, -11.79 / 49.44, 2.58, -11.58 |
| 11 | INTERACTIVE_Door_01 | 8 | 8 | [552] | 2/8 | 25.0% | -41.94, -0.75, 18.64 / -39.92, 2.19, 20.66 |
| 12 | INTERACTIVE_Boss_Alarm | 10 | 2 | [552] | 2/2 | 100.0% | -9.17, 1.34, 1.35 / 13.61, 1.81, 55.93 |
| 13 | INTERACTIVE_Spikes_traps | 588 | 6 | [552] | 1/5 | 53.1% | -16.29, 0.01, 0.66 / 31.41, 0.25, 48.19 |
| 14 | INTERACTIVE_Lifting_grate | 5 | 5 | [552] | 4/5 | 80.0% | -46.48, -0.02, 11.02 / -27.80, 1.88, 40.10 |
| 15 | INTERACTIVE_Valve | 1 | 1 | [552] | 1/1 | 100.0% | -2.85, 1.96, -31.72 / -2.50, 2.31, -31.69 |
| 16 | INTERACTIVE_Guntrap | 6 | 3 | [552] | 4/4 | 100.0% | -25.56, 1.24, 59.36 / -8.27, 2.91, 62.76 |
| 17 | DECALS_PROJECTED | 255 | 255 | [545, 546, 547, 548, 549, 550] | 0/234 | 0.0% | -50.97, -3.58, -36.57 / 51.02, 9.58, 75.16 |

## Choix

RootId=3, Level=547, GrandparentId=9787 : groupe complet actif hors LOD ou LOD0. 53 instances, 4 meshes, 9 materiaux opaques supportes. Plateformes, escaliers et garde-corps metalliques : ensemble architectural vertical de 3.42 x 22.58 x 3.78 m (axes Atlas). Aucun voisinage spatial ni troncature du groupe.

Les autres petites racines sont des interactifs ou des objets disperses. Les groupes de tuyaux/planches a 100 % de couverture sont souvent disperses ou moins architecturaux. Le groupe retenu couvre plusieurs slots par mesh sans lancer un import massif.

Les ids parent/grandparent sont replies et locaux au niveau : les dossiers Unreal encodent racine/niveau/ancetre/parent ; ils ne pretendent pas restaurer la hierarchie GameObject complete.

Materiaux admissibles : role opaque, alpha OPAQUE, double face, albedo et normal presentes ; aucun bloc vp/detail/parallax/emissive ni glassTRS. Les signatures restantes recoivent un fallback magenta dans le commandlet.

LOD superieurs exclus pour eviter la superposition ; associations LOD, collisions et lumieres source differees.

## RootId 3 complet importé (actif, LOD0) — 2026-09-20

Le tableau ci-dessus compte les records bruts du pack. Pour l’import complet RootId 3, le commandlet sélectionne explicitement les instances actives avec `LodIndex <= 0`, sans filtre d’ancêtre, sans troncature spatiale et sans plafond. Le rapprochement est exact : **2 159 records bruts − 215 LOD supérieurs − 0 inactif = 1 944 acteurs LOD0**. Les 215 records écartés sont des variantes LOD superposées, pas des instances manquantes.

| Sélection | Acteurs | Meshes uniques | Matériaux uniques | Textures distinctes | Slots uniques / instanciés | Slots supportés / fallback |
|---|---:|---:|---:|---:|---:|---:|
| RootId 3 actif, LOD0 | 1 944 | 285 | 292 (229 supportés, 63 fallback) | 200 | 341 / 2 150 | 1 754 / 396 (81,6 % supportés) |

Le compte brut du tableau (350 meshes, 351 matériaux, couverture de slots 77,1 %) et celui-ci diffèrent parce que le premier conserve les records LOD supérieurs. Les matériaux opaques admissibles réutilisent `/Game/Atlas/Common/Materials/M_AtlasOpaque`; les signatures non prises en charge utilisent `M_AtlasUnsupported_Magenta`. Aucun nouveau master Atlas n’est créé. La géométrie importée représente 241 409 triangles sur les meshes uniques.

La map dédiée est `/Game/Atlas/Sectors/Root_003/L_Atlas_Root003_Area03`. Elle contient un acteur par instance, organisé dans l’Outliner par root/niveau/ancêtre/parent ; aucun HISM, World Partition, lumière, collision ou décal n’a été ajouté. L’import Editor Development a réussi avec code 0. Le rapport d’exécution indique 0 doublon d’ID, 0 référence manquante et 0 anomalie. Erreur de translation maximale : 0 cm. Erreur maximale des coins : **0,135975894 cm** (instance 13817, mesh 763, affine orthogonale sans cisaillement) ; le seuil final correspondant est 0,15 cm, tandis que les contrôles de bornes AABB restent à 0,1 cm. Bounds UE validées (cm) : min `(-2287.021, -5346.584, -1039.593)`, max `(-629.743, -2591.222, 1478.870)`.

Contrôle Unreal/MCP live : map chargée, 1 944 acteurs trouvés sous `Atlas/Root_003`; les requêtes d’extrêmes et leurs bounds concordent avec les bounds staging (écart d’arrondi inférieur à 0,001 cm). Les acteurs échantillonnés conservent leurs tags d’instance/LOD et leurs transforms, un mesh utilise une MI parentée au master opaque partagé, et un mesh non pris en charge référence le fallback magenta. Le focus/capture global montre la forme architecturale et les plateformes ; les grandes surfaces magenta de fallback et l’absence d’éclairage de scène rendent le rendu visuel seulement partiel. Aucun éclairage source n’a été créé.

Le staging a produit 722 fichiers (721 `.uasset`, 1 `.umap`), 376 985 008 octets ; les 722 SHA-256 ont été comparés après copie, sans remplacement du master partagé. Les sorties live sont limitées à `MyProject/Content/Atlas/Sectors/Root_003`. Le [rapport de validation JSON](labyrinth-root3-validation.json) et son [journal texte détaillé](labyrinth-root3-validation.txt) conservent la sélection exacte, les compteurs, les IDs, les transforms, les anomalies et les mesures reproductibles.
