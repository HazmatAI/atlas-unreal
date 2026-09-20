# Atlas → Unreal Engine 5.8

Ce dossier définit le portage direct d'un pack Atlas `.eftpack` vers Unreal Engine 5.8.

La direction retenue est un plugin C++ d'éditeur, piloté depuis Unreal, qui lit directement le format Atlas. Unity, Blender, FBX, glTF et USD ne font pas partie du chemin de production. Blender reste seulement un outil de comparaison facultatif.

## Documents

- [Audit du pack Labyrinthe](labyrinth-audit.md) : contenu réellement disponible, chiffres, couverture et lacunes du POC.
- [Spécification de l'importeur](eftpack-importer-spec.md) : contrat de données, conversions, architecture UE, étapes et critères d'acceptation.
- [Workflow MCP Unreal](mcp-workflow.md) : règles de requête, distinction chemins d'assets/fichiers et état de l'éditeur de test.
- [Vue globale et checklist projet](project-checklist.md) : avancement réel, reste à faire, critères de sortie, chemin critique et risques de sur-ingénierie.

## État du plugin

Le premier jalon est implémenté dans `unreal/AtlasEftImporter` :

- module runtime `AtlasEftRuntime` : lecture et audit manifest-driven du pack ;
- module éditeur `AtlasEftImporter` : subsystem, commande de menu et commandlet headless ;
- lecture streamée de `instances.bin` ;
- validation des meshes, submeshes, matériaux, textures, instances, colliders et lumières ;
- conversion Atlas vers Unreal centralisée et couverte par des tests ;
- décodeur little-endian de `meshes.bin`, manifest-driven et validé par test ;
- création d'un `UStaticMesh` persistant avec positions/axes/centimètres, winding, normales, UV, vertex colors et sections ;
- premier asset réel validé : mesh 0 `stone1`, 4 024 vertices source, 3 690 triangles et 1 section ;
- lecture typée de `materials.json`, couvrant modes alpha, paramètres et références classées par rôle ;
- validation réelle sur Labyrinthe : 1 523 matériaux, 738 textures distinctes et zéro référence manquante ;
- importeur PNG déterministe avec réglages par sémantique et commandlet d'échantillonnage ;
- échantillon du matériau 17 validé : albedo 1024² sRGB, normal 1024² linéaire/Normalmap et specular 512² linéaire/Masks ;
- master opaque déterministe `/Game/Atlas/Common/Materials/M_AtlasOpaque` et material instance `/Game/Atlas/MaterialTest/Materials/MI_Atlas_0017` ;
- roughness du matériau 17 fidèlement reconstruite par `clamp(1 - albedo.a, 0.06, 1)` ; la `specMap` Atlas est conservée comme provenance inactive et n'est pas confondue avec une roughness ;
- map verticale `/Game/Atlas/MaterialTest/L_Atlas_Material_0017_Mesh0020` avec le mesh 20, sa material instance et un éclairage de contrôle ;
- second exemple opaque live : matériau 974 sur mesh 1240, utilisant la même MI parent `M_AtlasOpaque`, roughness `0.9`, metallic `0`, albedo et normal, sans specMap ;
- décodeur strict de `instances.bin` et test Automation associé ;
- commandlet `AtlasEftBuildScene` pour créer un niveau de contrôle à partir d'un voisinage d'instances ;
- prototype Labyrinthe validé avec 25 acteurs, 18 meshes uniques, 4 831 triangles et `0.000000 cm` d'erreur maximale de translation.

Le projet minimal `unreal/AtlasEftImporterHost` reste un host temporaire de build/test. Le projet Unreal live canonique est `D:\EXTRAtor tarkov to UE\unreal project\MyProject\MyProject.uproject`. Le script `unreal/Scripts/BuildAndTest.ps1` automatise le build, l'audit, les imports et les tests. Avec `-LiveProjectPath`, il ne synchronise que les 12 assets de la verticale comparative (master opaque, deux maps, deux MI, deux meshes et cinq textures) et préserve le plugin ainsi que les autres dossiers `Content/Atlas`.

État vérifié le 2026-09-20 : la dernière passe staging complète a compilé Editor Development, Game Development et Game Shipping, puis réussi l'audit du pack, les imports échantillons/batch, la scène architecturale de 25 instances, les maps matériaux 17/20 et 974/1240 et les cinq tests runtime. Cette passe précède l'ajout du commandlet secteur : celui-ci a été compilé séparément en Editor Development et a réussi son import ciblé ainsi que son contrôle Unreal/MCP ; Game Development, Game Shipping et les cinq tests n'ont pas été relancés avec cette extension. Smart App Control ayant été désactivé par l'utilisateur, aucune nouvelle erreur WDAC n'est apparue ; aucune politique de sécurité n'a été modifiée. La validation staging n'utilise pas `-LiveProjectPath`; ce paramètre ne doit être activé qu'après inspection explicite des maps et limites de synchronisation.

Exemple :

```powershell
& .\unreal\Scripts\BuildAndTest.ps1 `
  -PackPath 'D:\EXTRAtor tarkov to UE\atlas-0.1.0-3dce665-win64-full\packs\labyrinth.eftpack' `
  -SampleTextureMaterialId 17 -SampleMeshId 20 `
  -SecondMaterialId 974 -SecondMaterialMeshId 1240
```

Le rig headless des maps matériaux est validé structurellement : une Directional Light `Movable` (intensité 5, ombres désactivées), une Rect Light `Movable` (20 Candelas), Post Process manuel EV100 0, éclairage précalculé désactivé, backdrop neutre basé sur des assets Engine et caméra déterminée depuis les bounds du mesh. Cette validation ne remplace pas l'inspection visuelle des maps générées.

Commandlet d'import d'un mesh :

```powershell
UnrealEditor-Cmd.exe <projet.uproject> -run=AtlasEftImportMesh `
  -Pack='<map.eftpack>' -MeshId=0 -Destination=/Game/Atlas/Samples
```

Commandlet batch, avec dossiers shardés par blocs de 256 ids :

```powershell
UnrealEditor-Cmd.exe <projet.uproject> -run=AtlasEftImportMeshes `
  -Pack='<map.eftpack>' -FirstMeshId=0 -Count=100 `
  -Destination=/Game/Atlas/Meshes
```

Remplacer `-Count=100` par `-All` pour importer tous les meshes à partir de `FirstMeshId`. Le manifeste n'est lu qu'une fois par batch, les ids déterminent des chemins stables, et le commandlet échoue immédiatement au premier asset invalide.

Le niveau de contrôle architectural a été inspecté dans l'éditeur via MCP : la carte `/Game/Atlas/ArchitectureTest/L_Atlas_Architecture_Seed000000_Count025` contient exactement 25 acteurs `StaticMeshActor` Atlas. Le transform du seed 0 correspond à `(2591.4, -2457.4, 4.9)` cm à l'arrondi près ; trois autres acteurs répartis ont aussi des transforms finis et des bounds monde valides.

La verticale matériau est également automatisée. `AtlasEftBuildMaterialSample` reconstruit le matériau Atlas 17 sur le mesh 20, vérifie les paramètres et références, affecte la MI au slot du mesh puis sauvegarde une map dédiée sans toucher à la carte architecturale. Le `specMap` du pack n'est pas traité comme une roughness : Atlas calcule ici la roughness depuis l'alpha de l'albedo et garde cette texture seulement comme provenance.

La validation MCP live confirme l'acteur, son mesh, son override de matériau, le parent et tous les paramètres de la MI. Une capture déterministe depuis `(90, 0, 25)` cm montre le clavier texturé avec un albedo et des UV plausibles. Après ajout manuel d'un spotlight et `Build Lighting`, Unreal signale cependant `1.6%` de chevauchement sur les UV de lightmap du mesh 20. Le pack Atlas v1 ne contient pas d'UV secondaire : cet avertissement connu n'invalide pas l'UV0 matériau et ne bloque pas le pipeline actuel. Il ne sera pas corrigé isolément avant la décision Lumen/dynamique versus lightmaps statiques.

La scène live 974/1240 a été inspectée en lecture seule via MCP après des corrections manuelles : une Directional Light, une Rect Light, une caméra recadrée et un fond clair sont présents. Le cadrage de la scène live est meilleur, mais l'image reste sombre et ne permet pas de conclure sur la réponse de normale ou de roughness. Cette scène n'a pas été enregistrée ni remplacée. Le rig headless corrigé a ensuite passé ses assertions structurelles et tout le pipeline staging ; l'aspect visuel du rendu généré reste à examiner séparément avant toute synchronisation.

Commandlet de test architectural reproductible :

```powershell
UnrealEditor-Cmd.exe <projet.uproject> -run=AtlasEftBuildScene `
  -Pack='<map.eftpack>' -SeedInstance=0 -Count=25 `
  -Destination=/Game/Atlas/ArchitectureTest
```

Commandlet de verticale matériau reproductible :

```powershell
UnrealEditor-Cmd.exe <projet.uproject> -run=AtlasEftBuildMaterialSample `
  -Pack='<map.eftpack>' -MaterialId=17 -MeshId=20 `
  -SecondMaterialId=974 -SecondMeshId=1240 `
  -Destination=/Game/Atlas/MaterialTest
```

## Sources de référence

- [Documentation d'extraction Atlas](../extraction/README.md)
- [Format du pack](../extraction/build-pipeline-and-pack-format.md)
- [Géométrie et placement](../extraction/geometry-and-placement.md)
- [Textures et matériaux](../extraction/textures-and-materials.md)
- [Colliders et sémantique](../extraction/colliders-interactables-and-semantics.md)
- [Décals](../extraction/decals.md)
- [Éclairage SH](../extraction/lighting-and-sh-bake.md)
- [Interchange Framework — Unreal Engine 5.8](https://dev.epicgames.com/documentation/unreal-engine/interchange-framework-in-unreal-engine)
- [World Partition](https://dev.epicgames.com/documentation/unreal-engine/world-partition-in-unreal-engine)
- [World Partition HLOD](https://dev.epicgames.com/documentation/unreal-engine/world-partition---hierarchical-level-of-detail-in-unreal-engine)

## Secteur pilote par parenté

Le commandlet `AtlasEftBuildSector` importe un groupe complet, sans sélection spatiale ni troncature. Le pilote actuel est décrit dans [l'audit des racines](labyrinth-root-audit.md). Il utilise les meshes 750/784/860/927, 9 MI sur le master opaque partagé et 13 textures. Le secteur live est ouvert dans MyProject.

```powershell
python .\unreal\Scripts\AuditSector.py --pack '<labyrinth.eftpack>' --output '.\docs\unreal'
UnrealEditor-Cmd.exe <host.uproject> -run=AtlasEftBuildSector `
  -Pack='<labyrinth.eftpack>' -RootId=3 -Level=547 -GrandparentId=9787 -ExpectedCount=53 `
  -Destination=/Game/Atlas/Labyrinth/Pilot/R03_L547_G9787 `
  -DisablePlugins=ModelContextProtocol -unattended -nop4 -NullRHI -NoSplash
```

Compiler uniquement Editor avec `RunUAT.bat BuildPlugin -Plugin=<source.uplugin> -Package=<nouveau-dossier-de-build> -NoTargetPlatforms -NoDeleteHostProject -Rocket`. Utiliser le `HostProject/HostProject.uproject` produit, sans lui copier une règle AssetManager GameFeatures provenant d'un autre host. Le host ne sert qu'à la génération ; le projet Unreal live reste MyProject. Si le master live est réutilisé, copier d'abord son asset inchangé à la même adresse sous le Content du host. À défaut, le commandlet sait créer l'unique master commun. Pour un contrôle live, copier uniquement le nouveau dossier de secteur après vérification de toutes les sorties ; refuser une destination déjà existante et comparer le master par SHA256. Ne pas remplacer le plugin chargé ni les maps live antérieures.

Résultat : code 0, 53 acteurs, 4 meshes, 9 MI, 13 textures, 121 slots instanciés, erreurs de translation et des huit coins nulles. Le compte incorrect 54 est refusé avant toute création. Le rapport est `Saved/AtlasSectorValidation.txt`. Le dossier Outliner contient root/niveau/ancêtre/parent, avec transforms monde conservés. Le fallback magenta est implémenté mais non exercé par ce secteur entièrement opaque supporté. La capture MCP montre les escaliers/paliers, avec un éclairage sombre : validation structurelle acquise, matériau visuel partiel. LOD, collisions et lumières source restent à faire.

La compilation et l'exécution de cette session utilisent `C:\Users\Pc\Documents\ChatGPT\Aether\sector_build\HostProject`. Le plugin live reste à sa version précédente ; l'installation de la nouvelle version exigerait un redémarrage distinct et n'est pas nécessaire pour ouvrir les assets générés.

## RootId 3 complet — sélection active LOD0

Le mode RootId complet omet `-Level` et `-GrandparentId` et requiert toujours `-ExpectedCount`. La map stable est `/Game/Atlas/Sectors/Root_003/L_Atlas_Root003_Area03`; elle contient un acteur par instance, sélectionnés par `RootId=3`, actif, `LodIndex <= 0`, sans troncature spatiale. Le résultat validé est `2 159` records bruts moins `215` LOD supérieurs, soit `1 944` acteurs LOD0 (285 meshes). Les 215 variantes LOD ne sont pas une perte d'instances.

```powershell
UnrealEditor-Cmd.exe <host.uproject> -run=AtlasEftBuildSector `
  -Pack='<labyrinth.eftpack>' -RootId=3 -ExpectedCount=1944 `
  -Destination=/Game/Atlas/Sectors/Root_003 -DisablePlugins=ModelContextProtocol `
  -unattended -nop4 -NullRHI -NoSplash
```

Le staging Root3 a réussi ses assertions (IDs, transforms/corners/bounds, meshes, 2 150 slots, références et affectations), puis seuls les 722 fichiers de `Content/Atlas/Sectors/Root_003` ont été synchronisés au projet live avec comparaison SHA-256. Le master partagé et les autres contenus Atlas ne sont pas remplacés. Résultats détaillés dans l’[audit des racines](labyrinth-root-audit.md), le [rapport JSON](labyrinth-root3-validation.json) et le [journal détaillé](labyrinth-root3-validation.txt). La map est inspectée par MCP ; sa lecture visuelle globale reste partielle avec les matériaux fallback magenta et sans ajout d’éclairage.
