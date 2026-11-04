export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const error = await response.json()
      const detail = error.detail ?? error.error ?? error.message
      if (typeof detail === 'string') message = detail
      else if (Array.isArray(detail)) message = detail.map(item => `${item.loc?.slice(1).join('.') ?? ''}: ${item.msg}`).join('；')
    } catch { /* Non-JSON gateway errors use the status message. */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}
export const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) })
export const workspacePath = (id: string, path = '') => `/api/v1/workspaces/${encodeURIComponent(id)}${path}`
export function safeUrl(url: string | null | undefined) {
  try { const parsed = new URL(url ?? ''); return ['https:', 'http:'].includes(parsed.protocol) ? parsed.href : undefined } catch { return undefined }
}
export function download(content: BlobPart, filename: string, type = 'text/markdown;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url; link.download = filename; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export function errorText(error: unknown) { return error instanceof Error ? error.message : '操作失败，请稍后重试' }
