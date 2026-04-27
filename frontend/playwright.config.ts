import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright config for end-to-end tests.
 *
 * Local dev: `npm run dev` keeps a vite server on http://localhost:3000.
 *            Run `npm run e2e` in another terminal.
 * CI:        the workflow boots backend in docker + `vite preview` on 3000.
 *
 * The dev-mode auto-login (App.tsx) puts every test inside the app
 * pre-authenticated, so we don't need to script the login form.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,         // shared backend state — keep deterministic
  forbidOnly: !!process.env.CI, // .only() in committed test = fail CI
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
