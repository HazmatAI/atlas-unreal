#include "AtlasEftMaterialImporter.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AtlasEftTextureImporter.h"
#include "Engine/Texture2D.h"
#include "MaterialEditingLibrary.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpressionClamp.h"
#include "Materials/MaterialExpressionComponentMask.h"
#include "Materials/MaterialExpressionDeriveNormalZ.h"
#include "Materials/MaterialExpressionLinearInterpolate.h"
#include "Materials/MaterialExpressionMultiply.h"
#include "Materials/MaterialExpressionOneMinus.h"
#include "Materials/MaterialExpressionScalarParameter.h"
#include "Materials/MaterialExpressionStaticSwitchParameter.h"
#include "Materials/MaterialExpressionTextureSampleParameter2D.h"
#include "Materials/MaterialExpressionVectorParameter.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Misc/PackageName.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

namespace AtlasEft
{
namespace
{
const FName BaseColorTextureParameter(TEXT("BaseColorTexture"));
const FName NormalTextureParameter(TEXT("NormalTexture"));
const FName SpecularProvenanceTextureParameter(TEXT("SpecularProvenanceTexture"));
const FName TintParameter(TEXT("Tint"));
const FName MetallicParameter(TEXT("Metallic"));
const FName RoughnessParameter(TEXT("Roughness"));
const FName UseAlbedoAlphaRoughnessParameter(TEXT("UseAlbedoAlphaRoughness"));
const FName NormalScaleParameter(TEXT("NormalScale"));
const FName DielectricSpecularParameter(TEXT("DielectricSpecular"));
const FName DebugUseSpecularProvenanceParameter(TEXT("DebugUseSpecularProvenance"));

bool SaveAssetPackage(UObject* Asset, FString& OutError)
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

bool ConnectExpressions(
    UMaterialExpression* From,
    const TCHAR* FromOutput,
    UMaterialExpression* To,
    const TCHAR* ToInput,
    const TCHAR* Label,
    FString& OutError)
{
    if (UMaterialEditingLibrary::ConnectMaterialExpressions(From, FromOutput, To, ToInput))
    {
        return true;
    }
    OutError = FString::Printf(
        TEXT("Could not connect %s (outputs: %s; inputs: %s)."),
        Label,
        *FString::Join(UMaterialEditingLibrary::GetMaterialExpressionOutputNames(From), TEXT(",")),
        *FString::Join(UMaterialEditingLibrary::GetMaterialExpressionInputNames(To), TEXT(",")));
    return false;
}

bool ConnectProperty(
    UMaterialExpression* From,
    const TCHAR* FromOutput,
    EMaterialProperty Property,
    const TCHAR* Label,
    FString& OutError)
{
    if (UMaterialEditingLibrary::ConnectMaterialProperty(From, FromOutput, Property))
    {
        return true;
    }
    OutError = FString::Printf(
        TEXT("Could not connect material property %s (outputs: %s)."),
        Label,
        *FString::Join(UMaterialEditingLibrary::GetMaterialExpressionOutputNames(From), TEXT(",")));
    return false;
}

template <typename TExpression>
TExpression* AddExpression(UMaterial* Material, int32 X, int32 Y)
{
    return Cast<TExpression>(UMaterialEditingLibrary::CreateMaterialExpression(
        Material, TExpression::StaticClass(), X, Y));
}

const FTextureReference* FindTexture(const FMaterialDesc& Material, ETextureSemantic Semantic)
{
    return Material.Textures.FindByPredicate([Semantic](const FTextureReference& Reference)
    {
        return Reference.Semantic == Semantic;
    });
}

bool BuildMasterMaterial(
    const FString& DestinationPath,
    UMaterial*& OutMaterial,
    FString& OutError)
{
    const FString AssetName = TEXT("M_AtlasOpaque");
    const FString PackageName = DestinationPath / AssetName;
    UPackage* Package = CreatePackage(*PackageName);
    if (!Package)
    {
        OutError = FString::Printf(TEXT("Could not create master package %s."), *PackageName);
        return false;
    }
    Package->FullyLoad();
    UMaterial* Material = FindObject<UMaterial>(Package, *AssetName);
    const bool bNewAsset = Material == nullptr;
    if (bNewAsset)
    {
        Material = NewObject<UMaterial>(Package, *AssetName, RF_Public | RF_Standalone);
    }
    if (!Material)
    {
        OutError = TEXT("Could not create M_AtlasOpaque.");
        return false;
    }

    Material->PreEditChange(nullptr);
    UMaterialEditingLibrary::DeleteAllMaterialExpressions(Material);
    Material->MaterialDomain = MD_Surface;
    Material->BlendMode = BLEND_Opaque;
    Material->TwoSided = true;
    Material->SetShadingModel(MSM_DefaultLit);

    UTexture* DefaultColor = LoadObject<UTexture>(nullptr, TEXT("/Engine/EngineResources/DefaultTexture.DefaultTexture"));
    UTexture* DefaultNormal = LoadObject<UTexture>(nullptr, TEXT("/Engine/EngineMaterials/DefaultNormal.DefaultNormal"));
    if (!DefaultColor || !DefaultNormal)
    {
        OutError = TEXT("Could not load Unreal default color/normal textures.");
        return false;
    }

    UMaterialExpressionTextureSampleParameter2D* BaseTexture = AddExpression<UMaterialExpressionTextureSampleParameter2D>(Material, -900, -200);
    UMaterialExpressionVectorParameter* Tint = AddExpression<UMaterialExpressionVectorParameter>(Material, -900, -20);
    UMaterialExpressionMultiply* TintedBase = AddExpression<UMaterialExpressionMultiply>(Material, -550, -160);
    UMaterialExpressionTextureSampleParameter2D* NormalTexture = AddExpression<UMaterialExpressionTextureSampleParameter2D>(Material, -900, 240);
    UMaterialExpressionComponentMask* NormalXY = AddExpression<UMaterialExpressionComponentMask>(Material, -650, 240);
    UMaterialExpressionScalarParameter* NormalScale = AddExpression<UMaterialExpressionScalarParameter>(Material, -650, 390);
    UMaterialExpressionMultiply* ScaledNormalXY = AddExpression<UMaterialExpressionMultiply>(Material, -400, 260);
    UMaterialExpressionDeriveNormalZ* DerivedNormal = AddExpression<UMaterialExpressionDeriveNormalZ>(Material, -150, 260);
    UMaterialExpressionScalarParameter* Metallic = AddExpression<UMaterialExpressionScalarParameter>(Material, -500, 520);
    UMaterialExpressionScalarParameter* ScalarRoughness = AddExpression<UMaterialExpressionScalarParameter>(Material, -900, 650);
    UMaterialExpressionOneMinus* InvertAlpha = AddExpression<UMaterialExpressionOneMinus>(Material, -650, 700);
    UMaterialExpressionClamp* ClampRoughness = AddExpression<UMaterialExpressionClamp>(Material, -430, 700);
    UMaterialExpressionScalarParameter* UseAlphaRoughness = AddExpression<UMaterialExpressionScalarParameter>(Material, -430, 830);
    UMaterialExpressionLinearInterpolate* SelectRoughness = AddExpression<UMaterialExpressionLinearInterpolate>(Material, -150, 680);
    UMaterialExpressionScalarParameter* DielectricSpecular = AddExpression<UMaterialExpressionScalarParameter>(Material, -400, 980);
    UMaterialExpressionTextureSampleParameter2D* SpecularProvenance = AddExpression<UMaterialExpressionTextureSampleParameter2D>(Material, -900, 1040);
    UMaterialExpressionStaticSwitchParameter* SpecularSwitch = AddExpression<UMaterialExpressionStaticSwitchParameter>(Material, -120, 980);
    if (!BaseTexture || !Tint || !TintedBase || !NormalTexture || !NormalXY || !NormalScale
        || !ScaledNormalXY || !DerivedNormal || !Metallic || !ScalarRoughness || !InvertAlpha
        || !ClampRoughness || !UseAlphaRoughness || !SelectRoughness || !DielectricSpecular
        || !SpecularProvenance || !SpecularSwitch)
    {
        OutError = TEXT("Could not create all M_AtlasOpaque expressions.");
        return false;
    }

    BaseTexture->ParameterName = BaseColorTextureParameter;
    BaseTexture->Texture = DefaultColor;
    BaseTexture->SamplerType = SAMPLERTYPE_Color;
    Tint->ParameterName = TintParameter;
    Tint->DefaultValue = FLinearColor::White;
    NormalTexture->ParameterName = NormalTextureParameter;
    NormalTexture->Texture = DefaultNormal;
    NormalTexture->SamplerType = SAMPLERTYPE_Normal;
    NormalXY->R = true;
    NormalXY->G = true;
    NormalScale->ParameterName = NormalScaleParameter;
    NormalScale->DefaultValue = 1.0f;
    NormalScale->SliderMin = 0.0f;
    NormalScale->SliderMax = 2.0f;
    Metallic->ParameterName = MetallicParameter;
    Metallic->DefaultValue = 0.0f;
    Metallic->SliderMin = 0.0f;
    Metallic->SliderMax = 1.0f;
    ScalarRoughness->ParameterName = RoughnessParameter;
    ScalarRoughness->DefaultValue = 0.9f;
    ScalarRoughness->SliderMin = 0.0f;
    ScalarRoughness->SliderMax = 1.0f;
    ClampRoughness->MinDefault = 0.06f;
    ClampRoughness->MaxDefault = 1.0f;
    UseAlphaRoughness->ParameterName = UseAlbedoAlphaRoughnessParameter;
    UseAlphaRoughness->DefaultValue = 0.0f;
    UseAlphaRoughness->SliderMin = 0.0f;
    UseAlphaRoughness->SliderMax = 1.0f;
    DielectricSpecular->ParameterName = DielectricSpecularParameter;
    DielectricSpecular->DefaultValue = 0.5f;
    DielectricSpecular->SliderMin = 0.0f;
    DielectricSpecular->SliderMax = 1.0f;
    SpecularProvenance->ParameterName = SpecularProvenanceTextureParameter;
    SpecularProvenance->Texture = DefaultColor;
    SpecularProvenance->SamplerType = SAMPLERTYPE_Masks;
    SpecularProvenance->Desc = TEXT("Atlas provenance only. Production switch remains false, so specMap is not compiled or sampled.");
    SpecularSwitch->ParameterName = DebugUseSpecularProvenanceParameter;
    SpecularSwitch->DefaultValue = false;
    SpecularSwitch->Desc = TEXT("Debug-only provenance inspection. Must remain false for Atlas parity.");

    if (!ConnectExpressions(BaseTexture, TEXT("RGB"), TintedBase, TEXT("A"), TEXT("BaseColorTexture.RGB -> tint A"), OutError)
        || !ConnectExpressions(Tint, TEXT("RGB"), TintedBase, TEXT("B"), TEXT("Tint.RGB -> tint B"), OutError)
        || !ConnectProperty(TintedBase, TEXT(""), MP_BaseColor, TEXT("BaseColor"), OutError)
        || !ConnectExpressions(NormalTexture, TEXT("RGB"), NormalXY, TEXT(""), TEXT("NormalTexture.RGB -> mask"), OutError)
        || !ConnectExpressions(NormalXY, TEXT(""), ScaledNormalXY, TEXT("A"), TEXT("normal XY -> scale A"), OutError)
        || !ConnectExpressions(NormalScale, TEXT(""), ScaledNormalXY, TEXT("B"), TEXT("NormalScale -> scale B"), OutError)
        || !ConnectExpressions(ScaledNormalXY, TEXT(""), DerivedNormal, TEXT(""), TEXT("scaled XY -> DeriveNormalZ"), OutError)
        || !ConnectProperty(DerivedNormal, TEXT(""), MP_Normal, TEXT("Normal"), OutError)
        || !ConnectProperty(Metallic, TEXT(""), MP_Metallic, TEXT("Metallic"), OutError)
        || !ConnectExpressions(BaseTexture, TEXT("A"), InvertAlpha, TEXT(""), TEXT("albedo alpha -> OneMinus"), OutError)
        || !ConnectExpressions(InvertAlpha, TEXT(""), ClampRoughness, TEXT(""), TEXT("OneMinus -> roughness clamp"), OutError)
        || !ConnectExpressions(ScalarRoughness, TEXT(""), SelectRoughness, TEXT("A"), TEXT("scalar roughness -> lerp A"), OutError)
        || !ConnectExpressions(ClampRoughness, TEXT(""), SelectRoughness, TEXT("B"), TEXT("alpha roughness -> lerp B"), OutError)
        || !ConnectExpressions(UseAlphaRoughness, TEXT(""), SelectRoughness, TEXT("Alpha"), TEXT("RFA selector -> lerp alpha"), OutError)
        || !ConnectProperty(SelectRoughness, TEXT(""), MP_Roughness, TEXT("Roughness"), OutError)
        || !ConnectExpressions(DielectricSpecular, TEXT(""), SpecularSwitch, TEXT("False"), TEXT("dielectric specular -> provenance switch False"), OutError)
        || !ConnectExpressions(SpecularProvenance, TEXT("R"), SpecularSwitch, TEXT("True"), TEXT("specMap provenance -> debug switch True"), OutError)
        || !ConnectProperty(SpecularSwitch, TEXT(""), MP_Specular, TEXT("Specular"), OutError))
    {
        return false;
    }

    const TArray<FString> CompileErrors = UMaterialEditingLibrary::RecompileMaterial(Material);
    if (!CompileErrors.IsEmpty())
    {
        OutError = TEXT("M_AtlasOpaque compilation failed:\n") + FString::Join(CompileErrors, TEXT("\n"));
        return false;
    }
    Material->PostEditChange();
    if (bNewAsset)
    {
        FAssetRegistryModule::AssetCreated(Material);
    }
    if (!SaveAssetPackage(Material, OutError))
    {
        return false;
    }
    OutMaterial = Material;
    return true;
}
} // namespace

bool FMaterialImporter::ImportOpaqueMaterial(
    const FString& PackDirectory,
    const FMaterialDesc& Material,
    const FString& MasterDestinationPath,
    const FString& InstanceDestinationPath,
    const FString& TextureDestinationPath,
    FMaterialImportResult& OutResult,
    FString& OutError)
{
    OutResult = FMaterialImportResult();
    OutError.Reset();
    if (Material.AlphaMode != EMaterialAlphaMode::Opaque || Material.Role != TEXT("opaque"))
    {
        OutError = FString::Printf(TEXT("Material %u is not an opaque Atlas material."), Material.Id);
        return false;
    }
    for (const FString& Destination : {MasterDestinationPath, InstanceDestinationPath, TextureDestinationPath})
    {
        if (!FPackageName::IsValidLongPackageName(Destination))
        {
            OutError = FString::Printf(TEXT("Invalid Unreal destination path: %s"), *Destination);
            return false;
        }
    }

    const FTextureReference* AlbedoReference = FindTexture(Material, ETextureSemantic::Albedo);
    const FTextureReference* NormalReference = FindTexture(Material, ETextureSemantic::Normal);
    const FTextureReference* SpecularReference = FindTexture(Material, ETextureSemantic::Specular);
    if (!AlbedoReference || !NormalReference || !SpecularReference)
    {
        OutError = FString::Printf(TEXT("Material %u requires albedo, normal, and specMap provenance for this vertical test."), Material.Id);
        return false;
    }

    FTextureImportResult AlbedoResult;
    FTextureImportResult NormalResult;
    FTextureImportResult SpecularResult;
    if (!FTextureImporter::ImportTexture(PackDirectory, *AlbedoReference, TextureDestinationPath, AlbedoResult, OutError)
        || !FTextureImporter::ImportTexture(PackDirectory, *NormalReference, TextureDestinationPath, NormalResult, OutError)
        || !FTextureImporter::ImportTexture(PackDirectory, *SpecularReference, TextureDestinationPath, SpecularResult, OutError))
    {
        return false;
    }
    if (!AlbedoResult.bSRGB || AlbedoResult.CompressionSettings != TC_Default
        || NormalResult.bSRGB || NormalResult.CompressionSettings != TC_Normalmap || NormalResult.bFlipGreenChannel
        || SpecularResult.bSRGB || SpecularResult.CompressionSettings != TC_Masks)
    {
        OutError = TEXT("Imported textures do not match the required Atlas semantic settings.");
        return false;
    }

    UMaterial* Master = nullptr;
    if (!BuildMasterMaterial(MasterDestinationPath, Master, OutError))
    {
        return false;
    }

    const FString InstanceName = FString::Printf(TEXT("MI_Atlas_%04u"), Material.Id);
    const FString InstancePackageName = InstanceDestinationPath / InstanceName;
    UPackage* InstancePackage = CreatePackage(*InstancePackageName);
    if (!InstancePackage)
    {
        OutError = FString::Printf(TEXT("Could not create material-instance package %s."), *InstancePackageName);
        return false;
    }
    InstancePackage->FullyLoad();
    UMaterialInstanceConstant* Instance = FindObject<UMaterialInstanceConstant>(InstancePackage, *InstanceName);
    const bool bNewInstance = Instance == nullptr;
    if (bNewInstance)
    {
        Instance = NewObject<UMaterialInstanceConstant>(InstancePackage, *InstanceName, RF_Public | RF_Standalone);
    }
    if (!Instance)
    {
        OutError = TEXT("Could not create Atlas material instance.");
        return false;
    }
    Instance->PreEditChange(nullptr);
    UMaterialEditingLibrary::SetMaterialInstanceParent(Instance, Master);
    UMaterialEditingLibrary::ClearAllMaterialInstanceParameters(Instance);
    // Populate the serialized overrides directly. This is deterministic across headless editor runs and
    // also permits an explicit inactive provenance reference in the static switch's uncompiled branch.
    Instance->TextureParameterValues = {
        FTextureParameterValue(FMaterialParameterInfo(BaseColorTextureParameter), AlbedoResult.Texture),
        FTextureParameterValue(FMaterialParameterInfo(NormalTextureParameter), NormalResult.Texture),
        FTextureParameterValue(
        FMaterialParameterInfo(SpecularProvenanceTextureParameter),
        SpecularResult.Texture)
    };
    Instance->VectorParameterValues = {
        FVectorParameterValue(
            FMaterialParameterInfo(TintParameter),
            FLinearColor(Material.Tint.X, Material.Tint.Y, Material.Tint.Z, Material.Tint.W))
    };
    Instance->ScalarParameterValues = {
        FScalarParameterValue(FMaterialParameterInfo(MetallicParameter), Material.Metallic),
        FScalarParameterValue(FMaterialParameterInfo(RoughnessParameter), Material.Roughness),
        FScalarParameterValue(FMaterialParameterInfo(UseAlbedoAlphaRoughnessParameter), Material.bRoughnessFromAlbedoAlpha ? 1.0f : 0.0f),
        FScalarParameterValue(FMaterialParameterInfo(NormalScaleParameter), Material.NormalScale),
        FScalarParameterValue(FMaterialParameterInfo(DielectricSpecularParameter), 0.5f)
    };
    UMaterialEditingLibrary::UpdateMaterialInstance(Instance);
    Instance->PostEditChange();
    if (bNewInstance)
    {
        FAssetRegistryModule::AssetCreated(Instance);
    }
    if (!SaveAssetPackage(Instance, OutError))
    {
        return false;
    }

    const FTextureParameterValue* SpecularProvenanceOverride = Instance->TextureParameterValues.FindByPredicate([](const FTextureParameterValue& Value)
    {
        return Value.ParameterInfo.Name == SpecularProvenanceTextureParameter;
    });
    if (Instance->Parent != Master
        || UMaterialEditingLibrary::GetMaterialInstanceTextureParameterValue(Instance, BaseColorTextureParameter) != AlbedoResult.Texture
        || UMaterialEditingLibrary::GetMaterialInstanceTextureParameterValue(Instance, NormalTextureParameter) != NormalResult.Texture
        || !SpecularProvenanceOverride || SpecularProvenanceOverride->ParameterValue != SpecularResult.Texture
        || !FMath::IsNearlyEqual(UMaterialEditingLibrary::GetMaterialInstanceScalarParameterValue(Instance, UseAlbedoAlphaRoughnessParameter), 1.0f)
        || UMaterialEditingLibrary::GetMaterialInstanceStaticSwitchParameterValue(Instance, DebugUseSpecularProvenanceParameter))
    {
        OutError = TEXT("Material-instance parent or parameter round-trip validation failed.");
        return false;
    }

    OutResult.MasterMaterial = Master;
    OutResult.MaterialInstance = Instance;
    OutResult.AlbedoTexture = AlbedoResult.Texture;
    OutResult.NormalTexture = NormalResult.Texture;
    OutResult.SpecularProvenanceTexture = SpecularResult.Texture;
    OutResult.MasterObjectPath = Master->GetPathName();
    OutResult.InstanceObjectPath = Instance->GetPathName();
    return true;
}
} // namespace AtlasEft
