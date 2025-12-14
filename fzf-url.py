#!/usr/bin/env python3


import os
import sys
import shlex
import subprocess
import re
from urllib.parse import urlparse

RS = os.linesep  # equivalente di $RS / $INPUT_RECORD_SEPARATOR
MAX_URL_LENGTH = 8192  # lunghezza massima sicura per URL


def executable(*commands):
    for c in commands:
        cmd = c.split()[0]
        # usa la shell per avere il builtin `command -v`
        rc = subprocess.run(
            ["/bin/sh", "-c", f"command -v {shlex.quote(cmd)}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        if rc == 0:
            return c
    return None


def halt(message):
    subprocess.run(
        ["tmux", "display-message", message],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sys.exit(0)


def with_command(command, fn):
    # equivalente di IO.popen(command, 'r+') che passa dalla shell
    proc = None
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        old_stdout = sys.stdout
        sys.stdout = proc.stdin
        try:
            try:
                fn()
            except BrokenPipeError:
                pass  # come rescue Errno::EPIPE => nil
        finally:
            sys.stdout = old_stdout

        proc.stdin.close()
        output = proc.stdout.read().splitlines()
        proc.stdout.close()
        proc.wait()
        return output
    except Exception:
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait()
        return []


# TODO: Keep it simple for now
def extract_urls(line):
    potential_urls = re.findall(
        r"(?:https?|file)://[-a-zA-Z0-9@:%_+.~#?&/=]+[-a-zA-Z0-9@%_+.~#?&/=!]+",
        line,
    )
    # Valida e sanitizza gli URL
    valid_urls = []
    for url in potential_urls:
        # Limita lunghezza URL
        if len(url) > MAX_URL_LENGTH:
            continue
        # Valida che sia un URL parsabile
        try:
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https", "file") and parsed.netloc or parsed.path:
                valid_urls.append(url)
        except Exception:
            continue
    return valid_urls


try:
    lines = subprocess.check_output(
        ["tmux", "capture-pane", "-J", "-p", "-S", "-99999"],
        text=True,
        stderr=subprocess.DEVNULL,
    )
except (subprocess.CalledProcessError, FileNotFoundError):
    print("Error: tmux not available or not running in tmux session", file=sys.stderr)
    sys.exit(1)

urls = []
for line in lines.splitlines():
    line = line.strip()
    if not line:
        continue
    urls.extend(extract_urls(line))

# reverse + uniq preservando ordine come in Ruby
urls = list(dict.fromkeys(reversed(urls)))
if not urls:
    halt("No URLs found")

header = "Press CTRL-Y to copy URL to clipboard"

try:
    client_size = subprocess.check_output(
        ["tmux", "display-message", "-p", "#{client_width} #{client_height}"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).split()
    max_size = list(map(int, client_size))
except (subprocess.CalledProcessError, ValueError):
    max_size = [80, 24]  # fallback a dimensioni di default

width = max(len(u) for u in urls + [header]) + 2 + 4 + 2
height = len(urls) + 5 + 1 + 1
size = f"{min(width, max_size[0])},{min(height, max_size[1])}"

opts = [
    "--tmux",
    size,
    "--multi",
    "--no-margin",
    "--no-padding",
    "--wrap",
    "--expect",
    "ctrl-y",
    "--style",
    "default",
    "--header",
    header,
    "--header-border",
    "top",
    "--highlight-line",
    "--header-first",
    "--info",
    "inline-right",
    "--padding",
    "1,1,0,1",
    "--border-label",
    " URLs ",
]
opts = " ".join(shlex.quote(o) for o in opts)

selected = with_command(f"fzf {opts}", lambda: print("\n".join(urls)))
if len(selected) < 2:
    sys.exit(0)

if selected[0] == "ctrl-y":
    copier = executable(
        "reattach-to-user-namespace pbcopy",
        "pbcopy",
        "wl-copy",
        "xsel --clipboard --input",
        "xclip -selection clipboard",
    )
    if not copier:
        halt("No command to control clipboard with")

    def copy():
        # Limita dimensione totale del testo copiato
        text = "\n".join(selected[1:]).strip()
        if len(text) > MAX_URL_LENGTH * 10:  # max 10 URL lunghi
            text = text[: MAX_URL_LENGTH * 10]
        sys.stdout.write(text)

    result = with_command(copier, copy)
    if result is not None:
        halt("Copied to clipboard")
    else:
        halt("Error copying to clipboard")

opener = executable("open", "xdg-open")
if not opener:
    halt("No command to open URL with")

# Usa subprocess.run senza shell=True per evitare injection
for url in selected[1:]:
    try:
        if opener == "open":
            subprocess.run(
                ["open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        elif opener == "xdg-open":
            # xdg-open può essere lento, usa nohup per non bloccare
            subprocess.Popen(
                ["nohup", "xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass  # ignora errori nell'apertura di singoli URL
