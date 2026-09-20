#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "AtlasEftBuildSectorCommandlet.generated.h"

/** Builds either a complete root selection or a bounded root/level/ancestor group with materials and validation. */
UCLASS()
class ATLASEFTIMPORTER_API UAtlasEftBuildSectorCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    UAtlasEftBuildSectorCommandlet();
    virtual int32 Main(const FString& Params) override;
};
