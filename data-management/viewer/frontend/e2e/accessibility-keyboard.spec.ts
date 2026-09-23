import type { Page, TestInfo } from '@playwright/test'
import { expect, test } from '@playwright/test'

import { attachJson, openEmptyViewer, openViewer } from './accessibility-fixture'

// cspell:words describedby keyshortcuts spinbutton

interface FocusExpectation {
  scope: string
  role: 'button' | 'link' | 'tab'
  name: string | RegExp
  description: string
}

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'summary',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

const emptyShellExpectations: FocusExpectation[] = [
  { scope: 'header', role: 'button', name: 'Dataset', description: 'Dataset' },
  { scope: 'header', role: 'button', name: 'Toggle Diagnostics', description: 'Diagnostics' },
  { scope: 'header', role: 'link', name: 'Help', description: 'Help' },
  { scope: 'header', role: 'link', name: 'Report problem', description: 'Report problem' },
]

const loadedPrimaryExpectations: FocusExpectation[] = [
  ...emptyShellExpectations,
  { scope: 'aside', role: 'button', name: 'REVIEWED', description: 'Filter REVIEWED' },
  {
    scope: 'aside',
    role: 'button',
    name: 'NEEDS_ATTENTION',
    description: 'Filter NEEDS_ATTENTION',
  },
  { scope: 'aside', role: 'button', name: /Episode 0/, description: 'Episode 0' },
  { scope: 'aside', role: 'button', name: /Episode 1/, description: 'Episode 1' },
  {
    scope: '[data-testid="workspace-top-bar"]',
    role: 'button',
    name: 'Export',
    description: 'Export',
  },
  {
    scope: '[data-testid="workspace-top-bar"]',
    role: 'button',
    name: 'Save & Next Episode',
    description: 'Save and next',
  },
  {
    scope: '[data-testid="workspace-top-bar"]',
    role: 'tab',
    name: 'Trajectory Viewer',
    description: 'Trajectory tab',
  },
]

async function tagExpectedStops(page: Page, expectations: FocusExpectation[]) {
  for (let index = 0; index < expectations.length; index += 1) {
    const expectation = expectations[index]
    const locator = page.locator(expectation.scope).getByRole(expectation.role, {
      name: expectation.name,
      exact: typeof expectation.name === 'string',
    })
    await expect(locator, expectation.description).toHaveCount(1)
    await locator.evaluate((element, key) => {
      ;(element as HTMLElement).dataset.a11yFocusKey = key
    }, `focus-${index}`)
  }
}

async function scopedRuntimeKeys(page: Page, scopes: string[]) {
  return page.evaluate(
    ({ selector, scopeSelector }) =>
      [...document.querySelectorAll<HTMLElement>(selector)]
        .filter((element) =>
          element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }),
        )
        .filter((element) => element.closest(scopeSelector))
        .filter((element) => element.getAttribute('role') !== 'tablist')
        .filter(
          (element) =>
            element.tabIndex >= 0 ||
            (element.getAttribute('role') === 'tab' &&
              element.getAttribute('aria-selected') === 'true'),
        )
        .map((element) => element.dataset.a11yFocusKey ?? null),
    { selector: focusableSelector, scopeSelector: scopes.join(',') },
  )
}

async function traceDirection(
  page: Page,
  expectations: FocusExpectation[],
  key: 'Tab' | 'Shift+Tab',
) {
  const expectedKeys = expectations.map((_, index) => `focus-${index}`)
  const observed: string[] = []
  for (let step = 0; step < expectedKeys.length * 3 + 20; step += 1) {
    await page.keyboard.press(key)
    let activeKey = await page.evaluate(
      () => (document.activeElement as HTMLElement | null)?.dataset.a11yFocusKey ?? null,
    )
    const activeRole = await page.evaluate(() => document.activeElement?.getAttribute('role'))
    if (!activeKey && activeRole === 'tablist') {
      await expect
        .poll(
          () =>
            page.evaluate(
              () => (document.activeElement as HTMLElement | null)?.dataset.a11yFocusKey ?? null,
            ),
          { timeout: 500 },
        )
        .not.toBeNull()
      activeKey = await page.evaluate(
        () => (document.activeElement as HTMLElement | null)?.dataset.a11yFocusKey ?? null,
      )
    }
    if (activeKey && !observed.includes(activeKey)) observed.push(activeKey)
    if (expectedKeys.every((candidate) => observed.includes(candidate))) break
  }
  return observed
}

async function assertBidirectionalOrder(
  page: Page,
  testInfo: TestInfo,
  name: string,
  expectations: FocusExpectation[],
) {
  await tagExpectedStops(page, expectations)
  const scopes = [...new Set(expectations.map((expectation) => expectation.scope))]
  expect(await scopedRuntimeKeys(page, scopes)).toEqual(
    expectations.map((_, index) => `focus-${index}`),
  )

  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  const forward = await traceDirection(page, expectations, 'Tab')
  expect(forward).toEqual(expectations.map((_, index) => `focus-${index}`))

  const lastKey = `focus-${expectations.length - 1}`
  await page.locator(`[data-a11y-focus-key="${lastKey}"]`).focus()
  const reverse = [lastKey, ...(await traceDirection(page, expectations, 'Shift+Tab'))].filter(
    (key, index, values) => values.indexOf(key) === index,
  )
  expect(reverse).toEqual(expectations.map((_, index) => `focus-${index}`).reverse())

  await attachJson(testInfo, `${name}-tab-flow`, {
    expectations: expectations.map((expectation) => expectation.description),
    forward,
    reverse,
  })
}

test('V02 V03 and V04 empty shell follows authored bidirectional tab order', async ({
  page,
}, testInfo) => {
  const errors = await openEmptyViewer(page, testInfo)
  await assertBidirectionalOrder(page, testInfo, 'empty-shell', emptyShellExpectations)
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V02 V03 and V05 loaded primary controls follow authored bidirectional tab order', async ({
  page,
}, testInfo) => {
  const errors = await openViewer(page, testInfo)
  await assertBidirectionalOrder(page, testInfo, 'loaded-primary', loadedPrimaryExpectations)
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V05 V06 V08 and V09 composite controls support keyboard operation and exit', async ({
  page,
}, testInfo) => {
  const errors = await openViewer(page, testInfo, { trajectoryVariables: 'joints' })
  const playButton = page.getByRole('button', { name: /^(Play|Pause) playback$/ })
  if ((await playButton.getAttribute('aria-label')) === 'Pause playback') await playButton.click()

  const speedButton = page.getByRole('button', { name: /^Playback speed:/ })
  await speedButton.focus()
  await speedButton.press('Enter')
  await expect(page.getByRole('spinbutton', { name: 'Custom playback speed' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(speedButton).toBeFocused()

  const trajectoryTab = page.getByRole('tab', { name: 'Trajectory Viewer' })
  const analyzerTab = page.getByRole('tab', { name: 'Episode Analyzer' })
  await trajectoryTab.focus()
  await page.keyboard.press('ArrowRight')
  await expect(analyzerTab).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowLeft')
  await expect(trajectoryTab).toHaveAttribute('aria-selected', 'true')

  const customLabel = page.getByPlaceholder('Add custom label...')
  await customLabel.focus()
  const playbackNameBefore = await playButton.getAttribute('aria-label')
  await customLabel.press(' ')
  await expect(playButton).toHaveAttribute('aria-label', playbackNameBefore ?? 'Play playback')

  const shoulderJoint = page
    .getByTestId('joint-group-arm')
    .getByRole('button', { name: 'shoulder_joint', exact: true })
  await expect(shoulderJoint).toHaveAttribute('aria-keyshortcuts', /Alt\+ArrowRight/)
  const initialPressed = await shoulderJoint.getAttribute('aria-pressed')
  expect(initialPressed).toMatch(/^(true|false)$/)
  await shoulderJoint.press(' ')
  await expect(shoulderJoint).toHaveAttribute(
    'aria-pressed',
    initialPressed === 'true' ? 'false' : 'true',
  )
  await shoulderJoint.press(' ')
  await expect(shoulderJoint).toHaveAttribute('aria-pressed', initialPressed ?? 'true')
  const jointOrder = async () =>
    page
      .locator('[data-joint-chip]')
      .evaluateAll((elements) => elements.map((element) => element.textContent?.trim()))
  expect(await jointOrder()).toEqual(['shoulder_joint', 'target_joint'])
  await shoulderJoint.focus()
  await page.keyboard.press('Alt+ArrowRight')
  expect(await jointOrder()).toEqual(['target_joint', 'shoulder_joint'])
  await expect(shoulderJoint).toBeFocused()

  await page.getByRole('button', { name: 'Edit joint defaults' }).click()
  const editJointLabel = page.getByRole('button', { name: 'Edit joint label' }).first()
  await editJointLabel.focus()
  await expect(editJointLabel).toBeFocused()
  await expect(editJointLabel).toHaveCSS('opacity', '1')
  await page.keyboard.press('Escape')

  await attachJson(testInfo, 'composite-keyboard-trace', {
    journeys: ['V05', 'V06', 'V08', 'V09'],
    operations: [
      'speed popover exit',
      'tab arrows',
      'editable-field shortcut isolation',
      'joint selected state and focused action reveal',
      'joint reorder',
    ],
  })
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V07 V10 V12 and V16 task controls are keyboard operable', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)

  const displaySettings = page.getByRole('button', { name: 'Display Settings' })
  await displaySettings.focus()
  await displaySettings.press('Enter')
  await expect(displaySettings).toHaveAttribute('aria-expanded', 'true')
  const brightness = page
    .getByTestId('trajectory-playback-group-panel')
    .getByRole('slider', { name: 'Brightness' })
  const initialBrightness = await brightness.inputValue()
  await brightness.focus()
  await brightness.press('ArrowRight')
  expect(await brightness.inputValue()).not.toBe(initialBrightness)

  const addSubtask = page.getByTitle('Add subtask at current frame')
  await addSubtask.focus()
  await addSubtask.press('Enter')
  const subtaskLabel = page.getByTestId('trajectory-graph-panel').getByPlaceholder('Label')
  await expect(subtaskLabel).toHaveValue('Subtask 1')
  await subtaskLabel.fill('Inspect grasp')
  await expect(subtaskLabel).toHaveValue('Inspect grasp')

  const useInstruction = page.getByRole('button', { name: 'Use as Instruction' })
  await useInstruction.scrollIntoViewIfNeeded()
  await useInstruction.focus()
  await useInstruction.press('Enter')
  const taskInstruction = page.locator('#lang-instruction')
  await taskInstruction.scrollIntoViewIfNeeded()
  await taskInstruction.fill('Move the geometric block to the target')
  await expect(taskInstruction).toHaveAttribute('lang', 'en')
  const language = page.locator('#lang-language')
  await language.scrollIntoViewIfNeeded()
  await language.fill('bad_tag')
  await expect(language).toHaveAttribute('aria-invalid', 'true')
  const languageGuidance = page.locator('#lang-language-guidance')
  await expect(languageGuidance).toContainText('Use a BCP 47 language tag')
  await expect(language).toHaveAttribute('aria-describedby', 'lang-language-guidance')
  await expect(page.getByRole('button', { name: 'Save Annotation' })).toBeDisabled()
  await language.fill('FR-fr')
  await language.press('Tab')
  await expect(language).toHaveValue('fr-FR')
  await expect(language).toHaveAttribute('aria-invalid', 'false')
  await expect(languageGuidance).toBeHidden()
  await expect(page.getByRole('button', { name: 'Save Annotation' })).toBeEnabled()

  const blendFactor = page.getByRole('spinbutton', { name: 'Interpolation blend factor' })
  await blendFactor.focus()
  await blendFactor.press('ArrowUp')
  await expect(blendFactor).not.toHaveValue('0.5')

  await attachJson(testInfo, 'remaining-viewer-keyboard-tasks', {
    journeys: ['V07', 'V10', 'V12', 'V16'],
    operations: [
      'display adjustment',
      'subtask creation and rename',
      'invalid and corrected language metadata guidance',
      'frame insertion blend adjustment',
    ],
  })
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})
