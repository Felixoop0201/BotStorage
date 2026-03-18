import asyncio
import os
import logging
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn

import database as db

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8000))

if not TOKEN:
    logging.error("❌ BOT_TOKEN не найден в .env или переменных окружения")
    exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=TOKEN)
dp = Dispatcher()
db.init_db()

# --- СОСТОЯНИЯ (FSM) ---
class StorageStates(StatesGroup):
    waiting_for_folder_name = State()
    waiting_for_file = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_main_kb(user_id):
    builder = InlineKeyboardBuilder()
    folders = db.get_folders(user_id)
    for folder in folders:
        builder.button(text=f"📁 {folder}", callback_data=f"open:{folder}")
    builder.button(text="➕ Создать папку", callback_data="create_folder")
    builder.adjust(1)
    return builder.as_markup()

# --- ХЕНДЛЕРЫ БОТА ---
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "👋 Привет! Я твое личное облако.\nСоздавай папки и храни в них фото, видео и документы.", 
        reply_markup=get_main_kb(message.from_user.id)
    )

@dp.callback_query(F.data == "create_folder")
async def ask_folder_name(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("✍️ Введи название для новой папки:")
    await state.set_state(StorageStates.waiting_for_folder_name)

@dp.message(StorageStates.waiting_for_folder_name)
async def process_folder_name(message: types.Message, state: FSMContext):
    folder_name = message.text.strip()
    if db.create_folder(message.from_user.id, folder_name):
        await message.answer(f"✅ Папка '{folder_name}' создана!", reply_markup=get_main_kb(message.from_user.id))
    else:
        await message.answer("⚠️ Такая папка уже есть или название некорректно. Попробуй другое имя.")
    await state.clear()

@dp.callback_query(F.data.startswith("open:"))
async def open_folder(callback: CallbackQuery, state: FSMContext):
    folder_name = callback.data.split(":")[1]
    await state.update_data(current_folder=folder_name)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Добавить файл", callback_data="add_file")
    builder.button(text="👀 Посмотреть файлы", callback_data="view_files")
    builder.button(text="🗑 Удалить папку", callback_data=f"del_folder:{folder_name}")
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    builder.adjust(2)
    
    await callback.message.edit_text(f"📂 Папка: {folder_name}\nЧто сделаем?", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "add_file")
async def ask_file(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🖇 Отправь мне любой файл (фото, видео, документ, аудио).\nЯ сохраню его в текущую папку.")
    await state.set_state(StorageStates.waiting_for_file)

@dp.message(StorageStates.waiting_for_file)
async def handle_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    folder_name = data.get("current_folder")
    folder_id = db.get_folder_id(message.from_user.id, folder_name)
    
    if not folder_id:
        await message.answer("❌ Ошибка: папка не найдена. Попробуй еще раз.")
        await state.clear()
        return

    # Карта типов файлов
    file_handlers = {
        "photo": lambda m: m.photo[-1].file_id if m.photo else None,
        "video": lambda m: m.video.file_id if m.video else None,
        "document": lambda m: m.document.file_id if m.document else None,
        "audio": lambda m: m.audio.file_id if m.audio else None,
        "voice": lambda m: m.voice.file_id if m.voice else None,
    }

    file_id = None
    file_type = None

    for f_type, getter in file_handlers.items():
        file_id = getter(message)
        if file_id:
            file_type = f_type
            break
    
    if file_id:
        db.add_file(folder_id, file_id, file_type, message.caption)
        await message.answer(f"✅ Файл сохранен в '{folder_name}'!", reply_markup=get_main_kb(message.from_user.id))
        await state.clear()
    else:
        await message.answer("❌ Я не поддерживаю такой тип файла. Попробуй отправить фото, видео, документ или аудио.")

@dp.callback_query(F.data == "view_files")
async def view_files(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    folder_name = data.get("current_folder")
    folder_id = db.get_folder_id(callback.from_user.id, folder_name)
    files = db.get_files(folder_id)
    
    if not files:
        await callback.answer("В этой папке пока пусто 💨", show_alert=True)
        return

    await callback.message.answer(f"📦 Содержимое '{folder_name}':")
    for f_id, f_type, caption in files:
        try:
            if f_type == "photo":
                await callback.message.answer_photo(f_id, caption=caption)
            elif f_type == "video":
                await callback.message.answer_video(f_id, caption=caption)
            elif f_type == "document":
                await callback.message.answer_document(f_id, caption=caption)
            elif f_type == "audio":
                await callback.message.answer_audio(f_id, caption=caption)
            elif f_type == "voice":
                await callback.message.answer_voice(f_id, caption=caption)
        except Exception as e:
            logger.error(f"Ошибка при отправке файла {f_id}: {e}")
            continue
    
    await callback.message.answer("--- Конец папки ---", reply_markup=get_main_kb(callback.from_user.id))

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("🗂 Твои папки:", reply_markup=get_main_kb(callback.from_user.id))

@dp.callback_query(F.data.startswith("del_folder:"))
async def del_folder(callback: CallbackQuery):
    folder_name = callback.data.split(":")[1]
    db.delete_folder(callback.from_user.id, folder_name)
    await callback.message.edit_text(f"🗑 Папка '{folder_name}' удалена вместе со всеми файлами.", reply_markup=get_main_kb(callback.from_user.id))

import asyncio
import os
import logging
import httpx
from contextlib import asynccontextmanager
...
# --- FASTAPI & LIFESPAN ---
async def keep_alive(url: str):
    """Функция для поддержания активности сервера (анти-сон Render)"""
    if not url:
        logger.warning("⚠️ SELF_URL не задан, само-пинг отключен.")
        return
    
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(600)  # Пауза 10 минут
            try:
                response = await client.get(url)
                logger.info(f"📡 Self-ping status: {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Self-ping error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Действия при запуске
    logger.info("🚀 Запуск бота...")
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    # Запуск само-пинга, если задан URL
    self_url = os.getenv("SELF_URL")
    ping_task = asyncio.create_task(keep_alive(self_url))
    
    yield
    # Действия при остановке
    logger.info("🛑 Остановка бота...")
    await dp.stop_polling()
    await bot.session.close()
    polling_task.cancel()
    ping_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def read_root():
    return {"status": "active", "service": "Cloud Storage Bot", "version": "1.1"}

# Монтируем статику
if os.path.exists("static"):
    app.mount("/site", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    logger.info(f"🌐 Запуск веб-сервера на порту {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
