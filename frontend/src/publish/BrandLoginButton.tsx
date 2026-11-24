import React from 'react'
import { useNavigate } from 'react-router-dom'
import { Platform, socialApi } from './socialApi'

/*
 * One-click "Log in with …" buttons in each platform's own style.
 * - TikTok: black button, white logo (TikTok brand guidelines).
 * - Instagram: Instagram gradient, white glyph.
 * - Google: the standard "Sign in with Google" button (white, #747775 border, multicolour G),
 *   which Google requires for its OAuth consent; it connects a YouTube channel.
 * Icons: Simple Icons (CC0) for TikTok/Instagram, Google's published G mark.
 *
 * A configured platform navigates the top-level window to /social/oauth/{p}/start, so the browser
 * re-sends the AutoClip basic-auth credentials and the server answers with a 302 to the provider.
 * An unconfigured platform opens Publishing > Developer apps for that platform instead.
 */
const TIKTOK_PATH = 'M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z'
const INSTAGRAM_PATH = 'M7.0301.084c-1.2768.0602-2.1487.264-2.911.5634-.7888.3075-1.4575.72-2.1228 1.3877-.6652.6677-1.075 1.3368-1.3802 2.127-.2954.7638-.4956 1.6365-.552 2.914-.0564 1.2775-.0689 1.6882-.0626 4.947.0062 3.2586.0206 3.6671.0825 4.9473.061 1.2765.264 2.1482.5635 2.9107.308.7889.72 1.4573 1.388 2.1228.6679.6655 1.3365 1.0743 2.1285 1.38.7632.295 1.6361.4961 2.9134.552 1.2773.056 1.6884.069 4.9462.0627 3.2578-.0062 3.668-.0207 4.9478-.0814 1.28-.0607 2.147-.2652 2.9098-.5633.7889-.3086 1.4578-.72 2.1228-1.3881.665-.6682 1.0745-1.3378 1.3795-2.1284.2957-.7632.4966-1.636.552-2.9124.056-1.2809.0692-1.6898.063-4.948-.0063-3.2583-.021-3.6668-.0817-4.9465-.0607-1.2797-.264-2.1487-.5633-2.9117-.3084-.7889-.72-1.4568-1.3876-2.1228C21.2982 1.33 20.628.9208 19.8378.6165 19.074.321 18.2017.1197 16.9244.0645 15.6471.0093 15.236-.005 11.977.0014 8.718.0076 8.31.0215 7.0301.0839m.1402 21.6932c-1.17-.0509-1.8053-.2453-2.2287-.408-.5606-.216-.96-.4771-1.3819-.895-.422-.4178-.6811-.8186-.9-1.378-.1644-.4234-.3624-1.058-.4171-2.228-.0595-1.2645-.072-1.6442-.079-4.848-.007-3.2037.0053-3.583.0607-4.848.05-1.169.2456-1.805.408-2.2282.216-.5613.4762-.96.895-1.3816.4188-.4217.8184-.6814 1.3783-.9003.423-.1651 1.0575-.3614 2.227-.4171 1.2655-.06 1.6447-.072 4.848-.079 3.2033-.007 3.5835.005 4.8495.0608 1.169.0508 1.8053.2445 2.228.408.5608.216.96.4754 1.3816.895.4217.4194.6816.8176.9005 1.3787.1653.4217.3617 1.056.4169 2.2263.0602 1.2655.0739 1.645.0796 4.848.0058 3.203-.0055 3.5834-.061 4.848-.051 1.17-.245 1.8055-.408 2.2294-.216.5604-.4763.96-.8954 1.3814-.419.4215-.8181.6811-1.3783.9-.4224.1649-1.0577.3617-2.2262.4174-1.2656.0595-1.6448.072-4.8493.079-3.2045.007-3.5825-.006-4.848-.0608M16.953 5.5864A1.44 1.44 0 1 0 18.39 4.144a1.44 1.44 0 0 0-1.437 1.4424M5.8385 12.012c.0067 3.4032 2.7706 6.1557 6.173 6.1493 3.4026-.0065 6.157-2.7701 6.1506-6.1733-.0065-3.4032-2.771-6.1565-6.174-6.1498-3.403.0067-6.156 2.771-6.1496 6.1738M8 12.0077a4 4 0 1 1 4.008 3.9921A3.9996 3.9996 0 0 1 8 12.0077'

export const TikTokLogo: React.FC<{ size?: number }> = ({ size = 18 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <path d={TIKTOK_PATH} fill="#25F4EE" transform="translate(-0.8 -0.6)" />
    <path d={TIKTOK_PATH} fill="#FE2C55" transform="translate(0.8 0.6)" />
    <path d={TIKTOK_PATH} fill="#FFFFFF" />
  </svg>
)
export const InstagramLogo: React.FC<{ size?: number; color?: string }> = ({ size = 18, color = '#FFFFFF' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={INSTAGRAM_PATH} fill={color} /></svg>
)
export const GoogleLogo: React.FC<{ size?: number }> = ({ size = 18 }) => (
  <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
    <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
  </svg>
)

const LABEL: Record<Platform, string> = {
  tiktok: 'Log in with TikTok',
  instagram: 'Log in with Instagram',
  youtube: 'Sign in with Google',
}
const RECONNECT_LABEL: Record<Platform, string> = {
  tiktok: 'Reconnect TikTok', instagram: 'Reconnect Instagram', youtube: 'Reconnect with Google',
}
const ANOTHER_LABEL: Record<Platform, string> = {
  tiktok: 'Add another TikTok account', instagram: 'Add another Instagram account', youtube: 'Add another channel with Google',
}

export interface BrandLoginButtonProps {
  platform: Platform
  configured: boolean
  /** "login" (default), "another" (add one more account) or "reconnect" */
  intent?: 'login' | 'another' | 'reconnect'
  size?: 'large' | 'middle' | 'small'
  block?: boolean
  onNavigate?: () => void
}

export function startLogin(platform: Platform): void {
  window.location.assign(socialApi.connectUrl(platform))
}

const BrandLoginButton: React.FC<BrandLoginButtonProps> = ({ platform, configured, intent = 'login', size = 'large', block, onNavigate }) => {
  const navigate = useNavigate()
  const label = intent === 'reconnect' ? RECONNECT_LABEL[platform]
    : intent === 'another' ? ANOTHER_LABEL[platform] : LABEL[platform]
  const onClick = () => {
    onNavigate?.()
    if (configured) startLogin(platform)
    else navigate(`/publishing?tab=apps&setup=${platform}`)
  }
  const title = configured
    ? (platform === 'youtube' ? 'Connect a YouTube channel with your Google account' : undefined)
    : 'This platform needs its developer app keys first: opens Developer apps'
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      data-testid={`login-${platform}`}
      data-configured={configured ? 'true' : 'false'}
      className={`ac-brand-btn ac-brand-btn--${platform} ac-brand-btn--${size}${block ? ' ac-brand-btn--block' : ''}`}
    >
      <span className="ac-brand-btn__icon">
        {platform === 'tiktok' && <TikTokLogo size={size === 'small' ? 15 : 18} />}
        {platform === 'instagram' && <InstagramLogo size={size === 'small' ? 15 : 18} />}
        {platform === 'youtube' && <GoogleLogo size={size === 'small' ? 15 : 18} />}
      </span>
      <span className="ac-brand-btn__label">{label}</span>
      {!configured && <span className="ac-brand-btn__badge">Set up</span>}
    </button>
  )
}

export default BrandLoginButton
