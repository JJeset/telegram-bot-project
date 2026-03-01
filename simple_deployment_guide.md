# Простое развертывание Telegram Video Bot

## Обзор

Это руководство по развертыванию вашего текущего Telegram Video Bot на облачной платформе с возможностью быстрого обновления. Мы будем использовать Render.com как самую простую опцию для быстрого старта.

## Вариант 1: Render.com (Рекомендуется для быстрого старта)

### Преимущества:

- Бесплатный тариф для простых ботов
- Автоматическое развертывание из GitHub
- Простая настройка
- Встроенная поддержка Python

### Шаги для развертывания:

1. **Подготовка репозитория**
   - Создайте новый репозиторий на GitHub
   - Загрузите туда все файлы бота:
     - `telegram_video_bot.py`
     - `requirements.txt`
     - `env.example`
     - `README.md` (опционально)
   - Не загружайте `.env` и `cookies.txt` в репозиторий

2. **Создание аккаунта на Render**
   - Перейдите на https://render.com
   - Зарегистрируйтесь с помощью GitHub аккаунта

3. **Развертывание бота**
   - Нажмите "New" → "Web Service"
   - Подключите ваш GitHub репозиторий
   - Настройте параметры:
     - Name: `telegram-video-bot`
     - Region: `Frankfurt` (или другой ближайший)
     - Branch: `main`
     - Runtime: `Python 3`
     - Build Command: `pip install -r requirements.txt`
     - Start Command: `python telegram_video_bot.py`
     - Instance Type: `Free`

4. **Настройка переменных окружения**
   - В разделе "Environment Variables" добавьте:
     - `TELEGRAM_BOT_TOKEN`=ваш_токен
     - `OPENAI_API_KEY`=ваш*api*ключ
     - `EXTERNAL_API_URL`=https://api.polza.ai/api/v1

5. **Настройка автоматического развертывания**
   - Render автоматически пересобирает приложение при пуше в репозиторий
   - Для ручного развертывания нажмите "Manual Deploy" → "Deploy latest commit"

## Вариант 2: Heroku (Альтернатива)

### Преимущества:

- Широко используемая платформа
- Хорошая документация
- Простое масштабирование

### Подготовка для Heroku:

1. **Создание файлов для Heroku**

   Создайте файл `Procfile` со следующим содержимым:

   ```
   worker: python telegram_video_bot.py
   ```

2. **Создание runtime файла**

   Создайте файл `runtime.txt` со следующим содержимым:

   ```
   python-3.10.13
   ```

3. **Обновление requirements.txt**

   Убедитесь, что `requirements.txt` содержит:

   ```
   python-telegram-bot==20.7
   yt-dlp>=2024.1.19
   openai>=1.0.0
   requests==2.31.0
   python-dotenv==1.0.1
   ```

## Вариант 3: VPS с systemd (Для более продвинутых пользователей)

### Преимущества:

- Полный контроль над окружением
- Нет ограничений по ресурсам
- Возможность настройки под конкретные нужды

### Шаги для настройки:

1. **Подготовка VPS**
   - Закажите VPS с Ubuntu 20.04+ (например, на DigitalOcean, Vultr, Hetzner)
   - Рекомендуемая конфигурация: 2 CPU, 4GB RAM, 50GB SSD

2. **Установка зависимостей**

   ```bash
   sudo apt update
   sudo apt install -y python3 python3-pip git ffmpeg
   ```

3. **Клонирование репозитория**

   ```bash
   git clone https://github.com/ваш_логин/название_репозитория.git
   cd название_репозитория
   pip3 install -r requirements.txt
   ```

4. **Настройка systemd сервиса**

   Создайте файл `/etc/systemd/system/telegram-bot.service`:

   ```ini
   [Unit]
   Description=Telegram Video Bot
   After=network.target

   [Service]
   Type=simple
   User=ваш_пользователь
   WorkingDirectory=/путь/к/вашему/боту
   ExecStart=/usr/bin/python3 /путь/к/вашему/боту/telegram_video_bot.py
   Restart=always
   RestartSec=10
   Environment=TELEGRAM_BOT_TOKEN=ваш_токен
   Environment=OPENAI_API_KEY=ваш_api_ключ
   Environment=EXTERNAL_API_URL=https://api.polza.ai/api/v1

   [Install]
   WantedBy=multi-user.target
   ```

5. **Запуск сервиса**

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable telegram-bot
   sudo systemctl start telegram-bot
   sudo systemctl status telegram-bot
   ```

6. **Настройка автообновления**

   Создайте скрипт `update.sh`:

   ```bash
   #!/bin/bash
   cd /путь/к/вашему/боту
   git pull origin main
   pip3 install -r requirements.txt
   sudo systemctl restart telegram-bot
   ```

## Рекомендации по быстрому обновлению

### Для Render/Heroku:

1. Просто сделайте git push в ваш репозиторий
2. Платформа автоматически пересоберет и перезапустит приложение

### Для VPS:

1. Используйте скрипт обновления:

   ```bash
   #!/bin/bash
   cd /path/to/your/bot
   git pull
   sudo systemctl restart telegram-bot
   ```

2. Или используйте GitHub Actions для автоматического обновления:
   ```yaml
   name: Deploy to VPS
   on:
     push:
       branches: [main]
   jobs:
     deploy:
       runs-on: ubuntu-latest
       steps:
         - name: Deploy to server
           uses: appleboy/ssh-action@v0.1.5
           with:
             host: ${{ secrets.HOST }}
             username: ${{ secrets.USERNAME }}
             key: ${{ secrets.KEY }}
             script: |
               cd /path/to/your/bot
               git pull
               pip3 install -r requirements.txt
               sudo systemctl restart telegram-bot
   ```

## Резервное копирование cookies.txt

Поскольку файл `cookies.txt` содержит важные данные для доступа к YouTube, рекомендуется:

1. **Для Render/Heroku**:
   - Добавьте содержимое файла как переменную окружения
   - Или используйте secrets management

2. **Для VPS**:
   - Храните файл в безопасном месте
   - Регулярно обновляйте его
   - Делайте резервные копии

## Мониторинг

### Для Render/Heroku:

- Используйте встроенные логи платформы
- Настройте уведомления о падении сервиса

### Для VPS:

- Используйте journalctl для просмотра логов:
  ```bash
  sudo journalctl -u telegram-bot -f
  ```

## Следующие шаги

1. Выберите подходящий вариант для вашего уровня опыта
2. Начните с самого простого (Render.com)
3. По мере роста потребностей переходите к более продвинутым решениям
4. Не забудьте регулярно обновлять зависимости и следить за безопасностью

## Необходимые файлы для развертывания

Для успешного развертывания на различных платформах вам понадобятся следующие файлы:

1. `Procfile` - для Heroku, указывает команду запуска приложения
2. `runtime.txt` - для Heroku, указывает версию Python
3. `requirements.txt` - список зависимостей Python
4. `.gitignore` - для исключения конфиденциальных файлов из репозитория

Пример содержимого этих файлов:

**Procfile:**

```
worker: python telegram_video_bot.py
```

**runtime.txt:**

```
python-3.10.13
```

**.gitignore:**

```
.env
cookies.txt
downloads/
*.log
__pycache__/
*.pyc
```
