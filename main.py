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

# Инициализация БД (синхронно при старте - это ок)
db.init_db()

# --- СОСТОЯНИЯ (FSM) ---
class StorageStates(StatesGroup):
    waiting_for_folder_name = State()
    waiting_for_folder_rename = State()
    waiting_for_file = State()
    waiting_for_save_confirm = State()
    waiting_for_file_name = State()
    waiting_for_file_rename = State()
    waiting_for_search = State()

# --- КЛАВИАТУРЫ ---

def get_base_reply_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🗂 Мои папки"), KeyboardButton(text="➕ Создать папку"))
    builder.row(KeyboardButton(text="🔍 Поиск файла"), KeyboardButton(text="ℹ️ Помощь"))
    return builder.as_markup(resize_keyboard=True)

async def get_main_kb(user_id):
    builder = InlineKeyboardBuilder()
    # Выносим в поток, чтобы не блокировать event loop
    folders = await asyncio.to_thread(db.get_folders, user_id)
    for folder, count in folders:
        safe_name = escape(folder)
        builder.button(text=f"📁 {safe_name} ({count})", callback_data=f"folder:{folder}")
    if not folders:
        builder.button(text="У вас пока нет папок 💨", callback_data="none")
    builder.adjust(1)
    return builder.as_markup()

def get_folder_kb(folder_name):
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Добавить", callback_data=f"add_to:{folder_name}")
    builder.button(text="📂 Файлы", callback_data=f"files_in:{folder_name}")
    builder.button(text="✏️ Ред.", callback_data=f"rename_folder:{folder_name}")
    builder.button(text="🗑 Удалить", callback_data=f"delete_folder:{folder_name}")
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    builder.adjust(2)
    return builder.as_markup()

# --- ХЕНДЛЕРЫ ---

@dp.callback_query(F.data == "none")
async def none_callback(callback: CallbackQuery):
    await callback.answer("Создайте папку с помощью кнопки снизу 👇")

@dp.callback_query(F.data.startswith("delete_folder:"))
async def delete_folder_cmd(callback: CallbackQuery):
    folder_name = callback.data.split(":")[1]
    await asyncio.to_thread(db.delete_folder, callback.from_user.id, folder_name)
    await callback.answer(f"🗑 Папка удалена")
    await callback.message.edit_text("🗂 Ваши папки:", reply_markup=await get_main_kb(callback.from_user.id))

@dp.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Добро пожаловать в CloudBox!\n\nВаше личное облако. Поддерживает текст, фото, видео, кружочки и файлы.", 
                         reply_markup=get_base_reply_kb())
    await message.answer("🗂 Ваши папки:", reply_markup=await get_main_kb(message.from_user.id))

@dp.message(F.text == "🗂 Мои папки")
async def show_folders_text(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🗂 Ваши папки:", reply_markup=await get_main_kb(message.from_user.id))

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
        "<b>📖 Возможности CloudBox:</b>\n\n"
        "✅ <b>Любые файлы:</b> Текст, фото, видео, голосовые, кружочки, музыка.\n"
        "📁 <b>Организация:</b> Создавайте папки и переименовывайте их.\n"
        "🛡 <b>Безопасность:</b> Ваши данные хранятся в облаке Neon.\n"
        "🔍 <b>Поиск:</b> Находите файлы по имени во всех папках.\n\n"
        "Чтобы сохранить что-то, зайдите в папку и нажмите кнопку 'Добавить файл'."
    )
    await message.answer(help_msg, parse_mode="HTML")

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🗂 Ваши папки:", reply_markup=await get_main_kb(callback.from_user.id))

# --- ЛОГИКА ПАПОК ---

@dp.message(StorageStates.waiting_for_folder_name)
async def process_create_folder(message: Message, state: FSMContext):
    name = message.text.strip()
    success = await asyncio.to_thread(db.create_folder, message.from_user.id, name)
    if success:
        await message.answer(f"✅ Папка '<b>{escape(name)}</b>' создана!", 
                             parse_mode="HTML",
                             reply_markup=await get_main_kb(message.from_user.id))
        await state.clear()
    else:
        await message.answer("❌ Ошибка: такая папка уже существует.")

@dp.callback_query(F.data.startswith("folder:"))
async def open_folder(callback: CallbackQuery):
    folder_name = callback.data.split(":")[1]
    await callback.message.edit_text(f"📁 Папка: <b>{escape(folder_name)}</b>", 
                                    parse_mode="HTML", 
                                    reply_markup=get_folder_kb(folder_name))

# --- ЛОГИКА ФАЙЛОВ ---

@dp.callback_query(F.data.startswith("add_to:"))
async def ask_file(callback: CallbackQuery, state: FSMContext):
    folder_name = callback.data.split(":")[1]
    await state.update_data(current_folder=folder_name)
    await callback.message.edit_text(f"🖇 <b>Папка: {escape(folder_name)}</b>\n\nОтправьте мне что угодно: текст, фото, видео, голосовое или кружочек.", parse_mode="HTML")
    await state.set_state(StorageStates.waiting_for_file)

@dp.message(StorageStates.waiting_for_file)
async def handle_incoming_file(message: Message, state: FSMContext):
    file_id, f_type = None, None
    
    if message.photo: file_id, f_type = message.photo[-1].file_id, "photo"
    elif message.video: file_id, f_type = message.video.file_id, "video"
    elif message.document: file_id, f_type = message.document.file_id, "document"
    elif message.audio: file_id, f_type = message.audio.file_id, "audio"
    elif message.voice: file_id, f_type = message.voice.file_id, "voice"
    elif message.video_note: file_id, f_type = message.video_note.file_id, "video_note"
    elif message.animation: file_id, f_type = message.animation.file_id, "animation"
    elif message.sticker: file_id, f_type = message.sticker.file_id, "sticker"
    elif message.text: file_id, f_type = message.text, "text"
    
    if not file_id:
        await message.answer("❌ Я не смог распознать этот объект.")
        return

    data = await state.get_data()
    folder_name = data['current_folder']
    await state.update_data(last_file_id=file_id, last_file_type=f_type, last_caption=message.caption)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, сохранить", callback_data="confirm_save")
    builder.button(text="❌ Отмена", callback_data="back_to_main")
    
    await message.answer(f"📦 Объект получен. Сохранить его в папку <b>{escape(folder_name)}</b>?", 
                         parse_mode="HTML", reply_markup=builder.as_markup())
    await state.set_state(StorageStates.waiting_for_save_confirm)

@dp.callback_query(F.data == "confirm_save")
async def ask_file_name_final(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("✍️ Придумайте название для сохранения:")
    await state.set_state(StorageStates.waiting_for_file_name)

@dp.message(StorageStates.waiting_for_file_name)
async def save_file_final(message: Message, state: FSMContext):
    data = await state.get_data()
    folder_name = data.get('current_folder')
    
    folder_id = await asyncio.to_thread(db.get_folder_id, message.from_user.id, folder_name)
    name = message.text.strip()
    
    await asyncio.to_thread(db.add_file, folder_id, data['last_file_id'], data['last_file_type'], name, data.get('last_caption'))
    
    await message.answer(f"✅ Сохранено под именем '<b>{escape(name)}</b>'!", 
                         parse_mode="HTML", reply_markup=get_base_reply_kb())
    await message.answer(f"📁 Папка: {escape(folder_name)}", 
                         parse_mode="HTML", reply_markup=get_folder_kb(folder_name))
    await state.clear()

@dp.callback_query(F.data.startswith("files_in:"))
async def list_files(callback: CallbackQuery):
    folder_name = callback.data.split(":")[1]
    folder_id = await asyncio.to_thread(db.get_folder_id, callback.from_user.id, folder_name)
    files = await asyncio.to_thread(db.get_files_in_folder, folder_id)
    
    builder = InlineKeyboardBuilder()
    safe_folder = escape(folder_name)
    
    if not files:
        builder.button(text="➕ Добавить файл", callback_data=f"add_to:{folder_name}")
        builder.button(text="🔙 Назад", callback_data=f"folder:{folder_name}")
        builder.adjust(1)
        await callback.message.edit_text(f"📁 Папка <b>{safe_folder}</b> пока пуста 💨", 
                                        parse_mode="HTML", reply_markup=builder.as_markup())
        await callback.answer()
        return

    for f_id, f_name, f_type in files:
        icon = "🖼" if f_type == "photo" else "🎥" if f_type == "video" else "📄"
        if f_type == "text": icon = "✍️"
        elif f_type == "voice": icon = "🎤"
        elif f_type == "video_note": icon = "🔘"
        builder.button(text=f"{icon} {escape(f_name)}", callback_data=f"f_item:{f_id}")
    builder.button(text="🔙 Назад", callback_data=f"folder:{folder_name}")
    builder.adjust(1)
    
    await callback.message.edit_text(f"📦 Файлы в папке <b>{safe_folder}</b>:", 
                                    parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("f_item:"))
async def file_menu(callback: CallbackQuery):
    file_id_pk = int(callback.data.split(":")[1])
    details = await asyncio.to_thread(db.get_file_details, file_id_pk)
    if not details: 
        await callback.answer("Файл не найден ❌")
        return
    f_id, f_type, name, caption, folder_name = details
    
    builder = InlineKeyboardBuilder()
    builder.button(text="👁 Посмотреть", callback_data=f"f_view:{file_id_pk}")
    builder.button(text="✏️ Переименовать", callback_data=f"f_rename:{file_id_pk}")
    builder.button(text="🗑 Удалить", callback_data=f"f_del:{file_id_pk}")
    builder.button(text=f"🔙 В папку {escape(folder_name)}", callback_data=f"files_in:{folder_name}")
    builder.button(text="🏠 Главная", callback_data="back_to_main")
    builder.adjust(2, 2, 1)

    await callback.message.edit_text(f"📄 Файл: <b>{escape(name)}</b>\n📁 Папка: <b>{escape(folder_name)}</b>\nТип: {f_type}", 
                                    parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("f_view:"))
async def view_file_item(callback: CallbackQuery):
    file_id_pk = int(callback.data.split(":")[1])
    details = await asyncio.to_thread(db.get_file_details, file_id_pk)
    if not details: 
        await callback.answer("Файл не найден ❌", show_alert=True)
        return
    f_id, f_type, name, caption, folder_name = details
    
    try:
        await callback.answer("Открываю...")
        if f_type == "photo": await callback.message.answer_photo(f_id, caption=caption)
        elif f_type == "video": await callback.message.answer_video(f_id, caption=caption)
        elif f_type == "document": await callback.message.answer_document(f_id, caption=caption)
        elif f_type == "audio": await callback.message.answer_audio(f_id, caption=caption)
        elif f_type == "voice": await callback.message.answer_voice(f_id, caption=caption)
        elif f_type == "video_note": await callback.message.answer_video_note(f_id)
        elif f_type == "animation": await callback.message.answer_animation(f_id, caption=caption)
        elif f_type == "sticker": await callback.message.answer_sticker(f_id)
        elif f_type == "text": await callback.message.answer(f"✍️ <b>Сохраненный текст:</b>\n\n{escape(f_id)}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error sending file: {e}")
        await callback.answer("Ошибка при открытии", show_alert=True)

@dp.callback_query(F.data.startswith("f_rename:"))
async def ask_file_rename(callback: CallbackQuery, state: FSMContext):
    file_id_pk = int(callback.data.split(":")[1])
    await state.update_data(edit_file_id=file_id_pk)
    await callback.message.edit_text("✏️ Введите новое имя:")
    await state.set_state(StorageStates.waiting_for_file_rename)

@dp.message(StorageStates.waiting_for_file_rename)
async def process_file_rename(message: Message, state: FSMContext):
    data = await state.get_data()
    await asyncio.to_thread(db.rename_file, data['edit_file_id'], message.text.strip())
    await message.answer("✅ Имя файла изменено!", reply_markup=get_base_reply_kb())
    await state.clear()

@dp.callback_query(F.data.startswith("f_del:"))
async def delete_file_item(callback: CallbackQuery):
    file_id_pk = int(callback.data.split(":")[1])
    details = await asyncio.to_thread(db.get_file_details, file_id_pk)
    await asyncio.to_thread(db.delete_file, file_id_pk)
    await callback.answer("🗑 Удалено")
    if details:
        folder_name = details[4]
        await callback.message.edit_text(f"🗑 Файл удален из папки <b>{escape(folder_name)}</b>", 
                                        parse_mode="HTML", reply_markup=get_folder_kb(folder_name))
    else:
        await callback.message.edit_text("🗂 Ваши папки:", reply_markup=await get_main_kb(callback.from_user.id))

@dp.message(StorageStates.waiting_for_search)
async def process_search(message: Message, state: FSMContext):
    results = await asyncio.to_thread(db.search_files, message.from_user.id, message.text.strip())
    if not results:
        await message.answer("Ничего не найдено 🤷‍♂️", reply_markup=get_base_reply_kb())
    else:
        builder = InlineKeyboardBuilder()
        for f_pk, f_name, folder_name, f_type in results:
            icon = "🖼" if f_type == "photo" else "🎥" if f_type == "video" else "📄"
            if f_type == "text": icon = "✍️"
            elif f_type == "voice": icon = "🎤"
            elif f_type == "video_note": icon = "🔘"
            builder.button(text=f"{icon} {f_name} (📁 {folder_name})", callback_data=f"f_item:{f_pk}")
        builder.button(text="🔙 Назад", callback_data="back_to_main")
        builder.adjust(1)
        await message.answer(f"🔍 Найдено <b>{len(results)}</b> результатов:", 
                             parse_mode="HTML", reply_markup=builder.as_markup())
    await state.clear()

@dp.callback_query(F.data.startswith("rename_folder:"))
async def ask_rename_folder(callback: CallbackQuery, state: FSMContext):
    folder_name = callback.data.split(":")[1]
    await state.update_data(old_folder_name=folder_name)
    await callback.message.edit_text(f"✏️ Введите новое название для папки <b>{escape(folder_name)}</b>:", parse_mode="HTML")
    await state.set_state(StorageStates.waiting_for_folder_rename)

@dp.message(StorageStates.waiting_for_folder_rename)
async def process_rename_folder(message: Message, state: FSMContext):
    data = await state.get_data()
    old_name = data['old_folder_name']
    new_name = message.text.strip()
    success = await asyncio.to_thread(db.rename_folder, message.from_user.id, old_name, new_name)
    if success:
        await message.answer(f"✅ Папка переименована в '<b>{escape(new_name)}</b>'!", 
                             parse_mode="HTML", reply_markup=await get_main_kb(message.from_user.id))
        await state.clear()
    else:
        await message.answer("❌ Ошибка при переименовании.")

@dp.error()
async def error_handler(event: types.ErrorEvent):
    logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: {event.exception}", exc_info=True)
    try:
        if event.update.callback_query:
            await event.update.callback_query.answer("⚠️ Ошибка базы данных или соединения", show_alert=True)
        elif event.update.message:
            await event.update.message.answer("⚠️ Произошла ошибка. Попробуйте позже.")
    except: pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(dp.start_polling(bot))
    self_url = os.getenv("SELF_URL")
    if self_url:
        async def keep_alive():
            async with httpx.AsyncClient() as client:
                while True:
                    await asyncio.sleep(600)
                    try: await client.get(self_url)
                    except: pass
        asyncio.create_task(keep_alive())
    yield

app = FastAPI(lifespan=lifespan)
@app.get("/")
async def root(): return {"status": "ok"}
if os.path.exists("static"):
    app.mount("/site", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
