# 微信历史自动同步：一个SQLite写入口、两阶段快照

`sync-manager.mjs`供现有Node适配接线；它不直接写SQLite，不操作微信界面，也不发送聊天消息。采集子进程只生产已经校验的快照，数据导入仍通过主进程持有的唯一`vaultClient`标准输入输出写队列。

```js
const sync = createSyncManager({
  python: config.python,
  dataRoot: config.dataRoot,
  vaultClient, // 已有、唯一的Vault JSONL worker，request(op,args)返回value
  capture: {
    enabled: false, // 仅根接线确认新版客户端已登录后启用
    dbRoot: '明确账号的db_storage目录',
    sourceId: 'discover返回的source_id',
    accountId: '可信路由中的账号ID',
    accountUsername: '已核实的微信原生账号ID', // 没有就省略，收发方向保留unknown
    scope: 'chat', seconds: 90, maxDbMiB: 512,
  },
  isReady: () => verifiedLoggedInAndSupported,
  onState: value => updateLocalStatus(value),
});
sync.start();        // 已启用且ready时立即刷新；以后每300000ms检查刷新
await sync.refresh(); // 并发调用共享同一轮，不重入
await sync.stop();    // abort，回退未提交游标，释放采集进程和OS锁；不杀微信
```

默认`capture.enabled`和`isReady`都关闭；此模块不会依据旧状态文件或账号目录自作主张启用采集。`onState`只输出固定状态、时间、代码和计数，不输出候选路径、原文、账号、聊天名、原始收据、内存或密钥。

## 私有两阶段协议

Node启动 `python -u -X utf8 -m vault.capture_worker --root <dataRoot>`，cwd为本插件目录。请求为`{id,op,args}`，回应`{id,ok,value|error}`。

1. `collect`参数为`db_root,source_id,account_id,account_username?,pids?,scope?,seconds?,max_db_mib?`。子进程获得持久OS sync锁，只读采集、清理密钥并校验快照。返回`{jobId,candidatePath,captureReceipt,cursorSource,accountId}`，继续持有锁。不会创建`Vault`、打开业务SQLite或发布`latest.json`。
2. Node记录`vaultClient.request('status')`中的原游标；调用同一客户端的`import_snapshot({snapshotPath,account_id})`。主线程仍可收发微信，不等待其UI锁；历史库导入本身在唯一写队列中完成。
3. 导入返回`complete:true`且匹配snapshot/account后，Node发`commit({jobId,importResult})`。子进程再次验证候选、校验一致性，发布带`sync_job_id`的`latest.json`后释放锁。
4. 导入失败、EOF或停止时，Node仍通过原`vaultClient`调用`set_cursor({source,cursor})`恢复旧游标；首次没有旧游标时调用`clear_cursor({source})`。子进程`abort({jobId?})`或stdin EOF只释放锁，保留旧latest和候选供诊断，绝不发送消息。

`cursorSource`由producer明确返回，manager不自行猜其拼接规则。根writer必须提供`clear_cursor`维护接口；该接口、快照导入和commit都不应作为AI自主工具暴露。

停止发生在已经提交的最后一步时，以同job的持久`latest.json`为最终证据；不能因丢失一条IPC确认就把已经成功的同步改判为失败。尚未提交时需先等待原写队列导入完成／失败并回退游标，再终止producer，避免另一个同步进程在旧导入未结束时进入。只有专用collector无法处理abort时才终止该子进程，从不终止或重启微信。

旧的独立`CaptureService.capture_then_import`保留作离线维护测试，**已运行主Vault writer时不得调用它**。生产自动同步应只走本两阶段模块。

## 验证

- `python -X utf8 -m unittest vault.test_capture_worker vault.test_capture vault.test_snapshot_import vault.test_vault -v`
- `node --test sync-manager.test.mjs`

测试全部使用合成数据库、假collector或假子进程，不读取生产密钥、不运行真实微信采集。已覆盖成功、采集失败、导入失败、无旧游标、EOF、停止、OS锁重入及跨进程互斥、启动和5分钟调度。真实4.2.0兼容性与登录后启用由根流程验证。
