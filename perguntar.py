#!/usr/bin/env python3
# Visibilidade da Redicom em motores de IA: faz as 12 perguntas de perguntas.json ao Google (Gemini) e a um modelo da
# OpenAI (gpt-oss, servido de graca pela Groq) e grava as respostas em respostas/AAAA-MM-DD/<motor>.json
# ({p01: {texto, modelo, fontes}}). Corre todos os dias no GitHub Actions; a rotina na nuvem do Claude le estes ficheiros
# as 10:00 UTC e junta-os ao dashboard de notoriedade.
# Chaves (Secrets do repositorio): GEMINI_API_KEY e GROQ_API_KEY. Nunca sao escritas.
import json, os, sys, time, urllib.error, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

AQUI = os.path.dirname(os.path.abspath(__file__))
PERGUNTAS = json.load(open(os.path.join(AQUI, 'perguntas.json'), encoding='utf-8'))
DATA = datetime.now(ZoneInfo('Europe/Lisbon')).strftime('%Y-%m-%d')
SAIDA = os.path.join(AQUI, 'respostas', DATA)


class Quota(Exception):
    pass


def post(url, headers, body, tent=3):
    for i in range(tent):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={**headers, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = e.read().decode('utf-8', 'replace')[:300]
            if e.code == 429 and 'quota' in msg.lower():
                raise Quota(msg)
            if e.code in (429, 500, 502, 503, 504) and i < tent - 1:
                time.sleep(20 * (i + 1))
                continue
            raise RuntimeError(f'HTTP {e.code}: {msg}')
        except (urllib.error.URLError, TimeoutError) as e:
            if i < tent - 1:
                time.sleep(20)
                continue
            raise RuntimeError(f'rede: {e}')


# Google: com pesquisa Google enquanto a quota gratuita deixar; depois so com o que o modelo sabe
estado = {'pesquisa': True}


def google(q):
    h = {'x-goog-api-key': os.environ['GEMINI_API_KEY']}
    corpo = {'contents': [{'role': 'user', 'parts': [{'text': q}]}]}
    if estado['pesquisa']:
        try:
            r = post('https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent', h, {**corpo, 'tools': [{'google_search': {}}]})
            c = r['candidates'][0]
            fontes = [(ch.get('web', {}).get('title', '') + ' ' + ch.get('web', {}).get('uri', '')).strip() for ch in c.get('groundingMetadata', {}).get('groundingChunks', [])]
            return {'texto': ''.join(p.get('text', '') for p in c['content']['parts']), 'modelo': 'gemini-flash-latest (com pesquisa Google)', 'fontes': fontes}
        except Quota:
            estado['pesquisa'] = False
        except (RuntimeError, KeyError, IndexError):
            pass
    erro = None
    for m in ('gemini-3.5-flash', 'gemini-flash-latest', 'gemini-flash-lite-latest'):
        try:
            r = post(f'https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent', h, corpo)
            return {'texto': ''.join(p.get('text', '') for p in r['candidates'][0]['content']['parts']), 'modelo': f'{m} (sem pesquisa)', 'fontes': []}
        except (RuntimeError, Quota, KeyError, IndexError) as e:
            erro = e
    raise RuntimeError(erro)


# OpenAI: modelo aberto gpt-oss pela Groq; tenta com a pesquisa na web da Groq e, se nao der, sem pesquisa
estado_oa = {'pesquisa': True}


def openai(q):
    h = {'Authorization': 'Bearer ' + os.environ['GROQ_API_KEY']}
    corpo = {'model': 'openai/gpt-oss-120b', 'messages': [{'role': 'user', 'content': q}]}
    if estado_oa['pesquisa']:
        try:
            r = post('https://api.groq.com/openai/v1/chat/completions', h, {**corpo, 'tools': [{'type': 'browser_search'}]})
            msg = r['choices'][0]['message']
            fontes = []
            for t in msg.get('executed_tools') or []:
                for res in ((t.get('search_results') or {}).get('results') or []):
                    if res.get('url'):
                        fontes.append(res['url'])
            return {'texto': msg.get('content') or '', 'modelo': 'gpt-oss-120b (com pesquisa web)', 'fontes': fontes}
        except (RuntimeError, Quota, KeyError, IndexError):
            estado_oa['pesquisa'] = False
    r = post('https://api.groq.com/openai/v1/chat/completions', h, corpo)
    return {'texto': r['choices'][0]['message'].get('content') or '', 'modelo': 'gpt-oss-120b (sem pesquisa)', 'fontes': []}


def main():
    os.makedirs(SAIDA, exist_ok=True)
    falhou_tudo = True
    for motor, f, chave, pausa in (('google', google, 'GEMINI_API_KEY', 5), ('openai', openai, 'GROQ_API_KEY', 3)):
        if not os.environ.get(chave):
            print(f'{motor}: FALTA o secret {chave}')
            continue
        out, falhas = {}, []
        for q in PERGUNTAS:
            try:
                out[q['id']] = f(q['texto'])
            except Exception as e:
                falhas.append(f"{q['id']} {str(e)[:150]}")
            time.sleep(pausa)
        if out:
            falhou_tudo = False
            json.dump(out, open(os.path.join(SAIDA, f'{motor}.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        modelos = ', '.join(sorted({v['modelo'] for v in out.values()}))
        print(f'{motor}: {len(out)} de {len(PERGUNTAS)} respostas ({modelos})' + (f'; falhas: {" | ".join(falhas)}' if falhas else ''))
    sys.exit(1 if falhou_tudo else 0)


if __name__ == '__main__':
    main()
