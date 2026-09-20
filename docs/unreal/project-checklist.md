# Atlas → Unreal Engine 5.8 — Vue globale et checklist projet

Dernière mise à jour : 2026-09-20

Ce document répond à deux questions : **qu'avons-nous réellement validé depuis le début ?** et **que reste-t-il à faire pour obtenir Labyrinthe complète et utilisable dans Unreal Engine 5.8 ?** Il complète le journal détaillé [`progress.md`](../../progress.md), sans le remplacer.

## Objectif final

Importer directement un pack Atlas `.eftpack` dans Unreal Engine 5.8 et obtenir une map Tarkov exploitable : géométrie, textures, matériaux, placements, structure, collisions, lumières et données de scène utiles. Unity, Blender, FBX, glTF et USD ne font pas partie du chemin de production obligatoire.

## Lecture honnête de l'avancement

Le projet a **bien avancé sur les fondations techniques** : le vrai pack Labyrinthe est compris, lu et audité ; le plugin compile ; les conversions spatiales sont testées ; un mesh, des textures, un matériau et un petit niveau sont effectivement créés et contrôlés dans Unreal. Ce travail n'est pas du temps perdu : il réduit fortement le risque d'un import massif faux ou inutilisable.

Depuis l'import de Root4, les six racines structurelles Root1/3/4/5/6/7 totalisent **17 383 / 19 099 placements actifs LOD0, soit 91,0 % des placements** — ce pourcentage ne mesure **ni surface ni volume**. L'architecture principale est considérée provisoirement complète, sous réserve de la validation manuelle Root4 et de la dette des sept collisions convex exclues. Les contenus lumineux, décals et objets secondaires restent des jalons distincts.

Fourchettes indicatives, et non métriques contractuelles :

- infrastructure de l'importeur : environ **35–45 %** ;
- chemin total vers une map complète, structurée et utilisable : environ **15–25 %**.

La couverture structurelle se suit ici par nombre de placements actifs LOD0, pas par estimation de surface/volume; matériaux, lumières, décals et objets secondaires sont suivis séparément.

## Légende

- ✅ **Validé** : exécuté sur le vrai pack Labyrinthe et contrôlé.
- 🟡 **Partiel** : fondation ou échantillon validé, généralisation restante.
- ⬜ **À faire** : pas encore implémenté ou pas encore validé.
- ⏸ **Différé volontairement** : utile plus tard, mais hors chemin critique actuel.
- ⚠️ **Décision/risque** : arbitrage encore nécessaire ou dette connue.

## Checklist par phase

### 1. Extraction Atlas et contrat de données — ✅ Validé

**Acquis**

- Extraction live de `labyrinth` terminée et pack `selfContained` construit avec succès.
- Pack réel audité : 2 124 meshes, 21 734 instances, 1 523 matériaux, 738 textures distinctes, 24 661 colliders, 624 lumières et 18 racines.
- Schémas et conventions essentiels documentés : matrices affines, axes, unités, winding, UV, normal maps et espaces couleur.
- Lacunes connues identifiées : pas de tangentes, pas d'UV secondaire et pas de hiérarchie GameObject complète dans le pack v1.

**Reste**

- Rien de bloquant pour poursuivre le pilote Labyrinthe.
- Étendre Atlas uniquement si une donnée réellement indispensable manque pendant l'import complet.

**Critère de sortie** : atteint — le plugin peut lire le pack sans dépendre de Unity.

### 2. Socle du plugin et validation automatisée — ✅ Validé

**Acquis**

- Plugin UE 5.8 en modules runtime et éditeur.
- Lecteur/auditeur manifest-driven et commandlets headless.
- BuildPlugin Editor Development, Game Development et Game Shipping réussis dans la dernière passe staging complète avant l'ajout du commandlet secteur ; celui-ci a été compilé séparément en Editor Development.
- Cinq tests runtime (coordonnées, affine, meshes, matériaux et instances) ont passé dans la dernière passe staging complète avant l'ajout du commandlet secteur. Ils n'ont pas été relancés avec cette extension.
- Script reproductible `BuildAndTest.ps1` et staging Windows sur `C:`.
- Workflow de contrôle live via MCP documenté.
- Projet Unreal canonique fixé à `D:\EXTRAtor tarkov to UE\unreal project\MyProject` ; `-LiveProjectPath` copie uniquement les 12 assets comparatifs matériau validés vers `Content/Atlas`, préserve le plugin live et les autres assets Atlas, puis lance l'audit depuis le `.uproject` fourni.

**Reste**

- Garder les tests verts à chaque extension.
- Ajouter seulement les tests nécessaires aux nouveaux domaines : LOD, collisions, décals et lumières.

**Critère de sortie** : atteint pour le socle ; maintenance continue.

### 3. Géométrie et création de Static Meshes — 🟡 Partiel

**Acquis**

- Décodage de `meshes.bin` : positions, normales, UV0, vertex colors, indices et sections.
- Conversion Atlas → UE, winding et MikkTSpace appliqués.
- Création persistante de `UStaticMesh`, import unitaire et batch.
- Validation stricte des triangles et sections ; triangles source dégénérés traités explicitement.
- Plusieurs vrais meshes importés, dont le mesh 20 utilisé par le test matériau.

**Reste**

- Importer les 2 124 meshes dans un batch complet et produire un rapport d'échecs/anomalies.
- Reconstituer et valider les associations des 17 017 groupes de LOD.
- Définir les politiques Nanite par catégorie après validation de la scène complète.
- Décider si certains assets exigent une génération d'UV2.

**Critère de sortie** : tous les meshes requis et leurs LOD sont importables de manière idempotente, sans perte de géométrie valide.

### 4. Placements, structure et scène — 🟡 Partiel

**Acquis**

- Décodage strict des 21 734 records d'instances.
- Conversion affine validée sur Labyrinthe.
- Niveau architectural reproductible de 25 acteurs et 18 meshes uniques.
- Erreur maximale de translation mesurée : `0.000000 cm`.
- Contrôle MCP des acteurs, transforms et bounds réussi.

**Reste**

- Secteur par RootId/niveau/ancêtre validé : 53 instances Area_03, 4 meshes ; import code 0 et contrôle MCP/source exacts.
- Dossiers root/niveau/ancêtre/parent et tags record/LOD vérifiés pour le pilote ; la hiérarchie GameObject complète reste absente du pack.
- Importer et vérifier les 21 734 instances.
- Associer les LOD sans multiplier inutilement les acteurs.
- Choisir HISM/ISM/acteurs individuels selon répétition, mobilité et besoins d'édition.
- Vérifier les cas shear/mirror sur d'autres maps ; Labyrinthe n'en contient pas.

**Critère de sortie** : Labyrinthe complète est spatialement cohérente, structurée, réimportable et comparable à la source.

### 5. Textures et matériaux — 🟡 Partiel

**Acquis**

- Lecture typée des 1 523 matériaux et audit des 738 textures sans référence manquante.
- Import contrôlé de trois textures du matériau 17 : albedo, normal et specular/provenance.
- Réglages sRGB, compression et convention DirectX validés.
- Master opaque, material instance 17, affectation au mesh 20 et map verticale automatisés.
- Albedo et UV0 visuellement plausibles dans Unreal.
- Le projet live contient un seul master `M_AtlasOpaque`, partagé par les MI 17 et 974 ; MCP confirme les deux associations mesh/MI et les paramètres attendus.
- Second matériau opaque 974/mesh 1240 reconstruit par le pipeline staging avec la MI partagée, roughness `0.9`, metallic `0`, normal scale `1`, sans surcharge de specMap.
- Dernière passe staging complète avant l'ajout du commandlet secteur : BuildPlugin Editor Development, Game Development et Game Shipping ; audit, imports échantillons/batch, scène architecturale de 25 instances, deux maps matériau et cinq tests runtime réussis. Le commandlet secteur a depuis été compilé séparément en Editor Development et validé avec son audit ciblé ; les builds Game et les cinq tests n'ont pas été relancés avec cette extension.
- Les deux maps matériau générées ont passé les assertions structurelles du rig headless : une Directional Light, une Rect Light, intensités et mobilités attendues, Post Process manuel, backdrop Engine, caméra relative aux bounds et pré-calcul désactivé. Aucun asset généré n'a été synchronisé vers le Content live.
- 🟡 La map live 974/1240 a été inspectée en lecture seule après correction manuelle : rig mieux cadré et albedo perceptible, mais éclairage sombre ; la scène n'a été ni enregistrée ni remplacée. Cette inspection ne valide pas visuellement la réponse de normale ou de roughness.

**Reste**

- Inspecter visuellement les maps matériau générées sous l'éclairage headless reproductible ; conclure sur normale et roughness uniquement après un contrôle lisible.
- Inventorier et implémenter les autres signatures : cutout, transparent, emissive, vertex-paint et variantes réellement présentes.
- Générer les 1 523 material instances avec déduplication des 738 textures.
- Appliquer automatiquement les matériaux à tous les slots de meshes.
- Produire un rapport des paramètres ou textures non pris en charge.

**Critère de sortie** : chaque signature présente dans Labyrinthe possède un shader validé, puis tous les matériaux sont générés et assignés sans référence manquante.

### 6. UV de lightmap et stratégie d'éclairage — ⚠️ Décision ouverte, non bloquante maintenant

Après ajout manuel d'un spotlight et `Build Lighting`, Unreal a signalé sur `SM_Atlas_0020_model` :

> Object has overlapping UVs. Lightmap UV are overlapping by 1.6%.

Le pack Atlas v1 ne fournit pas d'UV secondaire dédié aux lightmaps. Cet avertissement concerne donc les **UV de lightmap/UV2**, pas les UV0 qui pilotent l'albedo ; le rendu texturé du clavier reste plausible. L'avertissement est **connu mais non corrigé**, et **ne bloque pas le pipeline actuel**.

**Reste / décision**

- Formaliser avant l'import complet si la cible principale utilise Lumen et des lumières dynamiques, ce qui constitue probablement le chemin le plus court.
- Si des lightmaps statiques sont réellement requises, générer et valider des UV2 non chevauchants, idéalement de façon automatisée et sélective.
- Ne pas générer des UV2 pour 2 124 meshes avant d'avoir confirmé ce besoin de production.

**Critère de sortie** : stratégie Lumen/dynamique ou lightmaps statiques explicitement choisie, avec validation adaptée.

### 7. Collisions — 🟡 Synchronisé offline, validation MCP en attente

**Acquis**

- Les 24 661 lignes pack/sidecar ont été associées exactement; RootId 3 se rattache sans ambiguïté à `SBG_Labyrinth_Area_03`, niveau 547.
- Root3 compte 3 407 colliders (1 617 LowPoly, 1 705 HighPoly, 80 Transparent, 3 DoorLowPoly, 2 Interactive). Le set de bloqueurs exclut HighPoly et triggers, et garde les deux boxes Interactive non-trigger : 1 702 composants (1 700 mesh + 2 box), 169 mesh IDs uniques et 4 acteurs de couche.
- `AtlasEftAddSectorCollisions` passe en build UE 5.8 Editor/Development/Shipping et deux exécutions staging; la seconde remplace exactement les quatre acteurs / 1 702 composants gérés précédents. Mesh assets partagés par meshId, collision mesh Complex-as-Simple; composants statiques, invisibles, sans ombres, overlap ou impact NavMesh, bloquant Pawn et WorldStatic.
- Voir le [rapport Root3 collisions](labyrinth-root3-collision-validation.txt) et son [résumé JSON](labyrinth-root3-collision-validation.json).
- Synchronisation offline ciblée achevée après fermeture de l'éditeur : le `.umap` live est exactement celui du Package12 staging PASS (16 073 277 octets; SHA256 `DCC7E3DF957B73F281CC9B7B7BD9B0197927F9F152E842296F225347F123D82C`) et les 169 assets de `CollisionMeshes` ont tous le même SHA256 que le staging. Seuls ce `.umap` et ces 169 assets Root_003 ont été remplacés. Une backup datée du `.umap`, des 169 anciens assets et du manifeste SHA256 est conservée sous `sector_collision_stage/LiveOriginalBackup/Root003_LiveBackup_20260920_165631`.
- Le post-copy est vérifié par hashes/fichiers, pas encore via une map chargée : aucun éditeur n'a été lancé après la copie. Les 1 944 acteurs géométriques et zéro collision actor sont les derniers compteurs MCP **avant** synchronisation, pas des mesures post-sync.

**Reste**

- Réouvrir le projet existant `MyProject.uproject` et la map `/Game/Atlas/Sectors/Root_003/L_Atlas_Root003_Area03`; via MCP revalider 1 944 acteurs géométriques et quatre acteurs / 1 702 composants collision.
- Tester manuellement Collision Visualization et les traces Pawn / WorldStatic sur un mesh collider et les deux boxes; vérifier l'invisibilité de rendu. Bounds staging max error `0.068625 cm` (tolérance `0.25 cm`), mais le chargement et comportement live/Pawn ne sont pas encore testés.
- Les spheres/capsules n'apparaissent pas dans le set sélectionné Root3; le présent pack ne couvre ici que les deux boxes et les mesh colliders sélectionnés.

**Critère de sortie** : package live chargé, compteurs/geometry confirmés via MCP après reload, puis traces Pawn / WorldStatic réussies sans rendu visible ni coût prohibitif.

### 8. Lumières, décals et données secondaires — ⬜ À faire

**Acquis**

- 624 lumières et les fichiers de décals, navigation et volumes sont présents et audités.

**Reste**

- Importer les lumières et convertir intensités, couleurs, portées et orientations.
- Reconstituer les décals et leurs matériaux.
- Évaluer volumes et données SH disponibles.
- Décider ce qui est utile parmi navigation Atlas, données Tarkmap et données de gameplay.

**Critère de sortie** : la scène possède ses éléments visuels et fonctionnels principaux, avec les données secondaires retenues explicitement.

### 9. Organisation grande échelle et performances — ⬜ À faire

**Reste**

- Configurer World Partition et Data Layers selon la structure réelle de Labyrinthe.
- Définir HISM/ISM, Nanite et HLOD après mesure, pas par défaut aveugle.
- Tester temps d'import, taille disque, temps de chargement, mémoire, draw calls et FPS.
- Garantir reimport partiel et chemins d'assets stables.

**Critère de sortie** : la map complète charge, s'édite et s'exécute avec des performances acceptables sur la cible choisie.

### 10. Validation finale et industrialisation — ⬜ À faire

**Reste**

- Comparer Atlas/source et UE par secteurs : placements, silhouettes, matériaux, lumières et collisions.
- Produire un rapport automatique de complétude et d'erreurs.
- Tester une extraction/importation propre depuis zéro.
- Étendre ensuite le pipeline à une map plus grande sans modifier manuellement les assets générés.

**Critère de sortie** : Labyrinthe est reproductible de bout en bout, vérifiée, documentée et utilisable ; aucune étape Unity ou Blender obligatoire.

## Chemin critique recommandé

### Maintenant

1. ✅ Groupe pilote `(Root 3, niveau 547, ancêtre 9787)` importé (53 instances) et carte dédiée du RootId 3 complet actif/LOD0 importée (1 944 acteurs).
2. 🟡 Capture MCP globale : forme architecturale reconnaissable ; fallback magenta important et absence d’éclairage de scène, donc fidélité visuelle des matériaux encore partielle.
3. 🟡 Collisions Root3 PASS en staging et synchronisées offline; réouverture MCP et traces physiques à faire.
4. 🟡 Root1, voisin architectural déterminé par bounds LOD0, importé avec collisions; assembly Root3+Root1 synchronisée offline. Validation MCP live requise.
5. ⬜ Étendre seulement après validation MCP/physique de cette jonction ; éclairage de contrôle dynamique sans lightmaps ni UV2.

### Ensuite

5. Couvrir les signatures de matériaux réellement présentes, puis lancer un batch textures/matériaux avec rapport.
6. Lancer un batch complet des meshes et LOD avec rapport d'anomalies.
7. Compléter la validation du secteur déjà assemblé : associations LOD, collisions, lumières source et comparaison des résultats.
8. Couvrir les signatures de matériaux pertinentes sur des secteurs supplémentaires avec un rapport d'anomalies.
9. Étendre à toute Labyrinthe seulement après que ce secteur vertical est fiable.

### Plus tard

10. Décals, volumes, navigation et données de gameplay selon la cible produit.
11. World Partition, HISM/HLOD et optimisations guidées par les mesures.
12. Support des grandes maps et éventuelles extensions du format Atlas.

## Ce qu'il ne faut pas approfondir maintenant

- Ne pas corriger manuellement les UV de lightmap du mesh 20 : ce changement ne serait ni reproductible ni représentatif.
- Ne pas générer des UV2 sur tous les meshes avant la décision d'éclairage.
- Ne pas lancer le batch global des 738 textures et 1 523 matériaux tant que les deux verticales matériau n'ont pas passé un contrôle visuel lisible et que les signatures non opaques pertinentes ne sont pas inventoriées.
- Ne pas optimiser prématurément World Partition, Nanite, HISM ou HLOD sur le petit secteur pilote de 53 instances ; attendre une scène plus représentative et mesurer les performances.
- Ne pas viser une reproduction parfaite des données secondaires avant d'avoir un secteur complet géométrie + matériaux + structure.
- Ne pas étendre Atlas pour restaurer toute la hiérarchie GameObject sans preuve qu'elle est indispensable à l'usage final.

## Décisions ouvertes

- Éclairage cible : Lumen/dynamique, statique lightmaps, ou hybride.
- Niveau de fidélité requis pour lumières, décals, navigation et données de gameplay.
- Politique LOD/Nanite par catégorie de mesh.
- Structure UE cible : acteurs, ISM/HISM, Level Instances, Data Layers et World Partition.
- Génération UV2 : jamais, sélective ou globale selon la décision d'éclairage.
- Critères de performance et plateforme cible.

## Principaux risques

- Variantes de matériaux non découvertes par l'échantillon 17.
- Mauvaise association LOD ou structure trop plate lors du passage aux 21 734 instances.
- Coût mémoire/disque/draw calls d'une traduction naïve en acteurs et assets uniques.
- Qualité des collisions mesh et coût des 24 078 collider meshes.
- Différences de modèle d'éclairage Unity/Tarkov vers UE/Lumen.
- Données absentes du pack v1 : UV2, tangentes et hiérarchie GameObject complète.

## Verdict actuel

Les exemples opaques 17/20 et 974/1240 ont passé leurs validations structurelles ; leur rendu visuel doit encore être jugé. Le groupe pilote Area_03 de 53 instances reste conservé comme test ciblé. Le RootId 3 complet actif/LOD0 est dans sa map dédiée de MyProject : 1 944 acteurs, 285 meshes, 292 matériaux uniques, avec 215 records LOD supérieurs explicitement exclus. La structure/transforms/slots et les références passent en staging et en MCP live ; une capture globale montre le volume architectural, mais le rendu reste partiel (fallback magenta, pas d’éclairage ajouté). Le pipeline collision Root3 passe en staging/idempotence et son `.umap` plus 169 assets collision ont été synchronisés offline avec hashes exacts Package12 et backup datée. Le niveau n'a pas encore été rouvert après synchronisation : la validation MCP des 1 944 acteurs géométriques + 4 acteurs/1 702 composants et les traces Pawn restent à faire. Lumières source, autres racines, World Partition et optimisation restent hors de ce jalon. Le plugin live n'a pas été remplacé.

## Voisin Root1 et assembly de contrôle — 2026-09-20

- L'audit de bounds géométriques LOD0 désigne RootId 1 `SBG_Labyrinth_Area_06` (10 009 instances, 522 meshes) comme voisin architectural; la paire Root3 instance 14303 / Root1 instance 6257 recouvre 0,100000 × 0,022000 × 1,170001 m. Voir [audit d'adjacence](labyrinth-root-adjacency.md).
- Root1 staging PASS: 10 009 acteurs LOD0, 522 meshes, 10 430 slots, 409 427 triangles, corner error max `0.004075875 cm`; 1 868 higher-LOD exclus, zéro ID dupliqué/référence manquante. Collisions: 6 805 composants (6 788 mesh + 17 boxes), 244 assets partagés, 4 layer actors. Seconde exécution remplace exactement 4 acteurs/6 805 composants, garde 10 009 acteurs et le CRC32 `1970692440`; bounds error max `0.221973 cm` sous la tolérance `0.25 cm`. Voir [validation Root1](labyrinth-root1-collision-validation.txt).
- Assembly `/Game/Atlas/Sectors/Assemblies/L_Atlas_Root003_Root001_Adjacent` PASS en staging: 2 références `ULevelStreamingAlwaysLoaded` identité, 0 acteur mesh persistant, 1 944 + 10 009 géométriques, 0 ID dupliqué; créations répétées et reload `-ValidateOnly` PASS.
- Validateur final assembly Package15: 8 coins des AABB LOD0 transformés par le RelativeTransform sérialisé du root StaticMeshComponent; bornes agrégées concordantes avec Root3/Root1 BuildSector à 0,25 cm. Le contact UE des instances 14303/6257 mesure `117,0001 / 10,0000 / 2,2000 cm` (>0,5 cm par axe). Deux créations et un reload `-ValidateOnly` PASS; le premier essai basé sur la cache transform non initialisée a été rejeté.
- Sync offline limitée à Root1 (1 331 fichiers, SHA-256 1 331/1 331) et l'assembly map (7 118 octets, hash `4ADC4028…D5E04C7`). Root1 inchangée; Root3 live non touchée: SHA256 `4CEF41B2…EEF5C`, différent du Root3 staging `DCC7E3DF…123D82C`. L'ancienne assembly live (`4E63A342…4B00B59`) a été sauvegardée datée sous `Aether/sector_collision_stage/Root1AdjacencyStageHost/Saved/Backups/LiveAssemblyBoundsFix`. État MCP pending. Prochain test: rouvrir MyProject, charger l'assembly, confirmer niveaux/counts/transforms et tester traces Pawn/WorldStatic sur mesh Root1 et box Root3. Rapports: [collisions](labyrinth-root1-collision-validation.json), [assembly](labyrinth-root3-root1-assembly-validation.txt).

## Validation du secteur du 2026-09-20

- Audit reproductible des 18 racines ; le groupe complet `(RootId=3, Level=547, GrandparentId=9787)` couvre 53 instances Area_03, 4 meshes, 9 matériaux opaques pris en charge et 13 textures, sans voisinage spatial ni troncature. Voir [l'audit des racines](labyrinth-root-audit.md), le [rapport d'import](labyrinth-sector-validation.txt) et le [rapport MCP](labyrinth-sector-mcp.json).
- Import RootId 3 complet actif/LOD0 réussi : `2 159 bruts − 215 LOD supérieurs − 0 inactif = 1 944 acteurs`, 285 meshes, 292 matériaux (229 supportés / 63 fallback), 200 chemins texture, 2 150 slots instanciés (1 754 supportés / 396 fallback), 241 409 triangles. Bounds UE vérifiées en MCP : min `(-2287.021, -5346.584, -1039.593)`, max `(-629.743, -2591.222, 1478.870)` cm. Erreur corners max `0.135975894 cm` sur instance 13817/mesh763 (tolérance finale 0,15 cm) ; translation 0, doublons/références/anomalies 0.
- Map live `/Game/Atlas/Sectors/Root_003/L_Atlas_Root003_Area03`, sous-dossier neuf : 722 sorties, 376 985 008 octets, tous les SHA-256 comparés au staging ; MCP indexe les 722 assets; master partagé et autres dossiers Atlas non copiés/modifiés. Rapport reproductible [JSON](labyrinth-root3-validation.json) + [texte détaillé](labyrinth-root3-validation.txt). Capture MCP : silhouette et plateformes visibles, grands plans magenta de fallback et scène sombre sans lumières source.
- Import ciblé réussi en Editor Development : code 0, 53 acteurs, 121 slots et 760 triangles sur les meshes uniques ; erreurs translation/corners `0.000000000 cm`. Le test négatif `ExpectedCount=54` échoue avant la création d'assets.
- La map est chargée dans MyProject et contrôlée dans Unreal/MCP : 53 identifiants, transforms, bounds, meshes, MI et références textures comparés ; aucun override parasite. Les 27 nouveaux assets ont été copiés et leurs SHA256 vérifiés ; le plugin live et les anciennes maps ont été préservés.
- Le contrôle visuel reconnaît les escaliers et paliers, mais l'image reste sombre : géométrie et structure validées, réponse visuelle des matériaux partielle. LOD, collisions, lumières source, fallback sur un asset réel et les tests runtime/Game du nouveau code restent à faire.
- Le pipeline de collisions Root3 est décrit dans [son rapport](labyrinth-root3-collision-validation.txt) : BuildPlugin Package12 Editor/Development/Shipping et commandlet staging PASS; 1 702 composants / 169 assets / 4 acteurs, second run idempotent, géométrie inchangée (1 944, CRC32 `4195649602`), bounds max error `0.068625 cm` sur tolérance `0.25 cm`. Après exploration MCP editor-native sans mutation (pas d'opération bulk/transaction), la map et les 169 assets ont été synchronisés offline au Package12 exact. Hash du `.umap` live=staging `DCC7E3DF…123D82C`; assets 169/169 SHA256 identiques; backup datée conservée. Le prochain test exact est de rouvrir uniquement Root3, confirmer en MCP les 1 944 acteurs géométriques + 4 acteurs/1 702 composants collision, puis tracer Pawn/WorldStatic sur un mesh et les deux boxes.
## Classification des 16 racines Labyrinthe restantes — 2026-09-20

- Root1 Player Collision : **PASS (confirmation manuelle rapportée par l’utilisateur)**.
- Alignement de la porte/jonction Root1↔Root3 : **PASS (confirmation manuelle rapportée par l’utilisateur)**. Cette mention ne remplace pas les traces MCP post-sync de Root3, qui restent à faire.
- Après Root4, les six racines structurelles Root1/3/4/5/6/7 totalisent **17 383 / 19 099 placements actifs LOD0 = 91,0 % des placements**, pas 91 % de surface ou de volume. L'architecture principale est provisoirement complète, en attente de la validation manuelle Root4 et du contrôle des sept zones de collision convex explicitement exclues.
- Les **1 716 placements actifs LOD0 restants** : Root2 767 modèles visibles lampe/accessoire (à distinguer des 624 vraies lumières sidecar, pipeline dédié); Root13 588 placements spikes/traps/eau/boue; Root17 255 décals géométriques (256 entrées sidecar); Root9 74 props; Root8 4 eau; autres interactifs Root10=3 portes, Root11=7 portes Collector, Root12=5 cabinets/alarme, Root14=5 grille/plateforme de quête, Root15=1 valve et Root16=6 guntrap/switch (27 au total); Root0=1 cas ambigu. Somme totale: 1 716. Voir le [rapport de classification](labyrinth-remaining-roots-classification.md) et le [JSON reproductible](labyrinth-remaining-roots-classification.json).
- Décision de périmètre : Roots8–16 sont **hors périmètre structurel actuel**; Root2 et Root17 sont différées vers leurs pipelines dédiés; Root0 doit être investiguée. Après validation manuelle Root4, demander le choix suivant : (A) Root2 géométrie + pipeline lumières, (B) matériaux/fallback, ou (C) objets secondaires différés. Root4 reste en contrôle manuel utilisateur; aucun PASS manuel n'est attribué ici.
- Le rapport de classification compare 24 661 colliders sidecar/pack. Root4 a 7 candidats convex explicitement exclus et documentés comme dette collision; les candidats convex Root9 restent une décision ultérieure.

## Root7 Area_02 — 2026-09-20

- Import actif/LOD0 PASS : 629 acteurs / 688 records, 59 LOD supérieurs exclus; 93 meshes, 114 matériaux (77 pris en charge / 37 fallback), 721 slots et 93 367 triangles. Erreur translation 0, coins max `0,001772 cm`, aucun ID dupliqué/référence manquante. Voir le [rapport Root7](labyrinth-root7-validation.txt) et son [JSON](labyrinth-root7-validation.json).
- Collisions PASS : 1 189 colliders source; 599 blockers (595 mesh + 4 boxes), zéro candidat convex. La seconde exécution remplace exactement 4 acteurs/599 composants; CRC32 géométrie inchangé `1451072567`, erreur bounds max `0,004679 cm` (<0,25 cm).
- Master `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural` PASS après création et reload : refs identité Root1/Root3/Root7, 10 009+1 944+629 = 12 582 géométries, zéro ID dupliqué/acteur mesh persistant. Contact Root7 porte 20204 / Root1 support 6230 : `1,362 × 0,986 × 6,814 cm`; bounds calculées depuis les transforms sérialisés.
- Sync offline limitée à `Root_007` (321 fichiers) et au master (7 435 octets), hashes staging/live identiques. Root1, Root3, Common et l’ancienne adjacent restent inchangés. Unreal fermé; validation MCP/manuelle post-sync en attente.
- Prochaine map à ouvrir : `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural`.

## Root6 Area_01 + Root5 Area_04 — 2026-09-20

- Imports actifs/LOD0 PASS : Root6 `SBG_Labyrinth_Area_01` = 943 acteurs / 160 meshes; Root5 `SBG_Labyrinth_Area_04` = 1 445 acteurs / 192 meshes. Transforms, slots, bounds, IDs et références validés; voir le [rapport combiné](labyrinth-root5-root6-validation.txt) et son [JSON](labyrinth-root5-root6-validation.json).
- Collisions PASS avec seconde exécution idempotente : Root6 748 composants / CRC32 `2597788775`; Root5 729 / CRC32 `3136661004`. Bounds errors max respectifs `0,001601 cm` et `0,057741 cm`, sous 0,25 cm; aucune convexité candidate.
- Master structural stable PASS: 5 refs identité Root1/3/7/6/5, total 14 970 géométries, zéro ID dupliqué/acteur mesh persistant. Les contacts Root6↔Root1 et Root5↔Root1 sont validés avec bounds sérialisées; ancienne map adjacent préservée.
- Sync offline limitée aux dossiers Root_005/Root_006 et master; backup daté de l’ancien master, hashes staging/live identiques et manifests protégés Root1/3/7/Common/ancienne adjacent égaux avant/après. Validation MCP/manuelle en attente.
- Prochaine map à ouvrir : `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural`.

## Root4 Area_05 — 2026-09-20

- Préflight ciblé PASS, fingerprint `a2fa9ad39c0dc867`: `SBG_Labyrinth_Area_05`, level 549, 2 413 actifs LOD0 / 204 mesh IDs; 544 blockers attendus parmi 1 131 colliders. Contact Root4 instance 15564 ↔ Root1 instance 9425, overlap AABB UE `49,844 × 47,291 × 1,061 cm` (seuil 0,5 cm/axe).
- BuildSector PASS: 2 413 acteurs, 2 570 records dont 157 LOD supérieurs écartés; 243 matériaux (147 pris en charge / 96 fallback), 2 630 slots validés, 221 236 triangles; erreurs translation `0 cm`, coins `0,002720 cm`, IDs/références/anomalies `0`. Bounds min `(310,539; 1357,597; -611,467)` / max `(6335,008; 5314,348; 1265,178) cm`.
- Collisions PASS deux fois et idempotentes: 537 composants pris en charge (531 mesh + 6 boxes), 132 assets partagés, 4 acteurs de couche; CRC32 géométrie `1889731938` inchangé, erreur bounds max `0,003160 cm` sous `0,25 cm`. Les 7 convex sont exclus uniquement avec `-AllowUnsupportedConvexExclusion`; sans ce flag, la politique reste l’échec explicite. Le report détaille chaque ligne, emplacement, couche, mesh et raison. Revue Player Collision manuelle prioritaire requise dans ces zones.
- Master `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural` créé puis rechargé/validé: 6 références identité Root1/3/7/6/5/4, 17 383 géométries, zéro ID dupliqué/acteur mesh persistant; bounds concordantes avec les rapports secteurs et cinq contacts Root1 valides. Ancienne map adjacent et umaps Root1/Root3 non touchées.
- Sync offline limitée à Root_004 (641 fichiers, 352 413 810 octets, SHA-manifest `805122CA0CDA5E85BE9CB26ABAA56C807F305E9F8401BA91C5CF5A3012881142`) et master (8 518 octets, SHA256 `769DCF5F733EA45908D8E267BC6AA23D3FA3305492EEC4658B320FE1121754BE`). Master précédent sauvegardé sous `Aether/sector_collision_stage/Backups/StructuralMaster_PreRoot4_20260920_222327` (SHA256 `E4990E464C811F28B464B59AA92886243E7E3CB1E8EA667C6F611C422197ECB8`). Manifest protégé Root1/3/5/6/7/Common/ancienne adjacent inchangé avant/après: 3 595 fichiers, SHA256 `D9482343822F5035D28D2F6AF17228D5BD855CE8B08B73329AEABCFD0501CDB1`.
- Root5, Root6 et le master: validation manuelle PASS rapportée par l’utilisateur. Root4: validation manuelle post-sync et Player Collision des sept zones convex restent pending; statut offline/MCP pending.
- Rapport dédié: [Root4 texte](labyrinth-root4-validation.txt) et [JSON](labyrinth-root4-validation.json). Prochaine map à ouvrir: `/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural`.
