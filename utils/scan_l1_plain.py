import SoapySDR
import numpy as np

# Инициализация устройства
args = "driver=soapyMiri,index=0"
sdr = SoapySDR.Device(args)

# Настройка частоты L1 GPS
sdr.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, 1575.42e6)

# Полоса 5 МГц
sdr.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, 5e6)

# Смотрим доступные элементы усиления и ставим первый
gains = sdr.listGains(SoapySDR.SOAPY_SDR_RX, 0)
print(f"Available gains: {gains}")
if gains:
    gname = gains[0]
    sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, gname, 20)
    print(f"Set gain '{gname}' to 20 dB")

# Антенна
antennas = sdr.listAntennas(SoapySDR.SOAPY_SDR_RX, 0)
print(f"Available antennas: {antennas}")

# Настраиваем поток
stream = sdr.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32, [0])
sdr.activateStream(stream)

# Буфер (256k комплексных выборок)
buff = np.empty(2**18, dtype=np.complex64)

# Читаем
sr = sdr.readStream(stream, [buff], len(buff))
print(f"Read {sr.ret} samples")

if sr.ret <= 0:
    sdr.deactivateStream(stream)
    raise RuntimeError(f"Не удалось прочитать данные (ret={sr.ret})")

# Закрываем поток
sdr.deactivateStream(stream)
sdr.closeStream(stream)

# Спектр (PSD)
psd = np.abs(np.fft.fft(buff[:sr.ret]))**2

# Центр и края
center_idx = len(psd) // 2
window = 256
center_avg = np.mean(psd[center_idx - window:center_idx + window])
edge_avg  = (np.mean(psd[:window]) + np.mean(psd[-window:])) / 2.0

diff_db = 20 * np.log10(center_avg / edge_avg) if edge_avg > 0 else 0.0

print(f"center_avg={center_avg:.2e}, edge_avg={edge_avg:.2e}")
print(f"diff_dB={diff_db:.1f} dB")
