import os
import logging
import sqlite3
import html
from contextlib import closing
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp
from pathlib import Path
import re
import requests
import json
from openai import OpenAI

# Загрузка переменных окружения из файла .env
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Конфигурация внешнего API
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
client = OpenAI(
    base_url="https://api.polza.ai/api/v1",
    api_key=OPENAI_API_KEY,
)


# --- Программный DNS-fallback для обхода заблокированного системного DNS ---
# Если системный резолвер не может разрешить хост (например, домены YouTube в РФ),
# используем DNS-over-HTTPS (DoH) через прямое подключение к 1.1.1.1 по IP.
# Это не зависит от настроек VPN/системного DNS и применяется только тогда,
# когда обычное разрешение завершается ошибкой (getaddrinfo failed).
import socket as _socket


def _doh_query(hostname, rtype='A'):
    """Резолв хоста через DNS-over-HTTPS (Cloudflare 1.1.1.1)."""
    payload = requests.get(
        'https://1.1.1.1/dns-query',
        params={'name': hostname, 'type': rtype},
        headers={'accept': 'application/dns-json'},
        timeout=10,
    ).json()
    return [a['data'] for a in payload.get('Answer', []) if a.get('type') in (1, 28)]


def _doh_addrinfo(hostname, port, type=0, proto=0):
    """Собирает результаты в формате socket.getaddrinfo."""
    addresses = _doh_query(hostname)
    wanted_types = [type] if type in (_socket.SOCK_STREAM, _socket.SOCK_DGRAM) \
        else [_socket.SOCK_STREAM, _socket.SOCK_DGRAM]
    out = []
    for addr in addresses:
        family = _socket.AF_INET6 if ':' in addr else _socket.AF_INET
        for stype in wanted_types:
            out.append((family, stype, proto, '', (addr, port)))
    return out


_orig_getaddrinfo = _socket.getaddrinfo
_orig_gethostbyname = _socket.gethostbyname
_orig_gethostbyname_ex = _socket.gethostbyname_ex


def _safe_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    except _socket.gaierror:
        try:
            return _doh_addrinfo(host, port, type, proto)
        except Exception:
            raise


def _safe_gethostbyname(hostname):
    try:
        return _orig_gethostbyname(hostname)
    except _socket.gaierror:
        try:
            return _doh_query(hostname)[0]
        except Exception:
            raise


def _safe_gethostbyname_ex(hostname):
    try:
        return _orig_gethostbyname_ex(hostname)
    except _socket.gaierror:
        try:
            addrs = _doh_query(hostname)
            return (hostname, [], addrs)
        except Exception:
            raise


def install_dns_fallback():
    """Устанавливает DoH-fallback для разрешения имён."""
    _socket.getaddrinfo = _safe_getaddrinfo
    _socket.gethostbyname = _safe_gethostbyname
    _socket.gethostbyname_ex = _safe_gethostbyname_ex


install_dns_fallback()


# --- Хранилище заметок (SQLite, отдельная "папка" заметок на каждого пользователя ТГ) ---
DB_PATH = Path('./clipnote.db')


def init_db():
    """Создаёт таблицы папок и заметок, если их ещё нет"""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                folder_id INTEGER,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT,
                source_url TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (folder_id) REFERENCES folders (id)
            )
        ''')
        conn.commit()


def db_get_folders(user_id: int):
    """Список папок пользователя вместе с количеством заметок в каждой: [(id, name, count), ...]"""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute('''
            SELECT f.id, f.name, COUNT(n.id)
            FROM folders f
            LEFT JOIN notes n ON n.folder_id = f.id
            WHERE f.user_id = ?
            GROUP BY f.id, f.name
            ORDER BY f.name COLLATE NOCASE
        ''', (user_id,))
        return cur.fetchall()


def db_get_or_create_folder(user_id: int, name: str) -> int:
    """Возвращает id папки с таким названием, создавая её при необходимости"""
    name = name.strip()[:64]
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            'INSERT OR IGNORE INTO folders (user_id, name) VALUES (?, ?)',
            (user_id, name)
        )
        conn.commit()
        row = conn.execute(
            'SELECT id FROM folders WHERE user_id = ? AND name = ?',
            (user_id, name)
        ).fetchone()
        return row[0]


def db_save_note(user_id: int, title: str, content: str, tags: str, source_url: str, folder_id=None) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            'INSERT INTO notes (user_id, folder_id, title, content, tags, source_url) VALUES (?, ?, ?, ?, ?, ?)',
            (user_id, folder_id, title, content, tags, source_url)
        )
        conn.commit()
        return cur.lastrowid


def db_get_notes_without_folder_count(user_id: int) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            'SELECT COUNT(*) FROM notes WHERE user_id = ? AND folder_id IS NULL',
            (user_id,)
        ).fetchone()
        return row[0]


def db_get_notes(user_id: int, folder_id):
    """Список заметок в папке (folder_id=None — заметки без папки): [(id, title, created_at), ...]"""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        if folder_id is None:
            cur = conn.execute(
                'SELECT id, title, created_at FROM notes WHERE user_id = ? AND folder_id IS NULL ORDER BY created_at DESC',
                (user_id,)
            )
        else:
            cur = conn.execute(
                'SELECT id, title, created_at FROM notes WHERE user_id = ? AND folder_id = ? ORDER BY created_at DESC',
                (user_id, folder_id)
            )
        return cur.fetchall()


def db_get_note(user_id: int, note_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        return conn.execute(
            'SELECT title, content, tags, source_url FROM notes WHERE user_id = ? AND id = ?',
            (user_id, note_id)
        ).fetchone()


def db_update_note_field(user_id: int, note_id: int, field: str, value: str):
    """Обновляет название или текст заметки. field — 'title' или 'text' (текст -> колонка content)"""
    column = 'title' if field == 'title' else 'content'
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            f'UPDATE notes SET {column} = ? WHERE user_id = ? AND id = ?',
            (value, user_id, note_id)
        )
        conn.commit()


def build_folder_picker_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора папки для только что созданной заметки"""
    folders = db_get_folders(user_id)
    buttons = []
    row = []
    for folder_id, name, count in folders:
        row.append(InlineKeyboardButton(f"📁 {name} ({count})", callback_data=f"sf:{folder_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton("➕ Новая папка", callback_data="sf:new"),
        InlineKeyboardButton("📥 Без папки", callback_data="sf:none"),
    ])
    return InlineKeyboardMarkup(buttons)


def build_folders_overview_keyboard(user_id: int):
    """Клавиатура со списком папок для команды /notes. Возвращает None, если сохранять пока нечего"""
    folders = db_get_folders(user_id)
    no_folder_count = db_get_notes_without_folder_count(user_id)
    if not folders and not no_folder_count:
        return None
    buttons = [
        [InlineKeyboardButton(f"📁 {name} ({count})", callback_data=f"vf:{folder_id}")]
        for folder_id, name, count in folders
    ]
    if no_folder_count:
        buttons.append([InlineKeyboardButton(f"📥 Без папки ({no_folder_count})", callback_data="vf:none")])
    return InlineKeyboardMarkup(buttons)


# Кнопка быстрого доступа к заметкам — держим её на клавиатуре пользователя постоянно,
# чтобы попасть в свои заметки можно было в любой момент, не вспоминая команду /notes
NOTES_BUTTON_TEXT = "📚 Мои заметки"
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton(NOTES_BUTTON_TEXT)]],
    resize_keyboard=True
)


class VideoProcessor:
    """Класс для обработки видео"""
    
    def __init__(self):
        self.download_path = Path('./downloads')
        self.download_path.mkdir(exist_ok=True)
    def extract_video_info(self, url: str) -> dict:
        """Получает информацию о видео и транскрипцию"""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['ru'],
            'skip_download': True,
            'outtmpl': str(self.download_path / '%(id)s.%(ext)s')
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                # Получаем субтитры если есть
                subtitles_text = ""
                if 'subtitles' in info and info['subtitles']:
                    for lang in ['ru']:
                        if lang in info['subtitles']:
                            subtitles_text = self._extract_subtitle_text(info['subtitles'][lang])
                            break
                
                # Если нет обычных субтитров, пробуем автоматические
                if not subtitles_text and 'automatic_captions' in info:
                    for lang in ['ru']:
                        if lang in info['automatic_captions']:
                            subtitles_text = self._extract_subtitle_text(info['automatic_captions'][lang])
                            break
                
                return {
                    'title': info.get('title', 'Без названия'),
                    'description': info.get('description', ''),
                    'duration': info.get('duration', 0),
                    'thumbnail': info.get('thumbnail', ''),
                    'subtitles': subtitles_text,
                    'uploader': info.get('uploader', 'Неизвестно'),
                    'view_count': info.get('view_count', 0),
                    'tags': info.get('tags', []),
                }
        except Exception as e:
            logger.error(f"Ошибка при извлечении информации: {e}")
            raise
    
    def _extract_subtitle_text(self, subtitle_list: list) -> str:
        """Извлекает текст из субтитров"""
        # Выбираем формат с расширением vtt или srv3
        for sub in subtitle_list:
            if sub.get('ext') in ['vtt', 'srv3']:
                url = sub.get('url')
                if url:
                    try:
                        import requests
                        response = requests.get(url)
                        if response.status_code == 200:
                            # Очищаем от тегов VTT
                            text = re.sub(r'<[^>]+>', '', response.text)
                            text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}', '', text)
                            text = re.sub(r'WEBVTT.*?\n\n', '', text, flags=re.DOTALL)
                            text = '\n'.join(line.strip() for line in text.split('\n') if line.strip() and not line.strip().isdigit())
                            return text[:60000]  # Ограничиваем размер
                    except Exception as e:
                        logger.error(f"Ошибка при загрузке субтитров: {e}")
        return ""
    

def analyze_with_external_api(prompt: str) -> str:
    """Анализирует видео с помощью внешнего API"""
    try:
        response = client.chat.completions.create(
            model="z-ai/glm-5.3-flash",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that analyzes video content and creates summaries."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.0
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка при обращении к внешнему API: {e}")
        return "❌ Не удалось проанализировать видео через внешний API."

def generate_tags_from_content(title: str, content: str) -> str:
    """Генерирует 3 тега на основе названия и содержания видео"""
    try:
        # Формируем промпт для генерации тегов
        prompt = f"""На основе названия и содержания видео создай 3 релевантных тега на русском языке.
Теги должны быть короткими (1-2 слова), релевантными тематике видео и полезными для поиска.
ВАЖНО: Отвечай ТОЛЬКО тремя тегами, разделенными запятыми, без дополнительных комментариев.

Название: {title}

Содержание:
{content}
"""
        
        response = client.chat.completions.create(
            model="z-ai/glm-5.3-flash",
            messages=[
                {"role": "system", "content": "You are an assistant that creates relevant tags for content."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100500,
            temperature=0.5,
            timeout=30
        )
        
        if not response or not hasattr(response, 'choices') or not response.choices:
            logger.warning("Получен пустой ответ от API при генерации тегов")
            return ""
            
        choice = response.choices[0]
        if not choice or not hasattr(choice, 'message') or not choice.message:
            logger.warning("Получен некорректный ответ от API при генерации тегов")
            return ""
            
        message = choice.message
        if not hasattr(message, 'content') or not message.content:
            logger.warning("Получен ответ без содержимого от API при генерации тегов")
            return ""
        
        tags_text = response.choices[0].message.content.strip()
        logger.info(f"Сгенерированные теги: {tags_text}")
        
        # Преобразуем теги в нужный формат
        tags_list = [tag.strip().lower().replace(' ', '_') for tag in tags_text.split(',')]
        tags_list = [tag for tag in tags_list if tag]  # Удаляем пустые теги
        tags_list = tags_list[:3]  # Ограничиваем 3 тегами
        
        # Форматируем теги с символом #
        formatted_tags = " ".join([f"#{tag}" for tag in tags_list])
        return formatted_tags
    except Exception as e:
        logger.error(f"Ошибка при генерации тегов: {e}")
        return ""


def translate_to_russian(text: str) -> str:
    """Переводит текст на русский язык с помощью внешнего API"""
    try:
        # Проверяем, есть ли в тексте кириллические символы
        cyrillic_chars = len(re.findall('[а-яА-Я]', text))
        total_chars = len(re.findall('[a-zA-Zа-яА-Я]', text))
        
        logger.info(f"Анализ языка текста: '{text[:50]}...' - кириллические символы: {cyrillic_chars}, всего букв: {total_chars}")
        
        # Если кириллических символов мало или их нет, считаем текст не на русском
        if total_chars > 0 and cyrillic_chars / total_chars < 0.5:
            logger.info(f"Текст требует перевода: '{text[:50]}...'")
            # Переводим текст на русский язык
            prompt = f"Переведи следующий текст на русский язык. ВАЖНО: Отвечай ТОЛЬКО переведенным текстом, без дополнительных комментариев:\n\n{text}"
            
            response = client.chat.completions.create(
                model="z-ai/glm-5.3-flash",
                messages=[
                    {"role": "system", "content": "You are a translator. Translate the following text to Russian."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1000,
                temperature=0.1,
                timeout=30  # Добавляем таймаут для запроса
            )
            logger.info(f"Получен ответ от API перевода для текста: '{text[:50]}...'")
            # Проверяем, что ответ не пустой
            if not response or not hasattr(response, 'choices') or not response.choices:
                logger.warning(f"Получен пустой ответ от API перевода для текста: '{text[:50]}...'")
                return text
                
            choice = response.choices[0]
            if not choice or not hasattr(choice, 'message') or not choice.message:
                logger.warning(f"Получен некорректный ответ от API перевода для текста: '{text[:50]}...'")
                return text
                
            message = choice.message
            if not hasattr(message, 'content') or not message.content:
                logger.warning(f"Получен ответ без содержимого от API перевода для текста: '{text[:50]}...'")
                return text
                
            translated_text = response.choices[0].message.content.strip()
            logger.info(f"Текст переведен: '{text[:50]}...' -> '{translated_text[:50]}...'")
            logger.info(f"Длина оригинального текста: {len(text)}, длина переведенного текста: {len(translated_text)}")
            # Проверяем, что переведенный текст не пустой
            if not translated_text:
                logger.warning(f"Переведенный текст пустой, возвращаем оригинальный текст: '{text[:50]}...'")
                return text
            return translated_text
        else:
            logger.info(f"Текст не требует перевода (уже на русском): '{text[:50]}...'")
            # Если текст уже преимущественно на русском, возвращаем его как есть
            return text
    except Exception as e:
        logger.error(f"Ошибка при переводе текста: {e}")
        logger.error(f"Текст, который не удалось перевести: '{text[:100]}...'")
        return text  # Возвращаем оригинальный текст в случае ошибки

class AIAnalyzer:
    """Класс для анализа видео через AI"""
    
    @staticmethod
    def analyze_video(video_info: dict) -> str:
        """Создает конспект видео"""
        
        # Формируем промпт для AI
        prompt = f"""Create a concise summary based on the provided content.
Extract only the information that is communicated to the audience.
Do not mention the format or refer to it as a video.
Remove any references to the original source being a video.
Write briefly and strictly to the point.
Eliminate filler, repetitions, and meta-commentary.
The result must be a short, structured summary containing only the key information without losing meaning.

Название: {video_info['title']}


"""

        if video_info['description']:
            prompt += f"Описание:\n{video_info['description'][:1000]}\n\n"
        
        if video_info['subtitles']:
            prompt += f"Транскрипция (субтитры):\n{video_info['subtitles']}\n\n"
        
        prompt += """
Create a concise summary based on the provided content.
Extract only the information that is communicated to the audience.
Do not mention the format or refer to it as a video.
Remove any references to the original source being a video.
Write briefly and strictly to the point.
Eliminate filler, repetitions, and meta-commentary.
The result must be a short, structured summary containing only the key information without losing meaning."""

        try:
            # Получаем анализ от нейросети
            analysis = analyze_with_external_api(prompt)
            # Переводим результат на русский язык
            translated_analysis = translate_to_russian(analysis)
            return translated_analysis
        except Exception as e:
            logger.error(f"Ошибка при обращении к внешнему API: {e}")
            return "❌ Не удалось проанализировать видео через AI. Попробуйте позже."

# Инициализируем процессоры
video_processor = VideoProcessor()
ai_analyzer = AIAnalyzer()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_message = """
ClipNote 🤖📝
Отправь ссылку на короткое видео — я превращу его в заметку.
Суть, ключевые мысли и порядок вместо хаоса сохранёнок.

Кнопка «📚 Мои заметки» внизу всегда под рукой — открывает ваши папки в любой момент.
"""
    await update.message.reply_text(welcome_message, reply_markup=MAIN_KEYBOARD)

async def process_video_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ссылок на видео"""
    url = update.message.text.strip()
    
    # Проверяем, что это похоже на URL
    if not re.match(r'https?://', url):
        await update.message.reply_text("❌ Пожалуйста, отправьте корректную ссылку на видео (начинающуюся с http:// или https://)")
        return
    
    # Отправляем сообщение о начале обработки
    processing_msg = await update.message.reply_text("⏳ Обрабатываю видео... Это может занять некоторое время.")
    
    try:
        # Извлекаем информацию о видео
        await processing_msg.edit_text("📥 Создаю заметку на основе видео...")
        video_info = video_processor.extract_video_info(url)
        # Проверяем длительность видео (не более 3 минут)
        if video_info['duration'] > 180:  # 180 секунд = 3 минуты
            await processing_msg.edit_text("❌ Видео слишком длинное. Оно должно быть не длиннее 3 минут.")
            return
        # Анализируем через AI
        await processing_msg.edit_text("🤖 Анализирую контент...")
        analysis = ai_analyzer.analyze_video(video_info)
        
        # Формируем итоговое сообщение с названием видео, отступами и тегами
        # Используем только название видео в верхнем регистре без тегов
        # Удаляем возможные теги из названия
        logger.info(f"Оригинальное название видео: '{video_info['title']}'")
        logger.info(f"Длина оригинального названия: {len(video_info['title'])}")
        translated_title = translate_to_russian(video_info['title'])
        logger.info(f"Название видео после перевода: '{translated_title}'")
        logger.info(f"Длина названия после перевода: {len(translated_title)}")
        clean_title = re.sub(r'#\w+', '', translated_title).strip().upper()
        logger.info(f"Окончательное название видео: '{clean_title}'")
        
        # Генерируем 3 тега на основе содержания конспекта
        tags = generate_tags_from_content(video_info['title'], analysis)
        
        result_message = f"""
[ {clean_title} ]

{analysis}

{tags}
"""
        
        # Ограничиваем длину сообщения до 4096 символов (лимит Telegram)
        if len(result_message) > 4096:
            result_message = result_message[:4093] + "..."
        
        # Удаляем сообщение о процессе
        try:
            await processing_msg.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение о процессе: {e}")
        
        # Отправляем текстовое сообщение
        await update.message.reply_text(result_message, parse_mode='HTML')

        # Заметка ещё не сохранена в базу — ждём, куда пользователь захочет её положить
        context.user_data['pending_note'] = {
            'title': clean_title,
            'content': analysis,
            'tags': tags,
            'source_url': url,
        }
        await update.message.reply_text(
            "Куда сохранить заметку?",
            reply_markup=build_folder_picker_keyboard(update.effective_user.id)
        )

    except Exception as e:
        logger.error(f"Ошибка при обработке видео: {e}")
        # Проверяем, существует ли еще сообщение о процессе перед редактированием
        try:
            await processing_msg.edit_text(
                f"❌ Произошла ошибка при обработке видео:\n{str(e)}\n\n"
                "Убедитесь, что ссылка корректна и видео доступно."
            )
        except Exception as edit_error:
            logger.warning(f"Не удалось отредактировать сообщение о процессе: {edit_error}")
            # Если не удалось отредактировать, отправляем новое сообщение
            await update.message.reply_text(
                f"❌ Произошла ошибка при обработке видео:\n{str(e)}\n\n"
                "Убедитесь, что ссылка корректна и видео доступно."
            )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
📖 <b>Как пользоваться ботом:</b>

1️⃣ Найдите видео на YouTube или другой платформе
2️⃣ Скопируйте ссылку на видео
3️⃣ Отправьте ссылку боту
4️⃣ Получите заметку полностью отражающую соедржание видео
5️⃣ Выберите папку, в которую её сохранить (или создайте новую)

<b>Команды:</b>
/start - Начать работу с ботом
/help - Показать эту справку
/notes - Открыть сохранённые заметки по папкам

<b>Что я могу:</b>
✅ Анализировать короткие видео
✅ Создавать на основе видео заметки с ключевым содержанием
✅ Хранить ваши заметки в личных папках по темам
✅ Редактировать текст и название сохранённых заметок вручную

<i>Бот работает на основе внешнего API и yt-dlp</i>
"""
    await update.message.reply_text(help_text, parse_mode='HTML', reply_markup=MAIN_KEYBOARD)


async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает папки пользователя с заметками — по команде /notes или кнопке «📚 Мои заметки».
    Доступно в любой момент: если пользователь был на середине ввода названия папки
    или правки заметки, это действие отменяется, а не тихо ломается."""
    had_awaiting_folder = context.user_data.pop('awaiting_folder_name', False)
    had_editing_note = context.user_data.pop('editing_note', None) is not None
    if had_awaiting_folder or had_editing_note:
        await update.message.reply_text("Предыдущее незавершённое действие отменено.")

    keyboard = build_folders_overview_keyboard(update.effective_user.id)
    if keyboard is None:
        await update.message.reply_text(
            "У вас пока нет сохранённых заметок. Отправьте ссылку на видео, чтобы создать первую!",
            reply_markup=MAIN_KEYBOARD
        )
        return
    await update.message.reply_text("📚 Ваши папки с заметками:", reply_markup=keyboard)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Маршрутизирует обычные текстовые сообщения: кнопку заметок, название новой папки,
    правку заметки или ссылку на видео"""
    if update.message.text.strip() == NOTES_BUTTON_TEXT:
        await notes_command(update, context)
        return
    if context.user_data.get('awaiting_folder_name'):
        await save_pending_note_to_new_folder(update, context)
        return
    if context.user_data.get('editing_note'):
        await save_note_edit(update, context)
        return
    await process_video_link(update, context)


async def save_pending_note_to_new_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создаёт папку с введённым названием и кладёт в неё отложенную заметку"""
    context.user_data['awaiting_folder_name'] = False
    user_id = update.effective_user.id
    folder_name = update.message.text.strip()

    pending = context.user_data.get('pending_note')
    if not pending:
        await update.message.reply_text("Заметка для сохранения не найдена — отправьте ссылку на видео заново.")
        return

    if not folder_name:
        await update.message.reply_text("Название папки не может быть пустым. Попробуйте ещё раз.")
        context.user_data['awaiting_folder_name'] = True
        return

    folder_id = db_get_or_create_folder(user_id, folder_name)
    db_save_note(
        user_id, pending['title'], pending['content'], pending['tags'], pending['source_url'],
        folder_id=folder_id
    )
    context.user_data.pop('pending_note', None)
    await update.message.reply_text(f"✅ Заметка сохранена в новую папку «{folder_name}».")


async def folder_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатие кнопки выбора папки сразу после создания заметки"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    pending = context.user_data.get('pending_note')
    if not pending:
        await query.edit_message_text("Эта заметка уже сохранена или устарела.")
        return

    if data == "sf:new":
        context.user_data['awaiting_folder_name'] = True
        await query.edit_message_text("Введите название новой папки одним сообщением:")
        return

    if data == "sf:none":
        db_save_note(
            user_id, pending['title'], pending['content'], pending['tags'], pending['source_url'],
            folder_id=None
        )
        context.user_data.pop('pending_note', None)
        await query.edit_message_text("✅ Заметка сохранена без папки.")
        return

    folder_id = int(data.split(":", 1)[1])
    db_save_note(
        user_id, pending['title'], pending['content'], pending['tags'], pending['source_url'],
        folder_id=folder_id
    )
    context.user_data.pop('pending_note', None)
    await query.edit_message_text("✅ Заметка сохранена в папку.")


async def notes_browse_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает навигацию по папкам и заметкам через /notes"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "vback":
        keyboard = build_folders_overview_keyboard(user_id)
        if keyboard is None:
            await query.edit_message_text("Заметок пока нет.")
            return
        await query.edit_message_text("📚 Ваши папки с заметками:", reply_markup=keyboard)
        return

    if data.startswith("vf:"):
        folder_key = data.split(":", 1)[1]
        folder_id = None if folder_key == "none" else int(folder_key)
        notes = db_get_notes(user_id, folder_id)
        if not notes:
            await query.edit_message_text("В этой папке пока нет заметок.")
            return
        buttons = [
            [InlineKeyboardButton(title[:40], callback_data=f"vn:{note_id}")]
            for note_id, title, _created_at in notes
        ]
        buttons.append([InlineKeyboardButton("« Назад к папкам", callback_data="vback")])
        await query.edit_message_text("Заметки:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("vn:"):
        note_id = int(data.split(":", 1)[1])
        note = db_get_note(user_id, note_id)
        if not note:
            await query.edit_message_text("Заметка не найдена.")
            return
        title, content, tags, _source_url = note
        text = f"[ {title} ]\n\n{content}\n\n{tags or ''}".strip()
        if len(text) > 4096:
            text = text[:4093] + "..."
        edit_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Изменить текст", callback_data=f"ed:text:{note_id}")],
            [InlineKeyboardButton("🏷 Изменить название", callback_data=f"ed:title:{note_id}")],
        ])
        await query.message.reply_text(text, parse_mode='HTML', reply_markup=edit_buttons)
        return


async def note_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатие кнопки редактирования заметки (текст или название)"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    _, field, note_id_str = query.data.split(":", 2)
    note_id = int(note_id_str)

    note = db_get_note(user_id, note_id)
    if not note:
        await query.message.reply_text("Заметка не найдена — возможно, она уже удалена.")
        return

    title, content, _tags, _source_url = note
    current_value = title if field == 'title' else content

    context.user_data['editing_note'] = {'id': note_id, 'field': field}

    # Показываем текущий текст моноширинным блоком — в Telegram по нему достаточно
    # один раз тапнуть, чтобы скопировать его целиком в буфер обмена
    display_value = current_value
    if len(display_value) > 3500:
        display_value = display_value[:3500] + "…"

    prompt = "новое название заметки" if field == 'title' else "новый текст заметки"
    await query.message.reply_text(
        "Текущий вариант (нажмите на текст, чтобы скопировать):\n\n"
        f"<code>{html.escape(display_value)}</code>\n\n"
        f"Отправьте {prompt} одним сообщением — можно вставить скопированный текст и поправить его.",
        parse_mode='HTML'
    )


async def save_note_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет новое название или текст заметки, введённые пользователем вручную"""
    user_id = update.effective_user.id
    editing = context.user_data.pop('editing_note', None)
    if not editing:
        await update.message.reply_text("Не понял, какую заметку вы редактируете — откройте её заново через /notes.")
        return

    new_value = update.message.text.strip()
    if not new_value:
        await update.message.reply_text("Текст не может быть пустым. Редактирование отменено.")
        return

    db_update_note_field(user_id, editing['id'], editing['field'], new_value)
    field_label = "Название" if editing['field'] == 'title' else "Текст"
    await update.message.reply_text(f"✅ {field_label} заметки обновлён.")

async def post_init(application: Application):
    """Регистрирует список команд в системном меню Telegram («/» рядом с полем ввода),
    чтобы /notes было видно и без набора вручную — ещё один способ попасть в заметки в любой момент"""
    await application.bot.set_my_commands([
        BotCommand("start", "Начать работу с ботом"),
        BotCommand("help", "Как пользоваться ботом"),
        BotCommand("notes", "Открыть мои заметки"),
    ])


def main():
    """Запуск бота"""
    if not TELEGRAM_TOKEN:
        raise ValueError("Не установлен TELEGRAM_BOT_TOKEN!")

    # Инициализируем базу данных заметок (создаёт clipnote.db при первом запуске)
    init_db()

    # Создаем приложение
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("notes", notes_command))
    application.add_handler(CallbackQueryHandler(folder_choice_callback, pattern=r'^sf:'))
    application.add_handler(CallbackQueryHandler(notes_browse_callback, pattern=r'^v'))
    application.add_handler(CallbackQueryHandler(note_edit_callback, pattern=r'^ed:'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Запускаем бота
    logger.info("Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

