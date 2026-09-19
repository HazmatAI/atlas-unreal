#include "AtlasEftImportMeshCommandlet.h"

#include "AtlasEftStaticMeshImporter.h"
#include "Misc/Parse.h"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftImportMeshCommandlet, Log, All);

UAtlasEftImportMeshCommandlet::UAtlasEftImportMeshCommandlet()
{
    IsClient = false;
    IsEditor = true;
    IsServer = false;
    LogToConsole = true;
    ShowErrorCount = true;
}

int32 UAtlasEftImportMeshCommandlet::Main(const FString& Params)
{
    FString PackDirectory;
    if (!FParse::Value(*Params, TEXT("Pack="), PackDirectory) || PackDirectory.IsEmpty())
    {
        UE_LOG(LogAtlasEftImportMeshCommandlet, Error, TEXT("Missing -Pack=<path to map.eftpack directory>."));
        return 2;
    }
    PackDirectory.TrimQuotesInline();

    int32 MeshId = 0;
    FParse::Value(*Params, TEXT("MeshId="), MeshId);
    if (MeshId < 0)
    {
        UE_LOG(LogAtlasEftImportMeshCommandlet, Error, TEXT("MeshId must be non-negative."));
        return 2;
    }

    FString Destination = TEXT("/Game/Atlas/Samples");
    FParse::Value(*Params, TEXT("Destination="), Destination);
    Destination.TrimQuotesInline();

    AtlasEft::FStaticMeshImportResult Result;
    FString Error;
    if (!AtlasEft::FStaticMeshImporter::ImportMesh(
        PackDirectory,
        static_cast<uint32>(MeshId),
        Destination,
        Result,
        Error))
    {
        UE_LOG(LogAtlasEftImportMeshCommandlet, Error, TEXT("%s"), *Error);
        return 1;
    }

    UE_LOG(LogAtlasEftImportMeshCommandlet, Display,
        TEXT("Atlas mesh import: PASS\nAsset: %s\nFile: %s\nSource vertices: %u  Render vertices: %u  Triangles: %u  Sections: %u"),
        *Result.ObjectPath,
        *Result.PackageFilename,
        Result.SourceVertexCount,
        Result.RenderVertexCount,
        Result.RenderTriangleCount,
        Result.RenderSectionCount);
    return 0;
}
