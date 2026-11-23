@echo off

python -m pip install -r requirements.txt pyinstaller || goto :errore

cargo build --release --manifest-path tools\merge\Cargo.toml || goto :errore

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name Karl ^
  --hidden-import PIL.ImageTk --hidden-import PIL._imagingtk --hidden-import PIL._tkinter_finder ^
  --collect-all tkinterdnd2 --collect-submodules pystray ^
  --icon karl\assets\icon.ico ^
  --add-data "karl\assets;karl\assets" ^
  --add-data "karl\locales;karl\locales" ^
  --add-binary "tools\merge\target\release\karl-merge.exe;karl\bin" ^
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
