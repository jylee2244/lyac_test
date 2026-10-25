"""
run_demo.py — 전체 흐름 end-to-end 데모 (단일 프로세스)

릴레이 + 에이전트 3대 + 콘솔을 한 이벤트 루프에서 띄워
요청→Push→동의→릴레이 터널→실행 전체 경로를 자동 검증한다.

에이전트:
  pc-01 : 모든 권한 허용, 자동 동의          (정상 경로)
  pc-02 : RegistryManagement 만 정책 거부     (DENIED_BY_POLICY)
  pc-03 : 모든 권한 허용, 항상 동의 거부      (DENIED_BY_USER)
"""

from __future__ import annotations

import asyncio

import protocol as P
from agent import Agent
from console import Console
from protocol import RemoteControlType as RT
from relay_server import RelayServer

import os
HOST, SIG, TUN = "127.0.0.1", 9500, 9600
PSK = os.environ.get("DEMO_PSK")  # 설정 시 AES 전송 암호화로 데모 실행


def banner(t):
    print("\n" + "=" * 70 + f"\n  {t}\n" + "=" * 70, flush=True)


async def main():
    if PSK:
        print(f"[demo] AES 전송 암호화 활성(PSK len={len(PSK)})")
    relay = RelayServer(HOST, SIG, TUN, psk=PSK)
    relay_task = asyncio.create_task(relay.serve())
    await asyncio.sleep(0.3)

    # pc-02: 레지스트리 권한만 끔
    perm_no_reg = P.RemoteCommandPermission.allow_all()
    perm_no_reg.RemoteControlType[int(RT.RegistryManagement)] = False

    agents = [
        Agent("pc-01", HOST, SIG, psk=PSK),
        Agent("pc-02", HOST, SIG, permission=perm_no_reg, psk=PSK),
        Agent("pc-03", HOST, SIG, force_decline=True, psk=PSK),
    ]
    for a in agents:
        asyncio.create_task(a.run())
    await asyncio.sleep(0.5)

    c = Console("admin-01", HOST, SIG, psk=PSK)
    await c.connect()

    banner("1) 온라인 에이전트 목록")
    print(await c.list_agents())

    banner("2) PcControl: 원격 셸 명령 실행 (pc-01)")
    print(await c.exec("pc-01", "echo hello from $(hostname); uname -s -m"))

    banner("3) FileExplorer: 디렉터리 목록 (pc-01, /etc 일부)")
    r = await c.ls("pc-01", "/etc")
    print("path:", r.get("path"), "| entries:", len(r.get("entries", [])))
    for e in r.get("entries", [])[:5]:
        print("  ", e)

    banner("4) FileExplorer: 파일 다운로드 pull (pc-01 /etc/hostname)")
    print(await c.pull("pc-01", "/etc/hostname", "/tmp/pulled_hostname.txt"))

    banner("5) ProcessManagement: 프로세스 목록 (pc-01, 상위 몇 줄)")
    r = await c.ps("pc-01")
    print("\n".join(r.get("text", "").splitlines()[:6]))

    banner("6) RegistryManagement: set -> list (pc-01, 시뮬레이션)")
    print(await c.reg("pc-01", "reg_set", key="HKLM\\SOFTWARE\\RemoteSupportTest",
                      name="AutoRun", value="calc.exe"))
    print(await c.reg("pc-01", "reg_list", key="HKLM\\SOFTWARE\\RemoteSupportTest"))

    banner("7) 정책 거부: pc-02 는 RegistryManagement 불가 (DENIED_BY_POLICY)")
    print(await c.reg("pc-02", "reg_list"))

    banner("8) 사용자 거부: pc-03 는 동의 거부 (DENIED_BY_USER)")
    print(await c.exec("pc-03", "echo should-not-run"))

    banner("9) 오프라인 에이전트 요청 (AGENT_OFFLINE)")
    print(await c.exec("pc-99", "echo nope"))

    banner("10) 전원 제어(그룹, reboot) — pc-01·pc-02 (시뮬레이션)")
    print(await c.power(["pc-01", "pc-02"], "reboot"))

    await c.close()
    banner("데모 완료")
    relay_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
