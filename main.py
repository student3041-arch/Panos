import os
import logging
import requests
from io import BytesIO
from PIL import Image
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import tempfile
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
import numpy as np
import asyncio

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Зчитуємо змінні з Railway
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
WATERMARK_URL = os.environ.get('WATERMARK_URL')
WATERMARK_OPACITY = float(os.environ.get('WATERMARK_OPACITY', '0.5'))
WATERMARK_SIZE = int(os.environ.get('WATERMARK_SIZE', '100'))

watermark_image = None

async def load_watermark():
    """Завантажує водяний знак з URL"""
    global watermark_image
    try:
        if WATERMARK_URL:
            response = requests.get(WATERMARK_URL)
            watermark_image = Image.open(BytesIO(response.content)).convert('RGBA')
            logger.info("✅ Водяний знак завантажено з URL")
            return True
    except Exception as e:
        logger.error(f"❌ Не вдалося завантажити водяний знак: {e}")
        return False

async def add_watermark_to_image(image_bytes: bytes) -> BytesIO:
    """Додає водяний знак до фото"""
    global watermark_image
    
    # Відкриваємо отримане фото
    img = Image.open(BytesIO(image_bytes)).convert('RGBA')
    
    # Копіюємо водяний знак
    watermark = watermark_image.copy()
    
    # Змінюємо розмір водяного знаку - ВИПРАВЛЕНО!
    watermark.thumbnail((WATERMARK_SIZE, WATERMARK_SIZE), Image.Resampling.LANCZOS)
    
    # Регулюємо прозорість
    if WATERMARK_OPACITY < 1.0:
        pixels = watermark.load()
        for i in range(watermark.width):
            for j in range(watermark.height):
                r, g, b, a = pixels[i, j]
                pixels[i, j] = (r, g, b, int(a * WATERMARK_OPACITY))
    
    # Позиція (правий верхній кут)
    padding = 20
    x = img.width - watermark.width - padding
    y = padding
    
    # Накладаємо водяний знак
    img.paste(watermark, (x, y), watermark)
    
    # Конвертуємо назад в RGB
    result = img.convert('RGB')
    
    # Зберігаємо в байти
    output = BytesIO()
    result.save(output, format='JPEG', quality=95)
    output.seek(0)
    
    return output

async def add_watermark_to_video(input_bytes: bytes, is_gif: bool = False) -> BytesIO:
    """Додає водяний знак до відео або GIF"""
    global watermark_image
    
    # Створюємо тимчасові файли
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_input:
        temp_input.write(input_bytes)
        temp_input_path = temp_input.name
    
    temp_output_path = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False).name
    
    try:
        # Завантажуємо відео
        video = VideoFileClip(temp_input_path)
        
        # Конвертуємо PIL Image в numpy array для moviepy
        watermark_array = np.array(watermark_image)
        watermark_clip = ImageClip(watermark_array, ismask=False, transparent=True)
        
        # Змінюємо розмір водяного знаку - ВИПРАВЛЕНО!
        watermark_clip = watermark_clip.resize(height=WATERMARK_SIZE)
        
        # Встановлюємо прозорість
        watermark_clip = watermark_clip.set_opacity(WATERMARK_OPACITY)
        
        # Встановлюємо позицію (правий верхній кут)
        padding = 20
        watermark_clip = watermark_clip.set_position((video.w - watermark_clip.w - padding, padding))
        
        # Встановлюємо тривалість як у відео
        watermark_clip = watermark_clip.set_duration(video.duration)
        
        # Накладаємо водяний знак
        final = CompositeVideoClip([video, watermark_clip])
        
        # Зберігаємо результат
        if is_gif:
            final.write_gif(temp_output_path, fps=video.fps)
        else:
            final.write_videofile(temp_output_path, codec='libx264', audio_codec='aac')
        
        # Зчитуємо результат
        with open(temp_output_path, 'rb') as f:
            output_bytes = f.read()
        
        return BytesIO(output_bytes)
        
    except Exception as e:
        logger.error(f"Помилка обробки відео: {e}")
        raise e
    finally:
        # Очищаємо тимчасові файли
        try:
            os.unlink(temp_input_path)
            os.unlink(temp_output_path)
        except:
            pass

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє фото"""
    try:
        if watermark_image is None:
            if not await load_watermark():
                await update.message.reply_text("❌ Помилка завантаження водяного знаку")
                return
        
        # Отримуємо фото
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        
        # Додаємо водяний знак
        watermarked_image = await add_watermark_to_image(image_bytes)
        
        # Відправляємо
        await update.message.reply_photo(
            photo=watermarked_image,
            caption="✅ Водяний знак додано до фото!"
        )
        
    except Exception as e:
        logger.error(f"Помилка: {e}")
        await update.message.reply_text(f"❌ Помилка: {str(e)}")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє відео"""
    try:
        if watermark_image is None:
            if not await load_watermark():
                await update.message.reply_text("❌ Помилка завантаження водяного знаку")
                return
        
        # Відправляємо повідомлення про обробку
        processing_msg = await update.message.reply_text("⏳ Обробка відео, зачекайте...")
        
        # Отримуємо відео
        video_file = await update.message.video.get_file()
        video_bytes = await video_file.download_as_bytearray()
        
        # Додаємо водяний знак
        watermarked_video = await add_watermark_to_video(video_bytes, is_gif=False)
        
        # Видаляємо повідомлення про обробку
        await processing_msg.delete()
        
        # Відправляємо
        await update.message.reply_video(
            video=watermarked_video,
            caption="✅ Водяний знак додано до відео!"
        )
        
    except Exception as e:
        logger.error(f"Помилка: {e}")
        await update.message.reply_text(f"❌ Помилка: {str(e)}")

async def handle_animation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє GIF (animation)"""
    try:
        if watermark_image is None:
            if not await load_watermark():
                await update.message.reply_text("❌ Помилка завантаження водяного знаку")
                return
        
        # Відправляємо повідомлення про обробку
        processing_msg = await update.message.reply_text("⏳ Обробка GIF, зачекайте...")
        
        # Отримуємо GIF
        animation_file = await update.message.animation.get_file()
        animation_bytes = await animation_file.download_as_bytearray()
        
        # Додаємо водяний знак
        watermarked_animation = await add_watermark_to_video(animation_bytes, is_gif=True)
        
        # Видаляємо повідомлення про обробку
        await processing_msg.delete()
        
        # Відправляємо
        await update.message.reply_animation(
            animation=watermarked_animation,
            caption="✅ Водяний знак додано до GIF!"
        )
        
    except Exception as e:
        logger.error(f"Помилка: {e}")
        await update.message.reply_text(f"❌ Помилка: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        "👋 Привіт! Я бот для додавання водяного знаку.\n\n"
        "Просто відправ мені:\n"
        "📸 Фото\n"
        "🎥 Відео\n"
        "🖼 GIF\n\n"
        "Я додам водяний знак у правий верхній кут!"
    )

def main():
    """Запуск бота"""
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не знайдено в Railway Variables")
        return
    
    # Створюємо додаток
    app = Application.builder().token(TOKEN).build()
    
    # Додаємо обробники
    app.add_handler(MessageHandler(filters.COMMAND, start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.ANIMATION, handle_animation))
    
    logger.info("✅ Бот запущено та готовий до роботи")
    app.run_polling()

if __name__ == '__main__':
    main()
