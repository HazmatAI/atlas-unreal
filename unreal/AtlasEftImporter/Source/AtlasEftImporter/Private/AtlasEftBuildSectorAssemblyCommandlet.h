#pragma once

#include "Commandlets/Commandlet.h"
#include "AtlasEftBuildSectorAssemblyCommandlet.generated.h"

UCLASS()
class UAtlasEftBuildSectorAssemblyCommandlet final : public UCommandlet
{
    GENERATED_BODY()

public:
    UAtlasEftBuildSectorAssemblyCommandlet();
    virtual int32 Main(const FString& Params) override;
};
