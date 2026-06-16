import { AssistantRuntimeProvider, ExportedMessageRepository, type ThreadMessage } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type * as React from 'react'
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'

import { Thread } from '@/components/assistant-ui/thread'
import { Backdrop } from '@/components/Backdrop'
import { PromptOverlays } from '@/components/prompt-overlays'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  appendGroupAssistantMessage,
  createGroupAgent,
  createGroupRoom,
  deleteGroupAgent,
  getGroupRoom,
  type GroupAgentRole,
  type GroupAgentRoleType,
  type GroupMessage,
  type GroupRoomBundle,
  listGroupRooms,
  listGroupSessions,
  listSavedModels,
  type QiQiClawGateway,
  type SavedModel,
  sendGroupMessage,
  type SessionCreateResponse,
  updateGroupAgent
} from '@/hermes'
import { useI18n } from '@/i18n'
import {
  appendAssistantTextPart,
  appendReasoningPart,
  assistantTextPart,
  type ChatMessage,
  type ChatMessagePart,
  type GatewayEventPayload,
  reasoningPart,
  renderMediaTags,
  textPart,
  upsertToolPart
} from '@/lib/chat-messages'
import { attachmentDisplayText, coerceGatewayText, coerceThinkingText, toRuntimeMessage } from '@/lib/chat-runtime'
import { Cpu, Lock, Plus, RefreshCw, Trash2 } from '@/lib/icons'
import { useIncrementalExternalStoreRuntime } from '@/lib/incremental-external-store-runtime'
import { cn } from '@/lib/utils'
import { setClarifyRequest } from '@/store/clarify'
import { clearComposerAttachments, type ComposerAttachment } from '@/store/composer'
import { notify, notifyError } from '@/store/notifications'
import { clearAllPrompts, setApprovalRequest, setSecretRequest, setSudoRequest } from '@/store/prompts'
import {
  $currentCwd,
  $currentModel,
  $currentProvider,
  $introPersonality,
  $introSeed,
  setActiveSessionId,
  setSessions
} from '@/store/session'
import type { RpcEvent } from '@/types/hermes'

import { ChatBar, ChatBarFallback } from '../chat/composer'
import type { ChatBarState } from '../chat/composer/types'
import type { DroppedFile } from '../chat/hooks/use-composer-actions'
import { titlebarHeaderBaseClass, titlebarHeaderShadowClass } from '../shell/titlebar'

interface GroupChatViewProps extends React.ComponentProps<'section'> {
  gateway: QiQiClawGateway | null
  maxVoiceRecordingSeconds?: number
  onAddUrl: (url: string) => void
  onAttachDroppedItems: (candidates: DroppedFile[]) => Promise<boolean | void> | boolean | void
  onAttachImageBlob: (blob: Blob) => Promise<boolean | void> | boolean | void
  onCancel: () => Promise<void> | void
  onPasteClipboardImage: () => void
  onPickFiles: () => void
  onPickFolders: () => void
  onPickImages: () => void
  onRemoveAttachment: (id: string) => void
  onTranscribeAudio?: (audio: Blob) => Promise<string>
}

const ROLE_LABELS: Record<GroupAgentRoleType, string> = {
  proposer: '方案提出者',
  opponent: '反对者',
  fact_checker: '事实核查员',
  risk_reviewer: '风险审查员',
  execution_reviewer: '执行评审员',
  consensus_builder: '意见统一者',
  custom: '自定义智能体'
}

const ROLE_OPTIONS: GroupAgentRoleType[] = [
  'proposer',
  'opponent',
  'fact_checker',
  'risk_reviewer',
  'execution_reviewer',
  'consensus_builder',
  'custom'
]

function modelLabel(agent: GroupAgentRole): string {
  if (!agent.saved_model_id) {
    return '未选择模型'
  }

  const provider = agent.provider_snapshot || '模型库'
  const model = agent.model_snapshot || agent.saved_model_id

  return `${provider} / ${model}`
}

function agentInitial(name: string): string {
  const trimmed = name.trim()

  return (trimmed[0] || 'A').toUpperCase()
}

type RevealedGroupMessage = Partial<Pick<GroupMessage, 'content' | 'reasoning' | 'reasoning_content'>>

function reasoningText(message: GroupMessage): string {
  return (
    message.reasoning ||
    message.reasoning_content ||
    (typeof message.reasoning_details === 'string' ? message.reasoning_details : '')
  )
}

function mergeRoomMessages(existing: GroupMessage[], incoming: GroupMessage[]): GroupMessage[] {
  const byId = new Map(existing.map(message => [message.id, message]))

  for (const message of incoming) {
    byId.set(message.id, message)
  }

  return [...byId.values()].sort((a, b) => a.created_at - b.created_at)
}

function optimisticUserMessage(
  roomId: string,
  content: string,
  attachments: Array<Record<string, unknown>>
): GroupMessage {
  return {
    attachments,
    content,
    created_at: Date.now() / 1000,
    id: `local_user_${Date.now()}`,
    mentions: [],
    phase: 'idle',
    room_id: roomId,
    round_index: 0,
    run_id: null,
    sender_name: '用户',
    sender_role_id: null,
    sender_type: 'user'
  }
}

const MAIN_AGENT_NAME = '主 agent'
const MAIN_AGENT_ROLE_ID = 'main-agent'
const STREAM_DELTA_FLUSH_MS = 33
const MENTION_BEFORE_BOUNDARY = new Set(['(', '[', '{', '<'])

const MENTION_AFTER_BOUNDARY = new Set([
  '.',
  ',',
  '!',
  '?',
  ';',
  ':',
  '，',
  '。',
  '！',
  '？',
  '；',
  '：',
  ')',
  ']',
  '}',
  '>'
])

function groupMessageToChatMessage(message: GroupMessage): ChatMessage {
  const text =
    message.sender_type === 'agent'
      ? `@${message.sender_name}\n\n${message.content}`
      : message.content

  const parts: ChatMessagePart[] = []
  const reasoning = reasoningText(message)

  if (message.sender_type === 'agent' && reasoning?.trim()) {
    parts.push(reasoningPart(reasoning))
  }

  if (text) {
    parts.push(message.sender_type === 'agent' ? assistantTextPart(text) : textPart(text))
  }

  return {
    id: message.id,
    role: message.sender_type === 'user' ? 'user' : 'assistant',
    parts,
    timestamp: message.created_at
  }
}

function hasMentionBoundary(content: string, start: number, end: number): boolean {
  const before = start > 0 ? content[start - 1] : ''
  const after = end < content.length ? content[end] : ''
  const beforeOk = !before || /\s/.test(before) || MENTION_BEFORE_BOUNDARY.has(before)
  const afterOk = !after || /\s/.test(after) || MENTION_AFTER_BOUNDARY.has(after)

  return beforeOk && afterOk
}

function hasAgentMention(content: string, agentName: string): boolean {
  const normalized = content.replace(/@simple:`(@[^`]+)`/g, '$1')
  const lower = normalized.toLowerCase()
  const token = `@${agentName.toLowerCase()}`
  let fromIndex = 0

  while (fromIndex < lower.length) {
    const index = lower.indexOf(token, fromIndex)

    if (index === -1) {
      return false
    }

    if (hasMentionBoundary(normalized, index, index + token.length)) {
      return true
    }

    fromIndex = index + 1
  }

  return false
}

function hasOnlineAgentMention(content: string, agents: GroupAgentRole[]): boolean {
  return agents.some(agent => agent.enabled && hasAgentMention(content, agent.name))
}

function visiblePromptText(rawText: string, attachments: ComposerAttachment[]): string {
  const visibleText = rawText.trim()

  const contextRefs = attachments
    .map(attachment => attachment.refText)
    .filter(Boolean)
    .join('\n')

  const attachmentRefs = attachments.map(attachmentDisplayText).filter((ref): ref is string => Boolean(ref))
  const hasImage = attachments.some(attachment => attachment.kind === 'image')

  return (
    [contextRefs, visibleText].filter(Boolean).join('\n\n') ||
    (hasImage ? 'What do you see in this image?' : attachmentRefs.join('\n'))
  )
}

function inlineErrorMessage(error: unknown, fallback: string): string {
  const raw = error instanceof Error ? error.message : typeof error === 'string' ? error : fallback

  return (raw.match(/Error invoking remote method '[^']+': Error: (.+)$/)?.[1] ?? raw).replace(/^Error:\s*/, '').trim()
}

type QueuedStreamDeltas = {
  assistant: string
  reasoning: string
}

export function GroupChatView({
  className,
  gateway,
  maxVoiceRecordingSeconds,
  onAddUrl,
  onAttachDroppedItems,
  onAttachImageBlob,
  onCancel,
  onPasteClipboardImage,
  onPickFiles,
  onPickFolders,
  onPickImages,
  onRemoveAttachment,
  onTranscribeAudio,
  ...props
}: GroupChatViewProps) {
  const queryClient = useQueryClient()
  const { t } = useI18n()
  const location = useLocation()
  const routeRoomId = location.pathname.match(/^\/group-chat\/([^/]+)$/)?.[1]
  const decodedRouteRoomId = routeRoomId ? decodeURIComponent(routeRoomId) : null
  const handledNewKeyRef = useRef<string | null>(null)
  const currentCwd = useStore($currentCwd)
  const currentModel = useStore($currentModel)
  const currentProvider = useStore($currentProvider)
  const introPersonality = useStore($introPersonality)
  const introSeed = useStore($introSeed)
  const [activeRoomId, setActiveRoomId] = useState<string | null>(decodedRouteRoomId)
  const [busy, setBusy] = useState(false)
  const [agentDialogOpen, setAgentDialogOpen] = useState(false)
  const [editingAgent, setEditingAgent] = useState<GroupAgentRole | null>(null)
  const [agentsOpen, setAgentsOpen] = useState(false)
  const [mainAgentMessages, setMainAgentMessages] = useState<Record<string, ChatMessage[]>>({})
  const mainAgentMessagesRef = useRef<Record<string, ChatMessage[]>>({})
  const mainAgentSessionByRoomRef = useRef<Map<string, string>>(new Map())
  const roomByMainAgentSessionRef = useRef<Map<string, string>>(new Map())
  const mainAgentStreamIdBySessionRef = useRef<Map<string, string>>(new Map())
  const queuedDeltasRef = useRef<Map<string, QueuedStreamDeltas>>(new Map())
  const flushHandleRef = useRef<number | null>(null)
  const lastFlushAtRef = useRef(0)

  const roomsQuery = useQuery({
    queryKey: ['group-chat', 'rooms'],
    queryFn: listGroupRooms
  })

  const rooms = roomsQuery.data?.rooms ?? []
  const selectedRoomId = decodedRouteRoomId ?? activeRoomId ?? rooms[0]?.id ?? null

  const roomQuery = useQuery({
    queryKey: ['group-chat', 'room', selectedRoomId],
    queryFn: () => getGroupRoom(selectedRoomId!),
    enabled: Boolean(selectedRoomId)
  })

  const bundle = roomQuery.data ?? null
  const room = bundle?.room ?? null
  const agents = useMemo(() => bundle?.agents ?? [], [bundle?.agents])
  const serverMessages = useMemo(() => bundle?.messages ?? [], [bundle?.messages])
  const serverMessageIds = useMemo(() => new Set(serverMessages.map(message => message.id)), [serverMessages])
  const [localMessages, setLocalMessages] = useState<GroupMessage[]>([])
  const [revealedMessages, setRevealedMessages] = useState<Record<string, RevealedGroupMessage>>({})
  const revealTimersRef = useRef<Set<number>>(new Set())
  const enabledAgents = useMemo(() => agents.filter(agent => agent.enabled), [agents])
  const waitingModelCount = useMemo(() => agents.filter(agent => agent.model_status === 'missing').length, [agents])

  const savedModelsQuery = useQuery({
    queryKey: ['models', 'library', 'group-chat'],
    queryFn: listSavedModels
  })

  const savedModels = savedModelsQuery.data?.models ?? []

  const messages = useMemo(
    () =>
      [
        ...serverMessages,
        ...localMessages.filter(message => message.room_id === selectedRoomId && !serverMessageIds.has(message.id))
      ].map(message => {
        const revealed = revealedMessages[message.id]

        return revealed ? { ...message, ...revealed } : message
      }),
    [localMessages, revealedMessages, selectedRoomId, serverMessageIds, serverMessages]
  )

  const roomMainAgentMessages = useMemo(
    () => (selectedRoomId ? (mainAgentMessages[selectedRoomId] ?? []) : []),
    [mainAgentMessages, selectedRoomId]
  )

  const showIntro = Boolean(selectedRoomId && !messages.length && !roomMainAgentMessages.length && !roomQuery.isLoading)

  useEffect(() => {
    mainAgentMessagesRef.current = mainAgentMessages
  }, [mainAgentMessages])

  useEffect(
    () => () => {
      for (const timer of revealTimersRef.current) {
        window.clearInterval(timer)
      }

      if (flushHandleRef.current !== null) {
        window.clearTimeout(flushHandleRef.current)
        flushHandleRef.current = null
      }

      revealTimersRef.current.clear()
    },
    []
  )

  const refreshGroupSessions = useCallback(async () => {
    const result = await listGroupSessions()

    setSessions(prev => {
      const nextById = new Map(prev.filter(session => session.source !== 'group-chat').map(session => [session.id, session]))

      for (const session of result.sessions) {
        nextById.set(session.id, session)
      }

      return [...nextById.values()].sort((a, b) => (b.last_active || b.started_at) - (a.last_active || a.started_at))
    })
  }, [])

  const mutateMainAgentMessage = useCallback(
    (
      sessionId: string,
      transform: (parts: ChatMessagePart[], message: ChatMessage) => ChatMessagePart[],
      seed: () => ChatMessagePart[],
      opts: { pending?: boolean | ((message: ChatMessage) => boolean) } = {}
    ) => {
      const roomId = roomByMainAgentSessionRef.current.get(sessionId)

      if (!roomId) {
        return
      }

      setMainAgentMessages(prev => {
        const streamId = mainAgentStreamIdBySessionRef.current.get(sessionId) ?? `main-agent-stream-${sessionId}-${Date.now()}`
        mainAgentStreamIdBySessionRef.current.set(sessionId, streamId)
        const messagesForRoom = prev[roomId] ?? []
        const existing = messagesForRoom.find(message => message.id === streamId)

        const pending = (message: ChatMessage) =>
          typeof opts.pending === 'function' ? opts.pending(message) : (opts.pending ?? true)

        const nextMessage: ChatMessage = existing
          ? {
              ...existing,
              parts: transform(existing.parts, existing),
              pending: pending(existing)
            }
          : {
              id: streamId,
              role: 'assistant',
              parts: seed(),
              pending: true
            }

        return {
          ...prev,
          [roomId]: existing
            ? messagesForRoom.map(message => (message.id === streamId ? nextMessage : message))
            : [...messagesForRoom, nextMessage]
        }
      })
    },
    []
  )

  const flushQueuedDeltas = useCallback(
    (sessionId?: string) => {
      const queue = queuedDeltasRef.current
      const ids = sessionId ? [sessionId] : [...queue.keys()]

      for (const id of ids) {
        const queued = queue.get(id)

        if (!queued) {
          continue
        }

        queue.delete(id)

        if (queued.reasoning) {
          mutateMainAgentMessage(
            id,
            parts => appendReasoningPart(parts, queued.reasoning),
            () => [reasoningPart(queued.reasoning)]
          )
        }

        if (queued.assistant) {
          mutateMainAgentMessage(
            id,
            parts => appendAssistantTextPart(parts, queued.assistant),
            () => [assistantTextPart(queued.assistant)]
          )
        }
      }
    },
    [mutateMainAgentMessage]
  )

  const scheduleDeltaFlush = useCallback(() => {
    if (flushHandleRef.current !== null) {
      return
    }

    const sinceLast = performance.now() - lastFlushAtRef.current
    const delay = Math.max(0, STREAM_DELTA_FLUSH_MS - sinceLast)

    flushHandleRef.current = window.setTimeout(() => {
      flushHandleRef.current = null
      lastFlushAtRef.current = performance.now()
      flushQueuedDeltas()
    }, delay)
  }, [flushQueuedDeltas])

  const queueDelta = useCallback(
    (sessionId: string, key: keyof QueuedStreamDeltas, delta: string) => {
      if (!delta) {
        return
      }

      const queued = queuedDeltasRef.current.get(sessionId) ?? { assistant: '', reasoning: '' }
      queued[key] += delta
      queuedDeltasRef.current.set(sessionId, queued)
      scheduleDeltaFlush()
    },
    [scheduleDeltaFlush]
  )

  const runtimeMessageRepository = useMemo(() => {
    const items: { message: ThreadMessage; parentId: string | null }[] = []
    let parentId: string | null = null

    const chatMessages = [...messages.map(groupMessageToChatMessage), ...roomMainAgentMessages]

    for (const message of chatMessages) {
      const runtimeMessage = toRuntimeMessage(message)
      items.push({ message: runtimeMessage, parentId })
      parentId = runtimeMessage.id
    }

    return ExportedMessageRepository.fromBranchableArray(items, { headId: parentId })
  }, [messages, roomMainAgentMessages])

  const runtime = useIncrementalExternalStoreRuntime<ThreadMessage>({
    messageRepository: runtimeMessageRepository,
    isRunning: busy,
    setMessages: () => undefined,
    onNew: async () => undefined,
    onCancel: async () => onCancel()
  })

  const chatBarState = useMemo<ChatBarState>(
    () => ({
      model: {
        model: currentModel || MAIN_AGENT_NAME,
        provider: currentProvider || MAIN_AGENT_NAME,
        canSwitch: false,
        loading: !currentModel && !currentProvider
      },
      tools: {
        enabled: true,
        label: 'Add context',
        suggestions: enabledAgents.map(agent => ({
          text: `@${agent.name}`,
          display: agent.name,
          meta: ROLE_LABELS[agent.role_type] ?? agent.role_type
        }))
      },
      voice: {
        enabled: true,
        active: false
      }
    }),
    [currentModel, currentProvider, enabledAgents]
  )

  const refreshRoom = useCallback(
    async (roomId = selectedRoomId) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['group-chat', 'rooms'] }),
        roomId ? queryClient.invalidateQueries({ queryKey: ['group-chat', 'room', roomId] }) : undefined
      ])
    },
    [queryClient, selectedRoomId]
  )

  const mergeRoomBundleMessages = useCallback(
    (incomingMessages: GroupMessage[], roomId = selectedRoomId) => {
      if (!roomId) {
        return
      }

      queryClient.setQueryData<GroupRoomBundle>(['group-chat', 'room', roomId], current => {
        if (!current) {
          return current
        }

        return {
          ...current,
          messages: mergeRoomMessages(current.messages, incomingMessages)
        }
      })
    },
    [queryClient, selectedRoomId]
  )

  const revealAgentMessages = useCallback((replyMessages: GroupMessage[]) => {
    for (const message of replyMessages) {
      if (message.sender_type !== 'agent') {
        continue
      }

      const fullReasoning = reasoningText(message)
      const fullContent = message.content

      const reasoningKey: keyof Pick<GroupMessage, 'reasoning' | 'reasoning_content'> = message.reasoning
        ? 'reasoning'
        : 'reasoning_content'

      setRevealedMessages(prev => ({
        ...prev,
        [message.id]: {
          content: '',
          [reasoningKey]: fullReasoning ? '' : undefined
        }
      }))

      let reasoningIndex = 0
      let contentIndex = 0
      const step = Math.max(2, Math.ceil((fullReasoning.length + fullContent.length) / 180))

      const timer = window.setInterval(() => {
        let done = false

        setRevealedMessages(prev => {
          if (reasoningIndex < fullReasoning.length) {
            reasoningIndex = Math.min(fullReasoning.length, reasoningIndex + step)

            return {
              ...prev,
              [message.id]: {
                ...(prev[message.id] ?? {}),
                content: '',
                [reasoningKey]: fullReasoning.slice(0, reasoningIndex)
              }
            }
          }

          contentIndex = Math.min(fullContent.length, contentIndex + step)
          done = contentIndex >= fullContent.length

          return {
            ...prev,
            [message.id]: {
              ...(prev[message.id] ?? {}),
              content: fullContent.slice(0, contentIndex),
              [reasoningKey]: fullReasoning
            }
          }
        })

        if (done) {
          window.clearInterval(timer)
          revealTimersRef.current.delete(timer)
        }
      }, 24)

      revealTimersRef.current.add(timer)
    }
  }, [])

  const completeMainAgentMessage = useCallback(
    (sessionId: string, finalText: string) => {
      const roomId = roomByMainAgentSessionRef.current.get(sessionId)

      if (!roomId) {
        return
      }

      flushQueuedDeltas(sessionId)

      const streamId = mainAgentStreamIdBySessionRef.current.get(sessionId)
      const currentMessage = (mainAgentMessagesRef.current[roomId] ?? []).find(message => message.id === streamId)
      const finalRendered = renderMediaTags(finalText).trim()

      const existingText =
        currentMessage?.parts
          .filter((part): part is Extract<ChatMessagePart, { type: 'text' }> => part.type === 'text')
          .map(part => part.text)
          .join('') ?? ''

      const persistedText = finalRendered || existingText

      const persistedReasoning =
        currentMessage?.parts
          .filter((part): part is Extract<ChatMessagePart, { type: 'reasoning' }> => part.type === 'reasoning')
          .map(part => part.text)
          .join('') ?? ''

      setMainAgentMessages(prev => {
        const messagesForRoom = prev[roomId] ?? []

        const nextMessages = messagesForRoom.map(message => {
          if (!streamId || message.id !== streamId) {
            return message
          }

          const kept = message.parts.filter(part => part.type !== 'text')
          const nextParts = persistedText ? [...kept, assistantTextPart(persistedText)] : kept

          return {
            ...message,
            parts: nextParts,
            pending: false
          }
        })

        return { ...prev, [roomId]: nextMessages }
      })

      clearAllPrompts(sessionId)
      setBusy(false)
      void appendGroupAssistantMessage(roomId, {
        content: persistedText || finalText,
        reasoning: persistedReasoning || null,
        sender_name: MAIN_AGENT_NAME,
        sender_role_id: MAIN_AGENT_ROLE_ID
      })
        .then(result => {
          setMainAgentMessages(prev => {
            const streamId = mainAgentStreamIdBySessionRef.current.get(sessionId)
            const messagesForRoom = prev[roomId] ?? []

            return {
              ...prev,
              [roomId]: streamId ? messagesForRoom.filter(message => message.id !== streamId) : messagesForRoom
            }
          })
          mainAgentStreamIdBySessionRef.current.delete(sessionId)
          mergeRoomBundleMessages([result.message], roomId)
          void refreshRoom(roomId)
          void refreshGroupSessions()
        })
        .catch(() => undefined)
    },
    [flushQueuedDeltas, mergeRoomBundleMessages, refreshGroupSessions, refreshRoom]
  )

  const failMainAgentMessage = useCallback(
    (sessionId: string, errorMessage: string) => {
      const roomId = roomByMainAgentSessionRef.current.get(sessionId)

      if (!roomId) {
        return
      }

      flushQueuedDeltas(sessionId)
      clearAllPrompts(sessionId)
      setMainAgentMessages(prev => {
        const streamId = mainAgentStreamIdBySessionRef.current.get(sessionId) ?? `main-agent-error-${sessionId}-${Date.now()}`
        mainAgentStreamIdBySessionRef.current.set(sessionId, streamId)
        const messagesForRoom = prev[roomId] ?? []
        const existing = messagesForRoom.find(message => message.id === streamId)

        const nextMessage: ChatMessage = existing
          ? { ...existing, error: errorMessage, pending: false }
          : {
              id: streamId,
              role: 'assistant',
              parts: [],
              error: errorMessage,
              pending: false
            }

        return {
          ...prev,
          [roomId]: existing
            ? messagesForRoom.map(message => (message.id === streamId ? nextMessage : message))
            : [...messagesForRoom, nextMessage]
        }
      })
      setBusy(false)
    },
    [flushQueuedDeltas]
  )

  const handleMainAgentEvent = useCallback(
    (event: RpcEvent) => {
      const sessionId = event.session_id || ''

      if (!sessionId || !roomByMainAgentSessionRef.current.has(sessionId)) {
        return
      }

      const payload = event.payload as GatewayEventPayload | undefined

      if (event.type === 'message.start') {
        flushQueuedDeltas(sessionId)
        mutateMainAgentMessage(sessionId, parts => parts, () => [], { pending: true })
      } else if (event.type === 'message.delta') {
        queueDelta(sessionId, 'assistant', coerceGatewayText(payload?.text))
      } else if (event.type === 'reasoning.delta') {
        queueDelta(sessionId, 'reasoning', coerceThinkingText(payload?.text))
      } else if (event.type === 'reasoning.available') {
        const reasoning = coerceThinkingText(payload?.text)

        if (reasoning) {
          flushQueuedDeltas(sessionId)
          mutateMainAgentMessage(
            sessionId,
            parts => [...parts.filter(part => part.type !== 'reasoning'), reasoningPart(reasoning)],
            () => [reasoningPart(reasoning)]
          )
        }
      } else if (event.type === 'tool.start' || event.type === 'tool.progress' || event.type === 'tool.generating') {
        flushQueuedDeltas(sessionId)
        mutateMainAgentMessage(
          sessionId,
          parts => upsertToolPart(parts, payload, 'running'),
          () => upsertToolPart([], payload, 'running')
        )
      } else if (event.type === 'tool.complete') {
        flushQueuedDeltas(sessionId)
        mutateMainAgentMessage(
          sessionId,
          parts => upsertToolPart(parts, payload, 'complete'),
          () => upsertToolPart([], payload, 'complete')
        )
      } else if (event.type === 'message.complete') {
        completeMainAgentMessage(sessionId, coerceGatewayText(payload?.text) || coerceGatewayText(payload?.rendered))
      } else if (event.type === 'clarify.request') {
        const requestId = typeof payload?.request_id === 'string' ? payload.request_id : ''
        const question = typeof payload?.question === 'string' ? payload.question : ''

        if (requestId && question) {
          setClarifyRequest({
            requestId,
            question,
            choices: Array.isArray(payload?.choices) ? payload.choices.filter(choice => typeof choice === 'string') : null,
            sessionId
          })
        }
      } else if (event.type === 'approval.request') {
        setApprovalRequest({
          command: typeof payload?.command === 'string' ? payload.command : '',
          description: typeof payload?.description === 'string' ? payload.description : 'dangerous command',
          sessionId
        })
      } else if (event.type === 'sudo.request') {
        const requestId = typeof payload?.request_id === 'string' ? payload.request_id : ''

        if (requestId) {
          setSudoRequest({ requestId, sessionId })
        }
      } else if (event.type === 'secret.request') {
        const requestId = typeof payload?.request_id === 'string' ? payload.request_id : ''

        if (requestId) {
          setSecretRequest({
            requestId,
            envVar: typeof payload?.env_var === 'string' ? payload.env_var : '',
            prompt: typeof payload?.prompt === 'string' ? payload.prompt : '',
            sessionId
          })
        }
      } else if (event.type === 'error') {
        failMainAgentMessage(sessionId, payload?.message || 'QiQiClaw reported an error')
      }
    },
    [
      completeMainAgentMessage,
      failMainAgentMessage,
      flushQueuedDeltas,
      mutateMainAgentMessage,
      queueDelta
    ]
  )

  useEffect(() => {
    if (!gateway) {
      return undefined
    }

    return gateway.onEvent(handleMainAgentEvent)
  }, [gateway, handleMainAgentEvent])

  const submitMainAgentMessage = useCallback(
    async (roomId: string, content: string, attachments: ComposerAttachment[]) => {
      if (!gateway) {
        notify({
          kind: 'error',
          title: 'Gateway unavailable',
          message: 'QiQiClaw gateway is not connected.'
        })

        return false
      }

      let sessionId = mainAgentSessionByRoomRef.current.get(roomId)

      if (!sessionId) {
        const created = await gateway.request<SessionCreateResponse>('session.create', {
          cols: 96,
          source: 'group-chat-main',
          ...(currentCwd.trim() && { cwd: currentCwd.trim() })
        })

        sessionId = created.session_id
        mainAgentSessionByRoomRef.current.set(roomId, sessionId)
        roomByMainAgentSessionRef.current.set(sessionId, roomId)
      }

      setActiveSessionId(sessionId)
      const promptText = visiblePromptText(content, attachments)
      const images = attachments.filter(attachment => attachment.kind === 'image' && attachment.path)

      for (const image of images) {
        await gateway.request('image.attach', {
          session_id: sessionId,
          path: image.path
        })
      }

      await gateway.request('prompt.submit', { session_id: sessionId, text: promptText || content })
      clearComposerAttachments()

      return true
    },
    [currentCwd, gateway]
  )

  const createRoom = useCallback(async () => {
    setBusy(true)

    try {
      const created = await createGroupRoom({ title: '新建群聊', objective: '' })

      setActiveRoomId(created.room.id)
      await queryClient.invalidateQueries({ queryKey: ['group-chat', 'rooms'] })
      queryClient.setQueryData(['group-chat', 'room', created.room.id], created)
      await refreshGroupSessions()

      return created
    } finally {
      setBusy(false)
    }
  }, [queryClient, refreshGroupSessions])

  useEffect(() => {
    if (decodedRouteRoomId) {
      setActiveRoomId(decodedRouteRoomId)
    }
  }, [decodedRouteRoomId])

  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const newKey = params.get('new')

    if (!newKey || handledNewKeyRef.current === newKey) {
      return
    }

    handledNewKeyRef.current = newKey
    void createRoom()
  }, [createRoom, location.search])

  useEffect(() => {
    if (roomsQuery.isLoading || roomsQuery.isFetching || selectedRoomId || handledNewKeyRef.current) {
      return
    }

    void createRoom()
  }, [createRoom, roomsQuery.isFetching, roomsQuery.isLoading, selectedRoomId])

  const submitMessage = useCallback(
    async (value: string, options?: { attachments?: ComposerAttachment[] }) => {
      const content = value.trim()

      if (!content || !selectedRoomId) {
        return false
      }

      const composerAttachments = options?.attachments ?? []
      const attachments = composerAttachments.map(item => ({ ...item })) as Array<Record<string, unknown>>
      const optimisticMessage = optimisticUserMessage(selectedRoomId, content, attachments)
      const explicitGroupAgentMention = hasOnlineAgentMention(content, enabledAgents)

      setLocalMessages(prev => [...prev, optimisticMessage])
      setBusy(true)

      try {
        const result = await sendGroupMessage(selectedRoomId, {
          content,
          attachments
        })

        setLocalMessages(prev => prev.filter(message => message.id !== optimisticMessage.id))
        mergeRoomBundleMessages([result.message, ...(result.replies ?? [])])
        void refreshRoom()
        await refreshGroupSessions()

        if (explicitGroupAgentMention) {
          revealAgentMessages(result.replies ?? [])
          setBusy(false)

          return true
        }

        await submitMainAgentMessage(selectedRoomId, content, composerAttachments)

        return true
      } catch (error) {
        setLocalMessages(prev => prev.filter(message => message.id !== optimisticMessage.id))
        const message = inlineErrorMessage(error, '群聊消息发送失败')
        notifyError(error, '群聊消息发送失败')
        setMainAgentMessages(prev => ({
          ...prev,
          [selectedRoomId]: [
            ...(prev[selectedRoomId] ?? []),
            {
              id: `main-agent-submit-error-${Date.now()}`,
              role: 'assistant',
              parts: [],
              error: message,
              pending: false
            }
          ]
        }))
        setBusy(false)

        return false
      }
    },
    [
      enabledAgents,
      mergeRoomBundleMessages,
      refreshGroupSessions,
      refreshRoom,
      revealAgentMessages,
      selectedRoomId,
      submitMainAgentMessage
    ]
  )

  return (
    <section
      className={cn('relative isolate flex h-full min-h-0 min-w-0 flex-col bg-(--ui-chat-surface-background)', className)}
      {...props}
    >
      <Backdrop />
      <header className={cn(titlebarHeaderBaseClass, titlebarHeaderShadowClass, 'justify-start gap-2 pr-28')}>
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Popover onOpenChange={setAgentsOpen} open={agentsOpen}>
            <PopoverTrigger asChild>
              <button
                className="pointer-events-auto inline-flex h-6 min-w-6 items-center justify-center gap-1 rounded-[4px] px-1.5 text-xs font-medium text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary) data-[state=open]:bg-(--chrome-action-hover) data-[state=open]:text-(--ui-text-primary) [-webkit-app-region:no-drag]"
                title="智能体成员"
                type="button"
              >
                <span>{enabledAgents.length}</span>
                <span
                  className={cn(
                    'size-1.5 rounded-full',
                    waitingModelCount ? 'bg-amber-500' : enabledAgents.length ? 'bg-emerald-500' : 'bg-(--ui-text-tertiary)'
                  )}
                />
              </button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-80 p-0" sideOffset={8}>
              <div className="flex items-center justify-between border-b border-(--ui-stroke-secondary) px-3 py-2">
                <div>
                  <div className="text-xs font-semibold">智能体</div>
                  <div className="mt-0.5 text-[0.6875rem] text-(--ui-text-tertiary)">
                    {enabledAgents.length}/{agents.length} 运行中
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <Button disabled={busy} onClick={() => void refreshRoom()} size="icon-xs" title={t.common.refresh} variant="ghost">
                    <RefreshCw />
                  </Button>
                  <Button
                    disabled={!selectedRoomId}
                    onClick={() => {
                      setEditingAgent(null)
                      setAgentDialogOpen(true)
                    }}
                    size="icon-xs"
                    title="添加智能体"
                    variant="ghost"
                  >
                    <Plus />
                  </Button>
                </div>
              </div>
              <div className="max-h-[22rem] overflow-y-auto p-2">
                {agents.length === 0 ? (
                  <div className="px-2 py-6 text-center text-xs text-(--ui-text-tertiary)">暂无智能体</div>
                ) : (
                  <div className="grid gap-2">
                    {agents.map(agent => (
                      <AgentRow
                        agent={agent}
                        key={agent.id}
                        onDelete={() => {
                          if (!selectedRoomId) {
                            return
                          }

                          void deleteGroupAgent(selectedRoomId, agent.id).then(() => refreshRoom())
                        }}
                        onEdit={() => {
                          setEditingAgent(agent)
                          setAgentDialogOpen(true)
                        }}
                        onToggle={enabled => {
                          if (!selectedRoomId) {
                            return
                          }

                          void updateGroupAgent(selectedRoomId, agent.id, { enabled }).then(() => refreshRoom())
                        }}
                      />
                    ))}
                  </div>
                )}
              </div>
            </PopoverContent>
          </Popover>

          <Button
            className="pointer-events-auto [-webkit-app-region:no-drag]"
            disabled={!selectedRoomId}
            onClick={() => {
              setEditingAgent(null)
              setAgentDialogOpen(true)
            }}
            size="icon-xs"
            title="添加智能体"
            variant="ghost"
          >
            <Plus />
          </Button>
        </div>
      </header>

      <main className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <PromptOverlays />
        {selectedRoomId && (
          <AssistantRuntimeProvider runtime={runtime}>
            <Thread
              clampToComposer
              cwd={null}
              gateway={gateway}
              intro={showIntro ? { personality: introPersonality, seed: introSeed } : undefined}
              loading={roomQuery.isLoading ? 'session' : undefined}
              onCancel={onCancel}
              sessionId={selectedRoomId}
              sessionKey={selectedRoomId}
            />
            <Suspense fallback={<ChatBarFallback />}>
              <ChatBar
                busy={busy}
                cwd={null}
                disabled={!gateway}
                extraAtMentions={[
                  ...enabledAgents.map(agent => ({
                    text: `@${agent.name}`,
                    label: `@${agent.name}`,
                    description: ROLE_LABELS[agent.role_type] ?? agent.description,
                    type: 'group-agent',
                    plainText: true
                  }))
                ]}
                focusKey={selectedRoomId}
                gateway={gateway}
                maxRecordingSeconds={maxVoiceRecordingSeconds}
                onAddUrl={onAddUrl}
                onAttachDroppedItems={onAttachDroppedItems}
                onAttachImageBlob={onAttachImageBlob}
                onCancel={onCancel}
                onPasteClipboardImage={onPasteClipboardImage}
                onPickFiles={onPickFiles}
                onPickFolders={onPickFolders}
                onPickImages={onPickImages}
                onRemoveAttachment={onRemoveAttachment}
                onSubmit={submitMessage}
                onTranscribeAudio={onTranscribeAudio}
                queueSessionKey={selectedRoomId}
                sessionId={selectedRoomId}
                state={chatBarState}
              />
            </Suspense>
          </AssistantRuntimeProvider>
        )}
      </main>

      <AgentDialog
        agent={editingAgent}
        models={savedModels}
        onOpenChange={setAgentDialogOpen}
        onSave={async values => {
          if (!selectedRoomId) {
            return
          }

          if (editingAgent) {
            await updateGroupAgent(selectedRoomId, editingAgent.id, values)
          } else {
            await createGroupAgent(selectedRoomId, values)
          }

          setAgentDialogOpen(false)
          setEditingAgent(null)

          await refreshRoom()
        }}
        open={agentDialogOpen}
      />
    </section>
  )
}

function AgentRow({
  agent,
  onDelete,
  onEdit,
  onToggle
}: {
  agent: GroupAgentRole
  onDelete: () => void
  onEdit: () => void
  onToggle: (enabled: boolean) => void
}) {
  return (
    <div className="rounded-md border border-(--ui-stroke-secondary) bg-(--ui-chat-bubble-background) p-2.5">
      <div className="flex items-start gap-2">
        <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-(--ui-bg-quaternary) text-[0.6875rem] font-semibold text-(--ui-text-secondary)">
          {agentInitial(agent.name)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <div className="truncate text-xs font-semibold">{agent.name}</div>
            <span
              className={cn(
                'shrink-0 rounded px-1.5 py-0.5 text-[0.625rem]',
                agent.model_status === 'missing'
                  ? 'bg-destructive/10 text-destructive'
                  : 'bg-(--ui-bg-quaternary) text-(--ui-text-tertiary)'
              )}
            >
              {agent.model_status === 'missing' ? '待选模型' : '模型库'}
            </span>
          </div>
          <div className="mt-1 text-[0.6875rem] text-(--ui-text-tertiary)">
            {ROLE_LABELS[agent.role_type]} · 只读建议
          </div>
        </div>
        <Switch checked={agent.enabled} onCheckedChange={onToggle} size="xs" />
      </div>
      <p className="mt-2 line-clamp-2 text-xs leading-5 text-(--ui-text-secondary)">{agent.description}</p>
      <div className="mt-2 flex items-center gap-1 text-[0.6875rem] text-(--ui-text-tertiary)">
        <Cpu className="size-3" />
        <span className="truncate">{modelLabel(agent)}</span>
      </div>
      <div className="mt-2 flex justify-end gap-2">
        <Button onClick={onEdit} size="xs" variant="outline">
          查看/配置
        </Button>
        <Button onClick={onDelete} size="icon-xs" variant="ghost">
          <Trash2 />
        </Button>
      </div>
    </div>
  )
}

function AgentDialog({
  agent,
  models,
  onOpenChange,
  onSave,
  open
}: {
  agent: GroupAgentRole | null
  models: SavedModel[]
  onOpenChange: (open: boolean) => void
  onSave: (values: {
    base_url?: null | string
    can_spawn_validation_subagents?: boolean
    credential_index?: null | number
    description?: string
    model?: null | string
    name: string
    participates_in_consensus?: boolean
    provider?: null | string
    receives_all?: boolean
    role_type?: GroupAgentRoleType
    saved_model_id?: null | string
  }) => Promise<void>
  open: boolean
}) {
  const [name, setName] = useState(agent?.name ?? '')
  const [description, setDescription] = useState(agent?.description ?? '')
  const [roleType, setRoleType] = useState<GroupAgentRoleType>(agent?.role_type ?? 'custom')
  const [savedModelId, setSavedModelId] = useState(agent?.saved_model_id ?? '')
  const [canSpawn, setCanSpawn] = useState(Boolean(agent?.can_spawn_validation_subagents))
  const [receivesAll, setReceivesAll] = useState(agent?.receives_all ?? true)

  const selectedModel = models.find(model => model.id === savedModelId)

  useEffect(() => {
    if (!open) {
      return
    }

    setName(agent?.name ?? '')
    setDescription(agent?.description ?? '')
    setRoleType(agent?.role_type ?? 'custom')
    setSavedModelId(agent?.saved_model_id ?? '')
    setCanSpawn(Boolean(agent?.can_spawn_validation_subagents))
    setReceivesAll(agent?.receives_all ?? true)
  }, [agent, open])

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{agent ? '配置智能体' : '添加智能体'}</DialogTitle>
          <DialogDescription>
            模型必须来自模型库。智能体可读取文件、监视主 agent 并提出建议，是否采纳由用户判断。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <label className="grid gap-1.5 text-xs">
            <span className="font-medium">名称</span>
            <Input onChange={event => setName(event.target.value)} value={name} />
          </label>
          <label className="grid gap-1.5 text-xs">
            <span className="font-medium">描述</span>
            <Textarea onChange={event => setDescription(event.target.value)} value={description} />
          </label>
          <label className="grid gap-1.5 text-xs">
            <span className="font-medium">角色类型</span>
            <select
              className="h-8 rounded border border-(--ui-stroke-secondary) bg-background px-2 text-xs"
              onChange={event => setRoleType(event.target.value as GroupAgentRoleType)}
              value={roleType}
            >
              {ROLE_OPTIONS.map(role => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1.5 text-xs">
            <span className="font-medium">模型库模型</span>
            <select
              className="h-8 rounded border border-(--ui-stroke-secondary) bg-background px-2 text-xs"
              onChange={event => setSavedModelId(event.target.value)}
              value={savedModelId}
            >
              <option value="">请选择模型</option>
              {models.map(model => (
                <option key={model.id} value={model.id}>
                  {model.name || model.model} · {model.provider}/{model.model}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center justify-between gap-3 text-xs">
            <span>
              <span className="block font-medium">允许驱动子代理验证</span>
              <span className="text-(--ui-text-tertiary)">用于只读文件查看与交叉验证，不直接执行真实改动。</span>
            </span>
            <Switch checked={canSpawn} onCheckedChange={setCanSpawn} />
          </label>
          <div className="flex items-center gap-2 rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) px-3 py-2 text-xs text-(--ui-text-tertiary)">
            <Lock className="size-3.5" />
            <span>写文件、发布和推送权限保持关闭；更改建议由用户判断后交给主 agent。</span>
          </div>
        </div>

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="outline">
            取消
          </Button>
          <Button
            disabled={!name.trim()}
            onClick={() =>
              void onSave({
                name: name.trim(),
                description: description.trim(),
                role_type: roleType,
                saved_model_id: selectedModel?.id ?? null,
                provider: selectedModel?.provider ?? null,
                model: selectedModel?.model ?? null,
                credential_index: selectedModel?.credential_index ?? null,
                base_url: selectedModel?.base_url ?? null,
                can_spawn_validation_subagents: canSpawn,
                participates_in_consensus: true,
                receives_all: receivesAll
              })
            }
          >
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
