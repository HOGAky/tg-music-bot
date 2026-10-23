"""Read-only мониторинг tcp/22 на VM + попытка SSH-подключения.

Ничего не меняет на VM: только TCP-проверки порта и read-only команды.

Настройка через переменные окружения (значения по умолчанию — прод-хост проекта):
  SSH_MONITOR_HOST   IP или домен VM      (по умолчанию 84.201.167.96)
  SSH_MONITOR_KEY    путь к приватному ключу (.deploy/deploy_key в корне проекта)
  SSH_MONITOR_USER   основной SSH-пользователь (yc-user)
  SSH_MONITOR_EXE    путь к ssh.exe       (Windows: Git for Windows)
"""

import datetime
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

HOST = os.getenv("SSH_MONITOR_HOST", "84.201.167.96")
PORT = 22
SSH_EXE = os.getenv("SSH_MONITOR_EXE", r"C:\Program Files\Git\usr\bin\ssh.exe")
KEY = os.getenv("SSH_MONITOR_KEY", str(PROJECT_ROOT / ".deploy" / "deploy_key"))
PRIMARY_USER = os.getenv("SSH_MONITOR_USER", "yc-user")
FALLBACK_USER = "hogaky"
MAX_ITER = 25  # ~25 итераций
PAUSE_SEC = 20  # пауза между итерациями
SOCKET_TIMEOUT = 5  # timeout python socket
SSH_SUBPROC_TIMEOUT = 75  # предохранитель на одну SSH-команду

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ssh_monitor.log")

# лог прошлого запуска обрезаем; дозапись ведёт log() через контекстный менеджер
with open(LOG_PATH, "w", encoding="utf-8"):
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as e:
    logging.getLogger(__name__).debug("не удалось переконфигурировать вывод: %s", e)


def log(msg):
    ts = (
        datetime.datetime.now(datetime.timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )
    line = f"{ts} {msg}"
    try:
        print(line, flush=True)
    except Exception as e:
        line = f"{line} [stdout недоступен: {e}]"  # в файл строка уйдёт целиком
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def check_port():
    """TCP-проверка порта через python socket, timeout 5с."""
    try:
        with socket.create_connection((HOST, PORT), timeout=SOCKET_TIMEOUT):
            return True, "OPEN"
    except TimeoutError:
        return False, "TIMEOUT(5s)"
    except OSError as e:
        return False, f"ERR:{type(e).__name__}"
    return False, "UNKNOWN"


def ssh_run(user, cmd):
    """Одна SSH-команда через Git ssh.exe, BatchMode, только чтение."""
    args = [
        SSH_EXE,
        "-i",
        KEY,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        f"{user}@{HOST}",
        cmd,
    ]
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=SSH_SUBPROC_TIMEOUT,
            check=False,
        )
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "LOCAL_SUBPROC_TIMEOUT(75s)"


def classify(rc, out, err):
    if rc == 0 and "SSH_OK" in out:
        return "ok"
    e = (err or "").lower()
    if "permission denied" in e:
        return "denied"
    if "timed out" in e or "timeout" in e or rc == 124:
        return "unreachable"
    if (
        "connection refused" in e
        or "connection reset" in e
        or "connection closed" in e
        or "reset by peer" in e
    ):
        return "conn_fail"
    if "host key" in e:
        return "hostkey"
    return f"error(rc={rc})"


def run_followups(user):
    """Read-only команды после успешного SSH_OK."""
    cmds = [
        "docker ps --format '{{.Names}}: {{.Status}}'",
        "ls ~/musicbot | head -20",
        "ls ~ | head -20",
        "uptime",
    ]
    for cmd in cmds:
        rc, out, err = ssh_run(user, cmd)
        log(f"[{user}] $ {cmd}  -> rc={rc}")
        if out:
            log("stdout: " + out.replace("\n", "\n  "))
        if err:
            log("stderr: " + err.replace("\n", "\n  "))
        if rc != 0:
            time.sleep(5)
            rc, out, err = ssh_run(user, cmd)
            log(f"[{user}] повтор: $ {cmd}  -> rc={rc}")
            if out:
                log("stdout: " + out.replace("\n", "\n  "))
            if err:
                log("stderr: " + err.replace("\n", "\n  "))


def main():
    log(
        f"СТАРТ: мониторинг tcp/{PORT} на {HOST} — до {MAX_ITER} итераций, "
        f"пауза {PAUSE_SEC}с, socket timeout {SOCKET_TIMEOUT}с"
    )
    log(f"SSH-клиент: {SSH_EXE}")
    log(f"Ключ: {KEY}")
    opens = 0
    ssh_attempts = 0
    for i in range(1, MAX_ITER + 1):
        ok, detail = check_port()
        if ok:
            opens += 1
        log(f"итерация {i:02d}/{MAX_ITER}: tcp/{PORT} -> {detail}")
        if ok:
            ssh_attempts += 1
            log(
                f"итерация {i:02d}: порт ОТКРЫТ -> немедленная SSH-попытка ({PRIMARY_USER})"
            )
            rc, out, err = ssh_run(PRIMARY_USER, "echo SSH_OK; hostname")
            log(
                f"ssh {PRIMARY_USER} 'echo SSH_OK; hostname': rc={rc} | stdout={out!r} | stderr={err!r}"
            )
            st = classify(rc, out, err)
            if st == "ok":
                log(f"РЕЗУЛЬТАТ: SSH_OK, юзер {PRIMARY_USER}, hostname-вывод: {out}")
                run_followups(PRIMARY_USER)
                log(
                    f"ИТОГ: итераций={i} порт_открыт_раз={opens} "
                    f"ssh_попыток={ssh_attempts} результат=УСПЕХ({PRIMARY_USER})"
                )
                return 0
            if st == "denied":
                log(
                    f"Permission denied для {PRIMARY_USER} -> пробую тот же ключ с юзером {FALLBACK_USER}"
                )
                rc2, out2, err2 = ssh_run(FALLBACK_USER, "echo SSH_OK; hostname")
                log(
                    f"ssh {FALLBACK_USER} 'echo SSH_OK; hostname': rc={rc2} | stdout={out2!r} | stderr={err2!r}"
                )
                st2 = classify(rc2, out2, err2)
                if st2 == "ok":
                    log(
                        f"РЕЗУЛЬТАТ: SSH_OK, юзер {FALLBACK_USER}, hostname-вывод: {out2}"
                    )
                    run_followups(FALLBACK_USER)
                    log(
                        f"ИТОГ: итераций={i} порт_открыт_раз={opens} "
                        f"ssh_попыток={ssh_attempts} результат=УСПЕХ({FALLBACK_USER})"
                    )
                    return 0
                if st2 == "denied":
                    log(
                        f"ОТКАЗ: Permission denied у ОБЕИХ юзеров ({PRIMARY_USER}, {FALLBACK_USER}) — "
                        "ключ не принят сервером. Стоп."
                    )
                    log(
                        f"ИТОГ: итераций={i} порт_открыт_раз={opens} "
                        f"ssh_попыток={ssh_attempts} результат=ОТКАЗ(permission denied)"
                    )
                    return 3
                st = st2
            if st == "hostkey":
                log(
                    "ОТКАЗ: проблема host key (verification failed / changed). "
                    "Стоп — требуется ручное решение."
                )
                log(f"ИТОГ: итераций={i} результат=ОТКАЗ(hostkey)")
                return 4
            log(
                f"итерация {i:02d}: SSH не прошёл ({st}) — похоже на сетевой флап, "
                "продолжаю цикл"
            )
        if i < MAX_ITER:
            time.sleep(PAUSE_SEC)
    log(
        f"ИТОГ: итераций={MAX_ITER} порт_открыт_раз={opens} "
        f"ssh_попыток={ssh_attempts} результат=ПОРТ_НЕ_ОТКРЫЛСЯ"
    )
    return 2


if __name__ == "__main__":
    rc = main()
    log(f"СКРИПТ ЗАВЕРШЁН код={rc}")
    sys.exit(rc)
