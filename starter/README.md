# 起步：从零跑通 DGHub 冒烟插件（M1）

这个目录是给 **OJ Hardcore Engine** 准备的最小可运行插件（`ojhc_probe`），目标只有四个：
**能看到卡片、能点一下、能停、能确认链路通了**。默认不输出任何电流。

---

## 0. 先解决"文档在哪"

**DGHub 没有独立的开发者文档站**，协议文档就在 SDK 仓库里，所以网页搜索基本搜不到。原始来源（已核实）：

| 内容 | 链接 |
|---|---|
| 插件协议全文（最重要） | https://github.com/curtainsmall/dghub-sdk-toolkit/blob/main/docs/PLUGIN_DEVELOPMENT.md |
| Python SDK 使用指南 | https://github.com/curtainsmall/dghub-sdk-toolkit/blob/main/docs/sdk-python.md |
| SDK / Studio 仓库根 | https://github.com/curtainsmall/dghub-sdk-toolkit |
| DGHub 官网与下载 | http://dghub.top/ |
| PyPI 包（可选） | https://pypi.org/project/dghub-sdk/ |

**更省事的两个入口（在 DGHub 应用里）**：

1. 插件中心 → 外部插件 → **「另存为…」**：把这份协议指南存成本机 `.md`，离线看；
2. 同一页 → **「另存示范插件…」**：保存官方 `demo_external.zip`，导入后就能看到官方示例源码，直接复制改名。

> 官方协议文档里还有一句很重要的：协议字段的权威定义以主程序内置 SDK 为准，文档与发版包同步更新。

---

## 1. 前置条件

- [ ] DGHub 2.0 已安装（http://dghub.top/）
- [ ] 郊狼设备已经能在 DGHub 里正常控制（先确认这一步，不要跳）
- [ ] Python 3.11+（`.py` 插件由 DGHub 用本机 Python 拉起）
- [ ] 可选：`pip install websockets`（只有"手动调试"方式需要；DGHub 内部已提供）

---

## 2. 跑起来（两种方式，选一个）

### 方式 A：交给 DGHub 自动拉起（推荐）

1. 在本目录执行 `.\build_zip.ps1`，生成 `ojhc_probe.zip`
   （或者手动压缩：zip 根目录直接放 `manifest.json` + `main.py`，不要再套一层）
2. DGHub → 插件中心 → 外部插件 → **导入 zip**
3. 启用插件
4. 打开插件配置页，**先不要**打开"允许真实输出"
5. 预期：DGHub 事件流出现一张"OJHC 测试"卡片；状态显示"待命（仅展示，不输出）"
6. 确认正常后：打开「允许真实输出」+ 打开「启用插件时自动发一次测试脉冲」→ 把插件关掉再打开 → 约 2 秒后收到一次 **15% / 0.8s** 的脉冲
7. 想再测一次：把「启用时自动发一次测试脉冲」关掉再打开（不用重启插件）

### 方式 B：手动调试（不经过 DGHub 拉起）

适合改代码时快速迭代：

1. 先让 DGHub 保持运行；
2. 浏览器打开 `http://127.0.0.1:27020/api/plugins/_session_token`，复制返回的 token
   （端口以 DGHub 实际监听为准，协议文档示例是 27020）
3. 在插件目录执行：

```powershell
python main.py --port 27020 --token <粘贴token>
```

4. 控制台会打印握手结果；在 DGHub 里改配置会实时推送过来。

### 方式 C：纯 CLI（终端党推荐，不用 zip、不用点配置页）

```powershell
# 1) 一条命令把插件目录拷进 DGHub 的 plugins/（自动探测；找不到就 -PluginsDir 指定）
.\install_plugin.ps1

# 2) 重启 DGHub（重启后插件会被识别）
#    在插件中心启用一次 —— 这是整个流程里唯一必须点 GUI 的地方

# 3) 之后全部在终端，参数走 CLI，不用打开插件配置页
python ojhc_probe\main.py --port 27020 --token <token> --armed --intensity 20 --duration 0.8 --fire-now
```

CLI 参数会**覆盖**插件配置：`--armed / --intensity / --duration / --preset / --channel / --fire-now`。
等 Engine 写完，连"启用插件"也会由 Engine 提示或代跑（DESIGN.md §9.7），日常就只剩 `ojhc run`。

> zip 方式（方式 A）保留给"不想动插件目录"的人；两种方式效果一样。

---

## 3. 验证清单（M1 验收）

- [ ] 控制台打印 `握手成功（sdk 1.0）`
- [ ] DGHub 事件流里出现"OJHC 测试"卡片
- [ ] 插件状态卡：`待命（仅展示）` → 打开开关后变 `就绪（真实输出已开启）`
- [ ] 打开开关后有一次短脉冲，**结束后强度自动回到 baseline**（rollback，不是永久加成）
- [ ] 关插件 / 拔设备 → 不再有任何输出
- [ ] 直接关 toggle（或 kill 进程）→ DGHub 自动清理，不留孤儿进程

---

## 4. 排查表

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 导入后找不到插件 | zip 层级不对 | zip 根目录必须直接是 `manifest.json`，或只包一层同名目录；用 `build_zip.ps1` 最稳 |
| 插件显示"未运行" | `entry` 没写 / Python 不在 PATH / 缺 `websockets` | 先在本目录 `python main.py --selftest`；再确认 Python 3.11+ |
| 握手被拒 `accepted:false` | `sdk` 版本不对；manifest 里有未知字段 | `sdk` 必须是 `"1"`；**manifest 只能放协议文档列出的字段**（主程序对未知字段是 `forbid`） |
| 有卡片但没感觉 | 没开 armed / 设备未连 / 强度低于感知阈值 / 脉冲太短 | 看"启动检查"三步；强度先 20~30%，时长 ≥0.8s |
| `trigger` 没反应 | `preset` 名称不存在 | 先把预设留空（只改强度），或从 DGHub 下拉里选（`type:"preset"` 会自动拉列表） |
| 手动调试连不上 | 端口或 token 错 | token 每次 DGHub 重启都会变，重新取 |
| 控制台中文乱码 | Windows 控制台编码 | `$env:PYTHONIOENCODING='utf-8'` 或以 UTF-8 方式运行 |

---

## 5. 安全边界（和正式版的关系）

这个探针刻意的限制：

- 只读配置、只发一次 `rollback` 脉冲、**默认不输出**；
- 强度/时长在代码里再钳一次（`HARD_CAP_PCT=40`、`HARD_MAX_DURATION_S=3`）；
- 只用 `strength_mode:"rollback"`，**永远不用 `permanent`**，所以不会留下"越电越强"的状态；
- 收到 `stop` / 断线 / Ctrl+C 立即退出。

正式插件（M1 之后）会变成 Engine 的"哑终端"：所有强度由 Engine 的 11 条闸门算好，插件只负责执行 + 二次钳制。

> **一个重要发现**：DGHub 协议本身**没有**"租约 / 心跳 / 死手开关"机制（`trigger` 只认强度、波形、模式、通道）。所以 DESIGN.md 里的 lease 必须由**我们自己的 Engine↔插件链路**实现，不能指望 DGHub。

---

## 6. 下一步（M1 正式骨架）

1. Engine 用本地 WS 连插件（和 DGHub 的 WS 分开），推 `event`/`intensity` 指令 + `lease` 续期；
2. 插件侧：收指令 → 校验 lease/nonce → 调 DGHub `trigger`；lease 过期立即停；
3. 校准向导：跑 §7.9.1 的流程，产出 `device_profile.json`；
4. 再把 CF 适配器的真实 WA/AC 事件接进来。

协议字段和消息样例见 `DESIGN.md` §9.0 与上面的官方文档链接。

---

## 生产插件：`ojhc_link`（M4，真实输出用）

`ojhc_probe` 只是冒烟测试；**真正要电的时候用 `ojhc_link`**：

```powershell
# 安装两个插件（probe + link）
.\install_plugin.ps1
# 或只装生产插件
.\install_plugin.ps1 -Plugin ojhc_link
```

然后：

1. 重启 DGHub → 插件中心启用「OJHC 硬核模式」；
2. 引擎侧带 `--device` 跑比赛（必须已有真实校准档案）：
   ```powershell
   cd ..\engine
   python ojhC.py run --url <比赛URL> --handle <handle> --device
   ```
3. 引擎会写 `~/.ojhc/device_link.json` 并打印 TCP 端口；插件自动发现并连上；
4. 会话结束/异常/Ctrl+C 都会**急停归零**；拔 DGHub、关插件、杀引擎同样归零。

安全语义：插件只认 `lease`（2s 续期，5s 过期）+ 一次性 `nonce` 的 `output` 指令；租约过期 1.35× 立即归零；绝不用 `permanent`。
