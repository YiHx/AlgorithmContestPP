# 把插件打成 DGHub 可导入的 zip（zip 根目录直接是 manifest.json）
# 用法：
#   .\build_zip.ps1                  # 打包 starter/ 下全部插件
#   .\build_zip.ps1 -Plugin ojhC_link
param(
    [string]$Plugin = "all"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Get-PluginNames {
    if ($Plugin -ne "all") { return @($Plugin) }
    return @(Get-ChildItem -Path $here -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName "manifest.json") } |
        ForEach-Object { $_.Name })
}

foreach ($name in (Get-PluginNames)) {
    $src = Join-Path $here $name
    $dst = Join-Path $here "$name.zip"
    $stage = Join-Path $here ".build_stage_$name"

    if (-not (Test-Path (Join-Path $src "manifest.json"))) {
        throw "找不到 $src\manifest.json，请确认插件目录结构"
    }

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
    $size = [math]::Round((Get-Item $dst).Length / 1KB, 1)
    Write-Host "已生成: $dst ($size KB)"
}

Write-Host "在 DGHub 里：插件中心 -> 外部插件 -> 导入 zip"
