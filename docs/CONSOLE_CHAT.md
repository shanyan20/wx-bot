# 命令行模拟好友对话

## 启动

在 PowerShell 中执行：

```powershell
cd E:/project/bot
conda activate E:/project/bot/.conda
python scripts/chat.py
```

输入一行文字并按回车，即模拟好友发来一条私聊消息。程序等待 bot 回复后允许输入下一句。
默认使用 `config/deepseek.toml` 配置的 DeepSeek 真实模型和既有加密凭证，会调用模型 API。
不需要启动 `wechat_bot run`，不需要手动向 JSONL 文件写消息，也不连接微信。

不激活 Conda 时，可以使用：

```powershell
conda run --no-capture-output --prefix E:/project/bot/.conda python scripts/chat.py
```

交互运行必须保留 `--no-capture-output`，避免 Conda 缓冲输出而看不到提示。
也可以通过项目 CLI 启动：`python -m wechat_bot chat`。

### 直接运行脚本时找不到 wechat_bot

`scripts/chat.py` 已加入源码启动支持：根据脚本位置把本项目 `src` 加入当前进程的模块搜索路径，
无需先执行 `pip install -e .`，也无需手工设置 PYTHONPATH。
默认配置同样根据脚本位置定位；显式 `--config` 的相对路径仍以当前目录为准。

在任意目录可执行：

```powershell
conda run --no-capture-output --prefix E:/project/bot/.conda python E:/project/bot/scripts/chat.py
```

仍需 Python 3.11+；真实模型需要 httpx 等依赖，项目 Conda 环境已安装。
`python -m wechat_bot chat` 是包入口，仍要求包已安装或配置模块搜索路径；直接脚本入口没有此要求。

## 使用示例

以下是对话形式示例；真实回复由模型生成：

```text
好友聊天模拟（文本，不连接微信）
模型：deepseek-v4-flash-vision-exp
...
好友 > 你好，我叫小林。
bot 正在回复…
bot  > 你好，小林！
好友 > 我刚才说自己叫什么？
bot 正在回复…
bot  > 你叫小林。
好友 > /reset
已开始新对话，后续回复不再使用旧上下文。
好友 > /quit
对话已结束。
```

| 输入 | 行为 |
|---|---|
| 普通文字 | 作为好友的文本消息发送给 bot |
| 空行 | 跳过，不调用模型 |
| `/help` | 显示帮助，不调用模型 |
| `/reset` | 切换到新会话，之后不读取旧上下文 |
| `/quit`、`/exit` | 关闭模型连接和数据库，正常退出 |
| `Ctrl+C` | 中断程序并执行资源释放 |

`/reset` 是重置对话上下文，不删除本次数据库中的历史记录。
每次启动是全新会话，不自动续聊上一次进程的内容。

## 离线模式与参数

```powershell
# 只验证交互和机器人流程，不调用真实 API，不需要真实密钥。
python scripts/chat.py --echo

# 指定另一份模型配置。
python scripts/chat.py --config config/local.toml

# 自定义本次记录的父目录，程序会再创建唯一子目录。
python scripts/chat.py --echo --session-root E:/project/bot/data/my-console-tests
```

离线模式收到“你好”时返回“收到：你好”。Echo 不具备语义理解或记忆回答能力，
连续对话上下文的自动化验证使用专门的 RecordingModel 测试替身。

## 使用的项目流程

```text
终端输入 → ConsoleAdapter → 触发规则 → SQLite
         → Engine → 历史上下文 + DeepSeek → 串行发送 → 终端展示
```

只替换微信收发边界；消息去重、入库、状态机、上下文限制、模型重试和失败兜底均复用原实现。
终端消息被视为一个白名单好友的私聊，群白名单在本次演示中为空。
若模型失败达到重试上限，会先显示“模型请求失败，以下为预设兜底回复”，避免误认为是模型答案。
超长输入等被规则拒绝时显示任务状态和原因。

每次启动在 `E:/project/bot/data/console/<时间-随机标识>/` 创建独立数据库和心跳，
不使用正式 bot 的数据库或消息文件，也不修改 TOML。
数据库含本次输入和回复，方便排查；本模式未调用日志初始化，因此通常不生成 bot.log。

目前只支持文本输入，不自动截图或把文件路径当图片上传。
视觉模型单次图片测试仍使用 `python -m wechat_bot model-test --image ...`。

## 对应测试代码与运行方法

代码：`tests/test_console.py`，六项离线测试（含两个参数化启动用例）：

| 函数 | 测试内容与判据 |
|---|---|
| `test_console_uses_history_and_reset_starts_new_session` | 第二轮模型输入包含第一轮问答；reset 后历史为空；不创建原配置数据库 |
| `test_console_reports_fallback_and_rejected_input` | 模型异常产生带 model_fallback 标识的兜底结果；超长输入被忽略且原因明确 |
| `test_console_script_accepts_lines_commands_and_exit` | 真实启动脚本子进程，输入空行、帮助、两轮文字、重置和退出；检查两条回显与退出码 0 |
| `test_console_filters_terminal_escape_characters` | 展示前过滤 ESC/NUL 控制字符，保留正常文字和换行 |
| `test_console_script_without_install_from_another_directory`（2 项） | 子进程用 `-I -S` 禁用环境搜索路径与 site，从临时目录启动；分别验证项目默认配置与显式相对配置，均完成回显和退出 |

```powershell
conda run --prefix E:/project/bot/.conda python -m pytest tests/test_console.py -v
```

全部测试和报告生成仍使用 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test.ps1`。

## 实际验证记录

2026-09-08 已在 Conda 环境通过脚本输入两轮真实 DeepSeek 对话：

```text
好友：请记住我的测试代号是青竹。只回复已记住。
bot：已记住
好友：我的测试代号是什么？只回复代号。
bot：青竹
```

随后 `/quit` 正常退出，退出码 0。此测试只使用合成文本，不连接微信。
完整离线测试已增至 52 项，结果见 [验证记录](VALIDATION.md)。
