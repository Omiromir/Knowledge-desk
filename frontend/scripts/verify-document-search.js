async (page) => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message)
  }
  const marker = 'Маркер [A+B]'
  const documents = Array.from({ length: 24 }, (_, index) => ({
    id: `search-check-${index}`,
    title: `Документ ${index + 1}`,
    source: 'Проверка поиска',
    text: `${'Вступление. '.repeat(220)} ${marker} содержится в полном тексте.`,
  }))
  documents.push({
    id: 'title-check',
    title: 'Отчёт по архиву',
    source: 'Проверка поиска',
    text: 'Здесь совпадает только заголовок.',
  })
  const api = 'http://127.0.0.1:8000/documents'
  const pattern = /http:\/\/127\.0\.0\.1:8000\/documents(?:\/[^?]+)?$/
  await page.route(pattern, (route) => {
    const url = route.request().url()
    const body = url === api
      ? documents
      : documents.find((entry) => url === `${api}/${entry.id}`)
    return route.fulfill({
      json: body,
      headers: { 'Access-Control-Allow-Origin': 'http://127.0.0.1:5173', 'Access-Control-Allow-Credentials': 'true' },
    })
  })
  try {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('http://127.0.0.1:5173/?check=document-search#documents')
    const search = page.getByRole('searchbox')
    const links = page.getByRole('navigation', { name: 'Выбор документа' }).getByRole('link')
    const article = page.getByRole('article', { name: 'Текст документа' })
    const back = page.getByRole('link', { name: 'К списку' })
    await links.first().waitFor()
    await search.fill('АРХИВУ')
    await links.first().waitFor()
    assert(await links.count() === 1, 'Search must match the title without regard to case')
    await links.first().press('Enter')
    await article.getByRole('heading', { name: 'Отчёт по архиву' }).waitFor()
    assert(!(await search.isVisible()) && await back.isVisible(), 'Desktop opens a dedicated reader')
    await page.keyboard.press('Control+k')
    await search.waitFor()
    assert(await search.evaluate((element) => element === document.activeElement), 'Search shortcut must focus the search field from the reader')
    await search.fill('Несуществующий запрос')
    await page.getByText('Ничего не найдено', { exact: true }).waitFor()
    await search.fill('')
    assert(await links.count() === documents.length, 'Clearing the search must restore the full list')

    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 760 })
      if (await back.isVisible()) await back.click()
      await search.fill(`  ${marker.toLowerCase()}  `)
      assert(await links.count() === 24, 'Search must match literal symbols in the full text')
      assert((await links.first().locator('.document-preview').textContent())?.includes(marker), 'Preview must show the match in the text')
      assert(await links.first().locator('.document-preview mark').textContent() === marker, 'Preview must highlight the search match')
      const target = links.nth(14)
      const scrollBox = page.locator('.document-results')
      await target.scrollIntoViewIfNeeded()
      const scrollTop = await scrollBox.evaluate((element) => element.scrollTop)
      assert(scrollTop > 0, 'Fixture must require scrolling')
      await target.press('Enter')
      await article.getByRole('heading', { name: 'Документ 15' }).waitFor()
      assert(!(await search.isVisible()), 'Mobile reader must occupy a separate screen')
      assert(await page.evaluate(() => document.activeElement?.textContent?.trim() === 'К списку'), 'Opening the reader must move focus to the return link')
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Mobile reader must fit the viewport')
      if (width === 390) {
        const text = article.locator('.document-text')
        const before = await text.evaluate((element) => parseFloat(getComputedStyle(element).fontSize))
        const bigger = page.getByRole('button', { name: 'Увеличить текст' })
        const smaller = page.getByRole('button', { name: 'Уменьшить текст' })
        const canIncrease = await bigger.isEnabled()
        await (canIncrease ? bigger : smaller).press('Enter')
        assert(await text.evaluate((element) => parseFloat(getComputedStyle(element).fontSize)) === before + (canIncrease ? 2 : -2), 'Reading size must change with the keyboard')
        await (canIncrease ? smaller : bigger).press('Enter')
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
        await page.waitForFunction(() => document.querySelector('progress')?.value === 100)
        assert(await page.locator('.reader-toolbar').evaluate((element) => Math.abs(element.getBoundingClientRect().top) < 2), 'Toolbar must remain visible while scrolling')
        await back.press('Enter')
      }
      else await page.goBack()
      await search.waitFor()
      assert(await search.inputValue() === `  ${marker.toLowerCase()}  `, 'Returning must keep the search')
      assert(await scrollBox.evaluate((element, position) => Math.abs(element.scrollTop - position) < 2, scrollTop), 'Returning must restore the list scroll position')
      assert(await target.evaluate((element) => element === document.activeElement), 'Returning must restore focus to the document')
      assert(!(await article.isVisible()), 'The mobile list must hide the reader')
    }
    return { result: 'passed', widths: [1440, 390, 320] }
  } finally {
    await page.unroute(pattern)
    await page.goto('http://127.0.0.1:5173/#documents')
  }
}
