async (page) => {
  const assert = (condition, message) => { if (!condition) throw new Error(message) }
  await page.getByRole('link', { name: 'Заявки', exact: true }).click()
  await page.locator('.requests-page').getByRole('link', { name: 'Новая заявка' }).click()
  const name = 'Проверка ошибки'
  const question = 'Этот текст должен сохраниться при отклонении запроса.'
  const cases = [
    { status: 422, contentType: 'application/json', body: JSON.stringify({ detail: [{ loc: ['body', 'question'], type: 'string_too_short', msg: 'Too short' }] }) },
    { status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Private database information' }) },
    { status: 201, contentType: 'application/json', body: JSON.stringify({ name, question, status: 'new' }) },
    { status: 503, contentType: 'text/plain', body: 'Gateway unavailable' },
  ]
  for (const testCase of cases) {
    await page.getByLabel('Ваше имя', { exact: false }).fill(name)
    await page.getByLabel('Ваш вопрос', { exact: false }).fill(question)
    await page.route('http://127.0.0.1:8000/requests', (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      return route.fulfill({ ...testCase, headers: { 'Access-Control-Allow-Origin': 'http://127.0.0.1:5173' } })
    })
    try {
      await page.getByRole('button', { name: 'Отправить заявку', exact: true }).click()
      await page.getByText('Отправка не подтверждена', { exact: true }).waitFor()
      assert(await page.getByLabel('Ваше имя', { exact: false }).inputValue() === name, 'Server error must preserve name')
      assert(await page.getByLabel('Ваш вопрос', { exact: false }).inputValue() === question, 'Server error must preserve question')
      assert(await page.locator('.submission-result').innerText() === '', 'An error or response without id must not show success')
      if (testCase.status === 422) {
        await page.getByText('Напишите ваш вопрос.', { exact: true }).waitFor()
        assert(await page.getByLabel('Ваш вопрос', { exact: false }).evaluate((el) => el === document.activeElement), 'Server validation error must focus the related field')
      }
      if (testCase.status === 500) assert(!(await page.locator('.form-error').innerText()).includes('Private database'), 'Server internals must not appear in the UI')
    } finally { await page.unrouteAll({ behavior: 'wait' }) }
  }
  await page.getByLabel('Ваше имя', { exact: false }).fill('')
  await page.getByLabel('Ваш вопрос', { exact: false }).fill('')
  await page.getByRole('link', { name: 'Документы', exact: true }).click()
  return { errorCases: [422, 500, '201 without id', '503 non-JSON'], result: 'passed' }
}
