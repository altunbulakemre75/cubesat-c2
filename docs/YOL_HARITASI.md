# CubeSat C2 — Geliştirme Yol Haritası

Bu doküman projenin tüm geliştirme fazlarını ve alt adımlarını içerir. Her faz tamamlandığında bir sonrakine geçilir. Sıra önemlidir — alt katman olmadan üst katman çalışmaz.

---

## Genel Durum (Nisan 2026)

| Faz | Konu | Durum | Not |
|-----|------|-------|-----|
| 0 | Kurulum | ✅ Tamamlandı | Docker, Python, Node hazır |
| 1 | Çekirdek Veri Akışı | ✅ Tamamlandı | Sim → NATS → Writer (batch) → Timescale |
| 2 | Pipeline Sağlamlaştırma | ✅ Tamamlandı | Multi-adapter (AX.25/KISS/CCSDS), JetStream durable, NAK on failure |
| 3 | Komut & Kontrol | ✅ Tamamlandı | RBAC, JWT refresh+logout, two-admin approval, state machine |
| 4 | Görselleştirme | ✅ Tamamlandı | Cesium globe, Recharts, WS streams, anomaly toasts |
| 5 | Operasyonel Olgunluk | ✅ Tamamlandı | Anomaly detector wired, FDIR, Celestrak refresher, Loki + Prometheus + Grafana |
| 6 | Açık Kaynak Yayını | ✅ Tamamlandı | v0.1.0 tagli, Apache 2.0, SECURITY.md, GitHub güvenlik özellikleri |

**Bilinen tamamlanmamış noktalar:**
- HF/UHF gerçek radyo entegrasyonu (donanım bekleniyor)
- Production K8s deploy (şu an docker-compose-only)
- E2E testler (sadece backend unit + frontend Vitest var)

---

## Faz 0: Kurulum (Tamamlandı ✓)

Amaç: Geliştirme ortamı hazır, workspace çalışır durumda.

### 0.1 Workspace Hazırlığı
- [x] `cubesat-c2` klasörü oluşturuldu
- [x] `AGENTS.md`, `README.md`, `MIMARI.md` yerleştirildi
- [x] `.gitignore`, `.env.example`, `docker-compose.yml` yerleştirildi

### 0.2 Git Başlatma
- [x] `git init`
- [x] `git add .`
- [x] `git commit -m "Initial commit: workspace skeleton"`
- [ ] GitHub'da private repo oluştur
- [ ] `git remote add origin <url>`
- [ ] `git push -u origin main`

### 0.3 Docker Altyapı Testi
- [ ] `copy .env.example .env`
- [ ] `docker compose up -d`
- [ ] `docker ps` ile 3 container kontrolü (timescaledb, redis, nats)
- [ ] TimescaleDB bağlantı testi: `docker exec -it cubesat-timescaledb psql -U cubesat`
- [ ] Redis testi: `docker exec -it cubesat-redis redis-cli ping`
- [ ] NATS management UI: `http://localhost:8222`

### 0.4 Python Ortamı
- [x] `backend/` klasörü oluştur
- [ ] Python 3.11+ kontrol: `python --version`
- [x] `backend/pyproject.toml` oluştur
- [ ] Virtual environment kur
- [ ] Temel bağımlılıkları yükle: fastapi, pydantic, skyfield, sgp4, nats-py, asyncpg

### 0.5 Node.js Ortamı
- [ ] `frontend/` klasörü oluştur
- [ ] Node.js 20+ kontrol: `node --version`
- [ ] `npm create vite@latest frontend -- --template react-ts`
- [ ] Bağımlılıklar: cesium, satellite.js, tailwindcss

**Faz 0 Çıkış Kriteri:** `docker compose up` komutu hatasız çalışıyor, backend ve frontend klasörleri boş iskeletle hazır.

---

## Faz 1: Çekirdek Veri Akışı

Amaç: Simüle edilmiş telemetri üretimi ve kaydı çalışıyor.

### 1.1 Uydu Simülatörü
- [ ] `simulator/` klasörü kur
- [ ] Sahte CubeSat sınıfı yaz: battery, temperature, mode, timestamp
- [ ] Durum makinesi: beacon → deployment → nominal → safe
- [ ] Saniyede 1 telemetri paketi üretir
- [ ] AX.25 çerçevesi formatında çıktı
- [ ] NATS'a `telemetry.raw.{satellite_id}` kanalına yayınla

### 1.2 Canonical Telemetri Şeması
- [ ] Pydantic modeli: `CanonicalTelemetry`
- [ ] JSON Schema export
- [ ] Field tanımları: timestamp, satellite_id, source, params dict
- [ ] Validation testleri

### 1.3 Protokol Adaptörleri
- [ ] Base adapter interface: `ProtocolAdapter`
- [ ] AX.25 adaptör implementasyonu
- [ ] KISS adaptör iskelet
- [ ] CCSDS adaptör iskelet
- [ ] Plugin registry: yeni protokol nasıl eklenir

### 1.4 SatNOGS API Client
- [ ] `satnogs_client.py` yaz
- [ ] Stations endpoint: yakın istasyonları listele
- [ ] Observations endpoint: geçmiş gözlemleri çek
- [ ] TLE verisi sorgu
- [ ] Rate limit handling
- [ ] Retry mekanizması

### 1.5 TLE Kaynak Entegrasyonu
- [ ] Celestrak fetcher: günlük TLE güncellemesi
- [ ] TLE veritabanı şeması
- [ ] Satellite metadata tablosu
- [ ] Cron job veya asyncio background task

### 1.6 NATS JetStream Kurulumu
- [ ] Stream tanımla: `cubesat`
- [ ] Subject'ler: `telemetry.*`, `commands.*`, `events.*`
- [ ] Retention policy: 7 gün
- [ ] Consumer grupları

### 1.7 TimescaleDB Şeması
- [ ] `telemetry` hypertable (zaman serisi)
- [ ] `satellites` tablosu
- [ ] `ground_stations` tablosu
- [ ] `tle_history` tablosu
- [ ] Alembic migration kurulumu

### 1.8 Telemetri Yazar Servisi
- [ ] NATS'tan dinle
- [ ] TimescaleDB'ye batch insert
- [ ] Redis'e "last known value" yaz
- [ ] Hata toleransı: DB down → retry queue

**Faz 1 Çıkış Kriteri:** Simülatör 1 dakika çalışıyor, TimescaleDB'de 60 kayıt var, Redis'te son değer görünüyor.

---

## Faz 2: İş Mantığı

Amaç: Uydular akıllıca yönetiliyor. Yörünge hesaplanıyor, anomali tespit ediliyor, komutlar güvenli akıyor.

### 2.1 Yörünge Hesabı
- [ ] Skyfield entegrasyonu
- [ ] TLE'den yörünge propagator
- [ ] Satellite position calculator (ECI, ECEF, Lat/Lon)
- [ ] Doppler frekans hesabı
- [ ] Testler: bilinen TLE'ler ile doğrula

### 2.2 Geçiş Planlama
- [ ] Pass predictor: hangi uydu, hangi istasyonda, ne zaman
- [ ] AOS/LOS zamanları (Acquisition of Signal, Loss of Signal)
- [ ] Elevation, azimuth hesabı
- [ ] Minimum elevation filter (10° altı yok)
- [ ] 24 saat ileri plan

### 2.3 Çakışma Çözücü
- [ ] Scheduling algorithm
- [ ] Aynı istasyon + aynı zaman → öncelik kuralları
- [ ] Alternatif istasyon ataması
- [ ] Rezervasyon tablosu

### 2.4 Uydu Durum Makinesi
- [ ] State enum: BEACON, DEPLOYMENT, NOMINAL, SCIENCE, SAFE
- [ ] State transition kuralları
- [ ] Current state tespit (telemetriden)
- [ ] State history logging

### 2.5 Komut Yaşam Döngüsü
- [ ] Command model: id (UUID), type, params, priority
- [ ] Command states: PENDING → SCHEDULED → TRANSMITTING → SENT → ACKED / TIMEOUT / RETRY / DEAD
- [ ] State transitions
- [ ] Idempotency key
- [ ] Safe retry flag

### 2.6 Politika Motoru
- [ ] Rule engine: mode + command_type → allow/deny
- [ ] Policy tablosu
- [ ] Custom rules desteği
- [ ] Deny sebep mesajları

### 2.7 FDIR Monitor
- [ ] Health check: son telemetri ne zaman?
- [ ] Threshold kontrolleri (batarya %20 altı, sıcaklık kritik, vs)
- [ ] 3 geçiş boyunca telemetri yoksa → uyarı
- [ ] Auto safe mode komutu üretimi
- [ ] Operatöre notification

### 2.8 Anomali Tespiti
- [ ] Rolling window: son 20 geçişin stats'i
- [ ] Z-score hesabı
- [ ] Threshold: 2σ → uyarı, 3σ → kritik
- [ ] Parameter-specific baseline
- [ ] False positive azaltma

**Faz 2 Çıkış Kriteri:** Simülasyonda uydu safe mode'a düştüğünde FDIR devreye giriyor, yanlış komut gönderildiğinde politika motoru reddediyor.

---

## Faz 3: API ve Frontend

Amaç: Kullanıcı tarayıcıdan her şeyi görüyor ve kontrol ediyor.

### 3.1 FastAPI Kurulumu
- [ ] `backend/src/api/main.py`
- [ ] Pydantic models
- [ ] Dependency injection
- [ ] Exception handlers
- [ ] CORS konfig
- [ ] OpenAPI otomatik doc

### 3.2 Kimlik Doğrulama
- [ ] User model + PostgreSQL tablosu
- [ ] Password hashing (bcrypt)
- [ ] JWT token generation
- [ ] Login endpoint
- [ ] Token refresh
- [ ] Logout (token blacklist)

### 3.3 RBAC
- [ ] Role enum: viewer, operator, admin
- [ ] Permission matrix
- [ ] Dependency: `require_role()`
- [ ] Kritik komut için "two-admin approval"

### 3.4 Audit Log
- [ ] Append-only audit_log tablosu
- [ ] Middleware: her komutu logla
- [ ] Fields: user, action, target, timestamp, IP, result
- [ ] Audit log query endpoint

### 3.5 REST Endpoints
- [ ] `/satellites` — liste, detay, TLE
- [ ] `/telemetry` — geçmiş, son değerler
- [ ] `/passes` — geçiş tahminleri
- [ ] `/commands` — gönder, listele, iptal
- [ ] `/stations` — yer istasyonları
- [ ] `/anomalies` — tespit edilenler

### 3.6 WebSocket
- [ ] `/ws/telemetry` — canlı telemetri
- [ ] `/ws/passes` — aktif geçiş durumu
- [ ] `/ws/events` — FDIR, anomaliler
- [ ] Auth: JWT over WebSocket

### 3.7 Frontend Kurulumu
- [ ] Vite + React + TypeScript
- [ ] Tailwind CSS
- [ ] React Router
- [ ] State management: Zustand
- [ ] API client (axios + react-query)

### 3.8 Ana Dashboard
- [ ] Uydu listesi
- [ ] Her uydu için: durum, batarya, son geçiş
- [ ] Aktif uyarılar paneli
- [ ] Son olaylar feed

### 3.9 3D Orbit Görselleştirme
- [ ] CesiumJS entegrasyonu
- [ ] TLE'den uydu konumu
- [ ] Yer istasyonu marker'ları
- [ ] Geçiş trajektörü çizimi
- [ ] Canlı güncelleme (WebSocket)

### 3.10 Telemetri Paneli
- [ ] Parametre seçici
- [ ] Zaman serisi grafik (Chart.js veya ECharts)
- [ ] Anomali işaretleme
- [ ] Export CSV

### 3.11 Komut Gönderim UI
- [ ] Komut katalog
- [ ] Form validation
- [ ] Uydu modu bazlı disabled state
- [ ] Kritik komutlar için çift onay dialog
- [ ] Komut geçmişi

### 3.12 Geçiş Takvimi
- [ ] Gantt benzeri zaman çizelgesi
- [ ] Sürüklenebilir komut bloku
- [ ] Çakışma uyarısı
- [ ] Takvim ekranı 7 gün ileri

**Faz 3 Çıkış Kriteri:** Bir kullanıcı tarayıcıdan login olup simüle edilmiş uyduyu izleyebiliyor ve komut gönderebiliyor.

---

## Faz 4: Görünürlük ve Dağıtım

Amaç: Sistem production-ready, kendi kendini izliyor, otomatik deploy oluyor.

### 4.1 Prometheus Entegrasyonu
- [ ] Backend metrikleri export et: `/metrics` endpoint
- [ ] Custom metrikler: komut sayısı, anomali sayısı, geçiş sayısı
- [ ] NATS metrikleri
- [ ] PostgreSQL exporter
- [ ] Redis exporter

### 4.2 Grafana Dashboard
- [ ] Sistem sağlığı dashboard
- [ ] Uydu operasyon dashboard
- [ ] Alert rules: kritik metrik eşikleri
- [ ] Notification channel: email, Slack

### 4.3 Loki Log Aggregation
- [ ] Structured logging (JSON format)
- [ ] Log shipping (Promtail)
- [ ] Log retention policy
- [ ] Grafana'da log viewer

### 4.4 Edge Node (Offline Operasyon)
- [ ] Leaf NATS node configuration
- [ ] Local TimescaleDB
- [ ] Sync mechanism (internet geldiğinde)
- [ ] Conflict resolution

### 4.5 Kubernetes Manifests
- [ ] `deployment/k8s/` klasörü
- [ ] Backend deployment + service
- [ ] Frontend deployment + ingress
- [ ] Stateful services (DB, Redis, NATS)
- [ ] ConfigMap + Secret
- [ ] HPA (horizontal pod autoscaler)

### 4.6 CI/CD Pipeline
- [ ] GitHub Actions workflow
- [ ] Python test job (pytest)
- [ ] Frontend test job (vitest)
- [ ] Docker build + push
- [ ] Staging deployment
- [ ] Production deployment (manuel onay)

### 4.7 Helm Chart
- [ ] Helm chart yapısı
- [ ] values.yaml (configurable)
- [ ] Install doc
- [ ] Upgrade / rollback

### 4.8 Güvenlik Sıkılaştırma
- [ ] Rate limiting (nginx veya API seviyesi)
- [ ] HTTPS/TLS (cert-manager veya Traefik)
- [ ] Security headers
- [ ] Dependency vulnerability scan (Dependabot, Snyk)
- [ ] Secret rotation mekanizması

**Faz 4 Çıkış Kriteri:** Kubernetes cluster'a tek komutla deploy olabilir, Grafana'da sistem sağlığı görünür, GitHub Actions her push'ta test çalıştırır.

---

## Faz 5: Topluluk ve Olgunlaşma

Amaç: Açık kaynak proje olarak yaşayan bir topluluk oluşuyor, kullanıcı edinim başlıyor.

### 5.1 Dokümantasyon
- [ ] Getting Started rehberi
- [ ] Architecture deep dive
- [ ] API reference (otomatik OpenAPI)
- [ ] Custom protocol adapter yazma rehberi
- [ ] Troubleshooting
- [ ] FAQ
- [ ] Video tutorial

### 5.2 GitHub Public
- [ ] Repo public yap
- [ ] Apache 2.0 lisans final
- [ ] CONTRIBUTING.md
- [ ] CODE_OF_CONDUCT.md
- [ ] Issue templates
- [ ] PR template
- [ ] GitHub Discussions aç

### 5.3 Demo ve Örnekler
- [ ] Public demo instance (örn: demo.cubesat-c2.io)
- [ ] Örnek TLE'lerle önizleme
- [ ] Video: "5 dakikada kurulum"
- [ ] Video: "İlk CubeSat'ınızı bağlayın"
- [ ] Blog yazısı: proje hikayesi

### 5.4 İlk Kullanıcılar
- [ ] ODTÜ uzay kulübü demo
- [ ] İTÜ uzay kulübü demo
- [ ] Boğaziçi ile görüşme
- [ ] Plan-S / Fergani'ye sunum
- [ ] Uluslararası CubeSat toplulukları (Reddit, Discord)

### 5.5 IAC 2026 Antalya Hazırlığı
- [ ] Abstract submission (Ekim 2026 konferans)
- [ ] Poster veya oral presentation
- [ ] Demo istasyonu
- [ ] Networking hedefleri

### 5.6 Akademik Publikasyon
- [ ] Conference paper draft
- [ ] Karşılaştırma: OpenC3 vs CubeSat C2
- [ ] Case study: en az 1 gerçek deployment

### 5.7 Başarı Metrikleri
- [ ] GitHub stars: 50+ (3 ay), 200+ (6 ay)
- [ ] Fork sayısı
- [ ] Issue + PR aktivitesi
- [ ] Discord / forum üye sayısı
- [ ] Gerçek deployment sayısı

### 5.8 Kariyer Yönlendirmesi
- [ ] CV güncelle: bu proje referansı
- [ ] LinkedIn aktivitesi
- [ ] TÜBİTAK UZAY başvurusu (Aday Araştırmacı Programı)
- [ ] Plan-S / Fergani başvurusu
- [ ] ESA remote pozisyonları
- [ ] (Uzun vade) NASA / Astroscale / LeoLabs başvuru

**Faz 5 Çıkış Kriteri:** Proje canlı, kullanıcıları var, Emre'ye iş teklifleri geliyor.

---

## Paralel Devam Eden Projeler

### ISAM Protokolü (İkinci Proje)

CubeSat C2 olgunlaştıkça, **ISAM protokol projesine** de başlanacak. İkisi aynı GitHub organizasyonu altında olabilir:

- `github.com/[org]/cubesat-c2` — operasyon platformu
- `github.com/[org]/open-isam-protocol` — servicing protokol standardı

Bu ikisi farklı seviyede prestij veriyor:
- CubeSat C2 → kullanıcı tabanı ve pratik katkı
- ISAM Protokol → standart koyucu pozisyonu, NASA/Astroscale ilgisi

### Golden Dome (Ertelendi)

Uzay tabanlı tehdit tespit sistemi ileride yapılmak üzere ertelendi. NIZAM ve bu projeler olgunlaştıktan sonra gündeme alınacak.

---

## Kritik Karar Noktaları

Bu noktalarda durup strateji değerlendirilecek:

1. **Faz 1 sonrası** — Mimari karar: Edge node'u şimdi mi, sonra mı?
2. **Faz 2 sonrası** — ML tabanlı anomali eklensin mi? (şu an cevap: hayır)
3. **Faz 3 sonrası** — İlk kullanıcı testleri yapılacak — feedback'e göre rota
4. **Faz 4 sonrası** — Hangi buluta optimize edelim? (Hetzner vs AWS vs self-hosted)
5. **Faz 5 başlangıcı** — İsim seçimi (şu an isimsiz, final isim gerekli)
6. **Faz 5 ortası** — ISAM protokol projesine başlama zamanı

---

## Süre Tahmini

| Faz | Süre | Yoğunluk |
|-----|------|----------|
| Faz 0 | 1 hafta | Düşük (kurulum) |
| Faz 1 | 3-4 hafta | Yüksek (çekirdek kod) |
| Faz 2 | 4-6 hafta | Yüksek (algoritma) |
| Faz 3 | 4-6 hafta | Orta (UI iş yükü) |
| Faz 4 | 3-4 hafta | Orta (DevOps) |
| Faz 5 | Sürekli | Değişken |

**Toplam MVP:** 15-21 hafta (~4-5 ay, yoğun çalışma ile)

Bu tahmin **solo developer + part-time** varsayımıyla. NIZAM projesiyle paralel ilerlediği için realistik süre daha uzun olabilir.

---

## Notlar

- Her faz sonunda `docs/FAZ_X_RAPOR.md` dosyası yazılacak
- Git tags ile faz geçişleri işaretlenecek: `v0.1-faz1`, `v0.2-faz2`, vb.
- Commit mesajları Conventional Commits formatında: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`
- Breaking changes önceden duyurulacak
- Changelog otomatik üretilecek (conventional-changelog)
