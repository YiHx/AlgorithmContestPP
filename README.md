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
| 多平台适配层（五平台 URL 识别） | ✅ P1/P2：CF、VJudge 原生；AtCoder/洛谷/牛客 走扩展 |
| VJudge：判题 + 榜单 | ✅ P1：判题接口实测；榜单按 `rank/single` 自算（封榜启发式） |
| AtCoder：判题 + 榜单 | 🟡 P2 扩展取数（登录态）；未真机验证 |
| 洛谷：榜单 | 🟡 P2 扩展取数（成绩表分页找自己）；判题事件待 `record/list` 状态映射核实 |
| 牛客：判题 | 🟡 P2 扩展取数（`status-list`）；**无实时榜单** → 自动降级 |
| 浏览器扩展（`extension/`，MV3） | ✅ 代码完成，未在真实浏览器验证 |
| SQLite 会话历史 + 赛后复盘（`sessions` / `report`） | ✅ 已实现 |
| 离线回放 + 闸门模拟（`ojhc run --mock`） | ✅ 已实现（打印 would-fire） |
| 真实设备输出（`run --device` + `starter/ojhc_link`） | ✅ **真机验证通过（2026-11-08）**：引擎→插件→DGHub→App→设备，30~34 强度可感知；租约/急停/归零生效 |
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
engine/          引擎 CLI（doctor / calibrate / status / run / sessions / report）+ 夹具
extension/       Chrome MV3 扩展：AtCoder / 洛谷 / 牛客 登录态页面取数 → 转发本机引擎
starter/         冒烟插件 ojhC_probe + 安装脚本 + 上手步骤
examples/        压力算法参考实现（可跑出与文档一致的数值）
research/        平台接口与 DGHub 协议的逐条事实核查（含 v2 比赛场景实测）
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

- 本仓库采用 **MIT License**（见 [LICENSE](LICENSE)）。可自由使用、修改、分发（含商业用途），请保留版权声明；使用风险自负。
- 本仓库**没有** import / vendor DGHub SDK 或官方 demo 代码（协议按公开文档独立实现），因此不继承 AGPLv3；若以后引入 AGPL 代码会单独说明。
- DG-Lab 公开的协议文档写明"商业使用需授权"，本仓库不包含其文档或示例代码，仅做非商业用途参考。
- 感谢 [DGHub](http://dghub.top/) 与 [DG-Lab](https://github.com/DG-LAB-OPENSOURCE) 提供设备与插件生态，感谢 Codeforces 公开 API。
- 项目名称中的 "DGHub" 仅表示集成对象，不代表官方关系。

## 真机验收清单（交付给设备持有者）

代码已全部推送（v0.3.9），下面是**你需要亲自验证的部分**（我这边无设备/无外网，无法替代）：

### A. 设备链路（最关键）

```powershell
# 1) 装生产插件并重启 DGHub
.\starter\install_plugin.ps1 -Plugin ojhc_link
# 2) 校准（3~5 分钟，出 I_p / I_c）
cd engine
python ojhC.py doctor
python ojhC.py calibrate
# 3) 用回放数据做一次"真输出"演练（不需要真比赛）
python ojhC.py run --mock --device --max-run-min 1
```

预期：插件连上 Engine、租约刷新；回放里通过闸门的事件会真的输出（约 4 次，26~28% / 0.8~1.2s）；
中途 Ctrl+C → 插件立即归零并退出。

### B. 各平台真实比赛

| 平台 | 命令 | 预期 |
|---|---|---|
| CF | `python ojhC.py run --url https://codeforces.com/contest/<id> --handle <you> [--device]` | 实时判题与榜单 |
| VJudge | `python ojhC.py run --url https://vjudge.net/contest/<id> --handle <you> [--device]` | 判题实时；榜单自算 |
| AtCoder | `python ojhC.py run --url https://atcoder.jp/contests/<id> --handle <you>` + 扩展 | 登录态下判题+榜单；默认不出电 |
| 洛谷 | `python ojhC.py run --url https://www.luogu.com.cn/contest/<id> --handle <you>` + 扩展 | v1 只上报榜单（判题待 record/list 映射） |
| 牛客 | `python ojhC.py run --url https://ac.nowcoder.com/acm/contest/<id> --handle <you>` + 扩展 | 判题事件；无榜单（自动降级） |

### C. 回归测试

```powershell
cd engine
python -m unittest discover -s tests -v   # 19 例，应全 OK
python ojhC.py selftest
python ojhC.py sessions
python ojhC.py report --session <id>
```

### D. 已知限制（不是 bug，先记录）

- 洛谷 v1 只有榜单、没有判题事件（`record/list` 状态码映射未核实，不猜）；
- 牛客没有榜单接口（只有提交信号）；
- AtCoder 封榜按场次声明，`Frozen` 标记来自第三方整理的字段，可能漂移；
- VJudge 封榜无法从 JSON 判定，用"末 60 分钟保守视为封榜"的启发式；
- CF/VJudge live 模式未在本机网络下验证过（开发沙箱无外网），首次跑请留意报错并回报。
