#include "AtlasImportSubsystem.h"

#include "AtlasEftPackReader.h"
#include "DesktopPlatformModule.h"
#include "Framework/Application/SlateApplication.h"
#include "IDesktopPlatform.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/MessageDialog.h"
#include "ToolMenus.h"

#define LOCTEXT_NAMESPACE "FAtlasEftImporterModule"

DEFINE_LOG_CATEGORY_STATIC(LogAtlasEftImporter, Log, All);

class FAtlasEftImporterModule final : public IModuleInterface
{
public:
    virtual void StartupModule() override
    {
        UToolMenus::RegisterStartupCallback(
            FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FAtlasEftImporterModule::RegisterMenus));
    }

    virtual void ShutdownModule() override
    {
        if (UToolMenus::IsToolMenuUIEnabled())
        {
            UToolMenus::UnRegisterStartupCallback(this);
            UToolMenus::UnregisterOwner(this);
        }
    }

private:
    void RegisterMenus()
    {
        FToolMenuOwnerScoped OwnerScoped(this);
        UToolMenu* Menu = UToolMenus::Get()->ExtendMenu(TEXT("LevelEditor.MainMenu.Tools"));
        FToolMenuSection& Section = Menu->FindOrAddSection(TEXT("AtlasEft"));
        Section.AddMenuEntry(
            TEXT("AtlasEftAuditPack"),
            LOCTEXT("AuditPackLabel", "Audit Atlas EFT Pack…"),
            LOCTEXT("AuditPackTooltip", "Validate an Atlas .eftpack directory without creating assets."),
            FSlateIcon(),
            FUIAction(FExecuteAction::CreateRaw(this, &FAtlasEftImporterModule::ChooseAndAuditPack)));
    }

    void ChooseAndAuditPack()
    {
        IDesktopPlatform* DesktopPlatform = FDesktopPlatformModule::Get();
        if (!DesktopPlatform)
        {
            FMessageDialog::Open(EAppMsgType::Ok, LOCTEXT("NoDesktopPlatform", "Desktop platform services are unavailable."));
            return;
        }

        const void* ParentHandle = FSlateApplication::Get().FindBestParentWindowHandleForDialogs(nullptr);
        FString SelectedDirectory;
        if (!DesktopPlatform->OpenDirectoryDialog(
            ParentHandle,
            TEXT("Select Atlas .eftpack directory"),
            FPaths::ProjectDir(),
            SelectedDirectory))
        {
            return;
        }

        AtlasEft::FAuditReport Report;
        AtlasEft::FPackReader::Audit(SelectedDirectory, Report);
        const FString Text = Report.ToText();
        UE_LOG(LogAtlasEftImporter, Display, TEXT("%s"), *Text);
        FMessageDialog::Open(
            EAppMsgType::Ok,
            FText::FromString(Report.bSuccess
                ? FString::Printf(TEXT("Audit passed.\n\n%s"), *Text)
                : FString::Printf(TEXT("Audit failed. See Output Log for details.\n\n%s"), *Text)));
    }
};

IMPLEMENT_MODULE(FAtlasEftImporterModule, AtlasEftImporter)

#undef LOCTEXT_NAMESPACE
