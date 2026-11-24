import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Avatar, Button, Card, Checkbox, Col, DatePicker, Empty, Input, InputNumber, List, message, Modal, Popconfirm,
  Progress, Row, Select, Space, Switch, Table, Tabs, Tag, Tooltip, Typography,
} from 'antd'
import dayjs, { Dayjs } from 'dayjs'
import {
  Caption, ClipRow, Platform, PLATFORM_LABEL, PublisherSettings, PublishJob, SeoReport, SeoResult, socialApi,
  SocialAccount, SocialStatus, Target, TARGET_LABEL,
} from '../publish/socialApi'

const { Text, Paragraph } = Typography

const STATUS_COLOR: Record<string, string> = {
  draft: 'default', queued: 'blue', rendering: 'cyan', uploading: 'geekblue', processing: 'purple',
  published: 'green', failed: 'red', cancelled: 'default',
}
const TARGETS_FOR: Record<Platform, Target[]> = { tiktok: ['tiktok'], instagram: ['instagram'], youtube: ['youtube_shorts', 'youtube'] }

function errText(e: unknown): string {
  const any = e as { response?: { data?: { detail?: string } }; message?: string }
  return any?.response?.data?.detail || any?.message || String(e)
}

const ScoreBadge: React.FC<{ report?: SeoReport }> = ({ report }) => {
  if (!report || report.score === undefined) return null
  const color = report.score >= 80 ? '#3f8600' : report.score >= 60 ? '#d48806' : '#cf1322'
  return (
    <Tooltip title={<div>{report.checks?.map((c) => <div key={c.id}>{c.ok ? '✓' : '✗'} {c.detail}</div>)}</div>}>
      <Tag color={color} style={{ cursor: 'help' }}>SEO {report.score}</Tag>
    </Tooltip>
  )
}

// ----------------------------------------------------------------- accounts ---
const AccountsPanel: React.FC<{
  status?: SocialStatus; accounts: SocialAccount[]; reload: () => void
}> = ({ status, accounts, reload }) => {
  const connect = (p: Platform) => { window.location.href = socialApi.connectUrl(p) }
  return (
    <Row gutter={[16, 16]}>
      {(['tiktok', 'instagram', 'youtube'] as Platform[]).map((p) => {
        const st = status?.platforms.find((x) => x.platform === p)
        const mine = accounts.filter((a) => a.platform === p)
        return (
          <Col xs={24} md={8} key={p}>
            <Card
              title={PLATFORM_LABEL[p]}
              data-testid={`account-card-${p}`}
              extra={st?.configured ? <Tag color="green">App configured</Tag> : <Tag color="orange">Not configured</Tag>}
            >
              {!st?.configured && (
                <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                  message="Developer app credentials missing"
                  description={<>
                    <div>Server .env needs: <Text code>{st?.missing_env.join(', ')}</Text></div>
                    <div style={{ marginTop: 6 }}>Redirect URI to register:</div>
                    <Text code copyable style={{ wordBreak: 'break-all' }}>{st?.redirect_uri}</Text>
                  </>} />
              )}
              {p === 'tiktok' && st?.configured && !status?.notes.tiktok_audited && (
                <Alert type="info" showIcon style={{ marginBottom: 12 }} message="Unaudited TikTok app: posts go out as private (SELF_ONLY) until TikTok approves the audit." />
              )}
              {p === 'youtube' && st?.configured && !status?.notes.google_verified && (
                <Alert type="info" showIcon style={{ marginBottom: 12 }} message="Google app still in Testing: only listed test users can connect and they must reconnect every 7 days. Publish the consent screen to remove this." />
              )}
              <List
                locale={{ emptyText: 'No account connected' }}
                dataSource={mine}
                renderItem={(a) => (
                  <List.Item actions={[
                    <Popconfirm key="d" title="Disconnect and revoke access?" onConfirm={async () => {
                      try { await socialApi.disconnect(a.id); message.success('Disconnected'); reload() } catch (e) { message.error(errText(e)) }
                    }}><Button size="small" danger>Disconnect</Button></Popconfirm>,
                  ]}>
                    <List.Item.Meta
                      avatar={<Avatar src={a.avatar_url}>{(a.display_name || '?')[0]}</Avatar>}
                      title={a.display_name || a.username}
                      description={<Space size={4} wrap>
                        {a.username && <Text type="secondary">@{a.username}</Text>}
                        <Tag color={a.status === 'active' ? 'green' : 'red'}>{a.status}</Tag>
                      </Space>}
                    />
                  </List.Item>
                )}
              />
              <Button type="primary" block style={{ marginTop: 12 }} disabled={!st?.configured} onClick={() => connect(p)}>
                {mine.length ? 'Connect another account' : `Connect ${PLATFORM_LABEL[p]}`}
              </Button>
            </Card>
          </Col>
        )
      })}
    </Row>
  )
}

// ------------------------------------------------------------------ compose ---
const CaptionEditor: React.FC<{ target: Target; value: Caption; report?: SeoReport; onChange: (c: Caption) => void }> = ({ target, value, report, onChange }) => {
  const yt = target.startsWith('youtube')
  const text = yt ? value.description || '' : value.caption || ''
  const limit = yt ? 5000 : 2200
  return (
    <Card size="small" title={<Space>{TARGET_LABEL[target]}<ScoreBadge report={report} /></Space>} style={{ marginBottom: 12 }}>
      {yt && (
        <Input
          addonBefore="Title" value={value.title} maxLength={100} showCount style={{ marginBottom: 8 }}
          onChange={(e) => onChange({ ...value, title: e.target.value })}
        />
      )}
      <Input.TextArea
        value={text} autoSize={{ minRows: 4, maxRows: 12 }} maxLength={limit} showCount
        onChange={(e) => onChange(yt ? { ...value, description: e.target.value, caption: e.target.value } : { ...value, caption: e.target.value, description: e.target.value })}
      />
      {yt && (
        <Select
          mode="tags" style={{ width: '100%', marginTop: 8 }} placeholder="Tags" value={value.tags}
          onChange={(tags) => onChange({ ...value, tags })}
        />
      )}
      {value.primary_keyword && <Text type="secondary" style={{ fontSize: 12 }}>Primary keyword: <b>{value.primary_keyword}</b></Text>}
    </Card>
  )
}

const ComposePanel: React.FC<{ accounts: SocialAccount[]; projectId?: string; clipId?: string; onQueued: () => void }> = ({ accounts, projectId, clipId, onQueued }) => {
  const [clips, setClips] = useState<ClipRow[]>([])
  const [clip, setClip] = useState<string | undefined>(clipId)
  const [picked, setPicked] = useState<string[]>([]) // `${accountId}|${target}`
  const [seo, setSeo] = useState<SeoResult>()
  const [captions, setCaptions] = useState<Record<string, Caption>>({})
  const [busy, setBusy] = useState<'' | 'seo' | 'publish'>('')
  const [when, setWhen] = useState<Dayjs | null>(null)

  useEffect(() => { socialApi.clips(projectId).then((r) => setClips(r.clips)).catch((e) => message.error(errText(e))) }, [projectId])
  useEffect(() => { if (clipId) setClip(clipId) }, [clipId])

  const options = useMemo(() => accounts.filter((a) => a.status === 'active').flatMap((a) =>
    TARGETS_FOR[a.platform].map((t) => ({ value: `${a.id}|${t}`, label: `${TARGET_LABEL[t]} · ${a.display_name || a.username || a.platform}` }))), [accounts])
  const targets = useMemo(() => Array.from(new Set(picked.map((p) => p.split('|')[1] as Target))), [picked])

  const generate = async () => {
    if (!clip) return
    setBusy('seo')
    try {
      const res = await socialApi.generate(clip, targets.length ? targets : ['tiktok', 'instagram', 'youtube_shorts', 'youtube'])
      setSeo(res)
      setCaptions(Object.fromEntries(Object.entries(res.targets).map(([t, v]) => [t, v.caption])))
      if (res.source !== 'llm') message.warning('AI model unavailable; used keyword-based copy. You can edit it.')
    } catch (e) { message.error(errText(e)) } finally { setBusy('') }
  }

  const publish = async (draft: boolean) => {
    if (!clip || !picked.length) return
    setBusy('publish')
    try {
      const res = await socialApi.createJobs({
        clip_id: clip,
        targets: picked.map((p) => { const [account_id, target] = p.split('|'); return { account_id, target: target as Target } }),
        captions: Object.fromEntries(targets.filter((t) => captions[t]).map((t) => [t, captions[t]])),
        scheduled_at: when ? when.toDate().toISOString() : undefined,
        draft,
      })
      message.success(`${res.jobs.length} post(s) ${draft ? 'saved as drafts' : when ? 'scheduled' : 'queued'}`)
      onQueued()
    } catch (e) { message.error(errText(e)) } finally { setBusy('') }
  }

  return (
    <Row gutter={24}>
      <Col xs={24} lg={9}>
        <Card title="1 · Clip and destinations">
          <Select
            showSearch style={{ width: '100%' }} placeholder="Choose a finished clip" value={clip} onChange={setClip}
            optionFilterProp="label" data-testid="clip-select"
            options={clips.map((c) => ({ value: c.id, label: `${c.title} (${Math.round(c.duration)}s${c.score ? ` · ${Math.round(c.score * 100)}` : ''}) — ${c.project_name || ''}` }))}
          />
          <div style={{ marginTop: 16 }}>
            {options.length ? (
              <Checkbox.Group value={picked} onChange={(v) => setPicked(v as string[])} style={{ display: 'grid', gap: 6 }} options={options} />
            ) : <Alert type="info" showIcon message="Connect an account in the Accounts tab first." />}
          </div>
          <Space direction="vertical" style={{ width: '100%', marginTop: 16 }}>
            <DatePicker showTime value={when} onChange={setWhen} style={{ width: '100%' }} placeholder="Publish now (or pick a time)"
              disabledDate={(d) => d.isBefore(dayjs().startOf('day'))} />
            <Button block onClick={generate} loading={busy === 'seo'} disabled={!clip}>Generate SEO captions</Button>
            <Button type="primary" block onClick={() => publish(false)} loading={busy === 'publish'} disabled={!clip || !picked.length}>
              {when ? 'Schedule' : 'Publish now'}
            </Button>
            <Button block onClick={() => publish(true)} disabled={!clip || !picked.length}>Save as drafts for approval</Button>
          </Space>
        </Card>
      </Col>
      <Col xs={24} lg={15}>
        <Card title={<Space>2 · Captions{seo && <Tag>{seo.source === 'llm' ? `AI · ${seo.model || ''}` : 'Keyword fallback'}</Tag>}</Space>}>
          {!seo ? (
            <Empty description="Generate SEO captions to review and edit them per platform. If you skip this, captions are generated automatically when the post is queued." />
          ) : (
            (targets.length ? targets : (Object.keys(seo.targets) as Target[])).map((t) => captions[t] && (
              <CaptionEditor key={t} target={t} value={captions[t]} report={seo.targets[t]?.report}
                onChange={(c) => setCaptions({ ...captions, [t]: c })} />
            ))
          )}
        </Card>
      </Col>
    </Row>
  )
}

// -------------------------------------------------------------------- queue ---
const QueuePanel: React.FC<{ jobs: PublishJob[]; accounts: SocialAccount[]; reload: () => void }> = ({ jobs, accounts, reload }) => {
  const [editing, setEditing] = useState<PublishJob>()
  const [draft, setDraft] = useState<Caption>({})
  const acct = (id: string) => accounts.find((a) => a.id === id)
  const act = async (job: PublishJob, patch: Record<string, unknown>) => {
    try { await socialApi.updateJob(job.id, patch); reload() } catch (e) { message.error(errText(e)) }
  }
  return (
    <>
      <Table<PublishJob>
        rowKey="id" dataSource={jobs} size="middle" pagination={{ pageSize: 15 }} scroll={{ x: 900 }}
        locale={{ emptyText: <Empty description="No posts yet" /> }}
        columns={[
          { title: 'Post', render: (_, j) => <div style={{ maxWidth: 360 }}>
            <div><b>{j.caption?.title || j.caption?.caption?.slice(0, 80)}</b></div>
            <Text type="secondary" style={{ fontSize: 12 }}>{TARGET_LABEL[j.target]} · {acct(j.account_id)?.display_name || 'disconnected'}{j.source === 'auto' ? ' · auto' : ''}</Text>
          </div> },
          { title: 'Status', render: (_, j) => <Space direction="vertical" size={2}>
            <Tag color={STATUS_COLOR[j.status]}>{j.status}</Tag>
            {j.error && <Tooltip title={j.error}><Text type="danger" style={{ fontSize: 12 }}>{j.error.slice(0, 60)}</Text></Tooltip>}
            {j.status === 'queued' && j.attempts > 0 && <Text type="secondary" style={{ fontSize: 12 }}>retry {j.attempts}/{j.max_attempts}</Text>}
          </Space> },
          { title: 'SEO', render: (_, j) => <ScoreBadge report={j.seo_report} /> },
          { title: 'When', render: (_, j) => <Text style={{ fontSize: 12 }}>
            {j.published_at ? `Published ${dayjs(j.published_at).format('MMM D HH:mm')}` : j.scheduled_at ? `Scheduled ${dayjs(j.scheduled_at).format('MMM D HH:mm')}` : dayjs(j.created_at).format('MMM D HH:mm')}
          </Text> },
          { title: '', render: (_, j) => <Space wrap>
            {j.remote_url && <Button size="small" href={j.remote_url} target="_blank">Open</Button>}
            {j.status === 'draft' && <Button size="small" type="primary" onClick={() => act(j, { action: 'approve' })}>Approve</Button>}
            {['draft', 'queued', 'failed'].includes(j.status) && <Button size="small" onClick={() => { setEditing(j); setDraft(j.caption) }}>Edit</Button>}
            {['draft', 'queued'].includes(j.status) && <Button size="small" onClick={() => act(j, { action: 'publish_now' })}>Post now</Button>}
            {['failed', 'cancelled'].includes(j.status) && <Button size="small" onClick={() => act(j, { action: 'retry' })}>Retry</Button>}
            {['draft', 'queued'].includes(j.status) && <Button size="small" danger onClick={() => act(j, { action: 'cancel' })}>Cancel</Button>}
            {['published', 'failed', 'cancelled', 'draft'].includes(j.status) && (
              <Popconfirm title="Remove from list?" onConfirm={async () => { await socialApi.deleteJob(j.id); reload() }}>
                <Button size="small" type="text">Remove</Button>
              </Popconfirm>)}
          </Space> },
        ]}
      />
      <Modal open={!!editing} title={editing ? `Edit ${TARGET_LABEL[editing.target]} caption` : ''} width={720}
        onCancel={() => setEditing(undefined)}
        onOk={async () => { if (editing) { await act(editing, { caption: draft }); setEditing(undefined) } }}>
        {editing && <CaptionEditor target={editing.target} value={draft} onChange={setDraft} report={editing.seo_report} />}
      </Modal>
    </>
  )
}

// --------------------------------------------------------------- automation ---
const AutomationPanel: React.FC<{ accounts: SocialAccount[] }> = ({ accounts }) => {
  const [s, setS] = useState<PublisherSettings>()
  useEffect(() => { socialApi.settings().then(setS).catch((e) => message.error(errText(e))) }, [])
  if (!s) return <Card loading />
  const save = async (patch: Partial<PublisherSettings>) => {
    try { setS(await socialApi.saveSettings(patch)); message.success('Saved') } catch (e) { message.error(errText(e)) }
  }
  const targetOptions = accounts.flatMap((a) => TARGETS_FOR[a.platform].map((t) => ({ value: `${a.id}|${t}`, label: `${TARGET_LABEL[t]} · ${a.display_name || a.username}` })))
  return (
    <Card title="Auto-publish new clips" style={{ maxWidth: 760 }}>
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Space><Switch checked={s.auto_publish} onChange={(v) => save({ auto_publish: v })} data-testid="auto-publish-toggle" />
          <span><b>Auto-publish</b> every clip that finishes processing{s.enabled_since ? ` (clips created after ${dayjs(s.enabled_since).format('MMM D HH:mm')})` : ''}</span></Space>
        <Space><Switch checked={s.require_approval} onChange={(v) => save({ require_approval: v })} />
          <span><b>Require my approval</b> — auto posts wait as drafts in the Queue</span></Space>
        <div>
          <div style={{ marginBottom: 6 }}><b>Post to</b></div>
          <Select mode="multiple" style={{ width: '100%' }} placeholder={targetOptions.length ? 'Choose accounts' : 'Connect accounts first'}
            value={s.targets.map((t) => `${t.account_id}|${t.target}`)} options={targetOptions}
            onChange={(v: string[]) => save({ targets: v.map((x) => { const [account_id, target] = x.split('|'); return { account_id, target: target as Target } }) })} />
        </div>
        <Space wrap size="large">
          <span>Minimum clip score <InputNumber min={0} max={1} step={0.05} value={s.min_score} onChange={(v) => save({ min_score: Number(v || 0) })} /></span>
          <span>Space posts by <InputNumber min={0} max={1440} value={s.spacing_minutes} onChange={(v) => save({ spacing_minutes: Number(v || 0) })} /> min</span>
          <span>Max auto posts/day/account <InputNumber min={1} max={50} value={s.max_per_day} onChange={(v) => save({ max_per_day: Number(v || 1) })} /></span>
        </Space>
        <Space wrap size="large">
          <span>Caption language <Select style={{ width: 140 }} value={s.language} onChange={(v) => save({ language: v })}
            options={[{ value: '', label: 'Auto-detect' }, { value: 'en', label: 'English' }, { value: 'zh', label: 'Chinese' }, { value: 'ar', label: 'Arabic' }, { value: 'es', label: 'Spanish' }]} /></span>
          <span>Burn subtitles <Switch checked={s.burn_subtitles} onChange={(v) => save({ burn_subtitles: v })} /></span>
        </Space>
        <div>
          <div style={{ marginBottom: 6 }}><b>Brand hashtags</b> (added to every post)</div>
          <Select mode="tags" style={{ width: '100%' }} value={s.brand_hashtags} onChange={(v) => save({ brand_hashtags: v })} placeholder="e.g. ZedosAI" />
        </div>
        <Space wrap size="large">
          <span>TikTok privacy <Select style={{ width: 210 }} value={String(s.defaults.tiktok?.privacy_level || 'PUBLIC_TO_EVERYONE')}
            onChange={(v) => save({ defaults: { ...s.defaults, tiktok: { ...s.defaults.tiktok, privacy_level: v } } })}
            options={['PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'FOLLOWER_OF_CREATOR', 'SELF_ONLY'].map((v) => ({ value: v, label: v }))} /></span>
          <span>YouTube visibility <Select style={{ width: 120 }} value={String(s.defaults.youtube?.privacy_status || 'public')}
            onChange={(v) => save({ defaults: { ...s.defaults, youtube: { ...s.defaults.youtube, privacy_status: v }, youtube_shorts: { ...s.defaults.youtube_shorts, privacy_status: v } } })}
            options={['public', 'unlisted', 'private'].map((v) => ({ value: v, label: v }))} /></span>
        </Space>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          The queue runs every minute: it renders a 9:16 version for TikTok, Reels and Shorts (60 s max), writes SEO captions with the AI model, uploads through each platform's official API, retries temporary failures (2, 10, 30 and 90 minutes) and refreshes tokens automatically.
        </Paragraph>
      </Space>
    </Card>
  )
}

// --------------------------------------------------------------------- page ---
const PublishingPage: React.FC = () => {
  const navigate = useNavigate()
  const { id: routeProject, clipId: routeClip } = useParams()
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  const projectId = routeProject || params.get('project') || undefined
  const clipId = routeClip || params.get('clip') || undefined
  const [tab, setTab] = useState<string>(params.get('tab') || (params.get('oauth') ? 'accounts' : 'compose'))
  const [status, setStatus] = useState<SocialStatus>()
  const [accounts, setAccounts] = useState<SocialAccount[]>([])
  const [jobs, setJobs] = useState<PublishJob[]>([])

  const reload = useCallback(async () => {
    try {
      const [st, ac, jb] = await Promise.all([socialApi.status(), socialApi.accounts(), socialApi.jobs()])
      setStatus(st); setAccounts(ac.accounts); setJobs(jb.jobs)
    } catch (e) { message.error(errText(e)) }
  }, [])
  useEffect(() => { void reload() }, [reload])
  useEffect(() => {
    const active = jobs.some((j) => ['queued', 'rendering', 'uploading', 'processing'].includes(j.status))
    if (!active) return
    const t = setInterval(() => { void socialApi.jobs().then((r) => setJobs(r.jobs)).catch(() => undefined) }, 10000)
    return () => clearInterval(t)
  }, [jobs])
  useEffect(() => {
    const oauth = params.get('oauth')
    if (!oauth) return
    if (params.get('result') === 'connected') message.success(`${PLATFORM_LABEL[oauth as Platform] || oauth} connected`)
    else message.error(`Could not connect ${oauth}: ${params.get('message') || 'unknown error'}`)
    navigate('/publishing?tab=accounts', { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const counts = useMemo(() => ({
    pending: jobs.filter((j) => ['draft'].includes(j.status)).length,
    active: jobs.filter((j) => ['queued', 'rendering', 'uploading', 'processing'].includes(j.status)).length,
    published: jobs.filter((j) => j.status === 'published').length,
    failed: jobs.filter((j) => j.status === 'failed').length,
  }), [jobs])
  const configured = status?.platforms.filter((p) => p.configured).length || 0

  return (
    <div className="ac-page" style={{ maxWidth: 1240 }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 className="ac-title" style={{ marginTop: 0 }}>Publishing</h1>
          <Text type="secondary">TikTok, Instagram Reels, YouTube Shorts and YouTube through each platform's official API, with AI-written SEO captions.</Text>
        </div>
        <Space size="large">
          <Tooltip title="Posts waiting for approval"><span>Drafts <b>{counts.pending}</b></span></Tooltip>
          <span>In queue <b>{counts.active}</b></span>
          <span>Published <b>{counts.published}</b></span>
          <span>Failed <b style={{ color: counts.failed ? '#cf1322' : undefined }}>{counts.failed}</b></span>
        </Space>
      </header>
      {status && configured < 3 && (
        <Alert style={{ marginTop: 16 }} type="info" showIcon
          message={`${configured} of 3 platform apps configured`}
          description="Each platform needs its own developer app (client ID and secret) in the server .env before accounts can connect. See docs/SOCIAL_PUBLISHING_SETUP.md in the repository." />
      )}
      {status && !status.token_encryption && (
        <Alert style={{ marginTop: 12 }} type="error" showIcon message="SOCIAL_TOKEN_KEY is not set: tokens cannot be stored." />
      )}
      <Tabs style={{ marginTop: 16 }} activeKey={tab} onChange={(k) => { setTab(k); void reload() }} items={[
        { key: 'compose', label: 'Compose', children: <ComposePanel accounts={accounts} projectId={projectId} clipId={clipId} onQueued={() => { setTab('queue'); void reload() }} /> },
        { key: 'queue', label: `Queue (${jobs.length})`, children: <QueuePanel jobs={jobs} accounts={accounts} reload={reload} /> },
        { key: 'accounts', label: `Accounts (${accounts.length})`, children: <AccountsPanel status={status} accounts={accounts} reload={reload} /> },
        { key: 'automation', label: 'Automation', children: <AutomationPanel accounts={accounts} /> },
      ]} />
      {counts.active > 0 && <Progress percent={100} status="active" showInfo={false} style={{ marginTop: 8 }} />}
    </div>
  )
}

export default PublishingPage
