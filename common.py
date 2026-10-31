"""Funções compartilhadas: leitura do .env, chamadas à Graph API da Meta e saída enxuta em JSON."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(SKILL_DIR, ".env")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()
API_VERSION = os.environ.get("META_API_VERSION") or "v24.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"


class MetaError(Exception):
    def __init__(self, info):
        super().__init__(info.get("erro"))
        self.info = info


def out(data):
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def die(msg, **extra):
    out({"erro": msg, **{k: v for k, v in extra.items() if v is not None}})
    sys.exit(1)


def run(main):
    """Executa o main e transforma erros da API em uma linha JSON curta."""
    try:
        main()
    except MetaError as e:
        out(e.info)
        sys.exit(1)


def act(account):
    account = str(account).strip()
    return account if account.startswith("act_") else f"act_{account}"


def token(account=None):
    """Token da conta (META_TOKEN_<id>) se existir; senão META_ACCESS_TOKEN."""
    if account:
        specific = os.environ.get(f"META_TOKEN_{act(account)[4:]}")
        if specific:
            return specific
    t = os.environ.get("META_ACCESS_TOKEN")
    if not t:
        die(f"Token não encontrado. Coloque META_ACCESS_TOKEN=... em {ENV_PATH}")
    return t


def parse_json(text, label):
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        die(f"JSON inválido em {label}: {e}")


def drop_none(value):
    if isinstance(value, dict):
        return {k: drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_none(v) for v in value]
    return value


def _encode(params):
    return {k: json.dumps(drop_none(v)) if isinstance(v, (dict, list)) else str(v)
            for k, v in params.items() if v is not None}


def _send(req):
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(body).get("error", {})
        except json.JSONDecodeError:
            raise MetaError({"erro": f"HTTP {e.code}", "resposta": body[:300]})
        raise MetaError(drop_none({
            "erro": err.get("error_user_msg") or err.get("message", "erro desconhecido"),
            "titulo": err.get("error_user_title"),
            "code": err.get("code"),
            "subcode": err.get("error_subcode"),
            "fbtrace_id": err.get("fbtrace_id"),
        }))
    except urllib.error.URLError as e:
        raise MetaError({"erro": f"Sem conexão com a Meta: {e.reason}"})


def api(method, path, params=None, access_token=None):
    params = _encode(params or {})
    params["access_token"] = access_token or token()
    url = f"{BASE_URL}/{path.lstrip('/')}"
    data = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    return _send(urllib.request.Request(url, data=data, method=method))


def get_all(path, params=None, access_token=None):
    """GET seguindo a paginação até o fim."""
    resp = api("GET", path, params, access_token)
    items = resp.get("data", [])
    next_url = resp.get("paging", {}).get("next")
    while next_url:
        resp = _send(urllib.request.Request(next_url))
        items += resp.get("data", [])
        next_url = resp.get("paging", {}).get("next")
    return items


def api_upload(path, fields, file_field, filename, content, access_token=None):
    """POST multipart (upload de vídeo a partir de arquivo local)."""
    boundary = uuid.uuid4().hex
    fields = _encode(fields)
    fields["access_token"] = access_token or token()
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                 f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    parts += [content, f"\r\n--{boundary}--\r\n".encode()]
    req = urllib.request.Request(f"{BASE_URL}/{path.lstrip('/')}", data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return _send(req)


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.read(), resp.headers.get_content_type()
    except urllib.error.URLError as e:
        die(f"Não consegui baixar o arquivo: {e}")


def money(cents):
    return None if cents in (None, "") else round(int(cents) / 100, 2)
