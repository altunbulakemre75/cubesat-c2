# CubeSat C2

Açık kaynak, hafif ve kolay kurulabilen CubeSat komuta-kontrol sistemi.

> **Durum:** Geliştirme aşamasında (Faz 0 — kurulum)

---

## Ne İşe Yarar?

CubeSat C2, küçük uydu operatörleri için tasarlanmış bir mission control platformudur. Bir TLE (iki satırlık yörünge verisi) yapıştırırsın ve sistem otomatik olarak:

- Yer istasyonlarından telemetri toplar (SatNOGS native)
- Yörünge ve geçiş pencerelerini hesaplar
- Anomalileri tespit eder
- Uydu moduna göre tehlikeli komutları engeller
- Otomatik arıza tespit ve safe mode tetikler (FDIR)
- 3D haritada uyduyu gerçek zamanlı gösterir
- İnternet kesilse bile yerel operasyonu sürdürür

## Neden Bu Proje?

Mevcut çözümler sorunlu:

| Seçenek | Problem |
|---------|---------|
| Ticari (STK, FreeFlyer) | Pahalı, kapalı kaynak |
| OpenC3 COSMOS | Kurulum 2 gün sürüyor |
| El yapımı Python scriptleri | Herkes tekrar yazıyor |

Bizim çözüm: **Tek komutla çalışır, hafif, modüler.**

## Kurulum

```bash
git clone <repo-url>
cd cubesat-c2
docker compose up
```

Tarayıcıyı aç: `http://localhost:3000`

5 dakikada çalışır. Gerçek donanım gerektirmez — uydu simülatörü dahil.

## Mimari

Detaylar için bkz: [docs/MIMARI.md](docs/MIMARI.md)

Kısa özet — 8 katmanlı sistem:

```
Dış kaynaklar (SatNOGS, TLE, SDR)
       ↓
Protokol adaptörleri (AX.25, KISS, CCSDS)
       ↓
İş mantığı (yörünge, FDIR, komut, politika)
       ↓
NATS JetStream (mesaj yolu)
       ↓
TimescaleDB + Redis + API
       ↓
CesiumJS operatör UI
```

## Teknoloji Yığını

- **Backend:** Python 3.11, FastAPI, NATS JetStream
- **Veri:** TimescaleDB, Redis
- **Frontend:** React 18, Vite, CesiumJS
- **Altyapı:** Docker, Kubernetes, Prometheus + Grafana

## Kimler Kullanabilir?

- Üniversite uzay kulüpleri (ODTÜ, İTÜ, Boğaziçi ve uluslararası)
- Küçük uydu operatörleri (Plan-S, Fergani Space tarzı)
- Amatör radyo ve satellite hobby topluluğu
- Araştırma projeleri

## Katkı

Proje henüz erken aşamada. Kısa sürede `CONTRIBUTING.md` eklenecek.

## Lisans

Apache 2.0 (geçici — revize edilecek)

## İletişim

Solo developer: Emre Altunbulak
