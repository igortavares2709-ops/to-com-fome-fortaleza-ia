# Avaliações de intenção e recuperação

Esta pasta contém uma avaliação de aceitação, reproduzível e sem rede, para a camada de interpretação local e para a recuperação estruturada. Ela não é um benchmark geral de linguagem e não mede um LLM externo.

Execute:

```bash
npm run eval
```

Para obter o relatório completo sem gravar arquivos:

```bash
npm run eval -- --json
```

`intent-cases.jsonl` reúne 50 pedidos curados em português. Os casos cobrem culinária, bairro, serviço, dieta, preço, raio, negações, conflitos, abstenção, entradas limítrofes e tentativas de injeção de prompt. Cada linha declara somente os campos que o caso avalia.

## Métricas

- `case_exact_rate`: proporção de casos em que todos os campos declarados e todas as verificações de recuperação passaram.
- `slot_exact_accuracy`: acerto exato dos campos declarados em `expected`.
- `collection_micro_f1`: micro-F1 dos campos de coleção (`cuisines`, `negatedCuisines`, `negated` e `unsupported`).
- `abstention_accuracy`: pedidos marcados para abstenção que retornaram todas as lacunas esperadas em `unsupported`.
- `constraint_precision`: resultados retornados que respeitam todas as restrições extraídas. A suíte não mede recall contra a população de restaurantes porque o snapshot OSM muda diariamente.
- `evidence_coverage`: resultados com motivo e URL da fonte.
- `negative_cuisine_violations`: resultados que contêm uma culinária explicitamente negada.
- `p50` e `p95`: tempo local de interpretação e recuperação, apenas informativo e sem gate no CI.

Os gates críticos exigem 100% de abstenção, precisão de restrições, cobertura de evidência, expectativas explícitas de recuperação e zero violação de culinária negada. Exact match e F1 têm limiares para impedir regressões agregadas.

O baseline versionado foi produzido por uma execução real do comando. Um resultado perfeito neste conjunto significa conformidade com o contrato curado; não demonstra generalização para pedidos fora dele. Avaliações pagas de um modelo externo devem ser executadas separadamente, com nome e versão do modelo, versão do prompt, custo, tokens e latência registrados. Nenhuma métrica de LLM não executada é publicada aqui.
