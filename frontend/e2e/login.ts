import { expect, type Page } from '@playwright/test'

// Credentials of the user created by e2e/seed_user.py (see the CI e2e job).
export const E2E_USERNAME = process.env.E2E_USERNAME ?? 'e2e'
export const E2E_PASSWORD = process.env.E2E_PASSWORD ?? ''

// Production builds have no dev auto-login, so every test signs in through
// the real login form.
export async function login(page: Page): Promise<void> {
  await page.goto('/login')
  await page.getByPlaceholder('admin').fill(E2E_USERNAME)
  await page.getByPlaceholder('••••••••').fill(E2E_PASSWORD)
  await page.getByRole('button', { name: /giriş|sign in|log in/i }).click()
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 })
}
