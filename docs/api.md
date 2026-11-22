# API: проверка запросов

Проверка выполнена через Swagger UI по адресу `http://127.0.0.1:8000/docs`.
Сервис был доступен только на loopback-интерфейсе `127.0.0.1`.

## Документ

Запрос `POST /documents`:

```json
{
  "title": "Local test document",
  "text": "Document created through Swagger docs.",
  "source": "Swagger UI test"
}
```

Ответ `201 Created`:

```json
{
  "title": "Local test document",
  "text": "Document created through Swagger docs.",
  "source": "Swagger UI test",
  "id": "ce883c83-bdce-4107-b61f-c29a6bb9c8b4"
}
```

## Заявка

Запрос `POST /requests`:

```json
{
  "name": "Local test user",
  "question": "How do I use this API?"
}
```

Ответ `201 Created`:

```json
{
  "name": "Local test user",
  "question": "How do I use this API?",
  "id": "04b94e11-048a-4233-a550-31fa9ad96c2f",
  "status": "new"
}
```

## Проверки ошибок

- `POST /documents` с `"text": "   "` вернул `422`; ошибка указала поле `text`.
- `POST /requests` без поля `question` вернул `422`; ошибка указала отсутствующее поле `question`.
- `GET /documents/missing-document-id` вернул `404` с `{"detail":"Document not found."}`.
- `GET /requests/missing-request-id` вернул `404` с `{"detail":"Request not found."}`.

До добавления защиты запускайте сервис только на loopback-интерфейсе:

```powershell
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```
