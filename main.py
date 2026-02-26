import os
import logging
import requests
import subprocess
from io import BytesIO
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import tempfile
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

# Словник для зберігання вибору позиції користувача
user_position = {}

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

def get_watermark_position(img_width, img_height, watermark_width, watermark_height, position):
    """Визначає координати для водяного знаку"""
    padding = 20
    
    if position == "right":
        x = img_width - watermark_width - padding
        y = padding
    elif position == "left":
        x = padding
        y = padding
    
    return x, y

async def add_watermark_to_image(image_bytes: bytes, position: str) -> BytesIO:
    """Додає водяний знак до фото"""
    global watermark_image
    
    # Відкриваємо отримане фото
    img = Image.open(BytesIO(image_bytes)).convert('RGBA')
    
    # Копіюємо водяний знак
    watermark = watermark_image.copy()
    
    # Змінюємо розмір водяного знаку
    watermark.thumbnail((WATERMARK_SIZE, WATERMARK_SIZE), Image.Resampling.LANCZOS)
    
    # Регулюємо прозорість
    if WATERMARK_OPACITY < 1.0:
        alpha = watermark.split()[3]
        alpha = alpha.point(lambda p: p * WATERMARK_OPACITY)
        watermark.putalpha(alpha)
    
    # Отримуємо позицію
    x, y = get_watermark_position(img.width, img.height, watermark.width, watermark.height, position)
    
    # Накладаємо водяний знак
    img.paste(watermark, (x, y), watermark)
    
    # Конвертуємо назад в RGB
    result = img.convert('RGB')
    
    # Зберігаємо в байти
    output = BytesIO()
    result.save(output, format='JPEG', quality=95)
    output.seek(0)
    
    return output

async def add_watermark_to_video(input_bytes: bytes, position: str, is_gif: bool = False) -> BytesIO:
    """Додає водяний знак до відео або GIF через ffmpeg"""
    
    # Створюємо тимчасові файли
    temp_input = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
    temp_input.write(input_bytes)
    temp_input_path = temp_input.name
    temp_input.close()
    
    temp_output = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
    temp_output_path = temp_output.name
    temp_output.close()
    
    # Зберігаємо водяний знак як тимчасовий файл
    watermark_temp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    watermark_image.save(watermark_temp.name)
    watermark_temp.close()
    
    try:
        # Визначаємо позицію для ffmpeg
        if position == "right":
            overlay_pos = "main_w-overlay_w-20:20"
        else:  # left
            overlay_pos = "20:20"
        
        # Команда ffmpeg для накладання водяного знаку
        cmd = [
            'ffmpeg', '-i', temp_input_path,
            '-i', watermark_temp.name,
            '-filter_complex',
            f'[1:v]scale={WATERMARK_SIZE}:{WATERMARK_SIZE},format=rgba,colorchannelmixer=aa={WATERMARK_OPACITY}[watermark];[0:v][watermark]overlay={overlay_pos}',
            '-codec:a', 'copy',
            '-y', temp_output_path
        ]
        
        # Виконуємо команду
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"FFmpeg помилка: {result.stderr}")
            raise Exception("Помилка обробки відео")
        
        # Зчитуємо результат
        with open(temp_output_path, 'rb') as f:
            output_bytes = f.read()
        
        return BytesIO(output_bytes)
        
    except Exception as e:
        logger.error(f"Помилка: {e}")
        raise e
    finally:
        # Очищаємо тимчасові файли
        try:
            os.unlink(temp_input_path)
            os.unlink(temp_output_path)
            os.unlink(watermark_temp.name)
        except:
            pass

async def position_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує кнопки вибору позиції"""
    keyboard = [
        [
            InlineKeyboardButton("⬅️ Лівий верхній", callback_data="left"),
            InlineKeyboardButton("Правий верхній ➡️", callback_data="right")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎯 Виберіть позицію для водяного знаку:",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопок"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    position = query.data
    
    # Зберігаємо вибір користувача
    user_position[user_id] = position
    
    position_text = "⬅️ ЛІВИЙ верхній кут" if position == "left" else "➡️ ПРАВИЙ верхній кут"
    
    await query.edit_message_text(
        f"✅ Вибрано: {position_text}\n\nТепер відправ мені фото, відео або GIF!"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє фото"""
    try:
        if watermark_image is None:
            if not await load_watermark():
                await update.message.reply_text("❌ Помилка завантаження водяного знаку")
                return
        
        # Отримуємо позицію користувача
        user_id = update.message.from_user.id
        position = user_position.get(user_id, "right")  # За замовчуванням правий
        
        # Отримуємо фото
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        
        # Додаємо водяний знак
        watermarked_image = await add_watermark_to_image(image_bytes, position)
        
        # Відправляємо
        position_text = "лівому" if position == "left" else "правому"
        await update.message.reply_photo(
            photo=watermarked_image,
            caption=f"✅ Водяний знак додано у {position_text} верхньому куті!"
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
        
        # Отримуємо позицію користувача
        user_id = update.message.from_user.id
        position = user_position.get(user_id, "right")
        
        # Відправляємо повідомлення про обробку
        processing_msg = await update.message.reply_text("⏳ Обробка відео, зачекайте...")
        
        # Отримуємо відео
        video_file = await update.message.video.get_file()
        video_bytes = await video_file.download_as_bytearray()
        
        # Додаємо водяний знак
        watermarked_video = await add_watermark_to_video(video_bytes, position, is_gif=False)
        
        # Видаляємо повідомлення про обробку
        await processing_msg.delete()
        
        # Відправляємо
        position_text = "лівому" if position == "left" else "правому"
        await update.message.reply_video(
            video=watermarked_video,
            caption=f"✅ Водяний знак додано у {position_text} верхньому куті!"
        )
        
    except Exception as e:
        logger.error(f"Помилка: {e}")
        await update.message.reply_text(f"❌ Помилка: {str(e)}")

async def handle_animation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє GIF"""
    try:
        if watermark_image is None:
            if not await load_watermark():
                await update.message.reply_text("❌ Помилка завантаження водяного знаку")
                return
        
        # Отримуємо позицію користувача
        user_id = update.message.from_user.id
        position = user_position.get(user_id, "right")
        
        # Відправляємо повідомлення про обробку
        processing_msg = await update.message.reply_text("⏳ Обробка GIF, зачекайте...")
        
        # Отримуємо GIF
        animation_file = await update.message.animation.get_file()
        animation_bytes = await animation_file.download_as_bytearray()
        
        # Додаємо водяний знак
        watermarked_animation = await add_watermark_to_video(animation_bytes, position, is_gif=True)
        
        # Видаляємо повідомлення про обробку
        await processing_msg.delete()
        
        # Відправляємо
        position_text = "лівому" if position == "left" else "правому"
        await update.message.reply_animation(
            animation=watermarked_animation,
            caption=f"✅ Водяний знак додано у {position_text} верхньому куті!"
        )
        
    except Exception as e:
        logger.error(f"Помилка: {e}")
        await update.message.reply_text(f"❌ Помилка: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        "👋 Привіт! Я бот для додавання водяного знаку.\n\n"
        "Використовуй /position щоб вибрати:\n"
        "⬅️ Лівий верхній кут\n"
        "➡️ Правий верхній кут\n\n"
        "А потім відправ:\n"
        "📸 Фото\n"
        "🎥 Відео\n"
        "🖼 GIF"
    )

async def position_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /position"""
    await position_choice(update, context)

def main():
    """Запуск бота"""
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не знайдено в Railway Variables")
        return
    
    # Перевіряємо наявність ffmpeg
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True)
        logger.info("✅ FFmpeg знайдено")
    except:
        logger.error("❌ FFmpeg не знайдено!")
    
    # Створюємо додаток
    app = Application.builder().token(TOKEN).build()
    
    # Додаємо обробники
    app.add_handler(MessageHandler(filters.COMMAND & filters.Regex('^/start$'), start))
    app.add_handler(MessageHandler(filters.COMMAND & filters.Regex('^/position$'), position_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.ANIMATION, handle_animation))
    
    logger.info("✅ Бот запущено та готовий до роботи")
    app.run_polling()

if __name__ == '__main__':
    main()
