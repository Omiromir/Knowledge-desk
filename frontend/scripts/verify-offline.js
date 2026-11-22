async (page) => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message)
  }
  await page
    .getByRole('button', { name: 'Обновить список', exact: true })
    .click()
  await page
    .getByText('Не удалось загрузить документы', { exact: true })
    .waitFor()
  await page
    .getByRole('button', { name: 'Повторить', exact: true })
    .waitFor()
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `Offline view must fit ${width}px`,
    )
    await page.evaluate(() => scrollTo(0, 0))
    await page.screenshot({
      path: `output/playwright/offline-documents-${width}.png`,
      fullPage: true,
    })
  }
  await page.getByRole('link', { name: 'Заявки', exact: true }).click()
  await page.locator('.requests-page').getByRole('link', { name: 'Новая заявка' }).click()
  const name = 'Проверка недоступного API'
  const question = 'Этот текст должен остаться в форме после сетевой ошибки.'
  await page.getByLabel('Ваше имя', { exact: false }).fill(name)
  await page.getByLabel('Ваш вопрос', { exact: false }).fill(question)
  await page
    .getByRole('button', { name: 'Отправить заявку', exact: true })
    .click()
  await page.getByText('Отправка не подтверждена', { exact: true }).waitFor()
  assert(
    (await page.getByLabel('Ваше имя', { exact: false }).inputValue()) === name,
    'Name must remain after failure',
  )
  assert(
    (await page.getByLabel('Ваш вопрос', { exact: false }).inputValue()) ===
      question,
    'Question must remain after failure',
  )
  assert(
    (await page.locator('.submission-result').innerText()) === '',
    'Failed submission must never show success',
  )
  assert(
    await page
      .getByRole('button', { name: 'Отправить заявку', exact: true })
      .isEnabled(),
    'Retry submit must become available',
  )
  await page.evaluate(() => scrollTo(0, 0))
  await page.screenshot({
    path: 'output/playwright/offline-request-mobile.png',
    fullPage: true,
  })
  await page.getByRole('link', { name: 'Документы', exact: true }).click()
  return {
    offline: 'passed',
    fieldsPreserved: true,
    falseConfirmation: false,
    retryAvailable: true,
  }
}
