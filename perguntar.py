#!/usr/bin/env python3
# Visibilidade da Redicom em motores de IA: faz as 12 perguntas de perguntas.json ao Google (Gemini) e a um modelo da
# OpenAI (gpt-oss, servido de graca pela Groq) e grava as respostas em respostas/AAAA-MM-DD/<motor>.json
# ({p01: {texto, modelo, fontes}}). Corre todos os dias no GitHub Actions; a rotina na nuvem do Claude le estes ficheiros
# as 09:00 e junta-os ao dashboard de notoriedade.
# Chaves (Secrets do repositorio): GEMINI_API_KEY e GROQ_API_KEY. Nunca sao escritas.
# Grava depois de cada resposta, para nao perder o que ja tem se o GitHub parar a meio.
import json, os, socket, time, urllib.error, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

AQUI = os.path.dirname(os.path.abspath(__file__))
PERGUNTAS = json.load(open(os.path.join(AQUI, 'perguntas.json'), encoding='utf-8'))
DATA = datetime.now(ZoneInfo('Europe/Lisbon')).strftime('%Y-%m-%d')
SAIDA = os.path.join(AQUI, 'respostas', DATA)


def post(url, headers, body, espera=90, tent=2):
    erro = None
    for i in range(tent):
        # a Groq (Cloudflare) recusa o User-Agent do urllib com o erro 1010
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={**headers, 'Content-Type': 'application/json', 'User-Agent': 'visibilidade-ia/1.0 (+https://github.com/joaofigueira-code/visibilidade-ia)'})
        try:
            with urllib.request.urlopen(req, timeout=espera) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            erro = RuntimeError(f'HTTP {e.code}: ' + e.read().decode('utf-8', 'replace')[:200])
            if e.code == 429 and 'quota' in str(erro).lower():
                raise erro  # quota do dia esgotada: nao vale a pena repetir
            if e.code in (429, 500, 502, 503, 504) and i < tent - 1:
                time.sleep(65 if "rate limit" in str(erro).lower() else 10)  # limite por minuto da Groq: espera e repete
                continue
            raise erro
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            erro = RuntimeError(f'rede: {e}')
            if i < tent - 1:
                time.sleep(5)
                continue
            raise erro
    raise erro


# so se tenta a pesquisa na web na 1.a pergunta; se falhar (quota gratuita, demora), o resto vai sem pesquisa
pesquisa = {'google': True, 'openai': True}


def google(q):
    h = {'x-goog-api-key': os.environ['GEMINI_API_KEY']}
    corpo = {'contents': [{'role': 'user', 'parts': [{'text': q}]}]}
    if pesquisa['google']:
        try:
            r = post('https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent', h, {**corpo, 'tools': [{'google_search': {}}]}, espera=60, tent=1)
            c = r['candidates'][0]
            fontes = [(ch.get('web', {}).get('title', '') + ' ' + ch.get('web', {}).get('uri', '')).strip() for ch in c.get('groundingMetadata', {}).get('groundingChunks', [])]
            return {'texto': ''.join(p.get('text', '') for p in c['content']['parts']), 'modelo': 'gemini-3.5-flash (com pesquisa Google)', 'fontes': fontes}
        except Exception as e:
            print(f'  google: sem pesquisa a partir daqui ({str(e)[:100]})', flush=True)
            pesquisa['google'] = False
    erro = None
    for m in ('gemini-3.5-flash', 'gemini-flash-lite-latest'):
        try:
            r = post(f'https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent', h, corpo)
            return {'texto': ''.join(p.get('text', '') for p in r['candidates'][0]['content']['parts']), 'modelo': f'{m} (sem pesquisa)', 'fontes': []}
        except Exception as e:
            erro = e
    raise RuntimeError(erro)


def openai(q):
    h = {'Authorization': 'Bearer ' + os.environ['GROQ_API_KEY']}
    corpo = {'model': 'openai/gpt-oss-120b', 'messages': [{'role': 'user', 'content': q}]}
    if pesquisa['openai']:
        try:
            r = post('https://api.groq.com/openai/v1/chat/completions', h, {**corpo, 'tools': [{'type': 'browser_search'}]}, espera=60, tent=1)
            msg = r['choices'][0]['message']
            fontes = [res['url'] for t in (msg.get('executed_tools') or []) for res in (((t.get('search_results') or {}).get('results')) or []) if res.get('url')]
            return {'texto': msg.get('content') or '', 'modelo': 'gpt-oss-120b (com pesquisa web)', 'fontes': fontes}
        except Exception as e:
            print(f'  openai: sem pesquisa a partir daqui ({str(e)[:100]})', flush=True)
            pesquisa['openai'] = False
    r = post('https://api.groq.com/openai/v1/chat/completions', h, corpo)
    return {'texto': r['choices'][0]['message'].get('content') or '', 'modelo': 'gpt-oss-120b (sem pesquisa)', 'fontes': []}


_print = print
LOG = []


def print(*a, **k):  # o que se escreve no ecra fica tambem em respostas/AAAA-MM-DD/estado.txt (sem chaves)
    LOG.append(' '.join(str(x) for x in a))
    _print(*a, **k)
    with open(os.path.join(SAIDA, 'estado.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(LOG) + '\n')


def main():
    os.makedirs(SAIDA, exist_ok=True)
    for motor, f, chave, pausa in (('google', google, 'GEMINI_API_KEY', 3), ('openai', openai, 'GROQ_API_KEY', 62)):  # Groq gratis: limite de texto por minuto
        if not os.environ.get(chave):
            print(f'{motor}: FALTA o secret {chave}', flush=True)
            continue
        out, falhas, ficheiro = {}, [], os.path.join(SAIDA, f'{motor}.json')
        for q in PERGUNTAS:
            t0 = time.time()
            try:
                out[q['id']] = f(q['texto'])
                json.dump(out, open(ficheiro, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                print(f"  {motor} {q['id']} ok em {time.time() - t0:.0f}s", flush=True)
            except Exception as e:
                falhas.append(f"{q['id']} {str(e)[:150]}")
                print(f"  {motor} {q['id']} FALHOU em {time.time() - t0:.0f}s: {str(e)[:150]}", flush=True)
            time.sleep(pausa)
        modelos = ', '.join(sorted({v['modelo'] for v in out.values()}))
        print(f'{motor}: {len(out)} de {len(PERGUNTAS)} respostas ({modelos})' + (f'; falhas: {" | ".join(falhas)}' if falhas else ''), flush=True)


if __name__ == '__main__':
    main()
