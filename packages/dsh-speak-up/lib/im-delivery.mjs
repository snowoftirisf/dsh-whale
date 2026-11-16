// Local compatibility fix: deliver completed speak-up turns through dsh-im.
// The original dice, followup, session history and message source stay unchanged.
export function createSpeakUpDelivery({ config, service, log = () => {} }) {
  const turns = new Map()
  let tail = Promise.resolve()
  let closed = false
  const textOf = (content) => Array.isArray(content)
    ? content.filter((block) => block.type === 'text').map((block) => block.text || '').join('')
    : typeof content === 'string' ? content : ''
  // The model "stays quiet" by answering with a zero-width space, which
  // String#trim keeps; treat invisible-only text as no reply at all.
  const INVISIBLE = new RegExp('[' + '\\s' + [[0x200b, 0x200f], [0x2028, 0x202f], [0x2060, 0x206f], [0xfeff, 0xfeff], [0xad, 0xad], [0x180e, 0x180e]].map(([a, b]) => String.fromCharCode(a) + '-' + String.fromCharCode(b)).join('') + ']+', 'g')
  const visible = (value) => String(value || '').replace(INVISIBLE, '') !== ''

  function event(session, event) {
    if (closed) return
    const sid = session?.id
    const settings = config()
    const route = settings.IM投递
    if (!route || route.sessionId !== sid || !settings.目标会话.includes(sid)) return
    const data = event?.data
    if (event.type === 'turn/start') {
      turns.set(sid, { turn: data?.turn, source: null, steps: new Map() })
      return
    }
    const state = turns.get(sid)
    if (!state || (data?.turn !== undefined && data.turn !== state.turn)) return
    if (event.type === 'user/message' && event.surfaceOp === 'append') {
      // Harness also appends runtime/time/memory snapshots as user messages.
      // They are context, not a second incoming speaker.
      if (data?.source?.form === 'snapshot') return
      // Never send normal user/IM/schedule turns a second time.
      state.source = state.source === null ? data?.source?.kind : 'mixed'
    } else if (event.type === 'step/start') {
      state.step = data?.step
    } else if (event.type === 'assistant/message' && event.surfaceOp === 'append' && !data?.interrupted) {
      state.steps.set(data?.step ?? state.step ?? 0, textOf(data?.message?.content))
    } else if (event.type === 'turn/end') {
      turns.delete(sid)
      const text = [...state.steps.values()].filter(visible).map((value) => value.trim()).join('\n\n')
      if (state.source !== 'speak-up' || (data?.reason?.kind ?? data?.reason) !== 'completed' || !visible(text)) return
      const send = async () => {
        if (closed) return
        const current = config().IM投递
        if (!current || current.sessionId !== sid || current.botId !== route.botId || current.targetId !== route.targetId) return
        const im = service()
        if (typeof im?.send !== 'function') throw new Error('dsh-im 投递服务未就绪')
        const result = await im.send(route.botId, route.targetId, text, { format: 'markdown' })
        if (result?.sent !== true) throw new Error('dsh-im 未返回发送成功')
        log('主动搭话已投递到已配置的 IM 目标')
      }
      // A failed/ambiguous send is logged, never blindly retried.
      tail = tail.then(send).catch((error) => log(`主动搭话 IM 投递失败：${error?.code || error?.name || 'error'}`))
    }
  }
  return { event, idle: () => tail, close() { closed = true; turns.clear() } }
}
