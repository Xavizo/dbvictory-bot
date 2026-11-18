"""
OT Protocol Proxy - Sits between the game client and server.

Handles TWO phases:
1. LOGIN phase (port 7171): Intercepts login, extracts XTEA keys,
   modifies character list response to redirect game server to our proxy.
2. GAME phase (port 7172): Intercepts game traffic, allows packet injection.
"""

import asyncio
import struct
import logging
import time
from collections import deque
from command_delivery import CommandReceipt, QueuedCommand
from packet_observer import decrypt_game_packet
from crypto import (
    rsa_decrypt, rsa_encrypt, xtea_decrypt, xtea_encrypt,
    adler32_checksum, generate_proxy_rsa_keypair, get_default_rsa_key,
    DEFAULT_RSA_N, DEFAULT_RSA_E
)
from protocol import PacketReader, PacketWriter, ServerOpcode, ClientOpcode
from constants import SERVER_IP_BYTES, SERVER_IP_STR

log = logging.getLogger("proxy")


class OTProxy:
    """
    TCP proxy for Open Tibia protocol.

    Handles both login and game connections.
    """

    def __init__(self, server_host: str, server_port: int, listen_port: int,
                 is_login_proxy: bool = False, shared_rsa_key=None):
        self.server_host = server_host
        self.server_port = server_port
        self.listen_port = listen_port
        self.is_login_proxy = is_login_proxy

        # Use the default OTClient RSA key - we know the private key!
        # No need to patch the client, just decrypt with the known key.
        self.default_rsa_key = get_default_rsa_key()

        # Lazy-init proxy RSA key only if needed (keygen is expensive)
        self._shared_rsa_key = shared_rsa_key
        self._proxy_rsa_key = None
        self.server_rsa_n = DEFAULT_RSA_N
        self.server_rsa_e = DEFAULT_RSA_E

        # Session state
        self.xtea_keys = None
        self.logged_in = False

        # Connection handles
        self.client_reader = None
        self.client_writer = None
        self.server_reader = None
        self.server_writer = None

        # Callbacks for bot
        self.on_server_packet = None          # legacy — only bot.py/start.py use it
        self._client_packet_callbacks = []    # list of (opcode, reader) callables
        self.on_login_success = None
        self.on_game_disconnected = None
        self.on_raw_server_data = None  # Called with full decrypted bytes
        self.on_encrypted_server_data = None  # Nonblocking passive tap after forwarding.
        self._inject_queue = asyncio.Queue(maxsize=256)
        self.command_delivery = True
        self.session_generation = 0
        self.command_sequence = 0
        self.command_history = deque(maxlen=100)
        self.injections_paused = False

        # Stats
        self.packets_from_server = 0
        self.packets_from_client = 0

        # Track the active connection handler task for clean shutdown
        self._connection_task: asyncio.Task | None = None
        self._server: asyncio.AbstractServer | None = None

        # Timestamps for proxy sequence debugging
        self._ts_created = time.time()
        self._ts_listening = None
        self._ts_client_connected = None
        self._ts_server_connected = None
        self._ts_xtea_captured = None
        self._ts_logged_in = None


    # ── Client packet callback registry ────────────────────────────
    def register_client_packet_callback(self, callback):
        if callback not in self._client_packet_callbacks:
            self._client_packet_callbacks.append(callback)

    def unregister_client_packet_callback(self, callback):
        try:
            self._client_packet_callbacks.remove(callback)
        except ValueError:
            pass

    @property
    def on_client_packet(self):
        return self._client_packet_callbacks[0] if self._client_packet_callbacks else None

    @on_client_packet.setter
    def on_client_packet(self, callback):
        if callback is None:
            self._client_packet_callbacks.clear()
        elif self._client_packet_callbacks:
            self._client_packet_callbacks[0] = callback
        else:
            self._client_packet_callbacks.append(callback)

    @property
    def proxy_rsa_key(self):
        if self._proxy_rsa_key is None:
            self._proxy_rsa_key = self._shared_rsa_key or generate_proxy_rsa_keypair()
        return self._proxy_rsa_key

    async def start(self):
        """Start the proxy server."""
        server = await asyncio.start_server(
            self._handle_client_connection,
            '127.0.0.1',
            self.listen_port
        )
        self._server = server
        self._ts_listening = time.time()
        addr = server.sockets[0].getsockname()
        mode = "LOGIN" if self.is_login_proxy else "GAME"
        log.info(f"[{mode}] Proxy listening on {addr[0]}:{addr[1]} -> {self.server_host}:{self.server_port}")

        async with server:
            await server.serve_forever()

    def close_server(self):
        """Explicitly close the listening server socket (for clean reset)."""
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None

    async def _handle_client_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Serialize ownership changes; an old handler cannot clear a newer session."""
        if not hasattr(self,'_handover_lock'):self._handover_lock=asyncio.Lock()
        generation=None
        try:
            async with self._handover_lock:
                previous=self._connection_task
                if previous and previous is not asyncio.current_task() and not previous.done():
                    previous.cancel()
                    await asyncio.gather(previous,return_exceptions=True)
                self._connection_task=asyncio.current_task()
                self.session_generation+=1;generation=self.session_generation
                self.discard_commands('session_changed')
                self.client_reader=reader;self.client_writer=writer
                self.server_reader=None;self.server_writer=None
                self.logged_in=False;self.xtea_keys=None
                self._ts_client_connected=time.time()
            self.server_reader,self.server_writer=await asyncio.open_connection(self.server_host,self.server_port)
            self._ts_server_connected=time.time()
            if self.is_login_proxy:await self._handle_login_session()
            else:await self._run_relay_loop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error('Proxy connection ended: %s',type(exc).__name__)
        finally:
            writer.close()
            if generation is not None and self.session_generation==generation:
                self.discard_commands('disconnected');self.logged_in=False;self.xtea_keys=None
                server_writer=self.server_writer
                self.client_writer=None;self.server_writer=None
                if server_writer:server_writer.close()
                if self.on_game_disconnected:
                    try:self.on_game_disconnected()
                    except Exception:log.exception('Game disconnect callback failed')
                for stream in (writer,server_writer):
                    if stream:
                        try:await asyncio.wait_for(stream.wait_closed(),timeout=1)
                        except (Exception,asyncio.CancelledError):pass

    async def _handle_login_session(self):
        """
        Handle a login session:
        1. Client sends login packet -> we intercept RSA, extract XTEA, re-encrypt, forward
        2. Server sends character list -> we decrypt, modify game server IP, re-encrypt, forward
        """
        # Step 1: Client login packet
        raw = await self._read_packet(self.client_reader)
        if raw is None:
            log.error("[LOGIN] No login packet received")
            return

        log.info(f"[LOGIN] Received login packet ({len(raw)} bytes)")

        processed = self._process_login_packet(raw)
        if processed is not None:
            self.server_writer.write(self._wrap_packet(processed))
            await self.server_writer.drain()
            log.info("[LOGIN] Forwarded login packet to server")
        else:
            self.server_writer.write(self._wrap_packet(raw))
            await self.server_writer.drain()
            log.warning("[LOGIN] Could not process login packet, forwarded as-is")

        # Step 2: Server response (character list)
        response = await self._read_packet(self.server_reader)
        if response is None:
            log.error("[LOGIN] No response from server")
            return

        log.info(f"[LOGIN] Received server response ({len(response)} bytes)")

        if self.xtea_keys:
            modified = self._modify_login_response(response)
            if modified:
                self.client_writer.write(self._wrap_packet(modified))
                await self.client_writer.drain()
                log.info("[LOGIN] Forwarded modified character list (game IP -> 127.0.0.1)")
                return

        # Forward as-is if we couldn't modify
        self.client_writer.write(self._wrap_packet(response))
        await self.client_writer.drain()
        log.info("[LOGIN] Forwarded response as-is")

    def _modify_login_response(self, data: bytes) -> bytes | None:
        """
        Decrypt the login response, modify game server IP to localhost,
        re-encrypt and return.
        """
        try:
            # Detect checksum
            has_checksum = False
            offset = 0
            if len(data) > 4:
                checksum = struct.unpack_from('<I', data, 0)[0]
                computed = adler32_checksum(data[4:])
                if checksum == computed:
                    has_checksum = True
                    offset = 4

            encrypted = data[offset:]
            if len(encrypted) % 8 != 0:
                log.warning("[LOGIN] Response not aligned to 8 bytes")
                return None

            decrypted = xtea_decrypt(encrypted, self.xtea_keys)
            inner_len = struct.unpack_from('<H', decrypted, 0)[0]
            payload = decrypted[2:2 + inner_len]

            log.info(f"[LOGIN] Decrypted response: {inner_len} bytes, first byte=0x{payload[0]:02X}")

            # Replace server IP in the response
            # The character list contains IP addresses as 4-byte values (packed IP)
            # and also as strings in some protocol versions
            server_ip_bytes = SERVER_IP_BYTES
            localhost_bytes = bytes([127, 0, 0, 1])

            # Also try string replacement
            server_ip_str = SERVER_IP_STR
            localhost_str = b"127.0.0.1" + b"\x00" * max(0, len(SERVER_IP_STR) - 9)

            modified_payload = bytearray(payload)
            replaced = False

            # Replace packed IP (4 bytes)
            idx = 0
            while True:
                idx = modified_payload.find(server_ip_bytes, idx)
                if idx == -1:
                    break
                modified_payload[idx:idx + 4] = localhost_bytes
                log.info(f"[LOGIN] Replaced packed IP at offset {idx}")
                replaced = True
                idx += 4

            # Replace string IP
            idx = 0
            while True:
                idx = modified_payload.find(server_ip_str, idx)
                if idx == -1:
                    break
                modified_payload[idx:idx + len(server_ip_str)] = localhost_str[:len(server_ip_str)]
                log.info(f"[LOGIN] Replaced string IP at offset {idx}")
                replaced = True
                idx += len(server_ip_str)

            if not replaced:
                log.warning("[LOGIN] No server IP found in character list to replace!")
                log.warning("[LOGIN] The response may use a different IP format.")
                # Try to log the raw payload for debugging
                log.debug(f"[LOGIN] Payload hex: {payload.hex()}")

            # Re-encrypt
            new_inner = struct.pack('<H', len(modified_payload)) + bytes(modified_payload)
            # Pad to match original
            if len(new_inner) < len(decrypted):
                new_inner = new_inner + decrypted[len(new_inner):]

            re_encrypted = xtea_encrypt(new_inner, self.xtea_keys)

            if has_checksum:
                new_checksum = adler32_checksum(re_encrypted)
                return struct.pack('<I', new_checksum) + re_encrypted
            else:
                return re_encrypted

        except Exception as e:
            log.error(f"[LOGIN] Error modifying response: {e}")
            log.debug("Login response modification traceback:", exc_info=True)
            return None

    async def _read_packet(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read a full OT protocol packet (length-prefixed)."""
        try:
            header = await reader.readexactly(2)
            length = struct.unpack('<H', header)[0]
            if length == 0 or length > 65535:
                return None
            data = await reader.readexactly(length)
            return data
        except (asyncio.IncompleteReadError, ConnectionError):
            return None

    def _wrap_packet(self, data: bytes) -> bytes:
        """Wrap data with length header."""
        return struct.pack('<H', len(data)) + data

    async def _run_relay_loop(self):
        """Supervised relay loop. Creates 3 relay tasks and monitors them."""
        while True:
            client_task = asyncio.create_task(self._relay_client_to_server())
            server_task = asyncio.create_task(self._relay_server_to_client())
            inject_task = asyncio.create_task(self._process_inject_queue())
            tasks = {client_task, server_task, inject_task}

            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                for t in tasks:
                    t.cancel()
                for t in tasks:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
                raise

            # Normal end: client or server disconnected
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            break

    async def _relay_client_to_server(self):
        """Relay packets from client to server, intercepting login."""
        while True:
            raw = await self._read_packet(self.client_reader)
            if raw is None:
                log.info("Client disconnected")
                break

            self.packets_from_client += 1

            if self.server_writer is None:
                log.warning("Server writer gone, stopping client relay")
                break

            if not self.logged_in:
                # Login phase: extract XTEA keys
                processed = self._process_login_packet(raw)
                if processed is not None:
                    self.server_writer.write(self._wrap_packet(processed))
                    await self.server_writer.drain()
                else:
                    self.server_writer.write(self._wrap_packet(raw))
                    await self.server_writer.drain()
            else:
                processed = self._process_client_game_packet(raw)
                self.server_writer.write(self._wrap_packet(processed))
                await self.server_writer.drain()

    async def _relay_server_to_client(self):
        """Relay packets from server to client."""
        while True:
            raw = await self._read_packet(self.server_reader)
            if raw is None:
                log.info("Server disconnected")
                break

            self.packets_from_server += 1

            if not self.logged_in and self.xtea_keys is not None:
                self.logged_in = True
                self._ts_logged_in = time.time()
                log.info("=== GAME SESSION ESTABLISHED ===")
                log.info("XTEA session keys captured (redacted)")
                if self.on_login_success:
                    self.on_login_success(self.xtea_keys)

            # Forward to game client FIRST — minimize client-side latency.
            # Decrypt + scan callbacks run AFTER the client already has the data.
            if self.client_writer is None:
                log.warning("Client writer gone, stopping server relay")
                break
            self.client_writer.write(self._wrap_packet(raw))
            await self.client_writer.drain()

            # The MCP observer owns bounded asynchronous analysis. Never decrypt
            # a large server frame on the socket-forwarding path in this mode.
            tap = getattr(self, "on_encrypted_server_data", None)
            if self.logged_in and tap:
                try:
                    tap(raw, self.xtea_keys, self.session_generation)
                except Exception:
                    log.warning("Passive observer unavailable; relay continues")
                continue

            # Legacy bot.py/start.py callbacks retain their existing interface.
            if self.logged_in and (self.on_server_packet or self.on_raw_server_data):
                try:
                    decrypted = self._decrypt_game_packet(raw)
                    if decrypted:
                        if self.on_raw_server_data:
                            try:
                                self.on_raw_server_data(decrypted)
                            except Exception:
                                pass
                        if self.on_server_packet:
                            pr = PacketReader(decrypted)
                            if pr.remaining > 0:
                                opcode = pr.read_u8()
                                try:
                                    self.on_server_packet(opcode, pr)
                                except Exception as e:
                                    log.debug(f"Server packet callback error: {e}")
                except Exception:
                    pass

    def _process_login_packet(self, data: bytes) -> bytes | None:
        """
        Process a login packet from the client.
        Extract XTEA keys from the RSA-encrypted block.

        Since the client uses the default OTClient RSA key and we know the
        private key, we just decrypt to extract XTEA keys and forward as-is.
        No re-encryption needed - the server has the same key.
        """
        try:
            has_checksum = False
            if len(data) > 4:
                checksum = struct.unpack_from('<I', data, 0)[0]
                computed = adler32_checksum(data[4:])
                if checksum == computed:
                    has_checksum = True

            offset = 4 if has_checksum else 0

            proto_byte = data[offset]
            log.info(f"Login packet: proto=0x{proto_byte:02X}, size={len(data)}, checksum={'yes' if has_checksum else 'no'}")

            # Skip small packets (handshake/challenge packets, not login)
            rsa_block_size = 128
            if len(data) < rsa_block_size + offset + 5:
                log.debug(f"Packet too small for RSA ({len(data)} bytes), forwarding as-is")
                return data  # Forward as-is

            # Try to find and decrypt the RSA block using the DEFAULT key
            # (the client encrypted with the default OTClient RSA public key)
            keys_to_try = [self.default_rsa_key, self.proxy_rsa_key]

            for key in keys_to_try:
                # Try the last 128 bytes first (most common RSA block position
                # in OT login packets), then fall back to brute-force iteration
                last_offset = len(data) - rsa_block_size
                offsets_to_try = [last_offset] if last_offset > offset else []
                offsets_to_try += [
                    o for o in range(offset + 1, len(data) - rsa_block_size + 1)
                    if o != last_offset
                ]

                for try_offset in offsets_to_try:
                    rsa_block = data[try_offset:try_offset + rsa_block_size]
                    try:
                        decrypted = rsa_decrypt(rsa_block, key)
                        if decrypted[0] == 0x00:
                            key_name = "DEFAULT" if key == self.default_rsa_key else "PROXY"
                            log.info(f"RSA block found at offset {try_offset} (using {key_name} key)")

                            xtea_data = decrypted[1:17]
                            self.xtea_keys = struct.unpack('<4I', xtea_data)
                            self._ts_xtea_captured = time.time()
                            log.info("XTEA session keys captured (redacted)")

                            # Log additional info from the decrypted block
                            try:
                                pr = PacketReader(decrypted[17:])
                                gm_flag = decrypted[17] if len(decrypted) > 17 else 0
                                log.debug(f"GM flag: {gm_flag}")
                            except Exception:
                                pass

                            # Forward the original packet as-is
                            # (server can decrypt it with the same RSA private key)
                            return data
                    except Exception:
                        continue

            log.warning("Could not find RSA block in login packet!")
            log.warning(f"Packet hex (first 32 bytes): {data[:32].hex()}")
            return data  # Forward as-is anyway

        except Exception as e:
            log.error(f"Error processing login packet: {e}")
            return data

    def _decrypt_game_packet(self, data: bytes) -> bytes | None:
        """Decrypt an XTEA-encrypted game packet."""
        return decrypt_game_packet(data, self.xtea_keys)

    def _encrypt_game_packet(self, payload: bytes) -> bytes:
        """Encrypt a game packet with XTEA for sending to server."""
        data = struct.pack('<H', len(payload)) + payload
        encrypted = xtea_encrypt(data, self.xtea_keys)
        checksum = adler32_checksum(encrypted)
        return struct.pack('<I', checksum) + encrypted

    def _process_client_game_packet(self, data: bytes) -> bytes:
        """Process a game packet from the client (inspect, forward)."""
        if self._client_packet_callbacks and self.xtea_keys:
            try:
                decrypted = self._decrypt_game_packet(data)
                if decrypted:
                    if len(decrypted)==5 and decrypted[0]==0xA1:self._notify_target_marker(b'\xa1\0\0\0\0')
                    for cb in list(self._client_packet_callbacks):
                        try:
                            pr = PacketReader(decrypted)
                            if pr.remaining > 0:
                                opcode = pr.read_u8()
                                cb(opcode, pr)
                        except Exception as e:
                            log.debug(f"Client packet callback error: {e}")
            except Exception:
                pass

        return data

    async def inject_to_server(self, payload: bytes, *, guard=None):
        """Inject a packet to the game server."""
        return self.enqueue_command('server', payload, guard)

    async def inject_to_client(self, payload: bytes):
        """Inject a packet to the client."""
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

    async def _process_inject_queue(self):
        """Process queued packet injections."""
        while True:
            queued = await self._inject_queue.get()
            target, payload = queued[:2]
            receipt=getattr(queued,'receipt',None)
            wrote=False
            if receipt and receipt.status!='queued':continue
            if self.injections_paused:
                if receipt:receipt.finish('cancelled','automation_paused')
                continue
            if receipt and receipt.session!=self.session_generation:
                receipt.finish('cancelled','session_changed');continue
            if receipt and time.monotonic()>receipt.expires:
                receipt.finish('expired','deadline');continue
            try:
                if len(queued)==3 and queued[2] is not None and not queued[2]():
                    if receipt:receipt.finish('rejected','conditions_changed')
                    continue  # Cancelled/expired navigation must not outlive its owner.
                encrypted = self._encrypt_game_packet(payload)
                packet = self._wrap_packet(encrypted)

                if target == 'server' and self.server_writer:
                    if receipt:receipt.write_started=True
                    self.server_writer.write(packet);wrote=True
                    await asyncio.wait_for(self.server_writer.drain(),timeout=1)
                    if receipt:receipt.finish('dispatched')
                    self._notify_target_marker(payload)
                    log.info(f"Injected to server: opcode=0x{payload[0]:02X} len={len(payload)}B")
                elif target == 'client' and self.client_writer:
                    if receipt:receipt.write_started=True
                    self.client_writer.write(packet);wrote=True
                    await asyncio.wait_for(self.client_writer.drain(),timeout=1)
                    if receipt:receipt.finish('dispatched')
                    log.info(f"Injected to client: opcode=0x{payload[0]:02X} len={len(payload)}B")
                else:
                    if receipt:receipt.finish('rejected','writer_unavailable')
                    log.warning(f"Inject DROPPED: target={target} "
                                f"server_writer={self.server_writer is not None} "
                                f"client_writer={self.client_writer is not None}")
            except asyncio.CancelledError:
                if receipt:receipt.finish('unknown' if wrote or receipt.write_started else 'cancelled','dispatcher_stopped')
                raise
            except Exception as e:
                if receipt:receipt.finish('unknown' if wrote or receipt.write_started else 'failed',type(e).__name__)
                log.error(f"Injection error: {type(e).__name__}")

    def pause_injections(self):
        """Latch off bot traffic without interrupting the user's relay connection."""
        self.injections_paused = True
        self._notify_target_marker(b'\xa1\0\0\0\0')
        self.discard_commands('automation_paused')

    async def send_emergency_stop(self):
        """Only cancellation packets may bypass the emergency latch."""
        from protocol import build_stop_walk_packet, build_attack_packet, build_follow_packet
        self.pause_injections()
        if self.logged_in and self.xtea_keys is not None and self.server_writer:
            for payload in (build_stop_walk_packet(), build_attack_packet(0), build_follow_packet(0)):
                self.server_writer.write(self._wrap_packet(self._encrypt_game_packet(payload)))
            await asyncio.wait_for(self.server_writer.drain(), timeout=1)

    def get_proxy_rsa_public_key(self) -> str:
        """Get the proxy's RSA public key as a decimal string."""
        return str(self.proxy_rsa_key.n)

