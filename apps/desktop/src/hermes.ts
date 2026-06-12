import { JsonRpcGatewayClient } from '@hermes/shared'

import type {
  ActionResponse,
  ActionStatusResponse,
  AnalyticsResponse,
  AudioSpeakResponse,
  AudioTranscriptionResponse,
  AuxiliaryModelsResponse,
  ConfigSchemaResponse,
  CredentialPoolResponse,
  ModelDiscoveryResponse,
  CronJob,
  CronJobCreatePayload,
  CronJobUpdates,
  ElevenLabsVoicesResponse,
  EnvVarInfo,
  QiQiClawConfig,
  QiQiClawConfigRecord,
  LogsResponse,
  MessagingPlatformsResponse,
  MessagingPlatformTestResponse,
  MessagingPlatformUpdate,
  ModelAssignmentRequest,
  ModelAssignmentResponse,
  ModelInfoResponse,
  ProviderCatalogResponse,
  ModelRouteValidationResponse,
  ModelOptionsResponse,
  OAuthPollResponse,
  OAuthProvidersResponse,
  OAuthStartResponse,
  OAuthSubmitResponse,
  PaginatedSessions,
  ProfileCreatePayload,
  SavedModel,
  ProfileSetupCommand,
  ProfileSoul,
  ProfilesResponse,
  SessionInfo,
  SessionMessagesResponse,
  SessionSearchResponse,
  SkillInfo,
  StatusResponse,
  ToolsetConfig,
  ToolsetInfo
} from '@/types/hermes'

const DEFAULT_GATEWAY_REQUEST_TIMEOUT_MS = 30_000
const SESSION_LIST_REQUEST_TIMEOUT_MS = 60_000

export type {
  ActionResponse,
  ActionStatusResponse,
  AnalyticsDailyEntry,
  AnalyticsModelEntry,
  AnalyticsResponse,
  AnalyticsSkillEntry,
  AnalyticsSkillsSummary,
  AnalyticsTotals,
  AudioSpeakResponse,
  AudioTranscriptionResponse,
  AuxiliaryModelsResponse,
  ConfigFieldSchema,
  ConfigSchemaResponse,
  CredentialPoolEntry,
  CredentialPoolProvider,
  CredentialPoolResponse,
  ModelDiscoveryResponse,
  CronJob,
  CronJobCreatePayload,
  CronJobSchedule,
  CronJobUpdates,
  ElevenLabsVoice,
  ElevenLabsVoicesResponse,
  EnvVarInfo,
  GatewayReadyPayload,
  QiQiClawConfig,
  QiQiClawConfigRecord,
  LogsResponse,
  MessagingEnvVarInfo,
  MessagingHomeChannel,
  MessagingPlatformInfo,
  MessagingPlatformsResponse,
  MessagingPlatformTestResponse,
  MessagingPlatformUpdate,
  ModelAssignmentRequest,
  ModelAssignmentResponse,
  ModelInfoResponse,
  ProviderCatalogEntry,
  ProviderCatalogResponse,
  ModelRouteValidationResponse,
  ModelOptionProvider,
  ModelOptionsResponse,
  PaginatedSessions,
  ProfileCreatePayload,
  ProfileInfo,
  SavedModel,
  ProfileSetupCommand,
  ProfileSoul,
  ProfilesResponse,
  RpcEvent,
  SessionCreateResponse,
  SessionInfo,
  SessionMessage,
  SessionMessagesResponse,
  SessionResumeResponse,
  SessionRuntimeInfo,
  SessionSearchResponse,
  SessionSearchResult,
  SkillInfo,
  StaleAuxAssignment,
  StatusResponse,
  ToolsetConfig,
  ToolsetInfo
} from '@/types/hermes'

export class QiQiClawGateway extends JsonRpcGatewayClient {
  constructor() {
    super({
      closedErrorMessage: 'QiQiClaw gateway connection closed',
      connectErrorMessage: 'Could not connect to QiQiClaw gateway',
      createRequestId: nextId => nextId,
      notConnectedErrorMessage: 'QiQiClaw gateway is not connected',
      requestTimeoutMs: DEFAULT_GATEWAY_REQUEST_TIMEOUT_MS
    })
  }
}

// Profile that profile-scoped REST settings (config/env/skills/tools/model/…)
// should target. Mirrors $activeGatewayProfile, pushed in from the store via
// setApiRequestProfile so this module needs no store import (avoids a cycle).
// Electron main consumes request.profile to pick which backend *process* serves
// the call; each pooled backend already has its own HERMES_HOME, so no backend
// change is needed. Null → primary, so single-profile users are unaffected.
let _apiProfile: null | string = null

export function setApiRequestProfile(profile: null | string): void {
  _apiProfile = profile || null
}

function profileScoped(): { profile?: string } {
  return _apiProfile ? { profile: _apiProfile } : {}
}

export async function listSessions(
  limit = 40,
  minMessages = 0,
  archived: 'exclude' | 'include' | 'only' = 'exclude',
  order: 'created' | 'recent' = 'recent'
): Promise<PaginatedSessions> {
  const result = await window.hermesDesktop.api<PaginatedSessions>({
    path: `/api/sessions?limit=${limit}&offset=0&min_messages=${Math.max(0, minMessages)}&archived=${archived}&order=${order}`,
    timeoutMs: SESSION_LIST_REQUEST_TIMEOUT_MS
  })

  return {
    ...result,
    sessions: result.sessions.slice(0, limit),
    offset: 0
  }
}

// Unified, read-only session list aggregated across ALL profiles. Served by the
// primary backend straight off each profile's state.db — no per-profile backend
// is spawned. Single-profile users get the same rows as listSessions(), tagged
// profile="default".
// Source scoping lets callers split the unified list into independent slices:
// recents pass `excludeSources: ['cron']`, the cron-jobs section passes
// `source: 'cron'`. Without this a burst of (always-newest) cron sessions
// consumes the whole recents page and starves real conversations.
export interface SessionSourceFilter {
  source?: string
  excludeSources?: string[]
}

export async function listAllProfileSessions(
  limit = 40,
  minMessages = 0,
  archived: 'exclude' | 'include' | 'only' = 'exclude',
  order: 'created' | 'recent' = 'recent',
  profile: 'all' | (string & {}) = 'all',
  filter: SessionSourceFilter = {}
): Promise<PaginatedSessions> {
  const sourceParam = filter.source ? `&source=${encodeURIComponent(filter.source)}` : ''

  const excludeParam = filter.excludeSources?.length
    ? `&exclude_sources=${encodeURIComponent(filter.excludeSources.join(','))}`
    : ''

  const result = await window.hermesDesktop.api<PaginatedSessions>({
    path:
      `/api/profiles/sessions?limit=${limit}&offset=0&min_messages=${Math.max(0, minMessages)}` +
      `&archived=${archived}&order=${order}&profile=${encodeURIComponent(profile)}${sourceParam}${excludeParam}`,
    timeoutMs: SESSION_LIST_REQUEST_TIMEOUT_MS
  })

  return {
    ...result,
    sessions: result.sessions.slice(0, limit),
    offset: 0
  }
}

// Mutations take the owning `profile` so Electron routes them to that profile's
// backend (remote pool or local primary) via request.profile — matching the
// read path. A remote session's row lives only on its remote host, so a mutation
// that hit the local primary would no-op or 404. Omit for the current/default.
export function setSessionArchived(id: string, archived: boolean, profile?: string | null): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    ...(profile ? { profile } : {}),
    path: `/api/sessions/${encodeURIComponent(id)}`,
    method: 'PATCH',
    body: { archived }
  })
}

export function searchSessions(query: string): Promise<SessionSearchResponse> {
  return window.hermesDesktop.api<SessionSearchResponse>({
    path: `/api/sessions/search?q=${encodeURIComponent(query)}`
  })
}

// Reads another profile's transcript. For a remote profile Electron reroutes
// this GET to the remote backend (which serves its own state.db); for a local
// profile the primary opens that profile's state.db via ?profile=. Omit for
// the current/default profile.
export function getSessionMessages(id: string, profile?: string | null): Promise<SessionMessagesResponse> {
  const suffix = profile ? `?profile=${encodeURIComponent(profile)}` : ''

  return window.hermesDesktop.api<SessionMessagesResponse>({
    path: `/api/sessions/${encodeURIComponent(id)}/messages${suffix}`
  })
}

export function deleteSession(id: string, profile?: string | null): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    ...(profile ? { profile } : {}),
    path: `/api/sessions/${encodeURIComponent(id)}`,
    method: 'DELETE'
  })
}

export function renameSession(
  id: string,
  title: string,
  profile?: string | null
): Promise<{ ok: boolean; title: string }> {
  return window.hermesDesktop.api<{ ok: boolean; title: string }>({
    ...(profile ? { profile } : {}),
    path: `/api/sessions/${encodeURIComponent(id)}`,
    method: 'PATCH',
    body: { title, ...(profile ? { profile } : {}) }
  })
}

export function getGlobalModelInfo(): Promise<ModelInfoResponse> {
  return window.hermesDesktop.api<ModelInfoResponse>({
    ...profileScoped(),
    path: '/api/model/info'
  })
}

export function getStatus(): Promise<StatusResponse> {
  return window.hermesDesktop.api<StatusResponse>({
    path: '/api/status'
  })
}

export function getLogs(params: {
  component?: string
  file?: string
  level?: string
  lines?: number
}): Promise<LogsResponse> {
  const query = new URLSearchParams()

  if (params.file) {
    query.set('file', params.file)
  }

  if (typeof params.lines === 'number') {
    query.set('lines', String(params.lines))
  }

  if (params.level && params.level !== 'ALL') {
    query.set('level', params.level)
  }

  if (params.component && params.component !== 'all') {
    query.set('component', params.component)
  }

  const suffix = query.toString()

  return window.hermesDesktop.api<LogsResponse>({
    ...profileScoped(),
    path: suffix ? `/api/logs?${suffix}` : '/api/logs'
  })
}

export function getQiQiClawConfig(): Promise<QiQiClawConfig> {
  return window.hermesDesktop.api<QiQiClawConfig>({
    ...profileScoped(),
    path: '/api/config'
  })
}

export function getQiQiClawConfigRecord(): Promise<QiQiClawConfigRecord> {
  return window.hermesDesktop.api<QiQiClawConfigRecord>({
    ...profileScoped(),
    path: '/api/config'
  })
}

export function getQiQiClawConfigDefaults(): Promise<QiQiClawConfigRecord> {
  return window.hermesDesktop.api<QiQiClawConfigRecord>({
    ...profileScoped(),
    path: '/api/config/defaults'
  })
}

export function getQiQiClawConfigSchema(): Promise<ConfigSchemaResponse> {
  return window.hermesDesktop.api<ConfigSchemaResponse>({
    ...profileScoped(),
    path: '/api/config/schema'
  })
}

export function saveQiQiClawConfig(config: QiQiClawConfigRecord): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    ...profileScoped(),
    path: '/api/config',
    method: 'PUT',
    body: { config }
  })
}

export function getEnvVars(): Promise<Record<string, EnvVarInfo>> {
  return window.hermesDesktop.api<Record<string, EnvVarInfo>>({
    ...profileScoped(),
    path: '/api/env'
  })
}

export function setEnvVar(key: string, value: string): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    ...profileScoped(),
    path: '/api/env',
    method: 'PUT',
    body: { key, value }
  })
}

export function validateProviderCredential(
  key: string,
  value: string
): Promise<{ ok: boolean; reachable: boolean; message: string; models?: string[] }> {
  return window.hermesDesktop.api<{ ok: boolean; reachable: boolean; message: string; models?: string[] }>({
    ...profileScoped(),
    path: '/api/providers/validate',
    method: 'POST',
    body: { key, value }
  })
}

export function deleteEnvVar(key: string): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    ...profileScoped(),
    path: '/api/env',
    method: 'DELETE',
    body: { key }
  })
}

export function revealEnvVar(key: string): Promise<{ key: string; value: string }> {
  return window.hermesDesktop.api<{ key: string; value: string }>({
    ...profileScoped(),
    path: '/api/env/reveal',
    method: 'POST',
    body: { key }
  })
}

export function getCredentialPool(): Promise<CredentialPoolResponse> {
  return window.hermesDesktop.api<CredentialPoolResponse>({
    ...profileScoped(),
    path: '/api/credentials/pool'
  })
}

export function getProviderCatalog(): Promise<ProviderCatalogResponse> {
  return window.hermesDesktop.api<ProviderCatalogResponse>({
    ...profileScoped(),
    path: '/api/providers/catalog'
  })
}

export function discoverProviderModels(body: {
  base_url?: string
  credential_index?: number
  provider: string
}): Promise<ModelDiscoveryResponse> {
  return window.hermesDesktop.api<ModelDiscoveryResponse>({
    ...profileScoped(),
    path: '/api/models/discover',
    method: 'POST',
    body
  })
}

export function addCredentialPoolEntry(body: {
  api_key: string
  base_url?: string
  label?: string
  provider: string
}): Promise<{ count: number; ok: boolean; provider: string }> {
  return window.hermesDesktop.api<{ count: number; ok: boolean; provider: string }>({
    ...profileScoped(),
    path: '/api/credentials/pool',
    method: 'POST',
    body
  })
}

export function removeCredentialPoolEntry(
  provider: string,
  index: number
): Promise<{ count: number; ok: boolean; provider: string }> {
  return window.hermesDesktop.api<{ count: number; ok: boolean; provider: string }>({
    ...profileScoped(),
    path: `/api/credentials/pool/${encodeURIComponent(provider)}/${encodeURIComponent(String(index))}`,
    method: 'DELETE'
  })
}

export function validateSavedModel(
  id: string,
  body: { credential_index?: number } = {}
): Promise<{
  checked?: Array<{ index: number; message: string; ok: boolean }>
  credential_index?: number
  message: string
  model: string
  ok: boolean
  provider: string
}> {
  return window.hermesDesktop.api<{
    checked?: Array<{ index: number; message: string; ok: boolean }>
    credential_index?: number
    message: string
    model: string
    ok: boolean
    provider: string
  }>({
    ...profileScoped(),
    path: `/api/models/library/${encodeURIComponent(id)}/validate`,
    method: 'POST',
    body
  })
}

export function validateModelRoute(body: {
  base_url?: string
  credential_index?: number
  model: string
  name?: string
  provider: string
}): Promise<ModelRouteValidationResponse> {
  return window.hermesDesktop.api<ModelRouteValidationResponse>({
    ...profileScoped(),
    path: '/api/models/route/validate',
    method: 'POST',
    body
  })
}

export function listOAuthProviders(): Promise<OAuthProvidersResponse> {
  return window.hermesDesktop.api<OAuthProvidersResponse>({
    ...profileScoped(),
    path: '/api/providers/oauth'
  })
}

export function startOAuthLogin(providerId: string): Promise<OAuthStartResponse> {
  return window.hermesDesktop.api<OAuthStartResponse>({
    ...profileScoped(),
    path: `/api/providers/oauth/${encodeURIComponent(providerId)}/start`,
    method: 'POST',
    body: {}
  })
}

export function submitOAuthCode(providerId: string, sessionId: string, code: string): Promise<OAuthSubmitResponse> {
  return window.hermesDesktop.api<OAuthSubmitResponse>({
    ...profileScoped(),
    path: `/api/providers/oauth/${encodeURIComponent(providerId)}/submit`,
    method: 'POST',
    body: { session_id: sessionId, code }
  })
}

export function pollOAuthSession(providerId: string, sessionId: string): Promise<OAuthPollResponse> {
  return window.hermesDesktop.api<OAuthPollResponse>({
    ...profileScoped(),
    path: `/api/providers/oauth/${encodeURIComponent(providerId)}/poll/${encodeURIComponent(sessionId)}`
  })
}

export function cancelOAuthSession(sessionId: string): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    ...profileScoped(),
    path: `/api/providers/oauth/sessions/${encodeURIComponent(sessionId)}`,
    method: 'DELETE'
  })
}

export function getSkills(): Promise<SkillInfo[]> {
  return window.hermesDesktop.api<SkillInfo[]>({
    ...profileScoped(),
    path: '/api/skills'
  })
}

export function toggleSkill(name: string, enabled: boolean): Promise<{ ok: boolean; name: string; enabled: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean; name: string; enabled: boolean }>({
    ...profileScoped(),
    path: '/api/skills/toggle',
    method: 'PUT',
    body: { name, enabled }
  })
}

export function getToolsets(): Promise<ToolsetInfo[]> {
  return window.hermesDesktop.api<ToolsetInfo[]>({
    ...profileScoped(),
    path: '/api/tools/toolsets'
  })
}

export function toggleToolset(
  name: string,
  enabled: boolean
): Promise<{ ok: boolean; name: string; enabled: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean; name: string; enabled: boolean }>({
    ...profileScoped(),
    path: `/api/tools/toolsets/${encodeURIComponent(name)}`,
    method: 'PUT',
    body: { enabled }
  })
}

export function getToolsetConfig(name: string): Promise<ToolsetConfig> {
  return window.hermesDesktop.api<ToolsetConfig>({
    ...profileScoped(),
    path: `/api/tools/toolsets/${encodeURIComponent(name)}/config`
  })
}

export function selectToolsetProvider(
  name: string,
  provider: string
): Promise<{ ok: boolean; name: string; provider: string }> {
  return window.hermesDesktop.api<{ ok: boolean; name: string; provider: string }>({
    ...profileScoped(),
    path: `/api/tools/toolsets/${encodeURIComponent(name)}/provider`,
    method: 'PUT',
    body: { provider }
  })
}

export function runToolsetPostSetup(name: string, key: string): Promise<ActionResponse & { key: string }> {
  return window.hermesDesktop.api<ActionResponse & { key: string }>({
    ...profileScoped(),
    path: `/api/tools/toolsets/${encodeURIComponent(name)}/post-setup`,
    method: 'POST',
    body: { key }
  })
}

export function getMessagingPlatforms(): Promise<MessagingPlatformsResponse> {
  return window.hermesDesktop.api<MessagingPlatformsResponse>({
    path: '/api/messaging/platforms'
  })
}

export function updateMessagingPlatform(
  platformId: string,
  body: MessagingPlatformUpdate
): Promise<{ ok: boolean; platform: string }> {
  return window.hermesDesktop.api<{ ok: boolean; platform: string }>({
    path: `/api/messaging/platforms/${encodeURIComponent(platformId)}`,
    method: 'PUT',
    body
  })
}

export function testMessagingPlatform(platformId: string): Promise<MessagingPlatformTestResponse> {
  return window.hermesDesktop.api<MessagingPlatformTestResponse>({
    path: `/api/messaging/platforms/${encodeURIComponent(platformId)}/test`,
    method: 'POST'
  })
}

export function getCronJobs(): Promise<CronJob[]> {
  return window.hermesDesktop.api<CronJob[]>({
    path: '/api/cron/jobs'
  })
}

export function getCronJob(jobId: string): Promise<CronJob> {
  return window.hermesDesktop.api<CronJob>({
    path: `/api/cron/jobs/${encodeURIComponent(jobId)}`
  })
}

export async function getCronJobRuns(jobId: string, limit = 20): Promise<SessionInfo[]> {
  const { runs } = await window.hermesDesktop.api<{ runs: SessionInfo[] }>({
    path: `/api/cron/jobs/${encodeURIComponent(jobId)}/runs?limit=${limit}`
  })

  return runs ?? []
}

export function createCronJob(body: CronJobCreatePayload): Promise<CronJob> {
  return window.hermesDesktop.api<CronJob>({
    path: '/api/cron/jobs',
    method: 'POST',
    body
  })
}

export function updateCronJob(jobId: string, updates: CronJobUpdates): Promise<CronJob> {
  return window.hermesDesktop.api<CronJob>({
    path: `/api/cron/jobs/${encodeURIComponent(jobId)}`,
    method: 'PUT',
    body: { updates }
  })
}

export function pauseCronJob(jobId: string): Promise<CronJob> {
  return window.hermesDesktop.api<CronJob>({
    path: `/api/cron/jobs/${encodeURIComponent(jobId)}/pause`,
    method: 'POST'
  })
}

export function resumeCronJob(jobId: string): Promise<CronJob> {
  return window.hermesDesktop.api<CronJob>({
    path: `/api/cron/jobs/${encodeURIComponent(jobId)}/resume`,
    method: 'POST'
  })
}

export function triggerCronJob(jobId: string): Promise<CronJob> {
  return window.hermesDesktop.api<CronJob>({
    path: `/api/cron/jobs/${encodeURIComponent(jobId)}/trigger`,
    method: 'POST'
  })
}

export function deleteCronJob(jobId: string): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    path: `/api/cron/jobs/${encodeURIComponent(jobId)}`,
    method: 'DELETE'
  })
}

export function getProfiles(): Promise<ProfilesResponse> {
  return window.hermesDesktop.api<ProfilesResponse>({
    path: '/api/profiles'
  })
}

export function createProfile(body: ProfileCreatePayload): Promise<{ name: string; ok: boolean; path: string }> {
  return window.hermesDesktop.api<{ name: string; ok: boolean; path: string }>({
    path: '/api/profiles',
    method: 'POST',
    body
  })
}

export function renameProfile(name: string, newName: string): Promise<{ name: string; ok: boolean; path: string }> {
  return window.hermesDesktop.api<{ name: string; ok: boolean; path: string }>({
    path: `/api/profiles/${encodeURIComponent(name)}`,
    method: 'PATCH',
    body: { new_name: newName }
  })
}

export function deleteProfile(name: string): Promise<{ ok: boolean; path: string }> {
  return window.hermesDesktop.api<{ ok: boolean; path: string }>({
    path: `/api/profiles/${encodeURIComponent(name)}`,
    method: 'DELETE'
  })
}

export function getProfileSoul(name: string): Promise<ProfileSoul> {
  return window.hermesDesktop.api<ProfileSoul>({
    path: `/api/profiles/${encodeURIComponent(name)}/soul`
  })
}

export function updateProfileSoul(name: string, content: string): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    path: `/api/profiles/${encodeURIComponent(name)}/soul`,
    method: 'PUT',
    body: { content }
  })
}

export function getProfileSetupCommand(name: string): Promise<ProfileSetupCommand> {
  return window.hermesDesktop.api<ProfileSetupCommand>({
    path: `/api/profiles/${encodeURIComponent(name)}/setup-command`
  })
}

export function getUsageAnalytics(days = 30): Promise<AnalyticsResponse> {
  return window.hermesDesktop.api<AnalyticsResponse>({
    ...profileScoped(),
    path: `/api/analytics/usage?days=${Math.max(1, Math.floor(days))}`
  })
}

export function getGlobalModelOptions(): Promise<ModelOptionsResponse> {
  return window.hermesDesktop.api<ModelOptionsResponse>({
    ...profileScoped(),
    path: '/api/model/options'
  })
}

export function listSavedModels(): Promise<{ models: SavedModel[] }> {
  return window.hermesDesktop.api<{ models: SavedModel[] }>({
    ...profileScoped(),
    path: '/api/models/library'
  })
}

export function addSavedModel(body: {
  api_key?: string
  base_url?: string
  model: string
  name: string
  provider: string
}): Promise<{ deduped: boolean; model: SavedModel; ok: boolean }> {
  return window.hermesDesktop.api<{ deduped: boolean; model: SavedModel; ok: boolean }>({
    ...profileScoped(),
    path: '/api/models/library',
    method: 'POST',
    body
  })
}

export function updateSavedModel(
  id: string,
  body: {
    base_url?: string
    model: string
    name: string
    provider: string
  }
): Promise<{ model: SavedModel; ok: boolean }> {
  return window.hermesDesktop.api<{ model: SavedModel; ok: boolean }>({
    ...profileScoped(),
    path: `/api/models/library/${encodeURIComponent(id)}`,
    method: 'PUT',
    body
  })
}

export function removeSavedModel(id: string): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    ...profileScoped(),
    path: `/api/models/library/${encodeURIComponent(id)}`,
    method: 'DELETE'
  })
}

export interface RecommendedDefaultModel {
  provider: string
  model: string
  /** True/false for Nous (free vs paid tier); null for other providers. */
  free_tier: boolean | null
}

// Recommended default model for a freshly-authenticated provider. Mirrors the
// curation `hermes model` does — for Nous it honors the free/paid tier so a
// free user gets a free model instead of a paid default.
export function getRecommendedDefaultModel(provider: string): Promise<RecommendedDefaultModel> {
  return window.hermesDesktop.api<RecommendedDefaultModel>({
    ...profileScoped(),
    path: `/api/model/recommended-default?provider=${encodeURIComponent(provider)}`
  })
}

export function setGlobalModel(
  provider: string,
  model: string,
  base_url?: string
): Promise<{ ok: boolean; provider: string; model: string }> {
  return window.hermesDesktop.api<{ ok: boolean; provider: string; model: string }>({
    ...profileScoped(),
    path: '/api/model/set',
    method: 'POST',
    body: {
      scope: 'main',
      provider,
      model,
      ...(base_url ? { base_url } : {})
    }
  })
}

export function getAuxiliaryModels(): Promise<AuxiliaryModelsResponse> {
  return window.hermesDesktop.api<AuxiliaryModelsResponse>({
    ...profileScoped(),
    path: '/api/model/auxiliary'
  })
}

export function setModelAssignment(body: ModelAssignmentRequest): Promise<ModelAssignmentResponse> {
  return window.hermesDesktop.api<ModelAssignmentResponse>({
    ...profileScoped(),
    path: '/api/model/set',
    method: 'POST',
    body
  })
}

export function restartGateway(): Promise<ActionResponse> {
  return window.hermesDesktop.api<ActionResponse>({
    path: '/api/gateway/restart',
    method: 'POST'
  })
}

export function updateQiQiClaw(): Promise<ActionResponse> {
  return window.hermesDesktop.api<ActionResponse>({
    path: '/api/qiqiclaw/update',
    method: 'POST'
  })
}

export function getActionStatus(name: string, lines = 200): Promise<ActionStatusResponse> {
  return window.hermesDesktop.api<ActionStatusResponse>({
    path: `/api/actions/${encodeURIComponent(name)}/status?lines=${Math.max(1, lines)}`
  })
}

export function transcribeAudio(dataUrl: string, mimeType?: string): Promise<AudioTranscriptionResponse> {
  return window.hermesDesktop.api<AudioTranscriptionResponse>({
    path: '/api/audio/transcribe',
    method: 'POST',
    body: {
      data_url: dataUrl,
      mime_type: mimeType
    }
  })
}

export function speakText(text: string): Promise<AudioSpeakResponse> {
  return window.hermesDesktop.api<AudioSpeakResponse>({
    path: '/api/audio/speak',
    method: 'POST',
    body: { text }
  })
}

export function getElevenLabsVoices(): Promise<ElevenLabsVoicesResponse> {
  return window.hermesDesktop.api<ElevenLabsVoicesResponse>({
    path: '/api/audio/elevenlabs/voices'
  })
}
