# Spécification — importeur direct Atlas `.eftpack` vers Unreal Engine 5.8

Statut : proposition d'architecture avant implémentation
Version : 2026-09-19

## 1. But

Importer un pack Atlas auto-contenu dans un projet Unreal Engine 5.8 et produire :

- des textures UE avec les bons espaces couleur et usages ;
- des Material Instances reposant sur un petit jeu de master materials ;
- des Static Meshes avec sections, normales, UV, vertex colors et LOD utiles ;
- une scène éditable et streamable, avec placement fidèle ;
- la collision physique indépendante de la géométrie visible ;
- les lumières, décals déjà bakés, eau et données de gameplay disponibles ;
- des métadonnées de provenance permettant audit et reimport.

Le chemin de production est direct. Aucun export FBX/glTF/USD, aucune ouverture Unity et aucun passage Blender ne sont requis.

## 2. Choix d'architecture

### 2.1 Plugin UE

Créer plus tard un plugin C++ composé de deux modules :

```text
AtlasEftRuntime   données/assets/composants nécessaires dans un build UE
AtlasEftImporter  module Editor-only : lecture du pack, création et reimport
```

Le plugin vivra d'abord dans `unreal/AtlasEftImporter/` de ce dépôt, afin d'être installable ensuite dans `<ProjetUE>/Plugins/AtlasEftImporter`.

### 2.2 Interchange, mais en mode hybride

UE 5.8 présente Interchange comme son framework extensible, asynchrone et réimportable. Il est pertinent pour les payloads mesh/texture et le suivi d'import. Cependant un `.eftpack` est un dossier, et la création coordonnée d'un niveau, des HISM, des collisions indépendantes, des Data Layers et des métadonnées World Partition dépasse un import d'asset standard.

Décision :

- un `UAtlasImportSubsystem` fournit la commande de haut niveau « Import Atlas Pack » et choisit le dossier ;
- `manifest.json` est la source logique et son parent doit finir par `.eftpack` ;
- une couche Interchange personnalisée peut fournir les payloads mesh/texture et le reimport ;
- l'assemblage de scène reste un post-process contrôlé par le subsystem ;
- aucun convertisseur intermédiaire n'est écrit sur disque.

Si l'API Interchange gêne les matrices résiduelles ou les Static Meshes multi-sections pendant le premier POC, le builder direct UE sera utilisé derrière la même interface. Le format interne du plugin ne dépendra donc pas d'Interchange.

### 2.3 Trois couches strictes

```text
EftPackReader       lecture/validation sans UObject ni accès Editor
AtlasImportModel    représentation normalisée, conversions et plan d'import
AtlasUeBuilder      création transactionnelle des assets et de la scène
```

Cette séparation rend le lecteur testable hors création d'assets et empêche les conventions de coordonnées de se disperser dans le code.

## 3. Contrat d'entrée

Entrée primaire : dossier `<map>.eftpack` contenant au minimum :

- `manifest.json` ;
- `meshes.bin` ;
- `instances.bin` ;
- `materials.json`.

Entrées optionnelles : colliders, collider meshes, lights, gamedata, terrain layers, grass, volume SH et navigation.

Règles :

1. Refuser un `manifest.version` inconnu, mais accepter les attributs/champs supplémentaires connus par leur nom.
2. Lire strides, offsets et formats depuis le manifest ; ne jamais figer 36/80/96 comme seule possibilité.
3. Vérifier chaque range avant lecture et chaque index de mesh/material/root.
4. Exiger `len(instances.bin) % stride == 0` et la concordance avec `instanceCount`.
5. Vérifier les fichiers et références de textures avant la phase de mutation UE.
6. Construire un rapport d'audit et un plan d'import immuable avant de créer le premier asset.

## 4. Coordonnées et unités

Le pack Atlas est right-handed, Y-up, en mètres. La conversion cible choisie restitue la convention Unity→UE habituelle :

```text
pUE_cm = 100 * C * pAtlas

C = [ 0  0  1 ]      Xue =  Zatlas
    [-1  0  0 ]      Yue = -Xatlas
    [ 0  1  0 ]      Zue =  Yatlas
```

`det(C) = -1`. Pour une affine Atlas `p' = L p + t` :

```text
Lue = C * L * inverse(C)
tue = 100 * C * t
```

Application :

- convertir les vertices locaux par `100*C` ;
- convertir les normales locales par `C`, puis normaliser ;
- inverser l'ordre des indices une fois pour compenser `det(C) < 0` ;
- convertir l'affine d'instance par conjugaison, jamais par transpose improvisée ;
- ne jamais réappliquer le `G = diag(-1,1,1)` Unity→Atlas : il est déjà baké dans le pack.

Tests obligatoires : triangle orienté, repère XYZ, texte asymétrique et comparaison des huit coins d'AABB avant/après conversion.

## 5. Affines, shear et miroir

`FTransform` et les transforms HISM ne représentent pas une matrice 3×3 arbitraire avec shear. Une décomposition TRS silencieuse est interdite.

Classification compatible Atlas :

1. Calculer les longueurs des trois colonnes de `Lue`.
2. Rang dégénéré si `lmax <= 1e-12` ou `lmin <= lmax*1e-6`.
3. Normaliser les colonnes et calculer `max(abs(dot(ci,cj)))`.
4. Shear si cette valeur dépasse `0.02`.
5. Mirror si `det(Lue) < 0`; vérifier la cohérence avec le flag Atlas.

Stratégie exacte :

- TRS orthogonal, déterminant positif : utiliser un `FTransform` normal ;
- mirror : placer la réflexion dans une variante de mesh, corriger winding/normales, garder un transform d'acteur à déterminant positif ;
- shear : factoriser `Lue = A * B`, où `A` est représentable par `FTransform` et `B` est le résidu ; baker `B` dans une variante de mesh ;
- rang dégénéré : baker la matrice complète dans la géométrie avec pseudo-inverse pour les normales ;
- dédupliquer les variantes par `(meshId, bits canoniques du résidu B, options géométriques)`.

Pour chaque bake résiduel :

```text
position' = B * position
normal'   = normalize(inverse(B)^T * normal)
```

Si `det(B) < 0`, inverser le winding. Les tangentes sont recalculées après le bake.

Le mode debug doit pouvoir matérialiser chaque catégorie dans une Data Layer distincte : normal, shear, mirror, baked-world et invalid.

## 6. Static Meshes

Pour chaque entrée `manifest.meshes[]` :

- lire le vertex block à `vtxOffset`, avec le stride déclaré ;
- lire les indices `u32` à `idxOffset` ;
- conserver les sections `submeshes[]` et leur `materialId` ;
- importer les normales fournies ;
- conserver UV0 et COLOR_0 ;
- générer les tangentes MikkTSpace, car le pack v1 n'en contient pas ;
- ne pas générer UV1 par défaut lorsque Lumen est la cible ; proposer une option lightmap UV ;
- attacher un `UAtlasAssetImportData` avec map, meshId, fingerprint source et hash des réglages.

Réglages initiaux :

- recompute normals : non ;
- recompute tangents : oui ;
- MikkTSpace : oui ;
- collision du render mesh : désactivée, sauf profil simplifié explicitement demandé ;
- Nanite : activé seulement si toutes les sections sont compatibles et si le gain est réel ; désactivé pour blend/decal/glass/water par défaut.

Le pack a déjà dupliqué les vertices par submesh. Une correction géométrique limitée à une section — par exemple le petit lift des overlays — ne doit donc pas contaminer les sections opaques voisines.

## 7. LOD

Deux profils seront exposés :

### `Parity`

- conserver les relations `lodGroup/lodIndex` ;
- regrouper les renderers d'un même Unity LODGroup ;
- respecter les seuils `srh` autant que possible ;
- ne jamais afficher plusieurs niveaux simultanément hors transition voulue.

Un Unity LODGroup peut contenir plusieurs renderers par niveau. Il ne peut donc pas toujours devenir une simple chaîne de LOD d'un seul `UStaticMesh`.

### `UE5Optimized` — profil recommandé après validation

- garder hors-LOD + LOD0 ;
- activer Nanite sur les meshes compatibles ;
- utiliser les LOD extraits pour les catégories non-Nanite si nécessaire ;
- générer HLOD par World Partition.

Le premier POC Labyrinthe peut utiliser `UE5Optimized`, mais les métadonnées LOD doivent déjà être préservées.

## 8. Textures

Résoudre les chemins relativement au pack lorsqu'ils ne sont pas absolus. Dédupliquer par identité source puis hash de contenu.

| Usage | sRGB | Compression/traitement UE |
|---|---:|---|
| albedo | oui | couleur avec alpha conservé |
| emissive | oui | couleur avec alpha si présent |
| normal | non | Normal Map/BC5, aucun second flip vert |
| vp heights/control | non | Masks/Data, éviter une compression qui déforme les poids |
| parallax height | non | Data |

Sampler par défaut : Repeat/Wrap, mipmaps activés. Les UV bakés sortent souvent de `[0,1]`.

L'import est idempotent : une texture inchangée ne doit pas être recréée ni recompressée à chaque reimport.

## 9. Matériaux

Jeu de masters prévu :

```text
M_AtlasOpaque
M_AtlasCutout
M_AtlasBlend
M_AtlasDecalGeometry
M_AtlasGlass
M_AtlasWater
M_AtlasVertPaint
```

Des static switches couvrent RFA, emissive, detail, parallax, glassTRS et soft-cutout sans créer un master différent pour chaque matériau.

Règles :

- `tint.rgb` est déjà linéaire ; ne pas le convertir une seconde fois ;
- `uvXform` est informatif pour la base ; ne pas le multiplier à nouveau ;
- `roughnessFromAlbedoAlpha` calcule `clamp(1-alpha, 0.06, 1)` ;
- utiliser le roughness scalaire déjà calculé ; ne pas échantillonner `specMap` comme gloss ;
- MASK utilise `alphaCutoff` sur `texture alpha * tint alpha` ;
- préserver `doubleSided` pour la parité ; proposer plus tard un audit d'optimisation ;
- vertex paint utilise COLOR_0 et les trois layers du bloc `vp` ;
- detail maps sont rebasées relativement au base ST, conformément à la documentation Atlas ;
- parallax est optionnel et doit pouvoir être désactivé globalement ;
- glassTRS est une approximation dédiée, pas un simple matériau translucide générique ;
- les décals géométriques reçoivent un petit offset configurable, initialement `0.6 cm`, ou une stratégie UE équivalente validée sans z-fighting.

Pour Labyrinthe, le nombre de Material Instances attendu est 1 523 avant une éventuelle déduplication UE.

## 10. Assemblage de scène et structure

Arborescence Content Browser proposée :

```text
/Game/Atlas/Labyrinth/
  Data/
  Maps/
  Materials/Instances/
  Meshes/<bucket>/
  Textures/Color/
  Textures/Normal/
  Textures/Data/
```

Les masters communs vivent dans le contenu du plugin ou `/Game/Atlas/Common`.

Structure d'acteurs :

- Data Layers : Geometry, Decals, Water, Collision, Lights, Gameplay, Debug ;
- regroupement logique par `lv`, `rootId` et cellule spatiale ;
- HISM par cellule pour les instances partageant mesh variant, matériaux et flags ;
- mode `Editable` facultatif créant des StaticMeshActors individuels ;
- ne pas créer un HISM traversant de nombreuses cellules World Partition, ce qui annulerait le streaming ;
- noms stables basés sur les ids Atlas, pas uniquement sur les noms de meshes.

Limite actuelle : `instances.bin` ne contient pas l'identité complète du GameObject. La première version peut reconstruire une structure utilisable par root/niveau/LOD et record index. Une extension Atlas devra ajouter `objectId`, `nameId` et une string table/path table pour un reimport incrémental parfaitement stable.

## 11. Collisions et navigation

Les colliders Atlas sont la source autoritaire.

- box/sphere/capsule : créer des primitives Chaos lorsque l'affine le permet ;
- primitive sheared/degenerate : convertir en géométrie collision exacte ou convexifiée ;
- mesh collider : créer les meshes de collision depuis `collider_meshes.bin`, jamais depuis le render mesh ;
- dédupliquer les collider meshes par id ;
- instancier/chunker spatialement les colliders non-trigger ;
- créer les triggers dans une Data Layer Gameplay séparée ;
- respecter `NAV_IGNORE` et les layer names ;
- offrir une table configurable Unity layer → UE collision profile.

`nav.bin` est une grille Atlas, pas un Recast NavMesh UE. Elle sert à l'audit/debug. La navigation UE doit être rebâtie depuis les collisions importées, avec un NavMeshBoundsVolume couvrant les bounds utiles.

## 12. Lumières, SH et gameplay

### Lumières

Importer Point et Spot dans une Data Layer dédiée : position, direction, couleur, range, angles, état et shadow flag. Les intensités sont unitless côté Atlas et doivent être calibrées avant conversion éventuelle en candelas/lumens UE.

Les lights sans ombre dans la source restent sans ombre. Un profil performance peut désactiver ou fusionner les petites lumières redondantes, mais le profil Parity les conserve.

### Volume SH

Ne pas prétendre convertir `volume.bin` en Volumetric Lightmap UE : il n'existe pas d'équivalence directe publique et stable.

Ordre recommandé :

1. Lumen + lumières importées pour le premier résultat utilisable ;
2. conserver `volume.json/bin/valid` dans un `UAtlasShVolumeData` ;
3. n'ajouter un chemin de shading SH personnalisé que si les comparaisons prouvent que Lumen ne répond pas au besoin de parité.

### Gameplay

Importer `gamedata.json` d'abord comme Data Asset et visualisation debug. Les portes, exfils, spawns, switches, rooms et portals deviennent des acteurs/composants seulement quand leur usage UE est défini. Cela évite de transformer des données d'audit en gameplay supposé.

## 13. Reimport et transactions

Chaque import écrit un `UAtlasImportManifest` contenant :

- chemin source ;
- `sourceFingerprint` ;
- hash du `manifest.json` et des réglages ;
- mapping ids Atlas → SoftObjectPaths UE ;
- version du plugin ;
- rapport d'erreurs/warnings ;
- liste des assets/actors créés.

Le reimport :

1. audite et construit le plan sans mutation ;
2. importe dans un namespace temporaire ;
3. remplace uniquement après validation ;
4. conserve les assets inchangés ;
5. ne supprime jamais les modifications utilisateur sans option explicite ;
6. fournit un dry-run diff.

Le premier reimport peut reconstruire les acteurs gérés dans une Data Layer Atlas dédiée. L'extension d'identité GameObject permettra ensuite un diff instance par instance.

## 14. Automatisation

Commandes prévues :

```text
Import Atlas Pack…                     UI Editor
Atlas.ImportPack <pack> <destination> console/commandlet
Atlas.AuditPack <pack>                 aucune mutation
Atlas.ValidateImport <manifest asset>  comparaison source/UE
```

Le commandlet doit permettre CI et batch import sans interface graphique. Les étapes longues publient progression et annulation ; le parsing et la préparation des payloads peuvent être parallélisés, la création d'UObjects reste sur le game thread lorsque l'API l'exige.

## 15. Validation

### Tests unitaires sans asset UE

- lecture little-endian et layouts déplacés ;
- ranges tronquées/refus propre ;
- conversion C et échelle 100 ;
- winding global ;
- affine pure TRS, non-uniform scale, shear, mirror et rank-deficient ;
- normal inverse-transpose ;
- déduplication des variantes résiduelles ;
- résolution des textures et material roles.

### Tests d'intégration Labyrinthe

- 2 124 meshes source lus ;
- 21 734 records d'instance reconnus ;
- 738 chemins texture résolus, zéro manquant ;
- 1 523 matériaux générés ou explicitement dédupliqués avec mapping complet ;
- 24 661 colliders reconnus ;
- AABB UE égal à `100*C*bounds` à la tolérance définie ;
- aucun texte/asymétrie miroir ;
- pas de double V-flip ni double tiling ;
- aucune normal map inversée ;
- reimport à source inchangée : zéro changement fonctionnel.

### Comparaison visuelle

Capturer des caméras homologues Atlas/UE :

1. corridor modulaire et texte ;
2. cutout fin/grillage ;
3. vertex-paint ;
4. décals coplanaires ;
5. verre et eau ;
6. zone sombre riche en lumières.

La géométrie/placement doit être validée avant toute tentative de faire correspondre l'éclairage.

## 16. Risques prioritaires

| Priorité | Risque | Réponse |
|---|---|---|
| P0 | shear perdu par `FTransform` | residual mesh variants, jamais TRS naïf |
| P0 | double conversion d'axes/winding | conversion centralisée + tests triangle/texte |
| P0 | UV/normal flip doublé | lire conventions et appliquer exactement une fois |
| P0 | explosion d'actors/assets | HISM chunkés et déduplication stable |
| P0 | reimport destructif | dry-run, namespace temporaire, assets gérés identifiés |
| P1 | tangentes absentes | MikkTSpace puis extension Atlas optionnelle |
| P1 | structure source incomplète | extension objectId/name/path table |
| P1 | transparence/decal sorting | masters spécialisés + tests rapprochés |
| P1 | collision trop lourde | instancing/chunking par type et cellule |
| P1 | lumière UE différente | calibration puis SH custom seulement si nécessaire |
| P1 | POC sans terrain/shear | seconde map de qualification obligatoire |

## 17. Jalons d'implémentation proposés

1. Lecteur/auditeur C++ sans création d'assets.
2. Test synthétique coordonnées, winding et affine résiduelle.
3. Import de quelques meshes/materials sélectionnés.
4. Import Labyrinthe géométrie LOD0 + placement.
5. Textures et masters/material instances.
6. Décals, vertex paint, verre et eau.
7. Collisions et NavMesh UE.
8. Lumières et gamedata debug.
9. HISM, World Partition, Data Layers, Nanite et HLOD.
10. Reimport/dry-run/commandlet.
11. Extension Atlas tangentes + identité de scène si validée.
12. Qualification sur une map extérieure avec terrain, grass et shear.

## 18. Définition de « terminé »

Le pipeline est terminé lorsque :

- une extraction Atlas auto-contenue peut être importée sans Unity ni Blender ;
- l'import est relançable et non destructif ;
- la scène est correctement orientée, à l'échelle et structurée pour UE ;
- les rôles matériaux principaux sont fidèles ;
- les colliders et données annexes ne sont pas perdus silencieusement ;
- Labyrinthe et une map extérieure passent les critères de qualification ;
- toute donnée Atlas non supportée est explicitement rapportée, jamais ignorée sans trace.
