# Atlas → Unreal Engine 5.8 — Vue globale et checklist projet

Dernière mise à jour : 2026-09-19

Ce document répond à deux questions : **qu'avons-nous réellement validé depuis le début ?** et **que reste-t-il à faire pour obtenir Labyrinthe complète et utilisable dans Unreal Engine 5.8 ?** Il complète le journal détaillé [`progress.md`](../../progress.md), sans le remplacer.

## Objectif final

Importer directement un pack Atlas `.eftpack` dans Unreal Engine 5.8 et obtenir une map Tarkov exploitable : géométrie, textures, matériaux, placements, structure, collisions, lumières et données de scène utiles. Unity, Blender, FBX, glTF et USD ne font pas partie du chemin de production obligatoire.

## Lecture honnête de l'avancement

Le projet a **bien avancé sur les fondations techniques** : le vrai pack Labyrinthe est compris, lu et audité ; le plugin compile ; les conversions spatiales sont testées ; un mesh, des textures, un matériau et un petit niveau sont effectivement créés et contrôlés dans Unreal. Ce travail n'est pas du temps perdu : il réduit fortement le risque d'un import massif faux ou inutilisable.

En revanche, **la couverture de la map reste faible**. Les validations portent encore sur des échantillons : 25 instances sur 21 734, un matériau reconstruit sur 1 523 et trois textures contrôlées sur 738. La scène complète, les associations LOD, les collisions, les lumières, les décals et l'organisation grande échelle ne sont pas encore importés.

Fourchettes indicatives, et non métriques contractuelles :

- infrastructure de l'importeur : environ **35–45 %** ;
- couverture réelle des contenus de Labyrinthe : environ **1–5 %** ;
- chemin total vers une map complète, structurée et utilisable : environ **15–25 %**.

Ces chiffres servent uniquement à situer le projet. Un import de masse fera monter rapidement la couverture, mais révélera probablement de nouvelles variantes de données.

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
- Build Editor, Development et Shipping validé.
- Cinq tests runtime passent : coordonnées, affine, meshes, matériaux et instances.
- Script reproductible `BuildAndTest.ps1` et staging Windows sur `C:`.
- Workflow de contrôle live via MCP documenté.

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

- Construire un test de scène cohérent par `RootId`/niveau plutôt qu'un simple voisinage spatial.
- Définir la représentation de la structure root/niveau/parenté malgré la hiérarchie GameObject incomplète.
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

**Reste**

- Rendre l'éclairage de contrôle reproductible pour juger normal map et roughness.
- Valider un second matériau opaque représentatif.
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

### 7. Collisions — ⬜ À faire

**Acquis**

- Données auditées : 578 box, 5 spheres et 24 078 mesh colliders ; format documenté.

**Reste**

- Décoder et importer box, sphere et collider meshes.
- Définir simple collision, complex-as-simple et collision channels selon les catégories.
- Contrôler performances et alignement sur un secteur, puis sur la map.

**Critère de sortie** : navigation physique et collisions principales cohérentes sans coût prohibitif.

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

1. Rendre reproductible l'éclairage de la map de contrôle du matériau 17 et conclure sur normal map/roughness.
2. Valider un second matériau opaque représentatif.
3. Construire une scène pilote cohérente par `RootId`/niveau et vérifier structure, slots de matériaux et LOD.
4. Formaliser le choix **Lumen/dynamique versus lightmaps statiques** avant tout traitement UV2 massif.

### Ensuite

5. Couvrir les signatures de matériaux réellement présentes, puis lancer un batch textures/matériaux avec rapport.
6. Lancer un batch complet des meshes et LOD avec rapport d'anomalies.
7. Assembler une racine ou un secteur complet avec matériaux, puis valider visuellement et quantitativement.
8. Ajouter les collisions et lumières sur ce même secteur.
9. Étendre à toute Labyrinthe seulement après que ce secteur vertical est fiable.

### Plus tard

10. Décals, volumes, navigation et données de gameplay selon la cible produit.
11. World Partition, HISM/HLOD et optimisations guidées par les mesures.
12. Support des grandes maps et éventuelles extensions du format Atlas.

## Ce qu'il ne faut pas approfondir maintenant

- Ne pas corriger manuellement les UV de lightmap du mesh 20 : ce changement ne serait ni reproductible ni représentatif.
- Ne pas générer des UV2 sur tous les meshes avant la décision d'éclairage.
- Ne pas importer immédiatement les 738 textures et 1 523 matériaux tant qu'une deuxième verticale et les principales signatures ne sont pas validées.
- Ne pas optimiser prématurément World Partition, Nanite, HISM ou HLOD sur seulement 25 acteurs.
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

La direction est bonne et les validations effectuées jusqu'ici sont nécessaires. Nous ne sommes pas en train de nous acharner inutilement sur le mesh 20 : il sert de verticale minimale pour découvrir tôt les erreurs de conventions. En revanche, continuer longtemps à polir ce seul asset deviendrait de la sur-ingénierie. Après un éclairage reproductible et un deuxième matériau, la priorité doit basculer vers un **secteur structurel cohérent**, puis vers des batches avec rapports, afin d'augmenter rapidement la couverture réelle de Labyrinthe.
