import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'
import type { HeavyStatus } from '../types'

const PANE = 'heavy'
const TITLE = '🧠 memory · 🪙 tokens'
const status = atom({ plugin: 'heavy-pane', key: 'status' } as const, '' as HeavyStatus, { shape: 'status-1' })

async function refresh($: EngineInterface) {
  const r = await $.process.run(['/bin/sh', '-lc', 'PATH="$HOME/.local/bin:$PATH" heavy status'])
  const text: string = r.exitCode === 0 ? r.stdout.trim() : 'heavy not found — run install.sh'
  await update($, status, () => text)
  const m = text.match(/memory: (\d+)% free · pressure (\S+)/)
  const t = text.match(/tokens today (\d+M) \((\d+)%/)
  const n = (s: string) => (text.match(new RegExp(s, 'g')) ?? []).length
  $.ui.status(m ? `🧠 ${m[1]}% · ${m[2]} · ▶${n('▶')} ⏸${n('⏸')} ⏳${n('⏳')}${t ? ` · 🪙 ${t[1]} (${t[2]}%)` : ''}` : undefined)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'heavy', description: 'Open the heavy memory / token pane' })
    void $.ui.open({ id: PANE, title: TITLE })
    void refresh($)
    $.clock.every(10000, () => refresh($))
    return next(e)
  })

  on('command.run', { command: 'heavy' }, async $ => {
    await $.ui.open({ id: PANE, title: TITLE })
    await refresh($)
    return { text: 'heavy pane opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const lines = ((await read($, status)) || 'reading…').split('\n')
    const tok = lines.find(l => l.startsWith('tokens')) ?? ''
    const [head, ...jobs] = lines.filter(l => l !== tok)
    return (
      <Box flexDirection="column">
        <Text bold>{head}</Text>
        {jobs.length === 0 ? <Text dimColor>no heavy jobs</Text> : jobs.map(j => <Text>{j.trim()}</Text>)}
        <Text>{tok}</Text>
        <Text dimColor>refreshes every 10s · big jobs auto-pause when memory runs low</Text>
      </Box>
    )
  })
}
