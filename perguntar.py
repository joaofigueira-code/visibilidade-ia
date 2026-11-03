#!/usr/bin/env python3
# Visibilidade da Redicom em motores de IA: faz as 12 perguntas de perguntas.json e grava as respostas em
# respostas/AAAA-MM-DD/<motor>.json ({p01: {texto, modelo, fontes}}):
#   gemini_web  Gemini com pesquisa Google, todos os dias (conta para a meta)
#   google      Gemini sem pesquisa e openai = gpt-oss (modelo aberto da OpenAI, servido de graca pela Groq), uma vez
#               por mes: medem o que os modelos sabem de memoria (fica a parte no dashboard)
# Corre no GitHub Actions (disparado pelo cron-job.org); a rotina na nuvem do Claude le estes ficheiros as 09:10 e
# junta-os ao dashboard de notoriedade.
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
            erro = RuntimeError(f'HTTP {e.code}: ' + e.read().decode('utf-8', 'replace')[:450])
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


# Gemini com pesquisa Google (o que se ve no gemini.google.com): conta todos os dias para a meta. Nao ha recurso sem
# pesquisa: se nenhum modelo pesquisar, a pergunta fica sem resposta e a corrida seguinte do dia volta a tentar.
# Tenta os modelos por ordem; o que falhar com quota (429), pedido invalido (400) ou nao existir (404) sai desta corrida.
MODELOS_PESQUISA = ['gemini-3.8-flash', 'gemini-3.5-flash', 'gemini-3.5-flash-lite', 'gemini-flash-latest', 'gemini-flash-lite-latest']
fora = set()


def gemini_web(q):
    h = {'x-goog-api-key': os.environ['GEMINI_API_KEY']}
    corpo = {'contents': [{'role': 'user', 'parts': [{'text': q}]}], 'tools': [{'google_search': {}}]}
    erros = []
    for m in MODELOS_PESQUISA:
        if m in fora:
            continue
        try:
            r = post(f'https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent', h, corpo, espera=90, tent=1)
            c = r['candidates'][0]
            fontes = [(ch.get('web', {}).get('title', '') + ' ' + ch.get('web', {}).get('uri', '')).strip() for ch in c.get('groundingMetadata', {}).get('groundingChunks', [])]
            return {'texto': ''.join(p.get('text', '') for p in c['content']['parts']), 'modelo': f'{m} (com pesquisa Google)', 'fontes': fontes}
        except Exception as e:
            erros.append(f'{m}: {str(e)[:300]}')
            if any(f'HTTP {c}' in str(e) for c in (400, 404, 429)):
                fora.add(m)
                print(f'  gemini_web: {m} fora desta corrida ({str(e)[:300]})', flush=True)
    raise RuntimeError(' | '.join(erros) or 'nenhum modelo com pesquisa disponivel')


def google(q):
    # memoria do Gemini, sem pesquisa: so uma vez por mes (ver main)
    h = {'x-goog-api-key': os.environ['GEMINI_API_KEY']}
    corpo = {'contents': [{'role': 'user', 'parts': [{'text': q}]}]}
    erro = None
    for m in ('gemini-3.5-flash', 'gemini-flash-lite-latest'):
        try:
            r = post(f'https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent', h, corpo)
            return {'texto': ''.join(p.get('text', '') for p in r['candidates'][0]['content']['parts']), 'modelo': f'{m} (sem pesquisa)', 'fontes': []}
        except Exception as e:
            erro = e
    raise RuntimeError(erro)


def openai(q):
    # sem pesquisa na web e com pouco raciocinio: na Groq gratis o limite por minuto conta o raciocinio e o que a pesquisa le,
    # e com pesquisa so passava 1 pergunta em cada 4 minutos
    h = {'Authorization': 'Bearer ' + os.environ['GROQ_API_KEY']}
    corpo = {'model': 'openai/gpt-oss-120b', 'messages': [{'role': 'user', 'content': q}], 'reasoning_effort': 'low', 'max_completion_tokens': 2000}
    r = post('https://api.groq.com/openai/v1/chat/completions', h, corpo, tent=4)
    return {'texto': r['choices'][0]['message'].get('content') or '', 'modelo': 'gpt-oss-120b (sem pesquisa)', 'fontes': []}


_print = print
LOG = []


def print(*a, **k):  # o que se escreve no ecra fica tambem em respostas/AAAA-MM-DD/estado.txt (sem chaves)
    LOG.append(' '.join(str(x) for x in a))
    _print(*a, **k)
    with open(os.path.join(SAIDA, 'estado.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(LOG) + '\n')


def memoria_feita(motor):
    # os modelos sem pesquisa so mudam quando sai uma versao nova: mede-se uma vez por mes (no 1.o dia em que o workflow corre)
    mes = DATA[:7]
    base = os.path.join(AQUI, 'respostas')
    for d in os.listdir(base) if os.path.isdir(base) else []:
        if d.startswith(mes) and d != DATA and os.path.exists(os.path.join(base, d, f'{motor}.json')):
            return True
    return False


def main():
    os.makedirs(SAIDA, exist_ok=True)
    motores = [('gemini_web', gemini_web, 'GEMINI_API_KEY', 7)]  # todos os dias
    for motor, f, chave, pausa in (('google', google, 'GEMINI_API_KEY', 3), ('openai', openai, 'GROQ_API_KEY', 62)):  # Groq gratis: limite de texto por minuto
        if os.environ.get('MEMORIA') == '1' or not memoria_feita(motor):
            motores.append((motor, f, chave, pausa))
        else:
            print(f'{motor}: memoria deste mes ja medida', flush=True)
    for motor, f, chave, pausa in motores:
        if not os.environ.get(chave):
            print(f'{motor}: FALTA o secret {chave}', flush=True)
            continue
        out, falhas, ficheiro = {}, [], os.path.join(SAIDA, f'{motor}.json')
        # o workflow corre mais de uma vez por dia (o GitHub atrasa os agendamentos): so pergunta o que ainda falta hoje
        if os.path.exists(ficheiro):
            out = json.load(open(ficheiro, encoding='utf-8'))
        for q in PERGUNTAS:
            if out.get(q['id'], {}).get('texto', '').strip():
                continue
            t0 = time.time()
            try:
                out[q['id']] = f(q['texto'])
                json.dump(out, open(ficheiro, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                print(f"  {motor} {q['id']} ok em {time.time() - t0:.0f}s", flush=True)
            except Exception as e:
                falhas.append(f"{q['id']} {str(e)[:450]}")
                print(f"  {motor} {q['id']} FALHOU em {time.time() - t0:.0f}s: {str(e)[:450]}", flush=True)
            time.sleep(pausa)
        modelos = ', '.join(sorted({v['modelo'] for v in out.values()}))
        print(f'{motor}: {len(out)} de {len(PERGUNTAS)} respostas ({modelos})' + (f'; falhas: {" | ".join(falhas)}' if falhas else ''), flush=True)


if __name__ == '__main__':
    main()
