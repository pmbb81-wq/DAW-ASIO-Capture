"""Test auto-routingu (zakladka Monitoring): watek tla sam podpina czytnik,
gdy proxy sie pojawia, i sam rozpinaw, gdy znika. Bez prawdziwego urzadzenia:
shm_quick jest podmieniony na symulacje, GUI zastapiony stubem.
"""
import queue
import threading
import time

import axeio_obs_manager as m

now = time.time


class FakeRoot:
    def after(self, ms, cb, *a):
        return cb(*a)


class FakeApp:
    """Instancja App bez GUI - tylko pola, ktorych uzywa _router_loop."""

    # ta sama implementacja co w App - testujemy prawdziwy tekst statusu
    _proxy_status_txt = m.App._proxy_status_txt

    def __init__(self):
        self.root = FakeRoot()
        self.mon = None
        self._ctl = m.DirectCtl()
        self._attach_fail_at = 0.0
        self._mon_err = ""
        self._au_spin = 0
        self._dev_refresh_at = 0.0
        self._rstop = threading.Event()
        self._rlock = threading.Lock()
        self._gui_q = queue.Queue()
        self._rdata = {"diag": "", "mon": "", "auto": True, "want_dev": "",
                       "cfg": (0, 0, 1, 0.0, 10),
                       "last_wp": None, "stall": 0.0, "refresh_dev": False,
                       "want_attach": False, "want_release": False,
                       "render_proxy": False}
        self.attach_n = 0
        self.release_n = 0

    def _ask_release(self):
        with self._rlock:
            self._rdata["want_release"] = True

    def _mon_attach(self):
        self.attach_n += 1
        self.mon = "_RUNNING_"

    def _mon_release(self):
        self.release_n += 1
        self.mon = None

    def service(self):
        """To samo co _mon_service_tick w watku GUI (bez after())."""
        with self._rlock:
            want = self._rdata.get("want_attach")
            rel = self._rdata.get("want_release")
            self._rdata["want_attach"] = False
            self._rdata["want_release"] = False
        if rel:
            self._mon_release()
        if want and self.mon is None:
            self._mon_attach()


def fake_shm_machine():
    """(ok, sr, nch, active, wp) zaleznie od czasu:
      0-1s  idzie, 1-3s obecny i leci, 3-5s obecny ale stoi,
      5s+   znikl (ma sie rozpiac po ~4 s nieobecnosci)."""
    t0 = now()

    def f():
        el = now() - t0
        if el < 1.0:
            return False, 0, 0, 0, 0
        if el < 3.0:
            return True, 48000, 2, 1, int(el * 48000)
        if el < 5.0:
            return True, 48000, 2, 1, 480000
        return False, 0, 0, 0, 0

    return f


def _run(app, seconds):
    """Watek routera + petla serwisu GUI, przez `seconds` sekund."""
    t = threading.Thread(target=lambda: m.App._router_loop(app), daemon=True)
    t.start()
    end = now() + seconds
    while now() < end:
        app.service()
        time.sleep(0.15)
    app._rstop.set()
    t.join(timeout=3)


def scenario_manager():
    """Domyslny tryb: router sam podpina strumien, gdy proxy zagra,
    i sam rozpinaw, gdy zniknie."""
    m.shm_quick = fake_shm_machine()
    m.out_devices = lambda: []
    app = FakeApp()
    _run(app, 11.0)

    print("  attach=%d release=%d mon=%r" % (app.attach_n, app.release_n, app.mon))
    print("  stall=%.2f last_wp=%r" % (app._rdata.get("stall"),
                                       app._rdata.get("last_wp")))

    ok = True
    if app.attach_n != 1:
        print("  BLAD: oczekiwano 1 attach (proxy sie pojawilo), a jest %d"
              % app.attach_n)
        ok = False
    if app.attach_n != app.release_n:
        print("  BLAD: attach(%d) != release(%d) - strumien nie wolno znikac"
              % (app.attach_n, app.release_n))
        ok = False
    if app.mon is not None:
        print("  BLAD: po zniknieciu proxy mon i tak dziala")
        ok = False
    return ok


def scenario_proxy():
    """Tryb direct monitoring: rendererem jest watek w DAW, wiec router
    NIGDY nie moze podpiac wlasnego strumienia - dwa zrodla gralyby to
    samo z roznych opoznien. Strumien podpiety wczesniej ma sie rozpiac."""
    m.shm_quick = fake_shm_machine()
    m.out_devices = lambda: []
    app = FakeApp()
    app._rdata["render_proxy"] = True
    app.mon = "_RUNNING_"          # podpiety w trybie manager
    _run(app, 6.0)                 # SHM jest obecny (1-5 s)

    print("  attach=%d release=%d mon=%r" % (app.attach_n, app.release_n, app.mon))
    print("  status=%r" % app._rdata.get("mon"))

    ok = True
    if app.attach_n != 0:
        print("  BLAD: direct monitoring a router podpiety strumien (%d)"
              % app.attach_n)
        ok = False
    if app.release_n < 1 or app.mon is not None:
        print("  BLAD: stary strumien nie zostal rozpiety (release=%d, mon=%r)"
              % (app.release_n, app.mon))
        ok = False
    if not str(app._rdata.get("mon", "")).startswith("PROXY DIRECT"):
        print("  BLAD: brak statusu direct z wodka DAW: %r"
              % app._rdata.get("mon"))
        ok = False
    return ok


def main():
    print(" tryb manager:")
    ok1 = scenario_manager()
    print()
    print(" tryb proxy (direct monitoring):")
    ok2 = scenario_proxy()
    print()
    if not (ok1 and ok2):
        print("  WYNIK: BLADY")
        return 1
    print("  WYNIK: WSZYSTKO OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())