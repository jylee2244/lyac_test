"""
relay_server.py — 릴레이/시그널링 서버

원본 ASMConsole 의 서버+릴레이 역할을 하나로 합친 테스트용 브로커.
  - 시그널링(기본 9500): 에이전트 등록/상주, 콘솔 요청 수신, Push 전달
  - 터널(기본 9600): 승인된 세션의 실제 데이터 중계(바이트 파이프)

원본 대응:
  Pusher / PusherMap(eventName->callback)   -> request_id 로 매칭되는 pending future
  GetRemoteRelayServerInfo                  -> 터널 IP/Port 를 콘솔에 반환
  SetRemoteSupport / SetRemoteCommand       -> REQUEST_REMOTE 처리
  ControlRemotePcPowerOff/Reboot(+ByGroup)  -> PC_POWER 처리(단건/그룹)
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
from dataclasses import dataclass, field

import protocol as P
from protocol import ResultCode, recv_frame, send_frame
from crypto import AesCipher


@dataclass
class AgentConn:
    agent_key: str
    writer: asyncio.StreamWriter
    permission: P.RemoteCommandPermission
    pending: dict[str, asyncio.Future] = field(default_factory=dict)


@dataclass
class TunnelSession:
    auth_key: str
    agent_key: str
    control_type: int
    agent_side: asyncio.Future = field(default_factory=asyncio.Future)
    console_side: asyncio.Future = field(default_factory=asyncio.Future)


class RelayServer:
    def __init__(self, host: str, sig_port: int, tun_port: int, psk: str | None = None):
        self.host = host
        self.sig_port = sig_port
        self.tun_port = tun_port
        self.agents: dict[str, AgentConn] = {}
        self.sessions: dict[str, TunnelSession] = {}
        # PSK 가 있으면 시그널링/터널 핸드셰이크 프레임을 AES 로 암호화
        self.cipher = AesCipher(psk) if psk else None

    # -- 로그 -----------------------------------------------------------------
    @staticmethod
    def log(*a):
        print("[relay]", *a, flush=True)

    # ========================================================================
    # 시그널링
    # ========================================================================
    async def handle_signaling(self, reader, writer):
        peer = writer.get_extra_info("peername")
        hello = await recv_frame(reader, self.cipher)
        if not hello or hello.get("type") != "HELLO":
            writer.close()
            return
        role = hello.get("role")
        if role == "agent":
            await self._serve_agent(hello, reader, writer)
        elif role == "console":
            await self._serve_console(hello, reader, writer)
        else:
            writer.close()

    # -- 에이전트 상주 연결 ---------------------------------------------------
    async def _serve_agent(self, hello, reader, writer):
        agent_key = hello["agent_key"]
        perm_raw = hello.get("permission") or {}
        permission = P.RemoteCommandPermission(
            UseRemote=perm_raw.get("UseRemote", True),
            RemoteControlType={int(k): bool(v)
                               for k, v in (perm_raw.get("RemoteControlType") or {}).items()},
        )
        conn = AgentConn(agent_key=agent_key, writer=writer, permission=permission)
        self.agents[agent_key] = conn
        self.log(f"agent online: {agent_key} (perm={permission.RemoteControlType})")
        await send_frame(writer, {"type": "REGISTERED", "agent_key": agent_key}, self.cipher)
        try:
            while True:
                msg = await recv_frame(reader, self.cipher)
                if msg is None:
                    break
                # 에이전트가 보내는 응답은 request_id 로 pending future 매칭
                rid = msg.get("request_id")
                if rid and rid in conn.pending and not conn.pending[rid].done():
                    conn.pending[rid].set_result(msg)
        finally:
            if self.agents.get(agent_key) is conn:
                del self.agents[agent_key]
            self.log(f"agent offline: {agent_key}")
            writer.close()

    async def _ask_agent(self, conn: AgentConn, msg: dict, timeout: float):
        """에이전트에 Push 하고 같은 request_id 응답을 기다림(Pusher 모델)."""
        rid = msg["request_id"]
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        conn.pending[rid] = fut
        try:
            await send_frame(conn.writer, msg, self.cipher)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            conn.pending.pop(rid, None)

    # -- 콘솔 연결 ------------------------------------------------------------
    async def _serve_console(self, hello, reader, writer):
        admin_id = hello.get("admin_id", "?")
        self.log(f"console connected: admin={admin_id}")
        try:
            while True:
                req = await recv_frame(reader, self.cipher)
                if req is None:
                    break
                t = req.get("type")
                if t == "REQUEST_REMOTE":
                    await self._handle_request_remote(req, writer)
                elif t == "PC_POWER":
                    await self._handle_pc_power(req, writer)
                elif t == "LIST_AGENTS":
                    await send_frame(writer, {"type": "AGENTS",
                                              "agents": list(self.agents.keys())}, self.cipher)
                else:
                    await send_frame(writer, {"type": "ERROR", "message": f"unknown {t}"}, self.cipher)
        finally:
            self.log(f"console disconnected: admin={admin_id}")
            writer.close()

    async def _handle_request_remote(self, req, writer):
        """SetRemoteSupport / SetRemoteCommand 대응: 요청→동의→터널 세션 발급."""
        agent_key = req["agent_key"]
        control_type = int(req["control_type"])
        rid = secrets.token_hex(8)
        conn = self.agents.get(agent_key)
        if conn is None:
            await send_frame(writer, {"type": "REMOTE_RESPONSE", "request_id": rid,
                                      "Result": ResultCode.AGENT_OFFLINE,
                                      "AuthKey": "", "AgentKey": agent_key, "Port": 0}, self.cipher)
            return
        # 정책 권한 체크 (ConfigRemoteCommandPermission.HasPermission)
        if not conn.permission.has_permission(control_type):
            self.log(f"DENY(policy) {agent_key} {P.control_type_name(control_type)}")
            await send_frame(writer, {"type": "REMOTE_RESPONSE", "request_id": rid,
                                      "Result": ResultCode.DENIED_BY_POLICY,
                                      "AuthKey": "", "AgentKey": agent_key, "Port": 0}, self.cipher)
            return
        # 에이전트에 동의 요청 Push (ConfirmRemoteSupportRequest)
        confirm = {
            "type": "PUSH_CONFIRM", "request_id": rid,
            "admin_id": req.get("admin_id", "?"),
            "AgentKey": agent_key,
            "ConfirmMessage": req.get("confirm_message", "원격 지원을 허용하시겠습니까?"),
            "IsUseConfirm": req.get("is_use_confirm", True),
            "RemoteControlType": control_type,
        }
        self.log(f"push confirm -> {agent_key} ({P.control_type_name(control_type)})")
        reply = await self._ask_agent(conn, confirm, timeout=req.get("timeout", 30))
        if reply is None:
            await send_frame(writer, {"type": "REMOTE_RESPONSE", "request_id": rid,
                                      "Result": ResultCode.TIMEOUT,
                                      "AuthKey": "", "AgentKey": agent_key, "Port": 0}, self.cipher)
            return
        if not reply.get("accepted"):
            self.log(f"DENY(user) {agent_key}: {reply.get('reason')}")
            await send_frame(writer, {"type": "REMOTE_RESPONSE", "request_id": rid,
                                      "Result": ResultCode.DENIED_BY_USER,
                                      "AuthKey": "", "AgentKey": agent_key, "Port": 0}, self.cipher)
            return
        # 승인 -> 터널 세션 발급 (AuthKey 핸드셰이크)
        auth_key = secrets.token_hex(16)
        self.sessions[auth_key] = TunnelSession(auth_key, agent_key, control_type)
        # 에이전트에게 터널 열라고 지시
        await send_frame(conn.writer, {"type": "OPEN_TUNNEL", "request_id": rid,
                                       "auth_key": auth_key,
                                       "relay_ip": self.host if self.host != "0.0.0.0" else "127.0.0.1",
                                       "relay_port": self.tun_port,
                                       "control_type": control_type}, self.cipher)
        # 콘솔에 AuthKey/Port 반환 (RemotePcCommandPushResponse)
        await send_frame(writer, {"type": "REMOTE_RESPONSE", "request_id": rid,
                                  "Result": ResultCode.OK,
                                  "AuthKey": auth_key, "AgentKey": agent_key,
                                  "Port": self.tun_port}, self.cipher)
        self.log(f"session granted {agent_key} auth={auth_key[:8]}… "
                 f"({P.control_type_name(control_type)})")

    async def _handle_pc_power(self, req, writer):
        """ControlRemotePcPowerOff/Reboot (+ByGroup) 대응: fire-and-forget."""
        action = req["action"]  # "off" | "reboot"
        agent_keys = req.get("agent_keys") or [req.get("agent_key")]
        results = []
        for ak in agent_keys:
            conn = self.agents.get(ak)
            if conn is None:
                results.append({"AgentKey": ak, "Result": ResultCode.AGENT_OFFLINE})
                continue
            if not conn.permission.has_permission(
                    P.RemoteControlType.PcPowerOff if action == "off"
                    else P.RemoteControlType.PcPowerReboot):
                results.append({"AgentKey": ak, "Result": ResultCode.DENIED_BY_POLICY})
                continue
            rid = secrets.token_hex(8)
            reply = await self._ask_agent(
                conn, {"type": "PUSH_POWER", "request_id": rid, "action": action},
                timeout=req.get("timeout", 15))
            if reply is None:
                results.append({"AgentKey": ak, "Result": ResultCode.TIMEOUT})
            else:
                results.append({"AgentKey": ak, "Result": reply.get("Result", ResultCode.OK)})
        await send_frame(writer, {"type": "POWER_RESPONSE", "results": results}, self.cipher)

    # ========================================================================
    # 터널 (바이트 중계)
    # ========================================================================
    async def handle_tunnel(self, reader, writer):
        hello = await recv_frame(reader, self.cipher)
        if not hello or "auth_key" not in hello or "role" not in hello:
            writer.close()
            return
        auth_key = hello["auth_key"]
        role = hello["role"]
        sess = self.sessions.get(auth_key)
        if sess is None:
            await send_frame(writer, {"type": "TUNNEL_DENIED"}, self.cipher)
            writer.close()
            return
        fut = sess.agent_side if role == "agent" else sess.console_side
        if not fut.done():
            fut.set_result((reader, writer))
        # 양쪽이 모두 붙으면 파이프 시작 (콘솔 쪽에서 트리거)
        if role == "console":
            try:
                a_reader, a_writer = await asyncio.wait_for(sess.agent_side, timeout=15)
            except asyncio.TimeoutError:
                writer.close()
                return
            self.log(f"tunnel paired auth={auth_key[:8]}… "
                     f"({P.control_type_name(sess.control_type)})")
            await send_frame(writer, {"type": "TUNNEL_READY"}, self.cipher)
            await self._pipe(a_reader, a_writer, reader, writer)
            self.sessions.pop(auth_key, None)
            self.log(f"tunnel closed auth={auth_key[:8]}…")

    @staticmethod
    async def _pipe(ar, aw, cr, cw):
        async def one(src, dst):
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass
        await asyncio.gather(one(ar, cw), one(cr, aw))

    # ========================================================================
    async def serve(self):
        sig = await asyncio.start_server(self.handle_signaling, self.host, self.sig_port)
        tun = await asyncio.start_server(self.handle_tunnel, self.host, self.tun_port)
        self.log(f"signaling on {self.host}:{self.sig_port}, tunnel on {self.host}:{self.tun_port}")
        async with sig, tun:
            await asyncio.gather(sig.serve_forever(), tun.serve_forever())


def main():
    ap = argparse.ArgumentParser(description="Remote Support 테스트 릴레이 서버")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--sig-port", type=int, default=9500)
    ap.add_argument("--tun-port", type=int, default=9600)
    ap.add_argument("--psk", default=None, help="사전공유키(AES 전송 암호화). 모든 노드가 동일해야 함")
    args = ap.parse_args()
    try:
        asyncio.run(RelayServer(args.host, args.sig_port, args.tun_port, args.psk).serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
