# OJ Hardcore ⚡ — OJ × DGHub

> 把「WA 只有一声提示音」变成「你真的能感觉到」。
> *Turn your Wrong Answers into something you can feel.*

**状态**：alpha（M1：设备链路 + 校准已实现，Codeforces 适配器在 M2）· **仅 Windows** · **非官方项目**

---

## ⚠️ 先读安全声明

本项目会驱动电刺激设备（DG-Lab 郊狼 / Coyote），请务必确认：

- **默认 `dry-run`**：没有完成校准、没有显式打开输出时，绝不会输出电流；
- **不要无人看管使用**；随时准备急停（关闭插件 / 面板急停 / 直接拔设备）；
- 这是**娱乐与训练辅助工具，不是医疗设备**，不用于任何医疗目的；
- 本项目是个人**非商业**开源项目，与 **DG-Lab、DGHub 官方无隶属关系**；
- 使用者需自行评估身体状况与风险，**使用后果自负**。

---

## 这是什么

算法竞赛的反馈回路太弱了：一道题写很久，提交一次，WA 了，然后……只有一行红色文字。

**OJ Hardcore** 把「比赛/刷题时发生的事」翻译成**可感知**的反馈：实时监听 OJ 的提交判题与榜单表现，算出一个「竞赛压力分」，再按一套严格的安全规则，决定是否、以及多强地驱动郊狼设备。

它不是「WA 就电一下」的玩具，而是有明确安全边界的三层结构：

```
OJ（Codeforces ...） → Contest Engine（压力分 + 安全闸门） → DGHub 插件 → 郊狼设备
```

## 工作原理（两条铁律）

1. **压力分永远不直接驱动设备**。分数是连续量、必然抖动；它只决定「允许的强度上限」和「门槛」。
2. **只有离散事件才能触发**：WA / TLE / RE、超时未提交、超时未 AC、等级跨越。可归因、可去重、可审计。

```
提交 → 判题事件 → 感受等级 s（0~5） → 个人校准区间映射 → 冷却/令牌桶/硬上限 → DGHub trigger
```

设备输出前必须通过 **11 条闸门**：已校准、会话进行中、非 dry-run、数据新鲜、无急停、令牌桶/冷却通过、等级 ≥ L1、强度 > 0、数据源可信、不在解封跳变窗口……任何一条不满足都只展示不输出。

## 现在能做什么 / 还不能做什么

| 能力 | 状态 |
|---|---|
| DGHub 冒烟插件（`starter/ojhc_probe`） | ✅ 代码完成，可 CLI 安装/导入 zip |
| 引擎 CLI：`doctor` / `calibrate` / `status` / `selftest` | ✅ 干跑验证通过（真机待验证） |
| 手感校准（测 `I_p` / `I_c`，映射档位） | ✅ 已实现 |
| 压力分引擎（4 子分 / 平滑 / 滞回） | ✅ 已实现，与 DESIGN.md §5.6 数值一致 |
| 离线回放 + 闸门模拟（`ojhc run --mock`） | ✅ 已实现（打印 would-fire） |
| Codeforces 实时判题/榜单 | 🟡 live 代码路径已写，未在真实网络验证 |
| 真实设备输出（DGHub `trigger`） | ⛔ M4（当前全部 dry-run） |
| 多平台（VJudge / AtCoder / 洛谷 / 牛客） | ⛔ 第二阶段 |
| Linux / macOS | ⛔ 不支持（DGHub 主程序是 Windows 应用） |

> 诚实说明：**目前没有任何一次真实设备验证**。代码按 DGHub 协议文档实现，正在等真机校准。

## 快速开始

前置：Windows 10/11、[DGHub 2.0](http://dghub.top/)、郊狼设备、Python 3.11+。

```powershell
# 1) 安装冒烟插件（纯 CLI，自动找 DGHub 的 plugins 目录）
.\starter\install_plugin.ps1

# 2) 重启 DGHub，在插件中心启用一次「OJHC 冒烟探针」

# 3) 引擎自检
pip install websockets
cd engine
python ojhC.py doctor

# 4) 校准（需要设备已连接；随时 Ctrl+C 安全中止）
python ojhC.py calibrate

# 5) 查看校准结果
python ojhC.py status
```

## 仓库结构

```
engine/          引擎 CLI（doctor / calibrate / status / run）+ 夹具
starter/         冒烟插件 ojhC_probe + 安装脚本 + 上手步骤
examples/        压力算法参考实现（可跑出与文档一致的数值）
research/        平台接口与 DGHub 协议的逐条事实核查（带 URL/置信度）
DESIGN.md        设计文档 v0.3.x（算法 / 安全 / 手感 / 里程碑）
```

## 设计文档

完整设计在 **[DESIGN.md](DESIGN.md)**，重点章节：

- §0 摘要与 8 个关键决策
- §5 竞赛压力算法（4 子分 + 期望模型 + 平滑/滞回）
- §7 反馈策略与安全闸门（11 条闸门 / 冷却 / 升压禁止 / 死手开关）
- §7.9 手感设计与校准（解决"没感觉"）
- §9 DGHub 协议事实与 CLI-first 工作流

## 路线图

| 里程碑 | 内容 |
|---|---|
| M0 | 骨架 + SQLite + CF 适配器 |
| **M1（已完成代码，待真机）** | 设备链路 + 校准 + 急停/死手 |
| **M2（当前）** | CF 适配器 + 压力引擎 + `ojhc run` 回放链路 |
| M3 | 会话面板 + 计时器 + 赛后报告 |
| M4 | 事件→强度映射 + casual/hardcore 预设 + 真实 `trigger` |
| M5 | 安全测试清单 + 混沌测试 |
| M6 | 多平台（VJudge / AtCoder / 洛谷 / 牛客） |

## 贡献与反馈

早期 alpha 阶段，欢迎 issue 讨论设计取舍（尤其是安全规则）。请不要在未读 `DESIGN.md` 安全章节的情况下直接提"调大强度"类 PR。

## 许可与致谢

- 本仓库计划采用 **AGPL-3.0**（与 DGHub SDK 生态一致；如无异议以此为准）。
- 感谢 [DGHub](http://dghub.top/) 与 [DG-Lab](https://github.com/DG-LAB-OPENSOURCE) 提供设备与插件生态，感谢 Codeforces 公开 API。
- 项目名称中的 "DGHub" 仅表示集成对象，不代表官方关系。
