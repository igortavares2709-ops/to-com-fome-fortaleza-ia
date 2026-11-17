# Case de produto — visão AI Builder

## Problema

Escolher onde comer exige combinar intenção, localização e restrições espalhadas:
bairro, culinária, raio, entrega, retirada, dieta, acessibilidade, horário e
preço. Buscas genéricas podem ignorar restrições ou apresentar informações sem
fonte.

## Job to be done

> Quando eu estiver procurando uma opção de alimentação em Fortaleza, quero
> descrever o que preciso em linguagem natural e receber alternativas
> explicáveis, para decidir com menos busca manual e sem depender de sugestões
> inventadas.

## Público e contexto

- Moradores de Fortaleza buscando opções próximas.
- Visitantes que ainda não conhecem bairros e restaurantes.
- Pessoas com restrições alimentares ou necessidades de acessibilidade.
- Usuários que valorizam entrega, retirada ou cardápio disponível.

A base atual é demonstrativa e não substitui a confirmação diretamente com o
estabelecimento.

## Proposta de valor

- Uma pergunta em português vira filtros estruturados.
- Cada resultado informa por que apareceu e de onde veio.
- Dado ausente permanece ausente; o produto não o transforma em fato.
- O fallback local mantém a experiência disponível sem chave de LLM.
- Mapa, filtros e explicações ficam reunidos em um único protótipo público.

## Jornada do protótipo

1. O usuário descreve necessidade e restrições.
2. A IA ou o interpretador local estrutura a intenção.
3. Validações e regras determinísticas preservam restrições.
4. A recuperação consulta dados geográficos e cardápios autorizados.
5. A interface apresenta alternativas, evidências, fontes e lacunas.

## Hipóteses e sinais de qualidade

| Hipótese | Sinal atual |
|---|---|
| Linguagem natural pode reduzir a configuração manual de filtros | 50/50 casos de intenção corretos |
| Restrições precisam ser preservadas | 101/101 slots estruturados corretos |
| Recomendações devem ser explicáveis | evidência e fonte em 100% dos resultados avaliados |
| O produto não deve depender de um provedor | fallback local e avaliação offline |
| Negação precisa ter precedência | zero violações de culinária negada |

Esses números avaliam o software e o conjunto de casos versionado. Ainda não
demonstram adoção, conversão ou economia real para usuários ou restaurantes.

## Experimentos de produto

1. Teste moderado com usuários para medir tempo até uma escolha útil.
2. Comparação entre formulário de filtros e consulta em linguagem natural.
3. Métrica de reformulação da busca e motivo de abandono.
4. Validação de clareza das fontes, lacunas e estados "não informado".
5. Entrevistas com restaurantes para avaliar atualização autorizada de cardápios.
6. Automação n8n para triagem de feeds, alertas de drift e revisão humana.

## Uso responsável

- Sem coleta de dados do iFood e sem vínculo oficial com a empresa.
- Localização é opcional e processada sem persistência.
- O modelo não escolhe restaurantes; ele apenas estrutura a intenção.
- Dados externos passam por validação, proveniência e quarentena.
- O produto explicita limites de cobertura, preço e atualização.

## Competências AI Builder demonstradas

Descoberta de problema, desenho de jornada, prototipação pública, prompt
engineering, integração de APIs, automação, métricas, experimentação, avaliação,
fallback e comunicação transparente de riscos.
