# 个人微信接入测试方案

日期：2026-09-09。对象：现有 Python 内核及 `WindowsUIAAdapter`，个人微信 Windows 客户端。
本方案沿用项目已有 UIA 接入，不把公众号或企业微信接口当作个人微信接口。

## 分阶段执行

1. **离线回归**：使用 `.conda/python.exe scripts/wechat_test.py`，运行全部 pytest、
   Ruff、依赖一致性与 CLI smoke。模型为 Echo，控件为合成替身，数据库使用临时目录。
2. **只读兼容性检查**：记录微信版本、可访问性控件结构，准备独立测试聊天窗口。
   核实 `config/windows.example.toml` 的每一项字段；先做人工字段验证，再设置 verified。
   `verified` 仅代表操作者已校准，脚本不能证明字段跨重启稳定。
3. **单好友实机收发**：仅指定的测试账号/测试会话，使用独立数据库和 Echo。
   人工从另一账号发送测试文本，逐条核对接收、生成、发送及对端实际收到的结果。
4. **故障与恢复**：独立测试会话验证断网、窗口关闭、已有草稿、发送后失去证据、重启。
5. **群聊及模型联调**：前四步通过后再启用测试群、前缀触发和真实模型。
   图片接入当前尚未实现，标为不支持；不得因模型支持图片就判定微信图片链路通过。

## 测试项目与验收矩阵

| ID | 项目 | 方法与预期 | 自动化证据 |
|---|---|---|---|
| WX-01 | 中文、emoji、换行、时区 | 正文原样保留，消息 ID 带会话作用域，自发标志正确 | test_snapshot_preserves_unicode_identity_and_self_flag |
| WX-02 | 消息字段异常 | 空/重复 ID、无时区、非法日期、空身份、系统行拒绝解析 | test_unreliable_rows_fail_closed |
| WX-03 | 控件歧义 | 重复 sender 控件拒绝解析 | test_duplicate_field_rejected |
| WX-04 | 会话定位 | 标题变化、窗口不存在或不唯一时停止 | test_changed_chat_identity_rejected / test_missing_or_ambiguous_window_rejected |
| WX-05 | 首次启动基线 | 历史消息不处理；两条相同正文但不同 ID 各处理一次 | test_startup_baseline_replay_until_ack_and_identical_text |
| WX-06 | 游标可靠性 | 未 ack 重放；读取失败后不丢失新消息 | test_failed_snapshot_does_not_advance_cursor |
| WX-07 | 内核集成 | UIA 真实解析/poll/ack → SQLite → Echo；自发过滤、滚动重现去重 | test_real_parser_to_engine_sqlite_echo_and_dedup |
| WX-08 | 草稿与输入 | 不覆盖草稿；输入回读不一致不点击发送 | test_existing_draft_prevents_send_and_is_preserved / test_input_readback_mismatch_never_invokes_send |
| WX-09 | 发送前身份检查 | 未知会话、输入后目标变化均不发送 | test_unknown_chat_never_touches_desktop / test_chat_changes_after_typing_never_invokes_send |
| WX-10 | 发送证据 | 新增同正文己方消息才给本地回执；旧行/他人/不同正文不足以确认 | test_new_matching_outgoing_row_is_local_send_evidence / test_no_new_matching_self_row_times_out_uncertain |
| WX-11 | 不确定结果 | invoke 异常/无回执进入 uncertain，内核不自动重发 | test_exception_after_invocation_is_uncertain / test_uncertain_is_never_automatically_retried |
| WX-12 | 只读检查工具 | 模板默认 blocked；仅返回计数，释放资源，不输出聊天正文 | tests/test_wechat_probe.py |
| WX-13 | 客户端字段兼容 | 独立聊天窗口暴露全部必需控件；跨滚动/重启 ID 稳定 | 实机人工，待执行 |
| WX-14 | 真实收发与隔离 | 20 条编号文本 + 2 条相同正文，全部对应一次回复，非白名单零回复 | 实机人工，待执行 |
| WX-15 | 断线、锁屏、重启 | 无错发/自动重发；明确记录启动基线导致的漏收 | 实机人工，待执行 |
| WX-16 | 群与媒体 | 同名成员身份隔离、前缀触发；图片明确不支持 | 实机人工，待执行 |

其余内核回归见原有 `TESTING.md`，包括上下文、暂停、过期、重试、持久化及模型边界。

## 实机步骤

1. 指定一个测试联系人和两个测试账号，人工登录，打开独立聊天窗口。
2. 按 `WECHAT_ADAPTER.md` 校准 PID、窗口标题、会话头、消息字段和发送控件。
   多次快照及 UI 重建后核验稳定 ID；普通昵称和 runtime_id 不算稳定身份。
3. 复制模板到被 Git 忽略的 `config/wechat.local.toml`。使用 Echo、仅测试好友白名单，
   app 下的数据库、日志、心跳、暂停文件分别改到 `../data/wechat-live/` 下。
4. 先执行只读检查（父进程 60 秒超时，超时会终止探测子进程）：

   ```powershell
   & .\.conda\python.exe scripts/wechat_test.py --probe-config config/wechat.local.toml
   ```

   `blocked` 时定位配置或控件问题；`snapshot_readable` 仅证明该时刻能读取快照。
   直接运行 `wechat_probe.py` 没有父进程超时保护，优先使用上述入口。
5. 字段验证通过后启动：

   ```powershell
   & .\.conda\python.exe -m wechat_bot --config config/wechat.local.toml check
   & .\.conda\python.exe -m wechat_bot --config config/wechat.local.toml run
   ```

6. 建立首次基线后人工发送 `WX-LIVE-001` 到 `WX-LIVE-020`（间隔至少 3 秒），再发送
   两条相同正文。记录测试编号、时间、任务状态、对端收到次数与延迟，不提交联系人或正文。
7. 测试草稿、暂停和断网。出现 `uncertain` 时先核对对端，不直接重跑发送。
   `uia_visible` 只证明本地出现消息行，不等于对端已收到。
8. 完成后 Ctrl+C 停止，在独立记录中填写 WX-13 至 WX-16 的实际结果。

## 退出标准

- 离线全部测试、Ruff、pip check、smoke 成功；失败不得写成通过。
- 实机限定样本中：22/22 消息有对应回复、零错发、零重复、非白名单零回复。
- Echo 端到端延迟记录中位数/P95/最大值；初始目标 P95 ≤ 10 秒，超出需分析后复测。
  这是验收目标，不是现有实现性能承诺。首次基线前/断线期间消息单独统计。
- 缺少稳定标识或必要控件时结论为「当前接入条件不满足」，不启用长期运行。

## 依据

- [pywinauto 官方入门](https://pywinauto.readthedocs.io/en/latest/getting_started.html)
- [UIA wrapper 官方 API](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.controls.uiawrapper.html)
- [桌面运行限制](https://pywinauto.readthedocs.io/en/latest/remote_execution.html)

这些文档说明 UIA 工具能力，不证明某一微信版本兼容。
