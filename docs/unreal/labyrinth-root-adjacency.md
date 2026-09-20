# Root3 – sélection du secteur adjacent

Date : 2026-09-20. L'audit lit `labyrinth.eftpack`, sélectionne les instances actives LOD0 et transforme les huit coins des AABB des meshes LOD0 par la matrice affine de chaque instance. Distances en mètres Atlas; la permutation d'axes UE ne change pas la distance euclidienne. Rapport machine staging : `sector_collision_stage/Root3CollisionValidationHost/Saved/Root3AdjacencyAudit.json`.

## Décision

Le premier voisin architectural est `RootId=1`, `SBG_Labyrinth_Area_06`, avec 10 009 instances actives LOD0 et 522 meshes uniques. Son AABB agrégée touche celle de Root3; 462 paires d'AABB sont à moins de 0,5 m (26 contacts face, 2 contacts arête/coin, 55 recouvrements, 379 petits écarts). La sélection s'appuie sur les bounds, pas le numéro de RootId.

Paire structurelle qui fournit une preuve de raccord local :

| Root3 | Root1 | Relation AABB | Recouvrement (m) |
|---|---|---|---|
| instance 14303, mesh 841, `Bunker_Door_B_01_R_220-110_LOD0` | instance 6257, mesh 224, `Pipe_support_wall_01_A_LOD0` | recouvrement, distance 0 | X 0,100000; Y 0,022000; Z 1,170001 |

Cela prouve un contact local des bounds de géométrie transformés, pas la navigabilité du passage.

## Comparaison spatiale

| Root | Nom | Instances LOD0 | Meshes | Distance AABB agrégée (m) | Distance minimum entre instances (m) |
|---:|---|---:|---:|---:|---:|
| 1 | SBG_Labyrinth_Area_06 | 10 009 | 522 | 0 | 0 |
| 2 | SBG_Labyrinth_Light | 767 | 54 | 0 | 0 |
| 9 | SBG_Labyrinth_DesignStuff | 74 | 21 | 0 | 0 |
| 10 | INTERACTIVE_Door_02 | 3 | 3 | 0 | 0 |
| 17 | DECALS_PROJECTED | 255 | 255 | 0 | 0 |
| 7 | SBG_Labyrinth_Area_02 | 629 | 93 | 11,92 | 13,70 |
| 13 | INTERACTIVE_Spikes_traps | 588 | 6 | 6,95 | 20,72 |
| 12 | INTERACTIVE_Boss_Alarm | 5 | 1 | 14,49 | 23,29 |
| 15 | INTERACTIVE_Valve | 1 | 1 | 29,75 | 29,93 |
| 4 | SBG_Labyrinth_Area_05 | 2 413 | 204 | 40,59 | 43,35 |

Root2, Root9/10 et Root17 comprennent surtout des lights, interactifs, décor ou decals : leur proximité brute n'en fait pas le prochain secteur architectural à assembler. Root7 est la racine architecturale suivante la plus proche, mais son instance la plus proche reste à 13,70 m.

## Assemblage offline

`/Game/Atlas/Sectors/Assemblies/L_Atlas_Root003_Root001_Adjacent` est une map de contrôle UE5 créée par `AtlasEftBuildSectorAssembly`: deux références `ULevelStreamingAlwaysLoaded`, Root3 et Root1, à transform identité. Aucun acteur mesh n'est copié dans le niveau persistant. Le reload `-ValidateOnly` staging a réussi: Root3 1 944 acteurs/IDs, Root1 10 009, aucun ID commun.

La Root3 staging utilisée pour ce contrôle a le hash DCC7…123D82C, tandis que la `.umap` Root3 live préexistante a le hash 4CEF…EEF5C. Le live Root3 a été préservé; l'assembly ne stocke qu'un chemin de package. Le reload headless a donc vérifié les références contre les sous-maps du staging, pas le contenu courant du Root3 live. L'ouverture de l'assembly dans MyProject et sa validation MCP restent requises.

### Vérification de bounds/corners après reload de packages

Un premier essai basé sur `GetComponentTransform()` et même `AActor::GetActorTransform()` après `LoadPackage` sans enregistrer le world retournait une cache transform invalide, manifestée par des bounds différentes des deux rapports sector. Il n'a pas été utilisé pour conclure sur la jonction. Le validateur final Package15 exige que le composant StaticMesh soit le root (invariant des StaticMeshActors produits), utilise son `RelativeTransform` sérialisé, transforme les 8 coins de son AABB LOD0, puis compare les bounds agrégées aux rapports BuildSector à 0,25 cm. Bounds finales: Root3 `(-2287.021,-5346.584,-1039.593)..(-629.743,-2591.222,1478.870)` cm; Root1 `(-3383.764,-4467.952,-472.591)..(6545.657,2547.392,739.713)` cm.

Le contact précisément audité reste présent dans les transforms UE: Root3 door instance 14303 / Root1 pipe-support instance 6257 ont AABB overlap `(117.0001, 10.0000, 2.2000)` cm sur les axes UE X/Y/Z, au-dessus du minimum exigé de 0,5 cm/axe. La map assembly live finale fait 7 118 octets, SHA256 `4ADC4028…D5E04C7`; l'ancienne assembly live est sauvegardée datée. Root3 live continue d'être préservée avec son hash `4CEF…EEF5C`, distinct de la Root3 staging testée `DCC7…123D82C`; le MCP live reste à faire.
