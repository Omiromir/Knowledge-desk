async page => { await page.setViewportSize({ width: 1440, height: 900 }); await (async (page, capture = async () => {}) => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const examples = [
    ['Алина Соколова', 'Когда будет готов эскизный проект и в каком формате мы получим материалы?', 'in_progress'],
    ['Марк Данилов', 'Можно ли уточнить состав комплекта чертежей?', 'new'],
    ['Вера Лукина', 'Кто согласует изменения в задании на проектирование?', 'new'],
    ['Никита Карпов', 'Нужна копия утверждённой спецификации.', 'resolved'],
    ['Анна Белова', 'Где найти график консультаций по проекту?', 'new'],
    ['Тимур Волков', 'Помогите сверить дату передачи макета.', 'in_progress'],
    ['Ирина Мирная', 'Можно ли прислать исправленный план?', 'resolved'],
  ]
  const records = examples.map(([name, question, status], i) => ({
    id: 'case-' + i, name, question, status, created_at: new Date(Date.UTC(2026, 10, 18, 12 - i)).toISOString(),
    document_id: 'brief', replies: [],
  }))
  records[0].replies.push({ id: 'first-reply', created_at: '2026-11-18T12:12:00Z',
    text: 'В договоре указан срок в 20 рабочих дней. Сверяю состав комплекта с приложением.' })
  const firstOperator = { user_id: 'operator', email: 'operator@example.test', role: 'operator' }
  const secondOperator = { ...firstOperator, user_id: 'operator-b', email: 'operator-b@example.test' }
  let member = firstOperator
  const document = { id: 'brief', title: 'Договор на проектирование', source: 'Договор.pdf', text: 'Эскизный проект передаётся заказчику.' }
  const context = page.context()
  const drafts = new Map()
  let writes = 0
  let retries = 0
  let firstAttemptId
  async function mockApi(target) {
    await target.route('**/auth/session', route => route.fulfill({ json: { mode: 'supabase', member } }))
    await target.route('**/documents', route => route.fulfill({ json: [document] }))
    await target.route(url => url.pathname === '/requests' || url.pathname.startsWith('/requests/'), replyRoute)
  }
  function replyRoute(route) {
    const [, id, action, operation] = new URL(route.request().url()).pathname.split('/').slice(1)
    const record = records.find(entry => entry.id === id)
    const draftKey = member.user_id + ':' + id
    if (action === 'reply-draft') {
      if (route.request().method() === 'PUT') {
        const { text, revision, expected_revision } = route.request().postDataJSON()
        if ((drafts.get(draftKey)?.revision ?? null) !== expected_revision) {
          return route.fulfill({ status: 409, json: { detail: { code: 'reply_draft_changed' } } })
        }
        if (text) drafts.set(draftKey, { text, revision })
        else drafts.delete(draftKey)
      }
      const draft = drafts.get(draftKey)
      return draft ? route.fulfill({ json: draft }) : route.fulfill({ contentType: 'application/json', body: 'null' })
    }
    if (action === 'delivery' && id !== 'case-0') return route.fulfill({ contentType: 'application/json', body: 'null' })
    if (action === 'delivery') {
      if (operation === 'retry' && route.request().method() === 'POST') retries++
      return route.fulfill({ json: {
        request_id: id, event_id: 'test-event', status: retries ? 'delivered' : 'unknown',
        reason: retries ? null : 'timeout', updated_at: '2026-11-18T12:01:00Z', can_retry: !retries,
        receipt: retries ? { status: 'accepted', event_id: 'test-event', request_id: id, execution_id: 'test-run' } : null,
      } })
    }
    if (action === 'replies' && route.request().method() === 'POST' && record) {
      const reply = route.request().postDataJSON()
      if (id === 'case-0' && !firstAttemptId) {
        firstAttemptId = reply.id
        return route.fulfill({ status: 503, json: { detail: 'Временно недоступно' } })
      }
      if (id === 'case-0') check(firstAttemptId === reply.id, 'При повторе после перезагрузки должен сохраниться ID отправки')
      writes++
      record.replies.push({ ...reply, created_at: '2026-11-18T14:05:00Z' })
      record.status = 'resolved'
      if (drafts.get(draftKey)?.revision === reply.id) drafts.delete(draftKey)
      if (id === 'case-1') return route.abort()
    }
    return route.fulfill({ json: id ? record : records })
  }
  await mockApi(context)
  await page.goto(new URL('/#requests?sort=oldest', page.url()).href)
  await page.reload()
  const queue = () => page.getByRole('complementary', { name: 'Очередь заявок' })
  const row = index => queue().locator('a').filter({ hasText: records[index].name })
  const reply = () => page.getByRole('textbox', { name: 'Ответ на заявку' })
  const back = () => page.getByRole('link', { name: 'К списку заявок' })
  const thread = () => page.getByRole('region', { name: 'Открытая заявка' })
  const ready = () => page.locator('.request-composer textarea:not([readonly])').waitFor()
  const sync = (target = page) => target.locator('.request-composer button[type=submit]:enabled').waitFor()
  const wide = await page.evaluate(() => matchMedia('(min-width: 1000px)').matches)
  async function queueFillsWorkspace() {
    const list = await queue().boundingBox()
    const workspace = await page.locator('.operator-inbox').boundingBox()
    return list && workspace && Math.abs(list.width - workspace.width) < 1
  }
  await row(0).waitFor()
  check(await page.getByRole('link', { name: 'Новая заявка', exact: true }).count() === 1, 'Одно действие создания заявки')
  check(await thread().count() === 0, 'Очередь должна открываться без выбранной заявки')
  check(await queueFillsWorkspace(), 'Очередь должна занимать всю рабочую область')
  await row(0).press('Enter')
  await page.getByText(records[0].replies[0].text).waitFor()
  check(await queue().isVisible() === wide, 'Очередь и переписка должны делить широкий экран')
  if (wide) {
    const list = await queue().boundingBox()
    const conversation = await page.locator('.request-activity').boundingBox()
    check(list.x + list.width <= conversation.x + 1, 'Переписка должна открываться справа от очереди')
    if (await page.evaluate(() => matchMedia('(min-width: 1300px)').matches)) {
      const properties = await page.getByRole('complementary', { name: 'Свойства' }).boundingBox()
      check(conversation.x + conversation.width <= properties.x + 1, 'Свойства должны открываться справа от переписки')
    }
  }
  const firstDraft = 'Проверено по приложению к договору.'
  const anotherDraft = 'Черновик к другой заявке.'
  const secondOperatorDraft = 'Другой оператор готовит свой ответ.'
  await reply().fill(firstDraft)
  await back().press('Enter')
  await thread().waitFor({ state: 'detached' })
  await row(0).waitFor()
  check(await queueFillsWorkspace(), 'После возврата очередь должна снова занимать всю ширину')
  check(await row(0).evaluate(link => document.activeElement === link), 'Фокус должен вернуться на строку заявки')
  const listParams = new URLSearchParams(new URL(page.url()).hash.split('?')[1])
  check(!listParams.has('id') && listParams.get('sort') === 'oldest', 'Возврат должен закрыть заявку и сохранить сортировку')
  await capture('operator-queue')
  await row(0).press('Enter')
  await sync()
  check(await reply().inputValue() === firstDraft, 'Черновик потерялся после возврата к очереди')
  check(!await page.getByText(/Синхронизируем черновик|Черновик сохранён в Supabase/).count(),
    'В редакторе осталась строка состояния черновика')
  await capture('operator')
  await page.getByRole('button', { name: 'Повторить доставку' }).click()
  await page.getByText('Доставка подтверждена', { exact:true }).waitFor()
  check(retries === 1 && writes === 0, 'Повтор доставки должен быть отдельным действием')
  async function open(index) {
    if (!wide && await back().count()) await back().click()
    await row(index).click()
  }
  await open(1)
  await page.getByRole('heading', { name: records[1].name }).waitFor()
  check(await reply().inputValue() === '', 'Черновик попал в другую заявку')
  await reply().fill(anotherDraft)
  await sync()
  await open(0)
  await page.getByRole('heading', { name: records[0].name }).waitFor()
  check(await reply().inputValue() === firstDraft, 'Черновик потерян')
  await page.reload()
  await sync()
  check(await reply().inputValue() === firstDraft, 'Черновик потерян при обновлении')

  const device = await context.browser().newContext({ viewport: page.viewportSize() })
  try {
    await mockApi(device)
    const otherPage = await device.newPage()
    await otherPage.goto(page.url())
    await sync(otherPage)
    const remoteReply = otherPage.getByRole('textbox', { name: 'Ответ на заявку' })
    check(await remoteReply.inputValue() === firstDraft, 'Черновик не загрузился на другом устройстве')
    await remoteReply.fill('Свежая версия с другого устройства.')
    await sync(otherPage)
    await reply().fill(firstDraft + ' Правка.')
    await page.getByText('Черновик изменился на другом устройстве.', { exact: false }).waitFor()
    check(drafts.get(firstOperator.user_id + ':case-0').text === 'Свежая версия с другого устройства.',
      'Устаревшее устройство перезаписало свежий черновик')
    await reply().fill(firstDraft)
    await page.getByRole('button', { name: 'Повторить сохранение моей версии' }).click()
    await sync()
  } finally {
    await device.close()
  }
  await reply().press('Control+Enter')
  await page.locator('#request-reply-error').waitFor()
  check(writes === 0 && await reply().inputValue() === firstDraft, 'Неудачная отправка не должна очищать ответ')

  const detailUrl = page.url()
  await page.close()
  page = await context.newPage()
  await page.goto(detailUrl)
  await sync()
  check(await reply().inputValue() === firstDraft, 'Черновик потерян после закрытия вкладки')
  member = secondOperator
  await page.reload()
  await ready()
  check(await reply().inputValue() === '', 'Черновик первого оператора виден второму')
  await reply().fill(secondOperatorDraft)
  await sync()
  member = firstOperator
  await page.reload()
  await sync()
  check(await reply().inputValue() === firstDraft, 'Второй оператор перезаписал чужой черновик')

  await reply().press('Control+Enter')
  await page.getByRole('button', { name: 'Возобновить' }).waitFor()
  check(writes === 1, 'Ответ должен быть отправлен один раз')
  await page.reload()
  await page.getByRole('button', { name: 'Возобновить' }).waitFor()
  check(await reply().count() === 0, 'Подтверждённая отправка должна очистить черновик')
  member = secondOperator
  await page.reload()
  await ready()
  check(await reply().inputValue() === secondOperatorDraft, 'Отправка очистила черновик другого оператора')
  member = firstOperator
  await page.reload()
  await open(1)
  await sync()
  check(await reply().inputValue() === anotherDraft, 'Отправка очистила черновик другой заявки')
  await reply().press('Control+Enter')
  await page.locator('#request-reply-error').waitFor()
  await page.reload()
  await page.getByRole('button', { name: 'Возобновить' }).waitFor()
  await reply().waitFor({ state: 'hidden' })
  check(writes === 2, 'Сохранённый сервером ответ должен очистить черновик без повтора')

  await back().click()
  await thread().waitFor({ state: 'detached' })
  await queue().getByRole('searchbox').fill('Проверено по приложению')
  await queue().getByRole('combobox', { name: 'Фильтр заявок по статусу' }).selectOption('resolved')
  await page.locator('.requests-page > header').getByText('1 из 7', { exact: true }).waitFor()
  await queue().getByRole('combobox', { name: 'Сортировка заявок по дате отправки' }).selectOption('oldest')
  await row(0).getByText('Закрыта').waitFor()
  await queue().getByRole('button', { name: 'Сбросить' }).click()
  await page.locator('.requests-page > header').getByText('7 из 7', { exact: true }).waitFor()
  member = { ...firstOperator, user_id: 'reader', role: 'reader' }
  await page.goto(new URL('/#requests', page.url()).href)
  await page.reload()
  const compose = page.getByRole('link', { name: 'Новая заявка', exact: true })
  await compose.waitFor()
  check(await compose.count() === 1 && !await page.getByLabel('Ваше имя').isVisible(), 'В списке видна только кнопка создания заявки')
  await compose.click()
  await page.getByLabel('Ваше имя').waitFor()
  check(await compose.count() === 0, 'В открытой форме кнопка создания не нужна')
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Горизонтальное переполнение')
}
)(page, state => page.screenshot({ path: 'D:/Personal projects and stuff/Knowledge desk/output/refactor-verification/browser/operator-inbox-1440-' + state + '.png', animations: 'disabled' })); return 'REFRACTOR_BROWSER_OK operator-inbox 1440'; }