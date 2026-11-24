import React, { useEffect, useRef, useState } from 'react'
import { Alert, Avatar, Button, Card, Col, Input, message, Popconfirm, Row, Space, Tag, Tooltip, Typography } from 'antd'
import { CheckCircleFilled, DeleteOutlined, ExportOutlined, LockOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import BrandLoginButton, { GoogleLogo, InstagramLogo, TikTokLogo } from './BrandLoginButton'
import { Platform, PLATFORM_LABEL, PlatformStatus, socialApi, SocialAccount, SocialStatus } from './socialApi'

const { Text, Paragraph, Link } = Typography
export const PLATFORMS: Platform[] = ['tiktok', 'instagram', 'youtube']

export function errText(e: unknown): string {
  const any = e as { response?: { data?: { detail?: string } }; message?: string }
  return any?.response?.data?.detail || any?.message || String(e)
}

export const PlatformMark: React.FC<{ platform: Platform; size?: number }> = ({ platform, size = 18 }) => (
  <span className={`ac-platform-mark ac-platform-mark--${platform}`} style={{ width: size + 10, height: size + 10 }}>
    {platform === 'tiktok' && <TikTokLogo size={size} />}
    {platform === 'instagram' && <InstagramLogo size={size} />}
    {platform === 'youtube' && <GoogleLogo size={size} />}
  </span>
)

function expiryText(iso?: string): { text: string; color?: string; title?: string } {
  if (!iso) return { text: 'No expiry reported' }
  const at = dayjs(iso)
  const mins = at.diff(dayjs(), 'minute')
  const title = `Access token expires ${at.format('YYYY-MM-DD HH:mm')} (your time). Refreshed automatically before it runs out.`
  if (mins <= 0) return { text: 'Token expired: refreshing on next use', color: 'orange', title }
  if (mins < 120) return { text: `Token expires in ${mins} min`, title }
  if (mins < 48 * 60) return { text: `Token expires in ${Math.round(mins / 60)} h`, title }
  return { text: `Token expires in ${Math.round(mins / 1440)} days`, title }
}

const REVOKE_NOTE: Record<Platform, string> = {
  tiktok: 'Disconnect also revokes AutoClip\'s access on TikTok.',
  youtube: 'Disconnect also revokes AutoClip\'s access to your Google account.',
  instagram: 'Instagram has no revoke API: after disconnecting, remove AutoClip in Instagram → Settings → Apps and websites.',
}

// ----------------------------------------------------------------- accounts ---
export const AccountsPanel: React.FC<{
  status?: SocialStatus; accounts: SocialAccount[]; reload: () => void; openApps: (p: Platform) => void
}> = ({ status, accounts, reload, openApps }) => (
  <Row gutter={[16, 16]}>
    {PLATFORMS.map((p) => {
      const st = status?.platforms.find((x) => x.platform === p)
      const configured = !!st?.configured
      const mine = accounts.filter((a) => a.platform === p)
      return (
        <Col xs={24} lg={8} key={p}>
          <Card
            data-testid={`account-card-${p}`}
            title={<Space><PlatformMark platform={p} />{p === 'youtube' ? 'YouTube' : PLATFORM_LABEL[p]}</Space>}
            extra={configured
              ? <Tooltip title={st?.source === 'env' ? 'Keys from the server .env' : 'Keys entered in Developer apps'}><Tag color="green">App ready</Tag></Tooltip>
              : <Tag color="orange" style={{ cursor: 'pointer' }} onClick={() => openApps(p)}>Needs app keys</Tag>}
          >
            {mine.length === 0 && (
              <Paragraph type="secondary" style={{ marginBottom: 16 }}>
                {p === 'tiktok' && 'Post videos straight to TikTok with the Content Posting API.'}
                {p === 'instagram' && 'Publish Reels to an Instagram professional (Business or Creator) account.'}
                {p === 'youtube' && 'Upload Shorts and videos to your YouTube channel with your Google account.'}
              </Paragraph>
            )}
            <Space direction="vertical" style={{ width: '100%' }} size={10}>
              {mine.map((a) => {
                const exp = expiryText(a.access_expires_at)
                return (
                  <div key={a.id} className="ac-account-row" data-testid={`account-${p}-${a.id}`}>
                    <Avatar size={44} src={a.avatar_url || undefined}>{(a.display_name || a.username || '?')[0]?.toUpperCase()}</Avatar>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {a.display_name || a.username || a.external_id}
                        {a.status === 'active' && <CheckCircleFilled style={{ color: '#52c41a', marginLeft: 6 }} />}
                      </div>
                      {a.username && <Text type="secondary">@{a.username}</Text>}
                      <div><Tooltip title={exp.title}><Text type={exp.color ? 'warning' : 'secondary'} style={{ fontSize: 12 }}>{exp.text}</Text></Tooltip></div>
                      {a.status !== 'active' && <Tag color="red" style={{ marginTop: 4 }}>{a.status === 'needs_reconnect' ? 'Needs reconnect' : a.status}</Tag>}
                      {a.last_error && a.status !== 'active' && <div><Text type="danger" style={{ fontSize: 12 }}>{a.last_error.slice(0, 160)}</Text></div>}
                      <Space size={6} style={{ marginTop: 8 }} wrap>
                        <BrandLoginButton platform={p} configured={configured} intent="reconnect" size="small" />
                        <Popconfirm
                          title={`Disconnect ${a.display_name || a.username || 'this account'}?`}
                          description={<div style={{ maxWidth: 260 }}>{REVOKE_NOTE[p]} Queued posts for it are cancelled.</div>}
                          okText="Disconnect" okButtonProps={{ danger: true }}
                          onConfirm={async () => {
                            try {
                              const r = await socialApi.disconnect(a.id) as { revoked?: boolean | string }
                              if (r?.revoked === true) message.success('Disconnected and access revoked')
                              else if (r?.revoked === 'unsupported') message.success('Disconnected. Also remove AutoClip in Instagram → Settings → Apps and websites.', 8)
                              else message.warning('Disconnected here, but the platform did not confirm the revoke. Remove access in the platform\'s settings.', 8)
                              reload()
                            } catch (e) { message.error(errText(e)) }
                          }}
                        ><Button size="small" danger data-testid={`disconnect-${a.id}`}>Disconnect</Button></Popconfirm>
                      </Space>
                    </div>
                  </div>
                )
              })}
              <BrandLoginButton platform={p} configured={configured} intent={mine.length ? 'another' : 'login'} block />
              {!configured && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  First add the {PLATFORM_LABEL[p]} developer app keys. <a onClick={() => openApps(p)}>Open Developer apps</a>
                </Text>
              )}
              {p === 'tiktok' && configured && !status?.notes.tiktok_audited && (
                <Alert type="info" showIcon message="Until TikTok approves the app audit, posts are visible only to you (private)." />
              )}
              {p === 'youtube' && configured && !status?.notes.google_verified && (
                <Alert type="info" showIcon message="While the Google consent screen is in Testing, only listed test users can sign in, and they must reconnect every 7 days." />
              )}
            </Space>
          </Card>
        </Col>
      )
    })}
  </Row>
)

// ----------------------------------------------------------- developer apps ---
const AppCard: React.FC<{ st: PlatformStatus; focus: boolean; onSaved: () => void; canEncrypt: boolean }> = ({ st, focus, onSaved, canEncrypt }) => {
  const p = st.platform
  const ref = useRef<HTMLDivElement>(null)
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (focus) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }, [focus])
  const save = async () => {
    setBusy(true)
    try {
      await socialApi.saveApp(p, { client_id: clientId.trim(), client_secret: secret.trim() || undefined })
      setClientId(''); setSecret('')
      message.success(`${PLATFORM_LABEL[p]} app saved (encrypted). The login button now goes straight to ${PLATFORM_LABEL[p]}.`)
      onSaved()
    } catch (e) { message.error(errText(e)) } finally { setBusy(false) }
  }
  const remove = async () => {
    try { await socialApi.deleteApp(p); message.success('Removed'); onSaved() } catch (e) { message.error(errText(e)) }
  }
  const locked = !!st.env_locked
  return (
    <div ref={ref}>
      <Card
        className={focus ? 'ac-app-card--focus' : undefined}
        data-testid={`app-card-${p}`}
        title={<Space><PlatformMark platform={p} />{p === 'youtube' ? 'Google / YouTube' : PLATFORM_LABEL[p]}</Space>}
        extra={st.configured
          ? <Tag color="green">{st.source === 'env' ? 'Configured in .env' : 'Configured'}</Tag>
          : <Tag color="orange">Not configured</Tag>}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div>
            <Text strong>1. Redirect URI</Text>
            <div style={{ fontSize: 12, color: 'var(--ac-sub)', marginBottom: 4 }}>Paste this exact value into the developer app{p === 'youtube' ? ' (OAuth client → Authorized redirect URIs)' : p === 'instagram' ? ' (Instagram → API setup with Instagram login → Business login settings → OAuth redirect URIs)' : ' (Login Kit → Redirect URI)'}:</div>
            <Text code copyable={{ text: st.redirect_uri, tooltips: ['Copy', 'Copied'] }} style={{ wordBreak: 'break-all' }} data-testid={`redirect-${p}`}>{st.redirect_uri}</Text>
          </div>
          <div>
            <Text strong>2. Keys</Text>
            {locked ? (
              <Alert style={{ marginTop: 6 }} type="success" showIcon icon={<LockOutlined />}
                message={`Set in the server .env (${(st.env_names || []).join(', ')}), which takes precedence.`}
                description={<>Client ID <Text code>{st.client_id_masked || '—'}</Text> · secret {st.secret_set ? 'set' : 'missing'}</>} />
            ) : (
              <Space direction="vertical" style={{ width: '100%', marginTop: 6 }} size={8}>
                {st.source === 'db' && (
                  <Text type="secondary" style={{ fontSize: 12 }}>Saved: <Text code>{st.client_id_masked}</Text> · secret •••••••• (stored encrypted, never shown again)</Text>
                )}
                <Input
                  addonBefore={st.id_label || 'Client ID'} value={clientId} autoComplete="off" spellCheck={false}
                  placeholder={st.source === 'db' ? 'Enter to replace' : (p === 'youtube' ? '1234-abc.apps.googleusercontent.com' : '')}
                  onChange={(e) => setClientId(e.target.value)} data-testid={`app-id-${p}`}
                />
                <Input.Password
                  addonBefore={st.secret_label || 'Client secret'} value={secret} autoComplete="new-password"
                  placeholder={st.source === 'db' ? 'Leave blank to keep the saved secret' : ''}
                  onChange={(e) => setSecret(e.target.value)} data-testid={`app-secret-${p}`}
                />
                <Space>
                  <Button type="primary" loading={busy} disabled={!canEncrypt || !clientId.trim() || (st.source !== 'db' && !secret.trim())} onClick={save} data-testid={`app-save-${p}`}>Save</Button>
                  {st.source === 'db' && (
                    <Popconfirm title="Remove these app keys? Connected accounts stay, but cannot refresh until keys are added again." onConfirm={remove}>
                      <Button icon={<DeleteOutlined />} danger>Remove</Button>
                    </Popconfirm>
                  )}
                </Space>
              </Space>
            )}
          </div>
          <div>
            <Text strong>3. Links</Text>
            <div><Link href={st.console_url} target="_blank" rel="noreferrer">{p === 'youtube' ? 'Google Cloud console' : p === 'instagram' ? 'Meta for Developers' : 'TikTok for Developers'} <ExportOutlined /></Link></div>
            <div><Link href={st.docs_url} target="_blank" rel="noreferrer">Platform docs <ExportOutlined /></Link></div>
            <div><Link href={st.setup_guide_url} target="_blank" rel="noreferrer">AutoClip setup guide <ExportOutlined /></Link></div>
          </div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {p === 'tiktok' && 'Add the Login Kit and Content Posting API products, enable Direct Post, and request scopes user.info.basic and video.publish. PKCE is used.'}
            {p === 'instagram' && 'Create a Business app, add "Instagram" (API setup with Instagram login), and request instagram_business_basic and instagram_business_content_publish. Use the Instagram app ID and secret, not the Meta app ones.'}
            {p === 'youtube' && 'Enable the YouTube Data API v3, configure the OAuth consent screen (add yourself as a test user while in Testing), then create an OAuth client of type "Web application". Scopes: youtube.upload and youtube.readonly. PKCE is used.'}
          </Text>
        </Space>
      </Card>
    </div>
  )
}

export const DeveloperAppsPanel: React.FC<{ focus?: Platform; onChanged: () => void }> = ({ focus, onChanged }) => {
  const [apps, setApps] = useState<PlatformStatus[]>([])
  const [canEncrypt, setCanEncrypt] = useState(true)
  const load = async () => {
    try { const r = await socialApi.apps(); setApps(r.apps); setCanEncrypt(r.token_encryption) } catch (e) { message.error(errText(e)) }
  }
  useEffect(() => { void load() }, [])
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert type="info" showIcon
        message="Developer apps: one per platform, created once by the AutoClip owner"
        description={<>Each login button needs that platform's app keys. Keys entered here are encrypted with the server's SOCIAL_TOKEN_KEY and are never sent back to the browser. Keys in the server <Text code>.env</Text> take precedence.</>} />
      {!canEncrypt && <Alert type="error" showIcon message="SOCIAL_TOKEN_KEY is missing on the server, so keys cannot be stored." />}
      <Row gutter={[16, 16]}>
        {apps.map((st) => (
          <Col xs={24} lg={8} key={st.platform}>
            <AppCard st={st} focus={focus === st.platform} canEncrypt={canEncrypt} onSaved={() => { void load(); onChanged() }} />
          </Col>
        ))}
      </Row>
    </Space>
  )
}
