# Структура проекта
medbot/
├── app.py                      # Точка входа FastAPI (инициализация, роуты, CORS, вебхуки)
├── bot.py                      # Логика Telegram-бота (обработчики aiogram)
├── openai_client.py            # Интеграция с OpenAI (создание тредов, анализ, генерация ответов)
├── repo.py                     # Репозиторий данных (ORM-доступ к users/messages)
├── db.py                       # Конфигурация SQLAlchemy, создание engine и моделей
├── storage.py                  # Работа с Redis (сессии, thread_id, блокировки)
├── admin_api.py                # API для админ-панели (чаты, аналитика, сообщения)
├── .env                        # Локальные переменные окружения (секреты, ключи, БД)
├── requirements.txt            # Список Python-зависимостей проекта
├── Dockerfile                  # Описание контейнера backend-сервера
├── docker-compose.yml          # Оркестрация контейнеров (API + БД + Redis)
└── backups/                    # Папка для SQL-бэкапов базы данных

admin-frontend/
├── src/
│   ├── App.jsx                 # Главный компонент SPA: навигация между чатами и аналитикой
│   ├── UserMessages.jsx        # Просмотр сообщений чата (бесконечный скролл, фильтры, поиск)
│   ├── ChatList.jsx            # Список пользователей/чатов с метриками
│   ├── Analytics.jsx           # Панель аналитики (входящие/исходящие, количество пользователей)
│   └── api.js                  # Базовые вызовы REST API (fetch, params, обработка ошибок)
├── .env                        # Настройки фронтенда (VITE_API_BASE, окружение dev/prod)
├── package.json                # Зависимости React-проекта
└── vite.config.js              # Конфиг Vite (сборка, дев-сервер)

.github/workflows/
└── deploy_medbot.yml           # CI/CD-пайплайн: сборка, деплой, перезапуск systemd-сервиса

/mnt/data/
└── backups/                    # Папка с SQL-дампами (pg_dump → gzip)


# Роли основных компонентов
Файл / Каталог	Назначение
app.py	Главная точка входа. Инициализация FastAPI, регистрация роутов, CORS, вебхуков.
bot.py	Настройка Telegram-бота (aiogram): команды, обработчики, связь с БД и OpenAI.
openai_client.py	Работа с Assistant API (создание тредов, run'ов, парсинг ответов).
repo.py	Взаимодействие с PostgreSQL: функции save_message, fetch_messages, upsert_user.
db.py	Подключение SQLAlchemy к базе (DB_URL), декларативные модели User, Message.
storage.py	Поддержка Redis: блокировки, кэширование, работа с thread_id.
admin_api.py	JSON API для панели администратора (чаты, аналитика, сообщения).
admin-frontend/	Интерфейс админ-панели (React + Vite): просмотр чатов, сообщений, статистики.
requirements.txt	Python-зависимости для установки через pip install -r requirements.txt.
.github/workflows/	CI/CD (деплой, обновление контейнера, рестарт systemd на сервере).
backups/	Архивы базы данных (создаются по расписанию cron/systemd).
🧩 Логика потоков данных

Пользователь пишет в Telegram → bot.py получает Message.

Сообщение сохраняется в БД (repo.save_message).

Передаётся в OpenAI через openai_client.py → формируется ответ.

Ответ бота сохраняется в БД и отправляется пользователю.

Админка (admin-frontend/) получает данные через /admin-api/....

Все метрики (чаты, сообщения, аналитика) визуализируются в UI.

🧱 Связи между слоями
Telegram ↔ Bot (aiogram)
        ↕
     FastAPI
        ↕
PostgreSQL (SQLAlchemy ORM)
        ↕
   Admin Frontend (React)
        ↕
   OpenAI API (Assistant)

🧰 Полезные команды напоминания

Перезапустить backend локально:

uvicorn app:app --host 127.0.0.1 --port 8011


Сбросить кэш Redis:

redis-cli flushall


Проверить подключение к БД:

psql -h 127.0.0.1 -U vl -d vl_admin -p 5433


Просмотреть последние логи backend:

journalctl -u medbot.service -n 50 --no-pager
