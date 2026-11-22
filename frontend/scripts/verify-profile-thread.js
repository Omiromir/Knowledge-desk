async (page, capture = async () => {}) => {
  const origin = new URL(page.url()).origin
  const id = 'profile-test'
  const oldPhoto = 'before'
  const newPhoto = 'after'
  const avatar = 'frontend/src/features/auth/auth-grid.webp'
  let profile = { display_name: 'Роман', avatar_version: oldPhoto }
  let threadReads = 0
  let pageLoads = 0
  let sendUpdate
  const update = new Promise((resolve) => { sendUpdate = resolve })
  const stamp = '2026-11-18T09:00:00Z'
  const thread = {
    id, name: 'Имя из формы', question: 'Текст заявки', status: 'resolved', created_at: stamp,
    author: { display_name: 'Алина', avatar_version: 'employee' },
    replies: [
      { id: 'reply', text: 'Ответ на вопрос', created_at: stamp,
        author: { is_self: true, display_name: 'Роман', avatar_version: oldPhoto } },
      { id: 'legacy', text: 'Старый ответ', created_at: stamp },
    ],
  }
  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) throw new Error('Production API uses another origin')
    if (request.isNavigationRequest()) pageLoads++
    switch (url.pathname) {
      case '/auth/session':
        return route.fulfill({ json: { mode: 'supabase', member: {
          user_id: 'operator-fixture', email: 'roman@example.test', role: 'operator', can_operate_requests: true,
        } } })
      case '/profile':
        if (request.method() === 'PATCH') profile.display_name = request.postDataJSON().display_name
        return route.fulfill({ json: profile })
      case '/profile/avatar':
        if (request.method() === 'GET') return route.fulfill({ path: avatar, contentType: 'image/webp' })
        profile = { ...profile, avatar_version: newPhoto }
        return route.fulfill({ json: profile })
      case '/documents':
        return route.fulfill({ json: [] })
      case '/requests':
        return route.fulfill({ json: [thread] })
      case `/requests/${id}`:
        threadReads++
        return route.fulfill({ json: thread })
      case `/requests/${id}/avatars/employee`:
        return route.fulfill({ path: avatar, contentType: 'image/webp' })
      case `/requests/${id}/reply-draft`:
      case `/requests/${id}/delivery`:
        return route.fulfill({ json: null })
      case '/requests/certificate-clarification-events':
        if (!url.searchParams.has('mine')) return route.fulfill({ status: 204 })
        await update
        return route.fulfill({
          contentType: 'text/event-stream', body: 'event: certificate-list\ndata: {"revision":"next","waiting":2}\n\n',
        })
      default:
        return route.continue()
    }
  })

  await page.goto(`${origin}/#requests?id=${id}`)
  const sidebar = page.locator('.sidebar-account')
  const reply = page.locator('.request-activity-event').filter({ hasText: 'Ответ на вопрос' })
  await reply.getByText('Роман', { exact: true }).waitFor()
  await page.locator('.request-activity-event').filter({ hasText: 'Старый ответ' }).getByText('Ответ', { exact: true }).waitFor()
  const profileLink = sidebar.getByRole('link', { name: 'Мой профиль — Роман' })
  await profileLink.focus()
  await page.keyboard.press('Enter')
  await page.getByRole('textbox', { name: 'Отображаемое имя' }).fill('Роман Волков')
  await page.getByRole('button', { name: 'Сохранить имя' }).click()
  await sidebar.getByText('Роман Волков', { exact: true }).waitFor()
  await page.locator('.profile-workspace input[type=file]').setInputFiles(avatar)
  await sidebar.locator('img[src$="after"]').waitFor()
  sendUpdate()
  await page.locator('.request-nav-count').filter({ hasText: /^2$/ }).waitFor()
  await capture('profile-after-save')
  await page.goBack()
  await reply.getByText('Роман Волков', { exact: true }).waitFor()
  await reply.locator('img[src$="after"]').waitFor()
  if (pageLoads !== 1 || threadReads !== 1) throw new Error('The browser reloaded the page or conversation')
  await capture('profile-thread')
  return { passed: true }
}
