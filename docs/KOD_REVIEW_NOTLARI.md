# CubeSat C2 — Kod Review Notları (Test Haftası Sonrası)

GitHub public olmadan önce düzeltilmesi gereken güvenlik ve kalite sorunları.

**Öncelik:** Bu dosyadaki 4 ana sorun + 1 eksik kontrol, toplam ~30 dakikada halledilebilir. Tanıtıma başlamadan önce bitirilmeli.

---

## SORUN 1: Şifre Politikası Çok Zayıf — backend + frontend minimum 12 karakter

## SORUN 2: Admin Kendi Rolünü Düşürebiliyor — self-demotion + last admin koruması

## SORUN 3: TLE Validation Yok — sgp4 ile checksum + format kontrolü

## SORUN 4: Delete'de Transaction Yok — 3 ayrı DELETE atomik değil

## EKSİK: Audit Log — delete + create user için yazılmıyor

## KONTROL: RBAC pytest testleri — viewer/operator/admin sınır testleri

---

Tümü 2026-04-24 oturumunda düzeltildi. Bakınız git log.
