# 验证记录

逐项测试内容、前置条件、预期结果与完整运行命令见 [测试手册](TESTING.md)。

验证日期：2026-09-08。当前环境：Windows、Conda、Python 3.12.14、`E:/project/bot/.conda`。
原 venv 环境已迁出运行流程，所有本次测试通过 Conda 命令执行。
旧 `.venv` 目录的删除被自动审批审查以“blocked by policy”拒绝，因此保留原目录但不再使用。
第三方依赖版本见 `requirements-dev.lock.txt`。

## 最新：脚本直接启动修复

已复现直接运行 `scripts/chat.py` 时的 `ModuleNotFoundError`：脚本目录被加入模块搜索路径，
但 `src` 未被加入，旧测试环境中的可编辑安装掩盖了该问题。
现在脚本按自身位置加载本项目源码和默认配置，同时保留显式 `--config` 的相对路径语义。

新增两个 `-I -S` 隔离子进程回归用例：从非项目目录启动，不依赖 site、PYTHONPATH 或可编辑安装，
分别验证默认和自定义配置下的对话与退出。当前 **52 项 pytest 全部通过**，
完整重跑中的 Ruff、依赖检查和 CLI smoke 也均通过。

- [本次最终阶段结果](../data/test-reports/20260908-193003-245/summary.json)
- [本次最终 JUnit 结果](../data/test-reports/20260908-193003-245/pytest.xml)

首轮完整检查中的 CLI smoke 曾在 Windows 退出码断言处失败一次，原脚本丢弃 stderr，
未取得该次具体原因；现已保留失败退出码与 stderr 诊断。随后单独 smoke 和完整重跑均通过。
不能将这次非复现退出异常宣称为已定位并修复；它不影响新隔离启动回归用例的通过结果。

## 前次交互程序验证

新增 `scripts/chat.py`、ConsoleAdapter 与四项离线测试。当前完整离线用例为 **50 项**；
pytest、Ruff、pip check、CLI smoke 均通过，全部使用项目 Conda 环境。
本次独立完成两轮真实 DeepSeek 对话：先记住“青竹”，再询问代号，回复正确；随后正常退出。
使用方式及逐项交互测试说明见 [CONSOLE_CHAT.md](CONSOLE_CHAT.md)。

- [最新阶段结果摘要](../data/test-reports/20260908-042417-125/summary.json)
- [最新逐用例 JUnit 结果](../data/test-reports/20260908-042417-125/pytest.xml)

## 前次文档与报告核对

通过更新后的 `scripts/test.ps1` 在 Conda 中完整执行一轮：46 项 pytest、Ruff、pip check、
CLI smoke 全部通过，四个阶段退出码均为 0。36 个测试函数经参数化展开为 46 个用例。
已用 AST 核对 `TESTING.md` 覆盖全部测试函数，并与 JUnit 中各模块用例数量一致。

- [本轮阶段结果摘要](../data/test-reports/20260908-035432-359/summary.json)
- [本轮逐用例 JUnit 结果](../data/test-reports/20260908-035432-359/pytest.xml)

报告为本机 `data/` 目录中的生成文件，不进入版本库；文件缺失时重新运行一键测试生成新报告。
本轮未重复请求付费模型；下方 DeepSeek 文本/视觉结果保留自前次真实 API 验证。

## 已验证

| 检查 | 结果 | 范围 |
|---|---|---|
| Editable 安装 | 通过 | `pip install -e ".[dev,llm]"` |
| 单元/集成测试 | 52 项通过 | `conda run --prefix E:/project/bot/.conda python -m pytest -q`，全部离线 |
| 静态检查 | 通过 | `python -m ruff check .` |
| 依赖一致性 | 通过 | `python -m pip check` |
| 独立进程 CLI smoke | 通过 | `python scripts/smoke.py` |
| 默认配置校验 | 通过 | `python -m wechat_bot check`，mock + DeepSeek HTTP |
| DeepSeek 文本实测 | 通过 | 官方 API 返回“模型连接成功” |
| DeepSeek 视觉实测 | 通过 | 上传自行生成的左右红蓝图，返回“左边红色，右边蓝色。” |

核心故障测试覆盖：重复投递、相同正文不同消息 ID、群触发和会话隔离、
同会话有序/其他会话可前进、发送中崩溃恢复为 uncertain、不确定发送不重试、
确认未发的有限重试、模型异常与总时限兜底、暂停后迟到回复不复活、
配置撤销白名单后不发送已有回复、接入异常暂停出站、TTL、持久化限速、
只纳入已发送历史、SQLite WAL 在线备份、半行 JSONL、实例锁和配置拒绝。

HTTP 提供者使用 httpx MockTransport 验证请求结构、异常清理及回复解析；没有外部请求。
新增离线测试覆盖密钥读取优先级、缺失凭证、图片格式/大小/数量限制及多模态请求结构。
真实模型实测为单独的 `model-test` 命令，使用 DPAPI 密钥文件，不计入离线 pytest。
UIA 替身测试验证已有草稿不覆盖、发送调用后异常归 uncertain、匹配新己方消息作为本地证据。
这些测试只覆盖适配器控制逻辑，不是客户端实测。

独立进程 smoke 使用临时目录执行：配置校验 → 注入好友和群消息 → 启动 → 两条回显 →
暂停/恢复 → 在线备份 → Windows CTRL_BREAK 正常退出。测试结束清理自身临时数据。

## 未验证及不包含的承诺

- 未安装/调用真实微信 UIA 接入，未读取聊天窗口，未发送真实微信消息。
- 未确认任何微信版本的控件、稳定消息标识、群成员身份、时间字段或送达证据。
- 已验证文本、视觉及两轮连续对话的官方 API 调用；未验证长对话、多图精度、费用和长期可用性。
- 未进行锁屏、远程桌面、客户端升级、断网、扫码恢复或一周以上长期运行测试。
- GitHub CI 的 Conda 配置已提供，远程 Linux / Windows 矩阵尚未实际执行。
- 无端到端消息不丢失保证、无 exactly-once 保证、无自动外部告警或账号不受限制保证。

真实接入前须完成 [TODO 的 P0 项](TODO.md) 与 [实机验收表](WECHAT_ADAPTER.md)。
