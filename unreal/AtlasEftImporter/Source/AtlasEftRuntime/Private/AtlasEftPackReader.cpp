#include "AtlasEftPackReader.h"

#include "AtlasEftCoordinate.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace AtlasEft
{
namespace
{
bool ReadJsonObject(const FString& Path, TSharedPtr<FJsonObject>& Out, FAuditReport& Report)
{
    FString Text;
    if (!FFileHelper::LoadFileToString(Text, *Path))
    {
        Report.Error(Path, TEXT("Cannot read JSON file."));
        return false;
    }
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
    if (!FJsonSerializer::Deserialize(Reader, Out) || !Out.IsValid())
    {
        Report.Error(Path, TEXT("Invalid JSON object."));
        return false;
    }
    return true;
}

bool ReadJsonArray(const FString& Path, TArray<TSharedPtr<FJsonValue>>& Out, FAuditReport& Report)
{
    FString Text;
    if (!FFileHelper::LoadFileToString(Text, *Path))
    {
        Report.Error(Path, TEXT("Cannot read JSON file."));
        return false;
    }
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
    if (!FJsonSerializer::Deserialize(Reader, Out))
    {
        Report.Error(Path, TEXT("Invalid JSON array."));
        return false;
    }
    return true;
}

TSharedPtr<FJsonObject> RequiredObject(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Name,
    const FString& Context,
    FAuditReport& Report)
{
    if (!Parent.IsValid() || !Parent->HasTypedField<EJson::Object>(Name))
    {
        Report.Error(Context, FString::Printf(TEXT("Missing object field '%s'."), Name));
        return nullptr;
    }
    return Parent->GetObjectField(Name);
}

const TArray<TSharedPtr<FJsonValue>>* RequiredArray(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Name,
    const FString& Context,
    FAuditReport& Report)
{
    if (!Parent.IsValid() || !Parent->HasTypedField<EJson::Array>(Name))
    {
        Report.Error(Context, FString::Printf(TEXT("Missing array field '%s'."), Name));
        return nullptr;
    }
    return &Parent->GetArrayField(Name);
}

bool NumberToUInt64(const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, uint64& Out)
{
    if (!Object.IsValid() || !Object->HasTypedField<EJson::Number>(Name))
    {
        return false;
    }
    const double Value = Object->GetNumberField(Name);
    if (!FMath::IsFinite(Value) || Value < 0.0 || Value > static_cast<double>(MAX_uint64) || FMath::FloorToDouble(Value) != Value)
    {
        return false;
    }
    Out = static_cast<uint64>(Value);
    return true;
}

bool NumberToUInt32(const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, uint32& Out)
{
    uint64 Temp = 0;
    if (!NumberToUInt64(Object, Name, Temp) || Temp > MAX_uint32)
    {
        return false;
    }
    Out = static_cast<uint32>(Temp);
    return true;
}

bool ParseLayout(
    const TSharedPtr<FJsonObject>& Object,
    const TCHAR* FieldsName,
    FLayoutDesc& Out,
    const FString& Context,
    FAuditReport& Report)
{
    if (!NumberToUInt32(Object, TEXT("stride"), Out.Stride) || Out.Stride == 0)
    {
        Report.Error(Context, TEXT("Invalid or missing stride."));
        return false;
    }
    const TArray<TSharedPtr<FJsonValue>>* Values = RequiredArray(Object, FieldsName, Context, Report);
    if (!Values)
    {
        return false;
    }
    for (const TSharedPtr<FJsonValue>& Value : *Values)
    {
        const TSharedPtr<FJsonObject> FieldObject = Value.IsValid() ? Value->AsObject() : nullptr;
        FFieldDesc Field;
        if (!FieldObject.IsValid()
            || !FieldObject->TryGetStringField(TEXT("name"), Field.Name)
            || !FieldObject->TryGetStringField(TEXT("fmt"), Field.Format)
            || !NumberToUInt32(FieldObject, TEXT("offset"), Field.Offset))
        {
            Report.Error(Context, TEXT("Malformed layout field."));
            return false;
        }
        Out.Fields.Add(MoveTemp(Field));
    }
    return true;
}

uint32 FormatSize(const FString& Format)
{
    if (Format == TEXT("f32x12")) return 48;
    if (Format == TEXT("f32x4")) return 16;
    if (Format == TEXT("f32x3")) return 12;
    if (Format == TEXT("f32x2")) return 8;
    if (Format == TEXT("f32") || Format == TEXT("u32") || Format == TEXT("i32")) return 4;
    if (Format == TEXT("unorm8x4")) return 4;
    return 0;
}

bool ValidateField(const FLayoutDesc& Layout, const TCHAR* Name, const TCHAR* Format, const FString& Context, FAuditReport& Report)
{
    const FFieldDesc* Field = Layout.Find(Name);
    if (!Field)
    {
        Report.Error(Context, FString::Printf(TEXT("Missing required field '%s'."), Name));
        return false;
    }
    if (Field->Format != Format)
    {
        Report.Error(Context, FString::Printf(TEXT("Field '%s' has format '%s', expected '%s'."), Name, *Field->Format, Format));
        return false;
    }
    const uint32 Size = FormatSize(Field->Format);
    if (Size == 0 || static_cast<uint64>(Field->Offset) + Size > Layout.Stride)
    {
        Report.Error(Context, FString::Printf(TEXT("Field '%s' overruns stride %u."), Name, Layout.Stride));
        return false;
    }
    return true;
}

bool CheckedEnd(uint64 Offset, uint64 Count, uint64 Stride, uint64& OutEnd)
{
    if (Stride != 0 && Count > (MAX_uint64 - Offset) / Stride)
    {
        return false;
    }
    OutEnd = Offset + Count * Stride;
    return true;
}

uint32 ReadU32LE(const uint8* Data)
{
    return static_cast<uint32>(Data[0])
        | (static_cast<uint32>(Data[1]) << 8)
        | (static_cast<uint32>(Data[2]) << 16)
        | (static_cast<uint32>(Data[3]) << 24);
}

int32 ReadI32LE(const uint8* Data)
{
    return static_cast<int32>(ReadU32LE(Data));
}

float ReadF32LE(const uint8* Data)
{
    const uint32 Bits = ReadU32LE(Data);
    float Value = 0.0f;
    FMemory::Memcpy(&Value, &Bits, sizeof(Value));
    return Value;
}

void AddTexturePath(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, TSet<FString>& Paths)
{
    FString Value;
    if (Object.IsValid() && Object->TryGetStringField(Field, Value) && !Value.IsEmpty())
    {
        Paths.Add(Value);
    }
}

void CollectMaterialTextures(const TSharedPtr<FJsonObject>& Material, TSet<FString>& Paths)
{
    AddTexturePath(Material, TEXT("albedo"), Paths);
    AddTexturePath(Material, TEXT("normal"), Paths);
    AddTexturePath(Material, TEXT("specMap"), Paths);

    for (const TCHAR* BlockName : {TEXT("emissive"), TEXT("parallax")})
    {
        if (Material->HasTypedField<EJson::Object>(BlockName))
        {
            AddTexturePath(Material->GetObjectField(BlockName), BlockName == FString(TEXT("emissive")) ? TEXT("texture") : TEXT("map"), Paths);
        }
    }

    if (Material->HasTypedField<EJson::Object>(TEXT("detail")))
    {
        const TSharedPtr<FJsonObject> Detail = Material->GetObjectField(TEXT("detail"));
        AddTexturePath(Detail, TEXT("albedo"), Paths);
        AddTexturePath(Detail, TEXT("normal"), Paths);
    }

    if (Material->HasTypedField<EJson::Object>(TEXT("vp")))
    {
        const TSharedPtr<FJsonObject> Vp = Material->GetObjectField(TEXT("vp"));
        AddTexturePath(Vp, TEXT("heights"), Paths);
        if (Vp->HasTypedField<EJson::Array>(TEXT("layers")))
        {
            for (const TSharedPtr<FJsonValue>& LayerValue : Vp->GetArrayField(TEXT("layers")))
            {
                const TSharedPtr<FJsonObject> Layer = LayerValue.IsValid() ? LayerValue->AsObject() : nullptr;
                AddTexturePath(Layer, TEXT("albedo"), Paths);
                AddTexturePath(Layer, TEXT("normal"), Paths);
            }
        }
    }
}

bool ReadFloatField(const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, float& Out)
{
    double Value = 0.0;
    if (!Object.IsValid() || !Object->TryGetNumberField(Name, Value) || !FMath::IsFinite(Value))
    {
        return false;
    }
    Out = static_cast<float>(Value);
    return FMath::IsFinite(Out);
}

bool ReadFloat4Field(const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, FVector4f& Out)
{
    if (!Object.IsValid() || !Object->HasTypedField<EJson::Array>(Name))
    {
        return false;
    }
    const TArray<TSharedPtr<FJsonValue>>& Values = Object->GetArrayField(Name);
    if (Values.Num() != 4)
    {
        return false;
    }
    float Components[4] = {};
    for (int32 Index = 0; Index < 4; ++Index)
    {
        const double Value = Values[Index]->AsNumber();
        if (!FMath::IsFinite(Value))
        {
            return false;
        }
        Components[Index] = static_cast<float>(Value);
    }
    Out = FVector4f(Components[0], Components[1], Components[2], Components[3]);
    return true;
}

void AddTypedTexture(
    const TSharedPtr<FJsonObject>& Object,
    const TCHAR* Field,
    ETextureSemantic Semantic,
    TArray<FTextureReference>& Out)
{
    FString Path;
    if (Object.IsValid() && Object->TryGetStringField(Field, Path) && !Path.IsEmpty())
    {
        FTextureReference& Reference = Out.AddDefaulted_GetRef();
        Reference.Path = MoveTemp(Path);
        Reference.Semantic = Semantic;
    }
}

void CollectTypedTextures(const TSharedPtr<FJsonObject>& Material, TArray<FTextureReference>& Out)
{
    AddTypedTexture(Material, TEXT("albedo"), ETextureSemantic::Albedo, Out);
    AddTypedTexture(Material, TEXT("normal"), ETextureSemantic::Normal, Out);
    AddTypedTexture(Material, TEXT("specMap"), ETextureSemantic::Specular, Out);

    if (Material->HasTypedField<EJson::Object>(TEXT("emissive")))
    {
        AddTypedTexture(Material->GetObjectField(TEXT("emissive")), TEXT("texture"), ETextureSemantic::Emissive, Out);
    }
    if (Material->HasTypedField<EJson::Object>(TEXT("detail")))
    {
        const TSharedPtr<FJsonObject> Detail = Material->GetObjectField(TEXT("detail"));
        AddTypedTexture(Detail, TEXT("albedo"), ETextureSemantic::DetailAlbedo, Out);
        AddTypedTexture(Detail, TEXT("normal"), ETextureSemantic::DetailNormal, Out);
    }
    if (Material->HasTypedField<EJson::Object>(TEXT("parallax")))
    {
        AddTypedTexture(Material->GetObjectField(TEXT("parallax")), TEXT("map"), ETextureSemantic::Parallax, Out);
    }
    if (Material->HasTypedField<EJson::Object>(TEXT("vp")))
    {
        const TSharedPtr<FJsonObject> Vp = Material->GetObjectField(TEXT("vp"));
        AddTypedTexture(Vp, TEXT("heights"), ETextureSemantic::VertexPaintHeights, Out);
        if (Vp->HasTypedField<EJson::Array>(TEXT("layers")))
        {
            for (const TSharedPtr<FJsonValue>& LayerValue : Vp->GetArrayField(TEXT("layers")))
            {
                const TSharedPtr<FJsonObject> Layer = LayerValue.IsValid() ? LayerValue->AsObject() : nullptr;
                AddTypedTexture(Layer, TEXT("albedo"), ETextureSemantic::VertexPaintAlbedo, Out);
                AddTypedTexture(Layer, TEXT("normal"), ETextureSemantic::VertexPaintNormal, Out);
            }
        }
    }
}

bool AuditInstances(const FString& Path, FAuditReport& Report)
{
    const FLayoutDesc& Layout = Report.Manifest.InstanceLayout;
    bool bLayoutValid = true;
    bLayoutValid &= ValidateField(Layout, TEXT("affine"), TEXT("f32x12"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("meshId"), TEXT("u32"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("lodIndex"), TEXT("i32"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("rootId"), TEXT("u32"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("flags"), TEXT("u32"), TEXT("manifest.instance"), Report);
    if (!bLayoutValid)
    {
        return false;
    }

    IPlatformFile& PlatformFile = FPlatformFileManager::Get().GetPlatformFile();
    TUniquePtr<IFileHandle> Handle(PlatformFile.OpenRead(*Path));
    if (!Handle)
    {
        Report.Error(Path, TEXT("Cannot open instances.bin."));
        return false;
    }
    const int64 FileSize = Handle->Size();
    if (FileSize < 0 || FileSize % Layout.Stride != 0)
    {
        Report.Error(Path, FString::Printf(TEXT("File size %lld is not a multiple of stride %u."), FileSize, Layout.Stride));
        return false;
    }
    const uint64 Count = static_cast<uint64>(FileSize / Layout.Stride);
    if (Count != Report.Manifest.InstanceCount)
    {
        Report.Error(Path, FString::Printf(TEXT("Record count %llu differs from manifest instanceCount %llu."), Count, Report.Manifest.InstanceCount));
    }

    const uint32 AffineOffset = Layout.Find(TEXT("affine"))->Offset;
    const uint32 MeshOffset = Layout.Find(TEXT("meshId"))->Offset;
    const uint32 RootOffset = Layout.Find(TEXT("rootId"))->Offset;
    const uint32 FlagsOffset = Layout.Find(TEXT("flags"))->Offset;
    constexpr uint64 ChunkRecords = 4096;
    TArray<uint8> Buffer;

    for (uint64 BaseRecord = 0; BaseRecord < Count; BaseRecord += ChunkRecords)
    {
        const uint64 Records = FMath::Min(ChunkRecords, Count - BaseRecord);
        const uint64 ByteCount64 = Records * Layout.Stride;
        if (ByteCount64 > static_cast<uint64>(MAX_int32))
        {
            Report.Error(Path, TEXT("Instance audit chunk exceeds UE array capacity."));
            return false;
        }
        Buffer.SetNumUninitialized(static_cast<int32>(ByteCount64));
        if (!Handle->Seek(static_cast<int64>(BaseRecord * Layout.Stride)) || !Handle->Read(Buffer.GetData(), ByteCount64))
        {
            Report.Error(Path, FString::Printf(TEXT("Failed reading record %llu."), BaseRecord));
            return false;
        }

        for (uint64 LocalRecord = 0; LocalRecord < Records; ++LocalRecord)
        {
            const uint8* Record = Buffer.GetData() + LocalRecord * Layout.Stride;
            float Affine[12];
            for (uint32 Index = 0; Index < 12; ++Index)
            {
                Affine[Index] = ReadF32LE(Record + AffineOffset + Index * 4);
            }
            const FAffineAnalysis Analysis = FCoordinate::AnalyzeAffine(Affine);
            Report.Stats.MaxNormalizedColumnDot = FMath::Max(Report.Stats.MaxNormalizedColumnDot, Analysis.MaxNormalizedColumnDot);
            Report.Stats.ShearedInstances += Analysis.bSheared ? 1 : 0;
            Report.Stats.DegenerateInstances += Analysis.bDegenerate ? 1 : 0;
            Report.Stats.MirroredInstances += Analysis.bMirrored ? 1 : 0;
            Report.Stats.NonFiniteInstances += Analysis.bFinite ? 0 : 1;

            const uint32 Flags = ReadU32LE(Record + FlagsOffset);
            const bool bMirrorFlag = (Flags & 0x1u) != 0;
            Report.Stats.MirrorFlagMismatches += bMirrorFlag != Analysis.bMirrored ? 1 : 0;
            Report.Stats.TerrainInstances += (Flags & 0x2u) != 0 ? 1 : 0;
            Report.Stats.BakedWorldInstances += (Flags & 0x4u) != 0 ? 1 : 0;
            Report.Stats.InactiveInstances += (Flags & 0x8u) != 0 ? 1 : 0;

            const uint32 MeshId = ReadU32LE(Record + MeshOffset);
            Report.Stats.InvalidMeshIds += MeshId >= static_cast<uint32>(Report.Manifest.Meshes.Num()) ? 1 : 0;
            const uint32 RootId = ReadU32LE(Record + RootOffset);
            Report.Stats.InvalidRootIds += !Report.Manifest.Roots.IsEmpty() && RootId >= static_cast<uint32>(Report.Manifest.Roots.Num()) ? 1 : 0;
        }
    }

    Report.Stats.Instances = Count;
    if (Report.Stats.NonFiniteInstances > 0)
    {
        Report.Error(Path, FString::Printf(TEXT("%llu instance affines contain non-finite values."), Report.Stats.NonFiniteInstances));
    }
    if (Report.Stats.InvalidMeshIds > 0 || Report.Stats.InvalidRootIds > 0)
    {
        Report.Error(Path, FString::Printf(TEXT("Invalid references: meshId=%llu rootId=%llu."), Report.Stats.InvalidMeshIds, Report.Stats.InvalidRootIds));
    }
    if (Report.Stats.MirrorFlagMismatches > 0)
    {
        Report.Warning(Path, FString::Printf(TEXT("%llu mirror flags disagree with affine determinant."), Report.Stats.MirrorFlagMismatches));
    }
    return true;
}

void AuditMaterials(const FString& PackDirectory, FAuditReport& Report)
{
    const FString Path = FPaths::Combine(PackDirectory, TEXT("materials.json"));
    TArray<FMaterialDesc> Materials;
    if (!FPackReader::ReadMaterials(PackDirectory, Report.Manifest, Materials, Report))
    {
        return;
    }
    Report.Stats.Materials = Materials.Num();

    TSet<FString> TexturePaths;
    for (const FMaterialDesc& Material : Materials)
    {
        ++Report.Stats.MaterialRoles.FindOrAdd(Material.Role);
        for (const FTextureReference& Reference : Material.Textures)
        {
            TexturePaths.Add(Reference.Path);
        }
    }

    Report.Stats.TextureReferences = TexturePaths.Num();
    for (const FString& TexturePath : TexturePaths)
    {
        const FString Resolved = FPaths::IsRelative(TexturePath)
            ? FPaths::Combine(PackDirectory, TexturePath)
            : TexturePath;
        if (!IFileManager::Get().FileExists(*Resolved))
        {
            ++Report.Stats.MissingTextures;
            Report.Error(TEXT("texture"), FString::Printf(TEXT("Missing referenced file: %s"), *TexturePath));
        }
    }
}

void AuditLights(const FString& PackDirectory, FAuditReport& Report)
{
    TArray<FString> Files;
    IFileManager::Get().FindFiles(Files, *FPaths::Combine(PackDirectory, TEXT("lights_*.json")), true, false);
    for (const FString& File : Files)
    {
        TArray<TSharedPtr<FJsonValue>> Values;
        if (ReadJsonArray(FPaths::Combine(PackDirectory, File), Values, Report))
        {
            Report.Stats.Lights += Values.Num();
        }
    }
}
} // namespace

bool FPackReader::ReadMesh(
    const FString& PackDirectory,
    const FManifest& Manifest,
    uint32 MeshId,
    FDecodedMesh& OutMesh,
    FAuditReport& Report)
{
    OutMesh = FDecodedMesh();
    if (!Manifest.Meshes.IsValidIndex(static_cast<int32>(MeshId)))
    {
        Report.Error(TEXT("mesh"), FString::Printf(TEXT("Mesh id %u is out of range."), MeshId));
        return false;
    }

    const FLayoutDesc& Layout = Manifest.VertexLayout;
    bool bLayoutValid = true;
    bLayoutValid &= ValidateField(Layout, TEXT("position"), TEXT("f32x3"), TEXT("manifest.vertex"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("normal"), TEXT("f32x3"), TEXT("manifest.vertex"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("uv"), TEXT("f32x2"), TEXT("manifest.vertex"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("color"), TEXT("unorm8x4"), TEXT("manifest.vertex"), Report);
    if (!bLayoutValid)
    {
        return false;
    }

    const FMeshDesc& Desc = Manifest.Meshes[MeshId];
    const FString Path = FPaths::Combine(PackDirectory, TEXT("meshes.bin"));
    IPlatformFile& PlatformFile = FPlatformFileManager::Get().GetPlatformFile();
    TUniquePtr<IFileHandle> Handle(PlatformFile.OpenRead(*Path));
    if (!Handle)
    {
        Report.Error(Path, TEXT("Cannot open meshes.bin."));
        return false;
    }

    const int64 FileSize = Handle->Size();
    uint64 VertexEnd = 0;
    uint64 IndexEnd = 0;
    if (FileSize < 0
        || !CheckedEnd(Desc.VertexOffset, Desc.VertexCount, Layout.Stride, VertexEnd)
        || !CheckedEnd(Desc.IndexOffset, Desc.IndexCount, sizeof(uint32), IndexEnd)
        || VertexEnd > static_cast<uint64>(FileSize)
        || IndexEnd > static_cast<uint64>(FileSize))
    {
        Report.Error(Path, FString::Printf(TEXT("Mesh %u byte ranges are invalid."), MeshId));
        return false;
    }
    if (Desc.VertexCount > static_cast<uint32>(MAX_int32)
        || Desc.IndexCount > static_cast<uint32>(MAX_int32))
    {
        Report.Error(Path, FString::Printf(TEXT("Mesh %u exceeds UE array capacity."), MeshId));
        return false;
    }

    const uint64 VertexByteCount64 = static_cast<uint64>(Desc.VertexCount) * Layout.Stride;
    const uint64 IndexByteCount64 = static_cast<uint64>(Desc.IndexCount) * sizeof(uint32);
    if (VertexByteCount64 > static_cast<uint64>(MAX_int32)
        || IndexByteCount64 > static_cast<uint64>(MAX_int32))
    {
        Report.Error(Path, FString::Printf(TEXT("Mesh %u decode buffers exceed UE array capacity."), MeshId));
        return false;
    }

    TArray<uint8> VertexBytes;
    TArray<uint8> IndexBytes;
    VertexBytes.SetNumUninitialized(static_cast<int32>(VertexByteCount64));
    IndexBytes.SetNumUninitialized(static_cast<int32>(IndexByteCount64));
    if (!Handle->Seek(static_cast<int64>(Desc.VertexOffset))
        || !Handle->Read(VertexBytes.GetData(), VertexByteCount64)
        || !Handle->Seek(static_cast<int64>(Desc.IndexOffset))
        || !Handle->Read(IndexBytes.GetData(), IndexByteCount64))
    {
        Report.Error(Path, FString::Printf(TEXT("Failed reading mesh %u."), MeshId));
        return false;
    }

    const uint32 PositionOffset = Layout.Find(TEXT("position"))->Offset;
    const uint32 NormalOffset = Layout.Find(TEXT("normal"))->Offset;
    const uint32 UVOffset = Layout.Find(TEXT("uv"))->Offset;
    const uint32 ColorOffset = Layout.Find(TEXT("color"))->Offset;

    OutMesh.Id = Desc.Id;
    OutMesh.Name = Desc.Name;
    OutMesh.Submeshes = Desc.Submeshes;
    OutMesh.Vertices.SetNumUninitialized(static_cast<int32>(Desc.VertexCount));
    for (uint32 VertexIndex = 0; VertexIndex < Desc.VertexCount; ++VertexIndex)
    {
        const uint8* Record = VertexBytes.GetData() + static_cast<uint64>(VertexIndex) * Layout.Stride;
        FDecodedVertex& Vertex = OutMesh.Vertices[static_cast<int32>(VertexIndex)];
        Vertex.Position = FVector3f(
            ReadF32LE(Record + PositionOffset),
            ReadF32LE(Record + PositionOffset + 4),
            ReadF32LE(Record + PositionOffset + 8));
        Vertex.Normal = FVector3f(
            ReadF32LE(Record + NormalOffset),
            ReadF32LE(Record + NormalOffset + 4),
            ReadF32LE(Record + NormalOffset + 8));
        Vertex.UV = FVector2f(
            ReadF32LE(Record + UVOffset),
            ReadF32LE(Record + UVOffset + 4));
        Vertex.Color = FColor(
            Record[ColorOffset],
            Record[ColorOffset + 1],
            Record[ColorOffset + 2],
            Record[ColorOffset + 3]);

        if (Vertex.Position.ContainsNaN() || Vertex.Normal.ContainsNaN() || Vertex.UV.ContainsNaN())
        {
            Report.Error(Path, FString::Printf(TEXT("Mesh %u vertex %u contains non-finite data."), MeshId, VertexIndex));
            return false;
        }
    }

    OutMesh.Indices.SetNumUninitialized(static_cast<int32>(Desc.IndexCount));
    for (uint32 Index = 0; Index < Desc.IndexCount; ++Index)
    {
        const uint32 VertexIndex = ReadU32LE(IndexBytes.GetData() + static_cast<uint64>(Index) * sizeof(uint32));
        if (VertexIndex >= Desc.VertexCount)
        {
            Report.Error(Path, FString::Printf(TEXT("Mesh %u index %u references vertex %u out of range."), MeshId, Index, VertexIndex));
            return false;
        }
        OutMesh.Indices[static_cast<int32>(Index)] = VertexIndex;
    }

    if (Desc.IndexCount % 3 != 0)
    {
        Report.Error(Path, FString::Printf(TEXT("Mesh %u index count is not divisible by three."), MeshId));
        return false;
    }
    for (const FSubmeshDesc& Submesh : Desc.Submeshes)
    {
        uint64 SubmeshEnd = 0;
        if (!CheckedEnd(Submesh.IndexStart, Submesh.IndexCount, 1, SubmeshEnd)
            || SubmeshEnd > Desc.IndexCount
            || Submesh.IndexCount % 3 != 0)
        {
            Report.Error(Path, FString::Printf(TEXT("Mesh %u has an invalid submesh triangle range."), MeshId));
            return false;
        }
    }
    return true;
}

bool FPackReader::ReadManifest(const FString& PackDirectory, FManifest& OutManifest, FAuditReport& Report)
{
    const FString Path = FPaths::Combine(PackDirectory, TEXT("manifest.json"));
    TSharedPtr<FJsonObject> Root;
    if (!ReadJsonObject(Path, Root, Report))
    {
        return false;
    }

    bool bOk = true;
    bOk &= NumberToUInt32(Root, TEXT("version"), OutManifest.Version);
    if (!bOk || OutManifest.Version != 1)
    {
        Report.Error(Path, FString::Printf(TEXT("Unsupported or missing manifest version %u."), OutManifest.Version));
    }
    Root->TryGetStringField(TEXT("dataset"), OutManifest.Dataset);
    Root->TryGetStringField(TEXT("map"), OutManifest.Map);
    Root->TryGetStringField(TEXT("sourceFingerprint"), OutManifest.SourceFingerprint);
    Root->TryGetBoolField(TEXT("selfContained"), OutManifest.bSelfContained);

    const TArray<TSharedPtr<FJsonValue>>* Bounds = RequiredArray(Root, TEXT("bounds"), Path, Report);
    if (Bounds && Bounds->Num() == 6)
    {
        for (int32 Index = 0; Index < 6; ++Index)
        {
            OutManifest.Bounds[Index] = (*Bounds)[Index]->AsNumber();
        }
    }
    else
    {
        Report.Error(Path, TEXT("bounds must contain exactly six numbers."));
        bOk = false;
    }

    const TSharedPtr<FJsonObject> Vertex = RequiredObject(Root, TEXT("vertex"), Path, Report);
    const TSharedPtr<FJsonObject> Instance = RequiredObject(Root, TEXT("instance"), Path, Report);
    bOk &= Vertex.IsValid() && ParseLayout(Vertex, TEXT("attrs"), OutManifest.VertexLayout, TEXT("manifest.vertex"), Report);
    bOk &= Instance.IsValid() && ParseLayout(Instance, TEXT("fields"), OutManifest.InstanceLayout, TEXT("manifest.instance"), Report);

    uint64 InstanceCount = 0;
    if (!NumberToUInt64(Root, TEXT("instanceCount"), InstanceCount))
    {
        Report.Error(Path, TEXT("Invalid instanceCount."));
        bOk = false;
    }
    OutManifest.InstanceCount = InstanceCount;
    if (!NumberToUInt32(Root, TEXT("materialCount"), OutManifest.MaterialCount))
    {
        Report.Error(Path, TEXT("Invalid materialCount."));
        bOk = false;
    }

    const TArray<TSharedPtr<FJsonValue>>* Meshes = RequiredArray(Root, TEXT("meshes"), Path, Report);
    if (Meshes)
    {
        for (const TSharedPtr<FJsonValue>& MeshValue : *Meshes)
        {
            const TSharedPtr<FJsonObject> Object = MeshValue.IsValid() ? MeshValue->AsObject() : nullptr;
            FMeshDesc Mesh;
            uint64 VertexCount = 0;
            uint64 IndexCount = 0;
            if (!Object.IsValid()
                || !NumberToUInt32(Object, TEXT("id"), Mesh.Id)
                || !Object->TryGetStringField(TEXT("name"), Mesh.Name)
                || !NumberToUInt64(Object, TEXT("vtxOffset"), Mesh.VertexOffset)
                || !NumberToUInt64(Object, TEXT("vtxCount"), VertexCount) || VertexCount > MAX_uint32
                || !NumberToUInt64(Object, TEXT("idxOffset"), Mesh.IndexOffset)
                || !NumberToUInt64(Object, TEXT("idxCount"), IndexCount) || IndexCount > MAX_uint32)
            {
                Report.Error(Path, TEXT("Malformed mesh entry."));
                bOk = false;
                continue;
            }
            Mesh.VertexCount = static_cast<uint32>(VertexCount);
            Mesh.IndexCount = static_cast<uint32>(IndexCount);
            if (Object->HasTypedField<EJson::Array>(TEXT("submeshes")))
            {
                for (const TSharedPtr<FJsonValue>& SubValue : Object->GetArrayField(TEXT("submeshes")))
                {
                    const TSharedPtr<FJsonObject> SubObject = SubValue.IsValid() ? SubValue->AsObject() : nullptr;
                    FSubmeshDesc Sub;
                    if (!SubObject.IsValid()
                        || !NumberToUInt32(SubObject, TEXT("materialId"), Sub.MaterialId)
                        || !NumberToUInt32(SubObject, TEXT("idxStart"), Sub.IndexStart)
                        || !NumberToUInt32(SubObject, TEXT("idxCount"), Sub.IndexCount))
                    {
                        Report.Error(Path, FString::Printf(TEXT("Malformed submesh in mesh %u."), Mesh.Id));
                        bOk = false;
                        continue;
                    }
                    Mesh.Submeshes.Add(Sub);
                }
            }
            OutManifest.Meshes.Add(MoveTemp(Mesh));
        }
    }

    if (Root->HasTypedField<EJson::Array>(TEXT("roots")))
    {
        for (const TSharedPtr<FJsonValue>& Value : Root->GetArrayField(TEXT("roots")))
        {
            OutManifest.Roots.Add(Value->AsString());
        }
    }
    if (Root->HasTypedField<EJson::Object>(TEXT("layerNames")))
    {
        for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Root->GetObjectField(TEXT("layerNames"))->Values)
        {
            uint32 Layer = 0;
            if (!LexTryParseString(Layer, *Pair.Key) || !Pair.Value.IsValid() || Pair.Value->Type != EJson::String)
            {
                Report.Error(Path, FString::Printf(TEXT("Malformed layerNames entry '%s'."), *Pair.Key));
                bOk = false;
                continue;
            }
            OutManifest.LayerNames.Add(Layer, Pair.Value->AsString());
        }
    }
    if (Root->HasTypedField<EJson::Array>(TEXT("lodGroups")))
    {
        OutManifest.LodGroupCount = Root->GetArrayField(TEXT("lodGroups")).Num();
    }

    if (Root->HasTypedField<EJson::Object>(TEXT("collider")))
    {
        FLayoutDesc Collider;
        if (ParseLayout(Root->GetObjectField(TEXT("collider")), TEXT("fields"), Collider, TEXT("manifest.collider"), Report))
        {
            OutManifest.ColliderLayout = MoveTemp(Collider);
        }
        NumberToUInt64(Root, TEXT("colliderCount"), OutManifest.ColliderCount);
        if (Root->HasTypedField<EJson::Array>(TEXT("colliderMeshes")))
        {
            for (const TSharedPtr<FJsonValue>& Value : Root->GetArrayField(TEXT("colliderMeshes")))
            {
                const TSharedPtr<FJsonObject> Object = Value.IsValid() ? Value->AsObject() : nullptr;
                FColliderMeshDesc Mesh;
                uint64 VertexCount = 0;
                uint64 IndexCount = 0;
                if (Object.IsValid()
                    && NumberToUInt32(Object, TEXT("id"), Mesh.Id)
                    && Object->TryGetStringField(TEXT("name"), Mesh.Name)
                    && NumberToUInt64(Object, TEXT("vtxOffset"), Mesh.VertexOffset)
                    && NumberToUInt64(Object, TEXT("vtxCount"), VertexCount) && VertexCount <= MAX_uint32
                    && NumberToUInt64(Object, TEXT("idxOffset"), Mesh.IndexOffset)
                    && NumberToUInt64(Object, TEXT("idxCount"), IndexCount) && IndexCount <= MAX_uint32)
                {
                    Mesh.VertexCount = static_cast<uint32>(VertexCount);
                    Mesh.IndexCount = static_cast<uint32>(IndexCount);
                    OutManifest.ColliderMeshes.Add(MoveTemp(Mesh));
                }
                else
                {
                    Report.Error(Path, TEXT("Malformed collider mesh entry."));
                    bOk = false;
                }
            }
        }
    }

    return bOk && !Report.HasErrors();
}

bool FPackReader::ReadMaterials(
    const FString& PackDirectory,
    const FManifest& Manifest,
    TArray<FMaterialDesc>& OutMaterials,
    FAuditReport& Report)
{
    OutMaterials.Reset();
    const FString Path = FPaths::Combine(PackDirectory, TEXT("materials.json"));
    TArray<TSharedPtr<FJsonValue>> Values;
    if (!ReadJsonArray(Path, Values, Report))
    {
        return false;
    }

    bool bOk = true;
    if (static_cast<uint32>(Values.Num()) != Manifest.MaterialCount)
    {
        Report.Error(Path, FString::Printf(
            TEXT("Array length %d differs from materialCount %u."),
            Values.Num(),
            Manifest.MaterialCount));
        bOk = false;
    }

    OutMaterials.Reserve(Values.Num());
    for (int32 Index = 0; Index < Values.Num(); ++Index)
    {
        const TSharedPtr<FJsonObject> Object = Values[Index].IsValid() ? Values[Index]->AsObject() : nullptr;
        if (!Object.IsValid())
        {
            Report.Error(Path, FString::Printf(TEXT("Material %d is not an object."), Index));
            bOk = false;
            continue;
        }

        FMaterialDesc Material;
        if (!NumberToUInt32(Object, TEXT("id"), Material.Id) || Material.Id != static_cast<uint32>(Index))
        {
            Report.Error(Path, FString::Printf(TEXT("Material %d has a non-matching id."), Index));
            bOk = false;
        }
        if (!Object->TryGetStringField(TEXT("role"), Material.Role) || Material.Role.IsEmpty())
        {
            Report.Error(Path, FString::Printf(TEXT("Material %d has no role."), Index));
            bOk = false;
        }

        FString AlphaMode;
        if (!Object->TryGetStringField(TEXT("alphaMode"), AlphaMode))
        {
            Report.Error(Path, FString::Printf(TEXT("Material %d has no alphaMode."), Index));
            bOk = false;
        }
        else if (AlphaMode == TEXT("OPAQUE"))
        {
            Material.AlphaMode = EMaterialAlphaMode::Opaque;
        }
        else if (AlphaMode == TEXT("MASK"))
        {
            Material.AlphaMode = EMaterialAlphaMode::Mask;
        }
        else if (AlphaMode == TEXT("BLEND"))
        {
            Material.AlphaMode = EMaterialAlphaMode::Blend;
        }
        else
        {
            Report.Error(Path, FString::Printf(TEXT("Material %d has unknown alphaMode '%s'."), Index, *AlphaMode));
            bOk = false;
        }

        if (!ReadFloatField(Object, TEXT("alphaCutoff"), Material.AlphaCutoff)
            || !ReadFloat4Field(Object, TEXT("tint"), Material.Tint)
            || !ReadFloat4Field(Object, TEXT("uvXform"), Material.UVTransform)
            || !ReadFloatField(Object, TEXT("metallic"), Material.Metallic)
            || !ReadFloatField(Object, TEXT("roughness"), Material.Roughness)
            || !ReadFloatField(Object, TEXT("normalScale"), Material.NormalScale))
        {
            Report.Error(Path, FString::Printf(TEXT("Material %d has malformed numeric fields."), Index));
            bOk = false;
        }

        if (!Object->TryGetBoolField(TEXT("normalGreenFlip"), Material.bNormalGreenFlip)
            || !Object->TryGetBoolField(TEXT("doubleSided"), Material.bDoubleSided)
            || !Object->TryGetBoolField(TEXT("roughnessFromAlbedoAlpha"), Material.bRoughnessFromAlbedoAlpha))
        {
            Report.Error(Path, FString::Printf(TEXT("Material %d has malformed boolean fields."), Index));
            bOk = false;
        }

        // Preserve feature presence, including blocks with no texture, for honest fallback classification.
        for (const TCHAR* Feature : {TEXT("vp"), TEXT("detail"), TEXT("parallax"), TEXT("emissive")})
        {
            const TSharedPtr<FJsonValue> Value = Object->TryGetField(Feature);
            Material.bUnsupportedOpaqueFeatures |= Value.IsValid() && Value->Type != EJson::Null;
        }
        bool bGlassTRS = false;
        Object->TryGetBoolField(TEXT("glassTRS"), bGlassTRS);
        Material.bUnsupportedOpaqueFeatures |= bGlassTRS;
        CollectTypedTextures(Object, Material.Textures);
        OutMaterials.Add(MoveTemp(Material));
    }
    return bOk;
}

bool FPackReader::ReadInstances(
    const FString& PackDirectory,
    const FManifest& Manifest,
    TArray<FInstanceDesc>& OutInstances,
    FAuditReport& Report)
{
    OutInstances.Reset();
    const FLayoutDesc& Layout = Manifest.InstanceLayout;
    bool bLayoutValid = true;
    bLayoutValid &= ValidateField(Layout, TEXT("affine"), TEXT("f32x12"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("meshId"), TEXT("u32"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("lodGroup"), TEXT("i32"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("lodIndex"), TEXT("i32"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("rootId"), TEXT("u32"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("flags"), TEXT("u32"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("par"), TEXT("u32"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("par2"), TEXT("u32"), TEXT("manifest.instance"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("lv"), TEXT("u32"), TEXT("manifest.instance"), Report);
    if (!bLayoutValid)
    {
        return false;
    }

    const FString Path = FPaths::Combine(PackDirectory, TEXT("instances.bin"));
    IPlatformFile& PlatformFile = FPlatformFileManager::Get().GetPlatformFile();
    TUniquePtr<IFileHandle> Handle(PlatformFile.OpenRead(*Path));
    if (!Handle)
    {
        Report.Error(Path, TEXT("Cannot open instances.bin."));
        return false;
    }
    const int64 FileSize = Handle->Size();
    if (FileSize < 0 || Layout.Stride == 0 || FileSize % Layout.Stride != 0)
    {
        Report.Error(Path, TEXT("Instance file size is not a multiple of its manifest stride."));
        return false;
    }
    const uint64 Count64 = static_cast<uint64>(FileSize / Layout.Stride);
    if (Count64 != Manifest.InstanceCount || Count64 > static_cast<uint64>(MAX_int32))
    {
        Report.Error(Path, FString::Printf(TEXT("Instance record count %llu differs from manifest count %llu or exceeds UE capacity."), Count64, Manifest.InstanceCount));
        return false;
    }

    TArray<uint8> Bytes;
    if (FileSize > MAX_int32 || !FFileHelper::LoadFileToArray(Bytes, *Path) || Bytes.Num() != FileSize)
    {
        Report.Error(Path, TEXT("Cannot load instances.bin into memory."));
        return false;
    }

    const FFieldDesc* AffineField = Layout.Find(TEXT("affine"));
    const FFieldDesc* MeshField = Layout.Find(TEXT("meshId"));
    const FFieldDesc* LodGroupField = Layout.Find(TEXT("lodGroup"));
    const FFieldDesc* LodIndexField = Layout.Find(TEXT("lodIndex"));
    const FFieldDesc* RootField = Layout.Find(TEXT("rootId"));
    const FFieldDesc* FlagsField = Layout.Find(TEXT("flags"));
    const FFieldDesc* ParentField = Layout.Find(TEXT("par"));
    const FFieldDesc* GrandparentField = Layout.Find(TEXT("par2"));
    const FFieldDesc* LevelField = Layout.Find(TEXT("lv"));

    OutInstances.Reserve(static_cast<int32>(Count64));
    for (uint64 Index = 0; Index < Count64; ++Index)
    {
        const uint8* Record = Bytes.GetData() + Index * Layout.Stride;
        FInstanceDesc& Instance = OutInstances.AddDefaulted_GetRef();
        Instance.Index = Index;
        for (uint32 Component = 0; Component < 12; ++Component)
        {
            Instance.Affine[Component] = ReadF32LE(Record + AffineField->Offset + Component * sizeof(float));
        }
        Instance.MeshId = ReadU32LE(Record + MeshField->Offset);
        Instance.LodGroup = ReadI32LE(Record + LodGroupField->Offset);
        Instance.LodIndex = ReadI32LE(Record + LodIndexField->Offset);
        Instance.RootId = ReadU32LE(Record + RootField->Offset);
        Instance.Flags = ReadU32LE(Record + FlagsField->Offset);
        Instance.ParentId = ReadU32LE(Record + ParentField->Offset);
        Instance.GrandparentId = ReadU32LE(Record + GrandparentField->Offset);
        Instance.Level = ReadU32LE(Record + LevelField->Offset);
        if (Instance.MeshId >= static_cast<uint32>(Manifest.Meshes.Num()) || !FCoordinate::AnalyzeAffine(Instance.Affine).bFinite)
        {
            Report.Error(Path, FString::Printf(TEXT("Invalid instance record %llu."), Index));
            return false;
        }
    }
    return true;
}

bool FPackReader::ReadColliders(
    const FString& PackDirectory,
    const FManifest& Manifest,
    TArray<FColliderDesc>& OutColliders,
    FAuditReport& Report)
{
    OutColliders.Reset();
    if (!Manifest.ColliderLayout.IsSet())
    {
        Report.Error(TEXT("manifest.collider"), TEXT("Collider layout is absent."));
        return false;
    }

    const FLayoutDesc& Layout = Manifest.ColliderLayout.GetValue();
    bool bLayoutValid = true;
    bLayoutValid &= ValidateField(Layout, TEXT("affine"), TEXT("f32x12"), TEXT("manifest.collider"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("kind"), TEXT("u32"), TEXT("manifest.collider"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("meshId"), TEXT("i32"), TEXT("manifest.collider"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("center"), TEXT("f32x3"), TEXT("manifest.collider"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("shape"), TEXT("f32x3"), TEXT("manifest.collider"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("layer"), TEXT("u32"), TEXT("manifest.collider"), Report);
    bLayoutValid &= ValidateField(Layout, TEXT("flags"), TEXT("u32"), TEXT("manifest.collider"), Report);
    if (!bLayoutValid)
    {
        return false;
    }

    const FString Path = FPaths::Combine(PackDirectory, TEXT("colliders.bin"));
    const int64 FileSize = IFileManager::Get().FileSize(*Path);
    if (FileSize < 0 || FileSize > MAX_int32 || FileSize % Layout.Stride != 0)
    {
        Report.Error(Path, TEXT("Collider file is missing, too large, or not a multiple of its manifest stride."));
        return false;
    }
    const uint64 Count64 = static_cast<uint64>(FileSize / Layout.Stride);
    if (Count64 != Manifest.ColliderCount || Count64 > static_cast<uint64>(MAX_int32))
    {
        Report.Error(Path, FString::Printf(TEXT("Collider record count %llu differs from manifest count %llu or exceeds UE capacity."), Count64, Manifest.ColliderCount));
        return false;
    }

    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *Path) || Bytes.Num() != FileSize)
    {
        Report.Error(Path, TEXT("Cannot load colliders.bin."));
        return false;
    }

    const FFieldDesc* AffineField = Layout.Find(TEXT("affine"));
    const FFieldDesc* KindField = Layout.Find(TEXT("kind"));
    const FFieldDesc* MeshField = Layout.Find(TEXT("meshId"));
    const FFieldDesc* CenterField = Layout.Find(TEXT("center"));
    const FFieldDesc* ShapeField = Layout.Find(TEXT("shape"));
    const FFieldDesc* LayerField = Layout.Find(TEXT("layer"));
    const FFieldDesc* FlagsField = Layout.Find(TEXT("flags"));

    OutColliders.Reserve(static_cast<int32>(Count64));
    for (uint64 Index = 0; Index < Count64; ++Index)
    {
        const uint8* Record = Bytes.GetData() + Index * Layout.Stride;
        FColliderDesc& Collider = OutColliders.AddDefaulted_GetRef();
        Collider.Index = Index;
        for (uint32 Component = 0; Component < 12; ++Component)
        {
            Collider.Affine[Component] = ReadF32LE(Record + AffineField->Offset + Component * sizeof(float));
        }
        const uint32 KindValue = ReadU32LE(Record + KindField->Offset);
        if (KindValue > static_cast<uint32>(EColliderKind::Mesh))
        {
            Report.Error(Path, FString::Printf(TEXT("Collider %llu has unsupported kind %u."), Index, KindValue));
            return false;
        }
        Collider.Kind = static_cast<EColliderKind>(KindValue);
        Collider.MeshId = ReadI32LE(Record + MeshField->Offset);
        Collider.Center = FVector3f(
            ReadF32LE(Record + CenterField->Offset),
            ReadF32LE(Record + CenterField->Offset + 4),
            ReadF32LE(Record + CenterField->Offset + 8));
        Collider.Shape = FVector3f(
            ReadF32LE(Record + ShapeField->Offset),
            ReadF32LE(Record + ShapeField->Offset + 4),
            ReadF32LE(Record + ShapeField->Offset + 8));
        Collider.Layer = ReadU32LE(Record + LayerField->Offset);
        Collider.Flags = ReadU32LE(Record + FlagsField->Offset);

        bool bFinite = FCoordinate::AnalyzeAffine(Collider.Affine).bFinite;
        bFinite &= FMath::IsFinite(Collider.Center.X) && FMath::IsFinite(Collider.Center.Y) && FMath::IsFinite(Collider.Center.Z);
        bFinite &= FMath::IsFinite(Collider.Shape.X) && FMath::IsFinite(Collider.Shape.Y) && FMath::IsFinite(Collider.Shape.Z);
        if (!bFinite || (Collider.Flags & ~0x0fu) != 0)
        {
            Report.Error(Path, FString::Printf(TEXT("Collider %llu has non-finite data or unknown flag bits."), Index));
            return false;
        }
        if (Collider.Kind == EColliderKind::Mesh)
        {
            if (Collider.MeshId < 0 || !Manifest.ColliderMeshes.ContainsByPredicate([&Collider](const FColliderMeshDesc& Mesh) { return Mesh.Id == static_cast<uint32>(Collider.MeshId); }))
            {
                Report.Error(Path, FString::Printf(TEXT("Mesh collider %llu references missing collider mesh %d."), Index, Collider.MeshId));
                return false;
            }
        }
        else if (Collider.MeshId != -1)
        {
            Report.Error(Path, FString::Printf(TEXT("Primitive collider %llu unexpectedly references mesh %d."), Index, Collider.MeshId));
            return false;
        }
        if (Collider.Kind == EColliderKind::Box && (Collider.Shape.X < 0.0f || Collider.Shape.Y < 0.0f || Collider.Shape.Z < 0.0f))
        {
            Report.Error(Path, FString::Printf(TEXT("Box collider %llu has a negative size."), Index));
            return false;
        }
        if ((Collider.Kind == EColliderKind::Sphere || Collider.Kind == EColliderKind::Capsule) && Collider.Shape.X < 0.0f)
        {
            Report.Error(Path, FString::Printf(TEXT("Sphere/capsule collider %llu has a negative radius."), Index));
            return false;
        }
        if (Collider.Kind == EColliderKind::Capsule
            && (Collider.Shape.Y < 0.0f || Collider.Shape.Z < 0.0f || Collider.Shape.Z > 2.0f || FMath::FloorToFloat(Collider.Shape.Z) != Collider.Shape.Z))
        {
            Report.Error(Path, FString::Printf(TEXT("Capsule collider %llu has invalid height/direction."), Index));
            return false;
        }
    }
    return true;
}

bool FPackReader::ReadColliderMesh(
    const FString& PackDirectory,
    const FManifest& Manifest,
    uint32 MeshId,
    FDecodedColliderMesh& OutMesh,
    FAuditReport& Report)
{
    TMap<uint32, FDecodedColliderMesh> Decoded;
    if (!ReadColliderMeshes(PackDirectory, Manifest, TArray<uint32>{MeshId}, Decoded, Report)) return false;
    FDecodedColliderMesh* Result = Decoded.Find(MeshId);
    if (!Result)
    {
        Report.Error(TEXT("manifest.colliderMeshes"), FString::Printf(TEXT("Collider mesh %u was not decoded."), MeshId));
        return false;
    }
    OutMesh = MoveTemp(*Result);
    return true;
}

bool FPackReader::ReadColliderMeshes(
    const FString& PackDirectory,
    const FManifest& Manifest,
    const TArray<uint32>& MeshIds,
    TMap<uint32, FDecodedColliderMesh>& OutMeshes,
    FAuditReport& Report)
{
    OutMeshes.Reset();
    if (MeshIds.IsEmpty()) return true;

    const FString Path = FPaths::Combine(PackDirectory, TEXT("collider_meshes.bin"));
    const int64 FileSize = IFileManager::Get().FileSize(*Path);
    if (FileSize < 0 || FileSize > MAX_int32)
    {
        Report.Error(Path, TEXT("Collider mesh file is missing or too large."));
        return false;
    }
    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *Path) || Bytes.Num() != FileSize)
    {
        Report.Error(Path, TEXT("Cannot load collider_meshes.bin."));
        return false;
    }

    TSet<uint32> UniqueMeshIds;
    OutMeshes.Reserve(MeshIds.Num());
    for (uint32 MeshId : MeshIds)
    {
        if (UniqueMeshIds.Contains(MeshId))
        {
            Report.Error(TEXT("manifest.colliderMeshes"), FString::Printf(TEXT("Requested collider mesh %u more than once."), MeshId));
            OutMeshes.Reset();
            return false;
        }
        UniqueMeshIds.Add(MeshId);
        const FColliderMeshDesc* Descriptor = Manifest.ColliderMeshes.FindByPredicate([MeshId](const FColliderMeshDesc& Mesh) { return Mesh.Id == MeshId; });
        uint64 VertexEnd = 0;
        uint64 IndexEnd = 0;
        if (!Descriptor || Descriptor->VertexCount < 3 || Descriptor->IndexCount < 3 || Descriptor->IndexCount % 3 != 0
            || !CheckedEnd(Descriptor->VertexOffset, Descriptor->VertexCount, 12, VertexEnd)
            || !CheckedEnd(Descriptor->IndexOffset, Descriptor->IndexCount, sizeof(uint32), IndexEnd)
            || VertexEnd > static_cast<uint64>(FileSize) || IndexEnd > static_cast<uint64>(FileSize))
        {
            Report.Error(Path, FString::Printf(TEXT("Collider mesh %u is absent or has invalid counts/byte ranges."), MeshId));
            OutMeshes.Reset();
            return false;
        }

        FDecodedColliderMesh& OutMesh = OutMeshes.Add(MeshId);
        OutMesh.Id = MeshId;
        OutMesh.Vertices.Reserve(Descriptor->VertexCount);
        for (uint32 Index = 0; Index < Descriptor->VertexCount; ++Index)
        {
            const uint8* Position = Bytes.GetData() + Descriptor->VertexOffset + static_cast<uint64>(Index) * 12;
            const FVector3f Vertex(ReadF32LE(Position), ReadF32LE(Position + 4), ReadF32LE(Position + 8));
            if (!FMath::IsFinite(Vertex.X) || !FMath::IsFinite(Vertex.Y) || !FMath::IsFinite(Vertex.Z))
            {
                Report.Error(Path, FString::Printf(TEXT("Collider mesh %u has a non-finite vertex %u."), MeshId, Index));
                OutMeshes.Reset();
                return false;
            }
            OutMesh.Vertices.Add(Vertex);
        }
        OutMesh.Indices.Reserve(Descriptor->IndexCount);
        for (uint32 Index = 0; Index < Descriptor->IndexCount; ++Index)
        {
            const uint32 Value = ReadU32LE(Bytes.GetData() + Descriptor->IndexOffset + static_cast<uint64>(Index) * sizeof(uint32));
            if (Value >= Descriptor->VertexCount)
            {
                Report.Error(Path, FString::Printf(TEXT("Collider mesh %u has out-of-range index %u at %u."), MeshId, Value, Index));
                OutMeshes.Reset();
                return false;
            }
            OutMesh.Indices.Add(Value);
        }
    }
    return true;
}

bool FPackReader::Audit(const FString& PackDirectory, FAuditReport& OutReport)
{
    OutReport = FAuditReport();
    OutReport.PackDirectory = FPaths::ConvertRelativePathToFull(PackDirectory);
    FPaths::NormalizeDirectoryName(OutReport.PackDirectory);

    if (!IFileManager::Get().DirectoryExists(*OutReport.PackDirectory))
    {
        OutReport.Error(OutReport.PackDirectory, TEXT("Pack directory does not exist."));
        return false;
    }
    if (!ReadManifest(OutReport.PackDirectory, OutReport.Manifest, OutReport))
    {
        OutReport.bSuccess = false;
        return false;
    }

    const FString MeshesPath = FPaths::Combine(OutReport.PackDirectory, TEXT("meshes.bin"));
    const int64 MeshesSize = IFileManager::Get().FileSize(*MeshesPath);
    if (MeshesSize < 0)
    {
        OutReport.Error(MeshesPath, TEXT("Required file is missing."));
    }
    else
    {
        for (int32 Index = 0; Index < OutReport.Manifest.Meshes.Num(); ++Index)
        {
            const FMeshDesc& Mesh = OutReport.Manifest.Meshes[Index];
            if (Mesh.Id != static_cast<uint32>(Index))
            {
                OutReport.Error(TEXT("manifest.meshes"), FString::Printf(TEXT("Mesh array index %d has id %u."), Index, Mesh.Id));
            }
            uint64 VertexEnd = 0;
            uint64 IndexEnd = 0;
            if (!CheckedEnd(Mesh.VertexOffset, Mesh.VertexCount, OutReport.Manifest.VertexLayout.Stride, VertexEnd)
                || VertexEnd > static_cast<uint64>(MeshesSize))
            {
                OutReport.Error(MeshesPath, FString::Printf(TEXT("Mesh %u vertex range is out of bounds."), Mesh.Id));
            }
            if (!CheckedEnd(Mesh.IndexOffset, Mesh.IndexCount, sizeof(uint32), IndexEnd)
                || IndexEnd > static_cast<uint64>(MeshesSize))
            {
                OutReport.Error(MeshesPath, FString::Printf(TEXT("Mesh %u index range is out of bounds."), Mesh.Id));
            }
            for (const FSubmeshDesc& Submesh : Mesh.Submeshes)
            {
                if (static_cast<uint64>(Submesh.IndexStart) + Submesh.IndexCount > Mesh.IndexCount)
                {
                    OutReport.Error(TEXT("manifest.submeshes"), FString::Printf(TEXT("Mesh %u submesh index range is invalid."), Mesh.Id));
                }
                if (Submesh.MaterialId >= OutReport.Manifest.MaterialCount)
                {
                    OutReport.Error(TEXT("manifest.submeshes"), FString::Printf(TEXT("Mesh %u references material %u out of range."), Mesh.Id, Submesh.MaterialId));
                }
            }
            ++OutReport.Stats.Meshes;
            OutReport.Stats.Vertices += Mesh.VertexCount;
            OutReport.Stats.Indices += Mesh.IndexCount;
            OutReport.Stats.Triangles += Mesh.IndexCount / 3;
            OutReport.Stats.Submeshes += Mesh.Submeshes.Num();
        }
    }

    AuditMaterials(OutReport.PackDirectory, OutReport);
    AuditInstances(FPaths::Combine(OutReport.PackDirectory, TEXT("instances.bin")), OutReport);

    OutReport.Stats.Colliders = OutReport.Manifest.ColliderCount;
    OutReport.Stats.ColliderMeshes = OutReport.Manifest.ColliderMeshes.Num();
    if (OutReport.Manifest.ColliderLayout.IsSet())
    {
        const FString CollidersPath = FPaths::Combine(OutReport.PackDirectory, TEXT("colliders.bin"));
        const int64 ColliderSize = IFileManager::Get().FileSize(*CollidersPath);
        const uint32 Stride = OutReport.Manifest.ColliderLayout->Stride;
        if (ColliderSize < 0)
        {
            OutReport.Error(CollidersPath, TEXT("Collider layout is declared but colliders.bin is missing."));
        }
        else if (Stride == 0 || ColliderSize % Stride != 0 || static_cast<uint64>(ColliderSize / Stride) != OutReport.Manifest.ColliderCount)
        {
            OutReport.Error(CollidersPath, TEXT("Collider file size/count does not match manifest layout."));
        }
    }

    AuditLights(OutReport.PackDirectory, OutReport);
    OutReport.bSuccess = !OutReport.HasErrors();
    return OutReport.bSuccess;
}
} // namespace AtlasEft
