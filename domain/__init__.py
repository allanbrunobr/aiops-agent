"""Camada de domínio — entidades puras do AIOps Agent.

Este pacote contém as entidades de negócio do sistema. Tudo aqui deve ser
puro Python (stdlib): sem Pydantic, sem framework de validação, sem I/O,
sem dependências de pipeline/, agents/, server/, rag/, safety/.

A regra de dependência é unidirecional: outras camadas podem importar de
domain/, mas domain/ não importa de mais nada do projeto.
"""
