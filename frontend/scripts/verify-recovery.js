async (page) => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('link', { name: 'Заявки', exact: true }).click()
  await page.getByLabel('Ваше имя', { exact: false }).fill('')
  await page.getByLabel('Ваш вопрос', { exact: false }).fill('')
  await page.getByRole('link', { name: 'Документы', exact: true }).click()
  const retry = page.getByRole('button', {
    name: 'Повторить загрузку',
    exact: true,
  })
  if (await retry.count()) await retry.click()
  else
    await page
      .getByRole('button', { name: 'Обновить список', exact: true })
      .click()
  const listResponse = await page.request.get('http://127.0.0.1:8000/documents')
  const documents = await listResponse.json()
  const target = documents.at(-1)
  assert(target, 'Create a document through the API before running this check')
  await page.getByRole('link', { name: target.title, exact: true }).last().click()
  await page.waitForFunction((id) => document.querySelector('.document-reader .record-id code')?.textContent === id, target.id)
  const documentId = await page
    .locator('.document-reader .record-id code')
    .textContent()
  const documentResponse = await page.request.get(
    `http://127.0.0.1:8000/documents/${documentId}`,
  )
  const document = await documentResponse.json()
  assert(documentResponse.status() === 200, 'Document must survive API restart')
  assert(
    (await page.locator('.document-text').innerText()) === document.text,
    'Full document text must come from the API',
  )
  assert(
    (await page.locator('.source').innerText()).includes(document.source),
    'Document source must match the API',
  )
  await page.getByRole('link', { name: 'К списку' }).click()

  let release
  const gate = new Promise((resolve) => {
    release = resolve
  })
  await page.route('http://127.0.0.1:8000/documents', async (route) => {
    await gate
    await route.continue()
  })
  try {
    await page
      .getByRole('button', { name: 'Обновить список', exact: true })
      .click()
    await page.getByText('Загружаем документы…', { exact: true }).waitFor()
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 })
      await page.evaluate(() => scrollTo(0, 0))
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        'Loading view must fit screen',
      )
      await page.screenshot({
        path: `output/playwright/loading-${width}.png`,
        fullPage: true,
      })
    }
  } finally {
    release()
    await page.unrouteAll({ behavior: 'wait' })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('navigation', { name: 'Выбор документа' }).getByRole('link').first().waitFor()

  // A controlled empty fixture checks the narrow layout; normal reads above use the real API.
  await page.setViewportSize({ width: 390, height: 900 })
  await page.route('http://127.0.0.1:8000/documents', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
      headers: { 'Access-Control-Allow-Origin': 'http://127.0.0.1:5173' },
    }),
  )
  try {
    await page
      .getByRole('button', { name: 'Обновить список', exact: true })
      .click()
    await page.getByText('Документов пока нет', { exact: true }).waitFor()
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      'Empty view must fit mobile',
    )
    await page.screenshot({
      path: 'output/playwright/mobile-empty.png',
      fullPage: true,
    })
  } finally {
    await page.unroute('http://127.0.0.1:8000/documents')
  }

  const ids = await page.evaluate(() =>
    JSON.parse(
      localStorage.getItem(
        'knowledge-desk.request-ids.v1:http://127.0.0.1:8000',
      ) || '[]',
    ),
  )
  assert(ids.length > 0, 'Browser must remember the confirmed request id')
  await page.reload()
  await page.getByRole('navigation', { name: 'Выбор документа' }).getByRole('link').first().waitFor()
  await page.getByRole('link', { name: 'Заявки', exact: true }).click()
  const savedRequest = page.getByRole('article', { name: 'Данные заявки' }).first()
  await savedRequest.waitFor()
  const requestId = await savedRequest.locator('.record-id code').textContent()
  const requestResponse = await page.request.get(
    `http://127.0.0.1:8000/requests/${requestId}`,
  )
  const request = await requestResponse.json()
  assert(
    requestId === ids[0] && requestResponse.status() === 200,
    'Same request must be readable after API restart and page reload',
  )
  assert(
    (await savedRequest.locator('.request-question').innerText()) === request.question,
    'Request history must show the server question',
  )
  assert(
    (await savedRequest.locator('.request-author').textContent()) === request.name,
    'Request history must show the server name',
  )
  assert(
    (await page.locator('.submission-result').innerText()) === '',
    'Reading history must not announce a new submission',
  )
  return { recovered: true, documentId, requestId, loadingAndEmptyMobile: true }
}
