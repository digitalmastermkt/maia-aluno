#!/usr/bin/env python3
"""upload_persona_to_minio.py - Sobe entregaveis de persona pro MinIO.

Uso:
    python upload_persona_to_minio.py <pasta_local> <slug>

Sobe TODOS os arquivos da pasta_local pra <bucket>/personas/YYYY-MM-DD-<slug>/

Le credenciais do /opt/MAIA/bot/.env:
    MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET, MINIO_CONSOLE_URL

Falha de upload NAO quebra o pipeline - imprime warning e retorna 1.
Categoria 'personas/' eh ATIVO ESTRATEGICO - SEM lifecycle (nao apaga em 30d).
"""
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

ENV_FILE = '/opt/MAIA/bot/.env'


def load_env():
    env = {}
    if not os.path.exists(ENV_FILE):
        return env
    with open(ENV_FILE) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def slugify(text: str) -> str:
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    return text or 'persona-sem-nome'


def upload_persona(pasta_local: str, slug: str) -> str | None:
    env = load_env()
    endpoint = env.get('MINIO_ENDPOINT')
    access = env.get('MINIO_ACCESS_KEY')
    secret = env.get('MINIO_SECRET_KEY')
    bucket = env.get('MINIO_BUCKET', 'seu-bucket')
    console_url = env.get('MINIO_CONSOLE_URL', 'http://SEU_IP_OU_HOST:9001')

    if not (endpoint and access and secret):
        print('[upload_persona_to_minio] WARNING: credenciais MinIO ausentes em .env', file=sys.stderr)
        return None

    pasta = Path(pasta_local)
    if not pasta.exists() or not pasta.is_dir():
        print(f'[upload_persona_to_minio] ERRO: pasta local nao existe: {pasta}', file=sys.stderr)
        return None

    slug = slugify(slug)
    data = datetime.now().strftime('%Y-%m-%d')
    prefix = f'personas/{data}-{slug}/'

    try:
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=access,
            aws_secret_access_key=secret,
        )

        uploaded = 0
        for arquivo in pasta.rglob('*'):
            if arquivo.is_file():
                rel = arquivo.relative_to(pasta).as_posix()
                key = prefix + rel
                client.upload_file(str(arquivo), bucket, key)
                uploaded += 1

        if uploaded == 0:
            print(f'[upload_persona_to_minio] WARNING: pasta vazia, nada enviado', file=sys.stderr)
            return None

        # Console URL com URL encoding
        prefix_enc = quote(prefix, safe='')
        link = f'{console_url}/browser/{bucket}/{prefix_enc}'
        print(f'[upload_persona_to_minio] OK: {uploaded} arquivo(s) em {prefix}', file=sys.stderr)
        print(link)  # stdout limpo
        return link

    except Exception as e:
        print(f'[upload_persona_to_minio] FALHA: {e}', file=sys.stderr)
        return None


def main():
    if len(sys.argv) < 3:
        print('Uso: upload_persona_to_minio.py <pasta_local> <slug>', file=sys.stderr)
        sys.exit(2)
    url = upload_persona(sys.argv[1], sys.argv[2])
    sys.exit(0 if url else 1)


if __name__ == '__main__':
    main()
