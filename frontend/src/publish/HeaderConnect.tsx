import React, { useState } from 'react'
import { Button, Popover, Typography } from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import BrandLoginButton from './BrandLoginButton'
import { Platform, socialApi, SocialStatus } from './socialApi'

/** Header shortcut: one-click social logins from any page. */
const HeaderConnect: React.FC = () => {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<SocialStatus>()
  const [count, setCount] = useState<number>()
  const onOpenChange = (next: boolean) => {
    setOpen(next)
    if (next) {
      socialApi.status().then(setStatus).catch(() => undefined)
      socialApi.accounts().then((r) => setCount(r.accounts.length)).catch(() => undefined)
    }
  }
  const content = (
    <div style={{ width: 280 }} data-testid="header-connect-panel">
      <Typography.Text strong>Connect social accounts</Typography.Text>
      <div style={{ fontSize: 12, color: 'var(--ac-sub)', margin: '2px 0 12px' }}>
        {count ? `${count} connected. ` : ''}Sign in on each platform's own page; AutoClip posts for you through the official APIs.
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {(['tiktok', 'instagram', 'youtube'] as Platform[]).map((p) => (
          <BrandLoginButton key={p} platform={p} size="middle" block
            configured={!!status?.platforms.find((x) => x.platform === p)?.configured}
            onNavigate={() => setOpen(false)} />
        ))}
      </div>
    </div>
  )
  return (
    <Popover trigger="click" placement="bottomRight" open={open} onOpenChange={onOpenChange} content={content}>
      <Button
        type="text" icon={<LinkOutlined />} data-testid="nav-connect"
        style={{ color: 'var(--ac-sub)', border: '1px solid var(--ac-line)', borderRadius: '999px', height: '36px', padding: '0 16px', background: 'var(--ac-card)' }}
      >Connect</Button>
    </Popover>
  )
}

export default HeaderConnect
