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
    static bool Audit(const FString& PackDirectory, FAuditReport& OutReport);
};
} // namespace AtlasEft
