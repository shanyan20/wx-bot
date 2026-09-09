# 本机接入验证（2026-09-10）

后续进展：发送按钮现已改用精确属性组合并完成只读验证，消息 ID 的数据库路径
仍待授权。见 [发送与消息身份报告](SEND_AND_ID_REPORT.md)。以下保留当时测试记录。

## 经用户授权后的热激活结果

用户已明确允许进程内存修改测试。已执行独立脚本 `scripts/accessibility_gate.py`，
未导入候选仓库的自动初始化器。结果：**激活成功，但真实收发尚未验证**。

- PID 13168，Weixin.dll 4.1.13.12。
- DLL SHA256：`e3240bf8a4d00593a4b3e6ce6c8b6ac26897622c27f410f6655c4eee17cb3b6d`。
- 静态扫描只有一个目标 RVA：181507688；附近有 Qt accessibility 字符串的 RIP 引用。
- 检查运行指令与文件一致；应用前再次匹配 PID、启动时间、HWND、DLL 哈希及基址。
- 读取原值 0，WriteProcessMemory 返回成功、写入长度 1，读回 1。
- 当前进程保留激活状态；未改磁盘 DLL、系统读屏设置、数据库、聊天内容，未发送消息。
- 不能据此承诺对整个微信进程的稳定性无影响；没有执行恢复字节或重启微信。
- 激活后 pywinauto 和 wxauto4 深度 24 / 节点上限 200 的复测均读取到 100 节点，未截断。

本地证据：`data/gate-preflight.json`、`data/gate-activation.json`、
`data/native-controls-after-gate.json`。含实例信息的证据不上传 GitHub。

已把实际可验证字段写入 `config/windows.example.toml`：
`header_id`、`header_text="shanyan"`、`list_id="chat_message_list"`、
`input_id="chat_input_field"`。`verified` 仍为 false。

### 原适配器仍不满足的条件

发送按钮实际存在（Button / mmui::XOutlineButton），但其 AutomationId 为空；
不能把按钮 Name 写入 send_button_id。多个消息行复用
`chat_message_list.qt_scrollarea_viewport.chat_bubble_item_view`，不能当唯一消息 ID。
当前没有验证消息发送者稳定标识、正文定位以及带时区 ISO 时间字段，相关 TODO 保留。
下一步需要适配真实控件结构及消息去重策略，不能仅切换 verified=true。

### 脚本校验

新增合成 PE 测试覆盖唯一目标成功、多个目标拒绝、缺失字符串引用拒绝、
指向可执行区域拒绝及非 AMD64 拒绝。测试不打开微信进程。
本次完整 pytest：96 passed；新增/修改的 Python 文件 Ruff 检查通过。
脚本默认仅预检；只有显式 --apply-report 且当前身份与先前报告一致才可能写入。
它是独立实验工具，不随 Bot 启动自动运行，也不作为已验证通用版本适配器发布。

以下是授权前的检查记录，保留用于说明判断过程。

最新要求：不使用虚拟机，直接在当前 Windows 环境测试。已撤回尚未提交的虚拟机部署脚本，
未安装虚拟机管理器、驱动或改动系统功能。

## 已执行

- 在项目 .conda 安装 wxauto4 41.1.7 及所需 pillow、psutil、pyperclip、tenacity。
- 使用 scripts/native_probe.py 对指定标题和 PID 进行只读探测；每个后端在独立子进程运行，
  超时 25 秒。未运行高层 WeChat() 初始化器、搜索、点击、剪贴板或发送方法。
- 读取器不输出聊天正文或联系人名称，仅输出节点类型、深度和 ID 是否存在。
- pywinauto：Window + Pane 共 2 节点，AutomationId 均为空。
- wxauto4 的低层 UIA：WindowControl + PaneControl 共 2 节点，AutomationId 均为空。
- 两次遍历均未达到截断上限；结果保存在 data/native-probe.json。
- 本结果说明换用该低层读取库没有解决控件缺失，不等于已经测试该库所有高层功能。

复测命令（PID 须以当次实际值为准）：

```powershell
& .\.conda\python.exe scripts/native_probe.py --title shanyan --pid 13168
```

## 新候选的静态审查

仓库：https://github.com/fanyuantaier/wechatauto-replica
本地审查提交：798989c9b61066c120b59ceba636b557b776ff7f。
仅克隆并读取源码，没有安装、导入或运行该仓库代码。

uia_driver.py 的 _hot_activate_accessibility 会：

1. 确定目标进程的 Weixin.dll 模块地址与磁盘路径。
2. 对 DLL 的字节模式和字符串引用做扫描，推断 Qt accessibility gate 的偏移。
3. 请求进程读写内存权限，读取一个字节，再通过 WriteProcessMemory 将其写为 1。
4. 其 _wake_accessibility 默认遍历符合条件的微信主窗口，不是只修改某个独立聊天窗口。

这是**运行中进程内存修改**，不是普通控件读取或配置调整。开关作用域是整个微信进程，
不能声称它只影响白名单聊天窗口。源码中的匹配逻辑及作者的兼容声明不能替代本机验证。
该热激活函数没有实现把原值恢复的配套事务，也不能假定重置字节会撤销全部 UI 状态变化。
因此当前未执行它。主窗口搜索、键盘发送、自动 OCR 降级也未执行。

## 下一步的具体边界

如果用户同意在本机进行进程内存修改测试，应先实现独立的预检和受限执行：
核实 PID/进程启动时间/DLL 路径与哈希，静态扫描必须有唯一可信候选；失败则不写。
不运行候选库的整体自动初始化或遍历全部微信实例，不操作数据库密钥，不发送消息。
即使激活成功，也只进入控件结构检查阶段；仍需改造消息解析和验证定向发送，
不能把激活成功写成真实收发通过。

用户同意本机测试，并不自动等于同意进程内存修改或读取全部账号数据库。
需要明确接受该新增修改范围及可能影响当前微信稳定性的风险后再执行。
