using UnrealBuildTool;

public class AtlasEftImporter : ModuleRules
{
    public AtlasEftImporter(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PrivateDependencyModuleNames.AddRange(new[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "Json",
            "UnrealEd",
            "MaterialEditor",
            "AssetTools",
            "AssetRegistry",
            "MeshDescription",
            "StaticMeshDescription",
            "MeshConversion",
            "EditorSubsystem",
            "AtlasEftRuntime",
            "ToolMenus",
            "Slate",
            "SlateCore",
            "DesktopPlatform"
        });
    }
}
