"""Bancada de eficiência sustentada (revisão 2026-11-20, item 6): lote frio/quente, latência p50/p95, RAM e
concorrência real, do jeito que o worker roda (um subprocesso `python -m docredux_engine processar` por job, pool
de perfis do LibreOffice compartilhado em DOCREDUX_LO_POOL).

Uso (de engine/):
    python tests/bancada.py --saida DIR --json ARQ [--concorrencia 1,2,4] [--repeticoes 2] [--so 03,05]
           [--sem-frio] [--sem-timeout]

Fases:
  frio      pool vazio: 1ª conversão cria o perfil; a 2ª reaproveita (quente).
  lote c    para cada c em --concorrencia: os exemplos x --repeticoes, c jobs ao mesmo tempo. Latência por job,
            p50/p95, vazão (docs/min), pico de RAM do lote inteiro e de cada job (árvore: python + LibreOffice),
            processos do LibreOffice que sobraram no fim.
  timeout   mata a árvore do job (como o worker: taskkill /T ou killpg) com o LibreOffice convertendo; depois
            confere processos que sobraram e se o próximo job no mesmo pool converte.
Lê os PDFs de $DOCREDUX_FIXTURES (fora do git). RAM: Windows via kernel32 (sem dependência), Linux via /proc.
"""
import argparse
import ctypes
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))

from docredux_engine import __version__, soffice  # noqa: E402
from conftest import EXEMPLOS  # noqa: E402

LO = ("soffice", "soffice.bin", "soffice.exe", "soffice.com", "oosplash", "soffice.bin.exe")
MB = 1024 * 1024


# ----------------------------------------------------------------------------- processos e memória
if os.name == "nt":
    from ctypes import wintypes

    class _PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]

    def processos():
        """{pid: (ppid, nome)}"""
        snap = _k32.CreateToolhelp32Snapshot(2, 0)
        out, e = {}, _PE()
        e.dwSize = ctypes.sizeof(_PE)
        ok = _k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile.lower())
            ok = _k32.Process32NextW(snap, ctypes.byref(e))
        _k32.CloseHandle(snap)
        return out

    def memoria(pid):
        """(rss, pico) em bytes, ou None se o processo sumiu."""
        h = _k32.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        try:
            c = _PMC(); c.cb = ctypes.sizeof(_PMC)
            if not _k32.K32GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                return None
            return c.WorkingSetSize, c.PeakWorkingSetSize
        finally:
            _k32.CloseHandle(h)
else:
    _PAG = os.sysconf("SC_PAGE_SIZE")

    def processos():
        out = {}
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                s = (d / "stat").read_text()
            except OSError:
                continue
            nome = s[s.index("(") + 1:s.rindex(")")].lower()
            out[int(d.name)] = (int(s[s.rindex(")") + 2:].split()[1]), nome)
        return out

    def memoria(pid):
        try:
            st = Path(f"/proc/{pid}/status").read_text().splitlines()
        except OSError:
            return None
        kv = {ln.split(":")[0]: ln.split()[1] for ln in st if ":" in ln and len(ln.split()) > 1}
        return int(kv.get("VmRSS", 0)) * 1024, int(kv.get("VmHWM", 0)) * 1024


def descendentes(raiz, procs):
    filhos = {}
    for pid, (ppid, _) in procs.items():
        filhos.setdefault(ppid, []).append(pid)
    out, pilha = [], [raiz]
    while pilha:
        p = pilha.pop()
        if p in procs:
            out.append(p)
        pilha += filhos.get(p, [])
    return out


def libreoffice_vivos():
    return sorted(pid for pid, (_, n) in processos().items() if n.startswith(LO))


class Monitor:
    """Amostra a RAM (working set / RSS) de árvores de processo a cada `passo` s."""

    def __init__(self, passo=0.2):
        self.passo, self.raizes, self.pico_job, self.pico_total = passo, {}, {}, 0
        self.pico_nome = {}
        self._fim = threading.Event()
        self._t = threading.Thread(target=self._laco, daemon=True)

    def __enter__(self):
        self._t.start(); return self

    def __exit__(self, *a):
        self._fim.set(); self._t.join()

    def seguir(self, chave, pid):
        self.raizes[chave] = pid

    def largar(self, chave):
        self.raizes.pop(chave, None)

    def _laco(self):
        while not self._fim.is_set():
            procs = processos()
            total = 0
            for chave, raiz in list(self.raizes.items()):
                soma = 0
                for pid in descendentes(raiz, procs):
                    nome = procs[pid][1]
                    # no Windows o PPID de um processo antigo pode coincidir com o PID reaproveitado pelo job:
                    # só python e LibreOffice são da árvore do motor
                    if not nome.startswith(("python",) + LO):
                        continue
                    m = memoria(pid)
                    if m is None:
                        continue
                    soma += m[0]
                    self.pico_nome[nome] = max(self.pico_nome.get(nome, 0), m[1])
                self.pico_job[chave] = max(self.pico_job.get(chave, 0), soma)
                total += soma
            self.pico_total = max(self.pico_total, total)
            self._fim.wait(self.passo)


# ----------------------------------------------------------------------------- jobs
def _matar_arvore(p):
    """Igual a api/worker/motor.py: a árvore inteira (LibreOffice incluído)."""
    if p.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()


def rodar_job(chave, pdf, perfil, saida, mon, matar_com_lo=False):
    """Um job como o worker: subprocesso novo do motor, grupo de processo próprio. Devolve a medição."""
    shutil.rmtree(saida, ignore_errors=True); saida.mkdir(parents=True)
    cmd = [sys.executable, "-m", "docredux_engine", "processar", str(pdf), str(saida), "--perfil", perfil]
    extra = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=str(AQUI.parent), **extra)
    mon.seguir(chave, p.pid)
    morto = False
    try:
        if matar_com_lo:
            while p.poll() is None:
                procs = processos()
                if any(procs[pid][1].startswith(LO) for pid in descendentes(p.pid, procs)):
                    time.sleep(1.0)  # LibreOffice já convertendo
                    _matar_arvore(p); morto = True
                    break
                time.sleep(0.1)
        out, _ = p.communicate()
    finally:
        mon.largar(chave)
    dt = time.perf_counter() - t0
    r = {"job": chave, "s": round(dt, 2), "pico_mb": round(mon.pico_job.get(chave, 0) / MB), "codigo": p.returncode}
    if morto:
        r["morto"] = True
        return r
    try:
        res = json.loads(out.decode("utf-8").strip().splitlines()[-1])
        r["gate"] = res.get("relatorio", {}).get("gate") or res.get("gate")
        r["tempos_s"] = res.get("relatorio", {}).get("tempos_s") or res.get("tempos_s")
    except (ValueError, IndexError):
        r["erro"] = "saida invalida"
    return r


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    a, b = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[a] + (xs[b] - xs[a]) * (k - a), 2)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", required=True)
    ap.add_argument("--json")
    ap.add_argument("--concorrencia", default="1,2,4")
    ap.add_argument("--repeticoes", type=int, default=2)
    ap.add_argument("--so", help="prefixos dos exemplos, ex.: 03,05")
    ap.add_argument("--sem-frio", action="store_true")
    ap.add_argument("--sem-timeout", action="store_true")
    a = ap.parse_args(argv)

    base = Path(os.environ.get("DOCREDUX_FIXTURES") or sys.exit("defina DOCREDUX_FIXTURES"))
    saida = Path(a.saida).resolve()
    pool = saida / "_pool"
    shutil.rmtree(pool, ignore_errors=True)
    os.environ[soffice.POOL_ENV] = str(pool)  # herdado pelos subprocessos
    exemplos = {n: v for n, v in EXEMPLOS.items() if not a.so or n in a.so.split(",")}
    pdfs = {n: sorted((base / v[0] / "original").glob("*.pdf"))[0] for n, v in exemplos.items()}

    res = {"ambiente": {"motor": __version__, "python": platform.python_version(), "so": platform.platform(),
                        "cpus": os.cpu_count(), "libreoffice": soffice.versao(), "max_perfis": soffice.MAX_PERFIS},
           "lo_antes": len(libreoffice_vivos())}
    print(json.dumps(res["ambiente"], ensure_ascii=False), flush=True)
    with Monitor() as mon:
        if not a.sem_frio:
            n = "03" if "03" in pdfs else next(iter(pdfs))
            frio = [rodar_job(f"frio-{i}", pdfs[n], exemplos[n][2], saida / f"frio{i}", mon) for i in range(2)]
            res["frio"] = {"exemplo": n, "frio": frio[0], "quente": frio[1]}
            print("frio", json.dumps(res["frio"], ensure_ascii=False), flush=True)
        res["lotes"] = []
        for c in [int(x) for x in a.concorrencia.split(",")]:
            fila = [(n, r) for r in range(a.repeticoes) for n in pdfs]
            mon.pico_total = 0
            t0 = time.perf_counter()
            with ThreadPoolExecutor(c) as ex:
                feitos = list(ex.map(lambda nr: {"exemplo": nr[0], **rodar_job(
                    f"c{c}-{nr[0]}-{nr[1]}", pdfs[nr[0]], exemplos[nr[0]][2], saida / f"c{c}" / f"{nr[0]}-{nr[1]}", mon)},
                    fila))
            total = time.perf_counter() - t0
            por = {}
            for f in feitos:
                por.setdefault(f["exemplo"], []).append(f)
            lote = {"concorrencia": c, "jobs": len(feitos), "total_s": round(total, 1),
                    "docs_por_min": round(len(feitos) / total * 60, 2),
                    "pico_ram_lote_mb": round(mon.pico_total / MB),
                    "falhas": [f for f in feitos if f.get("codigo") or f.get("gate") != "ok"],
                    "por_exemplo": {n: {"p50_s": pct([x["s"] for x in v], .5), "max_s": max(x["s"] for x in v),
                                        "pico_mb": max(x["pico_mb"] for x in v)} for n, v in sorted(por.items())},
                    "latencia_p50_s": pct([f["s"] for f in feitos], .5),
                    "latencia_p95_s": pct([f["s"] for f in feitos], .95),
                    "lo_vivos_depois": len(libreoffice_vivos()) - res["lo_antes"], "jobs_detalhe": feitos}
            res["lotes"].append(lote)
            print("lote", json.dumps({k: v for k, v in lote.items() if k not in ("falhas", "jobs_detalhe")},
                                     ensure_ascii=False),
                  "falhas", len(lote["falhas"]), flush=True)
        if not a.sem_timeout:
            n = "03" if "03" in pdfs else next(iter(pdfs))
            morto = rodar_job("timeout", pdfs[n], exemplos[n][2], saida / "timeout", mon, matar_com_lo=True)
            time.sleep(2)
            sobra = len(libreoffice_vivos()) - res["lo_antes"]
            depois = rodar_job("pos-timeout", pdfs[n], exemplos[n][2], saida / "pos-timeout", mon)
            res["timeout"] = {"morto": morto, "lo_vivos_apos_matar": sobra, "proximo_job": depois,
                              "perfis_no_pool": sorted(p.name for p in pool.iterdir() if p.is_dir())}
            print("timeout", json.dumps(res["timeout"], ensure_ascii=False), flush=True)
        res["pico_por_processo_mb"] = {k: round(v / MB) for k, v in sorted(mon.pico_nome.items())}
    res["lo_fim"] = len(libreoffice_vivos()) - res["lo_antes"]
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"pico_por_processo_mb": res["pico_por_processo_mb"], "lo_fim": res["lo_fim"]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
