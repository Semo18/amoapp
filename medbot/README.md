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

Открыть unit-файл:

sudo nano /etc/systemd/system/medbot.service

# РАБОТА НА СЕРВЕРЕ
Как зайти в нужную папку
# на сервере
sudo -iu deployer          # работаем от имени владельца файлов
cd /var/www/medbot
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