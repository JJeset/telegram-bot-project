import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
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
            model="anthropic/claude-sonnet-4",
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
            model="anthropic/claude-sonnet-4",
            messages=[
                {"role": "system", "content": "You are an assistant that creates relevant tags for content."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50,
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
                model="anthropic/claude-sonnet-4",
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
"""
    await update.message.reply_text(welcome_message)

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
4️⃣ Получите заметку полностью отражающую соедржание видео.

<b>Команды:</b>
/start - Начать работу с ботом
/help - Показать эту справку

<b>Что я могу:</b>
✅ Анализировать короткие видео
✅ Создавать на основе видео заметки с ключевым содержанием

<i>Бот работает на основе внешнего API и yt-dlp</i>
"""
    await update.message.reply_text(help_text, parse_mode='HTML')

def main():
    """Запуск бота"""
    if not TELEGRAM_TOKEN:
        raise ValueError("Не установлен TELEGRAM_BOT_TOKEN!")
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_video_link))
    
    # Запускаем бота
    logger.info("Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

