import api, { mediaUrl } from '../services/api'

export type Platform = 'tiktok' | 'instagram' | 'youtube'
export type Target = 'tiktok' | 'instagram' | 'youtube_shorts' | 'youtube'

export const TARGET_LABEL: Record<Target, string> = {
  tiktok: 'TikTok',
  instagram: 'Instagram Reels',
  youtube_shorts: 'YouTube Shorts',
  youtube: 'YouTube',
}
export const TARGET_PLATFORM: Record<Target, Platform> = {
  tiktok: 'tiktok', instagram: 'instagram', youtube_shorts: 'youtube', youtube: 'youtube',
}
export const PLATFORM_LABEL: Record<Platform, string> = { tiktok: 'TikTok', instagram: 'Instagram', youtube: 'YouTube' }

export interface PlatformStatus {
  platform: Platform
  configured: boolean
  missing_env: string[]
  redirect_uri: string
  login_mode?: string
  /** where the developer-app credentials come from: server .env (wins) or entered in the UI */
  source?: 'env' | 'db' | null
  env_locked?: boolean
  client_id_masked?: string
  secret_set?: boolean
  env_names?: string[]
  pkce?: boolean
  console_url?: string
  docs_url?: string
  setup_guide_url?: string
  id_label?: string
  secret_label?: string
}
export interface SocialStatus {
  platforms: PlatformStatus[]
  public_url: string
  token_encryption: boolean
  notes: { tiktok_audited: boolean; google_verified: boolean }
}
export interface SocialAccount {
  id: string
  platform: Platform
  display_name?: string
  username?: string
  avatar_url?: string
  status: string
  last_error?: string
  access_expires_at?: string
  external_id?: string
  scopes?: string[]
  created_at?: string
  metadata?: Record<string, unknown>
}
export interface Caption {
  target?: Target
  title?: string
  caption?: string
  description?: string
  hashtags?: string[]
  tags?: string[]
  chapters?: { start: number; title: string }[]
  primary_keyword?: string
  keywords?: string[]
}
export interface SeoCheck { id: string; ok: boolean; detail: string; weight: number }
export interface SeoReport { score: number; checks: SeoCheck[]; source?: string; model?: string }
export interface SeoResult {
  source: 'llm' | 'heuristic'
  model?: string
  language: string
  error?: string
  targets: Record<string, { caption: Caption; report: SeoReport }>
  clip: { id: string; title: string; duration_sec: number; transcript_cues: number }
}
export interface PublishJob {
  id: string
  project_id: string
  clip_id: string
  account_id: string
  target: Target
  status: string
  source: string
  scheduled_at?: string
  next_attempt_at?: string
  attempts: number
  max_attempts: number
  caption: Caption
  seo_report: SeoReport
  options: Record<string, unknown>
  remote_id?: string
  remote_url?: string
  error?: string
  history: { at: string; event: string; [k: string]: unknown }[]
  published_at?: string
  created_at: string
}
export interface ClipRow {
  id: string
  project_id: string
  project_name?: string
  title: string
  duration: number
  score?: number
  status: string
  created_at: string
}
export interface TikTokCreatorInfo {
  can_post: boolean
  error_code?: string | null
  error_message?: string | null
  audited: boolean
  creator_nickname?: string
  creator_username?: string
  creator_avatar_url?: string
  privacy_level_options: string[]
  comment_disabled: boolean
  duet_disabled: boolean
  stitch_disabled: boolean
  max_video_post_duration_sec?: number
}
export interface TikTokPostOptions {
  privacy_level?: string
  disable_comment: boolean
  disable_duet: boolean
  disable_stitch: boolean
  commercial_disclosure: boolean
  brand_organic_toggle: boolean
  brand_content_toggle: boolean
  tiktok_consent: boolean
}
export interface PublisherSettings {
  auto_publish: boolean
  require_approval: boolean
  targets: { account_id: string; target: Target }[]
  min_score: number
  spacing_minutes: number
  max_per_day: number
  enabled_since?: string | null
  language: string
  brand_hashtags: string[]
  burn_subtitles: boolean
  defaults: Record<Target, Record<string, unknown>>
}

export const socialApi = {
  status: (): Promise<SocialStatus> => api.get('/social/status'),
  accounts: (): Promise<{ accounts: SocialAccount[] }> => api.get('/social/accounts'),
  disconnect: (id: string): Promise<unknown> => api.delete(`/social/accounts/${id}`),
  connectUrl: (platform: Platform): string => mediaUrl(`/social/oauth/${platform}/start`),
  apps: (): Promise<{ apps: PlatformStatus[]; token_encryption: boolean; public_url: string }> => api.get('/social/apps'),
  saveApp: (platform: Platform, body: { client_id: string; client_secret?: string }): Promise<PlatformStatus> =>
    api.put(`/social/apps/${platform}`, body),
  deleteApp: (platform: Platform): Promise<PlatformStatus & { removed: boolean }> => api.delete(`/social/apps/${platform}`),
  settings: (): Promise<PublisherSettings> => api.get('/social/settings'),
  saveSettings: (patch: Partial<PublisherSettings>): Promise<PublisherSettings> => api.put('/social/settings', patch),
  clips: (projectId?: string): Promise<{ clips: ClipRow[] }> =>
    api.get('/social/clips', { params: projectId ? { project_id: projectId } : {} }),
  generate: (clipId: string, targets: Target[]): Promise<SeoResult> =>
    api.post('/social/seo/generate', { clip_id: clipId, targets }, { timeout: 180000 }),
  creatorInfo: (accountId: string): Promise<TikTokCreatorInfo> =>
    api.get(`/social/accounts/${accountId}/creator-info`),
  createJobs: (body: {
    clip_id: string
    targets: { account_id: string; target: Target }[]
    captions?: Record<string, Caption>
    options?: Record<string, Record<string, unknown>>
    scheduled_at?: string
    draft?: boolean
  }): Promise<{ jobs: PublishJob[] }> => api.post('/social/jobs', body, { timeout: 180000 }),
  jobs: (status?: string): Promise<{ jobs: PublishJob[] }> => api.get('/social/jobs', { params: status ? { status } : {} }),
  updateJob: (id: string, patch: Record<string, unknown>): Promise<PublishJob> => api.patch(`/social/jobs/${id}`, patch),
  deleteJob: (id: string): Promise<unknown> => api.delete(`/social/jobs/${id}`),
}
