import type { Unstable_TriggerAdapter, Unstable_TriggerItem } from '@assistant-ui/core'
import { useCallback } from 'react'

import type { QiQiClawGateway } from '@/hermes'

import type { ComposerMentionSuggestion } from '../types'

import type { CompletionEntry, CompletionPayload } from './use-live-completion-adapter'
import { useLiveCompletionAdapter } from './use-live-completion-adapter'

const KIND_RE = /^@(file|folder|url|image|tool|git):(.*)$/
const REF_STARTERS = new Set(['file', 'folder', 'url', 'image', 'tool', 'git'])

const STARTER_META: Record<string, string> = {
  file: 'Attach a file reference',
  folder: 'Attach a folder reference',
  url: 'Attach a URL reference',
  image: 'Attach an image reference',
  tool: 'Attach a tool reference',
  git: 'Attach git context'
}

function starterEntries(query: string): CompletionEntry[] {
  const q = query.trim().toLowerCase()
  const kinds = Array.from(REF_STARTERS)
  const filtered = q ? kinds.filter(kind => kind.startsWith(q)) : kinds

  return filtered.map(kind => ({
    text: `@${kind}:`,
    display: `@${kind}:`,
    meta: STARTER_META[kind] || ''
  }))
}

interface AtItemMetadata extends Record<string, string> {
  icon: string
  display: string
  meta: string
  plainText: string
  /** Raw `text` field from the gateway, e.g. `@file:src/main.tsx` or `@diff`. */
  rawText: string
  /** Just the value portion (after `@kind:`), or empty for simple refs. */
  insertId: string
}

function textValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

/** Parse the gateway's `text` field (`@file:src/foo.ts`, `@diff`, `@folder:`) into popover-ready data. */
function classify(entry: CompletionEntry): {
  type: string
  insertId: string
  display: string
  meta: string
} {
  const match = KIND_RE.exec(entry.text)

  if (match) {
    const [, kind, rest] = match

    return {
      type: kind,
      insertId: rest,
      display: textValue(entry.display, rest || `@${kind}:`),
      meta: textValue(entry.meta)
    }
  }

  return {
    type: 'simple',
    insertId: entry.text,
    display: textValue(entry.display, entry.text),
    meta: textValue(entry.meta)
  }
}

/** Live `@` completions backed by the gateway's `complete.path` RPC. */
export function useAtCompletions(options: {
  extraMentions?: ComposerMentionSuggestion[]
  gateway: QiQiClawGateway | null
  sessionId: string | null
  cwd: string | null
}): { adapter: Unstable_TriggerAdapter; loading: boolean } {
  const { extraMentions = [], gateway, sessionId, cwd } = options
  const enabled = Boolean(gateway)
  const mentionsOnly = extraMentions.some(item => item.type === 'group-agent')

  const fetcher = useCallback(
    async (query: string): Promise<CompletionPayload> => {
      const q = query.trim().toLowerCase()
      const starters = starterEntries(query)

      const mentionEntries: CompletionEntry[] = extraMentions
        .filter(item => !q || item.label.toLowerCase().includes(q) || item.text.toLowerCase().includes(q))
        .map(item => ({
          text: item.text,
          display: item.label,
          meta: item.description ?? '',
          type: item.type,
          plainText: item.plainText
        }))

      if (mentionsOnly) {
        return { items: mentionEntries, query }
      }

      if (!gateway) {
        return { items: [...mentionEntries, ...starters], query }
      }

      const word = REF_STARTERS.has(query) ? `@${query}:` : `@${query}`
      const params: Record<string, unknown> = { word }

      if (sessionId) {
        params.session_id = sessionId
      }

      if (cwd) {
        params.cwd = cwd
      }

      try {
        const result = await gateway.request<{ items?: CompletionEntry[] }>('complete.path', params)
        const items = result.items ?? []

        return { items: [...mentionEntries, ...(items.length > 0 ? items : starters)], query }
      } catch {
        return { items: [...mentionEntries, ...starters], query }
      }
    },
    [cwd, extraMentions, gateway, mentionsOnly, sessionId]
  )

  const toItem = useCallback((entry: CompletionEntry, index: number): Unstable_TriggerItem => {
    const classified = classify(entry)

    const metadata: AtItemMetadata = {
      icon: classified.type,
      display: classified.display,
      meta: classified.meta,
      plainText: entry.plainText ? 'true' : '',
      rawText: entry.text,
      insertId: classified.insertId
    }

    return {
      // Unique id keyed on the gateway's full `text` so two entries that share
      // a basename (e.g. multiple `index.ts`) don't collide in keyboard nav.
      id: `${entry.text}|${index}`,
      type: entry.type || classified.type,
      label: classified.display,
      ...(classified.meta ? { description: classified.meta } : {}),
      metadata
    }
  }, [])

  return useLiveCompletionAdapter({ enabled, fetcher, toItem })
}

/** Re-export `classify` for use by the formatter (insertion side). */
export { classify }
