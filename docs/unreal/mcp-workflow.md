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

## Projet canonique depuis le 2026-09-20

- projet live : `D:\EXTRAtor tarkov to UE\unreal project\MyProject\MyProject.uproject` ;
- configuration Codex/MCP : `MyProject\.codex\config.toml` ;
- endpoint : `http://127.0.0.1:8000/mcp` ;
- plugin Atlas installé : `MyProject\Plugins\AtlasEftImporter` ;
- sorties live : `MyProject\Content\Atlas`.

Le host `unreal/AtlasEftImporterHost` et `%LOCALAPPDATA%\AtlasEftImporterDev` sont uniquement des zones reproductibles de build/test headless. Ils ne sont pas le projet live. Lorsqu'un audit headless vise MyProject alors que son éditeur MCP est ouvert, passer `-DisablePlugins=ModelContextProtocol` au second processus pour éviter une concurrence sur le port 8000. L'option `-LiveProjectPath` de `BuildAndTest.ps1` synchronise uniquement les 12 assets matériaux validés (master, maps 17/20 et 974/1240, leurs MI, meshes et textures) ; elle préserve le plugin live ainsi que les assets ArchitectureTest, batch et sample.

Après installation ou remplacement du plugin Atlas, redémarrer une fois l'éditeur MyProject pour que l'instance live charge ses binaires. Ne jamais rediriger MCP vers le host temporaire.

## État historique vérifié le 2026-09-19

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

La génération reste la responsabilité du commandlet reproductible, pas du MCP. Le cycle de validation, en préservant les cartes live éditées manuellement, est :

1. exécuter `AtlasEftBuildMaterialSample` dans le staging headless ;
2. inspecter les assets/maps générés dans le staging ; ne pas synchroniser les maps live avant cette inspection et une autorisation explicite ;
3. si une vérification live est nécessaire, utiliser une copie/map distincte ou importer uniquement de nouveaux assets non conflictuels ; ne jamais remplacer une map live manuellement éditée ;
4. charger la map explicitement choisie avec `SceneTools` ;
5. retrouver l'acteur, vérifier ses références mesh/material si les getters les exposent ;
6. cadrer et capturer sans sauvegarder les scènes live comportant des corrections utilisateur ;
7. ne jamais corriger le graphe ou l'affectation manuellement dans l'éditeur : toute correction reproductible revient dans le C++ puis repasse par le pipeline.

Pour le matériau 17, les chemins attendus sont le master `/Game/Atlas/Common/Materials/M_AtlasOpaque`, la MI `/Game/Atlas/MaterialTest/Materials/MI_Atlas_0017` et le mesh `/Game/Atlas/MaterialTest/Meshes/SM_Atlas_0020_model`. Une texture `specMap` présente dans la MI est une référence de provenance inactive ; elle ne doit pas être interprétée comme une roughness ni branchée au shader de production.

Contrôle live du 2026-09-19 : le niveau se charge, l'acteur `Atlas_Material17_Mesh20` est présent et son composant pointe vers le mesh attendu avec `MI_Atlas_0017` en override. Le slot `AtlasMaterial_17`, 164 vertices et 180 triangles ont été confirmés. La MI référence le bon parent, les textures albedo/normal/provenance et les scalaires attendus ; `DebugUseSpecularProvenance` vaut `false`.

`FocusOnActors` n'a pas déplacé la caméra pour ce très petit mesh centré à l'origine. La capture de validation doit donc passer explicitement `captureTransform`, ici position `(90, 0, 25)` cm, rotation `(-12, 180, 0)` degrés et FOV 90°. Le clavier et son albedo sont visibles, mais l'ambiance très sombre ne permet pas encore une appréciation fiable de la normale ou de la roughness.

Contrôle MCP complémentaire du 2026-09-20 : la map `/Game/Atlas/MaterialTest/L_Atlas_Material_0974_Mesh1240` s'ouvre et contient `Atlas_Material974_Mesh1240`, relié à `SM_Atlas_1240_collector_Section_03_LOD0` et `MI_Atlas_0974`. Cette MI utilise le master partagé `M_AtlasOpaque`, roughness `0.9`, metallic `0`, sélecteur alpha `0`, normal scale `1`, textures albedo/normal et aucune surcharge `SpecularProvenanceTexture`. La recherche du Content live retourne un seul master `M_AtlasOpaque`.

#### Inspection de la scène live après corrections manuelles — 2026-09-20

Lecture MCP seule de `/Game/Atlas/MaterialTest/L_Atlas_Material_0974_Mesh1240` : un acteur `Atlas_Material974_Mesh1240` utilise le mesh 1240 et `MI_Atlas_0974`; la MI a pour parent l'unique master opaque `M_AtlasOpaque`, roughness `0.9`, metallic `0`, normal scale `1`, albedo/normal et aucune surcharge specMap. Une seule Directional Light reste dans le niveau (`Movable`, intensité 5, ombres désactivées) et une Rect Light (`Movable`, 20 Candelas). Le PP reste manuel `AEM_Manual`, bias 0, exposition physique désactivée, `bForceNoPrecomputedLighting=true`. La caméra vise près des bounds du mesh; le backdrop est `SM_ChamferCube` avec `MI_DefaultColorway`, teinte pâle/gris clair plutôt que blanc pur. Aucun changement n'a été sauvegardé. Un cadrage MCP temporaire montre mieux l'objet, mais l'image demeure sombre : albedo perceptible, réponse normal/roughness non concluante.

Le pipeline de staging du 2026-09-20 a depuis réussi avec le pack Labyrinthe, les paires 17/20 et 974/1240, trois configurations compilées et cinq tests runtime. Le générateur headless valide structurellement son propre rig (1 Directional, 1 Rect, PP manuel, backdrop Engine, caméra ciblant les bounds); ses maps n'ont pas été inspectées visuellement. Elles n'ont pas été copiées dans MyProject et ne doivent pas remplacer les cartes live corrigées manuellement.

## Validation MCP du RootId 3 complet — 2026-09-20

Après un import staging PASS, seule la nouvelle map `/Game/Atlas/Sectors/Root_003/L_Atlas_Root003_Area03` a été copiée au projet canonique. `SceneTools.get_actors_in_folder("Atlas/Root_003", recursive=true)` retourne exactement 1 944 acteurs. Le niveau pilote était propre (non dirty) avant le chargement ; aucun asset-editor n'était ouvert. La racine live était absente avant la synchronisation. Les 722 fichiers du dossier Root_003 ont tous le même SHA-256 que les fichiers de staging ; le master partagé n’a pas été copié.

`ActorTools.get_actor_transform`, `get_actor_bounds`, `get_tags` et `get_components` ont contrôlé l’acteur 12679/mesh668 (LOD0, MI supportée) et l’acteur 12751/mesh669 (slot 648 fallback magenta). La MI `MI_Atlas_0002` est un `MaterialInstanceConstant` parenté à `/Game/Atlas/Common/Materials/M_AtlasOpaque`, avec Metallic 0, Roughness 0.5, albedo et normal sous Root_003. Le slot `AtlasMaterial_648` du mesh 669 référence `/Game/Atlas/Sectors/Root_003/Materials/M_AtlasUnsupported_Magenta`. Les références des samples et dépendances textures sont présentes ; l’import automatique rapporte zéro référence manquante.

`AssetTools.find_assets` indexe les 722 assets du dossier Root_003 après chargement live. Les dépendances directes de la map incluent ses 285 meshes Root_003 et les références système UE attendues ; matériaux et textures sont référencés par les meshes et MIs correspondants.

Les bounds UE ont été contrôlées au staging et revérifiées via MCP en recherchant les acteurs aux six extrêmes, puis en lisant leurs bounds monde : min `(-2287.021107, -5346.583519, -1039.593029)`, max `(-629.742658, -2591.221527, 1478.869609)` cm. Cela concorde avec le rapport d’import arrondi à 0,001 cm. Une capture globale cadrée montre le volume des plateformes/structures, avec grandes faces magenta et un rendu sombre faute d’éclairage de scène ; aucun éclairage source n’a été créé et aucune retouche n’a été enregistrée. Les autres RootId, collisions, décals et World Partition restent hors du contrôle.
