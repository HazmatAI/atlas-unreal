# Atlas vers Unreal Engine 5.8 — Journal de progression

Dernière mise à jour : 2026-09-20
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
6. Importer textures, construire le master material et générer les material instances. En cours : verticales opaques 17/20 et 974/1240 passées de bout en bout en staging avec master partagé unique; généralisation contrôlée aux autres signatures de matériaux restante.
7. Recréer la scène et les placements, y compris shear, miroir et instances répétées. En cours : décodeur d'instances et prototype de niveau 25 acteurs validés avec une erreur de translation nulle ; scale et hiérarchie restants.
8. Ajouter collisions, lumières, decals, terrain/sol et données secondaires disponibles.
9. Mettre en place World Partition, Data Layers, Nanite/HISM et HLOD selon les catégories d'objets.
10. Comparer systématiquement Atlas et UE sur Labyrinthe : position, échelle, orientation, matériaux, éclairage et complétude.
11. Rendre le pipeline reproductible et l'étendre aux maps plus grandes.

## Prochaine étape

Le groupe pilote réel par parenté (53 instances) reste disponible comme test ciblé. Le RootId 3 complet actif/LOD0 est dans sa map dédiée MyProject : 1 944 acteurs, 285 meshes et 292 matériaux uniques. Structure, transforms, slots, matériaux et bounds contrôlés automatiquement et par MCP ; la capture globale montre la forme architecturale, mais les fallback magenta et l’absence d’éclairage de scène rendent l’aspect visuel partiel. Les collisions Root3 sont validées en staging/idempotence (1 702 composants) et synchronisées offline au package Package12 exact : `.umap` + 169 assets hash-match, backup datée conservée. La prochaine étape est de rouvrir le projet et la map Root3 existants, valider via MCP les comptes chargés et tester Pawn/WorldStatic sur un mesh et les deux boxes. Aucun résultat post-sync MCP/physique n'est encore revendiqué. Lumières source, décals, autres racines, World Partition et optimisation restent hors de ce jalon.

## Journal

### Secteur pilote structurel — 2026-09-20

- **Validé automatiquement** : audit reproductible des 18 RootId et niveaux, bornes géométriques et matériaux dans `docs/unreal/labyrinth-root-audit.md` et son JSON. Script standard-library `unreal/Scripts/AuditSector.py`.
- Sélection intégrale par `(RootId=3, Level=547, GrandparentId=9787)`, actif hors-LOD/LOD0 : escaliers métalliques Area_03, 53 instances, 4 meshes (750, 784, 860, 927), 9 matériaux (298, 299, 679, 680, 703, 704, 796, 797, 798). Aucun voisinage spatial ni troncature.
- Nouveau commandlet `AtlasEftBuildSector` : import limité, slots déterministes, dossiers root/niveau/ancêtre/parent, tags provenance/LOD, contrôle des huit coins transformés et des bounds. Le lecteur conserve la présence des fonctions opaques non supportées ; fallback magenta prévu, sans master par objet.
- Build Editor Development réussi. Import/reconstruction ciblée : **code 0**, 53 acteurs, 4 meshes, 9 MI, 13 textures distinctes, 121 slots instanciés, 760 triangles sur les meshes uniques. Erreurs maximales translation/corners `0.000000000 cm`. Aucun fallback nécessaire ici.
- Une première passe avait échoué globalement sur une configuration GameFeatures copiée dans le host minimal ; configuration corrigée. La relance a révélé la sauvegarde d'un package de map existant non marqué complètement reconstruit ; correctif `MarkAsFullyLoaded`, puis import réussi. Aucun fichier de map n'a été supprimé pour contourner ce problème.
- Test négatif nouveau : ExpectedCount=54 pour le groupe de 53 est refusé avec code 1 avant toute création d'asset.
- 27 nouveaux assets uniquement copiés vers `MyProject/Content/Atlas/Labyrinth/Pilot/R03_L547_G9787`, SHA256 vérifiés. Le master partagé est identique à celui du live et n'a pas été recopié. Plugin live et anciennes maps préservés ; le nouveau commandlet est compilé dans le host temporaire, pas installé dans l'éditeur déjà ouvert.
- **Validé dans Unreal/MCP** : map `/Game/Atlas/Labyrinth/Pilot/R03_L547_G9787/L_Atlas_Sector_R03_L547_G9787` chargée ; 53 identifiants exacts comparés indépendamment à instances.bin, transforms/translations et bounds, quatre meshes, neuf slots d'assets, neuf parents MI et références textures présents. Aucun override de matériau parasite. Rapport `labyrinth-sector-mcp.json`.
- Bounds UE (cm) : min `(-2281.769, -2976.645, -1039.593)`, max `(-1903.927, -2634.177, 1218.294)`. Translation source/MCP : erreur nulle.
- **Contrôle visuel unique** : ensemble vertical d'escaliers et paliers reconnaissable, textures visibles, mais rendu sombre. Cohérence structurelle visible ; fidélité normale/roughness **partielle**, pas de validation photométrique.
- **Non testé / différé** : branche fallback sur un asset réel, autres groupes, associations LOD, collisions et lumières source. Cibles Game Development/Shipping et anciens tests runtime non relancés dans ce jalon ; leurs PASS précédents ne sont pas attribués à cette version.
- Aucun commit/push. La copie initiale des sources a été refusée par la revue automatique ; application ensuite acceptée après diff exact, sauvegarde de l'état courant et contrôle SHA256 des six fichiers. Les changements préexistants sont conservés.

### Import complet RootId 3 actif/LOD0 — 2026-09-20

- Étendu minimalement `AtlasEftBuildSector` : mode RootId complet sans `GrandparentId` ni `Level`; le mode pilote `(RootId=3, Level=547, GrandparentId=9787, ExpectedCount=53)` reste inchangé. Filtre complet : RootId 3, actif, `LodIndex <= 0`, sans troncature spatiale.
- Compteurs calculés et validés : `2 159` records RootId 3 bruts, `0` inactif, `215` records LOD supérieurs retirés, soit exactement `1 944` acteurs. Sélection Root3 LOD0 : `285` meshes, `292` matériaux (229 supportés / 63 fallback), 200 chemins texture, 341 slots sur meshes uniques, 2 150 slots instanciés (1 754 supportés / 396 fallback), 241 409 triangles uniques.
- BuildPlugin UAT réussi pour UnrealEditor Development, UnrealGame Development et UnrealGame Shipping. Dry-run Root3 code 0. Import final staging code 0 : 43,04 s dans le rapport (43,24 s commandlet), pic mémoire 3 095 MB. Meshes et textures réutilisés dans le staging déjà préparé ; sortie finale 722 fichiers (721 `.uasset`, 1 `.umap`), 376 985 008 octets; source 95 958 955 octets.
- Trois essais de staging antérieurs n’avaient pas sauvegardé la map : deux s’arrêtaient au seuil corners de 0,1 cm (le maximum mesuré étant 0,135975894 cm), un autre révélait un package de fallback précédemment partiellement chargé. Après vérification d’une affine orthogonale et décision Sol de seuil 0,15 cm (bounds toujours 0,1 cm), le correctif `FullyLoad` et une unique exécution finale ont réussi. Aucun asset live n’a été touché avant cette réussite.
- Validations avant sauvegarde : 1 944 acteurs, IDs uniques, mesh/material refs, 2 150 slots, AABB/corners et bornes cohérentes ; translation max `0 cm`, corner max `0.135975894 cm` (instance 13817 / mesh 763, affine orthogonale). Après avis Sol, seuil corners final fixé à `0.15 cm`; bounds restent à `0.1 cm`. Références manquantes `0`, IDs dupliqués `0`, anomalies import `0`. Le log Unreal a émis 8 avertissements de performance Interchange/FindConsoleObject, sans erreur.
- Map stable `/Game/Atlas/Sectors/Root_003/L_Atlas_Root003_Area03`, acteurs organisés root/niveau/ancêtre/parent, un acteur par instance. Aucun HISM, WP, collision, éclairage ou décal ajouté.
- Cible live initialement absente. Copie limitée au nouveau `MyProject/Content/Atlas/Sectors/Root_003` : 722 fichiers / 376 985 008 octets. SHA-256 des 722 assets comparés staging/live, tous identiques. Aucun asset partagé/master ou autre sortie Atlas remplacé. Le master live `/Game/Atlas/Common/Materials/M_AtlasOpaque` a été vérifié dans MCP puis laissé inchangé.
- MCP live : map courante exacte, `Atlas/Root_003` récursif = 1 944 acteurs; `AssetTools.find_assets` indexe 722 assets Root_003; bounds extrêmes relus depuis 6 régions et 7 acteurs, union min `(-2287.021107, -5346.583519, -1039.593029)` / max `(-629.742658, -2591.221527, 1478.869609)` cm, conforme au staging. Échantillon acteur 12679 mesh 668 : transform/tags/bounds cohérents, slot MI `MI_Atlas_0002` parentée au master, albedo/normal présentes; acteur 12751 mesh 669 slot 648 : fallback `M_AtlasUnsupported_Magenta`. Aucun override matériel composant parasite.
- Capture MCP globale après cadrage : volume de plateformes reconnaissable, mais grandes surfaces magenta de fallback et fond sombre sans éclairage; fidélité visuelle des matériaux seulement partielle. Aucun polissage supplémentaire effectué.
- Rapports reproductibles conservés dans `docs/unreal/labyrinth-root3-validation.json` et `.txt`; l’audit des racines distingue maintenant clairement le tableau brut (2 159 records/350 meshes) du Root3 sélectionné LOD0. Aucun autre RootId importé; aucun commit/push.

### Collisions du RootId 3 — 2026-09-20

- Schéma vérifié avant implémentation : les 24 661 lignes pack/sidecar correspondent exactement; RootId 3 associe de manière unique `SBG_Labyrinth_Area_03` au niveau 547.
- Nouveau commandlet `AtlasEftAddSectorCollisions`, limité à la map Root3 existante; sélection auditable de 1 702 bloqueurs (1 700 mesh + 2 boxes Interactive non-trigger), 169 assets partagés par meshId, 4 layer actors. Les colliders HighPoly et triggers sont exclus. Components invisibles/statiques, `UseComplexAsSimple` pour les meshes, Pawn/WorldStatic bloqués, overlaps/nav désactivés; tags/version permettent le remplacement idempotent ciblé.
- BuildPlugin Package12 PASS pour UnrealEditor Development, UnrealGame Development et UnrealGame Shipping. Exécution staging headless PASS, puis seconde exécution remplaçant exactement 4 acteurs / 1 702 composants : pas de doublons. Géométrie avant/après identique : 1 944 acteurs, CRC32 `4195649602`. Bounds max error `0.068625 cm` (tolérance `0.25 cm`). Un plan déterministe de 1 702 composants (35 chunks MCP) est aussi généré. Voir `docs/unreal/labyrinth-root3-collision-validation.{txt,json}`.
- MCP live avait confirmé avant fermeture que Root3 était chargée avec 1 944 acteurs géométriques et 0 collision actor managé (« All Saved »). L'exploration MCP editor-native ultérieure s'est arrêtée sans mutation (pas d'exécuteur Python/console ni d'opération bulk/transactionnelle); plan lu en 35 chunks, 1 702 IDs uniques et 169 références indexées. Le live était alors clean (`AssetTools.is_dirty=false`).
- Après fermeture de l'éditeur, la synchronisation offline Package12 a été effectuée sous garde : aucun UnrealEditor/UnrealEditor-Cmd, test de verrou exclusif réussi, `.umap` original SHA256 `5EA6983A…A714A3EB`, staged PASS validé par JSON/log. Backup datée `C:\Users\Pc\Documents\ChatGPT\Aether\sector_collision_stage\LiveOriginalBackup\Root003_LiveBackup_20260920_165631` contient la map originale, les 169 assets originaux et leur manifeste SHA256.
- Seuls le `.umap` Root003 et ses 169 `.uasset` `CollisionMeshes` ont été remplacés. Live map = Package12 (16 073 277 bytes, SHA256 `DCC7E3DF957B73F281CC9B7B7BD9B0197927F9F152E842296F225347F123D82C`); assets 169/169 SHA256 identiques au staging. Statut `SYNCHRONIZED_OFFLINE`, non encore MCP-validated. Reste : rouvrir le projet/map existants, vérifier 1 944 geometry + 4 acteurs/1 702 components, puis Collision Visualization et traces Pawn/WorldStatic sur un mesh et les deux boxes. Aucun commit/push.


### 2026-09-20

- Note de migration : le projet Unreal actif est maintenant `D:\EXTRAtor tarkov to UE\unreal project\MyProject`. L'ancien projet `D:\EXTRAtor tarkov to UE\atlas-unreal\unreal\AtlasEftImporterHost` ne doit plus recevoir aucune modification Unreal. Toutes les futures modifications Unreal doivent cibler uniquement le nouveau projet.

- Projet Unreal canonique migré vers `D:\EXTRAtor tarkov to UE\unreal project\MyProject\MyProject.uproject`. L'ancien host sous `Aether\ue_test` ne contenait aucun asset `Content/Atlas` récupérable.
- Plugin actuel compilé depuis les sources du dépôt puis installé sous `MyProject\Plugins\AtlasEftImporter`; `AtlasEftImporter` est activé dans `MyProject.uproject`. La configuration MCP canonique est `MyProject\.codex\config.toml` sur `127.0.0.1:8000`.
- `BuildAndTest.ps1` accepte désormais `-LiveProjectPath`, synchronise les 12 assets validés de comparaison matériau vers le projet live tout en préservant le plugin existant et les autres dossiers `Content/Atlas`, puis lance un audit depuis ce `.uproject`. Le staging `%LOCALAPPDATA%` reste uniquement une contrainte technique de compilation/exécution des DLL non signées.
- Passe complète antérieure enregistrée : builds Editor/Development/Shipping, audit, imports, scène architecturale, deux matériaux et cinq tests runtime PASS. Cette entrée historique précède la reprise ci-dessous ; elle ne signifie pas que la relance avec les sources actuelles a réussi.
- L'entrée historique confirme les propriétés du rig dans les deux maps : key/fill/rim `Movable`, exposition manuelle EV100 0 et `ForceNoPrecomputedLighting=true`. La validation visuelle de normale et roughness reste partielle, voir la reprise ci-dessous.
- Le second matériau Atlas 974 sur mesh 1240 est enregistré avec roughness constante `0.900`, albedo + normal et sans specMap. Le contrôle MCP décrit plus bas a relu ces paramètres dans les assets live existants.
- Les assets des échantillons, textures, scène architecturale 25 instances, master opaque et maps matériaux 17/974 sont présents dans `MyProject\Content\Atlas`; leur présence ne prouve pas la réussite de la relance actuelle.
- L'audit directement exécuté depuis `MyProject` est enregistré PASS, code 0, lors d'une exécution précédente. Le processus headless désactivait `ModelContextProtocol` pour éviter la concurrence avec le serveur MCP live sur le port 8000.
- Règle Asset Manager `GameFeatureData` ajoutée au `DefaultGame.ini` de MyProject. Le MCP live répond depuis l'éditeur MyProject; l'instance déjà ouverte doit être redémarrée une fois pour charger le nouveau plugin Atlas installé.

#### Reprise du jalon matériaux — état avant rétablissement du headless, 2026-09-20

- Audit du checkout avant modification : code existant déjà contenait un master opaque réutilisable, génération des MI 17/974, maps séparées, rig de trois Directional Lights `Movable`, exposition manuelle, caméra déterministe, `ForceNoPrecomputedLighting=true` et support d'une specMap optionnelle.
- Correction nécessaire dans `BuildAndTest.ps1` : son défaut `SampleMeshId=0` ne correspondait pas au matériau 17. Le défaut est maintenant `20`, pour garder la paire documentée 17/20.
- BuildPlugin recompilé avec succès pour UnrealEditor Development, UnrealGame Development et UnrealGame Shipping.
- Le lancement headless échoue avant l'exécution de l'audit/imports/tests : Windows Code Integrity/WDAC `VerifiedAndReputableDesktop` bloque les DLL générées depuis `%LOCALAPPDATA%` (`GetLastError=4551`, status `0xc0e90002`, event 3077). Aucun contournement ni changement de stratégie de sécurité n'a été fait ; cette relance n'est pas un PASS de pipeline complet.
- MCP live a chargé les maps déjà présentes dans MyProject : `/Game/Atlas/MaterialTest/L_Atlas_Material_0017_Mesh0020` et `/Game/Atlas/MaterialTest/L_Atlas_Material_0974_Mesh1240`. La recherche disque confirme un seul master `M_AtlasOpaque.uasset` dans `Content/Atlas`.
- Map 17/20 : acteur lié à `SM_Atlas_0020_model` et `MI_Atlas_0017`; parent `/Game/Atlas/Common/Materials/M_AtlasOpaque`; metallic `0`, roughness `0.5`, selector alpha `1`, normal scale `1`; albedo, normal et specMap de provenance référencées.
- Map 974/1240 : acteur lié à `SM_Atlas_1240_collector_Section_03_LOD0` et `MI_Atlas_0974`, même master ; metallic `0`, roughness `0.9`, selector alpha `0`, normal scale `1`; seules albedo et normal sont surchargées, pas de specMap.
- Dans les deux maps, MCP confirme key/fill/rim `Movable` aux intensités `3.5/1.25/2`, PP unbound, `AEM_Manual`, bias `0`, exposition physique désactivée et `bForceNoPrecomputedLighting=true`.
- Captures MCP : le clavier (17/20) apparaît petit et sombre ; la maçonnerie (974/1240) montre du détail de surface en cadrage proche, mais l'image demeure sombre et le warning signale que trois Directional Lights rivalisent. La normale et la roughness restent sans validation visuelle concluante.
- Les vérifications MCP portent sur les assets déjà présents dans le projet live. Elles sont distinctes d'une exécution complète de la version courante ; elle reste à faire quand le chargement des modules compilés sera autorisé.

#### Reprise post-WDAC — pipeline et rig reproductible du 2026-09-20

- Smart App Control a été désactivé par l'utilisateur. La relance n'a nécessité aucun changement de stratégie de sécurité et n'a plus produit le blocage WDAC 4551.
- Le pipeline a été relancé avec `D:\EXTRAtor tarkov to UE\atlas-0.1.0-3dce665-win64-full\packs\labyrinth.eftpack`, les paires 17/20 et 974/1240, sans `-LiveProjectPath`. Sorties confinées au staging `%LOCALAPPDATA%\AtlasEftImporterDev`.
- BuildPlugin a réussi pour UnrealEditor Development, UnrealGame Development et UnrealGame Shipping. Audit pack, textures/imports, meshes d'échantillon et batch, map architecturale 25 instances, maps de comparaison des deux matériaux et cinq tests runtime ont tous réussi.
- Le rig du commandlet `AtlasEftBuildMaterialSample` a été rendu déterministe depuis les bounds du mesh : exactement une Directional Light `Movable`, intensité 5, ombres désactivées; une Rect Light `Movable`, 20 Candelas; PP manuel EV100 0; backdrop plan neutre utilisant les assets Engine; caméra déterminée par le centre/rayon des bounds et visant le mesh; `ForceNoPrecomputedLighting=true`. Des assertions du commandlet vérifient ces propriétés avant sauvegarde des maps headless.
- L'implémentation réutilise l'unique master opaque existant et ne crée aucun master Atlas supplémentaire ni dépendance au contenu de MyProject. Aucune map ou asset live n'a été synchronisé/remplacé.
- Inspection MCP en lecture seule de la map live 974/1240 : l'acteur référence mesh 1240 et `MI_Atlas_0974` (parent `M_AtlasOpaque`), roughness `0.9`, metallic `0`, normal scale `1`, albedo/normal; une Directional, une Rect Light, PP manuel, caméra mieux cadrée et backdrop pâle/gris clair. Aucun changement n'a été sauvegardé. Albedo lisible, mais normale et roughness restent visuellement non concluantes dans la capture sombre.
- Distinction de validation : le rig des maps générées est **validé structurellement headless**; la scène live est **inspectée manuellement en lecture seule**; la validation visuelle de normale/roughness sur les maps générées reste **partielle/à faire**.

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

### Voisinage Root3 → Root1, collisions et assembly — 2026-09-20

- Audit de géométrie active LOD0 via les huit coins des bounds transformés : meilleur voisin architectural Root1 `SBG_Labyrinth_Area_06`, 10 009 instances / 522 meshes; aggregate AABB gap 0 m et 462 paires sous 0,5 m. Preuve locale : Root3 porte instance 14303 recouvre Root1 Pipe_support_wall instance 6257 par 0,100000 × 0,022000 × 1,170001 m. Root7, autre root architecturale proche, a son instance la plus proche à 13,70 m. Voir [audit](docs/unreal/labyrinth-root-adjacency.md).
- Import staging Root1 actif/LOD0 PASS : 10 009 acteurs, 522 meshes, 10 430 slots, 409 427 triangles, 1 868 LOD supérieurs écartés; translation erreur 0, coins max `0.004075875 cm`, IDs dupliqués/références manquantes 0. Bounds UE min `(-3383.764,-4467.952,-472.591)`, max `(6545.657,2547.392,739.713)` cm.
- Association colliders Root1 exacte sur les 24 661 lignes sidecar/pack; sélection blockers 6 805 = 6 788 mesh + 17 box, 244 assets mesh partagés, 4 layer actors; HighPoly et triggers exclus. Deuxième exécution remplace exactement 4 acteurs/6 805 components; géométrie 10 009/10 009 et CRC32 `1970692440` avant/après; bounds erreur max `0.221973 cm / 0.25 cm`. BuildPlugin Package13 Editor Development/Game Development/Shipping PASS. Rapports : [collisions Root1 JSON](docs/unreal/labyrinth-root1-collision-validation.json) et [texte](docs/unreal/labyrinth-root1-collision-validation.txt).
- Map assembly `/Game/Atlas/Sectors/Assemblies/L_Atlas_Root003_Root001_Adjacent`: deux refs `ULevelStreamingAlwaysLoaded` identité, 0 acteur StaticMesh persistant. L'audit corrigé transforme les huit coins mesh LOD0 sous le RelativeTransform sérialisé du root mesh; bounds sector concordants à 0,25 cm. La paire Root3 door 14303 / Root1 pipe support 6257 overlap en UE `117,0001 × 10,0000 × 2,2000 cm` (>0,5 cm/axe). BuildPlugin Package15, deux creates et reload `-ValidateOnly` PASS; 1 944 + 10 009 géométriques, 0 IDs communs. Voir [rapport assembly](docs/unreal/labyrinth-root3-root1-assembly-validation.txt).
- Sync offline après contrôle editor fermé et assembly cible libre: Root1 (1 331 fichiers: 1 330 `.uasset`, 1 `.umap`, 602 094 379 octets, SHA256 1 331/1 331) inchangée; seule assembly map 7 118 octets mise à jour, SHA256 `4ADC4028…D5E04C7`. Ancienne map (hash `4E63A342…4B00B59`) sauvegardée datée sous `Aether/sector_collision_stage/Root1AdjacencyStageHost/Saved/Backups/LiveAssemblyBoundsFix`. Root3 live strictement préservée: hash `4CEF41B2…EEF5C`, différent du staging `DCC7E3DF…123D82C`; statut offline/MCP pending. La copie distribution `D:\EXTRAtor tarkov to UE\atlas-0.1.0-3dce665-win64-full\progress.md` est synchronisée avec le journal du dépôt; son original est sous `Aether/sector_collision_stage/Root1AdjacencyStageHost/Saved/Backups/distribution-progress`.
- Prochain test manuel précis : rouvrir MyProject, ouvrir uniquement l'assembly, confirmer les sublevels Root3 et Root1 loaded/visible à transform identité, counts géométrie 1 944 + 10 009, collisions 1 702 + 6 805 sans IDs dupliqués, puis traces Pawn/WorldStatic sur mesh blocker Root1 et box Root3. Vérifier pas d'overlap events ni génération automatique navmesh; ne pas enregistrer de modification manuelle.
## Classification des 16 racines Labyrinthe restantes — 2026-09-20

- L’utilisateur a confirmé manuellement **PASS** pour Root1 Player Collision et pour l’alignement de la porte/jonction Root1↔Root3. Je consigne ces résultats rapportés sans les présenter comme une nouvelle validation de cet audit. La validation MCP post-sync Root3 (1 944 géométriques, 4 acteurs / 1 702 composants collision et traces Pawn/WorldStatic) reste en attente.
- Inventaire source reproductible des racines restantes sous [rapport Markdown](docs/unreal/labyrinth-remaining-roots-classification.md) et [JSON](docs/unreal/labyrinth-remaining-roots-classification.json). Association collider sidecar↔pack : 24 661/24 661 lignes comparées après conversion, 0 mismatch de champs, max numérique 3,81e-6, 0 mismatch de niveaux sur les racines manifestées; 298 colliders portent des noms de racine non présents au manifest et sont laissés non attribués.
- Après Root4, les six racines structurelles Root1/3/4/5/6/7 comptent **17 383 / 19 099 placements actifs LOD0 = 91,0 % des placements**. Ce ratio est un compte de placements, **pas** une mesure de surface ou de volume couvert. L’architecture principale est considérée provisoirement complète, sous réserve de la validation manuelle Root4 et de la dette collision des 7 convex explicitement exclus.
- Les **1 716 placements actifs LOD0 restants** sont ventilés ainsi : Root2 767 modèles de géométrie visible (lampes/accessoires), à distinguer des 624 vraies lumières recensées dans les sidecars et à traiter par pipeline dédié; Root13 588 pièges/spikes/eau/boue; Root17 255 décals géométriques (256 entrées sidecar); Root9 74 props; Root8 4 placements eau. Autres interactifs: Root10=3 portes, Root11=7 portes Collector, Root12=5 cabinets/alarme, Root14=5 grille/plateforme de quête, Root15=1 valve, Root16=6 guntrap/switch; Root0=1 placement ambigu. La somme vaut 1 716.
- Décision actuelle : Roots8–16 restent **hors périmètre de la passe structurelle**; Root2 et Root17 restent en pipelines dédiés; Root0 demande investigation. Après validation manuelle Root4, le prochain choix explicite est : (A) Root2 géométrie + pipeline lumières, (B) matériaux/fallback, ou (C) objets secondaires différés. Les sidecars lumières restent per-level/map-level sans attribution RootId fiable.
- Limites importantes : sept candidats convex Root4 ont été exclus explicitement; les candidats Root9 restent non pris en charge par la politique collision existante. Root17 a 255 placements géométriques contre 256 lignes decals.json.

### Import structurel Root7 et master Labyrinth — 2026-09-20

- Préflight ciblé Root7 `SBG_Labyrinth_Area_02`, fingerprint `a2fa9ad39c0dc867`: 629 actifs LOD0, 93 mesh IDs, 599 blockers parmi 1 189 colliders, zéro candidat convex. Association sidecar/pack vérifiée sans mismatch; contact source avec Root1 confirmé.
- BuildSector PASS en staging : 629 acteurs, 93 meshes, 114 matériaux (77 supportés / 37 fallback), 721 slots, 93 367 triangles; translation error 0, corner error max `0,001772 cm`, IDs/références valides. Map `/Game/Atlas/Sectors/Root_007/L_Atlas_Root007_Area02`.
- Collisions PASS deux fois : 599 composants (595 mesh + 4 box), quatre layer actors, HighPoly exclu; second run idempotent, géométrie 629 et CRC32 `1451072567` inchangés, bounds error max `0,004679 cm` sous 0,25 cm.
- Master `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural` créé puis rechargé/validé : trois références AlwaysLoaded identité vers Root1/3/7, 12 582 acteurs géométriques (10 009+1 944+629), aucun ID dupliqué ni acteur mesh persistant. Contact Root7 instance 20204 / Root1 instance 6230 : overlap `1,362 × 0,986 × 6,814 cm`; bounds auditées avec RelativeTransform sérialisé. L’ancienne adjacent Root003_Root001 et les umaps Root1/Root3 ne sont pas modifiées.
- Synchronisation offline ciblée : Root_007, 321 fichiers / 184 021 579 octets, hash-manifest SHA256 `97DD7765A0BE64EF22FADCCC5B6F3A488C15E7B5890D8F2035FD28ED984584F8`; master 7 435 octets, SHA256 `E6391D50ABA79C3DC35F26F2300C867DC5373298BFA8220C18FDC7D3AB510082`. Manifeste des 2 224 fichiers Root1/Root3/Common/ancienne adjacent identique avant/après (`1B38A9A8AD59DC18D6E6C08C49F00F13FCB50D80597A4EB77E53ABC6D09BD6A4`). Editor fermé et locks vérifiés; aucune backup nécessaire, la cible master était absente.
- Rapports dédiés : [Root7 texte](docs/unreal/labyrinth-root7-validation.txt) et [JSON](docs/unreal/labyrinth-root7-validation.json). Statut sync offline PASS; validation MCP/manuelle post-sync en attente. Prochaine map à ouvrir : `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural`.

### Import structurel Root6 + Root5 et master Labyrinth — 2026-09-20

- Préflight ciblé, fingerprint `a2fa9ad39c0dc867`: Root6 `SBG_Labyrinth_Area_01` = 943 actifs LOD0 / 160 meshes / 748 blockers / 1 541 colliders / 0 convex; Root5 `SBG_Labyrinth_Area_04` = 1 445 / 192 / 729 / 1 508 / 0 convex. Contacts AABB Root1 défendables identifiés sans audit global.
- BuildSector staging PASS: Root6 943 géométries, 164 matériaux (113 supportés/51 fallback), 1 002 slots, 121 135 triangles, corner error `0,001853 cm`; Root5 1 445 géométries, 199 matériaux (137/62), 1 507 slots, 143 702 triangles, corner error `0,001423 cm`. Aucune translation, ID dupliqué ou référence manquante.
- Collisions PASS et seconde exécution idempotente: Root6 748 blockers (743 mesh + 5 box), CRC32 `2597788775` stable, bounds max error `0,001601 cm`; Root5 729 (726 + 3), CRC32 `3136661004` stable, bounds max `0,057741 cm`. Détails et contacts UE dans [rapport combiné](docs/unreal/labyrinth-root5-root6-validation.txt) et [JSON](docs/unreal/labyrinth-root5-root6-validation.json).
- Commandlet d’assembly généralisé une fois pour une liste explicite de maps/counts/reports/contacts; la voie legacy reste rétrocompatible. BuildPlugin Package17 Editor Development/Game Development/Shipping PASS. Master `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural` créé puis rechargé/validé: 5 refs identité Root1/3/7/6/5, 14 970 géométries, aucun ID dupliqué ni StaticMeshActor persistant; bounds fondées sur RelativeTransform sérialisé et 4 contacts Root1 vérifiés.
- Synchronisation offline ciblée uniquement sur Root_005 (568 fichiers, 244 962 623 octets, SHA-manifest `43F6E6425726E5B4C88BC2AF34AB0CAAC197285644A43DC0428B05F92257BECD`), Root_006 (482, 248 981 795, `EB12227B70AAC995BCED76079F57D5F0F8D2A5EDC1189E520DBF6788E2FDB38B`) et master (`E4990E464C811F28B464B59AA92886243E7E3CB1E8EA667C6F611C422197ECB8`). Backup daté master précédent `Aether/sector_collision_stage/Backups/StructuralMaster_PreRoot5Root6_20260920_214454` (SHA256 `E6391D50ABA79C3DC35F26F2300C867DC5373298BFA8220C18FDC7D3AB510082`). Manifests Root1/3/7/Common/ancienne adjacent égaux avant/après; aucun process Unreal pendant sync.
- Statut offline PASS; validation MCP/manuelle post-sync en attente. Prochaine map à ouvrir: `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural`.

### Import structurel Root4 et master Labyrinth — 2026-09-20

- Préflight ciblé fingerprint `a2fa9ad39c0dc867`: Root4 `SBG_Labyrinth_Area_05`, level 549, 2 413 actifs LOD0 / 204 mesh IDs / 544 blockers / 1 131 colliders; les sept candidats convex sont identifiés individuellement. Association source/pack vérifiée. Contact Root4 instance 15564 ↔ Root1 instance 9425: overlap AABB UE `49,844 × 47,291 × 1,061 cm`.
- BuildSector staging PASS: 2 413 acteurs, 2 570 records (157 higher LOD exclus), 2 630 slots validés, 221 236 triangles, 243 matériaux (147 supportés / 96 fallback); translation erreur 0, corner error `0,002720 cm`, IDs dupliqués/références manquantes/anomalies 0. Bounds dans le rapport dédié.
- Collisions PASS deux fois: sélection supportée explicite de 537 blockers (531 mesh + 6 boxes), 132 assets, 4 layer actors; 7 meshes convex `collider__549_7_637.obj`/packedMeshId 850 exclus par opt-in Root4 seulement, avec positions et dette documentées. Second run idempotent: 4 acteurs/537 composants remplacés, géométrie 2 413 et CRC32 `1889731938` inchangés; bounds max `0,003160 cm`.
- Master `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural` créé et reload `-ValidateOnly` PASS: 6 refs identité Root1/3/7/6/5/4, total 17 383, zéro ID dupliqué/acteur mesh persistant. Bounds reportées et contact Root4↔Root1 vérifiés. Aucune umap de secteur ni ancienne adjacent modifiée.
- Sync offline PASS limitée à Root_004 (641 fichiers, 352 413 810 octets, manifest SHA256 `805122CA0CDA5E85BE9CB26ABAA56C807F305E9F8401BA91C5CF5A3012881142`) et master (8 518 octets, SHA256 `769DCF5F733EA45908D8E267BC6AA23D3FA3305492EEC4658B320FE1121754BE`); backup du master précédent datée, protégés Root1/3/5/6/7/Common/ancienne adjacent inchangés (3 595 fichiers, SHA256 `D9482343822F5035D28D2F6AF17228D5BD855CE8B08B73329AEABCFD0501CDB1`). Unreal fermé, lock probe PASS.
- L’utilisateur rapporte PASS pour ses validations manuelles Root5, Root6 et master; Root4 reste à valider manuellement, en particulier les sept zones collision convex prioritaires. Statut sync offline PASS / MCP manuel pending. Rapport: [Root4 texte](docs/unreal/labyrinth-root4-validation.txt) et [JSON](docs/unreal/labyrinth-root4-validation.json). Map exacte à ouvrir: `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural`.
