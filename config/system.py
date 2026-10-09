# config/system.py

# Параметры RDS по умолчанию (для argparse)
DEFAULT_DWELL = 35               # сек на станцию для RDS
DEFAULT_GAINS = "10,25,50"     # усиления для сканирования
DEFAULT_RDS_GAIN = 50            # усиление RDS по умолчанию
DEFAULT_RDS_GAINS = "-1,50,25"
DEFAULT_SNAP = 100               # кГц, шаг привязки частот
DEFAULT_RDS_RATE = 228000          # Hz
DEFAULT_PPM = 0
DEFAULT_SNAP_KHZ = 100             # кГц

# Временные параметры
CALIBRATION_DURATION = 2.0         # сек
NARROW_RANGE_MHZ = 2.0             # МГц

# Пороговые значения (логика работы)
STEREO_THRESHOLD_DB = 10.0
STEREO_SNR_THRESHOLD = 10.0
#DEFAULT_THRESHOLD = -25.0          # dB
DEFAULT_THRESHOLD = None          # dB

# Защитные лимиты
MAX_DWELL_SEC = 120                # макс. время ожидания rtl_power
MIN_COVERAGE_PERCENT = 70          # мин. процент покрытия диапазона

# Границы FM-диапазона
FM_MIN = 87.5
FM_MAX = 108.0
FQ_MIN = 87.5
FQ_MAX = 108.0


# Эталонные частоты для калибровки PPM (в МГц)
PPM_REFERENCES = {
    # FM: калибровка по пилот-тону 19 кГц (используется существующая функция)
    "WFM": {"method": "pilot", "pilot_freq": 19000.0},

    # OIRT: калибровка по пилот-тону 31.25 кГц
    "OIRT": {"method": "pilot", "pilot_freq": 31250.0},

    # AM/HF: калибровка по несущей известных станций
    "AM": {
        "method": "carrier",
        "references": [
            {"freq": 4.996, "name": "RWM",  "desc": "Москва, время/частота"},
            {"freq": 9.996, "name": "RWM",  "desc": "Москва, время/частота"},
            {"freq": 14.996, "name": "RWM", "desc": "Москва, время/частота"},
            {"freq": 10.0,   "name": "WWV",  "desc": "США, время/частота"},
            {"freq": 15.0,   "name": "WWV",  "desc": "США, время/частота"},
        ]
    },

    # Air-band: калибровка по несущей
    "AM_AIR": {
        "method": "carrier",
        "references": [
            {"freq": 121.5, "name": "Emergency", "desc": "Аварийная частота"},
        ]
    },
}