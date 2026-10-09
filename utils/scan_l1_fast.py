import simplesoapy
import numpy as np

# Инициализация устройства (точно так же, как в soapy_power)
sdr = simplesoapy.SoapyDevice("driver=soapyMiri,index=0")

# Ставим частоту L1 GPS и полосу
sdr.setFrequency("RF", "LO", 1575.42e6)
sdr.setSampleRate("RF", 5e6)          # 5 МГц полоса — хватит для оценки фона
sdr.setGain("RF", 20)                 # усиление 20 дБ — старт

# Читаем один блок данных
samples = sdr.readStream()

# Считаем спектр (PSD)
psd = np.abs(np.fft.fft(samples))**2

# Делим на центр (полоса L1) и края (фон)
center_idx = len(psd) // 2
window = 256                          # окно по 256 точек в каждую сторону
center_avg = np.mean(psd[center_idx - window:center_idx + window])
edge_avg  = (np.mean(psd[:window]) + np.mean(psd[-window:])) / 2.0

# Разница в дБ
diff_db = 20 * np.log10(center_avg / edge_avg) if edge_avg > 0 else 0.0

print(f"center_avg={center_avg:.2e}, edge_avg={edge_avg:.2e}")
print(f"diff_dB={diff_db:.1f} dB")
