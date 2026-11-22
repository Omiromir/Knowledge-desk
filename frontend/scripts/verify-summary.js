async (page) => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message)
  }
  const origin = 'http://127.0.0.1:5173'
  const endpoint = 'http://127.0.0.1:8000/ai/summary'
  const question = 'Помогите выбрать курс Python с вечерними занятиями.'
  const summary = 'Нужна консультация по выбору вечернего курса Python.'
  const failures = [
    [504, 'provider_timeout', 'Модель не ответила за 15 секунд. Попробуйте ещё раз.'],
    [429, 'provider_rate_limit', 'Модель временно ограничила запросы. Повторите чуть позже.'],
    [502, 'provider_unavailable', 'Модель временно недоступна. Попробуйте ещё раз.'],
    [502, 'invalid_summary', 'Модель вернула некорректное резюме. Попробуйте ещё раз.'],
    [429, 'local_limit', 'Исчерпан лимит подготовки резюме для этой сессии сервера.'],
  ]
  let requests = 0
  let release
  const wait = new Promise((resolve) => { release = resolve })
  let releaseRetry
  const retryWait = new Promise((resolve) => { releaseRetry = resolve })

  await page.route(endpoint, async (route) => {
    requests += 1
    if (requests === 1) await wait
    if (requests === 4) await retryWait
    const [status, code] = failures[requests - 3] ?? [200]
    await route.fulfill({
      status,
      headers: { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Credentials': 'true' },
      json: status === 200
        ? { summary, request_type: 'consultation', needs_human: true }
        : { detail: { code, message: 'Test response' } },
    })
  })

  try {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.goto(`${origin}/?check=summary#requests?compose`)
    const input = page.getByLabel('Ваш вопрос')
    const block = page.getByRole('region', { name: 'Резюме заявки' })
    const button = block.getByRole('button')
    const status = block.getByRole('status')
    const draft = block.getByText(summary, { exact: true })
    assert(await button.isDisabled(), 'Blank input must be disabled')
    await input.fill('а'.repeat(4001))
    assert(await button.isDisabled(), 'Overlong input must be disabled')
    await input.fill(question)
    await button.focus()
    await page.keyboard.press('Enter')
    await status.getByText('Готовим резюме…').waitFor()
    assert(await button.isDisabled(), 'Pending button must be disabled')
    assert(await button.evaluate((element) => element === document.activeElement), 'Pending button must retain keyboard focus')
    await page.keyboard.press('Enter')
    await button.evaluate((element) => element.click())
    assert(requests === 1, 'Repeated activation must not send another request')

    const pages = block.locator('.desk-loader-page')
    assert(await pages.count() === 3, 'Summary must use the shared loading mark')
    assert(await pages.first().evaluate((element) => getComputedStyle(element).animationName) === 'desk-loader-pages', 'Loader must animate')
    const loadingArea = await block.locator('.is-loading').boundingBox()
    await page.screenshot({ path: 'output/playwright/summary-loading.png' })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    assert(await pages.evaluateAll((elements) => elements.every((element) => getComputedStyle(element).animationName === 'none')), 'Reduced motion must keep every page static')

    await input.fill(question + ' Уточнение.')
    release()
    await status.getByText('Готовим резюме…').waitFor({ state: 'hidden' })
    assert(!(await draft.isVisible()), 'A late response must stay hidden for changed input')
    await button.click()
    await draft.waitFor()
    const resultArea = await block.locator('.summary-output').boundingBox()
    assert(Math.abs(resultArea.y - loadingArea.y) < 1 && resultArea.height >= loadingArea.height - 1, 'Summary moved or shrank its result area')
    assert(await button.isDisabled(), 'An unchanged successful result must block regeneration')
    await button.evaluate((element) => element.click())
    assert(requests === 2, 'A ready draft must not send another request')

    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Summary must fit the viewport')
      if (width !== 320) await page.screenshot({ path: `output/playwright/summary-ready-${width}.png` })
    }

    await input.fill('Запрос на личную консультацию.')
    for (const [index, [, code, message]] of failures.entries()) {
      await button.click()
      if (index === 1) {
        const mark = button.locator('.desk-loader.is-compact')
        await mark.waitFor()
        assert(await button.isDisabled() && await block.getByText(failures[0][2], { exact: true }).isVisible(),
          'Pending retry must lock the button and keep the previous error')
        await button.press('Enter')
        await button.evaluate((element) => element.click())
        assert(requests === 4, 'A pending retry must block more requests')
        releaseRetry()
      }
      await block.getByRole('alert').getByText(message, { exact: true }).waitFor()
      assert(await input.inputValue() === 'Запрос на личную консультацию.', `${code} must preserve the draft`)
      assert(requests === index + 3, `${code} must not trigger automatic retries`)
      assert(await button.isEnabled() === (code !== 'local_limit'), `${code} has incorrect retry behavior`)
    }
    await input.fill('Ещё одно уточнение.')
    assert(await button.isDisabled(), 'Server limit must remain blocked after edits')
    await button.evaluate((element) => element.click())
    assert(requests === failures.length + 2, 'Blocked action must not retry the server')
    assert(await input.inputValue() === 'Ещё одно уточнение.', 'Errors must preserve the draft')
    assert(await page.getByRole('button', { name: 'Отправить заявку' }).isEnabled(), 'Summary error must not disable submission')
    return { result: 'passed', widths: [1440, 390, 320], mockRequests: requests }
  } finally {
    release()
    releaseRetry()
    await page.unroute(endpoint)
    await page.getByLabel('Ваш вопрос').fill('')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.reload()
  }
}
