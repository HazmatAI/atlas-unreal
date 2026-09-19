#include "AtlasEftBuildSceneCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AtlasEftCoordinate.h"
#include "AtlasEftPackReader.h"
#include "AtlasEftStaticMeshImporter.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftBuildSceneCommandlet, Log, All);

namespace
{
struct FInstanceCandidate
{
    const AtlasEft::FInstanceDesc* Instance = nullptr;
    double DistanceSquared = 0.0;
};

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
}

UAtlasEftBuildSceneCommandlet::UAtlasEftBuildSceneCommandlet()
{
    IsClient = false;
    IsEditor = true;
    IsServer = false;
    LogToConsole = true;
    ShowErrorCount = true;
}

int32 UAtlasEftBuildSceneCommandlet::Main(const FString& Params)
{
    FString PackDirectory;
    if (!FParse::Value(*Params, TEXT("Pack="), PackDirectory) || PackDirectory.IsEmpty())
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Missing -Pack=<path to map.eftpack directory>."));
        return 2;
    }
    PackDirectory.TrimQuotesInline();

    int32 SeedInstance = 0;
    int32 RequestedCount = 25;
    FParse::Value(*Params, TEXT("SeedInstance="), SeedInstance);
    FParse::Value(*Params, TEXT("Count="), RequestedCount);
    if (SeedInstance < 0 || RequestedCount < 1 || RequestedCount > 200)
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("SeedInstance must be non-negative and Count must be in [1, 200]."));
        return 2;
    }

    FString Destination = TEXT("/Game/Atlas/ArchitectureTest");
    FParse::Value(*Params, TEXT("Destination="), Destination);
    Destination.TrimQuotesInline();
    Destination.RemoveFromEnd(TEXT("/"));
    if (!FPackageName::IsValidLongPackageName(Destination))
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Invalid destination: %s"), *Destination);
        return 2;
    }

    AtlasEft::FAuditReport Report;
    Report.PackDirectory = PackDirectory;
    AtlasEft::FManifest Manifest;
    if (!AtlasEft::FPackReader::ReadManifest(PackDirectory, Manifest, Report))
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("%s"), *Report.ToText());
        return 1;
    }
    TArray<AtlasEft::FInstanceDesc> Instances;
    if (!AtlasEft::FPackReader::ReadInstances(PackDirectory, Manifest, Instances, Report))
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("%s"), *Report.ToText());
        return 1;
    }
    if (!Instances.IsValidIndex(SeedInstance))
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Seed instance %d is outside 0..%d."), SeedInstance, Instances.Num() - 1);
        return 2;
    }

    const AtlasEft::FInstanceDesc& Seed = Instances[SeedInstance];
    if (Seed.IsInactive() || Seed.LodIndex > 0)
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Seed instance %d is inactive or not LOD0."), SeedInstance);
        return 2;
    }
    const FVector3d SeedPosition(Seed.GetAtlasTranslation());
    TArray<FInstanceCandidate> Candidates;
    Candidates.Reserve(Instances.Num());
    for (const AtlasEft::FInstanceDesc& Instance : Instances)
    {
        if (Instance.IsInactive() || Instance.LodIndex > 0)
        {
            continue;
        }
        const AtlasEft::FAffineAnalysis Analysis = AtlasEft::FCoordinate::AnalyzeAffine(Instance.Affine);
        if (!Analysis.bFinite || Analysis.bSheared || Analysis.bDegenerate || Analysis.bMirrored)
        {
            continue; // AStaticMeshActor/FTransform is exact only for non-sheared, non-degenerate TRS.
        }
        FInstanceCandidate& Candidate = Candidates.AddDefaulted_GetRef();
        Candidate.Instance = &Instance;
        Candidate.DistanceSquared = FVector3d::DistSquared(SeedPosition, FVector3d(Instance.GetAtlasTranslation()));
    }
    Candidates.Sort([](const FInstanceCandidate& A, const FInstanceCandidate& B)
    {
        if (A.DistanceSquared != B.DistanceSquared)
        {
            return A.DistanceSquared < B.DistanceSquared;
        }
        return A.Instance->Index < B.Instance->Index;
    });
    if (Candidates.Num() < RequestedCount)
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Only %d compatible active LOD0 instances are available."), Candidates.Num());
        return 1;
    }
    Candidates.SetNum(RequestedCount);

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
        FString Error;
        if (!AtlasEft::FStaticMeshImporter::ImportMesh(
            PackDirectory,
            Manifest,
            MeshId,
            Destination + TEXT("/Meshes"),
            MeshResult,
            Error))
        {
            UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Mesh %u failed: %s"), MeshId, *Error);
            return 1;
        }
        ImportedMeshes.Add(MeshId, MeshResult.StaticMesh);
        TotalTriangles += MeshResult.RenderTriangleCount;
    }

    const FString LevelName = FString::Printf(TEXT("L_Atlas_Architecture_Seed%06d_Count%03d"), SeedInstance, RequestedCount);
    const FString PackageName = Destination + TEXT("/") + LevelName;
    const FString PackageFilename = FPackageName::LongPackageNameToFilename(PackageName, FPackageName::GetMapPackageExtension());
    if (IFileManager::Get().FileExists(*PackageFilename) && !IFileManager::Get().Delete(*PackageFilename, false, true, true))
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Could not replace existing generated map %s."), *PackageFilename);
        return 1;
    }
    UPackage* Package = CreatePackage(*PackageName);
    if (!Package)
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Could not create level package %s."), *PackageName);
        return 1;
    }
    UWorld* World = UWorld::CreateWorld(EWorldType::Editor, false, FName(*LevelName), Package);
    if (!World)
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Could not create test world."));
        return 1;
    }
    World->SetFlags(RF_Public | RF_Standalone);

    FBox ActorBounds(EForceInit::ForceInit);
    double MaxTranslationErrorCm = 0.0;
    for (const FInstanceCandidate& Candidate : Candidates)
    {
        const AtlasEft::FInstanceDesc& Instance = *Candidate.Instance;
        TObjectPtr<UStaticMesh>* MeshPtr = ImportedMeshes.Find(Instance.MeshId);
        if (!MeshPtr || !MeshPtr->Get())
        {
            UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Imported mesh %u was lost before scene assembly."), Instance.MeshId);
            return 1;
        }

        const FTransform Transform = MakeUnrealTransform(Instance);
        FActorSpawnParameters SpawnParameters;
        SpawnParameters.Name = FName(*FString::Printf(TEXT("Atlas_I%06llu_M%04u"), Instance.Index, Instance.MeshId));
        AStaticMeshActor* Actor = World->SpawnActor<AStaticMeshActor>(AStaticMeshActor::StaticClass(), Transform, SpawnParameters);
        if (!Actor)
        {
            UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Could not spawn instance %llu."), Instance.Index);
            return 1;
        }
        Actor->SetActorLabel(FString::Printf(TEXT("Atlas Instance %llu | Mesh %u | Level %u | Root %u"), Instance.Index, Instance.MeshId, Instance.Level, Instance.RootId));
        Actor->GetStaticMeshComponent()->SetStaticMesh(MeshPtr->Get());
        Actor->GetStaticMeshComponent()->SetMobility(EComponentMobility::Static);

        const FVector ExpectedPosition = AtlasEft::FCoordinate::PositionToUnreal(FVector3d(Instance.GetAtlasTranslation()));
        MaxTranslationErrorCm = FMath::Max(MaxTranslationErrorCm, FVector::Distance(Actor->GetActorLocation(), ExpectedPosition));
        ActorBounds += Actor->GetActorLocation();
    }
    if (MaxTranslationErrorCm > 0.01)
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Transform decomposition introduced %.6f cm translation error."), MaxTranslationErrorCm);
        return 1;
    }

    World->UpdateWorldComponents(true, false);
    Package->MarkPackageDirty();
    FAssetRegistryModule::AssetCreated(World);
    FSavePackageArgs SaveArgs;
    SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
    SaveArgs.SaveFlags = SAVE_NoError;
    SaveArgs.bSlowTask = false;
    if (!UPackage::SavePackage(Package, World, *PackageFilename, SaveArgs))
    {
        UE_LOG(LogAtlasEftBuildSceneCommandlet, Error, TEXT("Failed saving map %s."), *PackageFilename);
        World->CleanupWorld(true, true);
        return 1;
    }

    UE_LOG(LogAtlasEftBuildSceneCommandlet, Display,
        TEXT("Atlas architecture scene: PASS\nLevel: %s\nInstances: %d  Unique meshes: %d  Imported mesh triangles: %llu\nSeed Atlas: (%.3f, %.3f, %.3f)  Actor location bounds cm: min=%s max=%s\nMax translation error: %.6f cm"),
        *PackageName,
        Candidates.Num(),
        ImportedMeshes.Num(),
        TotalTriangles,
        SeedPosition.X, SeedPosition.Y, SeedPosition.Z,
        *ActorBounds.Min.ToCompactString(),
        *ActorBounds.Max.ToCompactString(),
        MaxTranslationErrorCm);
    World->CleanupWorld(true, true);
    return 0;
}
