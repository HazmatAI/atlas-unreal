using UnrealBuildTool;

public class AtlasEftRuntime : ModuleRules
{
    public AtlasEftRuntime(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "Json"
        });
    }
}

