# 算法竞赛++

[![Release](https://img.shields.io/github/v/release/YiHx/AlgorithmContestPP)](https://github.com/YiHx/AlgorithmContestPP/releases/latest)
[![MIT License](https://img.shields.io/github/license/YiHx/AlgorithmContestPP)](LICENSE)
[![CI](https://github.com/YiHx/AlgorithmContestPP/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/YiHx/AlgorithmContestPP/actions/workflows/ci.yml)

[![Moe Counter 访问计数](https://count.getloli.com/get/@YiHx-AlgorithmContestPP?theme=rule34)](https://github.com/journey-ad/Moe-Counter)

**简体中文** · [English](README.en.md)

算法竞赛++ 把 OJ 的判题结果接到 DGHub。刷题时收到 WA、TLE 或 RE，郊狼可以给一次短反馈。也可以关掉设备输出，只在终端看判题和赛后记录。

## 安装

准备一台 Windows 电脑，装好 [DGHub](http://dghub.top/) 和 Python 3.10 或更新版本。用 PowerShell 在仓库目录执行下面两行，装完重开 DGHub。

```powershell
python -m pip install -r requirements.txt
.\starter\install_plugin.ps1 -Plugin ojhc_link
```

从 [GitHub Releases](https://github.com/YiHx/AlgorithmContestPP/releases) 下载插件 ZIP 时，可以在 DGHub 的插件中心直接导入，省掉第二行命令。引擎仍从仓库或 Release 的源码包取得，放到本机后在插件配置里选 `engine` 目录。

牛客、AtCoder 和洛谷需要浏览器扩展。Codeforces 装了扩展可以走快速通道。Chrome、Edge 和 Firefox 共用仓库里的 `extension` 目录，按你的浏览器选对应的[安装步骤](extension/README.md#安装浏览器扩展)。

如果不知道从哪一步开始，可以把[仓库链接](https://github.com/YiHx/AlgorithmContestPP)复制到 DSH（DeepSeek Harness），告诉大肥鱼你用的是哪个浏览器，请它按 README 帮你安装。仓库链接打不开时，也可以把已经解压的源码目录交给它。

## 开一场

1. 在 DGHub 的插件中心打开「算法竞赛++」的配置页。
2. 在「Engine 启动器」填比赛网址和该平台的用户名，打开自动启动。引擎目录找不到时，填本仓库的 `engine` 文件夹。
3. 第一次跑先关闭「带设备输出」，启用插件。使用扩展的平台要登录并保持同一场比赛的页面打开。
4. 等引擎开始接收数据，再交一发新的提交。使用 CF 快速通道时，终端会先显示「采集已建立基线」，之后收到的新判题会带 `[fast]`。
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
