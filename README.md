# autopost — публикация видео в TikTok по расписанию

Сайт: https://oumountoumov-afk.github.io/autopost/

## Как добавить видео
1. Откройте папку `queue/` → **Add file → Upload files**.
2. Загрузите видео (`.mp4`, до 25 МБ через браузер) и, по желанию, текстовый файл с подписью **с тем же именем**:
   - `001.mp4` + `001.txt` (подпись и хэштеги)
   - `002.mp4` + `002.txt`
3. Видео публикуются по порядку имён. После публикации они переезжают в `posted/`.

### Большие видео (больше 25 МБ) — через релиз
1. Справа на главной странице репозитория: **Releases → Create a new release**
   (в первый раз: **Choose a tag** → введите `queue` → **Create new tag**; Release title: `queue`) → **Publish release**.
2. Дальше: **Releases → queue → ✏️ Edit** → перетащите файлы в поле **Attach binaries** → **Update release**.
3. Файлы называйте латиницей и цифрами (`004.mp4` + `004.txt`): GitHub портит русские имена в релизах.
4. Лимит — 2 ГБ на файл. После публикации файл удаляется из релиза, запись появляется в `posted/released.log`.

Видео из папки `queue/` и из релиза идут в общей очереди по алфавиту имён.

## Расписание
Файл `.github/workflows/publish.yml`, строки `cron` (время в UTC; сейчас 12:00 и 18:00 по Москве).
Запустить вручную: вкладка **Actions → Publish to TikTok → Run workflow**.

## Настройки
`config.json`: `privacy_level` (`PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR`, `SELF_ONLY`), отключение комментариев/дуэтов/стежков.

## Секреты (Settings → Secrets and variables → Actions)
- `TIKTOK_CLIENT_KEY`
- `TIKTOK_CLIENT_SECRET`

Токен TikTok хранится в `state/token.enc` в зашифрованном виде.
