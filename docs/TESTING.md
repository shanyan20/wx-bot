# 项目测试手册

本手册对应 `E:/project/bot` 的现有测试代码。所有命令在 **PowerShell** 中执行。
运行环境统一为 **Conda：`E:/project/bot/.conda`**。本手册区分离线测试、真实模型测试和微信实机验收。

## 1. 测试范围与执行顺序

| 层次 | 代码/入口 | 验证目标 | 是否联网/使用真实密钥 |
|---|---|---|---|
| 业务规则、存储、调度及边界 | `tests/test_*.py`，当前 52 个参数化展开后的用例 | 核心状态和错误处理符合断言 | 不联网；仅使用假密钥 |
| 静态检查 | Ruff | 导入、未使用变量、基础代码错误及风格 | 不联网 |
| 依赖一致性 | pip check | 已安装依赖版本不存在声明冲突 | 不联网 |
| 命令行进程集成 | `scripts/smoke.py` | 真正启动 bot 子进程，验证 mock 收发、管理和退出 | 不联网、不连接微信 |
| 模型文本/视觉调用 | CLI `model-test` | 配置、凭证、HTTP 服务和目标模型实际可用 | **真实调用 DeepSeek，会消耗 API 额度** |
| 微信实机兼容 | `docs/WECHAT_ADAPTER.md` 中的验收表 | 控件、身份、消息读取与真实发送是否可靠 | 需真实测试账号和客户端，尚未完成 |

建议先通过全部离线检查，再按需运行真实模型测试。`pytest` 通过不能证明微信客户端兼容，
也不能证明模型回答质量。当前 52 是测试数量，不是代码覆盖率百分比。

## 2. 环境准备与确认

```powershell
Set-Location E:/project/bot
conda run --prefix E:/project/bot/.conda python -c "import sys; print(sys.executable); print(sys.version)"
```

解释器路径应为 `E:\project\bot\.conda\python.exe`，当前实测版本为 Python 3.12.14。
现有机器已经安装环境，无需重复创建。只有首次部署、环境不存在时执行：

```powershell
Set-Location E:/project/bot
conda env create --prefix E:/project/bot/.conda --file environment.yml
conda run --prefix E:/project/bot/.conda python -m pip install -r requirements-dev.lock.txt -e ".[dev,llm]"
```

也可先 `conda activate E:/project/bot/.conda`，随后将本文的
`conda run --prefix E:/project/bot/.conda python` 简写为 `python`。
如果 PowerShell 尚未初始化 Conda，继续使用 `conda run`，不必切换到其他 Python。

## 3. 一次执行全部离线检查

测试入口：[scripts/test.ps1](../scripts/test.ps1)。

```powershell
Set-Location E:/project/bot
powershell -NoProfile -ExecutionPolicy Bypass -File E:/project/bot/scripts/test.ps1
```

`ExecutionPolicy Bypass` 只用于此次 PowerShell 子进程，不修改机器的持久执行策略。
脚本依次运行：pytest → Ruff → pip check → CLI smoke。任何阶段非零退出都会停止后续检查。
脚本会输出本次报告目录，包含 `pytest.xml` 和 `summary.json`；默认位于
`E:/project/bot/data/test-reports/<运行时间>/`。

预期看到：

```text
52 passed
All checks passed!
No broken requirements found.
PASS: CLI check / private+group replies / pause+resume / backup / graceful stop
```

用例增加后通过数量也会增加，以当次 `pytest --collect-only` 为准。完整通过要求所有阶段退出码为 0。

## 4. 按模块、单用例和关键词运行

以下命令都在项目根目录执行。

```powershell
# 列出真实收集到的用例名称，不执行测试。
conda run --prefix E:/project/bot/.conda python -m pytest --collect-only -q

# 显示每个用例和完整失败详情。
conda run --prefix E:/project/bot/.conda python -m pytest -v --tb=long

# 只运行调度引擎测试。
conda run --prefix E:/project/bot/.conda python -m pytest tests/test_engine.py -v

# 精确运行一个重启恢复用例。
conda run --prefix E:/project/bot/.conda python -m pytest tests/test_storage.py::test_restart_preserves_uncertain_send_and_blocks_following -v

# 只运行名称包含 uncertain 或 pause 的相关用例。
conda run --prefix E:/project/bot/.conda python -m pytest -k "uncertain or pause" -v

# 失败后快速定位：遇到第一个失败立即停止。
conda run --prefix E:/project/bot/.conda python -m pytest -x -v --tb=long

# 仅重跑上次失败的用例；若没有失败记录则不执行。
conda run --prefix E:/project/bot/.conda python -m pytest --lf --lfnf=none -v
```

参数化函数的一个名称会展开为多个用例。例如 `test_filter_reasons` 展开为 6 个。
用 `文件.py::函数名` 可运行该函数的所有参数组合；单一参数组合的精确 ID 请从 `--collect-only` 复制，
并在 PowerShell 中将完整 ID 放进双引号。

## 5. 逐项测试清单

各表的“函数”列是实际测试函数，可代入上面的 `文件.py::函数名` 命令。
步骤和预期结果均依据当前断言，不把未验证能力写成已通过。

### 5.1 消息触发规则：`tests/test_policy.py`（9 项）

运行：`conda run --prefix E:/project/bot/.conda python -m pytest tests/test_policy.py -v`

| 函数 | 输入/操作 | 预期结果 |
|---|---|---|
| `test_filter_reasons`（6 项） | 分别输入己方消息、图片消息、陌生会话、空白正文、过期时间和超前时间 | 全部拒绝；原因分别是 `self_message`、`unsupported_kind`、`not_allowlisted`、`empty_text`、`expired_on_receive`、`future_timestamp` |
| `test_group_requires_prefix_or_trusted_mention` | 同一白名单群分别发送普通文本、`/问 你是谁`、适配器标记的可信 @ | 普通文本不触发；前缀移除后 prompt 为“你是谁”；可信 @ 可触发 |
| `test_group_sessions_are_isolated` | 同群更换 sender，随后更换 private/group 类型 | session_id 不同，不混用会话键 |
| `test_input_limit` | 输入 4001 字符，默认上限 4000 | 拒绝，原因 `input_too_long` |

注意：图片在聊天规则层仍被拒绝。视觉模型接口测试通过不等于微信图片消息已经接入。

### 5.2 存储、顺序与恢复：`tests/test_storage.py`（7 项）

运行：`conda run --prefix E:/project/bot/.conda python -m pytest tests/test_storage.py -v`

| 函数 | 输入/操作 | 预期结果 |
|---|---|---|
| `test_dedup_is_identity_based_not_text_based` | 重复同一 source/id；相同正文换 id；相同 id 换 source | 原记录不重复；两个新身份各入库，合计 3 个 pending |
| `test_same_conversation_serial_other_conversation_progresses` | 同会话入队 one/two，另一会话入队 three | one、three 可领取，two 等待；one 发送完成后才能领取 two |
| `test_restart_preserves_uncertain_send_and_blocks_following` | 第一任务进入 sending，新建数据库连接执行 recover | 第一任务变 uncertain，后续任务阻塞；人工标记 sent 后解除阻塞 |
| `test_pause_during_generation_cannot_resurrect_reply` | processing 时暂停，之后提交迟到的模型回复，再恢复 | 任务保持 canceled，不能发送或重新领取 |
| `test_expiry_and_retry_bounds` | ready 任务超过 TTL；另一个 processing 任务消耗最后一次尝试后恢复 | 过期任务变 expired；用尽次数的任务变 failed，不能无限重试 |
| `test_persistent_send_rate_limit` | 100 秒发送一次，在 101 秒和 110 秒领取下次发送，间隔配置 10 秒 | 101 秒不允许，110 秒允许；此用例验证数据库限速逻辑，未单独重启进程 |
| `test_only_confirmed_history_used_and_backup_contains_wal` | 比较 ready/sent 时历史；发送后在线备份并重新打开备份 | ready 不入历史，sent 形成 user/assistant 对；备份包含 sent 记录 |

“发送中崩溃”用例通过新连接与 recover 模拟崩溃遗留状态，并未强杀真实微信进程。

### 5.3 调度与故障处理：`tests/test_engine.py`（8 项）

运行：`conda run --prefix E:/project/bot/.conda python -m pytest tests/test_engine.py -v`

| 函数 | 输入/操作 | 预期结果 |
|---|---|---|
| `test_mock_end_to_end_and_restart_no_duplicate` | JSONL 写入两份相同消息；完成回复后重建 Engine/MockAdapter | 出站只有一行，内容“收到：你好”，重放不重复发送 |
| `test_uncertain_is_never_automatically_retried` | 适配器发送抛 `UncertainSendError`，继续多次调度 | 状态 uncertain；send 调用仅 1 次 |
| `test_not_sent_retries_are_bounded` | 适配器每次发送抛 `NotSentError` | send 次数等于 max_attempts，最终 failed |
| `test_model_failure_falls_back` | 模型抛 ModelError，max_attempts 设为 1 | 发送配置的 fallback，并记录 sent 历史 |
| `test_pause_file_cancels_queued_reply` | 任务已经 ready，创建 PAUSE 文件后调度 | 不调用 send；任务 canceled |
| `test_removed_allowlist_prevents_reply_after_restart` | 旧配置生成 ready，创建使用空白名单的新 Engine | 发送前重新校验，取消任务，原因 not_allowlisted |
| `test_model_total_timeout_falls_back` | 模型模拟等待 10 秒，总时限设为 0.01 秒，尝试上限为 1 | 超时后发送 fallback，而非迟到答案 |
| `test_poll_failure_holds_outbound_queue` | ready 任务存在，但 poll 抛异常 | 不调用 send，任务保持 ready，心跳 poll_failures=1 |

调度测试通过 asyncio 驱动，模型与发送均使用替身；不是高并发压测。

### 5.4 文件、锁与配置边界：`tests/test_boundaries.py`（7 项）

运行：`conda run --prefix E:/project/bot/.conda python -m pytest tests/test_boundaries.py -v`

| 函数 | 输入/操作 | 预期结果 |
|---|---|---|
| `test_mock_partial_line_waits_for_completion` | 先写半行 JSONL，再补完整行；重复 poll，最后 ack | 半行不消费；未 ack 可重读；ack 后不再返回 |
| `test_instance_lock_released_after_exit` | 持锁时尝试再次加锁，退出后再获取 | 第二次获取失败；原锁释放后可获取。测试在同一进程的独立文件句柄中执行 |
| `test_invalid_settings_rejected`（4 项） | workers=0、poll_seconds=NaN、空群前缀、字符串形式白名单 | load_settings 抛 ValueError |
| `test_windows_profile_fails_closed_before_importing_uia` | 使用 verified=false 构造适配器 | Windows 上 ValueError；其他系统 RuntimeError；不访问真实桌面 |

### 5.5 HTTP 模型契约：`tests/test_model.py`（4 项）

运行：`conda run --prefix E:/project/bot/.conda python -m pytest tests/test_model.py -v`

| 函数 | 输入/操作 | 预期结果 |
|---|---|---|
| `test_http_payload_and_reply_without_network` | 使用假密钥构造客户端，MockTransport 返回“你好” | Authorization 配置正确；请求 /v1/chat/completions；含 system/当前 user，非流式；解析“你好” |
| `test_http_errors_sanitized`（3 项） | 模拟 HTTP 429、空 choices、空 content | 统一 ModelError；异常字符串不包含测试私有正文或假密钥 |

HTTP 调用被 MockTransport 截获，不进行 DNS/远程请求，不消耗真实 API 额度。

### 5.6 图片编码和凭证：`tests/test_vision_credentials.py`（8 项）

运行：`conda run --prefix E:/project/bot/.conda python -m pytest tests/test_vision_credentials.py -v`

| 函数 | 输入/操作 | 预期结果 |
|---|---|---|
| `test_environment_credential_takes_precedence` | 环境变量提供假密钥，同时指定不存在的密文文件 | 直接返回环境变量，不读取文件 |
| `test_missing_credential_does_not_include_secret` | 清除测试环境变量，不提供密文文件 | ValueError 指出应设置的变量名 |
| `test_dpapi_loader_boundary_without_user_credentials` | 用 monkeypatch 替换解密函数 | 返回替身密钥，验证回退分支；**不测试 Windows 真正解密算法** |
| `test_png_detected_by_bytes_not_extension` | 将 PNG 文件头写入 `.bin` 文件 | 按字节识别 image/png，Base64 可还原原字节 |
| `test_non_image_file_rejected` | `.png` 文件中写普通文本 | 文件头检查失败，不上传 |
| `test_oversized_image_rejected_before_upload` | 将大小上限临时降为 8 字节，写更大文件 | 拒绝；无需真的生成超过 8 MiB 的文件 |
| `test_too_many_images_rejected_before_reading` | 传入 5 个不存在的图片路径 | 在读取文件前拒绝数量超限 |
| `test_multimodal_model_request` | 传 PNG 文件头测试数据，由 MockTransport 接收请求 | model 正确，user content 同时含 text/image_url，max_tokens 正确，回复解析成功 |

PNG 单元测试仅验证魔数与编码，不验证完整图片解码。真正可解码的图片由
`scripts/create-vision-fixture.py` 生成，用于后面的真实模型测试。

### 5.7 UIA 发送边界：`tests/test_windows_contract.py`（3 项）

运行：`conda run --prefix E:/project/bot/.conda python -m pytest tests/test_windows_contract.py -v`

| 函数 | 输入/操作 | 预期结果 |
|---|---|---|
| `test_existing_draft_prevents_send_and_is_preserved` | 假输入框已有 human draft | NotSentError，草稿不变，发送按钮未调用 |
| `test_exception_after_invocation_is_uncertain` | 假发送按钮 invoke 后抛异常 | UncertainSendError，按钮只调用一次 |
| `test_new_matching_outgoing_row_is_local_send_evidence` | invoke 后快照出现新的、正文匹配的己方消息行 | 返回 `uia_visible:outbound` 回执 |

这些测试替换了窗口、控件和快照，不安装或操作真实微信。
回执只是本地发送证据，不能证明微信服务器交付或对方已读。

### 5.8 交互式好友模拟：`tests/test_console.py`（6 项）

运行：`conda run --prefix E:/project/bot/.conda python -m pytest tests/test_console.py -v`

| 函数 | 操作与预期结果 |
|---|---|
| `test_console_uses_history_and_reset_starts_new_session` | 连续询问两轮，第二轮携带第一轮问答；reset 后历史为空；原配置数据库未创建 |
| `test_console_reports_fallback_and_rejected_input` | 模型异常返回带 model_fallback 原因的兜底回复；超长输入 ignored/input_too_long |
| `test_console_script_accepts_lines_commands_and_exit` | 子进程输入空行、帮助、文字、重置、文字、退出；两次回显正确且正常退出 |
| `test_console_filters_terminal_escape_characters` | 终端展示过滤 ESC/NUL，保留普通文字和换行 |
| `test_console_script_without_install_from_another_directory`（2 项） | 使用 `-I -S` 禁用已安装包/PYTHONPATH，从非项目目录直接运行脚本；项目默认配置和显式相对配置均能完成回显并正常退出 |

手动启动：`conda run --no-capture-output --prefix E:/project/bot/.conda python scripts/chat.py`。
这会真实调用 DeepSeek，输入当作好友消息；加 `--echo` 切换到离线。
详细交互命令、数据位置和流程见 [CONSOLE_CHAT.md](CONSOLE_CHAT.md)。

## 6. 命令行进程集成测试

测试代码：[scripts/smoke.py](../scripts/smoke.py)。运行：

```powershell
conda run --no-capture-output --prefix E:/project/bot/.conda python scripts/smoke.py
```

| 顺序 | 脚本实际动作 | 检查结果 |
|---|---|---|
| 1 | 创建临时配置、数据库和 JSONL 路径，调用 check | 配置命令退出 0 |
| 2 | 注入好友“你好”与群“/问 群测试” | inject 命令退出 0 |
| 3 | 启动真正 bot 子进程，轮询 status | 15 秒内出现两个 sent，子进程未异常退出 |
| 4 | 读取临时 outbox | 回复集合为“收到：你好”和“收到：群测试” |
| 5 | pause friend-demo，读取 status，然后 resume | 暂停项包含 friend-demo；resume 命令成功。当前脚本未再次断言恢复后的暂停列表 |
| 6 | 调用 backup | 备份文件存在；备份内容正确性由 storage 测试覆盖 |
| 7 | Windows 发送 CTRL_BREAK，其他系统发 SIGTERM | 10 秒内正常退出，退出码为 0 |
| 8 | 清理临时目录；异常时终止仍存在的 bot 子进程 | 不遗留此次 smoke 的运行进程 |

成功输出以 `PASS: CLI check / private+group replies / pause+resume / backup / graceful stop` 开头。
该脚本不使用正式 `data/deepseek.sqlite3`；这是进程级 mock 测试，不是微信客户端收发测试。

## 7. 配置与真实 DeepSeek 测试

### 7.1 只校验配置，不联网

```powershell
conda run --prefix E:/project/bot/.conda python -m wechat_bot --config config/deepseek.toml check
```

预期 `adapter=mock, model=http`，退出 0。它不读取/验证真实密钥，也不验证服务可用性。

### 7.2 文本 API 测试

```powershell
conda run --no-capture-output --prefix E:/project/bot/.conda python -m wechat_bot --config config/deepseek.toml model-test --prompt "请仅回复：模型连接成功"
```

前置条件：官方服务可访问，`config/deepseek.toml` 中的模型与地址正确；
`DEEPSEEK_API_KEY` 环境变量可用，或当前 Windows 用户能解密 `.secrets/deepseek-api-key.dpapi`。
优先使用环境变量。密钥无须写在命令行；需更新密钥时运行 `scripts/set-api-key.ps1`。

通过判据：命令退出 0，返回非空、符合提示要求的内容，例如“模型连接成功”。
**退出 0 仅说明调用和非空回复解析成功，语义正确性须人工核对。**
该命令最多等待配置的 90 秒；失败时退出非零，不自动执行机器人中的 fallback 重试流程。

### 7.3 图片 API 测试

```powershell
conda run --prefix E:/project/bot/.conda python scripts/create-vision-fixture.py
conda run --no-capture-output --prefix E:/project/bot/.conda python -m wechat_bot --config config/deepseek.toml model-test --image data/vision-fixture.png --prompt "请只说出图片左半边与右半边分别是什么颜色，按左、右顺序回答。"
```

生成器写入 `E:/project/bot/data/vision-fixture.png`：256×256、左半红色、右半蓝色。
该图不含聊天记录或私人数据。模型应返回“左边红色，右边蓝色”或同义表达。
通过判据：退出 0，并人工确认左右和颜色都正确。

使用自己的测试图时，将 `--image` 后路径替换为明确指定的文件；多张图重复使用 `--image`。
当前最多 4 张，每张不超过 8 MiB。命令会把指定图片发送给配置的模型服务。
机器人消息流水线尚未接收微信图片，见 TODO(T11)。

### 7.4 结果记录

2026-09-08 前次真实 API 实测：文本返回“模型连接成功”；图片返回“左边红色，右边蓝色。”。
上述文本/图片结果来自先前验证，不是每次离线测试自动执行的项目。交互程序另已完成两轮真实模型上下文验证，见 CONSOLE_CHAT.md。
日后重测请记录时间、模型、配置文件、提示词、图片说明、退出码和人工判断，不记录密钥。

## 8. 测试数据、报告及问题定位

- `tests/conftest.py` 使用 pytest 的 `tmp_path`，每个测试独立数据库/收发文件；
  测试基线是 `config/example.toml`，不是带真实密钥路径的 DeepSeek 配置。
- pytest 临时目录可能按 pytest 默认策略保留以便排查；它们不在正式业务数据库路径。
- `scripts/smoke.py` 使用 TemporaryDirectory，退出时清理本次临时文件。
- 一键测试报告放在 `data/test-reports/<运行时间>/`，该目录已被 Git 忽略。
- `pytest.xml` 提供每个用例结果和失败堆栈；`summary.json` 提供各阶段命令、耗时和退出码。
  控制台仍是完整测试输出；summary 不是日志正文副本。

单独生成 JUnit 报告：

```powershell
New-Item -ItemType Directory -Force E:/project/bot/data/test-reports/manual | Out-Null
conda run --prefix E:/project/bot/.conda python -m pytest -v --junitxml=data/test-reports/manual/pytest.xml
```

| 问题 | 排查方法 |
|---|---|
| 找不到 conda | 确认 Conda 已安装且 PATH 正确；当前机器可用 `E:/Anaconda/Scripts/conda.exe` 替代命令中的 conda |
| 找不到 wechat_bot / pytest | 检查解释器路径，并在该 Conda 环境安装 `.[dev,llm]` |
| pytest 出现 AssertionError | 按失败 node ID 运行单用例并加 `-v --tb=long`，检查测试期望与实际状态 |
| pytest 未收集到用例 | 返回项目根目录，执行 `--collect-only -q`；pytest 无用例退出码通常为 5 |
| Ruff 非零退出 | 根据文件/行号修复后重跑；它不是功能测试失败 |
| smoke 超时 | 查看子进程退出情况、磁盘权限及主机负载；不要用反复加大超时掩盖死锁 |
| ModelError / TimeoutError | 检查密钥来源、模型名、网络和超时；当前错误脱敏可能不显示 HTTP 细节 |
| DPAPI 解密失败 | 使用创建密钥的 Windows 用户，或重新运行密钥配置脚本 |

## 9. 尚未覆盖的验收项

真实微信控件兼容、同名群成员身份、刷屏漏收、断网/锁屏恢复、客户端升级、UIA 卡死、
长期稳定性和费用预算均不能由上述 52 项测试推导为已通过。
现有图片测试未穷尽 JPEG/GIF/WebP 解码、多图语义和异常图片；DPAPI 真正解密仅随前次真实 API 调用验证，
离线用例用替身覆盖读取分支。具体真实微信步骤见 [WECHAT_ADAPTER.md](WECHAT_ADAPTER.md)，待办见 [TODO.md](TODO.md)。

## 10. 旧 `.venv` 清理状态

当前项目运行和测试全部使用 `.conda`。旧目录已确认是 `E:/project/bot/.venv` 内的历史 Python venv，
不是符号链接。本轮再次请求删除仍被自动审批审查以 `blocked by policy` 拒绝，故尚未清理。

如需手动清理，在你自己的 PowerShell 中核对目标后执行：

```powershell
Get-Item -Force -LiteralPath E:/project/bot/.venv
Get-Content -LiteralPath E:/project/bot/.venv/pyvenv.cfg
Remove-Item -LiteralPath E:/project/bot/.venv -Recurse -Force
Test-Path -LiteralPath E:/project/bot/.venv
Test-Path -LiteralPath E:/project/bot/.conda/conda-meta
```

最后两项应依次为 `False`、`True`。不要将删除目标改成项目根目录或 `.conda`。
