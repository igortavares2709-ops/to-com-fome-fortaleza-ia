# Tô com Fome Fortaleza IA

Assistente de descoberta que transforma pedidos em português em filtros verificáveis sobre uma base real de restaurantes de Fortaleza. A IA interpreta a intenção; código determinístico calcula distância, aplica filtros e explica cada resultado.

Projeto independente inspirado no ecossistema de delivery. **Sem vínculo com o iFood e sem coleta de dados do iFood.**

## O que já funciona

- Busca como `pizza no Meireles com delivery` ou `vegano no Benfica`.
- Filtros por bairro, culinária, raio, delivery, retirada, acessibilidade, dieta, horário e cardápio publicado.
- Localização opcional processada somente durante a busca, sem persistência.
- Mapa interativo com ruas do OpenStreetMap, zoom, escala, deslocamento e seleção sincronizada com os resultados. Uma base vetorial local mantém o limite de Fortaleza, mais de mil ruas locais longas, vias principais, água, parques, praias e os pontos visíveis quando os blocos detalhados falham; a distância usa a fórmula de Haversine.
- Explicação “Por que apareceu” baseada nos campos utilizados.
- Três estados para serviços: confirmado, não oferece e não informado.
- Links para a fonte OpenStreetMap e para cardápios públicos quando cadastrados.
- Consulta complementar opcional e em tempo real à Google Places API, exibida em lista separada e sem copiar o conteúdo para a base pública.
- Preços vindos de feeds oficiais autorizados, com data da fonte, data da coleta e estado de atualização.
- Direção visual gastronômica em tela inteira, com fotografias próprias e otimizadas para inspirar buscas por categoria.
- Interpretador local como fallback e integração opcional com um modelo generativo.

As contagens, a data e a origem do snapshot atual ficam nos metadados de `data/restaurants.json` e `data/menu-items.json`; `npm run check:data` confirma que esses valores correspondem ao conteúdo publicado.

## Atualização diária

O workflow `.github/workflows/daily-update.yml` roda diariamente às **06:15 no horário de Fortaleza** e também pode ser iniciado manualmente. Ele:

1. resolve a versão imutável mais recente do extrato Nordeste da Geofabrik;
2. baixa e verifica o MD5 somente quando a versão do OpenStreetMap mudou;
3. recorta o município usando `data/fortaleza-boundary.geojson`;
4. regenera a base vetorial local de limites, cobertura do solo e rede viária;
5. rejeita variações suspeitas no número de restaurantes;
6. atualiza cardápios e preços presentes na lista de feeds oficiais;
7. executa os testes e as validações;
8. registra somente dados realmente alterados;
9. publica a mesma versão verificada no GitHub Pages.

Fortaleza usa UTC−3 durante todo o ano, por isso `09:15 UTC` corresponde a `06:15` local. O PBF de aproximadamente 425 MB é armazenado em cache por versão e nunca é incluído no repositório.

Os dados do Google Maps não entram nessa rotina nem são gravados no repositório. Quando a integração opcional está ativa, cada busca solicitada pelo visitante consulta a Google Places API em tempo real; assim, o resultado já vem da fonte atual sem criar uma cópia diária proibida pelos termos do Google.

Ativar o workflow exige publicar este projeto em um repositório GitHub e habilitar **Settings → Pages → Source: GitHub Actions**. A publicação no Sites continua separada.

## Executar localmente

Requisito: Node.js 22 ou superior. Não há dependências de produção.

```bash
npm test
npm run check:data
npm run dev
```

Abra `http://127.0.0.1:8787`.

Sem chave de API, a interface usa o interpretador local e identifica o modo com transparência. Para experimentar a interpretação generativa, configure `OPENAI_API_KEY` somente no servidor:

```bash
export OPENAI_API_KEY='...'
export OPENAI_MODEL='gpt-4.1-mini'
npm run dev
```

Nunca coloque a chave no JavaScript do navegador ou em commits. O arquivo `.env.example` documenta as variáveis, mas o projeto não carrega `.env` automaticamente.

### Consulta ao Google Maps

A integração oficial exige uma conta Google Cloud com faturamento, a **Places API (New)** habilitada e uma chave restrita somente a essa API. Configure `GOOGLE_PLACES_API_KEY` como segredo no servidor ou no ambiente de produção do Sites. Defina também cotas de uso no Google Cloud para controlar custos.

```bash
export GOOGLE_PLACES_API_KEY='...'
npm run dev
```

A chave é usada apenas pelo Worker. O navegador recebe no máximo oito resultados em texto, com atribuição Google Maps, e nunca recebe a credencial. O conteúdo não é salvo, não é combinado com os marcadores do OpenStreetMap e só é solicitado depois de uma ação do visitante. A versão estática do GitHub Pages continua usando apenas a base local porque não possui um servidor para guardar a chave.

## Como uma busca é decidida

```text
Pedido em português
        │
        ▼
LLM opcional ──falha/sem chave──► interpretador local
        │                                │
        └──────── intenção JSON ◄────────┘
                         │
                         ▼
           validação de valores permitidos
                         │
                         ▼
       filtros + distância + ordenação em código
                         │
                         ▼
       resultados, evidências, fontes e lacunas
```

O modelo não escolhe restaurantes. Ele converte o texto para campos como `neighborhood`, `cuisines`, `radiusKm`, `delivery` e `maxPriceCents`. Valores não presentes na lista permitida são descartados. O filtro de preço é aplicado somente aos cardápios oficiais coletados; avaliações e outros dados ausentes entram em `unsupported`.

## Base de restaurantes

`data/restaurants.json` foi extraído do OpenStreetMap a partir do recorte municipal de Fortaleza:

- Município: relação OSM `302588`, código IBGE `2304400`.
- Fonte e data do snapshot: campos `meta.source_url` e `meta.snapshot_date` de `data/restaurants.json`.
- Critério: `amenity=restaurant` com nome.
- Deduplicação: mesmo nome normalizado dentro de 40 metros; fontes adicionais são preservadas.
- Licença: ODbL 1.0. Consulte [DATA_LICENSE.md](DATA_LICENSE.md).

O Google Maps não é usado para formar ou reescrever esse arquivo. Os termos da Places API proíbem copiar nomes, endereços e outros conteúdos para um banco público; por isso, a consulta oficial permanece transitória e identificada separadamente na interface.

Para reproduzir a extração, baixe o PBF versionado e execute:

```bash
python scripts/extract_geofabrik.py \
  --pbf /caminho/nordeste-AAMMDD.osm.pbf \
  --snapshot-date AAAA-MM-DD \
  --source-url https://download.geofabrik.de/south-america/brazil/nordeste-AAMMDD.osm.pbf
```

O script requer GDAL/`ogr2ogr`. Quando o Nominatim estiver indisponível, passe o polígono GeoJSON da relação `302588` usando `--boundary`.

`data/fortaleza-restaurants.overpassql` oferece uma consulta alternativa para servidores Overpass:

```overpass
[out:json][timeout:180];
rel["boundary"="administrative"]["IBGE:GEOCODIGO"="2304400"];
map_to_area->.fortaleza;
nwr["amenity"="restaurant"]["name"](area.fortaleza);
out center tags;
```

## Cardápios disponíveis

O projeto não copia cardápios do iFood. O botão **Ver cardápio** aparece apenas quando o registro do restaurante contém um endereço público em uma das tags de menu do OpenStreetMap. Cada link permanece associado ao ID da fonte.

Um link pode sair do ar ou ficar desatualizado. Por isso, a aplicação apresenta o cardápio como fonte externa e não transforma pratos ou preços em fatos locais sem uma coleta e uma data próprias. Para cadastrar um feed enviado pelo restaurante, use o contrato versionado abaixo.

As fotografias da abertura, das categorias e das faixas imersivas são imagens ilustrativas geradas para este projeto. Elas ajudam o usuário a expressar uma intenção de busca, mas não são apresentadas como pratos dos restaurantes listados. Os seis arquivos WebP ficam em `src/assets` e somam menos de 750 KB.

### Preços oficiais

`data/official-menu-feeds.json` é a lista permitida de fontes automatizadas. A cobertura inicial inclui Cocina 378 e Barraca Nossa Praia, que publicam JSON-LD de cardápio em suas páginas oficiais. Outras fontes só entram quando o restaurante oferece dados estruturados públicos ou autorizados. Páginas HTML arbitrárias e o iFood não são raspados.

Formatos aceitos:

- `json_v1`: objeto com `currency`, `updated_at` e uma lista `items` contendo `name`, `price`, descrição, categoria e disponibilidade.
- `csv_v1`: colunas `name`, `price`, `description`, `category`, `available` e `currency`.
- `jsonld_menu`: `MenuItem` e `Offer` em JSON-LD publicado na página oficial.

Exemplo de configuração:

```json
{
  "restaurant_id": "n123",
  "url": "https://restaurante.example/menu.json",
  "format": "json_v1",
  "permission_basis": "restaurant_public_feed"
}
```

Se uma fonte falhar, `scripts/update_menu_feeds.py` conserva o último preço válido e marca o cardápio como desatualizado. Um feed nunca é substituído por uma lista vazia devido a uma falha temporária.

## Qualidade e segurança

Os testes verificam:

- interpretação de intenções em português;
- filtros que exigem confirmação explícita;
- presença de fonte em todos os registros;
- URLs de cardápio rastreáveis;
- cálculo de distância;
- horários simples e abstenção para formatos não suportados;
- bloqueio de requisições POST de outra origem;
- consistência e limites geográficos do snapshot.

O Worker limita o pedido a 32 KB, restringe a consulta a 300 caracteres, valida coordenadas e aplica uma política CSP. Dados vindos da base são renderizados com `textContent`, sem injeção de HTML.

Os blocos do mapa passam pelo mesmo domínio da aplicação para evitar bloqueios de rede no navegador. O proxy aceita somente níveis de zoom 10 a 18 e coordenadas que intersectam a região de Fortaleza, preserva os cabeçalhos de cache da fonte e não funciona como proxy aberto. O arquivo `data/fortaleza-basemap.json`, derivado do mesmo snapshot OSM dos restaurantes, oferece limite municipal, cobertura do solo e uma rede viária compacta sem depender de outro domínio. A base usa Canvas e revela mais ruas conforme o zoom, preservando a fluidez.

## Limitações conhecidas

- OpenStreetMap é colaborativo e pode conter dados incompletos ou desatualizados.
- “Não informado” não significa que o restaurante não oferece o serviço.
- A leitura de `opening_hours` cobre formatos simples; expressões complexas resultam em estado desconhecido.
- A cobertura de preços ainda está limitada aos restaurantes com feed oficial autorizado; a base não possui taxa de entrega, tempo de entrega ou avaliações.
- A consulta ao Google Maps depende de uma chave de servidor, faturamento e cota disponíveis; quando ela falha, a busca local continua funcionando.
- Nomes de ruas e detalhes de quarteirões dependem do servidor público do OpenStreetMap; a base local com vias principais e locais selecionadas, cobertura do solo, pontos e lista continua visível quando ele falha.
- A geolocalização depende de autorização do navegador e não é armazenada.
- O projeto não confirma segurança para alergias.

## Avaliação recomendada para a trilha AI Builder

Monte um conjunto de 30 pedidos com intenção esperada e meça: extração correta de filtros, respeito aos filtros, resultados fundamentados e abstenção quando o dado falta. Registre modelo, data, tokens, custo e latência somente depois de executar a integração real. O repositório não apresenta métricas de IA ainda não medidas.

## Estrutura

- `src/worker.mjs`: API, interpretação, busca e servidor da aplicação.
- `src/index.html`, `styles.css`, `app.js`: interface sem frameworks.
- `src/terms.html`, `src/privacy.html`: documentos públicos exigidos pela integração opcional do Google Maps.
- `src/assets`: fotografias gastronômicas ilustrativas em WebP.
- `THIRD_PARTY_NOTICES.md`: origem e condições dos elementos de terceiros, incluindo a atribuição oficial do Google Maps.
- `src/vendor/leaflet`: Leaflet 1.9.4 hospedado localmente, sob licença BSD-2-Clause.
- `data/restaurants.json`: snapshot OSM normalizado.
- `data/fortaleza-basemap.json`: base vetorial local compacta com território, cobertura do solo e rede viária.
- `scripts/extract_geofabrik.py`: extração reproduzível do município.
- `scripts/extract_basemap.py`: extração reproduzível do limite e das vias principais.
- `scripts/update_menu_feeds.py`: atualização segura de preços oficiais.
- `.github/workflows/daily-update.yml`: atualização diária e GitHub Pages.
- `scripts/check-data.mjs`: validação da base.
- `tests/`: testes de regras e rotas.

O código está sob licença MIT. A base derivada mantém a ODbL 1.0. O mapa usa Leaflet sob BSD-2-Clause e mantém a atribuição exigida pelo OpenStreetMap visível.
