#include "AtlasEftPackTypes.h"

namespace AtlasEft
{
const FFieldDesc* FLayoutDesc::Find(const FString& Name) const
{
    return Fields.FindByPredicate([&Name](const FFieldDesc& Field)
    {
        return Field.Name == Name;
    });
}

void FAuditReport::Add(EIssueSeverity Severity, const FString& Context, const FString& Message)
{
    Issues.Add({Severity, Context, Message});
}

void FAuditReport::Error(const FString& Context, const FString& Message)
{
    Add(EIssueSeverity::Error, Context, Message);
}

void FAuditReport::Warning(const FString& Context, const FString& Message)
{
    Add(EIssueSeverity::Warning, Context, Message);
}

void FAuditReport::Info(const FString& Context, const FString& Message)
{
    Add(EIssueSeverity::Info, Context, Message);
}

bool FAuditReport::HasErrors() const
{
    return Issues.ContainsByPredicate([](const FIssue& Issue)
    {
        return Issue.Severity == EIssueSeverity::Error;
    });
}

FString FAuditReport::ToText() const
{
    FString Out;
    Out += FString::Printf(TEXT("Atlas EFT pack audit: %s\n"), bSuccess ? TEXT("PASS") : TEXT("FAIL"));
    Out += FString::Printf(TEXT("Pack: %s\n"), *PackDirectory);
    Out += FString::Printf(TEXT("Map: %s  Dataset: %s  Version: %u\n"),
        *Manifest.Map, *Manifest.Dataset, Manifest.Version);
    Out += FString::Printf(TEXT("Meshes: %llu  Vertices: %llu  Triangles: %llu  Submeshes: %llu\n"),
        Stats.Meshes, Stats.Vertices, Stats.Triangles, Stats.Submeshes);
    Out += FString::Printf(TEXT("Instances: %llu  Materials: %llu  Textures: %llu (%llu missing)\n"),
        Stats.Instances, Stats.Materials, Stats.TextureReferences, Stats.MissingTextures);
    Out += FString::Printf(TEXT("Affine: shear=%llu degenerate=%llu mirror=%llu baked=%llu maxDot=%.6f\n"),
        Stats.ShearedInstances, Stats.DegenerateInstances, Stats.MirroredInstances,
        Stats.BakedWorldInstances, Stats.MaxNormalizedColumnDot);
    Out += FString::Printf(TEXT("Colliders: %llu  Collider meshes: %llu  Lights: %llu\n"),
        Stats.Colliders, Stats.ColliderMeshes, Stats.Lights);

    if (!Issues.IsEmpty())
    {
        Out += TEXT("Issues:\n");
        for (const FIssue& Issue : Issues)
        {
            const TCHAR* Label = Issue.Severity == EIssueSeverity::Error ? TEXT("ERROR")
                : Issue.Severity == EIssueSeverity::Warning ? TEXT("WARN") : TEXT("INFO");
            Out += FString::Printf(TEXT("  [%s] %s: %s\n"), Label, *Issue.Context, *Issue.Message);
        }
    }
    return Out;
}
} // namespace AtlasEft

