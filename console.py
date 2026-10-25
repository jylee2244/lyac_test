"""
console.py — 운영자(관리자) 콘솔

원본 ASMConsole 의 RemoteSupportService / RemoteSupportPcCommandService /
RemoteSupportPcPowerService + RemoteSupportExecutor 역할.

흐름:
  1) 릴레이에 콘솔로 접속
  2) REQUEST_REMOTE 로 원격지원 요청 (SetRemoteSupport/SetRemoteCommand)
  3) REMOTE_RESPONSE 로 AuthKey/Port 수신 (RemotePcCommandPushResponse)
  4) RemoteSupportExecutor: 터널에 접속해 실제 명령 수행
  5) 전원 제어는 PC_POWER (fire-and-forget)

CLI 예:
  python console.py list-agents
  python console.py exec  --agent pc-01 --command "uname -a"
  python console.py ls    --agent pc-01 --path /etc
  python console.py pull  --agent pc-01 --path /etc/hostname --out ./hostname.txt
  python console.py ps    --agent pc-01
  python console.py reg-set --agent pc-01 --name AutoRun --value calc.exe
  python console.py reg-list --agent pc-01
  python console.py power --agents pc-01,pc-02 --action reboot
  python console.py shell --agent pc-01            # 대화형 원격 셸
"""

from __future__ import annotations

import argparse
import asyncio
import base64

import protocol as P
from protocol import RemoteControlType, ResultCode, recv_frame, send_frame
from crypto import AesCipher


class Console:
    def __init__(self, admin_id, relay_host, sig_port, psk=None):
        self.admin_id = admin_id
        self.relay_host = relay_host
        self.sig_port = sig_port
        self.reader = None
        self.writer = None
        self.cipher = AesCipher(psk) if psk else None

    def log(self, *a):
        print("[console]", *a, flush=True)

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(self.relay_host, self.sig_port)
        await send_frame(self.writer, {"type": "HELLO", "role": "console",
                                       "admin_id": self.admin_id}, self.cipher)

    async def close(self):
        if self.writer:
            self.writer.close()

    async def list_agents(self):
        await send_frame(self.writer, {"type": "LIST_AGENTS"}, self.cipher)
        resp = await recv_frame(self.reader, self.cipher)
        return resp.get("agents", [])

    # -- 원격지원 요청 -> 승인 -> 터널 핸드셰이크 ----------------------------
    async def request_remote(self, agent_key, control_type,
                             confirm_message="원격 지원을 허용하시겠습니까?",
                             is_use_confirm=True):
        await send_frame(self.writer, {
            "type": "REQUEST_REMOTE", "admin_id": self.admin_id,
            "agent_key": agent_key, "control_type": int(control_type),
            "confirm_message": confirm_message, "is_use_confirm": is_use_confirm}, self.cipher)
        resp = await recv_frame(self.reader, self.cipher)
        return P.RemotePcCommandPushResponse(
            Result=resp["Result"], AuthKey=resp["AuthKey"],
            AgentKey=resp["AgentKey"], Port=resp["Port"])

    # -- RemoteSupportExecutor: 터널 접속 ------------------------------------
    async def _open_executor(self, resp: P.RemotePcCommandPushResponse):
        r, w = await asyncio.open_connection(self.relay_host, resp.Port)
        await send_frame(w, {"role": "console", "auth_key": resp.AuthKey}, self.cipher)
        ready = await recv_frame(r, self.cipher)  # TUNNEL_READY
        if not ready or ready.get("type") != "TUNNEL_READY":
            w.close()
            raise RuntimeError(f"tunnel not ready: {ready}")
        # 터널 op 는 AuthKey 파생 키로 암호화(릴레이도 못 읽음)
        tun_cipher = AesCipher(resp.AuthKey) if self.cipher else None
        return r, w, tun_cipher

    async def _one_shot(self, agent_key, control_type, op_frame, confirm_message, is_use_confirm):
        resp = await self.request_remote(agent_key, control_type,
                                         confirm_message, is_use_confirm)
        if resp.Result != ResultCode.OK:
            return {"ok": False, "denied": ResultCode(resp.Result).name}
        r, w, tc = await self._open_executor(resp)
        try:
            await send_frame(w, op_frame, tc)
            return await recv_frame(r, tc)
        finally:
            w.close()

    # -- 고수준 기능 ---------------------------------------------------------
    async def exec(self, agent_key, command, is_use_confirm=True):
        return await self._one_shot(agent_key, RemoteControlType.PcControl,
                                    {"op": "exec", "command": command},
                                    f"관리자가 명령 실행을 요청했습니다: {command}", is_use_confirm)

    async def ls(self, agent_key, path, is_use_confirm=True):
        return await self._one_shot(agent_key, RemoteControlType.FileExplorer,
                                    {"op": "list_dir", "path": path},
                                    "관리자가 파일 탐색을 요청했습니다.", is_use_confirm)

    async def pull(self, agent_key, path, out, is_use_confirm=True):
        resp = await self._one_shot(agent_key, RemoteControlType.FileExplorer,
                                    {"op": "read_file", "path": path},
                                    "관리자가 파일 다운로드를 요청했습니다.", is_use_confirm)
        if resp.get("ok"):
            with open(out, "wb") as f:
                f.write(base64.b64decode(resp["data_b64"]))
            resp["saved_to"] = out
            resp.pop("data_b64", None)
        return resp

    async def push(self, agent_key, local, remote, is_use_confirm=True):
        with open(local, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return await self._one_shot(agent_key, RemoteControlType.FileExplorer,
                                    {"op": "write_file", "path": remote, "data_b64": data},
                                    "관리자가 파일 업로드를 요청했습니다.", is_use_confirm)

    async def ps(self, agent_key, is_use_confirm=True):
        return await self._one_shot(agent_key, RemoteControlType.ProcessManagement,
                                    {"op": "list_proc"},
                                    "관리자가 프로세스 조회를 요청했습니다.", is_use_confirm)

    async def kill(self, agent_key, pid, is_use_confirm=True):
        return await self._one_shot(agent_key, RemoteControlType.ProcessManagement,
                                    {"op": "kill", "pid": pid},
                                    f"관리자가 프로세스 종료(PID {pid})를 요청했습니다.", is_use_confirm)

    async def reg(self, agent_key, op, **kw):
        frame = {"op": op, **kw}
        return await self._one_shot(agent_key, RemoteControlType.RegistryManagement,
                                    frame, "관리자가 레지스트리 접근을 요청했습니다.", kw.pop("is_use_confirm", True))

    async def power(self, agent_keys, action):
        await send_frame(self.writer, {"type": "PC_POWER", "admin_id": self.admin_id,
                                       "agent_keys": agent_keys, "action": action}, self.cipher)
        resp = await recv_frame(self.reader, self.cipher)
        return resp.get("results", [])

    # -- 그룹 명령(여러 에이전트에 동시 exec) --------------------------------
    async def exec_group(self, agent_keys, command, is_use_confirm=True):
        results = {}
        for ak in agent_keys:
            try:
                results[ak] = await self.exec(ak, command, is_use_confirm)
            except Exception as e:
                results[ak] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return results

    # -- 대화형 원격 셸 ------------------------------------------------------
    async def shell(self, agent_key, is_use_confirm=True):
        resp = await self.request_remote(agent_key, RemoteControlType.PcControl,
                                         "관리자가 원격 셸을 요청했습니다.", is_use_confirm)
        if resp.Result != ResultCode.OK:
            self.log("요청 거부됨:", ResultCode(resp.Result).name)
            return
        r, w, tc = await self._open_executor(resp)
        self.log(f"원격 셸 연결됨 → {agent_key} (빈 줄 또는 exit 로 종료)")
        try:
            while True:
                try:
                    cmd = await asyncio.get_event_loop().run_in_executor(
                        None, input, f"{agent_key}$ ")
                except EOFError:
                    break
                if not cmd.strip() or cmd.strip() == "exit":
                    break
                await send_frame(w, {"op": "exec", "command": cmd}, tc)
                out = await recv_frame(r, tc)
                if out.get("ok"):
                    if out.get("stdout"):
                        print(out["stdout"], end="")
                    if out.get("stderr"):
                        print(out["stderr"], end="")
                    print(f"(exit {out.get('exit_code')})")
                else:
                    print("오류:", out.get("error"))
        finally:
            w.close()


    # -- 대화형 그룹 관리 UI -------------------------------------------------
    async def manage(self):
        self.log("그룹 관리 콘솔 (menu). 숫자 선택, q 종료.")
        while True:
            agents = await self.list_agents()
            print("\n── 온라인 에이전트 ──")
            for i, a in enumerate(agents):
                print(f"  [{i}] {a}")
            print("── 메뉴 ──")
            print("  1) 그룹 명령 실행(exec)   2) 프로세스 조회(ps)")
            print("  3) 그룹 전원(off/reboot)  4) 디렉터리 목록(ls)")
            print("  r) 새로고침   q) 종료")
            try:
                sel = (await asyncio.get_event_loop().run_in_executor(
                    None, input, "선택> ")).strip().lower()
            except EOFError:
                break
            if sel == "q":
                break
            if sel == "r" or sel == "":
                continue
            if sel == "1":
                targets = await self._pick_agents(agents)
                cmd = await self._ask("명령: ")
                res = await self.exec_group(targets, cmd)
                for ak, r in res.items():
                    tag = "OK" if r.get("ok") else r.get("denied") or r.get("error")
                    print(f"\n### {ak} [{tag}]")
                    if r.get("ok"):
                        print((r.get("stdout", "") + r.get("stderr", "")).rstrip())
            elif sel == "2":
                ak = (await self._pick_agents(agents, single=True))[0]
                r = await self.ps(ak)
                print("\n".join(r.get("text", "").splitlines()[:15]))
            elif sel == "3":
                targets = await self._pick_agents(agents)
                act = await self._ask("off/reboot: ")
                print(await self.power(targets, act))
            elif sel == "4":
                ak = (await self._pick_agents(agents, single=True))[0]
                path = await self._ask("경로: ")
                r = await self.ls(ak, path)
                for e in r.get("entries", [])[:30]:
                    print(("[D] " if e["is_dir"] else "    ") + e["name"])

    async def _ask(self, prompt):
        return (await asyncio.get_event_loop().run_in_executor(None, input, prompt)).strip()

    async def _pick_agents(self, agents, single=False):
        raw = await self._ask("대상 인덱스(쉼표, * 전체): " if not single else "대상 인덱스: ")
        if raw == "*":
            return agents
        idxs = [int(x) for x in raw.replace(" ", "").split(",") if x != ""]
        picked = [agents[i] for i in idxs]
        return picked[:1] if single else picked


# ============================================================================
import json  # noqa: E402


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


async def _amain(args):
    c = Console(args.admin, args.relay_host, args.sig_port, psk=args.psk)
    await c.connect()
    try:
        cmd = args.cmd
        if cmd == "list-agents":
            _print(await c.list_agents())
        elif cmd == "exec":
            _print(await c.exec(args.agent, args.command, not args.no_confirm))
        elif cmd == "ls":
            _print(await c.ls(args.agent, args.path, not args.no_confirm))
        elif cmd == "pull":
            _print(await c.pull(args.agent, args.path, args.out, not args.no_confirm))
        elif cmd == "push":
            _print(await c.push(args.agent, args.local, args.remote, not args.no_confirm))
        elif cmd == "ps":
            r = await c.ps(args.agent, not args.no_confirm)
            print(r.get("text", r))
        elif cmd == "kill":
            _print(await c.kill(args.agent, args.pid, not args.no_confirm))
        elif cmd == "reg-list":
            _print(await c.reg(args.agent, "reg_list", key=args.key))
        elif cmd == "reg-get":
            _print(await c.reg(args.agent, "reg_get", key=args.key, name=args.name))
        elif cmd == "reg-set":
            _print(await c.reg(args.agent, "reg_set", key=args.key, name=args.name, value=args.value))
        elif cmd == "reg-delete":
            _print(await c.reg(args.agent, "reg_delete", key=args.key, name=args.name))
        elif cmd == "power":
            _print(await c.power(args.agents.split(","), args.action))
        elif cmd == "exec-group":
            res = await c.exec_group(args.agents.split(","), args.command, not args.no_confirm)
            _print(res)
        elif cmd == "shell":
            await c.shell(args.agent, not args.no_confirm)
        elif cmd == "manage":
            await c.manage()
    finally:
        await c.close()


def main():
    ap = argparse.ArgumentParser(description="Remote Support 테스트 콘솔")
    ap.add_argument("--admin", default="admin")
    ap.add_argument("--relay-host", default="127.0.0.1")
    ap.add_argument("--sig-port", type=int, default=9500)
    ap.add_argument("--no-confirm", action="store_true", help="에이전트 동의창 끄기(IsUseConfirm=false)")
    ap.add_argument("--psk", default=None, help="사전공유키(AES 전송 암호화). 릴레이/에이전트와 동일해야 함")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-agents")
    DEFAULT_KEY = "HKLM\\SOFTWARE\\RemoteSupportTest"

    p = sub.add_parser("exec"); p.add_argument("--agent", required=True); p.add_argument("--command", required=True)
    p = sub.add_parser("ls"); p.add_argument("--agent", required=True); p.add_argument("--path", default=".")
    p = sub.add_parser("pull"); p.add_argument("--agent", required=True); p.add_argument("--path", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("push"); p.add_argument("--agent", required=True); p.add_argument("--local", required=True); p.add_argument("--remote", required=True)
    p = sub.add_parser("ps"); p.add_argument("--agent", required=True)
    p = sub.add_parser("kill"); p.add_argument("--agent", required=True); p.add_argument("--pid", type=int, required=True)
    p = sub.add_parser("reg-list"); p.add_argument("--agent", required=True); p.add_argument("--key", default=DEFAULT_KEY)
    p = sub.add_parser("reg-get"); p.add_argument("--agent", required=True); p.add_argument("--key", default=DEFAULT_KEY); p.add_argument("--name", required=True)
    p = sub.add_parser("reg-set"); p.add_argument("--agent", required=True); p.add_argument("--key", default=DEFAULT_KEY); p.add_argument("--name", required=True); p.add_argument("--value", required=True)
    p = sub.add_parser("reg-delete"); p.add_argument("--agent", required=True); p.add_argument("--key", default=DEFAULT_KEY); p.add_argument("--name", required=True)
    p = sub.add_parser("power"); p.add_argument("--agents", required=True, help="쉼표구분"); p.add_argument("--action", choices=["off", "reboot"], required=True)
    p = sub.add_parser("exec-group"); p.add_argument("--agents", required=True, help="쉼표구분"); p.add_argument("--command", required=True)
    p = sub.add_parser("shell"); p.add_argument("--agent", required=True)
    sub.add_parser("manage")

    args = ap.parse_args()
    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
