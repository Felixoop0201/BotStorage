import asyncio
import os
import logging
import httpx
from contextlib import asynccontextmanager
from typing import List

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
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
    logging.error("❌ BOT_TOKEN не найден")
    exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=TOKEN)
dp = Dispatcher()
db.init_db()

# --- СОСТОЯНИЯ (FSM) ---
class StorageStates(StatesGroup):
    waiting_for_folder_name = State()
    waiting_for_folder_rename = State()
    waiting_for_file = State()
    waiting_for_file_name = State()
    waiting_for_file_rename = State()
    waiting_for_search = State()

# --- КЛАВИАТУРЫ ---

def get_base_reply_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🗂 Мои папки"), KeyboardButton(text="➕ Создать папку"))
    builder.row(KeyboardButton(text="🔍 Поиск файла"), KeyboardButton(text="ℹ️ Помощь"))
    return builder.as_markup(resize_keyboard=True)

def get_main_kb(user_id):
    builder = InlineKeyboardBuilder()
    folders = db.get_folders(user_id)
    for folder in folders:
        builder.button(text=f"📁 {folder}", callback_data=f"folder:{folder}")
    if not folders:
        builder.button(text="У вас пока нет папок 💨", callback_data="none")
    builder.adjust(1)
    return builder.as_markup()

def get_folder_kb(folder_name):
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Добавить файл", callback_data=f"add_to:{folder_name}")
    builder.button(text="📂 Список файлов", callback_data=f"files_in:{folder_name}")
    builder.button(text="✏️ Редактировать", callback_data=f"rename_folder:{folder_name}")
    builder.button(text="🗑 Удалить", callback_data=f"delete_folder:{folder_name}")
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    builder.adjust(2)
    return builder.as_markup()

# --- ХЕНДЛЕРЫ ГЛАВНОГО МЕНЮ ---

@dp.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Добро пожаловать в CloudBox!\n\nВаше личное облако в Telegram. Используйте меню ниже для навигации.", 
                         reply_markup=get_base_reply_kb())
    await message.answer("🗂 Ваши папки:", reply_markup=get_main_kb(message.from_user.id))

@dp.message(F.text == "🗂 Мои папки")
async def show_folders_text(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🗂 Ваши актуальные папки:", reply_markup=get_main_kb(message.from_user.id))

@dp.message(F.text == "➕ Создать папку")
async def create_folder_text(message: Message, state: FSMContext):
    await message.answer("✍️ Введите название для новой папки:")
    await state.set_state(StorageStates.waiting_for_folder_name)

@dp.message(F.text == "🔍 Поиск файла")
async def search_file_text(message: Message, state: FSMContext):
    await message.answer("🔍 Введите название файла для поиска:")
    await state.set_state(StorageStates.waiting_for_search)

@dp.message(F.text == "ℹ️ Помощь")
async def help_text(message: Message):
    help_msg = (
        "📖 *Гайд по CloudBox:*\n\n"
        "📁 *Папки:* Создавайте папки для категорий (Скриншоты, Рефы, Музыка).\n"
        "📤 *Загрузка:* Заходите в папку -> Добавить файл -> Отправьте файл -> Дайте ему имя.\n"
        "👀 *Просмотр:* В списке файлов нажмите на файл, чтобы увидеть его содержимое.\n"
        "🔍 *Поиск:* Ищите файлы по имени сразу во всех папках.\n\n"
        "Все файлы хранятся в Telegram, а структура — в вашей облачной базе."
    )
    await message.answer(help_msg, parse_mode="Markdown")

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🗂 Ваши папки:", reply_markup=get_main_kb(callback.from_user.id))

# --- ЛОГИКА ПАПОК ---

@dp.message(StorageStates.waiting_for_folder_name)
async def process_create_folder(message: Message, state: FSMContext):
    name = message.text.strip()
    if db.create_folder(message.from_user.id, name):
        await message.answer(f"✅ Папка '{name}' создана!", reply_markup=get_main_kb(message.from_user.id))
        await state.clear()
    else:
        await message.answer("❌ Ошибка: такая папка уже существует.")

@dp.callback_query(F.data.startswith("folder:"))
async def open_folder(callback: CallbackQuery):
    folder_name = callback.data.split(":")[1]
    await callback.message.edit_text(f"📁 Папка: *{folder_name}*", parse_mode="Markdown", reply_markup=get_folder_kb(folder_name))

@dp.callback_query(F.data.startswith("rename_folder:"))
async def ask_rename_folder(callback: CallbackQuery, state: FSMContext):
    folder_name = callback.data.split(":")[1]
    await state.update_data(old_folder_name=folder_name)
    await callback.message.edit_text(f"✏️ Введите новое название для папки *{folder_name}*:", parse_mode="Markdown")
    await state.set_state(StorageStates.waiting_for_folder_rename)

@dp.message(StorageStates.waiting_for_folder_rename)
async def process_rename_folder(message: Message, state: FSMContext):
    data = await state.get_data()
    old_name = data['old_folder_name']
    new_name = message.text.strip()
    if db.rename_folder(message.from_user.id, old_name, new_name):
        await message.answer(f"✅ Папка '{old_name}' переименована в '{new_name}'!", reply_markup=get_main_kb(message.from_user.id))
        await state.clear()
    else:
        await message.answer("❌ Ошибка при переименовании.")

@dp.callback_query(F.data.startswith("delete_folder:"))
async def confirm_delete_folder(callback: CallbackQuery):
    folder_name = callback.data.split(":")[1]
    db.delete_folder(callback.from_user.id, folder_name)
    await callback.answer(f"🗑 Папка '{folder_name}' удалена")
    await callback.message.edit_text("🗂 Ваши папки:", reply_markup=get_main_kb(callback.from_user.id))

# --- ЛОГИКА ФАЙЛОВ ---

@dp.callback_query(F.data.startswith("add_to:"))
async def ask_file(callback: CallbackQuery, state: FSMContext):
    folder_name = callback.data.split(":")[1]
    await state.update_data(current_folder=folder_name)
    await callback.message.edit_text(f"🖇 *Папка: {folder_name}*\n\nОтправьте файл, который хотите сохранить.", parse_mode="Markdown")
    await state.set_state(StorageStates.waiting_for_file)

@dp.message(StorageStates.waiting_for_file)
async def handle_incoming_file(message: Message, state: FSMContext):
    file_id = None
    f_type = None
    if message.photo: file_id, f_type = message.photo[-1].file_id, "photo"
    elif message.video: file_id, f_type = message.video.file_id, "video"
    elif message.document: file_id, f_type = message.document.file_id, "document"
    elif message.audio: file_id, f_type = message.audio.file_id, "audio"
    
    if not file_id:
        await message.answer("❌ Этот формат не поддерживается.")
        return

    await state.update_data(last_file_id=file_id, last_file_type=f_type, last_caption=message.caption)
    await message.answer("✍️ Придумайте название для этого файла:")
    await state.set_state(StorageStates.waiting_for_file_name)

@dp.message(StorageStates.waiting_for_file_name)
async def save_file_name(message: Message, state: FSMContext):
    data = await state.get_data()
    folder_name = data.get('current_folder')
    if not folder_name:
        await message.answer("❌ Ошибка. Попробуйте еще раз.")
        await state.clear()
        return

    folder_id = db.get_folder_id(message.from_user.id, folder_name)
    name = message.text.strip()
    db.add_file(folder_id, data['last_file_id'], data['last_file_type'], name, data.get('last_caption'))
    
    await message.answer(f"✅ Файл '{name}' успешно сохранен!", reply_markup=get_base_reply_kb())
    await message.answer(f"📁 Папка: {folder_name}", reply_markup=get_folder_kb(folder_name))
    await state.clear()

@dp.callback_query(F.data.startswith("files_in:"))
async def list_files(callback: CallbackQuery):
    folder_name = callback.data.split(":")[1]
    folder_id = db.get_folder_id(callback.from_user.id, folder_name)
    files = db.get_files_in_folder(folder_id)
    
    if not files:
        await callback.answer("Пусто 💨", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for f_id, f_name, f_type in files:
        icon = "🖼" if f_type == "photo" else "🎥" if f_type == "video" else "📄"
        builder.button(text=f"{icon} {f_name}", callback_data=f"f_item:{f_id}")
    builder.button(text="🔙 Назад", callback_data=f"folder:{folder_name}")
    builder.adjust(1)
    
    await callback.message.edit_text(f"📦 Файлы в папке *{folder_name}*:", parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("f_item:"))
async def file_menu(callback: CallbackQuery):
    file_id_pk = int(callback.data.split(":")[1])
    details = db.get_file_details(file_id_pk)
    if not details: return

    f_id, f_type, name, caption = details
    builder = InlineKeyboardBuilder()
    builder.button(text="👁 Посмотреть", callback_data=f"f_view:{file_id_pk}")
    builder.button(text="✏️ Переименовать", callback_data=f"f_rename:{file_id_pk}")
    builder.button(text="🗑 Удалить", callback_data=f"f_del:{file_id_pk}")
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    builder.adjust(2)

    await callback.message.edit_text(f"📄 Файл: *{name}*\nТип: {f_type}", parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("f_view:"))
async def view_file_item(callback: CallbackQuery):
    file_id_pk = int(callback.data.split(":")[1])
    details = db.get_file_details(file_id_pk)
    if not details: return
    f_id, f_type, name, caption = details
    
    try:
        if f_type == "photo": await callback.message.answer_photo(f_id, caption=caption)
        elif f_type == "video": await callback.message.answer_video(f_id, caption=caption)
        elif f_type == "document": await callback.message.answer_document(f_id, caption=caption)
        elif f_type == "audio": await callback.message.answer_audio(f_id, caption=caption)
        await callback.answer()
    except Exception:
        await callback.answer("Ошибка при открытии файла", show_alert=True)

@dp.callback_query(F.data.startswith("f_rename:"))
async def ask_file_rename(callback: CallbackQuery, state: FSMContext):
    file_id_pk = int(callback.data.split(":")[1])
    await state.update_data(edit_file_id=file_id_pk)
    await callback.message.edit_text("✏️ Введите новое имя для этого файла:")
    await state.set_state(StorageStates.waiting_for_file_rename)

@dp.message(StorageStates.waiting_for_file_rename)
async def process_file_rename(message: Message, state: FSMContext):
    data = await state.get_data()
    db.rename_file(data['edit_file_id'], message.text.strip())
    await message.answer("✅ Имя файла успешно изменено!", reply_markup=get_base_reply_kb())
    await state.clear()

@dp.callback_query(F.data.startswith("f_del:"))
async def delete_file_item(callback: CallbackQuery):
    file_id_pk = int(callback.data.split(":")[1])
    db.delete_file(file_id_pk)
    await callback.answer("🗑 Файл удален")
    await callback.message.edit_text("🗂 Главное меню:", reply_markup=get_main_kb(callback.from_user.id))

@dp.message(StorageStates.waiting_for_search)
async def process_search(message: Message, state: FSMContext):
    results = db.search_files(message.from_user.id, message.text.strip())
    if not results:
        await message.answer("Ничего не найдено 🤷‍♂️", reply_markup=get_base_reply_kb())
    else:
        builder = InlineKeyboardBuilder()
        for f_id, f_name, folder_name in results:
            builder.button(text=f"📄 {f_name} (в {folder_name})", callback_data=f"f_item:{f_id}")
        builder.button(text="🔙 Назад", callback_data="back_to_main")
        builder.adjust(1)
        await message.answer(f"🔍 Найдено {len(results)} результатов:", reply_markup=builder.as_markup())
    await state.clear()

# --- LIFESPAN ---
async def keep_alive(url: str):
    if not url: return
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(600)
            try: await client.get(url)
            except: pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(dp.start_polling(bot))
    self_url = os.getenv("SELF_URL")
    asyncio.create_task(keep_alive(self_url))
    yield

app = FastAPI(lifespan=lifespan)
@app.get("/")
async def root(): return {"status": "ok"}

if os.path.exists("static"):
    app.mount("/site", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
