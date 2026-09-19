#include "AtlasEftStaticMeshImporter.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AtlasEftCoordinate.h"
#include "AtlasEftPackReader.h"
#include "Engine/StaticMesh.h"
#include "Materials/Material.h"
#include "MeshDescription.h"
#include "MeshDescriptionBuilder.h"
#include "Misc/PackageName.h"
#include "ObjectTools.h"
#include "StaticMeshAttributes.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

namespace AtlasEft
{
namespace
{
FVector4f ToLinearColor(const FColor& Color)
{
    constexpr float Scale = 1.0f / 255.0f;
    return FVector4f(Color.R * Scale, Color.G * Scale, Color.B * Scale, Color.A * Scale);
}

FString MakeAssetName(uint32 MeshId, const FString& SourceName)
{
    FString SafeName = ObjectTools::SanitizeObjectName(SourceName);
    if (SafeName.IsEmpty())
    {
        SafeName = TEXT("Mesh");
    }
    SafeName = SafeName.Left(80);
    return FString::Printf(TEXT("SM_Atlas_%04u_%s"), MeshId, *SafeName);
}
}

bool FStaticMeshImporter::ImportMesh(
    const FString& PackDirectory,
    uint32 MeshId,
    const FString& DestinationPath,
    FStaticMeshImportResult& OutResult,
    FString& OutError)
{
    FAuditReport Report;
    Report.PackDirectory = PackDirectory;
    FManifest Manifest;
    if (!FPackReader::ReadManifest(PackDirectory, Manifest, Report))
    {
        OutError = Report.ToText();
        return false;
    }

    return ImportMesh(PackDirectory, Manifest, MeshId, DestinationPath, OutResult, OutError);
}

bool FStaticMeshImporter::ImportMesh(
    const FString& PackDirectory,
    const FManifest& Manifest,
    uint32 MeshId,
    const FString& DestinationPath,
    FStaticMeshImportResult& OutResult,
    FString& OutError)
{
    OutResult = FStaticMeshImportResult();
    OutError.Reset();

    FString NormalizedDestination = DestinationPath;
    NormalizedDestination.RemoveFromEnd(TEXT("/"));
    if (!FPackageName::IsValidLongPackageName(NormalizedDestination))
    {
        OutError = FString::Printf(TEXT("Invalid Unreal destination path: %s"), *DestinationPath);
        return false;
    }

    FAuditReport Report;
    Report.PackDirectory = PackDirectory;
    FDecodedMesh Decoded;
    if (!FPackReader::ReadMesh(PackDirectory, Manifest, MeshId, Decoded, Report))
    {
        OutError = Report.ToText();
        return false;
    }

    const FString AssetName = MakeAssetName(MeshId, Decoded.Name);
    const FString PackageName = NormalizedDestination + TEXT("/") + AssetName;
    UPackage* Package = CreatePackage(*PackageName);
    if (!Package)
    {
        OutError = FString::Printf(TEXT("Could not create package %s."), *PackageName);
        return false;
    }
    Package->FullyLoad();

    UStaticMesh* StaticMesh = FindObject<UStaticMesh>(Package, *AssetName);
    const bool bNewAsset = StaticMesh == nullptr;
    if (bNewAsset)
    {
        StaticMesh = NewObject<UStaticMesh>(Package, *AssetName, RF_Public | RF_Standalone);
    }
    if (!StaticMesh)
    {
        OutError = FString::Printf(TEXT("Could not create static mesh %s."), *AssetName);
        return false;
    }

    StaticMesh->PreEditChange(nullptr);
    StaticMesh->SetNumSourceModels(0);
    StaticMesh->GetStaticMaterials().Reset();

    FStaticMeshSourceModel& SourceModel = StaticMesh->AddSourceModel();
    SourceModel.BuildSettings.bRecomputeNormals = false;
    SourceModel.BuildSettings.bRecomputeTangents = true;
    SourceModel.BuildSettings.bUseMikkTSpace = true;
    SourceModel.BuildSettings.bRemoveDegenerates = false;
    SourceModel.BuildSettings.bGenerateLightmapUVs = false;
    SourceModel.BuildSettings.bUseFullPrecisionUVs = false;

    FMeshDescription MeshDescription;
    FStaticMeshAttributes Attributes(MeshDescription);
    Attributes.Register();

    FMeshDescriptionBuilder Builder;
    Builder.SetMeshDescription(&MeshDescription);
    Builder.SetNumUVLayers(1);
    Builder.ReserveNewVertices(Decoded.Vertices.Num());

    TArray<FVertexID> VertexIds;
    VertexIds.Reserve(Decoded.Vertices.Num());
    for (const FDecodedVertex& Vertex : Decoded.Vertices)
    {
        const FVector3d Converted = FCoordinate::PositionToUnreal(FVector3d(Vertex.Position));
        VertexIds.Add(Builder.AppendVertex(FVector(Converted)));
    }

    TArray<FPolygonGroupID> PolygonGroups;
    PolygonGroups.Reserve(Decoded.Submeshes.Num());
    UMaterialInterface* DefaultMaterial = UMaterial::GetDefaultMaterial(MD_Surface);
    for (int32 SubmeshIndex = 0; SubmeshIndex < Decoded.Submeshes.Num(); ++SubmeshIndex)
    {
        const FSubmeshDesc& Submesh = Decoded.Submeshes[SubmeshIndex];
        const FName SlotName(*FString::Printf(TEXT("AtlasMaterial_%u"), Submesh.MaterialId));
        PolygonGroups.Add(Builder.AppendPolygonGroup(SlotName));
        StaticMesh->GetStaticMaterials().Add(FStaticMaterial(DefaultMaterial, SlotName, SlotName));
    }

    for (int32 SubmeshIndex = 0; SubmeshIndex < Decoded.Submeshes.Num(); ++SubmeshIndex)
    {
        const FSubmeshDesc& Submesh = Decoded.Submeshes[SubmeshIndex];
        for (uint32 LocalIndex = 0; LocalIndex < Submesh.IndexCount; LocalIndex += 3)
        {
            const uint32 AtlasIndices[3] = {
                Decoded.Indices[Submesh.IndexStart + LocalIndex],
                Decoded.Indices[Submesh.IndexStart + LocalIndex + 2],
                Decoded.Indices[Submesh.IndexStart + LocalIndex + 1]
            };
            FVertexInstanceID Instances[3];
            for (int32 Corner = 0; Corner < 3; ++Corner)
            {
                const uint32 VertexIndex = AtlasIndices[Corner];
                const FDecodedVertex& Vertex = Decoded.Vertices[VertexIndex];
                Instances[Corner] = Builder.AppendInstance(VertexIds[VertexIndex]);
                const FVector3d ConvertedNormal = FCoordinate::VectorToUnreal(FVector3d(Vertex.Normal)).GetSafeNormal();
                Builder.SetInstanceNormal(Instances[Corner], FVector(ConvertedNormal));
                Builder.SetInstanceUV(Instances[Corner], FVector2D(Vertex.UV), 0);
                Builder.SetInstanceColor(Instances[Corner], ToLinearColor(Vertex.Color));
            }
            Builder.AppendTriangle(Instances[0], Instances[1], Instances[2], PolygonGroups[SubmeshIndex]);
        }
    }

    StaticMesh->CreateMeshDescription(0, MoveTemp(MeshDescription));
    StaticMesh->CommitMeshDescription(0);
    StaticMesh->SetImportVersion(EImportStaticMeshVersion::LastVersion);
    StaticMesh->SetLightMapCoordinateIndex(0);
    StaticMesh->SetLightMapResolution(64);

    TArray<FText> BuildErrors;
    StaticMesh->Build(true, &BuildErrors);
    if (!BuildErrors.IsEmpty())
    {
        TArray<FString> Messages;
        for (const FText& Error : BuildErrors)
        {
            Messages.Add(Error.ToString());
        }
        OutError = FString::Join(Messages, TEXT("\n"));
        return false;
    }
    const FStaticMeshRenderData* RenderData = StaticMesh->GetRenderData();
    if (!RenderData || RenderData->LODResources.IsEmpty())
    {
        OutError = TEXT("Static mesh build produced no render LOD.");
        return false;
    }
    const FStaticMeshLODResources& RenderLod = RenderData->LODResources[0];
    const uint32 RenderVertexCount = RenderLod.GetNumVertices();
    const uint32 RenderTriangleCount = RenderLod.IndexBuffer.GetNumIndices() / 3;
    const uint32 RenderSectionCount = RenderLod.Sections.Num();
    uint32 DegenerateSourceTriangleCount = 0;
    for (int32 Index = 0; Index < Decoded.Indices.Num(); Index += 3)
    {
        const uint32 I0 = Decoded.Indices[Index];
        const uint32 I1 = Decoded.Indices[Index + 1];
        const uint32 I2 = Decoded.Indices[Index + 2];
        const FVector3d P0(Decoded.Vertices[I0].Position);
        const FVector3d P1(Decoded.Vertices[I1].Position);
        const FVector3d P2(Decoded.Vertices[I2].Position);
        const double TwiceAreaSquared = FVector3d::CrossProduct(P1 - P0, P2 - P0).SizeSquared();
        DegenerateSourceTriangleCount += (I0 == I1 || I0 == I2 || I1 == I2 || TwiceAreaSquared <= 1.0e-20) ? 1u : 0u;
    }
    const uint32 SourceTriangleCount = static_cast<uint32>(Decoded.Indices.Num() / 3);
    const uint32 RemovedTriangleCount = SourceTriangleCount - FMath::Min(SourceTriangleCount, RenderTriangleCount);
    if (RenderTriangleCount > SourceTriangleCount
        || RemovedTriangleCount > DegenerateSourceTriangleCount
        || RenderSectionCount != static_cast<uint32>(Decoded.Submeshes.Num()))
    {
        OutError = FString::Printf(
            TEXT("Render mesh count mismatch: triangles %u/%u (source degenerates %u), sections %u/%d."),
            RenderTriangleCount,
            SourceTriangleCount,
            DegenerateSourceTriangleCount,
            RenderSectionCount,
            Decoded.Submeshes.Num());
        return false;
    }
    StaticMesh->PostEditChange();
    StaticMesh->MarkPackageDirty();
    if (bNewAsset)
    {
        FAssetRegistryModule::AssetCreated(StaticMesh);
    }

    const FString PackageFilename = FPackageName::LongPackageNameToFilename(
        PackageName,
        FPackageName::GetAssetPackageExtension());
    FSavePackageArgs SaveArgs;
    SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
    SaveArgs.SaveFlags = SAVE_NoError;
    SaveArgs.bSlowTask = false;
    if (!UPackage::SavePackage(Package, StaticMesh, *PackageFilename, SaveArgs))
    {
        OutError = FString::Printf(TEXT("Failed saving package %s."), *PackageFilename);
        return false;
    }

    OutResult.StaticMesh = StaticMesh;
    OutResult.ObjectPath = StaticMesh->GetPathName();
    OutResult.PackageFilename = PackageFilename;
    OutResult.SourceVertexCount = Decoded.Vertices.Num();
    OutResult.RenderVertexCount = RenderVertexCount;
    OutResult.RenderTriangleCount = RenderTriangleCount;
    OutResult.RenderSectionCount = RenderSectionCount;
    OutResult.DegenerateSourceTriangleCount = DegenerateSourceTriangleCount;
    return true;
}
} // namespace AtlasEft
