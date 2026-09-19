#include "AtlasEftTextureImporter.h"

#include "AssetImportTask.h"
#include "AssetToolsModule.h"
#include "Engine/Texture2D.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "ObjectTools.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

namespace AtlasEft
{
namespace
{
bool IsColorSemantic(ETextureSemantic Semantic)
{
    return Semantic == ETextureSemantic::Albedo
        || Semantic == ETextureSemantic::Emissive
        || Semantic == ETextureSemantic::DetailAlbedo
        || Semantic == ETextureSemantic::VertexPaintAlbedo;
}

bool IsNormalSemantic(ETextureSemantic Semantic)
{
    return Semantic == ETextureSemantic::Normal
        || Semantic == ETextureSemantic::DetailNormal
        || Semantic == ETextureSemantic::VertexPaintNormal;
}

FString MakeAssetName(const FTextureReference& Reference)
{
    FString BaseName = ObjectTools::SanitizeObjectName(FPaths::GetBaseFilename(Reference.Path));
    if (BaseName.IsEmpty())
    {
        BaseName = TEXT("Texture");
    }
    BaseName = BaseName.Left(96);
    return FString::Printf(TEXT("T_Atlas_%s_%s"), FTextureImporter::SemanticName(Reference.Semantic), *BaseName);
}
}

const TCHAR* FTextureImporter::SemanticName(ETextureSemantic Semantic)
{
    switch (Semantic)
    {
    case ETextureSemantic::Albedo: return TEXT("Albedo");
    case ETextureSemantic::Normal: return TEXT("Normal");
    case ETextureSemantic::Specular: return TEXT("Specular");
    case ETextureSemantic::Emissive: return TEXT("Emissive");
    case ETextureSemantic::DetailAlbedo: return TEXT("DetailAlbedo");
    case ETextureSemantic::DetailNormal: return TEXT("DetailNormal");
    case ETextureSemantic::VertexPaintAlbedo: return TEXT("VertexPaintAlbedo");
    case ETextureSemantic::VertexPaintNormal: return TEXT("VertexPaintNormal");
    case ETextureSemantic::VertexPaintHeights: return TEXT("VertexPaintHeights");
    case ETextureSemantic::Parallax: return TEXT("Parallax");
    default: return TEXT("Unknown");
    }
}

bool FTextureImporter::ImportTexture(
    const FString& PackDirectory,
    const FTextureReference& Reference,
    const FString& DestinationPath,
    FTextureImportResult& OutResult,
    FString& OutError)
{
    OutResult = FTextureImportResult();
    OutError.Reset();

    FString NormalizedDestination = DestinationPath;
    NormalizedDestination.RemoveFromEnd(TEXT("/"));
    if (!FPackageName::IsValidLongPackageName(NormalizedDestination))
    {
        OutError = FString::Printf(TEXT("Invalid Unreal destination path: %s"), *DestinationPath);
        return false;
    }

    FString PackRoot = FPaths::ConvertRelativePathToFull(PackDirectory);
    FPaths::NormalizeDirectoryName(PackRoot);
    FString SourcePath = FPaths::ConvertRelativePathToFull(FPaths::Combine(PackRoot, Reference.Path));
    FPaths::NormalizeFilename(SourcePath);
    FString RootPrefix = PackRoot;
    FPaths::NormalizeFilename(RootPrefix);
    RootPrefix += TEXT("/");
    if (!SourcePath.StartsWith(RootPrefix, ESearchCase::IgnoreCase))
    {
        OutError = FString::Printf(TEXT("Texture path escapes pack root: %s"), *Reference.Path);
        return false;
    }
    if (!FPaths::FileExists(SourcePath) || !FPaths::GetExtension(SourcePath).Equals(TEXT("png"), ESearchCase::IgnoreCase))
    {
        OutError = FString::Printf(TEXT("Atlas texture is missing or is not PNG: %s"), *SourcePath);
        return false;
    }

    const FString AssetName = MakeAssetName(Reference);
    UAssetImportTask* Task = NewObject<UAssetImportTask>();
    Task->Filename = SourcePath;
    Task->DestinationPath = NormalizedDestination;
    Task->DestinationName = AssetName;
    Task->bAutomated = true;
    Task->bReplaceExisting = true;
    Task->bReplaceExistingSettings = false;
    Task->bSave = false;

    TArray<UAssetImportTask*> Tasks;
    Tasks.Add(Task);
    FAssetToolsModule::GetModule().Get().ImportAssetTasks(Tasks);

    UTexture2D* Texture = nullptr;
    for (UObject* ImportedObject : Task->GetObjects())
    {
        Texture = Cast<UTexture2D>(ImportedObject);
        if (Texture)
        {
            break;
        }
    }
    if (!Texture)
    {
        OutError = FString::Printf(TEXT("PNG import did not produce UTexture2D: %s"), *SourcePath);
        return false;
    }

    Texture->PreEditChange(nullptr);
    Texture->SRGB = IsColorSemantic(Reference.Semantic);
    Texture->AddressX = TA_Wrap;
    Texture->AddressY = TA_Wrap;
    Texture->LODGroup = IsNormalSemantic(Reference.Semantic) ? TEXTUREGROUP_WorldNormalMap : TEXTUREGROUP_World;
    Texture->bFlipGreenChannel = false; // Atlas PNG normals are already in DirectX convention.
    if (IsNormalSemantic(Reference.Semantic))
    {
        Texture->CompressionSettings = TC_Normalmap;
    }
    else if (Reference.Semantic == ETextureSemantic::Specular
        || Reference.Semantic == ETextureSemantic::VertexPaintHeights)
    {
        Texture->CompressionSettings = TC_Masks;
    }
    else if (Reference.Semantic == ETextureSemantic::Parallax)
    {
        Texture->CompressionSettings = TC_Grayscale;
    }
    else
    {
        Texture->CompressionSettings = TC_Default;
    }
    Texture->PostEditChange();
    Texture->MarkPackageDirty();
    Texture->UpdateResource();

    UPackage* Package = Texture->GetOutermost();
    const FString PackageName = Package->GetName();
    const FString PackageFilename = FPackageName::LongPackageNameToFilename(
        PackageName,
        FPackageName::GetAssetPackageExtension());
    FSavePackageArgs SaveArgs;
    SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
    SaveArgs.SaveFlags = SAVE_NoError;
    SaveArgs.bSlowTask = false;
    if (!UPackage::SavePackage(Package, Texture, *PackageFilename, SaveArgs))
    {
        OutError = FString::Printf(TEXT("Failed saving texture package %s."), *PackageFilename);
        return false;
    }

    const int32 SourceWidth = Texture->Source.GetSizeX();
    const int32 SourceHeight = Texture->Source.GetSizeY();
    if (SourceWidth <= 0 || SourceHeight <= 0)
    {
        OutError = FString::Printf(TEXT("Imported texture has invalid source dimensions: %s (%dx%d)."), *SourcePath, SourceWidth, SourceHeight);
        return false;
    }

    OutResult.Texture = Texture;
    OutResult.SourcePath = SourcePath;
    OutResult.ObjectPath = Texture->GetPathName();
    OutResult.PackageFilename = PackageFilename;
    OutResult.Semantic = Reference.Semantic;
    OutResult.Width = SourceWidth;
    OutResult.Height = SourceHeight;
    OutResult.bSRGB = Texture->SRGB;
    OutResult.CompressionSettings = static_cast<int32>(Texture->CompressionSettings);
    OutResult.bFlipGreenChannel = Texture->bFlipGreenChannel;
    return true;
}
} // namespace AtlasEft
