# OJ 平台数据源事实核查笔记

- 核查时间：2026-11-08（Asia/Shanghai）
- 方法：仅用 web_search / web_fetch。标注「实测」= 本次直接请求该 URL 观察到的响应；其余为文档引用。
- 置信度：高 = 官方文档或本次实测；中 = 可信第三方/间接证据；低 = 单一弱来源；「未找到证据」= 检索不到可引用来源，不作推测。

## 1. Codeforces

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| contest.standings 参数 | contestId(必填)、asManager、from、count、handles(≤10000)、room、showUnofficial、participantTypes。**普通公开赛匿名调用只允许"仅 contestId 一个参数"**，带 from/count/handles 等会被拒 | https://codeforces.com/apiHelp/methods | 高 |
| 返回结构 | `{contest, problems, rows}`；RanklistRow 含 party、rank、points、penalty、successfulHackCount、unsuccessfulHackCount、problemResults[]（points/penalty/rejectedAttemptCount/type/bestSubmissionTimeSeconds）、lastSubmissionTimeSeconds | https://codeforces.com/apiHelp/objects | 高 |
| 是否返回 rating | 否。party.members 只有 handle/name，rating 需另调 user.info 拼接 | https://codeforces.com/apiHelp/objects | 高 |
| freeze/frozen 字段 | Contest 级 `frozen`(bool) + `freezeDurationSeconds`；frozen=true 时 ranklist 只含封榜前提交。**行级无冻结标记**，RanklistRow 无 isFrozen 字段 | https://codeforces.com/apiHelp/objects | 高 |
| contest.status 参数 | contestId、asManager、handle（单数，非 handles）、from、count、includeSources | https://codeforces.com/apiHelp/methods | 高 |
| 返回字段 | Submission: id、contestId、creationTimeSeconds、relativeTimeSeconds、problem{index,...}、author、programmingLanguage、verdict、testset、passedTestCount、timeConsumedMillis、memoryConsumedBytes、points | https://codeforces.com/apiHelp/objects | 高 |
| verdict 取值 | FAILED/OK/PARTIAL/COMPILATION_ERROR/RUNTIME_ERROR/WRONG_ANSWER/TIME_LIMIT_EXCEEDED/MEMORY_LIMIT_EXCEEDED/IDLENESS_LIMIT_EXCEEDED/SECURITY_VIOLATED/CRASHED/INPUT_PREPARATION_CRASHED/CHALLENGED/SKIPPED/**TESTING**/REJECTED/**SUBMITTED** → 含"测试中"状态 | https://codeforces.com/apiHelp/objects | 高 |
| 频率限制原文 | "API may be requested at most 1 time per two seconds."；超限返回 status=FAILED + comment "Call limit exceeded" | https://codeforces.com/apiHelp | 高 |
| user.info 批量上限 | handles 分号分隔，No more than 10000 handles | https://codeforces.com/apiHelp/methods | 高 |
| 封榜规则（最后一小时） | 常规 Round 确有封榜且通常覆盖最后 1 小时，但未拿到官方规则明文；封榜时长逐场不同，以 `freezeDurationSeconds` 为准 | https://codeforces.com/apiHelp/objects | 中 |
| 解封后是否跳变 | 是。frozen 只回放封榜前提交，解封即恢复完整榜单，无增量/事件流接口 | https://codeforces.com/apiHelp/objects | 中 |

## 2. AtCoder

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| `/contests/<id>/standings/json` | 路由存在，但匿名访问 **302 跳转 `/login?continue=...`**（登录墙），拿不到 JSON | https://atcoder.jp/contests/abc300/standings/json | 高 |
| `/contests/<id>/submissions/json` | **不存在**：实测 404 且报 "Submission not found."（"json" 被当作 submission id 解析） | https://atcoder.jp/contests/abc300/submissions/json | 高 |
| 榜单/提交页面 | HTML 页同样 302 到登录页；virtual standings 亦然 | https://atcoder.jp/contests/abc300/standings | 高 |
| JSON 字段结构 | 未登录无法核实；未找到任何字段级文档 → 未找到证据 | — | 未找到证据 |
| 官方稳定性承诺 | 未找到 API 文档、版本化或 SLA 声明 | https://atcoder.jp/ | 未找到证据 |
| 访问限制 | 官方公告：限制同一 IP 连续访问数（细节不公开），共享出口 IP 可能被误伤 | https://atcoder.jp/posts/1027 | 高 |
| 封榜规则 | 未找到官方明文；ABC300 规则页只写 full-feedback，未提冻结 | https://atcoder.jp/contests/abc300/rules | 未找到证据 |
| 赛制 | ABC 为 full-feedback（比赛期间即时判题并计分），规则页未提封榜 | https://atcoder.jp/contests/abc300 | 高 |

## 3. 牛客 Nowcoder

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| 实时榜单 JSON 接口 | 未找到官方文档、路径或稳定公开接口；只有网页榜单 | — | 未找到证据 |
| 官方赛后导出 | 官方运营帖：ICPC 类比赛结束后可导出 ICPC Tools 兼容 `.ndjson` 滚榜文件（兼容 ICPC Contest API / DOMjudge event feed） | https://www.nowcoder.com/discuss/883755054304354304 | 高 |
| 导出前置条件 | 比赛已结束、全部判题完成、已设置封榜时间；存在排队/编译中/运行中/判题中/等待系统测试的提交会阻止导出 | https://www.nowcoder.com/discuss/883755054304354304 | 高 |
| 封榜 | 封榜是比赛配置项（"比赛已设置封榜时间，否则滚榜没有揭榜过程"） | https://www.nowcoder.com/discuss/883755054304354304 | 高 |
| 登录/签名 | `__ac_nonce`/`__ac_signature` 未找到官方或可靠来源；社区"统一签名"帖为个人博客，描述通用签名思路，非牛客官方参数说明 | https://www.nowcoder.com/discuss/353147960185790464 | 低 |
| 只能靠浏览器抓取的旁证 | 第三方 OpenCLI 将 nowcoder 标为 🌐/🔐，多数命令要求 Chrome 已登录 + Browser Bridge 扩展在页面上下文取数 | https://raw.githubusercontent.com/jackwener/OpenCLI/main/docs/adapters/browser/nowcoder.md | 中 |

## 4. 洛谷 Luogu

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| 成绩表接口 | `GET /fe/api/contest/scoreboard/:id?page=N` 返回 **JSON**（`scoreboard.result[].user.name`、`details`），第三方逆向文档 | https://0f-0b.github.io/luogu-api-docs/contests | 中 |
| 是否需要登录 cookie | 需要 `__client_id` 等登录 cookie（社区代码显式带 Cookie 头才取到数据） | https://wenku.csdn.net/answer/2a0ce437993b42668a1ae2d4ba86618e | 中 |
| 官方 API | 未见官方比赛榜单 API 文档；帮助中心仅链到"洛谷开放平台"入口（未核实是否覆盖比赛） | https://help.luogu.com.cn/manual/luogu/contest | 中 |
| 封榜规则 | 未找到明文规则 → 未找到证据 | — | 未找到证据 |
| OI 赛制可见性（官方） | 比赛期间**无法查看评测结果**，以最后一次提交为准 | https://help.luogu.com.cn/manual/luogu/contest | 高 |
| 乐多/ICPC/IOI 赛制 | 比赛期间**可查看评测结果**；乐多按测试点得分并有 0.95^(n-1) 衰减 | https://help.luogu.com.cn/manual/luogu/contest | 高 |
| 第三方库现状 | `luogu-api-python` 在 PyPI 存在（版本 0.0.2，非官方） | https://pypi.org/project/luogu-api-python/ | 中 |

## 5. VJudge

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| `/status/data` 是否存在 | **存在**。实测 200 JSON：`data[]{runId, userName, userId, oj, probNum, problemId, status, statusType, processing, runtime, memory, language, time, access, sourceLength}` + recordsTotal/recordsFiltered/draw | https://vjudge.net/status/data?draw=1&start=0&length=2 | 高（实测） |
| 参数 | `draw`、`start`、`length` 生效（返回 draw=1、2 条记录）；其余过滤参数未逐一验证 | 同上 | 中 |
| `/solution/data/{runId}` 是否存在 | **存在但反爬**：实测 400 `{"error":{"i18nKey":"solution.error.crawler_blocked","trustable":false}}` | https://vjudge.net/solution/data/1 | 高（实测） |
| 是否必须登录 | status/data 匿名可读；solution/data 需正常会话/Referer，否则被判定爬虫 | https://vjudge.net/solution/data/1 | 中 |
| 已知可靠性问题 | 非官方端点、无文档无 SLA；`recordsTotal=9999999` 为哨兵值，分页边界不可信；反爬策略会随时拦截 | https://vjudge.net/status/data?draw=1&start=0&length=2 | 高（实测） |
| 判题状态表达 | `status` 为字符串（如 "Accepted"）+ `statusType` 数值 + `processing` 布尔（表示判题中） | https://vjudge.net/status/data?draw=1&start=0&length=2 | 高（实测） |

## 6. DGHub 2.0 插件生态

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| 官网 | 站点标题为 "DGHub 2.0 — 让 DG-Lab 连接一切" | http://dghub.top/ | 高 |
| SDK Toolkit 仓库 | `curtainsmall/dghub-sdk-toolkit`：Python + TypeScript SDK、图形化打包 Studio，AGPLv3；PyPI `dghub-sdk` 0.16.2（2026-08-21） | https://github.com/curtainsmall/dghub-sdk-toolkit · https://pypi.org/project/dghub-sdk/ | 高 |
| 协议文档 | `docs/PLUGIN_DEVELOPMENT.md`（对应 SDK v1，握手 manifest.sdk="1"） | https://github.com/curtainsmall/dghub-sdk-toolkit/blob/main/docs/PLUGIN_DEVELOPMENT.md | 高 |
| 通信方式 | 插件=独立进程，JSON over WebSocket：`ws://{DGHUB_HOST}:{DGHUB_PORT}/ws/plugin?token={DGHUB_TOKEN}`；首帧必须 `hello`（带 token+manifest），主程序回 `hello_ack{accepted}`；token 每次启动重生成；调试可 `GET /api/plugins/_session_token` 取 | 同上 §1/§4 | 高 |
| 目录结构 | `plugins/<id>/manifest.json`（必需）+ entry 入口 + `vendor/`（可选依赖）+ 任意资源；工作目录固定为插件根；不联网安装依赖 | 同上 §2 | 高 |
| 配置 schema | `manifest.config_schema = [{section, fields:[{key,type,label,default,min,max,step,options,description}]}]`；type ∈ bool/percent/duration/number/text/select/channel/preset/path；不写则前端只显示开关 | 同上 §3/§7 | 高 |
| 事件上报字段 | `{"op":"event","label":必填,"name":必填,"username":可选,"strength_pct":可选(int,仅展示),"duration":倒计时秒默认1.0,"event_id":可选(去重)}` | 同上 §8.1 | 高 |
| 只展示不刺激的事件类型 | **有**：`event` 明确"只显示倒计时卡片，不会自动改强度，也不会触发高强度事件音效"（对比 `trigger`/`pulse`/`set_strength` 才动强度或波形） | 同上 §5/§6 | 高 |
| 强度模型 | 每插件一层，设备 A/B 通道取所有插件层最大值、绝不相加；trigger 支持 rollback/permanent、channel a/b/both | 同上 §6 | 高 |
| 打包工具 | **DGHub SDK Studio**（前身 DGHub SDK Packer），安装后 CLI `dgstudio-cli build`；产物固定 `.zip` | https://github.com/curtainsmall/dghub-sdk-toolkit （README "Studio"） | 高 |
| 分发/审核 | 分发=前端"插件中心 → 外部插件 → 导入 zip"；未找到任何商店、签名或审核流程文档 | 同上 §9 | 高（导入）/ 未找到证据（审核） |

## 7. DG-Lab 商业使用条款

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| 条款原文位置 | `README_CN.md` 开头 blockquote（"郊狼情趣脉冲主机"章节之前）："未经授权，请勿将本内容用于任何商业用途。如有商业合作需求，请[联系我们]" | https://github.com/DG-LAB-OPENSOURCE/DG-LAB-OPENSOURCE/blob/main/README_CN.md | 高 |
| 英文对应 | `README.md` 同一位置："Commercial use of this content requires prior authorization." | https://github.com/DG-LAB-OPENSOURCE/DG-LAB-OPENSOURCE/blob/main/README.md | 高 |
| 仓库许可文件 | 仓库根目录无 LICENSE 文件（`/LICENSE` 实测 404），条款仅写在 README | https://raw.githubusercontent.com/DG-LAB-OPENSOURCE/DG-LAB-OPENSOURCE/main/LICENSE | 高（实测） |

## 不确定项清单

1. **Codeforces 封榜时长**：未拿到官方规则明文，"最后一小时"来自通行说法；实际以每场 `freezeDurationSeconds` 为准。
2. **AtCoder 封榜规则**：完全未找到官方说明（规则页只提 full-feedback），不能假设有/无封榜。
3. **AtCoder `standings/json` 字段结构**：登录墙导致匿名取不到样本，字段名与是否含 frozen 标记均未知。
4. **牛客实时榜单 JSON**：未找到任何可引用接口；现有唯一官方能力是"赛后 ndjson 导出"。
5. **牛客反爬参数**（`__ac_nonce`/`__ac_signature`）：未找到官方或可靠来源。
6. **洛谷封榜规则**：未找到；且 scoreboard 接口的登录/分页要求只有社区代码佐证，可能已变动。
7. **VJudge `/solution/data/{runId}` 返回字段**：被 `crawler_blocked` 拦截，未取到正样本。
8. **DGHub 审核机制**：未找到；文档只写 SDK v1，"2.0"仅见官网标题，二者版本对应关系未核实。

## 对设计的硬约束建议

1. **封榜期间不得用排名/分数做惩罚信号**：CF `frozen=true`、牛客可配封榜、解封时榜单一次性跳变；应只吃"本人 verdict + relativeTimeSeconds"，或显式进入"封榜静默模式"。
2. **Codeforces 必须按"整场拉取 + 本地缓存"设计**：匿名 standings 只允许仅带 contestId（不能分页/按 handles 过滤），叠加 1 次/2 秒硬限流，大榜单次响应大且无法增量；排名要 rating 还得再花一次 `user.info`（≤10000 handles）配额。
3. **AtCoder 默认不可用，除非有用户登录态**：standings/submissions 均 302 登录墙 + 同 IP 连访限制；无登录态时只能做到"看不到排名"，压力分需降级或走浏览器扩展注入会话。
4. **洛谷必须按赛制分流、牛客不可承诺实时排名**：洛谷 OI 赛制比赛期间官方明确看不到评测结果；牛客无实时 JSON 榜单，只有赛后 ndjson → 这两个平台只能做"赛后/弱实时"信号，不能作为实时惩罚输入。
5. **DGHub 侧只用 `op:"event"` 做默认反馈**：它只显示倒计时卡片、不改强度不触发高强度音效；任何设备输出走 `trigger` 且需用户显式开启。插件靠手动导入 zip、无审核与签名 → 分发需自建来源校验。
