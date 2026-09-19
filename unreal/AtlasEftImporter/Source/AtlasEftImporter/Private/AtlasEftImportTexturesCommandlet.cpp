#include "AtlasEftImportTexturesCommandlet.h"

#include "AtlasEftPackReader.h"
#include "AtlasEftTextureImporter.h"
#include "Misc/Parse.h"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftImportTexturesCommandlet, Log, All);

UAtlasEftImportTexturesCommandlet::UAtlasEftImportTexturesCommandlet()
{
    IsClient = false;
    IsEditor = true;
    IsServer = false;
    LogToConsole = true;
    ShowErrorCount = true;
}

int32 UAtlasEftImportTexturesCommandlet::Main(const FString& Params)
{
    FString PackDirectory;
    if (!FParse::Value(*Params, TEXT("Pack="), PackDirectory) || PackDirectory.IsEmpty())
    {
        UE_LOG(LogAtlasEftImportTexturesCommandlet, Error, TEXT("Missing -Pack=<path to map.eftpack directory>."));
        return 2;
    }
    PackDirectory.TrimQuotesInline();

    int32 MaterialId = 17;
    int32 MaxTextures = 3;
    FParse::Value(*Params, TEXT("MaterialId="), MaterialId);
    FParse::Value(*Params, TEXT("MaxTextures="), MaxTextures);
    if (MaterialId < 0 || MaxTextures < 1 || MaxTextures > 32)
    {
        UE_LOG(LogAtlasEftImportTexturesCommandlet, Error, TEXT("MaterialId must be non-negative and MaxTextures must be in [1, 32]."));
        return 2;
    }

    FString Destination = TEXT("/Game/Atlas/Textures/Samples");
    FParse::Value(*Params, TEXT("Destination="), Destination);
    Destination.TrimQuotesInline();

    AtlasEft::FAuditReport Report;
    Report.PackDirectory = PackDirectory;
    AtlasEft::FManifest Manifest;
    if (!AtlasEft::FPackReader::ReadManifest(PackDirectory, Manifest, Report))
    {
        UE_LOG(LogAtlasEftImportTexturesCommandlet, Error, TEXT("%s"), *Report.ToText());
        return 1;
    }
    TArray<AtlasEft::FMaterialDesc> Materials;
    if (!AtlasEft::FPackReader::ReadMaterials(PackDirectory, Manifest, Materials, Report))
    {
        UE_LOG(LogAtlasEftImportTexturesCommandlet, Error, TEXT("%s"), *Report.ToText());
        return 1;
    }
    if (!Materials.IsValidIndex(MaterialId) || Materials[MaterialId].Id != static_cast<uint32>(MaterialId))
    {
        UE_LOG(LogAtlasEftImportTexturesCommandlet, Error, TEXT("Material %d is not available."), MaterialId);
        return 1;
    }

    const AtlasEft::FMaterialDesc& Material = Materials[MaterialId];
    const int32 ImportCount = FMath::Min(MaxTextures, Material.Textures.Num());
    if (ImportCount == 0)
    {
        UE_LOG(LogAtlasEftImportTexturesCommandlet, Error, TEXT("Material %d has no texture references."), MaterialId);
        return 1;
    }

    for (int32 Index = 0; Index < ImportCount; ++Index)
    {
        AtlasEft::FTextureImportResult Result;
        FString Error;
        if (!AtlasEft::FTextureImporter::ImportTexture(
            PackDirectory,
            Material.Textures[Index],
            Destination,
            Result,
            Error))
        {
            UE_LOG(LogAtlasEftImportTexturesCommandlet, Error, TEXT("Texture %d/%d failed: %s"), Index + 1, ImportCount, *Error);
            return 1;
        }
        UE_LOG(LogAtlasEftImportTexturesCommandlet, Display,
            TEXT("Imported %s: %s (%dx%d, sRGB=%s, compression=%d, flipGreen=%s)"),
            AtlasEft::FTextureImporter::SemanticName(Result.Semantic),
            *Result.ObjectPath,
            Result.Width,
            Result.Height,
            Result.bSRGB ? TEXT("true") : TEXT("false"),
            Result.CompressionSettings,
            Result.bFlipGreenChannel ? TEXT("true") : TEXT("false"));
    }

    UE_LOG(LogAtlasEftImportTexturesCommandlet, Display,
        TEXT("Atlas sample texture import: PASS\nMaterial: %d  Imported textures: %d/%d  Destination: %s"),
        MaterialId,
        ImportCount,
        Material.Textures.Num(),
        *Destination);
    return 0;
}
