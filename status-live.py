"""Marca PAUSADO / HORÁRIO ESTENDIDO na agenda, olhando se o gasto do expert continua subindo.
Roda depois do fetch-spend.py e antes do update-enc.js; lê hoje.json + agenda.json, mexe em agenda.json e guarda estado-live.json.
 - PAUSADO: está dentro de uma live agendada, já passaram 20 min do início e o gasto não sobe há mais de 10 min.
   A janela é cortada para "agora" (painéis antigos já mostram como fora do ar) e ganha st='pausado' (+ fimOrig).
 - HORÁRIO ESTENDIDO: passou 15 min do fim da última live e o gasto continua subindo: cria janela st='estendido' que se renova a cada ciclo."""
import os, json, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BRT = datetime.timezone(datetime.timedelta(hours=-3))
SEM_GASTO = 5      # min sem o gasto subir = live caiu (volta sozinha quando o gasto subir de novo)
CARENCIA_INI = 8   # min depois do início da live em que o Meta ainda pode estar atrasado
CARENCIA_FIM = 15  # min depois do fim agendado em que o atraso do Meta ainda gera gasto
now = datetime.datetime.now(BRT).replace(second=0, microsecond=0)
dia = now.date().isoformat()


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S-03:00")


def ler(nome, padrao):
    try:
        return json.load(open(os.path.join(HERE, nome), encoding="utf-8-sig"))
    except Exception:
        return padrao


hoje, agenda = ler("hoje.json", []), ler("agenda.json", [])
est = ler("estado-live.json", {})
if est.get("dia") != dia:
    est = {"dia": dia, "ex": {}}
ex = est["ex"]

total = {}
for r in hoje:
    total[r["e"]] = total.get(r["e"], 0) + r["v"]

nomes = set(total) | {a["e"] for a in agenda}
saida = []
log = {}
for e in sorted(nomes):
    s = ex.setdefault(e, {"total": total.get(e, 0.0), "ult": iso(now), "ext": None})
    if total.get(e, 0.0) > s["total"] + 0.01:
        s["ult"] = iso(now)
    s["total"] = round(total.get(e, 0.0), 2)
    parado = (now - datetime.datetime.fromisoformat(s["ult"])).total_seconds() / 60
    wins = [dict(a) for a in agenda if a["e"] == e]
    for w in wins:
        w["_i"], w["_f"] = datetime.datetime.fromisoformat(w["ini"]), datetime.datetime.fromisoformat(w["fim"])
    cur = [w for w in wins if w["_i"] <= now < w["_f"]]
    estado = None
    if cur:
        s["ext"] = None
        ini = min(w["_i"] for w in cur)
        # sem nenhum gasto lido hoje o mais provável é atraso do Meta (campanha nova): espera 60 min antes de marcar pausa
        carencia = CARENCIA_INI if s["total"] > 0 else 60
        # o relógio do "parado" só começa a contar quando a live agendada começa (gasto anterior não vale para a live nova)
        parado = min(parado, (now - ini).total_seconds() / 60)
        manual = ler("forcar-live.json", {}).get(e) == dia  # live marcada como no ar na mão: não marca encerrada
        if not manual and parado >= SEM_GASTO and (now - ini).total_seconds() / 60 > carencia:
            estado = "pausado"
            for w in cur:
                w["fimOrig"], w["fim"], w["st"] = w["fim"], iso(now), "pausado"
    else:
        passadas = [w for w in wins if w["_f"] <= now and now - w["_f"] < datetime.timedelta(hours=6)]
        ult_fim = max((w["_f"] for w in passadas), default=None)
        if ult_fim and parado < SEM_GASTO and s["total"] > 0 and (now - ult_fim).total_seconds() / 60 > CARENCIA_FIM:
            s["ext"] = s["ext"] or iso(ult_fim)
            wins.append({"e": e, "ini": s["ext"], "fim": iso(now + datetime.timedelta(minutes=10)), "st": "estendido"})
            estado = "estendido"
        else:
            s["ext"] = None
    if estado:
        log[e] = estado
    saida += [{k: v for k, v in w.items() if not k.startswith("_")} for w in wins]

# janelas das lives que ainda não começaram não passam por aqui (ficam como estão: cur vazio e sem gasto)
saida = sorted({(a["e"], a["ini"]): a for a in saida}.values(), key=lambda a: a["ini"])
with open(os.path.join(HERE, "agenda.json"), "w", encoding="utf-8") as f:
    json.dump(saida, f, ensure_ascii=False)
with open(os.path.join(HERE, "estado-live.json"), "w", encoding="utf-8") as f:
    json.dump(est, f, ensure_ascii=False)
print(json.dumps({"status": log}, ensure_ascii=False))
