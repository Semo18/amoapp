# medbot

1) Как запускать/останавливать бота
На сервере (systemd)
# старт/стоп/перезапуск/статус
sudo -n /usr/bin/systemctl start  medbot.service
sudo -n /usr/bin/systemctl stop   medbot.service
sudo -n /usr/bin/systemctl restart medbot.service
sudo -n /usr/bin/systemctl status  medbot.service --no-pager

# логи в реальном времени
journalctl -fu medbot.service

# автозапуск при ребуте (уже включён)
sudo -n /usr/bin/systemctl enable medbot.service

# health-check через loopback и через nginx
curl -s http://127.0.0.1:8011/health
curl -s https://amo.ap-development.com/medbot/health

Локально (для разработки)
# из каталога medbot
uvicorn app:app --host 127.0.0.1 --port 8011 --reload
# остановка — Ctrl+C в терминале


# Конфиг ginx (на сервере)
sudo nano /etc/nginx/sites-enabled/amoapp.conf

# Открыть unit-файл:

sudo nano /etc/systemd/system/medbot.service

# Конфиг .env (на сервере)
nano /var/www/medbot/.env
sudo nano /var/www/medbot/admin-panel/.env (для админки)

Открыть unit-файл:

sudo nano /etc/systemd/system/medbot.service

# РАБОТА НА СЕРВЕРЕ
Как зайти в нужную папку
# на сервере
sudo -iu deployer          # работаем от имени владельца файлов
# Как зайти под пользователем deployer
ssh deployer@amocrm-server
cd /var/www/medbot
source .venv/bin/activate (включить виртуальное окружение)
pwd && ls -la

# Или проверь, где ещё лежит код:

ls -la /var/www
ls -la /var/www/app/medbot

# Коррекция любого файла через: 
nano путь к файлу (пример /var/www/medbot/.env)

# статус сервиса и логи
sudo -n /usr/bin/systemctl status medbot.service --no-pager
journalctl -fu medbot.service

# перезапуск после правок .env
sudo -n /usr/bin/systemctl restart medbot.service

# хелсчеки
curl -s http://127.0.0.1:8011/health
curl -s https://amo.ap-development.com/medbot/health

# вебхук (разово уже сделали, но на всякий случай)
curl -s "https://amo.ap-development.com/medbot/admin/set_webhook"

# Проверка access_token через AMO_API_URL
curl -sS -H "Authorization: Bearer $AMO_ACCESS_TOKEN" \
  "$AMO_API_URL/api/v4/account?with=amojo_id"


# ОБНОВЛЕНИЕ ТОКЕНА 


set -a; source /var/www/medbot/.env; set +a
AMO_DOMAIN=$(echo "$AMO_API_URL" | sed -E 's~^https?://~~')

CODE="<сюда вставь 20-минутный код>" (СЮДА ВСТАВИТЬ ИЗ АМО КОТОРЫЙ НА 20 мину)

curl -sS -X POST "https://${AMO_DOMAIN}/oauth2/access_token" \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "'"$AMO_CLIENT_ID"'",
    "client_secret": "'"$AMO_CLIENT_SECRET"'",
    "grant_type": "authorization_code",
    "code": "'"$CODE"'",
    "redirect_uri": "'"$AMO_REDIRECT_URI"'"
  }' | jq .

ВАЖНО !!! Дальше получаем 
"access_token":
"refresh_token":