🩺 MedBot / AmoApp — Поле разработки и деплоя
# Подключение к серверу интеграций

SSH-доступ:

ssh amocrm-server

# Как зайти под пользователем deployer
ssh deployer@amocrm-server

💻 Локальная разработка на Mac
📂 Путь к проекту (iCloud)
cd ~/Library/Mobile\ Documents/com~apple~CloudDocs/amoapp

# GitHub CLI и переменные окружения

Список окружений:
gh api repos/:owner/:repo/environments | jq '.environments[].name'

Секреты окружения dev:
gh secret list --env dev


Переменные окружения dev (имена + значения):

gh variable list --env dev

# Отправка кода в репозиторий
git add .
git commit -m "workflow test"   # произвольный комментарий
git push

# SSH-ключи (по устройствам)
Устройство	            Приватный ключ	            Публичный ключ
iMac GitHub Deploy	    ~/.ssh/amoapp_ci	          amoapp_ci.pub
Mac Air GitHub Deploy	  ~/.ssh/amoapp_laptop	      amoapp_laptop.pub
iMac → сервер	          ~/.ssh/amoapp_imac	        amoapp_imac.pub
Mac Air → сервер	      ~/.ssh/amoapp_air	          amoapp_air.pub
DigitalOcean	          ~/.ssh/id_rsa_digitalocean	id_rsa_digitalocean.pub


# Бот жив по адресу
https://amo.ap-development.com/medbot/health

# Проверить ключи:

ls -la ~/.ssh
cat ~/.ssh/config


Пример записи в config:

Host github-amoapp
  HostName github.com
  User git
  IdentityFile ~/.ssh/amoapp_air
  AddKeysToAgent yes
  UseKeychain yes
  IdentitiesOnly yes


# Работа с сервером

Файл системной настройки 
 sudo systemctl edit medbot.service

Проверить содержимое:

ls -la /var/www/app
ls -la /var/www/app/amoapp


Посмотреть владельца:

stat /var/www/app/amoapp/run_worker.sh


Перезапустить сервис:

sudo systemctl restart amoapp.service


Посмотреть таймеры:

systemctl list-timers | grep amoapp-worker || true

# Переменные окружения и конфиги

Редактировать конфиг:

sudo nano /etc/amo-calc.env


Проверить содержимое:

cat /etc/amo-calc.env

🧩 AmoCRM API

Получить ID всех воронок:

set -a; source /etc/amo-calc.env; set +a
curl -s -H "Authorization: Bearer $AMO_TOKEN" \
  "https://$AMO_DOMAIN/api/v4/leads/pipelines?limit=250" \
| jq '._embedded.pipelines[] | {id, name}'


Получить ID нужной воронки по имени (подставить скои значения вместо ACCESS_TOKEN и ):

curl -H "Authorization: Bearer <ACCESS_TOKEN>" \
     https://voennik365.amocrm.ru/api/v4/leads/pipelines | jq '.["_embedded"].pipelines[] | {id: .id, name: .name}'



🗄️ Работа с PostgreSQL
Подключение к базе внутри контейнера
docker exec -it vl_admin_pg psql -U vl -d vl_admin

Основные команды psql
Цель	Команда
Список баз	\l
Подключиться к базе	\c vl_admin
Список таблиц	\dt
Структура таблицы	\d users или \d messages
Показать записи	SELECT * FROM users LIMIT 10;
Подсчитать записи	SELECT COUNT(*) FROM messages;
Выйти	\q
Расширенные примеры

Последние 20 сообщений:

SELECT id, chat_id, direction, created_at, LEFT(text, 200) AS preview
FROM messages
ORDER BY created_at DESC
LIMIT 20;


Сообщения конкретного пользователя:

SELECT id, direction, created_at, text
FROM messages
WHERE chat_id = 7541841215
ORDER BY created_at DESC
LIMIT 20 OFFSET 0;


Сводка за сегодня:

SELECT
  COUNT(*) AS messages_total,
  SUM((direction=0)::int) AS messages_in,
  SUM((direction=1)::int) AS messages_out
FROM messages
WHERE created_at >= CURRENT_DATE;

# Бэкап и восстановление БД

Создать папку:

mkdir -p /var/www/medbot/backups


Сделать бэкап:

docker exec -i vl_admin_pg pg_dump -U vl -d vl_admin | \
gzip > /var/www/medbot/backups/vl_admin_$(date +%F_%H%M).sql.gz


Просмотреть бэкапы:

ls -lh /var/www/medbot/backups


Восстановить из дампа:

gunzip -c /var/www/medbot/backups/vl_admin_2025-10-06_1600.sql.gz | \
docker exec -i vl_admin_pg psql -U vl -d vl_admin


# Просмотр логов Postgres:

docker logs vl_admin_pg --tail=200 -f

🧪 Локальное тестирование
Запуск backend:
uvicorn app:app --host 127.0.0.1 --port 8011


Проверка API:

curl -sS http://127.0.0.1:8011/health
curl -sS http://127.0.0.1:8011/admin-api/chats

Запуск frontend:
cd medbot/admin-frontend
npm install
npm run dev


Открыть в браузере:

http://localhost:5173/

🧱 Отладка CORS и API

Если в браузере появляется ошибка:

Access to fetch ... has been blocked by CORS policy


→ Проверь, что в app.py добавлено:

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://amo.ap-development.com",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

🔍 Полезные проверки

Проверить активные процессы:

ps aux | grep uvicorn


Проверить nginx:

sudo nginx -t && sudo systemctl reload nginx


Проверить сервисы:

sudo systemctl status medbot.service
sudo journalctl -u medbot.service -n 100 --no-pager

✅ Краткий чеклист перед деплоем

Проверить .env — актуальные токены и DB_URL.

Выполнить git pull origin main.

Перезапустить сервис:

sudo systemctl restart medbot.service


Проверить:

/health отвечает {"status":"ok"}

/admin-api/chats возвращает список.

Открыть панель:

https://amo.ap-development.com/medbot/admin/