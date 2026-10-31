"""Gasto de hoje (por conta e por live) + agenda das lives, em TODAS as contas que o token enxerga.
Grava hoje.json, lives.json e agenda.json na pasta da dash.   Uso: python -I fetch-spend.py [YYYY-MM-DD]"""
import sys, os, json, re, datetime, socket
socket.setdefaulttimeout(45)  # nunca ficar pendurado numa chamada de rede
MODO_ONTEM = len(sys.argv) > 1 and sys.argv[1] == "ontem"  # grava ontem.json (fechamento do dia anterior) e sai
PRESET = "yesterday" if MODO_ONTEM else "today"
SK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SK)
import common  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BRT = datetime.timezone(datetime.timedelta(hours=-3))
now = datetime.datetime.now(BRT)
# nomes gravados historicamente na dash para as contas antigas (mantém o histórico consistente)
try:  # nomes gravados historicamente para contas antigas (config privada: old-names.json)
    OLD = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "old-names.json"), encoding="utf-8-sig"))
except Exception:
    OLD = {}
ALIAS = {"VICTOSAMPAIO": "VICTORSAMPAIO", "RONY": "RONYLEITE", "RONY LEITE": "RONYLEITE", "MACRO": "MARCO"}  # erro de digitação no nome das campanhas
LIVE = re.compile(r"\[LIVE\]\[([^\]]+)\]\[(\d{1,2}:\d{2})-(\d{1,2}:\d{2})\]")


def lst(path, params):
    try:
        r = common.get_all(path, params)
        return r if isinstance(r, list) else r.get("data", [])
    except BaseException:  # noqa: BLE001
        return None


accs = {}
for x in lst("me/adaccounts", {"fields": "id,name,business,account_status", "limit": 200}) or []:
    accs[x["id"]] = {"nome": x.get("name"), "bm": (x.get("business") or {}).get("name"), "st": x.get("account_status")}
for b in lst("me/businesses", {"fields": "id,name", "limit": 100}) or []:
    for edge in ("owned_ad_accounts", "client_ad_accounts"):
        for x in lst(f"{b['id']}/{edge}", {"fields": "id,name,account_status", "limit": 200}) or []:
            accs.setdefault(x["id"], {"nome": x.get("name"), "bm": b["name"], "st": x.get("account_status")})

# cache das contas: se o Meta limitar a lista de BMs (erro #4), usa as contas já conhecidas em vez de ler só uma parte
CACHE = os.path.join(HERE, "accounts-cache.json")
try:
    _old = json.load(open(CACHE, encoding="utf-8"))
except Exception:
    _old = {}
for _k, _v in _old.items():
    accs.setdefault(_k, _v)
try:
    with open(CACHE, "w", encoding="utf-8") as _f:
        json.dump(accs, _f, ensure_ascii=False)
except Exception:
    pass

try:
    REGRAS = json.load(open(os.path.join(HERE, "reatribuir.json"), encoding="utf-8-sig"))
except Exception:
    REGRAS = []
DIA_ALVO = (now.date() - datetime.timedelta(days=1 if MODO_ONTEM else 0)).isoformat()

hoje, lives, agenda, erros = [], {}, {}, []
for acc, info in accs.items():
    aid = acc.replace("act_", "")
    label = OLD.get(aid) or f"{info['nome']} ({info['bm']})"
    ins = lst(f"{acc}/insights", {"fields": "campaign_name,spend", "level": "campaign", "date_preset": PRESET, "limit": 500})
    if ins is None:
        erros.append(label)
        continue
    camps = lst(f"{acc}/campaigns", {"fields": "name,start_time,stop_time,effective_status", "effective_status": '["ACTIVE","PAUSED"]', "limit": 500}) or [] if info["st"] == 1 else []
    # campanha criada em dia anterior e que atravessa a meia-noite: o gasto de hoje dela até o fim é só a madrugada
    antigas = set()
    for c in camps:
        try:
            if datetime.datetime.strptime(c["start_time"], "%Y-%m-%dT%H:%M:%S%z").astimezone(BRT).date() < now.date():
                antigas.add(c.get("name", ""))
        except Exception:
            pass
    por_exp = {}
    for r in ins:
        v = float(r.get("spend", 0))
        m = LIVE.search(r.get("campaign_name", ""))
        if not m or v <= 0:
            continue
        e = ALIAS.get(m.group(1).upper(), m.group(1).upper())
        # campanhas subidas com o nome do expert errado: reatribuir.json  [{dia, conta (final do id), expert, ini, para}]  (para vazio = não conta pra ninguém)
        reg = next((x for x in REGRAS if x["dia"] == DIA_ALVO and aid.endswith(x["conta"]) and x["expert"].upper() == e
                    and x["ini"].zfill(5) == m.group(2).zfill(5)), None)
        if reg:
            if not reg.get("para"):
                continue
            e = reg["para"].upper()
        por_exp[e] = por_exp.get(e, 0) + v
        s_, f_ = m.group(2).zfill(5), m.group(3).zfill(5)
        # live que atravessa a meia-noite (ex.: 23:00-04:00): o gasto de HOJE até o fim dela é a parte depois da meia-noite, então começa em 00:00
        # (o painel compara horários do mesmo dia e, com início 23:00, mostrava R$ 0,00 na madrugada)
        if f_ <= s_ and (now.strftime("%H:%M") < s_ or (r.get("campaign_name", "") in antigas and not MODO_ONTEM)):
            s_ = "00:00"  # (campanha da noite anterior já pausada: o gasto de hoje é só a madrugada, não conta na live de agora)
        k = (e, s_, f_)
        lives[k] = lives.get(k, 0) + v
    for e, v in por_exp.items():
        hoje.append({"a": label, "e": e, "v": round(v, 2)})
    if info["st"] == 1:  # só contas ativas podem ter live no ar / agendada
        for c in camps:
            if c.get("effective_status") != "ACTIVE":
                continue
            m = LIVE.search(c.get("name", ""))
            if not m:
                continue
            try:  # horário vem do NOME da campanha ([LIVE][EXPERT][HH:MM-HH:MM]); o start/stop do Meta é só a hora em que ela foi criada
                dia0 = now.date()
                if c.get("start_time"):
                    d0 = datetime.datetime.strptime(c["start_time"], "%Y-%m-%dT%H:%M:%S%z").astimezone(BRT).date()
                    if d0 > dia0:
                        dia0 = d0
                    elif d0 < dia0 - datetime.timedelta(days=1):
                        continue  # campanha antiga que ficou ativa: não é a live de hoje
                hi, mi = [int(x) for x in m.group(2).split(":")]
                hf, mf = [int(x) for x in m.group(3).split(":")]
                ini = datetime.datetime(dia0.year, dia0.month, dia0.day, hi, mi, tzinfo=BRT)
                fim = datetime.datetime(dia0.year, dia0.month, dia0.day, hf, mf, tzinfo=BRT)
                if fim <= ini:
                    fim += datetime.timedelta(days=1)
                    if ini - datetime.timedelta(days=1) <= now < fim - datetime.timedelta(days=1):  # live da noite anterior ainda no ar (virou a meia-noite)
                        ini, fim = ini - datetime.timedelta(days=1), fim - datetime.timedelta(days=1)
                if c.get("start_time") and d0 < now.date() and ini.date() >= now.date():
                    continue  # criada ontem: só vale se for a live de ontem à noite ainda no ar, não a de hoje
            except Exception:
                continue
            if fim <= now:
                continue
            agenda[(ALIAS.get(m.group(1).upper(), m.group(1).upper()), ini.strftime("%Y-%m-%dT%H:%M:%S-03:00"), fim.strftime("%Y-%m-%dT%H:%M:%S-03:00"))] = 1

if MODO_ONTEM:
    with open(os.path.join(HERE, "ontem.json"), "w", encoding="utf-8") as f:
        json.dump(hoje, f, ensure_ascii=False)
    print(json.dumps({"ontem_total": round(sum(r["v"] for r in hoje), 2), "linhas": len(hoje), "erros": erros}, ensure_ascii=False))
    sys.exit(0)
lv = [{"e": e, "s": s, "f": f, "v": round(v, 2)} for (e, s, f), v in sorted(lives.items(), key=lambda kv: (kv[0][0], kv[0][1]))]
ag = [{"e": e, "ini": i, "fim": f} for (e, i, f) in sorted(agenda, key=lambda x: x[1])]
for name, data in (("hoje.json", hoje), ("lives.json", lv), ("agenda.json", ag)):
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
tot = round(sum(r["v"] for r in hoje), 2)
por_conta = {}
for r in hoje:
    por_conta[r["a"]] = round(por_conta.get(r["a"], 0) + r["v"], 2)
print(json.dumps({"contas_lidas": len(accs), "erros": erros, "total_hoje": tot, "por_conta": por_conta, "agenda": len(ag)}, ensure_ascii=False))
