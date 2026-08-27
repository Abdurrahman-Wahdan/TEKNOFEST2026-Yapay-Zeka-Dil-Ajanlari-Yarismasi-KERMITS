# Trendyol-TTS — Streaming Entegrasyon Rehberi

Türkçe metin-seslendirme. Bu belge, modeli **streaming** modda, en verimli
ayarlarla bir UI'a bağlamak için gereken her şeyi içerir.

Tüm sayılar Apple M5 Max / 128 GB / MPS üzerinde ölçülmüştür.

---

## 1. Model

| | |
|---|---|
| Model | `Trendyol/Trendyol-TTS` |
| Taban | `openbmb/VoxCPM2` + Türkçe LoRA (20+ saat) |
| Lisans | MIT (ticari kullanım serbest) |
| Dil | Türkçe |
| Çıkış | 48 kHz mono, `float32` numpy dizisi |
| Disk | ~5 GB (ilk indirmede HF cache'e iner) |

---

## 2. Kurulum

```bash
pip install voxcpm sounddevice soundfile
```

`sounddevice` yalnız hoparlörden çalacaksan gerekir; UI'a byte gönderiyorsan
gerekmez.

Bağımlılık uyarısı: `voxcpm`, `datasets` üzerinden `fsspec`'i **2025.3.0'a
düşürür**. Aynı ortamda `fsspec`'in yeni sürümüne bağlı bir şey varsa ayrı
bir sanal ortam kullanın.

---

## 3. En küçük çalışan örnek

```python
from voxcpm.core import VoxCPM

model = VoxCPM.from_pretrained(
    hf_model_id="Trendyol/Trendyol-TTS",
    load_denoiser=False,     # denoiser gereksiz, yükleme süresini artırır
    optimize=True,
)
sr = model.tts_model.sample_rate          # 48000

for parca in model.generate_streaming(
        text="Katılım bankaları kâr payı esasına göre çalışır.",
        cfg_value=2.0,
        inference_timesteps=10,
        max_len=4096,
        normalize=True,
        denoise=False):
    # parca: float32 numpy dizisi, ortalama ~160 ms ses
    ...
```

`generate_streaming` bir **generator** döndürür — parçalar üretildikçe gelir,
tamamının bitmesini beklemez.

---

## 4. Ayarlar — ölçümle seçildi

### `inference_timesteps` (en önemli parametre)

Difüzyon adım sayısı. Kalite ve hızı birlikte belirler.

Aynı metin, 3 tekrarın ortalaması:

| değer | ilk ses | üretim | ses | oran |
|---|---|---|---|---|
| 4 | 0.41 s (±0.44) | 3.6 s | 9.7 s | 2.70x |
| **10** | **0.13 s (±0.00)** | 4.7 s | 9.2 s | 1.94x |
| 16 | 0.16 s (±0.00) | 6.4 s | 9.5 s | 1.48x |

**`inference_timesteps=10` kullanın.** Sezgiye aykırı ama ölçüm net: düşük
değer (4) toplam üretimi hızlandırsa da **ilk sesi geciktirir ve kararsızdır**
(sapma ±0.44 s). Streaming'de kullanıcının hissettiği gecikme ilk sestir, o
yüzden 10 daha iyi. Üstelik kalitesi de daha yüksek.

`4` değerinde uzun metinlerde oynatma kesintisi riski de ölçüldü: 165 parçanın
9'unda üretim çalmanın gerisinde kaldı. `10`'da bu sayı 1.

`2` denemeyin: model metnin bir kısmını atlıyor (55 sn'lik metin 29 sn çıktı).

### Diğer parametreler

| parametre | değer | neden |
|---|---|---|
| `cfg_value` | `2.0` | model kartının önerisi. `1.5` daha canlı tonlama verir; `2.5` kullanmayın (0 dBFS'e yaklaşan tepe genlik) |
| `normalize` | `True` | metin normalizasyonu (sayı, kısaltma) |
| `denoise` | `False` | girdi sesi yok, gereksiz |
| `load_denoiser` | `False` | yükleme süresini kısaltır |
| `retry_badcase` | `True` (varsayılan) | kapatmanın ölçülebilir faydası yok (17.4 s vs 18.0 s) |
| `max_len` | `4096` | uzun metinler için yeterli |

---

## 5. Performans

```
model yükleme     ~5.6 s     (süreç başına BİR KEZ — modeli bellekte tutun)
ilk ses           ~0.13 s    (ts=10)
üretim hızı       ~1.9x gerçek zaman
parça boyutu      ~160 ms
örnekleme         48 kHz
```

Üretim, konuşmadan hızlı olduğu için akış kesintisiz ilerler.

**Kritik:** `from_pretrained` her istekte çağrılırsa 5.6 saniye kaybedilir.
Modeli uygulama başlarken bir kez yükleyip bellekte tutun.

---

## 6. Konuşma hızı

Modelde konuşma hızı parametresi **yoktur** (kaynak kodu kontrol edildi).
Kullanıcı hız kontrolü isterse üretim sonrası zaman-esnetme gerekir:

```python
import librosa
hizli = librosa.effects.time_stretch(parca.astype(np.float32), rate=1.2)
```

Perde (ses tonu) korunur, yalnız tempo değişir.

**Tuzak:** `librosa` ilk çağrıda numba'yı derler (~70 ms) ve bu, akışın ilk
parçasını geciktirir. Model yüklenirken bir kez boşa çağırıp derlemeyi peşin
yaptırın; sonraki çağrılar 0.8 ms sürer:

```python
librosa.effects.time_stretch(np.zeros(4096, dtype=np.float32), rate=1.2)
```

Ölçüm: ısıtma olmadan ilk ses 8.52 s, ısıtmayla 1.01 s.

Hız gerekmiyorsa `librosa`'yı hiç kullanmayın — modelin kendi hızı en doğal
sonucu verir.

---

## 7. UI entegrasyonu

### Web (FastAPI + WebSocket)

```python
import asyncio, numpy as np
from fastapi import FastAPI, WebSocket
from voxcpm.core import VoxCPM

app = FastAPI()
model = None          # başlangıçta BİR KEZ yüklenir

@app.on_event("startup")
def yukle():
    global model
    model = VoxCPM.from_pretrained(hf_model_id="Trendyol/Trendyol-TTS",
                                   load_denoiser=False, optimize=True)

@app.websocket("/tts")
async def tts(ws: WebSocket):
    await ws.accept()
    metin = await ws.receive_text()
    dongu = asyncio.get_running_loop()

    def uret():
        return list(model.generate_streaming(
            text=metin, cfg_value=2.0, inference_timesteps=10,
            max_len=4096, normalize=True, denoise=False))

    # generate_streaming SENKRON bir generator — olay döngüsünü bloklamamak
    # için ayrı bir thread'de çalıştırın.
    for parca in await dongu.run_in_executor(None, uret):
        # 16-bit PCM'e çevir (tarayıcıda AudioContext ile çalınır)
        pcm = (np.clip(parca, -1, 1) * 32767).astype(np.int16)
        await ws.send_bytes(pcm.tobytes())
    await ws.close()
```

Yukarıdaki örnek parçaları toplayıp gönderir. **Gerçek anlık akış** için
generator'ı bir kuyruğa besleyin:

```python
import queue, threading

def akis(metin):
    q = queue.Queue()
    def calis():
        try:
            for p in model.generate_streaming(
                    text=metin, cfg_value=2.0, inference_timesteps=10,
                    max_len=4096, normalize=True, denoise=False):
                q.put(p)
        finally:
            q.put(None)
    threading.Thread(target=calis, daemon=True).start()
    while (p := q.get()) is not None:
        yield p
```

### Tarayıcı tarafı

```javascript
const ctx = new AudioContext({ sampleRate: 48000 });
let sonraki = ctx.currentTime;

ws.onmessage = async (ev) => {
  const pcm = new Int16Array(await ev.data.arrayBuffer());
  const buf = ctx.createBuffer(1, pcm.length, 48000);
  const kanal = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) kanal[i] = pcm[i] / 32768;

  const kaynak = ctx.createBufferSource();
  kaynak.buffer = buf;
  kaynak.connect(ctx.destination);
  // parçaları arka arkaya zamanla — üst üste binmesin, boşluk kalmasın
  sonraki = Math.max(sonraki, ctx.currentTime);
  kaynak.start(sonraki);
  sonraki += buf.duration;
};
```

`sampleRate: 48000` şart — model bu frekansta üretir, uyumsuzluk sesi
tizleştirir veya kalınlaştırır.

### Yerel oynatma (masaüstü/test)

```python
import sounddevice as sd, numpy as np

with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as akis:
    for parca in model.generate_streaming(...):
        akis.write(parca.astype(np.float32).reshape(-1, 1))
```

---

## 8. Eşzamanlılık

Model **thread-safe değildir.** Aynı örneği iki istek aynı anda kullanırsa
çıktılar bozulur.

- Tek kullanıcı: tek model örneği yeterli.
- Çok kullanıcı: istekleri bir kuyruğa alıp sırayla işleyin, ya da her biri
  kendi model örneğine sahip N worker açın (her örnek ~5 GB bellek).

MPS'te birden fazla örnek belleği hızla tüketir; kuyruk yaklaşımı daha
güvenlidir. Üretim gerçek zamandan ~1.9x hızlı olduğu için tek örnek birkaç
kullanıcıya yetişir.

---

## 9. Denenip elenen yollar

Bunları tekrar denemeyin, ölçüldü:

**MLX (Apple Silicon hızlandırma)** — `mlx-audio` paketi `voxcpm2` mimarisini
destekliyor, ancak Trendyol ağırlıkları yüklenmiyor: `Missing 233 parameters`.
Trendyol `audiovae.pth`'i ayrı bir PyTorch dosyası olarak tutuyor, MLX tek
`safetensors` bekliyor. `mlx-community/VoxCPM2-4bit` hazır bir MLX portudur
ama o **taban VoxCPM2**'dir, Türkçe finetune değildir — hız kazanılır, Türkçe
kalitesi kaybedilir.

**`retry_badcase=False`** — 18.0 s → 17.4 s. Ölçülebilir fayda yok, açık kalsın.

**`inference_timesteps=2`** — üretim 7.2 s'ye iner ama model metnin bir kısmını
atlar (55 sn'lik metin 29.6 sn çıktı).

---

## 10. Bilinen davranışlar

- İlk çalıştırmada model HF cache'e iner (~5 GB, ağa bağlı olarak dakikalar).
  Sonraki çalıştırmalar 5.6 saniyede yüklenir.
- MPS'te model `bfloat16 → float32` çevirir, bu normaldir.
- Konsolda `Warm up VoxCPMModel...` ve ilerleme çubuğu yazar.
  Susturmak için: `os.environ["TQDM_DISABLE"] = "1"` ve
  `warnings.filterwarnings("ignore")`.
- Parça boyutları eşit değildir (~160 ms ortalama); UI bunu varsaymamalı.

---

## 11. Çalışan referans betik

Uçtan uca çalışan, hoparlörden çalan bir örnek:
`~/Desktop/trendyol_tts/seslendir.py`

```bash
./seslendir.py "metin"            # varsayılan
./seslendir.py -h 1.0 "metin"     # modelin kendi hızı
./seslendir.py -k cikti.wav "metin"
```

Streaming döngüsü, kuyruk-tabanlı oynatma ve librosa ısıtması bu betikte
uygulanmış hâlde bulunur.
