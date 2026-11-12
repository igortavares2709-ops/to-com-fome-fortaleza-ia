# Menu Category NB — model card

## Visão geral

`menu-category-nb-v1` é um baseline experimental de classificação de texto para
normalizar itens de cardápio em cinco classes amplas. O modelo implementa
Multinomial Naive Bayes do zero, sem bibliotecas de machine learning. Ele recebe
somente `name + description`; preço, restaurante, URL e categoria de origem não
entram nas features.

O artefato treinado está em `ml/artifacts/menu-category-nb.json`. As métricas
completas, a matriz de confusão, o hash canônico do corpus e a divisão usada estão em
`ml/results/menu-classifier-baseline.json`.

## Dados e weak supervision

O snapshot contém 280 itens vindos de dois feeds oficiais de restaurantes de
Fortaleza. A categoria publicada por cada restaurante gera um rótulo fraco em
uma taxonomia comum:

| Classe | Itens |
|---|---:|
| `bebida_alcoolica` | 97 |
| `bebida_sem_alcool` | 37 |
| `entrada_acompanhamento` | 55 |
| `prato_principal` | 79 |
| `sobremesa` | 12 |

O mapeamento completo está em `CATEGORY_TO_LABEL`, no script de treino. A seção
`⭐️NOVIDADES E PROMOÇÕES⭐️` foi mapeada para bebida alcoólica porque os quatro
itens do snapshot atual são drinks. Esse rótulo precisa ser revisto se o conteúdo
da seção mudar. Outras categorias também são imperfeitas: `FESTIVAL DA LAGOSTA`
mistura ceviches e pratos principais; `COCINA EM CASA` reúne tipos distintos; e
`VEGETARIANO` contém prato e salada.

Esses rótulos não foram revisados individualmente por especialistas. As métricas
medem concordância com a taxonomia derivada das fontes, não uma verdade culinária
universal.

## Treino e avaliação

- Normalização Unicode NFKD, remoção de diacríticos e caixa baixa.
- Features de contagem: palavras alfanuméricas e bigramas adjacentes.
- Suavização de Laplace `alpha=1`.
- Priors e log-probabilidades serializados com 12 casas decimais para
  reprodutibilidade entre versões compatíveis do Python.
- Split determinístico e estratificado de 75/25.
- Produtos com o mesmo nome normalizado permanecem no mesmo lado do split.
- Seed: `fortaleza-menu-nb-v1`.
- O artefato final é retreinado nos 280 itens após medir o holdout.

Resultados medidos no snapshot `data/menu-items.json`:

| Avaliação | Exemplos | Accuracy | Macro-F1 |
|---|---:|---:|---:|
| Holdout estratificado por nome | 70 | 0,8143 | 0,7731 |
| Leave-one-restaurant-out | 280 | 0,6893 | 0,5993 |

No holdout, `entrada_acompanhamento` obteve F1 de 0,5263 e foi frequentemente
confundida com `prato_principal`. No teste entre restaurantes, o F1 dessa classe
caiu para 0,3117. `Sobremesa` possui apenas 12 exemplos. Esses resultados mostram
que o modelo ainda não generaliza o bastante para classificação automática em
produção.

## Uso pretendido

- Baseline reproduzível para comparar futuras abordagens de NLP.
- Experimento de normalização ampla de categorias de cardápios.
- Demonstração de pipeline de dados, treino, avaliação e serialização de modelo.

O modelo não está conectado à decisão principal da aplicação. A busca publicada
continua usando dados confirmados e regras determinísticas.

## Usos proibidos e riscos

- Não inferir alergias, presença de ingredientes ou segurança alimentar.
- Não decidir dietas, restrições médicas ou disponibilidade do prato.
- Não substituir revisão humana nem categorias fornecidas pelo restaurante.
- Não tratar a confiança do Naive Bayes como probabilidade calibrada.

O conjunto pequeno e restrito a dois restaurantes cria viés de cardápio, região,
idioma e estilo editorial. Descrições parecidas dentro da mesma fonte podem
facilitar o holdout; por isso o relatório inclui também leave-one-restaurant-out.

## Reprodução

```bash
python scripts/train_menu_classifier.py train
python scripts/train_menu_classifier.py evaluate
python scripts/train_menu_classifier.py predict \
  --text "suco natural de caju sem álcool"
python -m unittest tests.test_menu_classifier -v
```

O treino é determinístico. Executá-lo novamente sobre o mesmo arquivo de entrada
produz o mesmo modelo e o mesmo relatório.

O fingerprint do corpus considera apenas restaurante, nome, descrição, categoria
de origem e rótulo fraco. Mudanças operacionais em preço, disponibilidade,
`fetched_at` ou outras datas do feed não invalidam o experimento porque esses
campos não entram nas features nem nos rótulos.

## Próximos experimentos

1. Obter rótulos humanos por item e separar treino, validação e teste por restaurante.
2. Adicionar mais fontes, principalmente sobremesas e entradas.
3. Comparar TF-IDF + regressão logística e um encoder em português.
4. Calibrar confiança e criar política explícita de abstenção.
5. Publicar somente um modelo que supere o baseline no teste entre restaurantes.
