#pragma once

#include "CoreMinimal.h"
#include "AtlasEftPackTypes.h"

namespace AtlasEft
{
/** Read-only, manifest-driven parser and integrity auditor. Creates no UObjects or assets. */
class ATLASEFTRUNTIME_API FPackReader
{
public:
    static bool ReadManifest(const FString& PackDirectory, FManifest& OutManifest, FAuditReport& Report);
    /** Decode one render mesh without applying coordinate conversion or changing triangle winding. */
    static bool ReadMesh(
        const FString& PackDirectory,
        const FManifest& Manifest,
        uint32 MeshId,
        FDecodedMesh& OutMesh,
        FAuditReport& Report);
    /** Parse materials.json and retain every texture-bearing nested block with an explicit semantic. */
    static bool ReadMaterials(
        const FString& PackDirectory,
        const FManifest& Manifest,
        TArray<FMaterialDesc>& OutMaterials,
        FAuditReport& Report);
    /** Decode all fixed-stride instance records while preserving the raw affine matrix. */
    static bool ReadInstances(
        const FString& PackDirectory,
        const FManifest& Manifest,
        TArray<FInstanceDesc>& OutInstances,
        FAuditReport& Report);
    /** Decode manifest-layout collider records without applying coordinate conversion. */
    static bool ReadColliders(
        const FString& PackDirectory,
        const FManifest& Manifest,
        TArray<FColliderDesc>& OutColliders,
        FAuditReport& Report);
    /** Decode one positions/indices-only collider mesh by its manifest id. */
    static bool ReadColliderMesh(
        const FString& PackDirectory,
        const FManifest& Manifest,
        uint32 MeshId,
        FDecodedColliderMesh& OutMesh,
        FAuditReport& Report);
    /** Decode selected collider meshes with one collider_meshes.bin read. */
    static bool ReadColliderMeshes(
        const FString& PackDirectory,
        const FManifest& Manifest,
        const TArray<uint32>& MeshIds,
        TMap<uint32, FDecodedColliderMesh>& OutMeshes,
        FAuditReport& Report);
    static bool Audit(const FString& PackDirectory, FAuditReport& OutReport);
};
} // namespace AtlasEft
