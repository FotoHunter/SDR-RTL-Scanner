# config/user.py

# Диапазоны по умолчанию (FM)
DEFAULT_START = 87.5
DEFAULT_STOP = 108.0
DEFAULT_STEP = 100                 # шаг сканирования, кГц

# Усиления для мульти-скана (порядок важен)
USER_GAINS = [25, 15, 0, 50]

# Флаги управления логикой
DEBUG_MODE = False
USE_AUTO_THRESHOLD = True          # вкл/выкл авто-расчёт порога

# Пути (можно адаптировать под среду)
LOG_FILE_PATH = "logs/scanner.log"
UPDATE_FILE_PATH = "updates/latest.json"

# Пороги (пользовательский)
DEFAULT_THRESHOLD_DB = -90         # дБ, порог отсечения слабых сигналов

