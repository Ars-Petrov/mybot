# Удалённый сервер и бэкапы на домашний ПК

Готовые файлы рассчитаны на Linux с systemd на сервере и Windows 10/11 на ПК.
Адрес сервера, токен и ключи SSH задаются на ваших устройствах. Пока они не настроены,
бот не развёрнут и автоматическое резервное копирование не запущено.

## 1. Сервер

Нужны Python 3.10+, venv, Git, OpenSSH и systemd. Для свежего Debian/Ubuntu:

```bash
sudo apt update
sudo apt install python3 python3-venv git openssh-server
sudo git clone --branch codex/family-tools https://github.com/Ars-Petrov/mybot /srv/mybot
sudo useradd --system --home-dir /srv/mybot --shell /bin/bash mybot
sudo chown -R mybot:mybot /srv/mybot
sudo -u mybot mkdir -p /srv/mybot/data /srv/mybot/backups
sudo -u mybot python3 -m venv /srv/mybot/.venv
sudo -u mybot /srv/mybot/.venv/bin/pip install -r /srv/mybot/requirements.txt
sudo -u mybot cp /srv/mybot/.env.example /srv/mybot/.env
sudo chmod 600 /srv/mybot/.env
sudo nano /srv/mybot/.env
```

Если пользователь или папка уже существуют, используйте их вместо повторного создания.
После слияния изменений можно клонировать основную ветку вместо `codex/family-tools`.
В `.env` заполните `BOT_TOKEN`; `FAMILY_IDS` можно оставить пустым. Оставьте `DATABASE_PATH=data/tools.sqlite3`
для приложенных служб. Для другой базы исправьте путь и в службе резервного копирования.
После запуска создайте первую пригласительную ссылку командой ниже. Родственники
регистрируются автоматически по ней, без внесения ID в настройки.

Старую копию бота с тем же токеном остановите. При переносе существующей базы остановите
старый процесс, сделайте снимок `backup.py` и положите его как `/srv/mybot/data/tools.sqlite3`.
Если запускали бота без остановки, используйте снимок, а не обычную копию живого SQLite-файла.

```bash
sudo cp /srv/mybot/deploy/mybot.service /etc/systemd/system/
sudo cp /srv/mybot/deploy/mybot-backup.service /etc/systemd/system/
sudo cp /srv/mybot/deploy/mybot-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mybot.service
sudo systemctl status mybot.service
```

Для первого входа из консоли сервера:

```bash
cd /srv/mybot
sudo -u mybot .venv/bin/python manage.py invite
```

Откройте полученную ссылку в Telegram и нажмите Start. Затем приглашайте семью
кнопкой «Пригласить семью» в меню бота. `FAMILY_IDS` нужен только для необязательного
начального импорта: вступившие по ссылке сохраняются в базе и не требуют перезапуска.

Дождитесь ответа бота на `/id` в Telegram: при первом запуске это также подтверждает,
что файл базы уже создан. Затем создайте первый бэкап и включите расписание:

```bash
sudo systemctl start mybot-backup.service
sudo systemctl enable --now mybot-backup.timer
sudo systemctl list-timers mybot-backup.timer
```

Бэкап создаётся в 03:00 по Москве, независимо от часового пояса сервера. Пропущенный
запуск после выключения сервера выполняется при активации таймера (`Persistent=true`).
Используется SQLite Online Backup API, затем проверяется целостность и публикуется
`manifest.json` с контрольными суммами. Сначала создаётся новая копия, затем удаляются
самые старые собственные снимки сверх лимита 30. Один запуск службы = один снимок.
Служба systemd не запускает второй экземпляр той же службы параллельно; не запускайте
ручной backup.py одновременно с ней в ту же папку.

Логи и проверка последнего снимка:

```bash
sudo journalctl -u mybot.service -u mybot-backup.service -n 50
sudo -u mybot cat /srv/mybot/backups/manifest.json
```

Бот сам обновляет схему старой базы при первом запуске. Перед обновлением существующей
инсталляции сохраните отдельную копию базы. Бэкапы не включают код и `.env`; код в GitHub,
а токен и настройки нужно сохранить отдельно.

## 2. Доступ с Windows по SSH

На ПК нужен компонент Windows «Клиент OpenSSH» (`ssh.exe` и `scp.exe`). Для загрузки
используется SSH-ключ без интерактивных запросов. Пример создания отдельного ключа:

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\toolsbot_backup"
```

Открытый ключ из `toolsbot_backup.pub` добавьте в `/srv/mybot/.ssh/authorized_keys`
на сервере для пользователя `mybot`; папка `.ssh` должна принадлежать `mybot` с правами
700, файл `authorized_keys` — с правами 600. Закрытый ключ остаётся на ПК.
Если ключ защищён паролем, его нужно загрузить в Windows ssh-agent для текущего
пользователя до запуска задания; иначе BatchMode завершит скачивание ошибкой.

Добавьте в `%USERPROFILE%\.ssh\config`, заменив адрес:

```text
Host toolsbot-server
    HostName YOUR_SERVER_IP
    User mybot
    IdentityFile ~/.ssh/toolsbot_backup
    IdentitiesOnly yes
```

Выполните `ssh toolsbot-server` вручную, сверив отпечаток ключа сервера с его
консолью, и сохраните известный ключ. Затем проверьте вход без запросов:

```powershell
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes toolsbot-server exit
```

## 3. Копии на ПК

Скачайте/клонируйте проект на ПК в постоянную папку. Скрипты задания должны оставаться
по этому пути. Например, в PowerShell из корня проекта:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\pull-backups.ps1 -SshHost toolsbot-server
```

По умолчанию копии появятся в `%USERPROFILE%\ToolsBotBackups`. Можно задать
`-Destination 'D:\Backups\ToolsBot'`. Скрипт скачивает все отсутствующие снимки
из серверного манифеста, сначала во временную папку, проверяет SHA-256 и только
потом переносит файл в архив. При сбое существующие копии сохраняются, ротация не
выполняется. При следующих запусках уже проверенные файлы повторно не скачиваются.

После успешной ручной проверки установите задание от текущего пользователя:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-backup-task.ps1 -SshHost toolsbot-server
Start-ScheduledTask -TaskName ToolsBotBackups
Get-ScheduledTaskInfo -TaskName ToolsBotBackups
```

По умолчанию — ежедневно в 20:00 по времени ПК и при входе в Windows. Задание работает
в сеансе этого пользователя без прав администратора; после установки не выходите из
учётной записи до завершения скачивания. Для изменения времени откройте «Планировщик
заданий» → `ToolsBotBackups` → «Триггеры». Локально сохраняются последние 90 снимков.
Если нужен другой лимит, измените `-Keep` в аргументах действия задания.

ПК должен быть включён, пользователь должен войти, а сервер — доступен. Когда ПК
выключен, новые локальные копии не появляются; при следующем запуске будут скачаны
снимки, которые ещё хранятся на сервере (до 30). Выключенный больше месяца ПК не
сможет получить уже удалённые сервером снимки. В папке копий есть `last-success.txt`
со временем последнего успешного запуска и `errors.log` с ошибками. При отсутствии
сети в момент входа можно запустить задачу вручную после подключения.

## 4. Проверка восстановления

Скачайте выбранную копию с ПК обратно на сервер в отдельную папку. Сначала проверьте
её, подставив свой путь:

```bash
cd /srv/mybot
.venv/bin/python -c "from backup import verify; verify('/tmp/chosen-backup.sqlite3'); print('OK')"
```

Для восстановления остановите бота **и таймер бэкапов**, дождитесь завершения текущего
бэкапа. Сохраните текущую базу и все её sidecar-файлы (`-wal`, `-shm`, `-journal`) в
отдельную папку до замены. Положите выбранный снимок как `/srv/mybot/data/tools.sqlite3`,
задайте владельца `mybot:mybot`, права 600, затем запустите бота и таймер. Старые
sidecar-файлы не должны лежать рядом с восстановленной базой. Это возвращает весь
каталог к выбранной дате, включая пользователей, удаления и очередь уведомлений.
Уведомления, ещё не доставленные на дату копии, могут отправиться после восстановления.

Документация механизмов: [SQLite backup в Python](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup),
[таймеры systemd](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html),
[Планировщик Windows](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/).
