import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncpg

# Конфигурация из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DATABASE_URL = os.getenv("DATABASE_URL")

# Предотвращение ошибок со старым форматом ссылки PostgreSQL
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

router = Router()

async def init_db(pool: asyncpg.Pool):
    """Инициализация таблиц в базе данных PostgreSQL."""
    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS movies (
                code TEXT PRIMARY KEY,
                title TEXT,
                photo_id TEXT
            );
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                url TEXT
            );
        ''')

# --- Хелперы для проверки подписки ---
async def check_subscription(bot: Bot, db_pool: asyncpg.Pool, user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        channels = await conn.fetch('SELECT channel_id FROM channels')
    
    if not channels:
        return True
        
    for record in channels:
        channel_id = record['channel_id']
        try:
            chat_id = int(channel_id) if channel_id.lstrip('-').isdigit() else channel_id
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception:
            # Если бот не является админом в канале или канал недоступен
            pass
    return True

async def get_sub_keyboard(db_pool: asyncpg.Pool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with db_pool.acquire() as conn:
        channels = await conn.fetch('SELECT url FROM channels')
        
    for i, record in enumerate(channels, start=1):
        builder.button(text=f"📢 Kanal {i}", url=record['url'])
        
    builder.button(text="✅ A'zolikni tekshirish", callback_data="check_sub")
    builder.adjust(1)
    return builder.as_markup()

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


# --- Пользовательская часть (на узбекском языке) ---
@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, db_pool: asyncpg.Pool):
    # Фиксация пользователя в статистике
    async with db_pool.acquire() as conn:
        await conn.execute('INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING', message.from_user.id)
        movies_count = await conn.fetchval('SELECT COUNT(*) FROM movies')

    # Если рекламные коды/фильмы ещё не добавлены
    if movies_count == 0:
        await message.answer("Assalomu aleykum, hozircha reklama beruvchilar yoq, agarda sizda kanal yoki botni reklama qilish kerak bolsa, admin @lixuauto")
        return

    # Проверка обязательной подписки
    is_subbed = await check_subscription(bot, db_pool, message.from_user.id)
    if not is_subbed:
        kb = await get_sub_keyboard(db_pool)
        await message.answer("Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:", reply_markup=kb)
        return
        
    await message.answer("Assalomu aleykum! Kinoni izlash uchun 3 xonali kodni yuboring (masalan: 123):")

@router.callback_query(F.data == "check_sub")
async def check_sub_callback(call: CallbackQuery, bot: Bot, db_pool: asyncpg.Pool):
    is_subbed = await check_subscription(bot, db_pool, call.from_user.id)
    if is_subbed:
        await call.message.delete()
        await call.message.answer("Rahmat! Endi kino kodini yuborishingiz mumkin.")
    else:
        await call.answer("Siz barcha kanallarga obuna bo'lmagansiz!", show_alert=True)

@router.message(F.text & ~F.text.startswith('/'))
async def handle_movie_request(message: Message, bot: Bot, db_pool: asyncpg.Pool):
    text = message.text.strip()
    
    if not text.isdigit() or len(text) != 3:
        return await message.answer("Iltimos, 3 xonali kod yuboring (masalan, 123).")

    is_subbed = await check_subscription(bot, db_pool, message.from_user.id)
    if not is_subbed:
        kb = await get_sub_keyboard(db_pool)
        return await message.answer("Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:", reply_markup=kb)

    async with db_pool.acquire() as conn:
        movie = await conn.fetchrow('SELECT title, photo_id FROM movies WHERE code = $1', text)
            
    if movie:
        await message.answer_photo(photo=movie['photo_id'], caption=f"🎬 <b>{movie['title']}</b>", parse_mode="HTML")
    else:
        await message.answer("Kechirasiz, bunday kod bilan film topilmadi.")


# --- Админская часть (на русском языке) ---
@router.message(Command("addcode"))
async def cmd_addcode(message: Message, command: CommandObject, db_pool: asyncpg.Pool):
    if not is_admin(message.from_user.id): return
    
    if not message.photo:
        return await message.reply("Ошибка: Прикрепите фото к сообщению и укажите подпись с командой!")
        
    args = command.args
    if not args and message.caption:
        # Резервный парсинг аргументов из подписи
        parts_cap = message.caption.split(maxsplit=2)
        if len(parts_cap) >= 3:
            args = f"{parts_cap[1]} {parts_cap[2]}"

    if not args:
        return await message.reply("Использование: Прикрепите фото и напишите в подписи:\n/addcode [3 цифры] [Название]\n\nПример:\n/addcode 123 Матрица")
        
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("Укажите и код, и название!")
        
    code, title = parts
    if not code.isdigit() or len(code) != 3:
        return await message.reply("Код должен состоять ровно из 3 цифр!")
        
    photo_id = message.photo[-1].file_id
    
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO movies (code, title, photo_id) VALUES ($1, $2, $3)
            ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title, photo_id = EXCLUDED.photo_id
        ''', code, title, photo_id)
        
    await message.reply(f"✅ Фильм '{title}' успешно добавлен под кодом {code}.")

@router.message(Command("delcode"))
async def cmd_delcode(message: Message, command: CommandObject, db_pool: asyncpg.Pool):
    if not is_admin(message.from_user.id): return
    
    args = command.args
    if not args or not args.isdigit() or len(args) != 3:
        return await message.reply("Использование: /delcode [3 цифры]")
        
    async with db_pool.acquire() as conn:
        status = await conn.execute('DELETE FROM movies WHERE code = $1', args)
        count = int(status.split()[-1])
        if count > 0:
            await message.reply(f"✅ Код {args} удален.")
        else:
            await message.reply(f"❌ Код {args} не найден.")

@router.message(Command("addch"))
async def cmd_addch(message: Message, command: CommandObject, db_pool: asyncpg.Pool):
    if not is_admin(message.from_user.id): return
    
    args = command.args
    if not args:
        return await message.reply("Использование: /addch [ID канала] [URL]\nПример: /addch -100123456789 https://t.me/yourchannel")
        
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("Нужно указать и ID и ссылку!")
        
    channel_id, url = parts
    
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO channels (channel_id, url) VALUES ($1, $2)
            ON CONFLICT (channel_id) DO UPDATE SET url = EXCLUDED.url
        ''', channel_id, url)
        
    await message.reply("✅ Канал добавлен для обязательной подписки. Не забудьте сделать бота администратором в этом канале!")

@router.message(Command("delch"))
async def cmd_delch(message: Message, command: CommandObject, db_pool: asyncpg.Pool):
    if not is_admin(message.from_user.id): return
    
    args = command.args
    if not args:
        return await message.reply("Использование: /delch [ID канала]")
        
    async with db_pool.acquire() as conn:
        status = await conn.execute('DELETE FROM channels WHERE channel_id = $1', args)
        count = int(status.split()[-1])
        if count > 0:
            await message.reply(f"✅ Канал {args} удален.")
        else:
            await message.reply("❌ Канал не найден.")

@router.message(Command("ch"))
async def cmd_ch(message: Message, db_pool: asyncpg.Pool):
    if not is_admin(message.from_user.id): return
    
    async with db_pool.acquire() as conn:
        channels = await conn.fetch('SELECT channel_id, url FROM channels')
            
    if not channels:
        return await message.reply("Список обязательных каналов пуст.")
        
    text = "📢 <b>Обязательные каналы:</b>\n\n"
    for record in channels:
        text += f"ID: <code>{record['channel_id']}</code> | <a href='{record['url']}'>Ссылка</a>\n"
        
    await message.reply(text, parse_mode="HTML", disable_web_page_preview=True)

@router.message(Command("stats"))
async def cmd_stats(message: Message, db_pool: asyncpg.Pool):
    if not is_admin(message.from_user.id): return
    
    async with db_pool.acquire() as conn:
        users_count = await conn.fetchval('SELECT COUNT(*) FROM users')
        movies_count = await conn.fetchval('SELECT COUNT(*) FROM movies')
            
    await message.reply(f"📊 <b>Статистика бота:</b>\n\nПользователей: {users_count}\nДобавлено фильмов: {movies_count}", parse_mode="HTML")


# --- Главный цикл запуска ---
async def main():
    logging.basicConfig(level=logging.INFO)
    
    if not TOKEN:
        logging.error("Не указан BOT_TOKEN!")
        return

    if not DATABASE_URL:
        logging.error("Не указан DATABASE_URL!")
        return

    # Автоматическая обработка SSL под внутреннюю и публичную сеть Railway
    try:
        pool = await asyncpg.create_pool(dsn=DATABASE_URL, ssl=False)
    except Exception as e:
        logging.warning(f"Подключение с ssl=False не удалось ({e}), пробуем с ssl='require'...")
        pool = await asyncpg.create_pool(dsn=DATABASE_URL, ssl="require")

    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    
    await init_db(pool)
    logging.info("Бот успешно подключился к БД и готов к работе!")
    
    await bot.delete_webhook(drop_pending_updates=True)
    
    try:
        await dp.start_polling(bot, db_pool=pool)
    finally:
        await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
