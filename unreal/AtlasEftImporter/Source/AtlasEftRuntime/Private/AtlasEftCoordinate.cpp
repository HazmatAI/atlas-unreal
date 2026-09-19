#include "AtlasEftCoordinate.h"

namespace AtlasEft
{
namespace
{
constexpr double Basis[3][3] = {
    {0.0, 0.0, 1.0},
    {-1.0, 0.0, 0.0},
    {0.0, 1.0, 0.0}
};
}

FVector3d FCoordinate::PositionToUnreal(const FVector3d& P)
{
    return FVector3d(P.Z, -P.X, P.Y) * MetresToCentimetres;
}

FVector3d FCoordinate::VectorToUnreal(const FVector3d& V)
{
    return FVector3d(V.Z, -V.X, V.Y);
}

void FCoordinate::AffineToUnreal(const double A[12], double Out[12])
{
    double L[3][3] = {
        {A[0], A[1], A[2]},
        {A[4], A[5], A[6]},
        {A[8], A[9], A[10]}
    };

    double Converted[3][3] = {};
    for (int32 Row = 0; Row < 3; ++Row)
    {
        for (int32 Col = 0; Col < 3; ++Col)
        {
            for (int32 I = 0; I < 3; ++I)
            {
                for (int32 J = 0; J < 3; ++J)
                {
                    // C * L * C^T; C^-1 == C^T because C is orthogonal.
                    Converted[Row][Col] += Basis[Row][I] * L[I][J] * Basis[Col][J];
                }
            }
        }
    }

    const FVector3d T = PositionToUnreal(FVector3d(A[3], A[7], A[11]));
    Out[0] = Converted[0][0]; Out[1] = Converted[0][1]; Out[2] = Converted[0][2]; Out[3] = T.X;
    Out[4] = Converted[1][0]; Out[5] = Converted[1][1]; Out[6] = Converted[1][2]; Out[7] = T.Y;
    Out[8] = Converted[2][0]; Out[9] = Converted[2][1]; Out[10] = Converted[2][2]; Out[11] = T.Z;
}

FAffineAnalysis FCoordinate::AnalyzeAffine(const float A[12])
{
    FAffineAnalysis Result;
    for (int32 Index = 0; Index < 12; ++Index)
    {
        Result.bFinite &= FMath::IsFinite(A[Index]);
    }

    const FVector3d C0(A[0], A[4], A[8]);
    const FVector3d C1(A[1], A[5], A[9]);
    const FVector3d C2(A[2], A[6], A[10]);
    const double L0 = C0.Length();
    const double L1 = C1.Length();
    const double L2 = C2.Length();
    const double LMax = FMath::Max3(L0, L1, L2);
    const double LMin = FMath::Min3(L0, L1, L2);

    Result.bDegenerate = LMax <= 1.0e-12 || LMin <= LMax * 1.0e-6;
    if (!Result.bDegenerate)
    {
        const FVector3d U0 = C0 / L0;
        const FVector3d U1 = C1 / L1;
        const FVector3d U2 = C2 / L2;
        Result.MaxNormalizedColumnDot = FMath::Max3(
            FMath::Abs(U0.Dot(U1)),
            FMath::Abs(U0.Dot(U2)),
            FMath::Abs(U1.Dot(U2)));
    }
    else
    {
        Result.MaxNormalizedColumnDot = 1.0;
    }

    Result.Determinant =
        static_cast<double>(A[0]) * (static_cast<double>(A[5]) * A[10] - static_cast<double>(A[6]) * A[9]) -
        static_cast<double>(A[1]) * (static_cast<double>(A[4]) * A[10] - static_cast<double>(A[6]) * A[8]) +
        static_cast<double>(A[2]) * (static_cast<double>(A[4]) * A[9] - static_cast<double>(A[5]) * A[8]);
    Result.bMirrored = Result.Determinant < 0.0;
    Result.bSheared = Result.bDegenerate || Result.MaxNormalizedColumnDot > ShearTolerance;
    return Result;
}
} // namespace AtlasEft

