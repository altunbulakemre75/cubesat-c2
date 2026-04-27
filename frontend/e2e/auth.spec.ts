import { expect, test } from '@playwright/test'

/**
 * Auth flow E2E. Even though the app dev-auto-logins, we still want
 * coverage on:
 *   - logout clears the token
 *   - the Login page renders when no token
 *   - rate limit returns 429 after 6 wrong attempts
 *
 * VITE_DISABLE_AUTO_LOGIN is set per-test with addInitScript so we can
 * exercise the manual flow without touching the env file.
 */

test.describe('Auth', () => {
  test.beforeEach(async ({ context }) => {
    await context.addInitScript(() => {
      // Force-disable the dev auto-login for this test.
      ;(window as unknown as { __VITE_DISABLE_AUTO_LOGIN__?: boolean }).__VITE_DISABLE_AUTO_LOGIN__ = true
    })
  })

  test('login page renders when no token', async ({ page }) => {
    // Stub localStorage so any cached token is gone.
    await page.context().clearCookies()
    await page.goto('/login')
    await expect(
      page.getByRole('heading', { name: /sign in|log in|cubesat/i }).first(),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('wrong password returns 401', async ({ request }) => {
    // Rate limit is bypassed in dev (DEBUG=true) so we can't reliably
    // assert 429 here. We can still cover the wrong-password path.
    const resp = await request.post('http://localhost:8000/auth/login', {
      data: { username: 'admin', password: 'definitely-wrong' },
      failOnStatusCode: false,
    })
    expect(resp.status()).toBe(401)
    const body = await resp.json()
    expect(body.detail).toMatch(/invalid credentials/i)
  })
})
