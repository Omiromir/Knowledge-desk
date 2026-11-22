async (page, capture = async () => {}) => {
  const assert = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = new URL(page.url()).origin
  const api = new URL(origin).port === '5173' ? 'http://127.0.0.1:8000' : origin
  const headers = { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Credentials': 'true' }
  const documents = [
    { id: 'first', title: 'Вводная страница', source: 'first.md', text: '[[Навигация]]' },
    { id: 'middle', title: 'Навигация', source: 'middle.md', text: '[[Окна приложения]]' },
    { id: 'last', title: 'Окна приложения', source: 'last.md', text: 'Устройство интерфейса. [[Порядок выпуска]] [[Согласование прав]]' },
    { id: 'release', title: 'Порядок выпуска', source: 'release.md', text: '[[Журнал сборок]]' },
    { id: 'builds', title: 'Журнал сборок', source: 'builds.md', text: 'Перечень опубликованных версий.' },
    { id: 'branch', title: 'Согласование прав', source: 'branch.md', text: 'Допуски для гостей.' },
    { id: 'solo', title: 'Архив поездок', source: 'solo.md', text: '[[Закрытый документ]]' },
  ]
  let reads = 0
  await page.route(api + '/auth/session', route => route.fulfill({ headers, json: { mode: 'local', member: null } }))
  await page.route(api + '/documents', route => {
    reads++
    return route.fulfill({ headers, json: documents })
  })
  await page.route(api + '/documents/*', route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1)
    const record = documents.find(document => document.id === id)
    return record
      ? route.fulfill({ headers, json: record })
      : route.fulfill({ headers, status: 404, json: { detail: 'Документ недоступен.' } })
  })
  const toggle = page.getByRole('link', { name: 'Локальный граф', exact: true })
  const local = page.getByRole('group', { name: 'Локальный граф документа' })
  const depths = page.getByRole('group', { name: 'Глубина', exact: true })
  const one = depths.getByRole('radio', { name: '1 шаг', exact: true })
  const two = depths.getByRole('radio', { name: '2 шага', exact: true })
  const three = depths.getByRole('radio', { name: '3 шага', exact: true })
  const heading = (name) => page.getByRole('heading', { name, exact: true })
  await page.goto(origin + '/#documents?id=first')
  await heading('Вводная страница').waitFor()
  assert(reads === 0 && await toggle.getAttribute('aria-expanded') === 'false', 'Закрытый граф запросил каталог')
  await toggle.click()
  await local.waitFor()
  assert(await local.locator('[data-center="true"]').getAttribute('aria-label') === 'Вводная страница', 'Текущий документ отсутствует в центре')
  assert(await local.getByRole('link').count() === 1 && await local.getByRole('link', { name: 'Навигация' }).isVisible(), 'В граф попали не только прямые соседи')
  const visibleLines = local.locator('line[data-kind="link"][data-depth-hidden="false"]')
  assert(await visibleLines.count() === 1, 'Прямая ссылка исчезла')
  assert(await one.isChecked(), 'Глубина по умолчанию должна быть равна одному шагу')

  await depths.getByText('2 шага', { exact: true }).click()
  await local.getByRole('link', { name: 'Окна приложения' }).waitFor()
  assert(await two.isChecked() && await local.getByRole('link').count() === 2, 'Второй шаг показывает неверное окружение')
  await page.waitForTimeout(330)
  await local.evaluate(graph => {
    const world = graph.querySelector(':scope > g')
    const transforms = new Set([world.getAttribute('transform')])
    const observer = new MutationObserver(() => transforms.add(world.getAttribute('transform')))
    observer.observe(world, { attributes: true, attributeFilter: ['transform'] })
    window.stopGraphTrace = () => {
      observer.disconnect()
      return transforms.size
    }
  })
  await two.press('ArrowRight')
  await local.getByRole('link', { name: 'Порядок выпуска' }).waitFor()
  assert(await three.isChecked() && await local.getByRole('link').count() === 4, 'Третий шаг показывает неверное окружение')
  assert(await visibleLines.count() === 4 && reads === 1, 'Не все рёбра показаны или каталог запрошен повторно')
  await page.waitForTimeout(330)
  const transforms = await page.evaluate(() => window.stopGraphTrace())
  const reducedMotion = await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)
  assert(reducedMotion ? transforms === 2 : transforms > 3, 'Масштаб не соблюдает настройку сокращённого движения')
  const ringDistances = await local.evaluate(graph => {
    const center = graph.querySelector('[data-center="true"]').getScreenCTM()
    return [...graph.querySelectorAll('a[href]')].map(node => {
      const { e, f } = node.getScreenCTM()
      return { step: Number(node.dataset.step), distance: Math.hypot(e - center.e, f - center.f) }
    })
  })
  const firstRing = ringDistances.find(node => node.step === 1).distance
  assert(firstRing > 10 && ringDistances.every(({ step, distance }) => Math.abs(distance / firstRing - step) < .01),
    'Узлы расположены не на кольцах своего шага')
  assert(await local.locator('.document-graph-ring[data-depth-hidden="false"]').count() === 3, 'Уровни не видны на схеме')
  assert(await local.getByRole('link', { name: 'Порядок выпуска' }).getAttribute('aria-description') === 'Шаг 3 от открытого документа',
    'Узел не сообщает расстояние для скринридера')
  await capture('local-graph-depth-3')

  const release = local.getByRole('link', { name: 'Порядок выпуска', exact: true })
  const branch = local.getByRole('link', { name: 'Согласование прав', exact: true })
  const pageMark = await release.locator('.document-graph-mark').boundingBox()
  const centerMark = await local.locator('[data-center="true"] .document-graph-mark').boundingBox()
  assert(pageMark.height >= 11 && pageMark.width >= 8 && centerMark.height > pageMark.height,
    'Листы слишком мелкие на третьем шаге или теряется открытый документ')
  const routeTitles = ['Вводная страница', 'Навигация', 'Окна приложения', 'Порядок выпуска']
  const highlightedNodes = local.locator('.document-graph-node[data-highlighted="true"]')
  const highlightedLines = visibleLines.and(local.locator('[data-highlighted="true"]'))
  async function assertPath() {
    const titles = await highlightedNodes.evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label')))
    assert(titles.join(' → ') === routeTitles.join(' → '), 'Подсвечены не все документы кратчайшего пути')
    assert(await highlightedLines.count() === 3 && await visibleLines.and(local.locator('[data-dimmed="true"]')).count() === 1,
      'Подсвечены неверные связи')
    assert(await branch.getAttribute('data-dimmed') === 'true', 'Боковая ветка не приглушена')
  }
  async function capturePath() {
    await page.waitForFunction(() => {
      const node = document.querySelector('#reader-local-graph [aria-label="Согласование прав"]')
      return node && Number(getComputedStyle(node).opacity) < .3
    })
    await capture('local-graph-path')
  }
  const hoverAvailable = await page.evaluate(() => matchMedia('(any-hover: hover)').matches)
  if (hoverAvailable) {
    await branch.locator('.document-graph-mark').hover()
    assert(await branch.getAttribute('data-highlighted') === 'true', 'Наведение не выделило путь к другой ветке')
    await release.locator('.document-graph-mark').hover()
    await assertPath()
    await capturePath()
    await depths.getByText('3 шага', { exact: true }).hover()
    assert(await highlightedNodes.count() === 0 && await highlightedLines.count() === 0,
      'Уход указателя не погасил подсветку')
  }
  const overview = await local.locator(':scope > g').getAttribute('transform')
  await release.focus()
  await page.getByRole('status').filter({ hasText: routeTitles.join(' → ') }).waitFor()
  await assertPath()
  assert(await local.locator(':scope > g').getAttribute('transform') === overview,
    'Фокус на уже видимом узле вытеснил начало маршрута за пределы кадра')
  if (!hoverAvailable) await capturePath()
  if (hoverAvailable) await release.locator('.document-graph-mark').click()
  else await release.press('Enter')
  await heading('Порядок выпуска').waitFor()
  await local.getByRole('link', { name: 'Вводная страница' }).waitFor()
  assert(await three.isChecked(), 'Переход по узлу сбросил выбранную глубину')
  await page.goBack()
  await heading('Вводная страница').waitFor()
  await three.waitFor()
  assert(await three.isChecked(), 'Возврат сбросил выбранную глубину')
  await depths.getByText('1 шаг', { exact: true }).click()
  await local.getByRole('link', { name: 'Окна приложения' }).waitFor({ state: 'hidden' })
  assert(await local.getByRole('link').count() === 1, 'Лишние узлы остались при возврате к прямым связям')

  const next = local.getByRole('link', { name: 'Навигация' }).locator('.document-graph-mark')
  if (await page.evaluate(() => navigator.maxTouchPoints > 0)) await next.tap()
  else await next.click()
  await heading('Навигация').waitFor()
  await local.waitFor()
  assert(await local.locator('[data-center="true"]').getAttribute('aria-label') === 'Навигация', 'Граф не перешёл к новому документу')
  assert(await local.getByRole('link').count() === 2 && reads === 1, 'Соседи должны обновиться из того же каталога')
  await page.waitForFunction(() => document.querySelectorAll('#reader-local-graph .document-graph-title[data-visible]').length === 3)
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Панель не помещается в ширину окна')
  await capture('local-graph')
  await page.goBack()
  await heading('Вводная страница').waitFor()
  await local.waitFor()
  await toggle.click()
  await local.waitFor({ state: 'hidden' })
  await page.getByRole('link', { name: 'К списку' }).click()
  await page.getByRole('link', { name: 'Граф', exact: true }).click()
  const catalog = page.getByRole('group', { name: 'Граф документов', exact: true })
  await catalog.getByRole('link', { name: 'Архив поездок' }).press('Enter')
  await heading('Архив поездок').waitFor()
  await toggle.click()
  await page.getByText('Связанных документов пока нет', { exact: true }).waitFor()
  await depths.getByText('3 шага', { exact: true }).click()
  assert(await page.getByText('Связанных документов пока нет', { exact: true }).isVisible(), 'Пустой граф получил несвязанные документы')
  await page.getByRole('link', { name: 'К графу' }).click()
  await catalog.waitFor()
  await page.getByRole('link', { name: 'Список', exact: true }).click()
  await page.getByRole('navigation', { name: 'Выбор документа' }).waitFor()
  assert(reads === 1, 'Переключение режимов лишний раз запросило каталог')
}
