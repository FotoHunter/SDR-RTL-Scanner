import SoapySDR
import numpy as np

args = "driver=soapyMiri,index=0"
sdr = SoapySDR.Device(args)

center_freq = 1570.42e6  # пробуем центр на месте левого пика
sdr.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, center_freq)
sdr.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, 10e6)
sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, "LNA", 20)
sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, "Baseband", 20)

stream = sdr.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32, [0])
sdr.activateStream(stream)

chunk_size = 8192
n_chunks = 200
window = np.hamming(chunk_size)
big_buf = np.empty(0, dtype=np.complex64)
read_buf = np.empty(chunk_size * 4, dtype=np.complex64)

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
        psd_acc += np.abs(np.fft.fft(windowed))**2
        chunks_done += 1

sdr.deactivateStream(stream)
sdr.closeStream(stream)

psd_avg = psd_acc / chunks_done
psd_db = 10 * np.log10(psd_avg + 1e-20)
freqs = np.fft.fftshift(np.fft.fftfreq(chunk_size, 1/10e6)) + center_freq
sorted_idx = np.argsort(freqs)
freqs = freqs[sorted_idx]
psd_db = psd_db[sorted_idx]

print(f"Center freq: {center_freq/1e6:.2f} MHz")
print(f"Range: {freqs[0]/1e6:.2f} - {freqs[-1]/1e6:.2f} MHz")
print("--- Top 10 peaks ---")
top10 = np.argsort(psd_db)[-10:][::-1]
for i in top10:
    print(f"  {freqs[i]/1e6:.4f} MHz -> {psd_db[i]:.1f} dB")
