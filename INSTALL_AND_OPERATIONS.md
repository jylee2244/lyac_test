# Remote Support 테스트 시스템 — 설치 및 운영 매뉴얼

ASMConsole(알약 중앙관리 콘솔)의 RemoteSupportService 구조를 본뜬 독립 테스트
시스템의 설치·구성·운영 안내서입니다.

> ⚠️ 본 시스템은 **학습/테스트용**입니다. 알약 실제 서버/`SantaNetwork`
> 프로토콜과 연결되지 않습니다. 반드시 **본인이 소유·관리하는 장비**에서만,
> 격리된 테스트망에서 사용하세요. `exec`/파일/프로세스/레지스트리 기능은 대상
> 장비에 실제로 영향을 줍니다.

---

## 1. 구성 개요

세 가지 역할 + 공용 모듈로 구성됩니다.

| 구성요소 | 설명 | 설치 위치(권장) |
|---|---|---|
| **relay** | 시그널링 + 릴레이(터널) 브로커 | 콘솔·에이전트가 모두 접근 가능한 중앙 서버 |
| **agent** | 피제어 엔드포인트. 상주하며 명령 수행 | 관리 대상 PC마다 1개 |
| **console** | 운영자(관리자) 제어 도구 | 관리자 PC |
| protocol/crypto | 공용 메시지·암호화 모듈 | (실행파일에 내장됨) |

기본 포트: 시그널링 **9500/TCP**, 터널 **9600/TCP**.

통신 흐름: `콘솔 → (요청) → 릴레이 → (Push) → 에이전트 → (동의) → 릴레이가
AuthKey로 터널 매칭 → 콘솔↔에이전트 터널로 명령/결과 전달`.

---

## 2. 사전 요구사항

### 2-A. 실행파일로 운영 (권장, 런타임 불필요)
- 추가 설치 없이 `dist/` 의 바이너리만 배포하면 됩니다.
- 이 저장소의 `dist/` 는 **Linux x86-64 ELF** 입니다.
- **Windows .exe** 가 필요하면 아래 "6. 실행파일 빌드"를 Windows에서 수행하세요.

### 2-B. 소스로 운영
- Python **3.10+** (개발·검증은 3.13)
- 패키지: `cryptography` (AES 전송 암호화용)
  ```bash
  python3 -m pip install cryptography
  ```
- 그 외는 모두 표준 라이브러리.

### 2-C. 네트워크/방화벽
- 릴레이 서버의 **9500, 9600/TCP** 인바운드 허용.
- 콘솔·에이전트에서 릴레이로 아웃바운드 허용.

---

## 3. 설치

### 3-1. 실행파일 배포
```text
릴레이 서버 : relay          복사
대상 PC     : agent          복사
관리자 PC   : console        복사
```
Linux에서는 실행권한 부여:
```bash
chmod +x relay agent console
```

### 3-2. 소스 배포
`*.py` 전체를 같은 폴더에 두고 각 호스트에 복사한 뒤 2-B의 의존성을 설치합니다.

---

## 4. 구성(설정값)

본 시스템은 설정파일 없이 **명령행 인자**로 구성합니다.

| 항목 | 인자 | 기본값 | 적용 대상 |
|---|---|---|---|
| 릴레이 바인드 주소 | `--host` | 127.0.0.1 | relay |
| 시그널링 포트 | `--sig-port` | 9500 | 전부 |
| 터널 포트 | `--tun-port` | 9600 | relay |
| 릴레이 접속 주소 | `--relay-host` | 127.0.0.1 | agent, console |
| 전송 암호화 키 | `--psk` | 없음(평문) | **전부 동일값** |
| 에이전트 식별자 | `--agent-key` | (필수) | agent |
| 동의 방식 | `--manual-confirm` | 자동승인 | agent |
| 관리자 ID | `--admin` | admin | console |
| 동의창 생략요청 | `--no-confirm` | 끔 | console |

> **PSK 규칙**: `--psk` 를 쓰면 relay·agent·console **모두** 같은 값을 써야
> 합니다. 값이 다르거나 한쪽만 평문이면 접속이 거부됩니다(정상 동작).
> 외부망 테스트 시 PSK 사용을 권장합니다.

---

## 5. 운영

### 5-1. 기동 순서
항상 **릴레이 → 에이전트 → 콘솔** 순으로 띄웁니다.

**(1) 릴레이 서버** (중앙 서버에서)
```bash
# 외부에서 접속 받으려면 0.0.0.0 바인드
./relay --host 0.0.0.0 --sig-port 9500 --tun-port 9600 --psk "회사공용키"
```

**(2) 에이전트** (대상 PC마다)
```bash
./agent --agent-key pc-01 --relay-host <릴레이IP> --sig-port 9500 --psk "회사공용키"
```
- `--manual-confirm` : 원격 요청 시 대상 PC에서 y/N 로 직접 승인
- `--real-registry` : (Windows 전용) 실제 레지스트리(winreg) 사용. 미지정 시 시뮬레이션
- `--allow-real-power` : 전원 명령 실제 실행(기본 시뮬레이션, 주의)

**(3) 콘솔** (관리자 PC에서, 작업할 때마다 실행)
```bash
./console --relay-host <릴레이IP> --sig-port 9500 --psk "회사공용키" list-agents
```

### 5-2. 콘솔 명령 레퍼런스

| 명령 | 설명 | 제어유형 |
|---|---|---|
| `list-agents` | 온라인 에이전트 목록 | — |
| `exec --agent A --command "..."` | 원격 셸 명령 1회 | PcControl |
| `shell --agent A` | 대화형 원격 셸 | PcControl |
| `ls --agent A --path /etc` | 디렉터리 목록 | FileExplorer |
| `pull --agent A --path <원격> --out <로컬>` | 파일 다운로드 | FileExplorer |
| `push --agent A --local <로컬> --remote <원격>` | 파일 업로드 | FileExplorer |
| `ps --agent A` | 프로세스 목록 | ProcessManagement |
| `kill --agent A --pid <PID>` | 프로세스 종료 | ProcessManagement |
| `reg-list/get/set/delete --agent A ...` | 레지스트리 CRUD | RegistryManagement |
| `power --agents A,B --action off\|reboot` | 전원 제어(그룹) | PcPowerOff/Reboot |
| `exec-group --agents A,B --command "..."` | 여러 대 동시 명령 | PcControl |
| `manage` | 대화형 그룹 관리 메뉴 | 혼합 |

**사용 예**
```bash
# 단일 명령
./console --psk K exec --agent pc-01 --command "ipconfig /all"
# 파일 받기
./console --psk K pull --agent pc-01 --path "C:\\logs\\app.log" --out ./app.log
# 레지스트리 조회(Windows 실제값: 에이전트가 --real-registry 로 떠 있어야 함)
./console --psk K reg-get --agent pc-01 --key "HKCU\\Environment" --name TEMP
# 그룹 재부팅
./console --psk K power --agents pc-01,pc-02,pc-03 --action reboot
# 여러 대에 같은 점검 명령
./console --psk K exec-group --agents pc-01,pc-02 --command "systeminfo | find \"OS\""
# 대화형 그룹 관리 콘솔
./console --psk K manage
```

### 5-3. 대화형 그룹 관리(`manage`)
실행하면 온라인 에이전트 목록과 메뉴가 뜹니다.
- `1` 그룹 명령 실행 → 대상 인덱스(예: `0,2` 또는 `*` 전체) → 명령 입력
- `2` 프로세스 조회 / `3` 그룹 전원 / `4` 디렉터리 목록
- `r` 새로고침 / `q` 종료

### 5-4. 동의/권한 운영
- **동의**: 에이전트가 `--manual-confirm` 이면 대상 사용자가 수락해야 세션이
  열립니다. 거부 시 콘솔에 `DENIED_BY_USER`.
- **권한(정책)**: 에이전트별 허용 제어유형은 코드(`RemoteCommandPermission`)로
  제어합니다. 막힌 유형은 `DENIED_BY_POLICY`.
  (운영 중 정책을 외부 파일로 바꾸고 싶다면 "8. 커스터마이즈" 참고)

### 5-5. 반환 코드(결과 해석)
| 코드 | 의미 | 조치 |
|---|---|---|
| `OK` | 정상 | — |
| `DENIED_BY_USER` | 대상 사용자가 동의 거부 | 사용자 확인 |
| `DENIED_BY_POLICY` | 정책상 불가 유형 | 권한 설정 확인 |
| `AGENT_OFFLINE` | 대상 미접속 | 에이전트 기동/네트워크 확인 |
| `TIMEOUT` | 동의/응답 시간 초과 | 재시도 |

---

## 6. 실행파일 빌드

대상 OS에서 아래를 실행하면 단일 실행파일이 `dist/` 에 생성됩니다.
PyInstaller는 **교차 컴파일을 하지 않으므로** .exe 는 Windows에서 빌드해야 합니다.

**Linux/macOS**
```bash
bash build.sh
```
**Windows (PowerShell)**
```powershell
py -m pip install pyinstaller cryptography
pyinstaller --onefile --clean --name relay   --paths . relay_server.py
pyinstaller --onefile --clean --name agent   --paths . agent.py
pyinstaller --onefile --clean --name console --paths . console.py
```
산출물: `dist\relay.exe`, `dist\agent.exe`, `dist\console.exe`

---

## 7. 자동 시작(상주) 설정

### 7-A. Windows (에이전트를 서비스처럼)
- 작업 스케줄러 → "컴퓨터 시작 시" 트리거 → 동작:
  `C:\ASM\agent.exe --agent-key %COMPUTERNAME% --relay-host <IP> --psk <키> --real-registry`
- 또는 NSSM 등으로 서비스 등록.

### 7-B. Linux (systemd, 릴레이/에이전트)
```ini
# /etc/systemd/system/rs-relay.service
[Unit]
Description=Remote Support Relay
After=network.target
[Service]
ExecStart=/opt/rs/relay --host 0.0.0.0 --sig-port 9500 --tun-port 9600 --psk 회사공용키
Restart=always
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now rs-relay
```

---

## 8. 커스터마이즈(운영 확장 포인트)
- **에이전트 권한을 외부에서 제어**: `agent.py` 의 `Agent.__init__` 에서
  `permission` 을 파일/환경변수로 읽도록 확장(현재는 기본 전체허용, 데모만 제한).
- **실제 레지스트리**: 에이전트를 Windows에서 `--real-registry` 로 기동.
- **실제 전원 제어**: `--allow-real-power` (주의, 기본 비활성).
- **감사 로그**: 각 구성요소는 stdout 로 로그를 남깁니다. 서비스로 돌릴 때
  journald/파일로 리다이렉트하여 보관하세요.

---

## 9. 문제 해결(FAQ)

| 증상 | 원인/조치 |
|---|---|
| 콘솔에서 `AttributeError ... NoneType` 또는 즉시 끊김 | PSK 불일치(한쪽만 암호화). 모든 노드 `--psk` 값 일치시키기 |
| `list-agents` 가 비어 있음 | 에이전트 미기동 또는 `--relay-host`/포트 불일치 |
| `AGENT_OFFLINE` | 대상 `--agent-key` 오타 또는 에이전트 종료됨 |
| 연결은 되는데 명령이 `DENIED_BY_POLICY` | 해당 제어유형이 정책상 꺼짐 |
| Windows 레지스트리 값이 "simulated": true | 에이전트를 `--real-registry` 로 기동하지 않음 |
| 방화벽 환경에서 접속 안 됨 | 릴레이 9500/9600 인바운드 허용 확인 |

---

## 10. 보안 주의
- `exec`/`shell` 은 대상에서 임의 명령을 실행합니다. 접근 통제·감사 로그·동의
  절차를 반드시 운영 정책에 포함하세요.
- 외부 구간에서는 `--psk` 를 사용하고, 가능하면 VPN/사설망 위에서 운영하세요.
- 본 구현의 AES 전송 암호화는 구조 재현용입니다. 실제 제품 수준 보안이
  필요하면 TLS(mTLS) 적용을 권장합니다.
```
