# Разработка в АМО (Тестовое поле - сумма рассрочки)Заходим на сервер Интеграций АМОсрм: 
ssh amocrm-server


# Путь к проекту на Icloude 
cd ~/Library/Mobile\ Documents/com~apple~CloudDocs/amoapp

# Найти ssh ключ
ls -la ~/.ssh

cat ~/.ssh/amoapp_imac (последнее имя нужного ключа)







# Как посмотреть конфиг через CLI (опционально)
# список окружений
gh api repos/:owner/:repo/environments | jq '.environments[].name'

# секреты окружения dev (имена)
gh secret list --env dev

# переменные окружения dev (имена и значения)
gh variable list --env dev


# Как зайти под deployer
1. Убедись, что твой публичный ключ добавлен в /home/deployer/.ssh/authorized_keys.(CI-ключ там уже лежит, но можно добавить и твой личный).cat ~/.ssh/id_ed25519.pub | ssh root@amocrm-server "tee -a /home/deployer/.ssh/authorized_keys"
2. 
3. На своём Mac просто:ssh deployer@amocrm-server


# Отправить проект в репозиторий: 
git add .
git commit -m 'workflow test' (в кавычках произвльное название)
git push