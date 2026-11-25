import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Avatar, Checkbox, Select, Space, Spin, Switch, Typography } from 'antd'
import { projectApi } from '../services/api'
import {
  ClipRow, SocialAccount, TikTokCreatorInfo, TikTokPostOptions, socialApi,
} from './socialApi'
import { errText } from './AccountsAndApps'

const { Text } = Typography

export const MUSIC_USAGE_URL = 'https://www.tiktok.com/legal/page/global/music-usage-confirmation/en'
export const BRANDED_CONTENT_POLICY_URL = 'https://www.tiktok.com/legal/page/global/bc-policy/en'

export const PRIVACY_LABELS: Record<string, string> = {
  PUBLIC_TO_EVERYONE: 'Everyone',
  MUTUAL_FOLLOW_FRIENDS: 'Friends',
  FOLLOWER_OF_CREATOR: 'Followers',
  SELF_ONLY: 'Only me',
}

export const emptyTikTokOptions = (): TikTokPostOptions => ({
  privacy_level: undefined,
  disable_comment: true,
  disable_duet: true,
  disable_stitch: true,
  commercial_disclosure: false,
  brand_organic_toggle: false,
  brand_content_toggle: false,
  tiktok_consent: false,
})

export function tiktokFormBlockReason(
  opts: TikTokPostOptions,
  info?: TikTokCreatorInfo | null,
  durationSec?: number,
): string | undefined {
  if (info && !info.can_post) return info.error_message || 'This TikTok account cannot post right now. Try again later.'
  if (info && info.max_video_post_duration_sec && durationSec && durationSec > info.max_video_post_duration_sec) {
    return `This clip is ${Math.round(durationSec)}s; this TikTok account allows ${info.max_video_post_duration_sec}s max.`
  }
  if (!opts.privacy_level) return 'Select who can watch this post. There is no default privacy setting.'
  if (opts.commercial_disclosure && !opts.brand_organic_toggle && !opts.brand_content_toggle) {
    return 'You need to indicate if your content promotes yourself, a third party, or both.'
  }
  if (opts.brand_content_toggle && opts.privacy_level === 'SELF_ONLY') {
    return 'Branded content visibility cannot be set to private.'
  }
  if (!opts.tiktok_consent) {
    return opts.brand_content_toggle
      ? "Confirm that by posting, you agree to TikTok's Branded Content Policy and Music Usage Confirmation."
      : "Confirm that by posting, you agree to TikTok's Music Usage Confirmation."
  }
  return undefined
}

const ConsentCopy: React.FC<{ branded: boolean }> = ({ branded }) => (
  branded ? (
    <span>
      By posting, you agree to TikTok's{' '}
      <a href={BRANDED_CONTENT_POLICY_URL} target="_blank" rel="noreferrer">Branded Content Policy</a>
      {' '}and{' '}
      <a href={MUSIC_USAGE_URL} target="_blank" rel="noreferrer">Music Usage Confirmation</a>
    </span>
  ) : (
    <span>
      By posting, you agree to TikTok's{' '}
      <a href={MUSIC_USAGE_URL} target="_blank" rel="noreferrer">Music Usage Confirmation</a>
    </span>
  )
)

export const TikTokPostForm: React.FC<{
  account: SocialAccount
  clip?: ClipRow
  value: TikTokPostOptions
  onChange: (v: TikTokPostOptions) => void
  onValidityChange?: (blocked?: string) => void
}> = ({ account, clip, value, onChange, onValidityChange }) => {
  const [info, setInfo] = useState<TikTokCreatorInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(undefined)
    socialApi.creatorInfo(account.id)
      .then((r) => { if (!cancelled) setInfo(r) })
      .catch((e) => { if (!cancelled) { setInfo(null); setError(errText(e)) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [account.id])

  const durationSec = clip ? Number(clip.duration || 0) : undefined
  const block = useMemo(() => tiktokFormBlockReason(value, info, durationSec) || error, [value, info, durationSec, error])
  useEffect(() => { onValidityChange?.(block) }, [block, onValidityChange])
  const options = info?.privacy_level_options || []
  const brandedBlocked = value.privacy_level === 'SELF_ONLY'
  const onlyMeBlocked = value.brand_content_toggle

  const set = (patch: Partial<TikTokPostOptions>) => onChange({ ...value, ...patch })

  const previewUrl = clip?.project_id ? projectApi.getClipVideoUrl(clip.project_id, clip.id) : undefined

  return (
    <div className="ac-tiktok-form" data-testid="tiktok-post-form" style={{ display: 'grid', gap: 14 }}>
      <Text strong>Post to TikTok</Text>
      {loading && <Spin tip="Loading TikTok account…" />}
      {error && <Alert type="error" showIcon message={error} />}
      {info && (
        <Space align="center" size={12} data-testid="tiktok-creator">
          <Avatar src={info.creator_avatar_url} size={48} alt="">
            {(info.creator_nickname || account.display_name || 'T').slice(0, 1)}
          </Avatar>
          <div>
            <div data-testid="tiktok-nickname"><b>{info.creator_nickname || account.display_name || 'TikTok account'}</b></div>
            {(info.creator_username || account.username) && (
              <Text type="secondary">@{info.creator_username || account.username}</Text>
            )}
          </div>
        </Space>
      )}
      {info && !info.can_post && (
        <Alert type="error" showIcon data-testid="tiktok-cannot-post"
          message={info.error_message || 'This TikTok account cannot post right now. Try again later.'} />
      )}
      {info && info.max_video_post_duration_sec && durationSec && durationSec > info.max_video_post_duration_sec && (
        <Alert type="error" showIcon data-testid="tiktok-duration"
          message={`This clip is ${Math.round(durationSec)}s; this TikTok account allows ${info.max_video_post_duration_sec}s max.`} />
      )}
      {info && !info.audited && (
        <Alert type="warning" showIcon
          message="Until TikTok approves this app, the post will be saved as Only me after you choose a privacy setting." />
      )}

      {previewUrl && (
        <div>
          <div style={{ marginBottom: 6 }}><b>Preview</b></div>
          <video
            data-testid="tiktok-preview"
            src={previewUrl}
            controls
            playsInline
            muted
            style={{ width: '100%', maxHeight: 420, background: '#000', borderRadius: 8 }}
          />
        </div>
      )}

      <div>
        <div style={{ marginBottom: 6 }}><b>Who can watch</b></div>
        <Select
          data-testid="tiktok-privacy"
          style={{ width: '100%' }}
          placeholder="Select privacy — no default"
          value={value.privacy_level || undefined}
          allowClear={false}
          onChange={(v) => set({ privacy_level: v })}
          options={options.map((v) => ({
            value: v,
            label: PRIVACY_LABELS[v] || v,
            disabled: onlyMeBlocked && v === 'SELF_ONLY',
          }))}
          optionRender={(opt) => (
            <span title={opt.value === 'SELF_ONLY' && onlyMeBlocked ? 'Branded content visibility cannot be set to private.' : undefined}>
              {opt.label}
            </span>
          )}
        />
        {onlyMeBlocked && (
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
            Branded content visibility cannot be set to private.
          </Text>
        )}
      </div>

      <div>
        <div style={{ marginBottom: 6 }}><b>Allow interactions</b></div>
        <Space direction="vertical">
          <Checkbox data-testid="tiktok-allow-comment"
            checked={!value.disable_comment}
            disabled={!!info?.comment_disabled}
            onChange={(e) => set({ disable_comment: !e.target.checked })}>
            Allow Comment
          </Checkbox>
          <Checkbox data-testid="tiktok-allow-duet"
            checked={!value.disable_duet}
            disabled={!!info?.duet_disabled}
            onChange={(e) => set({ disable_duet: !e.target.checked })}>
            Allow Duet
          </Checkbox>
          <Checkbox data-testid="tiktok-allow-stitch"
            checked={!value.disable_stitch}
            disabled={!!info?.stitch_disabled}
            onChange={(e) => set({ disable_stitch: !e.target.checked })}>
            Allow Stitch
          </Checkbox>
        </Space>
        {(info?.comment_disabled || info?.duet_disabled || info?.stitch_disabled) && (
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
            Greyed-out options are turned off in this TikTok account's settings.
          </Text>
        )}
      </div>

      <div>
        <Space>
          <Switch data-testid="tiktok-commercial" checked={value.commercial_disclosure}
            onChange={(on) => set({
              commercial_disclosure: on,
              brand_organic_toggle: on ? value.brand_organic_toggle : false,
              brand_content_toggle: on ? value.brand_content_toggle : false,
            })} />
          <span>This content promotes yourself, a brand, a product, or a service</span>
        </Space>
        {value.commercial_disclosure && (
          <div style={{ marginTop: 10, display: 'grid', gap: 8, paddingLeft: 4 }}>
            <Checkbox data-testid="tiktok-your-brand" checked={value.brand_organic_toggle}
              onChange={(e) => set({ brand_organic_toggle: e.target.checked })}>
              Your brand
            </Checkbox>
            {value.brand_organic_toggle && !value.brand_content_toggle && (
              <Alert type="info" showIcon message="Your photo/video will be labeled as 'Promotional content'" />
            )}
            <Checkbox data-testid="tiktok-branded" checked={value.brand_content_toggle}
              disabled={brandedBlocked}
              onChange={(e) => set({ brand_content_toggle: e.target.checked })}>
              Branded content
            </Checkbox>
            {brandedBlocked && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                Branded content visibility cannot be set to private.
              </Text>
            )}
            {value.brand_content_toggle && (
              <Alert type="info" showIcon message="Your photo/video will be labeled as 'Paid partnership'" />
            )}
            {!value.brand_organic_toggle && !value.brand_content_toggle && (
              <Alert type="warning" showIcon
                message="You need to indicate if your content promotes yourself, a third party, or both." />
            )}
          </div>
        )}
      </div>

      <Checkbox data-testid="tiktok-consent" checked={value.tiktok_consent}
        onChange={(e) => set({ tiktok_consent: e.target.checked })}>
        <ConsentCopy branded={!!value.brand_content_toggle} />
      </Checkbox>

      <Alert type="info" showIcon data-testid="tiktok-processing-note"
        message="After you finish publishing, it may take a few minutes for the content to process and be visible on your profile." />

      {block && info?.can_post && (
        <Text type="secondary" style={{ fontSize: 12 }} data-testid="tiktok-block-reason">{block}</Text>
      )}
    </div>
  )
}

export default TikTokPostForm
