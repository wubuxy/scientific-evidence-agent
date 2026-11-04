import { useState } from 'react'
import { errorText, request, workspacePath } from './api'

interface Trace { name: string; arguments: Record<string, unknown>; data: Record<string, unknown> | null; error: string | null; status: string; elapsed_ms: number }
export default function ToolTrace({ workspaceId, callId, fallback }: { workspaceId: string; callId?: string; fallback: Record<string, unknown> }) {
  const [trace, setTrace] = useState<Trace | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function load() {
    if (!callId || trace || busy) return
    setBusy(true); setError('')
    try { setTrace(await request<Trace>(workspacePath(workspaceId, `/tools/calls/${encodeURIComponent(callId)}`))) }
    catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return <details onToggle={e => { if (e.currentTarget.open) void load() }}><summary>查看调用记录</summary>
    {busy ? <p>正在读取工具记录…</p> : trace ? <><p>{trace.status === 'succeeded' ? '调用成功' : '调用失败'} · {trace.elapsed_ms} ms</p><strong>调用参数</strong><pre>{JSON.stringify(trace.arguments, null, 2)}</pre><strong>工具返回</strong><pre>{JSON.stringify(trace.data ?? { error: trace.error }, null, 2)}</pre></> : <pre>{JSON.stringify(fallback, null, 2)}</pre>}
    {error && <p className="warning-text">{error}<button className="text-button" onClick={() => void load()}>重新读取</button></p>}
  </details>
}
