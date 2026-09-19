#pragma once

#include "CoreMinimal.h"

namespace AtlasEft
{
struct ATLASEFTRUNTIME_API FAffineAnalysis
{
    bool bFinite = true;
    bool bDegenerate = false;
    bool bSheared = false;
    bool bMirrored = false;
    double Determinant = 1.0;
    double MaxNormalizedColumnDot = 0.0;
};

/** Centralized Atlas (RH, Y-up, metres) -> Unreal (LH, Z-up, centimetres) conversion. */
class ATLASEFTRUNTIME_API FCoordinate
{
public:
    static constexpr double MetresToCentimetres = 100.0;
    static constexpr double ShearTolerance = 0.02;

    static FVector3d PositionToUnreal(const FVector3d& AtlasPosition);
    static FVector3d VectorToUnreal(const FVector3d& AtlasVector);

    /**
     * Converts a row-major Atlas world affine [3x4] by C * L * C^-1 and C * t.
     * Translation is converted to centimetres; the linear 3x3 remains dimensionless.
     */
    static void AffineToUnreal(const double AtlasAffine[12], double UnrealAffine[12]);

    /** Classifies the linear 3x3 using the same thresholds as Atlas' Blender importer. */
    static FAffineAnalysis AnalyzeAffine(const float AtlasAffine[12]);
};
} // namespace AtlasEft

