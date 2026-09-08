# 配置、部署和维护

## 运行方式

使用 `conda run --prefix E:/project/bot/.conda python`，无需激活环境；Conda 须在 PATH 或 CONDA_EXE 中。
PowerShell：`./scripts/run.ps1` 默认加载 `config/deepseek.toml`；离线运行传 `-Config config/example.toml`。
需要有限退出重启时：`./scripts/watchdog.ps1 -Config config/local.toml -MaxRestarts 3`。
watchdog 前台运行，只重启退出的 bot，不重启微信、不处理扫码，也不保证 UIA 卡死恢复。

通过任务计划部署时，Windows UIA 要选择“仅当用户登录时运行”，起始目录为项目根。
所有启动脚本使用项目 `.conda`；旧 `.venv` 目录未被使用，其删除操作被自动审查阻止。
将任务、模型凭证、微信放在同一专用用户环境中。此项目没有替你创建计划任务。
尽量使用专用电脑；锁屏、远程断开、睡眠和客户端升级需实测，不能假定服务后台能操作桌面。

## 配置约定

- 相对路径相对 TOML 文件，不相对启动目录；管理命令须使用同一 `--config`。
- 密钥优先从 `model.api_key_env` 环境变量读取，其次从 `model.api_key_file` 的 DPAPI 密文读取。
  当前 Windows 用户之外无法直接解密，换用户时使用 `scripts/set-api-key.ps1` 重新配置。
- `private_allowlist/group_allowlist` 使用适配器返回的会话 ID，不是随意填写的昵称。
- `workers` 控制模型并发；同一会话始终按顺序处理。
- `message_ttl_seconds` 控制接收/等待时效；`send_interval_seconds` 控制全局尝试频率。
- `max_attempts` 分别约束模型生成与确认未发送的重试次数。
- `context_turns` 控制历史轮数，可设为 0；字符限制不是模型 token 或费用硬预算。
- 配置修改需重启，没有热更新功能；`pause/resume` 管理操作实时写数据库。

## 暂停和接管

以下使用默认 DeepSeek 配置；离线测试加 `--config config/example.toml`，自定义配置须显式指定。

```powershell
conda run --prefix E:/project/bot/.conda python -m wechat_bot pause
conda run --prefix E:/project/bot/.conda python -m wechat_bot pause --conversation friend-demo
conda run --prefix E:/project/bot/.conda python -m wechat_bot resume --conversation friend-demo
conda run --prefix E:/project/bot/.conda python -m wechat_bot resume
```

`pause` 取消尚未发送任务，暂停期间新消息标为 ignored；恢复后不会补发旧消息。
同时存在全局和会话暂停时，需要分别解除。`resume` 不删除 `PAUSE` 文件。
紧急开关：在配置指定的 `pause_file` 路径创建空文件；恢复时手动移除该文件。
已进入外部发送操作的请求无法撤回。当前不自动识别“你在手机上手动回复过”，接管前应手动暂停。

## 不确定发送

```powershell
conda run --prefix E:/project/bot/.conda python -m wechat_bot jobs
# 先人工查看真实聊天记录，再选择其中一种：
conda run --prefix E:/project/bot/.conda python -m wechat_bot resolve 12 --as sent
conda run --prefix E:/project/bot/.conda python -m wechat_bot resolve 12 --as canceled
```

不要两条都执行。标记 sent 将回复纳入上下文；canceled 不会重发。
目前不提供 failed/uncertain 批量重放，避免误触造成刷屏。

## 观察和排障

`status` 返回各状态计数、最老活跃任务年龄、暂停项、心跳年龄和最近 poll 状态。
程序停止后心跳文件保留，必须看时间新鲜度，不能只看文件是否存在。
`last_poll_ok` 只说明最近适配器读取成功，不证明微信服务端在线或消息已送达。
日志位置由配置指定，单文件 5 MB、最多 5 个历史文件，不输出聊天正文。

| 现象 | 处理 |
|---|---|
| `poll_failed` 持续增长 | 检查登录、窗口、PID、桌面状态、控件版本；不要直接放宽身份校验 |
| `send_not_sent` | 检查输入草稿、目标窗口和发送前检查，重试用尽后进入 failed |
| `send_uncertain` | 人工查看聊天记录，用 resolve 处置 |
| `model_failed` | 检查服务地址、模型名、凭证、超时和额度；日志不暴露服务端正文 |
| 队列长时间 pending | 检查同会话更早任务是否 uncertain，以及暂停、限速和接入异常 |
| 心跳变旧 | 检查进程退出或 UIA 卡死；watchdog 只覆盖退出，不覆盖活进程阻塞 |

TODO(T07)：尚未接入邮件或其他外部告警。当前只有本地日志与心跳，不会自动通知手机。

## 备份与恢复

```powershell
conda run --prefix E:/project/bot/.conda python -m wechat_bot backup E:/project/bot/data/backups/bot-20260908.sqlite3
```

使用 SQLite backup API，可在运行时创建一致性副本；目标文件必须尚不存在。
恢复时先停 bot 和 watchdog，将备份恢复到新的数据库路径，修改 TOML 后启动。
不要覆盖运行中的数据库或手动拼接旧 WAL 文件。
旧备份可能不知道之后已经发送的消息；恢复前需核对这段时间的聊天记录，避免旧任务再次发送。
mock 出站文件与数据库必须成对保留；独立删除或复用会影响模拟幂等键判断。

## 日常/升级维护

- 每天检查 heartbeat、uncertain、failed、积压和磁盘空间。
- 定期备份并在隔离路径验证恢复；明确消息正文和备份的保存期限。
- 更新前暂停、备份，运行测试；微信更新另做控件/身份/收发实机验证。
- 更新失败时回滚代码与依赖；数据库版本不兼容时不可直接用旧程序打开。
- schema 当前为 v1，尚无后续迁移；结构调整必须编写迁移与恢复方案。
- `.conda` 不应跨机器复制；按项目依赖或已验证的锁定清单重新安装。

TODO(T08/T09)：自动保留策略、磁盘阈值、外部费用预算和完整多版本数据库迁移待实现。
