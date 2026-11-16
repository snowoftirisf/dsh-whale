// GPU work is serialized independently from chat reception and UI delivery.
export function createGpuQueue(signal) {
  let tail=Promise.resolve();
  return function run(work) {
    const task=tail.then(()=>{signal?.throwIfAborted();return work()});
    tail=task.catch(()=>{});
    return task;
  };
}
