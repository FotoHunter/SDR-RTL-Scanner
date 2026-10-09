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

chunk_size = 8192
n_chunks = 100
window = np.hamming(chunk_size)

# Большой накопительный буфер
big_buf = np.empty(0, dtype=np.complex64)
read_buf = np.empty(chunk_size * 4, dtype=np.complex64)

# Пропускаем переходный процесс
for _ in range(10):
    sdr.readStream(stream, [read_buf], len(read_buf))

psd_acc = np.zeros(chunk_size, dtype=np.float64)
chunks_done = 0

while chunks_done < n_chunks:
    sr = sdr.readStream(stream, [read_buf], len(read_buf))
    if sr.ret <= 0:
        continue
    big_buf = np.concatenate([big_buf, read_buf[:sr.ret]])
    
    while len(big_buf) >= chunk_size and chunks_done < n_chunks:
        data = big_buf[:chunk_size].copy()
        big_buf = big_buf[chunk_size:]
        data = data - np.mean(data)
        windowed = data * window
        fft = np.fft.fft(windowed)
        psd_acc += np.abs(fft)**2
        chunks_done += 1

sdr.deactivateStream(stream)
sdr.closeStream(stream)

psd_avg = psd_acc / chunks_done
psd_db = 10 * np.log10(psd_avg + 1e-20)

freqs = np.fft.fftshift(np.fft.fftfreq(chunk_size, 1/5e6)) + 1575.42e6
sorted_idx = np.argsort(freqs)
freqs = freqs[sorted_idx]
psd_db = psd_db[sorted_idx]

center_mask = np.abs(freqs - 1575.42e6) < 500e3
edge_mask = np.abs(freqs - 1575.42e6) > 1.5e6
center_avg = np.mean(psd_db[center_mask])
edge_avg = np.mean(psd_db[edge_mask])
diff_db = center_avg - edge_avg

print(f"chunks={chunks_done}")
print(f"center_avg={center_avg:.1f} dB, edge_avg={edge_avg:.1f} dB")
print(f"diff_dB={diff_db:.1f} dB")
print("---")
top5 = np.argsort(psd_db)[-5:][::-1]
for i in top5:
    print(f"  {freqs[i]/1e6:.4f} MHz -> {psd_db[i]:.1f} dB")
