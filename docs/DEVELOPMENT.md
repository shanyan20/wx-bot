# 开发约定

测试用例明细、单用例运行方式和真实模型验证请阅读 [TESTING.md](TESTING.md)。

## 环境与质量检查

```powershell
Set-Location E:/project/bot
conda env create --prefix E:/project/bot/.conda --file environment.yml
conda run --prefix E:/project/bot/.conda python -m pip install -e ".[dev,llm]"
conda run --prefix E:/project/bot/.conda python -m pytest -q
conda run --prefix E:/project/bot/.conda python -m ruff check .
```

核心无第三方运行依赖；HTTP 和 Windows 接入通过 extras 按需安装。
`requirements-dev.lock.txt` 记录本次 Windows 开发验证的第三方版本，不包含实验性 UIA 依赖。
可先安装该清单再 `pip install --no-deps -e .` 复现本次测试环境。

## 分层规则

- 数据契约、状态含义先改文档和测试，再改实现。
- 业务规则放 policy，不将白名单/群触发硬编码在接入库。
- 外部访问封装进 adapters 或 services，不在 domain/storage 引入桌面或模型 SDK。
- 数据库方法不能持有事务跨越 await；每次状态变化检查旧状态。
- 窗口读写都在同一个专用线程，COM 对象不跨线程传递。
- 新异常先判断“是否可能已经发送”，未知情况归 uncertain。
- 注释解释约束、边界和设计原因；不重复描述显而易见的代码。
- 任何待确认信息写成 `TODO(Txx)`，并更新 `docs/TODO.md` 的影响和验收条件。

## 模型扩展

实现 `ReplyModel.reply(prompt, history)` 和 `close()`，在 CLI 组合根选择实现。
可恢复的提供者错误转换成 `ModelError`；不要把 API key 或请求正文放进异常日志。
`engine` 实施请求总时限，HTTP 客户端自身也设置网络超时。
新模型至少测试超时、空回复、非法返回、错误响应、关闭连接和历史隔离。
当前模型不具有文件、命令或业务工具执行权限；增加工具调用须设计独立权限层。

## 数据库演进

当前 `PRAGMA user_version = 1`。遇到未知版本直接拒绝运行，不自动猜测修复。
未来迁移应事务化执行，升级前创建备份，并通过“旧版数据 → 升级 → 新版读取”的测试。
清理消息正文时要单独保留幂等 ID/终态记录，避免破坏去重。

## 测试策略

优先测试行为和故障，而非逐行镜像实现：
重复投递不重复回复、相同文本不同 ID 可回复、同会话有序、不同会话独立、
发送中崩溃不重发、人工暂停不被迟到模型回复覆盖、模型失败兜底、限速及在线备份。
自动测试不访问真实微信或外部模型。UIA 需要独立的实机兼容报告，不能用 mock 测试替代。
