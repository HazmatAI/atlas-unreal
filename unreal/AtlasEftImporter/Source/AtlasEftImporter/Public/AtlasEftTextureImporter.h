#pragma once

#include "CoreMinimal.h"
#include "AtlasEftPackTypes.h"

class UTexture2D;

namespace AtlasEft
{
struct ATLASEFTIMPORTER_API FTextureImportResult
{
    TObjectPtr<UTexture2D> Texture = nullptr;
    FString SourcePath;
    FString ObjectPath;
    FString PackageFilename;
    ETextureSemantic Semantic = ETextureSemantic::Albedo;
    int32 Width = 0;
    int32 Height = 0;
    bool bSRGB = false;
    int32 CompressionSettings = 0;
    bool bFlipGreenChannel = false;
};

/** Editor-only import and semantic configuration for one Atlas PNG texture. */
class ATLASEFTIMPORTER_API FTextureImporter
{
public:
    static bool ImportTexture(
        const FString& PackDirectory,
        const FTextureReference& Reference,
        const FString& DestinationPath,
        FTextureImportResult& OutResult,
        FString& OutError);

    static const TCHAR* SemanticName(ETextureSemantic Semantic);
};
} // namespace AtlasEft
