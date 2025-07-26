import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from supabase_utils import fetch_registrations, get_webinar_dates
from telebot import TeleBot

# Load environment variables
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
bot = TeleBot(TOKEN)

# Helper to fetch webinars as a dict by id
def get_webinars_by_id():
    webinars = get_webinar_dates()
    return {str(w['id']): w for w in webinars}

# Helper to send a message by username
def send_reminder(telegram_username, message):
    if not telegram_username:
        return
    if not telegram_username.startswith('@'):
        telegram_username = '@' + telegram_username
    try:
        bot.send_message(telegram_username, message)
    except Exception as e:
        print(f"Failed to send message to {telegram_username}: {e}")

# Schedule reminders for all registrations
def schedule_all_reminders():
    registrations = fetch_registrations()
    webinars_by_id = get_webinars_by_id()
    now = datetime.now(timezone.utc)
    for reg in registrations:
        username = reg.get('telegram_username')
        webinar_id = str(reg.get('webinar_id'))
        webinar = webinars_by_id.get(webinar_id)
        if not webinar:
            continue
        # Parse webinar date as UTC-aware
        try:
            webinar_dt = datetime.fromisoformat(webinar['date'])
            if webinar_dt.tzinfo is None:
                webinar_dt = webinar_dt.replace(tzinfo=timezone.utc)
            else:
                webinar_dt = webinar_dt.astimezone(timezone.utc)
        except Exception as e:
            print(f"Could not parse date for webinar {webinar_id}: {e}")
            continue
        # Schedule times
        reminders = [
            (webinar_dt - timedelta(days=1), f"📅 Reminder: Your webinar is tomorrow at {webinar_dt.strftime('%H:%M')}!"),
            (webinar_dt - timedelta(hours=1), f"⏳ Just 1 hour left until your webinar!"),
            (webinar_dt, f"🚀 Your webinar is starting now! Join: {webinar.get('link', '')}")
        ]
        for remind_time, msg in reminders:
            if remind_time > now:
                scheduler.add_job(send_reminder, 'date', run_date=remind_time, args=[username, msg])
                print(f"Scheduled reminder for {username} at {remind_time.isoformat()} : {msg}")

# APScheduler setup
scheduler = BackgroundScheduler(timezone=timezone.utc)
scheduler.start()

# On import, schedule all reminders
schedule_all_reminders()

# If you want to keep the scheduler running in a standalone script:
if __name__ == "__main__":
    import time
    print("Reminder scheduler running. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("Exiting...")
        scheduler.shutdown() 