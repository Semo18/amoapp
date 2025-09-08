# Разработка в АМО (Тестовое поле - сумма рассрочки)Заходим на сервер Интеграций АМОсрм: 
ssh amocrm-server

# Как зайти под deployer
На своём Mac просто:
ssh deployer@amocrm-server

# Путь к проекту на Icloude 
cd ~/Library/Mobile\ Documents/com~apple~CloudDocs/amoapp

# Как посмотреть конфиг через CLI (опционально)
# список окружений
gh api repos/:owner/:repo/environments | jq '.environments[].name'

# секреты окружения dev (имена)
gh secret list --env dev

# переменные окружения dev (имена и значения)
gh variable list --env dev



# Отправить проект в репозиторий: 
git add .
git commit -m 'workflow test' (в кавычках произвльное название)
git push


# Ключи SSH 

# Деплой на Imac в GitHub (Через Dev)
-rw-------@  1 semo  staff   399 Sep  2 09:50 amoapp_ci
-rw-r--r--@  1 semo  staff    91 Sep  2 09:50 amoapp_ci.pub

# Деплой на Mac Air в GitHub (Через Dev)
-rw-------@  1 sergeymonichev  staff   399  2 сен 10:05 amoapp_laptop
-rw-r--r--@  1 sergeymonichev  staff    95  2 сен 10:05 amoapp_laptop.pub

# Подключение к серверу локальных компьютеров на Imac
-rw-------@  1 semo  staff   444 Sep  1 16:13 amoapp_imacы
-rw-r--r--@  1 semo  staff    93 Sep  1 16:13 amoapp_imac.pub

# Подключение к серверу локальных компьютеров на Mac Air
-rw-------@  1 sergeymonichev  staff   444  1 сен 16:21 amoapp_air
-rw-r--r--@  1 sergeymonichev  staff    92  1 сен 16:21 amoapp_air.pub

# Подключение к https://cloud.digitalocean.com/droplets/515653766/graphs?i=decaa2&period=hour
-rw-------@  1 semo  staff  3434 Nov  7  2024 id_rsa_digitalocean
-rw-r--r--@  1 semo  staff   753 Nov  7  2024 id_rsa_digitalocean.pub

# Найти ssh ключ
ls -la ~/.ssh

cat ~/.ssh/amoapp_imac (последнее имя нужного ключа)

Зайти в config
cat ~/.ssh/config

# Команда коррекции агента (пример)
cat >> ~/.ssh/config <<'CFG'
Host github-amoapp
  HostName github.com
  User git
  IdentityFile ~/.ssh/amoapp_air
  AddKeysToAgent yes
  UseKeychain yes
  IdentitiesOnly yes


# Работа с comfig (на сервере)
Редактировать: 
sudo nano /etc/amo-calc.env

Проверить: 
cat /etc/amo-calc.env


# РАБОТА НА СЕРВЕРЕ
Проверить содержимое на сервере 
# где наш код:
ls -la /var/www/app
ls -la /var/www/app/amoapp

# кто владелец:
stat /var/www/app/amoapp/run_worker.sh

# Перезагрузить сервис
sudo systemctl restart amoapp.service (это именно калькулятор рассрочки)



# В ПРОЕКТЕ 
Посмотреть таймер 
systemctl list-timers | grep amoapp-worker || true

# Как узнать PIPELINE_ID (ID воронки)

На сервере уже есть токен и домен — просто выпишем все воронки и отфильтруем по имени:

set -a; source /etc/amo-calc.env; set +a

# посмотреть все воронки (id + name)
curl -s -H "Authorization: Bearer $AMO_TOKEN" \
  "https://$AMO_DOMAIN/api/v4/leads/pipelines?limit=250" \
| jq '._embedded.pipelines[] | {id, name}'

# вытащить ID нужной воронки по точному имени
curl -s -H "Authorization: Bearer $AMO_TOKEN" \
  "https://$AMO_DOMAIN/api/v4/leads/pipelines?limit=250" \
| jq -r '._embedded.pipelines[]
         | select(.name=="Рассрочка Москва (МО)") | .id'

