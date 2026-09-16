# Royal Price Monitor

Royal.az məhsullarını GitHub Actions ilə avtomatik izləyən monitor.

## İzlənən dəyişikliklər
- Qiymət dəyişiklikləri
- Endirim / compare-at qiyməti dəyişiklikləri
- Məhsulun stokda olub-olmaması
- Yeni məhsullar
- Kataloqdan çıxarılan məhsullar

Monitor GitHub Actions tərəfindən təxminən hər 5 dəqiqədən bir işə düşür və dəyişiklik olduqda Telegram bildirişi göndərir.

## Təhlükəsizlik
Telegram bot tokeni və chat ID kodda saxlanılmır. Bunlar GitHub Actions Secrets kimi əlavə edilməlidir:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

İlk işə düşmədə cari kataloq baza kimi saxlanılır və kütləvi dəyişiklik bildirişi göndərilmir.
