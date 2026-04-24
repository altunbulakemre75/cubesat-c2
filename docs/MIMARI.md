# CubeSat Mission Control — Sistem Mimarisi

## Genel Katmanlı Mimari

Sistem yedi katmanlı. Dikeyde veri aşağıdan yukarı akar. Sağda enine bir katman var: gözlem, güvenlik ve edge.

```
1. Dış kaynaklar                         │
   SatNOGS, TLE, RTL-SDR, operatör       │
                 |                       │
                 v                       │
2. Alım ve kodlama                       │  Enine katman:
   Protokol adaptörleri:                 │
   AX.25, KISS, CCSDS, özel              │  Gözlem
   Canonical telemetri objesi            │  - Prometheus
                 |                       │  - Loki
                 v                       │  - Grafana
3. İş mantığı                            │
   Yörünge + FDIR + Komut yaşamı         │  Güvenlik
   + Politika motoru                     │  - JWT
   Uydu durumuna göre kısıtlama          │  - RBAC
                 |                       │  - Audit
                 v                       │
4. NATS JetStream                        │  Edge
   Ana broker + leaf nodes               │  - Leaf node
                 |                       │  - Yerel kuyruk
        +--------+--------+              │  - Senkronize
        v                 v              │
5a. TimescaleDB      5b. FastAPI + WS    │
    + Redis + S3         Auth + RBAC     │
                              |          │
                              v          │
6. Sunum                                 │
   CesiumJS, komut UI, mobil             │
                 |                       │
                 v                       │
           Operatör                      │
```

---

## A) Protokol Adaptör Zinciri

Her uydu farklı radyo protokolü kullanır. Sistem bunu tek bir iç formata dönüştürür.

```
AX.25        KISS         CCSDS        Özel
amatör       TNC          uzay         protokol
CubeSat      frame        std.         plugin
  |            |            |            |
  v            v            v            v
AX.25        KISS         CCSDS        Özel
adaptör      adaptör      adaptör      adaptör
  |            |            |            |
  +------------+------+-----+------------+
                     |
                     v
    ========================================
    Canonical Telemetry Object
    {
      timestamp,
      satellite_id,
      params: { battery, temp, mode, ... }
    }
    JSON şema, her kaynaktan aynı format
    ========================================
                     |
                     v
          NATS JetStream → telemetry.*
```

**Önemli:** Yeni protokol eklemek için çekirdeği değiştirmezsin. Yeni bir adaptör plugin olarak eklenir. Açık kaynak topluluğu için kritik.

---

## B) Veri Akışı — Çift Yön

Telemetri aşağı, komut yukarı. NATS iki ayrı kanal yönetir: `telemetry.*` ve `commands.*`.

```
                 Uydu (CubeSat)
                      |
                 Yer istasyonu RF
                  /            \
          (Aşağı)              (Yukarı)
         telemetri              komut
             |                    ^
       Demodülatör            Modülatör
             |                    ^
       Protokol               Komut
       adaptörü               kodlayıcı
             |                    ^
             v                    |
      =========================================
      NATS JetStream
      telemetry.*        commands.*
      =========================================
             |                    ^
             v                    |
       İş mantığı             Komut sıra
       - Anomali              yöneticisi
       - Yörünge              - Zamanlama
       - FDIR                 - Yetki
       - Arşiv                - Durum kontrolü
             |                    ^
             v                    |
      =========================================
      FastAPI + WebSocket
      =========================================
             |                    ^
             v                    |
           Operatör UI (CesiumJS)
```

---

## C) Komut Yaşam Döngüsü + Politika Motoru + FDIR

Her komut bir durum makinesinden geçer. Politika motoru uydu moduna göre kısıtlar. FDIR arıza durumunda otomatik müdahale eder.

```
  Operatör            Politika           Uydu durumu
  komutu     ----->   motoru   <-----    (telemetriden)
  "kamera aç"         mod uygun mu?      beacon/nominal/safe
                           |
                           v
                     +---------+
                     | PENDING |
                     +----+----+
                          |
                          v
                    +----------+
                    |SCHEDULED |
                    +----+-----+
                         |
                         v  (geçiş penceresinde)
                   +--------------+
                   | TRANSMITTING |
                   +------+-------+
                          |
                          v
                     +---------+
                     |  SENT   |
                     +----+----+
                     /         \
                    v           v
              +----------+  +----------+
              | ACKED    |  | TIMEOUT  |
              | (başarı) |  +-----+----+
              +----------+        |
                                  v
                            +----------+
                            |  RETRY   | --- başarı ---> ACKED
                            | (max 3)  |
                            +-----+----+
                                  |
                                  v  (3 deneme başarısız)
                            +---------+
                            |  DEAD   |
                            +----+----+
                                 |
                                 v
                       +--------------------+
                       | FDIR tetiklenir    |
                       | Safe mode komutu   |
                       | Operatör uyarı     |
                       | Audit log          |
                       +--------------------+
```

**Kritik mekanizmalar:**

- **Idempotency** — Her komutun unique ID'si var. Aynı komut iki kez gitse uydu bir kez çalıştırır.
- **Exponential backoff retry** — 1sn, 4sn, 16sn. Max 3 deneme.
- **Safe retry bayrağı** — "Motoru ateşle" gibi tehlikeli komutlar retry edilmez.
- **Politika motoru** — Uydu safe mode'daysa "kamera aç" komutu reddedilir.
- **FDIR otomatik** — 3 deneme başarısız olursa safe mode tetiklenir.
- **LOS koruması** — Geçiş penceresi dışındaki timeout'lar retry sayımına dahil edilmez.

---

## D) Uydu Durum Makinesi

Her uydunun modu vardır, C2 buna göre komut kısıtlar.

```
        +--------------+
        |   BEACON     |   Sadece sağlık sinyali
        |   (başlangıç)|   İzin: mode_change
        +------+-------+
               |
               v
        +--------------+
        |  DEPLOYMENT  |   İlk fırlatma
        |  (ilk saat)  |   İzin: deployment komutları
        +------+-------+
               |
               v
    +----------------------+
    |      NOMINAL         |   Normal operasyon
    |   (günlük iş)        |   İzin: tüm normal komutlar
    +--+-------------+-----+
       |             |
       v             v
  +---------+   +----------+
  | SCIENCE |   |   SAFE   |   Arıza
  | (bilim) |   |  MODE    |   İzin: sadece recovery
  +---------+   +----------+
```

**Komut politika tablosu:**

| Uydu Modu | İzin Verilen Komutlar |
|-----------|----------------------|
| beacon | mode_change |
| deployment | deployment, mode_change |
| nominal | tüm normal komutlar |
| science | abort, mode_change |
| safe | recovery, mode_change, diagnostic |

**Not:** Mod bilgisinin yaşı/güveni kontrol edilir. Son beacon'dan 2+ saat geçmişse mod bilgisi "stale" işaretlenir ve operatör onayı istenir.

---

## E) Güvenlik Mimarisi

Üç rol ve değiştirilemez audit log.

```
   Kullanıcı
      |
      v
 HTTPS / TLS 1.3
      |
      v
 API ağ geçidi
 (rate limit, IP filter)
      |
      v
 JWT doğrulama
      |
      v
 RBAC motoru
      |
  +---+---+
  v   v   v
viewer operator admin
  |     |     |
  |     |   kritik komut = 2 admin onayı
  |     |   acil bypass = tek admin + audit özel işareti
  +-----+-----+
        v
  Audit log (append-only)
```

**Rol detayları:**

- **viewer** — telemetri okuma, komut yasak. Öğrenciler, demo.
- **operator** — rutin komutlar serbest, kritik komutlar yasak.
- **admin** — tüm komutlar, ancak kritik komutlar iki admin onayı ile.
  - Acil durum: tek admin bypass edebilir, audit log'a özel işaret düşer.

---

## F) Görev Planlama Modülü

Uydu geçiş pencereleri, yer istasyonu rezervasyonu, Doppler düzeltme.

```
  TLE verisi    Yer istasyonu    Komut kuyruğu
  Celestrak     SatNOGS+kendi    (operatör)
       |              |                |
       +--------------+----------------+
                      |
                      v
            Geçiş tahmini (SGP4)
            her uydu x her istasyon
                      |
                      v
            Çakışma çözücü
            önceliğe göre atama
                      |
                      v
          +-----------------------+
          | Geçiş zaman çizelgesi |
          +-----------------------+
              /        |         \
             v         v          v
        Pre-pass   In-pass    Post-pass
        - anten    - Doppler  - arşiv
        - komut    - real-    - sonraki
          yükle      time       geçiş
                     data       hesap
```

**Üç faz:**

1. **Pre-pass** (~15 dk önce) — Antenler yönlenir, komutlar yüklenir.
2. **In-pass** (~5-10 dk) — Doppler düzeltme SDR/DSP katmanında yapılır, gerçek zamanlı veri.
3. **Post-pass** — Arşiv, sonraki geçiş hesabı.

**Not:** Doppler düzeltme protokol adaptörünün altında (donanım/DSP katmanı) gerçekleşir, C2 iş mantığında değil.

---

## G) Edge / Offline Operasyon

İnternet yoksa yerel istasyon çalışmaya devam eder. NATS JetStream leaf node mimarisi.

```
 +----------------------+          +-----------------+
 | Uzak yer istasyonu   |          | Merkezi sunucu  |
 |                      |          |                 |
 | Yerel NATS (leaf)    | <------> | Ana NATS broker |
 | Yerel TimescaleDB    | internet | Ana TimescaleDB |
 | Yerel planlayıcı     | varsa    | Ana planlayıcı  |
 |                      |          |                 |
 | internet yoksa:      |          |                 |
 | - kendi başına çalış |          |                 |
 | - veriyi yerelde tut |          |                 |
 | - komut kuyruğu      |          |                 |
 |                      |          |                 |
 | internet gelince:    |          |                 |
 | - senkronize et      |          |                 |
 +----------------------+          +-----------------+
```

---

## H) Dağıtım Mimarisi

Geliştirme, test ve üretim aynı kod.

```
  Geliştirici --> GitHub --> CI/CD pipeline
                               |
                               v
                     Container Registry
                               |
           +-------------------+--------------------+
           v                   v                    v
    +-----------+       +------------+      +--------------+
    |Geliştirme |       |    Test    |      |   Üretim     |
    |Docker     |       |  staging   |      |  Kubernetes  |
    |Compose    |       |            |      |              |
    |+ simülatör|       | otomatik   |      |  2-3 replica |
    |           |       | test       |      |  auto-scale  |
    +-----------+       +------------+      +--------------+
           |                   |                    |
           +-------------------+--------------------+
                               v
                     Prometheus + Grafana + Loki
```

**Üç ortam:**

- **Geliştirme** — Docker Compose. `docker compose up` ile 5 dakikada ayakta. Uydu simülatörü dahil.
- **Test** — Staging. Her push'ta otomatik test, sahte telemetri üretilir.
- **Üretim** — Kubernetes. Replika + otomatik ölçekleme.

---

## Mimari Özet

| # | Diyagram | Ne gösterir |
|---|----------|-------------|
| Genel | Katmanlı mimari | 7 katman + enine gözlem/güvenlik/edge |
| A | Protokol adaptör | Farklı protokollerin canonical formata çevrimi |
| B | Veri akışı | Çift yön telemetri + komut |
| C | Komut yaşam döngüsü | Durum makinesi + politika + FDIR |
| D | Uydu durum makinesi | Mod bazlı komut kısıtı |
| E | Güvenlik | RBAC + audit log |
| F | Görev planlama | Pre/In/Post-pass |
| G | Edge / offline | Leaf node + senkronizasyon |
| H | Dağıtım | Docker + Kubernetes + gözlem |
