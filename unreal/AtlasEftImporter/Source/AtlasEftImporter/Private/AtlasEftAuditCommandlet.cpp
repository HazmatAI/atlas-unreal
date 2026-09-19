#include "AtlasEftAuditCommandlet.h"

#include "AtlasEftPackReader.h"
#include "Misc/Parse.h"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftAuditCommandlet, Log, All);

UAtlasEftAuditCommandlet::UAtlasEftAuditCommandlet()
{
    IsClient = false;
    IsEditor = true;
    IsServer = false;
    LogToConsole = true;
    ShowErrorCount = true;
}

int32 UAtlasEftAuditCommandlet::Main(const FString& Params)
{
    FString PackDirectory;
    if (!FParse::Value(*Params, TEXT("Pack="), PackDirectory) || PackDirectory.IsEmpty())
    {
        UE_LOG(LogAtlasEftAuditCommandlet, Error,
            TEXT("Missing -Pack=<path to map.eftpack directory>."));
        return 2;
    }

    PackDirectory.TrimQuotesInline();
    AtlasEft::FAuditReport Report;
    const bool bSuccess = AtlasEft::FPackReader::Audit(PackDirectory, Report);
    UE_LOG(LogAtlasEftAuditCommandlet, Display, TEXT("%s"), *Report.ToText());
    return bSuccess ? 0 : 1;
}

