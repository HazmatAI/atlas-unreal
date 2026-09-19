#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "AtlasEftBuildSceneCommandlet.generated.h"

/** Builds a small spatially coherent Atlas scene prototype for transform validation. */
UCLASS()
class ATLASEFTIMPORTER_API UAtlasEftBuildSceneCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    UAtlasEftBuildSceneCommandlet();
    virtual int32 Main(const FString& Params) override;
};
