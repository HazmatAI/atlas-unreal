#pragma once

#include "CoreMinimal.h"
#include "AtlasEftPackTypes.h"

class UMaterial;
class UMaterialInstanceConstant;
class UTexture2D;

namespace AtlasEft
{
struct ATLASEFTIMPORTER_API FMaterialImportResult
{
    TObjectPtr<UMaterial> MasterMaterial = nullptr;
    TObjectPtr<UMaterialInstanceConstant> MaterialInstance = nullptr;
    TObjectPtr<UTexture2D> AlbedoTexture = nullptr;
    TObjectPtr<UTexture2D> NormalTexture = nullptr;
    TObjectPtr<UTexture2D> SpecularProvenanceTexture = nullptr;
    FString MasterObjectPath;
    FString InstanceObjectPath;
};

/** Editor-only minimal opaque Atlas master and material-instance generator. */
class ATLASEFTIMPORTER_API FMaterialImporter
{
public:
    static bool ImportOpaqueMaterial(
        const FString& PackDirectory,
        const FMaterialDesc& Material,
        const FString& MasterDestinationPath,
        const FString& InstanceDestinationPath,
        const FString& TextureDestinationPath,
        FMaterialImportResult& OutResult,
        FString& OutError);
};
} // namespace AtlasEft
