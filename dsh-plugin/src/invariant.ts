/**
 * Package-owned invariant companion for `@deepseek-ai/dsh-client-ui-voice`.
 * @module @deepseek-ai/dsh-client-ui-voice/invariant
 */

/* jscpd:ignore-start */
import type { Context } from '@deepseek-ai/cordis'
import type { InvariantInstaller } from '@deepseek-ai/dsh-invariants'

const PACKAGE_NAME = '@deepseek-ai/dsh-client-ui-voice'

/** Cordis companion plugin name. */
export const name = 'client-ui-voice-invariant'
/** Service required before the companion can reserve package ownership. */
export const inject = ['invariants']

/**
 * No runtime invariant: slot registrations and the bridge HTTP calls are
 * effects owned by their respective registries and the local network service;
 * there is no host-side data invariant to assert.
 */
const install: InvariantInstaller = () => {}

/**
 * Register this package's invariant companion.
 * @param ctx - Cordis context carrying the invariant service.
 * @returns The installed registration's disposer after setup succeeds.
 */
export const apply = (ctx: Context): Promise<() => void> =>
  Promise.resolve(ctx.invariants.register(PACKAGE_NAME, install))
/* jscpd:ignore-end */
