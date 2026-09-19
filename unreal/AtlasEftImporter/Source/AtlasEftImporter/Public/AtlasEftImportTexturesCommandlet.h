#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "AtlasEftImportTexturesCommandlet.generated.h"

/** Imports a deliberately small texture sample from one Atlas material. */
UCLASS()
class ATLASEFTIMPORTER_API UAtlasEftImportTexturesCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    UAtlasEftImportTexturesCommandlet();
    virtual int32 Main(const FString& Params) override;
};
