# -*- coding: utf-8 -*-
"""Клиент именованного пайпа мода HK_AI_Mod (протокол v3).

Мод (C#) держит сервер пайпа ``\\\\.\\pipe\\hk_ai_mod`` и рассылает телеметрию
строками JSON (newline-delimited). Python открывает пайп по RAW-дескриптору
(``os.open`` + ``os.read``/``os.write``): читает телеметрию в фоновом потоке и
пишет текстовые команды в тот же дескриптор.

Почему не ``open(path, "r+b")``: это даёт ``BufferedRandom``, который на
именованном пайпе Windows отдаёт первый считанный чанк и навсегда зависает на
следующем ``readline()``. Сырые ``os.read``/``os.write`` работают корректно и
дают собственную построчную разборку потока.

Формат сообщений мода (одна строка = один JSON-объект):
    {"status": "pipe_hello", "protocol": 3, "mod_version": "1.3"}   — при подключении
    {"status": "fight", "restart_pending": 0, "scene": "GG_False_Knight", "hp": 9, ...}
    {"status": "main_menu" | "loading_scene" | "initialized" | ...}  — служебные
    {"status": "boss_list", "event": 1, "count": 60, "bosses": [...]}   — ответ на "bosses"
    {"status": "boss_selected", "event": 1, "scene": "...", ...}        — выбор босса принят
    {"status": "command_error", "event": 1, "command": "...", ...}      — команда отклонена

Телеметрия идёт потоком (~60/сек), поэтому хранится только последний кадр.
Одноразовые события (boss_list/boss_selected/command_error) помечены полем
``"event": 1``, кэшируются по статусу и НЕ подменяют последнюю телеметрию —
их можно дождаться через :meth:`HKPipeClient.wait_for_status`.

Команды Python -> мод (plain text, одна строка = одна команда):
    restart [scene] [gate]   — быстрый рестарт боя ( BeginSceneTransition )
    teleport                 — то же, что restart
    set_boss <scene>         — целевая сцена босса (понимает алиасы)
    set_gate <gate>          — точка входа на арену
    boss <запрос>            — выбрать босса пантеона и телепортироваться к нему
    bosses                   — прислать реестр боссов событием boss_list
    warp                     — вернуть героя к гейту арены без перезагрузки сцены
"""

import json
import os
import threading
import time

# Имя пайпа. HK_PIPE_NAME переопределяет его для стенда (tests/pipe_sim):
# макет слушает hk_ai_mod_sim, чтобы не занять пайп запущенной игры.
PIPE_PATH = "\\\\.\\pipe\\" + os.environ.get("HK_PIPE_NAME", "hk_ai_mod")

# Минимальная версия протокола, на которой есть команды пантеона.
REQUIRED_PROTOCOL = 3

_RETRY_OPEN_DELAY = 0.5
_READ_CHUNK = 65536
# Защита от потока мусора без переводов строк.
_MAX_LINE_BUFFER = 1 << 20

_O_BINARY = getattr(os, "O_BINARY", 0)


class HKPipeClient:
    """Постоянное подключение к пайпу мода с авто-реконнектом.

    Потокобезопасно: фоновый поток-читатель держит последний разобранный
    JSON (``_latest``), монотонный счётчик сообщений (``_seq``) и кэш
    одноразовых событий по полю ``status``.
    """

    def __init__(self, pipe_path=PIPE_PATH, reconnect_delay=_RETRY_OPEN_DELAY, verbose=True):
        self.pipe_path = pipe_path
        self.reconnect_delay = reconnect_delay
        self.verbose = verbose

        self._cond = threading.Condition()
        self._latest = None          # последний кадр телеметрии (без событий)
        self._last_any = None        # последнее сообщение любого типа (для отладки)
        self._seq = 0                # сколько сообщений пришло всего
        self._by_status = {}         # status -> последнее сообщение с этим статусом
        self._status_seq = {}        # status -> seq, на котором оно пришло
        self._hello = None           # hello-сообщение мода
        self._connected = False
        self._stop = False

        self._send_lock = threading.Lock()
        self._fd = None              # raw-дескриптор пайпа (r+w)

        self._thread = threading.Thread(target=self._run, daemon=True, name="HKPipeClient")
        self._thread.start()

    # ------------------------------------------------------------------ API

    @property
    def is_connected(self):
        with self._cond:
            return self._connected

    @property
    def hello(self):
        """hello-сообщение мода (None, пока оно не пришло)."""
        with self._cond:
            return self._hello

    @property
    def protocol(self):
        """Версия протокола мода или None."""
        with self._cond:
            hello = self._hello
            return hello.get("protocol") if hello else None

    @property
    def mod_version(self):
        """Версия мода из hello или None."""
        with self._cond:
            hello = self._hello
            return hello.get("mod_version") if hello else None

    def wait_connected(self, timeout=15.0):
        """Блокирует до открытия пайпа (или до таймаута). True = дескриптор открыт."""
        deadline = time.perf_counter() + timeout
        with self._cond:
            while not self._connected:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def wait_hello(self, timeout=3.0):
        """Ждёт hello-сообщение мода (в нём версия протокола). None по таймауту."""
        deadline = time.perf_counter() + timeout
        with self._cond:
            while self._hello is None:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            return self._hello

    def get_telemetry(self):
        """Последний разобранный JSON телеметрии или None (нет связи).

        Одноразовые события сюда НЕ попадают — только кадры наблюдений (и
        служебные статусы мода вроде loading_scene/main_menu).
        """
        with self._cond:
            return self._latest

    def get_last_message(self):
        """Последнее сообщение мода любого типа, включая события (для отладки)."""
        with self._cond:
            return self._last_any

    def get_seq(self):
        """Монотонный счётчик принятых сообщений (аналог старого mtime файла)."""
        with self._cond:
            return self._seq

    def get_status(self, status):
        """Последнее сообщение с данным ``status`` или None."""
        with self._cond:
            return self._by_status.get(status)

    def get_status_seq(self, status):
        """На каком seq пришло последнее сообщение с данным ``status`` (None — не было).

        Пара к :meth:`wait_for_status` с ``after_seq``: запомни seq до отправки
        команды, чтобы дождаться именно её ответа, а не старого.
        """
        with self._cond:
            return self._status_seq.get(status)

    def wait_for_status(self, status, timeout=5.0, after_seq=None):
        """Ждёт сообщение с данным ``status``. None по таймауту.

        ``after_seq`` — вернуть только сообщение новее указанного seq.
        """
        deadline = time.perf_counter() + timeout
        with self._cond:
            while True:
                seq = self._status_seq.get(status)
                if seq is not None and (after_seq is None or seq > after_seq):
                    return self._by_status.get(status)
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def wait_for_fresh(self, last_seq, timeout=0.15):
        """Ждёт НОВОЕ сообщение от мода. Возвращает актуальный seq.

        Если за timeout ничего не пришло (меню/пауза/нет связи) — возвращает
        прежний last_seq, как старый wait_for_fresh_telemetry по mtime.
        """
        with self._cond:
            if self._seq != last_seq:
                return self._seq
            self._cond.wait(timeout)
            return self._seq

    def send_command(self, text):
        """Отправить команду моду ('restart GG_False_Knight door_dreamEnter' и т.п.).

        True — строка ушла в пайп (мод заберёт её в течение кадра-двух).
        """
        payload = (text.rstrip("\n") + "\n").encode("utf-8")
        with self._send_lock:
            fd = self._fd
            if fd is None:
                return False
            try:
                while payload:
                    written = os.write(fd, payload)
                    if written <= 0:
                        return False
                    payload = payload[written:]
                return True
            except OSError:
                # пайп отвалился прямо во время записи — читатель реконнектится
                return False

    def stop(self):
        """Останавливает клиента и разблокирует поток-читатель."""
        self._stop = True
        with self._send_lock:
            fd = self._fd
            self._fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        with self._cond:
            self._cond.notify_all()

    # ------------------------------------------------------------ внутреннее

    def _log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    def _open_pipe(self):
        """Открывает пайп по raw-дескриптору. None, если сервера нет/не отвечает."""
        while not self._stop:
            try:
                # O_RDWR = GENERIC_READ|GENERIC_WRITE: сервер у нас duplex.
                fd = os.open(self.pipe_path, os.O_RDWR | _O_BINARY)
                return fd
            except FileNotFoundError:
                pass          # мод ещё не создал пайп (игра не запущена)
            except OSError:
                pass          # ERROR_PIPE_BUSY и прочее — тоже ждём
            time.sleep(self.reconnect_delay)
        return None

    def _run(self):
        while not self._stop:
            fd = self._open_pipe()
            if fd is None:
                break
            with self._send_lock:
                self._fd = fd
            self._set_connected(True)
            self._log(f"[PIPE] Подключено: {self.pipe_path}")
            try:
                self._read_loop(fd)
            except OSError:
                pass
            finally:
                with self._send_lock:
                    self._fd = None
                try:
                    os.close(fd)
                except OSError:
                    pass
                # hello относится к конкретному соединению — сбрасываем, чтобы
                # protocol/mod_version не пережили переподключение.
                with self._cond:
                    self._hello = None
                self._set_connected(False)
            if self._stop:
                break
            self._log("[PIPE] Соединение потеряно, переподключаюсь...")
            time.sleep(self.reconnect_delay)

    def _read_loop(self, fd):
        buffer = b""
        while True:
            # os.read на пайпе отдаёт доступные байты, блокируясь только пока
            # данных нет вообще; EOF — пустой результат.
            chunk = os.read(fd, _READ_CHUNK)
            if not chunk:
                raise OSError("pipe eof")

            if buffer:
                buffer += chunk
            else:
                buffer = chunk

            while True:
                idx = buffer.find(b"\n")
                if idx < 0:
                    break
                line = buffer[:idx]
                buffer = buffer[idx + 1:]
                self._handle_line(line)

            if len(buffer) > _MAX_LINE_BUFFER:
                buffer = buffer[-1024:]

    def _handle_line(self, raw):
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            return
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return  # обрезанная строка — пропускаем, мод шлёт построчно
        with self._cond:
            self._seq += 1
            self._last_any = data
            status = data.get("status")
            if status:
                self._by_status[status] = data
                self._status_seq[status] = self._seq
                if status == "pipe_hello":
                    self._hello = data
            # Одноразовые события (метка "event": 1) не подменяют последнюю
            # телеметрию: иначе шаг RL получил бы JSON без hp/x/y.
            if not data.get("event"):
                self._latest = data
            self._cond.notify_all()

    def _set_connected(self, value):
        with self._cond:
            self._connected = value
            self._cond.notify_all()


# ---------------- Общий клиент на процесс ----------------
# Мод держит до 4 клиентов, но один общий клиент проще и надёжнее: телеметрию
# читает training-цикл, а команды (рестарт/босс/варп) шлёт кто угодно через
# bosses.py — и всё это через одно соединение.

_shared_client = None
_shared_lock = threading.Lock()


def get_shared_client(verbose=True):
    """Процесс-синглтон клиента пайпа."""
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            _shared_client = HKPipeClient(verbose=verbose)
        return _shared_client


def send_command(text):
    """Отправить команду моду через общий клиент."""
    return get_shared_client().send_command(text)


def get_telemetry():
    """Последняя телеметрия через общий клиент."""
    return get_shared_client().get_telemetry()


def is_connected():
    return get_shared_client().is_connected


if __name__ == "__main__":
    # Быстрая проверка: python hk_pipe.py — печатаем живую телеметрию 5 секунд.
    client = HKPipeClient()
    print("Ждём мод (\\\\.\\pipe\\hk_ai_mod), до 10 секунд...")
    if not client.wait_connected(10.0):
        print("Мод не ответил. Игра запущена? Мод HK_AI_Mod.dll установлен?")
        raise SystemExit(1)

    hello = client.wait_hello(3.0) or {}
    print(f"Hello: protocol={hello.get('protocol')} mod_version={hello.get('mod_version')}")
    if (hello.get("protocol") or 0) < REQUIRED_PROTOCOL:
        print(f"ВНИМАНИЕ: команды пантеона требуют протокол >= {REQUIRED_PROTOCOL}. "
              f"Обнови DLL мода.")

    end = time.time() + 5.0
    last = -1
    while time.time() < end:
        seq = client.get_seq()
        if seq != last:
            last = seq
            print(seq, client.get_telemetry())
        time.sleep(0.05)
