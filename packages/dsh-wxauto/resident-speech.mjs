// Singing shares the GPU queue, but never evicts the selected speaking backend.
export function residentSingingRequest({runGpu,request,activeSpeech,alternatives}) {
  return (op,args)=>runGpu(async()=>{
    if(op==='synthesize') {
      const active=activeSpeech();
      for(const backend of alternatives())
        if(backend&&backend!==active&&backend.available())await backend.request('release');
    }
    return request(op,args);
  });
}
