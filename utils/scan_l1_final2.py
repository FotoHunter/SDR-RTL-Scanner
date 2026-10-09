import SoapySDR
import numpy as np

args = "driver=soapyMiri,index=0"
sdr = SoapySDR.Device(args)

sdr.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, 1575.42e6)
sdr.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, 5e6)
sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, "LNA", 20)
sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, "Baseband", 20)
print("LNA=20, Baseband=20")

stream = sdr.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32, [0])
sdr.activateStream(stream)

# Параметры метода Уэлша
chunk_size = 8192          # размер одного FFT-блока
n_chunks = 100            # 100 блоков -> 100 усреднённых спектров
window = np.hamming(chunk_size)

# Буфер для одного чанка
buff = np.empty(chunk_size, dtype=np.complex64)

# Накапливаем спектр
psd_acc = np.zeros(chunk_size, dtype=np.float64)

# Пропускаем первые несколько чтений (переходный процесс)
for _ in range(5):
    sdr.readStream(stream, [buff], chunk_size)

# Читаем n_chunks блоков и усредняем
for i in range(n_chunks):
    sr = sdr.readStream(stream, [buff], chunk_size)
    if sr.ret <= 0:
        continue
    data = buff[:sr.ret]
    # Убираем DC (среднее значение)
    data = data - np.mean(data)
    # Окно Хэмминга
    windowed = data * window
    # FFT и мощность
    fft = np.fft.fft(windowed)
    psd = np.abs(fft)**2
    psd_acc += psd

sdr.deactivateStream(stream)
sdr.closeStream(stream)

# Средний спектр
psd_avg = psd_acc / n_chunks

# Переводим в дБ
psd_db = 10 * np.log10(psd_avg + 1e-20)

# Частоты (от -2.5 МГц до +2.5 МГц от центра)
freqs = np.fft.fftshift(np.fft.fftfreq(chunk_size, 1/5e6)) + 1575.42e6

# Сортируем по частоте
sorted_idx = np.argsort(freqs)
freqs = freqs[sorted_idx]
psd_db = psd_db[sorted_idx]

# Центр (L1: 1575.42 МГц ± 500 кГц)
center_mask = np.abs(freqs - 1575.42e6) < 500e3
center_avg = np.mean(psd_db[center_mask])

# Края (далее 1.5 МГц от центра)
edge_mask = np.abs(freqs - 1575.42e6) > 1.5e6
edge_avg = np.mean(psd_db[edge_mask])

diff_db = center_avg - edge_avg

print(f"center_avg={center_avg:.1f} dB, edge_avg={edge_avg:.1f} dB")
print(f"diff_dB={diff_db:.1f} dB")
print(f"---")
print(f"Peak: freq={freqs[np.argmax(psd_db)]/1e6:.4f} MHz, power={np.max(psd_db):.1f} dB")
# Топ-5 пиков
top5 = np.argsort(psd_db)[-5:][::-1]
for i in top5:
    print(f"  {freqs[i]/1e6:.4f} MHz -> {psd_db[i]:.1f} dB")
