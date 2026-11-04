export interface Workspace { id: string; title: string; research_question: string; field: string; created_at: string }
export interface Source { id: string; title: string; kind: string; uri: string | null; version: string; source_hash: string; line_count: number; rag_indexed: boolean; created_at: string }
export interface Session { id: string; title: string; created_at: string }
export interface Message { id: string; role: 'user' | 'assistant'; content: string; run_id: string | null; created_at: string }
export interface Evidence { id: string; source_id: string; source_hash: string; title: string; uri: string | null; quote: string; start_line: number; end_line: number; locations: { page: number | null; start_seconds: number | null; end_seconds: number | null; block_type: string }[]; method?: string }
export interface Claim { text: string; evidence_ids: string[]; qualification: string }
export interface Comparison { source_id: string; method: string; dataset: string; model: string; metric: string; result: string; conditions: string; comparability: string; evidence_ids: string[]; attributes?: Record<string, string> }
export interface Report { title: string; claims: Claim[]; comparison_rows: Comparison[]; limitations: string[]; verification_status: string }
export type Strategy = 'grep' | 'rag' | 'combined'
export interface Run { id: string; session_id: string; question: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted'; retrieval_strategy: Strategy; allow_network: boolean; max_steps: number; max_tokens: number; model: string; step: number; tokens_used: number; tokens_estimated: boolean; report: Report | null; markdown?: string | null; error: string | null; created_at: string; source_coverage?: { status?: 'complete' | 'partial'; planned_source_ids?: string[]; read_source_ids?: string[]; unread_source_ids?: string[] } }
export interface RunEvent { id: number; type: string; data: Record<string, unknown>; created_at: string }
export interface ToolResult<T> { id: string; status: 'succeeded' | 'failed'; data: T; error: string | null; elapsed_ms: number }
export interface DiscoveryItem { title: string; uri: string; abstract?: string; snippet?: string; authors?: string[]; published_at?: string }
export interface DiscoveryResult { results: DiscoveryItem[]; provider: string; evidence_level: string; warnings?: string[]; note?: string }
export interface CollectionResult { job_id: string; status: 'completed' | 'partial' | 'failed'; variants: string[]; papers: { arxiv_id: string; title: string; status: 'imported' | 'already_present' | 'failed'; source_id?: string; indexed?: boolean; error?: string; index_error?: string }[]; imported_or_present: number; warnings: string[]; note: string }
export interface SearchResult { hits: Evidence[]; warnings: string[]; partial: boolean; truncated: boolean; backends: string[] }
export interface QueryResult { columns: string[]; rows: unknown[][]; truncated: boolean; scope: string }
