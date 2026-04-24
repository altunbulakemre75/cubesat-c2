# AGENTS.md — CubeSat C2 Workspace Constitution

Bu dosya, Antigravity IDE'de çalışan Claude Sonnet 4.5 için workspace rehberidir. Her session'da ilk okunması gereken dokümandır.

---

## Proje Kimliği

**Proje adı:** CubeSat C2 (final isim henüz seçilmedi)

**Ne yapıyoruz:** Açık kaynak, hafif, kolay kurulabilen bir CubeSat komuta-kontrol (C2) sistemi. Üniversite uzay kulüpleri, küçük uydu operatörleri ve Türk uzay ekosistemi için.

**Geliştirici:** Emre Altunbulak (solo developer)

**Workspace:** `C:\Users\altun\Desktop\Yeni klasör\cubesat-c2`

**IDE:** Google Antigravity + Claude Sonnet 4.5

**Paralel proje:** NIZAM (counter-UAS), `C:\Users\altun\Desktop\Yeni klasör\nizam-agent-c2`

---

## Projenin Değer Önermesi

Şu an piyasada üç seçenek var ve hepsi sorunlu:

1. **Ticari** (AGI STK, FreeFlyer) — pahalı, kapalı kaynak
2. **OpenC3 COSMOS** — açık kaynak ama kurulum 2 gün sürüyor
3. **El yapımı Python scriptleri** — her ekip tekrar yazıyor

Bizi ayıran 8 şey:

1. Tek komutla kurulum (`docker compose up`)
2. SatNOGS native entegrasyonu
3. Protokol soyutlama (AX.25, KISS, CCSDS, özel)
4. İstatistiksel anomali tespiti (AI değil)
5. Komut durum makinesi + idempotency
6. FDIR entegre (otomatik safe mode)
7. Edge operasyon (internet kesilse bile çalışır)
8. Time-series DB (TimescaleDB)

---

## Teknoloji Yığını

### Backend
- **Python 3.11+**
- **FastAPI** — REST + WebSocket
- **Pydantic v2** — şema doğrulama
- **python-jose** — JWT

### Yörünge ve Astronomi
- **skyfield** — modern astronomi
- **sgp4** — hızlı TLE propagator

### Protokol
- **pyax25** — AX.25
- **kiss-python** — KISS TNC
- **Kendi CCSDS parser** — Python'da iyi paket yok

### Mesaj ve Depolama
- **NATS JetStream** — mesaj yolu
- **TimescaleDB** — time-series
- **Redis** — cache
- **MinIO veya S3** — büyük veri

### Frontend
- **React 18 + Vite**
- **CesiumJS** — 3D orbit
- **satellite.js** — tarayıcıda SGP4
- **TailwindCSS** — styling

### DevOps
- **Docker + Docker Compose** — geliştirme
- **Kubernetes** — üretim (sonra)
- **Prometheus + Grafana + Loki** — gözlem

### Referans (Kopyalama Değil, Okuma)
- NASA cFS
- NASA OpenMCT
- OpenC3 COSMOS
- OreSat (üniversite CubeSat)
- gr-satellites

---

## Klasör Yapısı (Hedef)

```
cubesat-c2/
├── AGENTS.md                    # Bu dosya
├── README.md                    # Proje tanıtımı
├── docker-compose.yml           # Geliştirme ortamı
├── .gitignore
├── .env.example
├── docs/
│   ├── MIMARI.md               # Sistem mimarisi
│   ├── API.md                  # API dokümantasyonu
│   └── DEPLOYMENT.md           # Kurulum rehberi
├── backend/
│   ├── pyproject.toml
│   ├── src/
│   │   ├── ingestion/          # Protokol adaptörleri
│   │   ├── orbit/              # Yörünge hesabı
│   │   ├── anomaly/            # Anomali tespiti
│   │   ├── fdir/               # Fault detection
│   │   ├── commands/           # Komut yaşam döngüsü
│   │   ├── scheduler/          # Görev planlama
│   │   ├── api/                # FastAPI endpoints
│   │   └── storage/            # DB adaptörleri
│   └── tests/
├── frontend/
│   ├── package.json
│   └── src/
│       ├── components/
│       ├── pages/
│       ├── stores/             # State management
│       └── lib/                # CesiumJS, satellite.js
├── simulator/                  # Sahte uydu + sahte yer istasyonu
│   └── src/
└── deployment/
    ├── k8s/                    # Kubernetes manifests
    └── grafana/                # Dashboard configs
```

---

## Geliştirme Prensipleri

### Kod Kalitesi

- **Python**: Type hints zorunlu, ruff + mypy geçmeli
- **TypeScript**: Strict mode açık, any yasak
- **Test coverage**: Kritik modüllerde %80+
- **Commit mesajları**: Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`)

### Mimari Prensipler

- **Adapter Pattern**: Protokol adaptörleri için
- **Event-driven**: NATS JetStream üzerinden
- **Idempotent komutlar**: Unique ID'li
- **Fail-safe**: FDIR her katmanda düşünülmeli
- **Offline-first**: Edge node mimarisi

### Yasaklar

- **AI bağımlılığı yok**: LangChain, LangGraph, OpenAI API kullanmıyoruz. Bu projede AI yok.
- **Vendor lock-in yok**: AWS-only veya Google-only hizmetler yasak. Self-hosted zorunlu.
- **Browser storage (localStorage) yok**: Artifacts ortamında çalışmaz, in-memory state kullan.
- **Kopyala-yapıştır kod yok**: Referans repoları oku, kendi kodumazu yaz.

### Güvenlik

- **Secrets asla repo'da olmamalı**: `.env` kullan, `.env.example` commit'e dahil
- **Audit log append-only**: Değiştirilemez
- **RBAC**: viewer, operator, admin rolleri
- **JWT + HTTPS**: Auth zorunlu

---

## Claude'a Talimatlar

### Genel

1. Her session başında bu dosyayı ve `docs/MIMARI.md` dosyasını oku.
2. Türkçe cevap ver (Emre Türkçe tercih ediyor).
3. Kod yorumları İngilizce olabilir, commit mesajları İngilizce.
4. Önce plan, sonra kod.

### Kod Yazarken

1. **Küçük adımlar**: Bir PR = bir özellik. 500 satırlık commit yok.
2. **Test önce**: Yeni modül yazıyorsan önce testini yaz.
3. **Mimari dokümana sadık**: `docs/MIMARI.md`'deki katmanlara uy.
4. **Yeni bağımlılık = tartışma**: `pyproject.toml`'a paket eklemeden önce Emre'ye sor.

### Yapmamalıklar

1. `rm -rf` komutları, `git push --force`, `git reset --hard` — kullanmadan önce onay iste.
2. Yeni bir framework eklemeyi önerme (zaten seçilmiş stack var).
3. "AI ekleyelim" demek — bu projede AI yok.
4. Dosya silme ya da büyük yeniden yapılandırma — önce sor.

---

## Geliştirme Aşamaları

### Faz 0: Kurulum (şu an)
- Workspace hazır
- AGENTS.md, MIMARI.md, README.md
- Docker Compose iskelet
- Git repo başlat

### Faz 1: Çekirdek Veri Akışı
- Uydu simülatörü (sahte telemetri üretir)
- SatNOGS API client
- Protokol adaptörleri (AX.25 ile başla)
- NATS JetStream entegrasyonu
- TimescaleDB şeması

### Faz 2: İş Mantığı
- Yörünge hesabı (skyfield)
- Geçiş planlama (pre/in/post pass)
- Anomali tespiti (istatistiksel)
- Komut yaşam döngüsü
- FDIR monitor

### Faz 3: API ve Frontend
- FastAPI endpoints
- WebSocket canlı yayın
- JWT + RBAC
- Audit log
- React + CesiumJS UI
- Komut gönderim UI

### Faz 4: Görünürlük ve Dağıtım
- Prometheus + Grafana
- Loki log aggregation
- Kubernetes manifests
- CI/CD pipeline (GitHub Actions)
- Dokümantasyon

### Faz 5: Topluluk
- GitHub public
- CONTRIBUTING.md
- Kod örnekleri
- Video demo
- Blog yazısı

---

## Başarı Kriterleri

- **Kullanıcı**: Bir üniversite öğrencisi 5 dakikada kurar, 30 dakikada ilk telemetriyi görür.
- **Performans**: 10 CubeSat'ı aynı anda yönetir, saniyede 100 telemetri parametresi işler.
- **Güvenilirlik**: Tek bileşen çökerse sistem çalışmaya devam eder.
- **Topluluk**: GitHub'da ilk 3 ayda 50+ yıldız.
- **Adaptasyon**: En az 1 Türk üniversite takımı veya şirket kullanır.

---

## Emre Hakkında

- Solo developer
- Türkçe iletişim tercih ediyor
- Dürüst, direkt cevap istiyor — boş övgü yok
- NIZAM'da 89 vendor repo, 12 teknoloji alanı entegre etti
- Python + FastAPI + NATS + CesiumJS deneyimi var
- Bu proje uzay ekosistemine giriş kapısı olarak konumlanıyor

---

## İletişim Notları

- Uzun kod bloklarından önce "plan özeti" ver
- Bir özelliği tamamlayınca "bir sonraki adım" öner
- Hatalar olduğunda önce tanı, sonra çözüm
- "Neden bu yaklaşım?" diye sor — Emre kara kutu kod istemiyor
