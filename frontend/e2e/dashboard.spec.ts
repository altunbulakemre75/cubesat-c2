import { expect, test } from '@playwright/test'

/**
 * E2E happy-path: dashboard renders with at least one satellite + the
 * 3D globe loads without a runtime error.
 *
 * This test is the safety net we wished we had during the live-bug
 * sprint — the dashboard would have crashed in front of these checks.
 */

test.describe('Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    // Bubble React runtime errors up so an unhandled exception fails the
    // test instead of just rendering an error screen quietly.
    page.on('pageerror', (err) => {
      throw new Error(`Uncaught page error: ${err.message}`)
    })
    await page.goto('/')
  })

  test('loads, hides login screen, shows the satellite list', async ({ page }) => {
    // Auto-login should land us straight on the dashboard.
    await expect(page.getByText(/CubeSat C2/i)).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText(/SATELLITES/i)).toBeVisible()
    // At least one satellite card visible — the simulator publishes 3.
    await expect(page.getByText(/CUBESAT[123]/).first()).toBeVisible({ timeout: 15_000 })
  })

  test('renders Cesium globe canvas without crashing', async ({ page }) => {
    await expect(page.getByText(/3D ORBIT VISUALIZATION/i)).toBeVisible({ timeout: 15_000 })
    // Cesium injects a <canvas> element into its container. Wait for one.
    const canvas = page.locator('canvas').first()
    await expect(canvas).toBeVisible({ timeout: 30_000 })
  })

  test('alerts panel shows either alerts or "no active alerts"', async ({ page }) => {
    await expect(
      page.getByRole('heading', { name: /Active Alerts/i }),
    ).toBeVisible({ timeout: 15_000 })
    // Whatever happens, the panel must NOT throw — earlier this was where
    // the toUpperCase() crash brought down the whole page.
    const possibleStates = page.getByText(/No active alerts|ANOMALY|FDIR/i).first()
    await expect(possibleStates).toBeVisible({ timeout: 15_000 })
  })

  test('SatNOGS observations panel mounts', async ({ page }) => {
    await expect(page.getByText(/SATNOGS OBSERVATIONS/i)).toBeVisible({ timeout: 15_000 })
  })

  test('navigation: Commands page loads without errors', async ({ page }) => {
    await page.getByRole('link', { name: /Commands/i }).click()
    // The Commands header should render. We don't assert specific commands —
    // the table may be empty in CI.
    await expect(page).toHaveURL(/\/commands/)
  })
})
