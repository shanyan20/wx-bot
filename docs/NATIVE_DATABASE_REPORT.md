# 原生消息元数据实测报告（2026-09-11）

## 已验证的结果

用户已明确授权获取当前账号数据库密钥，在本地解密必要消息库，仅查询 shanyan
会话的消息标识、时间和发送者。没有发送消息，也没有查询消息正文。

1. 从指定微信 PID 打开的文件句柄确定账号目录，仅选 message_N.db 消息分片。
2. 对指定 PID 使用进程查询和内存读取权限，通过 Config.Cipher 对象查找候选逐库密钥。
   没有调用候选仓库的自动初始化、主密钥回退、跨 PID 扫描或写进程内存函数。
3. 一个消息分片密钥通过 SQLCipher4 第 1 页 HMAC 校验；密钥不打印、不明文落盘，
   用 Windows 当前用户 DPAPI 保护后保存到 Git 忽略的 data/db-probe/keys.dpapi。
4. 消息数据库及 WAL 读取为内存快照，两次读取内容不一致时拒绝继续。
   按 WAL 盐值、累计校验和及提交边界解析，只应用最后有效提交之前的帧。
   验证最终使用的每个加密页 HMAC，再解密到内存中的 SQLite。
   原数据库不建立写连接、不 checkpoint、不修改；不生成明文数据库文件。
5. SQLite quick_check 返回 ok；根据独立窗口的原生会话标识计算并精确查询该会话表。
   仅选择 local_id、server_id、real_sender_id、create_time、local_type，最多 20 条。
   Name2Id 只查询这些记录引用的发送者行，未枚举联系人名单。
6. 实际取得 19 条元数据，19 个组合键均唯一、19 条 server_id 均非零，
   发送者索引成功映射为两个原生用户标识，所有时间戳有效。

## 跨微信重启和数据库去重

首次实例 PID 13168；随后微信已由外部重启，复测检测到旧绑定失效并拒绝读取。
重新确认 shanyan 当前属于 PID 19452 后，重新执行独立的无障碍 gate 预检/授权激活：
相同 DLL 哈希、唯一候选、0→1 且读回成功。该过程与密钥提取脚本分离，
不会在密钥提取失败时自动写内存。

新实例重新提取并校验消息分片密钥。首次快照 22681 页、有效已提交 WAL 帧 0；
重启后的快照 22805 页，复测分别应用 9 帧、8 帧已提交 WAL。
这些帧数是对应时刻的记录，不是固定配置值。

| 验证项 | 结果 |
|---|---|
| 重启前/后样本条数 | 19 / 19 |
| 共同原生组合键 | 19 |
| 同一键对应元数据 | 全部一致 |
| 第一次入库新增 | 19 |
| 关闭并重开测试 SQLite 后第二次新增 | 0 |
| 发送任务 | 0，全部标记 ignored / metadata_only |

去重测试使用真实原生标识和时间、空正文、隔离临时 Store；未接入模型或发送适配器。
仅汇总结果可公开，账号标识和消息元数据留在本地忽略目录。

## 文件和复测

本次完整回归：132 项测试通过；Ruff 和 pip check 均通过。

- scripts/db_key_probe.py：限定 PID/聊天窗口的只读密钥探测，输出 DPAPI 密钥绑定。
- scripts/db_metadata_probe.py：检查 PID/启动时间/HWND/原生会话标识后读取目标会话元数据。
- scripts/db_replay_check.py：把两次样本放入隔离 Store，验证持久化去重。
- src/wechat_bot/adapters/sqlcipher_snapshot.py：SQLCipher4 页认证、WAL 提交边界解析。
- tests/test_sqlcipher_snapshot.py：合成数据验证，不读取用户微信。

在项目目录执行，PID 必须以当次实例为准：

```powershell
& .\.conda\python.exe -m pip install -e '.[db-probe]'
& .\.conda\python.exe scripts/db_key_probe.py --pid 19452 --title shanyan
& .\.conda\python.exe scripts/db_metadata_probe.py
```

首次元数据样本已保存在 data/db-probe/metadata-first.json；后续读取写入 metadata.json。
两份样本齐备时运行 scripts/db_replay_check.py。密钥绑定过期时会拒绝读取，
需要在确认目标窗口后重新探测，不会自动选择另一个账号。

## 边界与下一阶段

真实消息 ID 的来源已从重复 UIA 行标识转为数据库 local_id 及其范围信息。
组合键包含账号、数据库世代指纹、分片、会话、表和 local_id；server_id 不动态替换键。
当前世代指纹来自数据库盐值，已验证在这次客户端重启中不变，
**不能识别所有数据库还原/克隆/迁移情形**。这仍是诊断实现，不作为无人值守接入。

两次内容读取加 HMAC/quick_check 可以拒绝观察到的变化和损坏，
不等价于微信数据库提供的事务性在线备份接口；后续持续监听需要进一步处理并发写入。
消息分片解密后在内存中可能包含其他聊天，但 SQL 查询仅针对已绑定会话；
没有输出其他聊天正文、密钥或完整解密数据库。

Bot 保持关闭，windows.verified=false。现有 UIA 适配器仍不能把数据库字段
填作 sender_id/body_id/timestamp_id 的 AutomationId。下一阶段是实现并验证
数据库入站与 UIA 出站组合适配器、目标聊天正文解析、只读增量基线、停止屏障及发送回执。
本轮仅做经授权的元数据验证，不声称真实自动收发已通过。

格式参考：[SQLite 文件及 WAL 格式](https://sqlite.org/fileformat2.html)。
密钥对象布局参考已静态审查的 wechatauto-replica 提交 798989c 的 db.py，
本机执行的是独立的受限探测脚本，没有导入该项目。
