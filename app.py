from flask import Flask, render_template, request, redirect, jsonify
import urllib.parse

app = Flask(__name__)

# Официальный номер для бронирования
WHATSAPP_NUMBER = "996700656524"

# Список для хранения занятых мест (дата и время)
booked_slots = []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_booked', methods=['GET'])
def get_booked():
    return jsonify(booked_slots)

@app.route('/book', methods=['POST'])
def book():
    name = request.form.get('name')
    phone = request.form.get('phone')
    service = request.form.get('service')
    date = request.form.get('date')
    time = request.form.get('time')
    
    # Проверяем, не занято ли уже это время
    slot = {"date": date, "time": time}
    if slot not in booked_slots:
        booked_slots.append(slot)
    
    # Формируем сообщение для WhatsApp
    whatsapp_text = (
        f"👑 *ОНЛАЙН-ЗАПИСЬ В SULTANOV BARBER* 👑\n\n"
        f"👤 *Имя:* {name}\n"
        f"📞 *Телефон:* {phone}\n"
        f"✂️ *Услуга:* {service}\n"
        f"📅 *Дата:* {date}\n"
        f"⏰ *Время:* {time}\n\n"
        f"Жду подтверждения записи!"
    )
    
    encoded_text = urllib.parse.quote(whatsapp_text)
    whatsapp_url = f"https://wa.me/{WHATSAPP_NUMBER}?text={encoded_text}"
    
    return redirect(whatsapp_url)

if __name__ == '__main__':
    app.run(debug=False)
    