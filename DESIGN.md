# OJ Hardcore Engine 设计文档

> 一个把「算法竞赛实时表现」翻译成「可感知反馈」的训练引擎：监听提交判题与实时榜单，计算竞赛压力分，按规则决定是否反馈、反馈多强。
>
> 版本：v0.3.8（草案）　写作时间：2026-11-08　状态：**P1/P2/P3 代码完成；五平台 + 设备链路（真机待验收）**

---

## 0. 先看这一页（给评审者的摘要）

### 0.1 这个项目到底是什么

不是「WA 就电一下」的玩具，而是一个**只为郊狼（DG-Lab Coyote）服务**的竞赛反馈引擎。设备不是可选扩展，是唯一正式输出通道：

```
Contest Engine（CF/AtCoder 数据 → 会话状态机 → 压力分 → 离散事件）
        ↓
DGHub 2.0 插件（op:"event" 展示 / trigger 输出）
        ↓
郊狼设备  ← 唯一需要"真的有感觉"的地方
```

本地终端面板与日志**只用于调试和赛后审计**，不承担"替代设备"的产品角色；"没有设备也能玩"不是本项目目标。

**设计标准只有一条**：该动的时候，那一下要真的能被感觉到；不该动的时候绝对不动。

### 0.2 八个关键设计决策（评审重点）

| # | 决策 | 理由 | 可改吗 |
|---|---|---|---|
| D1 | 压力分**永远不直接**触发电刺激，只作为"允许强度上限"和"门槛" | 分数是连续量，抖动必然发生；直接映射等于把噪声接到身体上 | 不改 |
| D2 | 只有**离散事件**才能触发反馈：WA/TLE/RE、超时未提交、超时未 AC、等级跨越 | 离散事件有明确归因，可解释、可去重、可审计 | 不改 |
| D3 | **封榜期间排名信号冻结并降权（×0.5）**，主信号转为提交/停滞 | 封榜恰好落在比赛最激烈阶段，此时排名数据不可信；清零会丢压力感，继续更新会误判 | 不改 |
| D4 | OI 赛制**降级模式**：拿不到实时判题就只做计时+提交频率，绝不猜结果 | 洛谷 OI 赛制比赛期间看不到判题，猜=误触发 | 不改 |
| D5 | **首次必须走设备校准**（测出个人感知区间），之后每场一次确认即自动输出；`dry_run` 只用于未校准或调试 | DG-Lab 的强度百分比不是感知线性的——不校准就会"设了也没感觉"或"一上来太猛" | 校准可重跑，急停不可删 |
| D6 | 期望表现用**评级模型**（CF seed 公式）而非"和别人比排名绝对值" | 900 分选手打 Div.2 落后于 2400 分选手是正常的，不该被惩罚 | 权重可调 |
| D7 | **首发只对 Codeforces 承诺完整能力**，其他平台按能力探测降级并在界面明示 | 事实核查：只有 CF 是免登录官方接口；AtCoder/牛客/洛谷全部依赖登录态或只能弱实时（§4.2） | 可扩展 |
| D8 | **感知优先**：强度按个人感知区间 `[I_p, I_c]` 映射；单次脉冲 ≥0.6s；不同事件用不同节奏区分 | "没感觉"通常只有两个原因：强度低于个人感知阈值、脉冲太短 | 节奏/时长可调 |

### 0.3 一句话的算法形态

```
压力分 P ∈ [0,100]  = 塑形( Σ wᵢ · Sᵢ )，再经 EMA 平滑 + 滞回
    S_rank      相对目标/期望排名的落后程度
    S_progress  已 AC 题数落后于"按时间应得"的程度
    S_stagnation 距上次 AC 的停滞时间
    S_recent    最近提交的成败趋势（指数衰减窗口）
```

`P` 只决定「能不能给到中/高强度」；**触发**由离散事件 + 冷却 + 令牌桶决定。

### 0.4 不做的事（非目标）

- 不做实时对战/排名预测，不做社交功能。
- 不做"无设备也能玩"的产品化——面板/音效/通知只服务于调试与审计，不是第二产品线。
- 不支持郊狼以外的设备，不做手机马达/通用震动适配。
- 不接管用户账号密码（无公开 API 的平台一律用浏览器扩展在页面上下文取数）。
- 不在比赛期间把"排名下降"当成失败判据（只作为压力分的输入之一，且经过平滑与滞回）。
- 不承诺跨平台封榜/赛制差异的"完美"处理，只承诺**明确的降级行为**与日志。

---

## 1. 使用场景与用户分层

### 1.1 三个典型场景

**场景 A：连续刷题（无榜单）**
用户在 VJudge/CF 题库连续做题 2 小时。没有榜单，但有提交节奏。
→ 只用 S_progress（按目标题数）+ S_stagnation + S_recent；计时器驱动"太久没提交"提醒。

**场景 B：参加一场 CF/AtCoder 比赛（有榜单）**
比赛 90~120 分钟，有实时榜单、封榜、实时判题。
→ 四个子分全开。封榜期间排名信号冻结并降权（见 §6.4）。

**场景 C：牛客周赛 / 洛谷比赛**
牛客没有公开实时接口（官方只有赛后 ndjson 导出），洛谷 ACM 类赛制可见判题、OI 赛制官方明确不可见。
→ 都走浏览器扩展在用户已登录的页面内取数，属 C 级数据源：可以算分、可以显示，**设备输出默认关闭**（§7.5）。洛谷 OI 赛制直接进降级模式。

### 1.2 用户分层（决定默认配置）

| 用户 | 硬件/软件状态 | 默认行为 | 前置动作 |
|---|---|---|---|
| 新手（刚装） | 有郊狼 + 已装 DGHub | 强制 dry-run，只展示不输出 | 跑校准向导（§7.9），约 3~5 分钟 |
| 常规用户 | 已校准 | 每场点一次"启用输出"后自动输出（casual 预设） | 选预设 |
| 硬核用户 | 已校准 | hardcore 预设：事件更密、强度更接近校准上限 | 自选预设，自担风险 |
| 开发调试（我） | 可以没有设备 | 显示型事件 + 回放 | 只用于验证判题准确性，**不是产品形态** |

---

## 2. 总体架构

### 2.1 分层结构

```
【数据采集层】按能力探测选择适配器（详见 §4.2）
  Codeforces  官方 API，免登录              → A 级
  AtCoder     浏览器扩展（需登录态）          → C 级
  牛客         浏览器扩展（无实时接口）        → C 级（弱实时）
  洛谷         浏览器扩展（需 cookie）         → C 级
  VJudge      非官方 HTTP `status/data`      → B 级
                          │
                          ▼
   ┌──────────────────────────────────────────┐
   │ 归一化 Normalizer                          │ 统一 rank/solved/verdict/时间
   └────────────────────┬─────────────────────┘
                        ▼
   ┌──────────────────────────────────────────┐
   │ 会话存储 SQLite                            │ sessions / submissions /
   │ （可回放、可审计）                          │ snapshots / pressure_samples / events
   └────────────────────┬─────────────────────┘
                        ▼
   ┌──────────────────────────────────────────┐
   │ Contest Engine / 规则层                    │
   │  · Session 生命周期与时钟校准               │
   │  · 提交状态机（去重、终态判定）             │
   │  · 压力引擎 PressureEngine                 │
   │  · 计时器（未提交 / 未 AC）                 │
   └────────────────────┬─────────────────────┘
                        ▼
   ┌──────────────────────────────────────────┐
   │ Feedback Policy Gate（安全闸门）            │
   │ 门槛 / 滞回 / 冷却 / 令牌桶 / 硬上限        │
   └───────┬──────────────────────┬────────────┘
           ▼                      ▼
  ┌──────────────────┐   ┌────────────────────────┐
  │ 本地反馈           │   │ DGHub 插件适配器        │
  │ 终端/TUI/音效/通知 │   │ op:"event" / trigger    │
  └──────────────────┘   └───────────┬────────────┘
                                     ▼
                             DGHub 2.0 → 设备
```

### 2.2 为什么引擎和插件要分开

1. **安全边界**：设备输出是唯一可能伤人的部分，把它压到最薄的一层，方便单独审计和关闭。
2. **可测试性**：引擎可以用历史数据回放，不需要真实设备、不需要真实比赛。
3. **受众**：只有郊狼用户（需先装 DGHub）；引擎与插件仍分包发布，便于分别审计与替换。

### 2.3 进程与通信

| 组件 | 形态 | 通信方式 |
|---|---|---|
| Engine 核心 | Python 进程（CLI 启动） | 本地 WebSocket `127.0.0.1:<port>` + 事件 JSONL |
| 浏览器扩展 | Chrome MV3 扩展 | 与 Engine 本地 WS 通信；在页面上下文 fetch |
| DGHub 插件 | Python 进程（DGHub SDK `Agent`） | 订阅 Engine 的本地 WS/事件文件 |
| 终端面板 | Textual/Rich TUI | 同进程 |

> 端口固定为 `127.0.0.1:8765`（可配），并附加每次启动随机生成的 token；扩展通过 `GET /health` 探测可用性。**不依赖"读本地文件"**——Chrome MV3 扩展读不到 `~/.ojhc/`，同时 token 也避免任意网页脚本直接连上引擎。

---

## 3. 核心概念与数据模型

### 3.1 术语

| 术语 | 含义 |
|---|---|
| Session | 一次"开始训练/开始比赛"到"结束"的会话，所有触发只在 Session 内有效 |
| Snapshot | 某一时刻的榜单快照（rank、solved、frozen 标志） |
| Submission | 一次提交及其判题状态（可能经历 PENDING → 终态） |
| Verdict | 判题结果，统一枚举：AC / WA / TLE / MLE / RE / CE / PARTIAL / PENDING / UNKNOWN |
| PressureScore | 压力分 P ∈ [0,100]，连续量，不直接触发设备 |
| FeedbackEvent | 反馈事件（display/audio/device），带 reason 与强度 |
| Epoch | 会话内的数据纪元，用于隔离"会话开始前"的旧数据 |

### 3.2 会话（Session）

```json
{
  "session_id": "20261108-cf-1847-a3f",
  "mode": "contest",              // contest | practice
  "platform": "codeforces",
  "contest_id": 1847,
  "handle": "xuanh08",
  "started_at": "2026-11-08T20:35:12+08:00",
  "duration_s": 7200,
  "clock_offset_s": -0.8,          // 本地时钟相对服务器/比赛开始的修正
  "epoch": 1,                      // 数据纪元：用于隔离会话开始前的旧数据
  "start_cursor": "1847-000123",   // 会话开始时该平台已存在的最大提交 id
  "goal": { "percentile": 0.25, "expected_solves": 3.2 },
  "rating": 1400,
  "config_version": 3,
  "status": "active"              // active | paused | ended | aborted
}
```

**边界规则**：`started_at` 之前产生的提交、`submission_id ≤ start_cursor` 的提交，一律忽略（这是"刷新页面/重新登录不重复触发"的根本保证）。

### 3.3 提交状态机（去重的核心）

```
      ┌──────────┐  轮询发现   ┌───────────┐  判题完成  ┌───────────┐
      │ 不存在    │ ─────────► │ PENDING   │ ─────────► │ 终态 AC/WA │
      └──────────┘             └───────────┘            │ /TLE/RE/CE │
                                     │                 └─────┬─────┘
                                     │ 超时 5min 仍未终态       │ 只 emit 一次
                                     ▼                       ▼
                              标记 UNKNOWN（不触发）    触发评估（走闸门）
```

去重键：`(platform, contest_id or "practice", submission_id)`。状态迁移只允许"未终态 → 终态"一次；重复快照、重复轮询、多次返回同一结果都不会二次触发。

**实现要点**

- 每个 session 维护 `last_seen_submission_id`（单调游标）+ 本地 `pending` 表。
- 轮询按 id 倒序发现新提交，发现后把 id 加入 `pending`，等终态。
- 终态一旦落库即不可回退（防止接口抖动把 AC 改回 PENDING 又触发一次）。
- 同一 `problem_index` 多次失败**不去重**（那确实是多次失败），但受冷却与升压禁止约束（§7.4）。

### 3.4 榜单快照（Snapshot）

```json
{
  "ts": "2026-11-08T21:20:03+08:00",
  "source": "codeforces",
  "contest_id": 1847,
  "frozen": true,
  "frozen_since": "2026-11-08T21:05:00+08:00",
  "n_participants": 8123,
  "n_official": 8000,             // 只算 official，S_rank 的分母（缺失时 S_rank=None）
  "me": { "rank": 3210, "solved": 1, "penalty": 87,
          "last_ac_at": "2026-11-08T20:58:00+08:00" },
  "top": [ ... ],                 // 仅保留前 K 行用于展示
  "stale": false
}
```

字段缺失策略：**任何字段缺失都标记 `unknown`，对应子分按"缺失"处理（见 §5.5），不允许用默认值顶替**。

### 3.5 事件模型

```json
{
  "event_id": "evt-000123",
  "ts": "2026-11-08T21:20:10+08:00",
  "session_id": "20261108-cf-1847-a3f",
  "kind": "verdict",              // verdict | timeout_no_submit | timeout_no_ac |
                                  // level_up | contest_end | session_start | safety_kill
  "verdict": "WRONG_ANSWER",      // 仅 kind=verdict 时出现；事件名 = verdict + "." + 小写简写（§7.2）
  "severity": "failure",          // info | success | failure | critical
  "pressure": 62.4,
  "level": "L2",
  "reason": "problem B: WRONG_ANSWER (sub #1847-... )",
  "requested_intensity": 0.20,    // 0..1，0 表示仅展示
  "components": { "rank": 55.1, "progress": 60.3, "stagnation": 71.2, "recent": 100.0 },
  "allowed": true,                // 闸门裁决
  "deny_reason": null
}
```

`components` 必须透传，这是**可解释性的最低要求**：任何一次反馈都能说清"为什么"。

---

## 4. 数据采集层

### 4.1 适配器接口（所有平台统一）

每个平台实现同一组接口，引擎不关心平台差异：

```python
class StandingsSource(Protocol):
    def capabilities(self) -> Capabilities: ...
    # realtime_verdict / live_standings / freeze_aware / requires_auth
    def fetch_standings(self, session) -> Snapshot | None: ...
    def fetch_submissions(self, session, after_id: str | None) -> list[RawSubmission]: ...

@dataclass
class Capabilities:
    realtime_verdict: bool   # 能否拿到实时判题终态
    live_standings: bool     # 能否拿到实时榜单
    freeze_aware: bool       # 接口是否明确告知封榜状态
    requires_auth: bool      # 是否需要登录态（走浏览器扩展）
```

**能力探测**：会话开始时探测 `capabilities()`，据此选择算法模式（全量 / 降级），并在面板上明示当前模式——用户必须知道"现在这个模式能做什么"。

### 4.2 平台事实表（2026-11-08 核查）

> 每条结论的证据 URL 与置信度记录在 `research/platform-data-sources.md`；这里只保留**影响设计**的结论。

#### 4.2.1 能力总表

| 平台 | 实时拿到本人判题 | 实时榜单 | 封榜信息 | 认证需求 | 数据源级别 |
|---|---|---|---|---|---|
| Codeforces | 可以（`contest.status?handle=我`） | 可以，但**匿名只能整场拉取**（不允许分页/按 handle 过滤） | `Contest.frozen` + `freezeDurationSeconds`；**行级无冻结标记** | 无需 | **A** |
| AtCoder | **匿名取不到**（302 登录墙） | **匿名取不到**（302 登录墙） | 官方无明文 | 需用户登录态（扩展） | **C** |
| 牛客 | 未找到实时接口 | 未找到实时 JSON 接口 | 比赛配置项，外部不可知 | 需登录态（扩展） | **C（弱实时）** |
| 洛谷 | 仅 ACM 类赛制可见；OI 赛制官方明确不可见 | `/fe/api/contest/scoreboard/:id` JSON，需 `__client_id` cookie | 未找到明文 | 需登录 cookie（扩展） | **C** |
| VJudge | `/status/data` 匿名可读；单条详情 `/solution/data/{runId}` 被 `crawler_blocked` | 同一接口带过滤 | — | 匿名（详情需会话） | **B** |

**一句话结论**：**只有 Codeforces 能真正做到"稳定、免登录、官方接口的实时判题+榜单"**。其余平台都要么依赖用户登录态（扩展），要么只能弱实时。这直接决定了首发范围（§12）与设备输出范围（§7.5）。

#### 4.2.2 各平台关键坑

**Codeforces**
- 匿名 `contest.standings` **只允许仅带 `contestId` 一个参数**；带 `from/count/handles` 会被拒。→ 必须整场拉取（Div.2 上万人，响应可达数 MB），本地做增量 diff，不能靠分页降负载。
- 频率限制是硬性的**「1 次/2 秒」**，超限返回 `FAILED` + `Call limit exceeded`。→ 必须全局串行预算（§4.3）。
- 榜单**不含 rating**；评级 seed 模型需额外一次 `user.info`（handles ≤ 10000，结果缓存 6h）。
- `contest.status?handle=<单人>` 正好"只取我自己"，比全站提交便宜。
- 封榜时 `frozen=true`，**榜单只回放封榜前提交**；解封后一次性跳变，无增量/事件流接口。
- verdict 里有 `TESTING`/`SUBMITTED` 两个未终态，正好喂给 §3.3 状态机。

**AtCoder**
- `/contests/<id>/standings/json` 路由存在，但匿名访问 **302 跳登录页**；`/contests/<id>/submissions/json` **根本不存在**（"json" 被当 submission id 解析后 404）。
- 官方限制同 IP 连续访问数，共享出口 IP（公司/校园网）可能被误伤。
- 封榜规则**没有任何官方明文**（ABC 规则页只写 full-feedback）→ 不能假设有/无，只能启发式（§6.3）。

**牛客**
- 没有实时榜单 JSON；官方唯一相关能力是**赛后**导出 ICPC Tools `.ndjson`（且要求比赛结束、判题全部完成、已设封榜）。
- 这条事实本身说明：牛客的实时数据不在公开接口层 → 只能扩展在页面内取，**弱实时**，不承诺实时排名。

**洛谷**
- `/fe/api/contest/scoreboard/:id?page=N` 返回 JSON（`scoreboard.result[].user.name`、`details`），但需登录 cookie，且出自第三方逆向文档，**可能变动**。
- 官方帮助中心明确：**OI 赛制比赛期间看不到评测结果**；乐多/ICPC/IOI 赛制可见 → 与 D4 完全一致。
- 封榜规则无明文。

**VJudge**
- `/status/data?draw&start&length` 匿名 200，含 `status` / `statusType` / `processing`（`processing=true` 即判题中）。
- `/solution/data/{runId}` 返回 400 `crawler_blocked` → **禁止依赖单条详情接口**。
- `recordsTotal=9999999` 是哨兵值，分页边界不可信；无文档、无 SLA、无稳定性承诺。

#### 4.2.3 由事实导出的 5 条硬约束

1. **CF 必须按「整场拉取 + 本地缓存 + 预算调度」设计**，不能按页/按人查。
2. **AtCoder 没有登录态就等于不可用**（既无排名也无提交结果）→ 只能走扩展，且属 C 级源。
3. **牛客不承诺实时排名**，只做「页面内取数 + 计时为主」的弱实时。
4. **洛谷按赛制分流**：OI 赛制进降级模式；ACM 类赛制才允许判题事件。
5. **VJudge 只信任 `status/data`**，单条详情接口一律视为不可用。

### 4.3 轮询策略与限速

**先算预算，再定频率**（以 Codeforces 为例，硬限制 1 次/2 秒 = 30 次/分钟）：

```
预留 20% 给重试与 user.info → 可用预算 ≈ 24 次/分钟

contest.status?handle=我        5s 一次   → 12 次/分钟
contest.standings?contestId     60s 一次  →  1 次/分钟
user.info（评级，6h 缓存）        ≈ 可忽略
合计                                     ≈ 13 次/分钟 ✅
```

| 平台 | 榜单轮询 | 提交轮询 | 备注 |
|---|---|---|---|
| Codeforces | 60s（**整场**拉取，可能数 MB，需 gzip + 本地 diff） | 5s（单人 `contest.status`） | 严格串行；退避 2s→4s→8s→…→30s |
| AtCoder | 30s（扩展，页面上下文） | 3~5s（扩展） | 无登录态不是"降级"而是"没数据" |
| 牛客 | 15~30s（扩展，仅展示） | 2~3s（扩展） | 弱实时；榜单只展示、**不参与算分**（`S_rank = None`） |
| 洛谷 | 30s（扩展，`/fe/api/contest/scoreboard`） | 3s（扩展，仅 ACM 类赛制） | OI 赛制无实时判题 |
| VJudge | 60s | 5s | 只信 `status/data` |

规则：

1. **单飞**：同一平台同时最多一个在途请求（`asyncio.Lock`），避免并发触发限流。
2. **抖动**：实际间隔 = 基础间隔 × (0.85~1.15) 随机，避免固定节奏被识别。
3. **条件请求**：能带 `If-None-Match` / `If-Modified-Since` 的带；304 视为"数据未变"（不刷新 `stale` 计时）。
4. **退避**：429/403 → 指数退避（2s→4s→8s→30s 封顶），数据标记 `degraded`；连续失败 5 次 → 通知面板，**不补触发**。
5. **停止条件**：会话 `ended` 后立即停止全部轮询（防止"赛后突然补一堆事件"）。
6. **大响应保护**：CF 整场榜单单次响应 > 5MB 时，把榜单轮询降到 90s 并告警——数据量优先于新鲜度。
7. **预算记账**：所有请求过统一 `RateLimiter`，按分钟统计；超预算自动降频并落日志（便于复盘"是不是把额度用完了"）。

### 4.4 时钟与时间基准

- 以**服务器时间**为准：从响应头 `Date` 或接口里的 `relativeTimeSeconds` 反推比赛进度。
- 本地时钟偏移记录在 `session.clock_offset_s`，偏移 > 5s 在面板提示。
- 比赛时长以接口返回为准（CF 可能因故延长）。**不用本地 `duration_s` 判结束**：轮询到结束时间后若接口仍显示进行中，则顺延 10 分钟并重算 `duration_s`（可重复），`contest_end` 以接口状态为准。

### 4.5 采集异常的硬规则

| 异常 | 处理 |
|---|---|
| 网络超时 / 5xx | 标记 `stale`，压力计算冻结，**禁止 device 输出** |
| 429 / 403 | 退避 + 降级提示，不重试到死 |
| 返回结构与预期不符 | 记录原始响应到 `~/.ojhc/quarantine/`，降级为 display-only，**不猜测字段** |
| 会话结束后的残留响应 | 直接丢弃（以 `session_id` + `status` 双重校验） |
| 接口返回重复提交 | 靠 §3.3 去重键吞掉 |

---

## 5. 竞赛压力算法（核心章节）

> 目标：把"我现在的比赛表现如何"变成一个 0~100 的分，**单调、有界、可解释、抗抖动**，并且与"是否反馈"解耦。

### 5.1 五条设计原则

| 编号 | 原则 | 具体体现 |
|---|---|---|
| P1 | 可解释 | 每次输出必须带四个子分与权重，能回答"为什么是 62 分" |
| P2 | 公平/个性化 | 用**评级期望**判断，而不是绝对排名；900 分选手不该因落后 2400 分选手被惩罚 |
| P3 | 迟钝优先 | 宁可漏报不可误报：EMA + 滞回 + 死区，牺牲响应速度换稳定 |
| P4 | 单调有界 | 每个子分 ∈ [0,100] 且对"更差表现"单调不减；总分有界 |
| P5 | 可回放 | 全部输入可落盘，纯函数计算，同样输入必得同样输出（便于测试与复盘） |

### 5.2 输入与符号

| 符号 | 含义 | 来源 |
|---|---|---|
| `D` | 比赛总时长（秒） | 比赛元数据 |
| `τ` | 归一化进度 `τ = clamp(elapsed/D, 0, 1)` | 服务器时间 |
| `N` | 有效参赛人数（只算 official） | 榜单 |
| `R` | 我的当前排名 | 榜单 |
| `A` | 我已 AC 的题数（去重按题目） | 榜单/提交记录 |
| `t_ac` | 距上次 AC 的分钟数 | 榜单/提交记录 |
| `r` | 我的赛前 rating（CF/AtCoder） | user.info |
| `q` | 目标百分位（如 0.25 = 前 25%） | 用户配置 |
| `E_total` | 期望总解题数 | 题目难度 × 评级（或用户配置） |

### 5.3 期望模型

**(a) 目标排名**

```
r_goal = q × N
```

**(b) 评级期望排名（Codeforces seed 模型）**

若拿得到参赛者 rating（`user.info` 批量查询后缓存），按 CF 官方 rating 预测使用的 seed 公式：

```
seed_i = 1 + Σ_{j≠i} 1 / (1 + 10^((r_j - r_i)/400))
E_seed  = seed_i          # 其期望排名即 seed 值
```

若拿不到（人数过多、跨平台无 rating、查询被限流），退化为 `E_seed = None`。

**成本与可用性**：CF 需要一次 `user.info`（handles ≤ 10000，结果缓存 6h）；AtCoder 无登录态取不到 rating → `E_seed = None`，自动退化为目标百分位模型。

**(c) 有效期望（公平性核心）**

```
E_eff = max(r_goal, E_seed)        # E_seed 缺失时 E_eff = r_goal
```

含义：**如果你的目标比你的评级预期还高，系统只按评级预期要求你**。
例：1400 分选手设"前 5%"，其 seed 期望约为前 33%，则 `E_eff` 取 seed 值，只有掉到 33% 之外才开始积累压力；不会因为目标不现实就一路满压。

> 面板会额外提示"目标偏挑战（目标 2000 名 vs 评级期望 2600 名）"，但**不影响计算**。若 `E_eff ≤ 0`（人数/评级解析失败），`S_rank` 直接记 `None`，避免 `log(0)`。

**(d) 期望总解题数**

```
p_p = 1 / (1 + 10^((R_p - r)/400))        # 题目 p 的通过概率，R_p 为题目难度分
E_total = Σ_p p_p
```

题目难度分缺失（牛客/洛谷常见）→ `E_total` 取用户配置值（默认 3），并在事件里标记 `estimated=true`。

**(e) 期望解题进度曲线**

```
v1:  E_solves(τ) = E_total × τ                    # 线性模型（默认）
v2:  逐题模型 F_p(τ)，按题目难度错开 AC 时刻        # 预留，用回放数据校准后再开
```

线性模型对"难题比赛"偏乐观（会早一点报压力），因此 `progress_model` 是可配开关，v1 默认线性并在文档中写明偏差方向。

**(f) 停滞参考时长**

```
t_ref = clamp(1.2 × D / max(E_total, 0.8), 8min, 40min)
```

例：90 分钟、期望 3 题 → 36 min；120 分钟、期望 3.2 题 → 45 → 封顶 40 min。

### 5.4 四个子分（每个都是 0~100）

#### S_rank：相对排名落后程度

```
S_rank = 100 × clamp( ln(R / E_eff) / ln(ρ_max), 0, 1 )      ρ_max = 2.5
```

- `R ≤ E_eff` → 0（领先不产生负压力，避免"越做越怕"）。
- `ρ_max = 2.5` 的含义：排名掉到期望的 2.5 倍时给满分。对数尺度让"从 1000 名掉到 1500 名"和"从 3000 名掉到 4500 名"感觉一致。
- 数据缺失（OI 赛制 / 无榜单 / 解析失败）→ `None`。**封榜期不置 None**，而是冻结上一个可信值并降权（§6.4）。

#### S_progress：解题进度落后

```
deficit  = max(0, E_solves(τ) - A)
S_prog   = 100 × (1 - exp(-deficit / δ_ref))        δ_ref = 1.2 题
```

- 平滑饱和：落后 0.5 题不会立刻高分，落后 3 题逼近 92。
- 领先（deficit=0）→ 0，不产生负压力。

#### S_stag：停滞时间

```
t_ref  = §5.3(f)
ramp(τ) = clamp((τ - 0.08) / 0.10, 0, 1)            # 前 8% 比赛时间不判停滞
S_stag = 100 × (1 - exp(-t_ac / t_ref)) × ramp(τ)
```

- `t_ac` = now − **上次 AC 时刻**；本场一次都没 AC 时用"now − 比赛开始"（受 ramp 保护）。全文统一按这个定义（§5.2 / §7.2 / §8.1 一致）。
- 指数饱和：`t_ac = t_ref` 时约 63 分，2 倍时约 86 分。不会无限涨。

#### S_recent：最近提交趋势

```
窗口：最近 8 次提交 或 最近 25 分钟（取先到者）
衰减：w_i = 0.5^(age_i / 10min)                     # 半衰期 10 分钟
单次得分 s_i：AC = +1，WA/TLE/MLE/RE = -1，CE = -0.3，
              PARTIAL(f) = 2f - 1（f 为得分比例），PENDING = 跳过
中性先验：w0 = 1.0（"没有证据 = 中性"，权重≈一条刚发生的提交）
R̄ = Σ w_i s_i / (Σ w_i + w0)   ∈ (-1, 1)
S_recent = 100 × clamp((0.15 - R̄) / 0.9, 0, 1)
连败加成：连续失败 ≥ 3 次 → S_recent = min(100, S_recent + 15)
无提交 → 0（空窗由 S_stag 负责，避免重复计压）
```

**为什么分母里要加 `w0`**：若按 `Σw·s / Σw` 算加权平均，窗口内符号一致时衰减会被分子分母约掉——单条 WA 在 25 分钟内 R̄ 恒为 −1，永远"刚失败过"。加了中性先验后，同一次 WA 在 0 / 10 / 25 分钟时 S_recent ≈ 72 / 54 / 33，才会"陡升后回落"。

### 5.5 聚合：权重、缺失、塑形、平滑、分级

**权重（默认）**

```
w = { rank: 0.35, progress: 0.30, stagnation: 0.20, recent: 0.15 }
```

**晚期偏置（可选，默认开）**：`τ > 0.75` 时 `w_rank ×1.2`、`w_stag ×1.2`，重新归一化。
理由：比赛最后 1/4 时间，"落后"和"停滞"比"刚才那次 WA"更值得关注。

**缺失子分处理**

```
1) 缺失的子分（None）从权重和中去掉
2) 剩余权重按比例归一化（仅用于 P_raw 计算）
3) 参与计算的"原始权重和" < 0.55 → confidence=low
   例：OI 赛制只剩 progress(.30) + stagnation(.20) = 0.50 → low
4) confidence = min(权重规则结论, 数据源规则结论)
   数据源规则：C 级源或解析失败 → low（§6.1 / §6.6）
5) confidence=low 时：只允许 display/audio，禁止 device 输出
```

**塑形（γ 塑形，压低中段）**

```
P_raw = Σ w_i S_i / Σ w_i
P_s   = 100 × (P_raw / 100)^γ            γ = 1.25
```

γ > 1 **只会压低、不会抬高**：`P_raw=80` → 75.7，`P_raw=45` → 36.9。作用是把"中等偏差"压下去，只有明显落后才逼近 80+。

**时间平滑（EMA，按真实间隔）**

```
α = 1 - exp(-Δt / τ_ema)        τ_ema = 45s，α 上限 0.6
P = P_prev + α × (P_s - P_prev)
死区：|P_s - P| < 3 且未跨越等级 → 保持不变
更新最小间隔 30s（比这更密的采样只落库不更新 P）
```

**分级与滞回**

| 等级 | 区间 | 语义 | 允许的 device 强度 |
|---|---|---|---|
| L0 | 0–25 | 轻松 | 不允许 |
| L1 | 25–50 | 正常 | 仅低强度（≤0.2）且需 WA/TLE/RE 事件 |
| L2 | 50–75 | 有压力 | 中强度（≤0.35） |
| L3 | 75–100 | 高压力 | 高强度（≤用户上限） |

**滞回**（防止在阈值附近反复横跳）：

- 升级：`P ≥ 阈值 + 4`，且连续 2 次采样（≥60s 间隔）成立 → 升级。
- 降级：`P ≤ 阈值 − 6`，连续 2 次采样成立 → 降级。
- 一场比赛内等级最多升 3 次（L0→L3）；跨级事件是否触发反馈见 §7.2。

### 5.6 数值示例（可直接核对）

场景：CF Div.2，`D=120min`，`τ=0.55`（第 66 分钟），`r=1400`，`N=8000`，目标前 25%，`E_total=3.2`，当前 `R=3200`、`A=1`、距上次 AC 22 分钟、最近两次提交都是 WA（5 分钟与 9 分钟前）。

| 子分 | 计算 | 结果 |
|---|---|---|
| `r_goal` | 0.25 × 8000 | 2000 |
| `E_seed` | 假设 seed 模型给出 | 2600 |
| `E_eff` | max(2000, 2600) | 2600 |
| `S_rank` | 100 × ln(3200/2600)/ln(2.5) = 100×0.2076/0.9163 | **22.7** |
| `E_solves` | 3.2 × 0.55 | 1.76 |
| `S_prog` | 100 × (1 − e^(−0.76/1.2)) | **46.9** |
| `t_ref` | 1.2×120/3.2 = 45 → 封顶 40 | 40 |
| `S_stag` | 100 × (1 − e^(−22/40)) × 1 | **42.3** |
| `S_recent` | 两次 WA（5 / 9 分钟前）：R̄ = −(0.707+0.536)/(1.243+1) = −0.554 → 100×(0.15+0.554)/0.9 | **78.2** |
| `P_raw` | .35×22.7 + .30×46.9 + .20×42.3 + .15×78.2 | 42.2 |
| `P_s` | 100 × (0.422)^1.25 | 34.0 |
| `P`（EMA，上次 28，Δt=30s → α=0.487） | 28 + 0.487×(34.0−28) | **30.9 → L1** |

结论：**L1 正常**。此时即使刚吃了一个 WA，L1 最多也只允许 `s=3`（校准区间的中档），而且不会连续输出——这正是 D1 的目的：**有压力 ≠ 该电**。

三种典型状态的对照：

| 场景 | R | A | t_ac | recent | P_raw | P | 等级 |
|---|---|---|---|---|---|---|---|
| 领先 | 900 | 2 | 4min（刚 AC） | AC | ≈1.9 | ≈0.7 | L0 |
| 上面示例 | 3200 | 1 | 22min | WA,WA | 42.2 | 30.9 | L1 |
| 崩盘 | 5200 | 0 | 66min（=已进行时间） | 4 次失败 | 80.7 | 76.5 | L3 |

崩盘行可核对：`S_rank`=100×ln(2)/ln(2.5)=75.6；`S_prog`=100×(1−e^(−1.76/1.2))=76.9；`S_stag`=100×(1−e^(−66/40))=80.8；`S_recent`=100（4 次失败 + 连败加成）。

> 上面数字可用参考实现复算：`python examples/pressure_demo.py`。脚本只演示 **§5.4 / §5.5 的公式数值**（EMA 用 Δt=30s 的单一 α），不含滞回计数、confidence 与封榜逻辑——数字应一致，但它不是完整引擎。

### 5.7 伪代码

```python
def compute_pressure(inp: PressureInput, prev: PressureState, cfg) -> PressureState:
    # 1) 期望
    r_goal = inp.q * inp.n_official
    E_eff  = max(r_goal, inp.e_seed) if inp.e_seed else r_goal
    E_solves = inp.e_total * inp.tau                    # linear 模型

    # 2) 子分（缺失 -> None）
    s_rank = None
    if inp.rank is not None and E_eff > 0:              # 防 log(0)
        s_rank = 100 * clamp(math.log(inp.rank / E_eff) / math.log(2.5), 0, 1)
    s_prog = 100 * (1 - math.exp(-max(0.0, E_solves - inp.solved) / 1.2))
    t_ref  = clamp(1.2 * inp.duration / max(inp.e_total, 0.8), 8*60, 40*60)
    ramp   = clamp((inp.tau - 0.08) / 0.10, 0, 1)
    s_stag = 100 * (1 - math.exp(-inp.t_since_ac / t_ref)) * ramp
    s_recent = recent_score(inp.submissions, window=8, halflife=600,
                            prior_w=1.0, streak_bonus=15)

    # 3) 加权（缺失归一化 + 晚期偏置）
    w = dict(cfg.weights)
    if inp.tau > 0.75: w["rank"] *= 1.2; w["stagnation"] *= 1.2
    parts = {"rank": s_rank, "progress": s_prog,
             "stagnation": s_stag, "recent": s_recent}
    num = sum(w[k] * v for k, v in parts.items() if v is not None)
    den = sum(w[k] for k, v in parts.items() if v is not None)
    if den < 0.55:                                      # 参与计算的原始权重和
        return prev.with_confidence("low")              # 数据不足 -> 只 display
    p_raw = num / den

    # 4) 塑形 + 平滑 + 死区
    p_s = 100 * (p_raw / 100) ** cfg.gamma
    dt  = inp.now - prev.updated_at
    if dt < 30: return prev                             # 更新太密，忽略
    alpha = min(0.6, 1 - math.exp(-dt / 45.0))
    crossed = level_of(p_s) != prev.level               # 跨越等级时不吃死区
    p = prev.p if (abs(p_s - prev.p) < 3 and not crossed) \
        else prev.p + alpha * (p_s - prev.p)

    # 5) 等级 + 滞回
    level = hysteretic_level(p, prev, up=4, down=6, need=2)
    return PressureState(p=p, level=level, components=parts,
                         confidence="high", updated_at=inp.now)


def hysteretic_level(p, st, up, down, need):
    """st 必须持久化：level / up_cnt / down_cnt / upgrades_this_session。"""
    level = st.level
    if p >= next_threshold(level) + up:
        st.up_cnt, st.down_cnt = st.up_cnt + 1, 0
    elif p <= prev_threshold(level) - down:
        st.down_cnt, st.up_cnt = st.down_cnt + 1, 0
    else:
        st.up_cnt = st.down_cnt = 0
    if st.up_cnt >= need and st.upgrades_this_session < 3:
        level, st.upgrades_this_session = level + 1, st.upgrades_this_session + 1
        st.up_cnt = st.down_cnt = 0
    elif st.down_cnt >= need:
        level, st.up_cnt = level - 1, 0
        st.down_cnt = 0
    return level
```

`PressureState` 必含字段：`p`、`level`、`components`、`confidence`、`up_cnt`、`down_cnt`、`upgrades_this_session`、`updated_at`、`p_rank`（排名信号单独的慢 EMA 状态，§6.5）。

纯函数性质：`compute_pressure` 不读网络、不读时钟、不做 I/O，因此可单测、可回放。

### 5.8 校准与回放验证

**三个层次的验证**（排名类数据无法从官方 API 重建历史，必须分层面对待）：

| 层 | 数据来源 | 验证什么 | 对应测试 |
|---|---|---|---|
| V1 合成场景 | 手写 JSONL（领先/平稳/崩盘/抖动/封榜跳变/断网） | 公式边界、单调性、滞回、缺失处理 | 单元测试 |
| V2 提交时间线回放 | 本人历史比赛的 `contest.status`（有时间戳） | `S_prog`/`S_stag`/`S_recent` 的时间曲线是否合理 | 集成测试 |
| V3 真实比赛录制 | 比赛时每 30s 落盘 snapshot + event | 权重/阈值是否贴合真实感受 | 人工复盘 |

**验收指标（90 分钟比赛，按预设分别验收）**

| 指标 | casual | hardcore | 说明 |
|---|---|---|---|
| 反馈事件总数 | 3~8 | 6~12 | 事件密度（§7.9.5） |
| 前 5 分钟触发 | 0 | 0 | 开局保护 |
| 与真实失败事件同帧占比 | ≥60% | ≥70% | 不是"因为排名漂移"才动 |
| 压力曲线抖动（30s 一阶差分 95 分位） | <8 分 | <8 分 | 平滑性 |
| 单场等级升级次数 | ≤3 | ≤3 | 滞回有效 |
| 主观"这一下感觉到了" | ≥80% | ≥90% | 赛后三档反馈统计（§7.9.4） |
| 赛后复盘判定为"不该动"的触发 | 0 | 0 | 安全底线 |

**调参策略**：先冻结默认值，用 V2/V3 数据在 ±0.1 范围内网格搜索权重，**不做端到端黑盒调参**；任何调参都要留下 `config_version` 与回放报告。

### 5.9 算法参数默认值表

| 参数 | 默认 | 说明 |
|---|---|---|
| `weights` | rank .35 / progress .30 / stagnation .20 / recent .15 | 主旋钮 |
| `gamma` | 1.25 | 塑形强度，越大越"迟钝" |
| `ema_tau_s` | 45 | 平滑时间常数 |
| `update_min_interval_s` | 30 | 最小更新间隔 |
| `deadband` | 3 | 死区 |
| `rho_max` | 2.5 | 排名满分倍率 |
| `delta_ref` | 1.2 | 进度饱和题数 |
| `stagnation_ref` | `1.2D/E_total`，8~40min | 停滞参考 |
| `ramp` | 8%~18% | 开局保护 |
| `recent_window` | 8 次 / 25min | 提交窗口 |
| `recent_halflife_s` | 600 | 衰减半衰期 |
| `recent_prior_w` | 1.0 | 中性先验权重（保证衰减真正生效，§5.4） |
| `streak_bonus` | 15 | 连败加成 |
| `hysteresis` | +4 / −6，连续 2 次 | 等级滞回 |
| `rank_ema_tau_s` | 120 | 排名信号单独的慢 EMA（解封跳变滤波，§6.5） |
| `late_phase_bias` | 开 | τ>0.75 偏置 |

---

## 6. 数据可信度：封榜、赛制降级、过期处理

> 这一章是"不误触发"的底座。压力分再合理，喂给它假数据也会出事。

### 6.1 数据源可信度分级

| 级别 | 来源 | 允许的行为 |
|---|---|---|
| A | 官方 API（目前只有 Codeforces） | 全部反馈 |
| B | 非官方 JSON（VJudge `status/data`） | 全部反馈，解析失败即降级 |
| C | 浏览器扩展 / 页面内取数（AtCoder、牛客、洛谷） | display/audio 全开；**device 默认禁用**，需用户显式打开 `allow_device_on_dom_source`（§7.5 第 10 条） |

### 6.2 新鲜度与过期

```
stale_after      = 60s     # 超过 -> 压力冻结，禁止 device
pause_after      = 180s    # 超过 -> 面板警告，暂停计时器
```

"冻结"指：沿用最后一个 P 值但不更新、不升级等级、不产生新事件；恢复后**不补发**积压事件。

### 6.3 封榜检测（两路硬判定 + 一路降权提示）

| 路 | 依据 | 可用平台 | 可信度 |
|---|---|---|---|
| ① 接口明确标志 | CF `Contest.frozen=true` + `freezeDurationSeconds` | Codeforces | 高；**行级没有冻结标记**，不要拿"某人分数不动"当冻结证据 |
| ② 元数据时间窗 | `start + duration − freezeDuration ≤ now < end` | 仅 CF（其他平台无明文，**禁止使用**） | 中 |
| ③ 内容启发式 | 榜单内容 ≥ 10 分钟完全未变，但本人有新提交/新 AC | AtCoder / 牛客 / 洛谷 | 低；只记为 `possible_freeze`，只降权、不切模式 |

**CF 封榜期的额外事实**：`frozen=true` 时接口只回放封榜前提交，也就是说榜上根本没有封榜后发生的事。因此封榜期的排名**天然滞后**，既不能用来判断"刚被谁超过"，也不能在解封瞬间当真（§6.5）。

**没有明文的平台怎么办**：AtCoder/牛客/洛谷一律按"未知"处理——默认更保守（排名信号权重 ×0.5），并把 `possible_freeze` 显示在面板上，让用户自己知道"现在这个排名可能不准"。

### 6.4 封榜期间的策略（重要）

| 信号 | 封榜期处理 |
|---|---|
| `S_rank` | **冻结**为封榜前最后一个可信值，权重 ×0.5（`rank_freeze_policy: hold`，可改 `disable`） |
| `S_prog` / `S_stag` / `S_recent` | 正常工作（判题结果不封） |
| 等级跨越事件 | **禁止 device**，只 display |
| WA/TLE/RE 事件 | 允许 device，但强度 ×0.7（≈降一档，§7.3） |

理由：封榜期通常是比赛最后 30~60 分钟，恰恰是最可能触发反馈的阶段，而排名信号此时已不可信。把排名贡献"冻住"而不是"清零"或"继续用"，既不误判也不丢压力感。

### 6.5 解封跳变滤波

封榜解除后榜单会出现一次性大跳变（别人的成绩被揭晓）。

```
解封后前 3 个采样（间隔 ≥ 20s）：禁止 device 输出
单次 |ΔR| / R_prev > 25%：判定为跳变，用最近 3 次采样的中位数替代，标记 artifact=true
（样本不足 3 个时用已有样本；一个都没有则丢弃该采样，本轮不更新 S_rank）
排名类信号单独用更慢的 EMA（τ=120s），与总分 EMA 解耦
```

### 6.6 赛制降级模式

| 场景 | 检测 | 降级行为 |
|---|---|---|
| OI 赛制（洛谷）无实时判题 | 能力探测 `realtime_verdict=false` | 只有计时器 + 提交次数；`S_rank`/`S_recent` = None；禁止 WA 类事件；面板显示"降级模式" |
| AtCoder 无登录态 | `capabilities` 探测（302 登录墙） | **不是降级，是没数据**：提示"请在已登录的 AtCoder 页面打开扩展"；绝不猜测 |
| 牛客 | 无实时接口 | 弱实时模式：计时 + 扩展抓到的提交次数；`S_rank` = None |
| 洛谷 ACM 类赛制 | 扩展 + scoreboard JSON 可用 | C 级源：可算排名，但 `confidence=low`，device 默认禁用（§7.5） |
| 无榜单（练习模式） | 会话配置 | 只启用 `S_prog`（按目标题数）+ `S_stag` + `S_recent` |
| 榜单人数异常（N=0/解析失败） | 校验 | `S_rank` = None，`confidence=low` |
| 赛制未知 | 能力探测失败 | 最保守：只 display |

**降级必须可见**：面板、事件、日志三处都要有 `mode=degraded` 标记。用户不该在不知情的情况下"以为在监听"。

### 6.7 赛后与积压

- 会话状态一旦 `ended`：停止轮询、停止计时器、**丢弃所有待处理事件**。
- 断网恢复后：只处理"恢复时刻之后发生的新提交"，历史积压一律不补发。
- 原则：**反馈的时效性比完整性重要**，一条 3 分钟前的 WA 不值得现在电一下。

---

## 7. 反馈策略与安全闸门

> 本章回答一个问题：**压力分 62，接下来到底做什么？**
> 答案：什么都不一定做。压力分只决定"上限"，触发由离散事件决定。

### 7.1 两类信号，两套权限

| 信号 | 形态 | 能触发反馈吗 | 能决定强度吗 |
|---|---|---|---|
| 离散事件（WA/TLE/RE、超时、AC、等级跨越、比赛结束） | 瞬时、可归因 | **能**（走闸门） | 能（作为基数） |
| 压力分 P / 等级 L | 连续、有抖动 | **不能** | 能（作为倍率上限） |

### 7.2 事件清单与默认动作

| 事件 | 触发条件 | 默认动作 | 感受等级 s |
|---|---|---|---|
| `verdict.wa` | 本次会话内提交终态 WA | 显示 + 音效 + 设备 | 2 |
| `verdict.tle` / `verdict.mle` / `verdict.re` | 对应终态 | 显示 + 音效 + 设备 | 3 |
| `verdict.ce` | 终态 CE | 仅音效（可配是否计压） | 0 |
| `verdict.ac` | 终态 AC | 成功提示 + 音效；AC 奖励通道开启时 device | 0 / 1 |
| `timeout_no_submit` | 距上次提交 > 阈值（默认 30min） | 显示 + 音效 + 设备 | 3 |
| `timeout_no_ac` | 距上次 AC > min(60min, 0.5D) | 显示 + 音效 + 设备 | 4 |
| `level_up`（升到 L2/L3） | 滞回升级成立 | **仅显示**（默认）；开启时 device | 0 / 2 |
| `contest_end` | 比赛结束 | 总结面板 | 0 |
| `safety_kill` | 急停/看门狗 | 立即停止输出 + 显示 | — |

**注意 `level_up` 默认不触发设备**：等级跨越是连续量的产物，最容易受抖动影响。想开的人自己承担，且仍受 §7.4 约束。

### 7.3 强度决策：从"事件"到"真的能感觉到"

```
① 事件 → 感受等级 s（0~5，见 §7.2）
② 压力等级 gate：L1 最多 s=3；L2 最多 s=4；L3 才允许 s=5；L0 一律不输出
③ s → 设备强度（按个人校准区间，§7.9）：
   intensity = I_p + (user_max − I_p) × frac[s]
   user_max = min(I_c × 0.8, 0.6)     ← 安全上限
   frac = {0: 0, 1: 0.20, 2: 0.35, 3: 0.50, 4: 0.65, 5: 0.85}
④ 时长：s ≤ 2 → 0.6~0.8s；s ≥ 3 → 1.0~2.0s（见 §7.9.3 节奏表）
⑤ 修正：封榜期 ×0.7；连续失败 streak ≥ 3 ×0.7；升压禁止生效时封顶 s=2
⑥ 硬钳制：intensity ≤ min(I_c, user_max, 0.6)，duration ≤ 3s
```

> **映射区间必须是 `[I_p, user_max]`，不能是 `[I_p, I_c]`**：因为 `user_max = 0.8×I_c` 是安全余量，若按 `[I_p, I_c]` 算再钳到 `user_max`，高档位会全部撞到同一个上限——实测出现 s=3/4/5 全是 28%、**分不出强弱**的情况。校准还应保证 `user_max − I_p ≥ 10%`（§7.9.1）。

- `I_p` = 校准得到的"刚能感觉到"强度；`I_c` = "舒适上限"。**没有校准就没有 I_p/I_c，设备通道不允许开启。**
- `L0` 一律不输出：没压力就绝不输出，这是硬规则。
- 强度是"请求值"，最终由插件与设备侧再钳制一次（§9.3 双重钳制）。

### 7.4 冷却、令牌桶与升压禁止

| 机制 | 默认值 | 目的 |
|---|---|---|
| 全局冷却 | 相邻两次 device 输出 ≥ 90s | 防止连续判题造成连击 |
| 同类冷却 | 同 kind ≥ 120s | 防止"WA→TLE→WA"三连 |
| 令牌桶 | 容量 5，每 3 分钟补 1（≈20 次/小时，允许小突发） | 平滑长期频率 |
| 小时窗硬限 | 30 次/小时 | 绝对兜底 |
| **升压禁止** | 最近 3 次 device 输出均为 failure 且期间无任何 AC → 封顶 `s=2`（校准区间低档）且额外冷却 +60s | 人已经在挨罚状态，不该越罚越重 |
| 无 AC 输出上限 | 连续 device 输出 5 次仍无 AC → 强制回到 display-only，直到下一次 AC | 防止"卡死的 40 分钟"变成连续刺激 |
| 重启保护 | 计数与冷却落盘，进程重启不清零（15 分钟窗口内） | 防止"重启绕过限速" |

> 升压禁止是本设计里我最想强调的一条：**惩罚系统最危险的行为是自激**——你越焦虑越 WA，越 WA 越强，最后变成不可控。这条规则把它掐死。

### 7.5 安全闸门（device 输出的全部前置条件）

**以下 11 条必须全部成立**，否则拒绝并记录 `deny_reason`：

1. 设备已校准（`device_profile.json` 存在、未过期），且本会话点击了"启用输出"（一次确认即可，不再要求每次输入确认词）；
2. `session.status == active` 且 `now < start + duration`；
3. `dry_run == false`；
4. 数据非 stale（< 60s）；
5. `confidence != low`（子分缺失过多时不允许）；
6. 未处于急停状态、看门狗未触发；
7. 令牌桶非空且全局/同类冷却已过；
8. `level >= L1`；
9. `intensity > 0`；
10. 数据源可信度 A/B；**C 级（扩展/DOM 取数）默认不给设备**，需用户显式打开 `allow_device_on_dom_source` 并接受风险；
11. 当前不在封榜跳变滤波窗口内（§6.5）。

任何一条不满足 → 事件照常显示，但 `allowed=false`。**拒绝也要可见**，用户要能看到"系统为什么没动"。

### 7.6 急停与看门狗

**人工急停**
- 全局热键（默认 `Ctrl+Alt+Shift+X`，可改）→ 立即停止设备输出并断开插件连接；
- 面板红色按钮，单击即停（不需要确认，停止永远比继续安全）。

**自动急停（看门狗）**

| 条件 | 动作 |
|---|---|
| 与插件/引擎的 WS 断开 > 5s | 停设备 |
| 插件心跳缺失 > 5s | 停设备 |
| 数据 stale > 120s | 停设备并暂停会话（60s 只禁 device，120s 是更强一级） |
| 本地时钟偏移 > 30s | 停设备 |
| 会话结束 / 进程收到 SIGTERM | 停设备 |
| DGHub 无响应 > 3s | 停设备 |

**死手开关（dead-man switch）**：Engine 维护一个**输出许可（lease）**——每 2s 续期、有效期 5s、带自增 `lease_id`。插件每次请求输出必须携带当前 `lease_id` + 一次性 `nonce`（用过即作废）。Engine 挂掉 → lease 过期 → 插件 5s 内停止并清空队列。**默认失效，而不是默认继续。**

**M4 实现（`engine/ojhc_lib/device.py` + `starter/ojhc_link`）**：Engine 在 127.0.0.1 起 TCP JSONL 服务（默认 8768），把 `{host,port,token}` 写进 `~/.ojhc/device_link.json`；插件读到后连上来，之后只认三类消息：`lease`（续期）、`output`（一次脉冲，含 nonce）、`estop`。插件侧再钳一次强度/时长；租约超过 1.35×ttl 立即 `set_strength 0` 归零；Engine 断开 / DGHub stop / 插件退出都会归零。Engine 侧：真实输出要求 `--device` + **真实校准档案**（演示档案直接拒绝），会话结束强制 estop，审计日志区分 `would_fire` 与 `allowed`。
（DGHub 官方协议没有这个机制，lease 由 Engine↔插件这条自有链路实现，见 §9.0 / §9.2。）

**脉冲约束**：单次输出最长 3s，之后必须重新申请；不允许"一次申请发 60s"。

### 7.7 硬性边界（代码常量，不随配置改变）

| 项 | 硬性边界 | 理由 |
|---|---|---|
| 单次强度 | ≤ 0.6 | 留出安全余量，默认配置只用 0.5 以下 |
| 单次脉冲时长 | ≤ 3s | 短脉冲可中断 |
| 两次输出最短间隔（下限） | ≥ 30s | 再急也不能连击 |
| device 次数/小时 | ≤ 30 | 统计学上"异常"的阈值 |
| 连续无 AC 输出 | ≤ 5 次 | 强制冷却通道 |

这些值写在代码里，配置文件改不动。想突破只能改源码——**并明确写在文档与 README 里：本项目不鼓励这样做。**

### 7.8 审计日志

每次事件、裁决、输出都落库（SQLite + JSONL）：

```
ts, session_id, event_id, kind, pressure, level, intensity_requested,
allowed, deny_reason, device_ack, source_confidence, mode(degraded?)
```

- 保留 30 天；
- 比赛结束生成**复盘报告**：事件时间线、压力曲线、触发次数、拒绝原因分布、单次最高强度；
- 用途：判断"算法是否太敏感"，以及**万一发生意外刺激，能查清是哪条规则放的**。

### 7.9 手感设计与校准（"有感觉"是硬需求）

> 这一节回答：为什么"设了 0.3 却没感觉"，以及怎么保证它真的有感觉。

**三个常见原因**

| 原因 | 解释 | 对策 |
|---|---|---|
| 强度低于个人感知阈值 | DG-Lab 的强度百分比**不是感知线性的**；同样 0.3，不同人/不同贴合度差很多 | 校准出个人感知区间 `[I_p, I_c]`，按区间映射 |
| 脉冲太短 | < 0.5s 的单脉冲容易被当成"抖了一下"忽略 | 最短 0.6s；失败类事件 1.0~2.0s |
| 节奏单一 | 所有事件同一个波形，很快习惯化、感觉变弱 | 不同事件用不同节奏/波形（§7.9.3） |

#### 7.9.1 校准流程（首次使用，约 3~5 分钟）

```
Step 0 连接检查
   DGHub 运行中 → 设备已蓝牙连接 → 通道 A/B → 电量
   只发 op:"event" 卡片（不刺激），确认链路通

Step 1 感知阈值 I_p
   从 0.05 起，每步 +0.05，脉冲 0.8s，间隔 ≥3s
   用户按"有感觉了" → 重复 3 次取中位数（排除首次错觉/紧张）
   若到 0.50 仍无感 → 报错：检查贴合、电量、通道、波形预设

Step 2 舒适上限 I_c
   从 I_p 继续 +0.05，直到"不适但可接受"
   必须满足 I_c ≥ I_p + 0.15，否则提示"输出区间过窄"
   本次上限 user_max = min(I_c × 0.8, 0.6)
   还要满足 user_max − I_p ≥ 0.10，否则档位分不出强弱（重测/换贴合/换预设）

Step 3 波形映射
   给每类事件选 DGHub 波形预设（用户自己的 preset）
   默认：WA=短促、TLE/RE=波浪、超时=慢起、AC=轻快

Step 4 手感预览
   依次触发 s=2 / 3 / 4 各一次，用户确认"能分辨出强弱"
   写入 device_profile.json（含 calibrated_at / 通道 / 设备标识）
```

#### 7.9.2 每场只确认一次

面板显示 `设备就绪 · 感知区间 0.38–0.61 · 本次上限 0.50 · casual`，点一次"启用输出"即进入自动输出；`ended` 或急停后自动失效。**把"二次确认"放在每场开头，而不是每个事件**——既安全又不打断体验。

#### 7.9.3 事件节奏表（默认）

| 事件 | 感受等级 s | 脉冲时长 | 节奏描述 |
|---|---|---|---|
| `level_up`（开启时） | 1~2 | 0.6s | 单短促，像"提醒" |
| `verdict.ac`（奖励开启时） | 1 | 0.6s | 轻快单脉冲 |
| `verdict.wa` | 2 | 0.8s | 单脉冲，干脆 |
| `verdict.tle`/`mle`/`re` | 3 | 1.2s | 波浪式，稍有延续 |
| `timeout_no_submit` | 3 | 1.5s | 慢起，逐渐到顶 |
| `timeout_no_ac` | 4 | 2.0s | 双段：强—停—强 |
| L3 且 hardcore 的叠加档 | 5 | 2.0s | 最强节奏（仅 hardcore） |

#### 7.9.4 校准过期与自适应

- 触发重校准：换设备 / 换通道 / 距上次 > 30 天 / 用户体验反馈"没感觉" / 设备回执异常率 > 20%；
- 赛后三档反馈（太弱 / 合适 / 太强）→ 下次建议 `user_max ± 0.05`，**绝不自动越过校准上限**；
- **不靠"越来越强"弥补频率不足**：频率靠事件设计（§7.9.5），强度只按校准区间走。

#### 7.9.5 事件密度从哪来（3~12 次/场）

| 来源 | casual | hardcore |
|---|---|---|
| WA / TLE / RE（每条提交） | ✅ | ✅ |
| 停滞阶梯（每 15 分钟一档，最多 3 档） | 仅显示 | ✅（最多 2 次） |
| 超时未提交 / 未 AC | ✅ | ✅ |
| 等级跨越 | 仅显示 | 可选（s ≤ 2） |
| 比赛最后 10 分钟"冲刺提醒" | ❌ | ✅（1 次） |

**hardcore** = 事件更密 + 冷却 60s（仍受 §7.4 硬限约束）；**casual** = 冷却 90s、事件更少、L1 封顶 s=3。
密度提升仍受 §7.4 的令牌桶（≈20 次/小时）与升压禁止约束，不会退化成持续刺激。

---

## 8. 计时器与挑战模式

### 8.1 三个计时器

| 计时器 | 起点 | 到期事件 | 默认阈值 | 重置条件 |
|---|---|---|---|---|
| 提交倒计时 | 上次提交 / 会话开始 | `timeout_no_submit` | 30min（10~60 可配） | 任一提交 |
| AC 倒计时 | 上次 AC / 会话开始 | `timeout_no_ac` | min(60min, 0.5D) | 任一 AC |
| 比赛剩余 | 比赛开始 | `contest_end` | D | 不重置 |

### 8.2 规则

- 只在 `session.status == active` 时走表；暂停（上厕所、切去看题解）全部暂停；
- 到期事件**只触发一次**，然后进入"已提醒"状态，直到重置条件满足才重新武装（避免一直响）；
- 同类超时事件每小时最多 2 次；
- 计时器事件独立于压力分**生成**，但仍完整走 §7.4 冷却与 §7.5 闸门——尤其第 8 条（`level >= L1`）：没有压力就不会有设备输出，计时器只负责"该提醒了"；
- 依赖网络时间：以服务器时间为准，本地时钟只做显示。

### 8.3 三种挑战模式（用户可选）

| 模式 | 启用的信号 | 适合 |
|---|---|---|
| `verdict`（提交结果挑战） | 判题事件 + 压力分 | 有实时判题的比赛 |
| `pace`（进度挑战） | 计时器 + 进度/停滞 | 练习模式、OI 赛制、无榜单 |
| `contest`（完整比赛模式） | 全部 | CF / AtCoder；牛客只能弱实时、不承诺排名 |

---

## 9. DGHub 插件层（薄适配器）

### 9.0 已核实的协议事实（2026-11-08 全文核对）

**文档在哪（重要）**：DGHub **没有独立文档站**，协议文档是 SDK 仓库里的 `docs/PLUGIN_DEVELOPMENT.md`。另外 DGHub 应用内「插件中心 → 外部插件」有两个更省事的入口：

- **「另存为…」** → 把这份协议指南保存成本机 `.md`；
- **「另存示范插件…」** → 保存官方 `demo_external.zip`，导入后可直接看官方示例源码。

| 项目 | 事实 |
|---|---|
| 仓库 / 许可 | `curtainsmall/dghub-sdk-toolkit`（Python + TS SDK、Studio 打包器），**AGPLv3**；PyPI `dghub-sdk` 0.16.2（Python ≥ 3.11） |
| 协议版本 | SDK v1；握手 `manifest.sdk="1"`；不兼容时 `hello_ack.accepted=false` + `reason` |
| 连接方式 | 插件 = 独立进程，JSON over WebSocket：`ws://{host}:{port}/ws/plugin?token={token}`；首帧 `hello{token, manifest}` → `hello_ack{sdk_version, accepted, reason}`；token 每次主程序启动重生成 |
| 启动注入 | DGHub 拉起插件时注入环境变量 `DGHUB_HOST / DGHUB_PORT / DGHUB_TOKEN / DGHUB_PLUGIN_ID / DGHUB_PLUGIN_ROOT`；子进程在 Windows Job Object 里，主程序退出会被内核清理 |
| 手动调试 | 不经 DGHub 拉起时，从 `GET /api/plugins/_session_token` 取 token（仅本机） |
| 目录结构 | `plugins/<id>/manifest.json` + `entry` 入口 + 可选 `vendor/`；工作目录 = 插件根；**不联网装依赖**（第三方库放 `vendor/`，或打包成 exe） |
| manifest | 必需 `id/name/version/sdk`；可选 `author/description/homepage/entry/capabilities/config_schema`；**未知字段会被拒（forbid）** |
| config_schema | 声明式生成设置页，9 种字段：`bool/percent/duration/number/text/select/channel/preset/path`；不写则前端只显示一个开关 |
| 客户端消息 | `hello` / `trigger`（推荐）/ `event`（只展示）/ `pulse` / `set_strength` / `adjust_strength` / `status` / `log` / `set_config` |
| 服务端消息 | `hello_ack` / `config`（握手后全量）/ `config_changed` / `device_info` / `stop` / `ping`·`pong` |
| 强度模型 | 每插件一层，设备 A/B 通道分别取所有插件层的**最大值**（绝不相加）；每插件有 baseline（`config.idle_strength`，默认 0）；`trigger.strength_mode=rollback` 临时偏移、`permanent` 改 baseline 并落盘 |
| 打包分发 | **手动 zip 就够**（zip 根目录放 `manifest.json`，或包在唯一一级子目录）；Studio（`dgstudio-cli build`）是可选的 exe/自包含打包方案；导入路径「插件中心 → 外部插件 → 导入 zip」，**无审核、无签名** |

**对设计的四个直接影响**

1. **`event` 是纯展示**（文档明确"不改强度、不触发高强度事件音效"）→ 正好做 M1 冒烟与校准预览；正式反馈走 `trigger`。
2. **`trigger` 语义 = 相对 baseline 的增量 + 时长**：`{action, delta_pct(0~100), strength_mode, duration_s(0~300), preset, channel, label}`。我们的强度是 0~1，落协议时 `delta_pct = round(intensity × 100)`；只用 `action="both"`（有 preset）或 `action="strength"`（无 preset）。
3. **协议本身没有租约 / 心跳 / 死手开关**：`trigger` 发出去就执行。所以 §7.6 的 lease **必须由 Engine↔插件这条我们自己的链路实现**，插件每次发 `trigger` 前校验 lease/nonce —— 这是"官方给不了、必须自己补"的安全层。
4. **强度是每插件一层取最大值**：别的插件也会动设备 → README/面板提示"只启用本插件"；分发无审核无签名 → 发布 SHA-256。

**两个实用细节**

- `status` 支持 `display_status` 与 `startup_check`（配置页显示"启动检查"步骤）→ 可直接拿来做"引擎连接 / 设备 / 校准"三态显示；
- `entry="main.py"` 用本机 Python 跑；`entry="xxx.exe"` 直接 Popen（Go/Rust/C# 都行）；Node 建议用 `.bat` 包一层。

**许可边界**：SDK 是 AGPLv3，但本仓库**不 import / vendor SDK 或官方 demo 代码**（协议按公开文档独立实现）→ 仓库采用 **MIT**（LICENSE 已就位），不继承 AGPLv3；若以后引入 SDK 再单独说明许可变化。DG-Lab 协议仓库 README 明确"未经授权请勿用于商业用途"且根目录无 LICENSE 文件 → 项目保持非商业用途，不打包其文档/示例（§13.2）。

### 9.1 职责边界（单一职责）

| 做 | 不做 |
|---|---|
| 订阅 Engine 的本地事件流 | 不自己轮询 OJ |
| 上报事件：`op:"event"` 展示、`trigger` 输出 | 不自己计算压力分 |
| 请求强度（带凭证）并二次钳制 | 不做网络请求（除本地 WS） |
| 上报插件状态/心跳 | 不持久化会话数据 |
| 接受 Engine / CLI 下发的参数（完全无界面） | 不要求用户打开 DGHub 配置页 |

**理由**：设备层越薄，越容易审查"什么情况下会让设备动"。

### 9.2 数据流（两条链路，别混）

```
Engine ──(本地 WS：event / intensity / lease)──► 插件 ──(trigger / event)──► DGHub ──► 设备
   ▲                                              │
   └──────────(ack / 状态 / 心跳)─────────────────┘
```

1. **Engine↔插件**：我们自己定义的协议，负责事件、强度、`lease_id + nonce`、回执；
2. **插件↔DGHub**：官方协议，只负责 `event` / `trigger` / `status` / `log`。

单位映射：`intensity ∈ [0,1]` → `delta_pct = round(intensity × 100)`；`duration_s` 直接用秒；`preset` 用 DGHub 原生预设名；`channel ∈ {a,b,both}`。

### 9.3 插件内的二次校验（不信任上游）

即使 Engine 通过了闸门，插件仍要再检查一遍（防 Engine 配置错误 / 版本不一致）：

```
若 delta_pct > 60 or duration_s > 3 or lease 过期 or nonce 已用过
   or action 含 waveform 但 preset 为空: 拒绝
```

显示型事件上报时 `strength_pct = round(intensity × 100)`（整数，`op:"event"` 只展示不生效）；真正输出走 `trigger`，强度按 `delta_pct` 传入。

**双重钳制**不是冗余，是纵深防御。

### 9.4 显示型事件（开发期验证手段）

`op:"event"` 只展示不刺激，正好用来先验证"判题准不准、去重对不对、事件节奏合不合理"——但这是**开发阶段的验证手段，不是产品形态**：产品必须走到 `trigger`。建议顺序：M1 先用 event 卡片跑通链路与校准，M2 起接真实输出。

### 9.5 分发与兼容

- **手动 zip 就够**：`starter/build_zip.ps1` 生成 `ojhc_probe.zip`；Studio 只在需要 exe/自包含时用；
- 启动时探测协议版本：`hello_ack.accepted=false` 或 `sdk_version` 主版本不符 → 只展示并提示，不尝试输出；
- 插件崩溃不影响 Engine（Engine 把它当可选消费者，`stop`/断线即视为失效）。

### 9.6 开发者上手路径（从零到"点一下"）

| 步骤 | 做什么 | 验收 |
|---|---|---|
| 1 | 装 DGHub 2.0（dghub.top），先确认它能正常驱动设备 | DGHub 自己能让设备动 |
| 2 | 读协议：SDK 仓库 `docs/PLUGIN_DEVELOPMENT.md`（或应用内「另存为…」） | 能看懂 `hello` / `trigger` / `event` 字段 |
| 3 | 导入 `starter/ojhc_probe.zip` | 插件列表出现"OJHC 冒烟探针" |
| 4 | 启用插件（`armed=off`） | 事件流出现测试卡片，状态"待命（仅展示）" |
| 5 | 打开 `armed` + `fire_on_start`，关掉再启用插件 | 一次 15% / 0.8s 脉冲，结束自动回正 |
| 6 | 改代码时可手动跑：`python main.py --port <port> --token <token>` | 控制台看到握手 + 配置推送 |

常见卡点（详见 `starter/README.md` 排查表）：zip 层级不对、`manifest` 有未知字段（会被 forbid）、`sdk` 不是 `"1"`、`preset` 名不存在、设备未连。

### 9.7 CLI-first 工作流（默认形态）

整个项目**不是 GUI 工具**，终端是主入口；DGHub 只是设备后端：

| 层 | 形态 | 用户是否操作 |
|---|---|---|
| Engine（监听 OJ / 压力分 / 闸门 / 计时器） | **命令行程序**：`ojhc run --contest 1847` + TUI 面板 | ✅ 日常只用它 |
| DGHub 插件 | **无界面进程**，Engine 的哑执行端 | ❌ 装一次就不碰 |
| DGHub 主程序 | GUI，负责设备蓝牙与波形库 | 仅"启用插件"一次 toggle |

**安装（纯 CLI，不需要 zip / 不需要 Studio）**

```powershell
# 协议文档：把插件目录放进 plugins/ 并重启 DGHub 即可
.\starter\install_plugin.ps1            # 自动探测 DGHub plugins 目录
.\starter\install_plugin.ps1 -DryRun    # 只看探测结果
```

**调试（纯 CLI，不经过 DGHub 拉起）**

```powershell
python starter\ojhc_probe\main.py --port 27020 --token <token> `
    --armed --intensity 20 --duration 0.8 --fire-now
```

`--armed/--intensity/--duration/--preset/--channel/--fire-now` 会**覆盖**配置（CLI 优先级最高）。

**配置来源优先级**

```
CLI 参数  >  Engine 配置文件（~/.ojhc/config.yaml）  >  DGHub 插件配置页（仅兜底）
```

插件 `manifest.config_schema` 只保留**最小安全开关**（armed / 上限 / 测试参数），不承载业务配置——这样 CLI 就是唯一配置入口，换机器也不用重新点一遍界面。

**为什么仍然需要"插件"这个形态**：DGHub 官方只对插件进程开放设备控制（`ws://…/ws/plugin`），没有对外部程序直接发 `trigger` 的 CLI/HTTP 接口。所以插件**必须存在，但可以完全无界面**。

**已实现（M1，`engine/`）**

| 命令 | 作用 |
|---|---|
| `python engine/ojhC.py doctor` | 自检：DGHub 端口 / 握手 / 设备 / 校准档案 / 冒烟插件 |
| `python engine/ojhC.py calibrate` | 校准向导：测 `I_p` / `I_c`，写 `~/.ojhc/device_profile.json` |
| `python engine/ojhC.py calibrate --dry-run` | 离线干跑（不输出电流），可配 `--script` 自动化 |
| `python engine/ojhC.py status` | 查看档案、可用区间、档位映射 |
| `python engine/ojhC.py selftest` | 纯本地自检，不联网 |

---

## 10. 配置项

### 10.1 会话配置（示例）

```yaml
# ~/.ojhc/config.yaml
session:
  mode: contest                  # contest | practice
  platform: codeforces
  contest_id: 1847
  handle: xuanh08
  goal:
    percentile: 0.25             # 目标前 25%
    expected_solves: null        # null = 由题目难度推算
  rating: 1400                   # 可留空自动查询

algorithm:
  weights: {rank: 0.35, progress: 0.30, stagnation: 0.20, recent: 0.15}
  gamma: 1.25
  ema_tau_s: 45
  update_min_interval_s: 30
  rho_max: 2.5
  delta_ref: 1.2
  progress_model: linear         # linear | per_problem(预留)
  late_phase_bias: true
  rank_freeze_policy: hold       # hold | disable

timers:
  no_submit_minutes: 30
  no_ac_minutes: null            # null = min(60min, 0.5D)，由比赛时长自动算

feedback:
  audio: true
  preset: casual                 # casual（3~8 次/场，L1 封顶 s=3）| hardcore（6~12 次/场，可到 s=5）
  enable_ac_reward: false        # AC 奖励通道，默认关
  # 未校准/调试时自动进入 dry-run：不写开关，由 device_profile 是否存在决定

device:
  profile_path: ~/.ojhc/device_profile.json
  calibrated_at: 2026-11-08      # 超过 30 天提示重校准
  channel: both                  # a | b | both
  perceptible_i: 0.38            # I_p：刚能感觉到（校准得到）
  comfort_i: 0.61                # I_c：舒适上限（校准得到）
  user_max: 0.50                 # = min(I_c × 0.8, 0.6)
  expired_after_days: 30

safety:
  allow_device_on_dom_source: false   # C 级源（AtCoder/牛客/洛谷扩展）默认不给设备
  hotkey_kill: "ctrl+alt+shift+x"
  device_hourly_cap: 30
  cooldown_s: 90

platforms:
  codeforces:
    poll_standings_s: 60         # 整场拉取，数 MB，别调太小
    poll_submissions_s: 5        # 单人 contest.status
    stop_polling_after_end: true # ended 后立即停轮询（固定行为，显式声明）
    use_rating_seed: auto        # auto | on | off
  atcoder:
    requires_login: true
    dom_poll_s: 30
  nowcoder:
    weak_realtime: true
  luogu:
    depends_on_contest_type: true   # OI 赛制自动降级
```

> 插件自己的 `manifest.config_schema` 只放最小安全开关（armed / 上限 / 测试参数）；比赛与算法参数**全部在 Engine 配置里**——CLI 是唯一配置入口（§9.7）。

### 10.2 配置校验

- 启动时校验：类型、范围、互斥项；
- 越界值 → **钳制并警告**，不静默接受（例如 `user_max_intensity: 0.9` → 钳到 0.6 并提示）；
- `config_version` 写入每个 session，赛后报告里带上，方便"换了配置之后效果变差"的排查。

---

## 11. 测试计划

### 11.1 测试金字塔

| 层 | 对象 | 工具 | 关键用例 |
|---|---|---|---|
| 单元 | 评分函数、衰减、EMA、滞回、去重键 | pytest | 单调性、边界值、缺失子分、γ 塑形、死区 |
| 单元 | 安全闸门 | pytest | 11 条条件逐条 deny；令牌桶并发；重启后计数保留 |
| 集成 | 适配器 → 状态机 | pytest + fixture JSON | 重复返回、乱序、PENDING→终态、终态回退、封榜跳变 |
| 回放 | 合成场景 JSONL | 自研 replay CLI | 两张场景表（3 个场景）全部断言（§5.6） |
| 混沌 | 网络/时钟/进程 | 注入式 | 断网 90s、429、时钟跳变、插件假死、进程 kill -9 |
| 安全 | 手动清单 | checklist | 急停热键、拔线、会话结束瞬间输出、配置越界 |

### 11.2 必须存在的"反向测试"

1. 刷新榜单 100 次，触发次数必须为 0（幂等性）；
2. 同一提交重复返回 20 次，只允许 1 次事件；
3. 封榜→解封跳变，device 输出必须为 0；
4. 把 `user_max_intensity` 配成 0.95，实际输出必须 ≤ 0.6；
5. kill -9 Engine 后 5s 内设备必须停止（死手开关）；
6. 比赛结束后追加一批提交，必须 0 事件；
7. 未校准（或校准已过期）状态下，device 输出必须为 0；
8. 感知区间配成 `I_c - I_p < 0.15` 的窄区间时，校准必须拒绝并提示。

### 11.3 回放彩排（发布前）

用本人 2~3 场历史比赛的时间线跑一遍，人工看压力曲线是否"像人话"：领先时是否长期 0、崩盘时是否平滑上升、WA 后是否陡升但立刻回落。这一步不能用指标替代。

---

## 12. 里程碑与工作量估算

### 12.1 如果只给你一周（MVP）

| 天 | 产出 | 验收 |
|---|---|---|
| D1 | 工程骨架 + CF 适配器 + SQLite；用 `install_plugin.ps1`（CLI）打通 DGHub | 能打印本人提交 verdict；DGHub 出现测试卡片（不刺激） |
| D2 | 把探针升级成正式插件骨架 + 校准向导 + 急停/死手 | 设备能被"点一下"；急停 1s 内停 |
| D3~D4 | 压力引擎 + 事件→强度映射（§7.3） | §5.6 三场景可复现；WA 能真的触发 |
| D5 | 会话运行（打一场真比赛）+ 面板 + 计时器 | 整场跑完，设备按规则动 |
| D6 | 手感调参（强弱/时长/节奏）+ 赛后报告 | 主观"感觉到了"≥80% |
| D7 | 缓冲与修复 | 问题清单 + 下一场改进项 |

MVP **必须包含设备与校准**（这是核心目的），不含 AtCoder/牛客/洛谷、不含浏览器扩展。第一周的目标不是"功能多"，而是"打完一场比赛，那几下确实有感觉、而且没乱动"。

### 12.2 完整路线

| 里程碑 | 内容 | 粗估（工作日） | 完成标准 |
|---|---|---|---|
| M0 | 骨架：适配器接口、SQLite、CF 适配器 | 1~2 | 能拉取并落库 |
| **M1** | **DGHub 插件冒烟 + 校准向导 + 急停/死手** | 2~3 | 单次触发可控；急停 1s 内停；校准可重跑 |
| M2 | 压力引擎 + 测试 + 回放 | 2~3 | §11.2 的 1/2/3/7 条通过（4/5/8 属 M4/M5） |
| M3 | 会话运行 + 面板 + 计时器 + 赛后报告 | 2~3 | 一场真比赛跑完 |
| M4 | 事件→强度映射 + casual/hardcore 预设 + 调参 | 2~3 | 主观"感觉到了"≥80% |
| M5 | 安全测试清单 + 混沌测试 | 2~4 | §11.2 反向测试 6 条全过 |
| M6 | AtCoder / 浏览器扩展（可选，第二阶段） | 5~8 | 没登录态时明确报"无数据"，绝不猜 |
| M7 | 打包、中英文 README、演示视频 | 2~3 | 可发布 |

**合计：18~29 个工作日**。**CF + 设备闭环（M0~M5 + M7）≈ 13~21 天**——设备在**第 2 个里程碑就能真的动**，不用等到最后。

> **M1 进度（2026-11-08）**：`engine/ojhC.py` 已实现 `doctor` / `calibrate` / `status` / `selftest` 四个子命令，校准向导可 `--dry-run` 离线干跑（已用脚本答案验证通过：I_p=20% / I_c=45% / 上限=36% / 档位 26-28-30-34%）。
>
> **M2 进度（2026-11-08，代码已写、待审阅）**：新增 `pressure.py`（§5 的可执行版本，selftest 断言 §5.6 数值一致）、`cf.py`（CF 官方 API + 离线夹具两套数据源）、`feedback.py`（事件→感受等级→校准映射→闸门模拟）、`runner.py`（会话流水线 + 回放 + 总结），CLI 增加 `run` 子命令。`ojhc run --mock` 可零网络跑完整流水线并打印 would-fire；`live` 代码路径已写但未在真实网络验证。
>
> **P1 多平台进度（2026-11-08）**：`platforms/` 抽象层落地（base / codeforces / vjudge / mock），引擎只依赖统一模型；新增 `store.py`（SQLite）与 `sessions` / `report` 命令；VJudge 判题可用、榜单接口核实前自动降级。下一步 P2：浏览器扩展 + AtCoder / 洛谷 / 牛客 适配器。
>
> **P2 浏览器桥接进度（2026-11-08）**：`bridge.py`（127.0.0.1 本地 HTTP + 一次性 token）+ `extension/`（MV3，自动发现端口 8765~8774）落地；AtCoder（standings/json + submissions/me）、洛谷（scoreboard 分页自算名次）、牛客（status-list，中文判题）collectors 完成；VJudge 榜单改为 `/contest/rank/single` 自算。已用模拟扩展数据端到端验证：中文判题 → 事件 → 闸门 → would-fire 全链路通过。

风险集中在 M6（登录态/反爬）与 M5（安全测试）；设备链路（M1）要尽早做，因为"手感"是最难估的部分。

---

## 13. 风险、合规与开放问题

### 13.1 技术风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 平台接口变动/反爬升级 | 采集失败 | 适配器隔离 + 扩展兜底 + 失败即降级，绝不猜 |
| 封榜/赛制差异 | 误判表现 | 两路硬判定 + 一路降权提示 + 降级模式 + 事实表固化（§4.2） |
| 轮询被限流/封 IP | 数据中断 | 串行单飞 + 退避 + 官方 API 优先 |
| 时钟漂移 | 压力分相位错 | 服务器时间基准 + 偏移告警 |
| 单人维护 | 平台适配烂尾 | 适配器接口稳定 + 只承诺"能力探测失败就降级" |
| 登录态依赖 | AtCoder/牛客/洛谷无登录态就没数据；平台改 DOM/风控即失效 | 扩展在用户页面内取数、不代登录；失败明确报"无数据"；绝不用猜的字段 |

### 13.2 安全与合规

1. **设备安全**：必须完成校准才能开输出；每场一次确认；11 条闸门；硬上限；死手开关；急停。
2. **平台 ToS**：优先使用官方 API；无官方 API 的平台通过浏览器在**用户已登录的页面**内取数（用户自己的会话，不代登录、不破解风控、不批量爬取他人数据）。
3. **DG-Lab 协议授权**：DG-Lab 协议仓库 README 明确"未经授权，请勿将本内容用于任何商业用途"（英文版同义），且根目录**没有 LICENSE 文件** → 本项目定位为**非商业用途**，不打包其文档/示例代码。DGHub SDK 为 **AGPLv3**，但本仓库未使用其代码（协议独立实现），故仓库采用 **MIT**（见 LICENSE）。
4. **数据隐私**：只保存自己的提交与榜单；不上传云端；日志本地保留 30 天。
5. **DGHub 侧插件叠加**：设备强度取所有插件层的最大值，本插件**无法阻止其它插件在同一设备上输出** → README 与面板必须提示"使用时只启用本插件"；急停仍然覆盖全部。
6. **分发完整性**：DGHub 外部插件是手动导入 zip、无审核无签名 → 发布包附 SHA-256 校验值与官方下载地址。

### 13.3 产品风险

| 风险 | 说明 | 对策 |
|---|---|---|
| 反馈频率太低 | 一场比赛事件少，可能"没感觉" | 事件密度设计（§7.9.5）+ casual/hardcore 双预设 + 感知校准，而不是靠调大强度 |
| 强度个体差异大 | 同一套数值，有人无感有人过猛 | 校准区间映射（§7.9.1），每人一套 `device_profile` |
| 玩法改变提交习惯 | 害怕 WA 就不敢提交 | 提供"训练模式"（只记录、不惩罚）；明确"这是娱乐挑战不是效率工具" |
| 用户预期错位 | 以为能提高水平 | README 里写清：不加 AC 率，只加紧张感 |
| 受众太小 | 郊狼 ∩ 算法竞赛 | 不靠扩大受众；先把核心体验做扎实，先服务自己和队友 |
| 上手门槛 | 要先装 DGHub、连设备、做校准 | 一页式上手清单 + 校准向导容错提示 + 每场一次确认 |

---

## 14. 需要你拍板的问题

这几条会实质影响实现，越早定越好（括号里是我基于事实核查的建议）：

1. **你的硬件情况**：郊狼是哪一代、几个通道？DGHub 平时在用吗？（决定 M1 怎么测、校准时用单通道还是双通道）
2. **强度偏好**：默认用 casual（3~8 次/场、L1 封顶 s=3）还是 hardcore（6~12 次/场、可到 s=5）？
3. **校准能接受吗**：首次花 3~5 分钟做校准，不校准就只能 dry-run——这是"有感觉"的前提条件。能接受吗？
4. **节奏偏好**：判题出来后**立刻**电，还是等 2~5 秒（先看到 WA 再电，更像"被提醒"而不是"被吓"）？
5. **首发平台**：只做 CF（设备闭环 13~21 天），还是 CF + AtCoder（+3~5 天，AtCoder 必须装扩展）？（建议先 CF）
6. **"只有郊狼能用"确认**：我按"不做无设备模式、面板仅用于调试审计"改的，如果理解偏了现在说。
7. **AC 奖励通道**：开不开？（默认关；开了有正反馈闭环，但风险面稍大）

> 第 1~3 条决定 M1/M4 怎么做；第 2、4 条决定默认手感；第 5 条决定排期。

---

## 15. 附录

### 15.1 术语表

| 术语 | 含义 |
|---|---|
| 封榜 / freeze | 比赛最后一段时间隐藏实时排名与部分成绩，结束后解封 |
| OI 赛制 | 比赛期间看不到判题结果，赛后统一评测 |
| seed | CF rating 预测中"按所有对手评级算出的期望排名" |
| 死手开关 | 必须持续收到心跳才允许输出，心跳丢失即停止 |
| 冻结（数据） | 数据过期时保持最后值但不更新、不升级、不触发 |

### 15.2 设计决策记录（ADR 摘要）

| 决策 | 结论 | 主因 |
|---|---|---|
| 压力分能否直接映射强度 | 不能 | 连续量抖动 → 不可控风险（D1） |
| 是否可以不做浏览器扩展 | 不行（若要 AtCoder/牛客/洛谷） | 这几家全是登录墙或无实时接口（§4.2.3 / D7） |
| 封榜期是否清空排名压力 | 不清空，冻结并降权 | 既避免误判又保留压力感（D3） |
| 插件是否自己算分 | 不算 | 单一职责，便于安全审查 |
| 商业版是否可做 | 暂不 | 授权与受众（§13.2） |

---

### 15.3 参考链接（事实依据）

| 用途 | 链接 |
|---|---|
| CF API 方法/对象/限制 | https://codeforces.com/apiHelp/methods · https://codeforces.com/apiHelp/objects · https://codeforces.com/apiHelp |
| AtCoder 访问限制公告 | https://atcoder.jp/posts/1027 |
| AtCoder 登录墙（实测 302） | https://atcoder.jp/contests/abc300/standings/json |
| 牛客赛后 ndjson 导出 | https://www.nowcoder.com/discuss/883755054304354304 |
| 洛谷比赛规则（OI 赛制不可见评测） | https://help.luogu.com.cn/manual/luogu/contest |
| 洛谷 scoreboard JSON（第三方逆向） | https://0f-0b.github.io/luogu-api-docs/contests |
| VJudge status/data（实测 200） | https://vjudge.net/status/data?draw=1&start=0&length=2 |
| VJudge 详情接口反爬（实测 400） | https://vjudge.net/solution/data/1 |
| DGHub SDK Toolkit | https://github.com/curtainsmall/dghub-sdk-toolkit |
| DGHub 插件协议 | https://github.com/curtainsmall/dghub-sdk-toolkit/blob/main/docs/PLUGIN_DEVELOPMENT.md |
| DGHub Python SDK 指南 | https://github.com/curtainsmall/dghub-sdk-toolkit/blob/main/docs/sdk-python.md |
| DGHub 官网 / 下载 | http://dghub.top/ |
| DG-Lab 商业使用条款 | https://github.com/DG-LAB-OPENSOURCE/DG-LAB-OPENSOURCE/blob/main/README_CN.md |

> 完整核查笔记（逐条 URL + 置信度）：`research/platform-data-sources.md`

### 15.4 修订记录

| 版本 | 变更 |
|---|---|
| v0.1 | 初稿（架构 / 压力算法 / 安全闸门 / 里程碑） |
| v0.2 | 独立审查后修正：封榜语义统一为"冻结 + 降权"；`S_recent` 加中性先验使衰减真正生效；γ 塑形方向与 EMA 示例数值修正；C 级数据源的设备权限统一；`confidence` 改为双规则取小；事件枚举、输出许可（lease）、端口发现机制统一；补齐 `n_official` / `start_cursor` / `epoch` 等缺失字段；参数表补 `recent_prior_w`、`rank_ema_tau_s` |
| v0.3 | 定位改为 **Coyote-first**：设备是唯一正式输出通道，删除"无设备也能玩"；新增 §7.9 手感设计与校准（感知区间映射、最短脉冲、事件节奏、校准过期、事件密度）；设备接入从 M5 提前到 M1；里程碑与验收指标按 casual/hardcore 双预设重排 |
| v0.3.1 | 按协议全文核对 §9.0：订正"协议没有租约/心跳"，lease 明确为 Engine↔插件自有层；补 DGHub 文档位置与应用内两个入口、"手动 zip 即可"的分发方式；新增 §9.6 开发者上手路径；新增 `starter/ojhc_probe` 可导入冒烟插件 + 排查表 |
| v0.3.2 | **CLI-first 明确化**：新增 §9.7（Engine 是 CLI，插件无界面，DGHub 只做设备后端）、配置来源优先级（CLI > Engine 配置 > DGHub 配置页）、`install_plugin.ps1` 纯 CLI 安装、插件 CLI 参数覆盖（`--armed/--intensity/--duration/--preset/--channel/--fire-now`） |
| v0.3.3 | M1 落地：交付 `engine/` 下 `ojhc` CLI（`doctor`/`calibrate`/`status`/`selftest`）；修正 §7.3 强度映射区间为 `[I_p, user_max]`（原 `[I_p, I_c]` 会把高档压成同一值），§7.9.1 增加 `user_max − I_p ≥ 10%` 校验；§12.2 记录 M1 进度 |
| v0.3.4 | M2 代码落地（待审阅）：`pressure.py` / `cf.py` / `feedback.py` / `runner.py` + `ojhc run --mock`；selftest 增加"§5.6 数值回归 / 闸门映射 / 离线回放"三项；记录 live 模式未验证、罚时未参与、夹具为合成数据等限制 |
| v0.3.5 | 仓库初始化与许可落地：采用远端初始提交的 **MIT LICENSE**；README 与 §9.0 / §13.2 的许可说明同步为 MIT（本项目不 import/vendor AGPL 的 SDK 代码）；建立 3 个功能提交并推送到 GitHub |
| v0.3.6 | **P1 多平台架构**：新增 `platforms/`（base 抽象、CF、VJudge、mock），引擎不再依赖具体 OJ；URL 识别五平台；VJudge 判题可用（榜单接口待核实→降级）；新增 SQLite 会话存储与 `sessions` / `report` 命令；selftest 增加判题归一化、URL 识别、回放、存储往返四项 |
| v0.3.7 | **P2 浏览器桥接**：`bridge.py`（本地 HTTP + token，C 级数据源）+ `extension/`（MV3：AtCoder/洛谷/牛客 collectors、自动发现 Engine）；VJudge 榜单改按 `/contest/rank/single` 自算（无 rank 字段、4/6 元组两种赛制、封榜启发式）；判题归一化支持中文 statusMessage；research v2（平台端点实测）归档 |
| v0.3.8 | **P3 设备链路 M4**：`device.py`（TCP JSONL、租约续期、一次性 nonce、ack 超时、estop）；`starter/ojhc_link` 生产插件（DGHub + Engine 双连接、租约过期归零、Engine 断开归零、只用 rollback）；`run --device` 要求真实校准档案、C 级源默认禁设备；SQLite 记录 allowed；selftest 增加设备链路往返测试 |
| v0.3.10 | **真机联调修复**：DGHub API 实际端口 8000（自动发现）；`trigger` 必须带波形 `preset`，空 preset 只会改强度不出波形（校准 50% 也无感）→ 校准默认「呼吸」并从 `/api/pulse-presets` 校验；`test-fire` 验证波形帧确实下发 |
| v0.3.13 | 真机手感联调：DGHub 的百分比 × App 上限 = 绝对强度（实测 40 上限时 100%→40）；无校准模式映射定为 App 上限的 65%~90%（上限 40 → 绝对 30~34，落在用户已确认“有感觉”的 28~40 区间）；硬顶随之为 App 上限的 90% |
| v0.3.15 | 插件页面可调参：插件 config_schema 暴露强度档位/波形/冷却/配额/level_up，插件通过 hello + config_changed 把参数转发 Engine（Engine 再钳一次）；Engine 侧 RunOptions 之外支持运行时 tuning，硬安全边界不变 |
| v0.3.14 | **真机验收通过**：修复插件 45% 死钳制（改为 90%）、mock 支持带节奏回放、App 上限过低时拒绝运行；真机确认 30~34 强度可感知，端到端链路（Engine→插件→DGHub→App→设备）验收完成 |
| v0.3.11 | 新增 `--replay-speed N`：已结束的比赛按提交时间差 N 倍速重放（不再因实时时钟超时瞬间退出）；文档示例去掉 `<id>/<handle>` 占位符（Windows CMD 会把 `<>` 当重定向） |
| v0.3.12 | 新增 `--trust-app-limit` 无校准模式：直接读取 App 强度上限并映射（硬顶永不越界）；校准改为 10% 步进 / 1.2s 脉冲 / 最高 80% 上限；档案记录设备上限并在 `status` 显示绝对强度 |
| v0.3.9 | **P4 测试与交付**：`engine/tests/`（stdlib unittest，19 例：压力数值回归 / 判题归一化 / 档位映射 / 闸门 / VJudge 榜单解析 / URL 识别 / SQLite / 桥接 / 设备租约 / 回放）；README 增加真机验收清单；发布 alpha Release（插件 zip 附件） |

---

*文档状态：草案 v0.3.4。§9.0 已按 DGHub 协议全文核对；§4.2 平台事实已于 2026-11-08 核查回填。已交付 `starter/ojhc_probe`（可导入 DGHub 的冒烟插件）与 `engine/` 的 M1/M2 代码（回放链路已通、真机未验证）。算法参数待首批回放数据校准，手感参数待真实设备校准后定稿。*

