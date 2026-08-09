// REFERENCE a11y check (SYSTEM.md §5.4, grill-me Q8). Copy to <project>/tests/a11y/ at instantiation
// and adapt the routes. This is the deterministic accessibility gate — axe + keyboard + reduced-motion
// — so a11y is PROVEN in CI, not asserted by the builder. The ui-ux-reviewer judges taste, not this.
//
// Deps (project): @playwright/test, @axe-core/playwright.
// Wired by the `a11y` check in the stack manifest: `playwright test tests/a11y`.

import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// The user-facing routes that must stay accessible. Add every flagship surface (e.g. the carousel,
// shareable cards) — an untested route is an unguarded route.
const ROUTES = ['/', '/feed'];

for (const route of ROUTES) {
  test(`${route} has no serious/critical axe violations`, async ({ page }) => {
    await page.goto(route);
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa'])
      .analyze();
    const serious = results.violations.filter((v) => ['serious', 'critical'].includes(v.impact ?? ''));
    expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([]);
  });

  test(`${route} is keyboard operable (focus reaches interactive elements)`, async ({ page }) => {
    await page.goto(route);
    await page.keyboard.press('Tab');
    const active = await page.evaluate(() => document.activeElement?.tagName ?? null);
    expect(active, 'Tab should move focus to an interactive element').not.toBeNull();
    expect(active).not.toBe('BODY');
  });
}

test('respects prefers-reduced-motion (no long transitions/animations when reduced)', async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: 'reduce' });
  const page = await context.newPage();
  await page.goto(ROUTES[0]);
  const longAnimations = await page.evaluate(() =>
    [...document.querySelectorAll('*')].filter((el) => {
      const s = getComputedStyle(el);
      const dur = parseFloat(s.animationDuration) + parseFloat(s.transitionDuration);
      return dur > 0.1; // seconds — under reduced-motion, motion should be effectively off
    }).length,
  );
  expect(longAnimations, 'reduced-motion users should not get >100ms animations/transitions').toBe(0);
  await context.close();
});
