@echo off

python -m pip install -r requirements.txt pyinstaller || goto :errore

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name Karl ^
  --hidden-import PIL.ImageTk --hidden-import PIL._imagingtk --hidden-import PIL._tkinter_finder ^
  --icon karl\assets\icon.ico ^
  --add-data "karl\assets;karl\assets" ^
  --add-data "karl\locales;karl\locales" ^
  --add-data "karl\loader;karl\loader" ^
  main.py || goto :errore

echo.
echo Fatto: dist\Karl.exe
pause
exit /b 0

:errore
echo.
echo Qualcosa e' andato storto, guarda i messaggi sopra.
pause
exit /b 1
