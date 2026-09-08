# 个人微信接入测试报告

测试日期：2026-09-09（Asia/Shanghai）。范围：现有内核回归、UIA 接入契约、只读环境检查。

## 结论

**离线测试通过，真实个人微信收发尚未通过验收。**
本轮未发送真实微信消息，未调用付费模型，也未把合成 UI 控件结果当作微信客户端兼容证明。

## 环境与执行结果

| 项目 | 实测结果 |
|---|---|
| 项目 | E:/project/bot |
| Python | E:/project/bot/.conda/python.exe，3.12.14，Windows AMD64 |
| 微信 | Weixin.exe 4.1.13.12，检测到运行进程 |
| 接入依赖 | 本轮安装 pywinauto 0.6.9、comtypes 1.4.16、pywin32 312、six 1.17.0 |
| 修改前回归 | 52 passed in 5.95s |
| 修改后回归 | 74 passed in 6.01s，新增 22 项 |
| Ruff | exit 0 |
| pip check | exit 0 |
| CLI smoke | exit 0 |
| 模板只读探测 | exit 2，status=blocked，error_type=ValueError；模板未校准，符合预期 |
| 实机收发 | 未执行 |
| GitHub Actions | 工作流已配置；本表为本地结果，不代表云端已执行 |

统一命令：`.conda/python.exe scripts/wechat_test.py`。
执行记录起始 UTC：2026-09-08T19:07:52.480155+00:00（北京时间 2026-09-09 03:07:52）。
原始本地记录：`data/wechat-tests/20260908T190752480155Z/`。
可公开的离线原始结果副本见 `docs/test-results/2026-09-09/`。

## 客户端只读检查

通过 Computer Use 的 `list_windows` 和 `get_window_state(include_text=true,
include_screenshot=false)` 检查当前微信主窗口，返回结构仅包括：

```text
窗口 微信
  窗格 Weixin
  窗格 MMUIRenderSubWindow
```

未返回消息行、消息列表、输入框或发送按钮。项目模板仍为 `verified=false`、
PID=0、TODO 控件字段，尚无经过验证的独立测试会话配置。
因此当前证据不足以启用适配器；这一单次主窗口检查不证明所有独立聊天窗口都不支持 UIA。
本轮没有操作登录、切换聊天或修改客户端设置。

## 已覆盖与未覆盖

新增自动化覆盖 WX-01 至 WX-12，详见 `WECHAT_TEST_PLAN.md`。
使用真实 UIA 解析、游标方法及真实内核/SQLite 进行离线集成；桌面控件和出站传输使用替身。
原有发送回执、重试、uncertain 状态测试保留并通过。

WX-13 至 WX-16 尚未执行：需要指定测试会话、完成控件和稳定身份校准。
未测真实消息到达率、P95、长时间运行、实际断网/锁屏、群快刷和跨客户端重启稳定性。
不支持微信图片消息接入。零样本的实机测试不计算「100% 通过率」。

## 后续处理

1. 在独立测试聊天窗口验证控件；若仍缺少稳定消息/用户标识，则现有 UIA 方案不可启用，
   需要选择并实现另一个符合内核契约的接入方案。
2. 完成方案中的 22 条 Echo 收发样本和故障项目，补充实机报告。
3. 真实好友和群验收通过后，再进行模型服务联调与持续运行观察。

此次源码上传排除 `.conda/`、`.secrets/`、`data/`、本地配置与编辑器配置。
