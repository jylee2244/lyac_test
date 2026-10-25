# Remote Support 테스트 시스템

ASMConsole.exe(알약 중앙관리 콘솔)의 **RemoteSupportService** 계층을 정적 분석해
추출한 구조를 그대로 본떠 만든 **독립 실행 테스트용** 구현입니다.

> ⚠️ 알약 실제 서버 / `SantaNetwork` 와이어 프로토콜에 **연결되지 않습니다.**
> 같은 아키텍처(요청→Push→동의→릴레이 터널→실행)를 자체 프로토콜로 재현한
> 학습·테스트 하네스입니다. 본인이 관리하는 환경에서만 사용하세요.

## 구성 요소

| 파일 | 역할 | 원본 대응 |
|---|---|---|
| `protocol.py` | 메시지/VO/열거형/프레이밍 | `RemoteControlType`, `ConfirmRemoteSupportRequest`, `RemotePcCommand*`, `ConfigRemoteCommandPermission` 등 |
| `relay_server.py` | 시그널링 + 릴레이(터널) 브로커 | 서버 + Pusher/PusherMap + `GetRemoteRelayServerInfo` |
| `agent.py` | 피제어 엔드포인트 | 알약 에이전트(동의/권한/명령 수행) |
| `console.py` | 운영자 콘솔 + 실행기 | `RemoteSupportService` + `RemoteSupportExecutor` |
| `run_demo.py` | 전체 흐름 자동 데모 | — |
| `build.sh` | PyInstaller 실행파일 빌드 | — |

## 동작 흐름

```
Console ──REQUEST_REMOTE──▶ Relay ──PUSH_CONFIRM──▶ Agent
                              │                         │ (권한 HasPermission 체크 + 사용자 동의)
Console ◀─REMOTE_RESPONSE──  │ ◀──CONFIRM_RESULT───────┘
 (AuthKey, Port)              │
       │                      ├──OPEN_TUNNEL──▶ Agent
       ▼                      ▼                   ▼
   터널 접속(AuthKey) ──▶ Relay 가 양쪽 매칭 후 바이트 중계 ◀── 터널 접속(AuthKey)
       │                                              │
       └────── op(exec/list_dir/…) ───▶  Agent 가 수행하고 결과 반환
```

- **권한 게이트**: `ConfigRemoteCommandPermission.HasPermission(type)` → 정책상 막힌 유형은 `DENIED_BY_POLICY`
- **동의**: `ConfirmRemoteSupportRequest{IsUseConfirm, ConfirmMessage}` → 거부 시 `DENIED_BY_USER`
- **AuthKey 핸드셰이크**: 승인된 세션만 1회용 토큰으로 터널 매칭
- **전원 제어**: `PC_POWER`(단건/그룹) fire-and-forget → `RemotePcPowerPushResponse`

## 제어 유형별 실제 기능 (RemoteControlType)

| 유형 | 기능 | 콘솔 명령 |
|---|---|---|
| `PcControl` | 셸 명령 실행 | `exec`, `shell`(대화형) |
| `FileExplorer` | 목록/다운로드/업로드/stat | `ls`, `pull`, `push` |
| `ProcessManagement` | 프로세스 목록/종료 | `ps`, `kill` |
| `RegistryManagement` | 레지스트리 CRUD(※) | `reg-list/get/set/delete` |
| `PcPowerOff` / `PcPowerReboot` | 전원 제어(시뮬레이션) | `power --action off\|reboot` |

※ 레지스트리는 모든 OS에서 JSON 가상 하이브로 **시뮬레이션**합니다
(`fake_registry_<agent>.json`). Windows 실제 레지스트리를 쓰려면 `agent.py`의
`_reg_op`를 `winreg`로 교체하세요.

## 실행 방법

### 소스로 실행
```bash
# 1) 릴레이
python3 relay_server.py --host 127.0.0.1 --sig-port 9500 --tun-port 9600
# 2) 에이전트(피제어 PC 역할)
python3 agent.py --agent-key pc-01 --relay-host 127.0.0.1 --sig-port 9500
# 3) 콘솔(관리자)
python3 console.py exec  --agent pc-01 --command "uname -a"
python3 console.py ls    --agent pc-01 --path /etc
python3 console.py ps    --agent pc-01
python3 console.py reg-set --agent pc-01 --name AutoRun --value calc.exe
python3 console.py power  --agents pc-01 --action reboot
python3 console.py shell  --agent pc-01        # 대화형 원격 셸
```

### 전체 흐름 한 번에
```bash
python3 run_demo.py
```

### 실행파일 빌드
```bash
bash build.sh        # dist/relay, dist/agent, dist/console, dist/remote-support-demo
./dist/relay  --sig-port 9700 --tun-port 9800 &
./dist/agent  --agent-key box-7 --sig-port 9700 &
./dist/console --sig-port 9700 exec --agent box-7 --command "id"
```
- 이 컨테이너는 Linux 라 `dist/` 는 **Linux ELF** 입니다.
- **Windows `.exe`** 가 필요하면 동일 소스를 Windows에서 `build.sh`(또는 같은
  `pyinstaller --onefile` 명령)로 빌드하세요. PyInstaller는 교차 컴파일을 하지
  않으므로 대상 OS에서 빌드해야 합니다.

## 주요 옵션
- `agent.py --manual-confirm` : 자동 승인 대신 대화형 y/N 동의
- `agent.py --allow-real-power` : 전원 명령 실제 실행(기본 시뮬레이션, 주의)
- `console.py --no-confirm` : `IsUseConfirm=false` 로 동의창 생략 요청
- 권한을 코드로 조절: `protocol.RemoteCommandPermission` (데모의 pc-02 참고)

## 추가 기능
- **전송 AES 암호화**: 모든 노드에 `--psk <키>` 동일 지정 시 시그널링은 PSK로,
  터널 op 는 세션 AuthKey 파생키로 암호화(릴레이도 못 읽는 E2E). `crypto.py`.
- **그룹 관리**: `console exec-group --agents a,b,c --command ...`, 대화형
  `console manage` 메뉴(다중 선택/전체 `*`).
- **Windows 실제 레지스트리**: 에이전트 `--real-registry` (winreg). 미지정 시 시뮬레이션.
- 설치·운영 상세는 **INSTALL_AND_OPERATIONS.md** 참고.

## 원본과의 차이(의도된 단순화)
- 전송 구간 암호화 없음(원본은 AES/ARIA). 테스트 가독성을 위해 평문 JSON.
- 서버 인증/세션/계정 체계 없음. AuthKey는 터널 매칭용 1회 토큰만.
- 전원 제어는 기본 시뮬레이션(컨테이너 보호).
