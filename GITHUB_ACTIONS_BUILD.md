# GitHub Actions 로 Windows .exe 빌드하기 (로컬 설치 불필요)

이 PC에 Python/PyInstaller 를 설치하지 않고, GitHub 의 Windows 서버가 대신
`relay.exe` / `agent.exe` / `console.exe` 를 빌드하게 하는 방법입니다.
웹 브라우저만 있으면 됩니다. (git 설치도 필요 없음)

준비물: GitHub 계정 1개 (없으면 https://github.com 에서 무료 가입)

---

## 1. 새 저장소(repository) 만들기
1. https://github.com 로그인 → 오른쪽 위 **＋** → **New repository**
2. Repository name: 예) `remote-support-test`
3. **Private** 선택(권장) → **Create repository**

## 2. 소스 업로드
> 제가 드린 `remote_support_test_github.zip` 을 먼저 **압축 해제**해 두세요.
> 그 안에는 소스와 함께 `.github/workflows/build-windows.yml` 이 들어 있습니다.

1. 방금 만든 저장소 페이지에서 **Add file ▾ → Upload files** 클릭
2. 압축을 푼 폴더 안의 **모든 항목을 드래그&드롭**
   - 반드시 **`.github` 폴더도 함께** 올라가야 합니다(그 안에 빌드 설정이 있음).
   - 파일 목록에 `.github/workflows/build-windows.yml` 이 보이는지 확인.
3. 아래 **Commit changes** 클릭

## 3. 빌드 자동 실행 확인
- 업로드(커밋)되는 순간 빌드가 자동으로 시작됩니다.
- 저장소 상단 **Actions** 탭 클릭 → **build-windows** 실행 항목 클릭
- 녹색 체크(✓)가 뜨면 성공(보통 2~4분). 노란 점은 진행 중.
- (자동으로 안 돌면) Actions 탭 → 왼쪽 **build-windows** → 오른쪽 **Run workflow** → **Run workflow** 버튼으로 수동 실행.

## 4. 완성된 .exe 내려받기
1. 성공한 실행 항목(초록 체크) 클릭
2. 페이지 맨 아래 **Artifacts** 섹션의 **`windows-exe`** 클릭 → zip 다운로드
3. 압축 풀면 `relay.exe`, `agent.exe`, `console.exe` 가 들어 있습니다.

## 5. 실행 (대상/관리자 PC에서)
```bat
REM 릴레이 서버
relay.exe --host 0.0.0.0 --sig-port 9500 --tun-port 9600 --psk 회사공용키
REM 에이전트(대상 PC)
agent.exe --agent-key %COMPUTERNAME% --relay-host <릴레이IP> --psk 회사공용키 --real-registry
REM 콘솔(관리자 PC)
console.exe --relay-host <릴레이IP> --psk 회사공용키 list-agents
console.exe --relay-host <릴레이IP> --psk 회사공용키 exec --agent <대상> --command "ipconfig /all"
```
> 자세한 운영법은 `INSTALL_AND_OPERATIONS.md` 참고.

---

## 참고 / 문제 해결
- **Actions 가 안 보이거나 비활성**: 저장소 → Settings → Actions → General →
  "Allow all actions" 허용으로 설정.
- **빌드 실패(빨간 X)**: 해당 실행 로그를 열어 어느 단계에서 멈췄는지 확인.
  대부분 업로드 때 `.github/workflows/build-windows.yml` 이 누락된 경우입니다.
- **.exe 가 Windows Defender/백신에 걸림**: PyInstaller onefile 특성상 오탐이
  잦습니다. 테스트 장비에서 예외 처리하거나, 코드 서명을 적용하세요
  (`WINDOWS_BUILD.md` 9·10장).
- **빌드된 exe 는 64비트 Windows 용**입니다.
