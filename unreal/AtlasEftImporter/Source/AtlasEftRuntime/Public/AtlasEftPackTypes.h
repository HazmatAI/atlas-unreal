#pragma once

#include "CoreMinimal.h"

namespace AtlasEft
{
enum class EIssueSeverity : uint8
{
    Info,
    Warning,
    Error
};

struct ATLASEFTRUNTIME_API FIssue
{
    EIssueSeverity Severity = EIssueSeverity::Info;
    FString Context;
    FString Message;
};

struct ATLASEFTRUNTIME_API FFieldDesc
{
    FString Name;
    FString Format;
    uint32 Offset = 0;
};

struct ATLASEFTRUNTIME_API FLayoutDesc
{
    uint32 Stride = 0;
    TArray<FFieldDesc> Fields;

    const FFieldDesc* Find(const FString& Name) const;
};

struct ATLASEFTRUNTIME_API FSubmeshDesc
{
    uint32 MaterialId = 0;
    uint32 IndexStart = 0;
    uint32 IndexCount = 0;
};

struct ATLASEFTRUNTIME_API FMeshDesc
{
    uint32 Id = 0;
    FString Name;
    uint64 VertexOffset = 0;
    uint32 VertexCount = 0;
    uint64 IndexOffset = 0;
    uint32 IndexCount = 0;
    TArray<FSubmeshDesc> Submeshes;
};

/** One raw mesh-local Atlas vertex. Unit conversion and handedness conversion happen at the consumer boundary. */
struct ATLASEFTRUNTIME_API FDecodedVertex
{
    FVector3f Position = FVector3f::ZeroVector;
    FVector3f Normal = FVector3f::UpVector;
    FVector2f UV = FVector2f::ZeroVector;
    FColor Color = FColor::White;
};

/** Fully decoded render mesh. Indices remain in Atlas winding order and are local to Vertices. */
struct ATLASEFTRUNTIME_API FDecodedMesh
{
    uint32 Id = 0;
    FString Name;
    TArray<FDecodedVertex> Vertices;
    TArray<uint32> Indices;
    TArray<FSubmeshDesc> Submeshes;
};

/** One row from instances.bin. Affine remains Atlas row-major 3x4 until consumer conversion. */
struct ATLASEFTRUNTIME_API FInstanceDesc
{
    uint64 Index = 0;
    float Affine[12] = {};
    uint32 MeshId = 0;
    int32 LodGroup = -1;
    int32 LodIndex = -1;
    uint32 RootId = 0;
    uint32 Flags = 0;
    uint32 ParentId = 0;
    uint32 GrandparentId = 0;
    uint32 Level = 0;

    bool IsInactive() const { return (Flags & 0x8u) != 0; }
    FVector3f GetAtlasTranslation() const { return FVector3f(Affine[3], Affine[7], Affine[11]); }
};

enum class EMaterialAlphaMode : uint8
{
    Opaque,
    Mask,
    Blend
};

enum class ETextureSemantic : uint8
{
    Albedo,
    Normal,
    Specular,
    Emissive,
    DetailAlbedo,
    DetailNormal,
    VertexPaintAlbedo,
    VertexPaintNormal,
    VertexPaintHeights,
    Parallax
};

struct ATLASEFTRUNTIME_API FTextureReference
{
    FString Path;
    ETextureSemantic Semantic = ETextureSemantic::Albedo;
};

/** Typed subset of materials.json used by the UE master-material pipeline. */
struct ATLASEFTRUNTIME_API FMaterialDesc
{
    uint32 Id = 0;
    FString Role;
    EMaterialAlphaMode AlphaMode = EMaterialAlphaMode::Opaque;
    float AlphaCutoff = 0.0f;
    FVector4f Tint = FVector4f(1.0f, 1.0f, 1.0f, 1.0f);
    FVector4f UVTransform = FVector4f(1.0f, 1.0f, 0.0f, 0.0f);
    float Metallic = 0.0f;
    float Roughness = 0.9f;
    float NormalScale = 1.0f;
    bool bNormalGreenFlip = true;
    bool bDoubleSided = true;
    bool bRoughnessFromAlbedoAlpha = false;
    TArray<FTextureReference> Textures;
};

struct ATLASEFTRUNTIME_API FColliderMeshDesc
{
    uint32 Id = 0;
    FString Name;
    uint64 VertexOffset = 0;
    uint32 VertexCount = 0;
    uint64 IndexOffset = 0;
    uint32 IndexCount = 0;
};

struct ATLASEFTRUNTIME_API FManifest
{
    uint32 Version = 0;
    FString Dataset;
    FString Map;
    FString SourceFingerprint;
    bool bSelfContained = false;
    double Bounds[6] = {};
    FLayoutDesc VertexLayout;
    FLayoutDesc InstanceLayout;
    TArray<FMeshDesc> Meshes;
    uint64 InstanceCount = 0;
    uint32 MaterialCount = 0;
    TArray<FString> Roots;
    uint32 LodGroupCount = 0;
    TOptional<FLayoutDesc> ColliderLayout;
    uint64 ColliderCount = 0;
    TArray<FColliderMeshDesc> ColliderMeshes;
};

struct ATLASEFTRUNTIME_API FAuditStats
{
    uint64 Meshes = 0;
    uint64 Vertices = 0;
    uint64 Indices = 0;
    uint64 Triangles = 0;
    uint64 Submeshes = 0;
    uint64 Instances = 0;
    uint64 Materials = 0;
    uint64 TextureReferences = 0;
    uint64 MissingTextures = 0;
    uint64 Colliders = 0;
    uint64 ColliderMeshes = 0;
    uint64 Lights = 0;

    uint64 ShearedInstances = 0;
    uint64 DegenerateInstances = 0;
    uint64 MirroredInstances = 0;
    uint64 BakedWorldInstances = 0;
    uint64 TerrainInstances = 0;
    uint64 InactiveInstances = 0;
    uint64 NonFiniteInstances = 0;
    uint64 MirrorFlagMismatches = 0;
    uint64 InvalidMeshIds = 0;
    uint64 InvalidRootIds = 0;
    double MaxNormalizedColumnDot = 0.0;

    TMap<FString, uint64> MaterialRoles;
};

struct ATLASEFTRUNTIME_API FAuditReport
{
    FString PackDirectory;
    FManifest Manifest;
    FAuditStats Stats;
    TArray<FIssue> Issues;
    bool bSuccess = false;

    void Add(EIssueSeverity Severity, const FString& Context, const FString& Message);
    void Error(const FString& Context, const FString& Message);
    void Warning(const FString& Context, const FString& Message);
    void Info(const FString& Context, const FString& Message);
    bool HasErrors() const;
    FString ToText() const;
};
} // namespace AtlasEft
