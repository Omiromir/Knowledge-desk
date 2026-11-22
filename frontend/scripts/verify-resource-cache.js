async (page, capture = async () => {}) => {
  const assert = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = 'http://127.0.0.1:5173'
  const record = { id: 'request-1', name: 'Вера', question: 'Уточнить расписание', status: 'new' }
  const documents = Array.from({ length: 65 }, (_, index) => ({
    id: `doc-${index}`, title: `Документ ${index + 1}`, text: 'Учебный текст', source: 'fixture.md',
  }))
  let catalogReads = 0
  let documentReads = 0
  let historyReads = 0
  let catalogStatus = 200
  let releaseCatalog
  let holdCatalog = new Promise((resolve) => { releaseCatalog = resolve })
  let member = { user_id: 'reader-a', email: 'reader-a@example.test', role: 'reader' }
  const headers = { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Credentials': 'true' }
  await page.goto('about:blank')
  await page.route('http://127.0.0.1:8000/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let json = null
    let status = 200
    if (path === '/auth/session' || path === '/auth/login') json = { mode: 'supabase', member }
    if (path === '/auth/logout') member = { ...member, user_id: 'reader-b', email: 'reader-b@example.test' }
    if (path === '/requests') { historyReads++; json = [record] }
    if (path === '/requests/request-1') json = record
    if (path === '/documents') {
      catalogReads++
      await holdCatalog
      status = catalogStatus
      json = status === 200 ? (member.user_id === 'reader-a' ? documents : []) : { detail: 'Недоступно' }
    }
    if (path === '/documents/doc-0') { documentReads++; json = documents[0] }
    await route.fulfill({ json, headers, status })
  })

  await page.clock.install()
  await page.goto(origin + '/#requests')
  const nav = page.getByRole('navigation', { name: 'Основная навигация' })
  const list = page.getByRole('navigation', { name: 'Выбор документа' })
  const first = list.getByRole('link', { name: 'Документ 1', exact: true })
  const refresh = page.getByRole('button', { name: 'Обновить список' })
  const previous = page.getByRole('link', { name: 'К списку' })
  const question = page.getByRole('link', { name: /Уточнить расписание/ })
  const visibilityChanged = () => page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
  await question.waitFor()
  assert(catalogReads === 0 && historyReads === 1, `Лишние запросы при входе в заявки (документы: ${catalogReads}, заявки: ${historyReads})`)

  await nav.getByRole('link', { name: 'Документы' }).press('Enter')
  await page.getByText('Загружаем документы…').waitFor()
  assert(await refresh.isDisabled(), 'Повтор первого запроса должен быть заблокирован')
  await capture('initial-load')
  releaseCatalog()
  await first.waitFor()
  await first.press('Enter')
  await page.getByRole('article', { name: 'Текст документа' }).waitFor()
  await previous.press('Enter')
  await first.press('Enter')
  await page.getByRole('article', { name: 'Текст документа' }).waitFor()
  assert(documentReads === 1, 'Повторное открытие карточки прошло мимо кэша')
  await previous.press('Enter')
  await nav.getByRole('link', { name: 'Заявки' }).press('Enter')
  await question.waitFor()
  assert(historyReads === 1, 'Повторное открытие очереди вызвало запрос')
  await page.clock.fastForward(70_000)
  assert(await page.getByRole('button', { name: 'Обновить заявки' }).isEnabled() && historyReads === 1,
    'Открытая очередь опрашивает API в простое')

  await nav.getByRole('link', { name: 'Документы' }).press('Enter')
  await first.waitFor()
  assert(catalogReads === 1, 'Свежий каталог загрузился повторно')
  await visibilityChanged()
  assert(await refresh.isEnabled() && catalogReads === 1, 'Возврат на вкладку перечитал свежий каталог')

  const historyResponse = page.waitForResponse('http://127.0.0.1:8000/requests')
  await nav.getByRole('link', { name: 'Заявки' }).press('Enter')
  await historyResponse
  await question.waitFor()
  assert(historyReads === 2 && catalogReads === 1, 'Вход в раздел должен обновить только устаревшую очередь')
  await nav.getByRole('link', { name: 'Документы' }).press('Enter')
  await first.waitFor()

  holdCatalog = new Promise((resolve) => { releaseCatalog = resolve })
  await page.clock.fastForward(600_000)
  assert(await refresh.isEnabled() && catalogReads === 1 && historyReads === 2, 'В простое появились запросы по таймеру')
  await visibilityChanged()
  await page.getByText('Обновляем документы…').waitFor()
  assert(await first.isVisible() && await refresh.isDisabled(), 'Фоновый запрос скрыл данные или доступен повтор')
  await visibilityChanged()
  await capture('background-refresh')
  releaseCatalog()
  await page.getByText('Обновляем документы…').waitFor({ state: 'hidden' })
  assert(catalogReads === 2 && historyReads === 2, 'Возврат должен перечитать устаревший каталог один раз')
  await page.clock.fastForward(301_000)
  documents[1].title = 'Уточнённое название документа'
  await nav.getByRole('link', { name: 'Ответы', exact: true }).press('Enter')
  await page.getByRole('button', { name: documents[1].title, exact: true }).waitFor()
  await nav.getByRole('link', { name: 'Документы' }).press('Enter')
  await first.waitFor()
  assert(catalogReads === 3 && historyReads === 2, 'Разделы должны использовать один свежий каталог')

  catalogStatus = 500
  await refresh.press('Enter')
  await page.getByText('Не удалось обновить документы').waitFor()
  assert(await first.isVisible(), 'Сетевая ошибка скрыла последнее успешное состояние')
  await page.clock.fastForward(600_000)
  assert(catalogReads === 4, 'После ошибки запустился повтор по таймеру')
  catalogStatus = 404
  await page.getByRole('button', { name: 'Повторить', exact: true }).press('Enter')
  await page.getByText('Не удалось загрузить документы').waitFor()
  assert(!await first.isVisible(), 'Данные остались доступны после отзыва доступа')
  catalogStatus = 200
  await refresh.press('Enter')
  await first.waitFor()
  await page.reload()
  await first.waitFor()
  assert(catalogReads === 7, 'Ручное обновление и F5 должны перечитать каталог')

  await page.getByRole('button', { name: 'Выйти' }).click()
  await page.getByLabel('Почта', { exact: true }).fill(member.email)
  await page.getByLabel('Пароль', { exact: true }).fill('test-password')
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.getByText('Документов пока нет').waitFor()
  assert(catalogReads === 8 && !await first.isVisible(), 'Другой пользователь получил старый кэш')
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Интерфейс выходит за ширину экрана')
}
