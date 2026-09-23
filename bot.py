import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiosqlite

# Настройки конфигурации
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

# Путь к БД. Для Railway рекомендуется примонтировать Volume (например, в /data)
# Если Volume не используется, БД обнулится при следующем деплое
DB_PATH = os.getenv("DB_PATH", "bot.db")

router = Router()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)')
        await db.execute('CREATE TABLE IF NOT EXISTS movies (code TEXT PRIMARY KEY, title TEXT, photo_id TEXT)')
        await db.execute('CREATE TABLE IF NOT EXISTS channels (channel_id TEXT PRIMARY KEY, url TEXT)')
        await db.commit()

# --- Хелперы для подписки ---
async def check_subscription(bot: Bot, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT channel_id FROM channels') as cursor:
            channels = await cursor.fetchall()
    
    if not channels:
        return True  # Нет каналов для проверки
        
    for (channel_id,) in channels:
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception:
            # Если бот не админ в канале, пропускаем проверку, чтобы не блокировать юзеров
            pass
    return True

async def get_sub_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT url FROM channels') as cursor:
            channels = await cursor.fetchall()
            
    for i, (url,) in enumerate(channels, start=1):
        builder.button(text=f"📢 Kanal {i}", url=url)
        
    builder.button(text="✅ A'zolikni tekshirish", callback_data="check_sub")
    builder.adjust(1)
    return builder.as_markup()

# --- Пользовательская часть (на узбекском) ---
@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    # Регистрируем пользователя для статистики
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (message.from_user.id,))
        await db.commit()
        
        async with db.execute('SELECT COUNT(*) FROM movies') as cursor:
            movies_count = (await cursor.fetchone())[0]

    # Если база фильмов пуста
    if movies_count == 0:
        await message.answer("Assalomu aleykum, hozircha reklama beruvchilar yoq, agarda sizda kanal yoki botni reklama qilish kerak bolsa, admin @lixuauto")
        return

    # Проверка подписки
    is_subbed = await check_subscription(bot, message.from_user.id)
    if not is_subbed:
        kb = await get_sub_keyboard()
        await message.answer("Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:", reply_markup=kb)
        return
        
    await message.answer("Assalomu aleykum! Kinoni izlash uchun 3 xonali kodni yuboring (masalan: 123):")

@router.callback_query(F.data == "check_sub")
async def check_sub_callback(call: CallbackQuery, bot: Bot):
    is_subbed = await check_subscription(bot, call.from_user.id)
    if is_subbed:
        await call.message.delete()
        await call.message.answer("Rahmat! Endi kino kodini yuborishingiz mumkin.")
    else:
        await call.answer("Siz barcha kanallarga obuna bo'lmagansiz!", show_alert=True)

@router.message(F.text & ~F.text.startswith('/'))
async def handle_movie_request(message: Message, bot: Bot):
    text = message.text.strip()
    
    if not text.isdigit() or len(text) != 3:
        return await message.answer("Iltimos, 3 xonali kod yuboring (masalan, 123).")

    is_subbed = await check_subscription(bot, message.from_user.id)
    if not is_subbed:
        kb = await get_sub_keyboard()
        return await message.answer("Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:", reply_markup=kb)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT title, photo_id FROM movies WHERE code = ?', (text,)) as cursor:
            movie = await cursor.fetchone()
            
    if movie:
        title, photo_id = movie
        await message.answer_photo(photo=photo_id, caption=f"🎬 <b>{title}</b>", parse_mode="HTML")
    else:
        await message.answer("Kechirasiz, bunday kod bilan film topilmadi.")


# --- Админская часть (на русском) ---
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

@router.message(Command("addcode"))
async def cmd_addcode(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id): return
    
    if not message.photo:
        return await message.reply("Ошибка: Прикрепите фото к сообщению с командой!")
        
    args = command.args
    if not args:
        return await message.reply("Использование: /addcode [3 цифры] [Название]")
        
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("Укажите код и название. Пример: /addcode 123 Матрица")
        
    code, title = parts
    if not code.isdigit() or len(code) != 3:
        return await message.reply("Код должен состоять ровно из 3 цифр!")
        
    photo_id = message.photo[-1].file_id
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR REPLACE INTO movies (code, title, photo_id) VALUES (?, ?, ?)', (code, title, photo_id))
        await db.commit()
        
    await message.reply(f"✅ Фильм '{title}' успешно добавлен под кодом {code}.")

@router.message(Command("delcode"))
async def cmd_delcode(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id): return
    
    args = command.args
    if not args or not args.isdigit() or len(args) != 3:
        return await message.reply("Использование: /delcode [3 цифры]")
        
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('DELETE FROM movies WHERE code = ?', (args,))
        await db.commit()
        if cursor.rowcount > 0:
            await message.reply(f"✅ Код {args} удален.")
        else:
            await message.reply(f"❌ Код {args} не найден.")

@router.message(Command("addch"))
async def cmd_addch(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id): return
    
    args = command.args
    if not args:
        return await message.reply("Использование: /addch [ID канала] [URL]\nПример: /addch -100123456789 https://t.me/yourchannel")
        
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("Нужно указать и ID и ссылку!")
        
    channel_id, url = parts
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR REPLACE INTO channels (channel_id, url) VALUES (?, ?)', (channel_id, url))
        await db.commit()
        
    await message.reply("✅ Канал добавлен для обязательной подписки. Бот должен быть администратором в этом канале!")

@router.message(Command("delch"))
async def cmd_delch(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id): return
    
    args = command.args
    if not args:
        return await message.reply("Использование: /delch [ID канала]")
        
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('DELETE FROM channels WHERE channel_id = ?', (args,))
        await db.commit()
        if cursor.rowcount > 0:
            await message.reply(f"✅ Канал {args} удален.")
        else:
            await message.reply("❌ Канал не найден.")

@router.message(Command("ch"))
async def cmd_ch(message: Message):
    if not is_admin(message.from_user.id): return
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT channel_id, url FROM channels') as cursor:
            channels = await cursor.fetchall()
            
    if not channels:
        return await message.reply("Список обязательных каналов пуст.")
        
    text = "📢 <b>Обязательные каналы:</b>\n\n"
    for ch_id, url in channels:
        text += f"ID: <code>{ch_id}</code> | <a href='{url}'>Ссылка</a>\n"
        
    await message.reply(text, parse_mode="HTML", disable_web_page_preview=True)

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id): return
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM users') as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM movies') as cursor:
            movies_count = (await cursor.fetchone())[0]
            
    await message.reply(f"📊 <b>Статистика бота:</b>\n\nПользователей: {users_count}\nДобавлено фильмов: {movies_count}", parse_mode="HTML")

async def main():
    logging.basicConfig(level=logging.INFO)
    
    if not TOKEN:
        logging.error("Не указан BOT_TOKEN!")
        return

    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    
    await init_db()
    logging.info("Бот запущен...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
