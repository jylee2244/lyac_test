#!/usr/bin/env bash
# PyInstaller 로 단일 실행파일 빌드.
#   - 이 스크립트를 실행한 OS용 바이너리만 생성됩니다.
#     Linux 에서 돌리면 Linux ELF, Windows 에서 돌리면 .exe 가 나옵니다.
#   - Windows .exe 가 필요하면 Windows 에서 동일하게 실행하세요:
#       py -m pip install pyinstaller && build (아래 pyinstaller 명령 동일)
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --quiet --upgrade pyinstaller

build() {  # $1 = entry script, $2 = output name
  pyinstaller --onefile --clean --noconfirm \
    --name "$2" \
    --paths . \
    "$1"
}

build relay_server.py relay
build agent.py        agent
build console.py      console
build run_demo.py     remote-support-demo

echo
echo "빌드 완료. 실행파일: $(pwd)/dist/"
ls -la dist/
