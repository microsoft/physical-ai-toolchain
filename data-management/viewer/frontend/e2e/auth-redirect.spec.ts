import { once } from 'node:events'
import { readFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'

import type * as Msal from '@azure/msal-browser'
import { expect, type Page, test } from '@playwright/test'

const require = createRequire(import.meta.url)
const sdk = readFileSync(join(dirname(require.resolve('@azure/msal-browser')), 'msal-browser.js'))

async function acquireThroughIframe(page: Page, origin: string) {
  const iframeBridgeTimeout = ['localhost', '127.0.0.1'].includes(new URL(origin).hostname)
    ? 1000
    : 10000
  const authorizeRequests: URL[] = []
  const unexpectedRequests: string[] = []
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) {
      unexpectedRequests.push(url.origin)
      await route.abort()
    } else if (url.pathname === '/__auth-test/parent') {
      await route.fulfill({
        contentType: 'text/html',
        body: '<!doctype html><script src="/__auth-test/msal.js"></script>',
      })
    } else if (url.pathname === '/__auth-test/msal.js') {
      await route.fulfill({ contentType: 'text/javascript', body: sdk })
    } else if (url.pathname === '/__auth-test/authorize') {
      authorizeRequests.push(url)
      const redirect = new URL(url.searchParams.get('redirect_uri')!)
      redirect.hash = new URLSearchParams({
        error: 'login_required',
        error_description: 'Synthetic identity response',
        state: url.searchParams.get('state')!,
      }).toString()
      // Script navigation lets Playwright intercept the subsequent document;
      // intercepted HTTP redirects bypass routing on their destination.
      await route.fulfill({
        contentType: 'text/html',
        body: `<script>location.replace(${JSON.stringify(redirect.href)})</script>`,
      })
    } else {
      await route.fallback()
    }
  })
  await page.goto(`${origin}/__auth-test/parent`)
  const result = await page.evaluate(
    async ({ origin, iframeBridgeTimeout }) => {
      const msal = (window as Window & { msal: typeof Msal }).msal
      const client = new msal.PublicClientApplication({
        auth: {
          clientId: '00000000-0000-0000-0000-000000000001',
          authority: 'https://login.microsoftonline.com/common',
          knownAuthorities: ['login.microsoftonline.com'],
          redirectUri: `${origin}/redirect.html`,
          authorityMetadata: JSON.stringify({
            authorization_endpoint: `${origin}/__auth-test/authorize`,
            token_endpoint: `${origin}/__auth-test/token`,
            issuer: 'https://login.microsoftonline.com/common/v2.0',
            jwks_uri: `${origin}/__auth-test/keys`,
            end_session_endpoint: `${origin}/__auth-test/logout`,
          }),
        },
        system: { iframeBridgeTimeout },
      })
      await client.initialize()
      try {
        await client.acquireTokenSilent({
          scopes: ['openid'],
          cacheLookupPolicy: msal.CacheLookupPolicy.Skip,
          account: {
            homeAccountId: 'synthetic.synthetic',
            localAccountId: 'synthetic',
            environment: 'login.microsoftonline.com',
            tenantId: 'synthetic',
            username: 'synthetic@example.test',
          },
        })
        return { name: 'unexpected_success', errorCode: '', subError: '' }
      } catch (error) {
        if (!(error instanceof msal.AuthError)) throw error
        return {
          name: error.name,
          errorCode: error.errorCode,
          subError: error.subError,
        }
      }
    },
    { origin, iframeBridgeTimeout },
  )
  expect(unexpectedRequests).toEqual([])
  expect(authorizeRequests).toHaveLength(1)
  expect(authorizeRequests[0].searchParams.get('state')).toBeTruthy()
  expect(authorizeRequests[0].searchParams.get('redirect_uri')).toBe(`${origin}/redirect.html`)
  return result
}

test('built bridge delivers a real SDK silent-iframe interaction-required response', async ({
  page,
  baseURL,
}) => {
  expect(await acquireThroughIframe(page, baseURL!)).toEqual({
    name: 'InteractionRequiredAuthError',
    errorCode: 'login_required',
    subError: '',
  })
})

for (const hash of ['', '#code=synthetic-code&state=malformed-state']) {
  test(`bridge fails visibly without a valid response: ${hash || 'missing response'}`, async ({
    page,
  }) => {
    const errors: string[] = []
    const apiRequests: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text())
    })
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(request.url())
    })
    await page.goto(`/redirect.html${hash}`)
    await expect(page.getByRole('status')).toHaveText(
      'Authentication could not complete. Return to the application and try signing in again.',
    )
    expect(errors.some((message) => message.startsWith('Authentication redirect failed'))).toBe(
      true,
    )
    expect(errors.join('\n')).not.toMatch(/synthetic-code|malformed-state/)
    expect(apiRequests).toEqual([])
    expect(new URL(page.url()).hash).toBe('')
    await expect(page.locator('#root')).toHaveCount(0)
  })
}

test('strict framing headers block the bridge even when its script exists', async ({
  page,
  baseURL,
}) => {
  await page.route('**/redirect.html*', async (route) => {
    const response = await route.fetch()
    const headers = response.headers()
    headers['content-security-policy'] = headers['content-security-policy'].replace(
      "frame-ancestors 'self'",
      "frame-ancestors 'none'",
    )
    headers['x-frame-options'] = 'DENY'
    await route.fulfill({ response, headers })
  })
  expect(await acquireThroughIframe(page, baseURL!)).toEqual({
    name: 'BrowserAuthError',
    errorCode: 'timed_out',
    subError: 'redirect_bridge_timeout',
  })
})

test('same-origin framing without the bridge cannot deliver the SDK response', async ({
  page,
  baseURL,
}) => {
  await page.route('**/redirect.html*', async (route) => {
    const response = await route.fetch()
    await route.fulfill({ response, body: '<!doctype html><title>Missing bridge control</title>' })
  })
  expect(await acquireThroughIframe(page, baseURL!)).toEqual({
    name: 'BrowserAuthError',
    errorCode: 'timed_out',
    subError: 'redirect_bridge_timeout',
  })
})

for (const { parent, child } of [
  { parent: 'same-origin', child: '/' },
  { parent: 'cross-origin', child: '/redirect.html' },
]) {
  test(`${parent} parent cannot frame ${child}`, async ({ page, baseURL }) => {
    const body = `<!doctype html><iframe src="${baseURL}${child}"></iframe>`
    const isLoopback = ['localhost', '127.0.0.1'].includes(new URL(baseURL!).hostname)
    // Real loopback responses avoid Chrome's local-network check on mocked documents.
    const server =
      parent === 'cross-origin' && isLoopback
        ? createServer((_request, response) => {
            response.writeHead(200, { 'Content-Type': 'text/html' })
            response.end(body)
          })
        : undefined
    const blockedFrames: string[] = []
    page.on('console', (message) => {
      if (message.text().includes('frame-ancestors')) blockedFrames.push(message.text())
    })
    try {
      let origin = baseURL!
      if (server) {
        server.listen(0, '127.0.0.1')
        await once(server, 'listening')
        const address = server.address()
        if (!address || typeof address === 'string') {
          throw new Error('Frame parent server did not bind a TCP port')
        }
        origin = `http://127.0.0.1:${address.port}`
      } else {
        if (parent === 'cross-origin') {
          const alternateOrigin = new URL(baseURL!)
          alternateOrigin.hostname = 'frame-parent.example.test'
          origin = alternateOrigin.origin
        }
        await page.route(`${origin}/__auth-test/frame`, (route) =>
          route.fulfill({ contentType: 'text/html', body }),
        )
      }
      await page.goto(`${origin}/__auth-test/frame`)
      await expect.poll(() => blockedFrames.length).toBeGreaterThan(0)
      expect(page.frames().some((frame) => frame.url() === `${baseURL}${child}`)).toBe(false)
    } finally {
      if (server?.listening) {
        await new Promise<void>((resolve, reject) => {
          server.close((error) => (error ? reject(error) : resolve()))
        })
      }
    }
  })
}
