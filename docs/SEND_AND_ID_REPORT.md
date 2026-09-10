# 发送按钮与消息身份验证（2026-09-10）

2026-09-11 进展：用户已授权数据库探测，取得 19 条真实元数据，原生组合键通过
本次客户端重启与 SQLite 持久化去重测试。见 [数据库实测报告](NATIVE_DATABASE_REPORT.md)。
以下保留授权前记录。

## 结论

发送按钮已完成真实窗口上的唯一定位与 Invoke 能力检查，未执行发送。
UIA 消息行没有找到原生消息标识；数据库 ID 的组合键实现及测试已完成，
真实数据库读取仍待授权和验证。Bot 保持关闭，verified=false。

## 发送按钮

目标为 PID 13168 的 shanyan 独立窗口，先验证实际标题子控件。
在唯一 `chat_message_page` 容器内查询以下精确组合：

```toml
send_button_id = ""
send_scope_id = "chat_message_page"
send_button_name = "发送"
send_button_class = "mmui::XOutlineButton"
```

控件类型固定为 Button。实测仅一个匹配，支持 Invoke、可见；
空输入框时 enabled=false，不通过填入测试文字来改变它。
实现已用于适配器发送路径和控制台预检，并同步到本地绑定。
预检允许检查禁用按钮；真正发送必须可见且启用。
匹配缺失、多个匹配（含隐藏匹配）、无 Invoke 均拒绝，不降级坐标或快捷键。
配置了非空发送按钮 ID 时不会偷偷改用名称匹配。
发送前在停止/白名单屏障内重新核对窗口、草稿和按钮，不缓存旧按钮后直接调用。

同时修复 pywinauto 0.6.9 Wrapper.descendants 不接受 auto_id 参数的问题：
改为枚举后精确匹配 element_info.automation_id；原替身测试也改为真实调用签名。
面板新增名称/类名/容器三个字段，并修复表单固定行号造成的重叠。

本地只读证据：data/control-probe.json、data/send-selector-check.json；不上传实例数据。

## 消息 ID：实测限制

当前列表共 7 个行控件，包含正文、附件/名片和时间分隔行。
4 行复用同一 AutomationId，另 3 行为空。Help、ItemType、ItemStatus、
AriaRole、AriaProperties 为空；Legacy Description/Value 也未提供消息标识。
这些行没有子控件，不能继续寻找行内 sender_id/body_id/timestamp_id。
UIA Name 对正文行包含展示内容，对时间行只包含展示日期；不含经过验证的发送者身份
或原生消息 ID。报告不记录实际聊天正文。

RuntimeId 只用于当前控件实例比较，可能被复用，不能当作重启、滚动后稳定的消息 ID。
参考 [Microsoft GetRuntimeId 文档](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getruntimeid)。
正文哈希也不能区分两条相同消息，因此不作为消息唯一键。

## 原生 ID 路径

静态检查已下载的候选源码 db.py（提交 798989c）发现数据库查询使用
local_id、server_id、real_sender_id、create_time、sort_seq。
该信息只来自源码，尚未验证本机数据库架构。

`adapters/native_identity.py` 已实现未来原生读取器的组合键：

```
[版本, 账号身份, 数据库世代, 分片身份, 会话身份, 表身份, local_id]
```

这些字段必须来自经验证的读取器，绝不从 UIA 临时序号推断。
数据库世代在正常读取/进程重启时保持一致；还原、重建、迁移是否改变世代须由读取器
检测并拒绝未经校准的数据源，当前尚未实现该检测。
server_id 可在发送后补齐，不应把已有 local_id 组合键动态替换成 server_id，避免重复入库。
本实现尚未接到运行中的适配器；现有 UIA 对重复消息行仍拒绝运行。

继续实机验证需要明确授权获取当前账号数据库密钥及在本地解密必要消息库。
消息分片可能同时包含其他聊天，密钥访问范围也不能声称只限 shanyan；
应用查询将限定该会话，禁止发送、上传密钥或导出其他聊天正文。
此步骤尚未执行，不把原来的无障碍开关授权扩大为数据库访问授权。

## 测试

本次完整 pytest 结果为 118 passed；src、tests 和新增探测脚本的 Ruff 检查通过。

发送测试覆盖范围限制、隐藏重复、禁用/不可见、缺少 Invoke、错误类名、
缺失配置、禁止 ID 失败后降级、控件替换；真实窗口只执行定位和属性读取。
原生组合键测试覆盖不同账号/库世代/分片/会话/表/消息的区分、无效 ID 拒绝、
分隔符碰撞，以及真实 SQLite Store 对同文不同 ID 保留、同 ID 重读去重。
这些合成数据测试不等于微信数据库接入成功。
