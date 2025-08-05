import os
from dotenv import load_dotenv
import telebot
from telebot import types
from supabase_utils import save_registration_to_supabase, get_webinar_dates, fetch_registrations, get_service_account_credentials, save_course_registration_to_supabase, update_course_payment_status, get_course_registration_by_id, get_latest_course_registration_by_telegram_id, fetch_course_registrations
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
import io
import requests
import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
import re

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Easily editable sync interval (in minutes)
SYNC_INTERVAL_MINUTES = 30
EXCEL_FILE_NAME = 'WebinarRegistrations.xlsx'

# Circle video file_id (will be set after upload)
CIRCLE_VIDEO_FILE_ID = os.getenv('CIRCLE_VIDEO_FILE_ID', '')
CIRCLE_VIDEO_FILE_ID2 = os.getenv('CIRCLE_VIDEO_FILE_ID2', '')

bot = telebot.TeleBot(TOKEN)

# Store user registration data temporarily
user_data = {}

# Input validation functions
def validate_phone_number(phone):
    """
    Validate Kazakhstan phone number format.
    Accepts: +7 707 123 45 67, 87071234567, 8 (707) 123-45-67, +77071234567
    """
    # Remove all non-digit characters except +
    cleaned = re.sub(r'[^\d+]', '', phone)
    
    # Check for valid Kazakhstan mobile number patterns
    patterns = [
        r'^\+77\d{9}$',  # +77071234567
        r'^87\d{9}$',    # 87071234567
    ]
    
    for pattern in patterns:
        if re.match(pattern, cleaned):
            return True
    
    return False

def validate_email(email):
    """
    Validate email format using regex.
    """
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def format_phone_number(phone):
    """
    Format phone number to standard Kazakhstan format: +7 7XX XXX XX XX
    """
    # Remove all non-digit characters
    cleaned = re.sub(r'[^\d]', '', phone)
    
    # If it starts with 8, replace with +7
    if cleaned.startswith('8'):
        cleaned = '7' + cleaned[1:]
    
    # If it doesn't start with 7, add +7
    if not cleaned.startswith('7'):
        cleaned = '7' + cleaned
    
    # Format as +7 7XX XXX XX XX
    if len(cleaned) == 11 and cleaned.startswith('7'):
        return f"+7 {cleaned[1:4]} {cleaned[4:7]} {cleaned[7:9]} {cleaned[9:11]}"
    
    return phone  # Return original if can't format

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
    # Only use numeric chat_ids
    try:
        chat_id_int = int(chat_id)
    except (TypeError, ValueError):
        print(f"[WARNING] Skipping reminder: chat_id is not numeric: {chat_id}")
        return
    webinar_id = str(reg.get('webinar_id'))
    webinar = webinars_by_id.get(webinar_id)
    if not webinar or not chat_id_int:
        return
    # Parse webinar date as UTC-aware
    try:
        from dateutil import parser
        import pytz
        #dt = parser.isoparse(webinar['date'])
        #webinar_dt = dt.astimezone(timezone.utc)
        local_tz = pytz.timezone('Asia/Almaty')  # Replace with your desired timezone
        dt = parser.isoparse(webinar['date'])
        # Localize if it's a naive datetime (no tzinfo)
        if dt.tzinfo is None:
            dt = local_tz.localize(dt)
            
        # Convert to UTC for proper scheduling
        webinar_dt = dt.astimezone(timezone.utc)
        print(f"[DEBUG] Webinar local time (Asia/Almaty): {dt}")
        print(f"[DEBUG] Converted UTC time for scheduling: {webinar_dt}")
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
        scheduler.add_job(send_reminder, 'date', run_date=remind_time, args=[chat_id_int, msg])
        print(f"Scheduled reminder for {chat_id_int} at {remind_time.isoformat()} : {msg}")

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

def sync_course_registrations_to_drive():
    """Sync course registrations to Google Drive Excel file"""
    try:
        # 1. Fetch course registrations from Supabase
        course_registrations = fetch_course_registrations()
        # 2. Authenticate and find file in Drive
        service = get_drive_service()
        folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        
        try:
            file_metadata = find_file_metadata(service, folder_id, 'CoursesRegistrations.xlsx')
            file_id = file_metadata['id']
            mime_type = file_metadata['mimeType']
            # 3. Download the file (export if Google Sheet)
            local_path = 'CoursesRegistrations.xlsx'
            download_excel_file(service, file_id, mime_type, local_path)
        except FileNotFoundError:
            print("📝 Creating new CoursesRegistrations.xlsx file in Google Drive...")
            # Create a new Excel file with course registrations data
            df = pd.DataFrame(course_registrations)
            df.to_excel('CoursesRegistrations.xlsx', index=False)
            
            # Upload the new file to Google Drive
            file_metadata = {
                'name': 'CoursesRegistrations.xlsx',
                'parents': [folder_id]
            }
            media = MediaFileUpload('CoursesRegistrations.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            file_id = file.get('id')
            print(f"✅ Created new file with ID: {file_id}")
            return
        
        # 4. Update Sheet1
        update_excel_sheet(local_path, course_registrations)
        # 5. Upload back to Drive (replace original, convert if needed)
        upload_excel_file(service, file_id, local_path, mime_type)
        print(f"✅ Successfully synced course registrations to 'CoursesRegistrations.xlsx' in Google Drive.")
    except Exception as e:
        print(f"❌ Error syncing course registrations to Google Drive: {e}")

def sync_registrations_to_drive():
    """Sync webinar registrations to Google Drive Excel file"""
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

def sync_all_to_drive():
    """Sync both webinar and course registrations to Google Drive"""
    print("🔄 Starting sync of all registrations to Google Drive...")
    sync_registrations_to_drive()
    sync_course_registrations_to_drive()
    print("✅ All sync operations completed.")

# Schedule all reminders on startup
try:
    schedule_all_reminders()
    print("✅ Successfully scheduled all reminders on startup")
except Exception as e:
    print(f"⚠️ Warning: Could not schedule reminders on startup: {e}")
    print("Bot will continue running, but reminders may not be scheduled until next restart")

# Schedule Google Drive sync every SYNC_INTERVAL_MINUTES
scheduler.add_job(sync_all_to_drive, 'interval', minutes=SYNC_INTERVAL_MINUTES)

@bot.message_handler(commands=['upload_circle'])
def upload_circle_video(message):
    """Admin command to upload circle video and get file_id"""
    # Check if user is admin (you can customize this check)
    admin_chat_id = os.getenv('ADMIN_CHAT_ID')
    if not admin_chat_id or str(message.chat.id) != admin_chat_id:
        bot.reply_to(message, "❌ Эта команда доступна только администратору.")
        return
    
    try:
        # Send the video from local file
        with open('media/intro_circle.mp4', 'rb') as video_file:
            sent_video = bot.send_video_note(message.chat.id, video_file)
            
        # Get and display the file_id
        file_id = sent_video.video_note.file_id
        bot.reply_to(message, f"✅ Круговое видео загружено!\n\n📋 File ID для .env:\nCIRCLE_VIDEO_FILE_ID={file_id}\n\n💡 Скопируйте этот ID в переменную окружения CIRCLE_VIDEO_FILE_ID")
        
    except FileNotFoundError:
        bot.reply_to(message, "❌ Файл media/intro_circle.mp4 не найден. Убедитесь, что файл существует в папке media/")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка при загрузке видео: {e}")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    # Send circle video if file_id is available
    
    
    markup = types.InlineKeyboardMarkup()
    webinar_btn = types.InlineKeyboardButton('📅 Вебинар', callback_data='webinar_main')
    course_btn = types.InlineKeyboardButton('📸 Обучающий курс', callback_data='course_main')
    markup.add(webinar_btn, course_btn)
    
    welcome_text = """Привет! 👋  
Мы — команда Wowmotion. Здесь ты получишь всю информацию о вебинаре и обучающем курсе.

Выбери, что тебя интересует:"""
    
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'webinar_main')
def handle_webinar_main(call):

    if CIRCLE_VIDEO_FILE_ID2:
        try:
            bot.send_video_note(call.message.chat.id, CIRCLE_VIDEO_FILE_ID2)
        except Exception as e:
            print(f"Error sending circle video: {e}")
            # Continue with normal flow even if video fails
    
    # Small delay to let video load
    import time
    time.sleep(1)

    markup = types.InlineKeyboardMarkup()
    register_btn = types.InlineKeyboardButton('Зарегистрироваться', callback_data='register')
    markup.add(register_btn)
    bot.send_message(call.message.chat.id, "Добро пожаловать в бот для вебинаров!", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'course_main')
def handle_course_main(call):

    if CIRCLE_VIDEO_FILE_ID:
        try:
            bot.send_video_note(call.message.chat.id, CIRCLE_VIDEO_FILE_ID)
        except Exception as e:
            print(f"Error sending circle video: {e}")
            # Continue with normal flow even if video fails
    
    # Small delay to let video load
    import time
    time.sleep(1)

    markup = types.InlineKeyboardMarkup()
    how_btn = types.InlineKeyboardButton('📖 Как проходит обучение', callback_data='course_how')
    program_btn = types.InlineKeyboardButton('📚 Программа курса', callback_data='course_program')
    payment_btn = types.InlineKeyboardButton('💳 Стоимость и оплата', callback_data='course_payment')
    faq_btn = types.InlineKeyboardButton('❓ Вопрос–ответ', callback_data='course_faq')
    markup.add(how_btn, program_btn, payment_btn, faq_btn)
    
    course_text = """👨‍🏫 Это обучающий курс на 5 недель для тех, кто хочет освоить спортивную съёмку и начать зарабатывать.
Идеально для начинающих и тех, кто уже фотографирует, но хочет освоить новое направление."""
    
    bot.send_message(call.message.chat.id, course_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'course_how')
def handle_course_how(call):
    markup = types.InlineKeyboardMarkup()
    back_btn = types.InlineKeyboardButton('Назад', callback_data='course_main')
    markup.add(back_btn)
    
    how_text = """📆 Обучение длится 4 недели + 1 неделя практика  
🧠 Формат: видеоуроки + разборы + домашние задания  
📍 Всё проходит онлайн, с поддержкой куратора"""
    
    bot.send_message(call.message.chat.id, how_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'course_program')
def handle_course_program(call):
    markup = types.InlineKeyboardMarkup()
    back_btn = types.InlineKeyboardButton('Назад', callback_data='course_main')
    markup.add(back_btn)
    
    program_text = """📚 ПРОГРАММА КУРСА

🔹 Блок 1: Введение в спортивную фотографию

🎬 Понимание жанра и потенциала

— Что такое спортивная съёмка и в чём её уникальность
— Кто заказывает спортивные фото и где они нужны
— Примеры успешных работ и направлений
— Почему это востребовано и как начать даже без опыта

⸻

🔹 Блок 2: Основы фотографии

📸 Техническая база, без которой не обойтись

— Камера, объективы, аксессуары
— Выдержка, диафрагма, ISO, фокус
— Свет, композиция и цвет
— Как подготовиться к съёмке

⸻

🔹 Блок 3: Съёмка спорта на практике

🎯 Всё о том, как поймать момент и снять динамику

— Как снимать разные виды спорта (гимнастика, танцы, бокс и др.)
— Как выбрать точку съёмки и не мешать соревнованию
— Настройки камеры в сложных условиях
— Секреты «идеального кадра» в движении

⸻

🔹 Блок 4: Работа с клиентами и организация съёмок

🤝 Как стать востребованным фотографом

— Как общаться с клиентами: спортсмены, родители, тренеры
— Как выстраивать съёмочный процесс
— Как брать заказы и продавать фото
— Типичные ошибки и как их избежать

⸻

🔹 Блок 5: Практика, портфолио и рост

🚀 Старт твоей карьеры

— Практическая съёмка с куратором
— Анализ и обратная связь
— Как собрать портфолио
— Как развиваться в этом направлении и попасть в команду WOWMOTION
— Именной сертификат по завершению
⸻
"""
    
    bot.send_message(call.message.chat.id, program_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'course_payment')
def handle_course_payment(call):
    markup = types.InlineKeyboardMarkup()
    pay_btn = types.InlineKeyboardButton('🔐 Оплатить курс', callback_data='course_pay')
    back_btn = types.InlineKeyboardButton('Назад', callback_data='course_main')
    markup.add(pay_btn, back_btn)
    
    payment_text = """💰 Полная стоимость курса: 150,000₸  
🎁 Бонус: участие в закрытом чате, сертификат и поддержка после курса  
💵 Оплата на Kaspi / переводом  
📍 Место бронируется после оплаты

Есть вопросы? Напиши нам в Instagram или WhatsApp:
📸 @wowmotion_photo_video
📞 [номер WhatsApp]
Мы на связи и рады помочь!"""
    
    bot.send_message(call.message.chat.id, payment_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'course_pay')
def handle_course_pay(call):
    chat_id = call.message.chat.id
    user_data[chat_id] = {'type': 'course'}
    bot.send_message(chat_id, "Для регистрации на курс, пожалуйста, напишите ваше полное имя:")
    bot.register_next_step_handler_by_chat_id(chat_id, process_course_full_name)

@bot.callback_query_handler(func=lambda call: call.data == 'course_faq')
def handle_course_faq(call):
    markup = types.InlineKeyboardMarkup()
    back_btn = types.InlineKeyboardButton('Назад', callback_data='course_main')
    markup.add(back_btn)
    
    faq_text = """❓ ЧАСТО ЗАДАВАЕМЫЕ ВОПРОСЫ

🟢 Я новичок. Мне подойдёт курс?
— Да! Курс подходит для начинающих и тех, кто хочет новое направление.

🟢 У меня нет крутой камеры.
— Подойдёт любая камера — главное начать! Мы подскажем, как работать с тем, что у тебя есть.

🟢 Будет ли сертификат?
— Да, при прохождении всех занятий и практике — ты получаешь именной сертификат.

🟢 Я пропустил вебинар. Будет запись?
— Да, всем участникам вебинара отправим запись."""
    
    bot.send_message(call.message.chat.id, faq_text, reply_markup=markup)

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

def process_course_full_name(message):
    chat_id = message.chat.id
    user_data[chat_id]['full_name'] = message.text
    bot.send_message(chat_id, "Пожалуйста, напишите свой номер телефона:")
    bot.register_next_step_handler_by_chat_id(chat_id, process_course_phone)

def process_course_phone(message):
    chat_id = message.chat.id
    phone = message.text.strip()
    
    # Validate phone number
    if not validate_phone_number(phone):
        bot.send_message(chat_id, "🚫 Пожалуйста, введите корректный номер телефона (пример: +77011234567)")
        bot.register_next_step_handler_by_chat_id(chat_id, process_course_phone)
        return
    
    # Format phone number to standard format
    formatted_phone = format_phone_number(phone)
    user_data[chat_id]['phone'] = formatted_phone
    user_data[chat_id]['telegram_username'] = message.from_user.username
    
    # Save course registration to Supabase
    success = save_course_registration_to_supabase(user_data[chat_id], chat_id, message.from_user.username)
    
    if success:
        # Fetch the registration ID from the database
        registration = get_latest_course_registration_by_telegram_id(chat_id)
        
        if registration and registration.get('id'):
            registration_id = registration['id']
            user_data[chat_id]['registration_id'] = registration_id
            print(f"Retrieved registration ID from database: {registration_id}")
        else:
            print("Warning: Could not retrieve registration ID from database")
            user_data[chat_id]['registration_id'] = None
        
        bot.send_message(chat_id, "✅ Регистрация на курс прошла успешно!")
        
        # Send payment instructions
        payment_instructions = """💳 ИНСТРУКЦИИ ПО ОПЛАТЕ

💰 Стоимость курса: 150,000₸

📱 Оплата через Kaspi:
• Ссылка: https://pay.kaspi.kz/pay/s6llvgtb
• Получатель: [WowMotion]
• Назначение: Курс спортивной съёмки


📸 После оплаты, пожалуйста, отправьте фото чека для подтверждения."""
        
        bot.send_message(chat_id, payment_instructions)
        bot.send_message(chat_id, "📸 Отправьте фото чека об оплате:")
        bot.register_next_step_handler_by_chat_id(chat_id, process_payment_receipt)
    else:
        bot.send_message(chat_id, "⚠️ Что-то пошло не так. Пожалуйста, попробуйте снова позже.")

def process_payment_receipt(message):
    chat_id = message.chat.id
    if message.photo:
        # Get the largest photo size
        photo = message.photo[-1]
        file_id = photo.file_id
        
        try:
            # Download the photo
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            # Send confirmation to user
            bot.send_message(chat_id, """✅ Спасибо! Ваш чек получен. Мы проверим оплату и свяжемся с вами в течение 24 часов.
            Есть вопросы? Напиши нам в Instagram или WhatsApp:
            📸 @wowmotion_photo_video
            📞 [+7 (706) 651-22-93, +7 (705) 705-82-75]
            Мы на связи и рады помочь!""")
            
            # Notify admin about new course registration with photo
            admin_chat_id = os.getenv('ADMIN_CHAT_ID')  # Add this to your .env
            if admin_chat_id:
                try:
                    admin_chat_id_int = int(admin_chat_id)
                    registration_id = user_data[chat_id].get('registration_id')
                    
                    # Create inline keyboard with confirmation button (only if we have a real ID)
                    markup = None
                    if registration_id:
                        markup = types.InlineKeyboardMarkup()
                        confirm_btn = types.InlineKeyboardButton(
                            '✅ Подтвердить оплату', 
                            callback_data=f'confirm_{registration_id}'
                        )
                        markup.add(confirm_btn)
                    
                    # Send registration details
                    registration_text = f"""🎓 Новая регистрация на курс!

👤 Имя: {user_data[chat_id]['full_name']}
📱 Телефон: {user_data[chat_id]['phone']}
🆔 Username: @{user_data[chat_id]['telegram_username']}
📚 План: Спортивный Фотограф (5 недель)
💰 Статус: Ожидает подтверждения оплаты
📅 Дата регистрации: {datetime.now().strftime('%d.%m.%Y %H:%M')}"""
                    
                    if registration_id:
                        registration_text += f"\n🆔 ID регистрации: {registration_id}"
                    else:
                        registration_text += "\n⚠️ ID регистрации: Не удалось получить (требуется ручная проверка)"
                    
                    # Send the payment receipt photo with confirmation button
                    bot.send_photo(
                        admin_chat_id_int, 
                        downloaded_file, 
                        caption=registration_text,
                        reply_markup=markup
                    )
                    
                except Exception as e:
                    print(f"Error sending to admin: {e}")
            else:
                print("ADMIN_CHAT_ID not set in environment variables")
                
        except Exception as e:
            print(f"Error processing payment receipt: {e}")
            bot.send_message(chat_id, "⚠️ Ошибка при обработке чека. Пожалуйста, попробуйте снова.")
    else:
        bot.send_message(chat_id, "Пожалуйста, отправьте фото чека об оплате.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_'))
def handle_payment_confirmation(call):
    """Handle payment confirmation from admin"""
    try:
        # Extract registration ID from callback data
        registration_id = call.data.replace('confirm_', '')
        
        # Update payment status in Supabase
        success = update_course_payment_status(registration_id)
        
        if success:
            # Get registration details to notify the user
            registration = get_course_registration_by_id(registration_id)
            
            # Notify admin
            bot.answer_callback_query(call.id, "✅ Платёж подтверждён и записан в базу данных.")
            
            # Update the message to show it's confirmed
            bot.edit_message_caption(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                caption=call.message.caption + "\n\n✅ ПЛАТЁЖ ПОДТВЕРЖДЁН",
                reply_markup=None  # Remove the button
            )
            
            # Notify the original user
            if registration and registration.get('telegram_id'):
                try:
                    user_chat_id = int(registration['telegram_id'])
                    bot.send_message(
                        user_chat_id, 
                        "🎉 Ваша оплата подтверждена! Спасибо за регистрацию. Мы свяжемся с вами в ближайшее время."
                    )
                except Exception as e:
                    print(f"Error notifying user: {e}")
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка при подтверждении платежа.")
            
    except Exception as e:
        print(f"Error in payment confirmation: {e}")
        bot.answer_callback_query(call.id, "❌ Произошла ошибка.")

def process_full_name(message):
    chat_id = message.chat.id
    user_data[chat_id]['full_name'] = message.text
    bot.send_message(chat_id, "Пожалуйста, напишите свою электронную почту")
    bot.register_next_step_handler_by_chat_id(chat_id, process_email)

def process_email(message):
    chat_id = message.chat.id
    email = message.text.strip()
    
    # Validate email
    if not validate_email(email):
        bot.send_message(chat_id, "❗ Пожалуйста, введите корректный email.")
        bot.register_next_step_handler_by_chat_id(chat_id, process_email)
        return
    
    user_data[chat_id]['email'] = email
    bot.send_message(chat_id, "Пожалуйста, напишите свой номер телефона")
    bot.register_next_step_handler_by_chat_id(chat_id, process_phone)

def process_phone(message):
    chat_id = message.chat.id
    phone = message.text.strip()
    
    # Validate phone number
    if not validate_phone_number(phone):
        bot.send_message(chat_id, "❗ Пожалуйста, введите корректный номер телефона в формате +7 7XX XXX XX XX.")
        bot.register_next_step_handler_by_chat_id(chat_id, process_phone)
        return
    
    # Format phone number to standard format
    formatted_phone = format_phone_number(phone)
    user_data[chat_id]['phone'] = formatted_phone
    
    # Save to Supabase
    success = save_registration_to_supabase(user_data[chat_id], chat_id, message.from_user.username)
    if success:
        bot.send_message(chat_id, "✅ Регистрация прошла успешно! Вы получите напоминания перед вебинаром.")
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
👤 Организатор: @wowmotion_photo_video

🔓 Что вас ждёт:
— Как красиво снимать спорт в движении
— Настройки камеры для разных условий
— Подготовка к турниру: техника, команда, настроение
— Как передать силу, эмоции и динамику кадра
— Ошибки новичков и как их избежать
— Советы по обработке спортивных фото
— Как зарабатывать на спортивной съёмке

📢 Подпишитесь на наш Telegram-канал, чтобы не пропустить анонсы, материалы и запись вебинара: https://t.me/wowdancechannel

🎁 В конце вебинара — подарок и сертификат участника
{link}""")
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
    sync_all_to_drive()
    bot.send_message(message.chat.id, "✅ Manual sync completed!")

# TESTING: Command to manually trigger course registrations sync only
@bot.message_handler(commands=['test_course_sync'])
def test_course_sync(message):
    bot.send_message(message.chat.id, "🔄 Starting manual course registrations sync...")
    sync_course_registrations_to_drive()
    bot.send_message(message.chat.id, "✅ Course registrations sync completed!")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    chat_id = message.chat.id
    if chat_id in user_data and user_data[chat_id].get('type') == 'course':
        # This is a payment receipt for course registration
        process_payment_receipt(message)
    else:
        bot.send_message(chat_id, "Пожалуйста, используйте команду /start для начала работы с ботом.")

if __name__ == "__main__":
    print("Bot is polling...")
    print(f"Google Drive sync scheduled every {SYNC_INTERVAL_MINUTES} minutes")
    bot.polling(none_stop=True) 