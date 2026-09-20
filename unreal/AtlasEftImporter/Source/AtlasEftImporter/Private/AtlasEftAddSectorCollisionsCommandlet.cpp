#include "AtlasEftAddSectorCollisionsCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AtlasEftCoordinate.h"
#include "AtlasEftPackReader.h"
#include "Dom/JsonObject.h"
#include "Components/BoxComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/SceneComponent.h"
#include "Components/SphereComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "FileHelpers.h"
#include "Materials/Material.h"
#include "MeshDescription.h"
#include "MeshDescriptionBuilder.h"
#include "Misc/FileHelper.h"
#include "Misc/Crc.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "PhysicsEngine/BodySetup.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "StaticMeshAttributes.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftAddSectorCollisions, Log, All);

namespace
{
int32 TargetRootId = 3;
int32 ExpectedGeometryCount = 1944;
int32 ExpectedCollisionCount = 0;
FString TargetMap = TEXT("/Game/Atlas/Sectors/Root_003/L_Atlas_Root003_Area03");
constexpr int32 ExpectedRootColliderRows = 3407;
constexpr int32 ExpectedCollisionMeshCount = 1700;
constexpr int32 ExpectedSimpleCollisionCount = 2;
constexpr float ColliderFieldTolerance = 0.00005f;
constexpr double BoundsToleranceCm = 0.25;

struct FColliderSidecarRecord
{
    FString RootName;
    FString Type;
    FString MeshName;
    int32 Level = -1;
    int32 Layer = -1;
    bool bConvex = false;
};

struct FColliderMeshAsset
{
    TObjectPtr<UStaticMesh> Mesh = nullptr;
    FBox LocalBounds = FBox(EForceInit::ForceInit);
};

struct FSourceBounds
{
    FBox Box = FBox(EForceInit::ForceInit);
    double MaxTransformErrorCm = 0.0;
};

bool Near(float A, float B)
{
    return FMath::IsFinite(A) && FMath::IsFinite(B) && FMath::Abs(A - B) <= ColliderFieldTolerance;
}

bool ReadJsonVector(const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, int32 ExpectedCount, TArray<double>& Out)
{
    if (!Object.IsValid() || !Object->HasTypedField<EJson::Array>(Name)) return false;
    const TArray<TSharedPtr<FJsonValue>>& Values = Object->GetArrayField(Name);
    if (Values.Num() != ExpectedCount) return false;
    Out.Reset(ExpectedCount);
    for (const TSharedPtr<FJsonValue>& Value : Values)
    {
        const double Number = Value.IsValid() ? Value->AsNumber() : NAN;
        if (!FMath::IsFinite(Number)) return false;
        Out.Add(Number);
    }
    return true;
}

bool ReadJsonInt(const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, int32& Out)
{
    double Number = 0.0;
    if (!Object.IsValid() || !Object->TryGetNumberField(Name, Number)
        || !FMath::IsFinite(Number) || FMath::FloorToDouble(Number) != Number
        || Number < MIN_int32 || Number > MAX_int32)
    {
        return false;
    }
    Out = static_cast<int32>(Number);
    return true;
}

bool ReadOptionalTrue(const TSharedPtr<FJsonObject>& Object, const TCHAR* Name)
{
    bool Value = false;
    return Object.IsValid() && Object->TryGetBoolField(Name, Value) && Value;
}

FString ColliderKindName(AtlasEft::EColliderKind Kind)
{
    switch (Kind)
    {
    case AtlasEft::EColliderKind::Box: return TEXT("box");
    case AtlasEft::EColliderKind::Sphere: return TEXT("sphere");
    case AtlasEft::EColliderKind::Capsule: return TEXT("capsule");
    case AtlasEft::EColliderKind::Mesh: return TEXT("mesh");
    default: return TEXT("invalid");
    }
}

double Determinant(const float A[12])
{
    return static_cast<double>(A[0]) * (static_cast<double>(A[5]) * A[10] - static_cast<double>(A[6]) * A[9])
        - static_cast<double>(A[1]) * (static_cast<double>(A[4]) * A[10] - static_cast<double>(A[6]) * A[8])
        + static_cast<double>(A[2]) * (static_cast<double>(A[4]) * A[9] - static_cast<double>(A[5]) * A[8]);
}

FTransform MakeUnrealTransform(const AtlasEft::FColliderDesc& Collider)
{
    double AtlasAffine[12];
    double UnrealAffine[12] = {};
    for (int32 Index = 0; Index < 12; ++Index) AtlasAffine[Index] = Collider.Affine[Index];
    AtlasEft::FCoordinate::AffineToUnreal(AtlasAffine, UnrealAffine);
    const FMatrix Matrix(
        FPlane(UnrealAffine[0], UnrealAffine[4], UnrealAffine[8], 0.0),
        FPlane(UnrealAffine[1], UnrealAffine[5], UnrealAffine[9], 0.0),
        FPlane(UnrealAffine[2], UnrealAffine[6], UnrealAffine[10], 0.0),
        FPlane(UnrealAffine[3], UnrealAffine[7], UnrealAffine[11], 1.0));
    return FTransform(Matrix);
}

FVector3d TransformAtlasPoint(const float A[12], const FVector3d& P)
{
    return FVector3d(
        A[0] * P.X + A[1] * P.Y + A[2] * P.Z + A[3],
        A[4] * P.X + A[5] * P.Y + A[6] * P.Z + A[7],
        A[8] * P.X + A[9] * P.Y + A[10] * P.Z + A[11]);
}

bool ReadColliderSidecar(
    const FString& SourceJsonPath,
    const AtlasEft::FManifest& Manifest,
    const TArray<AtlasEft::FColliderDesc>& Packed,
    int32 RootId,
    const TSet<uint32>& RootLevels,
    TArray<FColliderSidecarRecord>& OutSource,
    TArray<int32>& OutCandidates,
    TMap<int32, FString>& OutLayerNames,
    AtlasEft::FAuditReport& Report)
{
    OutSource.Reset();
    OutCandidates.Reset();
    OutLayerNames.Reset();

    FString JsonText;
    TSharedPtr<FJsonObject> JsonRoot;
    if (!FFileHelper::LoadFileToString(JsonText, *SourceJsonPath))
    {
        Report.Error(SourceJsonPath, TEXT("Cannot read the source colliders.json sidecar."));
        return false;
    }
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, JsonRoot) || !JsonRoot.IsValid())
    {
        Report.Error(SourceJsonPath, TEXT("Invalid source colliders.json."));
        return false;
    }
    if (!JsonRoot->HasTypedField<EJson::Array>(TEXT("colliders"))
        || !JsonRoot->HasTypedField<EJson::Object>(TEXT("layers")))
    {
        Report.Error(SourceJsonPath, TEXT("Sidecar requires colliders and layers."));
        return false;
    }
    const TArray<TSharedPtr<FJsonValue>>& SourceRows = JsonRoot->GetArrayField(TEXT("colliders"));
    if (SourceRows.Num() != Packed.Num() || SourceRows.Num() != Manifest.ColliderCount)
    {
        Report.Error(SourceJsonPath, FString::Printf(TEXT("Sidecar records (%d), packed records (%d), and manifest count (%llu) must match exactly."),
            SourceRows.Num(), Packed.Num(), Manifest.ColliderCount));
        return false;
    }

    for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : JsonRoot->GetObjectField(TEXT("layers"))->Values)
    {
        uint32 Layer = 0;
        if (!LexTryParseString(Layer, *Pair.Key) || !Pair.Value.IsValid() || Pair.Value->Type != EJson::String)
        {
            Report.Error(SourceJsonPath, FString::Printf(TEXT("Malformed layer name '%s'."), *Pair.Key));
            return false;
        }
        const FString* ManifestName = Manifest.LayerNames.Find(Layer);
        const FString SidecarName = Pair.Value->AsString();
        if (!ManifestName || *ManifestName != SidecarName)
        {
            Report.Error(SourceJsonPath, FString::Printf(TEXT("Layer %u differs between manifest and source sidecar."), Layer));
            return false;
        }
        OutLayerNames.Add(static_cast<int32>(Layer), SidecarName);
    }
    if (OutLayerNames.Num() != Manifest.LayerNames.Num())
    {
        Report.Error(SourceJsonPath, TEXT("Layer table is incomplete relative to the pack manifest."));
        return false;
    }

    if (!Manifest.Roots.IsValidIndex(RootId))
    {
        Report.Error(TEXT("manifest.roots"), FString::Printf(TEXT("RootId %d is outside the manifest root table."), RootId));
        return false;
    }
    const FString TargetRootName = Manifest.Roots[RootId];
    TMap<FString, uint32> RootIdsByName;
    for (int32 Index = 0; Index < Manifest.Roots.Num(); ++Index)
    {
        if (RootIdsByName.Contains(Manifest.Roots[Index]))
        {
            Report.Error(TEXT("manifest.roots"), FString::Printf(TEXT("Root name '%s' is not unique."), *Manifest.Roots[Index]));
            return false;
        }
        RootIdsByName.Add(Manifest.Roots[Index], static_cast<uint32>(Index));
    }

    TMap<FString, uint32> ColliderMeshIdsByName;
    for (const AtlasEft::FColliderMeshDesc& Mesh : Manifest.ColliderMeshes)
    {
        if (ColliderMeshIdsByName.Contains(Mesh.Name))
        {
            Report.Error(TEXT("manifest.colliderMeshes"), FString::Printf(TEXT("Collider mesh name '%s' is ambiguous."), *Mesh.Name));
            return false;
        }
        ColliderMeshIdsByName.Add(Mesh.Name, Mesh.Id);
    }

    OutSource.Reserve(SourceRows.Num());
    for (int32 Index = 0; Index < SourceRows.Num(); ++Index)
    {
        const TSharedPtr<FJsonObject> Row = SourceRows[Index].IsValid() ? SourceRows[Index]->AsObject() : nullptr;
        if (!Row.IsValid())
        {
            Report.Error(SourceJsonPath, FString::Printf(TEXT("Collider source row %d is not an object."), Index));
            return false;
        }

        FColliderSidecarRecord& Source = OutSource.AddDefaulted_GetRef();
        int32 Layer = -1;
        if (!Row->TryGetStringField(TEXT("root"), Source.RootName)
            || !Row->TryGetStringField(TEXT("t"), Source.Type)
            || !ReadJsonInt(Row, TEXT("lv"), Source.Level)
            || !ReadJsonInt(Row, TEXT("lyr"), Layer))
        {
            Report.Error(SourceJsonPath, FString::Printf(TEXT("Collider source row %d lacks root/type/level/layer."), Index));
            return false;
        }
        Source.Layer = Layer;

        AtlasEft::EColliderKind Kind;
        if (Source.Type == TEXT("box")) Kind = AtlasEft::EColliderKind::Box;
        else if (Source.Type == TEXT("sphere")) Kind = AtlasEft::EColliderKind::Sphere;
        else if (Source.Type == TEXT("capsule")) Kind = AtlasEft::EColliderKind::Capsule;
        else if (Source.Type == TEXT("mesh")) Kind = AtlasEft::EColliderKind::Mesh;
        else
        {
            Report.Error(SourceJsonPath, FString::Printf(TEXT("Collider source row %d has unknown type '%s'."), Index, *Source.Type));
            return false;
        }

        TArray<double> Matrix;
        TArray<double> Center;
        TArray<double> Size;
        if (!ReadJsonVector(Row, TEXT("m"), 16, Matrix))
        {
            Report.Error(SourceJsonPath, FString::Printf(TEXT("Collider source row %d has an invalid world matrix."), Index));
            return false;
        }
        if (Kind != AtlasEft::EColliderKind::Mesh && !ReadJsonVector(Row, TEXT("c"), 3, Center))
        {
            Report.Error(SourceJsonPath, FString::Printf(TEXT("Primitive collider source row %d has no center."), Index));
            return false;
        }
        if (Center.IsEmpty()) Center.Init(0.0, 3);
        if (Kind == AtlasEft::EColliderKind::Box)
        {
            if (!ReadJsonVector(Row, TEXT("s"), 3, Size))
            {
                Report.Error(SourceJsonPath, FString::Printf(TEXT("Box collider source row %d has no size."), Index));
                return false;
            }
        }
        else if (Kind == AtlasEft::EColliderKind::Sphere || Kind == AtlasEft::EColliderKind::Capsule)
        {
            double Radius = 0.0;
            if (!Row->TryGetNumberField(TEXT("r"), Radius) || !FMath::IsFinite(Radius))
            {
                Report.Error(SourceJsonPath, FString::Printf(TEXT("Sphere/capsule source row %d has no radius."), Index));
                return false;
            }
            Size.Add(Radius); Size.Add(0.0); Size.Add(0.0);
            if (Kind == AtlasEft::EColliderKind::Capsule)
            {
                double Height = 0.0;
                int32 Direction = -1;
                if (!Row->TryGetNumberField(TEXT("h"), Height) || !FMath::IsFinite(Height)
                    || !ReadJsonInt(Row, TEXT("d"), Direction))
                {
                    Report.Error(SourceJsonPath, FString::Printf(TEXT("Capsule source row %d has invalid height/direction."), Index));
                    return false;
                }
                Size[1] = Height; Size[2] = Direction;
            }
        }
        else
        {
            Size.Init(0.0, 3);
            if (!Row->TryGetStringField(TEXT("mesh"), Source.MeshName))
            {
                Report.Error(SourceJsonPath, FString::Printf(TEXT("Mesh collider source row %d has no mesh name."), Index));
                return false;
            }
            if (!Row->TryGetBoolField(TEXT("convex"), Source.bConvex))
            {
                Report.Error(SourceJsonPath, FString::Printf(TEXT("Mesh collider source row %d has no convexity flag."), Index));
                return false;
            }
        }

        const AtlasEft::FColliderDesc& PackedCollider = Packed[Index];
        float ExpectedAffine[12] = {};
        for (int32 RowIndex = 0; RowIndex < 3; ++RowIndex)
        {
            const double RowSign = RowIndex == 0 ? -1.0 : 1.0;
            for (int32 Column = 0; Column < 3; ++Column)
            {
                const double ColumnSign = Column == 0 ? -1.0 : 1.0;
                ExpectedAffine[RowIndex * 4 + Column] = static_cast<float>(Matrix[RowIndex * 4 + Column] * RowSign * ColumnSign);
            }
            ExpectedAffine[RowIndex * 4 + 3] = static_cast<float>(Matrix[RowIndex * 4 + 3] * RowSign);
        }
        const double Det = Determinant(ExpectedAffine);
        const int32 MeshId = Kind == AtlasEft::EColliderKind::Mesh
            ? static_cast<int32>(ColliderMeshIdsByName.FindRef(Source.MeshName)) : -1;
        if (Kind == AtlasEft::EColliderKind::Mesh && !ColliderMeshIdsByName.Contains(Source.MeshName))
        {
            Report.Error(SourceJsonPath, FString::Printf(TEXT("Mesh collider source row %d names absent mesh '%s'."), Index, *Source.MeshName));
            return false;
        }
        const uint32 ExpectedFlags = (ReadOptionalTrue(Row, TEXT("trig")) ? 0x1u : 0u)
            | (ReadOptionalTrue(Row, TEXT("nav_ignore")) ? 0x2u : 0u)
            | (ReadOptionalTrue(Row, TEXT("vis")) ? 0x4u : 0u)
            | (Det < 0.0 ? 0x8u : 0u);
        const FVector3f ExpectedCenter(
            static_cast<float>(-Center[0]), static_cast<float>(Center[1]), static_cast<float>(Center[2]));
        const FVector3f ExpectedShape(static_cast<float>(Size[0]), static_cast<float>(Size[1]), static_cast<float>(Size[2]));
        bool bMatch = PackedCollider.Kind == Kind && PackedCollider.MeshId == MeshId
            && PackedCollider.Layer == static_cast<uint32>(Layer) && PackedCollider.Flags == ExpectedFlags;
        for (int32 Component = 0; Component < 12; ++Component) bMatch &= Near(PackedCollider.Affine[Component], ExpectedAffine[Component]);
        bMatch &= Near(PackedCollider.Center.X, ExpectedCenter.X) && Near(PackedCollider.Center.Y, ExpectedCenter.Y) && Near(PackedCollider.Center.Z, ExpectedCenter.Z);
        bMatch &= Near(PackedCollider.Shape.X, ExpectedShape.X) && Near(PackedCollider.Shape.Y, ExpectedShape.Y) && Near(PackedCollider.Shape.Z, ExpectedShape.Z);
        if (!bMatch)
        {
            Report.Error(SourceJsonPath, FString::Printf(TEXT("Source sidecar row %d does not match packed collider record %llu."), Index, PackedCollider.Index));
            return false;
        }

        if (Source.RootName == TargetRootName)
        {
            if (Source.Level < 0 || !RootLevels.Contains(static_cast<uint32>(Source.Level)))
            {
                Report.Error(SourceJsonPath, FString::Printf(TEXT("Root %d collider %d level %d does not match its RootId geometry records."), RootId, Index, Source.Level));
                return false;
            }
            const FString* LayerName = OutLayerNames.Find(Layer);
            const bool bBlockingLayer = LayerName && (*LayerName == TEXT("LowPolyCollider")
                || *LayerName == TEXT("DoorLowPolyCollider") || *LayerName == TEXT("TransparentCollider")
                || *LayerName == TEXT("Interactive") || *LayerName == TEXT("LevelBorder")
                || *LayerName == TEXT("Terrain") || *LayerName == TEXT("Default"));
            if (bBlockingLayer && (PackedCollider.Flags & 0x1u) == 0)
            {
                OutCandidates.Add(Index);
            }
        }
    }

    if (RootIdsByName.FindRef(TargetRootName) != static_cast<uint32>(RootId))
    {
        Report.Error(SourceJsonPath, TEXT("Target root name did not resolve uniquely to the requested RootId."));
        return false;
    }
    return true;
}

FString InstanceIdTag(const AActor* Actor)
{
    if (!Actor) return FString();
    for (const FName& Tag : Actor->Tags)
    {
        const FString Value = Tag.ToString();
        if (Value.StartsWith(TEXT("AtlasInstance="))) return Value;
    }
    return FString();
}

bool HasManagedRootTag(const AActor* Actor, int32 RootId)
{
    if (!Actor || !Actor->Tags.Contains(FName(TEXT("AtlasManagedCollision")))) return false;
    return Actor->Tags.Contains(FName(*FString::Printf(TEXT("AtlasRootId=%d"), RootId)));
}

bool CaptureGeometrySignature(
    UWorld* World,
    const TMap<uint64, const AtlasEft::FInstanceDesc*>& Expected,
    TMap<uint64, FString>& OutSignature,
    FString& OutError)
{
    OutSignature.Reset();
    OutError.Reset();
    if (!World || !World->PersistentLevel)
    {
        OutError = TEXT("Loaded map has no persistent level.");
        return false;
    }

    for (AActor* Actor : World->PersistentLevel->Actors)
    {
        if (!Actor || !Actor->IsA<AStaticMeshActor>()) continue;
        const FString Tag = InstanceIdTag(Actor);
        if (Tag.IsEmpty())
        {
            OutError = FString::Printf(TEXT("Untyped StaticMeshActor '%s' exists in the target map."), *Actor->GetName());
            return false;
        }
        FString Number;
        if (!Tag.Split(TEXT("="), nullptr, &Number))
        {
            OutError = FString::Printf(TEXT("Malformed instance tag '%s'."), *Tag);
            return false;
        }
        uint64 InstanceId = 0;
        if (!LexTryParseString(InstanceId, *Number) || !Expected.Contains(InstanceId))
        {
            OutError = FString::Printf(TEXT("Unexpected or invalid geometry instance tag '%s'."), *Tag);
            return false;
        }
        const AtlasEft::FInstanceDesc* Source = Expected.FindRef(InstanceId);
        const AStaticMeshActor* MeshActor = Cast<AStaticMeshActor>(Actor);
        const UStaticMeshComponent* Component = MeshActor ? MeshActor->GetStaticMeshComponent() : nullptr;
        if (!Component || !Component->GetStaticMesh())
        {
            OutError = FString::Printf(TEXT("Geometry instance %llu has no static mesh."), InstanceId);
            return false;
        }
        if (OutSignature.Contains(InstanceId))
        {
            OutError = FString::Printf(TEXT("Geometry instance %llu is duplicated."), InstanceId);
            return false;
        }
        const FString ExpectedName = FString::Printf(TEXT("Atlas_I%06llu_M%04u"), InstanceId, Source->MeshId);
        if (Actor->GetName() != ExpectedName)
        {
            OutError = FString::Printf(TEXT("Geometry instance %llu actor name changed: '%s' instead of '%s'."), InstanceId, *Actor->GetName(), *ExpectedName);
            return false;
        }
        FString Tags;
        for (const FName& ActorTag : Actor->Tags) Tags += ActorTag.ToString() + TEXT(",");
        const FString Signature = FString::Printf(TEXT("%s|%s|%s|%s|%s"), *Actor->GetPathName(),
            *Component->GetStaticMesh()->GetPathName(), *Actor->GetActorTransform().ToString(), *Actor->GetFolderPath().ToString(), *Tags);
        OutSignature.Add(InstanceId, Signature);
    }
    if (OutSignature.Num() != Expected.Num())
    {
        OutError = FString::Printf(TEXT("Target map has %d geometry actors; the pack selection requires %d."), OutSignature.Num(), Expected.Num());
        return false;
    }
    return true;
}

FBox ExpectedMeshWorldBounds(const AtlasEft::FColliderDesc& Collider, const FBox& LocalBounds)
{
    FBox Expected(EForceInit::ForceInit);
    for (int32 Corner = 0; Corner < 8; ++Corner)
    {
        const FVector UnrealLocal(
            (Corner & 1) ? LocalBounds.Max.X : LocalBounds.Min.X,
            (Corner & 2) ? LocalBounds.Max.Y : LocalBounds.Min.Y,
            (Corner & 4) ? LocalBounds.Max.Z : LocalBounds.Min.Z);
        const FVector3d AtlasLocal(-UnrealLocal.Y / 100.0 + Collider.Center.X,
            UnrealLocal.Z / 100.0 + Collider.Center.Y, UnrealLocal.X / 100.0 + Collider.Center.Z);
        Expected += FVector(AtlasEft::FCoordinate::PositionToUnreal(TransformAtlasPoint(Collider.Affine, AtlasLocal)));
    }
    return Expected;
}

double BoxError(const FBox& A, const FBox& B)
{
    if (!A.IsValid || !B.IsValid) return TNumericLimits<double>::Max();
    const FVector MinDelta = A.Min - B.Min;
    const FVector MaxDelta = A.Max - B.Max;
    return FMath::Max3(FMath::Max3(FMath::Abs(MinDelta.X), FMath::Abs(MinDelta.Y), FMath::Abs(MinDelta.Z)),
        FMath::Max3(FMath::Abs(MaxDelta.X), FMath::Abs(MaxDelta.Y), FMath::Abs(MaxDelta.Z)), 0.0);
}

bool BuildColliderMeshAsset(
    const AtlasEft::FDecodedColliderMesh& Decoded,
    const FString& Destination,
    FColliderMeshAsset& OutAsset,
    FString& OutError)
{
    OutError.Reset();
    const FString Name = FString::Printf(TEXT("SM_AtlasColliderMesh_%04u"), Decoded.Id);
    const FString PackageName = Destination + TEXT("/") + Name;
    UPackage* Package = CreatePackage(*PackageName);
    if (!Package)
    {
        OutError = FString::Printf(TEXT("Could not create package %s."), *PackageName);
        return false;
    }
    Package->FullyLoad();
    UStaticMesh* Mesh = FindObject<UStaticMesh>(Package, *Name);
    const bool bNew = Mesh == nullptr;
    if (bNew) Mesh = NewObject<UStaticMesh>(Package, *Name, RF_Public | RF_Standalone);
    if (!Mesh)
    {
        OutError = FString::Printf(TEXT("Could not create collider mesh asset %s."), *PackageName);
        return false;
    }

    Mesh->PreEditChange(nullptr);
    Mesh->SetNumSourceModels(0);
    Mesh->GetStaticMaterials().Reset();
    FStaticMeshSourceModel& SourceModel = Mesh->AddSourceModel();
    SourceModel.BuildSettings.bRecomputeNormals = true;
    SourceModel.BuildSettings.bRecomputeTangents = true;
    SourceModel.BuildSettings.bUseMikkTSpace = false;
    SourceModel.BuildSettings.bRemoveDegenerates = false;
    SourceModel.BuildSettings.bGenerateLightmapUVs = false;

    FMeshDescription Description;
    FStaticMeshAttributes Attributes(Description);
    Attributes.Register();
    FMeshDescriptionBuilder Builder;
    Builder.SetMeshDescription(&Description);
    Builder.SetNumUVLayers(1);
    Builder.ReserveNewVertices(Decoded.Vertices.Num());
    TArray<FVertexID> VertexIds;
    VertexIds.Reserve(Decoded.Vertices.Num());
    FBox LocalBounds(EForceInit::ForceInit);
    for (const FVector3f& Vertex : Decoded.Vertices)
    {
        const FVector Position(AtlasEft::FCoordinate::PositionToUnreal(FVector3d(Vertex)));
        VertexIds.Add(Builder.AppendVertex(Position));
        LocalBounds += Position;
    }
    const FName SlotName(TEXT("AtlasCollider"));
    const FPolygonGroupID PolygonGroup = Builder.AppendPolygonGroup(SlotName);
    Mesh->GetStaticMaterials().Add(FStaticMaterial(UMaterial::GetDefaultMaterial(MD_Surface), SlotName, SlotName));
    for (int32 Index = 0; Index < Decoded.Indices.Num(); Index += 3)
    {
        const uint32 SourceIndices[3] = {Decoded.Indices[Index], Decoded.Indices[Index + 2], Decoded.Indices[Index + 1]};
        FVertexInstanceID Instances[3];
        for (int32 Corner = 0; Corner < 3; ++Corner)
        {
            Instances[Corner] = Builder.AppendInstance(VertexIds[SourceIndices[Corner]]);
            Builder.SetInstanceUV(Instances[Corner], FVector2D::ZeroVector, 0);
        }
        Builder.AppendTriangle(Instances[0], Instances[1], Instances[2], PolygonGroup);
    }
    Mesh->CreateMeshDescription(0, MoveTemp(Description));
    Mesh->CommitMeshDescription(0);
    Mesh->SetImportVersion(EImportStaticMeshVersion::LastVersion);
    Mesh->SetLightMapCoordinateIndex(0);
    Mesh->CreateBodySetup();
    if (UBodySetup* Body = Mesh->GetBodySetup())
    {
        Body->CollisionTraceFlag = CTF_UseComplexAsSimple;
        Body->bDoubleSidedGeometry = true;
        Body->bGenerateMirroredCollision = false;
    }

    TArray<FText> BuildErrors;
    Mesh->Build(true, &BuildErrors);
    if (!BuildErrors.IsEmpty() || !Mesh->GetRenderData() || Mesh->GetRenderData()->LODResources.IsEmpty())
    {
        OutError = FString::Printf(TEXT("Collider mesh %u failed StaticMesh build (%d errors)."), Decoded.Id, BuildErrors.Num());
        return false;
    }
    Mesh->PostEditChange();
    if (UBodySetup* Body = Mesh->GetBodySetup())
    {
        Body->CollisionTraceFlag = CTF_UseComplexAsSimple;
        Body->bDoubleSidedGeometry = true;
        Body->bGenerateMirroredCollision = false;
        Body->InvalidatePhysicsData();
    }
    Mesh->MarkPackageDirty();
    if (bNew) FAssetRegistryModule::AssetCreated(Mesh);
    const FString Filename = FPackageName::LongPackageNameToFilename(PackageName, FPackageName::GetAssetPackageExtension());
    FSavePackageArgs SaveArgs;
    SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
    SaveArgs.SaveFlags = SAVE_NoError;
    SaveArgs.bSlowTask = false;
    if (!UPackage::SavePackage(Package, Mesh, *Filename, SaveArgs))
    {
        OutError = FString::Printf(TEXT("Could not save collider mesh asset %s."), *Filename);
        return false;
    }
    OutAsset.Mesh = Mesh;
    OutAsset.LocalBounds = LocalBounds;
    return true;
}

void ConfigureCollision(UPrimitiveComponent* Component)
{
    Component->SetMobility(EComponentMobility::Static);
    Component->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
    Component->SetCollisionObjectType(ECC_WorldStatic);
    Component->SetCollisionResponseToAllChannels(ECR_Ignore);
    Component->SetCollisionResponseToChannel(ECC_Pawn, ECR_Block);
    Component->SetCollisionResponseToChannel(ECC_WorldStatic, ECR_Block);
    Component->SetGenerateOverlapEvents(false);
    Component->SetCanEverAffectNavigation(false);
    Component->SetVisibility(false, true);
    Component->SetHiddenInGame(true, true);
}

bool CreateCollisionLayerActor(UWorld* World, int32 RootId, int32 LayerId, const FString& LayerName, AActor*& OutActor, FString& OutError)
{
    OutActor = nullptr;
    if (!World)
    {
        OutError = TEXT("Cannot create collision layer actor without a world.");
        return false;
    }
    FActorSpawnParameters SpawnParameters;
    // Let Unreal assign a fresh UObject name. DestroyActor removes the managed actor
    // from the world immediately, but its name may remain reserved until GC; fixed
    // names make a second commandlet run fail before it can replace the prior set.
    AActor* Actor = World->SpawnActor<AActor>(AActor::StaticClass(), FTransform::Identity, SpawnParameters);
    if (!Actor)
    {
        OutError = FString::Printf(TEXT("Could not create collision layer actor for layer %d."), LayerId);
        return false;
    }
    USceneComponent* Root = NewObject<USceneComponent>(Actor, TEXT("AtlasCollisionRoot"));
    Actor->SetRootComponent(Root);
    Actor->AddInstanceComponent(Root);
    Root->SetMobility(EComponentMobility::Static);
    Root->RegisterComponent();
    Actor->SetFolderPath(FName(*FString::Printf(TEXT("Atlas/Root_%03d/Collision/%s"), RootId, *LayerName)));
    Actor->SetActorLabel(FString::Printf(TEXT("Atlas Root %03d Collisions | %s"), RootId, *LayerName));
    Actor->SetActorHiddenInGame(true);
    Actor->Tags.Add(FName(TEXT("AtlasManagedCollision")));
    Actor->Tags.Add(FName(*FString::Printf(TEXT("AtlasRootId=%d"), RootId)));
    Actor->Tags.Add(FName(*FString::Printf(TEXT("AtlasColliderLayer=%d"), LayerId)));
    Actor->Tags.Add(FName(TEXT("AtlasColliderSchema=1")));
    OutActor = Actor;
    return true;
}

bool SpawnColliderComponent(
    AActor* Actor,
    const AtlasEft::FColliderDesc& Collider,
    const FColliderSidecarRecord& Source,
    const FString& LayerName,
    const FColliderMeshAsset* MeshAsset,
    UPrimitiveComponent*& OutCollisionComponent,
    FString& OutError)
{
    OutCollisionComponent = nullptr;
    OutError.Reset();
    if (!Actor || !Actor->GetRootComponent())
    {
        OutError = FString::Printf(TEXT("No collision layer actor exists for record %llu."), Collider.Index);
        return false;
    }

    const FTransform ColliderTransform = MakeUnrealTransform(Collider);
    const FVector LocalCenter(AtlasEft::FCoordinate::VectorToUnreal(FVector3d(Collider.Center)) * 100.0);
    FQuat LocalShapeRotation = FQuat::Identity;
    const FName ComponentObjectName(*FString::Printf(TEXT("AtlasCollider_%llu"), Collider.Index));
    UPrimitiveComponent* Component = nullptr;
    if (Collider.Kind == AtlasEft::EColliderKind::Box)
    {
        UBoxComponent* Box = NewObject<UBoxComponent>(Actor, ComponentObjectName);
        Box->SetBoxExtent(FVector(FMath::Abs(Collider.Shape.Z), FMath::Abs(Collider.Shape.X), FMath::Abs(Collider.Shape.Y)) * 50.0, false);
        Component = Box;
    }
    else if (Collider.Kind == AtlasEft::EColliderKind::Sphere)
    {
        USphereComponent* Sphere = NewObject<USphereComponent>(Actor, ComponentObjectName);
        Sphere->SetSphereRadius(Collider.Shape.X * 100.0f, false);
        Component = Sphere;
    }
    else if (Collider.Kind == AtlasEft::EColliderKind::Capsule)
    {
        UCapsuleComponent* Capsule = NewObject<UCapsuleComponent>(Actor, ComponentObjectName);
        Capsule->SetCapsuleSize(Collider.Shape.X * 100.0f, FMath::Max(Collider.Shape.Y * 50.0f, Collider.Shape.X * 100.0f), false);
        FVector Axis;
        switch (FMath::RoundToInt(Collider.Shape.Z))
        {
        case 0: Axis = FVector(0.0, -1.0, 0.0); break;
        case 1: Axis = FVector(0.0, 0.0, 1.0); break;
        default: Axis = FVector(1.0, 0.0, 0.0); break;
        }
        LocalShapeRotation = FQuat::FindBetweenNormals(FVector::UpVector, Axis);
        Component = Capsule;
    }
    else if (Collider.Kind == AtlasEft::EColliderKind::Mesh)
    {
        if (!MeshAsset || !MeshAsset->Mesh)
        {
            OutError = FString::Printf(TEXT("Collider %llu has no imported collision mesh asset."), Collider.Index);
            return false;
        }
        if (Source.bConvex)
        {
            OutError = FString::Printf(TEXT("Convex mesh collider %llu is unsupported by the selected complex-as-simple path."), Collider.Index);
            return false;
        }
        UStaticMeshComponent* MeshComponent = NewObject<UStaticMeshComponent>(Actor, ComponentObjectName);
        MeshComponent->SetStaticMesh(MeshAsset->Mesh);
        MeshComponent->SetCastShadow(false);
        MeshComponent->SetAffectDistanceFieldLighting(false);
        MeshComponent->SetReceivesDecals(false);
        Component = MeshComponent;
    }
    else
    {
        OutError = FString::Printf(TEXT("Collider %llu has an unknown shape."), Collider.Index);
        return false;
    }

    Actor->AddInstanceComponent(Component);
    Component->SetupAttachment(Actor->GetRootComponent());
    FTransform ComponentTransform = ColliderTransform;
    ComponentTransform.SetRotation(ColliderTransform.GetRotation() * LocalShapeRotation);
    Component->SetRelativeTransform(ComponentTransform);
    Component->SetRelativeLocation(ColliderTransform.TransformPosition(LocalCenter));
    ConfigureCollision(Component);
    Component->ComponentTags.Add(FName(TEXT("AtlasManagedCollision")));
    Component->ComponentTags.Add(FName(*FString::Printf(TEXT("AtlasColliderRecord=%llu"), Collider.Index)));
    Component->ComponentTags.Add(FName(*FString::Printf(TEXT("AtlasColliderKind=%s"), *Source.Type)));
    Component->ComponentTags.Add(FName(*FString::Printf(TEXT("AtlasColliderLayer=%d"), Source.Layer)));
    Component->RegisterComponent();
    OutCollisionComponent = Component;
    return true;
}

FBox ExpectedBoxWorldBounds(const AtlasEft::FColliderDesc& Collider)
{
    FBox Expected(EForceInit::ForceInit);
    for (int32 Corner = 0; Corner < 8; ++Corner)
    {
        const FVector3d Local(
            Collider.Center.X + ((Corner & 1) ? 0.5 : -0.5) * Collider.Shape.X,
            Collider.Center.Y + ((Corner & 2) ? 0.5 : -0.5) * Collider.Shape.Y,
            Collider.Center.Z + ((Corner & 4) ? 0.5 : -0.5) * Collider.Shape.Z);
        Expected += FVector(AtlasEft::FCoordinate::PositionToUnreal(TransformAtlasPoint(Collider.Affine, Local)));
    }
    return Expected;
}

FBox ExpectedSphereOrCapsuleWorldBounds(const AtlasEft::FColliderDesc& Collider)
{
    const FTransform ColliderTransform = MakeUnrealTransform(Collider);
    const FVector LocalCenter(AtlasEft::FCoordinate::VectorToUnreal(FVector3d(Collider.Center)) * 100.0);
    FQuat LocalShapeRotation = FQuat::Identity;
    FVector Extent(Collider.Shape.X * 100.0f);
    if (Collider.Kind == AtlasEft::EColliderKind::Capsule)
    {
        FVector Axis;
        switch (FMath::RoundToInt(Collider.Shape.Z))
        {
        case 0: Axis = FVector(0.0, -1.0, 0.0); break;
        case 1: Axis = FVector(0.0, 0.0, 1.0); break;
        default: Axis = FVector(1.0, 0.0, 0.0); break;
        }
        LocalShapeRotation = FQuat::FindBetweenNormals(FVector::UpVector, Axis);
        const double RadiusCm = Collider.Shape.X * 100.0;
        const double HalfHeightCm = FMath::Max(Collider.Shape.Y * 50.0, RadiusCm);
        Extent = FVector(RadiusCm, RadiusCm, HalfHeightCm);
    }
    FTransform ShapeTransform(ColliderTransform.GetRotation() * LocalShapeRotation,
        ColliderTransform.TransformPosition(LocalCenter), ColliderTransform.GetScale3D());
    FBox Expected(EForceInit::ForceInit);
    for (int32 Corner = 0; Corner < 8; ++Corner)
    {
        Expected += ShapeTransform.TransformPosition(FVector(
            (Corner & 1) ? Extent.X : -Extent.X,
            (Corner & 2) ? Extent.Y : -Extent.Y,
            (Corner & 4) ? Extent.Z : -Extent.Z));
    }
    return Expected;
}

uint32 GeometrySignatureCrc(const TMap<uint64, FString>& Signature)
{
    TArray<uint64> Ids;
    Signature.GetKeys(Ids);
    Ids.Sort();
    FString FullSignature;
    for (uint64 Id : Ids)
    {
        FullSignature += FString::Printf(TEXT("%llu:%s\n"), Id, *Signature.FindChecked(Id));
    }
    return FCrc::StrCrc32(*FullSignature);
}

bool SaveValidationReport(const FString& Status, const TSharedPtr<FJsonObject>& Json, const FString& Text, FString& OutError)
{
    OutError.Reset();
    const FString SavedDir = FPaths::ProjectSavedDir();
    IFileManager::Get().MakeDirectory(*SavedDir, true);
    const FString RootSuffix = FString::Printf(TEXT("Root%03d"), TargetRootId);
    const FString TextPath = SavedDir / FString::Printf(TEXT("AtlasCollider_%s_Validation.txt"), *RootSuffix);
    const FString JsonPath = SavedDir / FString::Printf(TEXT("AtlasCollider_%s_Validation.json"), *RootSuffix);
    Json->SetStringField(TEXT("status"), Status);
    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(Json.ToSharedRef(), Writer)
        || !FFileHelper::SaveStringToFile(Text, *TextPath)
        || !FFileHelper::SaveStringToFile(JsonText, *JsonPath))
    {
        OutError = FString::Printf(TEXT("Could not save validation reports in %s."), *SavedDir);
        return false;
    }
    return true;
}

TSharedPtr<FJsonObject> VectorJson(const FVector& Value)
{
    TSharedPtr<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("x"), Value.X);
    Json->SetNumberField(TEXT("y"), Value.Y);
    Json->SetNumberField(TEXT("z"), Value.Z);
    return Json;
}

TSharedPtr<FJsonObject> RotatorJson(const FRotator& Value)
{
    TSharedPtr<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("pitch"), Value.Pitch);
    Json->SetNumberField(TEXT("yaw"), Value.Yaw);
    Json->SetNumberField(TEXT("roll"), Value.Roll);
    return Json;
}

TSharedPtr<FJsonObject> MakeColliderPlanEntry(
    const AtlasEft::FColliderDesc& Collider,
    const FColliderSidecarRecord& Source,
    const FString& LayerName,
    const UPrimitiveComponent* Component)
{
    TSharedPtr<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("recordIndex"), static_cast<double>(Collider.Index));
    Json->SetNumberField(TEXT("layerId"), Source.Layer);
    Json->SetStringField(TEXT("layerName"), LayerName);
    Json->SetStringField(TEXT("sourceType"), Source.Type);
    Json->SetStringField(TEXT("kind"), ColliderKindName(Collider.Kind));
    Json->SetStringField(TEXT("componentClass"), Component->GetClass()->GetPathName());
    Json->SetObjectField(TEXT("relativeLocation"), VectorJson(Component->GetRelativeLocation()));
    Json->SetObjectField(TEXT("relativeRotation"), RotatorJson(Component->GetRelativeRotation()));
    Json->SetObjectField(TEXT("relativeScale3D"), VectorJson(Component->GetRelativeScale3D()));
    Json->SetStringField(TEXT("managedTag"), TEXT("AtlasManagedCollision"));
    Json->SetStringField(TEXT("recordTag"), FString::Printf(TEXT("AtlasColliderRecord=%llu"), Collider.Index));
    Json->SetStringField(TEXT("kindTag"), FString::Printf(TEXT("AtlasColliderKind=%s"), *Source.Type));
    if (const UStaticMeshComponent* MeshComponent = Cast<UStaticMeshComponent>(Component))
    {
        Json->SetNumberField(TEXT("meshId"), Collider.MeshId);
        Json->SetStringField(TEXT("meshAsset"), MeshComponent->GetStaticMesh()->GetPathName());
    }
    else if (const UBoxComponent* BoxComponent = Cast<UBoxComponent>(Component))
    {
        Json->SetObjectField(TEXT("boxExtentCm"), VectorJson(BoxComponent->GetUnscaledBoxExtent()));
    }
    else if (const USphereComponent* SphereComponent = Cast<USphereComponent>(Component))
    {
        Json->SetNumberField(TEXT("sphereRadiusCm"), SphereComponent->GetUnscaledSphereRadius());
    }
    else if (const UCapsuleComponent* CapsuleComponent = Cast<UCapsuleComponent>(Component))
    {
        Json->SetNumberField(TEXT("capsuleRadiusCm"), CapsuleComponent->GetUnscaledCapsuleRadius());
        Json->SetNumberField(TEXT("capsuleHalfHeightCm"), CapsuleComponent->GetUnscaledCapsuleHalfHeight());
    }
    return Json;
}

bool SaveColliderPlan(const TArray<TSharedPtr<FJsonValue>>& Entries, FString& OutError)
{
    TSharedPtr<FJsonObject> Plan = MakeShared<FJsonObject>();
    Plan->SetNumberField(TEXT("schemaVersion"), 1);
    Plan->SetStringField(TEXT("map"), TargetMap);
    Plan->SetNumberField(TEXT("rootId"), TargetRootId);
    Plan->SetNumberField(TEXT("componentCount"), Entries.Num());
    Plan->SetArrayField(TEXT("components"), Entries);
    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    const FString RootSuffix = FString::Printf(TEXT("Root%03d"), TargetRootId);
    const FString PlanPath = FPaths::ProjectSavedDir() / FString::Printf(TEXT("AtlasCollider_%s_Plan.json"), *RootSuffix);
    if (!FJsonSerializer::Serialize(Plan.ToSharedRef(), Writer) || !FFileHelper::SaveStringToFile(JsonText, *PlanPath))
    {
        OutError = FString::Printf(TEXT("Could not write deterministic collision placement plan %s."), *PlanPath);
        return false;
    }

    // MCP's editor file reader intentionally caps a single text response. Keep
    // deterministic chunks small enough to be consumed without truncation.
    constexpr int32 EntriesPerChunk = 50;
    const int32 ChunkCount = FMath::DivideAndRoundUp(Entries.Num(), EntriesPerChunk);
    for (int32 ChunkIndex = 0; ChunkIndex < ChunkCount; ++ChunkIndex)
    {
        TSharedPtr<FJsonObject> Chunk = MakeShared<FJsonObject>();
        Chunk->SetNumberField(TEXT("schemaVersion"), 1);
        Chunk->SetStringField(TEXT("map"), TargetMap);
        Chunk->SetNumberField(TEXT("rootId"), TargetRootId);
        Chunk->SetNumberField(TEXT("chunkIndex"), ChunkIndex);
        Chunk->SetNumberField(TEXT("chunkCount"), ChunkCount);
        TArray<TSharedPtr<FJsonValue>> ChunkEntries;
        const int32 First = ChunkIndex * EntriesPerChunk;
        const int32 Last = FMath::Min(First + EntriesPerChunk, Entries.Num());
        for (int32 Index = First; Index < Last; ++Index) ChunkEntries.Add(Entries[Index]);
        Chunk->SetArrayField(TEXT("components"), ChunkEntries);
        FString ChunkText;
        const TSharedRef<TJsonWriter<>> ChunkWriter = TJsonWriterFactory<>::Create(&ChunkText);
        const FString ChunkPath = FPaths::ProjectSavedDir() / FString::Printf(TEXT("AtlasCollider_%s_Plan_%02d.json"), *RootSuffix, ChunkIndex);
        if (!FJsonSerializer::Serialize(Chunk.ToSharedRef(), ChunkWriter) || !FFileHelper::SaveStringToFile(ChunkText, *ChunkPath))
        {
            OutError = FString::Printf(TEXT("Could not write deterministic collision plan chunk %d at %s."), ChunkIndex, *ChunkPath);
            return false;
        }
    }
    return true;
}
} // namespace

UAtlasEftAddSectorCollisionsCommandlet::UAtlasEftAddSectorCollisionsCommandlet()
{
    IsClient = false;
    IsEditor = true;
    IsServer = false;
    LogToConsole = true;
    ShowErrorCount = true;
}

int32 UAtlasEftAddSectorCollisionsCommandlet::Main(const FString& Params)
{
    const double StartSeconds = FPlatformTime::Seconds();
    FString PackDirectory;
    FString SourceJsonPath;
    FString MapFile;
    FString RequestedMapPackage;
    bool bHasExpectedGeometryCount = false;
    FParse::Value(*Params, TEXT("Pack="), PackDirectory);
    FParse::Value(*Params, TEXT("SourceJson="), SourceJsonPath);
    FParse::Value(*Params, TEXT("MapFile="), MapFile);
    const bool bHasRootId = FParse::Value(*Params, TEXT("RootId="), TargetRootId);
    bHasExpectedGeometryCount = FParse::Value(*Params, TEXT("ExpectedGeometryCount="), ExpectedGeometryCount);
    const bool bHasMapPackage = FParse::Value(*Params, TEXT("MapPackage="), RequestedMapPackage);
    PackDirectory.TrimQuotesInline();
    SourceJsonPath.TrimQuotesInline();
    MapFile.TrimQuotesInline();
    RequestedMapPackage.TrimQuotesInline();
    const bool bDryRun = FParse::Param(*Params, TEXT("DryRun"));
    const bool bAllowUnsupportedConvexExclusion = FParse::Param(*Params, TEXT("AllowUnsupportedConvexExclusion"));
    if (PackDirectory.IsEmpty() || SourceJsonPath.IsEmpty())
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Require -Pack=<directory> and -SourceJson=<colliders.json> arguments."));
        return 2;
    }
    if ((bHasRootId && TargetRootId != 3) && (!bHasExpectedGeometryCount || !bHasMapPackage))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Non-Root3 runs require explicit -RootId, -ExpectedGeometryCount, and -MapPackage; legacy Root3 defaults remain available."));
        return 2;
    }
    if (TargetRootId < 0 || (bHasExpectedGeometryCount && ExpectedGeometryCount < 1))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("RootId and ExpectedGeometryCount must be valid positive values."));
        return 2;
    }
    if (bAllowUnsupportedConvexExclusion && TargetRootId != 4)
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("-AllowUnsupportedConvexExclusion is scoped to Root4 only."));
        return 2;
    }
    if (bHasMapPackage)
    {
        TargetMap = RequestedMapPackage;
    }
    else if (TargetRootId != 3)
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Only the legacy Root3 run may omit -MapPackage."));
        return 2;
    }
    if (!FPackageName::IsValidLongPackageName(TargetMap))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Invalid target map package '%s'."), *TargetMap);
        return 2;
    }
    const FString ExpectedMapFile = FPackageName::LongPackageNameToFilename(TargetMap, FPackageName::GetMapPackageExtension());
    if (MapFile.IsEmpty())
    {
        MapFile = ExpectedMapFile;
    }
    MapFile = FPaths::ConvertRelativePathToFull(MapFile);
    if (!FPaths::FileExists(MapFile)
        || !FPaths::IsSamePath(MapFile, FPaths::ConvertRelativePathToFull(ExpectedMapFile)))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("MapFile must be the existing file for target package %s (%s), found '%s'."), *TargetMap, *ExpectedMapFile, *MapFile);
        return 2;
    }

    AtlasEft::FAuditReport Audit;
    Audit.PackDirectory = PackDirectory;
    AtlasEft::FManifest Manifest;
    if (!AtlasEft::FPackReader::ReadManifest(PackDirectory, Manifest, Audit)
        || !Manifest.Roots.IsValidIndex(TargetRootId))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Target root pack manifest preflight failed: %s"), *Audit.ToText());
        return 1;
    }
    const FString RootName = Manifest.Roots[TargetRootId];
    FString RootSuffix = RootName;
    RootSuffix.ReplaceInline(TEXT("SBG_Labyrinth_"), TEXT(""));
    RootSuffix.ReplaceInline(TEXT("_"), TEXT(""));
    const FString ExpectedMapPackage = FString::Printf(TEXT("/Game/Atlas/Sectors/Root_%03d/L_Atlas_Root%03d_%s"), TargetRootId, TargetRootId, *RootSuffix);
    if (TargetMap != ExpectedMapPackage)
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Target map %s does not match stable package derived from RootId %d/name %s: %s."),
            *TargetMap, TargetRootId, *RootName, *ExpectedMapPackage);
        return 2;
    }

    TArray<AtlasEft::FInstanceDesc> Instances;
    if (!AtlasEft::FPackReader::ReadInstances(PackDirectory, Manifest, Instances, Audit))
    {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Could not decode Root %d geometry source: %s"), TargetRootId, *Audit.ToText());
        return 1;
    }
    TMap<uint64, const AtlasEft::FInstanceDesc*> ExpectedGeometry;
    TSet<uint32> RootLevels;
    for (const AtlasEft::FInstanceDesc& Instance : Instances)
    {
        if (Instance.RootId != TargetRootId) continue;
        RootLevels.Add(Instance.Level);
        if (Instance.IsInactive() || Instance.LodIndex > 0) continue;
        if (ExpectedGeometry.Contains(Instance.Index))
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Duplicate Root %d geometry instance id %llu."), TargetRootId, Instance.Index);
            return 1;
        }
        ExpectedGeometry.Add(Instance.Index, &Instance);
    }
    if (ExpectedGeometry.Num() != ExpectedGeometryCount || RootLevels.IsEmpty())
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Root %d active LOD0 pack geometry count %d differs from the protected %d actor baseline."), TargetRootId, ExpectedGeometry.Num(), ExpectedGeometryCount);
        return 1;
    }

    TArray<AtlasEft::FColliderDesc> PackedColliders;
    if (!AtlasEft::FPackReader::ReadColliders(PackDirectory, Manifest, PackedColliders, Audit))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Could not decode collider source: %s"), *Audit.ToText());
        return 1;
    }
    TArray<FColliderSidecarRecord> SourceRows;
    TArray<int32> CandidateIndices;
    TMap<int32, FString> LayerNames;
    if (!ReadColliderSidecar(SourceJsonPath, Manifest, PackedColliders, TargetRootId, RootLevels, SourceRows, CandidateIndices, LayerNames, Audit))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Source sidecar does not match the pack collider records: %s"), *Audit.ToText());
        return 1;
    }

    TArray<int32> ExcludedUnsupportedConvexIndices;
    if (bAllowUnsupportedConvexExclusion)
    {
        TArray<int32> SupportedCandidateIndices;
        SupportedCandidateIndices.Reserve(CandidateIndices.Num());
        for (int32 Index : CandidateIndices)
        {
            const FColliderSidecarRecord& Source = SourceRows[Index];
            if (!Source.bConvex)
            {
                SupportedCandidateIndices.Add(Index);
                continue;
            }

            const AtlasEft::FColliderDesc& Collider = PackedColliders[Index];
            const FString* LayerName = LayerNames.Find(Source.Layer);
            if (Collider.Kind != AtlasEft::EColliderKind::Mesh
                || Source.RootName != TEXT("SBG_Labyrinth_Area_05")
                || Source.Level != 549
                || Source.MeshName != TEXT("collider__549_7_637.obj")
                || !LayerName || *LayerName != TEXT("LowPolyCollider"))
            {
                UE_LOG(LogAtlasEftAddSectorCollisions, Error,
                    TEXT("Root4 opt-in encountered an unexpected unsupported convex candidate at source row %d; refusing exclusion."), Index);
                return 1;
            }
            ExcludedUnsupportedConvexIndices.Add(Index);
        }
        CandidateIndices = MoveTemp(SupportedCandidateIndices);
        if (ExcludedUnsupportedConvexIndices.Num() != 7 || CandidateIndices.Num() != 537)
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error,
                TEXT("Root4 convex exclusion preflight mismatch: excluded=%d expected=7; selected=%d expected=537."),
                ExcludedUnsupportedConvexIndices.Num(), CandidateIndices.Num());
            return 1;
        }
    }

    int32 RootColliderCount = 0;
    int32 RootMeshCount = 0;
    int32 RootBoxCount = 0;
    int32 RootSphereCount = 0;
    int32 RootCapsuleCount = 0;
    int32 RootTriggerCount = 0;
    TMap<FString, int32> RootLayerCounts;
    for (int32 Index = 0; Index < SourceRows.Num(); ++Index)
    {
        const FColliderSidecarRecord& Source = SourceRows[Index];
        if (Source.RootName != Manifest.Roots[TargetRootId]) continue;
        ++RootColliderCount;
        const FString* LayerName = LayerNames.Find(Source.Layer);
        if (LayerName) RootLayerCounts.FindOrAdd(*LayerName)++;
        if (Source.Type == TEXT("mesh")) ++RootMeshCount;
        else if (Source.Type == TEXT("box")) ++RootBoxCount;
        else if (Source.Type == TEXT("sphere")) ++RootSphereCount;
        else if (Source.Type == TEXT("capsule")) ++RootCapsuleCount;
        if ((PackedColliders[Index].Flags & 1u) != 0) ++RootTriggerCount;
    }
    TSet<uint32> RequiredMeshIds;
    int32 CandidateMeshCount = 0;
    int32 CandidateSimpleCount = 0;
    TMap<int32, int32> CandidateLayerCounts;
    for (int32 Index : CandidateIndices)
    {
        const AtlasEft::FColliderDesc& Collider = PackedColliders[Index];
        const FColliderSidecarRecord& Source = SourceRows[Index];
        const AtlasEft::FAffineAnalysis Analysis = AtlasEft::FCoordinate::AnalyzeAffine(Collider.Affine);
        if (!Analysis.bFinite || Analysis.bSheared || Analysis.bDegenerate || Analysis.bMirrored)
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Collider %llu has an unsupported world affine (shear/degenerate/mirror)."), Collider.Index);
            return 1;
        }
        if (Collider.Kind == AtlasEft::EColliderKind::Mesh)
        {
            if (Source.bConvex)
            {
                UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Collider %llu requests convex mesh collision; only verified non-convex complex-as-simple data are supported."), Collider.Index);
                return 1;
            }
            ++CandidateMeshCount;
            RequiredMeshIds.Add(static_cast<uint32>(Collider.MeshId));
        }
        else
        {
            if (Collider.Kind == AtlasEft::EColliderKind::Box
                && (Collider.Shape.X <= 0.0f || Collider.Shape.Y <= 0.0f || Collider.Shape.Z <= 0.0f))
            {
                UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Selected box collider %llu has a zero or negative size."), Collider.Index);
                return 1;
            }
            if ((Collider.Kind == AtlasEft::EColliderKind::Sphere || Collider.Kind == AtlasEft::EColliderKind::Capsule)
                && (Collider.Shape.X <= 0.0f || (Collider.Kind == AtlasEft::EColliderKind::Capsule && Collider.Shape.Y <= 0.0f)))
            {
                UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Selected sphere/capsule collider %llu has a zero or negative radius/height."), Collider.Index);
                return 1;
            }
            ++CandidateSimpleCount;
        }
        CandidateLayerCounts.FindOrAdd(Source.Layer)++;
    }
    ExpectedCollisionCount = CandidateIndices.Num();
    const bool bRoot3AuditMismatch = TargetRootId == 3 && (SourceRows.Num() != 24661 || PackedColliders.Num() != 24661
        || RootColliderCount != ExpectedRootColliderRows || RootMeshCount != 3405 || RootBoxCount != 2
        || ExpectedCollisionCount != 1702 || CandidateMeshCount != ExpectedCollisionMeshCount
        || CandidateSimpleCount != ExpectedSimpleCollisionCount || RequiredMeshIds.Num() != 169 || RootTriggerCount != 0);
    if (ExpectedCollisionCount <= 0 || CandidateMeshCount + CandidateSimpleCount != ExpectedCollisionCount
        || RequiredMeshIds.Num() > CandidateMeshCount || bRoot3AuditMismatch)
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error,
            TEXT("Root %d source count/preflight mismatch: all=%d/%d root=%d mesh=%d box=%d sphere=%d capsule=%d trigger=%d candidates=%d mesh=%d simple=%d uniqueMeshes=%d."),
            TargetRootId, SourceRows.Num(), PackedColliders.Num(), RootColliderCount, RootMeshCount, RootBoxCount, RootSphereCount, RootCapsuleCount, RootTriggerCount,
            CandidateIndices.Num(), CandidateMeshCount, CandidateSimpleCount, RequiredMeshIds.Num());
        return 1;
    }

    UWorld* World = UEditorLoadingAndSavingUtils::LoadMap(MapFile);
    if (!World || !World->PersistentLevel || World->GetOutermost()->GetName() != TargetMap)
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Could not load the existing target map %s from %s."), *TargetMap, *MapFile);
        return 1;
    }
    TMap<uint64, FString> GeometryBefore;
    FString GeometryError;
    if (!CaptureGeometrySignature(World, ExpectedGeometry, GeometryBefore, GeometryError))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Existing map geometry baseline is unsafe: %s"), *GeometryError);
        return 1;
    }
    const uint32 GeometryBeforeCrc = GeometrySignatureCrc(GeometryBefore);

    int32 ReplacedManagedActorCount = 0;
    int32 ReplacedManagedComponentCount = 0;
    TArray<AActor*> ActorsToReplace;
    for (AActor* Actor : World->PersistentLevel->Actors)
    {
        if (!Actor) continue;
        const bool bManaged = Actor->Tags.Contains(FName(TEXT("AtlasManagedCollision")));
        const bool bCurrentRoot = Actor->Tags.Contains(FName(*FString::Printf(TEXT("AtlasRootId=%d"), TargetRootId)));
        if (bManaged && !Actor->Tags.Contains(FName(TEXT("AtlasColliderSchema=1"))))
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Found an unknown managed collision schema on actor %s; refusing replacement."), *Actor->GetPathName());
            return 1;
        }
        if (bCurrentRoot && !bManaged)
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Actor %s has a Root %d collision tag but is not marked managed; refusing replacement."), *Actor->GetPathName(), TargetRootId);
            return 1;
        }
        if (bManaged && bCurrentRoot)
        {
            if (Actor->IsA<AStaticMeshActor>())
            {
                UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Refusing to replace managed collision actor that is also a geometry StaticMeshActor: %s."), *Actor->GetPathName());
                return 1;
            }
            ActorsToReplace.Add(Actor);
            TArray<UPrimitiveComponent*> Components;
            Actor->GetComponents<UPrimitiveComponent>(Components);
            ReplacedManagedComponentCount += Components.Num();
        }
    }

    TArray<uint32> SortedMeshIds = RequiredMeshIds.Array();
    SortedMeshIds.Sort();
    TMap<uint32, AtlasEft::FDecodedColliderMesh> DecodedMeshes;
    if (!AtlasEft::FPackReader::ReadColliderMeshes(PackDirectory, Manifest, SortedMeshIds, DecodedMeshes, Audit))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Could not decode selected collider meshes: %s"), *Audit.ToText());
        return 1;
    }

    FString Validation = FString::Printf(
        TEXT("Map=%s\nRootId=%d RootName=%s\nSource=%s\nSourceColliderRows=%d RootColliderRows=%d\nRootByLayer="),
        *TargetMap, TargetRootId, *RootName, *Manifest.SourceFingerprint, SourceRows.Num(), RootColliderCount);
    for (const TPair<FString, int32>& Pair : RootLayerCounts)
    {
        Validation += FString::Printf(TEXT("%s:%d,"), *Pair.Key, Pair.Value);
    }
    Validation += FString::Printf(
        TEXT("\nSelection=blocker-layers-with-nontrigger records=%d mesh=%d simple=%d uniqueMeshIds=%d layers=%d\n"),
        CandidateIndices.Num(), CandidateMeshCount, CandidateSimpleCount, RequiredMeshIds.Num(), CandidateLayerCounts.Num());
    Validation += FString::Printf(
        TEXT("UnsupportedConvexExclusion opt-in=%s excluded=%d reason=unsupported convex mesh; deferred collision debt.\n"),
        bAllowUnsupportedConvexExclusion ? TEXT("true") : TEXT("false"), ExcludedUnsupportedConvexIndices.Num());

    TSharedPtr<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("map"), TargetMap);
    Json->SetStringField(TEXT("sourceFingerprint"), Manifest.SourceFingerprint);
    Json->SetNumberField(TEXT("sourceColliderRows"), SourceRows.Num());
    Json->SetNumberField(TEXT("rootId"), TargetRootId);
    Json->SetStringField(TEXT("rootName"), RootName);
    Json->SetNumberField(TEXT("rootColliderRows"), RootColliderCount);
    Json->SetNumberField(TEXT("rootMeshRows"), RootMeshCount);
    Json->SetNumberField(TEXT("rootBoxRows"), RootBoxCount);
    Json->SetNumberField(TEXT("rootSphereRows"), RootSphereCount);
    Json->SetNumberField(TEXT("rootCapsuleRows"), RootCapsuleCount);
    Json->SetNumberField(TEXT("rootTriggerRows"), RootTriggerCount);
    Json->SetNumberField(TEXT("selectedColliders"), CandidateIndices.Num());
    Json->SetBoolField(TEXT("allowUnsupportedConvexExclusion"), bAllowUnsupportedConvexExclusion);
    Json->SetNumberField(TEXT("excludedUnsupportedConvexCount"), ExcludedUnsupportedConvexIndices.Num());
    Json->SetNumberField(TEXT("selectedMeshColliders"), CandidateMeshCount);
    Json->SetNumberField(TEXT("selectedSimpleColliders"), CandidateSimpleCount);
    Json->SetNumberField(TEXT("uniqueColliderMeshes"), RequiredMeshIds.Num());
    Json->SetNumberField(TEXT("collisionLayerActors"), CandidateLayerCounts.Num());
    Json->SetNumberField(TEXT("geometryActorsBefore"), GeometryBefore.Num());
    Json->SetNumberField(TEXT("geometrySignatureCrc32Before"), GeometryBeforeCrc);
    Json->SetNumberField(TEXT("replacedManagedActors"), ActorsToReplace.Num());
    Json->SetNumberField(TEXT("replacedManagedComponents"), ReplacedManagedComponentCount);
    TArray<TSharedPtr<FJsonValue>> RootLayerValues;
    for (const TPair<FString, int32>& Pair : RootLayerCounts)
    {
        TSharedPtr<FJsonObject> Value = MakeShared<FJsonObject>();
        Value->SetStringField(TEXT("name"), Pair.Key);
        Value->SetNumberField(TEXT("count"), Pair.Value);
        RootLayerValues.Add(MakeShared<FJsonValueObject>(Value.ToSharedRef()));
    }
    Json->SetArrayField(TEXT("rootLayerCounts"), RootLayerValues);

    TArray<TSharedPtr<FJsonValue>> ExcludedConvexValues;
    for (int32 Index : ExcludedUnsupportedConvexIndices)
    {
        const AtlasEft::FColliderDesc& Collider = PackedColliders[Index];
        const FColliderSidecarRecord& Source = SourceRows[Index];
        const FString* LayerName = LayerNames.Find(Source.Layer);
        TSharedPtr<FJsonObject> Value = MakeShared<FJsonObject>();
        Value->SetNumberField(TEXT("sourceRowIndex"), Index);
        Value->SetNumberField(TEXT("colliderIndex"), static_cast<double>(Collider.Index));
        Value->SetNumberField(TEXT("packedMeshId"), Collider.MeshId);
        Value->SetStringField(TEXT("type"), Source.Type);
        Value->SetStringField(TEXT("mesh"), Source.MeshName);
        Value->SetStringField(TEXT("root"), Source.RootName);
        Value->SetNumberField(TEXT("level"), Source.Level);
        Value->SetNumberField(TEXT("layerId"), Source.Layer);
        Value->SetStringField(TEXT("layer"), LayerName ? *LayerName : FString(TEXT("Unknown")));
        Value->SetNumberField(TEXT("flags"), Collider.Flags);
        Value->SetNumberField(TEXT("atlasTranslationXSourceUnits"), Collider.Affine[3]);
        Value->SetNumberField(TEXT("atlasTranslationYSourceUnits"), Collider.Affine[7]);
        Value->SetNumberField(TEXT("atlasTranslationZSourceUnits"), Collider.Affine[11]);
        Value->SetStringField(TEXT("reason"), TEXT("unsupported convex mesh; excluded only by explicit Root4 opt-in; manual collision review required"));
        ExcludedConvexValues.Add(MakeShared<FJsonValueObject>(Value.ToSharedRef()));
    }
    Json->SetArrayField(TEXT("excludedUnsupportedConvex"), ExcludedConvexValues);

    if (bDryRun)
    {
        Json->SetNumberField(TEXT("geometryActorsAfter"), GeometryBefore.Num());
        Json->SetNumberField(TEXT("geometrySignatureCrc32After"), GeometryBeforeCrc);
        Json->SetNumberField(TEXT("collisionComponentsAfter"), 0);
        Json->SetNumberField(TEXT("elapsedSeconds"), FPlatformTime::Seconds() - StartSeconds);
        Validation += FString::Printf(TEXT("DRYRUN PASS geometryActors=%d geometrySignatureCrc32=%u; no actors, assets, or map packages changed.\n"), GeometryBefore.Num(), GeometryBeforeCrc);
        FString SaveError;
        if (!SaveValidationReport(TEXT("DRYRUN_PASS"), Json, Validation, SaveError))
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("%s"), *SaveError);
            return 1;
        }
        UE_LOG(LogAtlasEftAddSectorCollisions, Display, TEXT("%s"), *Validation);
        return 0;
    }

    for (AActor* Actor : ActorsToReplace)
    {
        if (!World->DestroyActor(Actor, true, true))
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Could not remove previously managed collision actor %s."), *Actor->GetPathName());
            return 1;
        }
    }

    const FString CollisionMeshDestination = FString::Printf(TEXT("/Game/Atlas/Sectors/Root_%03d/CollisionMeshes"), TargetRootId);
    const FString CollisionMeshDirectory = FPackageName::LongPackageNameToFilename(CollisionMeshDestination);
    IFileManager::Get().MakeDirectory(*CollisionMeshDirectory, true);
    TMap<uint32, FColliderMeshAsset> MeshAssets;
    for (uint32 MeshId : SortedMeshIds)
    {
        const AtlasEft::FDecodedColliderMesh* Decoded = DecodedMeshes.Find(MeshId);
        if (!Decoded)
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Decoded collider mesh %u is missing."), MeshId);
            return 1;
        }
        FColliderMeshAsset& Asset = MeshAssets.Add(MeshId);
        FString Error;
        if (!BuildColliderMeshAsset(*Decoded, CollisionMeshDestination, Asset, Error))
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Collider mesh %u asset build failed: %s"), MeshId, *Error);
            return 1;
        }
    }

    TMap<int32, AActor*> LayerActors;
    TMap<int32, int32> LayerComponentCounts;
    TSet<uint64> SpawnedRecordIds;
    TArray<TSharedPtr<FJsonValue>> ColliderPlan;
    ColliderPlan.Reserve(ExpectedCollisionCount);
    FBox CollisionBounds(EForceInit::ForceInit);
    double MaxBoundsErrorCm = 0.0;
    uint64 MaxBoundsErrorRecord = 0;
    for (int32 Index : CandidateIndices)
    {
        const AtlasEft::FColliderDesc& Collider = PackedColliders[Index];
        const FColliderSidecarRecord& Source = SourceRows[Index];
        if (SpawnedRecordIds.Contains(Collider.Index))
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Duplicate candidate collider record id %llu."), Collider.Index);
            return 1;
        }
        SpawnedRecordIds.Add(Collider.Index);
        const FString* LayerNamePtr = LayerNames.Find(Source.Layer);
        if (!LayerNamePtr)
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Collider %llu has an unmapped collision layer %d."), Collider.Index, Source.Layer);
            return 1;
        }
        AActor** LayerActorPtr = LayerActors.Find(Source.Layer);
        if (!LayerActorPtr)
        {
            AActor* NewLayerActor = nullptr;
            FString Error;
            if (!CreateCollisionLayerActor(World, TargetRootId, Source.Layer, *LayerNamePtr, NewLayerActor, Error))
            {
                UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("%s"), *Error);
                return 1;
            }
            LayerActors.Add(Source.Layer, NewLayerActor);
            LayerActorPtr = LayerActors.Find(Source.Layer);
        }

        const FColliderMeshAsset* MeshAsset = Collider.Kind == AtlasEft::EColliderKind::Mesh ? MeshAssets.Find(Collider.MeshId) : nullptr;
        UPrimitiveComponent* Component = nullptr;
        FString Error;
        if (!SpawnColliderComponent(*LayerActorPtr, Collider, Source, *LayerNamePtr, MeshAsset, Component, Error))
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Collider %llu: %s"), Collider.Index, *Error);
            return 1;
        }
        if (!Component->IsRegistered() || Component->GetMobility() != EComponentMobility::Static
            || Component->GetCollisionEnabled() != ECollisionEnabled::QueryAndPhysics
            || Component->GetCollisionResponseToChannel(ECC_Pawn) != ECR_Block
            || Component->GetCollisionResponseToChannel(ECC_WorldStatic) != ECR_Block
            || Component->GetGenerateOverlapEvents() || Component->IsVisible())
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Collider %llu component settings failed the static/hidden/Pawn+WorldStatic blocking checks."), Collider.Index);
            return 1;
        }

        FBox ExpectedBounds(EForceInit::ForceInit);
        if (Collider.Kind == AtlasEft::EColliderKind::Mesh)
        {
            UStaticMeshComponent* MeshComponent = Cast<UStaticMeshComponent>(Component);
            const FColliderMeshAsset* Asset = MeshAssets.Find(Collider.MeshId);
            if (!MeshComponent || !Asset || MeshComponent->GetStaticMesh() != Asset->Mesh
                || !Asset->Mesh->GetBodySetup() || Asset->Mesh->GetBodySetup()->CollisionTraceFlag != CTF_UseComplexAsSimple)
            {
                UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Collider %llu failed mesh asset/complex-as-simple checks."), Collider.Index);
                return 1;
            }
            ExpectedBounds = ExpectedMeshWorldBounds(Collider, Asset->LocalBounds);
        }
        else if (Collider.Kind == AtlasEft::EColliderKind::Box)
        {
            if (!Cast<UBoxComponent>(Component))
            {
                UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Collider %llu expected a box component."), Collider.Index);
                return 1;
            }
            ExpectedBounds = ExpectedBoxWorldBounds(Collider);
        }
        else if (Collider.Kind == AtlasEft::EColliderKind::Sphere)
        {
            USphereComponent* Sphere = Cast<USphereComponent>(Component);
            if (!Sphere || !FMath::IsNearlyEqual(Sphere->GetUnscaledSphereRadius(), Collider.Shape.X * 100.0f, 0.01f))
            {
                UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Collider %llu sphere component does not preserve the source radius."), Collider.Index);
                return 1;
            }
            ExpectedBounds = ExpectedSphereOrCapsuleWorldBounds(Collider);
        }
        else if (Collider.Kind == AtlasEft::EColliderKind::Capsule)
        {
            UCapsuleComponent* Capsule = Cast<UCapsuleComponent>(Component);
            const float ExpectedRadiusCm = Collider.Shape.X * 100.0f;
            const float ExpectedHalfHeightCm = FMath::Max(Collider.Shape.Y * 50.0f, ExpectedRadiusCm);
            if (!Capsule || !FMath::IsNearlyEqual(Capsule->GetUnscaledCapsuleRadius(), ExpectedRadiusCm, 0.01f)
                || !FMath::IsNearlyEqual(Capsule->GetUnscaledCapsuleHalfHeight(), ExpectedHalfHeightCm, 0.01f))
            {
                UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Collider %llu capsule component does not preserve the source radius/height."), Collider.Index);
                return 1;
            }
            ExpectedBounds = ExpectedSphereOrCapsuleWorldBounds(Collider);
        }
        else
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Unexpected Root %d primitive type %s in audited collision candidates."), TargetRootId, *Source.Type);
            return 1;
        }
        const FBox ActualBounds = Component->CalcBounds(Component->GetComponentTransform()).GetBox();
        const double ErrorCm = BoxError(ExpectedBounds, ActualBounds);
        if (ErrorCm > MaxBoundsErrorCm)
        {
            MaxBoundsErrorCm = ErrorCm;
            MaxBoundsErrorRecord = Collider.Index;
        }
        if (ErrorCm > BoundsToleranceCm)
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error,
                TEXT("Collider %llu world bounds differ by %.6f cm (limit %.3f cm): expected %s..%s actual %s..%s."),
                Collider.Index, ErrorCm, BoundsToleranceCm, *ExpectedBounds.Min.ToString(), *ExpectedBounds.Max.ToString(),
                *ActualBounds.Min.ToString(), *ActualBounds.Max.ToString());
            return 1;
        }
        ColliderPlan.Add(MakeShared<FJsonValueObject>(MakeColliderPlanEntry(Collider, Source, *LayerNamePtr, Component)));
        CollisionBounds += ActualBounds;
        LayerComponentCounts.FindOrAdd(Source.Layer)++;
    }

    for (TPair<int32, AActor*>& Pair : LayerActors)
    {
        const int32 Count = LayerComponentCounts.FindRef(Pair.Key);
        Pair.Value->Tags.Add(FName(*FString::Printf(TEXT("AtlasColliderCount=%d"), Count)));
        TArray<UPrimitiveComponent*> Components;
        Pair.Value->GetComponents<UPrimitiveComponent>(Components);
        int32 ManagedCount = 0;
        for (UPrimitiveComponent* Component : Components)
        {
            if (Component && Component->ComponentTags.Contains(FName(TEXT("AtlasManagedCollision")))) ++ManagedCount;
        }
        if (ManagedCount != Count)
        {
            UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Layer actor %s has %d managed components, expected %d."), *Pair.Value->GetPathName(), ManagedCount, Count);
            return 1;
        }
    }

    TMap<uint64, FString> GeometryAfter;
    if (!CaptureGeometrySignature(World, ExpectedGeometry, GeometryAfter, GeometryError))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Geometry changed while adding collisions: %s"), *GeometryError);
        return 1;
    }
    const uint32 GeometryAfterCrc = GeometrySignatureCrc(GeometryAfter);
    if (GeometryAfter.Num() != GeometryBefore.Num() || GeometryAfterCrc != GeometryBeforeCrc)
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Geometry signature changed: before=%d/%u after=%d/%u."),
            GeometryBefore.Num(), GeometryBeforeCrc, GeometryAfter.Num(), GeometryAfterCrc);
        return 1;
    }
    if (SpawnedRecordIds.Num() != ExpectedCollisionCount || MeshAssets.Num() != RequiredMeshIds.Num()
        || LayerActors.Num() != CandidateLayerCounts.Num() || CollisionBounds.IsValid == false)
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Final collision count/bounds validation failed: records=%d/%d meshes=%d/%d layers=%d/%d."),
            SpawnedRecordIds.Num(), ExpectedCollisionCount, MeshAssets.Num(), RequiredMeshIds.Num(), LayerActors.Num(), CandidateLayerCounts.Num());
        return 1;
    }

    World->UpdateWorldComponents(true, false);
    World->GetOutermost()->MarkPackageDirty();
    if (!UEditorLoadingAndSavingUtils::SaveMap(World, TargetMap))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Could not save existing map %s."), *TargetMap);
        return 1;
    }
    FString PlanError;
    if (ColliderPlan.Num() != ExpectedCollisionCount || !SaveColliderPlan(ColliderPlan, PlanError))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("%s"), PlanError.IsEmpty() ? TEXT("Collision placement plan count validation failed.") : *PlanError);
        return 1;
    }

    Json->SetNumberField(TEXT("geometryActorsAfter"), GeometryAfter.Num());
    Json->SetNumberField(TEXT("geometrySignatureCrc32After"), GeometryAfterCrc);
    Json->SetNumberField(TEXT("replacedManagedActors"), ActorsToReplace.Num());
    Json->SetNumberField(TEXT("replacedManagedComponents"), ReplacedManagedComponentCount);
    Json->SetNumberField(TEXT("collisionComponentsAfter"), SpawnedRecordIds.Num());
    Json->SetNumberField(TEXT("collisionMeshAssets"), MeshAssets.Num());
    Json->SetNumberField(TEXT("collisionLayerActorsAfter"), LayerActors.Num());
    Json->SetNumberField(TEXT("boundsErrorToleranceCm"), BoundsToleranceCm);
    Json->SetNumberField(TEXT("maxBoundsErrorCm"), MaxBoundsErrorCm);
    Json->SetNumberField(TEXT("maxBoundsErrorRecord"), static_cast<double>(MaxBoundsErrorRecord));
    Json->SetStringField(TEXT("boundsMinCm"), CollisionBounds.Min.ToString());
    Json->SetStringField(TEXT("boundsMaxCm"), CollisionBounds.Max.ToString());
    Json->SetNumberField(TEXT("elapsedSeconds"), FPlatformTime::Seconds() - StartSeconds);
    Validation += FString::Printf(
        TEXT("PASS collisionComponents=%d meshComponents=%d boxComponents=%d meshAssets=%d layerActors=%d replacedActors=%d replacedComponents=%d\nGeometryActors=%d/%d signatureCrc32=%u/%u\nBoundsMin=%s BoundsMax=%s maxBoundsErrorCm=%.6f record=%llu toleranceCm=%.3f\nMapSaved=%s elapsedSeconds=%.3f\n"),
        SpawnedRecordIds.Num(), CandidateMeshCount, CandidateSimpleCount, MeshAssets.Num(), LayerActors.Num(),
        ActorsToReplace.Num(), ReplacedManagedComponentCount, GeometryAfter.Num(), ExpectedGeometryCount,
        GeometryBeforeCrc, GeometryAfterCrc, *CollisionBounds.Min.ToString(), *CollisionBounds.Max.ToString(),
        MaxBoundsErrorCm, MaxBoundsErrorRecord, BoundsToleranceCm, *TargetMap, FPlatformTime::Seconds() - StartSeconds);
    FString SaveError;
    if (!SaveValidationReport(TEXT("PASS"), Json, Validation, SaveError))
    {
        UE_LOG(LogAtlasEftAddSectorCollisions, Error, TEXT("Map saved, but validation report write failed: %s"), *SaveError);
        return 1;
    }
    UE_LOG(LogAtlasEftAddSectorCollisions, Display, TEXT("%s"), *Validation);
    return 0;
}
