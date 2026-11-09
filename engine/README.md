# 算法竞赛++ Engine

命令行引擎：五平台比赛监听（CF/VJudge 原生、AtCoder/洛谷/牛客桥接）、压力算法 / 简单模式、DGHub 设备链路。

## 安装依赖

```powershell
# 在仓库根目录执行
python -m pip install -r requirements.txt
```

> 只用这一个第三方依赖；其余全是标准库。`websockets` 只在真正连 DGHub 时需要，
> 跑 `selftest` / `calibrate --dry-run` 不需要。

## 常用命令

```powershell
cd engine

# 环境与校准
python ojhC.py doctor                 # DGHub 端口 / 握手 / 设备 / 档案 / 插件
python ojhC.py calibrate              # 校准向导（需要设备已连接）
python ojhC.py calibrate --dry-run --fast --script "y,n,n,n,y,y,y,y,y,y,y,y,n,y"
python ojhC.py status                 # 档案与档位映射

# 比赛回放 / 实跑（多平台）
python ojhC.py run --mock --rating 1400          # 内置 CF 夹具，零网络
python ojhC.py run --url https://codeforces.com/contest/1847 --handle tourist
# 已结束的比赛用倍速重放（不访问实时时钟）：
python ojhC.py run --url https://codeforces.com/contest/1847 --handle tourist --replay-speed 60
python ojhC.py run --platform vjudge --contest 654321 --handle tourist --duration-min 120
python ojhC.py run --url https://atcoder.jp/contests/abc300 --handle tourist   # 桥接模式
python ojhC.py run --url https://ac.nowcoder.com/acm/contest/12345 --handle 1234567

# 历史与复盘
python ojhC.py sessions               # 历史会话列表
python ojhC.py report --session 3     # 事件时间线 + 压力曲线 + 降级信息

# 真实设备输出（M4，需真机校准档案 + DGHub 里启用 「算法竞赛++」插件）
python ojhC.py run --url https://codeforces.com/contest/1847 --handle tourist
# 已结束的比赛用倍速重放（不访问实时时钟）：
python ojhC.py run --url https://codeforces.com/contest/1847 --handle tourist --replay-speed 60 --device
python ojhC.py run --mock --device --max-run-min 1     # 演示档案会被拒绝（安全）

# 自检
python ojhC.py selftest               # 不联网，覆盖压力/闸门/回放/存储/桥接/设备链路
```

## 设备输出链路（M4）

```
Engine（--device）
  ├─ 127.0.0.1:8768 TCP JSONL 服务；写 ~/.ojhc/device_link.json {host,port,token}
  └─ 闸门通过 → fire(lease_id, nonce, intensity, duration) → 等 ack
        ▲
        │ lease 每 2s 续期（ttl 5s）；estop 立即归零
        ▼
starter/ojhc_link（DGHub 插件）
  ├─ 校验 lease/nonce/强度/时长/设备在线
  ├─ DGHub trigger（rollback，绝不用 permanent）
  └─ 租约过期 1.35× / Engine 断开 / DGHub stop / 自身退出 → set_strength 0
```

安全要点：
- `--device` 必须配合**真实校准档案**（演示档案直接拒绝）；
- 牛客和 AtCoder 扩展支持 `--device`（DGHub 插件页的「带设备输出」）。洛谷目前只接榜单；
- 会话结束/异常强制 estop；审计日志记录 `would_fire` 与 `allowed` 两列。

## 浏览器桥接（AtCoder / 洛谷 / 牛客）

这三个平台没有匿名公开接口，走 `extension/` 在**已登录的页面**里取数，POST 到本机桥接服务：

```
比赛页面（登录态） → extension/content.js 采集
        → extension/background.js POST http://127.0.0.1:8765/ingest/*
        → engine/ojhc_lib/bridge.py → BridgeAdapter → 压力引擎
```

- `run --url <比赛URL>` 会自动识别平台并启动桥接（打印端口与步骤）；
- 牛客和 CF 扩展平时每秒采集一次，待判时短时加速。AtCoder 和洛谷保持 3 秒采集间隔；
- 牛客和 AtCoder 的本人判题在显式启用设备输出后可触发设备。洛谷尚无判题事件；
- 安装步骤与平台限制见 [`extension/README.md`](../extension/README.md)。

## 多平台适配层（P1）

```
engine/ojhc_lib/platforms/
    base.py        ContestMeta / Submission / Standings / Capabilities / PlatformAdapter
    codeforces.py  CF 官方 API（A 级，实时判题 + 榜单 + 封榜）
    vjudge.py      VJudge 非官方接口（B 级，实时判题；榜单接口待核实 → 降级）
    mock.py        离线回放夹具（任何平台都能用同一套接口）
```

引擎只依赖 `base.py` 的四个模型，不认识具体 OJ。平台能力缺失时自动降级，
并在 `summary["degraded"]` 与 `report` 里明示——不猜数据。

- 平台识别：`--url` 支持五平台 URL（CF / VJudge / AtCoder / 洛谷 / 牛客）；
  AtCoder/洛谷/牛客的适配器在 P2（浏览器扩展）实现；
- VJudge 时长拿不到 → `--duration-min` 兜底；榜单接口未核实 → 只用提交信号算压力；
- 所有会话自动写 SQLite（默认 `~/.ojhc/ojhc.db`，不可写时回退 `engine/.local/`）。

## M2 的 `run` 做了什么

```
夹具/平台 API → 提交去重与终态判定 → 压力引擎（§5）→ 离散事件
             → 感受等级 s → 校准映射 → 闸门模拟（§7）→ 终端打印 + 存储 + 总结
```

- `would-fire` 是**模拟**：闸门通过 ≠ 真的输出，真正调用 DGHub `trigger` 在 M4 接；
- 没有校准档案时自动用**演示档案**（I_p=20% / I_c=45%）并在输出里标注。

## 已知限制（M2）

- `live` 代码路径已写，但**还没在真实网络下跑过**（开发沙箱无外网）；
- 夹具里的榜单是手写快照，起始名次偏高（真实比赛开局名次会随 AC 快速变化）；
- 罚时/penalty 尚未参与压力分，目前只看 rank / solved / 提交结果；
- 回放是"事件驱动"的：时间型事件在每次提交时和比赛结束时检查，不按真实秒表滴答。

## 校准做了什么

对应 `DESIGN.md` §7.9.1：

```
Step 0  连接检查（设备必须在线；dry-run 时跳过）
Step 1  找感知阈值 I_p：5% 起每档 +5%，脉冲 0.8s，确认 3 次取 2 次
Step 2  找舒适上限 I_c：继续升档，直到"不适"；必须满足 I_c − I_p ≥ 15%
Step 3  算本次上限 user_max = min(I_c×0.8, 60%)，并要求 user_max − I_p ≥ 10%
Step 4  手感预览 s=2/3/4 三档，确认能分辨强弱
Step 5  写入 ~/.ojhc/device_profile.json
```

**为什么有第 3 步的 10% 条件**：`user_max` 是安全余量，映射区间用的是 `[I_p, user_max]`。
如果 `user_max − I_p` 太窄，高档位会被压成同一个值，手感就没有层次了（这是干跑时实测到的坑）。

## 档案格式

`~/.ojhc/device_profile.json`：

```json
{
  "version": 1,
  "calibrated_at": "2026-11-08T13:38:50+08:00",
  "device_type": "v3",
  "channel": "both",
  "perceptible_pct": 20,
  "comfort_pct": 45,
  "user_max_pct": 36,
  "preset": "",
  "notes": ""
}
```

档位映射（`frac` 对应感受等级 s）：`s=2 → 0.35`、`s=3 → 0.50`、`s=4 → 0.65`、`s=5 → 0.85`。

## 安全约定（M1 已生效）

- 校准期脉冲上限 **50%**、时长 **0.8s**、间隔 ≥3s；
- 只用 `strength_mode: rollback`，**永不用 permanent**，脉冲结束自动回正；
- 强度/时长在客户端再钳一次（`HARD_CAP_PCT=60`、`MAX_PULSE_S=3`）；
- `Ctrl+C` 中止：不写档案、不留下持续输出；
- 校准档案 30 天后提示重测。

## 已知限制

- DGHub 端口自动探测只扫常见端口（27020~27023、27800）；找不到时用 `--port` 指定；
- 设备未连接时校准会拒绝开始（返回码 3）；
- 死手开关（lease）留给 M1 正式桥接阶段，当前 `calibrate` 是"人在旁边盯着"的交互式流程。

## 测试

```powershell
cd engine
python -m unittest discover -s tests -v    # 19 例，纯标准库
python ojhC.py selftest                    # 冒烟级自检（含设备链路）
```

## 校准注意（真机）

`calibrate` 必须带波形预设，否则 DGHub 只改强度、设备不出波形（表现为"50% 也没感觉"）：

```powershell
python ojhC.py calibrate                # 默认用「呼吸」，会从 DGHub /api/pulse-presets 校验
python ojhC.py calibrate --preset 潮汐  # 换一个内置预设
```

可先确认波形能下发：

```powershell
# 打一发 4 强度的「呼吸」测试脉冲（约 1 秒）
python -c "import json,urllib.request;req=urllib.request.Request('http://127.0.0.1:8000/api/test-fire',data=json.dumps({'action':'both','strength':4,'duration':1.0,'preset':'呼吸','channel':'both','strength_mode':'rollback'}).encode(),method='POST',headers={'Content-Type':'application/json'});print(urllib.request.urlopen(req,timeout=8).read().decode())"
```

若仍无感：先看 DGHub 显示的设备上限（`max_a`），在 DG-Lab App 里把「上限」适当调高再校准。

> 用 `--replay-speed` 重放已结束比赛时，`--handle` 必须是**真的参加过那场比赛**的账号，否则会取到 0 条提交。

> **百分比与绝对强度**：DGHub 的 `delta_pct` / `strength` 都是「设备上限的百分比」。
> 例如设备上限 40 时，50% = 绝对强度 20。校准会显示每一步的绝对强度估算并把上限写进档案，
> `status` 里也会标注（≈N）。如果觉得整体太弱，先在 DG-Lab App 里把上限调高再重新校准。

## 无校准模式（直接用 App 上限）

不想做校准？加 `--trust-app-limit`：引擎直接读 DG-Lab App 里的强度上限，按它的
65%~90% 映射（90% 是代码硬顶，绝不会超过你在 App 里设的上限）：

```powershell
python ojhC.py run --mock --device --trust-app-limit --max-run-min 1
python ojhC.py run --url https://codeforces.com/contest/2267 --handle xuanh08 --replay-speed 60 --device --trust-app-limit
```

映射结果会在启动时打印（`s2≈…% … 绝对强度 ≈N~M`）。觉得整体弱/强，告诉我要调整的比例即可。

> **Mock 演练要带节奏**：`--mock` 默认瞬时回放，多个脉冲会在同一毫秒连发、互相打断（真机听起来像没输出）。
> 加 `--replay-speed 2000`（2000 倍速）让事件间隔约 1~3 秒，才和真实比赛相近：
> ```powershell
> python ojhC.py run --mock --device --trust-app-limit --replay-speed 2000
> ```


## 在 DGHub 插件页面直接调参（推荐）

「算法竞赛++」插件配置页可直接调整（改完即时生效，Engine 会打印"插件页面调参已生效"）：

| 配置 | 默认 | 范围 | 作用 |
|---|---|---|---|
| **电击模式** | **简单：没 AC 才电** | 简单 / 压力算法 | 简单=只看距上次 AC 多久；压力算法=完整算法 |
| **多久没 AC 电一次** | 20 分钟 | 5~120 | 仅简单模式生效 |
| **没 AC 电击递增** | 5% | 0~20 | 每次没 AC 比上次强这么多，递增到「本插件硬上限」为止 |
| 基础强度（s=2 档） | 74% | 40~90% | 占 App 上限的百分比 |
| 每档递增 | 4% | 0~10% | s 每升一档加多少 |
| 最高档位 s | 4 | 2~5 | 允许的最高严重度 |
| 波形预设 | 呼吸 | DGHub 预设列表 | 手感 |
| 两次电击最小间隔 | 10s | 10~300s | 全局冷却 |
| 每小时最多次数 | 30 | 5~60 | 配额 |
| 等级上升时也电一下 | 关 | 开/关 | level_up 事件 |
| 本插件硬上限 | 90% | 0~90% | 代码级兜底，配置改不动 |

安全硬边界（急停/租约/归零/App 上限）不受这些配置影响。

## 从 DGHub 插件页直接启动引擎（不用开终端）

「算法竞赛++」插件配置页最上面新增 **Engine 启动器**：

| 配置 | 说明 |
|---|---|
| 自动启动引擎 | 开→插件启动/保存配置时自动弹出终端并运行 `ojhC.py run` |
| 比赛 URL | 例如 `https://codeforces.com/contest/1234` |
| 你的 OJ 用户名 | 例如 `xuanh08` |
| 带设备输出 | 关=只记录（dry-run）；开=真实输出 |
| 虚拟参赛 | 开→加 `--virtual`（从现在开始计时） |
| 引擎目录 | 留空自动查找；填 `ojhC.py` 所在目录 |
| Python | 默认 `python`，找不到可填完整路径 |

插件会在新终端窗口里启动引擎、读取它的输出；插件停用时也会终止自己启动的引擎。
