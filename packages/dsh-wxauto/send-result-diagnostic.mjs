// Never persist arbitrary SDK payloads, messages, paths or data dictionaries.
export function sdkResultDiagnostic(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || typeof value.type !== 'string' || !/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(value.type)) return null;
  const result={type:value.type};
  if (['success','failure','error'].includes(value.knownStatus)) result.knownStatus=value.knownStatus;
  if (Number.isSafeInteger(value.messageLength) && value.messageLength>=0
      && typeof value.messageSha256==='string' && /^[a-f0-9]{64}$/.test(value.messageSha256)) {
    result.messageLength=value.messageLength;
    result.messageSha256=value.messageSha256;
  }
  return result;
}

export function lastSendFailure(event, part, target, at) {
  const diagnostic=sdkResultDiagnostic(event.sdkResult);
  return {code:event.code,target,kind:part.kind,at,...(diagnostic?{sdkResult:diagnostic}:{})};
}

export function renderedFileEvidence(value) {
  if (!value || value.kind!=='rendered-file-row' || value.direction!=='self'
      || value.rowClass!=='mmui::ChatBubbleItemView' || value.stableObservations!==2
      || value.recipientBytesVerified!==false || !Number.isSafeInteger(value.filenameLength)
      || value.filenameLength<1 || value.filenameLength>255
      || !/^[a-f0-9]{64}$/.test(value.rowRuntimeIdSha256??'')
      || !/^[a-f0-9]{64}$/.test(value.filenameSha256??'')) return null;
  return {kind:value.kind,direction:value.direction,rowClass:value.rowClass,
    rowRuntimeIdSha256:value.rowRuntimeIdSha256,filenameSha256:value.filenameSha256,
    filenameLength:value.filenameLength,stableObservations:2,recipientBytesVerified:false};
}
