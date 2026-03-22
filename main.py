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
from aiogram.types import CallbackQuery, Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.utils.html import escape
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

# Инициализация БД
db.init_db()

# --- СОСТОЯНИЯ (FSM) ---
class StorageStates(StatesGroup):
    main_menu = State()
    inside_folder = State()
    file_view = State()
    waiting_for_folder_name = State()
    waiting_for_folder_rename = State()
    waiting_for_file = State()
    waiting_for_file_name = State()
    waiting_for_file_rename = State()
    waiting_for_search = State()

# --- КЛАВИАТУРЫ ---

async def get_main_reply_kb(user_id):
    builder = ReplyKeyboardBuilder()
    folders = await asyncio.to_thread(db.get_folders, user_id)
    
    # Добавляем папки
    for folder, count in folders:
        builder.add(KeyboardButton(text=f"📁 {folder}"))
    
    builder.adjust(2)
    # Системные кнопки
    builder.row(KeyboardButton(text="➕ Создать папку"))
    builder.row(KeyboardButton(text="🔍 Поиск"), KeyboardButton(text="ℹ️ Помощь"))
    
    return builder.as_markup(resize_keyboard=True)

async def get_folder_reply_kb(user_id, folder_name):
    builder = ReplyKeyboardBuilder()
    folder_id = await asyncio.to_thread(db.get_folder_id, user_id, folder_name)
    files = await asyncio.to_thread(db.get_files_in_folder, folder_id)
    
    for _, f_name, f_type in files:
        icon = "📄"
        if f_type == "photo": icon = "🖼"
        elif f_type == "video": icon = "🎥"
        elif f_type == "text": icon = "✍️"
        elif f_type == "voice": icon = "🎤"
        elif f_type == "video_note": icon = "🔘"
        builder.add(KeyboardButton(text=f"{icon} {f_name}"))
    
    builder.adjust(2)
    builder.row(KeyboardButton(text="📤 Добавить файл"))
    builder.row(KeyboardButton(text="⚙️ Настройки папки"), KeyboardButton(text="🔙 Назад"))
    
    return builder.as_markup(resize_keyboard=True)

def get_file_action_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="👁 Посмотреть"), KeyboardButton(text="✏️ Переименовать"))
    builder.row(KeyboardButton(text="🗑 Удалить"), KeyboardButton(text="🔙 Назад к списку"))
    return builder.as_markup(resize_keyboard=True)

def get_folder_settings_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="✏️ Переименовать папку"), KeyboardButton(text="🗑 Удалить папку"))
    builder.row(KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

# --- ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Добро пожаловать в CloudBox!\n\nВаше личное структурированное облако. Все папки и файлы теперь доступны прямо на клавиатуре.", 
                         reply_markup=await get_main_reply_kb(message.from_user.id))
    await state.set_state(StorageStates.main_menu)

# --- НАВИГАЦИЯ ---

@dp.message(F.text == "🔙 Назад", StorageStates.inside_folder)
@dp.message(F.text == "🔙 Назад", StorageStates.waiting_for_folder_name)
async def back_to_main(message: Message, state: FSMContext):
    await state.set_state(StorageStates.main_menu)
    await message.answer("🗂 Главное меню:", reply_markup=await get_main_reply_kb(message.from_user.id))

@dp.message(F.text == "🔙 Назад к списку", StorageStates.file_view)
async def back_to_folder(message: Message, state: FSMContext):
    data = await state.get_data()
    folder_name = data.get("current_folder")
    await state.set_state(StorageStates.inside_folder)
    await message.answer(f"📁 Папка: <b>{escape(folder_name)}</b>", 
                         parse_mode="HTML", 
                         reply_markup=await get_folder_reply_kb(message.from_user.id, folder_name))

# --- ЛОГИКА ПАПОК ---

@dp.message(F.text == "➕ Создать папку")
async def create_folder_init(message: Message, state: FSMContext):
    await message.answer("✍️ Введите название для новой папки:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True))
    await state.set_state(StorageStates.waiting_for_folder_name)

@dp.message(StorageStates.waiting_for_folder_name)
async def process_create_folder(message: Message, state: FSMContext):
    if message.text == "🔙 Назад": return await back_to_main(message, state)
    name = message.text.strip()
    success = await asyncio.to_thread(db.create_folder, message.from_user.id, name)
    if success:
        await message.answer(f"✅ Папка '<b>{escape(name)}</b>' создана!", 
                             parse_mode="HTML",
                             reply_markup=await get_main_reply_kb(message.from_user.id))
        await state.set_state(StorageStates.main_menu)
    else:
        await message.answer("❌ Ошибка: такая папка уже существует.")

@dp.message(F.text.startswith("📁 "), StorageStates.main_menu)
async def open_folder(message: Message, state: FSMContext):
    folder_name = message.text[2:].strip()
    await state.update_data(current_folder=folder_name)
    await state.set_state(StorageStates.inside_folder)
    await message.answer(f"📁 Папка: <b>{escape(folder_name)}</b>", 
                        parse_mode="HTML", 
                        reply_markup=await get_folder_reply_kb(message.from_user.id, folder_name))

@dp.message(F.text == "⚙️ Настройки папки", StorageStates.inside_folder)
async def folder_settings(message: Message, state: FSMContext):
    data = await state.get_data()
    folder_name = data.get("current_folder")
    await message.answer(f"⚙️ Настройки папки <b>{escape(folder_name)}</b>:", 
                         parse_mode="HTML", 
                         reply_markup=get_folder_settings_kb())

@dp.message(F.text == "🗑 Удалить папку", StorageStates.inside_folder)
async def delete_folder_confirm(message: Message, state: FSMContext):
    data = await state.get_data()
    folder_name = data.get("current_folder")
    await asyncio.to_thread(db.delete_folder, message.from_user.id, folder_name)
    await message.answer(f"🗑 Папка '{escape(folder_name)}' удалена.", reply_markup=await get_main_reply_kb(message.from_user.id))
    await state.set_state(StorageStates.main_menu)

@dp.message(F.text == "✏️ Переименовать папку", StorageStates.inside_folder)
async def rename_folder_init(message: Message, state: FSMContext):
    await message.answer("✍️ Введите новое название для папки:")
    await state.set_state(StorageStates.waiting_for_folder_rename)

@dp.message(StorageStates.waiting_for_folder_rename)
async def process_rename_folder(message: Message, state: FSMContext):
    data = await state.get_data()
    old_name = data.get("current_folder")
    new_name = message.text.strip()
    success = await asyncio.to_thread(db.rename_folder, message.from_user.id, old_name, new_name)
    if success:
        await state.update_data(current_folder=new_name)
        await message.answer(f"✅ Папка переименована в '<b>{escape(new_name)}</b>'!", 
                             parse_mode="HTML", reply_markup=await get_folder_reply_kb(message.from_user.id, new_name))
        await state.set_state(StorageStates.inside_folder)
    else:
        await message.answer("❌ Ошибка при переименовании.")

# --- ЛОГИКА ФАЙЛОВ ---

@dp.message(F.text == "📤 Добавить файл", StorageStates.inside_folder)
async def ask_file(message: Message, state: FSMContext):
    data = await state.get_data()
    folder_name = data.get("current_folder")
    await message.answer(f"🖇 <b>Папка: {escape(folder_name)}</b>\n\nОтправьте файл, фото, видео или текст:", 
                         parse_mode="HTML", 
                         reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True))
    await state.set_state(StorageStates.waiting_for_file)

@dp.message(StorageStates.waiting_for_file)
async def handle_incoming_file(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        data = await state.get_data()
        folder_name = data.get("current_folder")
        await state.set_state(StorageStates.inside_folder)
        return await message.answer(f"📁 Папка: {escape(folder_name)}", reply_markup=await get_folder_reply_kb(message.from_user.id, folder_name))

    file_id, f_type = None, None
    if message.photo: file_id, f_type = message.photo[-1].file_id, "photo"
    elif message.video: file_id, f_type = message.video.file_id, "video"
    elif message.document: file_id, f_type = message.document.file_id, "document"
    elif message.audio: file_id, f_type = message.audio.file_id, "audio"
    elif message.voice: file_id, f_type = message.voice.file_id, "voice"
    elif message.video_note: file_id, f_type = message.video_note.file_id, "video_note"
    elif message.animation: file_id, f_type = message.animation.file_id, "animation"
    elif message.text: file_id, f_type = message.text, "text"
    
    if not file_id:
        await message.answer("❌ Не удалось распознать файл.")
        return

    await state.update_data(last_file_id=file_id, last_file_type=f_type, last_caption=message.caption)
    await message.answer("✍️ Придумайте название для файла:")
    await state.set_state(StorageStates.waiting_for_file_name)

@dp.message(StorageStates.waiting_for_file_name)
async def save_file_final(message: Message, state: FSMContext):
    data = await state.get_data()
    folder_name = data.get('current_folder')
    folder_id = await asyncio.to_thread(db.get_folder_id, message.from_user.id, folder_name)
    name = message.text.strip()
    
    await asyncio.to_thread(db.add_file, folder_id, data['last_file_id'], data['last_file_type'], name, data.get('last_caption'))
    
    await message.answer(f"✅ Сохранено под именем '<b>{escape(name)}</b>'!", 
                         parse_mode="HTML")
    await message.answer(f"📁 Папка: {escape(folder_name)}", 
                         parse_mode="HTML", reply_markup=await get_folder_reply_kb(message.from_user.id, folder_name))
    await state.set_state(StorageStates.inside_folder)

@dp.message(F.text.regexp(r"^(📄|🖼|🎥|✍️|🎤|🔘) "), StorageStates.inside_folder)
async def open_file_menu(message: Message, state: FSMContext):
    f_name = message.text[2:].strip()
    data = await state.get_data()
    folder_name = data.get("current_folder")
    folder_id = await asyncio.to_thread(db.get_folder_id, message.from_user.id, folder_name)
    
    # Находим файл по имени в текущей папке
    files = await asyncio.to_thread(db.get_files_in_folder, folder_id)
    file_id_pk = next((f[0] for f in files if f[1] == f_name), None)
    
    if file_id_pk:
        await state.update_data(current_file_id=file_id_pk, current_file_name=f_name)
        await state.set_state(StorageStates.file_view)
        await message.answer(f"📄 Файл: <b>{escape(f_name)}</b>", 
                             parse_mode="HTML", 
                             reply_markup=get_file_action_kb())
    else:
        await message.answer("❌ Файл не найден.")

@dp.message(F.text == "👁 Посмотреть", StorageStates.file_view)
async def view_file(message: Message, state: FSMContext):
    data = await state.get_data()
    file_id_pk = data.get("current_file_id")
    details = await asyncio.to_thread(db.get_file_details, file_id_pk)
    if not details: return
    
    f_id, f_type, name, caption, _ = details
    try:
        if f_type == "photo": await message.answer_photo(f_id, caption=caption)
        elif f_type == "video": await message.answer_video(f_id, caption=caption)
        elif f_type == "document": await message.answer_document(f_id, caption=caption)
        elif f_type == "audio": await message.answer_audio(f_id, caption=caption)
        elif f_type == "voice": await message.answer_voice(f_id, caption=caption)
        elif f_type == "video_note": await message.answer_video_note(f_id)
        elif f_type == "text": await message.answer(f"✍️ <b>Текст:</b>\n\n{escape(f_id)}", parse_mode="HTML")
    except Exception as e:
        await message.answer("❌ Ошибка при отправке файла.")

@dp.message(F.text == "🗑 Удалить", StorageStates.file_view)
async def delete_file(message: Message, state: FSMContext):
    data = await state.get_data()
    file_id_pk = data.get("current_file_id")
    folder_name = data.get("current_folder")
    await asyncio.to_thread(db.delete_file, file_id_pk)
    await message.answer("🗑 Файл удален.")
    await state.set_state(StorageStates.inside_folder)
    await message.answer(f"📁 Папка: {escape(folder_name)}", reply_markup=await get_folder_reply_kb(message.from_user.id, folder_name))

@dp.message(F.text == "✏️ Переименовать", StorageStates.file_view)
async def rename_file_init(message: Message, state: FSMContext):
    await message.answer("✍️ Введите новое имя для файла:")
    await state.set_state(StorageStates.waiting_for_file_rename)

@dp.message(StorageStates.waiting_for_file_rename)
async def process_file_rename(message: Message, state: FSMContext):
    data = await state.get_data()
    file_id_pk = data.get("current_file_id")
    new_name = message.text.strip()
    await asyncio.to_thread(db.rename_file, file_id_pk, new_name)
    await state.update_data(current_file_name=new_name)
    await message.answer(f"✅ Файл переименован в <b>{escape(new_name)}</b>", parse_mode="HTML", reply_markup=get_file_action_kb())
    await state.set_state(StorageStates.file_view)

# --- ПРОЧЕЕ ---

@dp.message(F.text == "🔍 Поиск")
async def search_init(message: Message, state: FSMContext):
    await message.answer("🔍 Введите название файла для поиска:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True))
    await state.set_state(StorageStates.waiting_for_search)

@dp.message(StorageStates.waiting_for_search)
async def process_search(message: Message, state: FSMContext):
    if message.text == "🔙 Назад": return await back_to_main(message, state)
    results = await asyncio.to_thread(db.search_files, message.from_user.id, message.text.strip())
    if not results:
        await message.answer("Ничего не найдено 🤷‍♂️")
    else:
        text = "🔍 Результаты поиска:\n\n"
        for _, f_name, folder_name, f_type in results:
            text += f"• {f_name} (📁 {folder_name})\n"
        await message.answer(text, reply_markup=await get_main_reply_kb(message.from_user.id))
    await state.set_state(StorageStates.main_menu)

@dp.message(F.text == "ℹ️ Помощь")
async def help_cmd(message: Message):
    await message.answer("Это ваше структурированное облако.\n\n1. Создавайте папки.\n2. Заходите в них и добавляйте файлы.\n3. Все файлы всегда под рукой на клавиатуре!")

# --- FASTAPI ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(dp.start_polling(bot))
    yield

app = FastAPI(lifespan=lifespan)
@app.get("/")
async def root(): return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
