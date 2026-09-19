#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "AtlasEftImportMeshesCommandlet.generated.h"

/** Batch-imports a deterministic range (or all) of Atlas render meshes. */
UCLASS()
class ATLASEFTIMPORTER_API UAtlasEftImportMeshesCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    UAtlasEftImportMeshesCommandlet();
    virtual int32 Main(const FString& Params) override;
};
