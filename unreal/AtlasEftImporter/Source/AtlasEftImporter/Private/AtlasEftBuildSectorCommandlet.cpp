#include "AtlasEftBuildSectorCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AtlasEftMaterialImporter.h"
#include "Dom/JsonObject.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Materials/MaterialExpressionConstant3Vector.h"
#include "MaterialEditingLibrary.h"
#include "Engine/DirectionalLight.h"
#include "Engine/PostProcessVolume.h"
#include "Components/DirectionalLightComponent.h"
#include "GameFramework/WorldSettings.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "AtlasEftCoordinate.h"
#include "AtlasEftPackReader.h"
#include "AtlasEftStaticMeshImporter.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMemory.h"
#include "HAL/PlatformTime.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftBuildSectorCommandlet, Log, All);

namespace
{
struct FInstanceCandidate
{
    const AtlasEft::FInstanceDesc* Instance = nullptr;
    double DistanceSquared = 0.0;
};

FRotator MakeStableRotatorFromQuat(const FQuat& Rotation)
{
    // FQuat::Rotator() intentionally snaps to Pitch=+/-90 and Roll=0 near its
    // singularity threshold. That is lossy for valid transforms just outside the
    // exact singularity. Use the non-singular analytic conversion and let the
    // caller choose it only when it round-trips more accurately.
    const double SingularityTest = Rotation.Z * Rotation.X - Rotation.W * Rotation.Y;
    const double YawY = 2.0 * (Rotation.W * Rotation.Z + Rotation.X * Rotation.Y);
    const double YawX = 1.0 - 2.0 * (FMath::Square(Rotation.Y) + FMath::Square(Rotation.Z));
    const double Pitch = FMath::Asin(FMath::Clamp(2.0 * SingularityTest, -1.0, 1.0));
    const double Yaw = FMath::Atan2(YawY, YawX);
    const double Roll = FMath::Atan2(-2.0 * (Rotation.W * Rotation.X + Rotation.Y * Rotation.Z),
        1.0 - 2.0 * (FMath::Square(Rotation.X) + FMath::Square(Rotation.Y)));
    return FRotator(FMath::RadiansToDegrees(Pitch), FMath::RadiansToDegrees(Yaw), FMath::RadiansToDegrees(Roll));
}

FTransform MakeUnrealTransform(const AtlasEft::FInstanceDesc& Instance)
{
    double AtlasAffine[12];
    for (int32 Index = 0; Index < 12; ++Index)
    {
        AtlasAffine[Index] = Instance.Affine[Index];
    }
    double UnrealAffine[12] = {};
    AtlasEft::FCoordinate::AffineToUnreal(AtlasAffine, UnrealAffine);

    // Atlas stores column-vector L|t. UE's FMatrix uses row-vector storage, hence L transpose and t in row 3.
    const FMatrix Matrix(
        FPlane(UnrealAffine[0], UnrealAffine[4], UnrealAffine[8], 0.0),
        FPlane(UnrealAffine[1], UnrealAffine[5], UnrealAffine[9], 0.0),
        FPlane(UnrealAffine[2], UnrealAffine[6], UnrealAffine[10], 0.0),
        FPlane(UnrealAffine[3], UnrealAffine[7], UnrealAffine[11], 1.0));
    return FTransform(Matrix);
}

void CountFilesRecursive(const FString& Directory, int32& OutFileCount, int64& OutBytes)
{
    TArray<FString> Files;
    IFileManager::Get().FindFilesRecursive(Files, *Directory, TEXT("*"), true, false, true);
    OutFileCount = Files.Num();
    OutBytes = 0;
    for (const FString& File : Files)
    {
        const int64 FileSize = IFileManager::Get().FileSize(*File);
        if (FileSize > 0)
        {
            OutBytes += FileSize;
        }
    }
}
}

UAtlasEftBuildSectorCommandlet::UAtlasEftBuildSectorCommandlet()
{
    IsClient = false;
    IsEditor = true;
    IsServer = false;
    LogToConsole = true;
    ShowErrorCount = true;
}

int32 UAtlasEftBuildSectorCommandlet::Main(const FString& Params)
{
    const double StartSeconds = FPlatformTime::Seconds();
    FString PackDirectory;
    if (!FParse::Value(*Params, TEXT("Pack="), PackDirectory) || PackDirectory.IsEmpty())
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Missing -Pack=<path to map.eftpack directory>."));
        return 2;
    }
    PackDirectory.TrimQuotesInline();

    int32 RootId = -1, Level = -1;
    uint32 AncestorId = 0;
    int32 ExpectedCount = 0;
    const bool bHasGrandparent = FParse::Value(*Params, TEXT("GrandparentId="), AncestorId);
    const bool bHasLevel = FParse::Value(*Params, TEXT("Level="), Level);
    const bool bDryRun = FParse::Param(*Params, TEXT("DryRun"));
    FParse::Value(*Params, TEXT("RootId="), RootId);
    FParse::Value(*Params, TEXT("ExpectedCount="), ExpectedCount);
    const bool bCompleteRoot = !bHasGrandparent;
    if (RootId < 0 || ExpectedCount < 1
        || (bCompleteRoot && bHasLevel)
        || (!bCompleteRoot && (Level < 0 || AncestorId == 0)))
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Require RootId and positive ExpectedCount; group mode also requires Level and nonzero GrandparentId, while complete-root mode omits both."));
        return 2;
    }
    FString Destination = bCompleteRoot
        ? FString::Printf(TEXT("/Game/Atlas/Sectors/Root_%03d"), RootId)
        : TEXT("/Game/Atlas/Labyrinth/Pilot");
    FParse::Value(*Params, TEXT("Destination="), Destination);
    Destination.TrimQuotesInline();
    Destination.RemoveFromEnd(TEXT("/"));
    if (!FPackageName::IsValidLongPackageName(Destination))
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Invalid destination: %s"), *Destination);
        return 2;
    }

    AtlasEft::FAuditReport Report;
    Report.PackDirectory = PackDirectory;
    AtlasEft::FManifest Manifest;
    if (!AtlasEft::FPackReader::ReadManifest(PackDirectory, Manifest, Report))
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("%s"), *Report.ToText());
        return 1;
    }
    TArray<AtlasEft::FInstanceDesc> Instances;
    if (!AtlasEft::FPackReader::ReadInstances(PackDirectory, Manifest, Instances, Report))
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("%s"), *Report.ToText());
        return 1;
    }
    TArray<FInstanceCandidate> Candidates;
    int32 RootRecordCount = 0;
    int32 InactiveRootRecordCount = 0;
    int32 HigherLodRootRecordCount = 0;
    TSet<uint64> SelectedInstanceIds;
    TSet<uint32> RequiredMeshIds;
    int64 InstanceSlotCount = 0;
    for (const AtlasEft::FInstanceDesc& Instance : Instances)
    {
        if (Instance.RootId != static_cast<uint32>(RootId)) continue;
        ++RootRecordCount;
        if (Instance.IsInactive()) ++InactiveRootRecordCount;
        if (Instance.LodIndex > 0) ++HigherLodRootRecordCount;
        if (Instance.IsInactive() || Instance.LodIndex > 0) continue;
        if (!bCompleteRoot && (Instance.Level != static_cast<uint32>(Level) || Instance.GrandparentId != AncestorId)) continue;
        const auto Analysis = AtlasEft::FCoordinate::AnalyzeAffine(Instance.Affine);
        if (!Analysis.bFinite || Analysis.bSheared || Analysis.bDegenerate || Analysis.bMirrored)
        {
            UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Unsupported affine in selected group: %llu"), Instance.Index);
            return 1;
        }
        if (SelectedInstanceIds.Contains(Instance.Index))
        {
            UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Duplicate selected instance id %llu."), Instance.Index);
            return 1;
        }
        SelectedInstanceIds.Add(Instance.Index);
        if (!Manifest.Meshes.IsValidIndex(Instance.MeshId))
        {
            UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Selected instance %llu has invalid mesh id %u."), Instance.Index, Instance.MeshId);
            return 1;
        }
        RequiredMeshIds.Add(Instance.MeshId);
        InstanceSlotCount += Manifest.Meshes[Instance.MeshId].Submeshes.Num();
        Candidates.Add({&Instance, 0.0});
    }
    // Structural selection only: never silently truncate or spatially fill a root/group.
    if (!Manifest.Roots.IsValidIndex(RootId) || Candidates.Num() != ExpectedCount
        || Candidates.Num() != SelectedInstanceIds.Num())
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Selection count %d differs from expected %d or selected ids are not unique."), Candidates.Num(), ExpectedCount);
        return 1;
    }
    TArray<AtlasEft::FMaterialDesc> Materials;
    if (!AtlasEft::FPackReader::ReadMaterials(PackDirectory, Manifest, Materials, Report)) return 1;
    TSet<uint32> RequiredMaterials;
    int64 UniqueMeshSlotCount = 0;
    for (uint32 MeshId : RequiredMeshIds)
    {
        const auto& Mesh = Manifest.Meshes[MeshId];
        UniqueMeshSlotCount += Mesh.Submeshes.Num();
        for (const auto& Submesh : Mesh.Submeshes)
        {
            if (!Materials.IsValidIndex(Submesh.MaterialId))
            {
                UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Mesh %u references missing material %u."), MeshId, Submesh.MaterialId);
                return 1;
            }
            RequiredMaterials.Add(Submesh.MaterialId);
        }
    }
    TArray<uint32> MaterialIds = RequiredMaterials.Array();
    MaterialIds.Sort();
    TMap<uint32, TObjectPtr<UMaterialInterface>> ImportedMaterials;
    TSet<uint32> FallbackIds;
    TSet<uint32> SupportedIds;
    TSet<FString> RequiredTexturePaths;
    int64 SupportedInstanceSlots = 0;
    int64 FallbackInstanceSlots = 0;
    for (const FInstanceCandidate& Candidate : Candidates)
    {
        for (const auto& Submesh : Manifest.Meshes[Candidate.Instance->MeshId].Submeshes)
        {
            const auto& Material = Materials[Submesh.MaterialId];
            const bool bAlbedo = Material.Textures.ContainsByPredicate([](const auto& T) { return T.Semantic == AtlasEft::ETextureSemantic::Albedo; });
            const bool bNormal = Material.Textures.ContainsByPredicate([](const auto& T) { return T.Semantic == AtlasEft::ETextureSemantic::Normal; });
            const bool bSupported = Material.Role == TEXT("opaque") && Material.AlphaMode == AtlasEft::EMaterialAlphaMode::Opaque
                && Material.bDoubleSided && !Material.bUnsupportedOpaqueFeatures && bAlbedo && bNormal;
            if (bSupported)
            {
                SupportedIds.Add(Submesh.MaterialId);
                ++SupportedInstanceSlots;
                for (const auto& Texture : Material.Textures) RequiredTexturePaths.Add(Texture.Path);
            }
            else
            {
                FallbackIds.Add(Submesh.MaterialId);
                ++FallbackInstanceSlots;
            }
        }
    }
    if (SupportedInstanceSlots + FallbackInstanceSlots != InstanceSlotCount
        || SupportedIds.Num() + FallbackIds.Num() != MaterialIds.Num())
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Preflight material/slot accounting does not reconcile."));
        return 1;
    }
    FString Error;
    const FString RootName = Manifest.Roots.IsValidIndex(RootId) ? Manifest.Roots[RootId] : FString();
    FString Validation = FString::Printf(TEXT("Selection=%s Root=%d (%s) Level=%s Grandparent=%s Expected=%d RootRecords=%d Inactive=%d HigherLOD=%d\n"),
        bCompleteRoot ? TEXT("complete-root-active-lod0") : TEXT("root-level-grandparent"), RootId, *RootName,
        bHasLevel ? *FString::FromInt(Level) : TEXT("all"), bHasGrandparent ? *FString::Printf(TEXT("%u"), AncestorId) : TEXT("all"),
        ExpectedCount, RootRecordCount, InactiveRootRecordCount, HigherLodRootRecordCount);
    Validation += FString::Printf(TEXT("Preflight instances=%d meshes=%d materials=%d uniqueMeshSlots=%lld instanceSlots=%lld supportedInstanceSlots=%lld fallbackInstanceSlots=%lld\n"),
        Candidates.Num(), RequiredMeshIds.Num(), MaterialIds.Num(), UniqueMeshSlotCount, InstanceSlotCount, SupportedInstanceSlots, FallbackInstanceSlots);
    TArray<uint32> MeshIds = RequiredMeshIds.Array();
    MeshIds.Sort();
    if (bDryRun)
    {
        Validation += TEXT("DRYRUN PASS; no assets or map created.\n");
        Validation += FString::Printf(TEXT("Unique supported texture paths=%d\n"), RequiredTexturePaths.Num());
        Validation += TEXT("MeshIds=");
        for (uint32 Id : MeshIds) Validation += FString::Printf(TEXT("%u,"), Id);
        Validation += TEXT("\nMaterialIds=");
        for (uint32 Id : MaterialIds) Validation += FString::Printf(TEXT("%u,"), Id);
        Validation += TEXT("\n");
        const FString ReportSuffix = bCompleteRoot ? FString::Printf(TEXT("Root%03d_DryRun"), RootId) : TEXT("Group_DryRun");
        const FString TextPath = FPaths::ProjectSavedDir() / FString::Printf(TEXT("AtlasSector_%s.txt"), *ReportSuffix);
        const FString JsonPath = FPaths::ProjectSavedDir() / FString::Printf(TEXT("AtlasSector_%s.json"), *ReportSuffix);
        TSharedPtr<FJsonObject> JsonObject = MakeShared<FJsonObject>();
        JsonObject->SetStringField(TEXT("status"), TEXT("DRYRUN_PASS"));
        JsonObject->SetStringField(TEXT("selection"), bCompleteRoot ? TEXT("complete-root-active-lod0") : TEXT("root-level-grandparent"));
        JsonObject->SetNumberField(TEXT("rootId"), RootId);
        JsonObject->SetStringField(TEXT("rootName"), RootName);
        JsonObject->SetNumberField(TEXT("expectedInstances"), ExpectedCount);
        JsonObject->SetNumberField(TEXT("selectedInstances"), Candidates.Num());
        JsonObject->SetNumberField(TEXT("rootRecords"), RootRecordCount);
        JsonObject->SetNumberField(TEXT("inactiveRootRecords"), InactiveRootRecordCount);
        JsonObject->SetNumberField(TEXT("higherLodRootRecords"), HigherLodRootRecordCount);
        JsonObject->SetNumberField(TEXT("uniqueMeshes"), RequiredMeshIds.Num());
        JsonObject->SetNumberField(TEXT("uniqueMaterials"), MaterialIds.Num());
        JsonObject->SetNumberField(TEXT("supportedMaterials"), SupportedIds.Num());
        JsonObject->SetNumberField(TEXT("fallbackMaterials"), FallbackIds.Num());
        JsonObject->SetNumberField(TEXT("uniqueMeshSlots"), static_cast<double>(UniqueMeshSlotCount));
        JsonObject->SetNumberField(TEXT("instanceSlots"), static_cast<double>(InstanceSlotCount));
        JsonObject->SetNumberField(TEXT("supportedInstanceSlots"), static_cast<double>(SupportedInstanceSlots));
        JsonObject->SetNumberField(TEXT("fallbackInstanceSlots"), static_cast<double>(FallbackInstanceSlots));
        JsonObject->SetNumberField(TEXT("uniqueSupportedTexturePaths"), RequiredTexturePaths.Num());
        const FPlatformMemoryStats MemoryStats = FPlatformMemory::GetStats();
        JsonObject->SetNumberField(TEXT("usedPhysicalBytes"), static_cast<double>(MemoryStats.UsedPhysical));
        JsonObject->SetNumberField(TEXT("peakUsedPhysicalBytes"), static_cast<double>(MemoryStats.PeakUsedPhysical));
        TArray<TSharedPtr<FJsonValue>> IdValues;
        for (const FInstanceCandidate& Candidate : Candidates) IdValues.Add(MakeShared<FJsonValueNumber>(static_cast<double>(Candidate.Instance->Index)));
        JsonObject->SetArrayField(TEXT("instanceIds"), IdValues);
        TArray<TSharedPtr<FJsonValue>> MeshIdValues;
        for (uint32 Id : MeshIds) MeshIdValues.Add(MakeShared<FJsonValueNumber>(Id));
        JsonObject->SetArrayField(TEXT("meshIds"), MeshIdValues);
        FString JsonText;
        const TSharedRef<TJsonWriter<>> JsonWriter = TJsonWriterFactory<>::Create(&JsonText);
        FJsonSerializer::Serialize(JsonObject.ToSharedRef(), JsonWriter);
        if (!FFileHelper::SaveStringToFile(Validation, *TextPath) || !FFileHelper::SaveStringToFile(JsonText, *JsonPath))
        {
            UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Could not save dry-run reports."));
            return 1;
        }
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Display, TEXT("%s"), *Validation);
        return 0;
    }

    const FString MasterPath = TEXT("/Game/Atlas/Common/Materials");
    UMaterial* ExistingMaster = LoadObject<UMaterial>(nullptr, TEXT("/Game/Atlas/Common/Materials/M_AtlasOpaque.M_AtlasOpaque"));
    if (!ExistingMaster)
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Required existing master material is missing: /Game/Atlas/Common/Materials/M_AtlasOpaque."));
        return 1;
    }
    for (uint32 Id : MaterialIds)
    {
        if (!Materials.IsValidIndex(Id)) return 1;
        const auto& Material = Materials[Id];
        const bool bAlbedo = Material.Textures.ContainsByPredicate([](const auto& T) { return T.Semantic == AtlasEft::ETextureSemantic::Albedo; });
        const bool bNormal = Material.Textures.ContainsByPredicate([](const auto& T) { return T.Semantic == AtlasEft::ETextureSemantic::Normal; });
        const bool bSupported = Material.Role == TEXT("opaque") && Material.AlphaMode == AtlasEft::EMaterialAlphaMode::Opaque
            && Material.bDoubleSided && !Material.bUnsupportedOpaqueFeatures && bAlbedo && bNormal;
        if (bSupported)
        {
            AtlasEft::FMaterialImportResult Result;
            if (!AtlasEft::FMaterialImporter::ImportOpaqueMaterial(PackDirectory, Material,
                MasterPath, Destination / TEXT("Materials"), Destination / TEXT("Textures"), Result, Error, true))
            {
                UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Material %u: %s"), Id, *Error);
                return 1;
            }
            ImportedMaterials.Add(Id, Result.MaterialInstance);
            SupportedIds.Add(Id);
            for (const auto& Texture : Material.Textures) RequiredTexturePaths.Add(Texture.Path);
        }
        else
        {
            FallbackIds.Add(Id);
            const FString Name = TEXT("M_AtlasUnsupported_Magenta");
            UPackage* P = CreatePackage(*(Destination / TEXT("Materials") / Name));
            if (!P)
            {
                UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Could not create fallback material package for material %u."), Id);
                return 1;
            }
            P->FullyLoad();
            UMaterial* Fallback = FindObject<UMaterial>(P, *Name);
            if (!Fallback)
            {
                Fallback = NewObject<UMaterial>(P, *Name, RF_Public | RF_Standalone);
                Fallback->TwoSided = true;
                Fallback->SetShadingModel(MSM_Unlit);
                auto* Color = Cast<UMaterialExpressionConstant3Vector>(UMaterialEditingLibrary::CreateMaterialExpression(Fallback, UMaterialExpressionConstant3Vector::StaticClass()));
                Color->Constant = FLinearColor(1, 0, 1);
                if (!UMaterialEditingLibrary::ConnectMaterialProperty(Color, TEXT(""), MP_EmissiveColor)
                    || !UMaterialEditingLibrary::RecompileMaterial(Fallback).IsEmpty()) return 1;
                Fallback->PostEditChange();
                FAssetRegistryModule::AssetCreated(Fallback);
                FSavePackageArgs Args; Args.TopLevelFlags = RF_Public | RF_Standalone;
                if (!UPackage::SavePackage(P, Fallback, *FPackageName::LongPackageNameToFilename(P->GetName(), FPackageName::GetAssetPackageExtension()), Args)) return 1;
            }
            ImportedMaterials.Add(Id, Fallback);
        }
        Validation += FString::Printf(TEXT("Material %u %s %s\n"), Id, FallbackIds.Contains(Id) ? TEXT("FALLBACK") : TEXT("OPAQUE"), *ImportedMaterials[Id]->GetPathName());
    }
    TMap<uint32, TObjectPtr<UStaticMesh>> ImportedMeshes;
    uint64 TotalTriangles = 0;
    for (const FInstanceCandidate& Candidate : Candidates)
    {
        const uint32 MeshId = Candidate.Instance->MeshId;
        if (ImportedMeshes.Contains(MeshId))
        {
            continue;
        }
        AtlasEft::FStaticMeshImportResult MeshResult;
        if (!AtlasEft::FStaticMeshImporter::ImportMesh(
            PackDirectory,
            Manifest,
            MeshId,
            Destination + TEXT("/Meshes"),
            MeshResult,
            Error))
        {
            UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Mesh %u failed: %s"), MeshId, *Error);
            return 1;
        }
        UStaticMesh* Mesh = MeshResult.StaticMesh;
        if (!Mesh)
        {
            UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Mesh importer returned a null asset for mesh %u."), MeshId);
            return 1;
        }
        const auto& Sections = Manifest.Meshes[MeshId].Submeshes;
        if (Mesh->GetStaticMaterials().Num() != Sections.Num()) return 1;
        for (int32 Slot = 0; Slot < Sections.Num(); ++Slot)
        {
            Mesh->SetMaterial(Slot, ImportedMaterials[Sections[Slot].MaterialId]);
            if (Mesh->GetMaterial(Slot) != ImportedMaterials[Sections[Slot].MaterialId]
                || Mesh->GetStaticMaterials()[Slot].MaterialSlotName != FName(*FString::Printf(TEXT("AtlasMaterial_%u"), Sections[Slot].MaterialId))) return 1;
        }
        Mesh->PostEditChange();
        FSavePackageArgs Args; Args.TopLevelFlags = RF_Public | RF_Standalone;
        if (!UPackage::SavePackage(Mesh->GetOutermost(), Mesh, *MeshResult.PackageFilename, Args)) return 1;
        ImportedMeshes.Add(MeshId, Mesh);
        TotalTriangles += MeshResult.RenderTriangleCount;
    }

    FString LevelName;
    if (bCompleteRoot)
    {
        FString RootSuffix = RootName;
        RootSuffix.ReplaceInline(TEXT("SBG_Labyrinth_"), TEXT(""));
        RootSuffix.ReplaceInline(TEXT("_"), TEXT(""));
        LevelName = FString::Printf(TEXT("L_Atlas_Root%03d_%s"), RootId, *RootSuffix);
    }
    else
    {
        LevelName = FString::Printf(TEXT("L_Atlas_Sector_R%02d_L%u_G%u"), RootId, Level, AncestorId);
    }
    const FString PackageName = Destination + TEXT("/") + LevelName;
    const FString PackageFilename = FPackageName::LongPackageNameToFilename(PackageName, FPackageName::GetMapPackageExtension());
    // Reproducible replacement is confined to this generated map in the requested namespace.
    UPackage* Package = CreatePackage(*PackageName);
    if (!Package)
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Could not create level package %s."), *PackageName);
        return 1;
    }
    UWorld* World = UWorld::CreateWorld(EWorldType::Editor, false, FName(*LevelName), Package);
    if (!World)
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Could not create test world."));
        return 1;
    }
    // The generated package is reconstructed completely, including on a repeated import.
    Package->MarkAsFullyLoaded();
    World->SetFlags(RF_Public | RF_Standalone);
    World->GetWorldSettings()->bForceNoPrecomputedLighting = true;

    FBox ActorBounds(EForceInit::ForceInit);
    double MaxTranslationErrorCm = 0.0;
    uint64 MaxTranslationErrorInstance = 0;
    double MaxCornerErrorCm = 0.0;
    uint64 MaxCornerErrorInstance = 0;
    FVector MaxCornerLocal = FVector::ZeroVector;
    FVector MaxCornerExpected = FVector::ZeroVector;
    FVector MaxCornerActual = FVector::ZeroVector;
    FTransform MaxCornerSourceTransform = FTransform::Identity;
    FTransform MaxCornerActorTransform = FTransform::Identity;
    FTransform MaxCornerComponentWorldTransform = FTransform::Identity;
    FTransform MaxCornerComponentRelativeTransform = FTransform::Identity;
    FVector MaxCornerFromSpawnTransform = FVector::ZeroVector;
    FQuat MaxCornerActorQuat = FQuat::Identity;
    FQuat MaxCornerSourceQuat = FQuat::Identity;
    FRotator MaxCornerStableRotator = FRotator::ZeroRotator;
    FRotator MaxCornerRelativeRotator = FRotator::ZeroRotator;
    bool bMaxCornerAppliedStableRotator = false;
    float MaxCornerAffine[12] = {};
    int32 ValidatedSlots = 0;
    for (const FInstanceCandidate& Candidate : Candidates)
    {
        const AtlasEft::FInstanceDesc& Instance = *Candidate.Instance;
        TObjectPtr<UStaticMesh>* MeshPtr = ImportedMeshes.Find(Instance.MeshId);
        if (!MeshPtr || !MeshPtr->Get())
        {
            UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Imported mesh %u was lost before scene assembly."), Instance.MeshId);
            return 1;
        }

        const FTransform Transform = MakeUnrealTransform(Instance);
        FActorSpawnParameters SpawnParameters;
        SpawnParameters.Name = FName(*FString::Printf(TEXT("Atlas_I%06llu_M%04u"), Instance.Index, Instance.MeshId));
        AStaticMeshActor* Actor = World->SpawnActor<AStaticMeshActor>(AStaticMeshActor::StaticClass(), Transform, SpawnParameters);
        if (!Actor)
        {
            UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Could not spawn instance %llu."), Instance.Index);
            return 1;
        }
        Actor->SetActorLabel(FString::Printf(TEXT("Atlas Instance %llu | Mesh %u | Level %u | Root %u"), Instance.Index, Instance.MeshId, Instance.Level, Instance.RootId));
        Actor->GetStaticMeshComponent()->SetStaticMesh(MeshPtr->Get());
        Actor->GetStaticMeshComponent()->SetMobility(EComponentMobility::Static);
        // SpawnActor's actor/root setup can round-trip the rotation through FRotator,
        // which loses quaternion precision near Euler singularities (e.g. Pitch=90°).
        // Reapply the source FTransform directly so the world affine validation sees
        // the exact quaternion and not the lossy rotator conversion.
        Actor->SetActorTransform(Transform, false, nullptr, ETeleportType::TeleportPhysics);
        const FQuat SourceRotation = Transform.GetRotation();
        const FRotator StandardRotator = SourceRotation.Rotator();
        const FRotator StableRotator = MakeStableRotatorFromQuat(SourceRotation);
        const FQuat NormalizedSourceRotation = SourceRotation.GetNormalized();
        const FQuat StableRoundTrip = StableRotator.Quaternion().GetNormalized();
        const FQuat StandardRoundTrip = StandardRotator.Quaternion().GetNormalized();
        const double StableMismatch = 1.0 - FMath::Clamp(FMath::Abs(NormalizedSourceRotation | StableRoundTrip), 0.0, 1.0);
        const double StandardMismatch = 1.0 - FMath::Clamp(FMath::Abs(NormalizedSourceRotation | StandardRoundTrip), 0.0, 1.0);
        const bool bUseStableRotator = StableMismatch < StandardMismatch;
        if (bUseStableRotator)
        {
            USceneComponent* RootComponent = Actor->GetRootComponent();
            RootComponent->SetRelativeRotationExact(StableRotator, false, nullptr, ETeleportType::TeleportPhysics);
            // SetRelativeRotationExact writes the raw FRotator after its movement
            // update; explicitly refresh the cached component/world quaternion.
            RootComponent->UpdateComponentToWorld(EUpdateTransformFlags::None, ETeleportType::TeleportPhysics);
        }
        if (Actor->GetStaticMeshComponent()->GetStaticMesh() != MeshPtr->Get()
            || Actor->GetStaticMeshComponent()->GetNumMaterials() != Manifest.Meshes[Instance.MeshId].Submeshes.Num())
        {
            UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Instance %llu has an invalid mesh or slot reference."), Instance.Index);
            return 1;
        }

        const FVector ExpectedPosition = AtlasEft::FCoordinate::PositionToUnreal(FVector3d(Instance.GetAtlasTranslation()));
        const double TranslationErrorCm = FVector::Distance(Actor->GetActorLocation(), ExpectedPosition);
        if (TranslationErrorCm > MaxTranslationErrorCm)
        {
            MaxTranslationErrorCm = TranslationErrorCm;
            MaxTranslationErrorInstance = Instance.Index;
        }
        Actor->SetFolderPath(FName(*FString::Printf(TEXT("Atlas/Root_%03d/Level_%u/Ancestor_%u/Parent_%u"),
            RootId, Instance.Level, Instance.GrandparentId, Instance.ParentId)));
        Actor->Tags.Add(FName(*FString::Printf(TEXT("AtlasInstance=%llu"), Instance.Index)));
        Actor->Tags.Add(FName(*FString::Printf(TEXT("AtlasLODGroup=%d;LODIndex=%d"), Instance.LodGroup, Instance.LodIndex)));
        for (int32 Slot = 0; Slot < Manifest.Meshes[Instance.MeshId].Submeshes.Num(); ++Slot)
        {
            const uint32 Id = Manifest.Meshes[Instance.MeshId].Submeshes[Slot].MaterialId;
            if (Actor->GetStaticMeshComponent()->GetMaterial(Slot) != ImportedMaterials[Id]) return 1;
            ++ValidatedSlots;
        }
        const FBox Local = MeshPtr->Get()->GetBoundingBox();
        FBox ExpectedBounds(ForceInit);
        for (int32 Corner = 0; Corner < 8; ++Corner)
        {
            const FVector P((Corner & 1) ? Local.Max.X : Local.Min.X,
                (Corner & 2) ? Local.Max.Y : Local.Min.Y, (Corner & 4) ? Local.Max.Z : Local.Min.Z);
            const FVector3d AtlasP(-P.Y / 100.0, P.Z / 100.0, P.X / 100.0);
            const auto& A = Instance.Affine;
            const FVector3d AtlasWorld(A[0]*AtlasP.X+A[1]*AtlasP.Y+A[2]*AtlasP.Z+A[3],
                A[4]*AtlasP.X+A[5]*AtlasP.Y+A[6]*AtlasP.Z+A[7],
                A[8]*AtlasP.X+A[9]*AtlasP.Y+A[10]*AtlasP.Z+A[11]);
            const FVector Expected = AtlasEft::FCoordinate::PositionToUnreal(AtlasWorld);
            const double CornerErrorCm = FVector::Distance(Expected, Actor->GetActorTransform().TransformPosition(P));
            if (CornerErrorCm > MaxCornerErrorCm)
            {
                MaxCornerErrorCm = CornerErrorCm;
                MaxCornerErrorInstance = Instance.Index;
                MaxCornerLocal = P;
                MaxCornerExpected = Expected;
                MaxCornerActual = Actor->GetActorTransform().TransformPosition(P);
                MaxCornerSourceTransform = Transform;
                MaxCornerActorTransform = Actor->GetActorTransform();
                MaxCornerComponentWorldTransform = Actor->GetStaticMeshComponent()->GetComponentTransform();
                MaxCornerComponentRelativeTransform = Actor->GetStaticMeshComponent()->GetRelativeTransform();
                MaxCornerFromSpawnTransform = Transform.TransformPosition(P);
                MaxCornerActorQuat = Actor->GetActorQuat();
                MaxCornerSourceQuat = Transform.GetRotation();
                MaxCornerStableRotator = StableRotator;
                MaxCornerRelativeRotator = Actor->GetRootComponent()->GetRelativeRotation();
                bMaxCornerAppliedStableRotator = bUseStableRotator;
                FMemory::Memcpy(MaxCornerAffine, Instance.Affine, sizeof(MaxCornerAffine));
            }
            ExpectedBounds += Expected;
        }
        const FBox Actual = Actor->GetStaticMeshComponent()->CalcBounds(Actor->GetActorTransform()).GetBox();
        // UStaticMeshComponent::CalcBounds returns FBoxSphereBounds transformed by Unreal;
        // its conservative sphere-derived AABB can differ slightly from the exact 8-corner
        // geometric AABB. The independent transformed-corner check below remains tighter.
        constexpr double BoundsComparisonToleranceCm = 0.25;
        if (!Actual.IsValid || !Actual.Min.Equals(ExpectedBounds.Min, BoundsComparisonToleranceCm)
            || !Actual.Max.Equals(ExpectedBounds.Max, BoundsComparisonToleranceCm))
        {
            UE_LOG(LogAtlasEftBuildSectorCommandlet, Error,
                TEXT("World bounds mismatch for instance %llu: expected %s..%s, actual %s..%s."), Instance.Index,
                *ExpectedBounds.Min.ToString(), *ExpectedBounds.Max.ToString(), *Actual.Min.ToString(), *Actual.Max.ToString());
            World->CleanupWorld(true, true);
            return 1;
        }
        ActorBounds += Actual;
        Validation += FString::Printf(TEXT("Instance %llu mesh=%u parent=%u transform=%s\n"), Instance.Index, Instance.MeshId, Instance.ParentId, *Transform.ToHumanReadableString());
    }
    if (MaxTranslationErrorCm > 0.01 || MaxCornerErrorCm > 0.15)
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error,
            TEXT("Transform validation failed: max translation error %.9f cm at instance %llu; max corner error %.9f cm at instance %llu (limits 0.01/0.15 cm). Local=%s expected=%s actual=%s fromSpawn=%s transform=%s sourceQuat=%s standardRotator=%s stableRotator=%s appliedStable=%d relativeRotator=%s actorTransform=%s actorQuat=%s compWorld=%s compRelative=%s affine=[%.9f,%.9f,%.9f,%.9f;%.9f,%.9f,%.9f,%.9f;%.9f,%.9f,%.9f,%.9f]."),
            MaxTranslationErrorCm, MaxTranslationErrorInstance, MaxCornerErrorCm, MaxCornerErrorInstance,
            *MaxCornerLocal.ToString(), *MaxCornerExpected.ToString(), *MaxCornerActual.ToString(), *MaxCornerFromSpawnTransform.ToString(),
            *MaxCornerSourceTransform.ToHumanReadableString(), *MaxCornerSourceQuat.ToString(),
            *MaxCornerSourceQuat.Rotator().ToString(), *MaxCornerStableRotator.ToString(), bMaxCornerAppliedStableRotator ? 1 : 0, *MaxCornerRelativeRotator.ToString(),
            *MaxCornerActorTransform.ToHumanReadableString(), *MaxCornerActorQuat.ToString(),
            *MaxCornerComponentWorldTransform.ToHumanReadableString(), *MaxCornerComponentRelativeTransform.ToHumanReadableString(),
            MaxCornerAffine[0], MaxCornerAffine[1], MaxCornerAffine[2], MaxCornerAffine[3],
            MaxCornerAffine[4], MaxCornerAffine[5], MaxCornerAffine[6], MaxCornerAffine[7],
            MaxCornerAffine[8], MaxCornerAffine[9], MaxCornerAffine[10], MaxCornerAffine[11]);
        World->CleanupWorld(true, true);
        return 1;
    }
    int32 SpawnedInstanceActors = 0;
    for (AActor* Actor : World->GetCurrentLevel()->Actors)
    {
        if (Actor && Actor->IsA<AStaticMeshActor>()) ++SpawnedInstanceActors;
    }
    if (SpawnedInstanceActors != ExpectedCount || ImportedMeshes.Num() != RequiredMeshIds.Num()
        || ImportedMaterials.Num() != MaterialIds.Num() || ValidatedSlots != InstanceSlotCount
        || SupportedInstanceSlots + FallbackInstanceSlots != ValidatedSlots
        || SupportedIds.Num() + FallbackIds.Num() != MaterialIds.Num())
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error,
            TEXT("Final validation count mismatch: actors=%d/%d meshes=%d/%d materials=%d/%d slots=%d/%lld."),
            SpawnedInstanceActors, ExpectedCount, ImportedMeshes.Num(), RequiredMeshIds.Num(),
            ImportedMaterials.Num(), MaterialIds.Num(), ValidatedSlots, InstanceSlotCount);
        return 1;
    }

    ADirectionalLight* Light = World->SpawnActor<ADirectionalLight>();
    APostProcessVolume* PP = World->SpawnActor<APostProcessVolume>();
    ACameraActor* Camera = World->SpawnActor<ACameraActor>();
    if (!Light || !PP || !Camera) return 1;
    Light->SetActorRotation(FRotator(-35, -45, 0));
    Light->GetLightComponent()->SetMobility(EComponentMobility::Movable);
    Light->GetLightComponent()->SetIntensity(5);
    Light->GetLightComponent()->SetCastShadows(false);
    Light->SetActorLabel(TEXT("Atlas Review | Dynamic Directional"));
    PP->bUnbound = true;
    PP->Settings.bOverride_AutoExposureMethod = true;
    PP->Settings.AutoExposureMethod = EAutoExposureMethod::AEM_Manual;
    PP->Settings.bOverride_AutoExposureBias = true;
    PP->Settings.AutoExposureBias = 0;
    PP->Settings.bOverride_AutoExposureApplyPhysicalCameraExposure = true;
    PP->Settings.AutoExposureApplyPhysicalCameraExposure = false;
    const FVector Center = ActorBounds.GetCenter();
    const FVector CameraPosition = Center + FVector(1, 1, 0.25).GetSafeNormal() * ActorBounds.GetExtent().Size() * 3.2;
    Camera->SetActorLocation(CameraPosition);
    Camera->SetActorRotation((Center - CameraPosition).Rotation());
    Camera->GetCameraComponent()->SetFieldOfView(50);
    for (AActor* Rig : TArray<AActor*>{Light, PP, Camera}) Rig->SetFolderPath(TEXT("Atlas/Review"));
    Validation += FString::Printf(TEXT("Camera=%s Rotation=%s FOV=50\n"), *CameraPosition.ToString(), *Camera->GetActorRotation().ToString());
    World->UpdateWorldComponents(true, false);
    Package->MarkPackageDirty();
    FAssetRegistryModule::AssetCreated(World);
    FSavePackageArgs SaveArgs;
    SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
    SaveArgs.SaveFlags = SAVE_NoError;
    SaveArgs.bSlowTask = false;
    if (!UPackage::SavePackage(Package, World, *PackageFilename, SaveArgs))
    {
        UE_LOG(LogAtlasEftBuildSectorCommandlet, Error, TEXT("Failed saving map %s."), *PackageFilename);
        World->CleanupWorld(true, true);
        return 1;
    }

    FString ValidationPath;
    FString JsonPath;
    const double ElapsedSeconds = FPlatformTime::Seconds() - StartSeconds;
    int32 OutputAssetCount = 0;
    int64 OutputAssetBytes = 0;
    const FString ContentRelativeDirectory = Destination.RightChop(6);
    CountFilesRecursive(FPaths::Combine(FPaths::ProjectContentDir(), ContentRelativeDirectory), OutputAssetCount, OutputAssetBytes);
    const int64 SourceBytes = IFileManager::Get().FileSize(*(FPaths::Combine(PackDirectory, TEXT("manifest.json"))))
        + IFileManager::Get().FileSize(*(FPaths::Combine(PackDirectory, TEXT("instances.bin"))))
        + IFileManager::Get().FileSize(*(FPaths::Combine(PackDirectory, TEXT("meshes.bin"))))
        + IFileManager::Get().FileSize(*(FPaths::Combine(PackDirectory, TEXT("materials.json"))));
    const FPlatformMemoryStats MemoryStats = FPlatformMemory::GetStats();
    Validation += FString::Printf(TEXT("PASS instances=%d meshes=%d materials=%d supportedMaterials=%d fallbackMaterials=%d textures=%d uniqueMeshSlots=%lld instanceSlots=%lld validatedSlots=%d triangles=%llu translationErrorCm=%.9f cornerErrorCm=%.9f elapsedSeconds=%.3f sourceBytes=%lld outputAssets=%d outputBytes=%lld usedPhysicalBytes=%llu peakUsedPhysicalBytes=%llu\nBoundsMin=%s BoundsMax=%s\nMap=%s\n"),
        Candidates.Num(), ImportedMeshes.Num(), MaterialIds.Num(), SupportedIds.Num(), FallbackIds.Num(), RequiredTexturePaths.Num(),
        UniqueMeshSlotCount, InstanceSlotCount, ValidatedSlots, TotalTriangles, MaxTranslationErrorCm, MaxCornerErrorCm,
        ElapsedSeconds, SourceBytes, OutputAssetCount, OutputAssetBytes, MemoryStats.UsedPhysical, MemoryStats.PeakUsedPhysical,
        *ActorBounds.Min.ToString(), *ActorBounds.Max.ToString(), *PackageName);
    Validation += TEXT("Anomalies=0 MissingReferences=0 DuplicateInstanceIds=0\n");
    Validation += TEXT("InstanceIds=");
    for (const FInstanceCandidate& Candidate : Candidates) Validation += FString::Printf(TEXT("%llu,"), Candidate.Instance->Index);
    Validation += TEXT("\nMeshIds=");
    for (uint32 Id : MeshIds) Validation += FString::Printf(TEXT("%u,"), Id);
    Validation += TEXT("\nFallbackMaterialIds=");
    for (uint32 Id : MaterialIds) if (FallbackIds.Contains(Id)) Validation += FString::Printf(TEXT("%u,"), Id);
    Validation += TEXT("\n");
    const FString ReportSuffix = bCompleteRoot ? FString::Printf(TEXT("Root%03d"), RootId) : TEXT("Group");
    ValidationPath = FPaths::ProjectSavedDir() / FString::Printf(TEXT("AtlasSector_%s_Validation.txt"), *ReportSuffix);
    JsonPath = FPaths::ProjectSavedDir() / FString::Printf(TEXT("AtlasSector_%s_Validation.json"), *ReportSuffix);
    TSharedPtr<FJsonObject> JsonObject = MakeShared<FJsonObject>();
    JsonObject->SetStringField(TEXT("status"), TEXT("PASS"));
    JsonObject->SetStringField(TEXT("selection"), bCompleteRoot ? TEXT("complete-root-active-lod0") : TEXT("root-level-grandparent"));
    JsonObject->SetStringField(TEXT("sourceFingerprint"), Manifest.SourceFingerprint);
    JsonObject->SetStringField(TEXT("rootName"), RootName);
    JsonObject->SetNumberField(TEXT("rootId"), RootId);
    JsonObject->SetNumberField(TEXT("expectedInstances"), ExpectedCount);
    JsonObject->SetNumberField(TEXT("rootRecords"), RootRecordCount);
    JsonObject->SetNumberField(TEXT("inactiveRootRecords"), InactiveRootRecordCount);
    JsonObject->SetNumberField(TEXT("higherLodRootRecords"), HigherLodRootRecordCount);
    JsonObject->SetNumberField(TEXT("selectedInstances"), Candidates.Num());
    JsonObject->SetNumberField(TEXT("uniqueMeshes"), ImportedMeshes.Num());
    JsonObject->SetNumberField(TEXT("uniqueMaterials"), MaterialIds.Num());
    JsonObject->SetNumberField(TEXT("supportedMaterials"), SupportedIds.Num());
    JsonObject->SetNumberField(TEXT("fallbackMaterials"), FallbackIds.Num());
    JsonObject->SetNumberField(TEXT("uniqueSupportedTexturePaths"), RequiredTexturePaths.Num());
    JsonObject->SetNumberField(TEXT("uniqueMeshSlots"), static_cast<double>(UniqueMeshSlotCount));
    JsonObject->SetNumberField(TEXT("instanceSlots"), static_cast<double>(InstanceSlotCount));
    JsonObject->SetNumberField(TEXT("validatedSlots"), ValidatedSlots);
    JsonObject->SetNumberField(TEXT("supportedInstanceSlots"), static_cast<double>(SupportedInstanceSlots));
    JsonObject->SetNumberField(TEXT("fallbackInstanceSlots"), static_cast<double>(FallbackInstanceSlots));
    JsonObject->SetNumberField(TEXT("triangles"), static_cast<double>(TotalTriangles));
    JsonObject->SetNumberField(TEXT("translationErrorCm"), MaxTranslationErrorCm);
    JsonObject->SetNumberField(TEXT("cornerErrorCm"), MaxCornerErrorCm);
    JsonObject->SetStringField(TEXT("boundsMinCm"), ActorBounds.Min.ToString());
    JsonObject->SetStringField(TEXT("boundsMaxCm"), ActorBounds.Max.ToString());
    JsonObject->SetStringField(TEXT("map"), PackageName);
    JsonObject->SetNumberField(TEXT("elapsedSeconds"), ElapsedSeconds);
    JsonObject->SetNumberField(TEXT("sourceBytes"), static_cast<double>(SourceBytes));
    JsonObject->SetNumberField(TEXT("outputAssetFiles"), OutputAssetCount);
    JsonObject->SetNumberField(TEXT("outputBytes"), static_cast<double>(OutputAssetBytes));
    JsonObject->SetNumberField(TEXT("usedPhysicalBytes"), static_cast<double>(MemoryStats.UsedPhysical));
    JsonObject->SetNumberField(TEXT("peakUsedPhysicalBytes"), static_cast<double>(MemoryStats.PeakUsedPhysical));
    JsonObject->SetNumberField(TEXT("duplicateInstanceIds"), 0);
    JsonObject->SetNumberField(TEXT("missingReferences"), 0);
    TArray<TSharedPtr<FJsonValue>> InstanceIdValues;
    for (const FInstanceCandidate& Candidate : Candidates)
        InstanceIdValues.Add(MakeShared<FJsonValueNumber>(static_cast<double>(Candidate.Instance->Index)));
    JsonObject->SetArrayField(TEXT("instanceIds"), InstanceIdValues);
    TArray<TSharedPtr<FJsonValue>> MeshIdValues;
    for (uint32 Id : MeshIds) MeshIdValues.Add(MakeShared<FJsonValueNumber>(Id));
    JsonObject->SetArrayField(TEXT("meshIds"), MeshIdValues);
    TArray<TSharedPtr<FJsonValue>> FallbackIdValues;
    for (uint32 Id : MaterialIds) if (FallbackIds.Contains(Id)) FallbackIdValues.Add(MakeShared<FJsonValueNumber>(Id));
    JsonObject->SetArrayField(TEXT("fallbackMaterialIds"), FallbackIdValues);
    JsonObject->SetArrayField(TEXT("anomalies"), TArray<TSharedPtr<FJsonValue>>());
    FString JsonText;
    const TSharedRef<TJsonWriter<>> JsonWriter = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(JsonObject.ToSharedRef(), JsonWriter)
        || !FFileHelper::SaveStringToFile(Validation, *ValidationPath)
        || !FFileHelper::SaveStringToFile(JsonText, *JsonPath)) return 1;
    UE_LOG(LogAtlasEftBuildSectorCommandlet, Display, TEXT("%s"), *Validation);
    World->CleanupWorld(true, true);
    return 0;
}
