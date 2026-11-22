# Knowledge Desk frontend

React + TypeScript + Vite. Запуск и конфигурация API описаны в [основном README](../README.md).

```powershell
npm install
npm run dev
```

Адрес интерфейса: http://127.0.0.1:5173. Базовый URL API задаётся через VITE_API_BASE_URL в .env.local; пример находится в .env.example. По умолчанию используется http://127.0.0.1:8000.

```powershell
npm run build
npm run lint
```

API-клиент находится в src/api, документы и заявки в src/features, общий жизненный цикл GET в src/hooks/useResource.ts. Система оформления и референс описаны в ../DESIGN.md.

В scripts находятся функции для Playwright CLI: verify-ui.js (реальный POST и GET), verify-offline.js (API остановлен), verify-recovery.js (после запуска API), verify-errors.js (контролируемые ошибочные ответы). Команды и ограничения проверок: ../docs/ui-verification.md.
