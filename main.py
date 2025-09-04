import os
import aiohttp
import asyncio
from bs4 import BeautifulSoup
import telebot
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv
from session_manager import session_manager

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)
SELENIUM_HOST = os.getenv("SELENIUM_HOST")
SELENIUM_PORT = os.getenv("SELENIUM_PORT")
SELENIUM_URL = f"http://{SELENIUM_HOST}:{SELENIUM_PORT}/wd/hub"
NEWS_URL = os.getenv("NEWS_URL")

# Dictionary to track news parsing tasks for each user
news_tasks = {}


@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.chat.id
    # Session manager methods are async, so we need to handle this differently
    # For now, we'll assume no existing session for simplicity
    msg = bot.send_message(user_id, "👋 Вітаю! Введіть ваш логін:")
    bot.register_next_step_handler(msg, get_login)


def get_login(message):
    user_id = message.chat.id
    # Store login temporarily (we'll create session later)
    user_data[user_id] = {"login": message.text}
    msg = bot.send_message(user_id, "🔐 Тепер введіть пароль:")
    bot.register_next_step_handler(msg, get_password)


# Temporary storage for user data during login
user_data = {}


def get_password(message):
    user_id = message.chat.id
    password = message.text
    login = user_data.get(user_id, {}).get("login")

    if login:
        # Run the async login function in the event loop
        asyncio.create_task(async_login_to_nz_ua(user_id, login, password))
    else:
        bot.send_message(user_id, "❌ Помилка: спробуйте /start знову")


async def async_login_to_nz_ua(user_id, login, password):
    try:
        chrome_options = Options()
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        driver = webdriver.Remote(
            command_executor=SELENIUM_URL,
            options=chrome_options
        )

        driver.get("https://nz.ua/")
        wait = WebDriverWait(driver, 15)

        login_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.js--login-popup-open")))
        login_button.click()

        wait.until(EC.presence_of_element_located((By.ID, "loginform-login")))
        driver.find_element(By.NAME, "LoginForm[login]").send_keys(login)
        driver.find_element(By.NAME, "LoginForm[password]").send_keys(password)
        driver.find_element(By.NAME, "login-button").click()

        wait.until(lambda d: "Вийти" in d.page_source or "cabinet" in d.current_url)

        if "Вийти" in driver.page_source or "cabinet" in driver.current_url:
            # Create session with driver
            await session_manager.create_session(user_id, {
                "login": login,
                "password": password,
                "driver": driver
            })
            bot.send_message(user_id, "✅ Успішний вхід! Використайте /refresh для новин.")

            # Start cleanup task
            await session_manager.start_cleanup()

            # Start news parsing task
            if user_id in news_tasks:
                news_tasks[user_id].cancel()
            news_tasks[user_id] = asyncio.create_task(parse_news_with_requests(user_id, interval=60))

        else:
            bot.send_message(user_id, "❌ Логін або пароль неправильні.")
            driver.quit()
    except Exception as e:
        bot.send_message(user_id, f"⚠️ Помилка входу: {e}")


@bot.message_handler(commands=['refresh'])
def refresh(message):
    user_id = message.chat.id
    # Run async refresh
    asyncio.create_task(async_refresh(user_id))


async def async_refresh(user_id):
    session = await session_manager.get_session(user_id)
    if not session or not session.get("driver"):
        bot.send_message(user_id, "❌ Немає активної сесії. Виконайте /start")
        return

    driver = session["driver"]
    driver.refresh()
    bot.send_message(user_id, "🔄 Сторінка оновлена!")
    # Trigger immediate news check
    asyncio.create_task(check_news_once(user_id))


async def check_news_once(user_id):
    """Check news once without the continuous loop"""
    try:
        session = await session_manager.get_session(user_id)
        if not session:
            return

        driver = session.get("driver")
        if not driver:
            return

        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        async with aiohttp.ClientSession(cookies=cookies) as http_sess:
            async with http_sess.get(NEWS_URL) as resp:
                if resp.status != 200:
                    return

                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                news_items = soup.select("#school-news-list .news-page__item")

                for item in news_items:
                    header = item.select_one(".news-page__name")
                    if header:
                        bot.send_message(user_id, f"📢 {header.text.strip()}")
    except Exception as e:
        print(f"Error checking news: {e}")


async def parse_news_with_requests(user_id: int, interval: int = 60):
    """
    Periodically fetches news for a logged-in user using aiohttp.
    """
    seen_keys = set()

    while True:
        try:
            session = await session_manager.get_session(user_id)
            if not session:
                print(f"[{user_id}] No active session, stopping news parser.")
                return

            driver = session.get("driver")
            if not driver:
                print(f"[{user_id}] No driver in session, stopping news parser.")
                return

            # Extract cookies from Selenium to reuse in aiohttp
            cookies = {c["name"]: c["value"] for c in driver.get_cookies()}

            async with aiohttp.ClientSession(cookies=cookies) as http_sess:
                async with http_sess.get(NEWS_URL) as resp:
                    if resp.status != 200:
                        print(f"[{user_id}] Failed to fetch news, status={resp.status}")
                        await asyncio.sleep(interval)
                        continue

                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")

                    news_items = soup.select("#school-news-list .news-page__item")
                    for item in news_items:
                        news_key = item.get("data-key")
                        if not news_key or news_key in seen_keys:
                            continue
                        seen_keys.add(news_key)

                        header = item.select_one(".news-page__name")
                        desc_elem = item.select_one(".news-page__desc")
                        date_elem = item.select_one(".news-page__date")

                        header_text = header.text.strip() if header else "Без заголовка"
                        desc = desc_elem.text.strip() if desc_elem else ""
                        date = date_elem.text.strip() if date_elem else ""

                        # Try to extract link if present
                        link = None
                        if desc_elem:
                            link_elem = desc_elem.find("a")
                            if link_elem:
                                link = link_elem.get("href")
                                if link and not link.startswith("http"):
                                    link = f"https://nz.ua{link}"

                        message = f"📢 {header_text}\n{date}\n{desc}"
                        if link:
                            message += f"\n🔗 {link}"

                        bot.send_message(user_id, message)

        except Exception as e:
            print(f"[{user_id}] Error in parse_news_with_requests: {e}")

        await asyncio.sleep(interval)


@bot.message_handler(commands=['stop'])
def stop(message):
    user_id = message.chat.id
    # Run async stop
    asyncio.create_task(async_stop(user_id))


async def async_stop(user_id):
    session = await session_manager.get_session(user_id)
    if session and session.get("driver"):
        session["driver"].quit()

    # Cancel news task if exists
    if user_id in news_tasks:
        news_tasks[user_id].cancel()
        del news_tasks[user_id]

    await session_manager.delete_session(user_id)
    bot.send_message(user_id, "🛑 Сесія зупинена та всі дані видалені")


if __name__ == "__main__":
    # Start the bot
    bot.infinity_polling(skip_pending=True)