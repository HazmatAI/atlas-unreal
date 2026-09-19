# Atlas vers Unreal Engine 5.8 — Journal de progression

Dernière mise à jour : 2026-09-19
Dossier de travail Atlas : `D:\EXTRAtor tarkov to UE\atlas-0.1.0-3dce665-win64-full`
Fork source : `D:\EXTRAtor tarkov to UE\atlas-unreal`
Build Atlas : `0.1.0-3dce665-win64-full`
Map pilote : `labyrinth` (Labyrinthe)
Source : installation live d'Escape from Tarkov

## Objectif

Construire une solution de production maximale pour importer directement dans Unreal Engine 5.8 une map extraite par Atlas : géométrie, textures, matériaux, hiérarchie/placement et données de scène utiles. Unity et Blender ne doivent pas être des étapes obligatoires.

La vue d'ensemble, la checklist par phase et le chemin critique sont maintenus dans [`docs/unreal/project-checklist.md`](docs/unreal/project-checklist.md). Ce document distingue explicitement la maturité de l'infrastructure de la couverture réelle de la map.

## Décisions actées

- La voie retenue est `Atlas -> importeur Unreal Engine direct`.
- Labyrinthe est la map pilote, choisie pour réduire le temps d'itération.
- Atlas lit l'installation live du jeu.
- L'extraction de Labyrinthe a déjà été effectuée par l'utilisateur.
- Le développement avancera par étapes vérifiables.
- L'architecture et le contrat de données ont été validés ; le développement du plugin UE a commencé.
- Les matrices affines Atlas doivent être conservées sans décomposition TRS naïve : elles peuvent contenir shear et miroir.
- Les conversions d'axes, de handedness, d'UV et de normal maps doivent être appliquées exactement une fois.

## État initial vérifié

### Dataset intermédiaire

- Chemin : `eft_assets\labyrinth`
- 9 551 fichiers
- Environ 1,80 Go
- Contient notamment `scene.json`, `colliders.json`, `decals.json`, les lumières, les meshes OBJ/MSH, les vertex colors et les textures.

### Pack Atlas final

- Chemin : `packs\labyrinth.eftpack`
- 756 fichiers
- Environ 1,39 Go
- `selfContained: true`
- Résultat du journal de build : `[BUILD OK] pack ready`
- Empreinte source : `a2fa9ad39c0dc867`
- 2 124 meshes
- 21 734 instances
- 1 523 matériaux
- 24 661 colliders
- 1 787 collider meshes
- 17 017 groupes de LOD
- 18 racines de scène

### Données disponibles dans le pack

- Géométrie : `meshes.bin`
- Placement affine complet : `instances.bin`
- Matériaux : `materials.json`
- Textures : `tex\`
- Collisions : `colliders.bin`, `collider_meshes.bin`
- Lumières : `lights_551.json`, `lights_552.json`
- Navigation : `nav.bin`, `nav_blk.bin`, `nav_door.bin`, `nav_wallcell.bin`, `nav.json`
- Volume/éclairage : `volume.bin`, `volume_valid.bin`, `volume.json`
- Métadonnées et intégrité : `manifest.json`, `lod_integrity.json`
- Données Tarkmap : `gamedata.json`

### Conventions Atlas à préserver

- Transform : matrice monde affine 3x4 row-major, shear et miroir inclus.
- Normales : locales, à transformer par l'inverse-transposée 3x3 par instance.
- UV : V-flip et tiling déjà intégrés aux vertices ; ne pas les réappliquer dans UE.
- Normal maps : convention DirectX, canal vert déjà inversé.
- Espaces couleur : albedo/emissive en sRGB ; normales en linéaire.

### Remarque dépôt

La distribution binaire Atlas reste séparée du checkout Git. Le fork local a été identifié et vérifié : dépôt propre sur `master`, synchronisé avec `origin/master`, remote `https://github.com/HazmatAI/atlas-unreal.git`.

## Audit documentaire UE 5.8

Documents ajoutés dans le fork :

- `docs/unreal/README.md`
- `docs/unreal/labyrinth-audit.md`
- `docs/unreal/eftpack-importer-spec.md`

Décision d'architecture proposée : plugin C++ UE 5.8 en deux modules (`AtlasEftRuntime` et `AtlasEftImporter`), lecteur manifest-driven, intégration Interchange hybride et assemblage de scène contrôlé par un subsystem d'import.

Résultats complémentaires sur Labyrinthe :

- 1 883 999 vertices, 1 880 147 triangles et 2 637 sections ;
- 738 textures distinctes référencées, zéro référence manquante ;
- zéro instance sheared, mirrored ou dégénérée ;
- 624 lumières ;
- 578 box colliders, 5 spheres et 24 078 mesh colliders ;
- 188 matériaux vertex-paint, mais aucun detail/parallax ;
- aucun terrain/grass dans ce pack.

Limites de données identifiées : le pack v1 ne contient pas de tangentes, d'UV secondaire ni d'identité/hiérarchie GameObject complète. Le premier import utilisera MikkTSpace et une structure root/niveau/LOD ; des extensions Atlas optionnelles sont prévues pour la solution maximale.

## Plan par jalons

1. ~~Auditer le pack Labyrinthe et documenter précisément ses schémas binaires/JSON.~~ Terminé.
2. ~~Définir le contrat de l'importeur UE 5.8 et les règles de conversion Unity/Atlas vers Unreal.~~ Terminé, proposition prête à valider.
3. ~~Valider l'architecture du plugin/importeur avec l'utilisateur avant de coder.~~ Terminé.
4. ~~Créer le squelette UE 5.8 et valider le lecteur/auditeur sur le vrai pack Labyrinthe.~~ Terminé.
5. Importer meshes, index, normales, tangentes, UV, vertex colors et LOD. En cours : décodeur, `UStaticMesh`, import batch et assemblage architectural de 25 instances validés ; associations LOD restantes.
6. Importer textures, construire le master material et générer les material instances. En cours : verticale opaque validée de bout en bout pour le matériau 17 et le mesh 20 ; généralisation contrôlée aux autres signatures de matériaux restante.
7. Recréer la scène et les placements, y compris shear, miroir et instances répétées. En cours : décodeur d'instances et prototype de niveau 25 acteurs validés avec une erreur de translation nulle ; scale et hiérarchie restants.
8. Ajouter collisions, lumières, decals, terrain/sol et données secondaires disponibles.
9. Mettre en place World Partition, Data Layers, Nanite/HISM et HLOD selon les catégories d'objets.
10. Comparer systématiquement Atlas et UE sur Labyrinthe : position, échelle, orientation, matériaux, éclairage et complétude.
11. Rendre le pipeline reproductible et l'étendre aux maps plus grandes.

## Prochaine étape

La verticale opaque minimale est désormais reproductible pour le matériau Atlas 17 et le mesh 20 : master réutilisable, material instance, trois textures, affectation au slot du mesh et map de contrôle dédiée. La prochaine étape est de rendre l'éclairage de cette map de contrôle reproductible afin de conclure sur l'orientation de la normale et la réponse de roughness, puis de valider un second matériau opaque représentatif. Ensuite vient un test de scène cohérente par `RootId`/niveau, plutôt qu'un voisinage uniquement spatial. Le choix Lumen/lumières dynamiques versus lightmaps statiques doit être formalisé avant l'import complet et avant toute génération massive d'UV2. Ne pas importer les 738 textures tant que les contrôles visuels et les principales signatures de matériaux ne sont pas concluants.

## Journal

### 2026-09-19

- Choix confirmé : Atlas vers importeur UE direct, niveau de fidélité maximal.
- Map pilote confirmée : Labyrinthe.
- Source confirmée : installation live d'Escape from Tarkov.
- Extraction locale inspectée et validée.
- Pack auto-contenu et build réussi confirmés.
- Aucun code d'importeur ajouté.
- Création de ce journal de progression.
- Fork source local vérifié ; worktree propre.
- Audit quantitatif complet de Labyrinthe effectué.
- Architecture UE 5.8, conversions de coordonnées, stratégie shear/mirror, matériaux, collisions, structure, reimport et validation documentés.
- Documentation UE ajoutée au fork, sans code d'importeur.
- Exceptions `.gitignore` ajoutées pour versionner `docs/unreal/**/*.md` et `progress.md`.
- Création du plugin `unreal/AtlasEftImporter` avec modules runtime et éditeur.
- Ajout d'un lecteur/auditeur `.eftpack`, d'un subsystem, d'une commande de menu et du commandlet `AtlasEftAudit`.
- Build du plugin validé avec UE 5.8.2 pour UnrealEditor Development, UnrealGame Development et UnrealGame Shipping.
- Audit réel de `labyrinth.eftpack` exécuté dans UE : PASS en 0,19 s, compteurs et intégrité conformes.
- Deux tests Automation (`CoordinateConversion` et `AffineAnalysis`) exécutés : PASS.
- Projet hôte minimal et script reproductible build/audit/tests ajoutés.
- Contrainte locale documentée : Windows bloque les DLL non signées depuis `D:` ; staging exécutable sur `C:` requis, sans déplacer les sources.
- Décodeur manifest-driven de `meshes.bin` ajouté : positions, normales, UV, vertex colors, indices et sections.
- Test Automation `Atlas.Eft.Runtime.MeshDecode` ajouté ; suite runtime portée à 3 tests, tous PASS.
- Constructeur editor-only `FStaticMeshImporter` et commandlet `AtlasEftImportMesh` ajoutés.
- Conversion Atlas vers UE appliquée au mesh : mètres vers centimètres, changement d'axes et winding inversé exactement une fois ; UV et vertex colors conservés ; tangentes MikkTSpace recalculées.
- Premier asset persistant construit depuis le vrai pack : `/Game/Atlas/Samples/SM_Atlas_0000_stone1`, 4 024 vertices source, 3 690 triangles, 1 section ; build et sauvegarde `.uasset` PASS.
- `BuildAndTest.ps1` étendu pour exécuter build, audit, import d'un mesh réel et 3 tests Automation dans une seule passe.
- Ajout du commandlet batch `AtlasEftImportMeshes` avec plages, option `-All`, lecture unique du manifeste et chemins shardés stables par blocs de 256 ids.
- Batch réel mesh ids 0 à 2 validé : 3 assets, 4 077 vertices source/rendu, 3 721 triangles, 3 sections, zéro erreur.
- Validation du render data renforcée avant sauvegarde : le nombre de triangles et de sections UE doit correspondre exactement au pack.
- Connexion au serveur MCP Unreal local `127.0.0.1:8000` validée ; éditeur `AtlasEftImporterHost`, level `/Temp/Untitled_1`, PIE arrêté et plugin actif.
- Diagnostic de l'erreur `too many spaces` : un chemin Windows `.uproject` avait été envoyé à `AssetTools.exists`, qui attend un package path Unreal. Aucun déplacement ou renommage du dépôt n'est requis.
- Workflow MCP corrigé et documenté dans `docs/unreal/mcp-workflow.md` : `AssetTools` uniquement pour `/Game`/mount points ; inspection projet via Scene/Editor/Plugin toolsets.
- Après les requêtes MCP corrigées, aucune nouvelle erreur `DoesDirectoryExist`/`DoesAssetExist` n'a été générée ; seules les quatre anciennes lignes de 13:25:43–13:25:46 subsistent dans le log.
- Ajout d'un parseur typé pour `materials.json` : modes alpha, paramètres numériques et références de textures classées par rôle (albedo, normal, specular, emissive, detail, vertex-paint et parallax).
- Audit réel du nouveau parseur sur Labyrinthe : 1 523 matériaux, 738 textures distinctes et zéro référence manquante.
- Test Automation `Atlas.Eft.Runtime.MaterialDecode` ajouté ; la suite runtime comporte désormais 4 tests, tous PASS.
- Le projet hôte de test déclare désormais `GameFeatureData` dans l'Asset Manager. Cela empêche les toolsets MCP expérimentaux d'ajouter une erreur globale sans rapport avec Atlas et de fausser le code de sortie des commandlets.
- Relance intégrale validée après correctif : build UE 5.8 Editor/Development/Shipping, audit du pack, import du mesh 0, batch meshes 0–2 et 4 tests runtime, tous PASS avec code de sortie 0.
- Ajout de `FTextureImporter` et du commandlet `AtlasEftImportTextures`, volontairement limité par `-MaterialId` et `-MaxTextures` pour tester un petit échantillon avant tout import massif.
- Échantillon choisi : matériau 17 et mesh 20 (`model`), avec trois PNG distincts afin de couvrir albedo, normal et specular sans déduplication trompeuse.
- Import headless validé : albedo 1024×1024, sRGB, compression Default ; normal 1024×1024, linéaire, compression Normalmap, sans second flip vert ; specular 512×512, linéaire, compression Masks.
- Une première lecture des dimensions via la ressource GPU retournait 0×0 sous `-NullRHI`. Le contrôle utilise désormais les dimensions de `UTexture::Source` et échoue si elles sont invalides.
- `BuildAndTest.ps1` importe et contrôle désormais les trois textures d'essai, exige exactement le mesh échantillon demandé et conserve les audits, batch meshes et 4 tests runtime.
- Pipeline complet relancé avec `SampleMeshId=20`, `SampleTextureMaterialId=17` et `SampleTextureCount=3` : PASS, code de sortie 0.
- Test MCP live validé dans `/Game/Atlas/Textures/McpSamples` : exactement trois assets présents, tailles 1024²/1024²/512², albedo sRGB, normal linéaire/Normalmap sans flip vert et specular linéaire. Les trois packages ont été écrits ; aucune modification de scène.
- Limite MCP observée : `AssetTools.is_dirty` reste à `true` après que `save_assets` a retourné `true` et que les logs ont confirmé les trois écritures. Aucune sauvegarde répétée à l'aveugle ; la validation de persistance repose sur le résultat et les logs de package.
- Les assets de test et futurs assets Tarkov générés sous `unreal/AtlasEftImporterHost/Content/Atlas/` sont explicitement ignorés par Git : ils restent des sorties locales reproductibles et ne doivent jamais être redistribués avec le code source.
- Ajout du décodeur strict de `instances.bin` (records de 80 octets) avec matrice affine, ids mesh/LOD/root, flags, parenté et niveau ; test Automation `Atlas.Eft.Runtime.InstanceDecode` ajouté, suite portée à 5 tests.
- Ajout du commandlet `AtlasEftBuildScene`, qui sélectionne un voisinage spatial reproductible, importe les meshes uniques, convertit chaque affine Atlas vers un `FTransform` UE et sauvegarde un niveau de contrôle.
- Le validateur de meshes distingue désormais les triangles source dégénérés : Unreal peut supprimer uniquement ceux prouvés dégénérés, mais toute perte de triangle valide ou modification de sections reste une erreur. Le mesh 1810 contient 2 triangles source dégénérés sur 134.
- Test architectural Labyrinthe validé : seed instance 0, 25 acteurs, 18 meshes uniques, 4 831 triangles rendus, bounds des origines UE de `(2433.30, -2621.03, 3.42)` à `(2733.47, -2285.53, 12.86)` cm et erreur maximale de translation `0.000000 cm`.
- La génération de niveau est idempotente : le commandlet remplace uniquement son `.umap` généré portant le même nom et nettoie explicitement son monde temporaire avant de quitter.
- `BuildAndTest.ps1` couvre maintenant build Editor/Development/Shipping, audit pack, 3 textures, mesh simple, batch de 3 meshes, niveau architectural de 25 instances et 5 tests runtime ; passe complète avec code de sortie 0.
- Validation MCP live du niveau `/Game/Atlas/ArchitectureTest/L_Atlas_Architecture_Seed000000_Count025` : filtre par classe `/Script/Engine.StaticMeshActor` = exactement 25 acteurs Atlas.
- Le seed `Atlas_I000000_M0000` retourne une translation de `(2591.39, -2457.35, 4.90)` cm, conforme à l'attendu approximatif `(2591.4, -2457.4, 4.9)`. Ses bounds monde valides sont `(2408.51, -2646.41, 0.20)`–`(2787.58, -2265.03, 10.05)` cm.
- Trois acteurs choisis par dispersion spatiale ont aussi des transforms finis et des bounds valides : `Atlas_I007165_M0297` à `(2551.39, -2285.53, 4.22)` cm, `Atlas_I020868_M1813` à `(2532.36, -2621.03, 12.86)` cm et `Atlas_I011455_M0562` à `(2433.30, -2500.70, 4.20)` cm.
- Focus et capture du viewport MCP réussis. La géométrie apparaît grise/sans matériaux, ce qui est cohérent avec le jalon architectural avant assemblage du matériau. Prochaine étape réelle : master opaque et material instance Atlas 17, appliqués au mesh 20, puis contrôle albedo/normal/specular/UV.
- Sémantique exacte du matériau Atlas 17 confirmée : opaque, double face, teinte blanche, metallic `0`, roughness scalaire `0.5`, normal scale `1` et roughness active dérivée de `clamp(1 - albedo.a, 0.06, 1)`.
- Le champ Atlas `specMap` est une provenance conservée, pas une roughness : le pipeline Atlas ne l'échantillonne pas dans le rendu de production. Le master UE utilise donc un specular diélectrique `0.5`; la texture `pc_laptop_g` reste référencée comme provenance inactive et n'est pas branchée au shader normal.
- Ajout du master réutilisable `/Game/Atlas/Common/Materials/M_AtlasOpaque`, de la material instance `/Game/Atlas/MaterialTest/Materials/MI_Atlas_0017`, du mesh `/Game/Atlas/MaterialTest/Meshes/SM_Atlas_0020_model` et de la map `/Game/Atlas/MaterialTest/L_Atlas_Material_0017_Mesh0020`.
- La map de contrôle contient l'acteur `Atlas_Material17_Mesh20` et un éclairage directionnel. Le slot unique du mesh 20 utilise la material instance 17.
- Le commandlet `AtlasEftBuildMaterialSample` génère et valide ces assets de façon idempotente, sans modifier le niveau architectural existant.
- Pipeline intégral validé avec code de sortie `0` : audit, trois textures, mesh simple, batch de trois meshes, scène architecturale de 25 instances, verticale matériau et cinq tests runtime, tous PASS.
- Les sept assets nécessaires à ce contrôle (master, map, MI, mesh et trois textures) ont été synchronisés sous le `Content/Atlas` du projet hôte live; ils restent ignorés par Git et entièrement reproductibles.
- Validation MCP live réussie : niveau matériau chargé, acteur `Atlas_Material17_Mesh20` retrouvé, composant lié au mesh 20 et override lié à `MI_Atlas_0017`; slot `AtlasMaterial_17`, 164 vertices et 180 triangles confirmés.
- La MI live a pour parent `M_AtlasOpaque`; paramètres confirmés : albedo et normale attendus, provenance specMap attendue, metallic `0`, roughness `0.5`, sélection albedo-alpha `1`, normal scale `1`, specular diélectrique `0.5` et switch debug provenance `false`.
- Capture viewport MCP obtenue depuis `(90, 0, 25)` cm face au mesh : le clavier est visible avec son albedo et des UV cohérents à ce premier contrôle. La scène de contrôle reste très sombre et ne suffit pas encore à juger finement l'orientation de la normal map ou la réponse de roughness ; ce contrôle visuel affiné reste la prochaine étape.
- Après ajout manuel d'un spotlight et `Build Lighting`, Unreal signale sur `SM_Atlas_0020_model` : `Object has overlapping UVs` et `Lightmap UV are overlapping by 1.6%`. Le pack Atlas v1 ne fournit pas d'UV secondaire dédié ; cet avertissement concerne donc les UV de lightmap, pas l'UV0 matériau dont le rendu albedo reste plausible. Il est consigné mais non corrigé et ne bloque pas le pipeline actuel. La voie courte probable est Lumen/éclairage dynamique ; le besoin de lightmaps statiques et de génération UV2 doit être décidé explicitement avant tout traitement massif.
- Création de `docs/unreal/project-checklist.md` : vue globale depuis l'extraction, distinction infrastructure/couverture réelle, checklist par phase, critères de sortie, chemin critique, risques de sur-ingénierie et décisions ouvertes.
