#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "AtlasEftBuildMaterialSampleCommandlet.generated.h"

UCLASS()
class ATLASEFTIMPORTER_API UAtlasEftBuildMaterialSampleCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    UAtlasEftBuildMaterialSampleCommandlet();
    virtual int32 Main(const FString& Params) override;
};
