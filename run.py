"""Uma rodada do atualizador da dash, rodando no GitHub Actions (sem o PC ligado).
Fluxo (igual ao atualizar-dash.ps1 do PC): baixa o último bloco cifrado do painel, lê o Meta, atualiza e envia de volta.
Segredos (variáveis de ambiente): META_ACCESS_TOKEN, DASH_PW, PUSH_TOKEN, CONFIG_JSON ({arquivo: conteúdo})."""
import os, sys, json, time, subprocess, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BRT = datetime.timezone(datetime.timedelta(hours=-3))
now = datetime.datetime.now(BRT)
dia = now.date().isoformat()
BASE = "https://ads.apostamuito.com/lives/data.enc.json"


def log(msg):
    print(now.strftime("%d/%m %H:%M"), msg, flush=True)


def py(script, *args, timeout=240):
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, script), *args], capture_output=True, text=True,
                       timeout=timeout, cwd=HERE, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def node(*args, env=None, timeout=120):
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(["node", os.path.join(HERE, "update-enc.js"), *args], capture_output=True, text=True,
                       timeout=timeout, cwd=HERE, env=e, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout + r.stderr).strip()


# pausa da madrugada (02:00 às 07:00), como no PC; FORCAR=1 ignora
if 2 <= now.hour < 7 and not os.environ.get("FORCAR"):
    log("pausa da madrugada (02:00 às 07:00)")
    sys.exit(0)

# configurações que mudam pouco (contas, reatribuições, instagram dos experts, limite do cartão...) vêm do segredo CONFIG_JSON
cfg = json.loads(os.environ.get("CONFIG_JSON") or "{}")
for nome, conteudo in cfg.items():
    with open(os.path.join(HERE, nome), "w", encoding="utf-8") as f:
        f.write(conteudo if isinstance(conteudo, str) else json.dumps(conteudo, ensure_ascii=False))

# bloco cifrado atual do painel (estado de ontem/hoje, histórico, FIN...)
req = urllib.request.Request(BASE + "?t=%d" % time.time(), headers={"User-Agent": "dash-lives"})
base = urllib.request.urlopen(req, timeout=30).read()
if len(base) < 1000:
    log("bloco do painel veio vazio; não envio")
    sys.exit(1)
open(os.path.join(HERE, "data.enc.json"), "wb").write(base)

ok, ultimo = node("data.enc.json", "x.json", env={"EXTRACT": "1"})
if not ok:
    log("não consegui abrir o bloco do painel (senha?): " + ultimo[-200:])
    sys.exit(1)
ultimo = ultimo.splitlines()[-1].strip()

if ultimo != dia:  # virou o dia: lê o fechamento de ontem (update-enc.js aplica e apaga ontem.json)
    log("virou o dia (%s -> %s): lendo o fechamento de ontem" % (ultimo, dia))
    ok, out = py("fetch-spend.py", "ontem")
    if not ok:
        log("não consegui ler o fechamento de ontem: " + out[-200:])
        sys.exit(1)

lido = False
for t in (1, 2):
    ok1, o1 = py("fetch-spend.py")
    ok2, o2 = (py("fetch-charges.py") if ok1 else (False, ""))
    if ok1 and ok2:
        try:
            lidas = json.loads(o1.splitlines()[-1])["contas_lidas"]
            conhecidas = len(json.load(open(os.path.join(HERE, "accounts-cache.json"), encoding="utf-8")))
        except Exception:
            lidas, conhecidas = 0, 1
        if lidas >= conhecidas:
            lido = True
            log("gasto lido (%d contas)" % lidas)
            break
        log("leitura incompleta (%d de %d contas) - tentativa %d" % (lidas, conhecidas, t))
    else:
        log("falha na leitura: " + (o1 + " " + o2)[:200].replace("\n", " "))
    if t == 1:
        time.sleep(60)
if not lido:
    log("NÃO enviei: leitura do Meta falhou ou veio incompleta (a dash segue com os dados anteriores)")
    sys.exit(1)

ok, out = py("status-live.py")
if not ok:
    log("status-live falhou (segue sem PAUSADO/ESTENDIDO)")

ok, out = node("data.enc.json", "novo.enc.json")
log(out.replace("\n", " | ")[:300])
if not ok or "push apostamuito: 200" not in out:
    log("o envio ao painel não foi confirmado")
    sys.exit(1)
