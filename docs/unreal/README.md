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
- décodeur strict de `instances.bin` et test Automation associé ;
- commandlet `AtlasEftBuildScene` pour créer un niveau de contrôle à partir d'un voisinage d'instances ;
- prototype Labyrinthe validé avec 25 acteurs, 18 meshes uniques, 4 831 triangles et `0.000000 cm` d'erreur maximale de translation.

Le projet minimal `unreal/AtlasEftImporterHost` permet d'exécuter les commandlets et les tests. Le script `unreal/Scripts/BuildAndTest.ps1` automatise le build du plugin, l'audit d'un vrai pack, la création d'un asset échantillon et la suite Automation.

Sur le poste de développement actuel, la stratégie Windows de contrôle d'applications bloque l'exécution de DLL non signées depuis `D:` (`GetLastError=4551`). Le script package donc les binaires dans un répertoire de staging sous `%LOCALAPPDATA%` sur `C:` ; les sources et tous les changements versionnés restent dans le fork sur `D:`.

Exemple :

```powershell
& .\unreal\Scripts\BuildAndTest.ps1 `
  -PackPath 'D:\EXTRAtor tarkov to UE\atlas-0.1.0-3dce665-win64-full\packs\labyrinth.eftpack'
```

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

La prochaine étape est de rendre l'éclairage de la map de contrôle reproductible pour conclure sur l'orientation de la normal map et la réponse de roughness, puis de tester un second matériau opaque. Viendra ensuite une scène structurelle cohérente par `RootId`/niveau. Le choix d'éclairage doit être formalisé avant tout import complet ou génération massive d'UV2.

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
