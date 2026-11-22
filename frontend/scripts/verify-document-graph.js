async (page, capture = async () => {}) => {
  const assert = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = new URL(page.url()).origin
  const documents = [
    { id: 'course', title: 'Курс Python', source: 'notes/python.md',
      text: 'Основы программирования. [[Расписание Python|Время занятий]]' },
    { id: 'schedule', title: 'Расписание Python', source: 'notes/schedule.md',
      text: 'Занятия на платформе проходят по понедельникам.' },
    { id: 'analytics', title: 'Аналитика данных', source: 'analytics.pdf',
      text: 'Витрины, отчётность, таблицы, показатели, аналитика, визуализация.' },
    { id: 'report', title: 'Визуализация отчётности', source: 'reports.docx',
      text: 'Витрины, отчётность, таблицы, показатели, аналитика, визуализация. Уникальный маркер диаграммы.' },
    { id: 'policy', title: 'Правила доступа', source: 'rules.md',
      text: 'Внутренний архив. [[Закрытая инструкция]]' },
  ]
  let catalogReads = 0
  await page.route('**/auth/session', route => route.fulfill({
    json: { mode: 'supabase', member: { user_id: 'graph-reader', email: 'reader@example.test', role: 'reader' } },
  }))
  await page.route('**/requests/certificate-clarification-events*', route => route.fulfill({ status: 204 }))
  await page.route('**/documents', route => {
    catalogReads++
    return route.fulfill({ json: documents })
  })
  await page.route('**/documents/*', route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1)
    return route.fulfill({ json: documents.find(document => document.id === id) })
  })
  await page.goto(origin + '/#documents')

  const node = (title) => page.locator('.document-graph').getByRole('link', { name: title, exact: true })
  const graph = page.getByRole('group', { name: 'Граф документов' })
  const world = graph.locator(':scope > g')
  const transform = () => world.getAttribute('transform')
  const search = page.getByRole('searchbox', { name: 'Поиск по названию и тексту' })
  const list = page.getByRole('navigation', { name: 'Выбор документа' })
  await list.getByRole('link', { name: 'Курс Python', exact: true }).waitFor()
  await page.getByRole('link', { name: 'Граф', exact: true }).click()
  await graph.waitFor()
  await page.waitForFunction(() => Boolean(document.querySelector('.document-graph-viewport > g')?.getAttribute('transform')))
  assert(catalogReads === 1, 'При смене вида каталог загрузился повторно')
  assert(await graph.locator('a[href]').count() === 5, 'Состав графа отличается от списка')
  assert(await graph.locator('[data-kind="link"]').count() === 1, 'Явная вики-ссылка не распознана')
  assert(await graph.locator('[data-kind="text"]').count() >= 1, 'Общий текст не создал тематическую связь')
  await capture('graph-overview')

  const reducedMotion = await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)
  if (reducedMotion) {
    const before = await transform()
    const nodeTransform = await node('Курс Python').getAttribute('transform')
    await page.waitForTimeout(240)
    assert(await transform() === before && await node('Курс Python').getAttribute('transform') === nodeTransform,
      'Физика продолжила анимацию при сокращённом движении')
  }

  const canHover = await page.evaluate(() => matchMedia('(hover: hover)').matches)
  if (canHover) {
    await node('Курс Python').locator('.document-graph-mark').hover()
    assert(await graph.locator('[data-kind="link"][data-highlighted="true"]').count() === 1, 'Соседняя связь не выделена')
    assert(await node('Правила доступа').getAttribute('data-dimmed') === 'true', 'Посторонний узел не приглушён')
    await capture('graph-highlight')
  }

  const beforeZoom = await transform()
  await page.getByRole('button', { name: 'Увеличить масштаб' }).click()
  await page.waitForFunction(before => document.querySelector('.document-graph-viewport > g').getAttribute('transform') !== before, beforeZoom)
  const beforeKey = await transform()
  await graph.press('ArrowRight')
  assert(await transform() !== beforeKey, 'Клавиатура не перемещает граф')
  if (canHover) {
    const area = await graph.boundingBox()
    const beforePan = await transform()
    await page.mouse.move(area.x + 25, area.y + 45)
    await page.mouse.down()
    await page.mouse.move(area.x + 115, area.y + 85, { steps: 5 })
    await page.mouse.up()
    assert(await transform() !== beforePan, 'Перетаскивание холста не работает')
  }
  await page.getByRole('button', { name: 'Показать весь граф' }).click()
  await search.fill('уникальный маркер диаграммы')
  await node('Визуализация отчётности').waitFor()
  assert(await graph.locator('a[href]').count() === 1, 'Поиск по тексту не ограничил доступные узлы')
  if (canHover) await node('Визуализация отчётности').press('Enter')
  else await node('Визуализация отчётности').locator('.document-graph-mark').tap()
  await page.getByRole('heading', { name: 'Визуализация отчётности', exact: true }).waitFor()
  await page.getByRole('link', { name: 'К графу' }).click()
  await node('Визуализация отчётности').waitFor()
  assert(await search.inputValue() === 'уникальный маркер диаграммы', 'При возврате на граф сбросился поиск')
  assert(await node('Визуализация отчётности').evaluate(link => link === document.activeElement), 'Фокус не вернулся к открытому узлу')

  await search.fill('результатов не будет')
  await page.getByText('Ничего не найдено', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Очистить поиск' }).click()
  await graph.waitFor()
  await page.reload()
  await graph.waitFor()
  await page.getByRole('link', { name: 'Список', exact: true }).click()
  await list.getByRole('link', { name: 'Курс Python', exact: true }).waitFor()
}
