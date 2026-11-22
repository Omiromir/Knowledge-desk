async page => { await page.setViewportSize({ width: 1440, height: 900 }); await (async (page, capture) => {
  const assert = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = new URL(page.url()).origin
  const member = { user_id: 'auth-test', email: 'test@example.test', role: 'reader' }
  const password = 'test-only-password'
  let attempts = 0
  let finishRejectedLogin
  const waitToReject = new Promise((resolve) => { finishRejectedLogin = resolve })
  await page.addInitScript(() => {
    const clear = CanvasRenderingContext2D.prototype.clearRect
    const draw = CanvasRenderingContext2D.prototype.drawImage
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      if (this.canvas.classList.contains('auth-art-light')) {
        this.canvas.dataset.draws = String(Number(this.canvas.dataset.draws || 0) + 1)
      }
      return clear.apply(this, args)
    }
    CanvasRenderingContext2D.prototype.drawImage = function (...args) {
      const [, , , width, height] = args
      if (this.canvas.classList.contains('auth-art-light') && Math.abs(width / height - .36) < .001) {
        this.canvas.dataset.turning = String(Number(this.canvas.dataset.turning || 0) + 1)
      }
      return draw.apply(this, args)
    }
  })
  await page.route('**/auth/session', (route) => route.fulfill({ json: { mode: 'supabase', member: null } }))
  await page.route('**/auth/login', async (route) => {
    const body = route.request().postDataJSON()
    assert(body.email === member.email && body.password === password, 'В запросе изменились данные входа')
    attempts++
    if (attempts === 1) {
      await waitToReject
      return route.fulfill({ status: 401, json: { detail: 'Проверьте почту и пароль.' } })
    }
    return route.fulfill({ json: { mode: 'supabase', member } })
  })
  await page.route('**/auth/logout', (route) => route.fulfill({ status: 204 }))

  const email = page.getByLabel('Почта', { exact: true })
  const passwordInput = page.getByLabel('Пароль', { exact: true })
  const submit = page.getByRole('button', { name: 'Войти', exact: true })
  const show = page.getByRole('button', { name: 'Показать пароль' })
  const hide = page.getByRole('button', { name: 'Скрыть пароль' })
  const pending = page.getByRole('button', { name: 'Входим…' })
  const authBackground = page.locator('.auth-art')
  const canvas = page.locator('.auth-art-light')
  await page.goto(origin, { waitUntil: 'networkidle' })
  await submit.waitFor()
  assert(await authBackground.evaluate((screen) => getComputedStyle(screen).backgroundImage.includes('auth-grid')), 'Фон входа не подключён')
  const form = page.locator('.auth-form')
  const artBounds = await authBackground.boundingBox()
  const formBounds = await form.boundingBox()
  const emblem = authBackground.locator('svg.auth-emblem')
  const emblemBounds = await emblem.boundingBox()
  const markWidth = await emblem.locator('use').evaluate((mark) => mark.getBBox().width)
  assert(markWidth > 19 && markWidth < 26, 'Над формой должна быть только буква без рамки')
  assert(artBounds.width === await page.evaluate(() => innerWidth) &&
    Math.abs(formBounds.x + formBounds.width / 2 - artBounds.width / 2) < 2 &&
    Math.abs(formBounds.y + formBounds.height / 2 - artBounds.height / 2) < 2,
  'Фон занимает весь экран и форма центрирована')
  assert(Math.abs(emblemBounds.x + emblemBounds.width / 2 - artBounds.width / 2) < 2 && emblemBounds.y < formBounds.y,
    'Иконка должна стоять сверху по центру')
  const visualTitle = page.getByRole('heading', { name: 'Вход в Knowledge Desk' })
  assert(await visualTitle.evaluate((title) => getComputedStyle(title).clipPath !== 'none'), 'Заголовок нужен только для скринридера')
  await capture('auth-redesign')

  const canHover = await page.evaluate(() => matchMedia('(any-hover: hover) and (any-pointer: fine)').matches)
  if (canHover) {
    const art = await authBackground.boundingBox()
    const scale = Math.max(art.width / 1728, art.height / 1152)
    const cell = 24 * scale
    const gridX = (art.width - 1728 * scale) / 2
    const gridY = (art.height - 1152 * scale) / 2
    const row = Math.floor((art.height * .70 - gridY) / cell)
    const col = Math.floor((art.width * .22 - gridX) / cell)
    const point = (col, row) => ({ x: gridX + (col + .36) * cell, y: gridY + (row + .43) * cell })
    const start = point(col, row)
    const end = point(col + 6, row)
    const draws = async () => Number(await canvas.getAttribute('data-draws'))
    const alpha = (points) => canvas.evaluate((surface, points) => {
      const context = surface.getContext('2d')
      const scaleX = surface.width / surface.clientWidth
      const scaleY = surface.height / surface.clientHeight
      return points.map(({ x, y }) => context.getImageData(Math.floor(x * scaleX), Math.floor(y * scaleY), 1, 1).data[3])
    }, points)
    async function awaitDrawing(after) {
      await page.waitForFunction((count) => Number(document.querySelector('.auth-art-light').dataset.draws || 0) > count, after)
    }
    await page.mouse.move(art.x + start.x, art.y + start.y)
    await awaitDrawing(0)
    assert(Number(await canvas.getAttribute('data-turning')) >= 25, 'Страницы рядом не начали перелистываться вместе')
    const [first, beside, outside] = await alpha([start, point(col + 2, row), point(col, row - 4)])
    assert(first > beside && beside > 20 && outside === 0, 'Край фонарика должен быть мягче центра')
    await capture('auth-glow')

    const previousDraws = await draws()
    await page.mouse.move(art.x + end.x, art.y + end.y)
    await awaitDrawing(previousDraws)
    const crossed = Array.from({ length: 6 }, (_, index) => point(col + index + 1, row))
    const besidePath = crossed.map((_, index) => point(col + index + 1, row + 2))
    const offPath = crossed.map((_, index) => point(col + index + 1, row + 4))
    assert((await alpha(crossed)).every((value) => value > 20), 'Быстрое движение пропустило страницу на пути')
    assert((await alpha(besidePath)).every((value) => value > 20), 'След должен сохранять ширину фонарика')
    assert((await alpha(offPath)).every((value) => value === 0), 'Засветились страницы за пределами фонарика')
    await page.waitForTimeout(200)
    const [peak] = await alpha([end])
    await page.waitForTimeout(260)
    const [faded] = await alpha([end])
    assert(faded > 0 && faded < peak, `Подсветка должна плавно гаснуть (${peak} → ${faded})`)
    await capture('auth-trail')
    await page.waitForFunction(({ x, y }) => {
      const surface = document.querySelector('.auth-art-light')
      return surface.getContext('2d').getImageData(
        Math.floor(x * surface.width / surface.clientWidth), Math.floor(y * surface.height / surface.clientHeight), 1, 1,
      ).data[3] === 0
    }, end)
    await page.waitForTimeout(100)
    const settledDraws = await draws()
    await page.waitForTimeout(180)
    assert(await draws() === settledDraws, 'Подсветка продолжает рисовать без движения')

    const form = await email.boundingBox()
    await page.mouse.move(form.x + 20, form.y + 20)
    await page.mouse.move(form.x + 60, form.y + 20)
    await page.waitForTimeout(90)
    assert(await draws() === settledDraws, 'Движение над формой активировало фон')

    await page.emulateMedia({ reducedMotion: 'reduce' })
    await canvas.waitFor({ state: 'hidden' })
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    const reducedDraws = await draws()
    await page.mouse.move(art.x + start.x, art.y + start.y)
    await page.waitForTimeout(180)
    assert(await draws() === reducedDraws, 'При сокращённом движении подсветка рисует кадры')
  } else {
    await canvas.waitFor({ state: 'hidden' })
  }
  await email.fill(member.email)
  await page.waitForFunction(() => getComputedStyle(document.querySelector('label[for="auth-email"]')).transform === 'none')
  await passwordInput.fill(password)
  await passwordInput.press('Tab')
  assert(await show.evaluate((button) => button === document.activeElement && button.matches(':focus-visible')), 'Нет видимого фокуса на кнопке пароля')
  await show.press('Space')
  assert(await passwordInput.getAttribute('type') === 'text', 'Пароль не показан')
  await hide.press('Enter')
  assert(await passwordInput.getAttribute('type') === 'password' && await passwordInput.inputValue() === password, 'Пароль не скрыт или потерян')

  try {
    await submit.click()
    await pending.waitFor()
    assert(await pending.isDisabled(), 'Кнопка активна во время отправки')
  } finally {
    finishRejectedLogin()
  }
  await page.getByRole('alert').filter({ hasText: 'Проверьте почту и пароль.' }).waitFor()
  assert(await email.inputValue() === member.email && await passwordInput.inputValue() === password, 'Ошибка стёрла ввод')
  await submit.click()
  const logout = page.getByRole('button', { name: 'Выйти' })
  await logout.waitFor()
  assert(await page.locator('.brand-mark').evaluate((image) => image.naturalWidth > 0), 'Иконка рабочего пространства не загрузилась')
  assert(attempts === 2, 'Неожиданное число попыток входа')
  await logout.click()
  await submit.waitFor()
  assert(await passwordInput.getAttribute('type') === 'password' && await passwordInput.inputValue() === '', 'Форма показала прежний пароль после выхода')
}
)(page, state => page.screenshot({ path: 'D:/Personal projects and stuff/Knowledge desk/output/refactor-verification/browser/auth-1440-' + state + '.png', animations: 'disabled' })); return 'REFRACTOR_BROWSER_OK auth 1440'; }