# 个人微信聊天 Bot（Python 基础架构）

面向好友私聊与普通微信群，提供分层的消息处理、持久化队列、模型服务、串行发送及运维入口。
项目路径：`E:/project/bot`。Python 3.11+，开发验证环境为 Windows / Python 3.12。

**默认配置为 mock 微信接入 + DeepSeek 真实模型；离线测试使用 mock + echo。**
项目使用 Conda 环境 `E:/project/bot/.conda`，不使用 venv。
`DeepSeek-V4-Flash-Vision-Exp` 的文本和图片 API 调用均已实测；不代表真实微信接入已完成。
Windows UIA 适配器为实验实现，未在你的微信客户端实测，不是开箱即用的个人号接入。
真实接入取决于客户端能否提供所需控件与稳定消息标识；不满足时需要实现新的适配器。
目前不包含 Hook、协议破解、自动扫码、自动加好友或主动群发。

## 直接在命令行与 bot 对话

```powershell
cd E:/project/bot
conda activate E:/project/bot/.conda
python scripts/chat.py
```

输入内容视为好友消息，按回车等待回复；支持连续对话。`/reset` 重置上下文，`/quit` 退出。
默认调用 DeepSeek；加 `--echo` 可离线测试。详见 [命令行好友模拟说明](docs/CONSOLE_CHAT.md)。
脚本现可直接从源码启动，不依赖 `pip install -e .`；在其他目录使用脚本绝对路径即可。

## 已实现

- SQLite WAL 消息持久化、`source + message_id` 去重、重启恢复。
- 同一会话按接收顺序处理，其他会话并发生成，单消费者发送。
- 好友/群白名单、群命令前缀、可信 @ 标志、自发消息过滤、输入/回复长度限制。
- 群上下文按群和发送者隔离，只使用已确认发送的历史。
- 模型超时、有限指数退避、失败兜底；发送限速和有限重试。
- 发送结果不确定进入 `uncertain`，阻塞该会话，等待人工处置。
- 全局/会话暂停、任务过期、进程锁、结构化滚动日志、心跳、SQLite 在线备份。
- 离线 Echo 与可选 HTTP 模型；可选、严格校验的 Windows UIA 适配器。
- 故障场景自动化测试、PowerShell 运行与有次数限制的重启脚本。

## 文件分层

```text
bot/
├── pyproject.toml                  # 打包、依赖、命令入口、测试/静态检查配置
├── environment.yml                # Conda 环境与项目依赖
├── config/
│   ├── deepseek.toml              # 默认配置：DeepSeek 官方视觉模型
│   ├── example.toml               # 可直接运行的 mock 配置
│   └── windows.example.toml       # 需要实机校准的 UIA 模板
├── src/wechat_bot/
│   ├── domain.py                  # 消息、任务、发送回执及异常契约
│   ├── config.py                  # 配置解析与边界校验
│   ├── policy.py                  # 无副作用的消息触发规则
│   ├── storage.py                 # SQLite 事务、队列、状态机、历史与备份
│   ├── engine.py                  # 生成/发送调度、重试与生命周期
│   ├── cli.py                     # 依赖组装、运行和管理命令
│   ├── locking.py                 # 操作系统实例锁
│   ├── observability.py           # 日志和心跳
│   ├── adapters/
│   │   ├── base.py                # 可替换接入契约
│   │   ├── mock.py                # JSONL 本地收发
│   │   └── windows_uia.py         # 实验性 UIA 独立聊天窗口接入
│   └── services/                 # model 模型 / vision 图片编码 / credentials 密文凭证
├── tests/                         # 无真实微信、无外部模型调用的测试
├── scripts/                       # 启动与进程退出重启脚本
├── docs/                          # 架构、接入、运维、开发、TODO、验证报告
└── data/                          # 首次运行生成；不应提交版本库
```

## 五分钟运行模拟版

在 PowerShell 中执行，不要求激活虚拟环境：

```powershell
Set-Location E:/project/bot
conda env create --prefix E:/project/bot/.conda --file environment.yml
conda run --prefix E:/project/bot/.conda python -m pip install -e ".[dev]"
conda run --prefix E:/project/bot/.conda python -m wechat_bot --config config/example.toml check
conda run --no-capture-output --prefix E:/project/bot/.conda python -m wechat_bot --config config/example.toml run
```

项目交付时已经创建本地 `.conda`；已有环境时可以跳过创建步骤。
在第二个 PowerShell 窗口中注入测试消息：

```powershell
Set-Location E:/project/bot
conda run --prefix E:/project/bot/.conda python -m wechat_bot --config config/example.toml inject "你好"
conda run --prefix E:/project/bot/.conda python -m wechat_bot --config config/example.toml inject "/问 介绍一下你自己" --group --conversation group-demo
Get-Content -Encoding UTF8 data/outbox.jsonl
conda run --prefix E:/project/bot/.conda python -m wechat_bot --config config/example.toml status
conda run --prefix E:/project/bot/.conda python -m wechat_bot --config config/example.toml jobs
```

稍等调度完成，出站文件会出现 `收到：你好` 等回显。`Ctrl+C` 停止运行。
`inject` 每次创建新消息 ID；重复测试去重应使用同一个 JSONL 记录。
模拟输入文件必须只追加、单写入者使用，不能在运行中截断或轮换。

## DeepSeek 文本与视觉模型

默认 `config/deepseek.toml` 已配置：

- API：`https://api.deepseek.com/chat/completions`
- 模型：`deepseek-v4-flash-vision-exp`（API 使用小写名称）
- 密钥：优先 `DEEPSEEK_API_KEY`，否则读取 `.secrets/deepseek-api-key.dpapi`
- 超时 90 秒，单次最大输出 2048 tokens；模型默认推理行为由服务端决定。

你提供的密钥已加密保存，绑定当前 Windows 用户，代码和 TOML 不含明文。
换机器或 Windows 用户后用 `./scripts/set-api-key.ps1` 重新配置；不要将密文当作跨机器备份密钥。

```powershell
Set-Location E:/project/bot
conda activate E:/project/bot/.conda
python -m wechat_bot model-test
python scripts/create-vision-fixture.py
python -m wechat_bot model-test --image data/vision-fixture.png --prompt "左右分别是什么颜色？"
python -m wechat_bot run
```

`model-test` 会真实调用 API；默认机器人仍从 `data/deepseek-inbox.jsonl` 读取模拟消息。
可以在另一终端运行 `python -m wechat_bot inject "你好"`，查看 `data/deepseek-outbox.jsonl`。
视觉请求支持最多 4 张本地 PNG/JPEG/GIF/WebP，每张 8 MiB；只上传命令明确指定的文件。
**微信图片的接收、下载及媒体隔离尚未接入机器人消息流水线，见 TODO(T11)。**

接口依据：[DeepSeek 官方视觉文档](https://api-docs.deepseek.com/guides/vision/)。

### 更换其他兼容模型

```powershell
conda run --prefix E:/project/bot/.conda python -m pip install -e ".[llm]"
Copy-Item config/example.toml config/local.toml
$env:BOT_API_KEY = "填写你的服务密钥"
```

编辑 `config/local.toml` 中的 `[model]`：

```toml
[model]
provider = "http"
base_url = "https://YOUR_PROVIDER/v1"
name = "YOUR_MODEL_NAME"
api_key_env = "BOT_API_KEY"
timeout_seconds = 30.0
fallback = "暂时无法生成回复，请稍后再试。"
system_prompt = "你是简洁、友善的聊天助手。未知信息请明确说明。"
```

程序向 `base_url + /chat/completions` 发出非流式请求，服务商必须支持该协议。
启动：`conda run --prefix E:/project/bot/.conda python -m wechat_bot --config config/local.toml run`。
环境变量需要在实际启动进程的终端或任务计划环境中设置；程序不会自动读取 `.env`。
默认 DeepSeek 配置也可以从 DPAPI 密钥文件读取，其他服务配置可仅使用环境变量。
模型会收到被触发消息及有限历史；数据库会保存聊天正文，请按使用场景设置数据保留和访问权限。

## 接入真实微信

本轮接入测试：**74 项离线测试通过；微信 4.1.13.12 的真实收发仍待验收**。
见 [个人微信测试方案与项目矩阵](docs/WECHAT_TEST_PLAN.md) 和
[本轮测试报告](docs/WECHAT_TEST_REPORT.md)。

```powershell
& .\.conda\python.exe scripts/wechat_test.py
# 控件已人工校准后，可选只读实机检查：
& .\.conda\python.exe scripts/wechat_test.py --probe-config config/wechat.local.toml
```

入口输出 JUnit XML、逐阶段日志与 summary.json 到 data/wechat-tests/，失败返回非零退出码。

先阅读 [微信适配说明](docs/WECHAT_ADAPTER.md)。不要仅将 `verified` 改为 `true` 就开始发送。
必须验证具体微信版本、独立窗口、稳定身份、消息 ID、发送按钮及发送证据。
如果微信 UIA 不暴露必需字段，当前适配器会拒绝运行，需按契约新增适配器。

## 测试与文档

```powershell
./scripts/test.ps1
# 或分别执行：
conda run --prefix E:/project/bot/.conda python -m pytest -q
conda run --prefix E:/project/bot/.conda python -m ruff check .
```

- [架构和状态机](docs/ARCHITECTURE.md)
- [微信适配与验收](docs/WECHAT_ADAPTER.md)
- [配置、部署和运维](docs/OPERATIONS.md)
- [开发和扩展约定](docs/DEVELOPMENT.md)
- [待确认事项与 TODO](docs/TODO.md)
- [测试与验证范围](docs/VALIDATION.md)
- [逐项测试清单、运行命令与报告](docs/TESTING.md)
