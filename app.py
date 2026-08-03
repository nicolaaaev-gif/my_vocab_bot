import os
import subprocess
import sys
from flask import Flask

# Создаем минимальное Flask-приложение, чтобы Render видел порт
app = Flask(__name__)

@app.route('/')
def home():
    return "Бот работает!"

@app.route('/health')
def health():
    return "OK", 200

if __name__ == '__main__':
    # Запускаем основного бота в отдельном процессе
    # Это позволяет Flask и боту работать параллельно
    subprocess.Popen([sys.executable, "bot.py"])
    
    # Запускаем Flask на порту, который даёт Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)