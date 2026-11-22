async (page, capture = async () => {}) => {
  const assert = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = 'http://127.0.0.1:5173'
  const reduced = page.viewportSize().width < 500
  const record = { id: 'loader-request', name: 'Вера', question: 'Когда открывается запись?', status: 'new' }
  const document = { id: 'doc-0', title: 'Документ для проверки', text: 'Учебный текст', source: 'fixture.md' }
  let gate
  function hold(path, outcome = 'ready') {
    let release
    const finished = new Promise((resolve) => { release = resolve })
    gate = { path, outcome, finished, release, calls: 0 }
  }
  await page.emulateMedia({ reducedMotion: reduced ? 'reduce' : 'no-preference' })
  await page.route('http://127.0.0.1:8000/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const response = path === gate?.path ? gate : null
    if (response) {
      response.calls += 1
      await response.finished
    }
    if (response?.outcome === 'error') return route.fulfill({ status: 503, json: { detail: 'fixture' } })
    if (response?.outcome === 'retry-error') return route.fulfill({ status: 429, json: { detail: 'Повторите чуть позже.' } })
    if (response?.outcome === 'empty') return route.fulfill({ json: [] })
    if (path === '/auth/session') return route.fulfill({ json: { mode: 'supabase', member: {
      user_id: 'loader-reader', email: 'loader@example.test', role: 'reader',
    } } })
    if (path === '/documents') return route.fulfill({ json: [document] })
    if (path === '/documents/doc-0') return route.fulfill({ json: document })
    if (path === '/requests') return route.fulfill({ json: [record] })
    if (path === '/requests/loader-request') return route.fulfill({ json: record })
    if (path === '/requests/loader-request/delivery') return route.fulfill({ json: null })
    return route.fallback()
  })

  async function checkAppearance(target) {
    const reveal = await target.evaluate((element) => {
      const style = getComputedStyle(element)
      return { animation: style.animationName, delay: style.animationDelay, opacity: style.opacity }
    })
    assert(reduced ? reveal.animation === 'none' && reveal.opacity === '1' :
      reveal.animation === 'load-result-in' && reveal.delay === '0s', 'State must appear immediately and respect reduced motion')
  }

  async function checkReveal(target) {
    const { delay, duration, before, during, after } = await target.evaluate((element) => {
      const reveal = element.getAnimations().find((animation) => animation.animationName === 'desk-loader-reveal')
      const { delay, duration } = reveal.effect.getComputedTiming()
      reveal.pause()
      reveal.currentTime = delay - 1
      const before = Number(getComputedStyle(element).opacity)
      reveal.currentTime = delay + duration / 2
      const during = Number(getComputedStyle(element).opacity)
      reveal.currentTime = delay + duration + 1
      const after = Number(getComputedStyle(element).opacity)
      reveal.currentTime = delay + duration / 2
      return { delay, duration, before, during, after }
    })
    assert(delay === 180 && before === 0 && after === 1, 'Loader must stay invisible until the reveal delay')
    assert(reduced ? duration === 0 : during > 0 && during < 1, 'Loader fade must respect reduced motion')
  }

  async function check(title, fullPane) {
    const indicator = page.locator('.async-state.is-loading:visible').filter({ hasText: title })
    await indicator.waitFor()
    await checkReveal(indicator.getByRole('status'))
    const measurements = await indicator.evaluate((element) => {
      const area = element.getBoundingClientRect()
      const status = element.querySelector('[role="status"]').getBoundingClientRect()
      const front = element.querySelector('.desk-loader-page:last-child')
      const mark = front.parentElement
      const motion = front.getAnimations()[0]
      let moves = false
      if (motion) {
        const { duration, delay } = motion.effect.getComputedTiming()
        motion.pause()
        motion.currentTime = delay + duration * 4
        const rest = getComputedStyle(front).transform
        motion.currentTime = delay + duration * 4.48
        moves = rest !== getComputedStyle(front).transform
        motion.play()
      }
      return {
        centerX: Math.abs(status.x + status.width / 2 - area.x - area.width / 2),
        centerY: Math.abs(status.y + status.height / 2 - area.y - area.height / 2),
        height: area.height,
        viewport: innerHeight,
        decorative: mark.getAttribute('aria-hidden') === 'true' && mark.childElementCount === 3,
        loops: motion?.effect.getComputedTiming().iterations === Infinity,
        moves,
      }
    })
    assert(measurements.centerX < 2 && measurements.centerY < 2, `Loader is not centered: ${title}`)
    assert(!fullPane || measurements.height > measurements.viewport / 2, `Loader should fill the pane: ${title}`)
    assert(measurements.decorative && (reduced ? !measurements.moves && !measurements.loops :
      measurements.moves && measurements.loops), `Loader should keep moving while pending, except with reduced motion: ${title}`)
    return indicator
  }

  const cases = [
    ['#documents', '/auth/session', 'Проверяем вход…'],
    ['#documents', '/documents', 'Загружаем документы…'],
    ['#documents?id=doc-0', '/documents/doc-0', 'Открываем документ…'],
    ['#requests', '/requests', 'Загружаем заявки…'],
    ['#requests?id=loader-request', '/requests/loader-request', 'Загружаем заявку…'],
    ['#ask?chunk=fragment', '/ask/sources/fragment', 'Открываем источник…'],
    ['#ask?consultation=review', '/consultations/review', 'Загружаем черновик…'],
  ]
  try {
    hold('/documents')
    await page.goto(`${origin}/?check=quick-loader#documents`)
    const catalog = page.locator('.document-results')
    const waiting = catalog.locator('.is-loading').getByRole('status')
    await waiting.waitFor()
    const pendingCatalogBox = await catalog.boundingBox()
    const opacity = await waiting.evaluate((element) => {
      const reveal = element.getAnimations()[0]
      reveal.pause()
      reveal.currentTime = 0
      return Number(getComputedStyle(element).opacity)
    })
    assert(opacity === 0, 'Fast load should start with an invisible indicator')
    gate.release()
    await page.getByRole('link', { name: document.title, exact: true }).waitFor()
    assert(await waiting.count() === 0, 'Data must appear even if the reveal has not started')
    const readyCatalogBox = await catalog.boundingBox()
    assert(Math.abs(readyCatalogBox.height - pendingCatalogBox.height) < 1, 'The catalog shrank after loading')
    await checkAppearance(catalog.getByRole('navigation'))

    hold('/documents')
    const oldRow = await page.getByRole('link', { name: document.title, exact: true }).boundingBox()
    document.title = 'Документ после обновления'
    const refresh = page.getByRole('button', { name: 'Обновить список' })
    await refresh.press('Enter')
    const refreshing = refresh.locator('.desk-loader.is-compact')
    await refreshing.waitFor()
    await checkReveal(refreshing)
    gate.release()
    const refreshedRow = page.getByRole('link', { name: document.title, exact: true })
    await refreshedRow.waitFor()
    assert(Math.abs((await refreshedRow.boundingBox()).y - oldRow.y) < 1, 'The row moved during refresh')
    assert(await refreshing.count() === 0, 'A finished refresh must clear the loader immediately')

    const lists = [
      ['documents', 'Загружаем документы…', 'Документов пока нет', 'Не удалось загрузить документы', document.title],
      ['requests', 'Загружаем заявки…', 'Заявок пока нет', 'Список заявок недоступен', record.question],
    ]
    for (const [section, loadingText, emptyText, errorText, readyText] of lists) {
      for (const [outcome, title] of [['empty', emptyText], ['error', errorText]]) {
        hold(`/${section}`, outcome)
        await page.goto(`${origin}/?check=${section}-${outcome}#${section}`)
        const loader = await check(loadingText, true)
        const before = await loader.boundingBox()
        gate.release()
        const state = page.locator('.async-state.load-result:visible').filter({ hasText: title })
        await state.getByRole(outcome === 'error' ? 'alert' : 'status').waitFor()
        assert(await loader.count() === 0, 'The old loader must be removed at once')
        await checkAppearance(state)
        const after = await state.boundingBox()
        assert(['x', 'y', 'width', 'height'].every((field) => Math.abs(before[field] - after[field]) < 1),
          `The ${section} ${outcome} state moved or resized its loading area`)
        if (section === 'documents') await capture(`catalog-${outcome}`)

        if (outcome === 'error') {
          const retry = state.getByRole('button', { name: 'Повторить', exact: true })
          const buttonBefore = await retry.boundingBox()
          const alert = state.getByRole('alert')
          const originalMessage = await alert.textContent()
          hold(`/${section}`, 'retry-error')
          const requested = page.waitForRequest((request) => new URL(request.url()).pathname === `/${section}`)
          await retry.press('Enter')
          await requested
          const mark = retry.locator('.desk-loader.is-compact')
          await mark.waitFor()
          assert(await retry.isDisabled() && await retry.evaluate((element) => element === document.activeElement),
            'Retry must be blocked and keep keyboard focus')
          assert(await alert.isVisible() && await alert.textContent() === originalMessage && await loader.count() === 0,
            'The previous error must remain visible during retry')
          const buttonDuring = await retry.boundingBox()
          assert(['x', 'y', 'width', 'height'].every((field) => Math.abs(buttonBefore[field] - buttonDuring[field]) < 1),
            'The retry button moved or resized')
          assert(await mark.locator('.desk-loader-page').first().evaluate((page) => getComputedStyle(page).animationName) ===
            (reduced ? 'none' : 'desk-loader-pages'), 'The retry mark must respect reduced motion')
          await retry.press('Enter')
          await retry.evaluate((element) => element.click())
          if (section === 'documents') {
            await mark.evaluate((element) => element.getAnimations()[0]?.finish())
            await capture('catalog-retrying')
          }
          assert(gate.calls === 1, 'Repeated activation sent another request')
          gate.release()
          await alert.getByText('Повторите чуть позже.').waitFor()
          assert(await retry.isEnabled() && await mark.count() === 0, 'A failed retry must unlock the button')

          hold(`/${section}`)
          const recovering = page.waitForRequest((request) => new URL(request.url()).pathname === `/${section}`)
          await retry.evaluate((element) => { element.click(); element.click() })
          await recovering
          await mark.waitFor()
          assert(gate.calls === 1, 'A double click sent another request')
          gate.release()
          await page.getByRole('link', { name: readyText, exact: section === 'documents' }).waitFor()
          assert(await state.count() === 0, 'A successful retry did not clear the error')
        }
      }
    }

    for (const [index, [hash, path, title]] of cases.entries()) {
      hold(path)
      await page.goto(`${origin}/?check=loader-${index}${hash}`)
      const indicator = await check(title, index !== 0)
      if (index === 0 || index === 3) await capture(index === 0 ? 'auth-loading' : 'inbox-loading')
      gate.release()
      await indicator.waitFor({ state: 'hidden' })
    }
    hold('/ai/tools')
    await page.goto(`${origin}/?check=tool-loader#ask?tools`)
    await page.getByRole('textbox', { name: 'Что сделать?' }).fill('Найди расписание в документах')
    await page.getByRole('button', { name: 'Запустить помощника' }).click()
    const indicator = await check('Выбираем инструмент и ждём результат…', false)
    gate.release()
    await indicator.waitFor({ state: 'hidden' })
    await page.getByRole('heading', { name: 'Найденные фрагменты (1)' }).waitFor()

    hold('/ai/tools', 'error')
    await page.getByRole('button', { name: 'Запустить помощника' }).click()
    await indicator.waitFor()
    gate.release()
    const toolError = page.locator('.tool-feedback').getByRole('alert')
    await toolError.waitFor()

    hold('/ai/tools')
    const toolRetry = page.getByRole('button', { name: 'Повторить', exact: true })
    await toolRetry.press('Enter')
    await toolRetry.locator('.desk-loader.is-compact').waitFor()
    assert(await toolError.isVisible() && await toolRetry.isDisabled() && await indicator.count() === 0,
      'The assistant must keep the error with a loader in its disabled retry button')
    await toolRetry.press('Enter')
    assert(gate.calls === 1, 'Repeating the assistant action sent another request')
    gate.release()
    await page.getByRole('heading', { name: 'Найденные фрагменты (1)' }).waitFor()
    assert(await toolError.count() === 0, 'A successful assistant retry must clear the error')
  } finally {
    gate?.release()
  }
}
