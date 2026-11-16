// Keep an uncertain send's original outcome when releasing its UI reply lock
// fails too. Cleanup must run once; neither operation is automatically retried.
export async function withReplyBoundary({begin,send,end,onCleanupError=()=>{}}) {
  await begin();
  let failed=false;
  try {
    return await send();
  } catch(error) {
    failed=true;
    throw error;
  } finally {
    try {await end();}
    catch(error) {
      if(!failed)throw error;
      try {await onCleanupError(error);} catch {}
    }
  }
}
