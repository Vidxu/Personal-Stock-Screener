"""
Cross-platform desktop alert: system notification + on-screen toast + alert sound.

Works on macOS, Windows, and Linux without extra pip packages.

Usage:
    from alert_notify import alert

    alert("Screener done", "Found 12 matching stocks")

    # Custom sound length (seconds)
    alert("Price alert", "RELIANCE crossed ₹2,500", sound_seconds=3.0)

CLI test:
    python alert_notify.py
    python alert_notify.py "My title" "My message"
"""

from __future__ import annotations

import math
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from xml.sax.saxutils import escape

DEFAULT_SOUND_SECONDS = 2.5
DEFAULT_OVERLAY_SECONDS = 4.0


def alert(
    title: str = "Stock Screener Alert",
    message: str = "Something needs your attention.",
    *,
    sound_seconds: float = DEFAULT_SOUND_SECONDS,
    show_overlay: bool = True,
    overlay_seconds: float = DEFAULT_OVERLAY_SECONDS,
) -> None:
    """Show a system-wide alert (notification + optional overlay + sound)."""
    title = str(title)
    message = str(message)
    sound_seconds = max(1.0, min(float(sound_seconds), 10.0))

    threading.Thread(
        target=_play_alert_sound,
        args=(sound_seconds,),
        daemon=True,
    ).start()

    threading.Thread(
        target=_show_system_notification,
        args=(title, message),
        daemon=True,
    ).start()

    if show_overlay:
        threading.Thread(
            target=_show_on_screen_overlay,
            args=(title, message, overlay_seconds),
            daemon=True,
        ).start()


# ── Sound ─────────────────────────────────────────────────────────────────────

def _play_alert_sound(duration: float) -> None:
    """Play a two-tone alert WAV for roughly `duration` seconds."""
    wav_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name
        _write_alert_wav(wav_path, duration)
        _play_wav_file(wav_path)
    except Exception:
        _beep_fallback(duration)
    finally:
        if wav_path:
            try:
                Path(wav_path).unlink(missing_ok=True)
            except Exception:
                pass


def _write_alert_wav(path: str, duration: float, sample_rate: int = 22050) -> None:
    """Generate a short alternating-tone alert and write it as a WAV file."""
    tone_a, tone_b = 880.0, 1175.0  # A5 + D6 — clear alert pair
    pulse = 0.18
    gap = 0.07
    samples: list[int] = []
    t = 0.0

    while t < duration:
        for freq in (tone_a, tone_b):
            if t >= duration:
                break
            seg = min(pulse, duration - t)
            samples.extend(_sine_samples(freq, seg, sample_rate))
            t += seg
            if t >= duration:
                break
            gap_seg = min(gap, duration - t)
            samples.extend([0] * int(sample_rate * gap_seg))
            t += gap_seg

    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _sine_samples(freq: float, duration: float, sample_rate: int) -> list[int]:
    n = int(sample_rate * duration)
    amp = 16000
    return [
        int(amp * math.sin(2 * math.pi * freq * i / sample_rate))
        for i in range(n)
    ]


def _play_wav_file(path: str) -> None:
    system = platform.system()

    if system == "Darwin":
        if shutil.which("afplay"):
            subprocess.run(["afplay", path], check=False)
            return

    if system == "Windows":
        import winsound

        winsound.PlaySound(path, winsound.SND_FILENAME)
        return

    # Linux and other Unix-like systems
    for player in ("paplay", "aplay", "ffplay"):
        if not shutil.which(player):
            continue
        cmd = [player, path]
        if player == "ffplay":
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
        if player == "aplay":
            cmd = ["aplay", "-q", path]
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    if system == "Darwin" and shutil.which("osascript"):
        subprocess.run(["osascript", "-e", "beep 3"], check=False)


def _beep_fallback(duration: float) -> None:
    """Last-resort alert when no audio backend is available."""
    system = platform.system()
    if system == "Windows":
        import winsound

        end = __import__("time").time() + duration
        while __import__("time").time() < end:
            winsound.Beep(880, 200)
            __import__("time").sleep(0.12)
            winsound.Beep(1175, 200)
            __import__("time").sleep(0.12)
        return

    count = max(3, int(duration / 0.4))
    for _ in range(count):
        print("\a", end="", flush=True)
        __import__("time").sleep(0.35)


# ── System notification (visible in any app) ──────────────────────────────────

def _show_system_notification(title: str, message: str) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            _notify_macos(title, message)
        elif system == "Windows":
            _notify_windows(title, message)
        else:
            _notify_linux(title, message)
    except Exception:
        pass


def _notify_macos(title: str, message: str) -> None:
    t = escape(title).replace('"', '\\"')
    m = escape(message).replace('"', '\\"')
    script = f'display notification "{m}" with title "{t}" sound name "Glass"'
    subprocess.run(["osascript", "-e", script], check=False)


def _notify_windows(title: str, message: str) -> None:
    t = escape(title)
    m = escape(message)
    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@"
<toast duration="long">
  <visual>
    <binding template="ToastText02">
      <text id="1">{t}</text>
      <text id="2">{m}</text>
    </binding>
  </visual>
  <audio src="ms-winsoundevent:Notification.Default"/>
</toast>
"@)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Stock Screener").Show($toast)
"""
    subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", ps],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _notify_linux(title: str, message: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-a", "Stock Screener", "-u", "critical", title, message],
            check=False,
        )


# ── Always-on-top on-screen overlay ───────────────────────────────────────────

def _show_on_screen_overlay(title: str, message: str, seconds: float) -> None:
    """Spawn a small always-on-top window in a child process (safe with Flask)."""
    helper = f'''
import tkinter as tk

title = {title!r}
message = {message!r}
seconds = {seconds!r}

root = tk.Tk()
root.withdraw()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.configure(bg="#0f1117")

if root.tk.call("tk", "windowingsystem") == "aqua":
    root.attributes("-transparent", True)
    frame = tk.Frame(root, bg="#161a24", highlightthickness=2, highlightbackground="#2dd4a8")
else:
    frame = tk.Frame(root, bg="#161a24", highlightthickness=2, highlightbackground="#2dd4a8")

frame.pack(fill="both", expand=True)

accent = tk.Frame(frame, bg="#2dd4a8", height=4)
accent.pack(fill="x")

inner = tk.Frame(frame, bg="#161a24", padx=20, pady=16)
inner.pack(fill="both", expand=True)

tk.Label(
    inner, text="⚠  " + title, font=("Helvetica", 13, "bold"),
    fg="#2dd4a8", bg="#161a24", anchor="w", justify="left",
).pack(fill="x")

tk.Label(
    inner, text=message, font=("Helvetica", 11),
    fg="#e8eaef", bg="#161a24", anchor="w", justify="left", wraplength=360,
).pack(fill="x", pady=(8, 0))

root.update_idletasks()
w, h = 400, max(100, root.winfo_reqheight())
x = (root.winfo_screenwidth() - w) // 2
y = root.winfo_screenheight() - h - 60
root.geometry(f"{{w}}x{{h}}+{{x}}+{{y}}")
root.deiconify()
root.after(int(seconds * 1000), root.destroy)
root.mainloop()
'''
    subprocess.Popen(
        [sys.executable, "-c", helper],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else "Stock Screener Alert"
    m = sys.argv[2] if len(sys.argv) > 2 else "This is a test alert — sound + notification + on-screen toast."
    print(f"Firing alert: {t!r} — {m!r}")
    alert(t, m)
    __import__("time").sleep(DEFAULT_SOUND_SECONDS + 1)
