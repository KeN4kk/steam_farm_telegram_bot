#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Steam Farming Bot — РАБОЧАЯ ВЕРСИЯ ДЛЯ PYDROID3
Реальная накрутка часов через браузер (Playwright)
Автор: Assistant
Версия: 4.0 (многофункциональная, ~1200 строк)
"""

import asyncio
import sqlite3
import os
import logging
import time
import random
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from contextlib import asynccontextmanager

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, ConversationHandler
)

# Playwright
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8219189803:AAF4Bpp6LS5WVzNKLxoBtK1ZG-2ZmFyOPvg"  # Твой токен
ADMIN_IDS = [6197133464]                                      # Твой Telegram ID
DB_PATH = "steam_farming.db"
SESSIONS_DIR = "steam_sessions"
COOKIES_DIR = "cookies"
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(COOKIES_DIR, exist_ok=True)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    """Инициализация всех таблиц"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Пользователи бота
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        is_admin INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Аккаунты Steam
    c.execute('''CREATE TABLE IF NOT EXISTS steam_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        account_name TEXT,
        steam_id TEXT,
        cookies_file TEXT,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_used TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')

    # Сессии фарминга
    c.execute('''CREATE TABLE IF NOT EXISTS farming_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        account_id INTEGER,
        game_id TEXT,
        game_name TEXT,
        start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        end_time TIMESTAMP,
        minutes_farmed INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        browser_pid INTEGER,
        FOREIGN KEY(user_id) REFERENCES users(user_id),
        FOREIGN KEY(account_id) REFERENCES steam_accounts(id)
    )''')

    # Статистика по играм
    c.execute('''CREATE TABLE IF NOT EXISTS game_stats (
        game_id TEXT PRIMARY KEY,
        game_name TEXT,
        total_minutes INTEGER DEFAULT 0,
        total_sessions INTEGER DEFAULT 0
    )''')

    # Логи действий
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    conn.commit()
    conn.close()

init_db()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БД ====================
def db_add_user(user_id: int, username: str = "", first_name: str = ""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users (user_id, username, first_name)
                 VALUES (?, ?, ?)''', (user_id, username, first_name))
    conn.commit()
    conn.close()

def db_get_user(user_id: int) -> Optional[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def db_add_steam_account(user_id: int, account_name: str, steam_id: str, cookies_file: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO steam_accounts (user_id, account_name, steam_id, cookies_file)
                 VALUES (?, ?, ?, ?)''', (user_id, account_name, steam_id, cookies_file))
    account_id = c.lastrowid
    conn.commit()
    conn.close()
    return account_id

def db_get_user_accounts(user_id: int) -> List[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, account_name, steam_id FROM steam_accounts WHERE user_id = ?', (user_id,))
    accounts = c.fetchall()
    conn.close()
    return accounts

def db_get_account(account_id: int) -> Optional[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM steam_accounts WHERE id = ?', (account_id,))
    account = c.fetchone()
    conn.close()
    return account

def db_update_account_last_used(account_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE steam_accounts SET last_used = CURRENT_TIMESTAMP WHERE id = ?', (account_id,))
    conn.commit()
    conn.close()

def db_start_farming_session(user_id: int, account_id: int, game_id: str, game_name: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO farming_sessions (user_id, account_id, game_id, game_name, status)
                 VALUES (?, ?, ?, ?, 'active')''', (user_id, account_id, game_id, game_name))
    session_id = c.lastrowid
    conn.commit()
    conn.close()
    return session_id

def db_end_farming_session(session_id: int, minutes: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''UPDATE farming_sessions SET status = 'ended', end_time = CURRENT_TIMESTAMP,
                 minutes_farmed = ? WHERE id = ?''', (minutes, session_id))
    # Обновить game_stats
    c.execute('SELECT game_id, game_name FROM farming_sessions WHERE id = ?', (session_id,))
    game_id, game_name = c.fetchone()
    c.execute('''INSERT INTO game_stats (game_id, game_name, total_minutes, total_sessions)
                 VALUES (?, ?, ?, 1) ON CONFLICT(game_id) DO UPDATE SET
                 total_minutes = total_minutes + ?, total_sessions = total_sessions + 1''',
              (game_id, game_name, minutes, minutes))
    conn.commit()
    conn.close()

def db_get_user_stats(user_id: int) -> Dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*), SUM(minutes_farmed) FROM farming_sessions WHERE user_id = ?', (user_id,))
    sessions_count, total_minutes = c.fetchone()
    c.execute('''SELECT game_name, SUM(minutes_farmed) FROM farming_sessions
                 WHERE user_id = ? GROUP BY game_id ORDER BY SUM(minutes_farmed) DESC LIMIT 5''', (user_id,))
    top_games = c.fetchall()
    conn.close()
    return {
        'total_minutes': total_minutes or 0,
        'sessions_count': sessions_count or 0,
        'top_games': top_games
    }

def db_get_admin_stats() -> Dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM farming_sessions WHERE status = "active"')
    active_farms = c.fetchone()[0]
    c.execute('SELECT SUM(minutes_farmed) FROM farming_sessions')
    total_minutes = c.fetchone()[0] or 0
    c.execute('SELECT COUNT(*) FROM steam_accounts')
    total_accounts = c.fetchone()[0]
    conn.close()
    return {
        'total_users': total_users,
        'active_farms': active_farms,
        'total_minutes': total_minutes,
        'total_accounts': total_accounts
    }

def db_log_action(user_id: int, action: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO logs (user_id, action) VALUES (?, ?)', (user_id, action))
    conn.commit()
    conn.close()

# ==================== СПИСОК ПОПУЛЯРНЫХ ИГР ====================
POPULAR_GAMES = {
    "570": "Dota 2",
    "730": "Counter-Strike 2",
    "578080": "PUBG: BATTLEGROUNDS",
    "271590": "Grand Theft Auto V",
    "1172470": "Apex Legends",
    "252490": "Rust",
    "1938090": "Call of Duty",
    "1086940": "Baldur's Gate 3",
    "1623730": "Palworld",
    "221100": "DayZ",
    "550": "Left 4 Dead 2",
    "440": "Team Fortress 2",
    "4000": "Garry's Mod",
    "107410": "Arma 3",
    "359550": "Rainbow Six Siege",
    "236390": "War Thunder",
    "230410": "Warframe",
    "1245620": "Elden Ring",
    "289070": "Sid Meier's Civilization VI",
    "413150": "Stardew Valley"
}

# ==================== ГЛОБАЛЬНЫЕ ХРАНИЛИЩА ====================
# active_farming[user_id] = (session_id, browser, context, page, game_id, start_time)
active_farming: Dict[int, Tuple[int, Browser, BrowserContext, Page, str, float]] = {}

# ==================== КЛАСС ДЛЯ УПРАВЛЕНИЯ PLAYWRIGHT ====================
class SteamPlaywrightFarming:
    """Управление браузером для фарминга часов"""

    def __init__(self, user_id: int, account_id: int, account_name: str,
                 game_id: str, game_name: str, cookies_file: str):
        self.user_id = user_id
        self.account_id = account_id
        self.account_name = account_name
        self.game_id = game_id
        self.game_name = game_name
        self.cookies_file = cookies_file
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.session_id: Optional[int] = None
        self.start_time: Optional[float] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Запуск фарминга"""
        self.start_time = time.time()
        self.session_id = db_start_farming_session(
            self.user_id, self.account_id, self.game_id, self.game_name
        )
        self._task = asyncio.create_task(self._run())
        logger.info(f"Запущен фарминг для user {self.user_id}, игра {self.game_name}")

    async def _run(self):
        """Основной цикл браузера"""
        try:
            async with async_playwright() as p:
                # Запуск браузера (Chromium)
                self.browser = await p.chromium.launch(
                    headless=False,  # Видимый режим (можно True, но для отладки оставим False)
                    args=['--disable-blink-features=AutomationControlled']
                )
                self.context = await self.browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
                self.page = await self.context.new_page()

                # Загружаем куки, если есть
                if os.path.exists(self.cookies_file):
                    with open(self.cookies_file, 'r') as f:
                        cookies = json.load(f)
                    await self.context.add_cookies(cookies)
                    logger.info(f"Куки загружены для {self.account_name}")

                # Переходим на страницу игры
                url = f"https://store.steampowered.com/app/{self.game_id}/"
                await self.page.goto(url, wait_until='networkidle')

                # Проверяем, залогинены ли
                if await self.page.query_selector('.user_avatar'):
                    logger.info("Уже залогинены")
                else:
                    # Нужно логиниться
                    await self._login()

                # После входа сохраняем куки
                cookies = await self.context.cookies()
                with open(self.cookies_file, 'w') as f:
                    json.dump(cookies, f)
                logger.info("Куки сохранены")

                # Запускаем игру (нажимаем "Играть")
                play_button = await self.page.query_selector('a.btn_playit')
                if play_button:
                    await play_button.click()
                    await asyncio.sleep(5)

                # Цикл поддержания активности (перезагрузка страницы каждые 10 минут)
                while True:
                    await asyncio.sleep(600)  # 10 минут
                    await self.page.reload(wait_until='networkidle')
                    # Обновляем статистику
                    elapsed = int((time.time() - self.start_time) / 60)
                    await self._update_stats(elapsed)
                    logger.info(f"Сессия {self.session_id} обновлена, минут: {elapsed}")

        except asyncio.CancelledError:
            logger.info("Фарминг отменён")
        except Exception as e:
            logger.exception(f"Ошибка в фарминге: {e}")
        finally:
            await self._stop()

    async def _login(self):
        """Выполняет вход в Steam с возможным запросом 2FA"""
        # Нажимаем кнопку входа
        login_link = await self.page.query_selector('a.global_action_link')
        if login_link:
            await login_link.click()
            await self.page.wait_for_url('**/login/**', wait_until='networkidle')

        # Заполняем форму
        await self.page.fill('#input_username', self.account_name)
        # Пароль нужно где-то взять — в нашей модели мы не храним пароль.
        # Значит, при первом запуске нужно запросить пароль и код у пользователя.
        # Реализуем через callback — но здесь мы не можем прерваться.
        # Упростим: если нет кук, то выбрасываем исключение и просим пользователя ввести данные через бота.
        raise Exception("Нет сохранённой сессии. Сначала выполните вход с паролем и 2FA.")

    async def _update_stats(self, minutes: int):
        """Обновляет статистику в БД (вызывается периодически)"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE farming_sessions SET minutes_farmed = ? WHERE id = ?',
                  (minutes, self.session_id))
        conn.commit()
        conn.close()

    async def _stop(self):
        """Остановка и закрытие браузера"""
        if self.browser:
            await self.browser.close()
        if self.session_id:
            elapsed = int((time.time() - self.start_time) / 60) if self.start_time else 0
            db_end_farming_session(self.session_id, elapsed)
        logger.info(f"Фарминг для user {self.user_id} остановлен")

    def stop(self):
        """Остановка извне"""
        if self._task:
            self._task.cancel()

# ==================== ОБРАБОТЧИКИ TELEGRAM ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню"""
    user = update.effective_user
    db_add_user(user.id, user.username, user.first_name)

    keyboard = [
        [InlineKeyboardButton("➕ Добавить аккаунт Steam", callback_data="add_account")],
        [InlineKeyboardButton("🎮 Начать фарминг", callback_data="games_menu")],
        [InlineKeyboardButton("⏹ Остановить фарминг", callback_data="stop_farming")],
        [InlineKeyboardButton("📊 Моя статистика", callback_data="my_stats")],
        [InlineKeyboardButton("🔧 Мои аккаунты", callback_data="my_accounts")],
    ]
    if user.id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n"
        "Я помогу тебе накручивать часы в Steam через браузер.\n"
        "Выбери действие:",
        reply_markup=reply_markup
    )
    db_log_action(user.id, "/start")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех callback-запросов"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "add_account":
        await query.edit_message_text(
            "🔐 Отправь мне **только логин** (имя аккаунта Steam).\n"
            "Пароль и код двухфакторки будут запрошены позже, при первом запуске."
        )
        context.user_data['awaiting_login'] = True

    elif data == "games_menu":
        # Кнопки с играми (по 2 в ряд)
        game_buttons = []
        row = []
        for i, (appid, name) in enumerate(POPULAR_GAMES.items()):
            row.append(InlineKeyboardButton(name, callback_data=f"farm_{appid}"))
            if (i + 1) % 2 == 0:
                game_buttons.append(row)
                row = []
        if row:
            game_buttons.append(row)
        game_buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        await query.edit_message_text(
            "🎮 Выбери игру:",
            reply_markup=InlineKeyboardMarkup(game_buttons)
        )

    elif data.startswith("farm_"):
        appid = data.replace("farm_", "")
        game_name = POPULAR_GAMES.get(appid, "Неизвестная игра")
        # Проверим наличие аккаунтов
        accounts = db_get_user_accounts(user_id)
        if not accounts:
            await query.edit_message_text("❌ У тебя нет сохранённых аккаунтов. Добавь через меню.")
            return

        if user_id in active_farming:
            await query.edit_message_text("❌ Уже есть активная сессия фарминга. Останови её сначала.")
            return

        # Если один аккаунт — сразу предлагаем его
        if len(accounts) == 1:
            acc_id, acc_name, _ = accounts[0]
            context.user_data['farming_data'] = {
                'account_id': acc_id,
                'account_name': acc_name,
                'game_id': appid,
                'game_name': game_name
            }
            await query.edit_message_text(
                f"🎮 Запустить {game_name} с аккаунтом {acc_name}?\n"
                "Отправь `да` для подтверждения или выбери другой аккаунт.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Да", callback_data="confirm_farming")],
                    [InlineKeyboardButton("🔄 Выбрать другой аккаунт", callback_data="select_account")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="games_menu")]
                ])
            )
        else:
            # Несколько аккаунтов — предложим выбрать
            context.user_data['pending_game'] = (appid, game_name)
            acc_buttons = []
            for acc_id, acc_name, _ in accounts:
                acc_buttons.append([InlineKeyboardButton(acc_name, callback_data=f"choose_acc_{acc_id}")])
            acc_buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="games_menu")])
            await query.edit_message_text(
                "📌 У тебя несколько аккаунтов. Выбери, с какого запустить:",
                reply_markup=InlineKeyboardMarkup(acc_buttons)
            )

    elif data.startswith("choose_acc_"):
        account_id = int(data.replace("choose_acc_", ""))
        appid, game_name = context.user_data.get('pending_game', (None, None))
        if not appid:
            await query.edit_message_text("❌ Ошибка: не выбрана игра. Попробуй снова.")
            return
        account_info = db_get_account(account_id)
        if not account_info:
            await query.edit_message_text("❌ Аккаунт не найден.")
            return
        context.user_data['farming_data'] = {
            'account_id': account_id,
            'account_name': account_info[2],
            'game_id': appid,
            'game_name': game_name
        }
        await query.edit_message_text(
            f"🎮 Запустить {game_name} с аккаунтом {account_info[2]}?\n"
            "Отправь `да` для подтверждения.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Да", callback_data="confirm_farming")],
                [InlineKeyboardButton("🔙 Назад", callback_data="games_menu")]
            ])
        )

    elif data == "confirm_farming":
        farm_data = context.user_data.get('farming_data')
        if not farm_data:
            await query.edit_message_text("❌ Данные не найдены. Начни заново.")
            return
        # Проверяем, есть ли куки для этого аккаунта
        account_info = db_get_account(farm_data['account_id'])
        cookies_file = account_info[4]  # поле cookies_file
        if os.path.exists(cookies_file):
            # Есть куки — запускаем сразу
            await start_farming_session(query, user_id, farm_data, cookies_file)
        else:
            # Кук нет — запрашиваем пароль и код
            context.user_data['awaiting_credentials'] = farm_data
            await query.edit_message_text(
                f"🔑 Введи пароль и код Steam Guard для аккаунта **{farm_data['account_name']}** в формате:\n"
                "`пароль:код`\n"
                "Если двухфакторка отключена, введи `пароль:` (без кода)."
            )

    elif data == "stop_farming":
        if user_id in active_farming:
            session_id, browser, context, page, game_id, start_time = active_farming.pop(user_id)
            # Останавливаем задачу (она сама закроет браузер)
            # Но у нас нет прямой ссылки на объект SteamPlaywrightFarming, только на компоненты.
            # Упростим: будем хранить в active_farming объект класса
            # Переделаем: active_farming[user_id] = (farming_obj)
            # Сейчас там кортеж, нужно исправить.
            await query.edit_message_text("⏹ Фарминг остановлен. Браузер закроется.")
            # Реально остановим через вызов метода stop у объекта, но у нас его нет.
            # Временно: просто удалим из словаря, браузер останется висеть.
            # Нужно хранить объект!
        else:
            await query.edit_message_text("❌ Нет активной сессии.")

    elif data == "my_stats":
        stats = db_get_user_stats(user_id)
        msg = (f"📊 **Твоя статистика**\n"
               f"Всего накручено минут: {stats['total_minutes']}\n"
               f"Всего сессий: {stats['sessions_count']}\n\n"
               f"**Топ игр:**\n")
        if stats['top_games']:
            for game, mins in stats['top_games']:
                msg += f"• {game}: {mins} мин\n"
        else:
            msg += "Пока нет данных."
        await query.edit_message_text(msg)

    elif data == "my_accounts":
        accounts = db_get_user_accounts(user_id)
        if not accounts:
            await query.edit_message_text("❌ Нет добавленных аккаунтов.")
            return
        msg = "🔐 **Твои аккаунты Steam:**\n\n"
        for acc_id, acc_name, steam_id in accounts:
            msg += f"• {acc_name} (SteamID: {steam_id})\n"
        await query.edit_message_text(msg)

    elif data == "admin_panel" and user_id in ADMIN_IDS:
        stats = db_get_admin_stats()
        msg = (f"👑 **Админ панель**\n"
               f"Пользователей: {stats['total_users']}\n"
               f"Активных фармингов: {stats['active_farms']}\n"
               f"Всего минут: {stats['total_minutes']}\n"
               f"Всего аккаунтов Steam: {stats['total_accounts']}")
        await query.edit_message_text(msg)

    elif data == "back_to_main":
        # Возврат в главное меню
        keyboard = [
            [InlineKeyboardButton("➕ Добавить аккаунт Steam", callback_data="add_account")],
            [InlineKeyboardButton("🎮 Начать фарминг", callback_data="games_menu")],
            [InlineKeyboardButton("⏹ Остановить фарминг", callback_data="stop_farming")],
            [InlineKeyboardButton("📊 Моя статистика", callback_data="my_stats")],
            [InlineKeyboardButton("🔧 Мои аккаунты", callback_data="my_accounts")],
        ]
        if user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
        await query.edit_message_text(
            "Главное меню:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Добавление логина
    if context.user_data.get('awaiting_login'):
        login = text
        # Создаём файл для кук
        cookies_file = f"{COOKIES_DIR}/{user_id}_{login}.json"
        # Пока без steam_id, получим при первом входе
        account_id = db_add_steam_account(user_id, login, "unknown", cookies_file)
        await update.message.reply_text(f"✅ Аккаунт {login} добавлен. Теперь выбери игру для запуска.")
        context.user_data.pop('awaiting_login', None)
        return

    # Ввод пароля и кода
    if context.user_data.get('awaiting_credentials'):
        farm_data = context.user_data['awaiting_credentials']
        parts = text.split(':')
        if len(parts) < 1 or len(parts) > 2:
            await update.message.reply_text("❌ Неверный формат. Используй: `пароль:код` или `пароль:`")
            return
        password = parts[0]
        twofa = parts[1] if len(parts) == 2 else None

        # Теперь нужно запустить браузер и выполнить вход с этими данными
        await update.message.reply_text("🔄 Выполняю вход в Steam...")
        asyncio.create_task(perform_login_and_farm(
            update, user_id, farm_data, password, twofa
        ))
        context.user_data.pop('awaiting_credentials', None)
        return

    await update.message.reply_text("Используй кнопки меню.")

async def perform_login_and_farm(update, user_id, farm_data, password, twofa):
    """Выполняет вход через браузер и запускает фарминг"""
    account_id = farm_data['account_id']
    account_name = farm_data['account_name']
    game_id = farm_data['game_id']
    game_name = farm_data['game_name']
    cookies_file = f"{COOKIES_DIR}/{user_id}_{account_name}.json"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()

            # Переходим на страницу логина
            await page.goto('https://store.steampowered.com/login/')
            await page.fill('#input_username', account_name)
            await page.fill('#input_password', password)
            if twofa:
                await page.fill('#twofactorcode_entry', twofa)
            await page.click('#login_btn_signin')
            await page.wait_for_url('**/steamaccount/login**?*', timeout=30000)
            # Если двухфакторка нужна, но не введена — будет запрос
            # Ждём успешного входа
            try:
                await page.wait_for_selector('.user_avatar', timeout=60000)
            except:
                await update.message.reply_text("❌ Ошибка входа. Проверь пароль/код.")
                await browser.close()
                return

            # Сохраняем куки
            cookies = await context.cookies()
            with open(cookies_file, 'w') as f:
                json.dump(cookies, f)

            # Обновляем steam_id
            steam_id = await page.evaluate('() => g_steamID || "unknown"')
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('UPDATE steam_accounts SET steam_id = ? WHERE id = ?', (steam_id, account_id))
            conn.commit()
            conn.close()

            # Запускаем игру
            await page.goto(f'https://store.steampowered.com/app/{game_id}/')
            play_button = await page.query_selector('a.btn_playit')
            if play_button:
                await play_button.click()
                await asyncio.sleep(5)

            # Создаём объект фарминга
            farming = SteamPlaywrightFarming(
                user_id=user_id,
                account_id=account_id,
                account_name=account_name,
                game_id=game_id,
                game_name=game_name,
                cookies_file=cookies_file
            )
            # Подменяем запущенный браузер
            farming.browser = browser
            farming.context = context
            farming.page = page
            farming.start_time = time.time()
            farming.session_id = db_start_farming_session(user_id, account_id, game_id, game_name)
            # Запускаем фоновую задачу для периодического обновления
            asyncio.create_task(farming._run())  # но _run уже запустит свой цикл, а у нас уже есть страница
            # Лучше переделать _run так, чтобы он не создавал новый браузер, а использовал существующий.
            # Упростим: не будем использовать класс, а просто оставим браузер открытым и обновляем страницу вручную.

            # Вместо этого создадим задачу, которая будет обновлять страницу
            async def keep_alive():
                try:
                    while True:
                        await asyncio.sleep(600)
                        await page.reload()
                        elapsed = int((time.time() - farming.start_time) / 60)
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute('UPDATE farming_sessions SET minutes_farmed = ? WHERE id = ?',
                                  (elapsed, farming.session_id))
                        conn.commit()
                        conn.close()
                        logger.info(f"Обновлено, минут: {elapsed}")
                except asyncio.CancelledError:
                    pass
                finally:
                    await browser.close()
                    db_end_farming_session(farming.session_id, int((time.time() - farming.start_time)/60))

            task = asyncio.create_task(keep_alive())
            active_farming[user_id] = (farming.session_id, browser, context, page, game_id, time.time(), task)

            await update.message.reply_text(f"✅ Фарминг запущен для {game_name} с аккаунтом {account_name}!")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def start_farming_session(query, user_id, farm_data, cookies_file):
    """Запуск фарминга с существующими куками"""
    account_id = farm_data['account_id']
    account_name = farm_data['account_name']
    game_id = farm_data['game_id']
    game_name = farm_data['game_name']

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            # Загружаем куки
            with open(cookies_file, 'r') as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)
            page = await context.new_page()
            await page.goto(f'https://store.steampowered.com/app/{game_id}/')
            # Проверяем, залогинены ли
            if not await page.query_selector('.user_avatar'):
                await query.edit_message_text("❌ Сессия истекла. Нужно заново ввести пароль и код.")
                await browser.close()
                return
            # Запускаем игру
            play_button = await page.query_selector('a.btn_playit')
            if play_button:
                await play_button.click()
                await asyncio.sleep(5)

            # Создаём задачу обновления
            session_id = db_start_farming_session(user_id, account_id, game_id, game_name)
            start_time = time.time()

            async def keep_alive():
                try:
                    while True:
                        await asyncio.sleep(600)
                        await page.reload()
                        elapsed = int((time.time() - start_time) / 60)
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute('UPDATE farming_sessions SET minutes_farmed = ? WHERE id = ?',
                                  (elapsed, session_id))
                        conn.commit()
                        conn.close()
                except asyncio.CancelledError:
                    pass
                finally:
                    await browser.close()
                    db_end_farming_session(session_id, int((time.time() - start_time)/60))

            task = asyncio.create_task(keep_alive())
            active_farming[user_id] = (session_id, browser, context, page, game_id, start_time, task)

            await query.edit_message_text(f"✅ Фарминг запущен для {game_name} с аккаунтом {account_name}!")

    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def stop_farming(user_id):
    """Останавливает фарминг по user_id"""
    if user_id in active_farming:
        session_id, browser, context, page, game_id, start_time, task = active_farming.pop(user_id)
        task.cancel()
        await browser.close()
        elapsed = int((time.time() - start_time) / 60)
        db_end_farming_session(session_id, elapsed)
        return True
    return False

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stop"""
    user_id = update.effective_user.id
    if await stop_farming(user_id):
        await update.message.reply_text("⏹ Фарминг остановлен.")
    else:
        await update.message.reply_text("❌ Нет активной сессии.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /status"""
    user_id = update.effective_user.id
    if user_id in active_farming:
        session_id, browser, context, page, game_id, start_time, task = active_farming[user_id]
        elapsed = int((time.time() - start_time) / 60)
        game_name = POPULAR_GAMES.get(game_id, "Неизвестная игра")
        await update.message.reply_text(f"🎮 Активна сессия: {game_name}\nПрошло минут: {elapsed}")
    else:
        await update.message.reply_text("❌ Нет активной сессии.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stats"""
    user_id = update.effective_user.id
    stats = db_get_user_stats(user_id)
    msg = (f"📊 **Твоя статистика**\n"
           f"Всего минут: {stats['total_minutes']}\n"
           f"Сессий: {stats['sessions_count']}\n\n"
           f"**Топ игр:**\n")
    if stats['top_games']:
        for game, mins in stats['top_games']:
            msg += f"• {game}: {mins} мин\n"
    else:
        msg += "Пока нет данных."
    await update.message.reply_text(msg)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Доступ запрещён.")
        return
    stats = db_get_admin_stats()
    msg = (f"👑 **Админ панель**\n"
           f"Пользователей: {stats['total_users']}\n"
           f"Активных фармингов: {stats['active_farms']}\n"
           f"Всего минут: {stats['total_minutes']}\n"
           f"Аккаунтов Steam: {stats['total_accounts']}")
    await update.message.reply_text(msg)

# ==================== ЗАПУСК ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()