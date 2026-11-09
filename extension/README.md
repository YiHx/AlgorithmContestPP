# 算法竞赛++ 浏览器桥接

扩展在你打开的比赛页读取数据，再发给本机引擎（`127.0.0.1:8765+`）。牛客、AtCoder 和洛谷需要扩展，Codeforces 用扩展可以加快判题反馈。

## 安装浏览器扩展

使用 121 或更新版本的 Chrome 或 Microsoft Edge。Firefox 需要 140 或更新版本。

| 浏览器 | 安装方法 |
| --- | --- |
| Chrome | 打开 `chrome://extensions`，开启「开发者模式」，点「加载已解压的扩展程序」，选择 `extension` 文件夹。 |
| Microsoft Edge | 打开 `edge://extensions`，开启「开发人员模式」，点「加载解压缩的扩展」，选择同一个 `extension` 文件夹。[微软说明](https://learn.microsoft.com/en-us/microsoft-edge/extensions-chromium/getting-started/extension-sideloading) |
| Firefox | 打开 `about:debugging#/runtime/this-firefox`，点「临时载入附加组件」，选择 `extension/manifest.json`。关闭并重新打开 Firefox 后需要重新载入。长期安装需要 Mozilla 签名的 XPI 包。[Mozilla 说明](https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/) |

安装后，用 DGHub 或[命令行](../engine/README.md)启动引擎。用装了扩展的浏览器登录并打开同一场比赛。扩展弹窗会显示当前会话、上次成功采集时间和错误。更新代码后，在扩展管理页点重新加载，再刷新比赛页。

Firefox 的网站内容权限用于读取比赛标题、判题和榜单，并发给本机引擎。扩展不会读取或转发账号密码。

## 各平台支持情况

| 平台 | 判题结果 | 实时榜单 | 说明 |
|---|---|---|---|
| Codeforces | 本人提交页 | 引擎用官方 API 读取 | 扩展用于快速通道 |
| AtCoder | ✅ 本人提交页 `/contests/<id>/submissions/me` | ✅ `standings/json`（登录后） | 封榜按场次声明；冻结标记取 `TaskResults[].Frozen` |
| 洛谷 | ⚠️ 暂未接入（v1 只上报榜单） | ⚠️ 成绩表分页找自己，名次按顺序推算 | 需要登录；OI 赛制比赛期间看不到评测结果 |
| 牛客 | ✅ `status-list`（中文 statusMessage） | ❌ 无榜单接口（研究确认 404） | 只有提交信号，压力分自动降级 |

## 已知限制

- 洛谷当前只上报榜单，缺少可用的本人比赛判题来源；
- 牛客没有榜单 → 压力分只用进度/停滞/提交趋势（设计里允许的降级档）；
- CF 和牛客通常每秒查询一次，发现新提交待判后短时缩短到约 300 ms。AtCoder 和洛谷保持 3 秒采集间隔，遇到请求错误会延长间隔。
- 页面结构/接口变动会导致解析失败：扩展会静默降级（不误报），需要更新 `content.js`；
- 牛客和 AtCoder 的本人判题在引擎启用设备输出后可触发设备。

## 安全说明

- 扩展只向 `127.0.0.1` 发请求，且必须携带引擎本次会话随机生成的 token；
- 引擎不读取你的 Cookie，扩展也不保存账号密码；
- 不要把 `extension/` 目录改成"允许外部访问"之类的实验设置。
