#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "AtlasEftAuditCommandlet.generated.h"

/** Headless integrity audit: UnrealEditor-Cmd <project> -run=AtlasEftAudit -Pack=<directory>. */
UCLASS()
class ATLASEFTIMPORTER_API UAtlasEftAuditCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    UAtlasEftAuditCommandlet();
    virtual int32 Main(const FString& Params) override;
};

