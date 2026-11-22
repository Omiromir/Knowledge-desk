# Knowledge Desk frontend

React + TypeScript + Vite. Запуск и конфигурация API описаны в [основном README](../README.md).

```powershell
npm ci
npm run dev
```

Vite открывает интерфейс на http://127.0.0.1:5173 и обращается к API на http://127.0.0.1:8000. В production-сборке API работает на том же origin, что и интерфейс. Для явного переопределения задайте `VITE_API_BASE_URL` в `.env.local` по примеру `.env.example`.

```powershell
npm run build
npm run lint
```

API-клиент находится в `src/api`, экраны в `src/features`, навигация в `src/navigation`. Правила оформления описаны в [DESIGN.md](../DESIGN.md).

В scripts находятся функции для Playwright CLI: verify-ui.js (реальный POST и GET), verify-offline.js (API остановлен), verify-recovery.js (после запуска API), verify-errors.js (контролируемые ошибочные ответы). Команды и ограничения проверок: ../docs/ui-verification.md.
