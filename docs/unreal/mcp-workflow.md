# Workflow MCP Unreal local

Le serveur MCP Unreal local est joignable via `127.0.0.1:8000`. Les appels passent par le connecteur `mcp__unreal_mcp__` et utilisent le protocole MCP `2025-06-18`.

## Règle de chemins

Deux namespaces ne doivent jamais être confondus :

- `EditorAssetSubsystem` / `AssetTools` attend un chemin d'asset Unreal, par exemple `/Game/Atlas/Meshes/0000_0255/SM_Atlas_0000_stone1` ;
- un chemin Windows comme `D:/EXTRAtor tarkov to UE/.../HostProject.uproject` est un chemin de fichier, pas un asset Unreal.

Passer un chemin Windows à `AssetTools.exists` force Unreal à tenter une conversion en package path. Les espaces deviennent alors invalides et produisent le message trompeur `because there are too many spaces`. Ce n'est ni une limite de longueur, ni un défaut du chemin d'installation, ni une raison de déplacer le dépôt.

Le MCP actuellement exposé n'offre pas de getter filesystem/projet pour le chemin du `.uproject`. Il ne faut donc pas tenter de le deviner puis de le tester avec `AssetTools`. Pour confirmer le contexte :

1. utiliser `SceneTools.get_current_level` pour le level courant ;
2. utiliser `EditorAppToolset.IsPIERunning` pour l'état PIE ;
3. utiliser `PluginToolset.IsEnabled` et `PluginToolset.GetPluginInfo` pour le plugin ;
4. réserver `AssetTools` aux chemins `/Game`, `/Engine` ou aux autres mount points Unreal valides.

Exception explicite : `TextureTools.import_file(folder_path, asset_name, source_file)` combine deux namespaces. `folder_path` doit être un dossier Unreal tel que `/Game/Atlas/Textures/McpSamples`, tandis que `source_file` attend réellement un chemin de fichier Windows vers le PNG source. Il est donc correct de fournir `D:/.../texture.png` uniquement dans ce paramètre documenté.

## État antérieur vérifié le 2026-09-19

- éditeur : `AtlasEftImporterHost` ;
- level : `/Temp/Untitled_1` (non sauvegardé) ;
- PIE : arrêté ;
- plugin `AtlasEftImporter` : activé ;
- base du plugin : `D:/EXTRAtor tarkov to UE/atlas-unreal/unreal/BuildPluginPackage` ;
- mount point : `/AtlasEftImporter/` ;
- monde World Partition avec Landscape, proxies, HLOD et `WorldDataLayers`.

Après remplacement de la requête incorrecte, les logs contiennent uniquement les quatre anciennes erreurs émises à 13:25:43 et 13:25:46. Les requêtes sûres effectuées ensuite n'ont généré aucune nouvelle erreur de conversion de chemin.

Un test d'import live a ensuite créé trois textures sous `/Game/Atlas/Textures/McpSamples`. `find_assets` et `TextureTools.get_size` ont confirmé les trois assets et leurs dimensions. Les propriétés `sRGB`, `compressionSettings` et `bFlipGreenChannel` peuvent être lues et écrites avec `ObjectTools`. `save_assets` et les logs de package ont confirmé les sauvegardes, même si `AssetTools.is_dirty` est resté à `true` immédiatement après ; ne pas boucler sur la sauvegarde pour ce seul indicateur.

## Validation MCP du niveau architectural

Le niveau courant est `/Game/Atlas/ArchitectureTest/L_Atlas_Architecture_Seed000000_Count025`. `SceneTools.find_actors` avec `actor_type=/Script/Engine.StaticMeshActor` renvoie exactement 25 acteurs, tous étiquetés `Atlas_I…_M…`. La recherche sans classe retourne aussi les acteurs système UE ; il faut donc filtrer par classe avant de compter.

Les transforms et bounds monde ci-dessous proviennent de `ActorTools.get_actor_transform` et `ActorTools.get_actor_bounds`. Les unités sont des centimètres ; nombres arrondis à 0,01 cm. Le seed concorde avec la référence attendue `(2591.4, -2457.4, 4.9)` cm. Les trois autres acteurs ont été choisis parmi les 24 par dispersion spatiale XY.

| Acteur | Translation XYZ | Rotation P/Y/R (°) | Échelle XYZ | Bounds monde min → max (XYZ) |
|---|---|---|---|---|
| `Atlas_I000000_M0000` | `(2591.39, -2457.35, 4.90)` | `(90.00, 9.17, 0.00)` | `(0.64919, 0.64919, 0.64919)` | `(2408.51, -2646.41, 0.20)` → `(2787.58, -2265.03, 10.05)` |
| `Atlas_I007165_M0297` | `(2551.39, -2285.53, 4.22)` | `(90.00, -139.07, 0.00)` | `(1.00, 1.00, 1.00)` | `(2500.83, -2338.76, 4.28)` → `(2601.50, -2237.45, 10.19)` |
| `Atlas_I020868_M1813` | `(2532.36, -2621.03, 12.86)` | `(90.00, -90.00, 0.00)` | `(1.00, 1.00, 1.00)` | `(2471.38, -2642.52, 6.84)` → `(2592.05, -2578.93, 18.89)` |
| `Atlas_I011455_M0562` | `(2433.30, -2500.70, 4.20)` | `(90.00, 85.38, 0.00)` | `(1.00, 0.80, 0.80)` | `(2359.97, -2575.39, 4.40)` → `(2511.17, -2424.05, 30.86)` |

`EditorAppToolset.FocusOnActors` a cadré les 25 acteurs et `CaptureViewport` a renvoyé une capture valide à 90° de FOV depuis `(1982.52, -2610.36, 256.75)` cm, rotation `(-20.80, 11.20, 0.00)°`. La scène présente la géométrie grise sans matériaux ; c'est l'état attendu avant le jalon matériau. La prochaine validation porte sur un master opaque, une material instance du matériau Atlas 17, le mesh 20 et le rendu albedo/normale/specular/UV.

## Validation MCP d'une verticale matériau

La génération reste la responsabilité du commandlet reproductible, pas du MCP. Le cycle de validation est :

1. exécuter `AtlasEftBuildMaterialSample` dans le staging headless ;
2. copier seulement le master et le dossier de test générés vers `Content/Atlas` du projet live ;
3. charger `/Game/Atlas/MaterialTest/L_Atlas_Material_0017_Mesh0020` avec `SceneTools` ;
4. retrouver `Atlas_Material17_Mesh20`, vérifier ses références mesh/material si les getters les exposent ;
5. cadrer l'acteur et capturer le viewport ;
6. ne jamais corriger le graphe ou l'affectation manuellement dans l'éditeur : toute correction revient dans le C++ puis repasse par le pipeline.

Pour le matériau 17, les chemins attendus sont le master `/Game/Atlas/Common/Materials/M_AtlasOpaque`, la MI `/Game/Atlas/MaterialTest/Materials/MI_Atlas_0017` et le mesh `/Game/Atlas/MaterialTest/Meshes/SM_Atlas_0020_model`. Une texture `specMap` présente dans la MI est une référence de provenance inactive ; elle ne doit pas être interprétée comme une roughness ni branchée au shader de production.

Contrôle live du 2026-09-19 : le niveau se charge, l'acteur `Atlas_Material17_Mesh20` est présent et son composant pointe vers le mesh attendu avec `MI_Atlas_0017` en override. Le slot `AtlasMaterial_17`, 164 vertices et 180 triangles ont été confirmés. La MI référence le bon parent, les textures albedo/normal/provenance et les scalaires attendus ; `DebugUseSpecularProvenance` vaut `false`.

`FocusOnActors` n'a pas déplacé la caméra pour ce très petit mesh centré à l'origine. La capture de validation doit donc passer explicitement `captureTransform`, ici position `(90, 0, 25)` cm, rotation `(-12, 180, 0)` degrés et FOV 90°. Le clavier et son albedo sont visibles, mais l'ambiance très sombre ne permet pas encore une appréciation fiable de la normale ou de la roughness.
