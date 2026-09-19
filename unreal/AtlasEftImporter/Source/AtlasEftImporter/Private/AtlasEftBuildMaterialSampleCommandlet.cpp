#include "AtlasEftBuildMaterialSampleCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AtlasEftMaterialImporter.h"
#include "AtlasEftPackReader.h"
#include "AtlasEftStaticMeshImporter.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/Texture2D.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "MaterialEditingLibrary.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftBuildMaterialSampleCommandlet, Log, All);

namespace
{
bool SaveAsset(UObject* Asset, FString& OutError)
{
    UPackage* Package = Asset ? Asset->GetOutermost() : nullptr;
    if (!Package)
    {
        OutError = TEXT("Asset has no package.");
        return false;
    }
    Package->MarkPackageDirty();
    const FString Filename = FPackageName::LongPackageNameToFilename(
        Package->GetName(), FPackageName::GetAssetPackageExtension());
    FSavePackageArgs SaveArgs;
    SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
    SaveArgs.SaveFlags = SAVE_NoError;
    SaveArgs.bSlowTask = false;
    if (!UPackage::SavePackage(Package, Asset, *Filename, SaveArgs))
    {
        OutError = FString::Printf(TEXT("Failed saving package %s."), *Filename);
        return false;
    }
    return true;
}
}

UAtlasEftBuildMaterialSampleCommandlet::UAtlasEftBuildMaterialSampleCommandlet()
{
    IsClient = false;
    IsEditor = true;
    IsServer = false;
    LogToConsole = true;
    ShowErrorCount = true;
}

int32 UAtlasEftBuildMaterialSampleCommandlet::Main(const FString& Params)
{
    FString PackDirectory;
    if (!FParse::Value(*Params, TEXT("Pack="), PackDirectory) || PackDirectory.IsEmpty())
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Missing -Pack=<path to map.eftpack directory>."));
        return 2;
    }
    PackDirectory.TrimQuotesInline();

    int32 MaterialId = 17;
    int32 MeshId = 20;
    FParse::Value(*Params, TEXT("MaterialId="), MaterialId);
    FParse::Value(*Params, TEXT("MeshId="), MeshId);
    if (MaterialId < 0 || MeshId < 0)
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("MaterialId and MeshId must be non-negative."));
        return 2;
    }

    FString Destination = TEXT("/Game/Atlas/MaterialTest");
    FParse::Value(*Params, TEXT("Destination="), Destination);
    Destination.TrimQuotesInline();
    Destination.RemoveFromEnd(TEXT("/"));
    if (!FPackageName::IsValidLongPackageName(Destination))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Invalid destination: %s"), *Destination);
        return 2;
    }

    AtlasEft::FAuditReport Report;
    Report.PackDirectory = PackDirectory;
    AtlasEft::FManifest Manifest;
    if (!AtlasEft::FPackReader::ReadManifest(PackDirectory, Manifest, Report))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("%s"), *Report.ToText());
        return 1;
    }
    TArray<AtlasEft::FMaterialDesc> Materials;
    if (!AtlasEft::FPackReader::ReadMaterials(PackDirectory, Manifest, Materials, Report))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("%s"), *Report.ToText());
        return 1;
    }
    if (!Materials.IsValidIndex(MaterialId) || !Manifest.Meshes.IsValidIndex(MeshId))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Requested material or mesh id is outside the pack."));
        return 1;
    }
    const AtlasEft::FMaterialDesc& Material = Materials[MaterialId];
    const AtlasEft::FMeshDesc& MeshDescription = Manifest.Meshes[MeshId];
    if (Material.Id != static_cast<uint32>(MaterialId)
        || MeshDescription.Submeshes.Num() != 1
        || MeshDescription.Submeshes[0].MaterialId != static_cast<uint32>(MaterialId))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error,
            TEXT("Mesh %d is not the expected single-section consumer of material %d."), MeshId, MaterialId);
        return 1;
    }
    if (Material.Role != TEXT("opaque")
        || Material.AlphaMode != AtlasEft::EMaterialAlphaMode::Opaque
        || !Material.bRoughnessFromAlbedoAlpha)
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error,
            TEXT("Material %d is not the expected opaque RFA validation material."), MaterialId);
        return 1;
    }

    AtlasEft::FMaterialImportResult MaterialResult;
    FString Error;
    if (!AtlasEft::FMaterialImporter::ImportOpaqueMaterial(
        PackDirectory,
        Material,
        TEXT("/Game/Atlas/Common/Materials"),
        Destination + TEXT("/Materials"),
        Destination + TEXT("/Textures"),
        MaterialResult,
        Error))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("%s"), *Error);
        return 1;
    }

    AtlasEft::FStaticMeshImportResult MeshResult;
    if (!AtlasEft::FStaticMeshImporter::ImportMesh(
        PackDirectory,
        Manifest,
        static_cast<uint32>(MeshId),
        Destination + TEXT("/Meshes"),
        MeshResult,
        Error))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("%s"), *Error);
        return 1;
    }
    if (!MeshResult.StaticMesh || MeshResult.StaticMesh->GetStaticMaterials().Num() != 1)
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Imported mesh does not have exactly one material slot."));
        return 1;
    }
    MeshResult.StaticMesh->PreEditChange(nullptr);
    MeshResult.StaticMesh->SetMaterial(0, MaterialResult.MaterialInstance);
    MeshResult.StaticMesh->PostEditChange();
    if (MeshResult.StaticMesh->GetMaterial(0) != MaterialResult.MaterialInstance || !SaveAsset(MeshResult.StaticMesh, Error))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Mesh material assignment failed: %s"), *Error);
        return 1;
    }

    const FString LevelName = FString::Printf(TEXT("L_Atlas_Material_%04d_Mesh%04d"), MaterialId, MeshId);
    const FString LevelPackageName = Destination + TEXT("/") + LevelName;
    const FString LevelFilename = FPackageName::LongPackageNameToFilename(
        LevelPackageName, FPackageName::GetMapPackageExtension());
    if (IFileManager::Get().FileExists(*LevelFilename)
        && !IFileManager::Get().Delete(*LevelFilename, false, true, true))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Could not replace generated map %s."), *LevelFilename);
        return 1;
    }
    UPackage* LevelPackage = CreatePackage(*LevelPackageName);
    UWorld* World = LevelPackage
        ? UWorld::CreateWorld(EWorldType::Editor, false, FName(*LevelName), LevelPackage)
        : nullptr;
    if (!World)
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Could not create material validation world."));
        return 1;
    }
    World->SetFlags(RF_Public | RF_Standalone);

    FActorSpawnParameters MeshSpawnParameters;
    MeshSpawnParameters.Name = FName(TEXT("Atlas_Material17_Mesh20"));
    AStaticMeshActor* MeshActor = World->SpawnActor<AStaticMeshActor>(
        AStaticMeshActor::StaticClass(), FTransform::Identity, MeshSpawnParameters);
    if (!MeshActor)
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Could not spawn material validation mesh actor."));
        World->CleanupWorld(true, true);
        return 1;
    }
    MeshActor->SetActorLabel(TEXT("Atlas Material 17 | Mesh 20 | Opaque RFA"));
    MeshActor->GetStaticMeshComponent()->SetStaticMesh(MeshResult.StaticMesh);
    MeshActor->GetStaticMeshComponent()->SetMaterial(0, MaterialResult.MaterialInstance);
    MeshActor->GetStaticMeshComponent()->SetMobility(EComponentMobility::Static);

    FActorSpawnParameters LightSpawnParameters;
    LightSpawnParameters.Name = FName(TEXT("Atlas_MaterialTest_KeyLight"));
    ADirectionalLight* KeyLight = World->SpawnActor<ADirectionalLight>(
        ADirectionalLight::StaticClass(),
        FTransform(FRotator(-35.0, -45.0, 0.0)),
        LightSpawnParameters);
    if (!KeyLight)
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Could not spawn material validation light."));
        World->CleanupWorld(true, true);
        return 1;
    }
    KeyLight->SetActorLabel(TEXT("Atlas Material Test Key Light"));
    KeyLight->GetLightComponent()->SetIntensity(8.0f);

    if (MeshActor->GetStaticMeshComponent()->GetStaticMesh() != MeshResult.StaticMesh
        || MeshActor->GetStaticMeshComponent()->GetMaterial(0) != MaterialResult.MaterialInstance
        || MaterialResult.MaterialInstance->Parent != MaterialResult.MasterMaterial)
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Generated actor, mesh slot, or MI parent validation failed."));
        World->CleanupWorld(true, true);
        return 1;
    }

    World->UpdateWorldComponents(true, false);
    LevelPackage->MarkPackageDirty();
    FAssetRegistryModule::AssetCreated(World);
    FSavePackageArgs SaveArgs;
    SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
    SaveArgs.SaveFlags = SAVE_NoError;
    SaveArgs.bSlowTask = false;
    if (!UPackage::SavePackage(LevelPackage, World, *LevelFilename, SaveArgs))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Failed saving map %s."), *LevelFilename);
        World->CleanupWorld(true, true);
        return 1;
    }

    UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Display,
        TEXT("Atlas material vertical test: PASS\nLevel: %s\nMaster: %s\nInstance: %s\nMesh: %s\nTextures: albedo=%s normal=%s specMap-provenance=%s\nRoughness: clamp(1 - albedo.a, 0.06, 1); specMap deliberately not sampled."),
        *LevelPackageName,
        *MaterialResult.MasterObjectPath,
        *MaterialResult.InstanceObjectPath,
        *MeshResult.ObjectPath,
        *MaterialResult.AlbedoTexture->GetPathName(),
        *MaterialResult.NormalTexture->GetPathName(),
        *MaterialResult.SpecularProvenanceTexture->GetPathName());
    World->CleanupWorld(true, true);
    return 0;
}
