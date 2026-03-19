@echo off
git add .
git commit -m "Fix: hanging on empty folders and improved UI navigation"
git push origin main
echo ✅ Изменения отправлены в GitHub! Если настроен авто-деплой, бот скоро обновится.
pause
