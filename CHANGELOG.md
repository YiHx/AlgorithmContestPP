# 更新记录

## v0.1.0

- 在 DGHub 插件页填写比赛网址和用户名即可启动引擎。手动关闭引擎窗口后，修改比赛网址可以重新启动。
- Codeforces 和牛客扩展支持短时加速轮询。扩展弹窗显示当前会话、最近成功采集时间和错误。
- 扩展可在 Chrome、Edge 和 Firefox 本地载入。Firefox 的长期安装需要 Mozilla 签名。
- 全局冷却默认 10 秒，同类事件默认 30 秒。实机按指令发送时刻计冷却，判题反馈优先于升档反馈。可以单独关闭「长时间未 AC 时也反馈」。
- 加入 Windows CI、Python 依赖清单，以及 DGHub 插件和浏览器扩展 ZIP。

洛谷只接入榜单，牛客暂未接入榜单。AtCoder、Edge、Firefox 和 DGHub V4 的真实链路验证情况见 [验证说明](VALIDATION.md)。
