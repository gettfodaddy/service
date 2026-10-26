# Развёртывание тестового бота и веб-админки на отдельном VPS с доменом

**Схема:** отдельный Ubuntu VPS → Caddy как системный сервис на 80/443 → веб-приложение Docker на `127.0.0.1:8000`. Telegram-бот и PostgreSQL запускаются в том же Docker Compose. В командах замените `ADMIN_DOMAIN` на точный домен веб-админки, `SERVER_IP` на IP нового VPS. Домен панели Remnawave указывается отдельно.

Это инструкция для **текущего прототипа**: две админки, каталог тарифов и привязка внутренних сквадов Remnawave. Выдача триала и подписок, ЮKassa, реферальные бонусы и перенос клиентов пока не реализованы. Рабочие Bedolaga и Remnawave продолжают работать независимо.

## 1. Доступы и DNS

Подготовьте новый токен отдельного тестового бота от BotFather, свой числовой Telegram ID, действующий URL Remnawave и **новый** API-токен с чтением внутренних сквадов. Рабочий токен Bedolaga и ранее опубликованный ключ Remnawave не используйте.

Создайте DNS-запись `A`: `ADMIN_DOMAIN → SERVER_IP`. Если существует `AAAA`, она тоже должна указывать на этот VPS; ошибочную `AAAA` удалите. Проверьте `dig +short ADMIN_DOMAIN A`. У провайдера VPS разрешите входящие TCP 22, 80 и 443. Порты 8000 и 5432 извне не открывайте. Для автоматического сертификата Caddy нужны правильный DNS и доступные 80/443: [документация](https://caddyserver.com/docs/automatic-https).

## 2. Docker и Caddy на новом VPS

Установите Docker Engine с Compose Plugin по [официальной инструкции для Ubuntu](https://docs.docker.com/engine/install/ubuntu/). Проверьте:

```bash
docker --version
docker compose version
sudo ss -ltnp | grep -E ':(80|443|8000) ' || true
```

План предполагает свободные 80/443. Установите Caddy как системный сервис по [официальной инструкции](https://caddyserver.com/docs/install#debian-ubuntu-raspbian):

```bash
sudo apt install --yes debian-keyring debian-archive-keyring apt-transport-https curl dnsutils
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
systemctl status caddy --no-pager
```

Если на новом VPS уже есть обратный прокси, сначала определите, кто слушает 80/443. Второй сервер на тех же портах запускать нельзя.

## 3. После `git clone`: создать `.env` на VPS

Предполагается, что вы уже клонировали GitHub-репозиторий в `/opt/vpn-project`, а файлы `docker-compose.yml`, `Dockerfile` и `.env.example` лежат в его корне. Если выбрали другой путь, используйте его вместо `/opt/vpn-project` во всех командах. Для приватного репозитория настройте доступ GitHub через SSH-ключ чтения на сервере; сам ключ и `.env` в репозиторий не добавляйте.

На VPS:

```bash
cd /opt/vpn-project
git status --short
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
openssl rand -hex 32
nano .env
```

Два результата `openssl` используйте отдельно для `APP_SECRET` и `POSTGRES_PASSWORD`. Hex-пароль БД безопасно помещается в URL подключения без дополнительного кодирования. Заполните:

```dotenv
BOT_TOKEN=<новый токен тестового бота>
ADMIN_IDS=<ваш числовой Telegram ID>
APP_SECRET=<первая случайная строка>
REMNAWAVE_API_URL=https://<действующий домен панели Remnawave>
REMNAWAVE_API_KEY=<новый API-токен с правом чтения сквадов>
POSTGRES_PASSWORD=<вторая случайная строка>
COOKIE_SECURE=true
WEB_ORIGIN=https://ADMIN_DOMAIN
APP_NAME=<временное название>
```

В `WEB_ORIGIN` нужен точный адрес **без завершающего `/`**. Строку `DATABASE_URL` из шаблона для Docker менять не нужно: Compose заменяет её адресом PostgreSQL внутри сети контейнеров. Не пересылайте вывод `docker compose config`: он может раскрыть секреты из `.env`.

Перед первой публикацией в GitHub убедитесь, что `.env` и дампы БД не отслеживаются Git: в проекте для них настроены `.gitignore` и `.dockerignore`. Если секрет когда-либо попал в коммит, удаление файла из последнего коммита не делает секрет безопасным — отзовите/смените его, как рекомендует [GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

## 4. Запустить приложение

```bash
cd /opt/vpn-project
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 web bot
curl -fsS http://127.0.0.1:8000/health
```

Ожидаемый ответ `/health`: `{"status":"ok"}`. Веб-сервис опубликован только на loopback VPS, PostgreSQL не имеет публичного порта. Контейнеры перезапускаются после перезагрузки сервера. Бот использует polling; если тот же токен ранее работал через webhook, удалите его через [Telegram `deleteWebhook`](https://core.telegram.org/bots/api#deletewebhook). Второй экземпляр бота с тем же токеном не запускайте.

## 5. Caddy, доступ и сертификат

У логина текущего прототипа пока нет ограничения числа попыток ввода одноразового кода. Для публичного домена пилота добавьте второй рубеж входа — HTTP Basic Auth **поверх HTTPS**. Создайте отдельный длинный пароль и получите его хеш интерактивно (Caddy не показывает ввод на экране):

```bash
sudo caddy hash-password --algorithm argon2id
sudo nano /etc/caddy/Caddyfile
```

На чистом VPS замените содержимое Caddyfile следующим блоком, подставив домен и полученный хеш:

```caddyfile
ADMIN_DOMAIN {
    basic_auth argon2id {
        admin ВСТАВЬТЕ_ХЕШ
    }
    reverse_proxy 127.0.0.1:8000
}
```

Если там уже есть другие сайты, добавьте блок, сохранив их настройки. [Caddy требует хеш пароля](https://caddyserver.com/docs/caddyfile/directives/basic_auth), а не открытый пароль. Примените конфигурацию:

```bash
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo journalctl -u caddy -n 80 --no-pager
```

`fmt` форматирует, `validate` проверяет, `reload` применяет Caddyfile без перезапуска Docker. Caddy автоматически выпустит и будет продлевать сертификат при корректном DNS и открытых 80/443.

## 6. Проверить доступ и админки

```bash
dig +short ADMIN_DOMAIN A
curl -I https://ADMIN_DOMAIN/
curl -fsS http://127.0.0.1:8000/health
docker compose -f /opt/vpn-project/docker-compose.yml ps
```

Без Basic Auth `curl -I` должен получить `401`, а браузер — показать окно ввода логина/пароля. Затем сайт открывается по `https://ADMIN_DOMAIN`. В Telegram отправьте **новому** боту `/admin` → «Код для сайта» и введите одноразовый код на сайте. В «Сквадах» запустите синхронизацию и назначьте сквады тестовым тарифам. При ошибке сертификата проверьте DNS, 80/443 и `journalctl -u caddy`.

Пилот готов, когда `db`, `web`, `bot` запущены; сертификат корректный; сайт запрашивает Basic Auth и одноразовый код; `/admin` доступен только вашему Telegram ID; нужные внутренние сквады видны; тариф сохраняется в обеих админках; рабочие Bedolaga и Remnawave продолжают работать независимо.

## 7. Резервные копии, обновление и откат

Перед обновлением сохраняйте дамп отдельной БД проекта:

```bash
cd /opt/vpn-project
mkdir -p backups
chmod 700 backups
docker compose exec -T db pg_dump -U vpn_bot -d vpn_bot -Fc > "backups/vpn-bot-$(date +%F-%H%M).dump"
```

Сохраните хеш текущего коммита и защищённую копию `.env`:

```bash
git rev-parse HEAD
git status --short
```

Затем обновите код и пересоберите сервисы:

```bash
git pull --ff-only
docker compose up -d --build
docker compose ps
```

`.env` и Docker volume с PostgreSQL при `git pull` не меняются. Для отката кода переключитесь на сохранённый коммит (`git switch --detach <СОХРАНЁННЫЙ_SHA>`) и повторите `docker compose up -d --build`; чтобы затем продолжить обычные обновления, вернитесь на рабочую ветку. Если обновление меняло схему или данные БД, сначала проверьте восстановление дампа на отдельной тестовой базе. Не используйте `docker compose down -v` для обычного обновления: `-v` удаляет том с БД.

Текущий прототип создаёт таблицы через `create_all`; до запуска реальных продаж понадобятся миграции, регулярные резервные копии, выдача подписок, платежи и бонусный ledger. Будущую точку webhook ЮKassa нужно проектировать отдельно: браузерный Basic Auth для неё не подходит; уведомления следует проверять по источнику и статусу платежа.
