#pragma once

#include "CoreMinimal.h"
#include "EditorSubsystem.h"
#include "AtlasImportSubsystem.generated.h"

UCLASS()
class ATLASEFTIMPORTER_API UAtlasImportSubsystem : public UEditorSubsystem
{
    GENERATED_BODY()

public:
    /** Audits an Atlas .eftpack directory. This milestone performs no asset or level mutation. */
    UFUNCTION(BlueprintCallable, Category = "Atlas|EFT Pack")
    bool AuditPack(const FString& PackDirectory, FString& OutReport) const;
};
