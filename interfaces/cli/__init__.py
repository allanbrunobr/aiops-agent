"""Interface de linha de comando do AIOps Agent.

``main.py`` (raiz do repositório) é o entrypoint fino; o conteúdo vive aqui:

- ``console``     — instância única do Rich ``Console`` (ordenação/largura
                    consistentes; capturável por ``capsys``).
- ``exit_codes``  — contrato de saída da CLI (0/1/2/3).
- ``args``        — parser de argumentos e validação de exclusão mútua.

Regra: nenhum módulo deste pacote importa ``main`` (evita ciclo).
"""
