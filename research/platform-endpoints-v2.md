# 比赛场景数据获取核实 v2（fetch 能拿到什么）

- 核查时间：2026-11-08（Asia/Shanghai）；方法：web_search / web_fetch 实测 + 文档/源码引用，未做浏览器实测。
- 置信度：高 = 本次实测响应或官方文档/源码；中 = 可信第三方或间接证据；低 = 单一弱来源；「未找到证据」= 检索不到可引用来源，不推测。
- 「实测」= 本次直接 GET 该 URL 观察到的状态码与响应体（匿名、无 Cookie）。

## 1. VJudge

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| 比赛榜单接口 | `GET /contest/rank/single/<contestId>` → **200 JSON，匿名可读**。不是 `/contest/rank/data`（404） | https://vjudge.net/contest/rank/single/33 · https://vjudge.net/contest/rank/single/866174 | 高（实测） |
| 榜单返回结构 | `{id,title,begin,length,isReplay,participants{uid:{type,name,nickname,image,members[]}},submissions[]}`。**无 rank/罚时字段**，排名需前端按赛制自算（ICPC 或按 `score` 求和） | 同上 | 高（实测） |
| `submissions` 行格式随赛制变化 | 老 ACM 赛：`[uid, probIdx, flag(0/1), 耗时ms]`（4 元）；计分赛：`[uid, probIdx, flag, 耗时ms, 得分, 满分]`（6 元，如 `[1158632,6,0,13130,61.0,110.0]`）→ 解析必须按长度分支 | https://vjudge.net/contest/rank/single/33 · https://vjudge.net/contest/rank/single/866174 | 高（实测） |
| 比赛提交列表 | `GET /status/data?contestId=<id>&un=<user>&draw=&start=&length=` → 200 JSON，`contestId`/`un` 过滤生效 | https://vjudge.net/status/data?contestId=33&un=Isun&draw=1&start=0&length=3 | 高（实测） |
| 提交返回字段 | `runId, userName, userId, oj, probNum, problemId, contestId, contestNum, status, statusType, processing, runtime(ms), memory(KB), language, time(epoch ms), access, contestOpenness, sourceLength` | https://vjudge.net/status/data?draw=1&start=0&length=20 | 高（实测） |
| **statusType 不是判题枚举** | 0 = Accepted；1 = 一切非 AC（WA / TLE / RE / Compile Error 全部为 1，实测样本各 1 例以上）。真实原因只在 `status` 字符串（如 `Time Limit Exceeded on test 2-10`、`Compile Error`、`Runtime Error`） | 同上 | 高（实测，20 条样本） |
| 判题中 | `processing: true` 表示未出结果；正常记录为 false，此时 `status` 才有意义 | 同上 | 高（实测） |
| 登录 / CSRF | 公开比赛（id 33）匿名可读榜单与提交；私有/需登录比赛返回 `{"i18nKey":"toast.contest.login_required","trustable":false}`。GET 未观察到 CSRF 要求 | https://vjudge.net/contest/600000 | 高（实测）/ 中（POST 未验证） |
| 封榜（freeze） | 页面模板存在 i18n 键 `contest.frozen`，说明有封榜状态；但 `rank/single` JSON **不含任何 frozen 字段**，无法从 JSON 判断是否封榜 | https://vjudge.net/contest/33 | 中 |
| 榜单表 DOM（兜底） | 第三方脚本从 `#contest-rank-table tbody` 取数：`td:nth-child(1)`=排名，`td:nth-child(2) div a`=账号，题目格 `.accepted` 内含过题时间 | https://greasyfork.org/en/scripts/534384-vjudgerankparse/code | 中 |
| 其它可用端点 | `GET /contest/data?draw=&start=&length=` 返回比赛列表数组（实测 200） | https://vjudge.net/contest/data?draw=1&start=0&length=5 | 高（实测） |

## 2. AtCoder

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| `standings/json` 登录墙 | 匿名 `GET /contests/abc300/standings` 与 `/standings/json` 均 302 → `/login?continue=...`，登录后才拿 JSON | https://atcoder.jp/contests/abc300/standings | 高（实测） |
| `standings/json` 字段 | `{Fixed, AdditionalColumns, TaskInfo[{Assignment,TaskName,TaskScreenName}], StandingsData[{Rank, UserName, UserScreenName, UserIsDeleted, Affiliation, Country, Rating, OldRating, IsRated, IsTeam, Competitions, AtCoderRank, TaskResults{task:{Count,Failure,Penalty,Score(×100),Elapsed(ns),Status,Pending,Frozen,Additional}}, TotalResult{...同结构含 Frozen}}], Translation}` | https://scrapbox.io/magurofly/AtCoder_APIまとめ · https://cromarmot.github.io/yxr-atcoder-core/ac_core.html | 中（字段名）/ 高（结构在第三方库 `StandingStruct` 中复现） |
| 冻结标记位置 | 冻结是**逐题 + 总计**：`TaskResults[task].Frozen`、`TotalResult.Frozen`；顶层 `Fixed` 表示榜单已定稿 | 同上 | 中 |
| `/submissions/json` | **不存在**：`/contests/abc300/submissions/json` 被当作 submission id 解析 → 404 `Submission not found.`（前期实测） | https://atcoder.jp/contests/abc300/submissions/json | 中 |
| 本人提交页 | `GET /contests/<id>/submissions/me` 存在，匿名 302 → 登录页；官方菜单说明其作用为「今までに自分が提出した解答を閲覧」 | https://atcoder.jp/contests/abc300/submissions/me · https://atcoder.jp/contests/ahc058 | 高（实测 + 官方文案） |
| 实时判题 | 主流赛制为 full-feedback：比赛期间即时判题并计分 | https://atcoder.jp/contests/abc300 | 高 |
| **封榜规则按场次不同** | 逐场在「コンテスト情報」里声明，无全局默认：jsc2025-final 写「16:10 より順位表の凍結を行う予定です」（14:10–17:10 → 末 60 分钟）；ajo2025-final 写「凍結はありません」 | https://atcoder.jp/contests/jsc2025-final · https://atcoder.jp/contests/ajo2025-final | 高 |
| ABC 是否有封榜 | ABC 页面只写 full-feedback，未出现凍結字样；不能据此外推到 ARC/AGC/决赛 | https://atcoder.jp/contests/abc300 | 中 |
| 同 IP 限流原文 | 「アクセスの制限：詳細は伏せますが、同一IPアドレスからの連続アクセス数を制限しております」——规则不公开，共享出口 IP 会被误伤 | https://atcoder.jp/posts/1027 | 高 |

## 3. 洛谷 Luogu

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| 成绩表接口 | `GET /fe/api/contest/scoreboard/:id?page=N`（第三方逆向文档仍列此路由）；**匿名实测 403** `AccessDeniedHttpException` → 需登录/需有查看权限 | https://0f-0b.github.io/luogu-api-docs/contests · https://www.luogu.com.cn/fe/api/contest/scoreboard/329950?page=1 | 高（实测 403）/ 中（路由） |
| 成绩表字段 | `GetScoreboardResponse = {scoreboard: List<Score>, userScore, userRank, firstBloodUID}`；`Score = {details{pid:{score, runningTime?}}, user, squad?, score, runningTime}`。**行内没有 rank 字段**，名次靠返回顺序；OI 下 `runningTime` 为总毫秒，其它赛制为总罚时秒 | https://raw.githubusercontent.com/0f-0b/luogu-api-docs/main/luogu-api.d.ts | 高（类型定义） |
| 权限信号 | `ContestData.canViewScoreboard` 明确给出「能否看榜」，可用于登录态/权限探测 | 同上 | 高 |
| cookie 依赖 | 文档未提 `__client_id`；实测未登录访问 `/record/list` 返回 **401 `{"needLogin":1}`**，说明会话 cookie 必需 | https://www.luogu.com.cn/record/list?pid=P1001&_contentOnly=1 | 高（实测）/ 未找到证据（`__client_id` 专名） |
| 官方 API 是否覆盖比赛 | 洛谷开放平台只提供题库接入与评测能力，未提及比赛/榜单 | https://docs.lgapi.cn/open | 高 |
| 提交接口 | `POST /fe/api/problem/submit/:pid`，**参数带 `contestId?`** → `{rid}`；POST 需 `referer: https://www.luogu.com.cn/` 与 `x-csrf-token`（且须用题目页的令牌，主页令牌会失效） | https://0f-0b.github.io/luogu-api-docs/problems | 高 |
| 提交记录接口 | `GET /record/list`，参数 `page/pid/contestId/user/status/language/orderBy` → 可按 `contestId` + `user` 精确取本人比赛提交 | https://0f-0b.github.io/luogu-api-docs/records · 类型同 d.ts | 高 |
| 实时评测推送 | WebSocket 频道 `record.track`，`channel_param = rid`；首帧 `ClientboundInitialRecordStatusMessageData`，后续 `status_push`（含 score/status/time/memory）与 `flush` | https://0f-0b.github.io/luogu-api-docs/records | 高（文档） |
| OI 赛制可见性 | 官方帮助中心：「OI 赛制：传统赛制，比赛期间**无法查看评测结果**，以最后一次提交为准」 | https://help.luogu.com.cn/manual/luogu/contest | 高 |
| 乐多 / ICPC / IOI | 三者「比赛期间**可查看评测结果**」；乐多得分 = 评测得分 × 0.95^(提交次数-1)，最低扣到 70%；ICPC 每次未过 +20 分钟罚时、按过题数/总耗时排名；IOI 取该题最高分 | 同上 | 高 |
| 赛制枚举 | `ContestListParams.method`：1=OI，2=ICPC，3=乐多，4=IOI（可用于自动分流是否封榜/是否显示结果） | https://raw.githubusercontent.com/0f-0b/luogu-api-docs/main/luogu-api.d.ts | 高 |

## 4. 牛客 Nowcoder

| 项目 | 结论 | 证据 URL | 置信度 |
|---|---|---|---|
| **比赛提交列表接口** | `GET /acm/contest/status-list?token=&id=<contestId>&page=&pageSize=` → **200 JSON，匿名可读已结束比赛**；`token=` 可为空 | https://ac.nowcoder.com/acm/contest/status-list?token=&id=142560&page=1&pageSize=10 | 高（实测） |
| 返回结构 | `{msg,code,data:{data:[...], isContestFinished, basicInfo{contestId,pageCount,pageSize,statusCount,searchUserName,pageCurrent}}}`；单行 `submissionId,userId,userName,index(题号字母),problemId,statusMessage,time(ms),memory(KB),length,language,languageName,languageCategoryName,isTeam,colorLevel,submitTime(epoch ms)` | 同上 | 高（实测） |
| 判定文案 | `statusMessage` 为中文：`答案正确/答案错误/运行超时/内存超限`（CE 未取到样本）→ 需自建中文映射表 | 同上 | 高（实测部分） |
| 只看本人 | 追加 `searchUserName=<昵称>` 生效（实测只返回该用户 1 条） | https://ac.nowcoder.com/acm/contest/status-list?token=&id=142560&page=1&pageSize=5&searchUserName=chengyue | 高（实测） |
| 榜单接口 | `rank-list`、`rank`、`rank/single`、`scoreboard` 全部 404（实测）→ **未找到证据**；比赛页是 SPA（HTML 只有「加载中...」），榜单不在首屏 HTML | https://ac.nowcoder.com/acm/contest/142560 | 未找到证据 |
| 登录 / 签名 | 未发现 `__ac_nonce`/`__ac_signature` 的任何可靠来源；实际机制是 `csrf_token` cookie + `gw-c.nowcoder.com/api/sparta/base-oauth/access-token?sceneType=2&token=<csrf_token>` 换 accessToken，判题走 `victorinox.nowcoder.com/api/service/judge/{submit,submit-status}` | https://raw.githubusercontent.com/ketherworks/nowcoder-mcp-server/main/packages/nowcoder/src/client.ts | 中（第三方实现，非官方文档） |
| 反爬 | 页面返回风控 challenge HTML（需人工在浏览器过验证）、429 限流；请求需 `X-Requested-With: XMLHttpRequest` + `Origin/Referer: ac.nowcoder.com` | 同上 | 中 |
| 封榜 | 封榜是比赛配置项（官方运营帖：「比赛已设置封榜时间，否则滚榜没有揭榜过程」）；`status-list` 只给 `isContestFinished`，**没有 frozen 标记** | https://www.nowcoder.com/discuss/883755054304354304 · status-list 实测 | 高（配置）/ 未找到证据（实时 frozen 标记） |
| 比赛进行中能否看 | 未验证（本日 19:00 才开赛）。已结束比赛匿名可读、且能看到他人提交 → 弱实时可行、实时性待验 | — | 未找到证据 |
| DOM 兜底线索 | 仅第三方测试 fixture（合成样本）：`.my-state-main/.my-state-item/.state-num`、`table.table-hover`、提交详情 `/acm/contest/view-submission?submissionId=`、分页 `data-page` | https://raw.githubusercontent.com/ketherworks/nowcoder-mcp-server/main/packages/nowcoder/test/fixtures/submissions.html | 低 |

## 5. 最不确定的清单

1. **牛客实时榜单**：只有 `status-list`（提交流），没有榜单 JSON；封榜期无法判定。
2. **VJudge 封榜**：仅见 UI i18n 键 `contest.frozen`，JSON 无标记，无法程序化识别。
3. **AtCoder `standings/json` 现行字段**：字段级来源是 2021 年第三方整理 + 2022 年库结构，官方无文档，可能已漂移。
4. **洛谷成绩表匿名可用性**：本次 403，无法确认是「必须登录」还是「必须参赛」。
5. **牛客 `status-list` 在比赛进行中是否可读**、是否有 200 条上限（`statusCount=200` 疑似截断）。

## 6. 对浏览器扩展方案的 5 条硬约束建议

1. **判定信号只吃「本人 + `processing=false`」的提交**：VJudge `statusType` 只有 0/1 二值（非 AC 一律 1），AtCoder/洛谷有细粒度但需解析文本或 `status` 码；压力分必须能容忍"只知道 AC/非 AC"的降级档。
2. **榜单按「快照 + 差分」处理，别找 rank 字段**：VJudge `rank/single` 与洛谷 `Score` 都没有 rank，AtCoder 有 `Rank` 但登录墙；排名必须自算，且要处理 `submissions` 行长度不同的两套格式。
3. **封榜期一律静默排名类信号**：AtCoder 用 `Frozen`/`Fixed`、牛客用 `isContestFinished`、VJudge 完全无标记 → 统一策略是「开赛时长 > 赛程 - 60min 且无解冻事件时，只用本人 verdict，不用排名/分数」。
4. **每个平台都必须实现登录态探测与显式降级**：AtCoder 302→`/login`、洛谷 403/`needLogin`、牛客 challenge/429、VJudge `toast.contest.login_required`，扩展应把 `degraded` 状态回传而不是静默重试。
5. **轮询节流 + 端点易变自检**：AtCoder 同 IP 连访限制不公开、牛客有 429 与风控、洛谷非 GET 需 `referer`+`x-csrf-token`（且令牌按页面失效）；建议扩展侧统一 ≥3s 最小间隔、指数退避、单标签页去重，并对每个端点做「字段缺失即降级 + DOM 兜底」双路径。
