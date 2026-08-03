$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name PetCreator pet_creator.py
Write-Host "生成器位于 dist/PetCreator.exe"

