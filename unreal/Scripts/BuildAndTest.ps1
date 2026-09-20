[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Container })]
    [string]$PackPath,

    [string]$UnrealRoot = 'C:\Program Files\Epic Games\UE_5.8',

    [string]$StageRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'AtlasEftImporterDev'),

    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$LiveProjectPath,

    [ValidateRange(0, 2147483647)]
    [int]$SampleMeshId = 20,

    [ValidateRange(0, 2147483647)]
    [int]$SampleTextureMaterialId = 17,

    [ValidateRange(1, 32)]
    [int]$SampleTextureCount = 3,

    [ValidateRange(0, 2147483647)]
    [int]$SecondMaterialId = 974,

    [ValidateRange(0, 2147483647)]
    [int]$SecondMaterialMeshId = 1240,

    [ValidateRange(1, 2147483647)]
    [int]$BatchMeshCount = 3,

    [ValidateRange(0, 2147483647)]
    [int]$SceneSeedInstance = 0,

    [ValidateRange(1, 200)]
    [int]$SceneInstanceCount = 25
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$unrealDirectory = Split-Path -Parent $scriptRoot
$pluginDescriptor = Join-Path $unrealDirectory 'AtlasEftImporter\AtlasEftImporter.uplugin'
$hostProjectSource = Join-Path $unrealDirectory 'AtlasEftImporterHost\AtlasEftImporterHost.uproject'
$hostConfigSource = Join-Path $unrealDirectory 'AtlasEftImporterHost\Config\DefaultGame.ini'
$uat = Join-Path $UnrealRoot 'Engine\Build\BatchFiles\RunUAT.bat'
$editorCmd = Join-Path $UnrealRoot 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
$pluginPackage = Join-Path $StageRoot 'BuildPluginPackage'
$hostDirectory = Join-Path $StageRoot 'AtlasEftImporterHost'
$hostProject = Join-Path $hostDirectory 'AtlasEftImporterHost.uproject'

$liveProjectDirectory = $null
if ($LiveProjectPath) {
    $LiveProjectPath = (Resolve-Path -LiteralPath $LiveProjectPath).Path
    if ([System.IO.Path]::GetExtension($LiveProjectPath) -ne '.uproject') {
        throw "LiveProjectPath must point to a .uproject file: $LiveProjectPath"
    }
    $liveProjectDirectory = Split-Path -Parent $LiveProjectPath
}

foreach ($requiredPath in @($pluginDescriptor, $hostProjectSource, $hostConfigSource, $uat, $editorCmd)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required file not found: $requiredPath"
    }
}

New-Item -ItemType Directory -Force -Path $StageRoot, $hostDirectory | Out-Null

Write-Host '== Building Atlas EFT Importer ==' -ForegroundColor Cyan
& $uat BuildPlugin `
    "-Plugin=$pluginDescriptor" `
    "-Package=$pluginPackage" `
    -TargetPlatforms=Win64 `
    -Rocket
if ($LASTEXITCODE -ne 0) {
    throw "BuildPlugin failed with exit code $LASTEXITCODE"
}

Copy-Item -LiteralPath $hostProjectSource -Destination $hostProject -Force
$hostConfigDirectory = Join-Path $hostDirectory 'Config'
New-Item -ItemType Directory -Force -Path $hostConfigDirectory | Out-Null
Copy-Item -LiteralPath $hostConfigSource -Destination (Join-Path $hostConfigDirectory 'DefaultGame.ini') -Force

Write-Host '== Auditing EFT pack in Unreal ==' -ForegroundColor Cyan
& $editorCmd $hostProject `
    -run=AtlasEftAudit `
    "-Pack=$PackPath" `
    -unattended `
    -nop4 `
    -NullRHI `
    -NoSplash
if ($LASTEXITCODE -ne 0) {
    throw "AtlasEftAudit failed with exit code $LASTEXITCODE"
}

Write-Host "== Importing $SampleTextureCount sample textures from material $SampleTextureMaterialId ==" -ForegroundColor Cyan
& $editorCmd $hostProject `
    -run=AtlasEftImportTextures `
    "-Pack=$PackPath" `
    "-MaterialId=$SampleTextureMaterialId" `
    "-MaxTextures=$SampleTextureCount" `
    '-Destination=/Game/Atlas/Textures/Samples' `
    -unattended `
    -nop4 `
    -NullRHI `
    -NoSplash
if ($LASTEXITCODE -ne 0) {
    throw "AtlasEftImportTextures failed with exit code $LASTEXITCODE"
}

$sampleTextureAssets = @(Get-ChildItem -LiteralPath (Join-Path $hostDirectory 'Content\Atlas\Textures\Samples') -Filter '*.uasset' -File -ErrorAction Stop)
if ($sampleTextureAssets.Count -lt $SampleTextureCount) {
    throw "Texture sample import created only $($sampleTextureAssets.Count) assets; expected at least $SampleTextureCount."
}

Write-Host "== Importing sample UStaticMesh (mesh $SampleMeshId) ==" -ForegroundColor Cyan
& $editorCmd $hostProject `
    -run=AtlasEftImportMesh `
    "-Pack=$PackPath" `
    "-MeshId=$SampleMeshId" `
    '-Destination=/Game/Atlas/Samples' `
    -unattended `
    -nop4 `
    -NullRHI `
    -NoSplash
if ($LASTEXITCODE -ne 0) {
    throw "AtlasEftImportMesh failed with exit code $LASTEXITCODE"
}

$sampleAssetPrefix = 'SM_Atlas_{0:D4}_' -f $SampleMeshId
$sampleAssets = @(Get-ChildItem -LiteralPath (Join-Path $hostDirectory 'Content\Atlas\Samples') -Filter "$sampleAssetPrefix*.uasset" -File -ErrorAction Stop)
if ($sampleAssets.Count -ne 1) {
    throw "AtlasEftImportMesh expected exactly one $sampleAssetPrefix asset, found $($sampleAssets.Count)."
}

Write-Host "== Batch-importing $BatchMeshCount UStaticMeshes ==" -ForegroundColor Cyan
& $editorCmd $hostProject `
    -run=AtlasEftImportMeshes `
    "-Pack=$PackPath" `
    -FirstMeshId=0 `
    "-Count=$BatchMeshCount" `
    '-Destination=/Game/Atlas/Meshes' `
    -unattended `
    -nop4 `
    -NullRHI `
    -NoSplash
if ($LASTEXITCODE -ne 0) {
    throw "AtlasEftImportMeshes failed with exit code $LASTEXITCODE"
}

$batchAssets = @(Get-ChildItem -LiteralPath (Join-Path $hostDirectory 'Content\Atlas\Meshes') -Filter '*.uasset' -File -Recurse -ErrorAction Stop)
if ($batchAssets.Count -lt $BatchMeshCount) {
    throw "Batch import created only $($batchAssets.Count) assets; expected at least $BatchMeshCount."
}

Write-Host "== Building architecture scene from $SceneInstanceCount nearby instances (seed $SceneSeedInstance) ==" -ForegroundColor Cyan
& $editorCmd $hostProject `
    -run=AtlasEftBuildScene `
    "-Pack=$PackPath" `
    "-SeedInstance=$SceneSeedInstance" `
    "-Count=$SceneInstanceCount" `
    '-Destination=/Game/Atlas/ArchitectureTest' `
    -unattended `
    -nop4 `
    -NullRHI `
    -NoSplash
if ($LASTEXITCODE -ne 0) {
    throw "AtlasEftBuildScene failed with exit code $LASTEXITCODE"
}

$architectureMapName = 'L_Atlas_Architecture_Seed{0:D6}_Count{1:D3}.umap' -f $SceneSeedInstance, $SceneInstanceCount
$architectureMap = Join-Path $hostDirectory "Content\Atlas\ArchitectureTest\$architectureMapName"
if (-not (Test-Path -LiteralPath $architectureMap -PathType Leaf)) {
    throw "Architecture scene reported success but map was not created: $architectureMap"
}

Write-Host "== Building material comparison (materials $SampleTextureMaterialId and $SecondMaterialId) ==" -ForegroundColor Cyan
& $editorCmd $hostProject `
    -run=AtlasEftBuildMaterialSample `
    "-Pack=$PackPath" `
    "-MaterialId=$SampleTextureMaterialId" `
    "-MeshId=$SampleMeshId" `
    "-SecondMaterialId=$SecondMaterialId" `
    "-SecondMeshId=$SecondMaterialMeshId" `
    '-Destination=/Game/Atlas/MaterialTest' `
    -unattended `
    -nop4 `
    -NullRHI `
    -NoSplash
if ($LASTEXITCODE -ne 0) {
    throw "AtlasEftBuildMaterialSample failed with exit code $LASTEXITCODE"
}

$materialTestMapName = 'L_Atlas_Material_{0:D4}_Mesh{1:D4}.umap' -f $SampleTextureMaterialId, $SampleMeshId
$materialTestMap = Join-Path $hostDirectory "Content\Atlas\MaterialTest\$materialTestMapName"
$secondMaterialTestMapName = 'L_Atlas_Material_{0:D4}_Mesh{1:D4}.umap' -f $SecondMaterialId, $SecondMaterialMeshId
$secondMaterialTestMap = Join-Path $hostDirectory "Content\Atlas\MaterialTest\$secondMaterialTestMapName"
$materialMaster = Join-Path $hostDirectory 'Content\Atlas\Common\Materials\M_AtlasOpaque.uasset'
$materialInstance = Join-Path $hostDirectory ("Content\Atlas\MaterialTest\Materials\MI_Atlas_{0:D4}.uasset" -f $SampleTextureMaterialId)
$secondMaterialInstance = Join-Path $hostDirectory ("Content\Atlas\MaterialTest\Materials\MI_Atlas_{0:D4}.uasset" -f $SecondMaterialId)
$materialTestMeshPrefix = 'SM_Atlas_{0:D4}_' -f $SampleMeshId
$materialTestMeshes = @(Get-ChildItem -LiteralPath (Join-Path $hostDirectory 'Content\Atlas\MaterialTest\Meshes') -Filter "$materialTestMeshPrefix*.uasset" -File -ErrorAction Stop)
$secondMaterialTestMeshPrefix = 'SM_Atlas_{0:D4}_' -f $SecondMaterialMeshId
$secondMaterialTestMeshes = @(Get-ChildItem -LiteralPath (Join-Path $hostDirectory 'Content\Atlas\MaterialTest\Meshes') -Filter "$secondMaterialTestMeshPrefix*.uasset" -File -ErrorAction Stop)
$materialTestTextures = @(Get-ChildItem -LiteralPath (Join-Path $hostDirectory 'Content\Atlas\MaterialTest\Textures') -Filter '*.uasset' -File -ErrorAction Stop)
foreach ($requiredAsset in @($materialTestMap, $secondMaterialTestMap, $materialMaster, $materialInstance, $secondMaterialInstance)) {
    if (-not (Test-Path -LiteralPath $requiredAsset -PathType Leaf)) {
        throw "Material vertical test reported success but asset was not created: $requiredAsset"
    }
}
if ($materialTestMeshes.Count -ne 1 -or $secondMaterialTestMeshes.Count -ne 1 -or $materialTestTextures.Count -lt 5) {
    throw "Material comparison expected two meshes and at least five textures; found firstMeshes=$($materialTestMeshes.Count) secondMeshes=$($secondMaterialTestMeshes.Count) textures=$($materialTestTextures.Count)."
}

Write-Host '== Running Atlas runtime automation tests ==' -ForegroundColor Cyan
& $editorCmd $hostProject `
    '-ExecCmds=Automation RunTests Atlas.Eft.Runtime;Quit' `
    '-TestExit=Automation Test Queue Empty' `
    -unattended `
    -nop4 `
    -NullRHI `
    -NoSplash
if ($LASTEXITCODE -ne 0) {
    throw "Unreal automation run failed with exit code $LASTEXITCODE"
}

$automationLog = Join-Path $hostDirectory 'Saved\Logs\AtlasEftImporterHost.log'
$successCount = (Select-String -LiteralPath $automationLog -SimpleMatch 'Test Completed. Result={Success}' -ErrorAction Stop).Count
$failureCount = (Select-String -LiteralPath $automationLog -Pattern 'Test Completed\. Result=\{(Fail|Skipped)\}' -ErrorAction SilentlyContinue).Count

if ($successCount -lt 5 -or $failureCount -ne 0) {
    throw "Unexpected automation result: successes=$successCount failures=$failureCount. See $automationLog"
}

if ($LiveProjectPath) {
    Write-Host "== Synchronizing only the validated material-comparison assets to $LiveProjectPath ==" -ForegroundColor Cyan
    $liveContentDirectory = Join-Path $liveProjectDirectory 'Content\Atlas'
    $livePluginDescriptor = Join-Path $liveProjectDirectory 'Plugins\AtlasEftImporter\AtlasEftImporter.uplugin'
    if (-not (Test-Path -LiteralPath $livePluginDescriptor -PathType Leaf)) {
        throw "The live Atlas plugin is not installed; refusing to copy or replace plugin files during asset-only synchronization: $livePluginDescriptor"
    }

    $materialComparisonRelativeAssets = @(
        'Common\Materials\M_AtlasOpaque.uasset',
        "MaterialTest\$materialTestMapName",
        "MaterialTest\$secondMaterialTestMapName",
        ("MaterialTest\Materials\MI_Atlas_{0:D4}.uasset" -f $SampleTextureMaterialId),
        ("MaterialTest\Materials\MI_Atlas_{0:D4}.uasset" -f $SecondMaterialId),
        ("MaterialTest\Meshes\$($materialTestMeshes[0].Name)"),
        ("MaterialTest\Meshes\$($secondMaterialTestMeshes[0].Name)")
    )
    foreach ($textureAsset in $materialTestTextures) {
        $materialComparisonRelativeAssets += "MaterialTest\Textures\$($textureAsset.Name)"
    }

    foreach ($relativeAssetPath in $materialComparisonRelativeAssets) {
        $stagedAsset = Join-Path (Join-Path $hostDirectory 'Content\Atlas') $relativeAssetPath
        if (-not (Test-Path -LiteralPath $stagedAsset -PathType Leaf)) {
            throw "Validated staged material asset is missing: $stagedAsset"
        }
        $liveAsset = Join-Path $liveContentDirectory $relativeAssetPath
        $liveAssetDirectory = Split-Path -Parent $liveAsset
        New-Item -ItemType Directory -Force -Path $liveAssetDirectory | Out-Null
        Copy-Item -LiteralPath $stagedAsset -Destination $liveAsset -Force

        $stagedHash = (Get-FileHash -LiteralPath $stagedAsset -Algorithm SHA256).Hash
        $liveHash = (Get-FileHash -LiteralPath $liveAsset -Algorithm SHA256).Hash
        if ($stagedHash -ne $liveHash) {
            throw "Live material asset synchronization hash mismatch: $relativeAssetPath"
        }
    }

    if ($materialComparisonRelativeAssets.Count -ne 12) {
        throw "Material comparison sync expected exactly 12 assets, found $($materialComparisonRelativeAssets.Count)."
    }

    Write-Host '== Validating Atlas plugin from the live project ==' -ForegroundColor Cyan
    & $editorCmd $LiveProjectPath `
        -run=AtlasEftAudit `
        "-Pack=$PackPath" `
        -DisablePlugins=ModelContextProtocol `
        -unattended `
        -nop4 `
        -NullRHI `
        -NoSplash
    if ($LASTEXITCODE -ne 0) {
        throw "Live project AtlasEftAudit failed with exit code $LASTEXITCODE"
    }
}

Write-Host "PASS: pack audit, $($sampleTextureAssets.Count) sample textures, sample/batch meshes, $SceneInstanceCount-instance architecture map, two-material comparison, and $successCount runtime tests succeeded." -ForegroundColor Green
Write-Host "Sample asset: $($sampleAssets[0].FullName)"
Write-Host "Sample texture assets: $($sampleTextureAssets.Count)"
Write-Host "Batch assets: $($batchAssets.Count)"
Write-Host "Architecture map: $architectureMap"
Write-Host "Material test map: $materialTestMap"
Write-Host "Material instance: $materialInstance"
Write-Host "Second material test map: $secondMaterialTestMap"
Write-Host "Second material instance: $secondMaterialInstance"
Write-Host "Staged plugin: $pluginPackage"
if ($LiveProjectPath) {
    Write-Host "Live project: $LiveProjectPath"
    Write-Host "Live plugin preserved (not synchronized): $(Join-Path $liveProjectDirectory 'Plugins\AtlasEftImporter')"
    Write-Host "Live material assets synchronized: $($materialComparisonRelativeAssets.Count)"
}
