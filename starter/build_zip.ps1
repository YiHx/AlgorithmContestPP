# 把 ojhc_probe 打成 DGHub 可导入的 zip（根目录直接是 manifest.json）
# 用法：.\build_zip.ps1            -> 生成 ojhc_probe.zip
#       .\build_zip.ps1 -Out foo.zip
param(
    [string]$Out = "ojhc_probe.zip"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src  = Join-Path $here "ojhc_probe"
$dst  = Join-Path $here $Out
$stage = Join-Path $here ".build_stage"

if (-not (Test-Path (Join-Path $src "manifest.json"))) {
    throw "找不到 $src\manifest.json，请确认插件目录结构"
}

# 用干净暂存目录，避免把 __pycache__ / *.pyc / 本地调试文件打进包
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null

Get-ChildItem -Path $src -Force | Where-Object {
    $_.Name -notin @("__pycache__", ".pytest_cache") -and $_.Extension -ne ".pyc"
} | ForEach-Object {
    Copy-Item $_.FullName -Destination $stage -Recurse -Force
}

if (Test-Path $dst) { Remove-Item $dst -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $dst
Remove-Item $stage -Recurse -Force

$size = (Get-Item $dst).Length
Write-Host "已生成: $dst ($([math]::Round($size/1KB,1)) KB)"
Write-Host "在 DGHub 里：插件中心 -> 外部插件 -> 导入 zip"
