# 独立聊天窗口模式（离线实现，默认关闭）

`independent_windows.py` 是自研适配，不复制 `sources/wxauto4-source-fork` 的实现。该fork未找到LICENSE，仅读取其方法契约；已通过**只导入类、不创建实例**检查当前安装的免费wxauto4 41.1.7暴露以下接口：`WeChat.GetAllSubWindow/GetSubWindow`、`WeChatMainWnd.get_sub_wnd/open_separate_window(keywords,resize=False)`及`Chat(core)`。当前安装包的子窗口类常量是`mmui::ChatSingleWindow`，fork旧版的类名不同，不能据fork假定4.2.0已经兼容。

本模块未操作真实微信UI、未发送消息、未生成生产live验收receipt。单元测试仅使用fake对象，测试中的verified字段绝不写入生产配置。

## 根流程接线

```python
from independent_windows import create_window_monitor
selection = create_window_monitor(
    wx, routes,
    config={'enabled': False, 'openMissingAtStartup': False},
    receipt=loaded_live_receipt,
    current_version=observed_client_file_version, # 根进程读取的真实四段exe版本
    wxauto_version=observed_library_version,
    require_native_voice=native_voice_ready,
    receive_voice=receive_voice,
    image_directory=image_directory,
    attachment_directory=attachment_directory,
    data_root=data_root,
)
monitor = selection.initialize()
# 原轮询接口继续使用monitor.candidates/read/select/note_outbound。
# 每个发送part前必须重新取绑定Chat，不要缓存后直接发，也不能仍用主wx。
chat = selection.chat_for(destination)
# root经过原有路径/收件人/幂等校验后，再由同一UI工作线程调用chat.SendMsg/SendFiles。
```

`selection.mode`为`main/independent`；`selection.status()`返回模式、启用原因及逐聊天窗口错误。未开启、无receipt、版本／白名单／能力不匹配时，直接使用**原MainWindowMonitor**，不会枚举或打开独立窗口。

每个独立窗口内部使用`MainWindowMonitor`子类，仅读取自己的ChatInfo和消息控件；复用原`read`、`MessageCursor`、图片、表情、语音、附件解析，不复制一份解析器。默认不调用主窗口GetSession/ChatWith/Show/Focus。可以由根提供已经实测不会切换主聊天的`preview_provider(target)`，用于原读逻辑的预览变化／虚拟化尾部恢复；未提供时保留原游标的保守缺口报错，不编造未读数。

## 绑定与生命周期

- 初始化只枚举一次已有子窗口，准确匹配target和kind；同名同种类存在多个窗口时拒绝选第一个。
- 绑定固定Chat实例，并记录窗口句柄、进程ID、类名、UIA runtime ID；要求与主微信同进程、与主窗口不同句柄。每次读和获取发送Chat时复核身份、窗口存活及消息列表。
- 可明确开启`openMissingAtStartup`，但还需receipt的`startupOpen`已实测通过。只在初始化为每个缺失目标尝试打开一次，之后不自动重开、不盲目重试。
- 初始化绑定失败时整体回到原主窗口模式；初始化完成后窗口关闭或身份变化，只停用该窗口的读写，其他聊天继续。不能把已经绑定的回复悄悄转到主窗口或其他同名窗口。
- 发送接口返回实际绑定的Chat。子窗口发送方法的`who`可能被库忽略，所以不能用“给主wx加who参数”代替绑定检查。根流程必须保留全局UI串行、每段校验和发送回执。
- NativeVoice也要使用这个绑定Chat（其`_api.control/_chat_api`），不能仍以主wx录音。未验证独立窗口语音发送时，`require_native_voice=True`会拒绝启用。
- 不关闭用户窗口，不承诺最小化、托盘、锁屏或完全不抢焦点；媒体下载和发送是否激活窗口仍需真实版本验收。

## Live receipt契约

生产receipt只能由根流程真实验收后保存。必须包含：

- `verified:true, verificationKind:'live', mode:'independent-windows'`。
- `clientVersion`与当前实际四段版本完全一致；`wxautoVersion`与实际库版本一致。
- 带时区`verifiedAt`、非空`evidence`本地验收记录引用、实际`windowClasses`（禁止主窗口类）。
- `routes:[{target,kind,passed:true}]`覆盖当前全部白名单；新增群后需补验收。
- `checks`中的`passiveRead,targetBinding,textReceive,textSend,fileSend,closedWindowStopsSend,identityChangeStopsSend`全部真实通过。
- 启用图片、语音接收、附件时，分别要求`imageReceive,voiceReceive,fileReceive`；原生语音发送要求`nativeVoiceSend`；自动打开要求`startupOpen`。

代码不会自行把单测结果写成live receipt，也不会复制receipt的版本字段冒充实际版本探测。

测试：`python -m unittest test_independent_windows -v`。覆盖多窗口不切主聊天、旧模式fallback、旧消息不回放、媒体解析复用、关闭与误绑定拒绝、一次性打开和live能力门禁。
