"""Windows Pause/Break only; OS hotkey registration, no keyboard capture/input."""
import asyncio
import sys
import threading
import time


class PauseHotkey:
    def __init__(self, loop, callback):
        self.loop=loop;self.callback=callback;self.registered=False;self.error=None
        self.thread_id=None;self.busy=False;self.last_press=float('-inf')

    def snapshot(self):return {'registered':self.registered,'error':self.error,'key':'Pause/Break'}

    def press(self):
        now=time.monotonic()
        if self.busy or now-self.last_press<.8:return
        self.last_press=now;self.busy=True
        async def toggle():
            try:
                await self.callback();self.error=None
            except Exception as exc:self.error=str(exc)[:240]
            finally:self.busy=False
        self.loop.call_soon_threadsafe(lambda:asyncio.create_task(toggle()))

    def start(self):
        if sys.platform!='win32':self.error='Pause/Break wymaga Windows.';return
        threading.Thread(target=self._run,name='pause-break-hotkey',daemon=True).start()

    def _run(self):
        import ctypes
        from ctypes import wintypes
        user=ctypes.WinDLL('user32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        user.RegisterHotKey.argtypes=[wintypes.HWND,ctypes.c_int,wintypes.UINT,wintypes.UINT]
        user.RegisterHotKey.restype=wintypes.BOOL
        user.GetMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT]
        user.GetMessageW.restype=ctypes.c_int
        user.UnregisterHotKey.argtypes=[wintypes.HWND,ctypes.c_int]
        user.UnregisterHotKey.restype=wintypes.BOOL
        kernel.GetCurrentThreadId.restype=wintypes.DWORD
        self.thread_id=kernel.GetCurrentThreadId()
        key_id=0x4DB1
        # MOD_NOREPEAT; VK_PAUSE. This one key is reserved while the bot runs.
        if not user.RegisterHotKey(None,key_id,0x4000,0x13):
            self.error='Pause/Break jest zajęty lub niedostępny. Użyj pauzy w panelu.';return
        self.registered=True
        try:
            msg=wintypes.MSG()
            while True:
                result=user.GetMessageW(ctypes.byref(msg),None,0,0)
                if result<=0:break
                if msg.message==0x0312 and msg.wParam==key_id:self.press()
        finally:
            user.UnregisterHotKey(None,key_id);self.registered=False

    def stop(self):
        if self.thread_id and sys.platform=='win32':
            import ctypes
            from ctypes import wintypes
            post=ctypes.windll.user32.PostThreadMessageW
            post.argtypes=[wintypes.DWORD,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
            post.restype=wintypes.BOOL
            post(self.thread_id,0x0012,0,0)
