import SoapySDR
import numpy as np

args = "driver=soapyMiri,index=0"
sdr = SoapySDR.Device(args)

# Частота L1 GPS
sdr.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, 1575.42e6)

# Полоса 5 МГц
sdr.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, 5e6)

# Ручное усиление: LNA и Baseband (вместо Automatic)
sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, "LNA", 20)
sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, "Baseband", 20)
print("LNA=20 dB, Baseband=20 dB")

# Настройка потока
stream = sdr.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32, [0])
sdr.activateStream(stream)

# Буфер побольше: 2^20 = 1M выборок (это уже нормальный спектр)
buff = np.empty(2**20, dtype=np.complex64)

# Читаем
sr = sdr.readStream(stream, [buff], len(buff))
print(f"Read {sr.ret} samples")

if sr.ret <= 0:
    sdr.deactivateStream(stream)
    sdr.closeStream(stream)
    raise RuntimeError(f"Не удалось прочитать данные (ret={sr.ret})")

sdr.deactivateStream(stream)
sdr.closeStream(stream)

# Отбрасываем первые 10% как «холостые» (переходные процессы)
start = int(sr.ret * 0.1)
data = buff[start:sr.ret]

# Спектр (PSD)
psd = np.abs(np.fft.fft(data))**2

# Центр и края
center_idx = len(psd) // 2
window = 512  # чуть шире окно, чтобы усреднить шум
center_avg = np.mean(psd[center_idx - window:center_idx + window])
edge_avg  = (np.mean(psd[:window]) + np.mean(psd[-window:])) / 2.0

diff_db = 20 * np.log10(center_avg / edge_avg) if edge_avg > 0 else 0.0

print(f"center_avg={center_avg:.2e}, edge_avg={edge_avg:.2e}")
print(f"diff_dB={diff_db:.1f} dB")
