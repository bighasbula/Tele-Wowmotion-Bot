import os
from dotenv import load_dotenv
import telebot
from telebot import types
from supabase_utils import save_registration_to_supabase, get_webinar_dates, fetch_registrations, get_service_account_credentials
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
import io
import requests
import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Easily editable sync interval (in minutes)
SYNC_INTERVAL_MINUTES = 30
EXCEL_FILE_NAME = 'WebinarRegistrations.xlsx'

bot = telebot.TeleBot(TOKEN)

# Store user registration data temporarily
user_data = {}

# APScheduler setup
scheduler = BackgroundScheduler(timezone=timezone.utc)
scheduler.start()

def get_webinars_by_id():
    webinars = get_webinar_dates()
    return {str(w['id']): w for w in webinars}

def send_reminder(chat_id, message):
    try:
        bot.send_message(chat_id, message)
    except Exception as e:
        print(f"Failed to send message to {chat_id}: {e}")

def schedule_reminders_for_registration(reg, webinars_by_id):
    chat_id = reg.get('telegram_id')
    webinar_id = str(reg.get('webinar_id'))
    webinar = webinars_by_id.get(webinar_id)
    if not webinar or not chat_id:
        return
    # Parse webinar date as UTC-aware
    try:
        from dateutil import parser
        dt = parser.isoparse(webinar['date'])
        webinar_dt = dt.astimezone(timezone.utc)
    except Exception as e:
        print(f"Could not parse date for webinar {webinar_id}: {e}")
        return
    now = datetime.now(timezone.utc)
    reminders = []
    # Only schedule reminders that are in the future
    if webinar_dt - timedelta(days=1) > now:
        reminders.append((webinar_dt - timedelta(days=1), f"""Уже завтра! 🚀

{webinar_dt.strftime('%H:%M')} начнётся вебинар которого не было в Казахстане. Ты узнаешь секреты спортивной фотосессии. 

После вебинара ты уже будешь знать:

✅ Как выйти на стабильную съёмку спортивных мероприятий
✅ Какие настройки использовать для крутых кадров
✅ И как сразу получать заказы без рекламы и продвижения

⚠ Записи вебинара не будет — будь онлайн, чтобы не упустить возможности!"""))
    if webinar_dt - timedelta(hours=1) > now:
        reminders.append((webinar_dt - timedelta(hours=1), f"""Уже через час! 🔥

Вебинар, которого не было в Казахстане, стартует совсем скоро.
Ты узнаешь секреты спортивной фотосессии от профи 📸

После вебинара ты уже будешь знать:

✅ Как выйти на стабильную съёмку спортивных мероприятий
✅ Какие настройки использовать для крутых кадров
✅ И как сразу получать заказы без рекламы и продвижения

⚠ Записи вебинара не будет — подключайся вовремя и не упусти свой шанс!"""))
    if webinar_dt > now:
        # Debug print to check the webinar object and its link
        print(f"[DEBUG] Scheduling 'start' reminder for chat_id={chat_id}, webinar_id={webinar_id}, webinar={webinar}")
        link = webinar.get('link')
        if not link:
            link = "⚠️ Ссылка на вебинар не найдена. Пожалуйста, обратитесь к организатору."
        reminders.append((webinar_dt, f"""Мы начали! 🎬

Вебинар о спортивной фотосъёмке уже идёт!
Заходи скорее, чтобы не пропустить полезную информацию и живую демонстрацию.

Ты успеешь узнать:

✅ Как выйти на стабильную съёмку спортивных мероприятий
✅ Какие настройки использовать для крутых кадров
✅ И как сразу получать заказы без рекламы и продвижения

⚠ Записи не будет — подключайся прямо сейчас!
{link}"""))
    # If user registered less than 1 hour before, only send the relevant reminders
    # (i.e., if only the 'at start' reminder is in the future, only schedule that)
    for remind_time, msg in reminders:
        scheduler.add_job(send_reminder, 'date', run_date=remind_time, args=[chat_id, msg])
        print(f"Scheduled reminder for {chat_id} at {remind_time.isoformat()} : {msg}")

def schedule_all_reminders():
    registrations = fetch_registrations()
    webinars_by_id = get_webinars_by_id()
    for reg in registrations:
        schedule_reminders_for_registration(reg, webinars_by_id)

# Google Drive sync functions
def get_drive_service():
    creds = get_service_account_credentials()
    return build('drive', 'v3', credentials=creds)

def find_file_metadata(service, folder_id, file_name):
    query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get('files', [])
    if not files:
        raise FileNotFoundError(f"File '{file_name}' not found in folder '{folder_id}'")
    return files[0]  # returns dict with id, name, mimeType

def download_excel_file(service, file_id, mime_type, local_path):
    if mime_type == 'application/vnd.google-apps.spreadsheet':
        # Export Google Sheet as Excel
        request = service.files().export_media(fileId=file_id, mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    else:
        # Download native Excel file
        request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(local_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.close()

def update_excel_sheet(local_path, registrations):
    # Load Excel file
    with pd.ExcelWriter(local_path, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
        df = pd.DataFrame(registrations)
        df.to_excel(writer, sheet_name='Sheet1', index=False)
    # openpyxl preserves other sheets/styles

def upload_excel_file(service, file_id, local_path, mime_type):
    if mime_type == 'application/vnd.google-apps.spreadsheet':
        # Re-upload as Google Sheet (convert Excel to Google Sheet)
        media = MediaFileUpload(local_path, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        updated = service.files().update(
            fileId=file_id,
            media_body=media,
            body={'mimeType': 'application/vnd.google-apps.spreadsheet'}
        ).execute()
    else:
        # Replace Excel file
        media = MediaFileUpload(local_path, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        updated = service.files().update(fileId=file_id, media_body=media).execute()
    return updated

def sync_registrations_to_drive():
    """Sync registrations to Google Drive Excel file"""
    try:
        # 1. Fetch registrations from Supabase
        registrations = fetch_registrations()
        # 2. Authenticate and find file in Drive
        service = get_drive_service()
        folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        file_metadata = find_file_metadata(service, folder_id, EXCEL_FILE_NAME)
        file_id = file_metadata['id']
        mime_type = file_metadata['mimeType']
        # 3. Download the file (export if Google Sheet)
        local_path = EXCEL_FILE_NAME
        download_excel_file(service, file_id, mime_type, local_path)
        # 4. Update Sheet1
        update_excel_sheet(local_path, registrations)
        # 5. Upload back to Drive (replace original, convert if needed)
        upload_excel_file(service, file_id, local_path, mime_type)
        print(f"✅ Successfully synced registrations to '{EXCEL_FILE_NAME}' in Google Drive.")
    except Exception as e:
        print(f"❌ Error syncing to Google Drive: {e}")

# Schedule all reminders on startup
schedule_all_reminders()

# Schedule Google Drive sync every SYNC_INTERVAL_MINUTES
scheduler.add_job(sync_registrations_to_drive, 'interval', minutes=SYNC_INTERVAL_MINUTES)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup()
    register_btn = types.InlineKeyboardButton('Зарегистрироваться', callback_data='register')
    markup.add(register_btn)
    bot.send_message(message.chat.id, "Добро пожаловать в бот для вебинаров!", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'register')
def handle_register(call):
    markup = types.InlineKeyboardMarkup()
    try:
        dates = get_webinar_dates()
        for date in dates:
            # Format date string for button in Russian
            try:
                # Try to parse as ISO format
                from datetime import datetime
                dt = datetime.fromisoformat(date['date'])
                # Russian month names
                months = {
                    1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
                    5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
                    9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
                }
                date_str = f"{dt.day} {months[dt.month]} {dt.strftime('%H:%M')}"
            except Exception:
                date_str = str(date['date'])
            btn = types.InlineKeyboardButton(
                text=date_str,
                callback_data=f"date_{date['id']}"
            )
            markup.add(btn)
        if not dates:
            bot.send_message(call.message.chat.id, "В данный момент нет доступных вебинаров.")
            return
        bot.send_message(call.message.chat.id, "Пожалуйста, выберите дату вебинара:", reply_markup=markup)
    except Exception as e:
        bot.send_message(call.message.chat.id, f"Ошибка при получении дат вебинаров: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('date_'))
def handle_date_selection(call):
    chat_id = call.message.chat.id
    date_id = call.data.replace('date_', '')
    # Fetch all dates to find the selected one
    try:
        dates = get_webinar_dates()
        selected = next((d for d in dates if str(d['id']) == date_id), None)
        if not selected:
            bot.send_message(chat_id, "Выбранный вебинар не найден. Пожалуйста, попробуйте снова.")
            return
        user_data[chat_id] = {'date': selected['date'], 'date_id': selected['id'], 'link': selected.get('link')}
        bot.send_message(chat_id, "Напишите свое имя")
        bot.register_next_step_handler_by_chat_id(chat_id, process_full_name)
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка при обработке вашего выбора: {e}")

def process_full_name(message):
    chat_id = message.chat.id
    user_data[chat_id]['full_name'] = message.text
    bot.send_message(chat_id, "Пожалуйста, напишите свою электронную почту")
    bot.register_next_step_handler_by_chat_id(chat_id, process_email)

def process_email(message):
    chat_id = message.chat.id
    user_data[chat_id]['email'] = message.text
    bot.send_message(chat_id, "Пожалуйста, напишите свой номер телефона")
    bot.register_next_step_handler_by_chat_id(chat_id, process_phone)

def process_phone(message):
    chat_id = message.chat.id
    user_data[chat_id]['phone'] = message.text
    # Save to Supabase
    success = save_registration_to_supabase(user_data[chat_id], chat_id, message.from_user.username)
    if success:
        bot.send_message(chat_id, """Ты в списке! ✅ 

Мы напомним тебе о вебинаре и пришлём ссылку ближе к старту. 

А пока держи вдохновляющую историю 😉""")
        bot.send_message(chat_id, """Добро пожаловать на наш вебинар! Рады видеть вас здесь 💛

Мы — команда WOWMOTION 📸
Уже много лет с любовью и профессионализмом снимаем спорт в движении — танцы, гимнастику, бокс и другие яркие направления.

Что мы делаем?
— Улавливаем красоту, силу и эмоции в каждом движении
— Создаём эстетичный, динамичный и живой визуальный контент
— Помогаем спортсменам, тренерам и организаторам сохранять ценные моменты на долгие годы

Наша задача — не просто «снять кадр», а передать атмосферу, характер и энергию того, что происходит на площадке.

Где нас найти:
— [@wowdance.kz](https://www.instagram.com/wowdance.kz/) — для любителей танца
— [@wowrgym.kz](https://www.instagram.com/wowrgym.kz/) — гимнастика во всей её грации
🥊 Направление по боксу — совсем скоро в новом профиле!

Благодарим вас за доверие и интерес к нашему делу. Пусть этот вебинар станет для вас источником вдохновения и новых знаний. А мы, в свою очередь, готовы делиться всем, что знаем и умеем 💫

📲 Insta: [@wowmotion_photo_video](https://www.instagram.com/wowmotion_photo_video/)""",parse_mode='Markdown')
        # Optionally, send the webinar link if available
        link = user_data[chat_id].get('link')
        if link:
            # Format the selected date in Russian
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(user_data[chat_id]['date'])
                # Russian month names
                months = {
                    1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
                    5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
                    9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
                }
                formatted_date = f"{dt.day} {months[dt.month]} {dt.strftime('%H:%M')}"
            except Exception:
                formatted_date = user_data[chat_id]['date']
            
            bot.send_message(chat_id, f"""🎥 Вебинар "Секреты спортивной съёмки"
📅 Дата: {formatted_date}
📍 Формат: онлайн
👤 Организатор: [@wowmotion_photo_video](https://www.instagram.com/wowmotion_photo_video/)

🔓 Что вас ждёт:
— Как красиво снимать спорт в движении
— Настройки камеры для разных условий
— Подготовка к турниру: техника, команда, настроение
— Как передать силу, эмоции и динамику кадра
— Ошибки новичков и как их избежать
— Советы по обработке спортивных фото
— Как зарабатывать на спортивной съёмке

🎁 В конце вебинара — подарок и сертификат участника
{link}""",parse_mode='Markdown')
        # Schedule reminders for this registration
        webinars_by_id = get_webinars_by_id()
        reg = {
            'telegram_id': chat_id,
            'webinar_id': user_data[chat_id]['date_id']
        }
        schedule_reminders_for_registration(reg, webinars_by_id)
    else:
        bot.send_message(chat_id, "⚠️ Что-то пошло не так. Пожалуйста, попробуйте снова позже.")

# TESTING: Command to manually schedule reminders for all registrations (for testing with new webinar dates)
@bot.message_handler(commands=['test_reminders'])
def test_reminders(message):
    schedule_all_reminders()
    bot.send_message(message.chat.id, "Test: All reminders have been (re)scheduled based on current data.")

# TESTING: Command to manually trigger Google Drive sync
@bot.message_handler(commands=['test_sync'])
def test_sync(message):
    bot.send_message(message.chat.id, "🔄 Starting manual Google Drive sync...")
    sync_registrations_to_drive()
    bot.send_message(message.chat.id, "✅ Manual sync completed!")

if __name__ == "__main__":
    print("Bot is polling...")
    print(f"Google Drive sync scheduled every {SYNC_INTERVAL_MINUTES} minutes")
    bot.polling(none_stop=True) 