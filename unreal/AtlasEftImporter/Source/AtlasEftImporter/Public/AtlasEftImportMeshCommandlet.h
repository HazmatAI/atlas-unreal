#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "AtlasEftImportMeshCommandlet.generated.h"

/** Imports one Atlas render mesh as a persistent UStaticMesh sample asset. */
UCLASS()
class ATLASEFTIMPORTER_API UAtlasEftImportMeshCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    UAtlasEftImportMeshCommandlet();
    virtual int32 Main(const FString& Params) override;
};
