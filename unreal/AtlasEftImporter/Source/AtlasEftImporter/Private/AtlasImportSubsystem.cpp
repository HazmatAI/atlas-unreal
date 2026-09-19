#include "AtlasImportSubsystem.h"

#include "AtlasEftPackReader.h"

bool UAtlasImportSubsystem::AuditPack(const FString& PackDirectory, FString& OutReport) const
{
    AtlasEft::FAuditReport Report;
    const bool bResult = AtlasEft::FPackReader::Audit(PackDirectory, Report);
    OutReport = Report.ToText();
    return bResult;
}

