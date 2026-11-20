async (page) => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message)
  }
  await page.getByRole('link', { name: 'Заявки', exact: true }).click()
  await page.getByLabel('Ваше имя', { exact: false }).fill('')
  await page.getByLabel('Ваш вопрос', { exact: false }).fill('')
  await page
    .getByRole('button', { name: 'Отправить заявку', exact: true })
    .click()
  await page.getByText('Укажите ваше имя.', { exact: true }).waitFor()
  await page.getByText('Напишите ваш вопрос.', { exact: true }).waitFor()
  assert(
    await page
      .getByLabel('Ваше имя', { exact: false })
      .evaluate((el) => el === document.activeElement),
    'First invalid field must receive focus',
  )

  const name = 'Проверка интерфейса'
  const question = 'Сохранится ли эта заявка после перезапуска API?'
  await page.getByLabel('Ваше имя', { exact: false }).fill(name)
  await page.keyboard.press('Tab')
  assert(
    await page
      .getByLabel('Ваш вопрос', { exact: false })
      .evaluate((el) => el === document.activeElement),
    'Question must follow name in the keyboard order',
  )
  await page.getByLabel('Ваш вопрос', { exact: false }).fill(question)
  const focus = await page
    .getByLabel('Ваш вопрос', { exact: false })
    .evaluate((el) => ({
      visible: el.matches(':focus-visible'),
      width: getComputedStyle(el).outlineWidth,
    }))
  assert(
    focus.visible && parseFloat(focus.width) >= 2,
    'Keyboard focus must be visible',
  )

  let posts = 0
  await page.route('http://127.0.0.1:8000/requests', async (route) => {
    if (route.request().method() === 'POST') {
      posts += 1
      await new Promise((resolve) => setTimeout(resolve, 1200))
    }
    await route.continue()
  })
  await page.getByLabel('Ваш вопрос', { exact: false }).press('Tab')
  await page.keyboard.press('Enter')
  const pending = page.getByRole('button', { name: 'Отправляем…', exact: true })
  await pending.waitFor()
  assert(await pending.isDisabled(), 'Pending submit must be disabled')
  assert(
    (await page.getByText('Заявка сохранена', { exact: true }).count()) === 0,
    'No success before the server replies',
  )
  await pending.evaluate((button) => button.form.requestSubmit())
  await page.getByText('Заявка сохранена', { exact: true }).waitFor()
  assert(posts === 1, 'Duplicate submit must not send another POST')
  await page.unroute('http://127.0.0.1:8000/requests')

  const saved = page.getByRole('article', { name: 'Данные заявки' }).first()
  await saved.waitFor()
  const id = await saved.locator('.record-id code').textContent()
  const response = await page.request.get(
    `http://127.0.0.1:8000/requests/${id}`,
  )
  assert(
    response.status() === 200,
    'Created request must be readable through the API',
  )
  const record = await response.json()
  assert(
    record.id === id &&
      record.name === name &&
      record.question === question &&
      record.status === 'new',
    'UI and API request data must match',
  )
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.evaluate(() => scrollTo(0, 0))
  await page.screenshot({
    path: 'output/playwright/desktop-request.png',
    fullPage: true,
  })

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `No overflow at ${width}px`,
    )
    await page.evaluate(() => scrollTo(0, 0))
    await page.screenshot({
      path: `output/playwright/mobile-request-${width}.png`,
      fullPage: true,
    })
  }
  await page.getByRole('link', { name: 'Документы', exact: true }).click()
  await page
    .getByRole('button', { name: 'Обновить список', exact: true })
    .click()
  await page.getByRole('navigation', { name: 'Выбор документа' }).getByRole('link').first().click()
  await page.getByRole('article', { name: 'Текст документа' }).waitFor()
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    'Document view must fit 320px',
  )
  await page.screenshot({
    path: 'output/playwright/mobile-documents.png',
    fullPage: true,
  })
  return {
    result: 'passed',
    request: record,
    checks: [
      'inline validation',
      'keyboard order and focus',
      'single pending POST',
      'server-confirmed result',
      'GET request matches UI',
      'mobile 390/320px',
      'real document list and detail',
    ],
  }
}
