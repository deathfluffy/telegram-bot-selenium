import os
import time
import telebot
import threading
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


@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.chat.id
    existing = session_manager.get_session(user_id)

    if existing and existing.get("driver"):
        bot.send_message(user_id, "ℹ️ У вас вже є активна сесія. Використайте /refresh або /stop")
        return

    msg = bot.send_message(user_id, "👋 Вітаю! Введіть ваш логін:")
    bot.register_next_step_handler(msg, get_login)


def get_login(message):
    user_id = message.chat.id
    session_manager.create_session(user_id, {"login": message.text})
    msg = bot.send_message(user_id, "🔐 Тепер введіть пароль:")
    bot.register_next_step_handler(msg, get_password)


def get_password(message):
    user_id = message.chat.id
    session = session_manager.get_session(user_id)
    session["password"] = message.text
    session_manager.update_session_data(user_id, session)
    login_to_nz_ua(user_id)


@bot.message_handler(commands=['refresh'])
def refresh(message):
    user_id = message.chat.id
    session = session_manager.get_session(user_id)

    if not session or not session.get("driver"):
        bot.send_message(user_id, "❌ Немає активної сесії. Виконайте /start")
        return

    driver = session["driver"]
    driver.refresh()
    bot.send_message(user_id, "🔄 Сторінка оновлена!")
    parse_news(driver, user_id)


def login_to_nz_ua(user_id):
    session = session_manager.get_session(user_id)
    login = session["login"]
    password = session["password"]

    driver = None
    try:
        options = Options()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        driver = webdriver.Remote(
            command_executor='http://selenium-service.default.svc.cluster.local:4444/wd/hub',
            options=options
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
            session_manager.update_driver(user_id, driver)
            bot.send_message(user_id, "✅ Успішний вхід! Використайте /refresh для новин.")

            thread = threading.Thread(target=parse_news, args=(driver, user_id), daemon=True)
            thread.start()
        else:
            bot.send_message(user_id, "❌ Логін або пароль неправильні.")
            driver.quit()
    except Exception as e:
        bot.send_message(user_id, f"⚠️ Помилка входу: {e}")
        if driver:
            driver.quit()


def parse_news(driver, user_id, interval=60):
    seen_keys = set()
    while True:
        try:
            driver.get("https://nz.ua/dashboard/news")
            wait = WebDriverWait(driver, 15)
            wait.until(EC.presence_of_element_located((By.ID, "school-news-list")))

            news_items = driver.find_elements(By.CSS_SELECTOR, "#school-news-list .news-page__item")
            for item in news_items:
                news_key = item.get_attribute("data-key")
                if news_key in seen_keys:
                    continue
                seen_keys.add(news_key)

                try:
                    header = item.find_element(By.CLASS_NAME, "news-page__name").text.strip()
                    desc_elem = item.find_element(By.CLASS_NAME, "news-page__desc")
                    desc = desc_elem.text.strip() if desc_elem else ""

                    # Try to get link if present
                    link = None
                    try:
                        link_elem = desc_elem.find_element(By.TAG_NAME, "a")
                        link = link_elem.get_attribute("href")
                        if link and not link.startswith("http"):
                            link = f"https://nz.ua{link}"
                    except:
                        pass  # no link, ignore

                    date = item.find_element(By.CLASS_NAME, "news-page__date").text.strip()
                    message = f"📢 {header}\n{date}\n{desc}"
                    if link:
                        message += f"\n🔗 {link}"
                    bot.send_message(user_id, message)

                except Exception as e:
                    print(f"Error processing news item {news_key}: {e}")

            time.sleep(interval)

        except Exception as e:
            print(f"Background news watcher error for user {user_id}: {e}")
            time.sleep(interval)

@bot.message_handler(commands=['stop'])
def stop(message):
    user_id = message.chat.id
    session = session_manager.get_session(user_id)
    if session and session.get("driver"):
        session["driver"].quit()
    session_manager.delete_session(user_id)
    bot.send_message(user_id, "🛑 Сесія зупинена та всі дані видалені")



if __name__ == "__main__":
    bot.infinity_polling(skip_pending=True)
