"""
agent.py — 엔드포인트(피제어) 에이전트

원본 알약 에이전트가 콘솔의 원격지원 요청을 받아 처리하는 역할을 본뜸.
  - 릴레이 시그널링에 상주 등록(REGISTER, 권한 포함)
  - PUSH_CONFIRM 수신 -> 사용자 동의(자동/대화형)
  - OPEN_TUNNEL 수신 -> 릴레이 터널에 접속해 제어 서비스 제공
  - PUSH_POWER 수신 -> 전원 제어(테스트 환경에서는 시뮬레이션)

제어 유형별 실제 기능:
  PcControl          : 셸 명령 실행(exec)              (원본 RemoteCallViewer/PcControl)
  FileExplorer       : 디렉터리/파일 read·write·stat  (원본 RemoteExplorer.exe)
  ProcessManagement  : 프로세스 목록/종료             (원본 RemoteTaskManager.exe)
  RegistryManagement : 레지스트리 CRUD                (원본 RemoteRegEditor.exe)
                       - Windows: winreg 사용
                       - 그 외 OS: JSON 파일로 가상 레지스트리 시뮬레이션
  PcPowerOff/Reboot  : 전원 제어(기본 시뮬레이션)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import platform
import shutil
import subprocess
import sys

import protocol as P
from protocol import ResultCode, recv_frame, send_frame
from crypto import AesCipher

IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    try:
        import winreg  # type: ignore
    except ImportError:
        winreg = None
else:
    winreg = None


class Agent:
    def __init__(self, agent_key, relay_host, sig_port,
                 auto_confirm=True, allow_real_power=False, reg_file=None,
                 permission=None, force_decline=False, psk=None, use_real_registry=False):
        self.agent_key = agent_key
        self.relay_host = relay_host
        self.sig_port = sig_port
        self.auto_confirm = auto_confirm
        self.allow_real_power = allow_real_power
        self.force_decline = force_decline
        self.cipher = AesCipher(psk) if psk else None
        # Windows 에서 --real-registry 지정 시 winreg 사용(기본은 시뮬레이션)
        self.use_real_registry = use_real_registry and winreg is not None
        self.reg_file = reg_file or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), f"fake_registry_{agent_key}.json")
        self.permission = permission or P.RemoteCommandPermission.allow_all()

    def log(self, *a):
        print(f"[agent {self.agent_key}]", *a, flush=True)

    # ========================================================================
    async def run(self):
        reader, writer = await asyncio.open_connection(self.relay_host, self.sig_port)
        perm = {"UseRemote": self.permission.UseRemote,
                "RemoteControlType": {str(k): v for k, v in self.permission.RemoteControlType.items()}}
        await send_frame(writer, {"type": "HELLO", "role": "agent",
                                  "agent_key": self.agent_key, "permission": perm}, self.cipher)
        reg = await recv_frame(reader, self.cipher)
        self.log("registered:", reg)
        try:
            while True:
                msg = await recv_frame(reader, self.cipher)
                if msg is None:
                    break
                t = msg.get("type")
                if t == "PUSH_CONFIRM":
                    await self._on_confirm(msg, writer)
                elif t == "OPEN_TUNNEL":
                    asyncio.create_task(self._open_tunnel(msg))
                elif t == "PUSH_POWER":
                    await self._on_power(msg, writer)
        finally:
            writer.close()
            self.log("disconnected")

    # -- 동의 처리 (ConfirmRemoteSupportRequest) ------------------------------
    async def _on_confirm(self, msg, writer):
        ct = int(msg["RemoteControlType"])
        req = P.ConfirmRemoteSupportRequest(
            AgentKey=msg["AgentKey"], ConfirmMessage=msg["ConfirmMessage"],
            IsUseConfirm=msg["IsUseConfirm"], RemoteControlType=ct)
        accepted, reason = True, "auto"
        if self.force_decline:
            accepted, reason = False, "force-decline(demo)"
        elif req.IsUseConfirm:
            self.log(f"[동의 요청] admin={msg.get('admin_id')} "
                     f"type={P.control_type_name(ct)} msg={req.ConfirmMessage!r}")
            if self.auto_confirm:
                accepted, reason = True, "auto-confirm"
            else:
                try:
                    ans = await asyncio.get_event_loop().run_in_executor(
                        None, input, "  허용하시겠습니까? [y/N]: ")
                except EOFError:
                    ans = "n"
                accepted = ans.strip().lower() in ("y", "yes")
                reason = "user-accepted" if accepted else "user-declined"
        self.log(f"[동의 결과] accepted={accepted} ({reason})")
        await send_frame(writer, {"type": "CONFIRM_RESULT", "request_id": msg["request_id"],
                                  "accepted": accepted, "reason": reason}, self.cipher)

    # -- 전원 제어 (RemotePcPowerPushResponse) --------------------------------
    async def _on_power(self, msg, writer):
        action = msg["action"]
        self.log(f"[전원 제어] action={action} "
                 f"(real={self.allow_real_power})")
        if self.allow_real_power and not IS_WINDOWS:
            # 안전상 기본 비활성. 실제로 켜려면 --allow-real-power.
            cmd = "shutdown -h now" if action == "off" else "reboot"
            self.log(f"  (실제 전원 명령 실행 생략됨; 활성 시: {cmd})")
        result = ResultCode.OK
        await send_frame(writer, {"type": "POWER_RESULT", "request_id": msg["request_id"],
                                  "AgentKey": self.agent_key, "Result": result}, self.cipher)

    # ========================================================================
    # 터널 서비스
    # ========================================================================
    async def _open_tunnel(self, msg):
        auth_key = msg["auth_key"]
        ct = int(msg["control_type"])
        try:
            r, w = await asyncio.open_connection(msg["relay_ip"], msg["relay_port"])
        except Exception as e:
            self.log("tunnel connect failed:", e)
            return
        await send_frame(w, {"role": "agent", "auth_key": auth_key}, self.cipher)
        # 터널 op 프레임은 AuthKey 로 파생한 키로 암호화(릴레이도 못 읽는 E2E)
        tun_cipher = AesCipher(auth_key) if self.cipher else None
        self.log(f"tunnel up ({P.control_type_name(ct)})")
        try:
            while True:
                req = await recv_frame(r, tun_cipher)
                if req is None:
                    break
                resp = await self._dispatch(ct, req)
                await send_frame(w, resp, tun_cipher)
        except Exception as e:
            self.log("tunnel error:", e)
        finally:
            w.close()
            self.log("tunnel down")

    async def _dispatch(self, control_type, req):
        op = req.get("op")
        try:
            # --- PcControl: 셸 명령 ---
            if op == "exec":
                return await self._op_exec(req)
            # --- FileExplorer ---
            if op == "list_dir":
                return self._op_list_dir(req)
            if op == "stat":
                return self._op_stat(req)
            if op == "read_file":
                return self._op_read_file(req)
            if op == "write_file":
                return self._op_write_file(req)
            # --- ProcessManagement ---
            if op == "list_proc":
                return await self._op_list_proc()
            if op == "kill":
                return self._op_kill(req)
            # --- RegistryManagement ---
            if op == "reg_list":
                return self._reg_op("list", req)
            if op == "reg_get":
                return self._reg_op("get", req)
            if op == "reg_set":
                return self._reg_op("set", req)
            if op == "reg_delete":
                return self._reg_op("delete", req)
            if op == "ping":
                return {"ok": True, "pong": True, "agent": self.agent_key,
                        "os": platform.platform()}
            return {"ok": False, "error": f"unknown op: {op}"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # -- PcControl -----------------------------------------------------------
    async def _op_exec(self, req):
        command = req["command"]
        timeout = req.get("timeout", 20)
        self.log(f"exec: {command!r}")
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "timeout"}
        return {"ok": True, "exit_code": proc.returncode,
                "stdout": out.decode(errors="replace"),
                "stderr": err.decode(errors="replace")}

    # -- FileExplorer --------------------------------------------------------
    def _op_list_dir(self, req):
        path = req.get("path", ".")
        entries = []
        with os.scandir(path) as it:
            for e in it:
                try:
                    st = e.stat()
                    entries.append({"name": e.name, "is_dir": e.is_dir(),
                                    "size": st.st_size, "mtime": int(st.st_mtime)})
                except OSError:
                    entries.append({"name": e.name, "is_dir": e.is_dir(), "size": None})
        return {"ok": True, "path": os.path.abspath(path), "entries": entries}

    def _op_stat(self, req):
        st = os.stat(req["path"])
        return {"ok": True, "size": st.st_size, "mtime": int(st.st_mtime),
                "mode": oct(st.st_mode), "is_dir": os.path.isdir(req["path"])}

    def _op_read_file(self, req):
        path = req["path"]
        max_bytes = req.get("max_bytes", 1024 * 1024)
        with open(path, "rb") as f:
            data = f.read(max_bytes)
        return {"ok": True, "path": os.path.abspath(path), "size": len(data),
                "data_b64": base64.b64encode(data).decode()}

    def _op_write_file(self, req):
        path = req["path"]
        data = base64.b64decode(req["data_b64"])
        with open(path, "wb") as f:
            f.write(data)
        return {"ok": True, "path": os.path.abspath(path), "written": len(data)}

    # -- ProcessManagement ---------------------------------------------------
    async def _op_list_proc(self):
        if IS_WINDOWS:
            cmd = "tasklist"
        else:
            cmd = "ps -eo pid,ppid,comm,%cpu,%mem --sort=-%cpu" if shutil.which("ps") else "ls /proc"
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        return {"ok": True, "text": out.decode(errors="replace")}

    def _op_kill(self, req):
        pid = int(req["pid"])
        sig = int(req.get("signal", 15))
        os.kill(pid, sig)
        return {"ok": True, "killed": pid, "signal": sig}

    # -- RegistryManagement (Windows=winreg, 그 외=시뮬레이션) ----------------
    def _reg_load(self):
        if os.path.exists(self.reg_file):
            with open(self.reg_file, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _reg_save(self, data):
        with open(self.reg_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # Windows 실제 레지스트리 (winreg) ------------------------------------
    _HIVES = {}
    if winreg is not None:
        _HIVES = {
            "HKLM": winreg.HKEY_LOCAL_MACHINE, "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
            "HKCU": winreg.HKEY_CURRENT_USER, "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
            "HKCR": winreg.HKEY_CLASSES_ROOT, "HKU": winreg.HKEY_USERS,
        }

    def _reg_split(self, key):
        head, _, sub = key.replace("/", "\\").partition("\\")
        hive = self._HIVES.get(head.upper())
        if hive is None:
            raise ValueError(f"unknown hive: {head}")
        return hive, sub

    def _reg_op_winreg(self, action, req):
        key = req.get("key", "HKCU\\SOFTWARE\\RemoteSupportTest")
        hive, sub = self._reg_split(key)
        if action == "list":
            values = {}
            with winreg.OpenKey(hive, sub) as k:
                i = 0
                while True:
                    try:
                        name, val, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    values[name] = val
                    i += 1
            return {"ok": True, "key": key, "values": values, "simulated": False}
        if action == "get":
            with winreg.OpenKey(hive, sub) as k:
                val, _ = winreg.QueryValueEx(k, req["name"])
            return {"ok": True, "key": key, "name": req["name"], "value": val, "simulated": False}
        if action == "set":
            with winreg.CreateKey(hive, sub) as k:
                winreg.SetValueEx(k, req["name"], 0, winreg.REG_SZ, str(req["value"]))
            return {"ok": True, "key": key, "name": req["name"], "value": req["value"], "simulated": False}
        if action == "delete":
            with winreg.OpenKey(hive, sub, 0, winreg.KEY_SET_VALUE) as k:
                try:
                    winreg.DeleteValue(k, req["name"])
                    deleted = True
                except FileNotFoundError:
                    deleted = False
            return {"ok": True, "key": key, "name": req["name"], "deleted": deleted, "simulated": False}
        return {"ok": False, "error": "bad reg action"}

    def _reg_op(self, action, req):
        if self.use_real_registry:
            return self._reg_op_winreg(action, req)
        # 그 외: JSON 가상 레지스트리 시뮬레이션
        key = req.get("key", "HKLM\\SOFTWARE\\RemoteSupportTest")
        data = self._reg_load()
        bucket = data.setdefault(key, {})
        if action == "list":
            return {"ok": True, "key": key, "values": bucket, "simulated": True}
        if action == "get":
            name = req["name"]
            return {"ok": True, "key": key, "name": name,
                    "value": bucket.get(name), "simulated": True}
        if action == "set":
            name, value = req["name"], req["value"]
            bucket[name] = value
            self._reg_save(data)
            return {"ok": True, "key": key, "name": name, "value": value, "simulated": True}
        if action == "delete":
            name = req["name"]
            existed = bucket.pop(name, None) is not None
            self._reg_save(data)
            return {"ok": True, "key": key, "name": name, "deleted": existed, "simulated": True}
        return {"ok": False, "error": "bad reg action"}


def main():
    ap = argparse.ArgumentParser(description="Remote Support 테스트 에이전트")
    ap.add_argument("--agent-key", required=True, help="이 에이전트의 식별자")
    ap.add_argument("--relay-host", default="127.0.0.1")
    ap.add_argument("--sig-port", type=int, default=9500)
    ap.add_argument("--manual-confirm", action="store_true",
                    help="동의를 대화형(y/N)으로 묻기 (기본: 자동 승인)")
    ap.add_argument("--allow-real-power", action="store_true",
                    help="전원 명령을 실제로 실행(기본: 시뮬레이션). 주의!")
    ap.add_argument("--psk", default=None, help="사전공유키(AES 전송 암호화)")
    ap.add_argument("--real-registry", action="store_true",
                    help="Windows 실제 레지스트리(winreg) 사용(기본: 시뮬레이션)")
    args = ap.parse_args()
    agent = Agent(args.agent_key, args.relay_host, args.sig_port,
                  auto_confirm=not args.manual_confirm,
                  allow_real_power=args.allow_real_power,
                  psk=args.psk, use_real_registry=args.real_registry)
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
