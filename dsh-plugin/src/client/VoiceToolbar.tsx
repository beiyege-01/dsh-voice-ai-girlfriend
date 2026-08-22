/**
 * VoiceToolbar: the voice plugin's composer controls as ONE row ABOVE the
 * input card (conversation.input.dock) instead of inside the card's tool row.
 *
 * Previously each control (mic / toggles) registered its own
 * `conversation.input.left` entry; that made the tool row too wide and pushed
 * the model select onto a second line. Grouping them into a single dock row
 * keeps the input card's own row intact and lets this row control its own
 * compact spacing.
 *
 * BalanceBadge stays in `conversation.composer.dock` (the bottom stats line).
 */
import { memo } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { VoiceInjected } from './contract.ts'
import { MicButton } from './MicButton.tsx'
import { VoiceToggle } from './VoiceToggle.tsx'
import { DigitalHumanToggle } from './DigitalHumanToggle.tsx'
import { QqPushToggle } from './QqPushToggle.tsx'
import { CompanionToggle } from './CompanionToggle.tsx'
import { BusyToggle } from './BusyToggle.tsx'
import { BridgeStatus } from './BridgeStatus.tsx'
import { PersonaToggle } from './PersonaToggle.tsx'
import css from './VoiceToolbar.module.css'

/** Full props: composer.dock runtime share + `voice` locale seat + injected face. */
export type VoiceToolbarProps =
  PropsRuntime<'conversation.input.dock'> & PropsLocale<'voice'> & VoiceInjected

/**
 * @param props - framework runtime + locale + injected sendText/speaker/etc.
 */
export const VoiceToolbar = memo(function VoiceToolbar(props: VoiceToolbarProps) {
  // Each child is a slot component whose PropsRuntime seat differs from this
  // dock seat; at runtime they only read `t` + the injected face fields this
  // toolbar carries, so pass the whole props through with an explicit cast.
  const micProps = props as unknown as React.ComponentProps<typeof MicButton>
  const voiceProps = props as unknown as React.ComponentProps<typeof VoiceToggle>
  const dhProps = props as unknown as React.ComponentProps<typeof DigitalHumanToggle>
  const qqProps = props as unknown as React.ComponentProps<typeof QqPushToggle>
  const companionProps = props as unknown as React.ComponentProps<typeof CompanionToggle>
  const busyProps = props as unknown as React.ComponentProps<typeof BusyToggle>
  const bridgeProps = props as unknown as React.ComponentProps<typeof BridgeStatus>
  const personaProps = props as unknown as React.ComponentProps<typeof PersonaToggle>
  return (
    <div className={css.toolbar}>
      <BridgeStatus {...bridgeProps} />
      <PersonaToggle {...personaProps} />
      <MicButton {...micProps} />
      <VoiceToggle {...voiceProps} />
      <DigitalHumanToggle {...dhProps} />
      <QqPushToggle {...qqProps} />
      <CompanionToggle {...companionProps} />
      <BusyToggle {...busyProps} />
    </div>
  )
})
