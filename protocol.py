"""
protocol.py — 공용 프로토콜/데이터 모델

ASMConsole.exe 의 RemoteSupport 계층에서 추출한 구조를 그대로 본뜬
독립 테스트용 구현입니다. 알약 실제 서버/SantaNetwork 와이어 포맷과는
무관하며, 여기서는 자체적으로 '길이 접두 + JSON' 프레이밍을 사용합니다.

원본 대응:
  RemoteControlType                -> class RemoteControlType(IntEnum)
  ConfirmRemoteSupportRequest      -> class ConfirmRemoteSupportRequest
  RemotePcCommandParam             -> class RemotePcCommandParam
  RemotePcCommandPushResponse      -> class RemotePcCommandPushResponse
  RemotePcPowerPushResponse        -> class RemotePcPowerPushResponse
  GetRemoteRelayServerInfo         -> class RemoteRelayServerInfo
  ConfigRemoteCommandPermission    -> class RemoteCommandPermission
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import json
import struct
from typing import Any


# --- RemoteControlType (원본 enum 값 순서 그대로) ---------------------------
class RemoteControlType(enum.IntEnum):
    PcControl = 0            # 원격 제어(셸/명령) -> RemoteCallViewer 계열
    FileExplorer = 1         # RemoteExplorer.exe
    RegistryManagement = 2   # RemoteRegEditor.exe
    ProcessManagement = 3    # RemoteTaskManager.exe
    PcPowerOff = 4
    PcPowerReboot = 5


# --- 결과 코드(원본 valuetype:3932 "Result" 자리) ---------------------------
class ResultCode(enum.IntEnum):
    OK = 0
    DENIED_BY_USER = 1        # 에이전트 사용자가 동의 거부
    DENIED_BY_POLICY = 2      # ConfigRemoteCommandPermission.HasPermission == false
    AGENT_OFFLINE = 3
    TIMEOUT = 4
    ERROR = 5


# --- VO: 원격 명령 파라미터 (RemotePcCommandParam) --------------------------
@dataclasses.dataclass
class RemotePcCommandParam:
    AdminId: str = ""
    AgentName: str = ""
    ServerIp: str = ""
    ServerPort: int = 0
    AccessToken: str = ""
    LoginCheckEventName: str = ""


# --- VO: 콘솔이 받는 푸시 응답 (RemotePcCommandPushResponse) ----------------
@dataclasses.dataclass
class RemotePcCommandPushResponse:
    Result: int = ResultCode.OK
    AuthKey: str = ""        # 릴레이 터널 인증 토큰
    AgentKey: str = ""
    Port: int = 0            # 릴레이 터널 포트


# --- VO: 전원 제어 푸시 응답 (RemotePcPowerPushResponse) --------------------
@dataclasses.dataclass
class RemotePcPowerPushResponse:
    Result: int = ResultCode.OK
    AgentKey: str = ""


# --- VO: 에이전트 동의 요청 (ConfirmRemoteSupportRequest) -------------------
@dataclasses.dataclass
class ConfirmRemoteSupportRequest:
    AgentKey: str = ""
    ConfirmMessage: str = ""
    IsUseConfirm: bool = True
    RemoteControlType: int = RemoteControlType.PcControl


# --- VO: 릴레이 서버 정보 (GetRemoteRelayServerInfo) ------------------------
@dataclasses.dataclass
class RemoteRelayServerInfo:
    IP: str = "127.0.0.1"
    Port: int = 0


# --- 정책: 원격 명령 권한 (ConfigRemoteCommandPermission) -------------------
@dataclasses.dataclass
class RemoteCommandPermission:
    UseRemote: bool = True
    # RemoteControlType -> 허용 여부
    RemoteControlType: dict[int, bool] = dataclasses.field(default_factory=dict)

    def has_permission(self, control_type: int) -> bool:
        """원본 ConfigRemoteCommandPermission.HasPermission(type) 대응."""
        if not self.UseRemote:
            return False
        return bool(self.RemoteControlType.get(int(control_type), False))

    @staticmethod
    def allow_all() -> "RemoteCommandPermission":
        return RemoteCommandPermission(
            UseRemote=True,
            RemoteControlType={int(t): True for t in RemoteControlType},
        )


# ============================================================================
# 길이 접두(4바이트 big-endian) + JSON 프레이밍
#   - 사람이 읽기 쉬운 평문 JSON (테스트/디버깅 목적)
#   - 실제 제품은 여기서 AES/ARIA 로 암호화하지만, 테스트에서는 생략
# ============================================================================
_HEADER = struct.Struct(">I")
MAX_FRAME = 16 * 1024 * 1024


async def send_frame(writer: asyncio.StreamWriter, obj: Any, cipher=None) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if cipher is not None:
        data = cipher.encrypt(data)        # AESCrypto 스타일 전송 암호화
    if len(data) > MAX_FRAME:
        raise ValueError("frame too large")
    writer.write(_HEADER.pack(len(data)) + data)
    await writer.drain()


async def recv_frame(reader: asyncio.StreamReader, cipher=None) -> Any | None:
    try:
        header = await reader.readexactly(_HEADER.size)
    except asyncio.IncompleteReadError:
        return None
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME:
        raise ValueError("frame too large")
    body = await reader.readexactly(length)
    if cipher is not None:
        body = cipher.decrypt(body)
    return json.loads(body.decode("utf-8"))


def control_type_name(value: int) -> str:
    try:
        return RemoteControlType(int(value)).name
    except ValueError:
        return f"Unknown({value})"
