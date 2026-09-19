#pragma once

#include "CoreMinimal.h"
#include "AtlasEftPackTypes.h"

class UStaticMesh;

namespace AtlasEft
{
struct ATLASEFTIMPORTER_API FStaticMeshImportResult
{
    TObjectPtr<UStaticMesh> StaticMesh = nullptr;
    FString ObjectPath;
    FString PackageFilename;
    uint32 SourceVertexCount = 0;
    uint32 RenderVertexCount = 0;
    uint32 RenderTriangleCount = 0;
    uint32 RenderSectionCount = 0;
    uint32 DegenerateSourceTriangleCount = 0;
};

/** Editor-only bridge from one decoded Atlas render mesh to a persistent UStaticMesh asset. */
class ATLASEFTIMPORTER_API FStaticMeshImporter
{
public:
    static bool ImportMesh(
        const FString& PackDirectory,
        uint32 MeshId,
        const FString& DestinationPath,
        FStaticMeshImportResult& OutResult,
        FString& OutError);

    /** Batch-friendly overload: callers parse the manifest once and reuse it for every mesh. */
    static bool ImportMesh(
        const FString& PackDirectory,
        const FManifest& Manifest,
        uint32 MeshId,
        const FString& DestinationPath,
        FStaticMeshImportResult& OutResult,
        FString& OutError);
};
} // namespace AtlasEft
