[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Container })]
    [string]$PackPath,

    [string]$UnrealRoot = 'C:\Program Files\Epic Games\UE_5.8',

    [string]$StageRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'AtlasEftImporterDev'),

    [ValidateRange(0, 2147483647)]
    [int]$SampleMeshId = 0,

    [ValidateRange(0, 2147483647)]
    [int]$SampleTextureMaterialId = 17,

    [ValidateRange(1, 32)]
    [int]$SampleTextureCount = 3,

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

Write-Host "== Building material vertical test (material $SampleTextureMaterialId on mesh $SampleMeshId) ==" -ForegroundColor Cyan
& $editorCmd $hostProject `
    -run=AtlasEftBuildMaterialSample `
    "-Pack=$PackPath" `
    "-MaterialId=$SampleTextureMaterialId" `
    "-MeshId=$SampleMeshId" `
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
$materialMaster = Join-Path $hostDirectory 'Content\Atlas\Common\Materials\M_AtlasOpaque.uasset'
$materialInstance = Join-Path $hostDirectory ("Content\Atlas\MaterialTest\Materials\MI_Atlas_{0:D4}.uasset" -f $SampleTextureMaterialId)
$materialTestMeshPrefix = 'SM_Atlas_{0:D4}_' -f $SampleMeshId
$materialTestMeshes = @(Get-ChildItem -LiteralPath (Join-Path $hostDirectory 'Content\Atlas\MaterialTest\Meshes') -Filter "$materialTestMeshPrefix*.uasset" -File -ErrorAction Stop)
$materialTestTextures = @(Get-ChildItem -LiteralPath (Join-Path $hostDirectory 'Content\Atlas\MaterialTest\Textures') -Filter '*.uasset' -File -ErrorAction Stop)
foreach ($requiredAsset in @($materialTestMap, $materialMaster, $materialInstance)) {
    if (-not (Test-Path -LiteralPath $requiredAsset -PathType Leaf)) {
        throw "Material vertical test reported success but asset was not created: $requiredAsset"
    }
}
if ($materialTestMeshes.Count -ne 1 -or $materialTestTextures.Count -ne 3) {
    throw "Material vertical test expected one mesh and three textures; found meshes=$($materialTestMeshes.Count) textures=$($materialTestTextures.Count)."
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

Write-Host "PASS: pack audit, $($sampleTextureAssets.Count) sample textures, sample/batch meshes, $SceneInstanceCount-instance architecture map, material vertical test, and $successCount runtime tests succeeded." -ForegroundColor Green
Write-Host "Sample asset: $($sampleAssets[0].FullName)"
Write-Host "Sample texture assets: $($sampleTextureAssets.Count)"
Write-Host "Batch assets: $($batchAssets.Count)"
Write-Host "Architecture map: $architectureMap"
Write-Host "Material test map: $materialTestMap"
Write-Host "Material instance: $materialInstance"
Write-Host "Staged plugin: $pluginPackage"
