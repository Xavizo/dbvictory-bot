"""Local relay between the game client and the DBVictory servers.

Two instances run per bot session:

* the **login relay** (port 7171) sees one exchange per login. It learns the
  session's XTEA keys from the client's RSA block and rewrites the character
  list so the client then connects to the game relay instead of the server;
* the **game relay** (port 7172) forwards every frame unchanged in both
  directions, lets the bot observe traffic, and is the only place where bot
  commands enter the connection (``inject_to_server``).

On the wire a frame is ``u16 length`` + body. A body is ``u32 adler32`` +
XTEA data whose plain text is ``u16 inner length`` + packet + padding.
Server frames are always forwarded before anything inspects them.
"""
import asyncio
import contextlib
import logging
import struct
import time
from collections import deque

from dbv.client_bridge.command_delivery import CommandReceipt, QueuedCommand
from dbv.client_bridge.constants import SERVER_IP_BYTES, SERVER_IP_STR
from dbv.client_bridge.crypto import (
    DEFAULT_RSA_E, DEFAULT_RSA_N, adler32_checksum, generate_proxy_rsa_keypair,
    get_default_rsa_key, rsa_decrypt, xtea_decrypt, xtea_encrypt,
)
from dbv.client_bridge.packet_observer import decrypt_game_packet
from dbv.client_bridge.protocol import ClientOpcode, PacketReader, ServerOpcode

log = logging.getLogger(name="proxy")

RSA_BLOCK = 128
# The 26 Nov client's game login: checksum + opcode 0x0A, OS 10, version 372.
NOVEMBER_26_LOGIN = (137, struct.pack("<BHH", 10, 10, 372))
KEY_CHECK_ATTEMPTS = 8
LOCALHOST_PACKED = bytes((127, 0, 0, 1))
_CANCEL_ATTACK = bytes((ClientOpcode.ATTACK, 0, 0, 0, 0))


# ── frame helpers (pure) ────────────────────────────────────────────

def _checksummed(body: bytes) -> bool:
    return len(body) > 4 and struct.unpack_from("<I", body)[0] == adler32_checksum(body[4:])


def find_session_keys(body: bytes, rsa_keys) -> tuple | None:
    """XTEA keys from a login packet's RSA block, or None.

    The block is normally the last 128 bytes; other offsets are tried after
    it. A block decrypted with the right key starts with a zero byte,
    followed by the four XTEA key words.
    """
    start = 4 if _checksummed(body) else 0
    last = len(body) - RSA_BLOCK
    if last < start + 5:
        return None
    offsets = [last] + [o for o in range(start + 1, last) if o != last]
    for key in rsa_keys:
        for at in offsets:
            try:
                plain = rsa_decrypt(body[at:at + RSA_BLOCK], key)
            except Exception:
                continue
            if plain[0] == 0:
                log.info("RSA block found at offset %d", at)
                return struct.unpack_from("<4I", plain, 1)
    return None


def point_character_list_at_proxy(body: bytes, keys) -> bytes | None:
    """Rewrite the login reply so every game world address is 127.0.0.1.

    Both the packed form (4 bytes) and the text form are replaced; text is
    padded with zero bytes to keep the packet length. Returns the
    re-encrypted body, or None if it could not be decrypted.
    """
    checksum = _checksummed(body)
    sealed = body[4:] if checksum else body
    if not sealed or len(sealed) % 8:
        log.warning("[LOGIN] Reply is not whole XTEA blocks")
        return None
    plain = bytearray(xtea_decrypt(sealed, keys))
    size = struct.unpack_from("<H", plain)[0]
    packet = plain[2:2 + size]
    text_ip = b"127.0.0.1".ljust(len(SERVER_IP_STR), b"\0")[:len(SERVER_IP_STR)]
    replaced = packet.count(SERVER_IP_BYTES) + packet.count(SERVER_IP_STR)
    packet = packet.replace(SERVER_IP_BYTES, LOCALHOST_PACKED).replace(SERVER_IP_STR, text_ip)
    if not replaced:
        log.warning("[LOGIN] The character list names no known server address")
    plain[2:2 + size] = packet  # same length: only equal-sized replacements
    resealed = xtea_encrypt(bytes(plain), keys)
    return struct.pack("<I", adler32_checksum(resealed)) + resealed if checksum else resealed


class OTProxy:
    """One relay: listens on ``listen_port`` and connects each client to the server."""

    def __init__(self, server_host: str, server_port: int, listen_port: int, is_login_proxy: bool = False,
                 shared_rsa_key=None, *, client_key_reader=None) -> None:
        self.server_host, self.server_port, self.listen_port, self.is_login_proxy = (
            server_host, server_port, listen_port, is_login_proxy)
        # The client encrypts its login to the public OTClient key, whose private half is known,
        # so the relay reads the keys without patching the client's RSA key.
        self.default_rsa_key, self._shared_rsa_key = get_default_rsa_key(), shared_rsa_key
        self._proxy_rsa_key = None            # generated only when first needed
        self.server_rsa_n, self.server_rsa_e = DEFAULT_RSA_N, DEFAULT_RSA_E
        # 26 Nov client: keys are read from client memory and confirmed on server frames.
        self.client_key_reader = client_key_reader
        self.xtea_keys, self.logged_in = None, False
        self._reset_client_key()
        self.client_reader = self.client_writer = None
        self.server_reader = self.server_writer = None
        # Hooks for the bot.
        self._client_packet_callbacks = []    # callables (opcode, reader) for client packets
        self.on_server_packet = None          # (opcode, reader) for server packets
        self.on_raw_server_data = None        # (decrypted bytes)
        self.on_encrypted_server_data = None  # (frame, keys, session) passive tap, after forwarding
        self.on_login_success = self.on_game_disconnected = None   # (keys) and ()
        # Bot commands.
        self._inject_queue = asyncio.Queue(maxsize=256)
        self.command_delivery = True
        self.command_sequence = 0
        self.command_history = deque(maxlen=100)
        self.session_generation = 0
        self.injections_paused = False
        # Counters and timestamps for status and diagnostics.
        self.packets_from_server = self.packets_from_client = 0
        self.last_server_read = self.last_client_forward = None
        self._connection_task = None
        self._server = None
        self._stamp("_ts_created")
        self._ts_listening = self._ts_client_connected = self._ts_server_connected = None
        self._ts_xtea_captured = self._ts_logged_in = None

    def _stamp(self, name: str) -> None:
        """Record when a connection step happened (wall clock, for the panel)."""
        setattr(self, name, time.time())

    # ── hooks ────────────────────────────────────────────────────────
    def register_client_packet_callback(self, callback) -> None:
        callbacks = self._client_packet_callbacks
        if callback not in callbacks:
            callbacks.append(callback)

    def unregister_client_packet_callback(self, callback) -> None:
        callbacks = self._client_packet_callbacks
        with contextlib.suppress(ValueError):   # it was never registered
            callbacks.remove(callback)

    def _first_client_callback(self):
        return next(iter(self._client_packet_callbacks), None)

    def _replace_first_client_callback(self, callback) -> None:
        callbacks = self._client_packet_callbacks
        if callback is not None:
            callbacks[:1] = [callback]
        else:
            callbacks.clear()

    on_client_packet = property(_first_client_callback, _replace_first_client_callback,
                                doc="The first client callback; assigning replaces it, None removes all.")

    @property
    def proxy_rsa_key(self) -> "RSA.RsaKey":
        """The relay's own key pair, shared between relays or generated on first use."""
        key = self._proxy_rsa_key
        if key is None:
            key = self._proxy_rsa_key = self._shared_rsa_key or generate_proxy_rsa_keypair()
        return key

    def _rsa_keys(self):
        yield self.default_rsa_key
        yield self.proxy_rsa_key  # generated only if the OTClient key did not fit

    def get_proxy_rsa_public_key(self):
        """The relay's RSA modulus as decimal text."""
        return "%d" % self.proxy_rsa_key.n

    # ── listening ────────────────────────────────────────────────────
    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client_connection, "127.0.0.1", self.listen_port)
        self._stamp("_ts_listening")
        host, port = self._server.sockets[0].getsockname()[:2]
        log.info("[%s] relay on %s:%s -> %s:%s", "LOGIN" if self.is_login_proxy else "GAME",
                 host, port, self.server_host, self.server_port)
        async with self._server:
            await self._server.serve_forever()

    def close_server(self) -> None:
        server, self._server = self._server, None
        if server:
            with contextlib.suppress(Exception):
                server.close()

    async def _handle_client_connection(self, client_in: asyncio.StreamReader,
                                        client_out: asyncio.StreamWriter) -> None:
        """Serialize ownership changes; an old handler cannot clear a newer session."""
        if not hasattr(self, '_handover_lock'):
            self._handover_lock = asyncio.Lock()
        generation = None
        try:
            async with self._handover_lock:
                previous, current = self._connection_task, asyncio.current_task()
                if previous and previous is not current and not previous.done():
                    previous.cancel()
                    await asyncio.gather(previous, return_exceptions=True)
                self._connection_task = current
                self.session_generation += 1
                generation = self.session_generation
                self.discard_commands('session_changed')
                self.client_reader, self.client_writer = client_in, client_out
                self.server_reader = self.server_writer = None
                self.logged_in, self.xtea_keys = False, None
                self._reset_client_key()
                self.last_server_read = self.last_client_forward = None
                self._stamp("_ts_client_connected")
            self.server_reader, self.server_writer = await asyncio.open_connection(self.server_host, self.server_port)
            self._stamp("_ts_server_connected")
            if self.is_login_proxy:
                await self._handle_login_session()
            else:
                await self._run_relay_loop()
        except Exception as exc:   # cancellation is not an Exception and propagates
            log.error('Proxy connection ended: %s', type(exc).__name__)
        finally:
            client_out.close()
            if generation is not None and self.session_generation == generation:
                self.discard_commands('disconnected')
                self.logged_in, self.xtea_keys = False, None
                self._reset_client_key()
                server_writer = self.server_writer
                self.client_writer = self.server_writer = None
                if server_writer:
                    server_writer.close()
                on_disconnect = self.on_game_disconnected
                if on_disconnect:
                    try:
                        on_disconnect()
                    except Exception:
                        log.exception('Game disconnect callback failed')
                for stream in (client_out, server_writer):
                    if stream:
                        try:
                            await asyncio.wait_for(stream.wait_closed(), timeout=1)
                        except (Exception, asyncio.CancelledError):
                            pass

    # ── framing ──────────────────────────────────────────────────────
    @staticmethod
    def _wrap_packet(body: bytes) -> bytes:
        """Prefix a frame body with its u16 length."""
        return struct.pack("<H", len(body)) + body

    async def _send(self, writer, body: bytes):
        writer.write(self._wrap_packet(body))
        await writer.drain()

    def _log_stream_end(self, side, stage, reason, exc=None):
        now = time.monotonic()
        def age(stamp):
            return round((now-stamp)*1000) if stamp is not None else None
        log.info("Stream ended: side=%s stage=%s reason=%s errno=%s winerror=%s "
                 "last_server_read_ms=%s last_client_forward_ms=%s", side, stage, reason,
                 getattr(exc, 'errno', None), getattr(exc, 'winerror', None),
                 age(getattr(self, 'last_server_read', None)), age(getattr(self, 'last_client_forward', None)))

    async def _read_packet(self, stream) -> bytes | None:
        """One frame body, or None when the stream ends (only the reason is logged)."""
        side = 'server' if stream is self.server_reader else 'client'
        stage = 'header'
        try:
            (size,) = struct.unpack("<H", await stream.readexactly(2))
            if not size:
                self._log_stream_end(side, stage, 'empty_frame')
                return None
            stage = 'body'
            return await stream.readexactly(size)
        except asyncio.IncompleteReadError as exc:
            self._log_stream_end(side, stage, 'eof' if stage == 'header' and not exc.partial else 'truncated')
        except OSError as exc:
            self._log_stream_end(side, stage, type(exc).__name__, exc)
        return None

    def _decrypt_game_packet(self, frame: bytes) -> bytes | None:
        return decrypt_game_packet(frame, self.xtea_keys)

    def _encrypt_game_packet(self, packet: bytes) -> bytes:
        sealed = xtea_encrypt(struct.pack("<H", len(packet)) + packet, self.xtea_keys)
        return struct.pack("<I", adler32_checksum(sealed)) + sealed

    # ── login relay ──────────────────────────────────────────────────
    async def _handle_login_session(self) -> None:
        """Forward the login, then the character list rewritten to point at the game relay."""
        request = await self._read_packet(self.client_reader)
        if request is None:
            log.error("[LOGIN] The client sent no login")
            return
        await self._send(self.server_writer, self._process_login_packet(request) or request)
        reply = await self._read_packet(self.server_reader)
        if reply is None:
            log.error("[LOGIN] The server did not answer")
            return
        rewritten = self._modify_login_response(reply) if self.xtea_keys else None
        await self._send(self.client_writer, rewritten or reply)
        log.info("[LOGIN] Character list %s", "now points at the game relay" if rewritten else "forwarded unchanged")

    def _modify_login_response(self, reply: bytes) -> bytes | None:
        try:
            return point_character_list_at_proxy(reply, self.xtea_keys)
        except Exception as exc:
            log.error("[LOGIN] Could not rewrite the character list: %s", type(exc).__name__)
            return None

    def _process_login_packet(self, data: bytes) -> bytes:
        """Learn the session keys from a login packet; the packet itself is forwarded unchanged."""
        try:
            if not data:
                return data
            if self.client_key_reader is not None:
                # 26 Nov client: only note the exact game login; keys come from client memory.
                size, head = NOVEMBER_26_LOGIN
                if len(data) == size and data[4:9] == head and _checksummed(data):
                    self._client_login_seen = True
                return data
            keys = find_session_keys(data, self._rsa_keys()) if len(data) >= RSA_BLOCK + 5 else None
            if keys is None:
                if len(data) >= RSA_BLOCK + 5:
                    log.warning("No RSA block found in the login packet")
                return data
            self.xtea_keys = keys
            self._stamp("_ts_xtea_captured")
            log.info("XTEA session keys captured (redacted)")
            return data
        except Exception as exc:
            log.error('Error processing login packet: %s', type(exc).__name__)
            return data

    # ── game relay ───────────────────────────────────────────────────
    async def _run_relay_loop(self) -> None:
        """Run both directions and the command worker until one of them stops."""
        workers = {
            asyncio.create_task(self._relay_client_to_server()): 'towards_server',
            asyncio.create_task(self._relay_server_to_client()): 'towards_client',
            asyncio.create_task(self._process_inject_queue()): 'command_worker',
        }
        try:
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                error = None if task.cancelled() else task.exception()
                if error is not None:
                    self._log_stream_end(workers[task], 'relay', type(error).__name__, error)
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def _frames(self, stream, counter: str):
        """Frame bodies read from one side, counting each in ``counter``."""
        while (body := await self._read_packet(stream)) is not None:
            setattr(self, counter, getattr(self, counter) + 1)
            yield body

    async def _relay_client_to_server(self) -> None:
        async for body in self._frames(self.client_reader, "packets_from_client"):
            server = self.server_writer
            if server is None:
                log.warning("Server connection gone; client relay stops")
                return
            if self.logged_in:
                body = self._process_client_game_packet(body)
            else:
                body = self._process_login_packet(body) or body
            await self._send(server, body)
        log.info("Client disconnected; its stream ended")

    async def _relay_server_to_client(self) -> None:
        async for body in self._frames(self.server_reader, "packets_from_server"):
            self.last_server_read = time.monotonic()
            if not (self.logged_in or self.xtea_keys is None):
                self._establish_session()
            client = self.client_writer
            if client is None:
                log.warning("Client connection gone; server relay stops")
                return
            await self._send(client, body)   # the client first, inspection after
            self.last_client_forward = time.monotonic()
            frames = [body]
            if not self.logged_in and getattr(self, 'client_key_reader', None) is not None:
                frames = self._confirm_client_key(body)
                if not frames:
                    continue
                self._establish_session()
            if self.logged_in:
                self._observe_server_frames(frames)
        log.info("Server disconnected; its stream ended")

    def _observe_server_frames(self, frames):
        tap = getattr(self, "on_encrypted_server_data", None)
        if tap:
            # The observer decrypts in its own worker, never on the forwarding path.
            try:
                for frame in frames:
                    tap(frame, self.xtea_keys, self.session_generation)
            except Exception:
                log.warning("Passive observer unavailable; relay continues")
            return
        raw_tap, packet_tap = self.on_raw_server_data, self.on_server_packet
        if not (packet_tap or raw_tap):
            return
        for frame in frames:
            plain = self._decrypt_game_packet(frame)
            if not plain:
                continue
            if raw_tap:
                with contextlib.suppress(Exception):
                    raw_tap(plain)
            if packet_tap:
                reader = PacketReader(plain)
                try:
                    packet_tap(reader.read_u8(), reader)
                except Exception as exc:
                    log.debug("Server packet callback error: %s", exc)

    def _establish_session(self):
        self.logged_in, self._ts_logged_in = True, time.time()
        log.info("=== GAME SESSION ESTABLISHED === (keys redacted)")
        on_login = self.on_login_success
        if on_login:
            on_login(self.xtea_keys)

    def _reset_client_key(self):
        self._client_login_seen = False
        self._key_attempts = 0
        self._candidate_keys = None
        self._key_frames = []

    def _confirm_client_key(self, raw):
        """Confirm two frames before publishing a pinned native key. Forwarding is already complete."""
        if not self._client_login_seen or self._key_attempts >= KEY_CHECK_ATTEMPTS:
            return []
        self._key_attempts += 1
        try:
            keys = self.client_key_reader()
            if (not isinstance(keys, tuple) or len(keys) != 4 or not any(keys)
                    or any(type(k) is not int or not 0 <= k <= 0xffffffff for k in keys)):
                raise ValueError('invalid key shape')
            if (not 12 <= len(raw) <= 65535 or (len(raw)-4) % 8
                    or struct.unpack_from('<I', raw)[0] != adler32_checksum(raw[4:])):
                raise ValueError('invalid frame')
            # The first block supplies length/opcode. Do not decrypt a large
            # payload on the relay loop; normal analysis remains in its worker.
            prefix = xtea_decrypt(raw[4:12], keys)
            length = struct.unpack_from('<H', prefix)[0]
            opcode = ServerOpcode(prefix[2])
            if not 0 < length <= len(raw)-6 or len(raw)-6-length >= 8:
                raise ValueError('invalid inner length')
            if opcode in (ServerOpcode.LOGIN_ERROR, ServerOpcode.LOGIN_WAIT, ServerOpcode.UPDATE_NEEDED):
                raise ValueError('login not accepted')
            if keys != self._candidate_keys:
                self._candidate_keys, self._key_frames = keys, []
            self._key_frames.append(raw)
            if len(self._key_frames) < 2:
                return []
            self.xtea_keys = keys
            self._stamp("_ts_xtea_captured")
            frames, self._key_frames = self._key_frames, []
            self._candidate_keys = None
            log.info('Client protocol key confirmed against two server frames (redacted)')
            return frames
        except Exception:
            self._candidate_keys, self._key_frames = None, []
            if self._key_attempts == KEY_CHECK_ATTEMPTS:
                log.warning('Client protocol key could not be confirmed; relay continues without automation')
            return []

    def _process_client_game_packet(self, frame: bytes) -> bytes:
        """Show a client packet to the bot's callbacks; the packet is forwarded unchanged."""
        if not (self._client_packet_callbacks and self.xtea_keys):
            return frame
        plain = self._decrypt_game_packet(frame)
        if not plain:
            return frame
        if len(plain) == 5 and plain[0] == ClientOpcode.ATTACK:
            # The player picked a target in the client: drop the bot's visual marker request.
            self._notify_target_marker(_CANCEL_ATTACK)
        for callback in list(self._client_packet_callbacks):
            reader = PacketReader(plain)
            try:
                callback(reader.read_u8(), reader)
            except Exception as exc:
                log.debug("Client packet callback error: %s", exc)
        return frame

    # ── bot commands ─────────────────────────────────────────────────
    async def inject_to_server(self, payload: bytes, *, guard=None):
        return self.enqueue_command('server', payload, guard)

    async def inject_to_client(self, payload: bytes) -> CommandReceipt | None:
        return self.enqueue_command('client', payload)

    def enqueue_command(self,target,payload,guard=None):
        if not getattr(self,'command_delivery',False):
            # Legacy adapters remain supported; production connections use receipts.
            if not self.injections_paused and self.logged_in and self.xtea_keys is not None:
                self._inject_queue.put_nowait((target,payload,guard) if guard is not None else (target,payload))
            return None
        self.command_sequence+=1
        receipt=CommandReceipt(self.command_sequence,self.session_generation,target,payload[0] if payload else None)
        self.command_history.append(receipt)
        if self.injections_paused:receipt.finish('rejected','automation_paused')
        elif not self.logged_in or self.xtea_keys is None:receipt.finish('rejected','not_connected')
        elif not payload:receipt.finish('rejected','empty_payload')
        elif self._inject_queue.full():receipt.finish('rejected','queue_full')
        else:self._inject_queue.put_nowait(QueuedCommand(target,payload,guard,receipt))
        return receipt

    def discard_commands(self,reason):
        while not self._inject_queue.empty():
            queued=self._inject_queue.get_nowait()
            receipt=getattr(queued,'receipt',None)
            if receipt:receipt.finish('cancelled',reason)

    def command_snapshot(self):
        return dict(session=getattr(self,'session_generation',0),queued=self._inject_queue.qsize(),
                    recent=[r.snapshot() for r in list(getattr(self,'command_history',()))[-20:]])

    def _notify_target_marker(self,payload):
        if len(payload)!=5 or payload[0]!=0xA1:return
        callback=getattr(self,'client_target_marker',None)
        if callback is not None:
            try:callback(int.from_bytes(payload[1:],'little'))
            except Exception:log.warning('Visual marker unavailable; server attack unchanged.')

    def _skip_reason(self, receipt, guard):
        """Why a queued command must not be sent now, or None to send it."""
        if self.injections_paused:
            return 'cancelled', 'automation_paused'
        if receipt and receipt.session != self.session_generation:
            return 'cancelled', 'session_changed'
        if receipt and time.monotonic() > receipt.expires:
            return 'expired', 'deadline'
        if guard is not None and not guard():
            return 'rejected', 'conditions_changed'  # the command's owner is gone or changed its mind
        return None

    async def _process_inject_queue(self) -> None:
        """Send queued commands one by one, each with a receipt of what happened."""
        while (queued := await self._inject_queue.get()) is not None:
            target, payload = queued[:2]
            guard = queued[2] if len(queued) == 3 else None
            receipt = getattr(queued, 'receipt', None)
            if receipt and receipt.status != 'queued':
                continue
            wrote = False
            try:
                skip = self._skip_reason(receipt, guard)
                if skip:
                    if receipt:
                        receipt.finish(*skip)
                    continue
                writer = self.server_writer if target == 'server' else self.client_writer if target == 'client' else None
                if writer is None:
                    if receipt:
                        receipt.finish('rejected', 'writer_unavailable')
                    log.warning("Command dropped: no %s connection", target)
                    continue
                frame = self._wrap_packet(self._encrypt_game_packet(payload))
                if receipt:
                    receipt.write_started = True
                writer.write(frame)
                wrote = True
                await asyncio.wait_for(writer.drain(), timeout=1)
                if receipt:
                    receipt.finish('dispatched')
                if target == 'server':
                    self._notify_target_marker(payload)
                log.info("Sent to %s: opcode=0x%02X len=%dB", target, payload[0], len(payload))
            except (asyncio.CancelledError, Exception) as exc:
                stopped = isinstance(exc, asyncio.CancelledError)
                if receipt:
                    receipt.finish('unknown' if wrote or receipt.write_started else 'cancelled' if stopped else 'failed',
                                   'dispatcher_stopped' if stopped else type(exc).__name__)
                if stopped:
                    raise
                log.error("Command failed: %s", type(exc).__name__)

    def pause_injections(self):
        """Latch off bot traffic without interrupting the user's relay connection."""
        self.injections_paused = True
        self._notify_target_marker(b'\xa1\0\0\0\0')
        self.discard_commands('automation_paused')

    async def send_emergency_stop(self):
        """Only cancellation packets may bypass the emergency latch."""
        from dbv.client_bridge.protocol import build_stop_walk_packet, build_attack_packet, build_follow_packet
        self.pause_injections()
        if self.logged_in and self.xtea_keys is not None and self.server_writer:
            for payload in (build_stop_walk_packet(), build_attack_packet(0), build_follow_packet(0)):
                self.server_writer.write(self._wrap_packet(self._encrypt_game_packet(payload)))
            await asyncio.wait_for(self.server_writer.drain(), timeout=1)
