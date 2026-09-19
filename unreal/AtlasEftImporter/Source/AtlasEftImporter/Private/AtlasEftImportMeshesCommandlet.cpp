#include "AtlasEftImportMeshesCommandlet.h"

#include "AtlasEftPackReader.h"
#include "AtlasEftStaticMeshImporter.h"
#include "Misc/Parse.h"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftImportMeshesCommandlet, Log, All);

UAtlasEftImportMeshesCommandlet::UAtlasEftImportMeshesCommandlet()
{
    IsClient = false;
    IsEditor = true;
    IsServer = false;
    LogToConsole = true;
    ShowErrorCount = true;
}

int32 UAtlasEftImportMeshesCommandlet::Main(const FString& Params)
{
    FString PackDirectory;
    if (!FParse::Value(*Params, TEXT("Pack="), PackDirectory) || PackDirectory.IsEmpty())
    {
        UE_LOG(LogAtlasEftImportMeshesCommandlet, Error, TEXT("Missing -Pack=<path to map.eftpack directory>."));
        return 2;
    }
    PackDirectory.TrimQuotesInline();

    FString Destination = TEXT("/Game/Atlas/Meshes");
    FParse::Value(*Params, TEXT("Destination="), Destination);
    Destination.TrimQuotesInline();

    int32 FirstMeshId = 0;
    int32 RequestedCount = 10;
    FParse::Value(*Params, TEXT("FirstMeshId="), FirstMeshId);
    FParse::Value(*Params, TEXT("Count="), RequestedCount);
    const bool bAll = FParse::Param(*Params, TEXT("All"));
    if (FirstMeshId < 0 || (!bAll && RequestedCount <= 0))
    {
        UE_LOG(LogAtlasEftImportMeshesCommandlet, Error, TEXT("FirstMeshId must be non-negative and Count must be positive."));
        return 2;
    }

    AtlasEft::FAuditReport Report;
    Report.PackDirectory = PackDirectory;
    AtlasEft::FManifest Manifest;
    if (!AtlasEft::FPackReader::ReadManifest(PackDirectory, Manifest, Report))
    {
        UE_LOG(LogAtlasEftImportMeshesCommandlet, Error, TEXT("%s"), *Report.ToText());
        return 1;
    }
    if (!Manifest.Meshes.IsValidIndex(FirstMeshId))
    {
        UE_LOG(LogAtlasEftImportMeshesCommandlet, Error,
            TEXT("FirstMeshId %d is outside manifest range 0..%d."), FirstMeshId, Manifest.Meshes.Num() - 1);
        return 2;
    }

    const int32 Available = Manifest.Meshes.Num() - FirstMeshId;
    const int32 ImportCount = bAll ? Available : FMath::Min(RequestedCount, Available);
    uint64 SourceVertices = 0;
    uint64 RenderVertices = 0;
    uint64 Triangles = 0;
    uint64 Sections = 0;

    for (int32 Offset = 0; Offset < ImportCount; ++Offset)
    {
        const uint32 MeshId = static_cast<uint32>(FirstMeshId + Offset);
        const uint32 BucketStart = (MeshId / 256u) * 256u;
        const FString BucketPath = FString::Printf(
            TEXT("%s/%04u_%04u"),
            *Destination,
            BucketStart,
            BucketStart + 255u);

        AtlasEft::FStaticMeshImportResult Result;
        FString Error;
        if (!AtlasEft::FStaticMeshImporter::ImportMesh(
            PackDirectory,
            Manifest,
            MeshId,
            BucketPath,
            Result,
            Error))
        {
            UE_LOG(LogAtlasEftImportMeshesCommandlet, Error,
                TEXT("Mesh %u failed after %d successful imports:\n%s"), MeshId, Offset, *Error);
            return 1;
        }

        SourceVertices += Result.SourceVertexCount;
        RenderVertices += Result.RenderVertexCount;
        Triangles += Result.RenderTriangleCount;
        Sections += Result.RenderSectionCount;
        if ((Offset + 1) % 25 == 0 || Offset + 1 == ImportCount)
        {
            UE_LOG(LogAtlasEftImportMeshesCommandlet, Display,
                TEXT("Imported %d/%d meshes (last meshId=%u)."), Offset + 1, ImportCount, MeshId);
        }
    }

    UE_LOG(LogAtlasEftImportMeshesCommandlet, Display,
        TEXT("Atlas batch mesh import: PASS\nMeshes: %d  Source vertices: %llu  Render vertices: %llu  Triangles: %llu  Sections: %llu"),
        ImportCount,
        SourceVertices,
        RenderVertices,
        Triangles,
        Sections);
    return 0;
}
