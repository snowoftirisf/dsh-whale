// Delegate exact original unread voice sources before UI admission. No UI/model calls.
export function createNativeGroupVoiceAdmission({groupInbox,voiceInbox,readCatalog}) {
  return {
    async prepare(record,claimId){
      const queued=groupInbox.read(record);
      if(!queued||queued.claimId!==claimId)throw Error('native-group-voice-claim-mismatch');
      if(queued.state==='submitted')return queued; // Unknown/completed asks never reopen ownership.
      const voices=queued.messages.filter(m=>m.type==='voice');
      if(!voices.length)return queued;
      const catalog=await readCatalog(record.nativeChatKey,{snapshotId:queued.snapshotId});
      if(catalog?.source!=='authenticated-native-voice-catalog'||catalog.accountId!==queued.accountId
        ||catalog.snapshotId!==queued.snapshotId||!Array.isArray(catalog.voices)
        ||catalog.voices.some(v=>v.nativeChatKey!==record.nativeChatKey)
        ||new Set(catalog.voices.map(v=>v.serverId)).size!==catalog.voices.length)throw Error('native-group-voice-catalog-invalid');
      const byId=new Map(catalog.voices.map(v=>[v.serverId,v]));
      for(const m of voices){
        const v=byId.get(m.serverId);
        if(!v||v.direction!=='incoming'||v.nativeId!==m.nativeId||v.nativeChatKey!==queued.nativeChatKey
          ||v.senderId!==m.senderId||v.timestampSeconds!==m.timestampSeconds||v.rawText!==m.text
          ||v.snapshotSourceId!==m.snapshotSourceId||(m.localId!==undefined&&m.localId!==v.localId))
          throw Error('native-group-voice-source-conflict');
      }
      // Awaiting the reader must not authorize a replaced/expanded raw claim.
      const current=groupInbox.read(record);
      if(!current||current.state!=='queued'||current.claimId!==claimId||current.claimHash!==queued.claimHash)
        throw Error('native-group-voice-claim-changed');
      const ids=voices.map(m=>m.serverId);
      if(!voiceInbox.hasChat(record.nativeChatKey))voiceInbox.adoptChat(catalog,{nativeChatKey:record.nativeChatKey,unreadServerIds:ids});
      else voiceInbox.enqueue(catalog);
      const owners=voiceInbox.lookup(record.nativeChatKey,ids);
      // If the receipt save fails, durable voice ownership remains and retry is idempotent.
      return groupInbox.delegateVoices(record,claimId,owners);
    },
  };
}
