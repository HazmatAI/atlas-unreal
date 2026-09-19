#include "AtlasEftCoordinate.h"
#include "AtlasEftPackReader.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
void WriteU32LE(TArray<uint8>& Bytes, int32 Offset, uint32 Value)
{
    Bytes[Offset] = static_cast<uint8>(Value);
    Bytes[Offset + 1] = static_cast<uint8>(Value >> 8);
    Bytes[Offset + 2] = static_cast<uint8>(Value >> 16);
    Bytes[Offset + 3] = static_cast<uint8>(Value >> 24);
}

void WriteF32LE(TArray<uint8>& Bytes, int32 Offset, float Value)
{
    uint32 Bits = 0;
    FMemory::Memcpy(&Bits, &Value, sizeof(Value));
    WriteU32LE(Bytes, Offset, Bits);
}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FAtlasEftCoordinateTest,
    "Atlas.Eft.Runtime.CoordinateConversion",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAtlasEftCoordinateTest::RunTest(const FString& Parameters)
{
    using namespace AtlasEft;

    const FVector3d Converted = FCoordinate::PositionToUnreal(FVector3d(1.0, 2.0, 3.0));
    TestEqual(TEXT("X = Atlas Z in cm"), Converted.X, 300.0);
    TestEqual(TEXT("Y = -Atlas X in cm"), Converted.Y, -100.0);
    TestEqual(TEXT("Z = Atlas Y in cm"), Converted.Z, 200.0);

    const double AtlasIdentity[12] = {
        1.0, 0.0, 0.0, 1.0,
        0.0, 1.0, 0.0, 2.0,
        0.0, 0.0, 1.0, 3.0
    };
    double UnrealIdentity[12] = {};
    FCoordinate::AffineToUnreal(AtlasIdentity, UnrealIdentity);
    TestEqual(TEXT("Identity L00"), UnrealIdentity[0], 1.0);
    TestEqual(TEXT("Identity L11"), UnrealIdentity[5], 1.0);
    TestEqual(TEXT("Identity L22"), UnrealIdentity[10], 1.0);
    TestEqual(TEXT("Translated X"), UnrealIdentity[3], 300.0);
    TestEqual(TEXT("Translated Y"), UnrealIdentity[7], -100.0);
    TestEqual(TEXT("Translated Z"), UnrealIdentity[11], 200.0);

    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FAtlasEftAffineAnalysisTest,
    "Atlas.Eft.Runtime.AffineAnalysis",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAtlasEftAffineAnalysisTest::RunTest(const FString& Parameters)
{
    using namespace AtlasEft;

    const float Identity[12] = {
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0
    };
    const FAffineAnalysis IdentityResult = FCoordinate::AnalyzeAffine(Identity);
    TestFalse(TEXT("Identity is not sheared"), IdentityResult.bSheared);
    TestFalse(TEXT("Identity is not mirrored"), IdentityResult.bMirrored);

    const float Shear[12] = {
        1, 0.25f, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0
    };
    TestTrue(TEXT("Shear is detected"), FCoordinate::AnalyzeAffine(Shear).bSheared);

    const float Mirror[12] = {
        -1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0
    };
    TestTrue(TEXT("Negative determinant is detected"), FCoordinate::AnalyzeAffine(Mirror).bMirrored);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FAtlasEftMeshDecodeTest,
    "Atlas.Eft.Runtime.MeshDecode",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAtlasEftMeshDecodeTest::RunTest(const FString& Parameters)
{
    using namespace AtlasEft;

    constexpr int32 Stride = 36;
    TArray<uint8> Bytes;
    Bytes.SetNumZeroed(Stride * 3 + 3 * sizeof(uint32));
    const FVector3f Positions[3] = {
        FVector3f(1.0f, 2.0f, 3.0f),
        FVector3f(4.0f, 5.0f, 6.0f),
        FVector3f(7.0f, 8.0f, 9.0f)
    };
    for (int32 VertexIndex = 0; VertexIndex < 3; ++VertexIndex)
    {
        const int32 Base = VertexIndex * Stride;
        WriteF32LE(Bytes, Base, Positions[VertexIndex].X);
        WriteF32LE(Bytes, Base + 4, Positions[VertexIndex].Y);
        WriteF32LE(Bytes, Base + 8, Positions[VertexIndex].Z);
        WriteF32LE(Bytes, Base + 12, 0.0f);
        WriteF32LE(Bytes, Base + 16, 1.0f);
        WriteF32LE(Bytes, Base + 20, 0.0f);
        WriteF32LE(Bytes, Base + 24, VertexIndex == 1 ? 1.0f : 0.0f);
        WriteF32LE(Bytes, Base + 28, VertexIndex == 2 ? 1.0f : 0.0f);
        Bytes[Base + 32] = static_cast<uint8>(10 + VertexIndex);
        Bytes[Base + 33] = 20;
        Bytes[Base + 34] = 30;
        Bytes[Base + 35] = 255;
    }
    WriteU32LE(Bytes, Stride * 3, 0);
    WriteU32LE(Bytes, Stride * 3 + 4, 1);
    WriteU32LE(Bytes, Stride * 3 + 8, 2);

    const FString TempDirectory = FPaths::Combine(
        FPlatformProcess::UserTempDir(),
        FString::Printf(TEXT("AtlasEftMeshDecode-%s"), *FGuid::NewGuid().ToString(EGuidFormats::Digits)));
    IFileManager::Get().MakeDirectory(*TempDirectory, true);
    const FString MeshesPath = FPaths::Combine(TempDirectory, TEXT("meshes.bin"));
    TestTrue(TEXT("Synthetic meshes.bin was written"), FFileHelper::SaveArrayToFile(Bytes, *MeshesPath));

    FManifest Manifest;
    Manifest.VertexLayout.Stride = Stride;
    Manifest.VertexLayout.Fields = {
        {TEXT("position"), TEXT("f32x3"), 0},
        {TEXT("normal"), TEXT("f32x3"), 12},
        {TEXT("uv"), TEXT("f32x2"), 24},
        {TEXT("color"), TEXT("unorm8x4"), 32}
    };
    FMeshDesc Desc;
    Desc.Id = 0;
    Desc.Name = TEXT("SyntheticTriangle");
    Desc.VertexCount = 3;
    Desc.IndexOffset = Stride * 3;
    Desc.IndexCount = 3;
    Desc.Submeshes.Add({7, 0, 3});
    Manifest.Meshes.Add(Desc);

    FDecodedMesh Mesh;
    FAuditReport Report;
    const bool bDecoded = FPackReader::ReadMesh(TempDirectory, Manifest, 0, Mesh, Report);
    TestTrue(TEXT("Synthetic mesh decoded"), bDecoded);
    TestEqual(TEXT("Vertex count"), Mesh.Vertices.Num(), 3);
    TestEqual(TEXT("Index count"), Mesh.Indices.Num(), 3);
    if (Mesh.Vertices.Num() == 3 && Mesh.Indices.Num() == 3)
    {
        TestEqual(TEXT("Position is preserved in Atlas space"), Mesh.Vertices[0].Position, Positions[0]);
        TestEqual(TEXT("Normal is decoded"), Mesh.Vertices[1].Normal, FVector3f(0.0f, 1.0f, 0.0f));
        TestEqual(TEXT("UV is decoded"), Mesh.Vertices[2].UV, FVector2f(0.0f, 1.0f));
        TestEqual(TEXT("Color is decoded"), Mesh.Vertices[2].Color, FColor(12, 20, 30, 255));
        TestEqual(TEXT("Triangle indices are unchanged"), Mesh.Indices[2], static_cast<uint32>(2));
    }

    IFileManager::Get().DeleteDirectory(*TempDirectory, false, true);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FAtlasEftMaterialDecodeTest,
    "Atlas.Eft.Runtime.MaterialDecode",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAtlasEftMaterialDecodeTest::RunTest(const FString& Parameters)
{
    using namespace AtlasEft;

    const FString TempDirectory = FPaths::Combine(
        FPlatformProcess::UserTempDir(),
        FString::Printf(TEXT("AtlasEftMaterialDecode-%s"), *FGuid::NewGuid().ToString(EGuidFormats::Digits)));
    IFileManager::Get().MakeDirectory(*TempDirectory, true);
    const FString MaterialsPath = FPaths::Combine(TempDirectory, TEXT("materials.json"));
    const FString Json = TEXT(
        "[{\"id\":0,\"role\":\"cutout\",\"albedo\":\"tex/a.png\",\"normal\":\"tex/n.png\","
        "\"uvXform\":[1,1,0,0],\"alphaMode\":\"MASK\",\"alphaCutoff\":0.42,"
        "\"tint\":[0.5,0.6,0.7,1],\"metallic\":0.1,\"roughness\":0.8,\"normalScale\":0.9,"
        "\"normalGreenFlip\":true,\"doubleSided\":true,\"roughnessFromAlbedoAlpha\":false,"
        "\"specMap\":null,\"emissive\":{\"texture\":\"tex/e.png\"},"
        "\"detail\":{\"normal\":\"tex/dn.png\"},\"vp\":null,\"parallax\":null}]");
    TestTrue(TEXT("Synthetic materials.json was written"), FFileHelper::SaveStringToFile(Json, *MaterialsPath));

    FManifest Manifest;
    Manifest.MaterialCount = 1;
    TArray<FMaterialDesc> Materials;
    FAuditReport Report;
    const bool bDecoded = FPackReader::ReadMaterials(TempDirectory, Manifest, Materials, Report);
    TestTrue(TEXT("Synthetic material decoded"), bDecoded);
    TestEqual(TEXT("Material count"), Materials.Num(), 1);
    if (Materials.Num() == 1)
    {
        const FMaterialDesc& Material = Materials[0];
        TestEqual(TEXT("Role"), Material.Role, FString(TEXT("cutout")));
        TestEqual(TEXT("Alpha mode"), static_cast<uint8>(Material.AlphaMode), static_cast<uint8>(EMaterialAlphaMode::Mask));
        TestEqual(TEXT("Alpha cutoff"), Material.AlphaCutoff, 0.42f);
        TestEqual(TEXT("Texture reference count"), Material.Textures.Num(), 4);
        if (Material.Textures.Num() == 4)
        {
            TestEqual(TEXT("Albedo semantic"), static_cast<uint8>(Material.Textures[0].Semantic), static_cast<uint8>(ETextureSemantic::Albedo));
            TestEqual(TEXT("Detail normal semantic"), static_cast<uint8>(Material.Textures[3].Semantic), static_cast<uint8>(ETextureSemantic::DetailNormal));
        }
    }

    IFileManager::Get().DeleteDirectory(*TempDirectory, false, true);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FAtlasEftInstanceDecodeTest,
    "Atlas.Eft.Runtime.InstanceDecode",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAtlasEftInstanceDecodeTest::RunTest(const FString& Parameters)
{
    using namespace AtlasEft;

    constexpr int32 Stride = 80;
    TArray<uint8> Bytes;
    Bytes.SetNumZeroed(Stride);
    const float Affine[12] = {
        1, 0, 0, 1,
        0, 1, 0, 2,
        0, 0, 1, 3
    };
    for (int32 Index = 0; Index < 12; ++Index)
    {
        WriteF32LE(Bytes, Index * 4, Affine[Index]);
    }
    WriteU32LE(Bytes, 48, 7);
    WriteU32LE(Bytes, 52, 11);
    WriteU32LE(Bytes, 56, 0);
    WriteU32LE(Bytes, 60, 2);
    WriteU32LE(Bytes, 64, 0);
    WriteU32LE(Bytes, 68, 123);
    WriteU32LE(Bytes, 72, 456);
    WriteU32LE(Bytes, 76, 550);

    const FString TempDirectory = FPaths::Combine(
        FPlatformProcess::UserTempDir(),
        FString::Printf(TEXT("AtlasEftInstanceDecode-%s"), *FGuid::NewGuid().ToString(EGuidFormats::Digits)));
    IFileManager::Get().MakeDirectory(*TempDirectory, true);
    TestTrue(TEXT("Synthetic instances.bin was written"), FFileHelper::SaveArrayToFile(Bytes, *FPaths::Combine(TempDirectory, TEXT("instances.bin"))));

    FManifest Manifest;
    Manifest.InstanceCount = 1;
    Manifest.Meshes.SetNum(8);
    Manifest.InstanceLayout.Stride = Stride;
    Manifest.InstanceLayout.Fields = {
        {TEXT("affine"), TEXT("f32x12"), 0},
        {TEXT("meshId"), TEXT("u32"), 48},
        {TEXT("lodGroup"), TEXT("i32"), 52},
        {TEXT("lodIndex"), TEXT("i32"), 56},
        {TEXT("rootId"), TEXT("u32"), 60},
        {TEXT("flags"), TEXT("u32"), 64},
        {TEXT("par"), TEXT("u32"), 68},
        {TEXT("par2"), TEXT("u32"), 72},
        {TEXT("lv"), TEXT("u32"), 76}
    };

    TArray<FInstanceDesc> Instances;
    FAuditReport Report;
    const bool bDecoded = FPackReader::ReadInstances(TempDirectory, Manifest, Instances, Report);
    TestTrue(TEXT("Synthetic instance decoded"), bDecoded);
    TestEqual(TEXT("Instance count"), Instances.Num(), 1);
    if (Instances.Num() == 1)
    {
        TestEqual(TEXT("Mesh id"), Instances[0].MeshId, static_cast<uint32>(7));
        TestEqual(TEXT("LOD group"), Instances[0].LodGroup, 11);
        TestEqual(TEXT("Level"), Instances[0].Level, static_cast<uint32>(550));
        TestEqual(TEXT("Atlas translation"), Instances[0].GetAtlasTranslation(), FVector3f(1, 2, 3));
    }

    IFileManager::Get().DeleteDirectory(*TempDirectory, false, true);
    return true;
}

#endif
