# Öneriler — v0.2 için karar bekleyen konular

Bu doküman, v0.1.1 sonrası en çok değer katacak işleri ve bunlar için **kod yazmadan önce
verilmesi gereken kararları** listeler. Kod sağlığı ve güvenlik konuları v0.1.1'de kapandı
(bkz. CHANGELOG.md). Burada yalnızca yön kararları var.

Öncelik sırası:

1. Görev tanım dosyası
2. Gerçek bir uyduyla uçtan uca demo
3. Uplink komut doğrulaması
4. Küçük işler

---

## 1. Görev tanım dosyası (en önemli iş)

### Sorun
Telemetri şeması simülatörün 7 parametresine sabitlenmiş:
- `TelemetryParams` modeli ve `telemetry` tablosunun kolonları buna göre kurulu;
- adaptörler, çerçevenin bilgi alanında JSON bekliyor.

Hiçbir gerçek CubeSat JSON göndermez. Her görevin kendi binary paket formatı ve onlarca,
bazen yüzlerce parametresi vardır. Bu yüzden bir ekip kendi uydusunu **kod değiştirmeden**
ekleyemiyor. "30 dakikada ilk telemetri" vaadi şu an yalnızca simülatör için geçerli.

Aynı sabitlik başka yerlerde de var:
- FDIR eşikleri (`BATTERY_CRITICAL_V = 3.3`, tek hücre Li-ion varsayımı);
- mod politikası tablosu;
- komut listesi (frontend'deki `COMMAND_TYPES`).

### Öneri: görev başına tek bir tanım dosyası
Format olarak **TOML** öneriyorum. Python 3.11'in standart kütüphanesindeki `tomllib` ile
okunuyor, yani yeni bağımlılık gerekmiyor. YAML daha yaygın ama PyYAML ek bir bağımlılık olur.

```toml
[satellite]
id = "TRSAT1"
norad_id = 99999
name = "Örnek Üniversite CubeSat"

# ── Downlink ──────────────────────────────────────────────────────────
[[downlink.frames]]
name = "beacon"
protocol = "ax25"             # ax25 | kiss | ccsds
match = { src_callsign = "TA1SAT" }   # ccsds için: { apid = 100 }

  [[downlink.frames.fields]]
  name = "battery_voltage_v"
  type = "u16be"              # u8, i8, u16be/le, i16be/le, u32..., f32be/le, bits
  offset = 0                  # byte
  scale = 0.001               # ham → mühendislik birimi: raw * scale + bias
  unit = "V"
  limits = { warn_low = 3.5, crit_low = 3.3, warn_high = 4.25, crit_high = 4.3 }

  [[downlink.frames.fields]]
  name = "mode"
  type = "u8"
  offset = 12
  enum = { 0 = "beacon", 1 = "deployment", 2 = "nominal", 3 = "science", 4 = "safe" }
  role = "mode"               # politika motorunun okuduğu alan

# ── Uplink ────────────────────────────────────────────────────────────
[[uplink.commands]]
name = "mode_change"
opcode = 0x10
params = [{ name = "mode", type = "u8", enum_from = "mode" }]
allowed_modes = ["beacon", "deployment", "nominal", "science", "safe"]
safe_retry = true

[[uplink.commands]]
name = "separation"
opcode = 0xF0
critical = true               # iki admin onayı
allowed_modes = ["deployment"]
safe_retry = false
```

Bu dosyadan türeyecekler:

| Bugün sabit | Tanım dosyasından |
|---|---|
| `TelemetryParams` (7 alan) | `downlink.frames.fields` |
| Adaptörün JSON çözmesi | `type`/`offset`/`scale` ile binary çözme |
| `FDIR` eşik sabitleri | `limits` |
| `commands/policy.py` tablosu | `uplink.commands.allowed_modes` |
| `TWO_ADMIN_COMMANDS`, `UNSAFE_RETRY_TYPES` | `critical`, `safe_retry` |
| Frontend `COMMAND_TYPES` | `GET /satellites/{id}/definition` |
| Komutun JSON olarak gönderilmesi | `opcode` + `params` ile binary kodlama |

### Veri modeli kararı (senin onayın gerekli)

**Seçenek A — dar tablo (önerim).** Yeni bir hypertable:
`telemetry_values(time, satellite_id, param, value DOUBLE, raw BYTEA NULL)`.
- **Artısı:** Parametre sayısı sınırsız. TimescaleDB sıkıştırması bu tür veride çok
  verimli. Grafana sorguları basit kalır.
- **Eksisi:** Bir paket N satır olur. "Son değerler" için ayrı bir önbellek gerekir; Redis
  ya da materialized view olabilir.

**Seçenek B — mevcut tabloya `params JSONB` kolonu.**
- **Artısı:** Mevcut yapıya en az müdahale.
- **Eksisi:** JSONB üzerinde zaman serisi sorgusu ve sıkıştırma zayıf; limit ve anomali
  sorguları yavaşlar.

Geçiş planı: mevcut sabit kolonlar "çekirdek parametreler" olarak kalsın. Yeni parametreler
dar tabloya yazılsın; simülatör de bir tanım dosyasıyla çalıştırılsın. Böylece mevcut
UI kırılmaz.

### Çözücü kararı (senin onayın gerekli — yeni bağımlılık)

**Seçenek 1 — kendi çözücümüz.** `struct` tabanlı, tanım dosyasından sürülen bir çözücü.
- Bağımlılık yok, tam kontrol bizde.
- Bit alanları, değişken uzunluklu paketler ve koşullu alanlar zamanla karmaşıklaşır.

**Seçenek 2 — Kaitai Struct ve Libre Space'in `satnogs-decoders` kataloğu.**
- SatNOGS ağında çözücüsü yazılmış onlarca uydu **hiçbir ek iş olmadan** çalışır.
  Projenin "SatNOGS native" vaadini gerçekten karşılar.
- Ama yeni bir bağımlılık (`kaitaistruct` ve decoder paketi). AGENTS.md gereği bunun için
  senin onayın gerekiyor. Lisanslarını (decoder'lar AGPL olabilir) Apache 2.0 ile uyum
  açısından kontrol etmemiz gerekir. **Lisans kontrolü yapılmadan karar verilmemeli.**

Önerim: tanım dosyası ve Seçenek 1 çekirdek olsun; Seçenek 2 lisans uygunsa
**isteğe bağlı bir eklenti** olarak gelsin.

---

## 2. Gerçek bir uyduyla uçtan uca demo

v0.1.1'de simülatörle uçtan uca döngü kapandı. Projenin asıl kanıtı gerçek veri olacak.

Uydu seçim kriterleri:
- yörüngede ve amatör bantta düzenli beacon yayınlıyor;
- SatNOGS DB'de yakın tarihli, çözülmüş telemetrisi var;
- paket formatı belgelenmiş (ya da satnogs-decoders'ta çözücüsü var);
- tercihen Türkiye'den geçişleri iyi (eğimi ~50°'nin üzerinde).

Adımlar:
1. Uyduyu seç; SatNOGS gözlemlerinden 1 günlük ham çerçeveleri çek. Mevcut
   `satnogs_observations` tablosu bunu zaten yapıyor.
2. Tanım dosyasını yaz (1. maddeden sonra).
3. `satnogs_observations` → `telemetry.raw.*` köprüsünü kur. Şu an bu tablo ayrı duruyor ve
   anomali/FDIR hattına girmiyor.
4. Grafik, FDIR limitleri, geçiş içinde/dışında alarm davranışını gerçek veriyle göster.

Bu, IAC/TÜBİTAK sunumu için simülatörlü 8 özellikten daha güçlü bir demo olur.

---

## 3. Uplink komut doğrulaması (güvenlik — tasarım kararı)

Bugün C2, `commands.<sat>` konusuna imzasız JSON yayınlıyor. NATS erişimi artık kısıtlı.
Ama RF tarafında, frekansı ve formatı bilen herkes uyduya komut gönderebilir; bu, CubeSat
dünyasında bilinen ve yaygın bir zayıflık.

Öneri — HMAC-SHA256 ile kimliği doğrulanmış komutlar:
- **Anahtar:** Her uydunun kendi 256-bit anahtarı olur. C2'de secrets volume'unda saklanır;
  yer istasyonu köprüsü anahtarı görmez, yalnızca imzalı çerçeveyi iletir.
- **Çerçeve:** `opcode | params | counter (u32) | HMAC(key, opcode|params|counter)[:16]`
- **Tekrar koruması:** `counter` artmazsa uyduda reddedilir. C2 sayacı DB'de kalıcı tutar.
- Uydu tarafında doğrulama, görev ekibinin uçuş yazılımının işidir. C2 imzalamayı ve sayaç
  yönetimini sağlar.

Yeni bağımlılık gerekmiyor (`hmac` standart kütüphanede). Ancak uydu yazılımıyla uyumlu
bir çerçeve formatı seçilmeli. Bu yüzden bu iş, 1. maddedeki `uplink.commands` tanımıyla
birlikte yapılmalı.

---

## 4. Küçük işler

| İş | Neden | Efor |
|---|---|---|
| **Compose profilleri:** varsayılan çekirdek (db, redis, nats, backend, frontend, simulator); `--profile observability` ile Grafana/Prometheus/Loki/Promtail | "Hafif" vaadi. Varsayılan kurulumu 11 konteynerden 6'ya indirir, Promtail'in docker.sock erişimi de varsayılandan çıkar. | Küçük |
| **Tailwind 4 geçişi** | Kalan iki dev-tooling uyarısı (`braces`, `postcss-selector-parser`) bunu gerektiriyor | Orta |
| **K8s manifestlerini CI'da doğrulamak** (kind/k3d ile ayağa kaldırıp `/ready`) | Manifestler hiç bir cluster'da denenmedi | Orta |
| **Çok protokollü ingestion:** `telemetry.raw.<protokol>.<kaynak>` konu yapısı, protokol başına adaptör | KISS/CCSDS adaptörleri var ama hatta bağlı değil. 1. maddeyle birlikte anlam kazanır. | Küçük |
| **Audit boşlukları:** uydu oluşturma, TLE yükleme; `audit_log.ip_address` doldurulmuyor | Denetim izi eksik | Küçük |
| **Bağımlılık stub'ları:** `asyncpg-stubs` (dev) | mypy şu an asyncpg'yi `Any` görüyor | Küçük (yeni dev bağımlılığı → onay) |
| **Kullanıcı arayüzünde istasyon yönetimi** | İstasyon ekleme/uplink işaretleme şu an yalnızca API'den yapılabiliyor | Küçük |

---

## Karar listesi (senden beklenenler)

1. Telemetri veri modeli: **A (dar tablo)** mı, **B (JSONB)** mi?
2. Çözücü: yalnızca kendi çözücümüz mü, yoksa lisans kontrolünden sonra satnogs-decoders
   eklentisi de mi?
3. Demo için hangi uydu? (2. maddedeki kriterlere göre birlikte seçebiliriz.)
4. Compose profillerine geçelim mi? (Varsayılan kurulumda Grafana olmaz.)
5. `asyncpg-stubs` dev bağımlılığı eklensin mi?
