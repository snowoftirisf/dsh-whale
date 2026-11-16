# 小鲸本地历史库

仅使用 Python 标准库 SQLite；读取 `.zstd` Harness 旧日志时额外使用现有的 `zstandard`。没有网络端口，不操作微信、不读取密钥、不发送消息，也不调用模型。默认根目录为 `<DATA_ROOT>`，本模块的全部测试均使用临时目录。

## 调用入口

从 `plugins/dsh-wxauto` 目录运行：

```powershell
../../.venv-wxauto/Scripts/python.exe -m vault.cli --root <DATA_ROOT> status
../../.venv-wxauto/Scripts/python.exe -m vault.cli --root <DATA_ROOT> doctor
../../.venv-wxauto/Scripts/python.exe -m vault.cli --root <DATA_ROOT> backup
../../.venv-wxauto/Scripts/python.exe -m vault.cli restore <DATA_ROOT>/backups/具体备份 --destination <DATA_ROOT>-Restored
../../.venv-wxauto/Scripts/python.exe -m vault.cli --root <DATA_ROOT> worker
```

`worker` 是私有标准输入输出 JSONL 协议：请求 `{"id":"1","op":"status","args":{}}`，返回 `{"id":"1","ok":true,"value":{...}}`；错误返回 `ok:false,error`，不含聊天正文或异常局部变量。每次请求在单写线程队列上完成；`{"op":"stop"}` 正常退出。

普通 Python 调用使用 `Vault(root).request(op,args)`。不要从多个独立连接绕过这个写入入口。

## 主要 API

- `ensure_chat(account_id,chat_key,kind,display_name)` 返回 `id` 及聊天字段。`chat_key` 必须是采集层保存的稳定身份；仅改显示名不会改变 `id`。没有可信微信身份时使用本地持久映射，不能仅凭同名自动关联。
- `record_message(chat_id,source,source_id,text='',sender='',sender_id=None,direction='incoming',type='text',timestamp=None,timestamp_quality=None,native_id=None,delivery_state=None,metadata=None)` 返回 `{id,inserted,linked?}`。时间接受 Unix 秒、Unix 毫秒或带时区 ISO 字符串，内部保存毫秒。
- `source_id` 是该来源的永久消息标识；同一来源重放同一编号幂等，相同正文不同编号仍是不同消息。只有可信、账号内稳定的微信消息标识才能作为 `native_id`，用于不同快照／来源间明确关联。不可使用每次快照变化的匿名ID，也不通过正文猜测跨来源重复。
- `record_delivery(message_id,operation_id,state,detail=None)`：`generated/attempted/sent/failed/unknown`。终态不改成其他终态；同一操作重放幂等。失败／结果未知不代表允许自动重新发送。该模块不执行发送。
- `attach(message_id,path,metadata=None)`：文件必须已经位于数据根目录内，保存相对路径、字节数和 SHA256。原图／缩略图、图片尺寸、转写等来源信息可放入附件元数据。
- `search(chat_id,query='',start=None,end=None,limit=50,offset=0,sender=None,allowed_chat_ids=None,max_chars=12000)`；`history` 同接口但不检索关键词；返回新到旧消息、分页位置和 `untrusted_history_data:true`。FTS5 trigram 存在时支持中文子串检索，短词和无 FTS 环境走参数化子串查询。
- `context(chat_id,message_id,before=5,after=5,allowed_chat_ids=None)` 返回旧到新的前后文。所有读取均必须指定聊天；模型工具包装层必须从真实会话路由生成 `allowed_chat_ids`，不让模型自行扩权。
- `save_summary(chat_id,text,source_ids,through_timestamp,id=None)` 验证引用全部属于同一聊天；`get_summaries(chat_id,limit=3,allowed_chat_ids=None)` 取摘要。摘要生成由调用方负责，不能仅凭存储 API 存在就宣称摘要自动运行。
- `set_cursor(source,cursor)` 只在调用方已经完成且验证一轮导入后调用；`status` 返回数量和来源进度，不返回正文。库内消息存储与同步进度使用独立方法，调用方不得把失败导入标为完成。
- `list_chats(account_id=None)` 给本地维护和可信路由使用，不直接向群模型开放全部聊天目录。
- `merge_chat(source_chat_id,target_chat_id)` 是可信本地管理员维护接口，CLI 同时提供 `bind-chat SOURCE_ID TARGET_ID`。只允许同账号、同聊天种类合并；旧ID保留redirect，消息、附件、回执和摘要引用保留。相同 source/native 标识冲突时保留两份原消息并返回冲突数，不通过正文丢弃一份；需管理员核对。`ensure_chat` 的旧key以及历史工具中的旧ID均会解析到新ID。此能力绝不能直接作为小鲸模型工具开放，也不凭显示名自动绑定。schema v1 自动升级到 v2，保留旧数据。

## Harness 历史导入

`import_harness` 请求参数：`file,account_id,session_id,routes`，其中 routes 是 `{target,kind,chat_key?}` 数组。只识别 `source.kind=user`、`rpcId=wxauto-*` 且头部明确匹配目标的消息；不导入裸 UI 指令、工具结果、推理或后台报告。群消息逐条保留发言人；私聊合并输入保留为一条原始批次，不伪造其精确消息边界。只有完成的轮次导入 assistant 文字，状态永远为 `generated`，不能当成已送达。该 importer 是可确认旧记录的保守迁移，不能声称导入所有微信历史。

## 已解密只读快照导入

Python：`from vault.snapshot_import import import_snapshot; import_snapshot(vault,snapshotPath,account_id)`。JSONL 对应 `op=import_snapshot,args={snapshotPath,account_id}`；仅供采集／维护服务调用，不向模型开放。只加载包内`vault/vendor`的固定源码，每次加载核对vendor-lock里的SHA256。外部`reference_dir`被拒绝，不依赖工作区`sources`目录；历史导入只加载纯reader/schema/display，不运行采集或密钥代码。

只接受已经解密、静止、完成checkpoint的快照，保留原reader的UUID/profile、路径/重解析点、sidecar、schema限制，另外核对capture manifest中已声明的plaintext SHA256与认证标记、完整数据库integrity和导入前后源文件指纹。读取始终是immutable、query_only，导入不会修改源文件。压缩正文依赖`zstandard`；缺少时明确失败，不当成空文本；同时检查zstd帧完整结束，避免参考stream_reader把截断帧当作空串。

会话身份使用`account_id + wechat:<talker>`；来源键使用分片、表名、本地rowid，不含每次变化的snapshot UUID。只有正整数server_id作为`wechat-server:<id>`原生关联键。未解析出talker的表保留为`unknown`聊天，不按显示名归并；返回`chat_candidates`供可信操作者核对。缺少manifest账号身份时收发方向为unknown，不从目录推测。数据库里的outgoing记录也不代表投递回执，状态保留unknown；没有原媒体的记录仅存类型和缺失标记，不伪造恢复附件。

全部行扫描及导入后复核成功才推进`wechat-snapshot:<account_id>`游标；中途失败可能留下已经导入的历史行，但不推进成功游标，重试幂等。原分片格式不支持或同来源编号出现不同正文会明确报错，不跳过当成完整导入。摘要和模型调用由上层负责，历史导入本身不会触发回复。

在真实本人资料卡核实账号后，根适配配置可设置 `capture.accountUsername`；sync-manager把它传为 `account_username`本机账号由 discover 根据 db_storage 目录自动确认，不写进仓库。

重导旧unknown记录时，importer仅在同chat、同wechat-snapshot来源和永久source_id、同正整数server native_id、原sender_id非空且完全相同、正文/时间/类型不变时考虑提升。整个快照最终校验通过后才在事务中把unknown方向提升为incoming/outgoing，计数为 `directions_promoted`。缺server ID、缺旧sender或仅跨来源native链接不会提升；sender/已知方向冲突明确失败，不推进cursor。所有旧ID、来源、投递状态与原metadata保留，新增 `direction_resolution`记录旧方向、核实账号、快照/来源/native/sender及原投递状态。outgoing仍不代表sent；此提升不会写投递回执或发送消息。

维护方式是正常加载新importer、配置已核实身份后通过**现有唯一writer**导入新合法chat快照；原source幂等记录会得到上述保守提升。无需打开第二Vault、改生产SQL或重写原snapshot。新快照中不存在的旧消息或证据不足者继续unknown，不宣称已全部补齐方向。

## 备份与恢复

`backup` 使用 SQLite backup API 生成一致数据库，复制 `config/memory/persona/voice/media/outbox/snapshots` 以及根 manifest，最后写校验清单。数据文件夹内的符号链接／junction 拒绝备份。备份失败不会生成完成标记；其目录需由维护人员检查，不自动当成有效备份。

`restore` 仅恢复到尚不存在的目录；先逐文件校验，再复制并运行 SQLite/附件检查。恢复不覆盖现有目录、不恢复微信登录、不自动重写其他插件配置。初次迁移配置、记忆、人格和声线进入统一数据根目录，需要主适配显式完成；此模块不会搬动生产文件。

测试：`python -m unittest vault.test_vault vault.test_snapshot_import -v`，需要包含`zstandard`的Python。当前合成测试通过只证明这些输入契约，不能证明真实微信4.2.0采集兼容。

## 明确触发的 Windows 采集包装器

```powershell
python -X utf8 -m vault.capture --root <DATA_ROOT> status
python -X utf8 -m vault.capture --root <DATA_ROOT> discover --db-root C:/明确账号目录/db_storage
python -X utf8 -m vault.capture --root <DATA_ROOT> capture-then-import --db-root C:/明确账号目录/db_storage --source-id DISCOVER返回值 --account-id 可信路由账号ID --account-username 已核实微信账号ID
```

以上采集命令会读取指定本机微信的运行时密钥，必须由有权限的本地维护流程明确触发，不能向模型开放。`discover`仅列出账号数据库与进程，不读取密钥；`status`不触发扫描。`CaptureService(data_root).capture_then_import(...)`与CLI等价，参数还包括`pids`、`scope='chat'`、`seconds=90`（1–300）、`max_db_mib=512`（1–8192）。账号方向只使用明确传入的`account_username`或原manifest，不从路径推断。没有它时方向保留unknown。

生产采集强制要求 **zstandard==0.25.0、CPython 3.12 AMD64、Windows**；wheel哈希锁在`vendor/references/windows-snapshot/requirements.lock`。模块不会自动安装依赖，不改LOCALAPPDATA，不在导入模块时扫描进程。测试使用注入的假collector，跳过生产依赖检查；不能拿假collector测试宣称4.2.0已经兼容。

输出通过进程内覆盖原collector的`private_root()`定向到`<dataRoot>/snapshots/windows-live/`，保持上游文件不修改、原输出ACL和逐页认证/WAL处理逻辑。每次采集位于该目录的`snapshots/<新UUID>`。只有采集和导入都成功才发布该目录的`latest.json`；失败保留上次成功指针／游标、有效快照与诊断产物。另存`sync-state.json`作为本次尝试状态，无聊天正文或密钥。

`<dataRoot>/vault/sync.lock`是持久文件，Windows使用OS字节锁、其他平台测试使用flock；进程崩溃后由OS释放，不删除所谓“陈旧PID锁”。同进程嵌套与独立进程并发均拒绝`sync-busy`。它仅协调采集/导入，不长期占用微信UI操作锁。

测试：`python -X utf8 -m unittest vault.test_capture vault.test_snapshot_import vault.test_vault -v`。涵盖假采集两次幂等、采集失败、导入后失败回滚检查点、首次失败、跨进程锁及vendored源码校验。尚未执行任何生产密钥读取或真实采集。
