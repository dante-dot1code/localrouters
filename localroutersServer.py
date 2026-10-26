"""
Backend для лендинга "Городские тропы"
Собирает лиды из формы и статистику демо-режима.
Запуск: python server.py
"""

import os
import csv
import json
import sqlite3
import logging
from datetime import datetime
from functools import wraps

from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS

# ====== НАСТРОЙКИ ======
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "my-secret-token-change-me")
DATABASE = "data.db"
HOST = "0.0.0.0"
PORT = 5000

# ====== ИНИЦИАЛИЗАЦИЯ ======
app = Flask(__name__)
CORS(app, origins=[
    "https://localrouters.netlify.app",  # ваш Netlify-домен
    "http://localhost:5500"  # для локальной разработки
])
# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("server.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ====== БАЗА ДАННЫХ ======
def get_db():
    """Получить соединение с БД"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Создать таблицы при первом запуске"""
    conn = get_db()
    cursor = conn.cursor()

    # Таблица лидов (заявок с формы)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact TEXT NOT NULL,
            city TEXT NOT NULL,
            frequency TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip_address TEXT,
            user_agent TEXT
        )
    """)

    # Таблица статистики демо-режима
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS demo_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            duration TEXT NOT NULL,
            interest TEXT NOT NULL,
            budget TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip_address TEXT
        )
    """)

    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")


# ====== ПРОСТАЯ ЗАЩИТА АДМИНКИ ======
def require_admin(f):
    """Декоратор для защиты админских эндпоинтов"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.args.get("token") or request.headers.get("X-Admin-Token")
        if token != ADMIN_TOKEN:
            abort(403, description="Неверный токен администратора")
        return f(*args, **kwargs)
    return decorated


# ====== ПРОСТАЯ ЗАЩИТА ОТ СПАМА ======
# Храним последние запросы в памяти (для продакшена лучше Redis)
recent_requests = {}

def is_rate_limited(ip, limit=5, window=60):
    """Не более `limit` запросов за `window` секунд с одного IP"""
    now = datetime.now().timestamp()
    if ip not in recent_requests:
        recent_requests[ip] = []
    # Удаляем старые записи
    recent_requests[ip] = [t for t in recent_requests[ip] if now - t < window]
    if len(recent_requests[ip]) >= limit:
        return True
    recent_requests[ip].append(now)
    return False


# ====== API: ПУБЛИЧНЫЕ ЭНДПОИНТЫ ======

@app.route("/api/leads", methods=["POST"])
def create_lead():
    """Приём заявки с формы"""
    ip = request.remote_addr

    # Защита от спама
    if is_rate_limited(ip, limit=3, window=60):
        logger.warning(f"🚫 Rate limit превышен для IP {ip}")
        return jsonify({"error": "Слишком много запросов. Попробуйте позже."}), 429

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Неверный формат данных"}), 400

    # Валидация
    name = (data.get("name") or "").strip()
    contact = (data.get("contact") or "").strip()
    city = (data.get("city") or "").strip()
    frequency = (data.get("frequency") or "").strip()

    if not name or len(name) < 2:
        return jsonify({"error": "Укажите корректное имя"}), 400
    if not contact or len(contact) < 5:
        return jsonify({"error": "Укажите корректный контакт"}), 400
    if not city:
        return jsonify({"error": "Выберите город"}), 400

    # Сохранение в БД
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO leads (name, contact, city, frequency, ip_address, user_agent)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, contact, city, frequency, ip, request.headers.get("User-Agent", ""))
        )
        conn.commit()
        lead_id = cursor.lastrowid
        conn.close()

        logger.info(f"✅ Новый лид #{lead_id}: {name} ({city})")
        return jsonify({"success": True, "id": lead_id, "name": name}), 201

    except Exception as e:
        logger.error(f"❌ Ошибка сохранения лида: {e}")
        return jsonify({"error": "Ошибка сервера"}), 500


@app.route("/api/demo-stats", methods=["POST"])
def save_demo_stats():
    """Приём статистики из демо-режима"""
    ip = request.remote_addr

    if is_rate_limited(ip, limit=10, window=60):
        return jsonify({"error": "Слишком много запросов"}), 429

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Неверный формат"}), 400

    city = (data.get("city") or "").strip()
    duration = (data.get("duration") or "").strip()
    interest = (data.get("interest") or "").strip()
    budget = (data.get("budget") or "").strip()

    if not all([city, duration, interest, budget]):
        return jsonify({"error": "Не все поля заполнены"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO demo_stats (city, duration, interest, budget, ip_address)
               VALUES (?, ?, ?, ?, ?)""",
            (city, duration, interest, budget, ip)
        )
        conn.commit()
        conn.close()

        logger.info(f"📊 Демо-статистика: {city} / {interest} / {budget}")
        return jsonify({"success": True}), 201

    except Exception as e:
        logger.error(f"❌ Ошибка сохранения демо-статистики: {e}")
        return jsonify({"error": "Ошибка сервера"}), 500


# ====== API: АДМИНСКИЕ ЭНДПОИНТЫ ======

@app.route("/api/admin/leads", methods=["GET"])
@require_admin
def get_leads():
    """Получить все лиды (JSON)"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM leads ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/api/admin/stats", methods=["GET"])
@require_admin
def get_stats():
    """Сводная статистика"""
    conn = get_db()
    cursor = conn.cursor()

    # Общее количество лидов
    cursor.execute("SELECT COUNT(*) FROM leads")
    total_leads = cursor.fetchone()[0]

    # Лиды по городам
    cursor.execute("SELECT city, COUNT(*) as count FROM leads GROUP BY city ORDER BY count DESC")
    leads_by_city = [dict(row) for row in cursor.fetchall()]

    # Демо-статистика по интересам
    cursor.execute("SELECT interest, COUNT(*) as count FROM demo_stats GROUP BY interest ORDER BY count DESC")
    demo_by_interest = [dict(row) for row in cursor.fetchall()]

    # Демо-статистика по бюджетам
    cursor.execute("SELECT budget, COUNT(*) as count FROM demo_stats GROUP BY budget ORDER BY count DESC")
    demo_by_budget = [dict(row) for row in cursor.fetchall()]

    # Демо-статистика по городам
    cursor.execute("SELECT city, COUNT(*) as count FROM demo_stats GROUP BY city ORDER BY count DESC")
    demo_by_city = [dict(row) for row in cursor.fetchall()]

    conn.close()

    return jsonify({
        "total_leads": total_leads,
        "leads_by_city": leads_by_city,
        "demo_by_interest": demo_by_interest,
        "demo_by_budget": demo_by_budget,
        "demo_by_city": demo_by_city,
    })


@app.route("/api/admin/leads/csv", methods=["GET"])
@require_admin
def export_leads_csv():
    """Экспорт лидов в CSV"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM leads ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return jsonify({"error": "Нет данных для экспорта"}), 404

    csv_path = "leads_export.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Имя", "Контакт", "Город", "Частота", "Дата", "IP"])
        for row in rows:
            writer.writerow([
                row["id"], row["name"], row["contact"],
                row["city"], row["frequency"], row["created_at"], row["ip_address"]
            ])

    return send_file(csv_path, as_attachment=True, download_name="leads.csv")


@app.route("/api/admin/stats/csv", methods=["GET"])
@require_admin
def export_stats_csv():
    """Экспорт демо-статистики в CSV"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM demo_stats ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return jsonify({"error": "Нет данных"}), 404

    csv_path = "demo_stats_export.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Город", "Время", "Интерес", "Бюджет", "Дата", "IP"])
        for row in rows:
            writer.writerow([
                row["id"], row["city"], row["duration"],
                row["interest"], row["budget"], row["created_at"], row["ip_address"]
            ])

    return send_file(csv_path, as_attachment=True, download_name="demo_stats.csv")


# ====== СТАРТ ======
if __name__ == "__main__":
    init_db()
    logger.info(f"🚀 Сервер запущен на http://localhost:{PORT}")
    logger.info(f"🔑 Админка доступна по: http://localhost:{PORT}/api/admin/leads?token={ADMIN_TOKEN}")
    app.run(host=HOST, port=PORT, debug=True)
