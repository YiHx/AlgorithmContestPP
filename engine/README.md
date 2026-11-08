# OJHC Engine（M1）

命令行引擎。M1 只做**设备链路 + 校准**，比赛会话（`run`）在 M2 接 CF 适配器后加入。

## 安装依赖

```powershell
pip install websockets
```

> 只用这一个第三方依赖；其余全是标准库。`websockets` 只在真正连 DGHub 时需要，
> 跑 `selftest` / `calibrate --dry-run` 不需要。

## 五个命令

```powershell
cd engine

# 1) 自检：DGHub 端口 / 握手 / 设备 / 校准档案 / 冒烟插件
python ojhC.py doctor

# 2) 校准向导（需要 DGHub 在运行 + 设备已连接）
python ojhC.py calibrate

# 3) 离线干跑（不输出任何电流，用脚本答案自动走完，便于测试流程）
python ojhC.py calibrate --dry-run --fast --script "y,n,n,n,y,y,y,y,y,y,y,y,n,y"

# 4) 看当前档案与档位映射
python ojhC.py status

# 5) 会话回放（M2）：离线夹具跑完整流水线，打印"如果启用设备会打多强"
python ojhC.py run --mock --rating 1400
python ojhC.py run --mock --preset hardcore --json
python ojhC.py run --contest 1847 --handle <你的handle>   # live（需要网络）

# 6) 纯本地自检（不联网）
python ojhC.py selftest
```

## M2 的 `run` 做了什么

```
夹具/CF API → 提交去重与终态判定 → 压力引擎（§5）→ 离散事件
           → 感受等级 s → 校准映射 → 闸门模拟（§7）→ 终端打印 + 总结
```

- `--mock`：读 `fixtures/cf_1847_mock.json`（一场写死的 120 分钟比赛时间线），零网络；
- `--contest/--handle`：走真实 CF 官方 API（`contest.status` + `contest.standings`，全局限流 1 次/2 秒）；
- 没有校准档案时自动用**演示档案**（I_p=20% / I_c=45%）并在输出里标注——只为审阅流水线；
- `would-fire` 是**模拟**：闸门通过 ≠ 真的输出，真正调用 DGHub `trigger` 在 M4 接。

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
