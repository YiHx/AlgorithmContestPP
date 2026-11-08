# 纯 CLI 安装：把 ojhc_probe 目录直接拷进 DGHub 的 plugins/ 目录
#
# 用法：
#   .\install_plugin.ps1                          # 自动探测 DGHub 安装位置
#   .\install_plugin.ps1 -PluginsDir "C:\...\DGHub\plugins"
#   .\install_plugin.ps1 -DryRun                  # 只打印探测结果，不复制
#
# 说明：协议文档写明「把插件目录放进 plugins/ 后重启 DGHub，即可在插件中心看到」，
#       所以 zip 导入是可选的；本脚本就是给习惯终端的人用的。
param(
    [string]$PluginsDir,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src  = Join-Path $here "ojhc_probe"

if (-not (Test-Path (Join-Path $src "manifest.json"))) {
    throw "找不到 $src\manifest.json"
}

function Get-Candidates {
    $c = [System.Collections.Generic.List[string]]::new()

    # 1) 正在运行的 DGHub 进程所在目录
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -match "DGHub|DG-Lab|DGLab" } |
        ForEach-Object {
            try {
                if ($_.Path) { $c.Add((Join-Path (Split-Path -Parent $_.Path) "plugins")) }
            } catch { }
        }

    # 2) 环境变量
    foreach ($name in @("DGHUB_HOME", "DGHUB_PLUGIN_DIR", "DGHUB_DIR")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) {
            $c.Add($(if ($name -eq "DGHUB_HOME") { Join-Path $value "plugins" } else { $value }))
        }
    }

    # 3) 常见安装位置
    $roots = @(
        (Join-Path $env:LOCALAPPDATA "DGHub"),
        (Join-Path $env:APPDATA "DGHub"),
        (Join-Path $env:ProgramFiles "DGHub"),
        (Join-Path ${env:ProgramFiles(x86)} "DGHub")
    )
    foreach ($root in $roots) {
        if ($root) { $c.Add((Join-Path $root "plugins")) }
    }

    # 4) 盘符根下的 DGHub 目录（便携版常见）
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem).Root) {
        foreach ($name in @("DGHub", "dghub")) {
            $p = Join-Path $drive $name
            if (Test-Path $p) { $c.Add((Join-Path $p "plugins")) }
        }
    }

    # 去重：Windows 路径大小写不敏感，DGHub / dghub 会重复出现
    $seen = @{}
    $unique = foreach ($p in $c) {
        $key = $p.ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $p
        }
    }
    return $unique
}

if (-not $PluginsDir) {
    $found = @(Get-Candidates | Where-Object { Test-Path $_ })
    if ($found.Count -eq 0) {
        Write-Host "没自动找到 DGHub 的 plugins 目录。" -ForegroundColor Yellow
        Write-Host "请确认 DGHub 已安装并至少运行过一次，然后手动指定："
        Write-Host "  .\install_plugin.ps1 -PluginsDir `"<DGHub安装目录>\plugins`""
        exit 1
    }
    if ($found.Count -gt 1) {
        Write-Host "找到多个候选目录，请用 -PluginsDir 指定一个：" -ForegroundColor Yellow
        $found | ForEach-Object { Write-Host "  $_" }
        exit 1
    }
    $PluginsDir = $found[0]
}

Write-Host "目标目录: $PluginsDir"
if ($DryRun -or -not (Test-Path $PluginsDir)) {
    if (-not (Test-Path $PluginsDir)) { Write-Host "目录不存在（DryRun 模式只做探测）" -ForegroundColor Yellow }
    Write-Host "DryRun：已找到目标，未复制。"
    exit 0
}

$target = Join-Path $PluginsDir "ojhc_probe"
if (Test-Path $target) {
    $backup = "$target.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Write-Host "已存在旧版本，备份到: $backup"
    Move-Item $target $backup
}

Get-ChildItem -Path $src -Force | Where-Object {
    $_.Name -notin @("__pycache__") -and $_.Extension -ne ".pyc"
} | ForEach-Object {
    Copy-Item $_.FullName -Destination (New-Item -ItemType Directory -Path $target -Force) -Recurse -Force
}

Write-Host "已安装到: $target" -ForegroundColor Green
Write-Host "下一步：重启 DGHub -> 插件中心 -> 外部插件，应出现「OJHC 冒烟探针」"
