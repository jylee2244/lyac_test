# Windows `.exe` 빌드 상세 가이드

이 시스템의 `relay` / `agent` / `console` 을 **Windows 실행파일(.exe)** 로 만드는
전 과정입니다.

> 핵심 전제: **PyInstaller 는 교차 컴파일을 하지 않습니다.** 리눅스/맥에서
> Windows `.exe` 를 만들 수 없습니다. 반드시 **Windows 머신(또는 Windows VM,
> GitHub Actions 의 windows 러너)** 에서 빌드해야 합니다. 32bit/64bit, 그리고
> 빌드한 Windows 버전 이상에서 동작하는 바이너리가 나옵니다.

---

## 0. 준비물
- Windows 10/11 (또는 Windows Server) 64비트
- 인터넷 연결(패키지 설치용)
- 이 폴더의 소스 전체:
  `protocol.py  crypto.py  relay_server.py  agent.py  console.py  run_demo.py`
  (+ 선택) `build.bat`

---

## 1. Python 설치
1. https://www.python.org/downloads/windows/ 에서 Python **3.11 또는 3.12** 64bit 설치.
   - 3.13 도 가능하지만, 배포 호환성은 3.11/3.12 가 무난합니다.
2. 설치 첫 화면에서 **"Add python.exe to PATH" 체크**.
3. 확인(명령 프롬프트 `cmd` 또는 PowerShell):
   ```bat
   py --version
   py -m pip --version
   ```

---

## 2. 의존성 설치
```bat
py -m pip install --upgrade pip
py -m pip install --upgrade pyinstaller cryptography
```
- `cryptography` : AES 전송 암호화(`crypto.py`)에 필요.
- `pyinstaller` : 실행파일 생성기.

가상환경을 쓰면 더 깔끔합니다(선택):
```bat
py -m venv venv
venv\Scripts\activate
pip install --upgrade pyinstaller cryptography
```

---

## 3. 가장 쉬운 방법 — build.bat
소스와 같은 폴더에 있는 `build.bat` 를 더블클릭하거나:
```bat
build.bat
```
→ `dist\relay.exe`, `dist\agent.exe`, `dist\console.exe`,
`dist\remote-support-demo.exe` 생성.

---

## 4. 수동 빌드(명령어 직접 실행)
폴더에서:
```bat
py -m PyInstaller --onefile --clean --noconfirm --name relay   --paths . relay_server.py
py -m PyInstaller --onefile --clean --noconfirm --name agent   --paths . agent.py
py -m PyInstaller --onefile --clean --noconfirm --name console --paths . console.py
```
옵션 설명:
| 옵션 | 뜻 |
|---|---|
| `--onefile` | 단일 .exe 로 패키징(압축 해제 후 실행, 조금 느리게 시작) |
| `--clean` | 이전 빌드 캐시 삭제 |
| `--noconfirm` | dist/build 덮어쓰기 자동 승인 |
| `--name X` | 산출물 이름 지정 |
| `--paths .` | 로컬 모듈(`protocol`,`crypto` 등) import 경로 |
| `--console` | (기본값) 콘솔 창 사용. CLI 도구이므로 이대로 둡니다 |

> `agent`/`relay`/`console` 은 **콘솔 애플리케이션**입니다. `--noconsole`
> (= `--windowed`) 는 쓰지 마세요. 입력/출력이 안 보입니다.

---

## 5. onefile vs onedir
- **onefile**(위 방법): .exe 하나. 배포 간편. 실행 시 임시폴더에 풀려 시작이
  약간 느리고, 일부 백신이 민감하게 반응할 수 있음.
- **onedir**(폴더 배포): 시작 빠르고 백신 오탐 적음. 폴더 통째로 배포.
  ```bat
  py -m PyInstaller --onedir --clean --noconfirm --name agent --paths . agent.py
  ```
  → `dist\agent\agent.exe` + 의존 파일들. 운영 배포엔 onedir 를 권장하기도 합니다.

---

## 6. 아이콘 / 버전 정보(선택)
아이콘:
```bat
py -m PyInstaller --onefile --name console --icon app.ico console.py
```
버전 리소스(파일 속성의 회사/버전 표기)는 version 파일을 만들어 지정:
```bat
py -m PyInstaller --onefile --name agent --version-file version.txt agent.py
```
`version.txt` 예시는 PyInstaller 문서의 `GrabVersion.py`/`SetVersion` 참고.

---

## 7. hidden import / 누락 모듈 대응
`cryptography` 는 네이티브 백엔드가 있어 간혹 수집이 누락될 수 있습니다.
빌드 후 실행 시 `ModuleNotFoundError` 가 나면:
```bat
py -m PyInstaller --onefile --name agent --paths . ^
   --collect-submodules cryptography ^
   --collect-binaries cryptography ^
   agent.py
```
- `--collect-submodules cryptography` : 하위 모듈 전부 포함
- `--collect-binaries cryptography` : 네이티브 DLL 포함
- 특정 모듈만 지정하려면 `--hidden-import <모듈명>`

---

## 8. .spec 파일로 반복 빌드(선택)
한 번 생성된 `agent.spec` 를 수정해 재사용할 수 있습니다:
```bat
py -m PyInstaller agent.spec
```
여러 산출물을 하나의 spec 로 묶을 수도 있습니다(멀티 EXE). 자세한 건
PyInstaller 공식 문서의 "Using Spec Files" 참고.

---

## 9. 코드 서명(선signtool)
원본 알약처럼 **코드 서명**을 붙이면 SmartScreen/백신 경고가 줄어듭니다.
인증서(.pfx)가 있다면:
```bat
signtool sign /fd SHA256 /f mycert.pfx /p <암호> ^
  /tr http://timestamp.digicert.com /td SHA256 dist\agent.exe
```
- `signtool` 은 Windows SDK 에 포함.
- 서명은 선택사항이며, 내부 테스트용이라면 없어도 동작합니다.

---

## 10. 백신 오탐(중요)
PyInstaller onefile 바이너리는 **백신이 오탐**하는 경우가 흔합니다(특히 원격
제어/프로세스/레지스트리 기능 때문에 더). 대응:
- onedir 빌드 사용, 코드 서명 적용.
- 테스트 장비에서 해당 폴더를 백신 예외로 추가.
- 사내 배포 시 관리 콘솔로 허용 목록(화이트리스트) 등록.
- 이 바이너리는 본인이 만든 테스트 도구이므로, 출처를 아는 장비에서만 실행.

---

## 11. 빌드 결과 확인/실행
```bat
cd dist
relay.exe --host 0.0.0.0 --sig-port 9500 --tun-port 9600 --psk 회사공용키
REM 새 창에서
agent.exe --agent-key %COMPUTERNAME% --relay-host <릴레이IP> --psk 회사공용키 --real-registry
REM 또 다른 창에서
console.exe --relay-host <릴레이IP> --psk 회사공용키 list-agents
console.exe --relay-host <릴레이IP> --psk 회사공용키 exec --agent %COMPUTERNAME% --command "ipconfig /all"
```

---

## 12. (대안) 리눅스에서 Windows .exe 가 필요할 때
직접 교차 컴파일은 불가하므로 다음 중 하나를 사용:
1. **GitHub Actions `windows-latest` 러너**에서 빌드(권장, 자동화).
   워크플로 예시:
   ```yaml
   name: build-windows
   on: workflow_dispatch
   jobs:
     build:
       runs-on: windows-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: '3.12' }
         - run: pip install pyinstaller cryptography
         - run: |
             pyinstaller --onefile --name relay   --paths . relay_server.py
             pyinstaller --onefile --name agent   --paths . agent.py
             pyinstaller --onefile --name console --paths . console.py
         - uses: actions/upload-artifact@v4
           with: { name: windows-exe, path: dist/*.exe }
   ```
   실행 후 Actions 아티팩트에서 `.exe` 다운로드.
2. **Windows VM**(VirtualBox/VMware/Hyper-V)에서 `build.bat` 실행.
3. Wine 로 PyInstaller 를 돌리는 방법도 있으나 불안정하여 **권장하지 않음**.

---

## 13. 요약(한눈에)
```bat
REM Windows 에서
py -m pip install --upgrade pyinstaller cryptography
py -m PyInstaller --onefile --name relay   --paths . relay_server.py
py -m PyInstaller --onefile --name agent   --paths . agent.py
py -m PyInstaller --onefile --name console --paths . console.py
REM 결과: dist\relay.exe, dist\agent.exe, dist\console.exe
```
