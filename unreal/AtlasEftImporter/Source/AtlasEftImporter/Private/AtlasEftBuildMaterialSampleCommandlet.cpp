#include "AtlasEftBuildMaterialSampleCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AtlasEftMaterialImporter.h"
#include "AtlasEftPackReader.h"
#include "AtlasEftStaticMeshImporter.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/RectLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/RectLight.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/Texture2D.h"
#include "EngineUtils.h"
#include "Engine/World.h"
#include "GameFramework/WorldSettings.h"
#include "HAL/FileManager.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Materials/MaterialInterface.h"
#include "Math/RotationMatrix.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftBuildMaterialSampleCommandlet, Log, All);

namespace
{
struct FMaterialSample
{
    int32 MaterialId = INDEX_NONE;
    int32 MeshId = INDEX_NONE;
    const AtlasEft::FMaterialDesc* Material = nullptr;
    AtlasEft::FMaterialImportResult MaterialResult;
    AtlasEft::FStaticMeshImportResult MeshResult;
};

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

ADirectionalLight* SpawnDirectionalLight(
    UWorld* World,
    const TCHAR* ObjectName,
    const TCHAR* Label,
    const FVector& Location,
    const FRotator& Rotation,
    float Intensity,
    bool bCastShadows)
{
    FActorSpawnParameters SpawnParameters;
    SpawnParameters.Name = FName(ObjectName);
    ADirectionalLight* Light = World->SpawnActor<ADirectionalLight>(
        ADirectionalLight::StaticClass(), FTransform(Rotation, Location), SpawnParameters);
    if (!Light)
    {
        return nullptr;
    }
    Light->SetActorLabel(Label);
    UDirectionalLightComponent* Component = CastChecked<UDirectionalLightComponent>(Light->GetLightComponent());
    Component->SetMobility(EComponentMobility::Movable);
    Component->SetIntensity(Intensity);
    Component->SetCastShadows(bCastShadows);
    return Light;
}

ARectLight* SpawnRectLight(
    UWorld* World,
    const TCHAR* ObjectName,
    const TCHAR* Label,
    const FVector& Location,
    const FRotator& Rotation,
    float IntensityCandelas)
{
    FActorSpawnParameters SpawnParameters;
    SpawnParameters.Name = FName(ObjectName);
    ARectLight* Light = World->SpawnActor<ARectLight>(
        ARectLight::StaticClass(), FTransform(Rotation, Location), SpawnParameters);
    if (!Light)
    {
        return nullptr;
    }

    Light->SetActorLabel(Label);
    URectLightComponent* Component = CastChecked<URectLightComponent>(Light->GetLightComponent());
    Component->SetMobility(EComponentMobility::Movable);
    Component->SetIntensityUnits(ELightUnits::Candelas);
    Component->SetIntensity(IntensityCandelas);
    Component->SetSourceWidth(64.0f);
    Component->SetSourceHeight(64.0f);
    Component->SetCastShadows(true);
    return Light;
}

bool SpawnNeutralBackdrop(
    UWorld* World,
    const FVector& BoundsCenter,
    const FVector& CameraAwayDirection,
    float BoundsRadius,
    AStaticMeshActor*& OutBackdrop,
    FString& OutError)
{
    UStaticMesh* PlaneMesh = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Plane.Plane"));
    UMaterialInterface* NeutralMaterial = LoadObject<UMaterialInterface>(
        nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    if (!PlaneMesh || !NeutralMaterial)
    {
        OutError = TEXT("Could not load the Engine basic plane and neutral material for the review backdrop.");
        return false;
    }

    const float BackdropScale = FMath::Max(20.0f, BoundsRadius * 0.04f);
    const FVector BackdropLocation = BoundsCenter - CameraAwayDirection * (BoundsRadius * 1.55f);
    const FRotator BackdropRotation = FRotationMatrix::MakeFromZ(CameraAwayDirection).Rotator();
    FActorSpawnParameters SpawnParameters;
    SpawnParameters.Name = FName(TEXT("Atlas_MaterialTest_NeutralBackdrop"));
    OutBackdrop = World->SpawnActor<AStaticMeshActor>(
        AStaticMeshActor::StaticClass(),
        FTransform(BackdropRotation, BackdropLocation, FVector(BackdropScale)),
        SpawnParameters);
    if (!OutBackdrop)
    {
        OutError = TEXT("Could not spawn the neutral review backdrop.");
        return false;
    }

    OutBackdrop->SetActorLabel(TEXT("Atlas Material Test | Engine Neutral Backdrop"));
    UStaticMeshComponent* Component = OutBackdrop->GetStaticMeshComponent();
    Component->SetMobility(EComponentMobility::Static);
    Component->SetStaticMesh(PlaneMesh);
    Component->SetMaterial(0, NeutralMaterial);
    Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    return true;
}

bool SpawnDeterministicReviewRig(
    UWorld* World,
    AStaticMeshActor* MeshActor,
    ADirectionalLight*& OutDirectionalLight,
    ARectLight*& OutRectLight,
    AStaticMeshActor*& OutBackdrop,
    ACameraActor*& OutCamera,
    FString& OutError)
{
    if (!MeshActor || !MeshActor->GetStaticMeshComponent()->GetStaticMesh())
    {
        OutError = TEXT("Cannot place the review rig without the sample mesh actor and asset.");
        return false;
    }

    FVector BoundsCenter;
    FVector BoundsExtent;
    MeshActor->GetActorBounds(true, BoundsCenter, BoundsExtent);
    const float BoundsRadius = BoundsExtent.Size();
    if (BoundsRadius <= UE_SMALL_NUMBER)
    {
        OutError = TEXT("Sample mesh has empty bounds; cannot place the review rig.");
        return false;
    }

    // Keep lighting positions relative to the sample bounds so both material maps share a rig.
    OutDirectionalLight = SpawnDirectionalLight(
        World,
        TEXT("Atlas_MaterialTest_DirectionalLight"),
        TEXT("Atlas Material Test | Directional | Movable"),
        BoundsCenter + FVector(290.0f, -100.0f, 1120.0f),
        FRotator(-71.0, 75.0, 0.0),
        5.0f,
        false);
    OutRectLight = SpawnRectLight(
        World,
        TEXT("Atlas_MaterialTest_RectLight"),
        TEXT("Atlas Material Test | Rect | Movable | 20 cd"),
        BoundsCenter + FVector(260.0f, -160.0f, 170.0f),
        FRotator(-8.0, 180.0, 175.0),
        20.0f);

    FActorSpawnParameters PostProcessSpawnParameters;
    PostProcessSpawnParameters.Name = FName(TEXT("Atlas_MaterialTest_FixedExposure"));
    APostProcessVolume* PostProcess = World->SpawnActor<APostProcessVolume>(
        APostProcessVolume::StaticClass(), FTransform::Identity, PostProcessSpawnParameters);
    if (PostProcess)
    {
        PostProcess->SetActorLabel(TEXT("Atlas Material Test | Fixed Exposure EV100 0"));
        PostProcess->bUnbound = true;
        PostProcess->BlendWeight = 1.0f;
        PostProcess->Settings.bOverride_AutoExposureMethod = true;
        PostProcess->Settings.AutoExposureMethod = EAutoExposureMethod::AEM_Manual;
        PostProcess->Settings.bOverride_AutoExposureBias = true;
        PostProcess->Settings.AutoExposureBias = 0.0f;
        PostProcess->Settings.bOverride_AutoExposureApplyPhysicalCameraExposure = true;
        PostProcess->Settings.AutoExposureApplyPhysicalCameraExposure = false;
    }

    const FVector CameraAwayDirection = FVector(1.0f, 1.55f, 1.35f).GetSafeNormal();
    const float ReviewDistance = FMath::Max(100.0f, BoundsRadius * 4.2f);
    const FVector CameraLocation = BoundsCenter + CameraAwayDirection * ReviewDistance;
    const FRotator CameraRotation = (BoundsCenter - CameraLocation).Rotation();
    FActorSpawnParameters CameraSpawnParameters;
    CameraSpawnParameters.Name = FName(TEXT("Atlas_MaterialTest_ReviewCamera"));
    OutCamera = World->SpawnActor<ACameraActor>(
        ACameraActor::StaticClass(),
        FTransform(CameraRotation, CameraLocation),
        CameraSpawnParameters);
    if (OutCamera)
    {
        OutCamera->SetActorLabel(TEXT("Atlas Material Test | Deterministic Review Camera"));
        OutCamera->GetCameraComponent()->SetFieldOfView(45.0f);
    }

    if (!SpawnNeutralBackdrop(
            World, BoundsCenter, CameraAwayDirection, BoundsRadius, OutBackdrop, OutError))
    {
        return false;
    }

    if (!OutDirectionalLight || !OutRectLight || !PostProcess || !OutCamera || !OutBackdrop)
    {
        OutError = TEXT("Could not create the deterministic material review rig.");
        return false;
    }

    int32 DirectionalLightCount = 0;
    for (TActorIterator<ADirectionalLight> It(World); It; ++It)
    {
        ++DirectionalLightCount;
    }
    int32 RectLightCount = 0;
    for (TActorIterator<ARectLight> It(World); It; ++It)
    {
        ++RectLightCount;
    }

    const UDirectionalLightComponent* DirectionalComponent =
        Cast<UDirectionalLightComponent>(OutDirectionalLight->GetLightComponent());
    const URectLightComponent* RectComponent = Cast<URectLightComponent>(OutRectLight->GetLightComponent());
    const FVector CameraTargetDirection = (BoundsCenter - OutCamera->GetActorLocation()).GetSafeNormal();
    const float CameraTargetAlignment = FVector::DotProduct(
        OutCamera->GetActorForwardVector(), CameraTargetDirection);
    const float CameraDistance = FVector::Distance(BoundsCenter, OutCamera->GetActorLocation());
    const FVector BackdropOffset = OutBackdrop->GetActorLocation() - BoundsCenter;
    const float BackdropBehindDistance = -FVector::DotProduct(BackdropOffset, CameraAwayDirection);
    const float BackdropFacingAlignment = FVector::DotProduct(
        OutBackdrop->GetStaticMeshComponent()->GetUpVector(), CameraAwayDirection);
    const bool bRigValid = DirectionalLightCount == 1
        && RectLightCount == 1
        && DirectionalComponent
        && DirectionalComponent->Mobility == EComponentMobility::Movable
        && FMath::IsNearlyEqual(DirectionalComponent->Intensity, 5.0f)
        && !DirectionalComponent->CastShadows
        && RectComponent
        && RectComponent->Mobility == EComponentMobility::Movable
        && RectComponent->IntensityUnits == ELightUnits::Candelas
        && FMath::IsNearlyEqual(RectComponent->Intensity, 20.0f)
        && OutBackdrop->GetStaticMeshComponent()->GetStaticMesh()
            == LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Plane.Plane"))
        && OutBackdrop->GetStaticMeshComponent()->GetMaterial(0)
            == LoadObject<UMaterialInterface>(nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"))
        && OutBackdrop->GetStaticMeshComponent()->Mobility == EComponentMobility::Static
        && OutBackdrop->GetStaticMeshComponent()->GetStaticMesh()->GetPathName().StartsWith(TEXT("/Engine/"))
        && OutBackdrop->GetStaticMeshComponent()->GetCollisionEnabled() == ECollisionEnabled::NoCollision
        && BackdropBehindDistance >= BoundsRadius * 1.4f
        && BackdropFacingAlignment >= 0.995f
        && PostProcess->bUnbound
        && PostProcess->Settings.AutoExposureMethod == EAutoExposureMethod::AEM_Manual
        && !PostProcess->Settings.AutoExposureApplyPhysicalCameraExposure
        && FMath::IsNearlyEqual(PostProcess->Settings.AutoExposureBias, 0.0f)
        && FMath::IsNearlyEqual(OutCamera->GetCameraComponent()->FieldOfView, 45.0f)
        && FMath::IsNearlyEqual(CameraTargetAlignment, 1.0f, 0.001f)
        && CameraDistance >= BoundsRadius * 4.0f
        && World->GetWorldSettings()->bForceNoPrecomputedLighting;
    if (!bRigValid)
    {
        OutError = TEXT("Deterministic material review rig validation failed.");
        return false;
    }
    return true;
}

bool BuildReviewWorld(
    const FString& Destination,
    const FMaterialSample& Sample,
    FString& OutLevelPackageName,
    FString& OutError)
{
    const FString LevelName = FString::Printf(
        TEXT("L_Atlas_Material_%04d_Mesh%04d"), Sample.MaterialId, Sample.MeshId);
    OutLevelPackageName = Destination / LevelName;
    const FString LevelFilename = FPackageName::LongPackageNameToFilename(
        OutLevelPackageName, FPackageName::GetMapPackageExtension());
    if (IFileManager::Get().FileExists(*LevelFilename)
        && !IFileManager::Get().Delete(*LevelFilename, false, true, true))
    {
        OutError = FString::Printf(TEXT("Could not replace generated map %s."), *LevelFilename);
        return false;
    }

    UPackage* LevelPackage = CreatePackage(*OutLevelPackageName);
    UWorld* World = LevelPackage
        ? UWorld::CreateWorld(EWorldType::Editor, false, FName(*LevelName), LevelPackage)
        : nullptr;
    if (!World)
    {
        OutError = TEXT("Could not create material validation world.");
        return false;
    }
    World->SetFlags(RF_Public | RF_Standalone);
    World->GetWorldSettings()->bForceNoPrecomputedLighting = true;

    FActorSpawnParameters MeshSpawnParameters;
    MeshSpawnParameters.Name = FName(*FString::Printf(
        TEXT("Atlas_Material%d_Mesh%d"), Sample.MaterialId, Sample.MeshId));
    AStaticMeshActor* MeshActor = World->SpawnActor<AStaticMeshActor>(
        AStaticMeshActor::StaticClass(), FTransform::Identity, MeshSpawnParameters);
    if (!MeshActor)
    {
        OutError = TEXT("Could not spawn material validation mesh actor.");
        World->CleanupWorld(true, true);
        return false;
    }
    MeshActor->SetActorLabel(FString::Printf(
        TEXT("Atlas Material %d | Mesh %d | %s"),
        Sample.MaterialId,
        Sample.MeshId,
        Sample.Material->bRoughnessFromAlbedoAlpha ? TEXT("Albedo-alpha roughness") : TEXT("Scalar roughness")));
    MeshActor->GetStaticMeshComponent()->SetStaticMesh(Sample.MeshResult.StaticMesh);
    MeshActor->GetStaticMeshComponent()->SetMaterial(0, Sample.MaterialResult.MaterialInstance);
    MeshActor->GetStaticMeshComponent()->SetMobility(EComponentMobility::Static);
    const FBoxSphereBounds MeshBounds = Sample.MeshResult.StaticMesh->GetBounds();
    MeshActor->SetActorLocation(-MeshBounds.Origin);

    ADirectionalLight* DirectionalLight = nullptr;
    ARectLight* RectLight = nullptr;
    AStaticMeshActor* Backdrop = nullptr;
    ACameraActor* ReviewCamera = nullptr;
    if (!SpawnDeterministicReviewRig(
            World, MeshActor, DirectionalLight, RectLight, Backdrop, ReviewCamera, OutError))
    {
        World->CleanupWorld(true, true);
        return false;
    }
    if (MeshActor->GetStaticMeshComponent()->GetStaticMesh() != Sample.MeshResult.StaticMesh
        || MeshActor->GetStaticMeshComponent()->GetMaterial(0) != Sample.MaterialResult.MaterialInstance
        || Sample.MaterialResult.MaterialInstance->Parent != Sample.MaterialResult.MasterMaterial
        || !World->GetWorldSettings()->bForceNoPrecomputedLighting)
    {
        OutError = TEXT("Generated actor, mesh slot, MI parent, or no-bake validation failed.");
        World->CleanupWorld(true, true);
        return false;
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
        OutError = FString::Printf(TEXT("Failed saving map %s."), *LevelFilename);
        World->CleanupWorld(true, true);
        return false;
    }
    World->CleanupWorld(true, true);
    return true;
}

bool ValidateAndImportSample(
    const FString& PackDirectory,
    const FString& Destination,
    const AtlasEft::FManifest& Manifest,
    const TArray<AtlasEft::FMaterialDesc>& Materials,
    int32 MaterialId,
    int32 MeshId,
    bool bExpectedRoughnessFromAlpha,
    FMaterialSample& OutSample,
    FString& OutError)
{
    if (!Materials.IsValidIndex(MaterialId) || !Manifest.Meshes.IsValidIndex(MeshId))
    {
        OutError = TEXT("Requested material or mesh id is outside the pack.");
        return false;
    }
    const AtlasEft::FMaterialDesc& Material = Materials[MaterialId];
    const AtlasEft::FMeshDesc& MeshDescription = Manifest.Meshes[MeshId];
    if (Material.Id != static_cast<uint32>(MaterialId)
        || MeshDescription.Submeshes.Num() != 1
        || MeshDescription.Submeshes[0].MaterialId != static_cast<uint32>(MaterialId))
    {
        OutError = FString::Printf(
            TEXT("Mesh %d is not a single-section consumer of material %d."), MeshId, MaterialId);
        return false;
    }
    if (Material.Role != TEXT("opaque")
        || Material.AlphaMode != AtlasEft::EMaterialAlphaMode::Opaque
        || Material.bRoughnessFromAlbedoAlpha != bExpectedRoughnessFromAlpha)
    {
        OutError = FString::Printf(
            TEXT("Material %d does not match the expected opaque roughness mode."), MaterialId);
        return false;
    }

    OutSample.MaterialId = MaterialId;
    OutSample.MeshId = MeshId;
    OutSample.Material = &Material;
    if (!AtlasEft::FMaterialImporter::ImportOpaqueMaterial(
            PackDirectory, Material, TEXT("/Game/Atlas/Common/Materials"),
            Destination + TEXT("/Materials"), Destination + TEXT("/Textures"),
            OutSample.MaterialResult, OutError)
        || !AtlasEft::FStaticMeshImporter::ImportMesh(
            PackDirectory, Manifest, static_cast<uint32>(MeshId), Destination + TEXT("/Meshes"),
            OutSample.MeshResult, OutError))
    {
        return false;
    }
    if (!OutSample.MeshResult.StaticMesh
        || OutSample.MeshResult.StaticMesh->GetStaticMaterials().Num() != 1)
    {
        OutError = TEXT("Imported sample mesh does not have exactly one material slot.");
        return false;
    }
    OutSample.MeshResult.StaticMesh->PreEditChange(nullptr);
    OutSample.MeshResult.StaticMesh->SetMaterial(0, OutSample.MaterialResult.MaterialInstance);
    OutSample.MeshResult.StaticMesh->PostEditChange();
    if (OutSample.MeshResult.StaticMesh->GetMaterial(0) != OutSample.MaterialResult.MaterialInstance
        || !SaveAsset(OutSample.MeshResult.StaticMesh, OutError))
    {
        OutError = TEXT("Mesh material assignment failed: ") + OutError;
        return false;
    }
    return true;
}
} // namespace

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
    int32 SecondMaterialId = 974;
    int32 SecondMeshId = 1240;
    FParse::Value(*Params, TEXT("MaterialId="), MaterialId);
    FParse::Value(*Params, TEXT("MeshId="), MeshId);
    FParse::Value(*Params, TEXT("SecondMaterialId="), SecondMaterialId);
    FParse::Value(*Params, TEXT("SecondMeshId="), SecondMeshId);
    if (MaterialId < 0 || MeshId < 0 || SecondMaterialId < 0 || SecondMeshId < 0)
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("Material and mesh ids must be non-negative."));
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
    TArray<AtlasEft::FMaterialDesc> Materials;
    if (!AtlasEft::FPackReader::ReadManifest(PackDirectory, Manifest, Report)
        || !AtlasEft::FPackReader::ReadMaterials(PackDirectory, Manifest, Materials, Report))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("%s"), *Report.ToText());
        return 1;
    }

    FString Error;
    FMaterialSample FirstSample;
    FMaterialSample SecondSample;
    if (!ValidateAndImportSample(
            PackDirectory, Destination, Manifest, Materials,
            MaterialId, MeshId, true, FirstSample, Error)
        || !ValidateAndImportSample(
            PackDirectory, Destination, Manifest, Materials,
            SecondMaterialId, SecondMeshId, false, SecondSample, Error))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("%s"), *Error);
        return 1;
    }

    FString FirstLevel;
    FString SecondLevel;
    if (!BuildReviewWorld(Destination, FirstSample, FirstLevel, Error)
        || !BuildReviewWorld(Destination, SecondSample, SecondLevel, Error))
    {
        UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Error, TEXT("%s"), *Error);
        return 1;
    }

    UE_LOG(LogAtlasEftBuildMaterialSampleCommandlet, Display,
        TEXT("Atlas material comparison: PASS\n")
        TEXT("Sample A: material=%d mesh=%d level=%s roughness=albedo-alpha specMap=%s\n")
        TEXT("Sample B: material=%d mesh=%d level=%s roughness=%.3f specMap=%s\n")
        TEXT("Rig: one directional Movable at intensity 5 with shadows off; one RectLight Movable at 20 cd; fixed manual exposure EV100 0; Engine neutral backdrop; camera targets mesh bounds; ForceNoPrecomputedLighting=true."),
        MaterialId, MeshId, *FirstLevel,
        FirstSample.MaterialResult.SpecularProvenanceTexture ? TEXT("present (provenance only)") : TEXT("absent"),
        SecondMaterialId, SecondMeshId, *SecondLevel, SecondSample.Material->Roughness,
        SecondSample.MaterialResult.SpecularProvenanceTexture ? TEXT("present (provenance only)") : TEXT("absent"));
    return 0;
}
