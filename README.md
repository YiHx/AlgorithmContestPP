# 算法竞赛++

[![Release](https://img.shields.io/github/v/release/YiHx/AlgorithmContestPP)](https://github.com/YiHx/AlgorithmContestPP/releases/latest)
[![MIT License](https://img.shields.io/github/license/YiHx/AlgorithmContestPP)](LICENSE)
[![CI](https://github.com/YiHx/AlgorithmContestPP/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/YiHx/AlgorithmContestPP/actions/workflows/ci.yml)

[![Moe Counter 访问计数](https://count.getloli.com/get/@YiHx-AlgorithmContestPP?theme=rule34)](https://github.com/journey-ad/Moe-Counter)

**简体中文** · [English](README.en.md)

算法竞赛++ 是一款 DGHub 插件，可根据在线评测平台的判题结果触发郊狼设备反馈。WA、TLE 和 RE 均可触发短脉冲。关闭「带设备输出」后，可在终端查看判题结果，并在赛后查看会话记录。

## 安装

先在 Windows 上装好 [DGHub](http://dghub.top/) 和 Python 3.10 或更新版本。

1. 到 [GitHub Releases](https://github.com/YiHx/AlgorithmContestPP/releases/latest) 下载 `algorithmcontestpp-dghub.zip`，在 DGHub 的插件中心导入。
2. 同一页下载 `Source code (zip)` 并解压。在解压后的源码目录打开 PowerShell，运行 `python -m pip install -r requirements.txt`。
3. 回到插件配置，把引擎目录指向解压后的 `engine` 文件夹。

已克隆仓库的用户也可以在仓库根目录运行 `.\starter\install_plugin.ps1 -Plugin ojhc_link` 安装插件，随后重启 DGHub。

牛客、AtCoder 和洛谷需要浏览器扩展。Codeforces 默认可通过官方 API 获取判题结果，安装扩展后可使用快速通道。Chrome、Edge 和 Firefox 共用源码中的 `extension` 目录，具体操作见[浏览器扩展安装说明](extension/README.md#安装浏览器扩展)。

需要安装协助时，可以将仓库链接和所用浏览器提供给 DSH（DeepSeek Harness）中的大肥鱼，请它按本文操作。也可以直接提供解压后的源码目录。

## 启动比赛会话

1. 在 DGHub 的插件中心打开「算法竞赛++」的配置页。
2. 在「Engine 启动器」填比赛网址和该平台的用户名，打开自动启动。引擎目录找不到时，填本仓库的 `engine` 文件夹。
3. 第一次跑先关闭「带设备输出」，启用插件。使用扩展的平台要登录并保持同一场比赛的页面打开。
4. 等引擎开始接收数据后，再提交一次。使用 Codeforces 快速通道时，终端会先显示「采集已建立基线」，之后通过扩展收到的新判题会标记为 `[fast]`。
5. 想用设备反馈时，先在引擎窗口按 Ctrl+C 结束试跑，再停用插件。连上设备、打开「带设备输出」后重新启用插件。等终端显示 `[device] 插件就绪`，再交新提交。

练习已经结束的比赛时，可以勾选「虚拟参赛」，让引擎从启动时开始计时。关掉引擎窗口后，改比赛网址会重新打开终端。浏览器扩展的弹窗能查看当前比赛和上次成功采集的时间。

## 支持的平台

| 平台 | 判题 | 榜单 | 验证情况 |
| --- | --- | --- | --- |
| Codeforces | 有。可用扩展快速通道，官方 API 兜底 | 有 | 已用 V3 模式验证真机联动 |
| VJudge | 有 | 有 | 已验证公开比赛回放，待正式比赛实测 |
| 牛客 | 有。扩展在待判时加速采集 | 暂未接入 | 已在重现赛抓到新 WA，设备输出路径通过模拟验证 |
| AtCoder | 有。需要扩展 | 有 | 已用模拟页面验证，待登录后实测 |
| 洛谷 | 暂未接入 | 有。需要扩展 | 目前只能看榜单，WA 不会触发设备 |

本项目通过 DGHub 插件连接设备。当前真机测试用的是 DGHub 的 V3 模式，V4 还没做过端到端测试。
各浏览器的验证状态、实测记录和设备输出限制见 [验证说明](VALIDATION.md)。

## 反馈规则

默认用简单模式。WA、TLE、RE 和长时间没有 AC 都可能触发反馈，AC 会重置未 AC 的计时和递增档位。只想收到判题反馈时，可以关掉「长时间未 AC 时也反馈」。需要结合排名和做题进度时，可以切到压力模式。

默认全局冷却是 10 秒，同类事件间隔是 30 秒。设备输出还受频率配额、插件上限和手机 App 上限限制。比赛历史提交用来建立采集基线，启动后只处理新结果。

## 命令行

想先看效果，可以用仓库自带的比赛样例跑一遍。下面的回放只打印事件。

```powershell
cd engine
python ojhc.py run --mock
```

实际比赛也可以直接用命令行跑。把示例网址和用户名换成自己的。

```powershell
python ojhc.py run --url https://codeforces.com/contest/2280 --handle YOUR_HANDLE --virtual
python ojhc.py sessions
python ojhc.py report --session 1
```

`report` 的编号从 `sessions` 输出里找。更多参数和回放用法见 [引擎文档](engine/README.md)，扩展安装见 [扩展文档](extension/README.md)，实现细节见 [设计文档](DESIGN.md)。

这是个人项目，与 DGHub 和 DG-Lab 没有官方隶属关系。代码采用 [MIT 许可](LICENSE)。
