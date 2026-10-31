"""Soma o que foi COBRADO no cartão (ad_account_billing_charge do histórico da conta) por dia (BRT) em TODAS as contas que o token enxerga
e grava card.json (mantém o limite do dia já definido).
Uso: python -I fetch-charges.py   (usa o token e o common.py da skill meta-ads-ratos)"""
import sys, os, json, datetime, socket
socket.setdefaulttimeout(45)  # nunca ficar pendurado numa chamada de rede
SK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SK)
import common  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CARD = os.path.join(HERE, "card.json")
BRT = datetime.timezone(datetime.timedelta(hours=-3))
today = datetime.datetime.now(BRT).date()
since = (today - datetime.timedelta(days=2)).isoformat()


def lst(path, params):
    try:
        r = common.get_all(path, params)
        return r if isinstance(r, list) else r.get("data", [])
    except BaseException:  # noqa: BLE001
        return []


# descobre todas as contas de anúncio acessíveis (pessoais + de cada BM, próprias e de clientes)
accs = {}
for x in lst("me/adaccounts", {"fields": "id,name", "limit": 200}):
    accs[x["id"]] = x.get("name", x["id"])
for b in lst("me/businesses", {"fields": "id,name", "limit": 100}):
    for edge in ("owned_ad_accounts", "client_ad_accounts"):
        for x in lst(f"{b['id']}/{edge}", {"fields": "id,name", "limit": 200}):
            accs.setdefault(x["id"], x.get("name") or x["id"])

# contas conhecidas (cache gravado pelo fetch-spend.py) para não perder contas quando o Meta limita a lista de BMs
try:
    for _k, _v in json.load(open(os.path.join(HERE, "accounts-cache.json"), encoding="utf-8")).items():
        accs.setdefault(_k, _v.get("nome") or _k)
except Exception:
    pass

seen, rows, erros, por_conta = set(), {}, [], {}
for acc, nome in accs.items():
    data = None
    try:
        r = common.get_all(f"{acc}/activities", {"fields": "event_type,event_time,extra_data", "since": since, "limit": 200})
        data = r if isinstance(r, list) else r.get("data", [])
    except BaseException:  # noqa: BLE001
        erros.append(nome)
        continue
    for x in data:
        if x.get("event_type") != "ad_account_billing_charge":
            continue
        try:
            ed = json.loads(x.get("extra_data") or "{}")
        except Exception:
            continue
        tid = ed.get("transaction_id") or (acc + x.get("event_time", ""))
        if tid in seen or ed.get("type") != "payment_amount":
            continue
        seen.add(tid)
        t = datetime.datetime.strptime(x["event_time"], "%Y-%m-%dT%H:%M:%S%z").astimezone(BRT)
        d = t.date().isoformat()
        key = (d, acc.replace("act_", ""))
        rows[key] = rows.get(key, 0) + float(ed.get("new_value", 0)) / 100.0
        por_conta[acc.replace("act_", "")] = nome

try:
    old = json.load(open(CARD, encoding="utf-8-sig"))
except Exception:
    old = {"limit": None, "rows": []}
out = {"limit": old.get("limit"), "rows": [{"d": d, "a": por_conta.get(a, a), "id": a, "v": round(v, 2)} for (d, a), v in sorted(rows.items())]}
with open(CARD, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
hoje = sum(v for (d, a), v in rows.items() if d == today.isoformat())
print(json.dumps({"hoje": today.isoformat(), "contas_lidas": len(accs), "cobrado_hoje": round(hoje, 2), "contas_com_erro": len(erros),
                  "por_conta_hoje": {por_conta.get(a, a) + " " + a[-4:]: round(v, 2) for (d, a), v in sorted(rows.items(), key=lambda kv: -kv[1]) if d == today.isoformat()}}, ensure_ascii=False))
