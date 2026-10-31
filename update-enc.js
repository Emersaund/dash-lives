// Atualiza a dash cifrada: decifra o bloco, troca gasto de hoje/agenda/hora, cifra de novo.
// Uso: node update-enc.js <fonte.html> <saida.html>   (senha em env DASH_PW)
const fs = require('fs'), crypto = require('crypto'), path = require('path');
const [src, out] = process.argv.slice(2), pw = process.env.DASH_PW;
if (!src || !out || !pw) { console.error('uso: DASH_PW=... node update-enc.js fonte.html saida.html'); process.exit(1); }
const dir = __dirname;
const JSONMODE = src.endsWith('.json');
let html = fs.readFileSync(src, 'utf8').replace(/^\uFEFF/, '');
const m = JSONMODE ? null : html.match(/var ENC = (\{[^\n]*?\});\n/);
if (!JSONMODE && !m) throw new Error('bloco ENC nao encontrado');
const ENC = JSON.parse(JSONMODE ? html : m[1]);
const key = crypto.pbkdf2Sync(pw, Buffer.from(ENC.salt, 'base64'), ENC.iter, 32, 'sha256');
const raw = Buffer.from(ENC.data, 'base64'), tag = raw.subarray(raw.length - 16), ct = raw.subarray(0, raw.length - 16);
const dec = crypto.createDecipheriv('aes-256-gcm', key, Buffer.from(ENC.iv, 'base64')); dec.setAuthTag(tag);
const P = JSON.parse(Buffer.concat([dec.update(ct), dec.final()]).toString('utf8'));
const rd = f => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8').replace(/^﻿/, ''));
if (process.env.EXTRACT) { // só lê o bloco: grava o que precisa persistir entre as rodadas (limite do cartão, estado do "ao vivo pelo gasto") e imprime o dia
  fs.writeFileSync(path.join(dir, 'card.json'), JSON.stringify({ limit: (P.CARD || {}).limit || null, rows: [] }));
  if (P.ESTADO) fs.writeFileSync(path.join(dir, 'estado-live.json'), JSON.stringify(P.ESTADO));
  console.log(P.META.hoje); process.exit(0);
}
const hoje = rd('hoje.json'), agenda = rd('agenda.json');
const now = new Date(Date.now() - 3 * 3600e3), pad = n => ('0' + n).slice(-2);
const dia = now.toISOString().slice(0, 10), hora = pad(now.getUTCHours()) + ':' + pad(now.getUTCMinutes());
P.DATA = P.DATA.filter(r => r.d !== dia).concat(hoje.map(r => ({ d: dia, a: r.a, e: r.e, v: r.v })));
try { // fechamento do dia anterior (ontem.json é apagado depois de aplicado)
  const ont = rd('ontem.json'), yd = new Date(new Date(dia + 'T12:00:00Z').getTime() - 864e5).toISOString().slice(0, 10);
  P.DATA = P.DATA.filter(r => r.d !== yd).concat(ont.map(r => ({ d: yd, a: r.a, e: r.e, v: r.v })));
  fs.unlinkSync(path.join(dir, 'ontem.json')); console.log('fechamento aplicado em', yd, ont.length, 'linhas');
} catch (e) {}
P.DATA.sort((a, b) => a.d < b.d ? -1 : a.d > b.d ? 1 : a.e < b.e ? -1 : a.e > b.e ? 1 : a.a < b.a ? -1 : 1);
P.META.hoje = dia; P.META.hora = hora; P.META.atualizado = dia.slice(8, 10) + '/' + dia.slice(5, 7) + '/' + dia.slice(0, 4) + ' ' + hora;
try { const patch = rd('people-patch.json'); P.PEOPLE.forEach(p => { if (patch[p.name]) Object.assign(p, patch[p.name]); }); } catch (e) {}
try { const add = rd('people-add.json'); add.forEach(n => { if (!P.PEOPLE.some(p => p.name === n.name)) P.PEOPLE.push(n); }); } catch (e) {} // novos experts (people-add.json)
let ag = agenda; // ajustes manuais de horário (agenda-override.json: [{e, dia, fim:"HH:MM"}]) e uma entrada por expert/início
try { const ov = rd('agenda-override.json'); ag = ag.map(a => { const o = ov.find(x => x.e === a.e && a.ini.slice(0, 10) === x.dia); return o ? Object.assign({}, a, { fim: o.dia + 'T' + o.fim + ':00-03:00' }) : a; }); } catch (e) {}
const agm = {}; ag.forEach(a => { const k = a.e + a.ini; if (!agm[k] || agm[k].fim < a.fim) agm[k] = a; });
P.AGENDA = Object.values(agm).sort((a, b) => a.ini < b.ini ? -1 : 1);
try { P.LIVES = rd('lives.json'); } catch (e) { P.LIVES = P.LIVES || []; }
try { P.CARD = rd('card.json'); } catch (e) { P.CARD = P.CARD || { limit: null, rows: [] }; }
// limite zerado (cartao-config.json): a caixa "Cobrado no cartão hoje" começa em R$ 0 na hora definida e o limite passa a ser só os dólares informados (+ extra em reais)
try { const cf = rd('cartao-config.json'); if (cf.dia === dia) { P.CARD.rows = (P.CARD.rows || []).concat([{ d: dia, a: 'zerado às ' + cf.desde, id: 'ajuste', v: -cf.baseline }]); P.CARD.limit = Math.round((cf.limit_usd * cf.cambio + (cf.extra_brl || 0)) * 100) / 100; } } catch (e) {} // cobrado no cartão + limite do dia (informados pelo usuário)
try { P.ESTADO = rd('estado-live.json'); } catch (e) {} // estado do 'ao vivo pelo gasto' viaja dentro do bloco cifrado
const iv = crypto.randomBytes(12);
const enc = crypto.createCipheriv('aes-256-gcm', key, iv);
const ctn = Buffer.concat([enc.update(JSON.stringify(P), 'utf8'), enc.final(), enc.getAuthTag()]);
const NEW = { salt: ENC.salt, iv: iv.toString('base64'), iter: ENC.iter, data: ctn.toString('base64') };
if (JSONMODE) fs.writeFileSync(out, JSON.stringify(NEW), 'utf8');
else { html = html.replace(m[0], () => 'var ENC = ' + JSON.stringify(NEW) + ';\n'); fs.writeFileSync(out, html, 'utf8'); }
// envio opcional do bloco CIFRADO (sem a senha) para o painel do apostamuito; só roda se existir push.token ao lado deste script
try {
  const tokFile = [path.join(dir, 'push.token'), path.join(dir, 'push.token.txt')].find(f => fs.existsSync(f));
  if (tokFile || process.env.PUSH_TOKEN) {
    const tok = process.env.PUSH_TOKEN || fs.readFileSync(tokFile, 'utf8').trim();
    fetch('https://ads.apostamuito.com/lives/enc.php', { method: 'POST', headers: { 'X-Token': tok, 'Content-Type': 'application/json' }, body: JSON.stringify(NEW), signal: AbortSignal.timeout(20000) })
      .then(r => console.log('push apostamuito:', r.status)).catch(e => console.log('push apostamuito falhou:', String(e.message || e).slice(0, 100)));
  }
} catch (e) { console.log('push apostamuito falhou:', String(e.message || e).slice(0, 100)); }
console.log('ok', dia, hora, 'linhas hoje', hoje.length, 'total hoje', hoje.reduce((s, r) => s + r.v, 0).toFixed(2), 'FIN', P.FIN.length, 'hoje no FIN', P.FIN.filter(r => r.d === dia).length);
