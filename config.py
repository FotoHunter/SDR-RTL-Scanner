# config.py

# --- Юзерские переменные (можно менять под задачу) ---
# ANSI-цвета для консоли
C_GREEN  = '\033[92m'
C_RESET  = '\033[0m'
C_RED    = '\033[91m'
C_BLUE   = '\033[94m'
C_YELLOW = '\033[93m'
C_RESET  = '\033[0m'
#

# Формат: "min-max": {"default_gain": X, "auto_threshold_factor": 0.3}
# auto_threshold_factor: доля динамического порога (0.0–1.0), если нужно менять логику для разных диапазонов
DEFAULT_THRESHOLD_DB = -90  # дБ, порог отсечения слабых сигналов (пользовательский)
# Список усилений для мульти-скана (порядок важен: сначала оптимальные)
USER_GAINS = [25, 15, 0, 50] 

# --- Конфигурация антенн (пример) ---
ANTENNAS_CONFIG = {
    "87.5-108.0": {"default_gain": 25, "auto_threshold_factor": 0.3},
    "65.9-74.0": {"default_gain": 20, "auto_threshold_factor": 0.3}
}


# --- Глобальные флаги и настройки по умолчанию ---
DEBUG_MODE = False
LOG_FILE = "scanner.log"

# --- Константы для обработки данных ---
STEREO_THRESHOLD_DB = 10.0

# Максимальное время ожидания rtl_power (защита от зависаний)
MAX_DWELL_SEC = 120

# Минимальный процент покрытия диапазона (если меньше — предупреждение)
MIN_COVERAGE_PERCENT = 70

# Флаги управления логикой
USE_AUTO_THRESHOLD = True   # вкл/выкл авто-расчёт порога (если False — только DEFAULT_THRESHOLD_DB)

# --- Метаданные и пути (адаптировать под среду) ---
UPDATE_FILE_PATH = "updates/latest.json"
LOG_FILE_PATH = "logs/scanner.log"

# --- Дефолтовые константы (не трогать без необходимости) ---
# --- Границы диапазона частот ---
DEFAULT_START = 87.5    # начало диапазона (по умолчанию FM)
DEFAULT_STOP  = 108.0   # конец диапазона (по умолчанию FM)
DEFAULT_STEP  = 100     # шаг сканирования kHz (по умолчанию 100кГц)

# Частота дискретизации для RDS/аудио (должна совпадать с rtl_fm и redsea)
DEFAULT_RDS_RATE = 228000   # Hz
# --- Параметры сканирования ---
NARROW_RANGE_MHZ = 2.0  # Порог узкого диапазона

# Параметры rtl_power по умолчанию
DEFAULT_GAIN_DB = 25       # дБ, усиление по умолчанию
DEFAULT_PPM = 0            # ppm, коррекция частоты по умолчанию
DEFAULT_SNAP_KHZ = 100     # кГц, шаг «привязки» частот (0 = без привязки)

# Временные параметры
DEFAULT_DWELL_SEC = 10     # сек, длительность захвата для декодирования RDS
CALIBRATION_DURATION = 2.0 # сек, длительность захвата для калибровки PPM

# Пороги и фильтры
STEREO_SNR_THRESHOLD = 10.0 # дБ, порог SNR для определения стерео


# ============================
#
DEFAULT_THRESHOLD = -25.0   # dB (auto-adjusted if too low)
DEFAULT_DWELL    = 20       # seconds per station for RDS
DEFAULT_GAINS    = "0,10,25,50"
DEFAULT_RDS_GAIN = 50
DEFAULT_RDS_GAINS = "-1,50,25,10,0"
DEFAULT_SNAP     = 100      # kHz
#FM_MIN, FM_MAX   = 87.5, 108.0
#NARROW_RANGE_MHZ = 5.0

