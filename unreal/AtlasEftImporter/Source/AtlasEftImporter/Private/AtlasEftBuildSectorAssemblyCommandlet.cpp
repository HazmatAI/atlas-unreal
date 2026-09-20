#include "AtlasEftBuildSectorAssemblyCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/Level.h"
#include "Engine/LevelStreamingAlwaysLoaded.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/WorldSettings.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/PackageName.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftBuildSectorAssembly, Log, All);

namespace
{
struct FRootMapAudit
{
    int32 GeometryActors = 0;
    TSet<uint64> InstanceIds;
    TMap<uint64, FBox> InstanceBounds;
    FBox Bounds = FBox(EForceInit::ForceInit);
};

constexpr double SectorReportBoundsToleranceCm = 0.25;
constexpr double JunctionMinimumOverlapCm = 0.5;
constexpr uint64 Root3DoorInstanceId = 14303;
constexpr uint64 Root1PipeSupportInstanceId = 6257;
constexpr uint64 Root7DoorInstanceId = 20204;
constexpr uint64 Root1Root7SupportInstanceId = 6230;

bool ParseReportAxis(const FString& Text, TCHAR Axis, double& OutValue)
{
    const FString Label = FString::Printf(TEXT("%c="), Axis);
    const int32 LabelIndex = Text.Find(Label, ESearchCase::CaseSensitive, ESearchDir::FromStart);
    if (LabelIndex == INDEX_NONE) return false;
    int32 ValueStart = LabelIndex + Label.Len();
    while (ValueStart < Text.Len() && FChar::IsWhitespace(Text[ValueStart])) ++ValueStart;
    int32 ValueEnd = ValueStart;
    while (ValueEnd < Text.Len() && !FChar::IsWhitespace(Text[ValueEnd]) && Text[ValueEnd] != TEXT(',') && Text[ValueEnd] != TEXT(')'))
    {
        ++ValueEnd;
    }
    if (ValueEnd == ValueStart) return false;
    const FString Value = Text.Mid(ValueStart, ValueEnd - ValueStart);
    return LexTryParseString(OutValue, *Value) && FMath::IsFinite(OutValue);
}

bool ParseReportVector(const FString& Text, FVector& OutVector)
{
    double X = 0.0, Y = 0.0, Z = 0.0;
    if (!ParseReportAxis(Text, TEXT('X'), X) || !ParseReportAxis(Text, TEXT('Y'), Y) || !ParseReportAxis(Text, TEXT('Z'), Z)) return false;
    OutVector = FVector(X, Y, Z);
    return true;
}

bool ReadRoot7BoundsFromSectorReport(const FString& ReportPath, const FString& MapPackageName,
    int32 ExpectedCount, FBox& OutBounds, FString& OutError)
{
    FString ReportText;
    if (!FFileHelper::LoadFileToString(ReportText, *ReportPath))
    {
        OutError = FString::Printf(TEXT("Could not read Root7 sector validation report: %s."), *ReportPath);
        return false;
    }
    TSharedPtr<FJsonObject> Json;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ReportText);
    if (!FJsonSerializer::Deserialize(Reader, Json) || !Json.IsValid())
    {
        OutError = FString::Printf(TEXT("Root7 sector validation report is not valid JSON: %s."), *ReportPath);
        return false;
    }
    FString Status, Map, RootName, BoundsMinText, BoundsMaxText;
    double RootId = -1.0, ExpectedInstances = -1.0, SelectedInstances = -1.0;
    double DuplicateIds = -1.0, MissingReferences = -1.0;
    if (!Json->TryGetStringField(TEXT("status"), Status) || Status != TEXT("PASS")
        || !Json->TryGetStringField(TEXT("map"), Map) || Map != MapPackageName
        || !Json->TryGetStringField(TEXT("rootName"), RootName) || RootName != TEXT("SBG_Labyrinth_Area_02")
        || !Json->TryGetNumberField(TEXT("rootId"), RootId) || RootId != 7.0
        || !Json->TryGetNumberField(TEXT("expectedInstances"), ExpectedInstances) || ExpectedInstances != ExpectedCount
        || !Json->TryGetNumberField(TEXT("selectedInstances"), SelectedInstances) || SelectedInstances != ExpectedCount
        || !Json->TryGetNumberField(TEXT("duplicateInstanceIds"), DuplicateIds) || DuplicateIds != 0.0
        || !Json->TryGetNumberField(TEXT("missingReferences"), MissingReferences) || MissingReferences != 0.0
        || !Json->TryGetStringField(TEXT("boundsMinCm"), BoundsMinText)
        || !Json->TryGetStringField(TEXT("boundsMaxCm"), BoundsMaxText))
    {
        OutError = FString::Printf(TEXT("Root7 sector report did not pass its identity/count/reference checks: %s."), *ReportPath);
        return false;
    }
    FVector BoundsMin, BoundsMax;
    if (!ParseReportVector(BoundsMinText, BoundsMin) || !ParseReportVector(BoundsMaxText, BoundsMax))
    {
        OutError = FString::Printf(TEXT("Could not parse Root7 sector report bounds: %s."), *ReportPath);
        return false;
    }
    OutBounds = FBox(BoundsMin, BoundsMax);
    return OutBounds.IsValid != 0;
}

struct FScopedWorldCleanup
{
    UWorld* World = nullptr;
    ~FScopedWorldCleanup()
    {
        if (World && World->IsInitialized()) World->CleanupWorld(true, true);
    }
};

bool AuditRootMap(const FString& MapPackageName, int32 ExpectedCount, const FBox& ExpectedBounds,
    FRootMapAudit& OutAudit, FString& OutError)
{
    OutError.Reset();
    if (!FPackageName::IsValidLongPackageName(MapPackageName))
    {
        OutError = FString::Printf(TEXT("Invalid root map package: %s"), *MapPackageName);
        return false;
    }
    const FString MapFilename = FPackageName::LongPackageNameToFilename(MapPackageName, FPackageName::GetMapPackageExtension());
    if (!FPaths::FileExists(MapFilename))
    {
        OutError = FString::Printf(TEXT("Root map is missing: %s (%s)"), *MapPackageName, *MapFilename);
        return false;
    }
    UPackage* RootPackage = LoadPackage(nullptr, *MapPackageName, LOAD_NoWarn | LOAD_Quiet);
    UWorld* RootWorld = RootPackage ? FindObject<UWorld>(RootPackage, *FPackageName::GetLongPackageAssetName(MapPackageName)) : nullptr;
    FScopedWorldCleanup RootWorldCleanup{RootWorld};
    if (!RootWorld || !RootWorld->PersistentLevel)
    {
        OutError = FString::Printf(TEXT("Could not load persistent level in %s."), *MapPackageName);
        return false;
    }

    OutAudit = FRootMapAudit();
    for (AActor* Actor : RootWorld->PersistentLevel->Actors)
    {
        if (!Actor || !Actor->IsA<AStaticMeshActor>()) continue;
        FString InstanceTag;
        for (const FName& Tag : Actor->Tags)
        {
            const FString TagText = Tag.ToString();
            if (TagText.StartsWith(TEXT("AtlasInstance=")))
            {
                InstanceTag = TagText;
                break;
            }
        }
        if (InstanceTag.IsEmpty())
        {
            OutError = FString::Printf(TEXT("Unexpected untagged StaticMeshActor in %s: %s."), *MapPackageName, *Actor->GetPathName());
            return false;
        }
        FString NumberText;
        uint64 InstanceId = 0;
        if (!InstanceTag.Split(TEXT("="), nullptr, &NumberText) || !LexTryParseString(InstanceId, *NumberText)
            || OutAudit.InstanceIds.Contains(InstanceId))
        {
            OutError = FString::Printf(TEXT("Malformed/duplicate geometry tag %s in %s."), *InstanceTag, *MapPackageName);
            return false;
        }
        OutAudit.InstanceIds.Add(InstanceId);
        ++OutAudit.GeometryActors;

        const AStaticMeshActor* MeshActor = Cast<AStaticMeshActor>(Actor);
        const UStaticMeshComponent* Component = MeshActor ? MeshActor->GetStaticMeshComponent() : nullptr;
        if (!Component || !Component->GetStaticMesh())
        {
            OutError = FString::Printf(TEXT("Geometry actor %llu has no static mesh in %s."), InstanceId, *MapPackageName);
            return false;
        }
        // This commandlet loads the map package without registering/initializing its world.
        // GetComponentTransform()/AActor::GetActorTransform() then reflect an unregistered
        // ComponentToWorld cache and are not reliable world transforms. Generated geometry
        // actors use their static-mesh component as root, whose serialized RelativeTransform
        // is the actor transform. Require that invariant and transform the same 8 local mesh
        // AABB corners used by BuildSector.
        const FBox LocalBounds = Component->GetStaticMesh()->GetBoundingBox();
        const USceneComponent* RootComponent = MeshActor->GetRootComponent();
        if (!RootComponent || RootComponent != Component)
        {
            OutError = FString::Printf(TEXT("Geometry actor %llu in %s has a non-root static mesh component; its stored transform cannot be audited safely."), InstanceId, *MapPackageName);
            return false;
        }
        const FTransform ActorTransform = Component->GetRelativeTransform();
        if (!LocalBounds.IsValid)
        {
            OutError = FString::Printf(TEXT("Invalid local mesh bounds for geometry actor %llu in %s."), InstanceId, *MapPackageName);
            return false;
        }
        FBox ActorBounds(EForceInit::ForceInit);
        for (int32 CornerIndex = 0; CornerIndex < 8; ++CornerIndex)
        {
            const FVector LocalCorner(
                (CornerIndex & 1) ? LocalBounds.Max.X : LocalBounds.Min.X,
                (CornerIndex & 2) ? LocalBounds.Max.Y : LocalBounds.Min.Y,
                (CornerIndex & 4) ? LocalBounds.Max.Z : LocalBounds.Min.Z);
            ActorBounds += ActorTransform.TransformPosition(LocalCorner);
        }
        OutAudit.Bounds += ActorBounds;
        OutAudit.InstanceBounds.Add(InstanceId, ActorBounds);
    }
    if (OutAudit.GeometryActors != ExpectedCount || OutAudit.InstanceIds.Num() != ExpectedCount || !OutAudit.Bounds.IsValid)
    {
        OutError = FString::Printf(TEXT("Geometry audit mismatch for %s: expected %d, found %d actors/%d ids."),
            *MapPackageName, ExpectedCount, OutAudit.GeometryActors, OutAudit.InstanceIds.Num());
        return false;
    }
    if (!OutAudit.Bounds.Min.Equals(ExpectedBounds.Min, SectorReportBoundsToleranceCm)
        || !OutAudit.Bounds.Max.Equals(ExpectedBounds.Max, SectorReportBoundsToleranceCm))
    {
        OutError = FString::Printf(TEXT("World bounds mismatch for %s using transformed mesh corners: actual %s..%s expected sector report %s..%s (tol %.3f cm)."),
            *MapPackageName, *OutAudit.Bounds.Min.ToString(), *OutAudit.Bounds.Max.ToString(),
            *ExpectedBounds.Min.ToString(), *ExpectedBounds.Max.ToString(), SectorReportBoundsToleranceCm);
        return false;
    }
    return true;
}

bool ValidateJunctionPair(const FRootMapAudit& Root3Audit, const FRootMapAudit& Root1Audit, FVector& OutOverlapCm, FString& OutError)
{
    OutError.Reset();
    const FBox* Root3DoorBounds = Root3Audit.InstanceBounds.Find(Root3DoorInstanceId);
    const FBox* Root1PipeBounds = Root1Audit.InstanceBounds.Find(Root1PipeSupportInstanceId);
    if (!Root3DoorBounds || !Root1PipeBounds)
    {
        OutError = FString::Printf(TEXT("Required junction pair is missing: Root3 instance %llu / Root1 instance %llu."),
            Root3DoorInstanceId, Root1PipeSupportInstanceId);
        return false;
    }
    OutOverlapCm = FVector(
        FMath::Min(Root3DoorBounds->Max.X, Root1PipeBounds->Max.X) - FMath::Max(Root3DoorBounds->Min.X, Root1PipeBounds->Min.X),
        FMath::Min(Root3DoorBounds->Max.Y, Root1PipeBounds->Max.Y) - FMath::Max(Root3DoorBounds->Min.Y, Root1PipeBounds->Min.Y),
        FMath::Min(Root3DoorBounds->Max.Z, Root1PipeBounds->Max.Z) - FMath::Max(Root3DoorBounds->Min.Z, Root1PipeBounds->Min.Z));
    if (OutOverlapCm.X < JunctionMinimumOverlapCm || OutOverlapCm.Y < JunctionMinimumOverlapCm || OutOverlapCm.Z < JunctionMinimumOverlapCm)
    {
        OutError = FString::Printf(TEXT("Junction pair no longer overlaps usefully in UE world bounds: overlap %s cm, minimum %.3f cm per axis."),
            *OutOverlapCm.ToString(), JunctionMinimumOverlapCm);
        return false;
    }
    return true;
}

bool ValidateRoot7Contact(const FRootMapAudit& Root7Audit, const FRootMapAudit& Root1Audit,
    FVector& OutOverlapCm, FString& OutError)
{
    OutError.Reset();
    const FBox* Root7DoorBounds = Root7Audit.InstanceBounds.Find(Root7DoorInstanceId);
    const FBox* Root1SupportBounds = Root1Audit.InstanceBounds.Find(Root1Root7SupportInstanceId);
    if (!Root7DoorBounds || !Root1SupportBounds)
    {
        OutError = FString::Printf(TEXT("Required Root7/Root1 contact pair is missing: Root7 instance %llu / Root1 instance %llu."),
            Root7DoorInstanceId, Root1Root7SupportInstanceId);
        return false;
    }
    OutOverlapCm = FVector(
        FMath::Min(Root7DoorBounds->Max.X, Root1SupportBounds->Max.X) - FMath::Max(Root7DoorBounds->Min.X, Root1SupportBounds->Min.X),
        FMath::Min(Root7DoorBounds->Max.Y, Root1SupportBounds->Max.Y) - FMath::Max(Root7DoorBounds->Min.Y, Root1SupportBounds->Min.Y),
        FMath::Min(Root7DoorBounds->Max.Z, Root1SupportBounds->Max.Z) - FMath::Max(Root7DoorBounds->Min.Z, Root1SupportBounds->Min.Z));
    if (OutOverlapCm.X < JunctionMinimumOverlapCm || OutOverlapCm.Y < JunctionMinimumOverlapCm || OutOverlapCm.Z < JunctionMinimumOverlapCm)
    {
        OutError = FString::Printf(TEXT("Root7/Root1 contact pair no longer overlaps usefully in UE world bounds: overlap %s cm, minimum %.3f cm per axis."),
            *OutOverlapCm.ToString(), JunctionMinimumOverlapCm);
        return false;
    }
    return true;
}

ULevelStreamingAlwaysLoaded* AddAlwaysLoadedSublevel(UWorld* AssemblyWorld, const FString& MapPackageName, const TCHAR* StreamObjectName)
{
    const FName ObjectName(StreamObjectName);
    ULevelStreamingAlwaysLoaded* Streaming = NewObject<ULevelStreamingAlwaysLoaded>(AssemblyWorld,
        ULevelStreamingAlwaysLoaded::StaticClass(), ObjectName, RF_Transactional);
    if (!Streaming) return nullptr;
    Streaming->SetWorldAssetByPackageName(FName(*MapPackageName));
    Streaming->LevelTransform = FTransform::Identity;
    Streaming->SetShouldBeLoaded(true);
    Streaming->SetShouldBeVisible(true);
#if WITH_EDITOR
    Streaming->SetShouldBeVisibleInEditor(true);
#endif
    Streaming->bIsStatic = true;
    Streaming->bShouldBlockOnLoad = true;
    AssemblyWorld->AddStreamingLevel(Streaming);
    return Streaming;
}

TSharedPtr<FJsonObject> BoundsJson(const FBox& Bounds)
{
    TSharedPtr<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("minCm"), Bounds.Min.ToString());
    Json->SetStringField(TEXT("maxCm"), Bounds.Max.ToString());
    return Json;
}

struct FExplicitSectorSpec
{
    int32 RootId = 0;
    FString MapPackage;
    int32 ExpectedCount = 0;
    FString ReportPath;
    FBox ExpectedBounds = FBox(EForceInit::ForceInit);
    FRootMapAudit Audit;
};

struct FExplicitContactSpec
{
    int32 LeftRootId = 0;
    uint64 LeftInstanceId = 0;
    int32 RightRootId = 0;
    uint64 RightInstanceId = 0;
    double MinimumOverlapCm = 0.0;
    FVector OverlapCm = FVector::ZeroVector;
};

bool ParseReportBounds(const FString& ReportPath, const FString& MapPackage, int32 RootId,
    int32 ExpectedCount, FBox& OutBounds, FString& OutError)
{
    OutError.Reset();
    FString ReportText;
    if (!FFileHelper::LoadFileToString(ReportText, *ReportPath))
    {
        OutError = FString::Printf(TEXT("Sector report is missing/unreadable: %s."), *ReportPath);
        return false;
    }
    TSharedPtr<FJsonObject> Json;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ReportText);
    if (!FJsonSerializer::Deserialize(Reader, Json) || !Json.IsValid())
    {
        OutError = FString::Printf(TEXT("Sector report is invalid JSON: %s."), *ReportPath);
        return false;
    }
    FString Status, ReportMap, BoundsMinText, BoundsMaxText;
    double ReportRootId = -1.0, ExpectedInstances = -1.0, SelectedInstances = -1.0;
    double DuplicateIds = -1.0, MissingReferences = -1.0;
    if (!Json->TryGetStringField(TEXT("status"), Status) || Status != TEXT("PASS")
        || !Json->TryGetStringField(TEXT("map"), ReportMap) || ReportMap != MapPackage
        || !Json->TryGetNumberField(TEXT("rootId"), ReportRootId) || ReportRootId != RootId
        || !Json->TryGetNumberField(TEXT("expectedInstances"), ExpectedInstances) || ExpectedInstances != ExpectedCount
        || !Json->TryGetNumberField(TEXT("selectedInstances"), SelectedInstances) || SelectedInstances != ExpectedCount
        || !Json->TryGetNumberField(TEXT("duplicateInstanceIds"), DuplicateIds) || DuplicateIds != 0.0
        || !Json->TryGetNumberField(TEXT("missingReferences"), MissingReferences) || MissingReferences != 0.0
        || !Json->TryGetStringField(TEXT("boundsMinCm"), BoundsMinText)
        || !Json->TryGetStringField(TEXT("boundsMaxCm"), BoundsMaxText))
    {
        OutError = FString::Printf(TEXT("Sector report failed identity/count/reference checks: root=%d map=%s report=%s."),
            RootId, *MapPackage, *ReportPath);
        return false;
    }
    FVector BoundsMin, BoundsMax;
    if (!ParseReportVector(BoundsMinText, BoundsMin) || !ParseReportVector(BoundsMaxText, BoundsMax))
    {
        OutError = FString::Printf(TEXT("Sector report bounds are malformed: %s."), *ReportPath);
        return false;
    }
    OutBounds = FBox(BoundsMin, BoundsMax);
    if (!OutBounds.IsValid)
    {
        OutError = FString::Printf(TEXT("Sector report has invalid bounds: %s."), *ReportPath);
        return false;
    }
    return true;
}

bool ValidateExplicitContacts(TArray<FExplicitSectorSpec>& Sectors, TArray<FExplicitContactSpec>& Contacts,
    FString& OutError)
{
    for (FExplicitContactSpec& Contact : Contacts)
    {
        FExplicitSectorSpec* Left = Sectors.FindByPredicate([&](const FExplicitSectorSpec& Sector) { return Sector.RootId == Contact.LeftRootId; });
        FExplicitSectorSpec* Right = Sectors.FindByPredicate([&](const FExplicitSectorSpec& Sector) { return Sector.RootId == Contact.RightRootId; });
        if (!Left || !Right || Left == Right)
        {
            OutError = FString::Printf(TEXT("Contact references absent or identical RootIds %d/%d."), Contact.LeftRootId, Contact.RightRootId);
            return false;
        }
        const FBox* LeftBounds = Left->Audit.InstanceBounds.Find(Contact.LeftInstanceId);
        const FBox* RightBounds = Right->Audit.InstanceBounds.Find(Contact.RightInstanceId);
        if (!LeftBounds || !RightBounds)
        {
            OutError = FString::Printf(TEXT("Contact instance IDs are absent: Root%d/%llu and Root%d/%llu."),
                Contact.LeftRootId, Contact.LeftInstanceId, Contact.RightRootId, Contact.RightInstanceId);
            return false;
        }
        Contact.OverlapCm = FVector(
            FMath::Min(LeftBounds->Max.X, RightBounds->Max.X) - FMath::Max(LeftBounds->Min.X, RightBounds->Min.X),
            FMath::Min(LeftBounds->Max.Y, RightBounds->Max.Y) - FMath::Max(LeftBounds->Min.Y, RightBounds->Min.Y),
            FMath::Min(LeftBounds->Max.Z, RightBounds->Max.Z) - FMath::Max(LeftBounds->Min.Z, RightBounds->Min.Z));
        if (Contact.OverlapCm.X < Contact.MinimumOverlapCm || Contact.OverlapCm.Y < Contact.MinimumOverlapCm
            || Contact.OverlapCm.Z < Contact.MinimumOverlapCm)
        {
            OutError = FString::Printf(TEXT("Contact Root%d/%llu to Root%d/%llu overlap %s cm is below %.3f cm per axis."),
                Contact.LeftRootId, Contact.LeftInstanceId, Contact.RightRootId, Contact.RightInstanceId,
                *Contact.OverlapCm.ToString(), Contact.MinimumOverlapCm);
            return false;
        }
    }
    return true;
}

int32 RunExplicitSectorAssembly(const FString& Params, FString SectorSpecsText)
{
    FString AssemblyMap(TEXT("/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural"));
    FString RequestedAssemblyMap, ContactSpecsText;
    FParse::Value(*Params, TEXT("AssemblyMap="), RequestedAssemblyMap);
    FParse::Value(*Params, TEXT("Contacts="), ContactSpecsText);
    RequestedAssemblyMap.TrimQuotesInline();
    ContactSpecsText.TrimQuotesInline();
    SectorSpecsText.TrimQuotesInline();
    if (!RequestedAssemblyMap.IsEmpty()) AssemblyMap = RequestedAssemblyMap;
    const bool bValidateOnly = FParse::Param(*Params, TEXT("ValidateOnly"));
    if (!FPackageName::IsValidLongPackageName(AssemblyMap) || !AssemblyMap.StartsWith(TEXT("/Game/")))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Explicit assembly path must be a valid /Game package path."));
        return 2;
    }

    TArray<FString> SectorTokens, ContactTokens;
    SectorSpecsText.ParseIntoArray(SectorTokens, TEXT(","), true);
    ContactSpecsText.ParseIntoArray(ContactTokens, TEXT(","), true);
    if (SectorTokens.Num() < 2 || ContactTokens.Num() == 0)
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Explicit mode requires at least two sectors and one contact."));
        return 2;
    }

    TArray<FExplicitSectorSpec> Sectors;
    TSet<int32> RootIds;
    TSet<FString> MapPackages;
    FString Error;
    for (const FString& Token : SectorTokens)
    {
        TArray<FString> Fields;
        Token.ParseIntoArray(Fields, TEXT("|"), false);
        FExplicitSectorSpec Sector;
        if (Fields.Num() != 4 || !LexTryParseString(Sector.RootId, *Fields[0]) || Sector.RootId <= 0
            || !FPackageName::IsValidLongPackageName(Fields[1]) || !Fields[1].StartsWith(TEXT("/Game/"))
            || !LexTryParseString(Sector.ExpectedCount, *Fields[2]) || Sector.ExpectedCount <= 0
            || Fields[3].IsEmpty() || FPaths::GetCleanFilename(Fields[3]) != Fields[3]
            || RootIds.Contains(Sector.RootId) || MapPackages.Contains(Fields[1]) || Fields[1] == AssemblyMap)
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Malformed/duplicate sector spec (expected exactly rootId|/Game/map|positiveCount|reportFilename): %s"), *Token);
            return 2;
        }
        Sector.MapPackage = Fields[1];
        Sector.ReportPath = FPaths::Combine(FPaths::ProjectSavedDir(), Fields[3]);
        if (!IFileManager::Get().FileExists(*Sector.ReportPath)
            || !ParseReportBounds(Sector.ReportPath, Sector.MapPackage, Sector.RootId, Sector.ExpectedCount, Sector.ExpectedBounds, Error))
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Sector spec preflight failed: %s"), Error.IsEmpty() ? *Sector.ReportPath : *Error);
            return 1;
        }
        RootIds.Add(Sector.RootId);
        MapPackages.Add(Sector.MapPackage);
        Sectors.Add(MoveTemp(Sector));
    }

    TArray<FExplicitContactSpec> Contacts;
    for (const FString& Token : ContactTokens)
    {
        TArray<FString> Fields;
        Token.ParseIntoArray(Fields, TEXT("|"), false);
        FExplicitContactSpec Contact;
        if (Fields.Num() != 5 || !LexTryParseString(Contact.LeftRootId, *Fields[0]) || Contact.LeftRootId <= 0
            || !LexTryParseString(Contact.LeftInstanceId, *Fields[1]) || Contact.LeftInstanceId == 0
            || !LexTryParseString(Contact.RightRootId, *Fields[2]) || Contact.RightRootId <= 0
            || !LexTryParseString(Contact.RightInstanceId, *Fields[3]) || Contact.RightInstanceId == 0
            || !LexTryParseString(Contact.MinimumOverlapCm, *Fields[4]) || !FMath::IsFinite(Contact.MinimumOverlapCm)
            || Contact.MinimumOverlapCm <= 0.0 || Contact.LeftRootId == Contact.RightRootId)
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Malformed contact spec (expected exactly leftRoot|leftInstance|rightRoot|rightInstance|minOverlapCm): %s"), *Token);
            return 2;
        }
        Contacts.Add(Contact);
    }

    for (FExplicitSectorSpec& Sector : Sectors)
    {
        if (!AuditRootMap(Sector.MapPackage, Sector.ExpectedCount, Sector.ExpectedBounds, Sector.Audit, Error))
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Sector geometry/bounds preflight failed for Root%d: %s"), Sector.RootId, *Error);
            return 1;
        }
    }
    for (int32 Left = 0; Left < Sectors.Num(); ++Left)
    {
        for (int32 Right = Left + 1; Right < Sectors.Num(); ++Right)
        {
            for (uint64 InstanceId : Sectors[Left].Audit.InstanceIds)
            {
                if (Sectors[Right].Audit.InstanceIds.Contains(InstanceId))
                {
                    UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Duplicate geometry instance ID %llu between Root%d and Root%d."),
                        InstanceId, Sectors[Left].RootId, Sectors[Right].RootId);
                    return 1;
                }
            }
        }
    }
    if (!ValidateExplicitContacts(Sectors, Contacts, Error))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Explicit contact preflight failed: %s"), *Error);
        return 1;
    }

    if (bValidateOnly)
    {
        UPackage* AssemblyPackage = LoadPackage(nullptr, *AssemblyMap, LOAD_NoWarn | LOAD_Quiet);
        UWorld* LoadedAssembly = AssemblyPackage
            ? FindObject<UWorld>(AssemblyPackage, *FPackageName::GetLongPackageAssetName(AssemblyMap)) : nullptr;
        if (!LoadedAssembly || !LoadedAssembly->PersistentLevel || LoadedAssembly->GetStreamingLevels().Num() != Sectors.Num())
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded explicit assembly does not have exactly %d streamed references."), Sectors.Num());
            return 1;
        }
        TSet<FName> StreamedPackages;
        for (ULevelStreaming* Streaming : LoadedAssembly->GetStreamingLevels())
        {
            if (!Streaming || !Streaming->GetClass()->IsChildOf(ULevelStreamingAlwaysLoaded::StaticClass())
                || !Streaming->LevelTransform.Equals(FTransform::Identity) || !Streaming->ShouldBeLoaded()
                || !Streaming->GetShouldBeVisibleFlag())
            {
                UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded explicit assembly has invalid streaming type/state/transform."));
                return 1;
            }
            StreamedPackages.Add(Streaming->GetWorldAssetPackageFName());
        }
        if (StreamedPackages.Num() != Sectors.Num())
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded explicit assembly contains duplicate/missing streamed references."));
            return 1;
        }
        for (const FExplicitSectorSpec& Sector : Sectors)
        {
            if (!StreamedPackages.Contains(FName(*Sector.MapPackage)))
            {
                UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded assembly is missing Root%d map %s."), Sector.RootId, *Sector.MapPackage);
                return 1;
            }
        }
        int32 PersistentMeshActors = 0;
        for (AActor* Actor : LoadedAssembly->PersistentLevel->Actors)
        {
            if (Actor && Actor->IsA<AStaticMeshActor>()) ++PersistentMeshActors;
        }
        if (PersistentMeshActors != 0)
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded assembly contains %d persistent static-mesh actors."), PersistentMeshActors);
            return 1;
        }
        int32 ReloadedTotalGeometry = 0;
        for (const FExplicitSectorSpec& Sector : Sectors) ReloadedTotalGeometry += Sector.Audit.GeometryActors;
        UE_LOG(LogAtlasEftBuildSectorAssembly, Display, TEXT("PASS reload assembly=%s refs=%d sectors=%d totalGeometry=%d persistentStaticMeshActors=0 duplicateInstanceIds=0 bounds=sectorReports contacts=%d"),
            *AssemblyMap, Sectors.Num(), Sectors.Num(), ReloadedTotalGeometry, Contacts.Num());
        return 0;
    }

    const FString LevelName = FPackageName::GetLongPackageAssetName(AssemblyMap);
    const FString PackageFilename = FPackageName::LongPackageNameToFilename(AssemblyMap, FPackageName::GetMapPackageExtension());
    UPackage* Package = CreatePackage(*AssemblyMap);
    if (!Package)
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Could not create explicit assembly package %s."), *AssemblyMap);
        return 1;
    }
    UWorld* AssemblyWorld = UWorld::CreateWorld(EWorldType::Editor, false, FName(*LevelName), Package);
    FScopedWorldCleanup AssemblyWorldCleanup{AssemblyWorld};
    if (!AssemblyWorld || !AssemblyWorld->PersistentLevel)
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Could not create explicit assembly world %s."), *AssemblyMap);
        return 1;
    }
    Package->MarkAsFullyLoaded();
    AssemblyWorld->SetFlags(RF_Public | RF_Standalone);
    AssemblyWorld->GetWorldSettings()->bForceNoPrecomputedLighting = true;
    for (const FExplicitSectorSpec& Sector : Sectors)
    {
        const FString StreamObjectName = FString::Printf(TEXT("AtlasRoot%03dStreaming"), Sector.RootId);
        if (!AddAlwaysLoadedSublevel(AssemblyWorld, Sector.MapPackage, *StreamObjectName))
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Could not add always-loaded reference for Root%d."), Sector.RootId);
            return 1;
        }
    }
    if (AssemblyWorld->GetStreamingLevels().Num() != Sectors.Num())
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Created assembly streaming reference count mismatch."));
        return 1;
    }
    for (ULevelStreaming* Streaming : AssemblyWorld->GetStreamingLevels())
    {
        bool bFoundSpec = false;
        for (const FExplicitSectorSpec& Sector : Sectors) bFoundSpec |= Streaming && Streaming->GetWorldAssetPackageFName() == FName(*Sector.MapPackage);
        if (!Streaming || !bFoundSpec || !Streaming->LevelTransform.Equals(FTransform::Identity))
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Created assembly has an invalid or non-identity reference."));
            return 1;
        }
    }
    int32 PersistentMeshActors = 0;
    for (AActor* Actor : AssemblyWorld->PersistentLevel->Actors)
    {
        if (Actor && Actor->IsA<AStaticMeshActor>()) ++PersistentMeshActors;
    }
    if (PersistentMeshActors != 0)
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Assembly persistent level unexpectedly contains %d static-mesh actors."), PersistentMeshActors);
        return 1;
    }
    Package->MarkPackageDirty();
    FAssetRegistryModule::AssetCreated(AssemblyWorld);
    FSavePackageArgs SaveArgs;
    SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
    SaveArgs.SaveFlags = SAVE_NoError;
    SaveArgs.bSlowTask = false;
    if (!UPackage::SavePackage(Package, AssemblyWorld, *PackageFilename, SaveArgs))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Failed saving explicit assembly map %s."), *PackageFilename);
        return 1;
    }

    TSharedPtr<FJsonObject> Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("status"), TEXT("PASS"));
    Report->SetStringField(TEXT("assemblyMap"), AssemblyMap);
    Report->SetNumberField(TEXT("streamingRefs"), Sectors.Num());
    Report->SetNumberField(TEXT("persistentGeometryActors"), PersistentMeshActors);
    int32 TotalGeometry = 0;
    TArray<TSharedPtr<FJsonValue>> SectorReports, ContactReports;
    for (const FExplicitSectorSpec& Sector : Sectors)
    {
        TotalGeometry += Sector.Audit.GeometryActors;
        TSharedPtr<FJsonObject> SectorJson = MakeShared<FJsonObject>();
        SectorJson->SetNumberField(TEXT("rootId"), Sector.RootId);
        SectorJson->SetStringField(TEXT("map"), Sector.MapPackage);
        SectorJson->SetNumberField(TEXT("expectedGeometryActors"), Sector.ExpectedCount);
        SectorJson->SetNumberField(TEXT("geometryActors"), Sector.Audit.GeometryActors);
        SectorJson->SetNumberField(TEXT("uniqueInstanceIds"), Sector.Audit.InstanceIds.Num());
        SectorJson->SetObjectField(TEXT("bounds"), BoundsJson(Sector.Audit.Bounds));
        SectorJson->SetStringField(TEXT("levelTransform"), FTransform::Identity.ToString());
        SectorReports.Add(MakeShared<FJsonValueObject>(SectorJson));
    }
    Report->SetNumberField(TEXT("totalGeometryActors"), TotalGeometry);
    Report->SetBoolField(TEXT("duplicateInstanceIds"), false);
    Report->SetStringField(TEXT("streamingType"), TEXT("ULevelStreamingAlwaysLoaded"));
    Report->SetArrayField(TEXT("sublevels"), SectorReports);
    for (const FExplicitContactSpec& Contact : Contacts)
    {
        TSharedPtr<FJsonObject> ContactJson = MakeShared<FJsonObject>();
        ContactJson->SetNumberField(TEXT("leftRootId"), Contact.LeftRootId);
        ContactJson->SetNumberField(TEXT("leftInstanceId"), static_cast<double>(Contact.LeftInstanceId));
        ContactJson->SetNumberField(TEXT("rightRootId"), Contact.RightRootId);
        ContactJson->SetNumberField(TEXT("rightInstanceId"), static_cast<double>(Contact.RightInstanceId));
        ContactJson->SetArrayField(TEXT("overlapCm"), TArray<TSharedPtr<FJsonValue>>{
            MakeShared<FJsonValueNumber>(Contact.OverlapCm.X), MakeShared<FJsonValueNumber>(Contact.OverlapCm.Y), MakeShared<FJsonValueNumber>(Contact.OverlapCm.Z)});
        ContactJson->SetNumberField(TEXT("minimumOverlapPerAxisCm"), Contact.MinimumOverlapCm);
        ContactReports.Add(MakeShared<FJsonValueObject>(ContactJson));
    }
    Report->SetArrayField(TEXT("contacts"), ContactReports);
    FString ReportText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&ReportText);
    FJsonSerializer::Serialize(Report.ToSharedRef(), Writer);
    const FString ReportFilename = AssemblyMap == TEXT("/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural")
        ? TEXT("AtlasAssembly_LabyrinthStructural_Validation.json") : TEXT("AtlasAssembly_ExplicitSectors_Validation.json");
    const FString ReportPath = FPaths::ProjectSavedDir() / ReportFilename;
    if (!FFileHelper::SaveStringToFile(ReportText, *ReportPath))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Could not save explicit assembly report %s."), *ReportPath);
        return 1;
    }
    UE_LOG(LogAtlasEftBuildSectorAssembly, Display, TEXT("PASS assembly=%s refs=%d totalGeometry=%d persistentStaticMeshActors=0 duplicateInstanceIds=0 bounds=sectorReports contacts=%d report=%s"),
        *AssemblyMap, Sectors.Num(), TotalGeometry, Contacts.Num(), *ReportPath);
    return 0;
}
}

UAtlasEftBuildSectorAssemblyCommandlet::UAtlasEftBuildSectorAssemblyCommandlet()
{
    IsClient = false;
    IsEditor = true;
    IsServer = false;
    LogToConsole = true;
    ShowErrorCount = true;
}

int32 UAtlasEftBuildSectorAssemblyCommandlet::Main(const FString& Params)
{
    FString ExplicitSectorSpecs;
    if (FParse::Value(*Params, TEXT("Sectors="), ExplicitSectorSpecs))
    {
        return RunExplicitSectorAssembly(Params, ExplicitSectorSpecs);
    }
    FString Root3Map = TEXT("/Game/Atlas/Sectors/Root_003/L_Atlas_Root003_Area03");
    FString Root1Map = TEXT("/Game/Atlas/Sectors/Root_001/L_Atlas_Root001_Area06");
    FString AssemblyMap = TEXT("/Game/Atlas/Sectors/Assemblies/L_Atlas_Root003_Root001_Adjacent");
    FString Root7Map, Root7ValidationReport;
    FString RequestedRoot3Map, RequestedRoot1Map, RequestedRoot7Map, RequestedAssemblyMap;
    int32 Root3ExpectedCount = 1944;
    int32 Root1ExpectedCount = 10009;
    int32 Root7ExpectedCount = 629;
    const FBox Root3ExpectedBounds(FVector(-2287.021, -5346.584, -1039.593), FVector(-629.743, -2591.222, 1478.870));
    const FBox Root1ExpectedBounds(FVector(-3383.764, -4467.952, -472.591), FVector(6545.657, 2547.392, 739.713));
    FParse::Value(*Params, TEXT("Root3Map="), RequestedRoot3Map);
    FParse::Value(*Params, TEXT("Root1Map="), RequestedRoot1Map);
    const bool bIncludeRoot7 = FParse::Value(*Params, TEXT("Root7Map="), RequestedRoot7Map);
    FParse::Value(*Params, TEXT("AssemblyMap="), RequestedAssemblyMap);
    FParse::Value(*Params, TEXT("Root7ValidationReport="), Root7ValidationReport);
    FParse::Value(*Params, TEXT("Root3ExpectedCount="), Root3ExpectedCount);
    FParse::Value(*Params, TEXT("Root1ExpectedCount="), Root1ExpectedCount);
    FParse::Value(*Params, TEXT("Root7ExpectedCount="), Root7ExpectedCount);
    RequestedRoot3Map.TrimQuotesInline();
    RequestedRoot1Map.TrimQuotesInline();
    RequestedRoot7Map.TrimQuotesInline();
    RequestedAssemblyMap.TrimQuotesInline();
    Root7ValidationReport.TrimQuotesInline();
    const bool bValidateOnly = FParse::Param(*Params, TEXT("ValidateOnly"));
    if (!RequestedRoot3Map.IsEmpty()) Root3Map = RequestedRoot3Map;
    if (!RequestedRoot1Map.IsEmpty()) Root1Map = RequestedRoot1Map;
    if (bIncludeRoot7) Root7Map = RequestedRoot7Map;
    if (!RequestedAssemblyMap.IsEmpty()) AssemblyMap = RequestedAssemblyMap;
    else if (bIncludeRoot7) AssemblyMap = TEXT("/Game/Atlas/Assemblies/L_Atlas_Labyrinth_Structural");
    if (Root7ValidationReport.IsEmpty()) Root7ValidationReport = FPaths::ProjectSavedDir() / TEXT("AtlasSector_Root007_Validation.json");
    if (Root3ExpectedCount != 1944 || Root1ExpectedCount != 10009 || Root3Map == Root1Map || Root3Map == AssemblyMap || Root1Map == AssemblyMap
        || (bIncludeRoot7 && (Root7ExpectedCount != 629 || Root7Map != TEXT("/Game/Atlas/Sectors/Root_007/L_Atlas_Root007_Area02")
            || Root7Map == Root3Map || Root7Map == Root1Map || Root7Map == AssemblyMap)))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Assembly guard failed: Root1/Root3 must keep their audited counts; optional Root7 must be the audited complete Area02 map with 629 actors."));
        return 2;
    }

    FString Error;
    FBox Root7ExpectedBounds(EForceInit::ForceInit);
    if (bIncludeRoot7 && !ReadRoot7BoundsFromSectorReport(Root7ValidationReport, Root7Map, Root7ExpectedCount, Root7ExpectedBounds, Error))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Root7 sector-report preflight failed: %s"), *Error);
        return 1;
    }

    auto AuditAllRoots = [&](FRootMapAudit& Root3Audit, FRootMapAudit& Root1Audit, FRootMapAudit& Root7Audit)
    {
        if (!AuditRootMap(Root3Map, Root3ExpectedCount, Root3ExpectedBounds, Root3Audit, Error)
            || !AuditRootMap(Root1Map, Root1ExpectedCount, Root1ExpectedBounds, Root1Audit, Error)) return false;
        return !bIncludeRoot7 || AuditRootMap(Root7Map, Root7ExpectedCount, Root7ExpectedBounds, Root7Audit, Error);
    };
    auto ValidateUniqueInstanceIds = [&](const FRootMapAudit& Root3Audit, const FRootMapAudit& Root1Audit,
        const FRootMapAudit& Root7Audit)
    {
        TArray<const FRootMapAudit*> Audits{&Root3Audit, &Root1Audit};
        if (bIncludeRoot7) Audits.Add(&Root7Audit);
        for (int32 Left = 0; Left < Audits.Num(); ++Left)
        {
            for (int32 Right = Left + 1; Right < Audits.Num(); ++Right)
            {
                for (uint64 InstanceId : Audits[Left]->InstanceIds)
                {
                    if (Audits[Right]->InstanceIds.Contains(InstanceId))
                    {
                        Error = FString::Printf(TEXT("Geometry instance id %llu is duplicated between streamed roots."), InstanceId);
                        return false;
                    }
                }
            }
        }
        return true;
    };
    auto ValidateRootContacts = [&](const FRootMapAudit& Root3Audit, const FRootMapAudit& Root1Audit,
        const FRootMapAudit& Root7Audit, FVector& JunctionOverlapCm, FVector& Root7OverlapCm)
    {
        if (!ValidateJunctionPair(Root3Audit, Root1Audit, JunctionOverlapCm, Error)) return false;
        return !bIncludeRoot7 || ValidateRoot7Contact(Root7Audit, Root1Audit, Root7OverlapCm, Error);
    };

    if (bValidateOnly)
    {
        UPackage* AssemblyPackage = LoadPackage(nullptr, *AssemblyMap, LOAD_NoWarn | LOAD_Quiet);
        UWorld* LoadedAssembly = AssemblyPackage
            ? FindObject<UWorld>(AssemblyPackage, *FPackageName::GetLongPackageAssetName(AssemblyMap)) : nullptr;
        const int32 ExpectedRefCount = bIncludeRoot7 ? 3 : 2;
        if (!LoadedAssembly || !LoadedAssembly->PersistentLevel || LoadedAssembly->GetStreamingLevels().Num() != ExpectedRefCount)
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded assembly %s does not contain exactly %d streamed sublevels."), *AssemblyMap, ExpectedRefCount);
            return 1;
        }
        TSet<FName> StreamedPackages;
        for (ULevelStreaming* Streaming : LoadedAssembly->GetStreamingLevels())
        {
            if (!Streaming || !Streaming->GetClass()->IsChildOf(ULevelStreamingAlwaysLoaded::StaticClass())
                || !Streaming->LevelTransform.Equals(FTransform::Identity) || !Streaming->ShouldBeLoaded()
                || !Streaming->GetShouldBeVisibleFlag())
            {
                UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded assembly has an invalid streaming type/state/transform."));
                return 1;
            }
            StreamedPackages.Add(Streaming->GetWorldAssetPackageFName());
        }
        if (StreamedPackages.Num() != ExpectedRefCount || !StreamedPackages.Contains(FName(*Root3Map))
            || !StreamedPackages.Contains(FName(*Root1Map))
            || (bIncludeRoot7 && !StreamedPackages.Contains(FName(*Root7Map))))
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded assembly does not reference exactly the requested Root3/Root1/Root7 maps."));
            return 1;
        }
        int32 PersistentGeometryActors = 0;
        for (AActor* Actor : LoadedAssembly->PersistentLevel->Actors)
        {
            if (Actor && Actor->IsA<AStaticMeshActor>()) ++PersistentGeometryActors;
        }
        if (PersistentGeometryActors != 0)
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded assembly contains %d copied static-mesh actors."), PersistentGeometryActors);
            return 1;
        }
        FRootMapAudit Root3Audit, Root1Audit, Root7Audit;
        if (!AuditAllRoots(Root3Audit, Root1Audit, Root7Audit))
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded sublevel audit failed: %s"), *Error);
            return 1;
        }
        if (!ValidateUniqueInstanceIds(Root3Audit, Root1Audit, Root7Audit))
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded sublevel instance-id validation failed: %s"), *Error);
            return 1;
        }
        FVector JunctionOverlapCm, Root7OverlapCm;
        if (!ValidateRootContacts(Root3Audit, Root1Audit, Root7Audit, JunctionOverlapCm, Root7OverlapCm))
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Reloaded root-contact validation failed: %s"), *Error);
            return 1;
        }
        UE_LOG(LogAtlasEftBuildSectorAssembly, Display,
            TEXT("PASS reload assembly=%s refs=%d Root3=%d Root1=%d Root7=%d persistentStaticMeshActors=%d transforms=identity duplicateInstanceIds=0 bounds=sectorReports junctionPair=14303/6257 overlapCm=%s root7Contact=20204/6230 root7OverlapCm=%s"),
            *AssemblyMap, ExpectedRefCount, Root3Audit.GeometryActors, Root1Audit.GeometryActors, Root7Audit.GeometryActors,
            PersistentGeometryActors, *JunctionOverlapCm.ToString(), *Root7OverlapCm.ToString());
        return 0;
    }

    FRootMapAudit Root3Audit, Root1Audit, Root7Audit;
    if (!AuditAllRoots(Root3Audit, Root1Audit, Root7Audit))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Sublevel preflight failed: %s"), *Error);
        return 1;
    }
    if (!ValidateUniqueInstanceIds(Root3Audit, Root1Audit, Root7Audit))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Sublevel instance-id preflight failed: %s"), *Error);
        return 1;
    }
    FVector JunctionOverlapCm, Root7OverlapCm;
    if (!ValidateRootContacts(Root3Audit, Root1Audit, Root7Audit, JunctionOverlapCm, Root7OverlapCm))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Root-contact preflight failed: %s"), *Error);
        return 1;
    }

    if (!FPackageName::IsValidLongPackageName(AssemblyMap))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Invalid assembly map package %s."), *AssemblyMap);
        return 2;
    }
    const FString LevelName = FPackageName::GetLongPackageAssetName(AssemblyMap);
    const FString PackageFilename = FPackageName::LongPackageNameToFilename(AssemblyMap, FPackageName::GetMapPackageExtension());
    UPackage* Package = CreatePackage(*AssemblyMap);
    if (!Package)
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Could not create assembly package %s."), *AssemblyMap);
        return 1;
    }
    UWorld* AssemblyWorld = UWorld::CreateWorld(EWorldType::Editor, false, FName(*LevelName), Package);
    FScopedWorldCleanup AssemblyWorldCleanup{AssemblyWorld};
    if (!AssemblyWorld || !AssemblyWorld->PersistentLevel)
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Could not create assembly world %s."), *AssemblyMap);
        return 1;
    }
    Package->MarkAsFullyLoaded();
    AssemblyWorld->SetFlags(RF_Public | RF_Standalone);
    AssemblyWorld->GetWorldSettings()->bForceNoPrecomputedLighting = true;

    if (!AddAlwaysLoadedSublevel(AssemblyWorld, Root3Map, TEXT("AtlasRoot003Streaming"))
        || !AddAlwaysLoadedSublevel(AssemblyWorld, Root1Map, TEXT("AtlasRoot001Streaming"))
        || (bIncludeRoot7 && !AddAlwaysLoadedSublevel(AssemblyWorld, Root7Map, TEXT("AtlasRoot007Streaming"))))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Could not create always-loaded references to all audited maps."));
        return 1;
    }
    const int32 ExpectedRefCount = bIncludeRoot7 ? 3 : 2;
    if (AssemblyWorld->GetStreamingLevels().Num() != ExpectedRefCount)
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Assembly contains %d streaming refs instead of %d."), AssemblyWorld->GetStreamingLevels().Num(), ExpectedRefCount);
        return 1;
    }
    for (ULevelStreaming* Streaming : AssemblyWorld->GetStreamingLevels())
    {
        if (!Streaming || !Streaming->LevelTransform.Equals(FTransform::Identity)
            || !(Streaming->GetWorldAssetPackageFName() == FName(*Root3Map)
                || Streaming->GetWorldAssetPackageFName() == FName(*Root1Map)
                || (bIncludeRoot7 && Streaming->GetWorldAssetPackageFName() == FName(*Root7Map))))
        {
            UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Invalid/missing streamed map reference or non-identity transform."));
            return 1;
        }
    }
    int32 PersistentGeometryActors = 0;
    for (AActor* Actor : AssemblyWorld->PersistentLevel->Actors)
    {
        if (Actor && Actor->IsA<AStaticMeshActor>()) ++PersistentGeometryActors;
    }
    if (PersistentGeometryActors != 0)
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Assembly persistent level unexpectedly copied %d static-mesh actors."), PersistentGeometryActors);
        return 1;
    }

    Package->MarkPackageDirty();
    FAssetRegistryModule::AssetCreated(AssemblyWorld);
    FSavePackageArgs SaveArgs;
    SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
    SaveArgs.SaveFlags = SAVE_NoError;
    SaveArgs.bSlowTask = false;
    if (!UPackage::SavePackage(Package, AssemblyWorld, *PackageFilename, SaveArgs))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Failed saving assembly map %s."), *PackageFilename);
        return 1;
    }

    TSharedPtr<FJsonObject> Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("status"), TEXT("PASS"));
    Report->SetStringField(TEXT("assemblyMap"), AssemblyMap);
    Report->SetNumberField(TEXT("streamingRefs"), AssemblyWorld->GetStreamingLevels().Num());
    Report->SetNumberField(TEXT("persistentGeometryActors"), PersistentGeometryActors);
    Report->SetNumberField(TEXT("totalGeometryActors"), Root3Audit.GeometryActors + Root1Audit.GeometryActors + (bIncludeRoot7 ? Root7Audit.GeometryActors : 0));
    Report->SetBoolField(TEXT("duplicateInstanceIds"), false);
    Report->SetStringField(TEXT("streamingType"), TEXT("ULevelStreamingAlwaysLoaded"));
    TSharedPtr<FJsonObject> JunctionJson = MakeShared<FJsonObject>();
    JunctionJson->SetNumberField(TEXT("root3DoorInstanceId"), Root3DoorInstanceId);
    JunctionJson->SetNumberField(TEXT("root1PipeSupportInstanceId"), Root1PipeSupportInstanceId);
    JunctionJson->SetArrayField(TEXT("overlapCm"), TArray<TSharedPtr<FJsonValue>>{
        MakeShared<FJsonValueNumber>(JunctionOverlapCm.X), MakeShared<FJsonValueNumber>(JunctionOverlapCm.Y), MakeShared<FJsonValueNumber>(JunctionOverlapCm.Z)});
    JunctionJson->SetNumberField(TEXT("minimumOverlapPerAxisCm"), JunctionMinimumOverlapCm);
    JunctionJson->SetNumberField(TEXT("sectorBoundsToleranceCm"), SectorReportBoundsToleranceCm);
    Report->SetObjectField(TEXT("junctionPair"), JunctionJson);
    if (bIncludeRoot7)
    {
        TSharedPtr<FJsonObject> Root7ContactJson = MakeShared<FJsonObject>();
        Root7ContactJson->SetNumberField(TEXT("root7DoorInstanceId"), Root7DoorInstanceId);
        Root7ContactJson->SetNumberField(TEXT("root1SupportInstanceId"), Root1Root7SupportInstanceId);
        Root7ContactJson->SetArrayField(TEXT("overlapCm"), TArray<TSharedPtr<FJsonValue>>{
            MakeShared<FJsonValueNumber>(Root7OverlapCm.X), MakeShared<FJsonValueNumber>(Root7OverlapCm.Y), MakeShared<FJsonValueNumber>(Root7OverlapCm.Z)});
        Root7ContactJson->SetNumberField(TEXT("minimumOverlapPerAxisCm"), JunctionMinimumOverlapCm);
        Report->SetObjectField(TEXT("root7Root1Contact"), Root7ContactJson);
    }
    TArray<TSharedPtr<FJsonValue>> RootReports;
    TArray<TPair<FString, FRootMapAudit>> AuditedRoots{{Root3Map, Root3Audit}, {Root1Map, Root1Audit}};
    if (bIncludeRoot7) AuditedRoots.Add({Root7Map, Root7Audit});
    for (const auto& Pair : AuditedRoots)
    {
        TSharedPtr<FJsonObject> RootJson = MakeShared<FJsonObject>();
        RootJson->SetStringField(TEXT("map"), Pair.Key);
        RootJson->SetNumberField(TEXT("geometryActors"), Pair.Value.GeometryActors);
        RootJson->SetNumberField(TEXT("instanceIds"), Pair.Value.InstanceIds.Num());
        RootJson->SetObjectField(TEXT("bounds"), BoundsJson(Pair.Value.Bounds));
        RootJson->SetStringField(TEXT("levelTransform"), FTransform::Identity.ToString());
        RootReports.Add(MakeShared<FJsonValueObject>(RootJson));
    }
    Report->SetArrayField(TEXT("sublevels"), RootReports);
    FString ReportText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&ReportText);
    FJsonSerializer::Serialize(Report.ToSharedRef(), Writer);
    const FString ReportPath = FPaths::ProjectSavedDir() / (bIncludeRoot7
        ? TEXT("AtlasAssembly_LabyrinthStructural_Validation.json")
        : TEXT("AtlasAssembly_Root003_Root001_Validation.json"));
    if (!FFileHelper::SaveStringToFile(ReportText, *ReportPath))
    {
        UE_LOG(LogAtlasEftBuildSectorAssembly, Error, TEXT("Could not save assembly report %s."), *ReportPath);
        return 1;
    }
    UE_LOG(LogAtlasEftBuildSectorAssembly, Display,
        TEXT("PASS assembly=%s refs=%d Root3=%d Root1=%d Root7=%d totalGeometry=%d persistentStaticMeshActors=%d bounds=sectorReports junctionPair=14303/6257 overlapCm=%s root7Contact=20204/6230 root7OverlapCm=%s Root7Bounds=%s..%s"),
        *AssemblyMap, ExpectedRefCount, Root3Audit.GeometryActors, Root1Audit.GeometryActors, Root7Audit.GeometryActors,
        Root3Audit.GeometryActors + Root1Audit.GeometryActors + (bIncludeRoot7 ? Root7Audit.GeometryActors : 0), PersistentGeometryActors,
        *JunctionOverlapCm.ToString(),
        *Root7OverlapCm.ToString(), *Root7Audit.Bounds.Min.ToString(), *Root7Audit.Bounds.Max.ToString());
    return 0;
}
