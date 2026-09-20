#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "AtlasEftAddSectorCollisionsCommandlet.generated.h"

/** Adds provenance-tagged Root3 colliders to the existing validated Root3 map. */
UCLASS()
class ATLASEFTIMPORTER_API UAtlasEftAddSectorCollisionsCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    UAtlasEftAddSectorCollisionsCommandlet();
    virtual int32 Main(const FString& Params) override;
};
